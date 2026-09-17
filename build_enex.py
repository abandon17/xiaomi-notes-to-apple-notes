#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert an exported ``raw/`` folder into a single Evernote ``.enex`` file.

Apple Notes honours ``<created>`` and ``<updated>`` when importing ENEX, so the
original Xiaomi timestamps survive the migration. Standard library only.

    python3 build_enex.py --raw raw --out notes.enex
    python3 build_enex.py --sample            # 3 notes, for a quick trial import
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# Xiaomi inline markup -> ENML
INLINE_OPEN = {
    "b": "<b>",
    "size": '<span style="font-size:1.5em">',
    "mid-size": '<span style="font-size:1.2em">',
}
INLINE_CLOSE = {"b": "</b>", "size": "</span>", "mid-size": "</span>"}

TAG_RE = re.compile(r"</?([a-zA-Z0-9_\-]+)((?:\s+[^>]*)?)>")
# An image occupies a whole line: U+263A, a space, then the fileId.
IMG_LINE_RE = re.compile(r"^☺\s+(\S+)$")
INDENT_RE = re.compile(r'indent\s*=\s*"(\d+)"')
CHECK_RE = re.compile(r"^\s*(\[\s*\]|\[[xX✓]\]|☐|☑)\s*")

DEFAULT_TAG = "Xiaomi Notes"
IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic")
MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".heic": "image/heic",
}


def ts_utc(ms: int) -> str:
    return datetime.fromtimestamp((ms or 0) / 1000.0, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def strip_markup(s: str) -> str:
    """Drop every tag and unescape entities -- used to derive plain text."""
    s = TAG_RE.sub("", s)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&apos;", "'"), ("&amp;", "&")):
        s = s.replace(a, b)
    return s


def xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def convert_inline(line: str) -> str:
    """Translate known inline markup, strip unknown tags, keep the text."""
    out, pos = [], 0
    for m in TAG_RE.finditer(line):
        out.append(line[pos:m.start()])
        pos = m.end()
        name = m.group(1).lower()
        closing = m.group(0).startswith("</")
        if name in INLINE_OPEN:
            out.append(INLINE_CLOSE[name] if closing else INLINE_OPEN[name])
        # <text>, and anything unrecognised, is dropped; its content stays.
    out.append(line[pos:])
    return "".join(out)


def note_title(note: dict) -> str:
    try:
        extra = json.loads(note.get("extraInfo") or "{}")
    except ValueError:
        extra = {}
    for candidate in (extra.get("title"), note.get("subject")):
        if candidate and candidate.strip():
            return candidate.strip()[:120]
    body = note.get("content") or note.get("snippet") or ""
    for line in body.split("\n"):
        plain = strip_markup(line).strip()
        if plain and not plain.startswith("☺"):
            return plain[:60]
    d = datetime.fromtimestamp((note.get("createDate") or 0) / 1000.0)
    return "Note " + d.strftime("%Y-%m-%d")


def find_image(img_dir: str, file_id: str):
    for ext in IMG_EXTS:
        path = os.path.join(img_dir, file_id + ext)
        if os.path.exists(path):
            return path, MIME_BY_EXT[ext]
    return None, None


def build_note_xml(note: dict, img_dir: str, folder_names: dict, tag_mode: str):
    created = note.get("createDate") or note.get("modifyDate") or 0
    updated = note.get("modifyDate") or created
    body = note.get("content")
    if body is None:
        body = note.get("snippet") or ""

    resources, seen_hashes, divs = [], set(), []
    missing = 0
    for line in body.split("\n"):
        m = IMG_LINE_RE.match(line.strip())
        if m:
            file_id = m.group(1)
            path, mime = find_image(img_dir, file_id)
            if path:
                data = open(path, "rb").read()
                md5 = hashlib.md5(data).hexdigest()
                divs.append('<div><en-media type="%s" hash="%s"/></div>' % (mime, md5))
                if md5 not in seen_hashes:
                    seen_hashes.add(md5)
                    resources.append((md5, data, mime, os.path.basename(path)))
            else:
                missing += 1
                divs.append("<div>[missing image: %s]</div>" % xml_escape(file_id))
            continue

        # Xiaomi checkbox markers -> a literal box glyph Apple Notes can show.
        prefix = ""
        cm = CHECK_RE.match(strip_markup(line))
        if cm:
            token = cm.group(1)
            prefix = "☑ " if token == "☑" or token.lower() in ("[x]", "[✓]") else "☐ "
            line = re.sub(r"(\[\s*\]|\[[xX✓]\]|☐|☑)", "", line, count=1)

        indent = 0
        im = INDENT_RE.search(line)
        if im:
            indent = int(im.group(1))

        if not strip_markup(line).strip():
            divs.append("<div><br/></div>")
        else:
            style = ' style="margin-left:%dem"' % (indent * 2) if indent else ""
            divs.append("<div%s>%s%s</div>" % (style, prefix, convert_inline(line)))

    if not divs:
        divs.append("<div><br/></div>")

    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
        "<en-note>" + "".join(divs) + "</en-note>"
    )

    parts = ["  <note>",
             "    <title>%s</title>" % xml_escape(note_title(note)),
             "    <content><![CDATA[%s]]></content>" % content,
             "    <created>%s</created>" % ts_utc(created),
             "    <updated>%s</updated>" % ts_utc(updated)]
    if tag_mode != "none":
        tag = DEFAULT_TAG if tag_mode == "fixed" else \
            folder_names.get(note.get("folderId"), DEFAULT_TAG)
        parts.append("    <tag>%s</tag>" % xml_escape(tag))
    parts.append("    <note-attributes><source>xiaomi-notes</source></note-attributes>")
    for md5, data, mime, fname in resources:
        b64 = base64.b64encode(data).decode("ascii")
        b64 = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))
        parts += ["    <resource>",
                  '      <data encoding="base64">\n%s\n      </data>' % b64,
                  "      <mime>%s</mime>" % mime,
                  "      <resource-attributes><file-name>%s</file-name>"
                  "</resource-attributes>" % xml_escape(fname),
                  "    </resource>"]
    parts.append("  </note>")
    return "\n".join(parts), len(resources), missing


