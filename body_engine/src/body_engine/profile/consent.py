"""Electronic consent: the full consent text is in consent.md (identical to docs/family-consent.md, checked by tests); the version number is on its 「版本：」 ("Version:") line.
English: consent.en.md (identical to docs/family-consent.en.md), clause by clause the same text with the same version number (tests check
both pairs and the version). Shown in the request's language (X-Body-Lang); the signing record keeps which language was read (user_consents.language).

- Consent required = role is member and users.consent_at is empty. The owner (the deployer) does not need it.
- Until consent is given, this person's health data is not processed (gate(): import / scoring commands skip; history import is refused). Data synced to OW by the app and uploaded by the iOS Shortcut is stored as-is,
  and processing starts at the next hourly job after consent (import-ow pulls from the earliest data when there is no cursor; import-shortcut imports files not yet imported).
- After the consent form is revised (version changed), people who agreed to an older version are asked to reconfirm in the app, but data processing is not interrupted (only people who never consented are blocked).
- One-tap consent (rollout flag consent_one_tap): the first-run screen is a single 「开始使用」 button with one line naming this
  text (《数据与隐私说明》) and, when the fallback AI is set up, the Jarvis terms (ai/fallback.py CONSENT_POINTS, 《Jarvis AI 说明》);
  both open in full. One tap records this consent (method one_tap, the current version, the page's language) and the Jarvis
  consent (fallback.accept_consent, its current CONSENT_VERSION, method one_tap) in one transaction; reconfirming a revised
  text works the same way. Neither text nor version changes, and the gate above is the same: nothing is processed before the
  tap, and after it everything works as after the electronic consent (users.consent_at / consent_version are set the same way).
"""
import re
from functools import lru_cache
from importlib import resources

import psycopg

from ..i18n import L, is_en

# Key points shown in the dialog (correspond to sections 1-4 of the full text; never promise anything the full text does
# not say; change together with it)
SUMMARY = [
    "同步你在「健康」里授权的数据（心率、睡眠、训练、步数、心电图和心跳间期等），加上你自己记录的内容，用来算恢复分、睡眠分、负荷。",
    "只有你自己能看。家人默认看不到，你在 app 里加入圈子或请管理员开通共享后，指定的人才能看指定的内容；"
    "心电图还要另外单独授权。管理员作为服务器的维护者，技术上能接触到数据库。",
    "加入圈子（朋友页）时由你选给圈子成员看什么（默认：评分、训练、饮食达标率），随时可改、可退出；心电图永远不进圈子。",
    "AI 默认不开；你自己接入 AI 后，回答问题时会把需要的那部分数据发给你选的 AI 公司。"
    "也可以用管理员提供的托底 AI（DeepSeek），第一次用前会单独请你确认。",
    "AI 发现系统的问题或听到你想要的新功能，会自动报告给管理员（去掉健康数值、名字和联系方式）；"
    "在「我 → 反馈记录」查看，也可以改成先问你或关闭。",
    "随时可以改资料、删除账号（备份里的旧副本 6 个月内消失）、撤回同意。",
    "这是健康管理 app，不是医疗设备，分数只供参考；心电图只展示 Apple 的结果，不是诊断。",
]


