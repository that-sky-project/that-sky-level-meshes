#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meshes2obj.py - That Sky Level .meshes 全方位转换器 (纯 Python, 适配 Termux)

支持四种转换方向:
    .meshes -> .obj     网格 -> OBJ (touch_object)
    .obj    -> .meshes  OBJ -> 网格 (邻接分块算法)
    .meshes -> .json    网格 -> JSON (全部数据, 可视化)
    .json   -> .meshes  JSON -> 网格 (从 JSON 重建二进制)

用法:
    python3 meshes2obj.py -i input.meshes -o output.obj       # meshes -> obj
    python3 meshes2obj.py -i input.obj -o output.meshes       # obj -> meshes
    python3 meshes2obj.py -i input.meshes -o output.json      # meshes -> json
    python3 meshes2obj.py -i input.json -o output.meshes      # json -> meshes
    python3 meshes2obj.py -i input.meshes --info              # 仅打印文件信息

转换方向根据输入/输出文件扩展名自动判断, 也可用 --mode 显式指定。

移植自 https://github.com/that-sky-project/that-sky-level
仅依赖 Python 标准库, meshopt 编解码用纯 Python 重写, 无需任何原生模块。
"""

import sys
import os
import math
import struct
import json
import argparse
import time


# ============================================================
#  小端二进制流读取器
# ============================================================
class BinaryStream:
    __slots__ = ("data", "pos")

    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def remaining(self):
        return len(self.data) - self.pos

    def read(self, n):
        s = self.data[self.pos:self.pos + n]
        if len(s) != n:
            raise EOFError("读取超出文件末尾")
        self.pos += n
        return s

    def u8(self):
        b = self.data[self.pos]; self.pos += 1
        return b

    def u16(self):
        v = struct.unpack_from("<H", self.data, self.pos)[0]; self.pos += 2
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.data, self.pos)[0]; self.pos += 4
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.data, self.pos)[0]; self.pos += 4
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.data, self.pos)[0]; self.pos += 4
        return v

    def vec3(self):
        x, y, z = struct.unpack_from("<fff", self.data, self.pos)
        self.pos += 12
        return (x, y, z)

    def bytes(self, n):
        return self.read(n)


# ============================================================
#  小端二进制流写入器
# ============================================================
class WritableStream:
    __slots__ = ("buf",)

    def __init__(self):
        self.buf = bytearray()

    def u8(self, v):
        self.buf.append(v & 0xFF)

    def u16(self, v):
        self.buf.extend(struct.pack("<H", v & 0xFFFF))

    def u32(self, v):
        self.buf.extend(struct.pack("<I", v & 0xFFFFFFFF))

    def i32(self, v):
        self.buf.extend(struct.pack("<i", v))

    def f32(self, v):
        self.buf.extend(struct.pack("<f", v))

    def vec3(self, v):
        self.buf.extend(struct.pack("<fff", v[0], v[1], v[2]))

    def bytes(self, data):
        self.buf.extend(data)

    def data(self):
        return bytes(self.buf)

    def __len__(self):
        return len(self.buf)


# ============================================================
#  meshopt 顶点缓冲解码器 (纯 Python, 移植自 meshoptimizer v1.1)
# ============================================================
K_VERTEX_HEADER = 0xA0
K_VERTEX_BLOCK_SIZE_BYTES = 8192
K_VERTEX_BLOCK_MAX_SIZE = 256
K_BYTE_GROUP_SIZE = 16

_REVERSE_BITS8 = [int(format(i, "08b")[::-1], 2) for i in range(256)]


def _get_vertex_block_size(vertex_size):
    result = (K_VERTEX_BLOCK_SIZE_BYTES // vertex_size) & ~(K_BYTE_GROUP_SIZE - 1)
    return result if result < K_VERTEX_BLOCK_MAX_SIZE else K_VERTEX_BLOCK_MAX_SIZE


def _decode_bytes_group(data, pos, out, out_off, bits):
    if bits == 0:
        for i in range(16):
            out[out_off + i] = 0
        return pos
    if bits == 8:
        out[out_off:out_off + 16] = data[pos:pos + 16]
        return pos + 16

    sentinel = (1 << bits) - 1
    byte_size = 8 // bits
    fixed_count = 16 // byte_size
    var_pos = pos + fixed_count
    idx = out_off

    for fb in range(fixed_count):
        byte = data[pos + fb]
        if bits == 1:
            byte = _REVERSE_BITS8[byte]
        for _ in range(byte_size):
            enc = byte >> (8 - bits)
            byte = (byte << bits) & 0xFF
            if enc == sentinel:
                out[idx] = data[var_pos]
                var_pos += 1
            else:
                out[idx] = enc
            idx += 1
    return var_pos


def _decode_bytes(data, pos, buffer_size, bits_table):
    num_groups = buffer_size // 16
    header_size = (num_groups + 3) // 4
    header = data[pos:pos + header_size]
    pos += header_size
    out = bytearray(buffer_size)
    for g in range(num_groups):
        bitsk = (header[g // 4] >> ((g % 4) * 2)) & 3
        pos = _decode_bytes_group(data, pos, out, g * 16, bits_table[bitsk])
    return out, pos


def _decode_deltas_u8(planes, result, base, vertex_count, vertex_size, last_vertex, k):
    for kb in range(4):
        plane = planes[kb]
        p = last_vertex[k + kb]
        off = base + kb
        for i in range(vertex_count):
            v = plane[i]
            v = (((255 if (v & 1) else 0)) ^ (v >> 1)) + p & 0xFF
            result[off] = v
            p = v
            off += vertex_size


def _decode_deltas_u16(planes, result, base, vertex_count, vertex_size, last_vertex, k):
    for kb in (0, 2):
        p = last_vertex[k + kb] | (last_vertex[k + kb + 1] << 8)
        off = base + kb
        p0 = planes[kb]
        p1 = planes[kb + 1]
        for i in range(vertex_count):
            v = p0[i] | (p1[i] << 8)
            v = (((0xFFFF if (v & 1) else 0)) ^ (v >> 1)) + p & 0xFFFF
            result[off] = v & 0xFF
            result[off + 1] = (v >> 8) & 0xFF
            p = v
            off += vertex_size


def _decode_deltas_u32_xor(planes, result, base, vertex_count, vertex_size, last_vertex, k, rot):
    p = (last_vertex[k] | (last_vertex[k + 1] << 8) |
         (last_vertex[k + 2] << 16) | (last_vertex[k + 3] << 24))
    off = base
    p0, p1, p2, p3 = planes[0], planes[1], planes[2], planes[3]
    if rot == 0:
        for i in range(vertex_count):
            v = (p0[i] | (p1[i] << 8) | (p2[i] << 16) | (p3[i] << 24)) ^ p
            result[off] = v & 0xFF
            result[off + 1] = (v >> 8) & 0xFF
            result[off + 2] = (v >> 16) & 0xFF
            result[off + 3] = (v >> 24) & 0xFF
            p = v
            off += vertex_size
    else:
        rshift = 32 - rot
        for i in range(vertex_count):
            v = p0[i] | (p1[i] << 8) | (p2[i] << 16) | (p3[i] << 24)
            v = (((v << rot) | (v >> rshift)) & 0xFFFFFFFF) ^ p
            result[off] = v & 0xFF
            result[off + 1] = (v >> 8) & 0xFF
            result[off + 2] = (v >> 16) & 0xFF
            result[off + 3] = (v >> 24) & 0xFF
            p = v
            off += vertex_size


def _decode_vertex_block(data, pos, result, vertex_offset, vertex_count,
                         vertex_size, last_vertex, channels, version):
    vertex_count_aligned = (vertex_count + 15) & ~15
    control_size = 0 if version == 0 else vertex_size // 4
    control = data[pos:pos + control_size]
    pos += control_size

    planes = [None, None, None, None]
    for k in range(0, vertex_size, 4):
        ctrl_byte = 0 if version == 0 else control[k // 4]
        for j in range(4):
            ctrl = (ctrl_byte >> (j * 2)) & 3
            if ctrl == 3:
                planes[j] = bytearray(data[pos:pos + vertex_count])
                pos += vertex_count
            elif ctrl == 2:
                planes[j] = bytearray(vertex_count)
            else:
                if version == 0:
                    bits_table = (0, 2, 4, 8)
                else:
                    bits_table = (0, 1, 2, 4) if ctrl == 0 else (1, 2, 4, 8)
                planes[j], pos = _decode_bytes(data, pos, vertex_count_aligned, bits_table)

        channel = 0 if version == 0 else channels[k // 4]
        ctype = channel & 3
        base = vertex_offset * vertex_size + k
        if ctype == 0:
            _decode_deltas_u8(planes, result, base, vertex_count, vertex_size, last_vertex, k)
        elif ctype == 1:
            _decode_deltas_u16(planes, result, base, vertex_count, vertex_size, last_vertex, k)
        else:
            rot = (32 - (channel >> 4)) & 31
            _decode_deltas_u32_xor(planes, result, base, vertex_count, vertex_size, last_vertex, k, rot)

    last_start = vertex_offset * vertex_size + (vertex_count - 1) * vertex_size
    last_vertex[:vertex_size] = result[last_start:last_start + vertex_size]
    return pos


def meshopt_decode_vertex_buffer(vertex_count, vertex_size, data):
    """解码 meshopt 顶点缓冲, 返回 (bytearray, version)。"""
    if vertex_size % 4 != 0:
        raise ValueError("vertex size 必须是 4 的倍数")

    data_end = len(data)
    if data_end < 1:
        raise ValueError("meshopt 数据为空")

    header = data[0]
    if (header & 0xF0) != K_VERTEX_HEADER:
        raise ValueError("meshopt 顶点头不匹配: 0x%02X" % header)
    version = header & 0x0F
    if version > 1:
        raise ValueError("不支持的 meshopt 顶点版本: %d" % version)

    tail_size = vertex_size + (0 if version == 0 else vertex_size // 4)
    tail_size_min = 32 if version == 0 else 24
    tail_size_pad = max(tail_size, tail_size_min)

    if data_end < 1 + tail_size_pad:
        raise ValueError("meshopt 数据过短")

    tail_start = data_end - tail_size
    last_vertex = bytearray(256)
    last_vertex[:vertex_size] = data[tail_start:tail_start + vertex_size]
    if version != 0:
        channels = data[tail_start + vertex_size:tail_start + vertex_size + vertex_size // 4]
    else:
        channels = None

    vertex_block_size = _get_vertex_block_size(vertex_size)
    result = bytearray(vertex_count * vertex_size)
    pos = 1
    vertex_offset = 0
    while vertex_offset < vertex_count:
        block_size = min(vertex_block_size, vertex_count - vertex_offset)
        pos = _decode_vertex_block(data, pos, result, vertex_offset, block_size,
                                   vertex_size, last_vertex, channels, version)
        vertex_offset += block_size
    return result, version


# ============================================================
#  meshopt 顶点缓冲编码器 (纯 Python)
#  支持 version 0 (游戏兼容, bit-packed: bits=0 零组 / bits=8 字面量)
#  和 version 1 (ctrl=3 literal, channels=0)
# ============================================================
def _zigzag8_encode(d):
    """无符号 delta (0-255) -> zigzag 编码字节。"""
    if d < 128:
        return (d << 1) & 0xFF
    else:
        return ((d << 1) ^ 0xFF) & 0xFF


def meshopt_encode_vertex_buffer(vertex_count, vertex_size, vertex_data, version=0):
    """
    编码 meshopt 顶点缓冲。
    version=0: 游戏兼容格式 (bit-packed, bits=0/8)
    version=1: ctrl=3 literal, channels=0
    vertex_data: bytes-like, 长度 = vertex_count * vertex_size。
    """
    if vertex_size % 4 != 0:
        raise ValueError("vertex size 必须是 4 的倍数")
    if len(vertex_data) < vertex_count * vertex_size:
        raise ValueError("vertex_data 过短")

    if version == 0:
        return _encode_v0(vertex_count, vertex_size, vertex_data)
    else:
        return _encode_v1(vertex_count, vertex_size, vertex_data)


def _encode_v0(vertex_count, vertex_size, vertex_data):
    """
    Version 0 编码 (游戏兼容)。
    - 无控制字节, 所有通道均为 channel 0 (u8 zigzag delta)
    - 每 16 字节组用 bit-packed 编码, 自动选择最优 bits: 0/2/4/8
    - bits_table = (0, 2, 4, 8): selector 0→0, 1→2, 2→4, 3→8
    - tail: 仅 first_vertex (vertex_size 字节, 无 channels)
    """
    out = bytearray()
    out.append(K_VERTEX_HEADER | 0)  # 0xA0

    if vertex_count == 0:
        tail = bytearray(vertex_data[:vertex_size]) if len(vertex_data) >= vertex_size else bytearray(vertex_size)
        out.extend(tail)
        return bytes(out)

    vertex_block_size = _get_vertex_block_size(vertex_size)
    tail = bytearray(vertex_data[:vertex_size])
    last_vertex = bytearray(vertex_data[:vertex_size])

    vertex_offset = 0
    while vertex_offset < vertex_count:
        block_size = min(vertex_block_size, vertex_count - vertex_offset)
        vertex_count_aligned = (block_size + 15) & ~15
        num_groups = vertex_count_aligned // 16
        header_size = (num_groups + 3) // 4

        for k in range(0, vertex_size, 4):
            for kb in range(4):
                # zigzag delta for this channel
                deltas = bytearray(block_size)
                p = last_vertex[k + kb]
                for i in range(block_size):
                    v = vertex_data[(vertex_offset + i) * vertex_size + k + kb]
                    d = (v - p) & 0xFF
                    deltas[i] = _zigzag8_encode(d)
                    p = v
                last_vertex[k + kb] = vertex_data[(vertex_offset + block_size - 1) * vertex_size + k + kb]

                header = bytearray(header_size)
                data = bytearray()

                for g in range(num_groups):
                    start = g * 16
                    end = min(start + 16, block_size)
                    # pad group to 16 with zeros
                    vals = [deltas[start + i] if start + i < block_size else 0 for i in range(16)]
                    sel, enc = _encode_group_v0(vals)
                    header[g // 4] |= (sel << ((g % 4) * 2))
                    data.extend(enc)

                out.extend(header)
                out.extend(data)

        vertex_offset += block_size

    out.extend(tail)
    return bytes(out)


def _encode_group_v0(vals):
    """Encode a 16-value group for v0. Returns (selector, encoded_bytes).
    bits_table = (0, 2, 4, 8): sel 0→bits=0, sel 1→bits=2, sel 2→bits=4, sel 3→bits=8
    """
    # bits=0: all zero
    if all(v == 0 for v in vals):
        return 0, b''

    # Compute sizes for each option
    # bits=2: sentinel=3, 4 fixed bytes + 1 var byte per value>=3
    var2 = sum(1 for v in vals if v >= 3)
    size2 = 4 + var2

    # bits=4: sentinel=15, 8 fixed bytes + 1 var byte per value>=15
    var4 = sum(1 for v in vals if v >= 15)
    size4 = 8 + var4

    # bits=8: 16 literal bytes
    size8 = 16

    # Pick smallest
    if size2 <= size4 and size2 <= size8:
        return 1, _pack_bits(vals, 2)
    if size4 <= size8:
        return 2, _pack_bits(vals, 4)
    return 3, bytes(vals)


def _pack_bits(vals, bits):
    """Pack 16 values into bit-packed format (MSB-first, sentinel for overflow).
    Returns fixed_bytes + variable_bytes.
    """
    sentinel = (1 << bits) - 1
    byte_size = 8 // bits       # values per fixed byte
    fixed_count = 16 // byte_size
    fixed = bytearray(fixed_count)
    variable = bytearray()

    for i in range(16):
        v = vals[i]
        byte_idx = i // byte_size
        pos_in_byte = i % byte_size
        shift = 8 - bits * (pos_in_byte + 1)
        if v >= sentinel:
            fixed[byte_idx] |= (sentinel << shift)
            variable.append(v)
        else:
            fixed[byte_idx] |= (v << shift)

    return bytes(fixed) + bytes(variable)


def _encode_v1(vertex_count, vertex_size, vertex_data):
    """Version 1 编码: ctrl=3 literal, channels=0 (备用)。"""
    if vertex_count == 0:
        tail = bytearray(vertex_data[:vertex_size]) + bytearray(vertex_size // 4)
        return bytes([K_VERTEX_HEADER | 1]) + bytes(tail)

    out = bytearray()
    out.append(K_VERTEX_HEADER | 1)  # 0xA1

    vertex_block_size = _get_vertex_block_size(vertex_size)
    control_size = vertex_size // 4
    tail = bytearray(vertex_data[:vertex_size]) + bytearray(control_size)
    last_vertex = bytearray(vertex_data[:vertex_size])

    vertex_offset = 0
    while vertex_offset < vertex_count:
        block_size = min(vertex_block_size, vertex_count - vertex_offset)
        out.extend(b'\xff' * control_size)

        for k in range(0, vertex_size, 4):
            for kb in range(4):
                p = last_vertex[k + kb]
                for i in range(block_size):
                    v = vertex_data[(vertex_offset + i) * vertex_size + k + kb]
                    d = (v - p) & 0xFF
                    out.append(_zigzag8_encode(d))
                    p = v
                last_vertex[k + kb] = vertex_data[(vertex_offset + block_size - 1) * vertex_size + k + kb]

        vertex_offset += block_size

    out.extend(tail)
    return bytes(out)


# ============================================================
#  范数编解码辅助
# ============================================================
def _snorm(b):
    """R8G8B8A8_SNORM 单通道: 有符号字节 -> [-1, 1]"""
    s = b - 256 if b >= 128 else b
    v = s / 127.0
    return v if v >= -1.0 else -1.0


def _unorm(b):
    """R8G8B8A8_UNORM 单通道: 无符号字节 -> [0, 1]"""
    return b / 255.0


def _snorm_encode_byte(v):
    """[-1, 1] -> signed byte.
    Matches JS: round(clamp(v,-1,1)*127), clamp to [-128,127].
    -1.0 -> round(-127) = -127 -> byte 129 (matches original game data).
    """
    v = max(-1.0, min(1.0, v))
    x = round(v * 127)
    x = max(-128, min(127, x))
    return x & 0xFF


def _unorm_encode_byte(v):
    """[0, 1] -> 无符号字节 (匹配 JS: (255*clamp(v,0,1))|0, 截断)"""
    v = max(0.0, min(1.0, v))
    return int(255 * v) & 0xFF


# ============================================================
#  kMaterial 枚举表
# ============================================================
K_MATERIAL = {
    "None": 0, "Transparent": 2, "Void": 3, "Particle": 4,
    "WoodSlippery": 5, "VoidMinor": 6, "WoodPlank": 7,
    "Cliff": 16, "Soil": 17, "CliffLight": 18, "WallDamaged": 19,
    "Wall": 20, "Gold": 21, "Glacier": 22, "TileCeiling": 23,
    "TileFloor": 24, "TileWall": 25, "WallBrick": 26, "SoilWet": 27,
    "CliffWet": 28, "Bone": 29, "Wood": 30, "Ceramics": 31,
    "Sand": 32, "SandWet": 33, "SandLight": 34, "Snow": 35,
    "SandDeep": 36, "Mud": 37,
    "Grass": 48, "GrassWet": 49, "GrassLight": 50, "GrassMoss": 51,
    "Cloth": 52, "Cloud": 80,
}

K_MATERIAL_REVERSE = {v: k for k, v in K_MATERIAL.items()}


def _material_name_to_id(name):
    """材质名 -> ID, 匹配 JS TriangleMeshMaterial.getId()。"""
    if not name or not name.startswith("kMaterial_"):
        return 0
    return K_MATERIAL.get(name[10:], 0)


def _material_from_filename(filepath):
    """从文件名提取材质名。
    匹配顺序: 精确匹配 > 后缀分隔 > 包含匹配。
    例: 'output_Cliff.obj' -> 'Cliff', 'Cliff.obj' -> 'Cliff', 'my_Grass_test.obj' -> 'Grass'
    """
    name = os.path.splitext(os.path.basename(filepath))[0]
    # 精确匹配
    if name in K_MATERIAL:
        return name
    # 分隔符匹配 (从后往前找, 优先后缀)
    for sep in ('_', '-', ' '):
        parts = name.split(sep)
        for part in reversed(parts):
            if part in K_MATERIAL:
                return part
    # 包含匹配
    for mat_name in sorted(K_MATERIAL.keys(), key=len, reverse=True):
        if mat_name in name:
            return mat_name
    return "None"


# ============================================================
#  .meshes 结构
# ============================================================
VERTEX_SIZE = 36  # pos(12) + normal(4) + material(8) + input2/3/4(4*3)

MAGIC_LVL0 = 0x304C564C  # "LVL0"
SUPPORTED_VERSIONS = (0x3C, 0x3D)

FLT_MAX = struct.unpack("<f", b'\xff\xff\x7f\x7f')[0]

LOD0_FIXED_BYTES = bytes.fromhex("1B000100C0010000000000000000000000")


class LevelGeoVertex:
    """36 字节顶点, 同时存储原始字节和解码值。"""
    __slots__ = ("raw", "pos", "normal", "normal_w",
                 "material", "weights", "in2", "in3", "in4")

    @classmethod
    def unpack_all(cls, raw_buffer, count):
        """从解压后的原始字节 (count*36) 解析全部顶点。"""
        verts = [None] * count
        off = 0
        for i in range(count):
            v = cls()
            v.raw = bytes(raw_buffer[off:off + VERTEX_SIZE])
            v.pos = struct.unpack_from("<fff", v.raw, 0)
            n = struct.unpack_from("<I", v.raw, 12)[0]
            v.normal = (_snorm(n & 0xFF), _snorm((n >> 8) & 0xFF), _snorm((n >> 16) & 0xFF))
            v.normal_w = _snorm((n >> 24) & 0xFF)
            v.material = tuple(v.raw[16:20])
            v.weights = tuple(b / 255.0 for b in v.raw[20:24])
            v.in2 = tuple(b / 255.0 for b in v.raw[24:28])
            v.in3 = tuple(b / 255.0 for b in v.raw[28:32])
            v.in4 = tuple(b / 255.0 for b in v.raw[32:36])
            verts[i] = v
            off += VERTEX_SIZE
        return verts

    @classmethod
    def from_raw(cls, raw_bytes):
        """从 36 字节原始数据创建。"""
        v = cls()
        v.raw = bytes(raw_bytes)
        v.pos = struct.unpack_from("<fff", v.raw, 0)
        n = struct.unpack_from("<I", v.raw, 12)[0]
        v.normal = (_snorm(n & 0xFF), _snorm((n >> 8) & 0xFF), _snorm((n >> 16) & 0xFF))
        v.normal_w = _snorm((n >> 24) & 0xFF)
        v.material = tuple(v.raw[16:20])
        v.weights = tuple(b / 255.0 for b in v.raw[20:24])
        v.in2 = tuple(b / 255.0 for b in v.raw[24:28])
        v.in3 = tuple(b / 255.0 for b in v.raw[28:32])
        v.in4 = tuple(b / 255.0 for b in v.raw[32:36])
        return v

    @classmethod
    def from_values(cls, pos, normal, material_ids, material_weights, in2, in3, in4):
        """从解码值创建 (用于 obj->meshes)。"""
        raw = bytearray(VERTEX_SIZE)
        struct.pack_into("<fff", raw, 0, pos[0], pos[1], pos[2])
        nx = _snorm_encode_byte(normal[0])
        ny = _snorm_encode_byte(normal[1])
        nz = _snorm_encode_byte(normal[2])
        nw = _snorm_encode_byte(normal[3]) if len(normal) > 3 else 0
        struct.pack_into("<I", raw, 12, nx | (ny << 8) | (nz << 16) | (nw << 24))
        for i in range(4):
            raw[16 + i] = material_ids[i] if i < len(material_ids) else 0
        for i in range(4):
            raw[20 + i] = _unorm_encode_byte(material_weights[i]) if i < len(material_weights) else 0
        for i in range(4):
            raw[24 + i] = _unorm_encode_byte(in2[i]) if i < len(in2) else 0
            raw[28 + i] = _unorm_encode_byte(in3[i]) if i < len(in3) else 0
            raw[32 + i] = _unorm_encode_byte(in4[i]) if i < len(in4) else 0
        return cls.from_raw(raw)


class LevelGeoChunk:
    __slots__ = ("idx_start", "vtx_start", "subchunk_start",
                 "idx_count", "vtx_count", "subchunk_count",
                 "min", "max", "pad")

    @classmethod
    def from_stream(cls, s):
        c = cls()
        c.idx_start = s.u32()
        c.vtx_start = s.u32()
        c.subchunk_start = s.u32()
        c.idx_count = s.u16()
        c.vtx_count = s.u8()
        c.subchunk_count = s.u8()
        c.min = s.vec3()
        c.max = s.vec3()
        c.pad = (s.u32(), s.u32(), s.u32(), s.u32())
        return c

    def to_stream(self, s):
        s.u32(self.idx_start)
        s.u32(self.vtx_start)
        s.u32(self.subchunk_start)
        s.u16(self.idx_count)
        s.u8(self.vtx_count)
        s.u8(self.subchunk_count)
        s.vec3(self.min)
        s.vec3(self.max)
        for p in (self.pad if self.pad else (0, 0, 0, 0)):
            s.u32(p)


class LevelGeoSubchunk:
    __slots__ = ("material_id", "triangle_count", "vtx_count",
                 "triangle_start", "triangle_end", "vtx_start", "vtx_end")

    @classmethod
    def from_stream(cls, s):
        sc = cls()
        sc.material_id = s.u8()
        sc.triangle_count = s.u8()
        sc.vtx_count = s.u8()
        sc.triangle_start = s.u8()
        sc.triangle_end = s.u8()
        sc.vtx_start = s.u8()
        sc.vtx_end = s.u8()
        s.u8()  # padding
        return sc

    def to_stream(self, s):
        # u8 溢出检查: subchunk 所有字段都是 u8 (0-255)
        # 超过 255 会导致游戏读取错误数据, 渲染丢顶点
        for name, val in [
            ("material_id", self.material_id),
            ("triangle_count", self.triangle_count),
            ("vtx_count", self.vtx_count),
            ("triangle_start", self.triangle_start),
            ("triangle_end", self.triangle_end),
            ("vtx_start", self.vtx_start),
            ("vtx_end", self.vtx_end),
        ]:
            if val < 0 or val > 255:
                print("警告: subchunk.%s = %d 超出 u8 范围 (0-255), 已截断" % (name, val))
                val = max(0, min(255, val))
                setattr(self, name, val)
        s.u8(self.material_id)
        s.u8(self.triangle_count)
        s.u8(self.vtx_count)
        s.u8(self.triangle_start)
        s.u8(self.triangle_end)
        s.u8(self.vtx_start)
        s.u8(self.vtx_end)
        s.u8(0)  # padding


class LevelGeo:
    __slots__ = ("index_count", "vertex_count", "chunk_count",
                 "cloud_chunk_count", "subchunk_count",
                 "vertices", "local_indices", "chunks", "subchunks",
                 "meshopt_version")

    @classmethod
    def from_buffer(cls, buf):
        s = BinaryStream(buf)
        geo = cls()
        geo.index_count = s.u32()
        geo.vertex_count = s.u32()
        geo.chunk_count = s.u32()
        geo.cloud_chunk_count = s.u32()
        geo.subchunk_count = s.u32()

        if geo.vertex_count > 0:
            compressed_size = s.u32()
            compressed = s.bytes(compressed_size)
            raw, mv = meshopt_decode_vertex_buffer(geo.vertex_count, VERTEX_SIZE, compressed)
            geo.meshopt_version = mv
            geo.vertices = LevelGeoVertex.unpack_all(raw, geo.vertex_count)
        else:
            geo.meshopt_version = 0
            geo.vertices = []

        geo.local_indices = list(s.bytes(geo.index_count)) if geo.index_count > 0 else []

        total_chunks = geo.chunk_count + geo.cloud_chunk_count
        geo.chunks = [LevelGeoChunk.from_stream(s) for _ in range(total_chunks)]

        geo.subchunks = [LevelGeoSubchunk.from_stream(s) for _ in range(geo.subchunk_count)]
        return geo

    def to_buffer(self):
        s = WritableStream()
        s.u32(self.index_count)
        s.u32(self.vertex_count)
        s.u32(self.chunk_count)
        s.u32(self.cloud_chunk_count)
        s.u32(self.subchunk_count)

        # 顶点缓冲 (meshopt 压缩)
        if self.vertex_count > 0 and self.vertices:
            raw = bytearray()
            for v in self.vertices:
                raw.extend(v.raw)
            compressed = meshopt_encode_vertex_buffer(
                self.vertex_count, VERTEX_SIZE, bytes(raw),
                getattr(self, 'meshopt_version', 0))
            s.u32(len(compressed))
            s.bytes(compressed)

        # 索引
        for idx in self.local_indices:
            s.u8(idx)

        # 分块
        for chunk in self.chunks:
            chunk.to_stream(s)

        # 材质子区间
        for sc in self.subchunks:
            sc.to_stream(s)

        return s.data()


class TocSegment:
    __slots__ = ("type", "offset", "length")

    def __init__(self, type_, offset, length):
        self.type = type_
        self.offset = offset
        self.length = length


def _parse_toc(buf):
    s = BinaryStream(buf)
    count = s.u32()
    toc = {}
    for _ in range(count):
        type_raw = s.bytes(4)
        type_name = type_raw.rstrip(b"\x00").decode("ascii", "replace")
        offset = s.u32()
        length = s.u32()
        toc[type_name] = TocSegment(type_name, offset, length)
    return toc


def _write_toc(segments):
    """segments: list of (type_name, offset, length). 返回 100 字节 TOC。"""
    s = WritableStream()
    s.u32(len(segments))
    seg_data = bytearray(0x60)  # 96 bytes, zero-padded
    for i, (name, offset, length) in enumerate(segments):
        name_bytes = name.encode("ascii")[:4].ljust(4, b'\x00')
        off = i * 12
        seg_data[off:off + 4] = name_bytes
        struct.pack_into("<I", seg_data, off + 4, offset)
        struct.pack_into("<I", seg_data, off + 8, length)
    s.bytes(seg_data)
    return s.data()


class LevelMeshes:
    __slots__ = ("version", "max_pos", "min_pos", "toc", "desc", "desc_raw", "lod_raw", "geo")

    @classmethod
    def from_file_buffer(cls, buffer):
        s = BinaryStream(buffer)
        magic = s.u32()
        if magic != MAGIC_LVL0:
            raise ValueError("不是合法的 .meshes 文件 (魔数: 0x%08X)" % magic)
        version = s.u32()
        if version not in SUPPORTED_VERSIONS:
            raise ValueError("不支持的版本: 0x%X" % version)

        m = cls()
        m.version = version

        toc_buf = s.bytes(0x64)
        m.toc = _parse_toc(toc_buf)

        s.u32()  # padding
        m.max_pos = s.vec3()
        m.min_pos = s.vec3()

        # DESC 段
        seg = m.toc.get("DESC")
        if seg:
            m.desc_raw = buffer[seg.offset:seg.offset + seg.length]
            m.desc = _parse_desc(m.desc_raw)
        else:
            m.desc_raw = None
            m.desc = None

        # LOD0 段
        seg = m.toc.get("LOD0")
        if seg:
            m.lod_raw = buffer[seg.offset:seg.offset + seg.length]
        else:
            m.lod_raw = None

        # GEO0 段
        seg = m.toc.get("GEO0")
        if seg:
            m.geo = LevelGeo.from_buffer(buffer[seg.offset:seg.offset + seg.length])
        else:
            m.geo = None
        return m

    def to_file_buffer(self):
        """序列化为 .meshes 二进制。"""
        header_len = 4 + 4 + 100 + 4 + 12 + 12  # 136

        # 构建内容流 (DESC + LOD0 + GEO0)
        content = WritableStream()
        segments = []
        cursor = 0

        # DESC
        if self.desc_raw is not None:
            content.bytes(self.desc_raw)
            segments.append(("DESC", cursor + header_len, len(self.desc_raw)))
            cursor = len(content)

        # LOD0
        lod_data = self.lod_raw if self.lod_raw is not None else LOD0_FIXED_BYTES
        content.bytes(lod_data)
        segments.append(("LOD0", cursor + header_len, len(lod_data)))
        cursor = len(content)

        # GEO0
        if self.geo is not None:
            geo_data = self.geo.to_buffer()
            content.bytes(geo_data)
            segments.append(("GEO0", cursor + header_len, len(geo_data)))
            cursor = len(content)

        # 构建完整文件
        out = WritableStream()
        out.u32(MAGIC_LVL0)
        out.u32(self.version if self.version in SUPPORTED_VERSIONS else 0x3C)
        out.bytes(_write_toc(segments))
        out.u32(0)  # padding
        out.vec3((FLT_MAX, FLT_MAX, FLT_MAX))
        out.vec3((-FLT_MAX, -FLT_MAX, -FLT_MAX))
        out.bytes(content.data())
        return out.data()


# ============================================================
#  NBT 读取 (小端/Bedrock 风格, 用于 DESC 元数据)
# ============================================================
def _parse_desc(buf):
    try:
        root, _ = _nbt_read_payload(buf, buf[0], 1 + _nbt_skip_name(buf, 1))
        return root if isinstance(root, dict) else {}
    except Exception:
        return {}


def _nbt_skip_name(buf, pos):
    nlen = struct.unpack_from("<H", buf, pos)[0]
    return 2 + nlen


def _nbt_read_name(buf, pos):
    nlen = struct.unpack_from("<H", buf, pos)[0]
    pos += 2
    name = buf[pos:pos + nlen].decode("utf-8", "replace")
    return name, pos + nlen


def _nbt_read_payload(buf, tag_type, pos):
    if tag_type == 1:
        return buf[pos] - 256 if buf[pos] >= 128 else buf[pos], pos + 1
    if tag_type == 2:
        return struct.unpack_from("<h", buf, pos)[0], pos + 2
    if tag_type == 3:
        return struct.unpack_from("<i", buf, pos)[0], pos + 4
    if tag_type == 4:
        return struct.unpack_from("<q", buf, pos)[0], pos + 8
    if tag_type == 7:
        n = struct.unpack_from("<i", buf, pos)[0]; pos += 4
        return buf[pos:pos + n], pos + n
    if tag_type == 8:
        n = struct.unpack_from("<H", buf, pos)[0]; pos += 2
        return buf[pos:pos + n].decode("utf-8", "replace"), pos + n
    if tag_type == 9:
        et = buf[pos]; pos += 1
        n = struct.unpack_from("<i", buf, pos)[0]; pos += 4
        out = []
        for _ in range(n):
            v, pos = _nbt_read_payload(buf, et, pos)
            out.append(v)
        return out, pos
    if tag_type == 10:
        d = {}
        while True:
            ct = buf[pos]; pos += 1
            if ct == 0:
                break
            name, pos = _nbt_read_name(buf, pos)
            v, pos = _nbt_read_payload(buf, ct, pos)
            d[name] = v
        return d, pos
    if tag_type == 11:
        n = struct.unpack_from("<i", buf, pos)[0]; pos += 4
        return list(struct.unpack_from("<%di" % n, buf, pos)), pos + 4 * n
    raise ValueError("不支持的 NBT 标签类型: %d" % tag_type)


# ============================================================
#  NBT 写入 (小端/Bedrock 风格, 用于 DESC 元数据)
# ============================================================
def _nbt_write_desc(desc):
    """将 desc 字典序列化为 NBT 二进制 (匹配 JS LevelDesc.toStream 顺序)。"""
    out = bytearray()
    # 根 compound
    out.append(10)  # TAG_Compound
    out.extend(struct.pack("<H", 0))  # 根名长度 = 0

    # timeStamp (TAG_Int)
    _nbt_write_tag_header(out, "timeStamp", 3)
    out.extend(struct.pack("<i", desc.get("timeStamp", 0)))

    # fileName (TAG_String)
    _nbt_write_tag_header(out, "fileName", 8)
    _nbt_write_string_payload(out, desc.get("fileName", ""))

    # editor (TAG_String)
    _nbt_write_tag_header(out, "editor", 8)
    _nbt_write_string_payload(out, desc.get("editor", ""))

    # editorVersion (TAG_List of TAG_Int)
    _nbt_write_tag_header(out, "editorVersion", 9)
    ev = desc.get("editorVersion", [1, 0, 0])
    out.append(3)  # element type = TAG_Int
    out.extend(struct.pack("<i", len(ev)))
    for v in ev:
        out.extend(struct.pack("<i", v))

    # engineVersion (TAG_List of TAG_Int)
    _nbt_write_tag_header(out, "engineVersion", 9)
    gv = desc.get("engineVersion", [0, 32, 2])
    out.append(3)
    out.extend(struct.pack("<i", len(gv)))
    for v in gv:
        out.extend(struct.pack("<i", v))

    out.append(0)  # TAG_End
    return bytes(out)


def _nbt_write_tag_header(out, name, tag_type):
    out.append(tag_type)
    name_bytes = name.encode("utf-8")
    out.extend(struct.pack("<H", len(name_bytes)))
    out.extend(name_bytes)


def _nbt_write_string_payload(out, value):
    value_bytes = value.encode("utf-8")
    out.extend(struct.pack("<H", len(value_bytes)))
    out.extend(value_bytes)


# ============================================================
#  OBJ 生成 (touchObject)
# ============================================================
def _fmt(f):
    """对齐 JS Number.toString()。"""
    if f != f:
        return "0"
    if f == float("inf"):
        return "Infinity"
    if f == float("-inf"):
        return "-Infinity"
    if f == 0:
        return "0"
    neg = f < 0
    af = -f if neg else f
    s = _js_num_str(af)
    return ("-" + s) if neg else s


def _js_num_str(af):
    r = repr(af)
    if "e" in r:
        mant, exps = r.split("e")
        exp = int(exps)
        if "." in mant:
            intp, frac = mant.split(".")
        else:
            intp, frac = mant, ""
        digs = (intp + frac).rstrip("0") or "0"
        k = exp + len(intp) - 1
    else:
        if "." in r:
            intp, frac = r.split(".")
        else:
            intp, frac = r, ""
        if intp != "0" and intp != "":
            digs = (intp + frac).rstrip("0") or "0"
            k = len(intp) - 1
        else:
            idx = 0
            while idx < len(frac) and frac[idx] == "0":
                idx += 1
            if idx == len(frac):
                return "0"
            digs = frac[idx:].rstrip("0") or "0"
            k = -(idx + 1)

    n = k + 1
    if -6 < n <= 21:
        point = k + 1
        if point <= 0:
            return "0." + "0" * (-point) + digs
        if point >= len(digs):
            return digs + "0" * (point - len(digs))
        return digs[:point] + "." + digs[point:]
    else:
        exp = k
        mant = digs[0] + ("." + digs[1:] if len(digs) > 1 else "")
        return mant + "e" + (("+" if exp >= 0 else "-") + str(abs(exp)))


def touch_object(meshes, merge=False):
    """根据 LevelGeo 生成 OBJ 文本。"""
    geo = meshes.geo
    out = []
    ap = out.append

    for v in geo.vertices:
        ap("v %s %s %s\n" % (_fmt(v.pos[0]), _fmt(v.pos[1]), _fmt(v.pos[2])))
    ap("\n")

    for v in geo.vertices:
        n = v.normal
        ap("vn %s %s %s\n" % (_fmt(n[0]), _fmt(n[1]), _fmt(n[2])))
    ap("\n")

    li = geo.local_indices
    chunks = geo.chunks
    if merge:
        ap("o Chunks\n")

    for i in range(geo.chunk_count):
        if not merge:
            ap("o Chunk_%d\n" % i)
        chunk = chunks[i]
        idx_start = chunk.idx_start
        vtx_start = chunk.vtx_start
        idx_count = chunk.idx_count
        j = 0
        while j < idx_count:
            a = vtx_start + li[idx_start + j] + 1
            b = vtx_start + li[idx_start + j + 1] + 1
            c = vtx_start + li[idx_start + j + 2] + 1
            ap("f %d//%d %d//%d %d//%d\n" % (a, a, b, b, c, c))
            j += 3

    return "".join(out)


# ============================================================
#  多材质 OBJ 拆分输出
# ============================================================
def touch_object_multi(meshes, output_path, use_subchunk=False):
    """
    按材质拆分输出多个 OBJ 文件。

    顶点归属: 取权重最大的材质槽作为主材质。
    面归属: 3 个顶点主材质的多数投票, 平局取权重最高者。

    use_subchunk=True 时, 使用 subchunk 的材质区间数据 (游戏原始分配)。

    output_path: 基础路径, 实际输出为 output_path_MaterialName.obj
    返回: [(material_id, material_name, file_path, vertex_count, face_count), ...]
    """
    geo = meshes.geo
    if not geo or geo.vertex_count == 0:
        print("错误: 无几何数据")
        return []

    li = geo.local_indices

    # ---- 1. 计算每个顶点的主材质 ----
    vertex_dominant = []  # (material_id, weight) per vertex
    for v in geo.vertices:
        best_mat = 0
        best_w = -1.0
        for j in range(4):
            w = v.weights[j]
            if w > best_w:
                best_w = w
                best_mat = v.material[j]
        vertex_dominant.append((best_mat, best_w))

    # ---- 2. 收集每个面及其材质 ----
    face_materials = []  # (material_id, (a, b, c)) 全局顶点索引

    for ci in range(geo.chunk_count):
        chunk = geo.chunks[ci]
        idx_start = chunk.idx_start
        vtx_start = chunk.vtx_start

        if use_subchunk:
            # 使用 subchunk 材质区间 (游戏原始分配)
            for si in range(chunk.subchunk_count):
                sc = geo.subchunks[chunk.subchunk_start + si]
                mat_id = sc.material_id
                for tri in range(sc.triangle_start,
                                 sc.triangle_start + sc.triangle_count):
                    j = tri * 3
                    if j + 2 >= chunk.idx_count:
                        break
                    a = vtx_start + li[idx_start + j]
                    b = vtx_start + li[idx_start + j + 1]
                    c = vtx_start + li[idx_start + j + 2]
                    face_materials.append((mat_id, (a, b, c)))
        else:
            # 使用顶点权重投票
            num_tris = chunk.idx_count // 3
            for t in range(num_tris):
                j = t * 3
                a = vtx_start + li[idx_start + j]
                b = vtx_start + li[idx_start + j + 1]
                c = vtx_start + li[idx_start + j + 2]

                # 多数投票
                mat_votes = {}
                mat_weights = {}
                for vi in (a, b, c):
                    mid, w = vertex_dominant[vi]
                    mat_votes[mid] = mat_votes.get(mid, 0) + 1
                    if mid not in mat_weights or w > mat_weights[mid]:
                        mat_weights[mid] = w

                # 票数最多, 平局取权重最高
                best_mat = max(mat_votes.keys(),
                               key=lambda m: (mat_votes[m], mat_weights[m]))
                face_materials.append((best_mat, (a, b, c)))

    # ---- 3. 按材质分组 ----
    material_groups = {}  # mat_id -> {"faces": [...], "vertices": set()}
    for mat_id, (a, b, c) in face_materials:
        if mat_id not in material_groups:
            material_groups[mat_id] = {"faces": [], "vertices": set()}
        material_groups[mat_id]["faces"].append((a, b, c))
        material_groups[mat_id]["vertices"].update([a, b, c])

    # ---- 4. 输出每个材质的 OBJ ----
    results = []
    base_path = output_path
    if base_path.endswith(".obj"):
        base_path = base_path[:-4]

    for mat_id in sorted(material_groups.keys()):
        group = material_groups[mat_id]
        mat_name = K_MATERIAL_REVERSE.get(mat_id, "Unknown_%d" % mat_id)

        # 顶点重映射 (全局 -> 局部)
        vtx_list = sorted(group["vertices"])
        vtx_map = {gvi: li for li, gvi in enumerate(vtx_list)}

        out_path = "%s_%s.obj" % (base_path, mat_name)
        lines = []
        ap = lines.append

        ap("# Material: %s (ID=%d)\n" % (mat_name, mat_id))
        ap("# Vertices: %d  Faces: %d\n" % (len(vtx_list), len(group["faces"])))
        ap("o %s\n" % mat_name)

        for gvi in vtx_list:
            v = geo.vertices[gvi]
            ap("v %s %s %s\n" % (_fmt(v.pos[0]), _fmt(v.pos[1]), _fmt(v.pos[2])))

        for gvi in vtx_list:
            v = geo.vertices[gvi]
            n = v.normal
            ap("vn %s %s %s\n" % (_fmt(n[0]), _fmt(n[1]), _fmt(n[2])))

        for a, b, c in group["faces"]:
            la = vtx_map[a] + 1
            lb = vtx_map[b] + 1
            lc = vtx_map[c] + 1
            ap("f %d//%d %d//%d %d//%d\n" % (la, la, lb, lb, lc, lc))

        with open(out_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        results.append((mat_id, mat_name, out_path,
                        len(vtx_list), len(group["faces"])))
        print("  %s (ID=%d): %d 顶点, %d 面 -> %s" % (
            mat_name, mat_id, len(vtx_list), len(group["faces"]), out_path))

    return results


# ============================================================
#  OBJ 解析器 (移植自 wavefront.js parseObj)
# ============================================================
class _ObjVertex:
    """OBJ 解析后的顶点 (去重后)。"""
    __slots__ = ("pos", "normal", "nearby", "faces")

    def __init__(self, pos, normal):
        self.pos = pos
        self.normal = normal
        self.nearby = set()  # 共享同一 position 的顶点索引集合
        self.faces = []      # 包含此顶点的面索引列表 (插入序)


class _ObjFace:
    """OBJ 解析后的三角面。"""
    __slots__ = ("indices", "material_id")

    def __init__(self, indices, material_id):
        self.indices = indices
        self.material_id = material_id


def parse_obj(obj_text):
    """
    解析 OBJ 文本, 返回 (vertices, faces)。
    vertices: list of _ObjVertex
    faces: list of _ObjFace (已三角化)
    """
    positions = []
    normals = []
    position_refs = []  # 每个位置的顶点索引集合

    current_mtl = ""
    vertex_map = {}  # (posIdx, normIdx) -> vertex index
    vertices = []
    faces = []

    lines = obj_text.split("\n")
    for line in lines:
        line = line.strip()
        if not line or line[0] == "#":
            continue

        parts = line.split()
        keyword = parts[0]

        if keyword == "v":
            position_refs.append(set())
            positions.append((float(parts[1]), float(parts[2]), float(parts[3])))

        elif keyword == "vn":
            normals.append((float(parts[1]), float(parts[2]), float(parts[3])))

        elif keyword == "usemtl":
            current_mtl = parts[1] if len(parts) > 1 else ""

        elif keyword == "f":
            face_indices = []
            material_id = _material_name_to_id(current_mtl)

            for j in range(1, len(parts)):
                token = parts[j]
                sub = token.split("/")
                pos_idx = int(sub[0])
                norm_idx = None

                if len(sub) >= 3 and sub[2] != "":
                    norm_idx = int(sub[2])

                if pos_idx < 0:
                    pos_idx = len(positions) + 1 + pos_idx
                if norm_idx is not None and norm_idx < 0:
                    norm_idx = len(normals) + 1 + norm_idx

                norm_key = norm_idx if norm_idx is not None else "none"
                key = (pos_idx, norm_key)

                if key not in vertex_map:
                    pos = positions[pos_idx - 1]
                    pos_ref = position_refs[pos_idx - 1]
                    if norm_idx is not None and norm_idx - 1 < len(normals) and norm_idx >= 1:
                        norm = normals[norm_idx - 1]
                    else:
                        norm = (0.0, 1.0, 0.0)

                    vtx = _ObjVertex(pos, norm)
                    vtx_idx = len(vertices)
                    vertex_map[key] = vtx_idx
                    pos_ref.add(vtx_idx)
                    vtx.nearby = pos_ref  # 共享同一个 set 引用
                    vertices.append(vtx)

                face_indices.append(vertex_map[key])

            # 扇形三角化
            for j in range(1, len(face_indices) - 1):
                faces.append(_ObjFace(
                    [face_indices[0], face_indices[j], face_indices[j + 1]],
                    material_id
                ))

    # 构建 vertex.faces
    for fi, face in enumerate(faces):
        for vi in face.indices:
            vertices[vi].faces.append(fi)

    return vertices, faces


# ============================================================
#  邻接分块算法 (移植自 adjacency.js LevelCvtAdjacency)
# ============================================================
class _CvtChunk:
    """转换中的分块。"""
    __slots__ = ("vertices", "active_subchunks", "idx_buffer", "vtx_buffer", "sub_buffer",
                 "idx_start", "vtx_start", "subchunk_start",
                 "idx_count", "vtx_count", "subchunk_count", "min", "max", "pad")

    def __init__(self):
        self.vertices = {}  # vertex_idx -> local_index
        self.active_subchunks = {}  # material_id -> subchunk dict
        self.idx_buffer = []
        self.vtx_buffer = []
        self.sub_buffer = []

    def begin_subchunk(self, sc, material_id):
        sc["material_id"] = material_id if material_id else 16  # Cliff
        sc["triangle_start"] = len(self.idx_buffer) // 3
        sc["triangle_count"] = 1
        sc["vtx_start"] = 0

    def end_subchunk(self, sc):
        sc["triangle_end"] = sc["triangle_start"] + sc["triangle_count"] - 1
        sc["vtx_count"] = len(self.vtx_buffer)
        sc["vtx_end"] = sc["vtx_start"] + sc["vtx_count"] - 1

    def done(self):
        for sc in self.active_subchunks.values():
            self.end_subchunk(sc)
            self.sub_buffer.append(sc)
        self.active_subchunks.clear()

        self.idx_count = len(self.idx_buffer)
        self.vtx_count = len(self.vtx_buffer)
        self.subchunk_count = len(self.sub_buffer)

        min_x = min_y = min_z = float("inf")
        max_x = max_y = max_z = float("-inf")
        for vi in self.vtx_buffer:
            px, py, pz = all_vertices[vi].pos
            if px < min_x: min_x = px
            if py < min_y: min_y = py
            if pz < min_z: min_z = pz
            if px > max_x: max_x = px
            if py > max_y: max_y = py
            if pz > max_z: max_z = pz
        self.min = (min_x - 0.1, min_y - 0.1, min_z - 0.1)
        self.max = (max_x + 0.1, max_y + 0.1, max_z + 0.1)
        self.pad = (0, 0, 0, 0)

    def try_assign_active_subchunk(self, face):
        if len(self.sub_buffer) + len(self.active_subchunks) > 252:
            return False

        # 多材质模式: 直接使用面的材质 (每个面来自一个 OBJ, 材质明确)
        # 单材质模式: 使用顶点材质 (兼容原始行为)
        if use_face_material and face.material_id:
            face_mats = {face.material_id}
        else:
            face_mats = set()
            for vi in face.indices:
                face_mats.add(all_vertex_materials[vi])

        # 移除不在当前面材质中的活跃子块
        to_remove = []
        for m, sc in self.active_subchunks.items():
            if m not in face_mats:
                self.end_subchunk(sc)
                self.sub_buffer.append(sc)
                to_remove.append(m)
            else:
                sc["triangle_count"] += 1
        for m in to_remove:
            del self.active_subchunks[m]

        # 为当前面的材质添加新子块
        for m in face_mats:
            if m not in self.active_subchunks:
                sc = {}
                self.begin_subchunk(sc, m)
                self.active_subchunks[m] = sc

        return True

    def try_add_face(self, face):
        if len(self.idx_buffer) + 3 > 756:
            return False

        new_vtx_count = 0
        for vi in face.indices:
            if vi not in self.vertices:
                new_vtx_count += 1

        if len(self.vtx_buffer) + new_vtx_count > 252:
            return False

        if not self.try_assign_active_subchunk(face):
            return False

        for vi in face.indices:
            if vi not in self.vertices:
                idx = len(self.vtx_buffer)
                self.vertices[vi] = idx
                self.vtx_buffer.append(vi)
            else:
                idx = self.vertices[vi]
            self.idx_buffer.append(idx)

        return True


# 全局变量 (在 obj_to_geo 中设置)
all_vertices = None
all_vertex_materials = None
use_face_material = False  # 多材质模式开关


def obj_to_geo(vertices, faces, vertex_materials_multi=None):
    """
    将 OBJ 解析后的顶点和面转换为 LevelGeo。
    使用 BFS 邻接分块算法 (移植自 adjacency.js)。

    vertex_materials_multi: 多材质权重列表, 每个元素为 [(mat_id, weight), ...]
        若提供则启用多材质模式: 面使用自身 material_id, 顶点存储多材质权重。
    """
    global all_vertices, all_vertex_materials, use_face_material
    all_vertices = vertices
    all_vertex_materials = [0] * len(vertices)
    use_face_material = vertex_materials_multi is not None

    if use_face_material:
        # 多材质模式: 取权重最大的材质作为顶点主材质 (用于分块限制检查)
        for vi, mats in enumerate(vertex_materials_multi):
            if mats:
                all_vertex_materials[vi] = mats[0][0]
    else:
        # 单材质模式: 取第一个使用它的面的材质
        for face in faces:
            for vi in face.indices:
                if all_vertex_materials[vi] == 0:
                    all_vertex_materials[vi] = face.material_id

    # BFS 分块分配
    unprocessed = dict.fromkeys(range(len(vertices)), None)  # 保持插入序
    visited_face = set()
    chunks = []

    # 初始 loop: 第一个未处理顶点
    if unprocessed:
        first_key = next(iter(unprocessed))
        loop = {first_key}
    else:
        loop = set()

    while unprocessed:
        chunk = _assign_chunk(loop, unprocessed, visited_face, vertices, faces)
        chunk.done()
        chunks.append(chunk)

    # 构建全局数组
    local_indices = []
    geo_vertices = []
    subchunks = []

    for chunk in chunks:
        chunk.idx_start = len(local_indices)
        chunk.vtx_start = len(geo_vertices)
        chunk.subchunk_start = len(subchunks)

        local_indices.extend(chunk.idx_buffer)

        for vi in chunk.vtx_buffer:
            vtx = vertices[vi]
            if use_face_material and vertex_materials_multi:
                # 多材质: 填充 4 个材质槽
                mats = vertex_materials_multi[vi]
                mat_ids = [0, 0, 0, 0]
                mat_weights = [0.0, 0.0, 0.0, 0.0]
                for j, (mid, w) in enumerate(mats[:4]):
                    mat_ids[j] = mid
                    mat_weights[j] = w
                geo_v = LevelGeoVertex.from_values(
                    pos=vtx.pos,
                    normal=(vtx.normal[0], vtx.normal[1], vtx.normal[2], 0.0),
                    material_ids=tuple(mat_ids),
                    material_weights=tuple(mat_weights),
                    in2=(0.99, 0.99, 0.99, 0.99),
                    in3=(0.5, 0.5, 0.5, 0.5),
                    in4=(0.04, 0.004, 0.004, 0.004),
                )
            else:
                # 单材质
                mat_id = all_vertex_materials[vi]
                geo_v = LevelGeoVertex.from_values(
                    pos=vtx.pos,
                    normal=(vtx.normal[0], vtx.normal[1], vtx.normal[2], 0.0),
                    material_ids=(mat_id, 0, 0, 0),
                    material_weights=(1.0, 0.0, 0.0, 0.0),
                    in2=(0.99, 0.99, 0.99, 0.99),
                    in3=(0.5, 0.5, 0.5, 0.5),
                    in4=(0.04, 0.004, 0.004, 0.004),
                )
            geo_vertices.append(geo_v)

        subchunks.extend(chunk.sub_buffer)

    geo = LevelGeo()
    geo.cloud_chunk_count = 0
    geo.meshopt_version = 0  # 游戏兼容版本
    geo.index_count = len(local_indices)
    geo.vertex_count = len(geo_vertices)
    geo.chunk_count = len(chunks)
    geo.subchunk_count = len(subchunks)
    geo.local_indices = local_indices
    geo.vertices = geo_vertices

    # 构建 LevelGeoChunk 对象
    geo.chunks = []
    for chunk in chunks:
        c = LevelGeoChunk()
        c.idx_start = chunk.idx_start
        c.vtx_start = chunk.vtx_start
        c.subchunk_start = chunk.subchunk_start
        c.idx_count = chunk.idx_count
        c.vtx_count = chunk.vtx_count
        c.subchunk_count = chunk.subchunk_count
        c.min = chunk.min
        c.max = chunk.max
        c.pad = chunk.pad
        geo.chunks.append(c)

    # 构建 LevelGeoSubchunk 对象
    geo.subchunks = []
    for sc in subchunks:
        s = LevelGeoSubchunk()
        s.material_id = sc["material_id"]
        s.triangle_count = sc["triangle_count"]
        s.vtx_count = sc["vtx_count"]
        s.triangle_start = sc["triangle_start"]
        s.triangle_end = sc["triangle_end"]
        s.vtx_start = sc["vtx_start"]
        s.vtx_end = sc["vtx_end"]
        geo.subchunks.append(s)

    return geo


def _assign_chunk(start, unprocessed, visited_face, vertices, faces):
    """BFS 分块分配 (移植自 adjacency.js assignChunk)。"""
    recursive_vtx = set(start)
    chunk = _CvtChunk()
    done = False
    next_loop_vtx = set()

    while not done:
        next_loop_vtx = set()

        for vtx_idx in recursive_vtx:
            if vtx_idx not in unprocessed:
                continue

            del unprocessed[vtx_idx]

            # selectFace: 收集附近所有顶点的面
            nearby = vertices[vtx_idx].nearby
            candidate_faces = []
            seen = set()
            for v_idx in nearby:
                for f_idx in vertices[v_idx].faces:
                    if f_idx not in seen:
                        seen.add(f_idx)
                        candidate_faces.append(f_idx)

            for f_idx in candidate_faces:
                if f_idx in visited_face:
                    continue

                face = faces[f_idx]
                if not chunk.try_add_face(face):
                    done = True
                    break

                visited_face.add(f_idx)
                # updateNextLoop: 添加面中未处理的顶点
                for vi in face.indices:
                    if vi in unprocessed:
                        next_loop_vtx.add(vi)

            if done:
                break

        if not done and not next_loop_vtx:
            if not unprocessed:
                done = True
            else:
                next_loop_vtx.add(next(iter(unprocessed)))

        recursive_vtx = next_loop_vtx

    # 将剩余的连续顶点传递给下一个分块
    if next_loop_vtx:
        start.clear()
        start.update(next_loop_vtx)

    return chunk


def obj_to_meshes(obj_text):
    """OBJ 文本 -> LevelMeshes 对象。"""
    vertices, faces = parse_obj(obj_text)
    geo = obj_to_geo(vertices, faces)

    m = LevelMeshes()
    m.version = 0x3C
    m.max_pos = (FLT_MAX, FLT_MAX, FLT_MAX)
    m.min_pos = (-FLT_MAX, -FLT_MAX, -FLT_MAX)
    m.desc = {
        "timeStamp": 0,
        "fileName": "",
        "editor": "that-sky-level",
        "editorVersion": [1, 0, 0],
        "engineVersion": [0, 32, 2],
    }
    m.desc_raw = _nbt_write_desc(m.desc)
    m.lod_raw = LOD0_FIXED_BYTES
    m.geo = geo
    m.toc = None
    return m


# ============================================================
#  多 OBJ -> meshes (按材质拆分输入, 交界处权重过渡)
# ============================================================
def parse_multi_obj(obj_paths):
    """
    解析多个 OBJ 文件, 按位置合并顶点, 计算多材质权重。

    顶点合并: 相同坐标 (6位精度) 的顶点合并为一个, 法线取面几何自动计算。
    权重分配: 交界处顶点按各材质面数比例分配权重 (最多 4 个材质槽)。

    返回: (vertices, faces, vertex_materials_multi)
      - vertices: list of _ObjVertex
      - faces: list of _ObjFace (material_id 来自文件名)
      - vertex_materials_multi: list of [(mat_id, weight), ...] 每顶点
    """
    pos_map = {}       # (x,y,z) rounded -> vertex index
    vertices = []
    faces = []

    # 每个位置被各材质面引用的次数
    pos_mat_counts = {}  # pos_key -> {mat_id: face_count}

    for obj_path in obj_paths:
        mat_name = _material_from_filename(obj_path)
        mat_id = K_MATERIAL.get(mat_name, 0)
        if mat_id == 0:
            print("  警告: 无法从 %s 识别材质, 跳过" % os.path.basename(obj_path))
            continue

        print("  %s -> 材质 %s (ID=%d)" % (os.path.basename(obj_path), mat_name, mat_id))

        with open(obj_path, "r", encoding="utf-8") as f:
            obj_text = f.read()

        # 简单解析: 顶点位置 + 面
        obj_positions = []
        obj_faces = []

        for line in obj_text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if parts[0] == "v":
                obj_positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif parts[0] == "f":
                idxs = []
                for p in parts[1:]:
                    vi = int(p.split("/")[0])
                    idxs.append(vi - 1 if vi > 0 else len(obj_positions) + vi)
                for i in range(1, len(idxs) - 1):
                    obj_faces.append((idxs[0], idxs[i], idxs[i + 1]))

        # 合并到全局数组
        for f_tri in obj_faces:
            i0, i1, i2 = f_tri
            p0, p1, p2 = obj_positions[i0], obj_positions[i1], obj_positions[i2]

            global_idxs = []
            for pos in (p0, p1, p2):
                key = (round(pos[0], 6), round(pos[1], 6), round(pos[2], 6))
                if key not in pos_map:
                    vtx = _ObjVertex(pos, (0.0, 1.0, 0.0))
                    vtx_idx = len(vertices)
                    vertices.append(vtx)
                    pos_map[key] = vtx_idx
                    pos_mat_counts[key] = {}
                gvi = pos_map[key]
                global_idxs.append(gvi)

                # 统计该位置被当前材质引用的次数
                pos_mat_counts[key][mat_id] = pos_mat_counts[key].get(mat_id, 0) + 1

            faces.append(_ObjFace(global_idxs, mat_id))

    if not vertices:
        raise ValueError("所有 OBJ 文件均无有效几何数据")

    # ---- 计算法线 (从面几何) ----
    accum = [[0.0, 0.0, 0.0] for _ in range(len(vertices))]
    for face in faces:
        i0, i1, i2 = face.indices
        p0, p1, p2 = vertices[i0].pos, vertices[i1].pos, vertices[i2].pos
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 0.0001:
            nx, ny, nz = nx / length, ny / length, nz / length
        for vi in (i0, i1, i2):
            accum[vi][0] += nx
            accum[vi][1] += ny
            accum[vi][2] += nz

    for i, v in enumerate(vertices):
        nx, ny, nz = accum[i]
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 0.0001:
            v.normal = (nx / length, ny / length, nz / length)
        else:
            v.normal = (0.0, 1.0, 0.0)

    # ---- 构建 nearby 集合 (同位置顶点) ----
    for i, v in enumerate(vertices):
        v.nearby = {i}

    # ---- 构建 vertex.faces ----
    for fi, face in enumerate(faces):
        for vi in face.indices:
            vertices[vi].faces.append(fi)

    # ---- 计算每顶点的材质权重 ----
    vertex_materials_multi = []
    for i, v in enumerate(vertices):
        key = (round(v.pos[0], 6), round(v.pos[1], 6), round(v.pos[2], 6))
        mat_counts = pos_mat_counts.get(key, {})
        total = sum(mat_counts.values())
        if total > 0:
            # 按面数比例分配权重, 最多 4 个材质
            weights = sorted(
                [(mid, cnt / total) for mid, cnt in mat_counts.items()],
                key=lambda x: -x[1]
            )
        else:
            weights = [(0, 1.0)]
        vertex_materials_multi.append(weights)

    # 统计输出
    boundary_count = sum(1 for w in vertex_materials_multi if len(w) > 1)
    print("  合并结果: %d 顶点, %d 面, %d 交界顶点 (多材质)" % (
        len(vertices), len(faces), boundary_count))

    return vertices, faces, vertex_materials_multi


def multi_obj_to_meshes(obj_paths):
    """多 OBJ 文件 -> LevelMeshes 对象 (按材质名确定材质, 交界处权重过渡)。"""
    print("\n正在解析多 OBJ 文件...")
    vertices, faces, vertex_materials_multi = parse_multi_obj(obj_paths)

    print("\n正在执行邻接分块...")
    geo = obj_to_geo(vertices, faces, vertex_materials_multi)

    m = LevelMeshes()
    m.version = 0x3C
    m.max_pos = (FLT_MAX, FLT_MAX, FLT_MAX)
    m.min_pos = (-FLT_MAX, -FLT_MAX, -FLT_MAX)
    m.desc = {
        "timeStamp": int(time.time()),
        "fileName": "",
        "editor": "that-sky-level",
        "editorVersion": [1, 0, 0],
        "engineVersion": [0, 32, 2],
    }
    m.desc_raw = _nbt_write_desc(m.desc)
    m.lod_raw = LOD0_FIXED_BYTES
    m.geo = geo
    m.toc = None
    return m


# ============================================================
#  meshes -> JSON export (professional English, no raw bytes)
# ============================================================
def meshes_to_json(meshes):
    """Export LevelMeshes to a clean, professional JSON dict."""
    geo = meshes.geo
    tri_count = geo.index_count // 3 if geo else 0

    data = {
        "format": "that-sky-level-meshes",
        "version": "0x%X" % meshes.version,
        "metadata": _build_desc_json(meshes),
        "bounds": {
            "max": {"x": meshes.max_pos[0], "y": meshes.max_pos[1], "z": meshes.max_pos[2]},
            "min": {"x": meshes.min_pos[0], "y": meshes.min_pos[1], "z": meshes.min_pos[2]},
        },
        "geometry": {
            "summary": {
                "vertex_count": geo.vertex_count if geo else 0,
                "triangle_count": tri_count,
                "index_count": geo.index_count if geo else 0,
                "chunk_count": geo.chunk_count if geo else 0,
                "cloud_chunk_count": geo.cloud_chunk_count if geo else 0,
                "subchunk_count": geo.subchunk_count if geo else 0,
                "meshopt_version": getattr(geo, 'meshopt_version', 0) if geo else 0,
            },
            "vertices": [],
            "triangles": [],
            "chunks": [],
            "subchunks": [],
            "indices": list(geo.local_indices) if geo else [],
        },
    }

    if not geo:
        return data

    # ---- vertices ----
    for i, v in enumerate(geo.vertices):
        materials = []
        for j in range(4):
            mid = v.material[j]
            materials.append({
                "name": K_MATERIAL_REVERSE.get(mid, "Unknown_%d" % mid),
                "id": mid,
                "weight": v.weights[j],
            })

        data["geometry"]["vertices"].append({
            "index": i,
            "position": {"x": v.pos[0], "y": v.pos[1], "z": v.pos[2]},
            "normal": {"x": v.normal[0], "y": v.normal[1], "z": v.normal[2], "w": v.normal_w},
            "materials": materials,
            "input2_ao_roughness": {"x": v.in2[0], "y": v.in2[1], "z": v.in2[2], "w": v.in2[3]},
            "input3_detail": {"x": v.in3[0], "y": v.in3[1], "z": v.in3[2], "w": v.in3[3]},
            "input4_misc": {"x": v.in4[0], "y": v.in4[1], "z": v.in4[2], "w": v.in4[3]},
        })

    # ---- triangles ----
    tri_global = 0
    for ci, chunk in enumerate(geo.chunks):
        if ci >= geo.chunk_count:
            break  # skip cloud chunks
        idx_start = chunk.idx_start
        vtx_start = chunk.vtx_start
        for j in range(0, chunk.idx_count, 3):
            a = vtx_start + geo.local_indices[idx_start + j]
            b = vtx_start + geo.local_indices[idx_start + j + 1]
            c = vtx_start + geo.local_indices[idx_start + j + 2]
            data["geometry"]["triangles"].append({
                "index": tri_global,
                "chunk": ci,
                "indices": [a, b, c],
                "positions": [
                    {"x": geo.vertices[a].pos[0], "y": geo.vertices[a].pos[1], "z": geo.vertices[a].pos[2]},
                    {"x": geo.vertices[b].pos[0], "y": geo.vertices[b].pos[1], "z": geo.vertices[b].pos[2]},
                    {"x": geo.vertices[c].pos[0], "y": geo.vertices[c].pos[1], "z": geo.vertices[c].pos[2]},
                ],
            })
            tri_global += 1

    # ---- chunks ----
    for i, c in enumerate(geo.chunks):
        is_cloud = i >= geo.chunk_count
        data["geometry"]["chunks"].append({
            "index": i,
            "type": "cloud" if is_cloud else "terrain",
            "vertex_range": {"start": c.vtx_start, "count": c.vtx_count},
            "index_range": {"start": c.idx_start, "count": c.idx_count},
            "subchunk_range": {"start": c.subchunk_start, "count": c.subchunk_count},
            "bounds": {
                "min": {"x": c.min[0], "y": c.min[1], "z": c.min[2]},
                "max": {"x": c.max[0], "y": c.max[1], "z": c.max[2]},
            },
        })

    # ---- subchunks ----
    for i, sc in enumerate(geo.subchunks):
        mname = K_MATERIAL_REVERSE.get(sc.material_id, "Unknown_%d" % sc.material_id)
        data["geometry"]["subchunks"].append({
            "index": i,
            "material": {"name": mname, "id": sc.material_id},
            "triangle_range": {"start": sc.triangle_start, "end": sc.triangle_end, "count": sc.triangle_count},
            "vertex_range": {"start": sc.vtx_start, "end": sc.vtx_end, "count": sc.vtx_count},
        })

    # ---- binary segments (required for exact reconstruction) ----
    # DESC and LOD0 are small binary blobs that cannot be losslessly
    # reconstructed from decoded values (field order, extra fields, etc.)
    if meshes.desc_raw is not None:
        data["desc_raw"] = list(meshes.desc_raw)
    if meshes.lod_raw is not None:
        data["lod0_raw"] = list(meshes.lod_raw)

    return data


def _build_desc_json(meshes):
    """Build DESC metadata JSON."""
    if meshes.desc is None:
        return None
    desc = meshes.desc
    ts = desc.get("timeStamp", 0)
    time_str = ""
    if ts:
        try:
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        except Exception:
            time_str = ""

    ev = desc.get("editorVersion", [])
    gv = desc.get("engineVersion", [])
    ev_str = ".".join(str(x) for x in ev) if ev else ""
    gv_str = ".".join(str(x) for x in gv) if gv else ""

    return {
        "file_name": desc.get("fileName", ""),
        "editor": desc.get("editor", ""),
        "editor_version": ev_str,
        "engine_version": gv_str,
        "timestamp": ts,
        "time": time_str,
    }


# ============================================================
#  JSON -> meshes import (reconstruct from decoded values)
# ============================================================
def _dict_to_vec3(d, default=(0.0, 0.0, 0.0)):
    """Extract (x, y, z) from a dict or list."""
    if isinstance(d, dict):
        return (d.get("x", default[0]), d.get("y", default[1]), d.get("z", default[2]))
    if isinstance(d, (list, tuple)):
        return (d[0], d[1], d[2])
    return default


def _dict_to_vec4(d, default=(0.0, 0.0, 0.0, 0.0)):
    """Extract (x, y, z, w) from a dict or list."""
    if isinstance(d, dict):
        return (d.get("x", default[0]), d.get("y", default[1]), d.get("z", default[2]), d.get("w", default[3]))
    if isinstance(d, (list, tuple)):
        return (d[0], d[1], d[2], d[3] if len(d) > 3 else 0.0)
    return default


def json_to_meshes(data):
    """Reconstruct LevelMeshes from JSON dict."""
    m = LevelMeshes()

    # version
    ver = data.get("version", 0x3C)
    if isinstance(ver, str):
        ver = int(ver, 16)
    m.version = ver

    # bounds
    bbox = data.get("bounds", data.get("header", {}))
    max_pos = bbox.get("max", bbox.get("max_pos", {}))
    min_pos = bbox.get("min", bbox.get("min_pos", {}))
    m.max_pos = _dict_to_vec3(max_pos, (FLT_MAX, FLT_MAX, FLT_MAX))
    m.min_pos = _dict_to_vec3(min_pos, (-FLT_MAX, -FLT_MAX, -FLT_MAX))

    # DESC
    desc_raw = data.get("desc_raw")
    if desc_raw is not None:
        m.desc_raw = bytes(desc_raw)
        m.desc = _parse_desc(m.desc_raw)
    else:
        desc_json = data.get("metadata", data.get("desc"))
        if desc_json is not None:
            if isinstance(desc_json, dict):
                ev_str = desc_json.get("editor_version", desc_json.get("editorVersion", ""))
                gv_str = desc_json.get("engine_version", desc_json.get("engineVersion", ""))
                ev = [int(x) for x in ev_str.split(".")] if ev_str else desc_json.get("editorVersion", [1, 0, 0])
                gv = [int(x) for x in gv_str.split(".")] if gv_str else desc_json.get("engineVersion", [0, 32, 2])
                m.desc = {
                    "timeStamp": desc_json.get("timestamp", desc_json.get("timeStamp", 0)),
                    "fileName": desc_json.get("file_name", desc_json.get("fileName", "")),
                    "editor": desc_json.get("editor", ""),
                    "editorVersion": ev,
                    "engineVersion": gv,
                }
            else:
                m.desc = desc_json
            m.desc_raw = _nbt_write_desc(m.desc)
        else:
            m.desc = None
            m.desc_raw = None

    # LOD0
    lod_raw = data.get("lod0_raw")
    if lod_raw is not None:
        m.lod_raw = bytes(lod_raw)
    else:
        m.lod_raw = LOD0_FIXED_BYTES

    # GEO0
    geo_data = data.get("geometry", data.get("geo"))
    if geo_data is not None:
        geo = LevelGeo()

        stats = geo_data.get("summary", geo_data)
        geo.vertex_count = stats.get("vertex_count", geo_data.get("vertex_count", 0))
        geo.index_count = stats.get("index_count", geo_data.get("index_count", 0))
        geo.chunk_count = stats.get("chunk_count", geo_data.get("chunk_count", 0))
        geo.cloud_chunk_count = stats.get("cloud_chunk_count", geo_data.get("cloud_chunk_count", 0))
        geo.subchunk_count = stats.get("subchunk_count", geo_data.get("subchunk_count", 0))
        geo.meshopt_version = stats.get("meshopt_version", geo_data.get("meshopt_version", 0))
        geo.local_indices = geo_data.get("indices", geo_data.get("local_indices", []))

        # vertices — reconstruct from decoded values
        vtx_list = geo_data.get("vertices", [])
        raw_vtx = data.get("_原始数据_精确往返用", {}).get("顶点原始字节")
        geo.vertices = []
        for i, vd in enumerate(vtx_list):
            # backward-compat: use raw bytes if present
            raw = vd.get("raw", vd.get("原始字节_36B"))
            if raw is not None:
                geo.vertices.append(LevelGeoVertex.from_raw(bytes(raw)))
                continue
            if raw_vtx is not None and i < len(raw_vtx):
                geo.vertices.append(LevelGeoVertex.from_raw(bytes(raw_vtx[i])))
                continue
            # reconstruct from decoded values
            pos = _dict_to_vec3(vd.get("position", vd.get("pos", {})))
            norm_d = vd.get("normal", {})
            norm = _dict_to_vec3(norm_d, (0.0, 1.0, 0.0))
            norm_w = norm_d.get("w", vd.get("normal_w", 0.0)) if isinstance(norm_d, dict) else 0.0

            mats_field = vd.get("materials", vd.get("材质"))
            if isinstance(mats_field, list) and mats_field and isinstance(mats_field[0], dict):
                mat_ids = tuple(mm.get("id", mm.get("ID", 0)) for mm in mats_field)
                mat_weights = tuple(mm.get("weight", mm.get("权重", 0)) for mm in mats_field)
            else:
                mat_ids = tuple(vd.get("material_ids", (0, 0, 0, 0)))
                mat_weights = tuple(vd.get("material_weights", (0, 0, 0, 0)))

            in2 = _dict_to_vec4(vd.get("input2_ao_roughness", vd.get("input2", {})), (0.99, 0.99, 0.99, 0.99))
            in3 = _dict_to_vec4(vd.get("input3_detail", vd.get("input3", {})), (0.5, 0.5, 0.5, 0.5))
            in4 = _dict_to_vec4(vd.get("input4_misc", vd.get("input4", {})), (0.04, 0.004, 0.004, 0.004))

            geo.vertices.append(LevelGeoVertex.from_values(
                pos=pos,
                normal=norm + (norm_w,),
                material_ids=mat_ids,
                material_weights=mat_weights,
                in2=in2,
                in3=in3,
                in4=in4,
            ))

        # chunks
        chunk_list = geo_data.get("chunks", geo_data.get("地形块列表", []))
        geo.chunks = []
        for cd in chunk_list:
            c = LevelGeoChunk()
            vr = cd.get("vertex_range", {})
            ir = cd.get("index_range", {})
            sr = cd.get("subchunk_range", {})
            c.vtx_start = vr.get("start", cd.get("vtx_start", 0))
            c.vtx_count = vr.get("count", cd.get("vtx_count", 0))
            c.idx_start = ir.get("start", cd.get("idx_start", 0))
            c.idx_count = ir.get("count", cd.get("idx_count", 0))
            c.subchunk_start = sr.get("start", cd.get("subchunk_start", 0))
            c.subchunk_count = sr.get("count", cd.get("subchunk_count", 0))
            cb = cd.get("bounds", {})
            c.min = _dict_to_vec3(cb.get("min", cd.get("min", (0, 0, 0))))
            c.max = _dict_to_vec3(cb.get("max", cd.get("max", (0, 0, 0))))
            c.pad = tuple(cd.get("pad", (0, 0, 0, 0)))
            geo.chunks.append(c)

        # subchunks
        sub_list = geo_data.get("subchunks", geo_data.get("材质子区间列表", []))
        geo.subchunks = []
        for sd in sub_list:
            s = LevelGeoSubchunk()
            mat_field = sd.get("material", {})
            s.material_id = mat_field.get("id", mat_field.get("ID", sd.get("material_id", 0))) if isinstance(mat_field, dict) else sd.get("material_id", 0)
            tr = sd.get("triangle_range", {})
            s.triangle_start = tr.get("start", sd.get("triangle_start", 0))
            s.triangle_end = tr.get("end", sd.get("triangle_end", 0))
            s.triangle_count = tr.get("count", sd.get("triangle_count", 0))
            vr = sd.get("vertex_range", {})
            s.vtx_start = vr.get("start", sd.get("vtx_start", 0))
            s.vtx_end = vr.get("end", sd.get("vtx_end", 0))
            s.vtx_count = vr.get("count", sd.get("vtx_count", 0))
            geo.subchunks.append(s)

        m.geo = geo

        # 安全检查: vertex_count 必须等于实际顶点列表长度
        # 不匹配会导致 meshopt 编码器丢顶点或读取越界
        if geo.vertex_count != len(geo.vertices):
            print("警告: vertex_count(%d) != 实际顶点数(%d), 已自动修正" % (
                geo.vertex_count, len(geo.vertices)))
            geo.vertex_count = len(geo.vertices)
    else:
        m.geo = None

    m.toc = None
    return m


# ============================================================
#  信息打印
# ============================================================
def print_info(meshes):
    print("版本: 0x%X" % meshes.version)
    print("LOD0: %s" % ("已烘焙" if meshes.lod_raw else "缺失"))
    print("全局包围盒 max: %s" % (meshes.max_pos,))
    print("全局包围盒 min: %s" % (meshes.min_pos,))
    if meshes.toc:
        print("TOC 段: %s" % ", ".join(meshes.toc.keys()))

    if meshes.desc:
        ts = meshes.desc.get("timeStamp")
        print("DESC 文件名: %s" % meshes.desc.get("fileName", "?"))
        print("DESC 编辑器: %s" % meshes.desc.get("editor", "?"))
        if ts is not None:
            try:
                print("DESC 时间戳: %d (%s)" % (ts, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))))
            except Exception:
                print("DESC 时间戳: %d" % ts)
        ev = meshes.desc.get("editorVersion")
        gv = meshes.desc.get("engineVersion")
        if ev is not None:
            print("编辑器版本: %s" % (list(ev),))
        if gv is not None:
            print("引擎版本: %s" % (list(gv),))

    if meshes.geo:
        geo = meshes.geo
        print("\n[GEO0 几何]")
        print("  顶点数: %d" % geo.vertex_count)
        print("  索引数: %d (三角形 %d)" % (geo.index_count, geo.index_count // 3))
        print("  地形块: %d  云块: %d" % (geo.chunk_count, geo.cloud_chunk_count))
        print("  材质子区间: %d" % geo.subchunk_count)


# ============================================================
#  CLI
# ============================================================
def _detect_mode(input_path, output_path, explicit_mode=None):
    """根据文件扩展名或显式参数判断转换模式。"""
    if explicit_mode:
        return explicit_mode

    in_ext = input_path.rsplit(".", 1)[-1].lower() if "." in input_path else ""
    out_ext = output_path.rsplit(".", 1)[-1].lower() if "." in output_path else ""

    if in_ext == "meshes" and out_ext == "obj":
        return "m2o"
    if in_ext == "obj" and out_ext == "meshes":
        return "o2m"
    if in_ext == "meshes" and out_ext == "json":
        return "m2j"
    if in_ext == "json" and out_ext == "meshes":
        return "j2m"
    if in_ext == "meshes" and not out_ext:
        return "m2o"  # 默认输出到 stdout

    raise ValueError("无法自动判断转换方向 (输入: .%s, 输出: .%s), 请用 --mode 指定" % (in_ext, out_ext))


# ============================================================
#  交互式 OBJ 选择菜单
# ============================================================
def _interactive_obj_selector(start_dir=None):
    """
    交互式目录浏览 + OBJ 文件选择菜单。
    用户通过序号选择多个 .obj 文件。
    返回: 选中的文件路径列表, 或 None 表示取消。
    """
    if start_dir:
        current_dir = os.path.abspath(start_dir)
    else:
        current_dir = os.getcwd()

    selected = []  # 已选中的文件 (绝对路径)
    selected_set = set()

    while True:
        # ---- 收集目录内容 ----
        try:
            entries = sorted(os.listdir(current_dir))
        except OSError as e:
            print("无法读取目录: %s" % e)
            current_dir = os.path.dirname(current_dir) or "/"
            continue

        subdirs = []
        obj_files = []
        for name in entries:
            full = os.path.join(current_dir, name)
            if os.path.isdir(full):
                subdirs.append(name)
            elif name.lower().endswith(".obj"):
                obj_files.append(name)

        # ---- 显示菜单 ----
        print("\n" + "=" * 60)
        print("当前目录: %s" % current_dir)
        if selected:
            print("已选 %d 个文件:" % len(selected))
            for i, s in enumerate(selected):
                mat = _material_from_filename(s)
                print("  [%d] %s -> %s" % (i, os.path.basename(s), mat))
        print("-" * 60)

        idx = 1
        menu_items = []  # (序号, 类型, 路径/动作)

        # 上级目录
        parent = os.path.dirname(current_dir)
        if parent != current_dir:
            print("  %2d.  [上级目录] .." % idx)
            menu_items.append((idx, "up", parent))
            idx += 1

        # 子目录
        for d in subdirs:
            print("  %2d.  [文件夹] %s/" % (idx, d))
            menu_items.append((idx, "dir", os.path.join(current_dir, d)))
            idx += 1

        # OBJ 文件
        for f in obj_files:
            full = os.path.join(current_dir, f)
            mat = _material_from_filename(f)
            mark = " *" if full in selected_set else "  "
            print("  %2d.  [OBJ]%s %s  (%s)" % (idx, mark, f, mat))
            menu_items.append((idx, "obj", full))
            idx += 1

        if not obj_files and not subdirs:
            print("  (空目录)")

        print("-" * 60)
        print("操作: 输入序号选择/取消 OBJ, 'a' 全选当前目录, 'c' 清空, 'd' 完成, 'q' 取消")
        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return None

        if not choice:
            continue

        # 完成
        if choice == "d":
            if not selected:
                print("还未选择任何文件!")
                continue
            return selected

        # 取消
        if choice == "q":
            return None

        # 全选当前目录 OBJ
        if choice == "a":
            for f in obj_files:
                full = os.path.join(current_dir, f)
                if full not in selected_set:
                    selected.append(full)
                    selected_set.add(full)
            print("已全选当前目录的 %d 个 OBJ" % len(obj_files))
            continue

        # 清空
        if choice == "c":
            selected.clear()
            selected_set.clear()
            print("已清空选择")
            continue

        # 数字选择
        try:
            num = int(choice)
        except ValueError:
            print("无效输入: %s" % choice)
            continue

        matched = None
        for mi in menu_items:
            if mi[0] == num:
                matched = mi
                break

        if not matched:
            print("序号超出范围: %d" % num)
            continue

        kind = matched[1]
        path = matched[2]

        if kind == "up" or kind == "dir":
            current_dir = path
        elif kind == "obj":
            if path in selected_set:
                selected.remove(path)
                selected_set.discard(path)
                print("已取消: %s" % os.path.basename(path))
            else:
                selected.append(path)
                selected_set.add(path)
                print("已选择: %s" % os.path.basename(path))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="That Sky Level .meshes 全方位转换器 (纯 Python / Termux)")
    parser.add_argument("-i", "--input", help="输入文件")
    parser.add_argument("-o", "--output", help="输出文件 (省略且非 --info 时打印到 stdout)")
    parser.add_argument("-m", "--merge", action="store_true", help="meshes->obj 时合并为单个对象")
    parser.add_argument("--info", action="store_true", help="仅打印 .meshes 文件信息")
    parser.add_argument("--split-material", action="store_true",
                        help="meshes->obj 时按材质拆分多个 OBJ (顶点取权重最大材质, 面取多数投票)")
    parser.add_argument("--use-subchunk", action="store_true",
                        help="配合 --split-material: 使用 subchunk 材质区间 (游戏原始分配) 而非顶点权重")
    parser.add_argument("--multi-obj", action="store_true",
                        help="多 OBJ -> meshes: 启动交互式菜单选择 OBJ (以文件名确定材质, 交界处权重过渡)")
    parser.add_argument("--dir", metavar="PATH", help="交互式菜单的起始目录 (配合 --multi-obj)")
    parser.add_argument("--mode", choices=["m2o", "o2m", "m2j", "j2m"],
                        help="显式指定转换方向: m2o=meshes->obj, o2m=obj->meshes, "
                             "m2j=meshes->json, j2m=json->meshes")
    args = parser.parse_args(argv)

    # --multi-obj 模式 (交互式多 OBJ -> meshes)
    if args.multi_obj:
        # 交互式选择 OBJ 文件
        print("=" * 60)
        print("多 OBJ -> meshes 交互式选择")
        print("以文件名确定材质, 交界处顶点权重过渡")
        print("=" * 60)

        obj_paths = _interactive_obj_selector(args.dir)
        if not obj_paths:
            print("未选择任何文件, 退出")
            return 0

        # 输出路径
        out_path = args.output
        if not out_path:
            print("\n请输入输出 .meshes 路径 (回车=output.meshes):")
            try:
                out_path = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n已取消")
                return 0
            if not out_path:
                out_path = "output.meshes"
        if not out_path.endswith(".meshes"):
            out_path += ".meshes"

        # 确认
        print("\n已选文件:")
        for p in obj_paths:
            mat = _material_from_filename(p)
            print("  %s -> %s" % (os.path.basename(p), mat))
        print("输出: %s" % out_path)
        try:
            confirm = input("\n确认转换? (回车=继续, q=取消): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 0
        if confirm == "q":
            print("已取消")
            return 0

        try:
            meshes = multi_obj_to_meshes(obj_paths)
            data = meshes.to_file_buffer()
            with open(out_path, "wb") as f:
                f.write(data)
            print("\n已写出: %s" % out_path)
            print_info(meshes)
        except Exception as e:
            print("转换失败: %s" % e, file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return 1
        return 0

    # 非 multi-obj 模式必须提供 -i
    if not args.input:
        parser.error("the following arguments are required: -i/--input")

    # --info 模式
    if args.info:
        try:
            with open(args.input, "rb") as f:
                buffer = f.read()
        except OSError as e:
            print("读取文件失败: %s" % e, file=sys.stderr)
            return 1
        try:
            meshes = LevelMeshes.from_file_buffer(buffer)
        except Exception as e:
            print("解析 .meshes 失败: %s" % e, file=sys.stderr)
            return 1
        print_info(meshes)
        return 0

    # 判断转换方向
    mode = _detect_mode(args.input, args.output or "", args.mode)

    try:
        if mode == "m2o":
            # meshes -> obj
            with open(args.input, "rb") as f:
                buffer = f.read()
            meshes = LevelMeshes.from_file_buffer(buffer)

            if args.split_material:
                # 按材质拆分多个 OBJ
                if not args.output:
                    print("错误: --split-material 需要指定 -o 输出路径", file=sys.stderr)
                    return 1
                print("按材质拆分输出 (use_subchunk=%s)..." % args.use_subchunk)
                results = touch_object_multi(meshes, args.output, args.use_subchunk)
                print("\n共输出 %d 个材质 OBJ:" % len(results))
                for mat_id, mat_name, path, vc, fc in results:
                    print("  %s (ID=%d): %d 顶点, %d 面" % (mat_name, mat_id, vc, fc))
                print_info(meshes)
            else:
                obj = touch_object(meshes, args.merge)
                if args.output:
                    with open(args.output, "w", encoding="utf-8") as f:
                        f.write(obj)
                    print("已写出: %s" % args.output)
                    print_info(meshes)
                else:
                    sys.stdout.write(obj)

        elif mode == "o2m":
            # obj -> meshes
            with open(args.input, "r", encoding="utf-8") as f:
                obj_text = f.read()
            meshes = obj_to_meshes(obj_text)
            data = meshes.to_file_buffer()
            with open(args.output, "wb") as f:
                f.write(data)
            print("已写出: %s" % args.output)
            print_info(meshes)

        elif mode == "m2j":
            # meshes -> json
            with open(args.input, "rb") as f:
                buffer = f.read()
            meshes = LevelMeshes.from_file_buffer(buffer)
            j = meshes_to_json(meshes)
            text = json.dumps(j, indent=2, ensure_ascii=False)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(text)
                print("已写出: %s" % args.output)
            else:
                sys.stdout.write(text)

        elif mode == "j2m":
            # json -> meshes
            with open(args.input, "r", encoding="utf-8") as f:
                text = f.read()
            j = json.loads(text)
            meshes = json_to_meshes(j)
            data = meshes.to_file_buffer()
            with open(args.output, "wb") as f:
                f.write(data)
            print("已写出: %s" % args.output)

    except Exception as e:
        print("转换失败: %s" % e, file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
