"""React to a Discord message as the bot.

  discord_react.py <message id> <emoji> [channel id]   react to a message (default: the main channel)
  discord_react.py last <name> <emoji> [channel id]    react to that person's newest message there

<emoji> is a normal emoji (⭐), a server emoji as :name:, name:id or <:name:id>, or several
comma-separated."""
import json, re, sys, urllib.parse, urllib.request

import config

GENERAL = config.MAIN_CHANNEL


def call(method, url):
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": "Bot " + config.token(), "User-Agent": "DiscordBot (prominence-chan, 1)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def main(args):
    if len(args) >= 3 and args[0] == "last":
        who, emoji, channel = args[1].lower(), args[2], (args[3] if len(args) > 3 else GENERAL)
        msgs = call("GET", f"https://discord.com/api/v10/channels/{channel}/messages?limit=50")
        def names(m):
            a = m["author"]
            return {(a.get("global_name") or "").lower(), a["username"].lower(),
                    ((m.get("member") or {}).get("nick") or "").lower()}
        hit = next((m for m in msgs if any(who in n for n in names(m) if n)), None)
        if not hit:
            sys.exit(f"no recent message from {who}")
        mid, msg = hit["id"], hit
        print(f"reacting to {hit['author']['username']}: {hit['content'][:80]!r}")
    elif len(args) >= 2 and args[0] != "last":
        mid, emoji, channel = args[0], args[1], (args[2] if len(args) > 2 else GENERAL)
        msg = None
    else:
        sys.exit(__doc__)
    for e in emoji.split(","):
        e = resolve(e.strip(), channel)
        call("PUT", f"https://discord.com/api/v10/channels/{channel}/messages/{mid}"
                    f"/reactions/{urllib.parse.quote(e)}/@me")
        print("reacted", e)
        log(channel, mid, msg, e)


def log(channel, mid, msg, emoji):
    """Append to reactions.jsonl (same format bot.py writes) so later prompts remember the reaction."""
    try:
        import time
        msg = msg or call("GET", f"https://discord.com/api/v10/channels/{channel}/messages/{mid}")
        ch = call("GET", f"https://discord.com/api/v10/channels/{channel}")
        a = msg["author"]
        who = "YOU" if a.get("bot") and a["id"] == call("GET", "https://discord.com/api/v10/users/@me")["id"] \
            else ((msg.get("member") or {}).get("nick") or a.get("global_name") or a["username"])
        with open(config.data("reactions.jsonl"), "a") as f:
            f.write(json.dumps({"time": time.time(), "channel": channel, "guild": ch.get("guild_id"), "id": mid,
                                "author": who, "text": (msg.get("content") or "[attachment/empty]")[:200],
                                "emoji": emoji.split(":")[0], "why": "work mode"}) + "\n")
    except Exception as e:  # logging is best-effort; the reaction itself already happened
        print("couldn't log the reaction:", repr(e)[:100], file=sys.stderr)


def resolve(token, channel):
    """Emoji token -> what the API wants (name:id for custom ones, unicode as is)."""
    m = re.fullmatch(r"<a?:(\w+):(\d+)>", token)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    m = re.fullmatch(r":(\w+):", token)
    if m:
        guild = call("GET", f"https://discord.com/api/v10/channels/{channel}")["guild_id"]
        hit = next((e for e in call("GET", f"https://discord.com/api/v10/guilds/{guild}/emojis")
                    if e["name"].lower() == m.group(1).lower()), None)
        if not hit:
            sys.exit(f"no server emoji named {m.group(1)}")
        return f"{hit['name']}:{hit['id']}"
    return token


if __name__ == "__main__":
    main(sys.argv[1:])
