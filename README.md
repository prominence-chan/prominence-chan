<div align="center">

# prominence-chan

**A Discord group-chat bot with a personality, powered by Claude Code and Codex.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey?logo=apple)](#requirements)
[![Discord bot](https://img.shields.io/badge/discord-bot-5865F2?logo=discord&logoColor=white)](https://discord.com/developers/applications)
[![Claude Code](https://img.shields.io/badge/powered%20by-Claude%20Code-D97757)](https://claude.com/claude-code)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

prominence-chan hangs out in your friend group's server as "Fable 6.7", a made-up AI model with a deadpan,
dry-texter persona (or a bubbly anime-girl one, if you `/toggle` it). It reads the room, joins in when it has
something to say, and stays quiet when it doesn't. For its owner it doubles as a full coding and research agent
you can text from Discord.

## Features

- **Group chat that feels natural.** It decides for itself when to reply, reacts with emoji, sets its own status
  and remembers older conversations. Ping it or start a message with `!` and it always answers.
- **Two personas.** A terse dry texter by default, an upbeat anime girl after `/toggle`, and `/imitate <user>` to
  talk like someone in the server.
- **Parody slash commands.** `/help`, `/model` and friends look like Claude Code output; `/reset`, `/clear`,
  `/admin` and others actually do things.
- **Owner work mode.** The owner gets a real Claude Code agent in DMs (and via `!` in the server): scheduled
  workflows, long-term memory, mid-task check-ins and server moderation on request.
- **Guest mode.** In channels you choose, anyone can ask for a web-research task, or real work inside a project
  folder you point it at.
- **DMs.** Anyone can DM it for a one-on-one chat.
- **Self-updating.** It can improve its own code, with a supervisor that rolls back changes that crash.

> [!WARNING]
> Owner work mode runs Claude Code with `--permission-mode bypassPermissions` in your home directory: the model
> gets **full, unsupervised access to the host machine**. Only the account in `owner_id` can trigger it, but anyone
> who controls that Discord account (or the bot token) controls your machine. Run it somewhere you're comfortable
> handing over, and keep the token secret.

## Quick start

```sh
git clone https://github.com/prominence-chan/prominence-chan.git && cd prominence-chan
pip install -r requirements.txt
cp config.example.json config.json     # fill in your token and ids (see below)
QUIET_BOOT=1 nohup python3 supervise.py >> bot.log 2>&1 &
```

`python3 bot.py` runs it directly without the supervisor. Leave out `QUIET_BOOT=1` to post a boot banner.

### Requirements

- macOS, Python 3.10+
- The [`claude`](https://claude.com/claude-code) and `codex` CLIs, both logged in
- A Discord bot with the **Message Content** and **Server Members** intents, invited with permission to read and
  send messages, add reactions and manage nicknames (plus moderation permissions if you want those features)

### Configuration

<details>
<summary><code>config.json</code> keys</summary>

| key | meaning |
| --- | --- |
| `token` | bot token (or set `DISCORD_TOKEN`) |
| `owner_id` | your Discord user id: the only account with work mode and admin commands |
| `main_channel_id` | the channel the bot lives in |
| `guest_channel_ids` | channels where anyone's `!` request gets web-only guest mode |
| `name`, `anime_name` | the bot's nickname in each persona |
| `claude_bin`, `claude_model` | Claude Code CLI path and model |
| `codex_bin`, `gate_model` | Codex CLI path and the small model that decides when to reply |
| `fast_model` | Claude model used when Codex isn't available, and for quick passes |
| `owner_name` | optional: how prompts refer to the owner |
| `guest_code_channels`, `guest_code_guilds` | channels (or whole servers) where people can ask for real work in a given project folder |
| `passive_channel_ids` | extra channels it chats in without being pinged |
| `ping_role_ids` | roles whose pings count as pinging the bot |
| `partner_agent` | optional channel for working with another AI agent |
| `server_emoji` | custom emoji it can use |
| `git_name`, `git_email` | identity for commits the agent makes |
| `work_label` | how the agent describes the machine it's working on |
| `data_dir` | where state, logs and the index live (default: this folder) |
| `doc_roots`, `project_roots` | folders indexed for the owner agent's memory |

Every key can also be set as `PROMINENCE_<KEY>` (lists comma-separated); `PROMINENCE_CONFIG` points to another
config file. Turn on Discord's developer mode to copy ids with right click > Copy ID.

</details>

## Project layout

| file | what it is |
| --- | --- |
| `bot.py` | the bot itself |
| `supervise.py` | keeps it running and rolls back bad self-updates |
| `config.py` | settings |
| `workflows.py`, `context.py`, `dm.py` | the owner agent's schedules, memory and texting |
| `promises.py`, `steer_hook.py`, `send.py` | follow-ups, mid-task steering and posting for running agents |
| `discord_read.py`, `discord_react.py`, `mod.py` | the agent's Discord tools |
| `selfupdate.py` | safe self-editing |
| `tools/` | extras |

## Credit

prominence-chan is open source under the MIT license. **If you use its code, credit the original
[prominence-chan](https://github.com/prominence-chan/prominence-chan)** somewhere people will see it: a comment where
the code is used, your license or NOTICE text, your README, or your bot's about / info text. Something like:

```
Based on prominence-chan (https://github.com/prominence-chan/prominence-chan)
```

## License

[MIT](LICENSE)
