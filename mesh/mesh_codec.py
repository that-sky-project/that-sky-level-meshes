"""
Mesh 编解码库
从原版 fmt_RE_MESH.py 提取核心解析与打包逻辑
统一使用 MeshData 数据结构，供转换器调用
"""

import struct
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

try:
    import lz4.block
    LZ4_AVAILABLE = True
except ImportError:
    LZ4_AVAILABLE = False
    try:
        from ctypes import CDLL
        _lz4 = CDLL('liblz4.so.1')
        LZ4_SO_AVAILABLE = True
    except:
        LZ4_SO_AVAILABLE = False

# ============================================================
# 常量
# ============================================================

VERTEX_STRIDE = 16
UV_STRIDE = 16
INDEX_STRIDE_32 = 4
INDEX_STRIDE_16 = 2

HEADER_VERSION_MAP = {
    b'\x17\x00\x00\x00': 23, b'\x18\x00\x00\x00': 24,
    b'\x19\x00\x00\x00': 25, b'\x1a\x00\x00\x00': 26,
    b'\x1b\x00\x00\x00': 27, b'\x1c\x00\x00\x00': 28,
    b'\x1d\x00\x00\x00': 29, b'\x1e\x00\x00\x00': 30,
    b'\x1f\x00\x00\x00': 31, b'\x20\x00\x00\x00': 32,
}

# 版本分组
V17_18 = (b'\x17\x00\x00\x00', b'\x18\x00\x00\x00')
V19_1B = (b'\x19\x00\x00\x00', b'\x1a\x00\x00\x00', b'\x1b\x00\x00\x00')
V1C_1D = (b'\x1c\x00\x00\x00', b'\x1d\x00\x00\x00')
V1E = (b'\x1e\x00\x00\x00',)
V1F = (b'\x1f\x00\x00\x00',)
V20 = (b'\x20\x00\x00\x00',)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class BoneInfo:
    name: str
    parent: int  # -1 = root
    matrix: list  # 16 floats (4x4)


@dataclass
class MeshData:
    """统一的 mesh 数据结构"""
    verts: list = field(default_factory=list)           # [(x,y,z), ...]
    faces: list = field(default_factory=list)            # [(a,b,c), ...]
    uv_layers: list = field(default_factory=list)        # [[(u,v),...], ...] 可多层
    normals: list = field(default_factory=list)          # [(x,y,z), ...] 可选
    weights: list = field(default_factory=list)          # [(bone_ids[], bone_ws[]), ...]
    bones: list = field(default_factory=list)            # [BoneInfo, ...]
    version: int = 0
    filename: str = ""
    extra_info: dict = field(default_factory=dict)
    # 原始二进制数据（用于打包时保留未知字段）
    raw_payload: Optional[bytes] = None
    raw_header: Optional[bytes] = None
    is_compressed: bool = False
    is_special: bool = False  # anim/anc 特殊文件


# ============================================================
# LZ4 工具
# ============================================================

def lz4_block_decompress(src, uncompressed_size=None):
    if LZ4_AVAILABLE:
        return lz4.block.decompress(src, uncompressed_size=uncompressed_size)
    elif LZ4_SO_AVAILABLE:
        import ctypes
        dest = ctypes.create_string_buffer(uncompressed_size)
        ret = _lz4.LZ4_decompress_safe(src, dest, len(src), uncompressed_size)
        if ret <= 0:
            raise IOError('LZ4解压失败')
        return dest.raw
    else:
        return _lz4_pure_python(src, uncompressed_size)


def lz4_block_compress(src):
    if LZ4_AVAILABLE:
        return lz4.block.compress(src, store_size=False)
    elif LZ4_SO_AVAILABLE:
        import ctypes
        mx = _lz4.LZ4_compressBound(len(src))
        dest = ctypes.create_string_buffer(mx)
        ret = _lz4.LZ4_compress_default(src, dest, len(src), mx)
        if ret <= 0:
            raise IOError("LZ4 compress failed")
        return dest.raw[:ret]
    else:
        raise RuntimeError("没有可用的LZ4压缩方法，请安装lz4库 (pip install lz4)")


def _lz4_pure_python(src, uncompressed_size):
    i = 0; out = bytearray(); src_len = len(src)
    def read_len(base):
        nonlocal i
        ln = base
        if ln == 15:
            while True:
                if i >= src_len: raise ValueError("LZ4: truncated")
                s = src[i]; i += 1; ln += s
                if s != 255: break
        return ln
    while i < src_len:
        token = src[i]; i += 1
        lit_len = read_len(token >> 4)
        if i + lit_len > src_len: raise ValueError("LZ4: literal OOB")
        out += src[i:i+lit_len]; i += lit_len
        if i >= src_len: break
        if i + 2 > src_len: raise ValueError("LZ4: missing offset")
        offset = src[i] | (src[i+1] << 8); i += 2
        if offset == 0: raise ValueError("LZ4: offset=0")
        match_len = read_len(token & 0x0F) + 4
        start = len(out) - offset
        if start < 0: raise ValueError("LZ4: offset beyond buf")
        for _ in range(match_len): out.append(out[start]); start += 1
    if uncompressed_size is not None and len(out) != uncompressed_size:
        raise ValueError(f"LZ4: size mismatch {len(out)} vs {uncompressed_size}")
    return bytes(out)


