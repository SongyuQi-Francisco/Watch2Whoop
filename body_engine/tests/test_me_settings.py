"""「我」 page v2 + 「设置」 sub-page behind the me_settings flag (issue #140): static checks of the page wiring.

The page logic is plain JS without a test runner here; this pins what must not regress: the flag is declared, known
plugin rows are routed, a plugin row nobody routed (a server's private admin plugin) still lands on the main page, and
the account-level rows sit in the settings sub-page.
"""
import re
from pathlib import Path

from body_engine.rollout import registry

APP = (Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static" / "app.js").read_text()


def test_flag_declared_and_checked():
    assert "me_settings" in registry.FLAGS
    assert '(S.flags || []).includes("me_settings")' in APP


def test_plugin_rows_routed_and_unknown_kept_on_main():
    route = re.search(r"const ME_ROUTE = \{(.*?)\};", APP, re.S).group(1)
    for row in ("me-profile", "me-circles", "me-feedback", "me-signin", "me-invite", "me-delete-account"):
        assert f'"{row}"' in route, row
    assert '|| "other"' in APP
    start = APP.index("if (meSettingsOn()) {\n      const chev")
    main = APP[start:APP.index("</div></div>`;", start)]   # the v2 main page template
    assert "rows.other" in main and 'id="me-settings"' in main and 'id="me-archive"' in main


def test_settings_page_has_account_rows():
    s = APP[APP.index("function meSettingsHtml"):APP.index("const RENDER_me")]
    for x in ('id="me-back"', "langRow()", 'id="me-intro"', 'id="me-logout"', 'rows["settings-danger"]', 'rows["settings-account"]',
              'rows["settings-ai"]', "S.version"):
        assert x in s, x
