"""Long-term context for prominence-chan's owner agent: a full-text search index over notes it
saves plus the owner's docs, project summaries and past Claude Code / Codex sessions. Nothing here is
in the agent's prompt; it looks things up when it needs them (run with python3, from anywhere).

  context.py search <words> [-n N] [--kind K]  best matches (kinds: note, doc, project, session,
                                               memory, chat)
  context.py get <id>                          the full text of one result
  context.py note "<text>" [--title T] [--tags a,b] [--expires YYYY-MM-DD]
                                               save a fact worth remembering (deadline, preference, ...)
  context.py notes [--tag T] [--all]           list saved notes (newest first; --all includes expired)
  context.py forget <id>                       delete a note
  context.py related <id> [-n N]               other items about the same things as result <id>
  context.py index [--quiet]                   refresh the file/session index (the bot does this itself)
  context.py stats

Sessions (every Claude Code and Codex session on this machine, with titles, projects and files touched):
  context.py sessions [<words>] [--project P] [--tool claude|codex] [--since YYYY-MM-DD] [-n N]
                                               newest first, or the best matches for <words>
  context.py session <id> [--grep <words>] [--tail N]
                                               one session: what it was, files, how to resume it;
                                               --grep finds passages in its transcript, --tail shows
                                               its last N exchanges
  context.py projects                          projects by recent activity, with their sessions

Chat: the server's public channels are indexed too (kind chat, conversations with names and
times), so search can find what people said and when.

Notes are the agent's own memory and always outrank file hits for the same words. Search uses
SQLite FTS5 (BM25): every word must match, with a fallback to any word if that finds too little;
"quoted phrases" and prefix* work.
"""
import calendar, json, os, re, sqlite3, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

DB = config.data("context.db")
HOME = os.path.expanduser("~")

DOC_ROOTS = config.DOC_ROOTS          # folders searched for docs (config "doc_roots")
PROJECT_ROOTS = config.PROJECT_ROOTS  # each folder in these is a project (config "project_roots")
DOC_EXT = {".md", ".txt", ".tex", ".rst", ".org", ".docx", ".rtf"}
SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "site-packages", "__pycache__", "dist", "build",
             "target", ".next", ".cache", "Library", "replays", "logs"}
SECRET = re.compile(r"(^\.env|token|secret|password|credential|\.key$|\.pem$|\.ppk$|id_rsa)", re.I)
MAX_DOC_BYTES = 400_000
CHUNK = 1500                 # characters per indexed chunk
MAX_CHUNKS = 60              # per file or session
SESSION_DIRS = [os.path.join(HOME, ".claude", "projects"), os.path.join(HOME, ".codex", "sessions")]


