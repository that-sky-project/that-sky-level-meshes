"""
FBX 二进制文件编解码核心库
支持 FBX 7.4 (32位偏移) 和 7.5+ (64位偏移) 二进制格式
基于 Blender Foundation 公开的格式规范
"""

import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Any, Optional, Union, Tuple

# FBX 版本常量
FBX_VERSION_7400 = 7400  # 32位偏移
FBX_VERSION_7500 = 7500  # 64位偏移

FBX_MAGIC = b"Kaydara FBX Binary  \x00"  # 21 bytes

# ============================================================
# 属性数据类
# ============================================================

class FbxProperty:
    """FBX 属性，封装类型码和值"""
    __slots__ = ("type_code", "value")
    def __init__(self, type_code: str, value: Any):
        self.type_code = type_code
        self.value = value

    def __repr__(self):
        v = self.value
        if isinstance(v, (list, tuple)) and len(v) > 8:
            v = f"[{len(v)} items]"
        return f"FbxProp({self.type_code}, {v})"


# 属性构造便捷函数
def p_bool(v: bool) -> FbxProperty: return FbxProperty("C", bool(v))
def p_int16(v: int) -> FbxProperty: return FbxProperty("Y", int(v))
def p_int32(v: int) -> FbxProperty: return FbxProperty("I", int(v))
def p_int64(v: int) -> FbxProperty: return FbxProperty("L", int(v))
def p_float32(v: float) -> FbxProperty: return FbxProperty("F", float(v))
def p_float64(v: float) -> FbxProperty: return FbxProperty("D", float(v))
def p_string(v: Union[str, bytes]) -> FbxProperty:
    if isinstance(v, str): v = v.encode("utf-8")
    return FbxProperty("S", v)
def p_raw(v: bytes) -> FbxProperty: return FbxProperty("R", v)
def p_float64_array(v) -> FbxProperty: return FbxProperty("d", list(v))
def p_int32_array(v) -> FbxProperty: return FbxProperty("i", list(v))
def p_int64_array(v) -> FbxProperty: return FbxProperty("l", list(v))
def p_float32_array(v) -> FbxProperty: return FbxProperty("f", list(v))
def p_bool_array(v) -> FbxProperty: return FbxProperty("b", [1 if x else 0 for x in v])


# ============================================================
# 节点数据类
# ============================================================

@dataclass
class FbxNode:
    """FBX 节点记录"""
    name: str = ""
    properties: List[FbxProperty] = field(default_factory=list)
    children: List["FbxNode"] = field(default_factory=list)

    def add_prop(self, prop: FbxProperty) -> "FbxNode":
        self.properties.append(prop)
        return self

    def add_child(self, child: "FbxNode") -> "FbxNode":
        self.children.append(child)
        return child

    def find(self, name: str) -> Optional["FbxNode"]:
        for c in self.children:
            if c.name == name:
                return c
        return None

    def find_all(self, name: str) -> List["FbxNode"]:
        return [c for c in self.children if c.name == name]

    def prop_value(self, index: int, default=None):
        if index < len(self.properties):
            return self.properties[index].value
        return default


# ============================================================
# 属性二进制编码（写入）
# ============================================================

def _encode_property(prop: FbxProperty) -> bytes:
    tc = prop.type_code
    v = prop.value
    out = bytearray()

    if tc in ("Y",):
        out += b"Y" + struct.pack("<h", v)
    elif tc in ("C",):
        out += b"C" + struct.pack("<B", 1 if v else 0)
    elif tc in ("I",):
        out += b"I" + struct.pack("<i", v)
    elif tc in ("F",):
        out += b"F" + struct.pack("<f", v)
    elif tc in ("D",):
        out += b"D" + struct.pack("<d", v)
    elif tc in ("L",):
        out += b"L" + struct.pack("<q", v)
    elif tc in ("f", "d", "l", "i", "b"):
        out += tc.encode("ascii")
        arr_len = len(v)
        # 尝试压缩
        if tc == "f": raw = struct.pack("<%df" % arr_len, *v); elem_sz = 4
        elif tc == "d": raw = struct.pack("<%dd" % arr_len, *v); elem_sz = 8
        elif tc == "l": raw = struct.pack("<%dq" % arr_len, *v); elem_sz = 8
        elif tc == "i": raw = struct.pack("<%di" % arr_len, *v); elem_sz = 4
        elif tc == "b": raw = struct.pack("<%dB" % arr_len, *[1 if x else 0 for x in v]); elem_sz = 1
        # 只对足够大的数组压缩
        if arr_len * elem_sz > 128:
            comp = zlib.compress(raw)
            if len(comp) < len(raw):
                out += struct.pack("<III", arr_len, 1, len(comp))
                out += comp
            else:
                out += struct.pack("<III", arr_len, 0, len(raw))
                out += raw
        else:
            out += struct.pack("<III", arr_len, 0, len(raw))
            out += raw
    elif tc in ("S", "R"):
        out += tc.encode("ascii")
        if isinstance(v, str): v = v.encode("utf-8")
        out += struct.pack("<I", len(v))
        out += v
    else:
        raise ValueError(f"未知属性类型码: {tc}")

    return bytes(out)


