"""What shipped, in words a user would use. Kept by hand: every pull request that changes something a user can see adds one
entry here (CONTRIBUTING.md, "What's new").

How to write one
- `day`: the day it reached users (the merge / deploy day), not the day you wrote it; newest first, and within a day the
  one most people will notice first.
- `zh` / `en`: ONE short sentence each, spoken, no full stop in the middle. No jargon: no PR or issue numbers, no feature
  flag names, no words like API, token or server (tests/test_whats_new.py checks length and these words).
- `url` (optional): a path inside the Today page, e.g. "/app/?tab=me"; tapping the entry in the sheet goes there.
- Changes only some people can see yet (a feature behind a switch that is not on for everyone) wait until they are.

Sent weekly to each person (`body-engine whats-new send`): what is new since their last message, never from before
their account existed. The Today page lists all of it under 我 → 设置 → 更新记录.
"""
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Entry:
    day: date
    zh: str
    en: str
    url: str | None = None


ENTRIES: tuple[Entry, ...] = (
    Entry(date(2026, 10, 28), "「我」页可以手动记体重，还能标上空腹、饭后等测量情况。",
          "You can log your weight by hand under Me, and note things like fasting or after a meal.", "/app/?tab=me"),
    Entry(date(2026, 10, 27), "手机同步更稳了：第一次只读最近 14 天，几分钟就能看到数据。",
          "Phone sync is steadier: the first sync reads the last 14 days, so your data shows up in minutes.", "/app/?tab=today"),
    Entry(date(2026, 10, 27), "按住说话改用手机自带的语音识别，边说边出字。",
          "Hold-to-talk now uses your phone's own speech recognition and shows words as you speak."),
    Entry(date(2026, 10, 27), "英文界面里漏掉的中文都补上了。",
          "Chinese text that was missing from the English screens is now translated."),
    Entry(date(2026, 10, 26), "今天页可以一键开启同步，还会说清同步卡在哪一步。",
          "You can turn on sync with one tap on the Today page, and it tells you where sync is stuck.", "/app/?tab=today"),
    Entry(date(2026, 10, 26), "今天页打开更快，切换页面也更顺。",
          "The Today page opens faster and switching pages feels smoother."),
    Entry(date(2026, 10, 25), "手机同步断了会提醒你，点一下就能重新连接。",
          "You get a heads-up when phone sync stops, and one tap reconnects it."),
    Entry(date(2026, 10, 25), "手表有新数据，几分钟内就能出分。",
          "New watch data turns into scores within minutes."),
    Entry(date(2026, 10, 25), "还没有恢复分的时候，今天页会说清原因和下一步。",
          "When there is no recovery score yet, the Today page explains why and what to do next."),
    Entry(date(2026, 10, 25), "App 里的链接可以直接打开了，一年的趋势图也会标出年份。",
          "Links now open from inside the app, and year-long trend charts show the year."),
)


def between(after: date, through: date, entries: tuple[Entry, ...] | None = None) -> list[Entry]:
    """Entries dated after `after` up to and including `through`, newest first (file order within a day)."""
    return sorted((e for e in (ENTRIES if entries is None else entries) if after < e.day <= through), key=lambda e: e.day, reverse=True)


def weeks(through: date, count: int, entries: tuple[Entry, ...] | None = None) -> list[dict]:
    """The last `count` calendar weeks (Monday start) up to the one holding `through`, newest first; empty weeks are left out."""
    first = through - timedelta(days=through.weekday() + 7 * (count - 1))
    out: dict[date, list[Entry]] = {}
    for e in between(first - timedelta(days=1), through, entries):
        out.setdefault(e.day - timedelta(days=e.day.weekday()), []).append(e)
    return [{"week_start": w, "items": items} for w, items in sorted(out.items(), reverse=True)]
