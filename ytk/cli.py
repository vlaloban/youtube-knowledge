"""ytk — a toolkit for harvesting knowledge from YouTube.

Subcommands:
  find        search YouTube or list a channel's recent videos
  info        show metadata + chapters for one video
  transcript  fetch captions as timestamped text (Whisper if there are none)
  frames      extract screenshots from a time range (no full download)
  doctor      check that the toolkit's dependencies are in working order
"""
import argparse
import datetime
import glob
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import common, whisper
from .common import (eprint, fmt_duration, fmt_time, get_info, parse_time,
                     video_url, workspace, ytdlp)
from .subs import format_lines, parse_vtt


# ----------------------------------------------------------------------------- find
def cmd_find(args):
    if args.channel:
        target = args.query
        if not target.startswith("http"):
            handle = target if target.startswith("@") else "@" + target
            target = f"https://www.youtube.com/{handle}/videos"
        limit = args.last
        label = f"channel {args.query}"
    else:
        target = f"ytsearch{args.max}:{args.query}"
        limit = args.max
        label = f"search '{args.query}'"

    flags = ["--flat-playlist", "--no-warnings", "-J", "--playlist-end", str(limit), target]
    r = ytdlp(flags)
    if r.returncode != 0:
        eprint(r.stderr or "yt-dlp failed")
        return 1
    data = json.loads(r.stdout)
    entries = data.get("entries", []) or []

    rows = []
    for e in entries:
        if not e:
            continue
        rows.append({
            "id": e.get("id", ""),
            "title": (e.get("title") or "").strip(),
            "channel": e.get("channel") or e.get("uploader") or data.get("channel") or "",
            "duration": fmt_duration(e.get("duration")),
            "url": f"https://www.youtube.com/watch?v={e.get('id','')}",
        })

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    eprint(f"# {label}: {len(rows)} videos\n")
    for i, row in enumerate(rows, 1):
        ch = f"  · {row['channel']}" if row["channel"] else ""
        print(f"{i:2d}. [{row['duration']:>7}] {row['title']}{ch}")
        print(f"    {row['id']}  {row['url']}")
    return 0


