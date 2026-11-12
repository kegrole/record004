"""H.264 sequence parameter set construction and parsing.

Only the fields needed to recover frame dimensions (and the fixed High
profile test SPS) are modelled, but every syntax element that precedes them
in ITU-T H.264 7.3.2.1 is consumed so arbitrary conforming Baseline/Main/High
profile SPS streams can be parsed.
"""

from typing import Dict

from .nal import add_emulation_prevention, nal_type, remove_emulation_prevention


class BitReader:
    """Big-endian bit reader with unsigned / signed Exp-Golomb support."""

    def __init__(self, data: bytes):
        self.data = data
        self.bit_pos = 0

    def read_bits(self, count: int) -> int:
        if count == 0:
            return 0
        end = self.bit_pos + count
        if end > len(self.data) * 8:
            raise ValueError("unexpected end of RBSP")
        value = 0
        for index in range(count):
            byte = self.data[(self.bit_pos + index) // 8]
            bit = (byte >> (7 - ((self.bit_pos + index) % 8))) & 1
            value = (value << 1) | bit
        self.bit_pos = end
        return value

    def read_ue(self) -> int:
        leading_zeros = 0
        while self.read_bits(1) == 0:
            leading_zeros += 1
            if leading_zeros > 32:
                raise ValueError("malformed Exp-Golomb value")
        suffix = self.read_bits(leading_zeros) if leading_zeros else 0
        return (1 << leading_zeros) - 1 + suffix

    def read_se(self) -> int:
        code_num = self.read_ue()
        value = (code_num + 1) // 2
        return value if code_num % 2 else -value

    def flag(self) -> int:
        return self.read_bits(1)


class BitWriter:
    """Big-endian bit writer producing an RBSP (add RBSP trailing bits)."""

    def __init__(self):
        self._bits = []

    def write_bits(self, value: int, count: int) -> None:
        for index in range(count - 1, -1, -1):
            self._bits.append((value >> index) & 1)

    def write_ue(self, value: int) -> None:
        if value < 0:
            raise ValueError("unsigned Exp-Golomb requires a non-negative value")
        code_num = value + 1
        length = code_num.bit_length()
        self._bits.extend([0] * (length - 1))
        self.write_bits(code_num, length)

    def write_se(self, value: int) -> None:
        code_num = 2 * value - 1 if value > 0 else -2 * value
        self.write_ue(code_num)

    def write_flag(self, value: int) -> None:
        self.write_bits(value, 1)

    def rbsp_trailing_bits(self) -> None:
        self._bits.append(1)
        while len(self._bits) % 8:
            self._bits.append(0)

    def to_bytes(self) -> bytes:
        if len(self._bits) % 8:
            raise ValueError("RBSP is not byte aligned; call rbsp_trailing_bits()")
        out = bytearray()
        for offset in range(0, len(self._bits), 8):
            byte = 0
            for bit in self._bits[offset:offset + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
        return bytes(out)


def _wrap_nal(nal_unit_type: int, nal_ref_idc: int, rbsp: bytes) -> bytes:
    header = (nal_ref_idc << 5) | nal_unit_type
    return bytes([header]) + add_emulation_prevention(rbsp)


def build_sps(
    profile_idc: int = 100,
    constraint_set: int = 0,
    level_idc: int = 40,
    log2_max_frame_num_minus4: int = 0,
    pic_order_cnt_type: int = 2,
    max_num_ref_frames: int = 1,
    gaps_in_frame_num_value_allowed_flag: int = 0,
    pic_width_in_mbs_minus1: int = 79,
    pic_height_in_map_units_minus1: int = 44,
    frame_mbs_only_flag: int = 1,
    direct_8x8_inference_flag: int = 1,
    vui_parameters_present_flag: int = 0,
) -> bytes:
    """Construct the fixed High profile SPS NAL unit used by the tests."""
    writer = BitWriter()
    writer.write_bits(profile_idc, 8)
    writer.write_bits(constraint_set, 8)
    writer.write_bits(level_idc, 8)
    writer.write_ue(0)  # seq_parameter_set_id

    if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        writer.write_ue(1)  # chroma_format_idc = 4:2:0
        writer.write_ue(0)  # bit_depth_luma_minus8
        writer.write_ue(0)  # bit_depth_chroma_minus8
        writer.write_flag(0)  # qpprime_y_zero_transform_bypass_flag
        writer.write_flag(0)  # seq_scaling_matrix_present_flag

    writer.write_ue(log2_max_frame_num_minus4)
    writer.write_ue(pic_order_cnt_type)
    if pic_order_cnt_type == 0:
        writer.write_ue(0)  # log2_max_pic_order_cnt_lsb_minus4
    elif pic_order_cnt_type == 1:
        writer.write_flag(0)
        writer.write_se(0)
        writer.write_se(0)
        writer.write_ue(0)

    writer.write_ue(max_num_ref_frames)
    writer.write_flag(gaps_in_frame_num_value_allowed_flag)
    writer.write_ue(pic_width_in_mbs_minus1)
    writer.write_ue(pic_height_in_map_units_minus1)
    writer.write_flag(frame_mbs_only_flag)
    if not frame_mbs_only_flag:
        writer.write_flag(0)  # mb_adaptive_frame_field_flag
    writer.write_flag(direct_8x8_inference_flag)
    writer.write_flag(0)  # frame_cropping_flag
    writer.write_flag(vui_parameters_present_flag)
    writer.rbsp_trailing_bits()
    return _wrap_nal(7, 3, writer.to_bytes())


def build_pps(pic_init_qp_minus26: int = 0) -> bytes:
    """Construct a minimal CAVLC PPS NAL unit referencing SPS id 0."""
    writer = BitWriter()
    writer.write_ue(0)  # pic_parameter_set_id
    writer.write_ue(0)  # seq_parameter_set_id
    writer.write_flag(0)  # entropy_coding_mode_flag (CAVLC)
    writer.write_flag(0)  # bottom_field_pic_order_in_frame_present_flag
    writer.write_ue(0)  # num_slice_groups_minus1
    writer.write_ue(0)  # num_ref_idx_l0_default_active_minus1
    writer.write_ue(0)  # num_ref_idx_l1_default_active_minus1
    writer.write_flag(0)  # weighted_pred_flag
    writer.write_bits(0, 2)  # weighted_bipred_idc
    writer.write_se(pic_init_qp_minus26)
    writer.write_se(0)  # pic_init_qs_minus26
    writer.write_se(0)  # chroma_qp_index_offset
    writer.write_flag(0)  # deblocking_filter_control_present_flag
    writer.write_flag(0)  # constrained_intra_pred_flag
    writer.write_flag(0)  # redundant_pic_cnt_present_flag
    writer.rbsp_trailing_bits()
    return _wrap_nal(8, 3, writer.to_bytes())


def _skip_scaling_list(reader: BitReader, size: int) -> None:
    last_scale = 8
    next_scale = 8
    for index in range(size):
        if next_scale != 0:
            delta_scale = reader.read_se()
            next_scale = (last_scale + delta_scale + 256) % 256
        if next_scale != 0:
            last_scale = next_scale


def parse_sps(nal_unit: bytes) -> Dict[str, int]:
    """Parse an SPS NAL unit and return profile/level/width/height info."""
    if nal_type(nal_unit) != 7:
        raise ValueError("not an SPS NAL unit (nal_unit_type != 7)")
    rbsp = remove_emulation_prevention(nal_unit[1:])
    reader = BitReader(rbsp)

    profile_idc = reader.read_bits(8)
    constraint_set = reader.read_bits(8)
    level_idc = reader.read_bits(8)
    sps_id = reader.read_ue()

    chroma_format_idc = 1
    if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma_format_idc = reader.read_ue()
        reader.read_ue()  # bit_depth_luma_minus8
        reader.read_ue()  # bit_depth_chroma_minus8
        reader.flag()  # qpprime_y_zero_transform_bypass_flag
        if reader.flag():  # seq_scaling_matrix_present_flag
            lists = (8 if chroma_format_idc != 3 else 12)
            for index in range(lists):
                if reader.flag():  # seq_scaling_list_present_flag
                    _skip_scaling_list(reader, 16 if index < 6 else 64)

    log2_max_frame_num_minus4 = reader.read_ue()
    pic_order_cnt_type = reader.read_ue()
    if pic_order_cnt_type == 0:
        reader.read_ue()
    elif pic_order_cnt_type == 1:
        reader.flag()
        reader.read_se()
        reader.read_se()
        cycle_count = reader.read_ue()
        for _ in range(cycle_count):
            reader.read_se()

    max_num_ref_frames = reader.read_ue()
    gaps_allowed = reader.flag()
    width_in_mbs_minus1 = reader.read_ue()
    height_in_map_units_minus1 = reader.read_ue()
    frame_mbs_only_flag = reader.flag()
    if not frame_mbs_only_flag:
        reader.flag()  # mb_adaptive_frame_field_flag
    direct_8x8_inference_flag = reader.flag()

    frame_cropping_flag = reader.flag()
    crop_left = crop_right = crop_top = crop_bottom = 0
    if frame_cropping_flag:
        crop_left = reader.read_ue()
        crop_right = reader.read_ue()
        crop_top = reader.read_ue()
        crop_bottom = reader.read_ue()
    vui_present = reader.flag()

    width_mbs = width_in_mbs_minus1 + 1
    height_map_units = height_in_map_units_minus1 + 1
    crop_unit_x = 2 if chroma_format_idc in (1, 2) else 1
    crop_unit_y = (2 - frame_mbs_only_flag) * (2 if chroma_format_idc == 1 else 1)
    width = width_mbs * 16 - (crop_left + crop_right) * crop_unit_x
    height = (
        (2 - frame_mbs_only_flag) * height_map_units * 16
        - (crop_top + crop_bottom) * crop_unit_y
    )

    return {
        "profile_idc": profile_idc,
        "constraint_set": constraint_set,
        "level_idc": level_idc,
        "sps_id": sps_id,
        "chroma_format_idc": chroma_format_idc,
        "log2_max_frame_num_minus4": log2_max_frame_num_minus4,
        "pic_order_cnt_type": pic_order_cnt_type,
        "max_num_ref_frames": max_num_ref_frames,
        "gaps_in_frame_num_value_allowed_flag": gaps_allowed,
        "pic_width_in_mbs_minus1": width_in_mbs_minus1,
        "pic_height_in_map_units_minus1": height_in_map_units_minus1,
        "frame_mbs_only_flag": frame_mbs_only_flag,
        "direct_8x8_inference_flag": direct_8x8_inference_flag,
        "frame_cropping_flag": frame_cropping_flag,
        "vui_parameters_present_flag": vui_present,
        "width": width,
        "height": height,
    }
