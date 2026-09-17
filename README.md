# xiaomi-notes-to-apple-notes

把**小米云笔记**迁移到 **Apple 备忘录**，并保留每条笔记原始的创建时间和修改时间。

Migrate **Xiaomi Cloud Notes** into **Apple Notes**, preserving each note's original creation and modification date.

---

## 中文

### 这个工具解决什么问题

小米笔记官方没有导出到 Apple 备忘录的功能。网上常见的做法是复制粘贴，或者导出 HTML/TXT 再逐条新建——这样做所有笔记的日期都会变成「今天」，几年的时间线一次性作废。

Apple 备忘录的 **ENEX（Evernote 导出格式）**导入通道会尊重文件里的 `<created>` / `<updated>` 字段。所以这个工具做两件事：

1. `export_xiaomi_notes.py` —— 用你浏览器里已登录的 cookie，从 i.mi.com 拉下全部笔记正文和图片，存到本地 `raw/`。
2. `build_enex.py` —— 把 `raw/` 转成一个 `.enex` 文件，原始时间戳写进 `<created>` / `<updated>`，图片内嵌成 `<resource>`，正文标记转成 ENML。

导入后，备忘录里的日期就是笔记当年真正写下的日期。

### 环境要求

- macOS，Python 3.8 以上（系统自带的 `python3` 即可）
- **不需要安装任何第三方包**，两个脚本只用标准库
- 可选：`pip3 install browser_cookie3`，用于自动读取 Chrome 里的 cookie

### 第一步：取 cookie

**脚本不会、也不应该拿你的账号密码登录。** 小米账号登录普遍需要双重验证（手机短信验证码），这一步只有你本人能完成，任何脚本都替代不了。所以正确的流程是：**你先在浏览器里正常登录，然后把登录后的会话 cookie 交给脚本。**

1. 用浏览器打开 <https://i.mi.com/note/h5>，正常登录小米账号（该输验证码就输验证码），直到能看到自己的笔记列表。
2. 按 `F12`（或 `⌥⌘I`）打开开发者工具，切到 **Network / 网络** 面板。
3. 刷新页面，在请求列表里点任意一条发往 `i.mi.com` 的请求。
4. 在右侧 **Headers → Request Headers** 里找到 `Cookie:` 这一行，把**冒号后面的整串值**复制下来（很长，通常几百个字符，里面必须含有 `serviceToken=`）。
5. 存成一个文本文件，例如 `cookie.txt`：

   ```bash
   pbpaste > cookie.txt      # 或者直接用编辑器粘贴保存
   ```

**关于有效期**：这个 cookie 就是一份登录凭据，通常几小时到几天后失效，你在别处退出登录也会立刻失效。脚本检测到失效会直接提示「cookie 已失效或未登录」，这时**回到第 1 步重新登录、重新复制**即可，不需要做别的。导出中途失效也不要紧，脚本可以断点续跑（见下）。

> 可选路径：如果装了 `browser_cookie3`，可以用 `--from-browser` 让脚本直接从 Chrome 的 cookie 库里读。**这同样要求你事先已经在 Chrome 里登录过 i.mi.com**，它只是省掉手动复制那一步，不是自动登录。读不到就回退到上面的手动方式。

### 第二步：导出笔记

```bash
python3 export_xiaomi_notes.py --cookie-file cookie.txt
```

三种等价的传 cookie 方式，任选其一：

```bash
python3 export_xiaomi_notes.py --cookie-file cookie.txt      # 推荐
python3 export_xiaomi_notes.py --cookie "serviceToken=...; userId=..."
XIAOMI_COOKIE="serviceToken=...; ..." python3 export_xiaomi_notes.py
python3 export_xiaomi_notes.py --from-browser                # 需要 browser_cookie3
```

产物：

```
raw/notes.json      全部笔记（含正文）
raw/files.json      附件清单
raw/images/         图片原文件
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--limit N` | 只拉最近改动的 N 条，先试跑用 |
| `--out DIR` | 换输出目录（默认 `raw`） |
| `--skip-images` | 不下图片 |
| `--image-gap 2.0` | 加大图片下载间隔（默认 1.5 秒） |
| `--refetch` | 忽略已有缓存，全部重拉 |

**断点续跑**：脚本会复用 `raw/notes.json` 里 `modifyDate` 没变的笔记，已经下载好的图片也直接跳过。中断了（或 cookie 失效了）就换新 cookie 再跑一次同样的命令，不会重复拉取。