# ============================================================
# 属性二进制解码（读取）
# ============================================================

class _FbxReader:
    __slots__ = ("data", "pos", "size", "use64")
    def __init__(self, data: bytes, use64: bool):
        self.data = data
        self.pos = 0
        self.size = len(data)
        self.use64 = use64

    def read(self, n: int) -> bytes:
        b = self.data[self.pos:self.pos+n]
        self.pos += n
        return b

    def read_u32(self) -> int:
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def read_u64(self) -> int:
        v = struct.unpack_from("<Q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def read_offset(self) -> int:
        return self.read_u64() if self.use64 else self.read_u32()


def _decode_property(r: _FbxReader) -> FbxProperty:
    tc = chr(r.data[r.pos])
    r.pos += 1

    if tc == "Y":
        v = struct.unpack_from("<h", r.data, r.pos)[0]; r.pos += 2
        return FbxProperty("Y", v)
    elif tc == "C":
        v = r.data[r.pos]; r.pos += 1
        return FbxProperty("C", bool(v))
    elif tc == "I":
        v = struct.unpack_from("<i", r.data, r.pos)[0]; r.pos += 4
        return FbxProperty("I", v)
    elif tc == "F":
        v = struct.unpack_from("<f", r.data, r.pos)[0]; r.pos += 4
        return FbxProperty("F", v)
    elif tc == "D":
        v = struct.unpack_from("<d", r.data, r.pos)[0]; r.pos += 8
        return FbxProperty("D", v)
    elif tc == "L":
        v = struct.unpack_from("<q", r.data, r.pos)[0]; r.pos += 8
        return FbxProperty("L", v)
    elif tc in ("f", "d", "l", "i", "b"):
        arr_len = struct.unpack_from("<I", r.data, r.pos)[0]; r.pos += 4
        encoding = struct.unpack_from("<I", r.data, r.pos)[0]; r.pos += 4
        comp_len = struct.unpack_from("<I", r.data, r.pos)[0]; r.pos += 4
        raw = r.data[r.pos:r.pos+comp_len]; r.pos += comp_len
        if encoding == 1:
            raw = zlib.decompress(raw)
        if tc == "f":
            vals = list(struct.unpack("<%df" % arr_len, raw[:arr_len*4]))
        elif tc == "d":
            vals = list(struct.unpack("<%dd" % arr_len, raw[:arr_len*8]))
        elif tc == "l":
            vals = list(struct.unpack("<%dq" % arr_len, raw[:arr_len*8]))
        elif tc == "i":
            vals = list(struct.unpack("<%di" % arr_len, raw[:arr_len*4]))
        elif tc == "b":
            vals = [bool(x) for x in raw[:arr_len]]
        return FbxProperty(tc, vals)
    elif tc in ("S", "R"):
        length = struct.unpack_from("<I", r.data, r.pos)[0]; r.pos += 4
        v = r.data[r.pos:r.pos+length]; r.pos += length
        if tc == "S":
            try:
                v = v.decode("utf-8")
            except:
                pass
        return FbxProperty(tc, v)
    else:
        raise ValueError(f"未知属性类型码: {tc} @ pos {r.pos-1}")


# ============================================================
# 节点树写入
# ============================================================

def _calc_node_size(node: FbxNode, use64: bool) -> int:
    """计算节点的字节大小（含 NULL 记录）"""
    hdr_sz = (3 * 8 + 1) if use64 else (3 * 4 + 1)
    name_bytes = node.name.encode("utf-8")
    size = hdr_sz + len(name_bytes)
    for prop in node.properties:
        size += len(_encode_property(prop))
    if node.children:
        for child in node.children:
            size += _calc_node_size(child, use64)
        size += (3 * 8 + 1) if use64 else (3 * 4 + 1)  # NULL record
    return size


def _write_node(buf: bytearray, node: FbxNode, base_offset: int, use64: bool) -> int:
    """写入一个节点，返回写入的字节数。base_offset 是此节点起始的绝对偏移"""
    hdr_sz = (3 * 8 + 1) if use64 else (3 * 4 + 1)
    name_bytes = node.name.encode("utf-8")

    # 计算总大小
    total_size = _calc_node_size(node, use64)
    end_offset = base_offset + total_size

    # 编码属性
    prop_bytes = bytearray()
    for prop in node.properties:
        prop_bytes += _encode_property(prop)

    pack_fmt = "<QQQ" if use64 else "<III"

    # 写 header
    buf += struct.pack(pack_fmt, end_offset, len(node.properties), len(prop_bytes))
    buf += struct.pack("<B", len(name_bytes))
    buf += name_bytes
    buf += prop_bytes

    # 写子节点
    if node.children:
        child_offset = base_offset + hdr_sz + len(name_bytes) + len(prop_bytes)
        for child in node.children:
            written = _write_node(buf, child, child_offset, use64)
            child_offset += written
        # NULL record
        buf += struct.pack(pack_fmt, 0, 0, 0)
        buf += b"\x00"

    return total_size


def write_fbx(filepath: str, root: FbxNode, version: int = FBX_VERSION_7400):
    """将节点树写入 FBX 二进制文件"""
    use64 = version >= FBX_VERSION_7500

    buf = bytearray()
    # Header (27 bytes)
    buf += FBX_MAGIC
    buf += bytes([0x1A, 0x00])
    buf += struct.pack("<I", version)

    # 顶层节点（空名空属性，包含所有内容）
    # 计算顶层节点起始偏移 = 27
    _write_node(buf, root, 27, use64)

    # Footer (16 bytes of magic + padding)
    buf += b"\x00" * 16  # footer magic area
    # Top-level end code
    code = b'\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0d'
    buf += code

    with open(filepath, "wb") as f:
        f.write(bytes(buf))


# ============================================================
# 节点树读取
# ============================================================

def read_fbx(filepath: str) -> Tuple[FbxNode, int]:
    """读取 FBX 二进制文件，返回 (根节点, 版本号)"""
    with open(filepath, "rb") as f:
        data = f.read()

    if len(data) < 27:
        raise ValueError("文件太小，不是有效的 FBX")

    # 校验魔数
    magic = data[:21]
    if magic != FBX_MAGIC:
        # 尝试 ASCII FBX
        if data[:3] in (b";;?", b";FB", b"fbx"):
            raise ValueError("这是 ASCII 格式 FBX，本工具仅支持二进制格式")
        raise ValueError("FBX 魔数不匹配")

    # 读取版本
    version = struct.unpack_from("<I", data, 23)[0]
    use64 = version >= FBX_VERSION_7500

    r = _FbxReader(data, use64)
    r.pos = 27

    root = _read_node(r, use64)
    return root, version


def _read_node(r: _FbxReader, use64: bool) -> Optional[FbxNode]:
    """递归读取一个节点"""
    if r.pos >= r.size:
        return None

    start = r.pos
    end_offset = r.read_offset()
    num_props = r.read_offset()
    prop_list_len = r.read_offset()
    name_len = r.data[r.pos]; r.pos += 1

    # 检查是否是 NULL record（结束标记）
    if end_offset == 0 and num_props == 0 and prop_list_len == 0 and name_len == 0:
        return None

    name = r.data[r.pos:r.pos+name_len].decode("utf-8", errors="ignore")
    r.pos += name_len

    node = FbxNode(name=name)

    # 读取属性
    prop_end = r.pos + prop_list_len
    for _ in range(num_props):
        if r.pos >= prop_end:
            break
        node.properties.append(_decode_property(r))
    r.pos = prop_end  # 对齐

    # 读取子节点
    if end_offset > r.pos:
        while r.pos < end_offset - ((3*8+1) if use64 else (3*4+1)):
            child = _read_node(r, use64)
            if child is None:
                break
            node.children.append(child)
        # 跳过 NULL record
        null_sz = (3*8+1) if use64 else (3*4+1)
        r.pos = end_offset
    else:
        r.pos = end_offset

    return node


# ============================================================
# 辅助：生成唯一ID
# ============================================================

class FbxIdGenerator:
    def __init__(self, start=1000):
        self._next = start

    def new_id(self) -> int:
        self._next += 1
        return self._next
