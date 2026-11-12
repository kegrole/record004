"""MP4 (ISOBMFF) muxer for raw H.264 frames plus demo / fixture helpers.

File layout::

    ftyp | moov(mvhd, trak(tkhd, mdia(mdhd, hdlr, minf(vmhd, dinf, stbl(...))))) | mdat

The media timescale is 30000 and every frame has a 1000-tick duration (30fps).
Only the Python standard library is used.
"""

import argparse
import os
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .nal import add_emulation_prevention, build_avcc
from .sps import BitWriter, build_pps, build_sps


TIMESCALE = 30000
FRAME_DURATION = 1000


def _box(box_type: str, payload: bytes, force_largesize: bool = False) -> bytes:
    if not force_largesize and len(payload) + 8 <= 0xFFFFFFFF:
        return (
            (len(payload) + 8).to_bytes(4, "big")
            + box_type.encode("latin-1")
            + payload
        )
    size = len(payload) + 16
    return (
        (1).to_bytes(4, "big")
        + box_type.encode("latin-1")
        + size.to_bytes(8, "big")
        + payload
    )


def _full_box(box_type: str, version: int, flags: int, payload: bytes) -> bytes:
    return _box(box_type, bytes([version]) + flags.to_bytes(3, "big") + payload)


def _ftyp() -> bytes:
    payload = (
        b"isom"
        + (0x200).to_bytes(4, "big")
        + b"isom"
        + b"iso2"
        + b"avc1"
    )
    return _box("ftyp", payload)


UNITY_MATRIX = bytes(
    [
        0x00, 0x01, 0x00, 0x00, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0x00, 0x01, 0x00, 0x00, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0x40, 0x00, 0x00, 0x00,
    ]
)


def _mvhd(duration: int) -> bytes:
    payload = bytearray()
    payload.extend((0).to_bytes(4, "big"))  # creation_time
    payload.extend((0).to_bytes(4, "big"))  # modification_time
    payload.extend(TIMESCALE.to_bytes(4, "big"))
    payload.extend(duration.to_bytes(4, "big"))
    payload.extend((0x00010000).to_bytes(4, "big"))  # rate 1.0
    payload.extend((0x0100).to_bytes(2, "big"))  # volume
    payload.extend(b"\x00" * 10)  # reserved
    payload.extend(UNITY_MATRIX)
    payload.extend(b"\x00" * 24)  # pre_defined
    payload.extend((2).to_bytes(4, "big"))  # next_track_ID
    return _full_box("mvhd", 0, 0, bytes(payload))


def _tkhd(duration: int, width: int, height: int) -> bytes:
    payload = bytearray()
    payload.extend((0).to_bytes(4, "big"))  # creation_time
    payload.extend((0).to_bytes(4, "big"))  # modification_time
    payload.extend((1).to_bytes(4, "big"))  # track_ID
    payload.extend((0).to_bytes(4, "big"))  # reserved
    payload.extend(duration.to_bytes(4, "big"))
    payload.extend(b"\x00" * 8)  # reserved
    payload.extend((0).to_bytes(2, "big"))  # layer
    payload.extend((0).to_bytes(2, "big"))  # alternate_group
    payload.extend((0).to_bytes(2, "big"))  # volume
    payload.extend(b"\x00" * 2)  # reserved
    payload.extend(UNITY_MATRIX)
    payload.extend((width << 16).to_bytes(4, "big"))
    payload.extend((height << 16).to_bytes(4, "big"))
    return _full_box("tkhd", 0, 0x000007, bytes(payload))


def _mdhd(duration: int) -> bytes:
    payload = bytearray()
    payload.extend((0).to_bytes(4, "big"))  # creation_time
    payload.extend((0).to_bytes(4, "big"))  # modification_time
    payload.extend(TIMESCALE.to_bytes(4, "big"))
    payload.extend(duration.to_bytes(4, "big"))
    payload.extend((0x55C4).to_bytes(2, "big"))  # language 'und'
    payload.extend((0).to_bytes(2, "big"))  # pre_defined
    return _full_box("mdhd", 0, 0, bytes(payload))


def _hdlr() -> bytes:
    payload = (
        (0).to_bytes(4, "big")  # pre_defined
        + b"vide"
        + b"\x00" * 12  # reserved
        + b"VideoHandler\x00"
    )
    return _full_box("hdlr", 0, 0, payload)


def _vmhd() -> bytes:
    return _full_box("vmhd", 0, 1, b"\x00" * 8)


def _dinf() -> bytes:
    dref_entry = _full_box("url ", 0, 1, b"")
    dref = _full_box("dref", 0, 0, (1).to_bytes(4, "big") + dref_entry)
    return _box("dinf", dref)