### 第三步：生成 .enex 并导入

**强烈建议先导一个小样本核对日期**，确认无误再导全部：

```bash
python3 build_enex.py --raw raw --out sample.enex --sample
```

这会挑出「最早的一条 + 最新的一条 + 带图片的一条」共 3 条。打开**备忘录 → 文件 → 导入到备忘录…**，选 `sample.enex`，然后在列表里确认日期确实是当年的日期、图片也在。

确认没问题后导全部：

```bash
python3 build_enex.py --raw raw --out notes.enex
```

再次「文件 → 导入到备忘录…」选 `notes.enex` 即可。备忘录会把它们放进一个新文件夹，可以先在那里检查，再决定要不要移动或合并。

`build_enex.py` 的参数：

| 参数 | 作用 |
| --- | --- |
| `--sample` | 只生成 3 条样本（配 `--sample-size N` 改数量） |
| `--limit N` | 只转换最早的 N 条 |
| `--tags folder\|fixed\|none` | 标签策略，默认按小米笔记的文件夹名打标签 |

脚本写完文件后会用 XML 解析器回读校验：条数对不上、日期格式不合法都会直接报错，不会给你一个静默损坏的 `.enex`。

### 已知限制

- **待办事项不导出。** 小米笔记侧栏的「待办」是另一套接口（`/todo/v1/user/records`），不在本工具范围内。笔记正文里的勾选框会被转成 `☐` / `☑` 字符保留下来，但不是备忘录的原生清单项。
- **「最近删除」里的笔记不导出。** 只处理正常状态的笔记。
- **文件夹变成标签。** ENEX 没有文件夹概念，小米的文件夹名会写成 `<tag>`。备忘录导入时会把所有笔记放进同一个新文件夹，标签信息可能不完全保留——如果你的笔记分了很多文件夹，导入后需要手动整理。
- **样式是有损转换。** 加粗、两级字号、缩进会保留；其它小米私有标记会被剥掉，只留纯文字。小米笔记本身格式就很简单，实际观感差别不大。
- **图片下载有节流。** 短时间内重复请求同一张图，i.mi.com 会返回 503。脚本默认每张间隔 1.5 秒并自动重试；图片多的话这一步会比较慢，急的话也别把 `--image-gap` 调太小。
- **图片是重新编码嵌入的**，体积等于原图 base64 后约 1.33 倍，几百张图会让 `.enex` 变得很大。可以先 `--limit` 分批导。
- **接口是逆向出来的，非官方。** 小米改接口这个工具就可能失效。

### 安全提示

- **cookie 等同于你的登录凭据。** 拿到它就能读你的云笔记。不要提交进 git、不要贴到聊天群、不要发给任何人。`.gitignore` 已经屏蔽了 `cookie*`，但请自己再确认一遍。
- 用完就删：`rm cookie.txt`。
- **`raw/` 和生成的 `.enex` 里是你笔记的明文原文**（包括你可能记在里面的密码、账号、身份证号）。导入备忘录确认无误后，把它们删掉：`rm -rf raw *.enex`。
- 这两个文件也都在 `.gitignore` 里。如果你 fork 了这个仓库，提交前务必 `git status` 确认没有把它们带上。
- 脚本跟随图片下载的 302 跳转时会主动**剥掉 Cookie 头**，避免把登录凭据发给小米的 CDN 域名。

---

## English

### What this solves

Xiaomi Notes has no official export path to Apple Notes. Copy-paste, or exporting HTML and re-typing, stamps every note with today's date and destroys years of timeline.

Apple Notes' **ENEX (Evernote export) importer honours the `<created>` and `<updated>` fields** in the file. So this tool does two things:

1. `export_xiaomi_notes.py` — uses the session cookie from your already-logged-in browser to pull every note body and image from i.mi.com into a local `raw/` folder.
2. `build_enex.py` — converts `raw/` into a single `.enex` with the original timestamps, images embedded as `<resource>`, and the body markup translated to ENML.

After import, each note carries the date it was actually written.

### Requirements

- macOS, Python 3.8+ (the system `python3` is fine)
- **No third-party packages required** — both scripts are standard library only
- Optional: `pip3 install browser_cookie3` to read the Chrome cookie automatically

### Step 1 — get the cookie

