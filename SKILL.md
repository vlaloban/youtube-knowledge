---
name: youtube-knowledge
description: Harvest knowledge from YouTube videos, channels, or searches. Use when the user shares a YouTube link, asks to research a topic across videos, monitor a channel's recent videos, get a transcript, or pull screenshots/frames from a video to inspect what is shown on screen (diagrams, demos, slides, code).
---

# YouTube Knowledge Toolkit

A set of CLI tools that fetch transcripts and on-demand screenshots from YouTube
so **you (Claude)** can read, watch, and synthesize what a video teaches. You are
the orchestrator: read the transcript, decide which moments need a visual look,
pull frames for those moments, open the PNGs yourself, and write up the knowledge.

## The command

The launcher is `bin/ytk`, sitting **next to this `SKILL.md`** in the same skill
directory. Invoke it by its full path, resolved from this file's location — e.g.
if this skill is installed at `~/.claude/skills/youtube-knowledge/`, the launcher
is `~/.claude/skills/youtube-knowledge/bin/ytk`. Never hardcode a different path.

```
<skill-dir>/bin/ytk <subcommand> ...
```

The launcher resolves its own absolute location (following symlinks), so it works
from any working directory. On first invocation it builds a Python virtualenv (in
`~/.cache/ytk/venv`, outside the skill) and installs `yt-dlp` automatically, then
refreshes it every 7 days (`YTK_UPDATE_DAYS`, `0` disables).
Requires `python3` and `ffmpeg` on the system, plus a JavaScript runtime
(`deno`, `node` or `bun`) for full YouTube format support.

All artifacts are saved under `~/yt-knowledge/<video_id>/`:
`info.json`, `transcript.txt`, `frames/`. The video id is the 11-char YouTube id.
Override the output root with the `YTK_HOME` env var.

## Subcommands

- `ytk find "<query>" [--max 10] [--json]` — search YouTube, return a list of videos.
- `ytk find "<@handle|url>" --channel [--last 50] [--json]` — list a channel's recent videos.
- `ytk info <url|id> [--json]` — title, channel, duration, **chapters**, available
  caption languages, description. Saved to `info.json`.
- `ytk transcript <url|id> [--lang en] [--quiet]` — fetch existing captions as
  `[MM:SS] text` lines. Prefers manual subs in the original language, falls back to
  auto-captions. If the video has no captions at all, it transcribes the audio
  with Whisper instead: a local install (`mlx_whisper` or `whisper` on `PATH`) if
  there is one, otherwise the Groq or OpenAI API when `GROQ_API_KEY` or
  `OPENAI_API_KEY` is set (`--whisper` forces Whisper, `--no-whisper` forbids it,
  `--whisper-provider local|groq|openai` picks one). With neither available it
  reports that no captions exist.
- `ytk frames <url|id> --from <t> --to <t> [--every 5]` — extract screenshots across
  a time range without downloading the whole video. Or `--at <t>` for a single frame.
  Times accept `mm:ss`, `h:mm:ss`, or raw seconds. Prints the saved PNG paths.
- `ytk doctor [--json]` — check yt-dlp (version and age), the JavaScript runtime,
  `ffmpeg`, Whisper (local install or API key) and the output directory. Exit code 1 means a
  required piece is missing.

## Workflow for ONE video

1. `ytk info <video>` — get chapters & confirm captions exist. Chapters are
   author-provided "interesting moments" — use them to navigate.
2. `ytk transcript <video>` — read it. This is your primary source.
3. While reading, watch for places where the words reference something on screen
   ("as you can see here", "this diagram", "look at this code", "the graph shows").
   Note the `[MM:SS]` timestamp.
4. For each such moment, pull frames a little *before* the reference through a little
   after, e.g. a phrase at 14:00 → `ytk frames <video> --from 13:55 --to 14:20 --every 5`.
   On-screen content usually appears slightly before it's mentioned.
5. **Open each PNG with the Read tool** and look. Extract the diagram/demo/slide/code.
6. Write findings to `~/yt-knowledge/<video_id>/notes.md` with timestamp references,
   weaving transcript quotes together with what you saw in the frames.

## Workflow for a SEARCH (research a topic)

1. `ytk find "<topic>" --max 10` to get candidate videos.
2. Pick the most relevant (by title/channel/duration), then run the single-video
   workflow on each. Synthesize across them into one summary, citing each video.

## Workflow for a CHANNEL (monitor / mine)

1. `ytk find "<@handle>" --channel --last 50`.
2. Triage by title; run the single-video workflow on the relevant ones.

## Tips

- Frames cost time/bandwidth (transcript is nearly free). Pull frames only for
  moments that actually need a visual — don't blanket-screenshot a whole video.
- Use `--every` to control density: 5s for a stable diagram, 2s for a fast demo.
- `--height` (default 720) controls frame resolution; raise it if fine text on a
  slide is unreadable.
- Always transcribe in the video's original language (the default behavior).
- If `transcript` reports no captions and no Whisper, ask the user to either
  install a local Whisper (`mlx_whisper` on Apple Silicon, or `whisper`) or add
  `GROQ_API_KEY` or `OPENAI_API_KEY` to their environment. The API route uploads
  the video's audio to that provider, so do not set a key on the user's behalf.
- Local Whisper is slow on long videos and downloads its model on first use;
  warn the user before transcribing anything over about an hour.
- When a command fails in a way that looks like an environment problem (yt-dlp
  errors, missing formats, ffmpeg not found), run `ytk doctor` before retrying.