def _avc1(width: int, height: int, avcc: bytes) -> bytes:
    payload = bytearray()
    payload.extend(b"\x00" * 6)  # reserved
    payload.extend((1).to_bytes(2, "big"))  # data_reference_index
    payload.extend(b"\x00" * 16)  # pre_defined / reserved
    payload.extend(width.to_bytes(2, "big"))
    payload.extend(height.to_bytes(2, "big"))
    payload.extend((0x00480000).to_bytes(4, "big"))  # horizresolution
    payload.extend((0x00480000).to_bytes(4, "big"))  # vertresolution
    payload.extend(b"\x00" * 4)  # reserved
    payload.extend((1).to_bytes(2, "big"))  # frame_count
    payload.extend(b"\x00" * 32)  # compressorname
    payload.extend((0x0018).to_bytes(2, "big"))  # depth
    payload.extend((0xFFFF).to_bytes(2, "big"))  # pre_defined
    payload.extend(_box("avcC", avcc))
    return _box("avc1", bytes(payload))


def _stts(frame_count: int) -> bytes:
    payload = (
        (1).to_bytes(4, "big")
        + frame_count.to_bytes(4, "big")
        + FRAME_DURATION.to_bytes(4, "big")
    )
    return _full_box("stts", 0, 0, payload)


def _stss(keyframe_numbers: Sequence[int]) -> bytes:
    payload = bytearray()
    payload.extend(len(keyframe_numbers).to_bytes(4, "big"))
    for number in keyframe_numbers:
        payload.extend(number.to_bytes(4, "big"))
    return _full_box("stss", 0, 0, bytes(payload))


def _stsc(frame_count: int) -> bytes:
    payload = (
        (1).to_bytes(4, "big")
        + (1).to_bytes(4, "big")  # first_chunk
        + (1).to_bytes(4, "big")  # samples_per_chunk (one chunk per frame)
        + (1).to_bytes(4, "big")  # sample_description_index
    )
    return _full_box("stsc", 0, 0, payload)


def _stsz(sizes: Sequence[int]) -> bytes:
    payload = bytearray()
    payload.extend((0).to_bytes(4, "big"))  # sample_size 0 => variable sizes
    payload.extend(len(sizes).to_bytes(4, "big"))
    for size in sizes:
        payload.extend(size.to_bytes(4, "big"))
    return _full_box("stsz", 0, 0, bytes(payload))


def _stco(offsets: Sequence[int], use_co64: bool = False) -> bytes:
    payload = bytearray()
    payload.extend(len(offsets).to_bytes(4, "big"))
    if use_co64:
        for offset in offsets:
            payload.extend(offset.to_bytes(8, "big"))
        return _full_box("co64", 0, 0, bytes(payload))
    for offset in offsets:
        payload.extend(offset.to_bytes(4, "big"))
    return _full_box("stco", 0, 0, bytes(payload))


def _stbl(width, height, avcc, sizes, offsets, keyframe_numbers, use_co64):
    stsd = _full_box(
        "stsd", 0, 0, (1).to_bytes(4, "big") + _avc1(width, height, avcc)
    )
    children = [
        stsd,
        _stts(len(sizes)),
        _stss(keyframe_numbers),
        _stsc(len(sizes)),
        _stsz(sizes),
        _stco(offsets, use_co64=use_co64),
    ]
    return _box("stbl", b"".join(children))


def _moov(width, height, avcc, sizes, offsets, keyframe_numbers, use_co64):
    duration = len(sizes) * FRAME_DURATION
    minf = _box(
        "minf",
        _vmhd()
        + _dinf()
        + _stbl(width, height, avcc, sizes, offsets, keyframe_numbers, use_co64),
    )
    mdia = _box("mdia", _mdhd(duration) + _hdlr() + minf)
    trak = _box("trak", _tkhd(duration, width, height) + mdia)
    return _box("moov", _mvhd(duration) + trak)


@dataclass
class MuxResult:
    data: bytes
    sample_sizes: List[int]
    mdat_payload_offset: int
    mdat_payload_length: int
    sample_offsets: List[int]


