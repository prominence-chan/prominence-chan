"""Text the owner in the bot's DM while you're still working (run with python3, from anywhere).

  dm.py say "<text>"                 send a message now and keep working
  dm.py ask "<question>" [--wait M]  send a question and wait up to M minutes (default 15) for the
                                     reply, which is printed; if none comes, carry on with your best call
  dm.py react <emoji>                react to the owner's newest message instead of texting (a quick "on it",
                                     or a reaction when one fits); emoji: unicode, or :name: for a server emoji
  dm.py recent [N]                   the last N messages of your DM with the owner (default 20)
  dm.py file <path> ["<text>"]       send a file as an attachment (up to ~10 MB), with optional text;
                                     a bigger video is re-encoded to fit (split into parts if it's long)

Your final message is still posted at the end as usual, so don't repeat what you said here.
"""
import fcntl, json, math, os, subprocess, sys, tempfile, time, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import workflows  # noqa: E402  (shared outbox log)
import promises  # noqa: E402  ("i'll do X later" -> a one-off follow-up workflow)
import config  # noqa: E402

TOKEN = config.token()
OWNER = config.OWNER_ID
ASK_FILE = config.data("ask_pending.json")   # the bot routes the owner's next message here...
ANSWER_FILE = config.data("ask_answer.json")  # ...and writes it here
ASK_LOCK = config.data(".ask.lock")
SOURCE = os.environ.get("PROMINENCE_SOURCE", "owner")
UPLOAD_LIMIT = 10 * 1024 * 1024    # Discord's attachment cap for this bot
FIT_BYTES = int(9.3 * 1024 * 1024)  # what a re-encoded part aims for, leaving room for overshoot
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")
MIN_VIDEO_KBPS = 450                # below this a part looks like mush, so split instead
AUDIO_KBPS = 64


