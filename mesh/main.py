#!/usr/bin/env python3
"""
Mesh ↔ OBJ / FBX 双向转换工具
支持全部 6 个 mesh 版本 (0x17-0x20) 的解析与打包
支持 FBX 二进制格式读写 (v7.4/v7.5)
OBJ/FBX → Mesh 使用模板模式（用原始 mesh 作为模板）
支持单文件和批量转换

用法:
  交互菜单:   python main.py
  命令行:     python main.py <模式> <输入> [参数] [输出]

单文件模式:
  mesh2obj    <mesh>                              → <mesh>.obj
  mesh2fbx    <mesh>                              → <mesh>.fbx
  obj2mesh    <obj> <template.mesh>               → <template>_from_obj.mesh
  fbx2mesh    <fbx> <template.mesh>               → <template>_from_fbx.mesh
  fbx2obj     <fbx>                               → <fbx>.obj

批量模式:
  batch_mesh2obj  <目录>                          → 目录下所有 .mesh → .obj
  batch_mesh2fbx  <目录>                          → 目录下所有 .mesh → .fbx
  batch_obj2mesh  <目录> <template.mesh>          → 目录下所有 .obj → .mesh
  batch_fbx2mesh  <目录> <template.mesh>          → 目录下所有 .fbx → .mesh
  batch_fbx2obj   <目录>                          → 目录下所有 .fbx → .obj

  批量模式可选 --out <输出目录> 指定输出目录
"""

import sys
import os

# 确保能导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mesh_codec import LZ4_AVAILABLE
from converter import (
    mesh_to_obj, obj_to_mesh,
    mesh_to_fbx, fbx_to_mesh, fbx_to_obj,
    batch_mesh_to_obj, batch_mesh_to_fbx,
    batch_obj_to_mesh, batch_fbx_to_mesh, batch_fbx_to_obj,
)


def print_banner():
    print("=" * 60)
    print("  Mesh ↔ OBJ / FBX 双向转换工具")
    print("  支持版本: 0x17-0x20 (v23-v32)")
    print("  OBJ/FBX→Mesh 使用模板模式")
    print("  支持单文件和批量转换")
    print(f"  LZ4 支持: {'是' if LZ4_AVAILABLE else '否 (需 pip install lz4)'}")
    print("=" * 60)


def interactive_menu():
    """交互式菜单"""
    print_banner()
    while True:
        print("\n--- 操作菜单 ---")
        print("  --- 单文件转换 ---")
        print("  1. Mesh → OBJ")
        print("  2. Mesh → FBX")
        print("  3. OBJ → Mesh (使用模板 mesh)")
        print("  4. FBX → Mesh (使用模板 mesh)")
        print("  5. FBX → OBJ")
        print("  --- 批量转换 ---")
        print("  6. 批量 Mesh → OBJ")
        print("  7. 批量 Mesh → FBX")
        print("  8. 批量 OBJ → Mesh (使用模板 mesh)")
        print("  9. 批量 FBX → Mesh (使用模板 mesh)")
        print(" 10. 批量 FBX → OBJ")
        print("  0. 退出")
        choice = input("\n请选择 [0-10]: ").strip()

        if choice == "0":
            print("再见！")
            break
        # --- 单文件 ---
        elif choice == "1":
            p = input("Mesh 文件路径: ").strip().strip('"')
            if p and os.path.exists(p):
                try:
                    mesh_to_obj(p)
                except Exception as e:
                    print(f"错误: {e}")
            else:
                print("文件不存在")
        elif choice == "2":
            p = input("Mesh 文件路径: ").strip().strip('"')
            if p and os.path.exists(p):
                try:
                    mesh_to_fbx(p)
                except Exception as e:
                    print(f"错误: {e}")
            else:
                print("文件不存在")
        elif choice == "3":
            obj_p = input("OBJ 文件路径: ").strip().strip('"')
            tpl_p = input("模板 Mesh 文件路径: ").strip().strip('"')
            if os.path.exists(obj_p) and os.path.exists(tpl_p):
                try:
                    obj_to_mesh(obj_p, tpl_p)
                except Exception as e:
                    print(f"错误: {e}")
            else:
                print("文件不存在")
        elif choice == "4":
            fbx_p = input("FBX 文件路径: ").strip().strip('"')
            tpl_p = input("模板 Mesh 文件路径: ").strip().strip('"')
            if os.path.exists(fbx_p) and os.path.exists(tpl_p):
                try:
                    fbx_to_mesh(fbx_p, tpl_p)
                except Exception as e:
                    print(f"错误: {e}")
            else:
                print("文件不存在")
        elif choice == "5":
            p = input("FBX 文件路径: ").strip().strip('"')
            if p and os.path.exists(p):
                try:
                    fbx_to_obj(p)
                except Exception as e:
                    print(f"错误: {e}")
            else:
                print("文件不存在")
        # --- 批量 ---
        elif choice == "6":
            d = input("输入目录或文件路径: ").strip().strip('"')
            out_d = input("输出目录 (留空=同目录): ").strip().strip('"')
            if d and os.path.exists(d):
                try:
                    batch_mesh_to_obj(d, out_d if out_d else None)
                except Exception as e:
                    print(f"错误: {e}")
            else:
                print("路径不存在")
        elif choice == "7":
            d = input("输入目录或文件路径: ").strip().strip('"')
            out_d = input("输出目录 (留空=同目录): ").strip().strip('"')
            if d and os.path.exists(d):
                try:
                    batch_mesh_to_fbx(d, out_d if out_d else None)
                except Exception as e:
                    print(f"错误: {e}")
            else:
                print("路径不存在")
        elif choice == "8":
            d = input("OBJ 输入目录或文件路径: ").strip().strip('"')
            tpl_p = input("模板 Mesh 文件路径: ").strip().strip('"')
            out_d = input("输出目录 (留空=同目录): ").strip().strip('"')
            if os.path.exists(d) and os.path.exists(tpl_p):
                try:
                    batch_obj_to_mesh(d, tpl_p, out_d if out_d else None)
                except Exception as e:
                    print(f"错误: {e}")
            else:
                print("路径不存在")
        elif choice == "9":
            d = input("FBX 输入目录或文件路径: ").strip().strip('"')
            tpl_p = input("模板 Mesh 文件路径: ").strip().strip('"')
            out_d = input("输出目录 (留空=同目录): ").strip().strip('"')
            if os.path.exists(d) and os.path.exists(tpl_p):
                try:
                    batch_fbx_to_mesh(d, tpl_p, out_d if out_d else None)
                except Exception as e:
                    print(f"错误: {e}")
            else:
                print("路径不存在")
        elif choice == "10":
            d = input("输入目录或文件路径: ").strip().strip('"')
            out_d = input("输出目录 (留空=同目录): ").strip().strip('"')
            if d and os.path.exists(d):
                try:
                    batch_fbx_to_obj(d, out_d if out_d else None)
                except Exception as e:
                    print(f"错误: {e}")
            else:
                print("路径不存在")
        else:
            print("无效选择")


