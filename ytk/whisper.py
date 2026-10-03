"""Whisper fallback for videos that have no captions.

Downloads the audio track and transcribes it, preferring a Whisper installed on
this machine (nothing leaves the computer) and otherwise an OpenAI-compatible
transcription endpoint (Groq or OpenAI), which gets the audio in small mono
chunks. Only the standard library is used, so the virtualenv needs nothing
beyond yt-dlp.
"""
import glob
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .common import eprint, video_url, ytdlp

# provider -> (API key env var, endpoint, default model)
PROVIDERS = {
    "groq": ("GROQ_API_KEY",
             "https://api.groq.com/openai/v1/audio/transcriptions",
             "whisper-large-v3-turbo"),
    "openai": ("OPENAI_API_KEY",
               "https://api.openai.com/v1/audio/transcriptions",
               "whisper-1"),
}

# Local Whisper command-line tools, in order of preference:
# name -> (default model, flag style). Both write <audio stem>.json with a
# "segments" list. Override the model with YTK_WHISPER_MODEL.
LOCAL_BACKENDS = {
    "mlx_whisper": ("mlx-community/whisper-large-v3-turbo", "-"),
    "whisper": ("turbo", "_"),
}
LOCAL = "local"

# 16 kHz mono at 32 kbit/s is about 2.4 MB per 10 minutes, far below the 25 MB
# upload limit both providers apply.
CHUNK_SECONDS = 600


class WhisperError(RuntimeError):
    pass


def local_backend():
    """Return (name, path) of a Whisper CLI installed on this machine, or None."""
    for name in LOCAL_BACKENDS:
        path = shutil.which(name)
        if path:
            return name, path
    return None


def pick_provider(requested=None):
    """Return "local", "groq" or "openai", or None when nothing is usable.

    A local Whisper wins over the hosted APIs: it is free and keeps the audio
    on this machine.
    """
    if requested == LOCAL:
        return LOCAL if local_backend() else None
    if requested and requested != "auto":
        return requested if os.environ.get(PROVIDERS[requested][0]) else None
    if local_backend():
        return LOCAL
    for name, (env, _, _) in PROVIDERS.items():
        if os.environ.get(env):
            return name
    return None


def describe(provider):
    """Human-readable name of a provider returned by pick_provider."""
    if provider == LOCAL:
        return f"local {local_backend()[0]}"
    return provider


def _download_audio(video, workdir):
    out_tmpl = str(Path(workdir) / "audio.%(ext)s")
    r = ytdlp(["-f", "bestaudio/best", "--no-warnings", "--no-playlist",
               "-o", out_tmpl, video_url(video)])
    if r.returncode != 0:
        raise WhisperError((r.stderr or "yt-dlp audio download failed").strip())
    files = glob.glob(str(Path(workdir) / "audio.*"))
    if not files:
        raise WhisperError("audio file not produced")
    return files[0]


def _split_audio(src, workdir):
    pattern = str(Path(workdir) / "chunk_%04d.mp3")
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src,
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
        "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
        "-reset_timestamps", "1", pattern,
    ]
    p = subprocess.run(cmd, text=True, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise WhisperError(f"ffmpeg failed: {p.stderr.strip()[:300]}")
    chunks = sorted(glob.glob(str(Path(workdir) / "chunk_*.mp3")))
    if not chunks:
        raise WhisperError("ffmpeg produced no audio chunks")
    return chunks


def _multipart(fields, file_field, filename, payload):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: audio/mpeg\r\n\r\n'.encode()
        + payload + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _transcribe_chunk(path, provider, lang):
    env, endpoint, model = PROVIDERS[provider]
    fields = {"model": model, "response_format": "verbose_json"}
    if lang:
        fields["language"] = lang
    body, content_type = _multipart(
        fields, "file", Path(path).name, Path(path).read_bytes())
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "Authorization": f"Bearer {os.environ[env]}",
        "Content-Type": content_type,
        # Groq sits behind Cloudflare, which rejects the default urllib agent.
        "User-Agent": "ytk/0.2",
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        if e.code == 429:
            raise WhisperError(f"{provider} rate limit reached: {detail}") from e
        raise WhisperError(f"{provider} returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise WhisperError(f"could not reach {provider}: {e.reason}") from e


def _segment_lines(data, offset=0.0):
    segments = data.get("segments") or []
    if not segments and (data.get("text") or "").strip():
        segments = [{"start": 0.0, "text": data["text"]}]
    lines = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if text:
            lines.append((offset + float(seg.get("start") or 0.0), text))
    return lines


def _transcribe_local(audio, workdir, lang):
    name, path = local_backend()
    default_model, sep = LOCAL_BACKENDS[name]
    model = os.environ.get("YTK_WHISPER_MODEL") or default_model
    out_dir = Path(workdir) / "out"
    out_dir.mkdir()
    cmd = [path, audio, "--model", model,
           f"--output{sep}format", "json", f"--output{sep}dir", str(out_dir),
           "--verbose", "False"]
    if lang:
        cmd += ["--language", lang]
    eprint(f"Transcribing with {name} (model {model}); the first run downloads the model ...")
    p = subprocess.run(cmd, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    results = glob.glob(str(out_dir / "*.json"))
    if p.returncode != 0 or not results:
        raise WhisperError(f"{name} failed: {(p.stderr or '').strip()[-400:]}")
    return _segment_lines(json.loads(Path(results[0]).read_text(encoding="utf-8")))


def transcribe(video, provider, lang=None):
    """Transcribe a video's audio. Returns a list of (start_sec, text)."""
    lines = []
    with tempfile.TemporaryDirectory(prefix="ytk-whisper-") as workdir:
        eprint("Downloading audio ...")
        audio = _download_audio(video, workdir)
        if provider == LOCAL:
            return _transcribe_local(audio, workdir, lang)
        chunks = _split_audio(audio, workdir)
        for i, chunk in enumerate(chunks):
            eprint(f"Transcribing chunk {i + 1}/{len(chunks)} via {provider} ...")
            data = _transcribe_chunk(chunk, provider, lang)
            lines.extend(_segment_lines(data, offset=i * CHUNK_SECONDS))
    return lines
