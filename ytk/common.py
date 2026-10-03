"""Shared helpers for the ytk toolkit."""
import functools
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Root for all collected knowledge. Override with YTK_HOME.
HOME = Path(os.environ.get("YTK_HOME", Path.home() / "yt-knowledge"))


# JavaScript runtimes yt-dlp can use for YouTube, in its own priority order.
JS_RUNTIMES = ("deno", "node", "bun")


@functools.lru_cache(maxsize=1)
def js_runtime():
    """Return (name, path) of the first JavaScript runtime on PATH, or None."""
    for name in JS_RUNTIMES:
        path = shutil.which(name)
        if path:
            return name, path
    return None


def _js_runtime_args():
    # yt-dlp enables only deno by default; any other runtime has to be named.
    rt = js_runtime()
    if rt and rt[0] != "deno":
        return ["--js-runtimes", f"{rt[0]}:{rt[1]}"]
    return []


def ytdlp(args, capture=True):
    """Run yt-dlp (from this interpreter's env) and return CompletedProcess."""
    cmd = [sys.executable, "-m", "yt_dlp", *_js_runtime_args(), *args]
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def extract_video_id(s):
    """Pull an 11-char video id out of a URL or return s if it already looks like one."""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = re.search(r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})", s)
    if m:
        return m.group(1)
    return None  # not a single video (could be a channel/playlist/search)


def video_url(s):
    vid = extract_video_id(s)
    return f"https://www.youtube.com/watch?v={vid}" if vid else s


def workspace(video_id, create=True):
    d = HOME / video_id
    if create:
        (d / "frames").mkdir(parents=True, exist_ok=True)
    return d


def parse_time(s):
    """'835' | '13:55' | '1:13:55' | '13:55.5' -> seconds (float)."""
    s = str(s).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    parts = s.split(":")
    if not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise ValueError(f"bad timestamp: {s!r}")
    parts = [float(p) for p in parts]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def fmt_time(sec, compact=False):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}" if not compact else f"{h:d}-{m:02d}-{s:02d}"
    return f"{m:d}:{s:02d}" if not compact else f"{m:02d}-{s:02d}"


def fmt_duration(sec):
    if not sec:
        return "?"
    return fmt_time(sec)


def get_info(target):
    """Return yt-dlp -J info dict for a single video (no download)."""
    r = ytdlp(["-J", "--no-warnings", video_url(target)])
    if r.returncode != 0:
        sys.stderr.write(r.stderr or "yt-dlp failed\n")
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        sys.stderr.write("could not parse yt-dlp output\n")
        return None


def eprint(*a):
    print(*a, file=sys.stderr)
