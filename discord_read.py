"""Read-only Discord lookups for the owner's DM agent (run with python3, from anywhere).

  discord_read.py general [N]            last N messages in the main channel (default 50)
  discord_read.py channel <id> [N]       last N messages in any channel or DM the bot can see
  discord_read.py channels               the server's text channels (id, name)
  discord_read.py search <text> [N]      search the crawled index of the server's public channels
  discord_read.py dms [N]                the bot's recent DM conversations with other people
  discord_read.py talkstats [--days N] [--dms] [--top N]
                                         who talks to the bot most: counts every message aimed at it
                                         (pings incl. the ping roles, replies to it, "!" requests
                                         where "!" works, and passive replies: a plain bot message
                                         credits the last human message before it), per #general and
                                         across all server channels + active threads. --dms adds the
                                         bot's DMs as a separate column (private: don't post anyone's
                                         DM count in public, only the server numbers). Takes a few
                                         minutes for the full history; --days limits it.
"""
import collections, json, os, re, sys, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

TOKEN = config.token()
GENERAL = config.MAIN_CHANNEL
ADMIN_ID = config.OWNER_ID
PING_ROLES = set(config.PING_ROLES)  # pinging one of these roles counts as pinging the bot
BANG_CHANNELS = set(config.GUEST_CHANNELS) | set(config.GUEST_CODE)  # anyone's "!" works here
EPOCH_MS = 1420070400000
_me = {}


def bot_id():
    if "id" not in _me:
        _me["id"] = get("/users/@me")["id"]
    return _me["id"]


def get(path):
    req = urllib.request.Request("https://discord.com/api/v10" + path, headers={
        "Authorization": f"Bot {TOKEN}", "User-Agent": "DiscordBot (https://claude.com, 1.0)"})
    for attempt in range(5):  # the running bot shares this token's rate limit: wait out 429s
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 4:
                raise
            try:
                wait = float(json.load(e).get("retry_after", 1))
            except ValueError:
                wait = 1
            time.sleep(min(wait, 10) + 0.5)


def when(snowflake):
    ts = ((int(snowflake) >> 22) + 1420070400000) / 1000
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%m-%d %H:%M")


def show(channel, n):
    msgs, before = [], ""
    while len(msgs) < n:
        batch = get(f"/channels/{channel}/messages?limit={min(100, n - len(msgs))}{before}")
        if not batch:
            break
        msgs += batch
        before = f"&before={batch[-1]['id']}"
    for m in reversed(msgs):
        a = m["author"]
        who = a.get("global_name") or a["username"]
        files = " ".join(f"[file: {x['url']}]" for x in m.get("attachments", []))
        print(f"{when(m['id'])} {who} ({a['username']}): {m.get('content', '')} {files}".rstrip())