# ============================================================
# 读取器
# ============================================================

class Reader:
    __slots__ = ("data", "ofs", "size")
    def __init__(self, data):
        self.data = data; self.ofs = 0; self.size = len(data)
    def tell(self): return self.ofs
    def seek(self, pos, whence=0):
        if whence == 0: self.ofs = pos
        elif whence == 1: self.ofs += pos
        elif whence == 2: self.ofs = self.size + pos
        if self.ofs < 0 or self.ofs > self.size:
            raise ValueError(f"Seek out of range: {self.ofs}/{self.size}")
    def read_bytes(self, n):
        if self.ofs + n > self.size:
            raise ValueError(f"Read out of range: {self.ofs}+{n} > {self.size}")
        b = self.data[self.ofs:self.ofs+n]; self.ofs += n; return b
    def read_u8(self): return struct.unpack_from("<B", self.data, self.ofs)[0]  # noqa
    def read_u32(self):
        v = struct.unpack_from("<I", self.data, self.ofs)[0]; self.ofs += 4; return v
    def read_fmt(self, fmt):
        sz = struct.calcsize(fmt)
        out = struct.unpack_from(fmt, self.data, self.ofs); self.ofs += sz; return out


def find_first_01(data, start=0):
    for i in range(start, len(data)):
        if data[i] == 0x01: return i
    return None


# ============================================================
# 解析：统一入口
# ============================================================

def parse_mesh(filepath) -> MeshData:
    """解析 mesh 文件，返回 MeshData"""
    with open(filepath, 'rb') as f:
        data = f.read()
    if len(data) < 4:
        raise ValueError("文件太小")
    h = data[:4]
    fn = os.path.basename(filepath)
    v = HEADER_VERSION_MAP.get(h)
    if v is None:
        raise ValueError(f"未知的文件头: {h.hex()}")

    if h in V17_18:
        return _parse_v17(data, filepath, fn, v)
    elif h in V19_1B:
        return _parse_v1a(data, filepath, fn, v)
    elif h in V1C_1D:
        return _parse_v1c(data, filepath, fn, v)
    elif h in V1E:
        return _parse_v1e(data, filepath, fn, v)
    elif h in V1F:
        return _parse_v1f20(data, filepath, fn, v, is_20=False)
    elif h in V20:
        return _parse_v1f20(data, filepath, fn, v, is_20=True)
    raise ValueError(f"不支持的头: {h.hex()}")


def _extract_verts_float(raw_v):
    vbuf = []
    for i in range(0, len(raw_v), 16):
        c = raw_v[i:i+16]
        if len(c) >= 12:
            try:
                x, y, z = struct.unpack('<fff', c[:12])
                vbuf.append((x, y, z))
            except:
                pass
    return vbuf


def _extract_uv_float(raw_uv, vc):
    ubuf = []
    for i in range(0, len(raw_uv), 16):
        c = raw_uv[i:i+16]
        if len(c) >= 8:
            try:
                u, v = struct.unpack('<ff', c[:8])
                ubuf.append((u, v))
            except:
                ubuf.append((0.0, 0.0))
    while len(ubuf) < vc: ubuf.append((0.0, 0.0))
    if len(ubuf) > vc: ubuf = ubuf[:vc]
    return ubuf


def _extract_indices_u32(raw_i):
    ivs = []
    for i in range(0, len(raw_i), 4):
        if i + 4 <= len(raw_i):
            try: ivs.append(struct.unpack('<I', raw_i[i:i+4])[0])
            except: pass
    return [tuple(ivs[i:i+3]) for i in range(0, len(ivs) - 2, 3)]


def _extract_indices_u16(raw_i):
    ivs = []
    for i in range(0, len(raw_i), 2):
        if i + 2 <= len(raw_i):
            try: ivs.append(struct.unpack('<H', raw_i[i:i+2])[0])
            except: pass
    return [tuple(ivs[i:i+3]) for i in range(0, len(ivs) - 2, 3)]


def _check_special(fn):
    fn_l = fn.lower()
    return ('anim' in fn_l or 'anc' in fn_l) and 'ancestor' not in fn_l


def _parse_v17(data, filepath, fn, version):
    fs = len(data)
    md = MeshData(version=version, filename=fn)
    md.is_compressed = False

    # StripAnim 特殊文件
    if "StripAnim" in fn:
        vip, iip, vs = 0x4061, 0x4065, 0x408D
        vc = struct.unpack('<I', data[vip:vip+4])[0]
        vb = vc * 16
        raw_v = data[vs:vs+vb]
        md.verts = _extract_verts_float(raw_v)
        gap = vb // 4
        ns = vs + vb
        us = ns + gap; ue = us + vb
        raw_uv = data[us:ue]
        md.uv_layers = [_extract_uv_float(raw_uv, len(md.verts))]
        eg = vc * 8
        ic = struct.unpack('<I', data[iip:iip+4])[0]
        idx_s = ue + eg; idx_e = idx_s + ic * 4
        raw_i = data[idx_s:idx_e]
        md.faces = _extract_indices_u32(raw_i)
        md.is_special = True
        md.extra_info = {'vertex_count': vc, 'gap': gap, 'index_count': ic, 'extra_gap': eg}
        return md

    p01 = find_first_01(data)
    vip = p01 + 45
    vc = struct.unpack('<I', data[vip:vip+4])[0]
    vb = vc * 16
    VS = 0x9d
    raw_v = data[VS:VS+vb]
    md.verts = _extract_verts_float(raw_v)
    gap = vb // 4
    ns = VS + vb
    us = ns + gap; ue = us + vb
    raw_uv = data[us:ue]
    md.uv_layers = [_extract_uv_float(raw_uv, len(md.verts))]
    IIP = 0x75
    ic = struct.unpack('<I', data[IIP:IIP+4])[0]
    raw_i = data[ue:ue + ic * 4]
    md.faces = _extract_indices_u32(raw_i)
    md.extra_info = {'vertex_count': vc, 'gap': gap, 'index_count': ic,
                     'vertex_info_pos': vip, 'index_info_pos': IIP}
    return md


