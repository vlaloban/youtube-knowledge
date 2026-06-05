"""Parse YouTube VTT subtitles into clean timestamped lines.

Handles both manual subtitles (cue start + text) and auto-generated captions
(which carry per-word inline <hh:mm:ss.mmm> timestamps and heavy duplication).
"""
import re

from .common import fmt_time

_TS = r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
CUE_HEADER = re.compile(rf"{_TS}\s+-->\s+{_TS}")
INLINE_TS = re.compile(rf"<{_TS}>")
TAG = re.compile(r"</?c[^>]*>|<{_TS}>".format(_TS=_TS))


def _ts_to_sec(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _strip_tags(text):
    return re.sub(TAG, "", text).strip()


def parse_vtt(text):
    """Return list of (start_sec, line_text) cleaned and deduplicated."""
    blocks = re.split(r"\n\n+", text)
    tokens = []  # (sec, word)
    have_word_level = False

    for block in blocks:
        lines = block.splitlines()
        m = None
        header_idx = None
        for i, l in enumerate(lines):
            m = CUE_HEADER.search(l)
            if m:
                header_idx = i
                break
        if m is None or header_idx is None:
            continue
        cue_start = _ts_to_sec(*m.groups()[:4])
        body = "\n".join(lines[header_idx + 1:])
        if not body.strip():
            continue

        if INLINE_TS.search(body):
            have_word_level = True
            # First word(s) belong to cue_start; subsequent words get inline ts.
            pos = 0
            cur_time = cue_start
            for mt in INLINE_TS.finditer(body):
                chunk = body[pos:mt.start()]
                for w in _strip_tags(chunk).split():
                    tokens.append((cur_time, w))
                cur_time = _ts_to_sec(*mt.groups())
                pos = mt.end()
            for w in _strip_tags(body[pos:]).split():
                tokens.append((cur_time, w))
        else:
            cleaned = _strip_tags(body).replace("\n", " ")
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                tokens.append((cue_start, ("__CUE__", cleaned)))

    if not tokens:
        return []

    if have_word_level:
        return _words_to_lines(tokens)
    return _cues_to_lines(tokens)


def _dedupe_words(tokens):
    """Auto-captions repeat words across rolling cues; drop adjacent repeats."""
    out = []
    for sec, w in tokens:
        if isinstance(w, tuple):
            continue
        if out and out[-1][1] == w and abs(out[-1][0] - sec) < 0.05:
            continue
        out.append((sec, w))
    # Remove longer-range rolling duplication: collapse runs where the same
    # word appears at near-identical times.
    return out


def _words_to_lines(tokens, max_gap=2.5, max_words=14):
    words = _dedupe_words(tokens)
    lines = []
    buf = []
    buf_start = None
    last_t = None
    for sec, w in words:
        if buf_start is None:
            buf_start = sec
        if buf and (sec - (last_t or sec) > max_gap or len(buf) >= max_words):
            lines.append((buf_start, " ".join(buf)))
            buf = []
            buf_start = sec
        buf.append(w)
        last_t = sec
    if buf:
        lines.append((buf_start, " ".join(buf)))
    return lines


def _cues_to_lines(tokens):
    lines = []
    last_text = None
    for sec, payload in tokens:
        text = payload[1] if isinstance(payload, tuple) else payload
        if text == last_text:
            continue
        # Drop cues whose text is fully contained in the previous line.
        if last_text and text in last_text:
            continue
        lines.append((sec, text))
        last_text = text
    return lines


def format_lines(lines):
    return "\n".join(f"[{fmt_time(sec)}] {text}" for sec, text in lines)