def post(path, body):
    req = urllib.request.Request("https://discord.com/api/v10" + path, method="POST",
                                 data=json.dumps(body).encode(), headers={
        "Authorization": f"Bot {TOKEN}", "User-Agent": "DiscordBot (https://claude.com, 1.0)",
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def aimed_at_bot(m, channel, dm):
    """Whether a human message was directed at the bot (not counting passive replies)."""
    if dm:
        return True
    BOT_ID = bot_id()
    if any(u["id"] == BOT_ID for u in m.get("mentions", [])) or PING_ROLES & set(m.get("mention_roles", [])):
        return True
    if ((m.get("referenced_message") or {}).get("author") or {}).get("id") == BOT_ID:
        return True
    text = re.sub(rf"^\s*<@!?{BOT_ID}>\s*", "", m.get("content") or "")
    return text.startswith("!") and (channel in BANG_CHANNELS or m["author"]["id"] == ADMIN_ID)


def scan_talk(channel, dm, floor):
    """Message ids (with authors) aimed at the bot in one channel, back to snowflake `floor`."""
    BOT_ID = bot_id()
    msgs, before = [], ""
    while True:
        try:
            batch = get(f"/channels/{channel}/messages?limit=100{before}")
        except urllib.error.HTTPError:
            break  # no access to this channel
        if not batch:
            break
        msgs += [m for m in batch if int(m["id"]) > floor]
        before = f"&before={batch[-1]['id']}"
        if int(batch[-1]["id"]) <= floor or len(batch) < 100:
            break
    msgs.sort(key=lambda m: int(m["id"]))
    hits, last_human = {}, None
    for m in msgs:
        a = m["author"]
        if a["id"] == BOT_ID:
            ref = m.get("referenced_message") or {}
            ra = ref.get("author") or {}
            if ref and ra.get("id") != BOT_ID and not ra.get("bot"):
                hits[ref["id"]] = ra
            elif not ref and last_human:  # passive reply: credit whoever spoke right before
                hits[last_human["id"]] = last_human["author"]
            continue
        if a.get("bot"):
            continue
        last_human = m
        if aimed_at_bot(m, channel, dm):
            hits[m["id"]] = a
    return hits, len(msgs)


def talkstats(args):
    days = int(args[args.index("--days") + 1]) if "--days" in args else None
    top = int(args[args.index("--top") + 1]) if "--top" in args else 15
    floor = int(bot_id())  # nothing before the bot existed
    if days:
        floor = max(floor, int((time.time() * 1000 - days * 86400000) - EPOCH_MS) << 22)
    guild = get(f"/channels/{GENERAL}")["guild_id"]
    chans = [c["id"] for c in get(f"/guilds/{guild}/channels") if c["type"] in (0, 5)]
    chans += [t["id"] for t in (get(f"/guilds/{guild}/threads/active") or {}).get("threads", [])]
    dms = set()
    if "--dms" in args:
        try:
            dms = {json.loads(l)["channel"] for l in open(config.data("dm_log.jsonl"))}
        except OSError:
            pass
        try:
            dms.add(post("/users/@me/channels", {"recipient_id": ADMIN_ID})["id"])
        except Exception:
            pass
    names, gen, srv, dmc = {}, collections.Counter(), collections.Counter(), collections.Counter()
    total = [0]
    lock = threading.Lock()

    def work(item):
        channel, dm = item
        hits, n = scan_talk(channel, dm, floor)
        with lock:
            total[0] += n
            for a in hits.values():
                names.setdefault(a["id"], a.get("global_name") or a["username"])
                (dmc if dm else srv)[a["id"]] += 1
                if channel == GENERAL:
                    gen[a["id"]] += 1

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(work, [(c, False) for c in chans] + [(d, True) for d in dms]))
    since = "since the bot joined" if not days else f"last {days} days"
    print(f"scanned {total[0]} messages in {len(chans)} server channels" + (f" + {len(dms)} DMs" if dms else "")
          + f", {since}. counts = pings + replies to the bot + \"!\" requests + passive replies")
    for title, counter in (("#general", gen), ("all server channels", srv)) + ((("DMs (private)", dmc),) if dms else ()):
        print(f"\n{title}:")
        for i, (uid, n) in enumerate(counter.most_common(top), 1):
            print(f"  {i}. {names[uid]} ({uid}): {n}")


def main(args):
    cmd = args[0] if args else "general"
    if cmd == "general":
        show(GENERAL, int(args[1]) if len(args) > 1 else 50)
    elif cmd == "channel":
        show(args[1], int(args[2]) if len(args) > 2 else 50)
    elif cmd == "channels":
        guild = get(f"/channels/{GENERAL}")["guild_id"]
        for c in get(f"/guilds/{guild}/channels"):
            if c["type"] in (0, 5):
                print(c["id"], "#" + c["name"])
    elif cmd == "search":
        text, n = args[1].lower(), int(args[2]) if len(args) > 2 else 30
        index = json.load(open(config.data("message_index.json")))
        hits = [m for m in index["messages"].values() if text in (m[3] or "").lower()]
        for mid, channel, author, body in sorted(hits, key=lambda m: int(m[0]))[-n:]:
            print(f"{when(mid)} <#{channel}> {author}: {body}")
    elif cmd == "dms":
        n = int(args[1]) if len(args) > 1 else 50
        try:
            lines = open(config.data("dm_log.jsonl")).read().splitlines()[-n:]
        except OSError:
            lines = []
        for line in lines:
            e = json.loads(line)
            print(f"{e['time']} [{e['channel']}] {e['user']}: {e['said']!r} -> bot: {e['reply']!r}")
    elif cmd == "talkstats":
        talkstats(args[1:])
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
