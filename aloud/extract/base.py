"""Shared extraction data types. Stdlib only."""

from dataclasses import dataclass


@dataclass
class Block:
    kind: str  # "heading" or "paragraph"
    text: str


@dataclass
class Document:
    title: str | None
    blocks: list[Block]
    source_path: str

    @property
    def full_text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)