SUMMARY_EN = [
    "Syncs the data you allow in Apple Health (heart rate, sleep, workouts, steps, ECGs and beat-to-beat intervals and so on), "
    "plus what you log yourself, "
    "to calculate recovery, sleep score and strain.",
    "Only you can see it. Family members cannot by default; once you join a circle in the app or ask the administrator to "
    "set up sharing, the named people can see the named content. ECGs need a separate permission. As the person who "
    "maintains the server, the administrator can technically access the database.",
    "When you join a circle (Friends page) you choose what its members see (by default: scores, workouts, nutrition "
    "adherence); change it or leave at any time. ECGs are never shared through circles.",
    "AI is off by default; once you connect your own AI, the part of your data an answer needs is sent to the AI company "
    "you chose. You can also use the backup AI the administrator provides (DeepSeek); you are asked separately before "
    "the first use.",
    "When the AI finds a problem in the system or hears about a feature you want, it reports it to the administrator by "
    "itself (without health values, names or contact details); see it under Me → Feedback, or switch to ask-first or off.",
    "Any time you can change your profile, delete your account (old copies in backups disappear within 6 months) or "
    "withdraw consent.",
    "This is a health management app, not a medical device; the scores are for reference only, and ECGs only show "
    "Apple's results, not a diagnosis.",
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


BASE_SETTING = "consent_base_version"
ONE_TAP_FLAG = "consent_one_tap"
METHODS = ("app", "one_tap", "paper")   # user_consents.method (CHECK in migrations 140 / 680)


def vkey(v: str) -> tuple:
    """Sort key of a consent version YYYY-MM-DD[.N]: by date, then N as a number (so .10 comes after .9)."""
    day, _, n = v.partition(".")
    return (day, int(n) if n.isdigit() else 0, n)


def newest(*versions: str) -> str:
    return max(versions, key=vkey)


def base_version(conn: psycopg.Connection) -> str:
    """The version everyone must have agreed to: rollout_settings.consent_base_version, else the text's own version.
    Pin it to the previous version while a new text goes to a cohort first (docs/rollout.md)."""
    from ..rollout import flags
    pinned = flags.get_setting(conn, BASE_SETTING)
    return version() if not pinned or vkey(pinned) > vkey(version()) else pinned


def required_version(conn: psycopg.Connection, user_id: int) -> str:
    """max(base version, min_consent_version of every flag that targets this user), never newer than the text itself.
    Without a pinned base and flag requirements this is the text's version, i.e. the behaviour before flags existed."""
    from ..rollout import flags
    need = newest(base_version(conn), *flags.consent_versions_needed(conn, user_id))
    return version() if vkey(need) > vkey(version()) else need


def status(conn: psycopg.Connection, user_id: int) -> dict:
    u = conn.execute("SELECT role, consent_at, consent_version FROM users WHERE id = %s", (user_id,)).fetchone()
    cur = version()
    required = u["role"] != "owner" and u["consent_at"] is None
    need = required_version(conn, user_id)
    last = conn.execute("SELECT language, method FROM user_consents WHERE user_id = %s ORDER BY accepted_at DESC, id DESC LIMIT 1",
                        (user_id,)).fetchone()
    agreed = u["consent_version"]
    return {"required": required, "version": cur, "required_version": need, "accepted_at": u["consent_at"],
            "accepted_version": agreed, "accepted_language": last["language"] if last else None,
            "accepted_method": last["method"] if last else None,
            # paper consent is never asked again in the app (the admin collects a new signature)
            "reconfirm": (not required and u["role"] != "owner" and agreed not in (None, "paper") and vkey(agreed) < vkey(need)),
            "one_tap": u["role"] != "owner" and one_tap_on(conn, user_id)}   # the page asks with the one-tap screen


def needs_consent(conn: psycopg.Connection, user_id: int) -> bool:
    u = conn.execute("SELECT role, consent_at FROM users WHERE id = %s", (user_id,)).fetchone()
    return u is not None and u["role"] != "owner" and u["consent_at"] is None


def one_tap_on(conn: psycopg.Connection, user_id: int) -> bool:
    """Rollout flag consent_one_tap. It has no min_consent_version, so `--state all` reaches people who never consented."""
    from ..rollout import flags
    return flags.enabled(conn, user_id, ONE_TAP_FLAG)


def accept(conn: psycopg.Connection, user_id: int, ver: str, method: str = "app", user_agent: str | None = None,
           at=None, language: str | None = None) -> dict:
    """`language` = the text the person read (zh / en); default the request's language. Both texts carry the same version.
    method: app (read the sheet, ticked the box), one_tap (the one-tap screen) or paper (recorded by the admin)."""
    if method not in METHODS:
        raise ValueError(f"unknown consent method {method!r}")
    if method in ("app", "one_tap") and ver != version():
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


def agreed_at_least(conn: psycopg.Connection, user_id: int, minimum: str) -> bool:
    """The owner, or someone who agreed in the app to version `minimum` or later (versions are YYYY-MM-DD.N, compared as
    vkey). Paper consent ("paper") and older versions do not count: they never named what `minimum` added."""
    u = conn.execute("SELECT role, consent_at, consent_version FROM users WHERE id = %s", (user_id,)).fetchone()
    if u is None:
        return False
    if u["role"] == "owner":
        return True
    v = u["consent_version"]
    return u["consent_at"] is not None and v not in (None, "paper") and vkey(v) >= vkey(minimum)


SKIP_MESSAGE = "user {} 还没在 app 里同意（同意书 docs/family-consent.md）：健康数据暂存、不处理"


def gate(conn: psycopg.Connection, user_id: int) -> str | None:
    """Call at the start of commands that process health data: returns a skip message if consent is missing (the caller returns directly, no error)."""
    return SKIP_MESSAGE.format(user_id) if needs_consent(conn, user_id) else None
