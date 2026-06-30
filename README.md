# meshes2obj

That Sky Level `.meshes` 网格文件的纯 Python 转换器，支持四种转换方向，零依赖，可在 Termux 等移动环境直接运行。

移植自 [that-sky-project/that-sky-level](https://github.com/that-sky-project/that-sky-level)，meshopt 顶点编解码器用纯 Python 重写，无需任何原生模块。

## 功能

| 方向 | 说明 |
|------|------|
| `.meshes` → `.obj` | 网格转 OBJ (touch_object) |
| `.obj` → `.meshes` | OBJ 转网格 (BFS 邻接分块算法) |
| `.meshes` → `.json` | 网格转 JSON (全部字段可视化) |
| `.json` → `.meshes` | JSON 转网格 (精确还原) |

## 环境要求

- Python 3.7+
- 仅使用标准库，无需 pip 安装任何依赖

## 快速开始

```bash
# meshes -> obj
python3 meshes2obj.py -i level.meshes -o level.obj

# obj -> meshes
python3 meshes2obj.py -i level.obj -o level.meshes

# meshes -> json
python3 meshes2obj.py -i level.meshes -o level.json

# json -> meshes
python3 meshes2obj.py -i level.json -o level.meshes

# 仅查看文件信息
python3 meshes2obj.py -i level.meshes --info
```

转换方向根据输入/输出文件扩展名自动判断，也可用 `--mode` 显式指定：

```bash
python3 meshes2obj.py -i in.meshes -o out --mode m2o   # m2o / o2m / m2j / j2m
```

## JSON 格式

导出的 JSON 包含全部几何数据，结构清晰，可直接读取或修改：

```json
{
  "format": "that-sky-level-meshes",
  "version": "0x3C",
  "metadata": {
    "file_name": "level.meshes",
    "editor": "that-sky-level",
    "editor_version": "1.0.0",
    "engine_version": "0.32.2",
    "timestamp": 0,
    "time": "1970-01-01 08:00:00"
  },
  "bounds": {
    "max": { "x": ..., "y": ..., "z": ... },
    "min": { "x": ..., "y": ..., "z": ... }
  },
  "geometry": {
    "summary": {
      "vertex_count": 30923,
      "triangle_count": 48764,
      "index_count": 146292,
      "chunk_count": 165,
      "cloud_chunk_count": 30,
      "subchunk_count": 410,
      "meshopt_version": 0
    },
    "vertices": [
      {
        "index": 0,
        "position": { "x": ..., "y": ..., "z": ... },
        "normal": { "x": ..., "y": ..., "z": ..., "w": ... },
        "materials": [
          { "name": "Grass", "id": 48, "weight": 1.0 },
          { "name": "None",  "id": 0,  "weight": 0.0 },
          { "name": "None",  "id": 0,  "weight": 0.0 },
          { "name": "None",  "id": 0,  "weight": 0.0 }
        ],
        "input2_ao_roughness": { "x": ..., "y": ..., "z": ..., "w": ... },
        "input3_detail":     { "x": ..., "y": ..., "z": ..., "w": ... },
        "input4_misc":       { "x": ..., "y": ..., "z": ..., "w": ... }
      }
    ],
    "triangles": [
      {
        "index": 0,
        "chunk": 0,
        "indices": [0, 1, 2],
        "positions": [
          { "x": ..., "y": ..., "z": ... },
          { "x": ..., "y": ..., "z": ... },
          { "x": ..., "y": ..., "z": ... }
        ]
      }
    ],
    "chunks": [
      {
        "index": 0,
        "type": "terrain",
        "vertex_range":   { "start": 0,   "count": 252 },
        "index_range":    { "start": 0,   "count": 756 },
        "subchunk_range": { "start": 0,   "count": 8 },
        "bounds": {
          "min": { "x": ..., "y": ..., "z": ... },
          "max": { "x": ..., "y": ..., "z": ... }
        }
      }
    ],
    "subchunks": [
      {
        "index": 0,
        "material": { "name": "Grass", "id": 48 },
        "triangle_range": { "start": 0, "end": 11, "count": 12 },
        "vertex_range":   { "start": 0, "end": 23, "count": 24 }
      }
    ],
    "indices": [0, 1, 2, 3, 4, 5, ...]
  },
  "desc_raw": [10, 0, 0, ...],
  "lod0_raw": [27, 0, 1, ...]
}
```

### 字段说明

- **`vertices`** — 顶点列表，每个顶点包含位置、法线、材质权重、三个输入通道
- **`triangles`** — 三角面列表，已展开为顶点索引 + 坐标，方便直接查看
- **`chunks`** — 地形块列表，每个块有自己的顶点/索引范围和 AABB 包围盒（碰撞检测用）
- **`subchunks`** — 材质子区间，按材质划分的面区间，告诉游戏用哪种材质渲染
- **`indices`** — 扁平索引数组，每 3 个为一组对应一个三角面
- **`desc_raw` / `lod0_raw`** — DESC 和 LOD0 段的原始字节，用于精确还原（游戏文件可能含额外字段）

## 材质列表

| 名称 | ID |
|------|-----|
| None | 0 |
| Transparent | 2 |
| Void | 3 |
| Particle | 4 |
| WoodSlippery | 5 |
| VoidMinor | 6 |
| WoodPlank | 7 |
| Cliff | 16 |
| Soil | 17 |
| CliffLight | 18 |
| WallDamaged | 19 |
| Wall | 20 |
| Gold | 21 |
| Glacier | 22 |
| TileCeiling | 23 |
| TileFloor | 24 |
| TileWall | 25 |
| WallBrick | 26 |
| SoilWet | 27 |
| CliffWet | 28 |
| Bone | 29 |
| Wood | 30 |
| Ceramics | 31 |
| Sand | 32 |
| SandWet | 33 |
| SandLight | 34 |
| Snow | 35 |
| SandDeep | 36 |
| Mud | 37 |
| Grass | 48 |
| GrassWet | 49 |
| GrassLight | 50 |
| GrassMoss | 51 |
| Cloth | 52 |
| Cloud | 80 |

## .meshes 文件结构

```
+----------------------+
| Header (136 bytes)   |   魔数 "LVL0" + 版本 + TOC + padding + maxPos + minPos
+----------------------+
| DESC segment         |   NBT 格式的元数据 (文件名/编辑器/版本/时间戳)
+----------------------+
| LOD0 segment         |   LOD 层级数据 (固定 16+ 字节)
+----------------------+
| GEO0 segment         |   几何数据
|  ├ counts (5×u32)    |   索引/顶点/分块/云块/子区间 数量
|  ├ vertices          |   meshopt 压缩的顶点缓冲 (每个顶点 36 字节)
|  ├ indices           |   扁平 u8 索引数组
|  ├ chunks            |   分块数据 (每个 56 字节)
|  └ subchunks         |   材质子区间 (每个 8 字节)
+----------------------+
```

### 顶点格式 (36 字节)

| 偏移 | 大小 | 说明 |
|------|------|------|
| 0 | 12 | 位置 Vec3 (float32×3) |
| 12 | 4 | 法线 R8G8B8A8_SNORM |
| 16 | 4 | 材质 ID (4×u8) |
| 20 | 4 | 材质权重 (4×u8, 除以 255) |
| 24 | 4 | 输入通道 2 (AO/粗糙度, UNORM) |
| 28 | 4 | 输入通道 3 (细节, UNORM) |
| 32 | 4 | 输入通道 4 (其他, UNORM) |

## OBJ → meshes 说明

反向转换时使用 BFS 邻接分块算法（移植自 `adjacency.js`），约束：

- 每个分块最多 252 顶点、756 索引、252 子区间
- 顶点按相邻关系广度优先分配到分块
- 未指定材质时默认 Grass (ID 48)
- input2/3/4 使用默认值：0.99 / 0.5 / 0.04

## 命令行参数

```
-i, --input     输入文件路径（必需）
-o, --output    输出文件路径（省略时输出到 stdout）
-m, --merge     meshes->obj 时合并为单个对象
    --info      仅打印文件信息
    --mode      显式指定转换方向: m2o / o2m / m2j / j2m
```

## 许可证

与原项目一致，LGPL 2.1。