# ----------------------------------------------------------------------------- info
def cmd_info(args):
    info = get_info(args.video)
    if not info:
        return 1
    vid = info.get("id", "")
    ws = workspace(vid)
    summary = {
        "id": vid,
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "duration": info.get("duration"),
        "duration_str": fmt_duration(info.get("duration")),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "language": info.get("language"),
        "url": f"https://www.youtube.com/watch?v={vid}",
        "chapters": [
            {"start": c.get("start_time"), "start_str": fmt_time(c.get("start_time", 0)),
             "title": c.get("title")}
            for c in (info.get("chapters") or [])
        ],
        "description": info.get("description"),
        "subtitles": sorted((info.get("subtitles") or {}).keys()),
        "auto_captions": sorted((info.get("automatic_captions") or {}).keys())[:20],
    }
    (ws / "info.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    eprint(f"saved -> {ws/'info.json'}\n")
    print(f"Title:    {summary['title']}")
    print(f"Channel:  {summary['channel']}")
    print(f"Duration: {summary['duration_str']}   Uploaded: {summary['upload_date']}   Views: {summary['view_count']}")
    print(f"Lang:     {summary['language']}   Manual subs: {summary['subtitles'] or '—'}")
    if summary["chapters"]:
        print("\nChapters:")
        for c in summary["chapters"]:
            print(f"  [{c['start_str']:>7}] {c['title']}")
    desc = (summary["description"] or "").strip()
    if desc:
        print("\nDescription:")
        print("\n".join("  " + l for l in desc.splitlines()[:30]))
    return 0


# ----------------------------------------------------------------------------- transcript
def _pick_sub_lang(info, requested):
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    orig = info.get("language")

    def first_real(d):
        return [k for k in d if not k.startswith("live_chat")]

    if requested:
        if requested in manual:
            return requested, "manual"
        if requested in auto:
            return requested, "auto"
        return None, None
    # automatic_captions lists every machine translation (alphabetically, so
    # "ab" comes first); only the "<lang>-orig" keys are the spoken language.
    spoken = [k for k in auto if k.endswith("-orig")]
    if not orig and spoken:
        orig = spoken[0][:-len("-orig")]
    # Prefer manual subs in the original language, then any manual, then auto-orig, then any auto.
    if orig and orig in manual:
        return orig, "manual"
    real_manual = first_real(manual)
    if real_manual:
        return real_manual[0], "manual"
    if orig and orig in auto:
        return orig, "auto"
    if spoken:
        return spoken[0], "auto"
    real_auto = first_real(auto)
    if real_auto:
        return ("en" if "en" in real_auto else real_auto[0]), "auto"
    return None, None


def _save_transcript(ws, info, vid, source, lines, quiet):
    body = format_lines(lines)
    header = f"# {info.get('title')}\n# {video_url(vid)}\n# captions: {source}\n\n"
    out_path = ws / "transcript.txt"
    out_path.write_text(header + body + "\n", encoding="utf-8")
    eprint(f"saved -> {out_path}  ({len(lines)} lines)")
    if not quiet:
        print(header + body)


def _whisper_transcript(args, info, vid, ws):
    provider = whisper.pick_provider(args.whisper_provider)
    if not provider:
        reason = ("" if args.whisper else
                  "No subtitles available for this video (manual or auto). ")
        eprint(reason + "To transcribe the audio, install a local Whisper "
               f"({' or '.join(whisper.LOCAL_BACKENDS)} on PATH) or set "
               "GROQ_API_KEY or OPENAI_API_KEY in the environment.")
        return 2
    source = whisper.describe(provider)
    eprint(f"Transcribing the audio of {vid} with Whisper ({source}) ...")
    lang = args.lang or (info.get("language") or "").split("-")[0] or None
    try:
        lines = whisper.transcribe(vid, provider, lang)
    except whisper.WhisperError as e:
        eprint(f"whisper failed: {e}")
        return 1
    if not lines:
        eprint("whisper returned no speech")
        return 1
    _save_transcript(ws, info, vid, f"whisper [{source}]", lines, args.quiet)
    return 0


def cmd_transcript(args):
    info = get_info(args.video)
    if not info:
        return 1
    vid = info.get("id", "")
    ws = workspace(vid)
    if args.whisper:
        return _whisper_transcript(args, info, vid, ws)
    lang, kind = _pick_sub_lang(info, args.lang)
    if not lang:
        available = sorted(set(info.get("subtitles") or {})
                           | {k for k in (info.get("automatic_captions") or {})
                              if k.endswith("-orig")})
        if args.lang and available:
            eprint(f"No captions in '{args.lang}'. Available: {', '.join(available)} "
                   "(plus machine translations).")
            return 2
        if args.no_whisper:
            eprint("No subtitles available for this video (manual or auto).")
            return 2
        return _whisper_transcript(args, info, vid, ws)

    eprint(f"Fetching {kind} captions [{lang}] for {vid} ...")
    out_tmpl = str(ws / "_sub")
    flags = [
        "--skip-download", "--no-warnings",
        "--sub-format", "vtt", "--sub-langs", lang,
        "-o", out_tmpl, video_url(vid),
    ]
    flags.insert(1, "--write-auto-subs" if kind == "auto" else "--write-subs")
    r = ytdlp(flags)
    if r.returncode != 0:
        eprint(r.stderr or "yt-dlp failed")
        return 1

    vtts = glob.glob(str(ws / "_sub*.vtt"))
    if not vtts:
        eprint("subtitle file not produced")
        return 1
    vtt_text = Path(vtts[0]).read_text(encoding="utf-8", errors="replace")
    lines = parse_vtt(vtt_text)
    for f in vtts:
        os.remove(f)

    _save_transcript(ws, info, vid, f"{kind} [{lang}]", lines, args.quiet)
    return 0


# ----------------------------------------------------------------------------- frames
def _stream_url(video, max_height):
    fmt = f"bv*[height<=?{max_height}]/b[height<=?{max_height}]/best"
    r = ytdlp(["-g", "-f", fmt, "--no-warnings", video_url(video)])
    if r.returncode != 0:
        eprint(r.stderr or "yt-dlp -g failed")
        return None
    urls = [u for u in r.stdout.strip().splitlines() if u.strip()]
    return urls[0] if urls else None


def cmd_frames(args):
    vid = common.extract_video_id(args.video)
    if not vid:
        # Not a plain video URL/id: ask yt-dlp rather than guess a folder name.
        info = get_info(args.video)
        vid = (info or {}).get("id") if (info or {}).get("_type", "video") == "video" else None
        if not vid:
            eprint(f"could not resolve a single video from {args.video!r}")
            return 1
    ws = workspace(vid)
    frames_dir = ws / "frames"

    if args.at is not None:
        times = [parse_time(args.at)]
    else:
        if args.from_ is None or args.to is None:
            eprint("provide --at T, or both --from and --to")
            return 1
        start, end = parse_time(args.from_), parse_time(args.to)
        if end < start:
            start, end = end, start
        step = float(args.every)
        times = []
        t = start
        while t <= end + 1e-6:
            times.append(round(t, 3))
            t += step

    eprint(f"Resolving stream for {vid} ...")
    url = _stream_url(vid, args.height)
    if not url:
        return 1

    written = []
    for t in times:
        name = f"{vid}_{fmt_time(t, compact=True)}.png"
        out = frames_dir / name
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
            "-ss", f"{t}", "-i", url,
            "-frames:v", "1", "-q:v", "2", str(out),
        ]
        p = subprocess.run(cmd, text=True, stderr=subprocess.PIPE)
        if p.returncode == 0 and out.exists():
            written.append(out)
        else:
            eprint(f"  ! failed at {fmt_time(t)}: {p.stderr.strip()[:200]}")

    eprint(f"\n{len(written)} frames -> {frames_dir}")
    for w in written:
        print(w)
    return 0 if written else 1