def _parse_v1a(data, filepath, fn, version):
    fs = len(data)
    md = MeshData(version=version, filename=fn)
    md.is_compressed = False
    VCO, ICO, VS = 0x66, 0x6A, 0x92
    vc = struct.unpack('<I', data[VCO:VCO+4])[0]
    vb = vc * 16
    ic = struct.unpack('<I', data[ICO:ICO+4])[0]
    raw_v = data[VS:VS+vb]
    md.verts = _extract_verts_float(raw_v)
    gap = vb // 4
    ns = VS + vb
    us = ns + gap; ue = us + vb
    raw_uv = data[us:ue]
    md.uv_layers = [_extract_uv_float(raw_uv, len(md.verts))]
    is_sp = _check_special(fn)
    md.is_special = is_sp
    eg = vc * 8 if is_sp else 0
    idx_s = ue + eg
    raw_i = data[idx_s:idx_s + ic * 4]
    md.faces = _extract_indices_u32(raw_i)
    md.extra_info = {'vertex_count': vc, 'gap': gap, 'index_count': ic,
                     'is_special': is_sp, 'extra_gap': eg}
    return md


def _parse_v1c(data, filepath, fn, version):
    md = MeshData(version=version, filename=fn)
    md.is_compressed = True
    cs = struct.unpack('<I', data[0x4E:0x52])[0]
    us = struct.unpack('<I', data[0x52:0x56])[0]
    dr = lz4_block_decompress(data[0x56:0x56+cs], us)
    md.raw_payload = dr
    ds = len(dr)
    VCO, ICO, VS = 0x34, 0x38, 0x60
    vc = struct.unpack('<I', dr[VCO:VCO+4])[0]
    vb = vc * 16
    ic = struct.unpack('<I', dr[ICO:ICO+4])[0]
    raw_v = dr[VS:VS+vb]
    md.verts = _extract_verts_float(raw_v)
    gap = vb // 4
    ns = VS + vb
    us2 = ns + gap; ue = us2 + vb
    raw_uv = dr[us2:ue]
    md.uv_layers = [_extract_uv_float(raw_uv, len(md.verts))]
    is_sp = _check_special(fn)
    md.is_special = is_sp
    eg = vc * 8 if is_sp else 0
    idx_s = ue + eg
    raw_i = dr[idx_s:idx_s + ic * 4]
    md.faces = _extract_indices_u32(raw_i)
    md.extra_info = {'vertex_count': vc, 'gap': gap, 'index_count': ic,
                     'is_special': is_sp, 'extra_gap': eg,
                     'compressed_size': cs, 'uncompressed_size': us,
                     'decompressed_size': ds}
    return md


def _parse_v1e(data, filepath, fn, version):
    md = MeshData(version=version, filename=fn)
    md.is_compressed = True
    cs = struct.unpack('<I', data[0x4E:0x52])[0]
    us = struct.unpack('<I', data[0x52:0x56])[0]
    dr = lz4_block_decompress(data[0x56:0x56+cs], us)
    md.raw_payload = dr
    ds = len(dr)
    svc = struct.unpack('<I', dr[0x74:0x78])[0]
    tvc = struct.unpack('<I', dr[0x78:0x7C])[0]
    pc = struct.unpack('<I', dr[0x80:0x84])[0]
    vst = 0xB3
    vb = svc * 16
    raw_v = dr[vst:vst+vb]
    md.verts = _extract_verts_float(raw_v)
    avc = len(md.verts)
    is_sp = _check_special(fn)
    md.is_special = is_sp
    if is_sp:
        gap = vb // 4
        ns = vst + vb
        us2 = ns + gap; uvsz = vb
        eg = svc * 8
        idx_s = us2 + uvsz + eg
    else:
        gap = svc * 4 - 4
        ns = vst + vb
        us2 = ns + gap; uvsz = svc * 16
        idx_s = us2 + uvsz + 4; eg = 0
    fc = tvc // 3
    ue = us2 + uvsz
    raw_uv = dr[us2:ue]
    # 半精度 UV
    ubuf = []
    p = 0
    while p + 16 <= len(raw_uv):
        c = raw_uv[p:p+16]
        try:
            u, v = struct.unpack('<ee', c[4:8])
            ubuf.append((float(u), float(v)))
        except:
            ubuf.append((0.0, 0.0))
        p += 16
    while len(ubuf) < avc: ubuf.append((0.0, 0.0))
    if len(ubuf) > avc: ubuf = ubuf[:avc]
    md.uv_layers = [ubuf]
    idxb = fc * 6
    raw_i = dr[idx_s:idx_s + idxb]
    md.faces = _extract_indices_u16(raw_i)
    md.extra_info = {'vertex_count': svc, 'gap': gap, 'index_count': fc * 3,
                     'is_special': is_sp, 'extra_gap': eg,
                     'total_vertex_count': tvc, 'point_count': pc,
                     'decompressed_size': ds}
    return md


