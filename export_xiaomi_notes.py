#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export Xiaomi Cloud Notes (i.mi.com) to a local ``raw/`` folder.

This script never logs in. Xiaomi accounts use two-factor authentication (an
SMS code), which no script can complete on your behalf, so you sign in with a
normal browser at https://i.mi.com/note/h5 yourself and then hand the
resulting session cookie to this script.

Only the Python standard library is required. ``browser_cookie3`` is an
optional convenience for reading that same cookie straight out of Chrome --
it too requires that you have already logged in there.

Outputs
-------
raw/notes.json   {"folders": [...], "notes": [...]}  full note bodies
raw/files.json   [{"noteId", "fileId", "mimeType", "digest"}, ...]
raw/images/<fileId>.<ext>

The run is resumable: notes already stored with the same ``modifyDate`` and
images already on disk are skipped on the next run.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

BASE_URL = "https://i.mi.com"
LIST_PATH = "/note/full/page/"
NOTE_PATH = "/note/note/{note_id}/"
FILE_PATH = "/file/full"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
}


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------
class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects but never leak the ``Cookie`` header to another host.

    Image downloads on i.mi.com 302 to a signed CDN URL that needs no cookie.
    Forwarding the login cookie there would hand the credential to a third
    party for no reason.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            for key in list(new.headers):
                if key.lower() == "cookie":
                    del new.headers[key]
            new.unredirected_hdrs.pop("Cookie", None)
        return new


_OPENER = urllib.request.build_opener(_SameHostRedirect)


def _decompress(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    if encoding == "gzip":
        return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


RELOGIN_HINT = (
    "\nThe cookie is expired or you are not signed in.\n"
    "Xiaomi accounts use two-factor authentication, so no script can log in\n"
    "for you. Open https://i.mi.com/note/h5 in your browser, sign in again\n"
    "(including the SMS code), then copy a fresh Cookie request header from\n"
    "DevTools > Network and run this script again.\n"
)


class AuthExpired(SystemExit):
    def __init__(self, detail: str = ""):
        super().__init__(("cookie rejected: %s\n" % detail if detail else "") + RELOGIN_HINT)


def _looks_like_login_page(body: bytes) -> bool:
    head = body[:2048].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        return True
    return b"account.xiaomi.com" in head or b"serviceloginauth" in head


def http_get(url: str, cookie: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Referer": BASE_URL + "/note/h5",
            "Cookie": cookie,
        },
    )
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            body = _decompress(resp.read(), resp.headers.get("Content-Encoding", ""))
            if resp.geturl().startswith("https://account.xiaomi.com"):
                raise AuthExpired("redirected to the Xiaomi login page")
            return body
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthExpired("HTTP %d from i.mi.com" % exc.code)
        raise


def get_json(url: str, cookie: str, timeout: int = 30) -> dict:
    body = http_get(url, cookie, timeout=timeout)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        if _looks_like_login_page(body):
            raise AuthExpired("the server returned an HTML login page instead of JSON")
        raise SystemExit(
            "Unexpected non-JSON response from %s (first bytes: %r)"
            % (url, body[:120].decode("utf-8", "replace"))
        )
    code = payload.get("code")
    if code not in (0, None):
        desc = str(payload.get("description") or payload.get("result") or "")
        if code in (401, 403, 21317) or "login" in desc.lower() or "auth" in desc.lower():
            raise AuthExpired("API code=%s %s" % (code, desc))
        raise SystemExit("i.mi.com API error: code=%s description=%s" % (code, desc))
    return payload


def ts() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------
# Cookie resolution
# --------------------------------------------------------------------------
REQUIRED_COOKIE_KEYS = ("serviceToken",)


def cookie_from_browser() -> str:
    """Reuse a session an already-logged-in browser holds. No login happens here."""
    try:
        import browser_cookie3  # type: ignore
    except ImportError:
        return ""
    chunks = {}
    for loader in ("chrome", "edge", "brave", "firefox", "safari"):
        fn = getattr(browser_cookie3, loader, None)
        if fn is None:
            continue
        for domain in ("i.mi.com", "mi.com"):
            try:
                for c in fn(domain_name=domain):
                    if c.name in ("serviceToken", "userId", "cUserId") or c.name.startswith("i.mi.com"):
                        chunks.setdefault(c.name, c.value)
            except Exception:
                continue
        if "serviceToken" in chunks:
            break
    return "; ".join("%s=%s" % kv for kv in chunks.items())


