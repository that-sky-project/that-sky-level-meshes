# Mesh ↔ OBJ / FBX 双向转换工具

支持全部 6 个 mesh 版本 (0x17–0x20) 的解析与打包，以及 FBX 二进制格式 (v7.4/v7.5) 的读写。支持单文件和批量转换。

## 文件结构

```
mesh_fbx_tool/
├── main.py        # 主程序入口（交互菜单 + 命令行）
├── mesh_codec.py  # Mesh 编解码库（v23-v32 全版本解析/打包）
├── fbx_codec.py   # FBX 二进制编解码核心库（节点树读写）
├── converter.py   # 转换器（5 条转换路径 + 批量转换）
└── README.md      # 本说明文件
```

## 支持的 Mesh 版本

| 头部 hex | 版本 | 压缩 | UV 类型 | 索引位宽 | 骨骼 |
|----------|------|------|---------|----------|------|
| 17/18    | 23/24| 否   | float32 | 32-bit   | 否   |
| 19/1A/1B | 25-27| 否   | float32 | 32-bit   | 否   |
| 1C/1D    | 28/29| LZ4  | float32 | 32-bit   | 否   |
| 1E       | 30   | LZ4  | 半精度  | 16-bit   | 否   |
| 1F       | 31   | LZ4  | 4层半精度| 16-bit  | 是   |
| 20       | 32   | LZ4  | 4层半精度| 16-bit  | 是   |

## 依赖

```bash
pip install lz4
```

（v28+ 版本的 LZ4 压缩/解压需要此库；不装也能用 v23-v27 的未压缩版本）

## 使用方法

### 交互模式
```bash
python main.py
```
按菜单选择操作，菜单分"单文件转换"和"批量转换"两组。

### 命令行 - 单文件模式
```bash
# Mesh → OBJ
python main.py mesh2obj  <mesh文件>

# Mesh → FBX
python main.py mesh2fbx  <mesh文件>

# OBJ → Mesh（模板模式，用原始 mesh 作为模板）
python main.py obj2mesh  <obj文件> <模板mesh文件>

# FBX → Mesh（模板模式，用原始 mesh 作为模板）
python main.py fbx2mesh  <fbx文件> <模板mesh文件>

# FBX → OBJ
python main.py fbx2obj   <fbx文件>
```

### 命令行 - 批量模式
```bash
# 批量 Mesh → OBJ
python main.py batch_mesh2obj  <目录> [--out 输出目录]

# 批量 Mesh → FBX
python main.py batch_mesh2fbx  <目录> [--out 输出目录]

# 批量 OBJ → Mesh（需要模板 mesh）
python main.py batch_obj2mesh  <目录> <模板.mesh> [--out 输出目录]

# 批量 FBX → Mesh（需要模板 mesh）
python main.py batch_fbx2mesh  <目录> <模板.mesh> [--out 输出目录]

# 批量 FBX → OBJ
python main.py batch_fbx2obj   <目录> [--out 输出目录]
```

批量模式说明：
- 输入可以是目录（自动扫描对应扩展名文件）或单个文件
- `--out` 可选，指定输出目录（留空则输出到源文件同目录）
- 转换完成后输出成功/失败统计

## OBJ/FBX → Mesh 模板模式

用原始 mesh 文件作为模板，复用模板的头部和尾部，只替换顶点/UV/索引/权重数据。
转换器会保留模板中不参与转换的数据（法线、weld映射、面积等）。

### 为什么需要"模板 mesh"？
mesh 格式是扁平二进制，偏移量随版本硬编码，且包含大量未知字段。
转换时需要原始 mesh 文件来确定：
- 目标版本号和格式
- 填充未知的头部/尾部字段
- 保留不参与转换的数据

## 骨骼权重
- mesh 格式：每顶点 4 根骨骼，权重为 uint8 (0-255)
- FBX 格式：Cluster 节点，权重为 double
- 转换时自动归一化，FBX→Mesh 时截断为权重最大的 4 根

## 特殊文件
- 文件名含 `anim` 或 `anc`（不含 `ancestor`）：UV 与索引间有额外间隙
- 文件名含 `StripAnim`：v23/24 走固定偏移分支
- 文件名含 `ZipPos`：v1F 走压缩顶点分支（顶点存为 RGBA）
