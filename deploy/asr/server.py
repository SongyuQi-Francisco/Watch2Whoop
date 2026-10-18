"""Local speech recognition for Jarvis (docs/ai-assistant-plan.md §9.11): sherpa-onnx + SenseVoice-Small int8, CPU only.

Only Body Engine calls it (compose network http://asr:8000; the host publishes 127.0.0.1:8770 for doctor only).
  POST /transcribe   body: WAV (PCM16 mono, 8–48 kHz, <= ASR_MAX_SECONDS)  ->  {"text", "seconds", "ms"}
  GET  /healthz      -> {"ok", "loaded", "rss_mb", "busy", "waiting"}
- One recognition at a time (ASR_THREADS onnxruntime threads); at most ASR_MAX_WAITING requests wait, more get 503 (Body Engine says "busy").
- The model (~240 MB) is loaded on the first request and unloaded after ASR_IDLE_UNLOAD_S idle seconds (malloc_trim gives the memory back),
  so the container sits at ~40 MB most of the day on a shared 2-vCPU machine.
- Audio is only ever in memory: nothing is written to disk and nothing about the content is logged (only length and timing).
"""
import ctypes
import gc
import io
import json
import os
import resource
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

MODEL_DIR = os.environ.get("ASR_MODEL_DIR", "/models")
THREADS = int(os.environ.get("ASR_THREADS", "1"))
MAX_SECONDS = float(os.environ.get("ASR_MAX_SECONDS", "61"))
MAX_BYTES = 6 * 1024 * 1024
MAX_WAITING = int(os.environ.get("ASR_MAX_WAITING", "3"))
IDLE_UNLOAD_S = int(os.environ.get("ASR_IDLE_UNLOAD_S", "600"))
PORT = int(os.environ.get("ASR_PORT", "8000"))

_lock = threading.Lock()           # one recognition at a time
_state = {"rec": None, "last": 0.0, "waiting": 0, "busy": False}
_state_lock = threading.Lock()


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), msg, file=sys.stderr, flush=True)


def rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
    except OSError:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1048576 if sys.platform == "darwin" else 1024)  # peak


def recognizer():
    if _state["rec"] is None:
        import sherpa_onnx
        t = time.monotonic()
        _state["rec"] = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=os.path.join(MODEL_DIR, "model.int8.onnx"), tokens=os.path.join(MODEL_DIR, "tokens.txt"),
            num_threads=THREADS, use_itn=True, language="auto")
        log(f"model loaded in {time.monotonic() - t:.1f}s, rss {rss_mb():.0f} MB")
    return _state["rec"]


def unload_when_idle() -> None:
    while True:
        time.sleep(30)
        with _lock:
            if _state["rec"] is not None and time.monotonic() - _state["last"] > IDLE_UNLOAD_S:
                _state["rec"] = None
                gc.collect()
                try:
                    ctypes.CDLL("libc.so.6").malloc_trim(0)
                except OSError:
                    pass
                log(f"model unloaded after {IDLE_UNLOAD_S}s idle, rss {rss_mb():.0f} MB")


def read_wav(data: bytes) -> tuple[int, np.ndarray]:
    with wave.open(io.BytesIO(data)) as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("need PCM16 mono")
        rate, frames = w.getframerate(), w.readframes(w.getnframes())
    if not 8000 <= rate <= 48000:
        raise ValueError("sample rate 8–48 kHz")
    x = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if len(x) / rate > MAX_SECONDS:
        raise ValueError("too long")
    return rate, x


class Handler(BaseHTTPRequestHandler):
    server_version = "body-asr/0.1"

    def log_message(self, fmt, *args):  # no request lines in the log (paths only, but keep it quiet)
        pass

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/healthz":
            return self._json(404, {"error": "not found"})
        self._json(200, {"ok": True, "loaded": _state["rec"] is not None, "rss_mb": round(rss_mb()),
                         "busy": _state["busy"], "waiting": _state["waiting"]})

    def do_POST(self):
        if self.path != "/transcribe":
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= MAX_BYTES:
            return self._json(413, {"error": "size"})
        data = self.rfile.read(n)
        try:
            rate, x = read_wav(data)
        except (ValueError, wave.Error, EOFError) as e:
            return self._json(422, {"error": str(e)})
        del data
        with _state_lock:
            if _state["waiting"] >= MAX_WAITING:
                return self._json(503, {"error": "busy"})
            _state["waiting"] += 1
        try:
            with _lock:
                with _state_lock:
                    _state["waiting"] -= 1
                _state["busy"] = True
                t = time.monotonic()
                try:
                    s = recognizer().create_stream()
                    s.accept_waveform(rate, x)
                    recognizer().decode_stream(s)
                    text = s.result.text.strip()
                finally:
                    _state["busy"] = False
                    _state["last"] = time.monotonic()
                ms = round((time.monotonic() - t) * 1000)
        except Exception as e:  # noqa: BLE001
            log(f"recognition failed: {type(e).__name__}")
            return self._json(500, {"error": "recognition failed"})
        seconds = len(x) / rate
        log(f"{seconds:.1f}s audio in {ms} ms (x{seconds * 1000 / max(ms, 1):.1f}), rss {rss_mb():.0f} MB")
        self._json(200, {"text": text, "seconds": round(seconds, 2), "ms": ms})


def main() -> None:
    for f in ("model.int8.onnx", "tokens.txt"):
        if not os.path.exists(os.path.join(MODEL_DIR, f)):
            log(f"missing {MODEL_DIR}/{f}: run deploy/asr/fetch_model.sh on the host")
            sys.exit(1)
    threading.Thread(target=unload_when_idle, daemon=True).start()
    log(f"listening on :{PORT}, threads {THREADS}, unload after {IDLE_UNLOAD_S}s idle")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
