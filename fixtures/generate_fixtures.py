"""Generate the deterministic test inputs used by tests/test_mp4.py.

Run from the repository root: ``python3 fixtures/generate_fixtures.py``.
Everything is regenerated from the H.264/ISOBMFF primitives in the mp4
package, so no fixture is a hand-edited binary or tied to a specific parser.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mp4.muxer import build_demo_frames, mux_mp4
from mp4.nal import add_emulation_prevention, nals_to_annexb
from mp4.sps import build_pps, build_sps


HERE = os.path.dirname(os.path.abspath(__file__))
WIDTH = 1280
HEIGHT = 720


def write(path, data):
    with open(path, "wb") as handle:
        handle.write(data)
    print(f"wrote {os.path.relpath(path)} ({len(data)} bytes)")


def main():
    os.makedirs(HERE, exist_ok=True)

    # 1. Canonical SPS built exactly per the acceptance construction rules.
    sps = build_sps()
    pps = build_pps()
    write(os.path.join(HERE, "sps.h264"), sps)
    write(os.path.join(HERE, "pps.h264"), pps)

    # 2. SPS with emulation prevention bytes inserted by the H.264 rule.
    escaped = bytes([sps[0]]) + add_emulation_prevention(sps[1:])
    write(os.path.join(HERE, "sps_escaped.h264"), escaped)

    # 2b. SPS that is guaranteed to contain at least two 00 00 03 escape
    # sequences. The extra bytes sit after the RBSP stop bit, so the parsed
    # dimensions must remain exactly 1280x720.
    forced = sps + b"\x00\x00\x03\x01" + b"\x00\x00\x03\x02"
    write(os.path.join(HERE, "sps_forced_escapes.h264"), forced)

    # 3. Annex-B stream with mixed 3- and 4-byte start codes: SPS, PPS, IDR, P.
    idr = bytes([0x65, 0x88, 0x80, 0x40])  # type 5, payload is arbitrary here
    non_idr = bytes([0x41, 0x9A, 0x01, 0x7F])  # type 1
    annexb = (
        b"\x00\x00\x00\x01" + sps
        + b"\x00\x00\x01" + pps
        + b"\x00\x00\x00\x01" + idr
        + b"\x00\x00\x01" + non_idr
    )
    write(os.path.join(HERE, "annexb_mixed.h264"), annexb)

    # 4. Three MP4 box shapes: plain 32-bit, largesize mdat, and co64 offsets.
    demo_sps, demo_pps, frames = build_demo_frames(4, width=160, height=96)
    plain = mux_mp4(frames, 160, 96, demo_sps, demo_pps)
    write(os.path.join(HERE, "plain.mp4"), plain.data)

    large = mux_mp4(
        frames, 160, 96, demo_sps, demo_pps, force_largesize=True
    )
    write(os.path.join(HERE, "largesize.mp4"), large.data)

    co64 = mux_mp4(
        frames, 160, 96, demo_sps, demo_pps, use_co64=True
    )
    write(os.path.join(HERE, "co64.mp4"), co64.data)


if __name__ == "__main__":
    main()
