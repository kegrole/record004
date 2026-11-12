"""H.264 NAL unit handling: Annex-B splitting, AVCC and avcC conversion."""

from dataclasses import dataclass
from typing import List, Sequence, Tuple


@dataclass
class NAL:
    """One NAL unit (without any start code).

    ``payload`` is the complete NAL unit byte string including its single
    header byte; the NAL unit type is the low five bits of that header.
    """

    type: int
    payload: bytes

    @property
    def header(self) -> int:
        return self.payload[0] if self.payload else 0


def nal_type(nal_unit: bytes) -> int:
    if not nal_unit:
        raise ValueError("empty NAL unit")
    return nal_unit[0] & 0x1F


def split_annexb(data: bytes) -> List[NAL]:
    """Split an Annex-B byte stream into NAL units.

    Both three-byte (``00 00 01``) and four-byte (``00 00 00 01``) start
    codes are recognised.  Leading zero bytes and any zero padding between
    NAL units are consumed as part of the start codes and never appear in the
    returned payloads.
    """
    starts: List[Tuple[int, int]] = []
    n = len(data)
    i = 0
    while i + 2 < n:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                starts.append((i, i + 3))
                i += 3
                continue
            if i + 3 < n and data[i + 2] == 0 and data[i + 3] == 1:
                starts.append((i, i + 4))
                i += 4
                continue
        i += 1

    nals: List[NAL] = []
    for index, (_, payload_start) in enumerate(starts):
        next_start = starts[index + 1][0] if index + 1 < len(starts) else n
        payload = data[payload_start:next_start]
        # Strip trailing zero padding (zero byte cannot end a real RBSP).
        payload = payload.rstrip(b"\x00")
        if payload:
            nals.append(NAL(nal_type(payload), payload))
    return nals


def annexb_to_avcc(data: bytes) -> Tuple[bytes, List[NAL]]:
    """Convert an Annex-B stream to AVCC format.

    Every start code is replaced by a four-byte big-endian length prefix.
    The resulting byte string and the parsed NAL units are both returned.
    """
    nals = split_annexb(data)
    out = bytearray()
    for nal in nals:
        out.extend(len(nal.payload).to_bytes(4, "big"))
        out.extend(nal.payload)
    return bytes(out), nals


def remove_emulation_prevention(ebsp: bytes) -> bytes:
    """Strip emulation prevention bytes (the ``03`` in ``00 00 03``).

    An emulation prevention byte is removed only when it is actually preceded
    by two zero bytes; a genuine ``0x03`` data byte is left untouched.
    """
    out = bytearray()
    zeros = 0
    i = 0
    n = len(ebsp)
    while i < n:
        byte = ebsp[i]
        if byte == 0x03 and zeros >= 2 and i + 1 < n and ebsp[i + 1] <= 0x03:
            # Drop the emulation prevention byte; zeros stay at zero because
            # the next byte (00/01/02/03) breaks the 00 00 run anyway.
            i += 1
            zeros = 0
            continue
        out.append(byte)
        if byte == 0:
            zeros += 1
        else:
            zeros = 0
        i += 1
    return bytes(out)


def add_emulation_prevention(rbsp: bytes) -> bytes:
    """Insert emulation prevention bytes per ITU-T H.264 7.3.1."""
    out = bytearray()
    zeros = 0
    for byte in rbsp:
        if zeros >= 2 and byte <= 0x03:
            out.append(0x03)
            zeros = 0
        out.append(byte)
        if byte == 0:
            zeros += 1
        else:
            zeros = 0
    return bytes(out)


def build_avcc(sps: bytes, pps: bytes) -> bytes:
    """Build an ``avcC`` decoder configuration record from SPS and PPS.

    Profile/compatibility/level bytes are taken from the SPS NAL unit
    (header + RBSP).  Length-size-minus-one is fixed at 3 (4-byte prefixes)
    and one SPS / one PPS are embedded.
    """
    if nal_type(sps) != 7:
        raise ValueError("first argument must be an SPS NAL unit (type 7)")
    if nal_type(pps) != 8:
        raise ValueError("second argument must be a PPS NAL unit (type 8)")
    out = bytearray()
    out.append(1)  # configurationVersion
    out.append(sps[1])  # AVCProfileIndication
    out.append(sps[2])  # profile_compatibility
    out.append(sps[3])  # AVCLevelIndication
    out.append(0xFF)  # 6 reserved bits + lengthSizeMinusOne = 3
    out.append(0xE1)  # 3 reserved bits + numOfSequenceParameterSets = 1
    out.extend(len(sps).to_bytes(2, "big"))
    out.extend(sps)
    out.append(1)  # numOfPictureParameterSets
    out.extend(len(pps).to_bytes(2, "big"))
    out.extend(pps)
    return bytes(out)


def parse_avcc(avcc: bytes) -> Tuple[List[bytes], List[bytes]]:
    """Parse an ``avcC`` record and return ``(sps_list, pps_list)``."""
    if len(avcc) < 7 or avcc[0] != 1:
        raise ValueError("unsupported avcC record")
    offset = 5
    sps_count = avcc[offset] & 0x1F
    offset += 1
    sps_list: List[bytes] = []
    for _ in range(sps_count):
        length = int.from_bytes(avcc[offset:offset + 2], "big")
        offset += 2
        sps_list.append(bytes(avcc[offset:offset + length]))
        offset += length
    pps_count = avcc[offset]
    offset += 1
    pps_list: List[bytes] = []
    for _ in range(pps_count):
        length = int.from_bytes(avcc[offset:offset + 2], "big")
        offset += 2
        pps_list.append(bytes(avcc[offset:offset + length]))
        offset += length
    return sps_list, pps_list


def iter_avcc_samples(data: bytes) -> List[bytes]:
    """Split a length-prefixed (AVCC) sample into its individual NAL units."""
    nals: List[bytes] = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 4:
            raise ValueError("truncated AVCC length prefix")
        length = int.from_bytes(data[offset:offset + 4], "big")
        offset += 4
        if length == 0 or offset + length > len(data):
            raise ValueError("invalid AVCC NAL length")
        nals.append(bytes(data[offset:offset + length]))
        offset += length
    return nals


def nals_to_annexb(nals: Sequence[bytes]) -> bytes:
    """Join NAL units with four-byte Annex-B start codes (test helper)."""
    return b"".join(b"\x00\x00\x00\x01" + nal for nal in nals)
