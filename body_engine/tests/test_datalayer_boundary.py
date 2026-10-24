"""The datalayer framework never imports Watch2Whoop's business modules (issue #116, docs/datalayer-design.md section
2.1): only the standard library, itself and datalayer.testing.ALLOWED_THIRD_PARTY. Imports inside functions and dynamic
imports with a literal name count too. The old import paths stay usable and are the very same objects."""
import textwrap
from pathlib import Path

import body_engine
from body_engine.datalayer import testing

ROOT = Path(body_engine.__file__).parent / "datalayer"


def test_datalayer_imports_no_business_module():
    assert testing.check_imports(ROOT, "body_engine", "body_engine.datalayer") == []


def test_the_check_catches_every_kind_of_import(tmp_path):
    fw = tmp_path / "datalayer"
    (fw / "sub").mkdir(parents=True)
    (fw / "ok.py").write_text(textwrap.dedent("""
        import json, os.path
        from typing import Any
        from . import spec
        from .sub import x
        from body_engine.datalayer import compact
        import anyio
        from mcp.types import Tool
    """))
    (fw / "sub" / "bad.py").write_text(textwrap.dedent("""
        from .. import spec
        from ... import auth
        from ...ai import params
        import body_engine.api
        import requests
        def lazy():
            from body_engine import db
            import importlib
            importlib.import_module("body_engine.queries")
            __import__("fastapi")
            from importlib import resources
            resources.files("body_engine.ai.playbooks")
    """))
    bad = testing.check_imports(fw, "body_engine", "body_engine.datalayer")
    assert [b.split(" ", 1)[1] for b in bad] == [
        "body_engine", "body_engine.ai", "body_engine.api", "requests", "body_engine",
        "body_engine.queries", "fastapi", "body_engine.ai.playbooks"]
    assert all(b.startswith("sub/bad.py:") for b in bad)


def test_old_import_paths_are_the_moved_objects():
    from body_engine import datalayer, toolspec
    from body_engine.ai import playbooks
    from body_engine.assistant import compact, toolsets, tools
    assert compact is datalayer.compact
    assert tools.Tool is datalayer.Tool and toolsets.Access is datalayer.Access
    assert toolspec.schema is datalayer.schema and toolspec.nullable is datalayer.nullable
    assert (toolsets.FIND, toolsets.RUN_READ, toolsets.RUN_WRITE) == ("find_tools", "run_read_tool", "run_write_tool")
    assert isinstance(playbooks._SOURCE, datalayer.PlaybookSource)


def test_phase_2b_old_paths_run_through_the_framework():
    from body_engine import datalayer, mcp_server
    from body_engine.ai import mcp_http
    from body_engine.assistant import tools, writelog
    from body_engine.datalayer.mcp.http import McpHttpApp
    assert mcp_server.Identity is datalayer.Caller
    assert isinstance(tools.EXECUTOR, datalayer.Executor) and tools.EXECUTOR.tools is tools.TOOLS_BY_NAME
    assert isinstance(writelog.LOG, datalayer.WriteLog) and tools.EXECUTOR.writelog is writelog.LOG
    assert isinstance(mcp_http.mcp_app, McpHttpApp) and mcp_http.mcp_app.identity_key == mcp_http.IDENTITY_KEY


def test_rollback_kinds_are_the_ones_before_the_move():
    """The rollback dispatch moved from one if / elif chain to handlers registered by kind (datalayer/writelog.py)."""
    from body_engine.assistant import writelog
    assert writelog.ROLLBACK_KINDS == {"food", "session", "sets", "weight", "workout", "lab", "merge", "template", "water",
                                       "pantry", "profile", "observation"}
    only = {"merge": "merge", "template": "create", "observation": "update"}
    for kind in writelog.ROLLBACK_KINDS:
        for action in ("create", "update", "delete", "merge", "rollback"):
            assert writelog._can_roll(kind, action) == (action != "rollback" and only.get(kind, action) == action), (kind, action)
    assert not writelog._can_roll("other", "other")
