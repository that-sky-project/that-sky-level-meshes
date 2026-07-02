# meshes2obj_json

0x3c&0x3d
That Sky Level `.meshes` 网格文件的纯 Python 转换器，支持可逆 OBJ 转换、交互式菜单，零依赖，可在 Termux 等移动环境直接运行。

县小威移植 [that-sky-project/that-sky-level](https://github.com/that-sky-project/that-sky-level)，meshopt 顶点编解码器用纯 Python 重写，无需任何原生模块。

## 功能

| 方向 | 说明 |
|------|------|
| `.meshes` → `.obj` (可逆) | 网格转 OBJ，含全部信息，建模软件可读，可直接转回 (字节级无损) |
| `.meshes` → `.obj` (标准) | 网格转 OBJ，仅几何 (touch_object) |
| `.obj` → `.meshes` | OBJ 转网格 (自动检测：可逆OBJ精确还原 / 标准OBJ邻接分块) |
| `.meshes` → 多个 `.obj` | 按材质拆分 (顶点取权重最大材质, 面取多数投票) |
| 多个 `.obj` → `.meshes` | 交互式双阶段选择, 可加入非法OBJ (可逆OBJ, 完整保留数据) |
| `.meshes` → `.json` | 网格转 JSON (全部字段可视化) |
| `.json` → `.meshes` | JSON 转网格 (精确还原) |

## 环境要求

- Python 3.7+
- 仅使用标准库，无需 pip 安装任何依赖

## 快速开始

### 交互式菜单（推荐）

```bash
python3 meshes2obj_json.py
```

无参数启动即进入菜单，按数字选择转换方向：

```
  --- meshes ↔ OBJ ---
  1. .meshes → .obj  (可逆, 含全部信息, 可直接转回)
  2. .meshes → .obj  (标准, 仅几何)
  3. .obj → .meshes  (自动检测可逆/标准)
  4. .meshes → 多个 .obj  (按材质拆分)
  5. 多个 .obj → .meshes  (交互式, 可加入非法OBJ)
  --- meshes ↔ JSON ---
  6. .meshes → .json
  7. .json → .meshes
  --- 其他 ---
  8. 查看文件信息
  0. 退出
```

### 命令行

```bash
# meshes -> obj (可逆, 含全部信息)
python3 meshes2obj_json.py -i level.meshes -o level.obj --full

# meshes -> obj (标准, 仅几何)
python3 meshes2obj_json.py -i level.meshes -o level.obj

# obj -> meshes (自动检测可逆/标准)
python3 meshes2obj_json.py -i level.obj -o level.meshes

# meshes -> json
python3 meshes2obj_json.py -i level.meshes -o level.json

# json -> meshes
python3 meshes2obj_json.py -i level.json -o level.meshes

# 仅查看文件信息
python3 meshes2obj_json.py -i level.meshes --info
```

转换方向根据输入/输出文件扩展名自动判断，也可用 `--mode` 显式指定：

```bash
python3 meshes2obj_json.py -i in.meshes -o out --mode m2of  # m2o / m2of / o2m / m2j / j2m
```

### 按材质拆分 OBJ

将 `.meshes` 中混合的多种材质拆分为独立的 OBJ 文件，每个材质一个：

```bash
# 按顶点权重投票拆分 (默认)
python3 meshes2obj_json.py -i level.meshes -o output --split-material
# 产出: output_Cliff.obj, output_Grass.obj, output_Sand.obj, ...

# 使用 subchunk 材质区间 (游戏原始分配)
python3 meshes2obj_json.py -i level.meshes -o output --split-material --use-subchunk
```

### 多 OBJ 合并为 meshes

将多个不同材质的 OBJ 合并为一个 `.meshes`，文件名确定材质，交界处顶点自动权重过渡。

**菜单方式 (选项 5)** 采用双阶段选择：

1. **阶段 1** — 选择标准 OBJ（仅含几何，材质由文件名确定）
2. **阶段 2** — 可选加入非法 OBJ（可逆 OBJ，含完整 meshes 数据）

非法 OBJ 即通过 `--full` 生成的可逆 OBJ，包含完整的顶点数据（4 材质槽、权重、input2-4）、分块、子区间。合并时这些数据**完整保留**，与标准 OBJ 的几何拼接为同一 meshes 文件。

```bash
# 交互式菜单 (推荐)
python3 meshes2obj_json.py --multi-obj -o merged.meshes

# 指定起始目录
python3 meshes2obj_json.py --multi-obj --dir /path/to/obj_files -o merged.meshes

# 加入非法 OBJ (--include-full 启用第二阶段选择)
python3 meshes2obj_json.py --multi-obj --include-full --dir /path/to/obj_files -o merged.meshes
```

合并策略：

| 类型 | 处理方式 | 保留数据 |
|------|---------|---------|
| 标准 OBJ | 邻接分块算法，材质来自文件名 | 位置、法线、材质（文件名）、权重（交界处自动） |
| 非法 OBJ (可逆) | 精确还原，直接追加 | 全部 36 字节顶点数据、分块、子区间、索引 |

### 元数据继承 (LOD0/DESC/bounds)

当混合合并包含非法 OBJ 时，以下元数据从**第一个可逆 OBJ** 继承，保证游戏渲染正常：

| 元数据 | 说明 | 丢失后果 |
|--------|------|---------|
| **LOD0** | 烘焙的光照/贴图数据，可能几百字节到数 KB | **贴图渲染异常**（材质/几何正常但贴图丢失） |
| **DESC** | NBT 格式元数据（文件名/编辑器/版本/时间戳） | 文件信息丢失 |
| **meshopt_version** | 顶点编码版本 | 顶点解码可能出错 |
| **bounds** | 从所有顶点实际坐标计算包围盒 | 视锥裁剪失效（旧代码用 FLT_MAX 无限大） |

纯标准 OBJ 合并时（无可逆 OBJ），LOD0 使用 17 字节默认值，DESC 新建，bounds 从顶点计算。

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
| LOD0 segment         |   LOD 层级数据 (烘焙光照/贴图, 默认 17 字节, 真实文件可能数百字节~数 KB)
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

## meshes → OBJ 说明

### 可逆 OBJ (--full)

```bash
python3 meshes2obj_json.py -i level.meshes -o level.obj --full
# 或菜单选择 1
```

生成单个 OBJ 文件，同时满足三个条件：

1. **建模软件可读** — 标准 `v`/`vn`/`f` 几何数据，Blender/Maya 等可直接导入
2. **包含全部信息** — 顶点 (位置/法线/4材质槽/权重/input2-4)、分块、子区间、索引、DESC、LOD0 (含烘焙贴图/光照数据) 全部保留
3. **可直接转回** — `obj → meshes` 自动检测元数据，字节级无损还原原始 `.meshes`

原理：OBJ 几何数据正常输出；额外信息以 `# @` 注释形式嵌入文件末尾的 `@MESHES_META` 块。建模软件忽略注释，转换器读取注释还原。

```
# 标准 OBJ 几何 (建模软件读取)
v 1.23 5.67 9.01
vn 0 1 0
o Chunk_0
usemtl Grass
f 1//1 2//2 3//3

# --- meshes 元数据 (转换器读取, 建模软件忽略) ---
# @MESHES_META_BEGIN
# @version 0x3C
# @desc_raw <hex>
# @lod0_raw <hex>
# @vraw 0 <36字节hex>     # 顶点原始字节, 保证字节级无损
# @chunk 0 ...             # 分块结构
# @subchunk 0 ...          # 材质子区间
# @indices 0 1 2 3 ...     # 索引数组
# @MESHES_META_END
```

与 `meshes → json` 信息量等价，但以 OBJ 格式呈现，可直接在建模软件中编辑几何后转回。

### 标准 OBJ (默认)

```bash
python3 meshes2obj_json.py -i level.meshes -o level.obj
python3 meshes2obj_json.py -i level.meshes -o level.obj --merge   # 合并为单个对象
```

所有材质的顶点和面混合输出到一个 OBJ 文件。仅含几何数据，不可逆。

### 按材质拆分 (--split-material)

将混合多种材质的 `.meshes` 拆分为多个独立 OBJ，每个材质一个文件：

```bash
python3 meshes2obj_json.py -i level.meshes -o output --split-material
# 产出: output_Cliff.obj, output_Grass.obj, output_Sand.obj, ...
```

拆分规则：

- **顶点归属**：每个顶点有 4 个材质槽，取权重最大的槽作为该顶点的主材质
- **面归属**：对每个三角形的 3 个顶点做多数投票，票数最多的主材质即为该面的材质；平局时取权重最高的顶点材质
- **输出**：每个 OBJ 只包含该材质的面及其引用的顶点，顶点索引重映射为局部连续编号

使用 `--use-subchunk` 时，面的材质直接读取 subchunk 的 `material_id` 和 `triangle_range`（游戏原始分配），而非顶点权重投票：

```bash
python3 meshes2obj_json.py -i level.meshes -o output --split-material --use-subchunk
```

拆分与多 OBJ 合并互为逆操作，可配合使用形成完整闭环。

## OBJ → meshes 说明

反向转换时**自动检测** OBJ 类型：

- **可逆 OBJ**（含 `@MESHES_META` 块）→ 精确还原，字节级无损，直接恢复原始结构
- **标准 OBJ**（普通 OBJ 文件）→ BFS 邻接分块算法（移植自 `adjacency.js`），从零重建

可逆 OBJ 检测：从文件末尾向前分块搜索 `@MESHES_META_BEGIN` 标记（每次 64KB + 重叠区），确保大文件（顶点多时元数据块可达数百 KB）也能正确识别。

标准 OBJ 反向转换的约束：

- 每个分块最多 252 顶点、756 索引、252 子区间
- 顶点按相邻关系广度优先分配到分块
- 未指定材质时默认 Grass (ID 48)
- input2/3/4 使用默认值：0.99 / 0.5 / 0.04
- LOD0 使用 17 字节默认值（无烘焙数据）
- bounds 设为 FLT_MAX/-FLT_MAX（无限大）

### 单 OBJ 模式

```bash
python3 meshes2obj_json.py -i model.obj -o model.meshes
```

每个顶点只填充一个材质槽，权重 1.0。材质通过 OBJ 中的 `usemtl` 指令确定，未指定时默认 Grass。

### 多 OBJ 模式 (--multi-obj)

输入多个不同材质的 OBJ，合并为一个 `.meshes`。核心机制：

**材质识别** — 从文件名提取材质名，匹配顺序：精确匹配 > 分隔符后缀 > 包含匹配。例如 `output_Cliff.obj` → Cliff (ID 16)，`model_Grass_test.obj` → Grass (ID 48)。

**顶点合并** — 所有 OBJ 的顶点按坐标（6 位精度）去重合并。相同位置的顶点合并为一个，法线从合并后的面几何重新计算，保证跨 OBJ 一致。

**权重过渡** — 交界处顶点（被多个材质的面引用）按各材质面数比例分配权重，填入 4 个材质槽。例如某顶点被 2 个 Cliff 面 + 1 个 Grass 面引用，则 Cliff 权重 0.67、Grass 权重 0.33。

**面材质** — 多材质模式下每个面的材质直接取自所属 OBJ 的文件名（而非顶点投票），subchunk 按面材质划分。

交互式菜单操作：

```
============================================================
当前目录: /path/to/obj_files
已选 2 个文件:
  [0] model_Cliff.obj -> Cliff
  [1] model_Grass.obj -> Grass
------------------------------------------------------------
   1.  [上级目录] ..
   2.  [文件夹] sub/
   3.  [OBJ] * model_Cliff.obj  (Cliff)
   4.  [OBJ]   model_Grass.obj  (Grass)
------------------------------------------------------------
操作: 输入序号选择/取消 OBJ, 'a' 全选当前目录, 'c' 清空, 'd' 完成, 'q' 取消
```

| 输入 | 作用 |
|------|------|
| 数字 | 选择/取消对应项（文件夹=进入，OBJ=选中/取消） |
| `a` | 全选当前目录所有 OBJ |
| `c` | 清空已选 |
| `d` | 完成选择，进入输出确认 |
| `q` | 取消退出 |

选中的 OBJ 后面标记 `*`，可跨目录选择。

## 命令行参数

```
(无参数)             启动交互式菜单
-i, --input          输入文件路径 (--multi-obj 模式下可省略)
-o, --output         输出文件路径 (省略时输出到 stdout)
-m, --merge          meshes->obj 时合并为单个对象
    --full           meshes->obj 时生成可逆 OBJ (含全部信息, 可直接转回)
    --info           仅打印文件信息
    --split-material meshes->obj 时按材质拆分多个 OBJ
    --use-subchunk   配合 --split-material: 使用 subchunk 材质区间而非顶点权重
    --multi-obj      启动交互式菜单选择多个 OBJ, 合并为 meshes
    --include-full   配合 --multi-obj: 额外选择非法OBJ (可逆OBJ) 合并
    --dir PATH       交互式菜单的起始目录 (配合 --multi-obj)
    --mode           显式指定转换方向: m2o / m2of / o2m / m2j / j2m
```

mode 取值说明：

| mode  | 方向 | 说明 |
|-------|------|------|
| m2o   | meshes→obj | 标准, 仅几何 |
| m2of  | meshes→obj | 可逆, 含全部信息 |
| o2m   | obj→meshes | 自动检测可逆/标准 |
| m2j   | meshes→json | 全部字段 |
| j2m   | json→meshes | 精确还原 |

## 许可证

与原项目一致，LGPL 2.1。