def build(notes, img_dir, folder_names, outpath, tag_mode):
    export_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    chunks = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export3.dtd">',
        '<en-export export-date="%s" application="xiaomi-notes-to-apple-notes" '
        'version="1.0">' % export_date,
    ]
    total_res = total_missing = 0
    for n in notes:
        xml, nres, nmiss = build_note_xml(n, img_dir, folder_names, tag_mode)
        total_res += nres
        total_missing += nmiss
        chunks.append(xml)
    chunks.append("</en-export>")
    with open(outpath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(chunks) + "\n")
    return total_res, total_missing


def pick_sample(notes, count):
    """Oldest + newest + the first few notes that carry images."""
    picked, ids = [], set()

    def add(n):
        if n and n["id"] not in ids:
            ids.add(n["id"])
            picked.append(n)

    add(notes[0])
    add(notes[-1])
    for n in reversed(notes):
        if len(picked) >= count:
            break
        if (n.get("setting") or {}).get("data"):
            add(n)
    for n in notes:
        if len(picked) >= count:
            break
        add(n)
    picked.sort(key=lambda n: n.get("createDate") or 0)
    return picked[:count]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Turn an exported raw/ folder into an Apple Notes friendly .enex")
    ap.add_argument("--raw", default="raw", help="input directory (default: raw)")
    ap.add_argument("--out", default="notes.enex", help="output .enex path")
    ap.add_argument("--limit", type=int, default=0,
                    help="only convert the N oldest notes (0 = all)")
    ap.add_argument("--sample", action="store_true",
                    help="build a 3-note sample to trial-import first")
    ap.add_argument("--sample-size", type=int, default=3)
    ap.add_argument("--tags", choices=("folder", "fixed", "none"), default="folder",
                    help="folder: one tag per Xiaomi folder (default); "
                         "fixed: a single '%s' tag; none: no tags" % DEFAULT_TAG)
    args = ap.parse_args()

    raw = os.path.abspath(args.raw)
    notes_path = os.path.join(raw, "notes.json")
    if not os.path.exists(notes_path):
        raise SystemExit("%s not found -- run export_xiaomi_notes.py first" % notes_path)
    with open(notes_path, encoding="utf-8") as fh:
        data = json.load(fh)

    notes = data["notes"] if isinstance(data, dict) else data
    folders = (data.get("folders") if isinstance(data, dict) else None) or []
    folder_names = {}
    for fo in folders:
        try:
            folder_names[int(fo["id"])] = (fo.get("subject") or "").strip() or DEFAULT_TAG
        except (KeyError, TypeError, ValueError):
            pass

    notes = sorted(notes, key=lambda n: n.get("createDate") or 0)
    if args.sample:
        notes = pick_sample(notes, args.sample_size)
    elif args.limit:
        notes = notes[: args.limit]
    if not notes:
        raise SystemExit("no notes to convert")

    img_dir = os.path.join(raw, "images")
    res, missing = build(notes, img_dir, folder_names, args.out, args.tags)

    # Read the result back with a real XML parser before trusting it.
    root = ET.parse(args.out).getroot()
    got = len(root.findall("note"))
    if got != len(notes):
        raise SystemExit("verification failed: wrote %d notes, parsed %d" % (len(notes), got))
    for note in root.findall("note"):
        for field in ("created", "updated"):
            value = (note.findtext(field) or "").strip()
            datetime.strptime(value, "%Y%m%dT%H%M%SZ")

    print("OK %s: %d notes, %d images embedded, %.1f KB"
          % (args.out, got, res, os.path.getsize(args.out) / 1024.0))
    if missing:
        print("warning: %d image references had no local file "
              "(re-run export_xiaomi_notes.py to fetch them)" % missing)
    print("date range: %s .. %s"
          % (ts_utc(notes[0].get("createDate")), ts_utc(notes[-1].get("createDate"))))
    print("\nNow open Apple Notes and choose File > Import to Notes..., "
          "then pick %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