def resolve_cookie(args) -> str:
    cookie = ""
    source = ""
    if args.cookie:
        cookie, source = args.cookie, "--cookie"
    elif args.cookie_file:
        with open(args.cookie_file, "r", encoding="utf-8") as fh:
            cookie, source = fh.read(), "--cookie-file"
    elif os.environ.get("XIAOMI_COOKIE"):
        cookie, source = os.environ["XIAOMI_COOKIE"], "XIAOMI_COOKIE"
    elif args.from_browser:
        cookie, source = cookie_from_browser(), "browser_cookie3"

    cookie = " ".join(cookie.split()).strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if not cookie:
        raise SystemExit(
            "No cookie supplied.\n"
            "This script cannot log in for you -- Xiaomi accounts require an SMS\n"
            "second factor. Sign in yourself at https://i.mi.com/note/h5 , then:\n"
            "  1. open DevTools (F12) > Network and click any i.mi.com request\n"
            "  2. copy the entire 'Cookie:' request header from Request Headers\n"
            "  3. save it to cookie.txt and pass --cookie-file cookie.txt\n"
            "Alternatives: --cookie '...', the XIAOMI_COOKIE env var, or\n"
            "--from-browser (needs the optional browser_cookie3 package and a\n"
            "Chrome profile that is already logged in)."
        )
    missing = [k for k in REQUIRED_COOKIE_KEYS if (k + "=") not in cookie]
    if missing:
        print("warning: cookie has no %s= ; the request will probably fail"
              % ", ".join(missing), file=sys.stderr)
    print("cookie source: %s (%d chars)" % (source, len(cookie)))
    return cookie


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------
def fetch_list(cookie: str, limit: int = 200, max_pages: int = 200):
    """Walk the paginated note index. Returns (entries, folders)."""
    entries, folders = [], []
    seen_ids, seen_folder_ids = set(), set()
    sync_tag = None
    for page in range(1, max_pages + 1):
        params = {"ts": ts(), "limit": limit}
        if sync_tag:
            params["syncTag"] = sync_tag
        url = BASE_URL + LIST_PATH + "?" + urllib.parse.urlencode(params)
        data = get_json(url, cookie).get("data") or {}

        page_entries = data.get("entries") or []
        for e in page_entries:
            if e.get("id") in seen_ids:
                continue
            seen_ids.add(e.get("id"))
            entries.append(e)
        for f in data.get("folders") or []:
            if f.get("id") in seen_folder_ids:
                continue
            seen_folder_ids.add(f.get("id"))
            folders.append(f)

        print("  page %d: +%d notes (total %d)" % (page, len(page_entries), len(entries)))
        if data.get("lastPage") or not page_entries:
            break
        sync_tag = data.get("syncTag")
        if not sync_tag:
            break
        time.sleep(0.2)
    return entries, folders


def fetch_note(cookie: str, note_id: str) -> dict:
    url = BASE_URL + NOTE_PATH.format(note_id=urllib.parse.quote(str(note_id)))
    url += "?" + urllib.parse.urlencode({"ts": ts()})
    return (get_json(url, cookie).get("data") or {}).get("entry") or {}


