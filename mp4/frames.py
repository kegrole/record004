"""Reconstruct per-frame byte offsets and durations from an stbl box."""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .box import Box, find_box, find_boxes, parse_boxes
from .nal import parse_avcc
from .sps import parse_sps


@dataclass
class Frame:
    """One sample (video frame) located inside the mdat payload."""

    index: int
    offset: int
    size: int
    duration: int
    is_keyframe: bool


@dataclass
class FrameInfo:
    """Everything needed to read the encoded frames back out of the file."""

    frames: List[Frame]
    width: int
    height: int
    timescale: int
    mvhd_timescale: int
    duration: int
    mvhd_duration: int
    sample_count: int
    sample_sizes: List[int]
    keyframe_numbers: List[int]
    chunk_offsets: List[int]
    offset_box_type: str


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "big")


def _u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 8], "big")


def parse_stsz(data: bytes, stsz: Box) -> Tuple[int, List[int]]:
    base = stsz.payload_offset
    uniform_size = _u32(data, base + 4)
    count = _u32(data, base + 8)
    if uniform_size:
        return count, [uniform_size] * count
    sizes = [_u32(data, base + 12 + index * 4) for index in range(count)]
    return count, sizes


def parse_stts(data: bytes, stts: Box, sample_count: int) -> List[int]:
    """Expand the time-to-sample table into one duration per sample."""
    base = stts.payload_offset
    entry_count = _u32(data, base + 4)
    durations: List[int] = []
    cursor = base + 8
    for _ in range(entry_count):
        run_count = _u32(data, cursor)
        delta = _u32(data, cursor + 4)
        durations.extend([delta] * run_count)
        cursor += 8
    if len(durations) != sample_count:
        raise ValueError(
            f"stts covers {len(durations)} samples but stsz declares {sample_count}"
        )
    return durations


def parse_stss(data: bytes, stss: Optional[Box], sample_count: int) -> List[int]:
    """Return 1-based keyframe sample numbers; absent stss means all key."""
    if stss is None:
        return list(range(1, sample_count + 1))
    base = stss.payload_offset
    entry_count = _u32(data, base + 4)
    return [_u32(data, base + 8 + index * 4) for index in range(entry_count)]


def parse_stsc(data: bytes, stsc: Box) -> List[Tuple[int, int, int]]:
    base = stsc.payload_offset
    entry_count = _u32(data, base + 4)
    entries = []
    cursor = base + 8
    for _ in range(entry_count):
        first_chunk = _u32(data, cursor)
        samples_per_chunk = _u32(data, cursor + 4)
        desc_index = _u32(data, cursor + 8)
        entries.append((first_chunk, samples_per_chunk, desc_index))
        cursor += 12
    return entries


def parse_chunk_offsets(data: bytes, stbl: Box) -> Tuple[List[int], str]:
    co64 = find_box(stbl.children, "co64")
    if co64 is not None:
        base = co64.payload_offset
        count = _u32(data, base + 4)
        return [_u64(data, base + 8 + index * 8) for index in range(count)], "co64"
    stco = find_box(stbl.children, "stco")
    if stco is None:
        raise ValueError("stbl contains neither stco nor co64")
    base = stco.payload_offset
    count = _u32(data, base + 4)
    return [_u32(data, base + 8 + index * 4) for index in range(count)], "stco"


def _samples_per_chunk_map(
    stsc_entries: List[Tuple[int, int, int]], chunk_count: int
) -> List[int]:
    """Expand sample-to-chunk entries to one samples-per-chunk value."""
    result = [0] * chunk_count
    entry_index = 0
    for chunk in range(1, chunk_count + 1):
        while (
            entry_index + 1 < len(stsc_entries)
            and stsc_entries[entry_index + 1][0] <= chunk
        ):
            entry_index += 1
        first_chunk, samples_per_chunk, _ = stsc_entries[entry_index]
        if first_chunk > chunk:
            raise ValueError(f"no stsc entry applies to chunk {chunk}")
        result[chunk - 1] = samples_per_chunk
    return result


def parse_tkhd_dimensions(data: bytes, tkhd: Box) -> Tuple[int, int]:
    base = tkhd.payload_offset
    version = data[base]
    if version == 1:
        dims_offset = base + 84
    else:
        dims_offset = base + 76
    width = _u32(data, dims_offset) >> 16
    height = _u32(data, dims_offset + 4) >> 16
    return width, height


