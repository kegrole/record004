"""Pure-stdlib MP4 (ISOBMFF) parser and H.264 muxer.

Modules:
    box:    recursive ISOBMFF box parser
    frames: sample/frame reconstruction from stbl
    nal:    Annex-B splitting and AVCC / avcC conversion
    sps:    H.264 SPS construction and parsing
    muxer:  MP4 encapsulation, fixtures and command line entry point
"""

from .box import Box, parse_boxes, find_boxes, find_box
from .frames import Frame, FrameInfo, read_mp4
from .nal import NAL, split_annexb, annexb_to_avcc, build_avcc, parse_avcc
from .sps import build_sps, parse_sps, remove_emulation_prevention

__all__ = [
    "Box",
    "parse_boxes",
    "find_boxes",
    "find_box",
    "Frame",
    "FrameInfo",
    "read_mp4",
    "NAL",
    "split_annexb",
    "annexb_to_avcc",
    "build_avcc",
    "parse_avcc",
    "build_sps",
    "parse_sps",
    "remove_emulation_prevention",
]
