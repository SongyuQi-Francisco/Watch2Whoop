"""ECG recordings from the Apple Watch ECG app (HKElectrocardiogram, about 30 s single lead similar to Lead I, 512 Hz, µV).

We only show Apple's classification; nothing here diagnoses anything. Derived values (heart rate, RR intervals, RMSSD) come from
a simple Pan-Tompkins-style R-peak detector: baseline removal -> derivative² -> 150 ms moving integration -> adaptive threshold,
250 ms refractory period, the R peak is the extreme of the baseline-removed signal near each detection (either polarity: a watch worn
on the right wrist or with the crown on the other side inverts the lead).

Storage: rounded to 1 µV as little-endian int16 (QRS is about 1,000 µV) and zlib-compressed: about 20–25 KB per recording.
Name and date of birth in the export CSV header are never read.
"""
import re
import statistics
import struct
import zlib
from dataclasses import dataclass, field
from datetime import datetime

from . import hrv

# HKElectrocardiogram.Classification raw names; the export CSV writes English labels (also used as fallback text)
CLASS_LABELS = {
    "sinus rhythm": "sinusRhythm",
    "atrial fibrillation": "atrialFibrillation",
    "heart rate over 120": "inconclusiveHighHeartRate", "heart rate over 150": "inconclusiveHighHeartRate",
    "high heart rate": "inconclusiveHighHeartRate",
    "heart rate under 50": "inconclusiveLowHeartRate", "low heart rate": "inconclusiveLowHeartRate",
    "poor recording": "inconclusivePoorReading", "inconclusive": "inconclusiveOther",
    "窦性心律": "sinusRhythm", "房颤": "atrialFibrillation", "心房颤动": "atrialFibrillation",
    "记录不佳": "inconclusivePoorReading", "无法判断": "inconclusiveOther",
}
CLASS_ZH = {"sinusRhythm": "窦性心律", "atrialFibrillation": "房颤迹象", "inconclusiveHighHeartRate": "心率过高，无法判断",
            "inconclusiveLowHeartRate": "心率过低，无法判断", "inconclusivePoorReading": "记录不佳",
            "inconclusiveOther": "无法判断", "unrecognized": "未识别", "notSet": "未分类"}
CLASS_EN = {"sinusRhythm": "Sinus rhythm", "atrialFibrillation": "Signs of AFib", "inconclusiveHighHeartRate": "Inconclusive: high heart rate",
            "inconclusiveLowHeartRate": "Inconclusive: low heart rate", "inconclusivePoorReading": "Poor recording",
            "inconclusiveOther": "Inconclusive", "unrecognized": "Unrecognized", "notSet": "Not classified"}   # issue #42
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}")
HZ_RE = re.compile(r"([\d.]+)\s*(hertz|hz|赫兹)", re.I)
MAX_SAMPLES = 512 * 60          # a recording is 30 s; anything far longer is not an Apple ECG


class EcgError(ValueError):
    pass


@dataclass
class Ecg:
    start_at: datetime
    sample_hz: float
    voltage_uv: list[float]
    classification: str = "notSet"
    classification_label: str | None = None
    symptoms: str | None = None
    device: str | None = None
    software_version: str | None = None
    avg_hr: float | None = None                 # Apple's averageHeartRate (only from HealthKit)
    meta: dict = field(default_factory=dict)


def classify(label: str | None) -> str:
    s = (label or "").strip().lower()
    if not s:
        return "notSet"
    for k, v in CLASS_LABELS.items():
        if k in s:
            return v
    return "unrecognized"


def parse_csv(text: str) -> Ecg:
    """electrocardiograms/ecg_YYYY-MM-DD*.csv from the Health export."""
    head: dict[str, str] = {}
    values: list[float] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if values or re.fullmatch(r"-?\d+(\.\d+)?", line):
            try:
                values.append(float(line))
            except ValueError:
                pass
            continue
        key, _, val = line.partition(",")
        head[key.strip().lower()] = val.strip().strip('"')
    ts = next((m.group(0) for v in head.values() if (m := TS_RE.search(v))), None)
    hz = next((float(m.group(1)) for v in head.values() if (m := HZ_RE.search(v))), None)
    if ts is None or hz is None or not values:
        raise EcgError("不是 Apple 心电图导出的 csv（缺记录时间、采样率或电压数据）")
    if len(values) > MAX_SAMPLES:
        raise EcgError("心电图太长，不是 Apple Watch 的 30 秒记录")
    label = head.get("classification") or head.get("分类")
    return Ecg(start_at=datetime.strptime(ts, "%Y-%m-%d %H:%M:%S %z"), sample_hz=hz, voltage_uv=values,
               classification=classify(label), classification_label=label or None,
               symptoms=head.get("symptoms") or head.get("症状") or None,
               device=head.get("device") or head.get("设备") or None,
               software_version=head.get("software version") or head.get("软件版本") or None)


