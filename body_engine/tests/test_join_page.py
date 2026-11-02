"""The circle invite landing page (web/static/join.html, join.js) and the handbook the app opens: static, no API call and none
of the Today page's scripts, so the forced upgrade (client/policy.py) never stands in front of them. test_upgrade.py checks the
same pages over HTTP after the block date. No database."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"


def test_landing_page_is_static():
    html = (STATIC / "join.html").read_text(encoding="utf-8")
    assert re.findall(r'<script src="([^"]+)"', html) == ["i18n.js", "i18n-en.js", "join.js"]
    assert "<script>" not in html, "inline scripts are blocked by the page's CSP"
    js = (STATIC / "join.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    assert "fetch(" not in code and "/v1/" not in code and "XMLHttpRequest" not in code, "no API call: the 426 can't reach it"
    assert "localStorage" not in code


@pytest.mark.parametrize("page", ["handbook/index.html", "handbook/en/index.html"])
def test_handbook_is_static(page):
    html = (STATIC / page).read_text(encoding="utf-8")
    assert "upgrade.js" not in html and "/v1/" not in html and "app.js" not in html


def test_landing_page_logic():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = (f"const J = require({json.dumps(str(STATIC / 'join.js'))});\n"
             "console.log(JSON.stringify({"
             "fragment: J.codeFrom('#qwert-yup23', ''), query: J.codeFrom('', '?circle=QWERT-YUP23&x=1'),"
             "both: J.codeFrom('#AAAAA-BBBBB', '?circle=CCCCC-DDDDD'), none: J.codeFrom('', ''),"
             "junk: J.codeFrom('#<img src=x>', ''), encoded: J.codeFrom('#QWERT%2DYUP23', ''), bad: J.codeFrom('#%E0%A4%A', ''),"
             "long: J.clean('A'.repeat(99)).length,"
             "app: J.appLink('QWERT-YUP23'), web: J.webLink('QWERT-YUP23'),"
             "iphone: J.isIOS('Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X)', 'iPhone', 5),"
             "ipad: J.isIOS('Mozilla/5.0 (Macintosh)', 'MacIntel', 5), mac: J.isIOS('Mozilla/5.0 (Macintosh)', 'MacIntel', 0),"
             "wechat: J.isWeChat('Mozilla/5.0 (iPhone) MicroMessenger/8.0'), safari: J.isWeChat('Mozilla/5.0 (iPhone) Safari/604.1'),"
             "}));")
    out = json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)
    assert out["fragment"] == out["query"] == out["encoded"] == "QWERT-YUP23"
    assert out["both"] == "AAAAA-BBBBB" and out["none"] == "" and out["long"] == 60
    assert out["junk"] == "IMGSRCX", "whatever comes in, only letters, digits and dashes reach the page and the link"
    assert out["bad"] == "E0A4A"
    assert out["app"] == "watch2whoop://circle?code=QWERT-YUP23" and out["web"] == "./?circle=QWERT-YUP23"
    assert out["iphone"] and out["ipad"] and not out["mac"]
    assert out["wechat"] and not out["safari"]
