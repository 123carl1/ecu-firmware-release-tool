"""严格三段版本值及 Windows 文件版本转换。"""

from __future__ import annotations

from dataclasses import dataclass
import re


_VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


@dataclass(frozen=True, order=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> "SemanticVersion":
        match = _VERSION_RE.fullmatch(text)
        if match is None:
            raise ValueError("版本必须为无前导零的三段数字")
        parts = tuple(int(value) for value in match.groups())
        if any(value > 65535 for value in parts):
            raise ValueError("版本段超出 Windows 文件版本范围")
        return cls(*parts)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def windows_tuple(self) -> tuple[int, int, int, int]:
        return self.major, self.minor, self.patch, 0
