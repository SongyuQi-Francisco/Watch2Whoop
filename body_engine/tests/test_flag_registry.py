"""The flag registry stays honest (retrospective 2026-11, action 8): every key has a kind and an added date, code checks
only declared keys, and every declared key is read by some code. No database."""
import re
from datetime import date
from pathlib import Path

from body_engine.rollout.registry import FLAG_META, FLAGS, KINDS

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "body_engine" / "src" / "body_engine"
STATIC = SRC / "web" / "static"

# Declared keys whose only reader is the frozen web page (web/static) or its native-bridge: no Python or Swift code checks
# them. They stay until that page goes; the test fails when one gains a reader elsewhere (remove it here) or loses its
# reader in web/static (delete the key).
WEB_ONLY = {"intro_v2", "records_v2", "cached_first_paint", "tab_prefetch", "local_first_start_off", "native_jarvis", "native_today"}


def _read(paths) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in paths)


def _py_src() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if p.name != "registry.py"]


def _quoted(key: str, text: str) -> bool:
    return f'"{key}"' in text or f"'{key}'" in text


def test_every_flag_has_a_kind_and_an_added_date():
    assert set(FLAG_META) == set(FLAGS), (sorted(set(FLAGS) - set(FLAG_META)), sorted(set(FLAG_META) - set(FLAGS)))
    for key, (kind, added) in FLAG_META.items():
        assert kind in KINDS, (key, kind)
        assert date.fromisoformat(added) <= date.today(), (key, added)


def test_code_only_checks_declared_flags():
    """Literal keys passed to enabled() / guard() / require() / set_flag() in the Python source are all declared
    (flags.py raises KeyError at run time too, but only on the path that runs)."""
    call = re.compile(r"\b(?:enabled|guard|require|set_flag|flag_on)\(([^()]*)\)")
    keyish = re.compile(r"""["']([a-z][a-z0-9]*(?:_[a-z0-9]+)+)["']""")
    unknown = {}
    for p in _py_src():
        if p.parent.name == "rollout" and p.name in ("flags.py", "cli.py"):
            continue   # the machinery itself takes keys as variables / CLI arguments
        for m in call.finditer(p.read_text(encoding="utf-8")):
            for k in keyish.findall(m.group(1)):
                if k not in FLAGS and k not in ("user_id",):
                    unknown[(p.name, k)] = True
    # a literal that is not a flag key but sits in such a call (e.g. a table or role name) goes here with a reason
    assert not unknown, sorted(unknown)


def test_every_declared_flag_is_read_by_some_code():
    py = _read(_py_src())
    swift = _read([p for p in (ROOT / "apps" / "ios").rglob("*") if p.suffix in (".swift", ".py", ".json") and p.is_file()])
    web = _read(list(STATIC.rglob("*.js")) + list(STATIC.rglob("*.html")))
    unread = [k for k in FLAGS if not _quoted(k, py) and not _quoted(k, swift) and k not in WEB_ONLY]
    assert not unread, f"no code reads {unread}: delete the key (and its database row) or say why"
    for k in WEB_ONLY:
        assert k in FLAGS, f"{k} left the registry: remove it from WEB_ONLY"
        assert not _quoted(k, py) and not _quoted(k, swift), f"{k} has a Python / Swift reader now: remove it from WEB_ONLY"
        assert _quoted(k, web), f"{k} is not read by web/static either: delete the key"
