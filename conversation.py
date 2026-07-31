from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from benchmark import billable_characters

ROLES = ("Agent", "Customer")

# The conversation PII API enforces this per conversation item, not per
# conversation; an async job is rejected whole if any item exceeds it.
MAX_TURN_LENGTH = 1000


@dataclass(frozen=True)
class Turn:
    role: str
    text: str

    def billable_characters(self) -> int:
        return billable_characters(self.text)


@dataclass(frozen=True)
class Conversation:
    turns: tuple[Turn, ...]

    def billable_characters(self) -> int:
        return sum(turn.billable_characters() for turn in self.turns)

    def to_conversation_items(self) -> list[dict[str, str]]:
        return [
            {
                "id": str(index),
                "participantId": turn.role,
                "role": turn.role,
                "text": turn.text,
            }
            for index, turn in enumerate(self.turns, start=1)
        ]

    def as_text(self) -> str:
        return "\n".join(f"{turn.role}: {turn.text}" for turn in self.turns)

    def to_dict(self) -> list[dict[str, str]]:
        return [{"role": turn.role, "text": turn.text} for turn in self.turns]

    @classmethod
    def from_dicts(cls, turns: list[dict[str, Any]]) -> "Conversation":
        return cls(tuple(Turn(role=item["role"], text=item["text"]) for item in turns))
