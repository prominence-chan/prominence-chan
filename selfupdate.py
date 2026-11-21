"""Change your own code (the bot lives in this folder) without taking yourself down. Run with
python3, from anywhere:

  selfupdate.py snapshot "<what you're about to change>"   copy the code aside first (always)
  selfupdate.py check                                      compile + pyflakes every .py here
  selfupdate.py restart "<what changed>"                   check, then queue a restart
  selfupdate.py rollback [<snapshot dir>]                  put the last snapshot back, queue a restart
  selfupdate.py log "<entry>"                              add a line to CHANGELOG.md (what + why)
  selfupdate.py changes [N]                                the last N changelog entries (default 10)
  selfupdate.py status                                     supervisor, pending restart, last snapshots

Never kill or relaunch bot.py yourself: your own reply is delivered by the running bot, and a
half-edited bot.py has crashed it before. `restart` leaves a request that the bot carries out by
itself once nothing is running (no DM reply or workflow in progress), and the supervisor rolls back
to the snapshot if the new code dies within 10 minutes. Test what you changed before restarting
(run the function in isolation, replay real inputs) because check only catches syntax errors and
undefined names. Changes to a helper script (context.py, workflows.py, ...) take effect on its next
run and don't need a restart.
"""
import glob, json, os, py_compile, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402

SNAPSHOTS = config.data("versions")
RESTART_FILE = config.data("restart_request.json")
TRIAL_FILE = config.data("restart_trial.json")
CHANGELOG = config.data("CHANGELOG.md")
KEEP = 30


def code_files():
    return sorted(glob.glob(os.path.join(HERE, "*.py")))


def snapshot(why):
    os.makedirs(SNAPSHOTS, exist_ok=True)
    path = os.path.join(SNAPSHOTS, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(path, exist_ok=True)
    for f in code_files():
        shutil.copy2(f, path)
    with open(os.path.join(path, "WHY.txt"), "w") as f:
        f.write(why + "\n")
    for old in sorted(glob.glob(os.path.join(SNAPSHOTS, "*")))[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return path


def latest_snapshot():
    snaps = sorted(d for d in glob.glob(os.path.join(SNAPSHOTS, "*")) if os.path.isdir(d))
    return snaps[-1] if snaps else None


def check():
    """Problems as a list of strings (empty = fine)."""
    problems = []
    for f in code_files():
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            problems.append(str(e))
    r = subprocess.run([sys.executable, "-m", "pyflakes", *code_files()], capture_output=True, text=True)
    if "No module named pyflakes" in r.stderr:
        problems.append("pyflakes missing: python3 -m pip install pyflakes")
    for line in r.stdout.splitlines():
        # unused imports and the like don't break anything; these do
        if any(k in line for k in ("undefined name", "syntax error", "invalid syntax",
                                   "referenced before assignment", "unexpected indent")):
            problems.append(line.replace(HERE + "/", ""))
    return problems


def queue_restart(why, snap):
    with open(RESTART_FILE + ".tmp", "w") as f:
        json.dump({"why": why, "snapshot": snap, "time": time.time(),
                   "by": os.environ.get("PROMINENCE_SOURCE", "owner")}, f)
    os.replace(RESTART_FILE + ".tmp", RESTART_FILE)


def supervised():
    # ps, not pgrep: pgrep -f misses it here
    r = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True)
    return any("supervise.py" in line and "python" in line for line in r.stdout.splitlines())


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "snapshot":
        print("snapshot:", snapshot(" ".join(rest) or "(no reason given)"))
    elif cmd == "check":
        problems = check()
        print("\n".join(problems) if problems else "ok")
        return 1 if problems else 0
    elif cmd in ("restart", "rollback"):
        if cmd == "rollback":
            snap = rest[0] if rest else latest_snapshot()
            if not snap or not os.path.isdir(snap):
                print("no snapshot to roll back to")
                return 1
            for name in os.listdir(snap):
                if name.endswith(".py"):
                    shutil.copy2(os.path.join(snap, name), os.path.join(HERE, name))
            why, snap_for_trial = f"rollback to {os.path.basename(snap)}", None
        else:
            why, snap_for_trial = " ".join(rest) or "(no reason given)", latest_snapshot()
        problems = check()
        if problems:
            print("not restarting, fix these first:\n" + "\n".join(problems))
            return 1
        if not supervised():
            print("warning: supervise.py isn't running, so the bot can't restart itself; the request "
                  "stays queued until it is")
        queue_restart(why, snap_for_trial)
        print("restart queued: the bot restarts once nothing is running (your reply goes out first)")
    elif cmd == "log":
        entry = " ".join(rest).strip()
        if not entry:
            print("need an entry")
            return 1
        new = not os.path.exists(CHANGELOG)
        with open(CHANGELOG, "a") as f:
            if new:
                f.write("# prominence-chan changelog\n\nWhat changed and why, newest last.\n\n")
            f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} ({os.environ.get('PROMINENCE_SOURCE', 'owner')}): "
                    f"{entry}\n")
        print("logged")
    elif cmd == "changes":
        n = int(rest[0]) if rest else 10
        try:
            lines = [l for l in open(CHANGELOG) if l.startswith("- ")]
        except OSError:
            lines = []
        print("".join(lines[-n:]).strip() or "no changes logged yet")
    elif cmd == "status":
        print("supervisor:", "running" if supervised() else "NOT running")
        for label, path in (("pending restart", RESTART_FILE), ("update on trial", TRIAL_FILE)):
            if os.path.exists(path):
                print(f"{label}:", open(path).read().strip())
        snaps = sorted(glob.glob(os.path.join(SNAPSHOTS, "*")))[-5:]
        for s in snaps:
            why = open(os.path.join(s, "WHY.txt")).read().strip() if os.path.exists(os.path.join(s, "WHY.txt")) else ""
            print("snapshot", os.path.basename(s), "-", why)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
