"""Settings shared by bot.py and its helper scripts.

Read from config.json next to this file (or the file named by PROMINENCE_CONFIG); an environment
variable PROMINENCE_<KEY> (e.g. PROMINENCE_OWNER_ID) overrides any key. The bot token comes from
DISCORD_TOKEN, else "token" in config.json, else a token.txt in the data dir. See
config.example.json for every key. Importing this module never touches the network.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("PROMINENCE_CONFIG") or os.path.join(HERE, "config.json")

try:
    with open(CONFIG_FILE) as _f:
        _cfg = json.load(_f)
except FileNotFoundError:
    _cfg = {}
except ValueError as _e:
    sys.exit(f"{CONFIG_FILE} isn't valid JSON: {_e}")


def get(key, default=None):
    """config.json's `key`, overridden by the environment variable PROMINENCE_<KEY>."""
    env = os.environ.get("PROMINENCE_" + key.upper())
    if env is not None and env != "":
        return env
    value = _cfg.get(key)
    return default if value in (None, "") else value


def get_list(key, default=()):
    """A list setting: a JSON list in config.json, or comma-separated in the env var."""
    value = get(key, list(default))
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value if str(v).strip()]


def get_dict(key):
    """A mapping setting: a JSON object in config.json, or JSON text in the env var."""
    value = get(key, {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            sys.exit(f"PROMINENCE_{key.upper()} isn't a JSON object")
    if not isinstance(value, dict):
        sys.exit(f"setting {key} should be a JSON object")
    return {str(k): v for k, v in value.items() if not str(k).startswith("_")}


DATA_DIR = os.path.abspath(os.path.expanduser(get("data_dir") or HERE))  # state, logs, the index db


def data(name):
    """Path of a state file in the data dir (created on first use)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name)


OWNER_ID = str(get("owner_id", ""))              # the Discord user who runs the bot (full work mode)
MAIN_CHANNEL = str(get("main_channel_id", ""))   # the group chat the bot lives in ("#general" in prompts)
GUEST_CHANNELS = set(get_list("guest_channel_ids"))  # where anyone's "!" request gets web-only work mode
GUEST_CODE = get_dict("guest_code_channels")     # channel id -> settings: work mode with code access (see README)
GUEST_CODE_GUILDS = get_dict("guest_code_guilds")  # server id -> the same settings, for every text channel in it
PASSIVE_CHANNELS = set(get_list("passive_channel_ids"))  # other servers' main chats it hangs out in
PING_ROLES = get_list("ping_role_ids")           # pinging one of these roles counts as pinging the bot
PARTNER = get_dict("partner_agent")              # optional: a channel shared with another team's agent (see README)
NAME = get("name", "prominence-chan")
NAMES = {"dry texter": NAME,                     # server nickname per persona
         "anime girl": get("anime_name", "athena-chan")}
OWNER_NAME = get("owner_name", "")               # optional: what the bot may call the owner
OWNER = OWNER_NAME or "the owner"
SERVER_EMOJI = {k: str(v) for k, v in get_dict("server_emoji").items()}  # name -> id, for :name: shorthands
WORK_WHERE = get("work_label", "the owner's computer")  # shown after the activity while an agent works
GIT_NAME, GIT_EMAIL = get("git_name", ""), get("git_email", "")  # optional git identity for the agents' commits
CLAUDE = os.path.expanduser(get("claude_bin", "claude"))
CLAUDE_MODEL = get("claude_model", "opus")
FAST_MODEL = get("fast_model", "haiku")          # quick yes/no and routing calls, and the gate's fallback
GATE_MODEL = get("gate_model", "gpt-6-luna")     # cheap model (via the Codex CLI) for yes/no decisions
# GPT-6 models are only served to recent Codex clients; an old npm `codex` on PATH may be refused,
# so prefer the copy bundled with the ChatGPT desktop app when it's there
_APP_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
CODEX = os.path.expanduser(get("codex_bin") or (_APP_CODEX if os.path.exists(_APP_CODEX) else "codex"))
DOC_ROOTS = [os.path.expanduser(p) for p in get_list("doc_roots", ["~/Desktop", "~/Documents"])]
PROJECT_ROOTS = [os.path.expanduser(p) for p in get_list("project_roots", ["~/Desktop/Development", "~/dev"])]


def token():
    """The bot token; exits with a clear message if there isn't one."""
    tok = os.environ.get("DISCORD_TOKEN") or _cfg.get("token")
    if not tok:
        for path in (data("token.txt"), os.path.join(HERE, "token.txt")):
            if os.path.exists(path):
                tok = open(path).read().strip()
                break
    if not tok:
        sys.exit("no Discord bot token: set DISCORD_TOKEN, or \"token\" in config.json "
                 "(copy config.example.json to config.json)")
    return tok.strip()


def require(*keys):
    """Exit with a clear message unless these settings are present (for bot.py and the scripts)."""
    missing = [k for k in keys if not get(k)]
    if missing:
        sys.exit(f"missing setting(s): {', '.join(missing)}. Put them in {CONFIG_FILE} (copy "
                 "config.example.json) or set PROMINENCE_<KEY> environment variables")