def _parse_mdhd(data: bytes, mdhd: Box) -> Tuple[int, int]:
    base = mdhd.payload_offset
    version = data[base]
    if version == 1:
        timescale = _u32(data, base + 20)
        duration = _u64(data, base + 24)
    else:
        timescale = _u32(data, base + 12)
        duration = _u32(data, base + 16)
    return timescale, duration


def _parse_mvhd(data: bytes, mvhd: Box) -> Tuple[int, int]:
    base = mvhd.payload_offset
    version = data[base]
    if version == 1:
        timescale = _u32(data, base + 20)
        duration = _u64(data, base + 24)
    else:
        timescale = _u32(data, base + 12)
        duration = _u32(data, base + 16)
    return timescale, duration


def _dimensions_from_avcc(data: bytes, stbl: Box) -> Optional[Tuple[int, int]]:
    avcc_box = find_box(stbl.children, "avcC")
    if avcc_box is None:
        return None
    avcc = data[avcc_box.payload_offset:avcc_box.end]
    sps_list, _ = parse_avcc(avcc)
    if not sps_list:
        return None
    info = parse_sps(sps_list[0])
    return info["width"], info["height"]


def read_mp4(data: bytes) -> FrameInfo:
    """Parse an MP4 byte string and reconstruct every frame's location."""
    root = parse_boxes(data)
    moov = find_box(root, "moov")
    if moov is None:
        raise ValueError("file has no moov box")
    stbl = find_box(moov.children, "stbl")
    if stbl is None:
        raise ValueError("moov has no stbl box")

    stsz = find_box(stbl.children, "stsz")
    stts = find_box(stbl.children, "stts")
    stsc = find_box(stbl.children, "stsc")
    if stsz is None or stts is None or stsc is None:
        raise ValueError("stbl is missing stsz/stts/stsc")

    sample_count, sizes = parse_stsz(data, stsz)
    durations = parse_stts(data, stts, sample_count)
    keyframe_numbers = parse_stss(data, find_box(stbl.children, "stss"), sample_count)
    stsc_entries = parse_stsc(data, stsc)
    chunk_offsets, offset_type = parse_chunk_offsets(data, stbl)
    samples_per_chunk = _samples_per_chunk_map(stsc_entries, len(chunk_offsets))

    if sum(samples_per_chunk) != sample_count:
        raise ValueError(
            f"stsc maps {sum(samples_per_chunk)} samples but stsz declares {sample_count}"
        )
    if sum(samples_per_chunk) != len(sizes):
        raise ValueError("stsc sample count does not match stsz entry count")

    keyframe_set = set(keyframe_numbers)
    frames: List[Frame] = []
    sample_index = 0
    for chunk_index, chunk_offset in enumerate(chunk_offsets):
        cursor = chunk_offset
        for _ in range(samples_per_chunk[chunk_index]):
            size = sizes[sample_index]
            frames.append(
                Frame(
                    index=sample_index,
                    offset=cursor,
                    size=size,
                    duration=durations[sample_index],
                    is_keyframe=(sample_index + 1) in keyframe_set,
                )
            )
            cursor += size
            sample_index += 1

    mdhd = find_box(moov.children, "mdhd")
    mvhd = find_box(moov.children, "mvhd")
    timescale, mdhd_duration = _parse_mdhd(data, mdhd)
    mvhd_timescale, mvhd_duration = _parse_mvhd(data, mvhd)

    tkhd = find_box(moov.children, "tkhd")
    width, height = parse_tkhd_dimensions(data, tkhd)
    if width == 0 or height == 0:
        fallback = _dimensions_from_avcc(data, stbl)
        if fallback is not None:
            width, height = fallback

    return FrameInfo(
        frames=frames,
        width=width,
        height=height,
        timescale=timescale,
        mvhd_timescale=mvhd_timescale,
        duration=mdhd_duration,
        mvhd_duration=mvhd_duration,
        sample_count=sample_count,
        sample_sizes=sizes,
        keyframe_numbers=keyframe_numbers,
        chunk_offsets=chunk_offsets,
        offset_box_type=offset_type,
    )


def read_mp4_file(path: str) -> FrameInfo:
    with open(path, "rb") as handle:
        return read_mp4(handle.read())