@dataclass
class _ContainerInfo:
    payload: bytes
    bones: list
    cds: int  # 压缩数据起始
    csz: int  # 压缩大小
    bf: int   # 骨骼标志
    bar: bytes  # 尾部数据


def _parse_container_1f20(data, is_20):
    r = Reader(data)
    if is_20:
        hdr = r.read_fmt("<18IH")
        extra = r.read_fmt("<4I")
        h = hdr[17:] + extra
        csz = int(h[4]); usz = int(h[5])
    else:
        hdr = r.read_fmt("<18IH")
        extra = r.read_fmt("<3I")
        h = hdr[17:] + extra
        csz = int(h[3]); usz = int(h[4])
    bf = int(h[1])
    cds = r.tell()
    comp = r.read_bytes(csz)
    bds = r.tell()
    bar = data[bds:]
    payload = lz4_block_decompress(comp, usz)
    bones = []
    if bf == 1:
        bi = r.read_fmt("<20I"); b = r.read_u8(); ti = r.read_u32()
        bc = int((bi + (b, ti))[17])
        for x in range(bc):
            nr = r.read_bytes(64)
            nm = nr.split(b"\x00", 1)[0].decode("ascii", errors="ignore") or f"bone_{x}"
            mb = r.read_bytes(64)
            vals = list(struct.unpack("<16f", mb))
            pr = int(r.read_u32()) - 1
            bones.append(BoneInfo(name=nm, parent=pr, matrix=vals))
        bar = data[bds:]
    return _ContainerInfo(payload=payload, bones=bones, cds=cds, csz=csz, bf=bf, bar=bar)


