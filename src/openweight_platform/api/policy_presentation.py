"""Employee answer presentation; original model text stays with RAG validation."""

import re


class PolicyAnswerPresentation:
    """Remove SOURCE wrappers even when they arrive across stream boundaries.

    Ordinary inline citations remain text. Only structured, validated citation
    IDs can become citation controls in the browser.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._source_depth = 0

    def feed(self, text: str) -> str:
        output: list[str] = []
        for char in text:
            if self._source_depth:
                if char == "[":
                    self._source_depth += 1
                elif char == "]":
                    self._source_depth -= 1
                continue
            if not self._pending:
                if char == "[":
                    self._pending = char
                else:
                    output.append(char)
                continue
            self._pending += char
            candidate = self._pending.upper()
            if "[SOURCE".startswith(candidate):
                continue
            if candidate.startswith("[SOURCE") and (char.isspace() or char == "["):
                self._source_depth = 2 if char == "[" else 1
                self._pending = ""
                continue
            # This is ordinary answer text, including a regular citation ID.
            output.append(self._pending)
            self._pending = ""
        return "".join(output)

    def finish(self) -> str:
        pending = self._pending
        self._pending = ""
        if self._source_depth or pending.upper().startswith("[SOURCE"):
            return ""
        return pending


def present_policy_answer(answer: str) -> str:
    presentation = PolicyAnswerPresentation()
    text = presentation.feed(answer) + presentation.finish()
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