# ----------------------------------------------------------------------------- doctor
YTDLP_STALE_DAYS = 60


def _ytdlp_age_days(version):
    """yt-dlp versions are release dates (2026.08.19); return the age in days."""
    try:
        y, m, d = (int(x) for x in version.split(".")[:3])
        return (datetime.date.today() - datetime.date(y, m, d)).days
    except (ValueError, TypeError):
        return None


def _doctor_checks():
    """Return a list of {name, status: ok|warn|fail, detail}."""
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    try:
        version = importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        add("yt-dlp", "fail", "not installed in the ytk virtualenv; delete the venv and rerun")
    else:
        age = _ytdlp_age_days(version)
        if age is not None and age > YTDLP_STALE_DAYS:
            add("yt-dlp", "warn", f"{version} is {age} days old; the launcher refreshes it "
                                  "every YTK_UPDATE_DAYS (default 7) days")
        else:
            add("yt-dlp", "ok", version)

    try:
        add("yt-dlp-ejs", "ok", importlib.metadata.version("yt-dlp-ejs"))
    except importlib.metadata.PackageNotFoundError:
        add("yt-dlp-ejs", "warn", "missing; some YouTube formats may be unavailable "
                                  "(the launcher installs it on the next refresh)")

    rt = common.js_runtime()
    if rt:
        add("js runtime", "ok", f"{rt[0]} ({rt[1]})")
    else:
        add("js runtime", "warn", "none of deno/node/bun on PATH; some YouTube formats "
                                  "may be missing (brew install deno)")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        add("ffmpeg", "ok", ffmpeg)
    else:
        add("ffmpeg", "fail", "not found; frames and Whisper need it (brew install ffmpeg)")

    provider = whisper.pick_provider()
    if provider == whisper.LOCAL:
        name, path = whisper.local_backend()
        add("whisper", "ok", f"local {name} ({path})")
    elif provider:
        add("whisper", "ok", f"{provider} API ({whisper.PROVIDERS[provider][0]} is set); "
                             "no local Whisper found")
    else:
        add("whisper", "warn", "no local Whisper "
                               f"({'/'.join(whisper.LOCAL_BACKENDS)}) and no GROQ_API_KEY "
                               "or OPENAI_API_KEY; videos without captions cannot be "
                               "transcribed")

    try:
        common.HOME.mkdir(parents=True, exist_ok=True)
        writable = os.access(common.HOME, os.W_OK)
    except OSError:
        writable = False
    add("output dir", "ok" if writable else "fail",
        str(common.HOME) + ("" if writable else " is not writable"))
    return checks


def cmd_doctor(args):
    checks = _doctor_checks()
    failed = any(c["status"] == "fail" for c in checks)
    if args.json:
        print(json.dumps({"ok": not failed, "checks": checks}, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    marks = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}
    for c in checks:
        print(f"[{marks[c['status']]}] {c['name']:<11} {c['detail']}")
    return 1 if failed else 0


# ----------------------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(prog="ytk", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("find", help="search YouTube or list a channel")
    f.add_argument("query", help="search terms, or a channel @handle/URL with --channel")
    f.add_argument("--channel", action="store_true", help="treat query as a channel")
    f.add_argument("--max", type=int, default=10, help="max search results (default 10)")
    f.add_argument("--last", type=int, default=50, help="channel: recent videos (default 50)")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_find)

    i = sub.add_parser("info", help="metadata + chapters for one video")
    i.add_argument("video", help="video URL or id")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_info)

    t = sub.add_parser("transcript", help="fetch captions as timestamped text")
    t.add_argument("video", help="video URL or id")
    t.add_argument("--lang", help="force a caption language code (e.g. en, ru)")
    t.add_argument("--quiet", action="store_true", help="save only, don't print")
    w = t.add_mutually_exclusive_group()
    w.add_argument("--whisper", action="store_true",
                   help="skip captions and transcribe the audio with Whisper")
    w.add_argument("--no-whisper", action="store_true",
                   help="never fall back to Whisper when there are no captions")
    t.add_argument("--whisper-provider",
                   choices=["auto", whisper.LOCAL, *whisper.PROVIDERS], default="auto",
                   help="where to run Whisper (default: local if installed, "
                        "else the first API with a key set)")
    t.set_defaults(func=cmd_transcript)

    fr = sub.add_parser("frames", help="extract screenshots from a time range")
    fr.add_argument("video", help="video URL or id")
    fr.add_argument("--from", dest="from_", help="start time (mm:ss / h:mm:ss / seconds)")
    fr.add_argument("--to", help="end time")
    fr.add_argument("--at", help="single timestamp (alternative to --from/--to)")
    fr.add_argument("--every", type=float, default=5.0, help="seconds between frames (default 5)")
    fr.add_argument("--height", type=int, default=720, help="max frame height (default 720)")
    fr.set_defaults(func=cmd_frames)

    d = sub.add_parser("doctor", help="check dependencies (yt-dlp, JS runtime, ffmpeg, Whisper key)")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
