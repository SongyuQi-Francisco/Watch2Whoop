"""robots.txt per RFC 9309: groups by user-agent, `*` and `$` in paths, the longest matching rule wins, Allow wins a tie.
(urllib.robotparser does neither wildcards nor longest-match, and Coles' file relies on both: `Allow: /on-special?filter_Special=halfprice`
next to `Disallow: *?filter_Special=*`.)"""
import re
import urllib.parse


class Robots:
    def __init__(self, text: str, agent: str):
        self.rules = self._rules(text or "", agent.lower())

    @staticmethod
    def _groups(text: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
        groups, agents, rules, in_rules = [], [], [], False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, val = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if in_rules:   # a new group starts
                    groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(val.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if agents:
                    rules.append((key, val))
        if agents:
            groups.append((agents, rules))
        return groups

    def _rules(self, text: str, agent: str) -> list[tuple[str, str]]:
        groups = self._groups(text)
        token = agent.split("/")[0]
        mine = [r for a, rs in groups for r in rs if any(x != "*" and x in token for x in a)]
        if mine:
            return mine
        return [r for a, rs in groups for r in rs if "*" in a]

    @staticmethod
    def _match(pattern: str, path: str) -> bool:
        if not pattern:
            return False
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        rx = "".join(".*" if c == "*" else re.escape(c) for c in body)
        return re.match(rx + ("$" if anchored else ""), path) is not None

    def allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for kind, pattern in self.rules:
            if kind == "disallow" and pattern == "":
                continue   # "Disallow:" with nothing = allow everything
            if self._match(pattern, path):
                cand = (len(pattern), kind == "allow")
                if best is None or cand > best:   # longer wins; on a tie (same length) allow (True) > disallow
                    best = cand
        return True if best is None else best[1]
