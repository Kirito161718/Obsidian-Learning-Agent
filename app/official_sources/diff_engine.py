from __future__ import annotations

import difflib


class OfficialDiffEngine:
    def compare(self, previous: str | None, current: str) -> dict:
        if previous is None:
            return {"baseline": True, "changed": True, "added_lines": current.splitlines(), "removed_lines": [], "unified_diff": ""}
        if previous == current:
            return {"baseline": False, "changed": False, "added_lines": [], "removed_lines": [], "unified_diff": ""}
        diff = list(difflib.ndiff(previous.splitlines(), current.splitlines()))
        unified = "\n".join(
            difflib.unified_diff(previous.splitlines(), current.splitlines(), fromfile="previous", tofile="current", lineterm="")
        )
        return {
            "baseline": False,
            "changed": True,
            "added_lines": [line[2:] for line in diff if line.startswith("+ ")],
            "removed_lines": [line[2:] for line in diff if line.startswith("- ")],
            "unified_diff": unified,
        }
