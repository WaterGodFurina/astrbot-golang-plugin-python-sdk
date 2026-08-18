from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class CommandTokens:
    tokens: list[str] = None

    @property
    def len(self) -> int:
        return len(self.tokens or [])

    def __getitem__(self, item):
        return self.tokens[item]

    def __iter__(self):
        return iter(self.tokens)

    def get(self, idx: int, default: str | None = None) -> str | None:
        """按下标取 token（对齐原版 command_parser.py 的 get）。

        越界返回 default（默认 None），否则返回去首尾空白的 token。
        """
        tokens = self.tokens or []
        if idx < 0 or idx >= len(tokens):
            return default
        return tokens[idx].strip()


class CommandParserMixin:
    def parse_commands(self, message: str) -> CommandTokens:
        cmd_tokens = CommandTokens()
        cmd_tokens.tokens = re.split(r"\s+", message)
        return cmd_tokens

    def parse_command(self, message: str) -> CommandTokens:
        return self.parse_commands(message)

    def regex_match(self, message: str, command: str) -> bool:
        return re.search(command, message, re.MULTILINE) is not None