def api(method, path, body=None):
    req = urllib.request.Request("https://discord.com/api/v10" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers={
                                     "Authorization": f"Bot {TOKEN}", "Content-Type": "application/json",
                                     "User-Agent": "DiscordBot (https://claude.com, 1.0)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def channel():
    return api("POST", "/users/@me/channels", {"recipient_id": OWNER})["id"]


def send(text):
    ch = channel()
    last = None
    for piece in [text[k:k + 1900] for k in range(0, len(text), 1900)] or [""]:
        last = api("POST", f"/channels/{ch}/messages", {"content": piece})
    workflows.log_sent(SOURCE, text)
    promises.spawn(text, SOURCE)
    return last


def send_file(path, text=""):
    """Upload `path` as an attachment (multipart), with optional message text."""
    ch = channel()
    boundary = uuid.uuid4().hex
    name = os.path.basename(path)
    payload = json.dumps({"content": text[:1900], "attachments": [{"id": 0, "filename": name}]})
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n{payload}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + open(path, "rb").read() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://discord.com/api/v10/channels/{ch}/messages", method="POST", data=body, headers={
        "Authorization": f"Bot {TOKEN}", "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "DiscordBot (https://claude.com, 1.0)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    workflows.log_sent(SOURCE, (text + " " if text else "") + f"[file: {name}]")
    return out


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    return float(out)


def encode(path, out, start, length, video_kbps):
    """Two-pass H.264 of [start, start+length) at `video_kbps`, at most 720p, so the size lands on target."""
    log = os.path.join(os.path.dirname(out), "pass")
    common = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path,
              "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264", "-preset", "medium",
              "-b:v", f"{video_kbps}k", "-passlogfile", log]
    subprocess.run(common + ["-pass", "1", "-an", "-f", "mp4", os.devnull], check=True, timeout=1800)
    subprocess.run(common + ["-pass", "2", "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k",
                             "-movflags", "+faststart", out], check=True, timeout=1800)


def fit_video(path, workdir):
    """Re-encode a video too big to attach into one or more parts that each fit under the cap."""
    total = duration(path)
    budget_kbps = FIT_BYTES * 8 / 1000  # kilobits one part may hold
    parts = max(1, math.ceil(total * (MIN_VIDEO_KBPS + AUDIO_KBPS) / budget_kbps))
    length = total / parts
    stem = os.path.splitext(os.path.basename(path))[0]
    outs = []
    for k in range(parts):
        out = os.path.join(workdir, f"{stem}{f'_part{k + 1}' if parts > 1 else ''}.mp4")
        kbps = int(budget_kbps / length - AUDIO_KBPS)
        for _ in range(3):  # two-pass is close, but shrink and redo if it still came out over
            encode(path, out, k * length, length, kbps)
            if os.path.getsize(out) <= UPLOAD_LIMIT - 64 * 1024:
                break
            kbps = int(kbps * 0.85)
        else:
            raise RuntimeError(f"couldn't get {os.path.basename(out)} under the upload cap")
        outs.append(out)
    return outs


def send_any(path, text=""):
    """send_file, but a video over the cap is re-encoded (and split if needed) first. Returns what was sent."""
    if os.path.getsize(path) <= UPLOAD_LIMIT:
        send_file(path, text)
        return [os.path.basename(path)]
    if not path.lower().endswith(VIDEO_EXT):
        raise RuntimeError(f"{os.path.basename(path)} is {os.path.getsize(path) / 2**20:.1f} MB, over the "
                           "~10 MB cap, and only videos get shrunk automatically")
    with tempfile.TemporaryDirectory() as workdir:
        outs = fit_video(path, workdir)
        for k, out in enumerate(outs):
            label = text if k == 0 else ""
            if len(outs) > 1:
                label = (label + " " if label else "") + f"({k + 1}/{len(outs)})"
            send_file(out, label)
        return [os.path.basename(o) for o in outs]


def ask(question, wait_min):
    with open(ASK_LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # one open question at a time
        qid = str(uuid.uuid4())
        with open(ASK_FILE, "w") as f:
            json.dump({"id": qid, "pid": os.getpid(), "time": time.time()}, f)
        try:
            send(question)
            deadline = time.time() + wait_min * 60
            while time.time() < deadline:
                try:
                    ans = json.load(open(ANSWER_FILE))
                    if ans.get("id") == qid:
                        os.remove(ANSWER_FILE)
                        return ans["text"]
                except (OSError, ValueError):
                    pass
                time.sleep(2)
            return None
        finally:
            try:
                if json.load(open(ASK_FILE)).get("id") == qid:
                    os.remove(ASK_FILE)
            except (OSError, ValueError):
                pass


SERVER_EMOJI = config.SERVER_EMOJI  # name -> id, for :name: shorthands


def react(emoji):
    """React to the owner's newest message in the DM."""
    import urllib.parse
    m = __import__("re").fullmatch(r"<?a?:?(\w+):(\d+)>?|:(\w+):", emoji.strip())
    if m and m.group(3):
        if m.group(3) not in SERVER_EMOJI:
            print(f"unknown server emoji :{m.group(3)}:")
            return 1
        emoji = f"{m.group(3)}:{SERVER_EMOJI[m.group(3)]}"
    elif m:
        emoji = f"{m.group(1)}:{m.group(2)}"
    ch = channel()
    msgs = api("GET", f"/channels/{ch}/messages?limit=20")
    mine = next((x for x in msgs if x["author"]["id"] == OWNER), None)
    if not mine:
        print("no message of theirs to react to")
        return 1
    req = urllib.request.Request(f"https://discord.com/api/v10/channels/{ch}/messages/{mine['id']}/reactions/"
                                 f"{urllib.parse.quote(emoji)}/@me", method="PUT", headers={
                                     "Authorization": f"Bot {TOKEN}", "User-Agent": "DiscordBot (https://claude.com, 1.0)"})
    urllib.request.urlopen(req, timeout=15)
    print("reacted")
    return 0


def main(argv):
    if not argv or argv[0] not in ("say", "ask", "recent", "file", "react"):
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    if cmd == "recent":
        n = int(args[0]) if args else 20
        msgs = api("GET", f"/channels/{channel()}/messages?limit={min(n, 100)}")
        for m in reversed(msgs):
            who = "you" if m["author"].get("bot") else "owner"
            ts = time.strftime("%m-%d %H:%M", time.localtime(((int(m["id"]) >> 22) + 1420070400000) / 1000))
            print(f"{ts} {who}: {m.get('content', '')}")
        return 0
    if cmd == "react":
        if not args:
            print("need an emoji")
            return 1
        return react(args[0])
    if cmd == "file":
        if not args or not os.path.isfile(args[0]):
            print("need a file path")
            return 1
        try:
            sent = send_any(args[0], " ".join(args[1:]).strip())
        except (RuntimeError, subprocess.SubprocessError, ValueError) as e:
            print(f"not sent: {e}")
            return 1
        print("sent " + ", ".join(sent))
        return 0
    wait = 15.0
    if "--wait" in args:
        k = args.index("--wait")
        wait = float(args[k + 1])
        del args[k:k + 2]
    text = " ".join(args).strip() or sys.stdin.read().strip()
    if not text:
        print("nothing to send")
        return 1
    if cmd == "say":
        send(text)
        print("sent")
    else:
        ans = ask(text, wait)
        print(f"owner replied: {ans}" if ans is not None else
              f"(no reply after {wait:g} min; carry on with your best judgment and say what you assumed)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
