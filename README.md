# mp4 — 纯标准库 MP4 (ISOBMFF) 解析器与 H.264 封装器

只依赖 Python 3 标准库，实现 ISOBMFF box 递归解析、`stbl` 样本表还原、
H.264 Annex-B/AVCC 转换、SPS 解析以及 MP4 封装。

## 目录结构

| 路径 | 作用 |
| --- | --- |
| `mp4/box.py` | box 递归解析（含 64 位 largesize） |
| `mp4/frames.py` | `stsz/stts/stss/stsc/stco/co64` 解析与每帧偏移还原 |
| `mp4/nal.py` | Annex-B 切分、emulation prevention、AVCC 与 `avcC` |
| `mp4/sps.py` | 位读写器、SPS/PPS 构造与 SPS 解析 |
| `mp4/muxer.py` | MP4 封装、可解码 IDR 帧合成、demo 与命令行入口 |
| `fixtures/` | 由 `fixtures/generate_fixtures.py` 生成的测试输入 |
| `tests/test_mp4.py` | 覆盖六条判定口径的 `unittest` |

## Box 结构

ISOBMFF 中每个 box 的磁盘布局为：

```
+---------+------+-----------------------------+
| size(4) | type | payload (size - header 字节) |
+---------+------+-----------------------------+
```

- `size` 为普通 32 位 box 的总字节数（含 header）。
- `size == 1` 时使用 64 位 `largesize`：header 变为
  `size(4)=1 + type(4) + largesize(8)`，共 16 字节。
- `size == 0` 表示 box 一直延伸到所在区域（通常是文件）末尾。
- 容器 box（`moov/trak/mdia/minf/stbl/dinf/...`）的 payload 全部由子 box
  组成，解析器递归展开到 `Box.children`。`meta` 是 full box，先跳过
  version/flags 4 字节再解析子 box。

解析结果是 `Box(type, size, payload_offset, header_size, children)`，其中
`payload_offset` 是 **相对文件开头的绝对字节偏移**，指向完整 header 之后的
第一个 payload 字节：普通 box 为 `box_start + 8`，largesize box 为
`box_start + 16`。

```python
from mp4.box import parse_boxes

data = open("demo.mp4", "rb").read()
for box in parse_boxes(data):
    print(box.type, box.size, box.payload_offset)
```

## 偏移规则：如何还原每一帧

样本表全部位于 `moov/trak/mdia/minf/stbl`：

- `stsz`：样本总数与每个样本的字节大小（uniform size 时按表展开）。
- `stts`：(sample_count, sample_delta) 运行表，展开为每样本时长。
- `stss`：关键帧的 **1 基** 样本号；不存在 `stss` 表示全部是关键帧。
- `stsc`：(first_chunk, samples_per_chunk, sample_description_index)，
  展开得到每个 chunk 中的样本数。
- `stco`：32 位 chunk 绝对文件偏移；`co64`：64 位 chunk 绝对文件偏移。

还原算法：按 chunk 顺序，从该 chunk 的绝对偏移开始，依次放上
`stsc` 规定数量的样本，每个样本占 `stsz` 给出的字节数，样本首尾相接：

```
cursor = chunk_offset[i]
for sample in chunk_i:
    frame.offset = cursor
    frame.size   = stsz[sample]
    cursor      += frame.size
```

因此 `frame.offset` 直接指向该帧在文件中的第一个字节。AVCC 样本的前 4 字节
是大端 NAL 长度前缀，测试中用 `open(...).seek(offset).read(4)` 在磁盘上验证。

## Annex-B 与 AVCC 转换

Annex-B 码流用起始码分隔 NAL：

- 3 字节起始码 `00 00 01`
- 4 字节起始码 `00 00 00 01`

`split_annexb` 同时识别两种起始码，NAL 之间的零填充不会进入 payload，返回
`NAL(type, payload)`，`type = payload[0] & 0x1F`。

AVCC（MP4 内部格式）去掉所有起始码，每个 NAL 前改写为 **4 字节大端长度
前缀**：

```python
from mp4.nal import split_annexb, annexb_to_avcc

nals = split_annexb(annexb_bytes)            # [(type, payload), ...]
avcc, nals = annexb_to_avcc(annexb_bytes)    # len(4)+NAL, len(4)+NAL, ...
```

SPS/PPS 不放帧数据，而是封进 `stsd/avc1/avcC` decoder configuration record，
`build_avcc(sps, pps)` 负责构造，长度前缀宽度固定为 4 字节
（`lengthSizeMinusOne = 3`）。

## Emulation prevention 与 SPS 解析

H.264 RBSP 中出现 `00 00 00/01/02/03` 时会在第三个字节前插入 `03`，形成
`00 00 03 xx`。解析前先做逆变换：

- 仅当 `03` 前面确实有两个零字节、且后继字节 `<= 03` 时才删除该 `03`；
- 普通数据中的真实 `0x03`（不满足前缀条件）原样保留。

之后用 Exp-Golomb 位读取器按 H.264 7.3.2.1 顺序消费语法元素，直到
`frame_cropping_flag`，再按

```
width  = (pic_width_in_mbs_minus1 + 1) * 16 - 裁剪量
height = (2 - frame_mbs_only_flag) * (pic_height_in_map_units_minus1 + 1) * 16 - 裁剪量
```

计算帧尺寸。判定用 SPS（High profile, 80x45 个宏块）解析结果为 1280x720。

## 封装器

`mux_mp4(frames, width, height, sps, pps, ...)` 写出的顶层结构：

```
ftyp
moov
  mvhd                       timescale=30000
  trak
    tkhd                     宽高（16.16 定点）
    mdia
      mdhd                   timescale=30000, duration=N*1000
      hdlr                   handler=vide
      minf
        vmhd
        dinf(dref(url ))
        stbl
          stsd(avc1(avcC))   SPS/PPS
          stts               每帧 delta=1000（30 fps）
          stss               关键帧样本号
          stsc               每 chunk 1 样本
          stsz               每样本大小
          stco 或 co64       chunk 绝对偏移
mdat                        所有 AVCC 样本首尾拼接
```

可选参数 `use_co64=True` 生成 `co64`，`force_largesize=True` 让 `mdat` 使用
64 位 largesize header。样本偏移在 `moov` 尺寸确定后回填，保证
`stco/co64[0] == mdat payload 的绝对偏移`。

demo 帧使用 `mb_type=I_PCM`（codeNum 25）合成真正符合 H.264 语法的 IDR
切片：对齐后直接携带原始亮度/色度样本，无需熵编码残差，因此标准解码器可解。

## 命令行

```bash
python -m mp4.muxer --demo --out out/demo.mp4
```

标准输出一行摘要（帧数、总时长秒数、宽高、所有 box 类型与字节偏移），例如：

```
demo: frames=5 duration=0.167s width=160 height=96 boxes=[ftyp@0; moov@28; ...]
```

## 测试

```bash
python fixtures/generate_fixtures.py     # 重新生成 fixtures/（可选）
python -m unittest test_mp4 -v
```

测试逐条覆盖：SPS 构造规则与 1280x720、emulation prevention（含真实 0x03
不被误删）、混合起始码 Annex-B 切分、三种 box 形态（32 位/largesize/co64）、
磁盘 seek 验证 AVCC 前缀、自封装自洽（样本数、大小、偏移、时长、文件大小）。
