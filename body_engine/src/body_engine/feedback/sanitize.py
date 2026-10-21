"""Server-side sanitising of AI feedback before anything leaves the server (docs/ai-feedback-plan.md §2).

The root rule already tells the AI not to write health values, names or contact details; this is the safety net that does
not depend on the AI obeying. Conservative on purpose: a redacted bug report is still useful, a leaked one cannot be undone.
Replaced with placeholders:
- e-mail addresses, URLs and app links (whole), IP addresses, phone numbers, token-like strings (letters + digits, ≥ 16);
- a number with a health / body unit (kg, bpm, ms, mmol/L, %, kcal, hours, …) and a number right after a health word
  (体重, 心率, HRV, 尿酸, 恢复分, …), blood-pressure style pairs, a date after 生日 / 出生;
- every user's display name (all users of this server, not only the reporter) and every known e-mail.
Dates, tool names, page names, HTTP codes and plain counts stay: they are what makes a report reproducible.
"""
from __future__ import annotations

import re

P_EMAIL, P_LINK, P_CODE, P_PHONE, P_IP, P_VALUE, P_PERSON = (
    "‹邮箱›", "‹链接›", "‹码›", "‹电话›", "‹IP›", "‹数值›", "‹某人›")
PLACEHOLDER_RE = re.compile(r"‹[^›]{1,4}›")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LINK = re.compile(r"\b(?:https?|watch2whoop|shortcuts)://\S+", re.I)
_IP = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")
_TOKEN = re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])")
_PHONE = re.compile(r"(?<![A-Za-z0-9_.])\+?\d[\d \-]{7,}\d(?![A-Za-z0-9_.])")
_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")
_NUM = r"\d+(?:[.,]\d+)?"
# Units of body / health values; longest first so "kcal" is not read as "k" + "cal", "mmol/L" before "mmol"
_UNITS = sorted("""kg 公斤 斤 lb lbs bpm 次/分 次/分钟 ms 毫秒 mmol/L mmol μmol/L µmol/L umol/L μmol µmol umol mg/dL mg/dl
    mg g 克 kcal 千卡 大卡 卡 kJ % 小时 h hr 分钟 min cm 厘米 mm ml mL 毫升 L 升 步 岁 ℃ °C 度 U/L IU/L ng/mL pg/mL
    mmHg km 公里 cal""".split(), key=len, reverse=True)
_UNIT_ALT = "|".join(re.escape(u) for u in _UNITS)
_UNIT_RE = re.compile(rf"(?<![A-Za-z0-9_.])(?<!第)(?<!第 ){_NUM}\s*(?:{_UNIT_ALT})(?![A-Za-z])")
_HEALTH_WORDS = sorted("""体重 体脂 体脂率 BMI 腰围 心率 静息心率 最高心率 平均心率 HRV 心率变异性 血氧 SpO2 呼吸 呼吸频率 体温 手腕温度
    恢复 恢复分 睡眠 睡眠分 睡眠时长 负荷 strain 压力 尿酸 ALT AST GGT 甘油三酯 胆固醇 LDL HDL 血糖 糖化 HbA1c 血压 肌酐 VO2max VO₂max
    步数 热量 蛋白质 碳水 脂肪 饱和脂肪 纤维 年龄 身高 分数 得分 评分 身体年龄 recovery sleep weight""".split(), key=len, reverse=True)
_HEALTH_RE = re.compile(rf"((?:{'|'.join(re.escape(w) for w in _HEALTH_WORDS)})\s*(?:是|为|=|:|：)?\s*(?:约|大概)?\s*)"
                        rf"{_NUM}(?:\s*[-~–/]\s*{_NUM})?(?:\s*(?:{_UNIT_ALT})(?![A-Za-z]))?", re.I)
_PAIR = re.compile(r"(?<![\d.])\d{2,3}\s*/\s*\d{2,3}(?![\d.])")     # 120/80
_BIRTH = re.compile(r"((?:生日|出生|出生日期|birthday)\s*(?:是|为|:|：)?\s*)\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", re.I)


def _names_re(names: list[str]) -> re.Pattern | None:
    parts = []
    for n in sorted({(n or "").strip() for n in names}, key=len, reverse=True):
        if len(n) < 2 or n in ("me", "测试", "用户", "管理员"):
            continue
        e = re.escape(n)
        parts.append(rf"(?<![A-Za-z]){e}(?![A-Za-z])" if n.isascii() else e)
    return re.compile("|".join(parts), re.I) if parts else None


class Sanitizer:
    """sanitize(text) -> (clean text, number of replacements). names / emails: every user of the server."""

    def __init__(self, names: list[str] = (), emails: list[str] = ()):
        self.names = _names_re(list(names))
        self.emails = [e.lower() for e in emails if e]

    def sanitize(self, text: str | None) -> tuple[str, int]:
        if not text:
            return "", 0
        s = str(text)
        count = 0

        def sub(rx, repl, s):
            nonlocal count
            out, n = rx.subn(repl, s)
            count += n
            return out

        s = sub(_LINK, P_LINK, s)
        s = sub(_EMAIL, P_EMAIL, s)
        for e in self.emails:   # an address the regex missed (unusual characters)
            if e in s.lower():
                s, n = re.subn(re.escape(e), P_EMAIL, s, flags=re.I)
                count += n
        s = sub(_IP, P_IP, s)
        s = sub(_TOKEN, P_CODE, s)
        s = sub(_BIRTH, lambda m: m.group(1) + P_VALUE, s)

        def phone(m):
            nonlocal count
            v = m.group(0)
            if _DATE.search(v) or sum(c.isdigit() for c in v) < 9:
                return v
            count += 1
            return P_PHONE
        s = _PHONE.sub(phone, s)
        s = sub(_HEALTH_RE, lambda m: m.group(1) + P_VALUE, s)
        s = sub(_UNIT_RE, P_VALUE, s)
        s = sub(_PAIR, P_VALUE, s)
        if self.names:
            s = sub(self.names, P_PERSON, s)
        return s, count


def placeholder_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(len(m.group(0)) for m in PLACEHOLDER_RE.finditer(text)) / len(text)


def from_db(conn) -> Sanitizer:
    names = [r["display_name"] for r in conn.execute("SELECT display_name FROM users WHERE display_name IS NOT NULL")]
    try:
        with conn.transaction():
            emails = [r["email"] for r in conn.execute("SELECT DISTINCT email FROM user_identities WHERE email IS NOT NULL")]
    except Exception:   # a database without self-signup (migration 295)
        emails = []
    return Sanitizer(names, emails)