def _parse_args(rest):
    """解析位置参数和 --out 选项
    返回: (tpl, out_path, out_dir)
    """
    tpl = None; out_path = None; out_dir = None
    i = 0
    while i < len(rest):
        if rest[i] == '--out' and i + 1 < len(rest):
            out_dir = rest[i + 1]
            i += 2
        elif not rest[i].startswith('--'):
            if tpl is None:
                tpl = rest[i]
            else:
                out_path = rest[i]
            i += 1
        else:
            i += 1
    return tpl, out_path, out_dir


def main():
    args = sys.argv[1:]
    if len(args) == 0:
        interactive_menu()
        return

    mode = args[0]

    # --- 单文件模式 ---
    if mode == "mesh2obj" and len(args) >= 2:
        mesh_to_obj(args[1], args[2] if len(args) > 2 else None)
    elif mode == "mesh2fbx" and len(args) >= 2:
        mesh_to_fbx(args[1], args[2] if len(args) > 2 else None)
    elif mode == "obj2mesh":
        if len(args) < 3:
            print("用法: obj2mesh <obj> <模板.mesh> [输出]")
            sys.exit(1)
        tpl, out, _ = _parse_args(args[2:])
        obj_to_mesh(args[1], tpl, out)
    elif mode == "fbx2mesh":
        if len(args) < 3:
            print("用法: fbx2mesh <fbx> <模板.mesh> [输出]")
            sys.exit(1)
        tpl, out, _ = _parse_args(args[2:])
        fbx_to_mesh(args[1], tpl, out)
    elif mode == "fbx2obj" and len(args) >= 2:
        fbx_to_obj(args[1], args[2] if len(args) > 2 else None)

    # --- 批量模式 ---
    elif mode == "batch_mesh2obj" and len(args) >= 2:
        _, _, out_dir = _parse_args(args[2:])
        batch_mesh_to_obj(args[1], out_dir)
    elif mode == "batch_mesh2fbx" and len(args) >= 2:
        _, _, out_dir = _parse_args(args[2:])
        batch_mesh_to_fbx(args[1], out_dir)
    elif mode == "batch_obj2mesh":
        if len(args) < 3:
            print("用法: batch_obj2mesh <目录> <模板.mesh> [--out 输出目录]")
            sys.exit(1)
        tpl, _, out_dir = _parse_args(args[2:])
        batch_obj_to_mesh(args[1], tpl, out_dir)
    elif mode == "batch_fbx2mesh":
        if len(args) < 3:
            print("用法: batch_fbx2mesh <目录> <模板.mesh> [--out 输出目录]")
            sys.exit(1)
        tpl, _, out_dir = _parse_args(args[2:])
        batch_fbx_to_mesh(args[1], tpl, out_dir)
    elif mode == "batch_fbx2obj" and len(args) >= 2:
        _, _, out_dir = _parse_args(args[2:])
        batch_fbx_to_obj(args[1], out_dir)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