def db():
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS item(id INTEGER PRIMARY KEY, kind TEXT, title TEXT, source TEXT,
            tags TEXT DEFAULT '', created REAL, expires REAL, mtime REAL);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, body, tags, tokenize='porter unicode61');
        CREATE TABLE IF NOT EXISTS seen(source TEXT PRIMARY KEY, mtime REAL, size INTEGER);
        CREATE INDEX IF NOT EXISTS item_source ON item(source);
        CREATE TABLE IF NOT EXISTS sess(id INTEGER PRIMARY KEY, source TEXT UNIQUE, tool TEXT, sid TEXT,
            cwd TEXT, title TEXT, started REAL, ended REAL, prompts INTEGER, first TEXT, last TEXT,
            files TEXT, noise INTEGER, mtime REAL, size INTEGER);
        CREATE INDEX IF NOT EXISTS sess_ended ON sess(ended);
    """)
    return con


def _add(con, kind, title, body, source="", tags="", expires=None, mtime=None):
    cur = con.execute("INSERT INTO item(kind,title,source,tags,created,expires,mtime) VALUES(?,?,?,?,?,?,?)",
                      (kind, title, source, tags, time.time(), expires, mtime))
    con.execute("INSERT INTO fts(rowid,title,body,tags) VALUES(?,?,?,?)", (cur.lastrowid, title, body, tags))
    return cur.lastrowid


def _drop_source(con, source):
    ids = [r[0] for r in con.execute("SELECT id FROM item WHERE source=?", (source,))]
    for i in ids:
        con.execute("DELETE FROM fts WHERE rowid=?", (i,))
    con.execute("DELETE FROM item WHERE source=?", (source,))


def chunks(text, cap=MAX_CHUNKS):
    """~CHUNK-sized pieces, split on paragraph breaks where possible (at most `cap`)."""
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        while len(para) > CHUNK:
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:CHUNK])
            para = para[CHUNK:]
        if len(cur) + len(para) > CHUNK and cur:
            out.append(cur)
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        out.append(cur)
    return out[:cap]


# --- indexing -------------------------------------------------------------------------------------
def _read_doc(path):
    if path.endswith((".docx", ".rtf")):
        try:
            return subprocess.run(["textutil", "-convert", "txt", "-stdout", path], capture_output=True,
                                  text=True, timeout=30).stdout
        except Exception:
            return ""
    with open(path, errors="replace") as f:
        return f.read()


def _doc_files():
    for root in DOC_ROOTS:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext in DOC_EXT and not SECRET.search(fn) and not fn.endswith(".log"):
                    yield os.path.join(dirpath, fn)


NOISE_CWDS = ("/private/var/folders", "/tmp", "/private/tmp", "/var/folders")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
PATCH_FILE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\s\\]+)")  # stops at a JSON-escaped \\n


def _ts(value):
    try:  # transcripts use UTC ISO timestamps
        return calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def _parse_session(path):
    """(catalog fields, prose) for a Claude Code or Codex transcript. The prose is user prompts and
    assistant text only (no tool output)."""
    parts, meta = [], {"cwd": "", "sid": "", "title": "", "started": None, "ended": None,
                       "prompts": 0, "first": "", "last": "", "files": []}
    tool = "codex" if os.path.join(".codex", "") in path else "claude"
    files = {}
    with open(path, errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = _ts(d.get("timestamp") or "")
            if ts:
                meta["started"] = meta["started"] or ts
                meta["ended"] = ts
            payload = d.get("payload") if isinstance(d.get("payload"), dict) else {}
            meta["cwd"] = meta["cwd"] or d.get("cwd") or payload.get("cwd") or ""
            meta["sid"] = meta["sid"] or d.get("sessionId") or payload.get("session_id") or ""
            if payload.get("thread_source") == "subagent":
                meta["subagent"] = True  # a Codex helper thread spawned by another session
            if d.get("type") == "ai-title" and d.get("aiTitle"):
                meta["title"] = d["aiTitle"]
            # files touched: Claude's edit tools, Codex's apply_patch
            if payload.get("type") in ("custom_tool_call", "function_call"):
                for fp in PATCH_FILE.findall(str(payload.get("input") or payload.get("arguments") or "")):
                    files[fp] = files.get(fp, 0) + 1
            msg = d.get("message") if isinstance(d.get("message"), dict) else payload
            role = msg.get("role") or d.get("type")
            if role not in ("user", "assistant") or d.get("isMeta") or d.get("isSidechain"):
                continue
            content = msg.get("content")
            blocks = content if isinstance(content, list) else []
            for c in blocks:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in EDIT_TOOLS:
                    fp = (c.get("input") or {}).get("file_path") or (c.get("input") or {}).get("notebook_path")
                    if fp:
                        files[fp] = files.get(fp, 0) + 1
            texts = [content] if isinstance(content, str) else [
                c.get("text", "") for c in blocks if isinstance(c, dict)
                and c.get("type") in ("text", "input_text", "output_text")]
            for t in texts:
                t = t.strip()
                if not t or t.startswith(("<command-", "<local-command", "<system-reminder",
                                          "<environment_context", "Caveat:", "<user_instructions",
                                          "# AGENTS.md", "<permissions", "<task-notification")):
                    continue
                if role == "user":
                    meta["prompts"] += 1
                    # the first prompt that says something ("u", "hi" make poor titles)
                    if not meta["first"] or (len(meta["first"]) < 20 <= len(t) and meta["prompts"] <= 5):
                        meta["first"] = t[:400]
                else:
                    meta["last"] = t[:500]
                if len(t) >= 40:  # short ones ("hi", "ok") are noise in search
                    parts.append(("USER: " if role == "user" else "AI: ") + t[:4000])
    meta["files"] = sorted(files, key=lambda k: -files[k])
    meta["tool"] = tool
    if not meta["sid"]:
        meta["sid"] = os.path.basename(path).rsplit(".", 1)[0][-36:]
    return meta, "\n\n".join(parts)


def _session_text(path):
    meta, text = _parse_session(path)
    return meta["cwd"], text


_codex_titles = None


def _codex_title(sid):
    global _codex_titles
    if _codex_titles is None:
        _codex_titles = {}
        try:
            for line in open(os.path.join(HOME, ".codex", "session_index.jsonl")):
                e = json.loads(line)
                _codex_titles[e.get("id")] = e.get("thread_name") or ""
        except (OSError, ValueError):
            pass
    return _codex_titles.get(sid, "")


def _catalog(con, path, st, meta):
    """Upsert one session's row in the catalog."""
    title = meta["title"] or (_codex_title(meta["sid"]) if meta["tool"] == "codex" else "") \
        or " ".join(meta["first"].split())[:90]
    # the bot's own throwaway runs: group-chat replies, scheduled workflows, #general tasks
    noise = int(meta["cwd"].startswith(NOISE_CWDS) or not (meta["prompts"] or meta["last"])
                or meta.get("subagent", False)
                or meta["first"].startswith(("Workflow `", "[#general just before", "[test run of", "[test:")))
    con.execute("INSERT INTO sess(source,tool,sid,cwd,title,started,ended,prompts,first,last,files,noise,"
                "mtime,size) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET "
                "tool=excluded.tool, sid=excluded.sid, cwd=excluded.cwd, title=excluded.title, "
                "started=excluded.started, ended=excluded.ended, prompts=excluded.prompts, "
                "first=excluded.first, last=excluded.last, files=excluded.files, noise=excluded.noise, "
                "mtime=excluded.mtime, size=excluded.size",
                (path, meta["tool"], meta["sid"], meta["cwd"], title, meta["started"],
                 meta["ended"] or st.st_mtime, meta["prompts"], meta["first"], meta["last"],
                 "\n".join(meta["files"][:40]), noise, st.st_mtime, st.st_size))
    return title


