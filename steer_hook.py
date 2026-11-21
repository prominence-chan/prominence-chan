"""PostToolUse hook for the owner's agent: hands it any messages the owner sent while it was working that the bot
routed to it as a steer (steer_<session>.json), so the running task can adjust mid-way instead of finishing
first. Messages it never picks up (no more tool calls before the reply) are left for the bot to queue."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402


def main():
    try:
        sid = json.load(sys.stdin).get("session_id") or ""
    except ValueError:
        return
    path = config.data(f"steer_{sid}.json")
    if not sid or not os.path.exists(path):
        return
    taken = path + f".taken.{os.getpid()}"
    try:
        os.replace(path, taken)  # atomic: the bot never appends to a file we're halfway through reading
    except OSError:
        return
    try:
        items = json.load(open(taken))
    except ValueError:
        items = []
    with open(config.data("steer_consumed.jsonl"), "a") as f:
        for it in items:
            f.write(json.dumps({"session": sid, "id": it.get("id"), "time": time.time()}) + "\n")
    os.remove(taken)
    if not items:
        return
    dms = [it for it in items if not it.get("from")]
    thread = [it for it in items if it.get("from")]  # from the Discord thread on a work-mode request
    parts = []
    if dms:
        parts.append("[The owner just texted you while you're working on this; it's for the task you're on right now. "
                     "Take it into account from here (change course if it asks you to), and cover it in your final reply.]\n"
                     + "\n\n".join(it["text"] for it in dms))
    if thread:
        parts.append("[New messages about the request you're working on (from its thread, or the channel it was asked in; "
                     "your reply goes where the request was). They're steering this task: take them into account from here, change course if "
                     "they ask, and cover them in your final reply. Your usual rules about who you take orders from "
                     "and what you'd do for them still apply.]\n" + "\n\n".join(it["text"] for it in thread))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n\n".join(parts)}}))


main()
