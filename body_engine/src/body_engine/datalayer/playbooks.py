"""Playbooks: step-by-step instructions for one task, for the AI, loaded from Markdown files (moved from the loading part
of ai/playbooks/__init__.py, issue #100 / #116).

Each NAME.md in the application's directory is served two ways with the very same text: as an MCP prompt NAME and by a
get_playbook(name) tool. The first line `# title` is the title, the `> …` line under it the one-line description; HTML
comments are for developers and are stripped. `find_line` (if any) is put under the description of every playbook.
`tool_re` finds the tool names a playbook mentions, so the application can test that every one of them exists.
Which playbooks a restricted caller may run, and what it gets instead, stays with the application."""
import re
from typing import Iterable

from .messages import Messages


class PlaybookSource:
    """files: a directory (importlib.resources Traversable or pathlib.Path) holding NAME.md for every name."""

    def __init__(self, files, names: Iterable[str], *, messages: Messages, find_line: str = "",
                 tool_re: re.Pattern | None = None):
        self.files = files
        self.names = tuple(names)
        self.messages = messages
        self.find_line = find_line
        self.tool_re = tool_re
        self._texts: dict[str, str] = {}

    def check(self, name: str) -> None:
        if name not in self.names:
            m = self.messages
            raise ValueError(m.unknown_playbook.format(name=name, names=m.playbook_sep.join(self.names)))

    def text(self, name: str) -> str:
        if name not in self._texts:
            self.check(name)
            raw = self.files.joinpath(f"{name}.md").read_text(encoding="utf-8")
            body = re.sub(r"<!--.*?-->\s*", "", raw, flags=re.S).strip()
            if self.find_line:
                body = re.sub(r"^(> .+)$", lambda m: f"{m.group(1)}\n\n{self.find_line}", body, count=1, flags=re.M)
            self._texts[name] = body
        return self._texts[name]

    def title(self, name: str) -> str:
        return self.text(name).splitlines()[0].lstrip("# ").strip()

    def description(self, name: str) -> str:
        m = re.search(r"^> (.+)$", self.text(name), flags=re.M)
        return m.group(1).strip() if m else self.title(name)

    def tools_mentioned(self, name: str) -> set[str]:
        return set(self.tool_re.findall(self.text(name))) if self.tool_re else set()
