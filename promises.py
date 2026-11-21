"""Catch "i'll do X later" in what the bot texts the owner and schedule a one-off workflow for it, so a
deferred promise ("i'll look that up after your exam") doesn't just evaporate when the turn ends. Run with python3, from anywhere:

  promises.py check "<text>"     what the check would schedule for this text (dry run, prints JSON)
  promises.py watch [--source S] read a sent text on stdin and schedule a follow-up if it defers
                                 something (what bot.py's send_dm and dm.py say call, detached)

Cheap regex first; only texts that look like a deferral reach the haiku judge.
"""
import fcntl, json, os, re, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import workflows as wf  # noqa: E402
import config  # noqa: E402

LOCK = config.data(".promises.lock")  # one check at a time, so the second sees the first's workflow
LOG = config.data("promises.jsonl")
OWNER_NOTE = f" ({config.OWNER_NAME})" if config.OWNER_NAME else ""
SELF = r"(?:i'?ll|i will|ill|i'?m gonna|im gonna|lemme|let me|i can)"
LATER = (r"(?:after|later|tomorrow|tonight|in the morning|once|when|next time|by (?:mon|tue|wed|thu|fri|sat|sun|"
         r"tonight|tomorrow|the)|in (?:a|an|\d+) ?(?:bit|min|hour|hr|h\b|day)|check back|circle back|get back|"
         r"follow up|remind)")
PROMISE = re.compile(rf"\b{SELF}\b[^.!?\n]{{0,120}}\b{LATER}|\b{LATER}[^.!?\n]{{0,80}}\b{SELF}\b", re.I)


def looks_deferred(text):
    return bool(PROMISE.search(text or ""))


def judge(text):
    """Ask haiku whether `text` promises later work by the bot itself; None or {"at", "task", "name"}."""
    active = [w for w in wf.load()["workflows"].values() if w.get("enabled", True) and not w.get("finished")]
    try:
        recent = subprocess.run([sys.executable, os.path.join(HERE, "dm.py"), "recent", "12"],
                                capture_output=True, text=True, timeout=30).stdout[-3000:]
    except subprocess.TimeoutExpired:
        recent = ""
    prompt = (
        f"Now: {time.strftime('%a %Y-%m-%d %H:%M')} (local time).\n"
        f"A personal assistant bot just texted its owner{OWNER_NOTE} this:\n<<<\n" + text[:3000] + "\n>>>\n\n"
        "Their recent DM, for context (may already include that text):\n<<<\n" + recent + "\n>>>\n\n"
        "Already scheduled follow-ups and recurring jobs:\n"
        + ("\n".join(f"- {w['id']}: {wf.describe(w['schedule'])}, next {wf.fmt(w.get('next_run'))}: "
                     f"{w['task'][:200]}" for w in active) or "(none)") + "\n\n"
        "Does the text promise that the BOT will do something itself LATER, after this conversation turn "
        "ends (look something up after an exam, check back tomorrow, remind them tonight, finish X once Y "
        "happens on a known schedule)? Answer NO when: the work is being done right now in the same turn "
        "('on it', 'checking now', 'writing it in now'); it waits on the owner acting or replying first ('once "
        "you've paid send me the login and i'll book it', 'say the word and i'll...'); it's an offer, not a "
        "promise; or one of the scheduled jobs above already covers it.\n"
        "If NO, reply exactly: NO\n"
        "If YES, reply with only JSON: {\"at\": \"YYYY-MM-DD HH:MM\", \"name\": \"followup-<2-3 words>\", "
        "\"task\": \"...\"}. 'at' is when to do it: the time it names, or right after the event it waits for "
        "(guess sensibly from the context; if unknown, a few hours from now, not between 01:00 and 08:00 "
        "unless it's urgent). 'task' is written for a future agent with full tools and no memory of this chat: "
        "what was promised and in what context, to check first whether it's still needed or already done "
        "(dm.py recent), then do it and text the owner the result if it's still worth saying.")
    cmd = [config.CLAUDE, "-p", "--model", config.FAST_MODEL, "--tools", "", "--strict-mcp-config", "--no-session-persistence",
           "--disable-slash-commands", "--no-chrome", "--output-format", "json"]
    try:
        out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=90,
                             cwd=tempfile.gettempdir()).stdout
        ans = (json.loads(out).get("result") or "").strip()
    except (subprocess.TimeoutExpired, ValueError):
        return None
    m = re.search(r"\{.*\}", ans, re.S)
    if ans.upper().startswith("NO") or not m:
        return None
    try:
        plan = json.loads(m.group(0))
        when = wf.parse_time(plan["at"])
    except (ValueError, KeyError, TypeError):
        return None
    if not plan.get("task") or when < time.time() - 300 or when > time.time() + 14 * 86400:
        return None
    plan["name"] = re.sub(r"[^a-z0-9-]+", "-", (plan.get("name") or "followup").lower()).strip("-")[:40]
    return plan


def watch(text, source="owner"):
    if not looks_deferred(text):
        return None
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan = judge(text)
        if plan:
            subprocess.run([sys.executable, os.path.join(HERE, "workflows.py"), "add", plan["task"],
                            "--name", plan["name"], "--at", plan["at"], "--times", "1"],
                           capture_output=True, text=True, timeout=30)
        with open(LOG, "a") as f:
            f.write(json.dumps({"time": time.time(), "source": source, "text": text[:500], "plan": plan}) + "\n")
    return plan


def spawn(text, source="owner"):
    """Run `watch` in a detached process (it can take a minute) without blocking the sender."""
    if not looks_deferred(text):
        return
    try:
        p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "watch", "--source", source],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, text=True)
        p.stdin.write(text)
        p.stdin.close()
    except OSError:
        pass


def main(argv):
    if not argv or argv[0] not in ("check", "watch"):
        print(__doc__)
        return 1
    if argv[0] == "check":
        text = " ".join(argv[1:]) or sys.stdin.read()
        print(json.dumps({"looks_deferred": looks_deferred(text), "plan": judge(text) if looks_deferred(text) else None}))
        return 0
    source = argv[argv.index("--source") + 1] if "--source" in argv else "owner"
    plan = watch(sys.stdin.read(), source)
    print(json.dumps(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