def _session_files():
    for root in SESSION_DIRS:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("subagents", "memory", "tool-results")]
            for fn in files:
                if fn.endswith(".jsonl"):
                    yield os.path.join(dirpath, fn)


def _memory_files():
    root = os.path.join(HOME, ".claude", "projects")
    for proj in os.listdir(root) if os.path.isdir(root) else []:
        mem = os.path.join(root, proj, "memory")
        if os.path.isdir(mem):
            for fn in os.listdir(mem):
                if fn.endswith(".md") and fn != "MEMORY.md":
                    yield os.path.join(mem, fn)


def _project_dirs():
    for root in PROJECT_ROOTS:
        for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            p = os.path.join(root, name)
            if os.path.isdir(p) and not name.startswith("."):
                yield p


def _project_summary(p):
    try:
        entries = sorted(os.listdir(p))
    except OSError:
        return ""
    readme = next((e for e in entries if e.lower().startswith(("readme", "claude.md", "agents.md"))), None)
    head = ""
    if readme and os.path.isfile(os.path.join(p, readme)):
        head = _read_doc(os.path.join(p, readme))[:3000]
    listing = ", ".join(e for e in entries if not e.startswith(".") and not SECRET.search(e))[:1500]
    return f"Project folder {p}\nContents: {listing}\n\n{head}"


def _changed(con, source, mtime, size):
    row = con.execute("SELECT mtime,size FROM seen WHERE source=?", (source,)).fetchone()
    return row is None or row[0] != mtime or row[1] != size


def _mark(con, source, mtime, size):
    con.execute("INSERT OR REPLACE INTO seen VALUES(?,?,?)", (source, mtime, size))