def mux_mp4(
    frames: Sequence[bytes],
    width: int,
    height: int,
    sps: bytes,
    pps: bytes,
    keyframe_numbers: Optional[Sequence[int]] = None,
    use_co64: bool = False,
    force_largesize: bool = False,
) -> MuxResult:
    """Mux AVCC-formatted frame samples into a complete MP4 byte string.

    ``frames`` must already use four-byte AVCC length prefixes. When
    ``force_largesize`` is true the mdat box is written with a 64-bit
    largesize header; ``use_co64`` switches the chunk offset box to co64.
    """
    if not frames:
        raise ValueError("at least one frame is required")
    sizes = [len(frame) for frame in frames]
    if keyframe_numbers is None:
        keyframe_numbers = list(range(1, len(frames) + 1))
    avcc = build_avcc(sps, pps)

    mdat_header_size = 16 if force_largesize else 8
    placeholder_moov = _moov(
        width, height, avcc, sizes, [0] * len(frames), keyframe_numbers, use_co64
    )
    first_sample_offset = len(_ftyp()) + len(placeholder_moov) + mdat_header_size
    offsets: List[int] = []
    cursor = first_sample_offset
    for size in sizes:
        offsets.append(cursor)
        cursor += size

    moov = _moov(width, height, avcc, sizes, offsets, keyframe_numbers, use_co64)
    if len(moov) != len(placeholder_moov):
        raise RuntimeError("internal error: moov size changed while filling offsets")
    mdat_payload = b"".join(frames)
    mdat = _box("mdat", mdat_payload, force_largesize=force_largesize)
    data = _ftyp() + moov + mdat
    return MuxResult(
        data=data,
        sample_sizes=sizes,
        mdat_payload_offset=first_sample_offset,
        mdat_payload_length=len(mdat_payload),
        sample_offsets=offsets,
    )


def build_idr_frame(width: int, height: int, qp: int = 26) -> bytes:
    """Build a decodable I_PCM IDR slice and return an AVCC sample.

    ``mb_type=25`` (I_PCM) carries raw luma/chroma samples and requires no
    entropy-coded residual, which makes a genuinely decodable H.264 frame
    trivial to synthesise with the standard library alone.
    """
    if width % 16 or height % 16:
        raise ValueError("I_PCM demo frames require dimensions divisible by 16")
    mb_width = width // 16
    mb_height = height // 16
    mb_count = mb_width * mb_height

    writer = BitWriter()
    writer.write_ue(0)  # first_mb_in_slice
    writer.write_ue(7)  # slice_type I
    writer.write_ue(0)  # pic_parameter_set_id
    writer.write_ue(0)  # frame_num (log2_max_frame_num_minus4 = 0)
    writer.write_ue(0)  # idr_pic_id
    writer.write_se(0)  # slice_qp_delta => SliceQPY = 26 + delta

    pcm_payload = bytearray()
    for _ in range(mb_count):
        # mb_type I_PCM is codeNum 25: 25 leading zeros + 6-bit suffix.
        writer.write_ue(25)
        while len(writer._bits) % 8:
            writer._bits.append(0)
        # pcm_alignment_zero_bit (the padding above) then raw samples.
        pcm_payload.extend(b"\x80" * 256)  # 16x16 luma
        pcm_payload.extend(b"\x80" * 64)  # 8x8 Cb
        pcm_payload.extend(b"\x80" * 64)  # 8x8 Cr

    writer.rbsp_trailing_bits()
    rbsp = writer.to_bytes() + bytes(pcm_payload)
    nal = bytes([0x65]) + add_emulation_prevention(rbsp)
    return len(nal).to_bytes(4, "big") + nal


def build_demo_frames(frame_count: int, width: int = 160, height: int = 96):
    """Return (sps, pps, [AVCC IDR samples]) for a synthetic playable clip."""
    sps = build_sps(
        profile_idc=100,
        level_idc=30,
        pic_width_in_mbs_minus1=width // 16 - 1,
        pic_height_in_map_units_minus1=height // 16 - 1,
    )
    pps = build_pps()
    frames = [build_idr_frame(width, height) for _ in range(frame_count)]
    return sps, pps, frames


def write_demo(path: str, frame_count: int = 5, width: int = 160, height: int = 96):
    sps, pps, frames = build_demo_frames(frame_count, width, height)
    result = mux_mp4(frames, width, height, sps, pps)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(result.data)
    return result


def _demo_summary(path: str, result: MuxResult, width: int, height: int) -> str:
    from .box import parse_boxes

    frame_count = len(result.sample_sizes)
    seconds = frame_count * FRAME_DURATION / TIMESCALE
    with open(path, "rb") as handle:
        data = handle.read()
    boxes = parse_boxes(data)
    rows = []

    def walk(children):
        for box in children:
            rows.append(box)
            walk(box.children)

    walk(boxes)
    box_text = "; ".join(
        f"{box.type}@{box.payload_offset - box.header_size}" for box in rows
    )
    return (
        f"demo: frames={frame_count} duration={seconds:.3f}s "
        f"width={width} height={height} boxes=[{box_text}]"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pure-stdlib MP4 H.264 muxer")
    parser.add_argument("--demo", action="store_true", help="write a synthetic clip")
    parser.add_argument("--out", required=True, help="output .mp4 path")
    parser.add_argument("--frames", type=int, default=5, help="demo frame count")
    args = parser.parse_args(argv)
    if not args.demo:
        parser.error("only --demo mode is supported")
    width, height = 160, 96
    result = write_demo(args.out, frame_count=args.frames, width=width, height=height)
    print(_demo_summary(args.out, result, width, height))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
