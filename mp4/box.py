"""Recursive parser for ISOBMFF (MP4) boxes.

A box on disk is laid out as::

    size (4 bytes, big endian)  + type (4 ASCII bytes) [+ largesize (8)]

followed by the box payload. ``Box.payload_offset`` is the absolute byte
offset inside the file where that payload starts (i.e. immediately after the
full box header, 8 bytes for a normal box and 16 bytes for a ``largesize``
box).
"""

from dataclasses import dataclass
from typing import List, Optional


# Boxes whose payload consists entirely of child boxes. ``meta`` is a full
# box (version/flags prefix) but still contains child boxes afterwards.
CONTAINER_TYPES = frozenset(
    {
        "moov",
        "trak",
        "mdia",
        "minf",
        "stbl",
        "dinf",
        "udta",
        "edts",
        "mvex",
        "moof",
        "traf",
        "mfra",
        "skip",
        "meco",
        "mere",
    }
)


@dataclass
class Box:
    """A single parsed ISOBMFF box."""

    type: str
    size: int
    payload_offset: int
    header_size: int
    children: List["Box"]

    @property
    def end(self) -> int:
        """Absolute offset of the first byte after this box."""
        return self.payload_offset + (self.size - self.header_size)

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return (
            f"Box(type={self.type!r}, size={self.size}, "
            f"payload_offset={self.payload_offset})"
        )


def _parse_region(data: bytes, start: int, end: int) -> List[Box]:
    boxes: List[Box] = []
    offset = start
    while offset < end:
        if end - offset < 8:
            raise ValueError(
                f"truncated box header at offset {offset} ({end - offset} bytes left)"
            )
        size = int.from_bytes(data[offset:offset + 4], "big")
        box_type = data[offset + 4:offset + 8].decode("latin-1")
        header_size = 8
        payload_offset = offset + 8
        if size == 1:
            if end - payload_offset < 8:
                raise ValueError(f"truncated largesize box at offset {offset}")
            size = int.from_bytes(data[offset + 8:offset + 16], "big")
            header_size = 16
            payload_offset = offset + 16
        elif size == 0:
            # Box extends to the end of the enclosing region / file.
            size = end - offset
        if size < header_size:
            raise ValueError(
                f"invalid size {size} for box {box_type!r} at offset {offset}"
            )
        box_end = offset + size
        if box_end > end:
            raise ValueError(
                f"box {box_type!r} at offset {offset} overruns region: "
                f"end {box_end} > {end}"
            )

        children: List[Box] = []
        child_start = payload_offset
        if box_type == "meta":
            # Full box: skip version + flags before parsing children.
            child_start = payload_offset + 4
        if box_type in CONTAINER_TYPES or box_type == "meta":
            children = _parse_region(data, child_start, box_end)

        boxes.append(Box(box_type, size, payload_offset, header_size, children))
        offset = box_end
    return boxes


def parse_boxes(data: bytes, offset: int = 0, length: Optional[int] = None) -> List[Box]:
    """Parse every box found in ``data[offset:offset + length]``.

    Container boxes (including ``stbl``, ``mdia`` and ``minf``) are expanded
    recursively into :attr:`Box.children`.
    """
    end = len(data) if length is None else offset + length
    return _parse_region(data, offset, end)


def find_boxes(boxes: List[Box], box_type: str) -> List[Box]:
    """Depth-first search returning every box of the given type."""
    found: List[Box] = []
    for box in boxes:
        if box.type == box_type:
            found.append(box)
        found.extend(find_boxes(box.children, box_type))
    return found


def find_box(boxes: List[Box], box_type: str) -> Optional[Box]:
    """Return the first box of the given type found via depth-first search."""
    matches = find_boxes(boxes, box_type)
    return matches[0] if matches else None
