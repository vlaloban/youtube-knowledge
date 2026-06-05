# youtube-knowledge

An agent **skill** for harvesting knowledge from YouTube. The agent reads transcripts
and pulls **on-demand screenshots**, so it can both *read* and *watch* a video — then
synthesize what it teaches. Works on a single video, a topic search, or a whole channel.

The agent is the orchestrator: it reads the transcript, notices when the speaker
references something on screen ("as you can see here…"), pulls frames around that
timestamp, opens the images itself, and writes up the knowledge.

This repo **is** the skill — `SKILL.md` lives at the root, so you install it by
cloning straight into your agent's skills directory. The `SKILL.md` format is an
open standard supported by Claude Code, OpenAI Codex, Cursor, Gemini CLI, and others.

## What it does

| Tool | Purpose |
| --- | --- |
| `find` | Search YouTube by topic, or list a channel's recent videos |
| `info` | Title, duration, **chapters**, available caption languages, description |
| `transcript` | Existing captions (original language) as `[MM:SS] text` |
| `frames` | Screenshots from any time range — **without downloading the whole video** |

## Requirements

- An agent that supports the open `SKILL.md` skill format (Claude Code, Codex, Cursor, …)
- `python3` and `ffmpeg` on your system
  (`brew install ffmpeg` on macOS, `apt install ffmpeg` on Debian/Ubuntu)

`yt-dlp` installs itself on first run into a local virtualenv (`~/.cache/ytk/venv`).

## Install

Clone this repo directly into your agent's skills folder, naming the folder
`youtube-knowledge`:

```bash
# Claude Code
git clone https://github.com/vlaloban/youtube-knowledge \
  ~/.claude/skills/youtube-knowledge

# OpenAI Codex
git clone https://github.com/vlaloban/youtube-knowledge \
  ~/.agents/skills/youtube-knowledge
```

That's it. No build step, no config — the agent picks it up by its `description`,
and the launcher bootstraps its own dependencies on first use. To update later:
`git pull` inside that folder.

## Usage

Just talk to the agent — the skill triggers on YouTube links, topic research, channel
mining, or screenshot requests. Examples:

- "Pull the key ideas from `https://youtu.be/…`, with screenshots of the diagrams."
- "Research *retrieval-augmented generation* across the top 10 videos."
- "Mine the last 50 videos from `@some-channel` for anything about X."

Harvested data is saved under `~/yt-knowledge/<video_id>/`
(`info.json`, `transcript.txt`, `frames/`). Override the root with `YTK_HOME`.

## Layout

```
youtube-knowledge/
├── SKILL.md        # instructions the agent follows (open skill format)
├── README.md       # this file
├── bin/ytk         # launcher (self-resolving path; bootstraps the venv)
└── ytk/            # Python package: cli.py, common.py, subs.py
```

You can also run the tools directly without an agent:
`bin/ytk transcript <url>`, `bin/ytk frames <url> --from 13:55 --to 14:20`.

## Not included yet

- **Whisper fallback** for videos without captions. `transcript` currently reports
  when no captions exist instead of transcribing the audio.

## License

MIT — see [LICENSE](LICENSE).
