# prominence-chan

A Discord group-chat bot driven by the [Claude Code](https://claude.com/claude-code) and Codex CLIs.
It plays "Fable 6.7", a made-up model with a deadpan dry-texter persona (or, after `/toggle`, a
bubbly anime-girl one), and lives in one main channel of a friend-group server.

- **Chat**: a cheap Codex model gates every new message (reply or stay quiet; Claude's fast model
  stands in when Codex is unavailable); Claude writes the replies. Pings (of the bot, or of a role
  it holds or one in `ping_role_ids`) and messages starting with `!` always get an answer. It
  reads images, short videos (as a frame grid), text files, zips, forwarded messages, embeds and
  Components V2 messages, reacts with emoji, sets its own custom status, shows fake
  "Playing"/"Listening" activities and online/idle/dnd moods, remembers older chat through a local
  search index, and can leave itself private context notes on its own messages.
- **Fake slash commands**: `/help`, `/model`, ... are parodied Claude Code output. Real ones:
  `/imitate <user>` (talks as that person, learned from their messages), `/reset`, `/toggle`,
  `/clear`, `/logout` / `/login`, `/optout` / `/optin`, `/collect` (anonymous submissions by DM,
  plus a Colonel Blotto "castle game"), and `/admin`, `/op`, `/unop` for standing admin
  instructions.
- **Owner work mode**: the owner (the Discord user in `owner_id`) gets a full Claude Code agent, in
  DMs and when they ask for real work in the server. A classifier decides whether a ping or `!`
  message is a task or just chat (Codex gate model first, Claude's fast model as the fallback).
  Messages sent while the agent works are routed to it mid-run (a `PostToolUse` hook,
  `steer_hook.py`) or to a parallel agent if they're unrelated; edits to a request count too.
  Replies go through a voice pass (`revoice`), and public-channel replies are also trimmed down
  when they run long. Standing instructions the owner gives are saved as context notes tagged
  `rule` and injected into every work-mode prompt. Plain `@name` mentions in agent replies are
  turned into real pings.
- **Work-mode threads**: a thread opened on a work-mode request belongs to that task. While it
  runs, anything said in the thread steers it; afterwards, a new message there resumes the same
  agent session.
- **Workflows**: scheduled one-off or recurring runs (`workflows.py`). Each run is a fresh agent
  that decides whether anything is worth texting the owner; its text goes through a finalize pass
  (strips narration about the run, fixes the voice) before it's sent. "i'll do X later" in a text to
  the owner schedules its own follow-up (`promises.py`). Runs that fail during an API outage are
  retried, and workflow times follow a machine clock that's been set wrong.
- **Tools for the agents**: long-term memory and a search index over local docs, projects and past
  Claude Code / Codex sessions (`context.py`), texting the owner mid-task, files and reactions
  included (`dm.py`), reading the server (`discord_read.py`), reacting (`discord_react.py`),
  posting (`send.py`) and moderation on request (`mod.py`).
- **Guest work mode**: in the channels in `guest_channel_ids`, anyone's `!` request gets a web-only
  agent (WebSearch/WebFetch, nothing else), rate-limited. Channels in `guest_code_channels` (or
  every channel of a server in `guest_code_guilds`) get an agent with code access in a configured
  folder, one run at a time per channel; a request that comes in while one runs waits for it.
  People who keep sending troll requests there are quietly ignored for a while.
- **Partner agent channel** (optional): a channel shared with another team's agent bot. Every
  message there and in its threads is read without a ping; replies follow a simple protocol (one
  message per turn, mention the partner, SKIP or a reaction when nothing is needed, long answers as
  an attachment, one topic per thread), with a loop guard.
- **Other servers**: channels in `passive_channel_ids` are joined like the main channel (the gate
  decides, pings are answered). It also plays Mudae's word games if Mudae is in the server.
- **DMs**: anyone can DM it for a one-on-one chat in the current persona (no tools), with a daily
  message budget per person.
- **Watchdogs**: low disk space starts a cleanup agent and texts the owner; a machine clock that's
  off gets one text; a dead Claude login gets one text.
- **Self-improving**: the owner's agent may edit its own code with `selfupdate.py` (snapshot,
  check, changelog, queued restart); `supervise.py` restarts it and rolls back to the snapshot if
  the new code dies within 10 minutes. Replies can flag a capability gap or an idea, and a
  periodic "idea watch" reads the chat for feature requests; both start self-improvement runs.

> **Warning:** owner work mode runs `claude -p --permission-mode bypassPermissions` in your home
> directory. The model gets **full, unsupervised access to the host machine**: any command, any
> file, your logged-in CLIs. Only the account in `owner_id` can trigger it, but anyone who controls
> that Discord account (or the bot token) controls your machine. Guest code channels also run with
> bypassed permissions in their folder; only list channels whose members you trust. Run it on a
> machine or user account you're comfortable handing over, and keep the token secret.

## Requirements

- macOS (image handling uses `sips`, doc indexing uses `textutil`; `tools/mouse.py` needs Quartz)
- Python 3.10+ with `websockets` (`pip install -r requirements.txt`)
- The `claude` CLI, logged in
- The `codex` CLI, logged in, with access to the gate model (default `gpt-6-luna`); without it the
  gates fall back to Claude's fast model
- Optional: `ffmpeg`/`ffprobe` (videos), `pyflakes` (used by `selfupdate.py check`)

## Setup

1. Create a bot at <https://discord.com/developers/applications>, enable the **Message Content**,
   **Server Members** and (optionally, for the owner's presence) **Presence** intents, and invite
   it to your server with permission to read and send messages, add reactions, create threads,
   manage nicknames (for `/imitate`), manage channel permissions (the short mute during
   `/imitate`) and, if you want the moderation features, moderate members and manage roles.
2. `cp config.example.json config.json` and fill it in (Discord developer mode gives you ids
   with right click > Copy ID):

   | key | meaning |
   | --- | --- |
   | `token` | bot token (or set `DISCORD_TOKEN`) |
   | `owner_id` | your Discord user id: the only account with work mode, `/op` and `/unop` |
   | `owner_name` | optional: your name, so the bot knows who "the owner" is in chat logs |
   | `main_channel_id` | the channel the bot lives in (called "#general" in its prompts) |
   | `guest_channel_ids` | channels where anyone's `!` request gets web-only work mode |
   | `guest_code_channels` | channel id -> settings for work mode with code access (below) |
   | `guest_code_guilds` | server id -> the same settings, applied to every text channel there |
   | `passive_channel_ids` | other servers' main chats the bot hangs out in |
   | `ping_role_ids` | roles whose ping counts as pinging the bot (its own roles always do) |
   | `partner_agent` | optional partner agent channel (below) |
   | `server_emoji` | `{"name": "emoji id"}`: custom emoji the agents may use as `:name:` |
   | `name`, `anime_name` | the bot's server nickname in each persona |
   | `claude_bin`, `claude_model`, `fast_model` | the Claude Code CLI, its main model, and the quick one for routing and fallbacks |
   | `codex_bin`, `gate_model` | the Codex CLI and the cheap gate model |
   | `git_name`, `git_email` | optional git identity for commits the agents make |
   | `work_label` | shown after the activity while an agent works ("▸ running commands · ...") |
   | `data_dir` | where state, logs and the index db go (default: this folder) |
   | `doc_roots`, `project_roots` | folders `context.py` indexes for docs and projects |

   Any key can also be set as an environment variable `PROMINENCE_<KEY>` (lists comma-separated,
   objects as JSON), and `PROMINENCE_CONFIG` points to a different config file. Keys of
   `guest_code_channels`, `guest_code_guilds` and `server_emoji` that start with `_` are ignored,
   so the example entry does nothing until you replace `_CHANNEL_ID` with a real id.

   A `guest_code_channels` entry:

   | field | meaning |
   | --- | --- |
   | `dir` | the folder the agent works in (default: `guest-workspace` in the data dir) |
   | `extra_dirs` | more folders it may use |
   | `about`, `game` | what the channel works on, and its short name, for the prompt |
   | `rules` | `"code"`: trusted teammates, flexible code access; `"scratch"`: everyday tasks inside `dir` only, with the rest of the machine off limits |
   | `deny` | folders the file tools can't read or edit (`"scratch"` adds your personal folders) |
   | `roles` | role ids whose members get work mode there (empty: everyone) |
   | `limited` | apply the per-person cooldown and daily cap (default on for `"scratch"`) |
   | `always_work` | every summons is work; a router folds follow-ups into the running task |
   | `timeout_min` | minutes a run may take |

   `partner_agent`: `{"channel_id": "...", "bot_id": "...", "name": "...", "notes": "...",
   "work": {...}}`. `notes` is appended to the protocol (house rules for that collaboration);
   `work` is a `guest_code_channels`-style entry for the channel, unless the channel is already
   listed there.
3. Start it under the supervisor (it restarts the bot after self-updates and crashes):

   ```sh
   pip install -r requirements.txt
   QUIET_BOOT=1 nohup python3 supervise.py >> bot.log 2>&1 &
   ```

   `python3 bot.py` runs it directly, without restarts. Leave out `QUIET_BOOT=1` to post a boot
   banner in the main channel.

In the owner's DM, `/stop` kills the running agents, `/new` starts fresh agent sessions and
`/workflows` lists scheduled workflows. Messages starting with `?` are invisible to the bot.

## License

MIT
