"""Opt-in compatibility cleanup for chat replies from affected models.

Inspired by #144's report of community models emitting opening dividers.
This is deliberately outside the provider layer: structured model calls must
retain their content, and visible deltas must agree with the final reply.
"""

from __future__ import annotations

import re
from typing import Any

_RULE = re.compile(r"[ ]{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*")
# Be conservative around YAML/front matter and indented code. An ambiguous
# prefix is more useful intact than incorrectly 'repaired'.
_STRUCTURED = re.compile(r"(?:[ \t]+|[#%{\[\"']|[-?] |[^\s:]+:[ \t]|[^\s:]+:$)")


def divider_cleanup_enabled(config: dict[str, Any]) -> bool:
    """Only exact model IDs in an explicit list enable the workaround."""
    models = config.get("strip_leading_divider_models")
    return isinstance(models, list) and config.get("model") in models


class LeadingDividerFilter:
    """Buffer only an opening prefix; after deciding, stream unchanged.

    The first body line is held until newline/EOF to protect YAML headers.
    If that decision takes over 4096 characters, pass everything through.
    The bound is independent of transport chunk sizes.
    """

    MAX_PREFIX_CHARS = 4096

    def __init__(self) -> None:
        self._pending = ""
        self._done = False
        self.removed = False

    def feed(self, text: str) -> str:
        if self._done:
            return text
        output = []
        # Resolve at line boundaries so the result cannot depend on where a
        # provider splits its deltas (including a very large first delta).
        for part in text.splitlines(keepends=True):
            if self._done:
                output.append(part)
                continue
            self._pending += part
            output.append(self._drain(final=False))
        return "".join(output)

    def finish(self) -> str:
        return "" if self._done else self._drain(final=True)

    def _release(self, text: str) -> str:
        self._pending = ""
        self._done = True
        return text

    def _drain(self, *, final: bool) -> str:
        if len(self._pending) > self.MAX_PREFIX_CHARS:
            return self._release(self._pending)
        offset = 0
        rules = False
        for line in self._pending.splitlines(keepends=True):
            complete = line.endswith("\n") or final
            body = line.rstrip("\r\n")
            if not body.strip(" \t"):
                offset += len(line)
                continue
            if _RULE.fullmatch(body) and complete:
                rules = True
                offset += len(line)
                continue
            if not rules:
                # A partial rule can still become a rule in the next delta.
                if not complete and re.fullmatch(r"[ \t]*[-*_ \t]*\r?", body):
                    break
                return self._release(self._pending)
            if not complete:
                break
            if _STRUCTURED.match(body):
                return self._release(self._pending)
            self.removed = True
            return self._release(self._pending[offset:])
        if final:
            self.removed = rules
            return self._release("" if rules else self._pending)
        return ""


def strip_leading_divider(content: str) -> str:
    """Strip opening rule lines when explicitly requested for an affected model.

    No unconditional whitespace trimming. Internal rules, indentation and
    recognizable document headers are preserved. Literal opening rules remain
    ambiguous, so this must never be enabled globally.
    """
    cleaner = LeadingDividerFilter()
    return cleaner.feed(content) + cleaner.finish()