def pack(voltage_uv: list[float]) -> bytes:
    ints = [max(-32768, min(32767, round(v))) for v in voltage_uv]
    return zlib.compress(struct.pack(f"<{len(ints)}h", *ints), 9)


def unpack(blob: bytes) -> list[int]:
    raw = zlib.decompress(blob)
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


def _moving_mean(x: list[float], w: int) -> list[float]:
    if w <= 1:
        return list(x)
    out, s, half = [], 0.0, w // 2
    pref = [0.0]
    for v in x:
        s += v
        pref.append(s)
    n = len(x)
    for i in range(n):
        a, b = max(0, i - half), min(n, i + half + 1)
        out.append((pref[b] - pref[a]) / (b - a))
    return out


def r_peaks(x: list[float], hz: float) -> list[int]:
    """Indexes of R peaks."""
    n = len(x)
    if n < hz * 3:
        return []
    base = _moving_mean(x, int(0.6 * hz))
    f = [a - b for a, b in zip(x, base)]
    d = [0.0] + [(f[i + 1] - f[i - 1]) for i in range(1, n - 1)] + [0.0]
    integ = _moving_mean([v * v for v in d], int(0.15 * hz))
    ref = sorted(integ)[int(0.98 * (n - 1))]
    if ref <= 0:
        return []
    thr, refractory, win = 0.3 * ref, int(0.25 * hz), int(0.08 * hz)
    cands: list[int] = []
    i = 0
    while i < n:
        if integ[i] >= thr:
            j = i
            while j < n and integ[j] >= thr:
                j += 1
            cands.append(max(range(i, j), key=lambda k: integ[k]))
            i = j
        else:
            i += 1
    if not cands:
        return []
    # polarity: compare the typical positive vs negative extreme around the detections
    pos = statistics.median(max(f[max(0, c - win):c + win + 1]) for c in cands)
    neg = statistics.median(-min(f[max(0, c - win):c + win + 1]) for c in cands)
    sign = 1 if pos >= neg else -1
    peaks: list[int] = []
    for c in cands:
        lo, hi = max(0, c - win), min(n, c + win + 1)
        p = max(range(lo, hi), key=lambda k: sign * f[k])
        if peaks and p - peaks[-1] < refractory:
            if sign * f[p] > sign * f[peaks[-1]]:
                peaks[-1] = p
            continue
        peaks.append(p)
    return _prune(peaks, [sign * v for v in f])


def _prune(peaks: list[int], g: list[float]) -> list[int]:
    """Drop T waves / noise picked up as beats: amplitude < 50% of the median R amplitude, or closer than 60% of the median RR
    to the previous kept peak (then the smaller of the two goes)."""
    if len(peaks) < 4:
        return peaks
    amp = statistics.median(g[p] for p in peaks)
    peaks = [p for p in peaks if g[p] >= 0.5 * amp]
    if len(peaks) < 4:
        return peaks
    med_rr = statistics.median(b - a for a, b in zip(peaks, peaks[1:]))
    kept = [peaks[0]]
    for p in peaks[1:]:
        if p - kept[-1] < 0.6 * med_rr:
            if g[p] > g[kept[-1]]:
                kept[-1] = p
            continue
        kept.append(p)
    return kept


def derive(voltage_uv: list[float], hz: float) -> dict:
    """R peaks -> heart rate, RR, RMSSD and a quality flag (not a diagnosis)."""
    peaks = r_peaks(voltage_uv, hz)
    beats = [p / hz for p in peaks]
    res = hrv.analyze(beats)
    rr = [round((b - a) * 1000) for a, b in zip(beats, beats[1:])]
    good = res.n_valid >= 10 and res.artifact_ratio <= 0.2
    return {"algo": "ecg-r-v1", "r_peaks": peaks, "rr_ms": rr, "n_beats": len(peaks),
            "hr": round(60000 / statistics.median(rr), 1) if rr else None,
            "rmssd": res.rmssd if good else None, "sdnn": res.sdnn if good else None,
            "artifact_ratio": res.artifact_ratio, "quality": "ok" if good else "poor"}