def _parse_v1f20(data, filepath, fn, version, is_20):
    md = MeshData(version=version, filename=fn)
    md.is_compressed = True
    ci = _parse_container_1f20(data, is_20)
    payload = ci.payload
    md.raw_payload = payload
    md.bones = ci.bones
    has_bones = len(ci.bones) > 0

    r = Reader(payload)
    r.seek(116); vnum = r.read_u32()
    r.seek(120); inum = r.read_u32()
    VBS = 179
    r.seek(VBS)
    vbuf = r.read_bytes(vnum * 16)
    normals_raw = r.read_bytes(vnum * 4)
    uvbuf = r.read_bytes(vnum * 16)
    wbuf = r.read_bytes(vnum * 8) if has_bones else b''
    ibuf = r.read_bytes(inum * 2)

    # 索引之后的数据: Weld映射(vnum*4) + 累计面积(trinum*4)
    trinum = inum // 3
    weld_raw = r.read_bytes(vnum * 4)
    area_raw = r.read_bytes(trinum * 4)
    ptrail = payload[r.tell():]  # payload 尾部剩余

    # 顶点
    md.verts = []
    for i in range(vnum):
        x, y, z = struct.unpack_from("<3f", vbuf, i * 16)
        md.verts.append((x, y, z))

    # 法线: int8×3 + padding
    md.normals = []
    for i in range(vnum):
        raw = struct.unpack_from("<I", normals_raw, i * 4)[0]
        b0 = raw & 0xFF; b1 = (raw>>8)&0xFF; b2 = (raw>>16)&0xFF
        x = (b0 - 256 if b0 >= 128 else b0) / 127.0
        y = (b1 - 256 if b1 >= 128 else b1) / 127.0
        z = (b2 - 256 if b2 >= 128 else b2) / 127.0
        md.normals.append((x, y, z))

    # UV (4层半精度)
    md.uv_layers = [[], [], [], []]
    for i in range(vnum):
        uvs = struct.unpack_from("<8e", uvbuf, i * 16)
        for l in range(4):
            md.uv_layers[l].append((uvs[l*2], uvs[l*2+1]))

    # 索引
    idx = struct.unpack("<" + "H" * inum, ibuf)
    md.faces = [(idx[t*3], idx[t*3+1], idx[t*3+2]) for t in range(inum // 3)]

    # 权重
    if wbuf:
        bm = list(range(-1, len(ci.bones))); bm[0] = 0
        md.weights = []
        for i in range(vnum):
            base = i * 8
            idxs = list(wbuf[base:base+4])
            ws = list(wbuf[base+4:base+8])
            bids = []; bws = []
            for j in range(4):
                fw = ws[j] / 255.0
                if fw <= 0 or idxs[j] >= len(bm):
                    continue
                bi = bm[idxs[j]]
                if bi < 0 or bi >= len(ci.bones):
                    continue
                bids.append(bi); bws.append(fw)
            s = sum(bws)
            if s > 0:
                bws = [w / s for w in bws]
            md.weights.append((bids, bws))

    md.extra_info = {
        'vertex_count': vnum, 'index_count': inum,
        'gap': vnum * 4, 'has_bones': has_bones,
        'cds': ci.cds, 'csz': ci.csz, 'bf': ci.bf,
        'decompressed_size': len(payload)
    }
    md.extra_info['container_bar'] = ci.bar
    return md


# ============================================================
# 打包：统一入口
# ============================================================

# ============================================================
# 模板打包（用原始 mesh 作为模板，替换顶点/UV/索引数据）
# ============================================================

def pack_mesh(template_path, md: MeshData, output_path):
    """用模板文件打包 MeshData 为 mesh 文件"""
    with open(template_path, 'rb') as f:
        orig = f.read()
    h = orig[:4]
    fn = os.path.basename(template_path)
    if h in V17_18:
        result = _repack_v17(orig, md, fn)
    elif h in V19_1B:
        result = _repack_v1a(orig, md, fn)
    elif h in V1C_1D:
        result = _repack_v1c(orig, md, fn)
    elif h in V1E:
        result = _repack_v1e(orig, md, fn)
    elif h in V1F:
        result = _repack_v1f20(orig, md, fn, is_20=False)
    elif h in V20:
        result = _repack_v1f20(orig, md, fn, is_20=True)
    else:
        raise ValueError(f"不支持的头: {h.hex()}")

    with open(output_path, 'wb') as f:
        f.write(result)
    return output_path


# --- 构建辅助 ---

def _build_vertex_bytes(verts):
    """构建顶点缓冲: 3×float + 4字节padding(顶点色/切线)"""
    b = bytearray()
    # 默认padding: ff007f00 (97.9%的顶点用这个值)
    default_pad = b'\xff\x00\x7f\x00'
    for x, y, z in verts:
        b += struct.pack('<fff', x, y, z) + default_pad
    return bytes(b)


def _build_uv_float(uvs):
    b = bytearray()
    for u, v in uvs:
        b += struct.pack('<ff', u, v) + b'\x00' * 8
    return bytes(b)


def _build_uv_half_1e(uvs):
    """构建v30 UV: 16字节/顶点, 布局: 4B padding + Layer0(2×half) + Layer1(2×half) + Layer2(2×half)
    OBJ只有1层UV(diffuse)，Layer1-2设为零(lightmap UV不可从OBJ重建)。
    """
    b = bytearray()
    for u, v in uvs:
        b += b'\x00' * 4              # padding
        b += struct.pack('<ee', u, v)  # Layer0 (diffuse UV)
        b += b'\x00' * 4              # Layer1 = 0 (lightmap UV不可用)
        b += b'\x00' * 4              # Layer2 = 0 (second UV不可用)
    return bytes(b)


def _build_uv_half_1f20(uvs):
    """构建v31/32 UV: 4层×2半精度 = 16字节/顶点
    OBJ只有1层UV(diffuse)，Layer1-3设为零(lightmap UV不可从OBJ重建)。
    原始Layer1==Layer3为lightmap UV，Layer2为第二UV，均无法从OBJ推导。
    """
    b = bytearray()
    for u, v in uvs:
        # Layer0: 实际UV (diffuse)
        b += struct.pack('<ee', u, v)
        # Layer1-3: 设为零 (lightmap UV不可用)
        b += b'\x00' * 12  # 3层 × 4字节 = 12字节
    return bytes(b)


def _build_idx32(faces):
    b = bytearray()
    for tri in faces:
        for i in tri:
            b += struct.pack('<I', i)
    return bytes(b)


def _build_idx16(faces):
    b = bytearray()
    for tri in faces:
        for i in tri:
            b += struct.pack('<H', i)
    return bytes(b)


def _adj_size(orig, new_sz):
    if new_sz <= 0: return b''
    ol = len(orig)
    if ol == 0: return b'\x00' * new_sz
    if ol == new_sz: return orig
    if ol > new_sz: return orig[:new_sz]
    r = bytearray()
    while len(r) < new_sz:
        r += orig[:new_sz - len(r)]
    return bytes(r[:new_sz])


# --- 各版本模板打包 ---

def _repack_v17(orig, md, fn):
    fs = len(orig)
    verts = md.verts; uvs = md.uv_layers[0] if md.uv_layers else [(0.0,0.0)]*len(verts)
    faces = md.faces
    sa = "StripAnim" in fn
    if sa:
        vip, iip, vs = 0x4061, 0x4065, 0x408D
        ovc = struct.unpack('<I', orig[vip:vip+4])[0]; ovb = ovc*16; og = ovb//4
        oic = struct.unpack('<I', orig[iip:iip+4])[0]
        ns = vs+ovb; ogd = orig[ns:ns+og]; ue = ns+og+ovb
        oeg = ovc*8; oegd = orig[ue:ue+oeg]; ois = ue+oeg; oie = ois+oic*4
        trail = orig[oie:] if oie < fs else b''
        nvc = len(verts); nic = len(faces)*3; nvb = nvc*16; ng = nvb//4
        hdr = bytearray(orig[:vs])
        struct.pack_into('<I', hdr, vip, nvc); struct.pack_into('<I', hdr, iip, nic)
        return bytes(hdr) + _build_vertex_bytes(verts) + _adj_size(ogd, ng) + \
               _build_uv_float(uvs) + _adj_size(oegd, nvc*8) + _build_idx32(faces) + trail
    p01 = find_first_01(orig)
    if p01 is None: return None
    vip = p01+45; IIP = 0x75; VS = 0x9D
    ovc = struct.unpack('<I', orig[vip:vip+4])[0]; ovb = ovc*16; og = ovb//4
    oic = struct.unpack('<I', orig[IIP:IIP+4])[0]
    ns = VS+ovb; ogd = orig[ns:ns+og]; ue = ns+og+ovb; oie = ue+oic*4
    trail = orig[oie:] if oie < fs else b''
    nvc = len(verts); nic = len(faces)*3; ng = (nvc*16)//4
    hdr = bytearray(orig[:VS])
    struct.pack_into('<I', hdr, vip, nvc); struct.pack_into('<I', hdr, IIP, nic)
    return bytes(hdr) + _build_vertex_bytes(verts) + _adj_size(ogd, ng) + \
           _build_uv_float(uvs) + _build_idx32(faces) + trail


def _repack_v1a(orig, md, fn):
    fs = len(orig)
    verts = md.verts; uvs = md.uv_layers[0] if md.uv_layers else [(0.0,0.0)]*len(verts)
    faces = md.faces
    VCO, ICO, VS = 0x66, 0x6A, 0x92
    ovc = struct.unpack('<I', orig[VCO:VCO+4])[0]; ovb = ovc*16; og = ovb//4
    oic = struct.unpack('<I', orig[ICO:ICO+4])[0]
    ns = VS+ovb; ogd = orig[ns:ns+og]; ue = ns+og+ovb
    sp = _check_special(fn)
    if sp: oeg = ovc*8; oegd = orig[ue:ue+oeg]; ois = ue+oeg
    else: oegd = b''; ois = ue
    oie = ois+oic*4; trail = orig[oie:] if oie < fs else b''
    nvc = len(verts); nic = len(faces)*3; ng = (nvc*16)//4
    hdr = bytearray(orig[:VS])
    struct.pack_into('<I', hdr, VCO, nvc); struct.pack_into('<I', hdr, ICO, nic)
    egd = _adj_size(oegd, nvc*8) if sp else b''
    return bytes(hdr) + _build_vertex_bytes(verts) + _adj_size(ogd, ng) + \
           _build_uv_float(uvs) + egd + _build_idx32(faces) + trail


def _repack_v1c(orig, md, fn):
    verts = md.verts; uvs = md.uv_layers[0] if md.uv_layers else [(0.0,0.0)]*len(verts)
    faces = md.faces
    ocs = struct.unpack('<I', orig[0x4E:0x52])[0]
    ous = struct.unpack('<I', orig[0x52:0x56])[0]
    dr = lz4_block_decompress(orig[0x56:0x56+ocs], ous)
    VCO, ICO, VS = 0x34, 0x38, 0x60
    ovc = struct.unpack('<I', dr[VCO:VCO+4])[0]; ovb = ovc*16; og = ovb//4
    oic = struct.unpack('<I', dr[ICO:ICO+4])[0]
    ns = VS+ovb; ogd = dr[ns:ns+og]; ue = ns+og+ovb
    sp = _check_special(fn)
    if sp: oeg = ovc*8; oegd = dr[ue:ue+oeg]; ois = ue+oeg
    else: oegd = b''; ois = ue
    oie = ois+oic*4; trail = dr[oie:]
    nvc = len(verts); nic = len(faces)*3; ng = (nvc*16)//4
    ih = bytearray(dr[:VS])
    struct.pack_into('<I', ih, VCO, nvc); struct.pack_into('<I', ih, ICO, nic)
    egd = _adj_size(oegd, nvc*8) if sp else b''
    np = bytes(ih) + _build_vertex_bytes(verts) + _adj_size(ogd, ng) + \
         _build_uv_float(uvs) + egd + _build_idx32(faces) + trail
    nc = lz4_block_compress(np)
    oh = bytearray(orig[:0x56])
    struct.pack_into('<I', oh, 0x4E, len(nc)); struct.pack_into('<I', oh, 0x52, len(np))
    return bytes(oh) + nc + orig[0x56+ocs:]


def _repack_v1e(orig, md, fn):
    verts = md.verts; uvs = md.uv_layers[0] if md.uv_layers else [(0.0,0.0)]*len(verts)
    faces = md.faces
    ocs = struct.unpack('<I', orig[0x4E:0x52])[0]
    ous = struct.unpack('<I', orig[0x52:0x56])[0]
    dr = lz4_block_decompress(orig[0x56:0x56+ocs], ous)
    osvc = struct.unpack('<I', dr[0x74:0x78])[0]
    otvc = struct.unpack('<I', dr[0x78:0x7C])[0]
    vst = 0xB3; ovb = osvc*16
    sp = _check_special(fn)
    if sp:
        og = ovb//4; ogd = dr[vst+ovb:vst+ovb+og]; ue = vst+ovb+og+ovb
        oeg = osvc*8; oegd = dr[ue:ue+oeg]; ois = ue+oeg
    else:
        og = osvc*4-4; ogd = dr[vst+ovb:vst+ovb+og]; ue = vst+ovb+og+osvc*16
        ois = ue+4; oegd = b''
    ofc = otvc//3; oie = ois+ofc*6; trail = dr[oie:]
    nvc = len(verts); nic = len(faces)*3
    ih = bytearray(dr[:vst])
    struct.pack_into('<I', ih, 0x74, nvc); struct.pack_into('<I', ih, 0x78, nic)
    if sp:
        ng = (nvc*16)//4
        body = _build_vertex_bytes(verts) + _adj_size(ogd, ng) + _build_uv_half_1e(uvs) + \
               _adj_size(oegd, nvc*8) + _build_idx16(faces) + trail
    else:
        ng = max(0, nvc*4-4)
        body = _build_vertex_bytes(verts) + _adj_size(ogd, ng) + _build_uv_half_1e(uvs) + \
               b'\x00'*4 + _build_idx16(faces) + trail
    np = bytes(ih) + body
    nc = lz4_block_compress(np)
    oh = bytearray(orig[:0x56])
    struct.pack_into('<I', oh, 0x4E, len(nc)); struct.pack_into('<I', oh, 0x52, len(np))
    return bytes(oh) + nc + orig[0x56+ocs:]


def _repack_payload_1f20(payload, old_vn, old_in, new_vn, new_in, has_bones,
                         new_vdata, new_ndata, new_uvdata, new_wdata, new_idata):
    VBS = 179
    hdr = bytearray(payload[:VBS])
    struct.pack_into('<I', hdr, 116, new_vn)
    struct.pack_into('<I', hdr, 120, new_in)
    old_body = old_vn*16 + old_vn*4 + old_vn*16
    if has_bones: old_body += old_vn*8
    old_body += old_in*2
    trailing_start = VBS + old_body
    trailing = payload[trailing_start:] if trailing_start < len(payload) else b''
    new_payload = bytes(hdr) + new_vdata + new_ndata + new_uvdata + new_wdata + new_idata + trailing
    return new_payload


def _repack_v1f20(orig, md, fn, is_20):
    verts = md.verts
    uvs = md.uv_layers[0] if md.uv_layers else [(0.0, 0.0)] * len(verts)
    faces = md.faces

    r = Reader(orig)
    if is_20:
        hdr = r.read_fmt("<18IH"); extra = r.read_fmt("<4I")
        h = hdr[17:] + extra
        ocsz = int(h[4]); ousz = int(h[5])
    else:
        hdr = r.read_fmt("<18IH"); extra = r.read_fmt("<3I")
        h = hdr[17:] + extra
        ocsz = int(h[3]); ousz = int(h[4])
    bf = int(h[1])
    cds = r.tell()
    oc = r.read_bytes(ocsz)
    bds = r.tell()
    bar = orig[bds:]
    payload = lz4_block_decompress(oc, ousz)
    has_bones = (bf == 1)

    pr = Reader(payload)
    pr.seek(116); ovn = pr.read_u32()
    pr.seek(120); oin = pr.read_u32()
    VBS = 179; pr.seek(VBS)
    # 跳过原始 buffers
    old_normals_start = VBS + ovn * 16
    old_uv_start = old_normals_start + ovn * 4
    old_w_start = old_uv_start + ovn * 16
    old_i_start = old_w_start + (ovn * 8 if has_bones else 0)
    # 提取原始法线数据用于保留
    orig_normals = payload[old_normals_start:old_uv_start]
    orig_weights = payload[old_w_start:old_i_start] if has_bones else b''

    nvn = len(verts); nin = len(faces) * 3
    nv = _build_vertex_bytes(verts)
    nn = _adj_size(orig_normals, nvn * 4)
    nu = _build_uv_half_1f20(uvs)
    nw = _adj_size(orig_weights, nvn * 8) if has_bones else b''
    ni = _build_idx16(faces)

    np = _repack_payload_1f20(payload, ovn, oin, nvn, nin, has_bones,
                               nv, nn, nu, nw, ni)
    nc = lz4_block_compress(np)
    oh = bytearray(orig[:cds])
    struct.pack_into('<I', oh, cds - 8, len(nc))
    struct.pack_into('<I', oh, cds - 4, len(np))
    return bytes(oh) + nc + bar


# ============================================================
# OBJ 解析与输出（复用原版逻辑）
# ============================================================

def parse_obj_file(obj_path):
    verts = []; uvs = []; fv = []; ft = []
    with open(obj_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line[0] == '#': continue
            p = line.split()
            if not p: continue
            if p[0] == 'v' and len(p) >= 4:
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif p[0] == 'vt' and len(p) >= 3:
                uvs.append((float(p[1]), float(p[2])))
            elif p[0] == 'f':
                fvi = []; fti = []
                for s in p[1:]:
                    ids = s.split('/')
                    fvi.append(int(ids[0]) - 1)
                    fti.append(int(ids[1]) - 1 if len(ids) >= 2 and ids[1] != '' else -1)
                for i in range(1, len(fvi) - 1):
                    fv.append((fvi[0], fvi[i], fvi[i + 1]))
                    ft.append((fti[0], fti[i], fti[i + 1]))
    if not fv:
        return verts, uvs, []
    need_exp = False
    has_uv = uvs and any(t != -1 for tri in ft for t in tri)
    if has_uv:
        for a, b in zip(fv, ft):
            for vi, ti in zip(a, b):
                if vi != ti and ti != -1:
                    need_exp = True; break
            if need_exp: break
    if not need_exp:
        uo = uvs[:len(verts)] if has_uv else [(0.0, 0.0)] * len(verts)
        while len(uo) < len(verts): uo.append((0.0, 0.0))
        return verts, uo, fv
    nv = []; nu = []; nf = []; vm = {}
    for a, b in zip(fv, ft):
        tri = []
        for vi, ti in zip(a, b):
            k = (vi, ti)
            if k not in vm:
                vm[k] = len(nv); nv.append(verts[vi])
                nu.append(uvs[ti] if 0 <= ti < len(uvs) else (0.0, 0.0))
            tri.append(vm[k])
        nf.append(tuple(tri))
    return nv, nu, nf


def write_obj(filepath, verts, uvs, faces, bones=None, name="mesh", mesh_version=0):
    with open(filepath, 'w') as f:
        f.write(f"# {name}\n")
        if mesh_version:
            f.write(f"# mesh_version: {mesh_version}\n")
        if bones:
            f.write(f"# Bones: {len(bones)}\n")
            for i, b in enumerate(bones):
                f.write(f"# bone {i}: {b.name} parent={b.parent}\n")
        for v in verts:
            f.write(f"v {v[0]:.8f} {v[1]:.8f} {v[2]:.8f}\n")
        if uvs:
            for uv in uvs:
                f.write(f"vt {uv[0]:.8f} {uv[1]:.8f}\n")
        for tri in faces:
            f.write(f"f {tri[0]+1}/{tri[0]+1} {tri[1]+1}/{tri[1]+1} {tri[2]+1}/{tri[2]+1}\n")


def read_obj_version(obj_path):
    """从 OBJ 注释中读取 mesh 版本号，找不到返回 0"""
    with open(obj_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line.startswith('#'):
                break
            if 'mesh_version:' in line:
                try:
                    return int(line.split('mesh_version:')[1].strip())
                except:
                    pass
    return 0


# --- 辅助计算函数 ---

def _calc_normals_bytes(verts, faces):
    """从面法线计算顶点法线，返回 int8×3+pad 的 bytes"""
    import math
    vnum = len(verts)
    v_norm = [[0.0, 0.0, 0.0] for _ in range(vnum)]
    for a, b, c in faces:
        va, vb, vc = verts[a], verts[b], verts[c]
        e1 = (vb[0]-va[0], vb[1]-va[1], vb[2]-va[2])
        e2 = (vc[0]-va[0], vc[1]-va[1], vc[2]-va[2])
        nx = e1[1]*e2[2]-e1[2]*e2[1]
        ny = e1[2]*e2[0]-e1[0]*e2[2]
        nz = e1[0]*e2[1]-e1[1]*e2[0]
        l = math.sqrt(nx*nx+ny*ny+nz*nz)
        if l > 0:
            nx, ny, nz = nx/l, ny/l, nz/l
        for vi in (a, b, c):
            v_norm[vi][0] += nx
            v_norm[vi][1] += ny
            v_norm[vi][2] += nz
    buf = bytearray()
    for i in range(vnum):
        x, y, z = v_norm[i]
        l = math.sqrt(x*x+y*y+z*z)
        if l > 0:
            x, y, z = x/l, y/l, z/l
        ix = max(-127, min(127, round(x*127)))
        iy = max(-127, min(127, round(y*127)))
        iz = max(-127, min(127, round(z*127)))
        # 转为 uint8
        ux = ix + 256 if ix < 0 else ix
        uy = iy + 256 if iy < 0 else iy
        uz = iz + 256 if iz < 0 else iz
        buf += struct.pack('<BBBB', ux, uy, uz, 0)
    return bytes(buf)


def _calc_weld_bytes(verts, uvs):
    """计算 Weld 映射，返回 vnum×4 bytes
    从零构建时使用顺序索引(0,1,2,...,vn-1)，不做去重。
    原始mesh使用位置+全部4层UV的空间哈希去重(产生gap)，
    但从OBJ无法获得lightmap UV，去重会导致weld_count过小，
    引擎用4层UV处理时缓冲区溢出崩溃。
    顺序索引确保weld_count=vn，缓冲区足够大。
    """
    vnum = len(verts)
    buf = bytearray()
    for i in range(vnum):
        buf += struct.pack('<HH', i, i)
    return bytes(buf)


def _calc_area_bytes(faces, verts):
    """计算三角形累计面积，返回 trinum×4 bytes"""
    import math
    buf = bytearray()
    cum = 0.0
    for a, b, c in faces:
        va, vb, vc = verts[a], verts[b], verts[c]
        e1 = (vb[0]-va[0], vb[1]-va[1], vb[2]-va[2])
        e2 = (vc[0]-va[0], vc[1]-va[1], vc[2]-va[2])
        cx = e1[1]*e2[2]-e1[2]*e2[1]
        cy = e1[2]*e2[0]-e1[0]*e2[2]
        cz = e1[0]*e2[1]-e1[1]*e2[0]
        area = 0.5 * math.sqrt(cx*cx+cy*cy+cz*cz)
        cum += area
        buf += struct.pack('<f', cum)
    return bytes(buf)