**These scripts never log in with a username and password, by design.** Xiaomi accounts require two-factor authentication (an SMS code), which only you can complete — no script can stand in for it. The supported flow is therefore: **you log in with a normal browser, then hand the resulting session cookie to the script.**

1. Open <https://i.mi.com/note/h5> and sign in to your Xiaomi account (SMS code included) until your notes are listed.
2. Press `F12` (or `⌥⌘I`) to open DevTools and switch to the **Network** tab.
3. Reload the page and click any request going to `i.mi.com`.
4. Under **Headers → Request Headers**, find the `Cookie:` line and copy **the entire value after the colon**. It is long (hundreds of characters) and must contain `serviceToken=`.
5. Save it to a text file, e.g. `cookie.txt`.

**Lifetime**: this cookie *is* a login credential. It typically expires after a few hours to a few days, and immediately if you sign out elsewhere. When it expires the script says so in plain language — just **repeat step 1, copy a fresh cookie, and run again**. Nothing else is needed; the export resumes where it left off.

> Optional: with `browser_cookie3` installed, `--from-browser` reads the cookie out of Chrome's cookie store. **This still requires that you already logged in to i.mi.com in Chrome** — it only skips the manual copy, it does not log in for you. If it finds nothing, fall back to the manual method above.

### Step 2 — export

```bash
python3 export_xiaomi_notes.py --cookie-file cookie.txt
```

Equivalent ways to pass the cookie: `--cookie "..."`, the `XIAOMI_COOKIE` environment variable, or `--from-browser`.

Produces `raw/notes.json`, `raw/files.json` and `raw/images/`.

Useful flags: `--limit N` (newest N notes only, good for a trial run), `--out DIR`, `--skip-images`, `--image-gap 2.0`, `--refetch`.

**Resumable**: notes whose `modifyDate` is unchanged are reused from `raw/notes.json` and existing images are skipped, so re-running after an interruption or an expired cookie costs almost nothing.

### Step 3 — build the .enex and import

**Import a small sample first and check the dates**:

```bash
python3 build_enex.py --raw raw --out sample.enex --sample
```

That picks the oldest note, the newest note and one with images. In Apple Notes choose **File → Import to Notes…** and select `sample.enex`, then confirm the dates really are the original ones.

Then do the whole set:

```bash
python3 build_enex.py --raw raw --out notes.enex
```

Flags: `--sample` (with `--sample-size N`), `--limit N`, `--tags folder|fixed|none`.

The script parses the file back with an XML parser before reporting success, so a silently corrupt `.enex` is not possible.

### Known limitations

- **To-dos are not exported.** The "To-do" sidebar in Xiaomi Notes is a separate API (`/todo/v1/user/records`) and is out of scope. Checkbox markers *inside* note bodies are preserved as `☐` / `☑` characters, not as native Apple Notes checklist items.
- **Notes in "Recently deleted" are not exported.** Only notes in normal status.
- **Folders become tags.** ENEX has no folder concept, so Xiaomi folder names are written as `<tag>`. Apple Notes puts every imported note into one new folder, and tag fidelity varies — expect to reorganise by hand if you used many folders.
- **Styling conversion is lossy.** Bold, two heading sizes and indentation survive; other Xiaomi-private markup is stripped to plain text. Xiaomi Notes formatting is simple enough that the difference is usually invisible.
- **Image downloads are throttled.** i.mi.com returns 503 if the same image is requested in quick succession, so downloads are spaced 1.5 s apart with retries. This is the slow part of a large export; don't set `--image-gap` too low.
- **Images are embedded as base64**, roughly 1.33× the original size, so a library with hundreds of photos produces a very large `.enex`. Split it with `--limit` if needed.
- **The API is reverse-engineered and unofficial.** If Xiaomi changes it, this breaks.

### Security notes

- **The cookie is a login credential** — anyone holding it can read your cloud notes. Never commit it, paste it in chat, or share it. `cookie*` is in `.gitignore`; check `git status` anyway.
- Delete it when done: `rm cookie.txt`.
- **`raw/` and the generated `.enex` contain your notes in plain text**, including any passwords or ID numbers you kept in them. Once the import looks right, remove them: `rm -rf raw *.enex`.
- When following the 302 to Xiaomi's image CDN, the script **strips the Cookie header** so the credential is never sent to a different host.

---

## License

MIT — see [LICENSE](LICENSE).

Not affiliated with, endorsed by, or supported by Xiaomi or Apple.
