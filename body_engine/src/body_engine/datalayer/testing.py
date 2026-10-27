"""Contract checks for the framework and the applications using it (issue #116, docs/datalayer-design.md section 2.1).

check_imports: the framework imports only the standard library, itself and a short list of third-party packages; never
the application. Every import statement counts, also the ones inside functions, and so do importlib.import_module /
__import__ / importlib.resources.files calls with a literal module name.
check_toolset: the application's toolset configuration matches its tools and every set sent to a model fits its budget."""
import ast
import sys
from pathlib import Path

ALLOWED_THIRD_PARTY = frozenset({"mcp", "anyio", "starlette", "psycopg"})
_DYNAMIC = {"import_module", "__import__", "files"}   # importlib.import_module, __import__, importlib.resources.files


def _modules(tree: ast.AST, package: str, rel_parts: tuple[str, ...]):
    """(line, absolute module name) of every import in a file of `package` at `rel_parts` (its package path parts)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = (package, *rel_parts)
                if node.level > len(base):
                    yield node.lineno, "." * node.level + (node.module or "")   # escapes the top package
                    continue
                parent = base[:len(base) - node.level + 1]
                yield node.lineno, ".".join((*parent, *([node.module] if node.module else [])))
            else:
                yield node.lineno, node.module or ""
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if name in _DYNAMIC:
                yield node.lineno, node.args[0].value


def check_imports(root: Path, package: str, framework: str,
                  allowed_third_party: frozenset = ALLOWED_THIRD_PARTY) -> list[str]:
    """Violations (file:line module) in the framework directory `root`, whose import name is `framework` (for example
    "body_engine.datalayer") inside the top-level `package` ("body_engine")."""
    fw_parts = tuple(framework.split("."))
    bad = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        pkg_parts = (*fw_parts[1:], *rel.parent.parts)   # the package this file is in, below `package`
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for line, mod in _modules(tree, package, pkg_parts):
            top = mod.split(".")[0]
            ok = (mod == framework or mod.startswith(framework + ".")) if top == package else \
                bool(top) and (top in sys.stdlib_module_names or top in allowed_third_party)
            if not ok:
                bad.append(f"{rel}:{line} {mod}")
    return bad


def check_toolset(cfg, tools, budgets) -> list[str]:
    """An application's one-line contract test for its tools (docs/datalayer-design.md section 3.6): the ToolsetConfig
    is consistent with the tools (toolsets.config_problems) and every set the framework sends fits its Budgets
    (toolsets.budget_problems). Empty = fine; each entry says what and by how much."""
    from .toolsets import budget_problems, config_problems
    tools = list(tools)
    return config_problems(cfg, tools) + budget_problems(cfg, tools, budgets)
