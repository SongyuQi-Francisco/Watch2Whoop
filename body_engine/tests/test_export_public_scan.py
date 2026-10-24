"""tools/export_public.py --scan: the repository address in the two READMEs is the only place the owner name may appear
(PRIVATE_WORD_ALLOW, PR #245). Uses a fictional owner as the private word; standard library only, no database."""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
OWNER = "ExampleOwner"
PRIVATE = {"deny": [OWNER.lower()], "replace": []}


@pytest.fixture(scope="module")
def ep():
    spec = importlib.util.spec_from_file_location("export_public", REPO / "tools" / "export_public.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["export_public"] = mod
    spec.loader.exec_module(mod)
    return mod


def _hits(ep, tmp_path: Path, rel: str, line: str) -> list[str]:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(line + "\n", encoding="utf-8")
    return [x for x in ep.scan(tmp_path, PRIVATE, []) if x.startswith(f"{rel}:") and "private word" in x]


@pytest.mark.parametrize("rel", ["README.md", "README.zh-CN.md"])
@pytest.mark.parametrize("line", [
    f"git clone https://github.com/{OWNER}/Watch2Whoop.git",
    f'<a href="https://github.com/{OWNER}/Watch2Whoop/stargazers">'
    f'<img src="https://img.shields.io/github/stars/{OWNER}/Watch2Whoop?style=social" alt="Stars"></a>',
    f"[issues](https://github.com/{OWNER}/Watch2Whoop/issues)",
])
def test_repo_address_in_readmes_is_allowed(ep, tmp_path, rel, line):
    assert _hits(ep, tmp_path, rel, line) == []


@pytest.mark.parametrize("rel, line", [
    ("docs/guide.md", f"git clone https://github.com/{OWNER}/Watch2Whoop.git"),   # same address, other file
    ("docs/README.md", f"git clone https://github.com/{OWNER}/Watch2Whoop.git"),
    ("README/notes.md", f"git clone https://github.com/{OWNER}/Watch2Whoop.git"),
    ("README.md", f"Maintained by {OWNER}."),                                      # the name on its own
    ("README.md", f"https://github.com/{OWNER}/Watch2Whoop by {OWNER}"),           # allowed address + the name again
    ("README.md", f"https://github.com/{OWNER}/OtherRepo"),                        # another repository
    ("README.md", f"https://{OWNER}.github.io/Watch2Whoop"),                       # another shape
])
def test_owner_name_elsewhere_is_still_a_finding(ep, tmp_path, rel, line):
    assert _hits(ep, tmp_path, rel, line), (rel, line)
