"""Electronic consent: the full consent text is in consent.md (identical to docs/family-consent.md, checked by tests); the version number is on its 「版本：」 ("Version:") line.
English: consent.en.md (identical to docs/family-consent.en.md), clause by clause the same text with the same version number (tests check
both pairs and the version). Shown in the request's language (X-Body-Lang); the signing record keeps which language was read (user_consents.language).

- Consent required = role is member and users.consent_at is empty. The owner (the deployer) does not need it.
- Until consent is given, this person's health data is not processed (gate(): import / scoring commands skip; history import is refused). Data synced to OW by the app and uploaded by the iOS Shortcut is stored as-is,
  and processing starts at the next hourly job after consent (import-ow pulls from the earliest data when there is no cursor; import-shortcut imports files not yet imported).
- After the consent form is revised (version changed), people who agreed to an older version are asked to reconfirm in the app, but data processing is not interrupted (only people who never consented are blocked).
"""
import re
from functools import lru_cache
from importlib import resources

import psycopg

from ..i18n import L, is_en

# Key points shown in the dialog (correspond to sections 1-5 of the full text; change together with it)
SUMMARY = [
    "同步你在「健康」里授权的数据（心率、睡眠、训练、步数等），加上你自己记录的内容，用来算恢复分、睡眠分、负荷。",
    "数据存在管理员的服务器，HTTPS 加密传输，每晚加密备份；不卖、不给任何第三方。",
    "只有你自己能看。家人默认看不到；管理员只在维护、排错、按你的要求导出 / 删除时接触。",
    "AI 默认不开；你自己接入 AI 后，回答问题时会把需要的那部分数据发给你选的 AI 公司。"
    "也可以用管理员提供的托底 AI（DeepSeek，服务器在中国），第一次用前会单独请你确认。",
    "随时可以改资料、要求导出全部数据、删除账号（备份里的旧副本 6 个月内消失）、撤回同意。",
    "这是家庭自建的测试系统，不是医疗设备，分数只供参考。",
]


SUMMARY_EN = [
    "Syncs the data you allow in Apple Health (heart rate, sleep, workouts, steps and so on), plus what you log yourself, "
    "to calculate recovery, sleep score and strain.",
    "The data is stored on the administrator's server, sent over HTTPS and backed up encrypted every night; never sold or "
    "given to any third party.",
    "Only you can see it. Family members cannot by default; the administrator touches it only for maintenance, "
    "troubleshooting, or exporting / deleting it at your request.",
    "AI is off by default; once you connect your own AI, the part of your data an answer needs is sent to the AI company "
    "you chose. You can also use the backup AI the administrator provides (DeepSeek, servers in China); you are asked "
    "separately before the first use.",
    "Any time you can change your profile, ask for an export of all your data, delete your account (old copies in backups "
    "disappear within 6 months) or withdraw consent.",
    "This is a test system built by a family, not a medical device; the scores are for reference only.",
]
FILES = {"zh": "consent.md", "en": "consent.en.md"}
VERSION_LINE = {"zh": r"^版本：\s*(\S+)", "en": r"^Version:\s*(\S+)"}


def lang() -> str:
    return "en" if is_en() else "zh"


@lru_cache(maxsize=2)
def text(language: str = "zh") -> str:
    return resources.files("body_engine.profile").joinpath(FILES[language]).read_text(encoding="utf-8")


def version(language: str = "zh") -> str:
    m = re.search(VERSION_LINE[language], text(language), re.M)
    if not m:
        raise RuntimeError(f"{FILES[language]} has no version line")
    return m.group(1)


def summary(language: str | None = None) -> list[str]:
    return SUMMARY_EN if (language or lang()) == "en" else SUMMARY


def display_text(language: str | None = None) -> str:
    """Full text shown to the user, in the request's language: strips the leading quoted note addressed to the admin."""
    return "\n".join(l for l in text(language or lang()).splitlines() if not l.startswith(">")).strip() + "\n"


def status(conn: psycopg.Connection, user_id: int) -> dict:
    u = conn.execute("SELECT role, consent_at, consent_version FROM users WHERE id = %s", (user_id,)).fetchone()
    cur = version()
    required = u["role"] != "owner" and u["consent_at"] is None
    last = conn.execute("SELECT language FROM user_consents WHERE user_id = %s ORDER BY accepted_at DESC, id DESC LIMIT 1",
                        (user_id,)).fetchone()
    return {"required": required, "version": cur, "accepted_at": u["consent_at"], "accepted_version": u["consent_version"],
            "accepted_language": last["language"] if last else None,
            "reconfirm": (not required and u["role"] != "owner" and u["consent_version"] not in (None, cur, "paper"))}


def needs_consent(conn: psycopg.Connection, user_id: int) -> bool:
    u = conn.execute("SELECT role, consent_at FROM users WHERE id = %s", (user_id,)).fetchone()
    return u is not None and u["role"] != "owner" and u["consent_at"] is None


def accept(conn: psycopg.Connection, user_id: int, ver: str, method: str = "app", user_agent: str | None = None,
           at=None, language: str | None = None) -> dict:
    """`language` = the text the person read (zh / en); default the request's language. Both texts carry the same version."""
    if method == "app" and ver != version():
        raise ValueError(L(f"同意书已经更新到 {version()}，请刷新后再看一遍",
                           f"The consent was updated to {version()}; reload and read it again"))
    language = language if language in FILES else lang()
    with conn.transaction():
        conn.execute("""INSERT INTO user_consents (user_id, version, method, accepted_at, user_agent, language)
                        VALUES (%s, %s, %s, COALESCE(%s, now()), %s, %s)""",
                     (user_id, ver, method, at, (user_agent or "")[:300] or None, language))
        conn.execute("""UPDATE users SET consent_at = COALESCE(%s, now()), consent_version = %s WHERE id = %s""",
                     (at, ver, user_id))
    return status(conn, user_id)


SKIP_MESSAGE = "user {} 还没在 app 里同意（同意书 docs/family-consent.md）：健康数据暂存、不处理"


def gate(conn: psycopg.Connection, user_id: int) -> str | None:
    """Call at the start of commands that process health data: returns a skip message if consent is missing (the caller returns directly, no error)."""
    return SKIP_MESSAGE.format(user_id) if needs_consent(conn, user_id) else None
