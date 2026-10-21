"""Voice -> text for Jarvis (docs/ai-assistant-plan.md §9.11): the page records, turns the audio into a 16 kHz mono WAV and posts it here;
we check it and pass it to the local speech recognition container (deploy/asr/, sherpa-onnx + SenseVoice, CPU only, BODY_ASR_URL).

- Nothing leaves the server and nothing is stored: the audio only exists in memory during the request (not written to disk, not logged);
  the text goes back to the page, the user checks / edits it, and only then sends it as an ordinary chat message.
- Free (local CPU), but limited: ASR_PER_DAY recognitions per person per local day, ASR_PER_MINUTE per minute, at most MAX_SECONDS each.
  Each recognition is one row in asr_usage (duration and timing only, no audio, no text).
- Works for everyone with in-page chat (own API provider or the fallback AI); without BODY_ASR_URL it is off and the page
  falls back to the keyboard's dictation.
"""
import io
import json
import os
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..i18n import L

MAX_SECONDS = 60
MIN_SECONDS = 0.3
MAX_BYTES = 6 * 1024 * 1024          # 60 s at 48 kHz PCM16 mono; the page sends 16 kHz (1.9 MB)
ASR_PER_DAY = 50
ASR_PER_MINUTE = 6
TIMEOUT_S = 90                       # queueing behind one other recognition + up to 60 s of audio on one slow core


class AsrError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def url() -> str | None:
    return (os.environ.get("BODY_ASR_URL") or "").rstrip("/") or None


def enabled() -> bool:
    return url() is not None


def too_large_message() -> str:
    return L(f"录音太大（最长 {MAX_SECONDS} 秒）", f"The recording is too big (at most {MAX_SECONDS} s)")


def check_wav(data: bytes) -> float:
    """PCM16 mono WAV, 8–48 kHz, MIN_SECONDS–MAX_SECONDS. Returns the duration in seconds."""
    if len(data) > MAX_BYTES:
        raise AsrError("too_large", too_large_message(), 413)
    try:
        with wave.open(io.BytesIO(data)) as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError):
        raise AsrError("bad_audio", L("不是有效的 WAV 录音", "Not a valid WAV recording"))
    if ch != 1 or width != 2 or not 8000 <= rate <= 48000:
        raise AsrError("bad_audio", L("录音格式要是 16 位单声道 WAV", "The recording must be a 16-bit mono WAV"))
    seconds = n / rate
    if seconds < MIN_SECONDS:
        raise AsrError("too_short", L("太短了，按住多说一会儿", "Too short; hold and talk a little longer"))
    if seconds > MAX_SECONDS + 0.5:
        raise AsrError("too_long", L(f"一条最长 {MAX_SECONDS} 秒", f"At most {MAX_SECONDS} s per recording"))
    return seconds


def usage_today(conn: psycopg.Connection, user_id: int) -> int:
    tz = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"] or "UTC"
    start = datetime.now(ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
    return conn.execute("SELECT count(*) AS n FROM asr_usage WHERE user_id = %s AND at >= %s",
                        (user_id, start)).fetchone()["n"]


def status(conn: psycopg.Connection, user_id: int) -> dict:
    """For /v1/web/me: whether voice input is on, and today's count."""
    if not enabled():
        return {"enabled": False}
    return {"enabled": True, "max_seconds": MAX_SECONDS, "per_day": ASR_PER_DAY, "used_today": usage_today(conn, user_id)}


def _limits(conn, user_id: int) -> None:
    if usage_today(conn, user_id) >= ASR_PER_DAY:
        raise AsrError("rate_limit", L(f"今天的语音识别用完了（每天 {ASR_PER_DAY} 条），可以用键盘上的听写",
                                      f"No voice input left today ({ASR_PER_DAY} a day); use the keyboard's dictation"), 429)
    since = datetime.now(ZoneInfo("UTC")) - timedelta(minutes=1)
    n = conn.execute("SELECT count(*) AS n FROM asr_usage WHERE user_id = %s AND at >= %s", (user_id, since)).fetchone()["n"]
    if n >= ASR_PER_MINUTE:
        raise AsrError("rate_limit", L("说得太快了，歇一下再试", "Too many at once; wait a moment and try again"), 429)


def _post(data: bytes) -> dict:
    req = urllib.request.Request(url() + "/transcribe", data=data, method="POST",
                                 headers={"Content-Type": "audio/wav"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 (internal service address)
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 503:
            raise AsrError("busy", L("语音识别正忙，稍后再试", "Voice input is busy; try again shortly"), 503)
        raise AsrError("asr_failed", L(f"语音识别出错（{e.code}）", f"Voice input failed ({e.code})"), 502)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AsrError("asr_down", L("语音识别暂时不可用，可以用键盘上的听写",
                                    "Voice input is unavailable right now; use the keyboard's dictation"), 503)


def transcribe(conn: psycopg.Connection, user_id: int, data: bytes) -> dict:
    if not enabled():
        raise AsrError("asr_off", L("这台服务器没有开通语音识别，可以用键盘上的听写",
                                  "This server has no voice input; use the keyboard's dictation"), 503)
    seconds = check_wav(data)
    _limits(conn, user_id)
    t0 = time.monotonic()
    ok, err = False, None
    try:
        res = _post(data)
        ok = True
    except AsrError as e:
        err = e.code
        raise
    finally:  # counted even when it failed (a failing request still used the CPU / the quota)
        conn.execute("INSERT INTO asr_usage (user_id, audio_ms, proc_ms, ok, error) VALUES (%s, %s, %s, %s, %s)",
                     (user_id, round(seconds * 1000), round((time.monotonic() - t0) * 1000), ok, err))
        conn.commit()
    text = (res.get("text") or "").strip()
    return {"text": text, "seconds": round(seconds, 1), "used_today": usage_today(conn, user_id), "per_day": ASR_PER_DAY}