def download_image(cookie: str, file_id: str, dest: str, retries: int = 5,
                   gap: float = 1.5) -> bool:
    url = BASE_URL + FILE_PATH + "?" + urllib.parse.urlencode(
        {"type": "note_img", "fileid": file_id}
    )
    for attempt in range(1, retries + 1):
        try:
            blob = http_get(url, cookie, timeout=60)
            if len(blob) < 64:
                raise ValueError("suspiciously small response (%d bytes)" % len(blob))
            tmp = dest + ".part"
            with open(tmp, "wb") as fh:
                fh.write(blob)
            os.replace(tmp, dest)
            return True
        except Exception as exc:  # 503 throttling is the common case
            wait = gap * attempt + random.uniform(0, 0.5)
            print("    retry %d/%d for %s after %s (%.1fs)"
                  % (attempt, retries, file_id, type(exc).__name__, wait))
            if attempt == retries:
                print("    GIVING UP on %s: %s" % (file_id, exc))
                return False
            time.sleep(wait)
    return False


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export Xiaomi Cloud Notes (i.mi.com) into a local raw/ folder."
    )
    ap.add_argument("--cookie", help="full Cookie request header value")
    ap.add_argument("--cookie-file", help="file containing the Cookie header value")
    ap.add_argument("--from-browser", action="store_true",
                    help="read the cookie via the optional browser_cookie3 package; "
                         "you must already be logged in to i.mi.com in that browser")
    ap.add_argument("--out", default="raw", help="output directory (default: raw)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only fetch the N newest notes (0 = all)")
    ap.add_argument("--page-size", type=int, default=200)
    ap.add_argument("--delay", type=float, default=0.25,
                    help="seconds between note requests (default 0.25)")
    ap.add_argument("--image-gap", type=float, default=1.5,
                    help="seconds between image downloads; i.mi.com 503s when "
                         "the same image is requested too fast (default 1.5)")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--refetch", action="store_true",
                    help="ignore the existing raw/notes.json cache")
    args = ap.parse_args()

    cookie = resolve_cookie(args)
    out = os.path.abspath(args.out)
    img_dir = os.path.join(out, "images")
    os.makedirs(img_dir, exist_ok=True)
    notes_path = os.path.join(out, "notes.json")
    files_path = os.path.join(out, "files.json")

    cache = {}
    if os.path.exists(notes_path) and not args.refetch:
        try:
            with open(notes_path, encoding="utf-8") as fh:
                for n in (json.load(fh).get("notes") or []):
                    cache[str(n.get("id"))] = n
            print("resume: %d notes already in %s" % (len(cache), notes_path))
        except (ValueError, OSError):
            cache = {}

    print("fetching note index ...")
    entries, folders = fetch_list(cookie, limit=args.page_size)
    entries.sort(key=lambda e: e.get("modifyDate") or 0, reverse=True)
    if args.limit:
        entries = entries[: args.limit]
    print("index: %d notes, %d folders" % (len(entries), len(folders)))

    notes, fetched, reused = [], 0, 0
    for i, entry in enumerate(entries, 1):
        note_id = str(entry.get("id"))
        cached = cache.get(note_id)
        if cached and cached.get("modifyDate") == entry.get("modifyDate") \
                and cached.get("content") is not None:
            notes.append(cached)
            reused += 1
        else:
            detail = fetch_note(cookie, note_id)
            merged = dict(entry)
            merged.update({k: v for k, v in detail.items() if v is not None})
            notes.append(merged)
            fetched += 1
            time.sleep(args.delay)
        if i % 20 == 0 or i == len(entries):
            print("  notes %d/%d (fetched %d, reused %d)"
                  % (i, len(entries), fetched, reused))
            with open(notes_path, "w", encoding="utf-8") as fh:
                json.dump({"folders": folders, "notes": notes}, fh,
                          ensure_ascii=False, indent=1)

    with open(notes_path, "w", encoding="utf-8") as fh:
        json.dump({"folders": folders, "notes": notes}, fh, ensure_ascii=False, indent=1)
    print("wrote %s (%d notes)" % (notes_path, len(notes)))

    files = []
    for n in notes:
        for item in ((n.get("setting") or {}).get("data") or []):
            if item.get("fileId"):
                files.append({
                    "noteId": str(n.get("id")),
                    "fileId": item["fileId"],
                    "mimeType": item.get("mimeType") or "image/jpeg",
                    "digest": item.get("digest"),
                })
    with open(files_path, "w", encoding="utf-8") as fh:
        json.dump(files, fh, ensure_ascii=False, indent=1)
    print("wrote %s (%d attachments)" % (files_path, len(files)))

    if args.skip_images or not files:
        return 0

    ok = skipped = failed = 0
    for i, f in enumerate(files, 1):
        ext = EXT_BY_MIME.get((f["mimeType"] or "").lower(), ".jpg")
        dest = os.path.join(img_dir, f["fileId"] + ext)
        if os.path.exists(dest) and os.path.getsize(dest) > 64:
            skipped += 1
            continue
        print("  image %d/%d %s" % (i, len(files), f["fileId"]))
        if download_image(cookie, f["fileId"], dest, gap=args.image_gap):
            ok += 1
        else:
            failed += 1
        time.sleep(args.image_gap)
    print("images: %d downloaded, %d already present, %d failed"
          % (ok, skipped, failed))
    print("\nNext step:  python3 build_enex.py --raw %s --out notes.enex" % args.out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
