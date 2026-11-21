"""Send a message as the bot. Run with python3, from anywhere:

  send.py "<text>"                          post in the main channel
  send.py "<text>" --channel <id|name>      post in another channel (id, or a channel name)
  send.py "<text>" --reply <message id>     post it as a reply to that message (no ping)
  send.py --dm <user id|name> "<text>"      DM someone
  echo "<text>" | send.py -                 read the text from stdin (multi-line is fine)

A blank line in the text splits it into separate messages, like the bot's own replies."""
import json, os, sys, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

GENERAL = config.MAIN_CHANNEL
API = "https://discord.com/api/v10"
HEADERS = {"Authorization": "Bot " + config.token(),
           "User-Agent": "DiscordBot (fable-bot, 1)", "Content-Type": "application/json"}


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, headers=HEADERS,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def guild():
    return call("GET", f"/channels/{GENERAL}")["guild_id"]


def channel_id(arg):
    if arg.isdigit():
        return arg
    name = arg.lstrip("#").lower()
    # two channels can share a name: take the one that was posted in most recently
    hits = [c for c in call("GET", f"/guilds/{guild()}/channels") if c.get("name", "").lower() == name]
    hit = max(hits, key=lambda c: int(c.get("last_message_id") or 0), default=None)
    if not hit:
        sys.exit(f"no channel named {arg}")
    return hit["id"]


def user_id(arg):
    if arg.isdigit():
        return arg
    found = call("GET", f"/guilds/{guild()}/members/search?query={urllib.parse.quote(arg)}&limit=5")
    if not found:
        sys.exit(f"no member matching {arg}")
    return found[0]["user"]["id"]


def main(args):
    channel, reply, dm, text = GENERAL, None, None, None
    rest = []
    k = 0
    while k < len(args):
        a = args[k]
        if a in ("--channel", "--reply", "--dm") and k + 1 < len(args):
            if a == "--channel":
                channel = channel_id(args[k + 1])
            elif a == "--reply":
                reply = args[k + 1]
            else:
                dm = user_id(args[k + 1])
            k += 2
            continue
        rest.append(a)
        k += 1
    if not rest:
        sys.exit(__doc__)
    text = sys.stdin.read() if rest == ["-"] else " ".join(rest)
    if dm:
        channel = call("POST", "/users/@me/channels", {"recipient_id": dm})["id"]
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    for i, part in enumerate(parts):
        body = {"content": part[:2000], "allowed_mentions": {"parse": ["users"], "replied_user": False}}
        if reply and i == 0:
            body["message_reference"] = {"message_id": reply, "fail_if_not_exists": False}
        sent = call("POST", f"/channels/{channel}/messages", body)
        print("sent", sent["id"])


if __name__ == "__main__":
    main(sys.argv[1:])