def index(quiet=False):
    con = db()
    live, added, cataloged = set(), 0, 0
    jobs = ([("memory", f) for f in _memory_files()] + [("doc", f) for f in _doc_files()]
            + [("session", f) for f in _session_files()])
    for kind, path in jobs:
        try:
            st = os.stat(path)
        except OSError:
            continue
        if kind == "doc" and st.st_size > MAX_DOC_BYTES:
            continue
        live.add(path)
        changed = _changed(con, path, st.st_mtime, st.st_size)
        if kind == "session":
            # the catalog keeps up with live sessions; the search index waits until one has been
            # quiet 10 min, since a session still being written changes every minute
            row = con.execute("SELECT mtime, size FROM sess WHERE source=?", (path,)).fetchone()
            need_catalog = row is None or tuple(row) != (st.st_mtime, st.st_size)
            changed = changed and time.time() - st.st_mtime >= 600
            if not (need_catalog or changed):
                continue
            try:
                meta, session_prose = _parse_session(path)
            except Exception as e:
                if not quiet:
                    print("skip", path, e, file=sys.stderr)
                continue
            if need_catalog:
                session_title = _catalog(con, path, st, meta)
                cataloged += 1
            else:
                session_title = con.execute("SELECT title FROM sess WHERE source=?", (path,)).fetchone()[0]
        if not changed:
            continue
        _drop_source(con, path)
        try:
            if kind == "session":
                cwd, text = meta["cwd"], session_prose
                if cwd.startswith(NOISE_CWDS):
                    text = ""  # the bot's own one-off group-chat replies: noise
                title = (f"session {time.strftime('%Y-%m-%d', time.localtime(st.st_mtime))} in {cwd or '?'}"
                         + (f": {session_title}" if session_title else ""))
                # keep the start (what was asked) and the end (where it landed) of long sessions
                pieces = chunks(text, cap=10 ** 6)
                if len(pieces) > MAX_CHUNKS:
                    pieces = pieces[:MAX_CHUNKS // 2] + pieces[-MAX_CHUNKS // 2:]
            else:
                text = _read_doc(path)
                title = (os.path.basename(path) if kind == "doc" else
                         "memory: " + os.path.basename(path)[:-3]) + "  (" + path.replace(HOME, "~") + ")"
                pieces = chunks(text)
            for k, piece in enumerate(pieces):
                _add(con, kind, title + (f" [part {k + 1}]" if len(pieces) > 1 else ""), piece,
                     source=path, mtime=st.st_mtime)
            added += 1
        except Exception as e:
            if not quiet:
                print("skip", path, e, file=sys.stderr)
        _mark(con, path, st.st_mtime, st.st_size)
        if (added + cataloged) % 200 == 0:
            con.commit()
    # one summary per project folder, refreshed every run (cheap)
    for p in _project_dirs():
        key = "project:" + p
        live.add(key)
        _drop_source(con, key)
        _add(con, "project", "project " + os.path.basename(p) + "  (" + p.replace(HOME, "~") + ")",
             _project_summary(p), source=key, tags="project")
    # files that are gone (notes have no source, so they're never touched here)
    chats = index_chat(con)
    for (source,) in con.execute("SELECT source FROM seen").fetchall():
        if source not in live and not source.startswith("chatpos:"):
            _drop_source(con, source)
            con.execute("DELETE FROM seen WHERE source=?", (source,))
    for (source,) in con.execute("SELECT source FROM sess").fetchall():
        if source not in live:
            con.execute("DELETE FROM sess WHERE source=?", (source,))
    con.commit()
    if not quiet:
        print(f"indexed {added} changed files/sessions and {chats} chat conversations, "
              f"cataloged {cataloged} sessions")


# --- the server's chat: conversations from the bot's crawl of its public channels -----------------
CHAT_INDEX = config.data("message_index.json")
CHAT_GAP = 30 * 60  # this long without a message in a channel starts a new conversation


def _discord(path):
    import urllib.request
    req = urllib.request.Request("https://discord.com/api/v10" + path, headers={
        "Authorization": f"Bot {config.token()}", "User-Agent": "DiscordBot (https://claude.com, 1.0)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _chat_names():
    """(user id -> display name, channel id -> name), best effort."""
    users, chans = {}, {}
    try:
        me = _discord("/users/@me")
        users[me["id"]] = f"{me['username']} (the bot)"
        guild = _discord(f"/channels/{config.MAIN_CHANNEL}")["guild_id"]
        for m in _discord(f"/guilds/{guild}/members?limit=1000"):
            u = m["user"]
            users[u["id"]] = m.get("nick") or u.get("global_name") or u["username"]
        chans = {c["id"]: c["name"] for c in _discord(f"/guilds/{guild}/channels")}
    except Exception:
        pass
    return users, chans


PING = re.compile(r"<@!?(\d+)>")


def _snowflake_time(i):
    return ((int(i) >> 22) + 1420070400000) / 1000


def index_chat(con):
    """Index each conversation (messages in one channel with no 30-min gap) as kind 'chat', redoing
    only conversations that got new messages since the last run. Returns how many were (re)indexed."""
    try:
        with open(CHAT_INDEX) as f:
            msgs = json.load(f)["messages"].values()
    except (OSError, ValueError):
        return 0
    by_channel = {}
    for mid, channel, author, text in msgs:
        by_channel.setdefault(channel, []).append((int(mid), author, text))
    users = chans = None
    done = 0
    for channel, ms in by_channel.items():
        ms.sort()
        row = con.execute("SELECT mtime FROM seen WHERE source=?", ("chatpos:" + channel,)).fetchone()
        last_seen = int(row[0]) if row else 0
        if ms[-1][0] <= last_seen:
            continue
        convos, cur = [], []
        for m in ms:
            if cur and _snowflake_time(m[0]) - _snowflake_time(cur[-1][0]) > CHAT_GAP:
                convos.append(cur)
                cur = []
            cur.append(m)
        convos.append(cur)
        if users is None:
            users, chans = _chat_names()
        cname = "#" + chans.get(channel, channel)
        for convo in convos:
            if convo[-1][0] <= last_seen:
                continue  # unchanged since the last run
            source = f"chat:{channel}:{convo[0][0]}"
            _drop_source(con, source)
            mention = lambda m: "@" + users.get(m.group(1), "someone")  # noqa: E731
            lines = [f"{time.strftime('%m-%d %H:%M', time.localtime(_snowflake_time(mid)))} "
                     # the regex stays outside the f-string: python 3.9 rejects backslashes there
                     f"{users.get(author, author)}: " + PING.sub(mention, text)
                     for mid, author, text in convo]
            day = time.strftime("%Y-%m-%d %H:%M", time.localtime(_snowflake_time(convo[0][0])))
            people = sorted({users.get(a, a) for _, a, _ in convo})
            pieces = chunks("\n".join(lines).replace("\n", "\n\n"), cap=10 ** 6)
            for k, piece in enumerate(pieces):
                _add(con, "chat", f"chat {cname} {day}" + (f" [part {k + 1}]" if len(pieces) > 1 else "")
                     + f" ({', '.join(people[:8])})", piece.replace("\n\n", "\n"), source=source,
                     mtime=_snowflake_time(convo[-1][0]))
            done += 1
            if done % 200 == 0:
                con.commit()
        con.execute("INSERT OR REPLACE INTO seen VALUES(?,?,?)", ("chatpos:" + channel, ms[-1][0], 0))
    return done


CHAT_STOP = set("""remember said say says thing things going know think really what how why
who lol lmao bro yeah yea idk tbh ngl did does doing gonna wanna want need see look good bad
time today tomorrow yesterday right now still even much more most very well back over prominence
fable bot chan""".split())


def recall_chat(text, before, n=2):
    """Older server conversations about the same rare things as `text` (from before `before`, a unix
    time): [(title, snippet)], best first. For the bot's group-chat replies, so it can remember."""
    con = db()
    total = con.execute("SELECT count(*) FROM item").fetchone()[0] or 1
    words = []
    for w in {w.strip("'") for w in re.findall(r"[a-z0-9']{3,}", text.lower())} - CHAT_STOP - STOP:
        # how many items mention it (a MATCH, not fts5vocab: the index holds porter stems)
        df = con.execute("SELECT count(*) FROM fts WHERE fts MATCH ?", (f'"{w}"',)).fetchone()[0]
        if df:
            words.append((df, w))
    words.sort()
    # the rarest word has to be rare enough to mean something; others can be common
    if len(words) < 2 or words[0][0] >= total * 0.03:
        return []
    words = [w for _, w in words[:4]]
    q = " OR ".join(f'"{w}"' for w in words)
    rows = con.execute("SELECT item.title, snippet(fts, 1, '', '', ' … ', 30), fts.body, "
                       "bm25(fts, 4.0, 1.0, 2.0) AS score FROM fts JOIN item ON item.id = fts.rowid "
                       "WHERE fts MATCH ? AND item.kind = 'chat' AND item.mtime < ? ORDER BY score LIMIT 40",
                       (q, before)).fetchall()
    out = []
    for title, snip, body, _ in rows:
        low = body.lower()
        if words[0] in low and sum(w in low for w in words) >= 2:  # the rarest word, plus another
            out.append((title, " ".join(snip.split())))
        if len(out) >= n:
            break
    return out


# --- lookups --------------------------------------------------------------------------------------
def _fts_query(words, any_word=False):
    phrases = re.findall(r'"([^"]+)"', words)
    rest = re.sub(r'"[^"]+"', " ", words)
    toks = ['"' + p.replace('"', "") + '"' for p in phrases]
    for t in re.findall(r"[\w.@#+-]+\*?", rest):
        star = t.endswith("*")
        t = t.rstrip("*").replace('"', "")
        if t:
            toks.append('"' + t + '"' + ("*" if star else ""))
    return (" OR " if any_word else " ").join(toks)


def _noise_sources(con):
    return {r[0] for r in con.execute("SELECT source FROM sess WHERE noise = 1")}


def search(words, n=8, kind=None):
    con = db()
    noise = _noise_sources(con)
    now = time.time()
    rows = []
    for any_word in (False, True):
        q = _fts_query(words, any_word)
        if not q:
            return []
        sql = ("SELECT item.id, item.kind, item.title, item.created, item.expires, "
               "snippet(fts, 1, '[', ']', ' … ', 24), bm25(fts, 4.0, 1.0, 2.0) AS score, item.source "
               "FROM fts JOIN item ON item.id = fts.rowid WHERE fts MATCH ? "
               + ("AND item.kind = ? " if kind else "") + "ORDER BY score LIMIT ?")
        args = [q] + ([kind] if kind else []) + [n * 3]
        try:
            rows = [r[:7] for r in con.execute(sql, args).fetchall() if r[7] not in noise]
        except sqlite3.OperationalError as e:
            print("search error:", e, file=sys.stderr)
            return []
        if len(rows) >= min(3, n):
            break
    # notes first (unexpired), then everything else by score
    rows = [r for r in rows if not (r[1] == "note" and r[4] and r[4] < now)]
    rows.sort(key=lambda r: (r[1] != "note", r[6]))
    return rows[:n]


def get(i):
    con = db()
    row = con.execute("SELECT item.kind, item.title, item.source, item.tags, item.created, fts.body "
                      "FROM item JOIN fts ON fts.rowid = item.id WHERE item.id=?", (i,)).fetchone()
    return row


def note(text, title="", tags="", expires=None):
    con = db()
    title = title or text.strip().splitlines()[0][:80]
    i = _add(con, "note", title, text, tags=tags, expires=expires)
    con.commit()
    return i


def notes(tag=None, include_expired=False):
    con = db()
    now = time.time()
    rows = con.execute("SELECT item.id, item.title, item.tags, item.created, item.expires, fts.body FROM item "
                       "JOIN fts ON fts.rowid=item.id WHERE kind='note' ORDER BY created DESC").fetchall()
    return [r for r in rows if (include_expired or not r[4] or r[4] >= now)
            and (not tag or tag in (r[2] or "").split(","))]


def same_tag_notes(i, tags, limit=4):
    """Other live notes sharing a tag with note i, most specific overlap first, so a new note that
    supersedes an old one (plans changed, date moved) gets the stale one noticed and forgotten."""
    mine = {t.strip().lower() for t in (tags or "").split(",") if t.strip()}
    if not mine:
        return []
    rows = [r for r in notes() if r[0] != i]
    freq = {}
    for r in rows:
        for t in {t.strip().lower() for t in (r[2] or "").split(",") if t.strip()}:
            freq[t] = freq.get(t, 0) + 1
    scored = []
    for r in rows:
        shared = mine & {t.strip().lower() for t in (r[2] or "").split(",") if t.strip()}
        if shared:
            scored.append((-sum(1 / freq[t] for t in shared), -r[3], r))
    scored.sort(key=lambda x: x[:2])
    return [r for *_, r in scored[:limit]]


def forget(i):
    con = db()
    if not con.execute("SELECT 1 FROM item WHERE id=? AND kind='note'", (i,)).fetchone():
        return False
    con.execute("DELETE FROM fts WHERE rowid=?", (i,))
    con.execute("DELETE FROM item WHERE id=?", (i,))
    con.commit()
    return True


# --- sessions ---------------------------------------------------------------------------------------
def _project_of(cwd, files=""):
    """The project a session belongs to: the folder under a project root it ran in, or for a
    session started from one of those roots, the folder most of its edits went to (else the cwd)."""
    roots = tuple(r.replace(HOME, "~").rstrip("/") + "/" for r in PROJECT_ROOTS)
    rel = cwd.replace(HOME, "~")
    for root in roots:
        if rel.startswith(root):
            return rel[len(root):].split("/")[0]
    counts = {}
    for f in (files or "").split("\n"):
        f = f.replace(HOME, "~")
        for root in roots:
            if f.startswith(root) and rel + "/" == root:
                name = f[len(root):].split("/")[0]
                counts[name] = counts.get(name, 0) + 1
    return max(counts, key=counts.get) if counts else (rel or "?")


def _when(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "?"


SESS_COLS = "id, tool, sid, cwd, title, started, ended, prompts, first, last, files, source"


def sessions(words="", project=None, tool=None, since=None, n=15, include_noise=False):
    con = db()
    where, args = [], []
    if not include_noise:
        where.append("noise = 0")
    if tool:
        where.append("tool = ?")
        args.append(tool)
    if since:
        where.append("ended >= ?")
        args.append(since)
    rows = con.execute(f"SELECT {SESS_COLS} FROM sess" + (" WHERE " + " AND ".join(where) if where else "")
                       + " ORDER BY ended DESC", args).fetchall()
    if project:
        rows = [r for r in rows if project.lower() in _project_of(r[3], r[10]).lower()
                or project.lower() in r[3].lower()]
    if not words:
        return [(r, None) for r in rows[:n]]
    # best transcript chunk per session, plus matches in the title or first prompt
    by_source = {r[11]: r for r in rows}
    hits, best = [], {}
    for any_word in (False, True):
        q = _fts_query(words, any_word)
        if not q:
            return []
        found = con.execute("SELECT item.source, bm25(fts, 4.0, 1.0, 2.0) AS score, "
                            "snippet(fts, 1, '[', ']', ' … ', 18) FROM fts JOIN item ON item.id = fts.rowid "
                            "WHERE fts MATCH ? AND item.kind = 'session' ORDER BY score LIMIT 400",
                            (q,)).fetchall()
        for source, score, snip in found:
            if source in by_source and source not in best:
                best[source] = (score, snip)
        terms = [t.lower() for t in re.findall(r"\w+", words)]
        for r in rows:
            text = f"{r[4]} {r[8]}".lower()
            if r[11] not in best and terms and all(t in text for t in terms):
                best[r[11]] = (-100.0, r[4])
        if len(best) >= min(3, n):
            break
    hits = sorted(best.items(), key=lambda kv: kv[1][0])[:n]
    return [(by_source[src], snip) for src, (score, snip) in hits]


def session_row(key):
    """A catalog row by its number (s123 / 123) or by (a prefix of) the session id."""
    con = db()
    key = key.lstrip("s#")
    row = None
    if key.isdigit():
        row = con.execute(f"SELECT {SESS_COLS} FROM sess WHERE id=?", (int(key),)).fetchone()
    if not row:
        row = con.execute(f"SELECT {SESS_COLS} FROM sess WHERE sid LIKE ? ORDER BY ended DESC",
                          (key + "%",)).fetchone()
    return row


def resume_hint(row):
    _, tool, sid, cwd = row[:4]
    if tool == "codex":
        return f"cd {cwd.replace(HOME, '~')} && codex resume {sid}"
    return f"cd {cwd.replace(HOME, '~')} && claude --resume {sid}"


def session_exchanges(row):
    """The prose of a session as (role, text) pairs, from its transcript."""
    _, text = _parse_session(row[11])
    out = []
    for block in text.split("\n\n"):
        if block.startswith(("USER: ", "AI: ")):
            role, _, body = block.partition(": ")
            out.append([role, body])
        elif out:
            out[-1][1] += "\n\n" + block
    return out


def grep_session(row, words, context=1, limit=8):
    terms = [t.lower() for t in re.findall(r"[\w.@#+-]+", words)]
    ex = session_exchanges(row)
    out = []
    for k, (role, body) in enumerate(ex):
        low = body.lower()
        if terms and all(t in low for t in terms):
            out.append([(j, ex[j][0], ex[j][1]) for j in range(max(0, k - context), min(len(ex), k + context + 1))])
            if len(out) >= limit:
                break
    return out


def projects(n=25):
    con = db()
    rows = con.execute("SELECT cwd, ended, title, tool, files FROM sess WHERE noise = 0 ORDER BY ended DESC").fetchall()
    out = {}
    for cwd, ended, title, tool, files in rows:
        p = out.setdefault(_project_of(cwd, files), {"last": ended, "count": 0, "titles": [], "tools": set()})
        p["count"] += 1
        p["tools"].add(tool)
        if len(p["titles"]) < 3 and title:
            p["titles"].append(title)
    return sorted(out.items(), key=lambda kv: -(kv[1]["last"] or 0))[:n]


# --- related: other items sharing an item's most distinctive words --------------------------------
STOP = set("""the and for that this with you your are was were have has had not but from they them
their what when where which who will would there here then than into about just like also some
any can could should does did done its it's i'm im dont don't yes no all one two out get got make
ai user assistant session""".split())


def related(i, n=8):
    con = db()
    row = con.execute("SELECT item.kind, item.source, fts.title, fts.body FROM item JOIN fts ON "
                      "fts.rowid = item.id WHERE item.id=?", (i,)).fetchone()
    if not row:
        return None
    kind, source, title, body = row
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9_]{2,}", (title + " " + body).lower()) if w not in STOP]
    if not words:
        return []
    counts = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    total = con.execute("SELECT count(*) FROM item").fetchone()[0] or 1
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_vocab USING fts5vocab(fts, 'row')")
    scored = []
    for w, c in counts.items():
        df = con.execute("SELECT doc FROM fts_vocab WHERE term=?", (w,)).fetchone()
        if df and 1 < df[0] < total * 0.05:  # in other items, but not everywhere
            scored.append((c / (df[0] ** 0.5), w))
    terms = [w for _, w in sorted(scored, reverse=True)[:12]]
    if not terms:
        return []
    q = " OR ".join(f'"{t}"' for t in terms)
    rows = con.execute("SELECT item.id, item.kind, item.title, item.source, "
                       "snippet(fts, 1, '[', ']', ' … ', 20), bm25(fts, 4.0, 1.0, 2.0) AS score "
                       "FROM fts JOIN item ON item.id = fts.rowid WHERE fts MATCH ? ORDER BY score LIMIT ?",
                       (q, n * 6)).fetchall()
    out, seen_sources = [], ({source} if source else set()) | _noise_sources(con)
    for r in rows:
        if r[0] == i or (r[3] and r[3] in seen_sources):
            continue
        seen_sources.add(r[3])
        out.append(r)
        if len(out) >= n:
            break
    return terms, out


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""


def _parse_date(s):
    return time.mktime(time.strptime(s, "%Y-%m-%d")) + 86399 if s else None


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]

    def opt(name, default=None):
        if name in args:
            k = args.index(name)
            val = args[k + 1] if k + 1 < len(args) else default
            del args[k:k + 2]
            return val
        return default

    def flag(name):
        if name in args:
            args.remove(name)
            return True
        return False

    if cmd == "search":
        n, kind = int(opt("-n", 8)), opt("--kind")
        rows = search(" ".join(args), n, kind)
        if not rows:
            print("no matches")
        for i, k, title, created, expires, snip, _ in rows:
            extra = f" (saved {_day(created)}" + (f", expires {_day(expires)}" if expires else "") + ")" \
                if k == "note" else ""
            print(f"#{i} [{k}] {title}{extra}\n    {' '.join(snip.split())}")
    elif cmd == "get":
        row = get(int(args[0].lstrip("#")))
        if not row:
            print("no such id")
            return 1
        kind, title, source, tags, created, body = row
        print(f"[{kind}] {title}\nsource: {source or '-'}  tags: {tags or '-'}  saved: {_day(created)}\n")
        print(body)
    elif cmd == "note":
        title, tags, exp = opt("--title", ""), opt("--tags", ""), opt("--expires")
        text = " ".join(args) or sys.stdin.read()
        if not text.strip():
            print("empty note")
            return 1
        i = note(text, title, tags, _parse_date(exp))
        print(f"saved note #{i}")
        near = same_tag_notes(i, tags)
        if near:
            print("notes with the same tags (if the new one makes any of these wrong or outdated, forget it):")
            for j, _, t, created, _, body in near:
                print(f"  #{j} {_day(created)} [{t}]: {' '.join(body.split())[:200]}")
    elif cmd == "notes":
        tag, everything = opt("--tag"), flag("--all")
        rows = notes(tag, everything)
        if not rows:
            print("no notes")
        for i, title, tags, created, expires, body in rows:
            print(f"#{i} {_day(created)}" + (f" (expires {_day(expires)})" if expires else "")
                  + (f" [{tags}]" if tags else "") + f": {' '.join(body.split())[:300]}")
    elif cmd == "forget":
        print("deleted" if forget(int(args[0].lstrip("#"))) else "no such note")
    elif cmd == "sessions":
        n, project, tool, since = int(opt("-n", 15)), opt("--project"), opt("--tool"), opt("--since")
        since = time.mktime(time.strptime(since, "%Y-%m-%d")) if since else None
        everything = flag("--all")
        rows = sessions(" ".join(args), project, tool, since, n, include_noise=everything)
        if not rows:
            print("no sessions")
        for r, snip in rows:
            sid, tool_, cwd, title, started, ended, prompts = r[0], r[1], r[3], r[4], r[5], r[6], r[7]
            print(f"s{sid} {_when(ended)} {tool_} {_project_of(cwd, r[10])} · {title or '(untitled)'} ({prompts} prompts)")
            if snip:
                print(f"    {' '.join(snip.split())[:220]}")
    elif cmd == "session":
        grep, tail = opt("--grep"), opt("--tail")
        row = session_row(args[0]) if args else None
        if not row:
            print("no such session")
            return 1
        rid, tool_, sid, cwd, title, started, ended, prompts, first, last, files, source = row
        if grep:
            hits = grep_session(row, grep)
            print(f"s{rid} {title}: {len(hits)} passage(s) matching {grep!r}" + (" (first 8)" if len(hits) >= 8 else ""))
            for passage in hits:
                print("---")
                for j, role, body in passage:
                    print(f"[{j}] {role}: {body[:1200]}")
            return 0
        if tail:
            for role, body in session_exchanges(row)[-int(tail) * 2:]:
                print(f"{role}: {body[:2000]}\n")
            return 0
        print(f"s{rid} [{tool_}] {title or '(untitled)'}\nproject: {_project_of(cwd, files)}  cwd: {cwd.replace(HOME, '~')}"
              f"\nfrom {_when(started)} to {_when(ended)}, {prompts} prompts\nresume: {resume_hint(row)}"
              f"\ntranscript: {source.replace(HOME, '~')}")
        if files:
            fl = files.split("\n")
            print(f"files touched ({len(fl)}): " + ", ".join(f.replace(HOME, "~") for f in fl[:15])
                  + (" …" if len(fl) > 15 else ""))
        print(f"\nfirst prompt: {' '.join(first.split())}\n\nlast reply: {' '.join(last.split())}")
    elif cmd == "projects":
        for name, p in projects():
            print(f"{name}: {p['count']} sessions ({'/'.join(sorted(p['tools']))}), last {_when(p['last'])}"
                  + (" · " + " | ".join(p["titles"]) if p["titles"] else ""))
    elif cmd == "related":
        res = related(int(args[0].lstrip("#")), int(opt("-n", 8))) if args else None
        if res is None:
            print("no such id")
            return 1
        if not res:
            print("nothing related found")
            return 0
        terms, rows = res
        print("shared terms: " + ", ".join(terms))
        for i, k, title, source, snip, _ in rows:
            print(f"#{i} [{k}] {title}\n    {' '.join(snip.split())}")
    elif cmd == "index":
        index(quiet=flag("--quiet"))
    elif cmd == "stats":
        con = db()
        for k, c in con.execute("SELECT kind, count(*) FROM item GROUP BY kind"):
            print(f"{k}: {c}")
        real, total = con.execute("SELECT sum(noise = 0), count(*) FROM sess").fetchone()
        print(f"session catalog: {real or 0} sessions (+{(total or 0) - (real or 0)} bot/temp runs hidden)")
        print(f"db size: {os.path.getsize(DB) / 1e6:.1f} MB")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
