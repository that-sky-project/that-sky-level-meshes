"""
转换器：Mesh ↔ OBJ, Mesh ↔ FBX
"""

import os
import math
from fbx_codec import (
    FbxNode, FbxIdGenerator, write_fbx, read_fbx,
    FBX_VERSION_7400,
    p_bool, p_int16, p_int32, p_int64, p_float32, p_float64,
    p_string, p_raw, p_float64_array, p_int32_array, p_int64_array,
)
from mesh_codec import (
    MeshData, BoneInfo, parse_mesh, pack_mesh,
    parse_obj_file, write_obj, read_obj_version,
)


# ============================================================
# Mesh → OBJ
# ============================================================

def mesh_to_obj(mesh_path, obj_path=None):
    """Mesh 转 OBJ"""
    md = parse_mesh(mesh_path)
    if obj_path is None:
        base = os.path.splitext(mesh_path)[0]
        obj_path = base + ".obj"

    uvs = md.uv_layers[0] if md.uv_layers else []
    write_obj(obj_path, md.verts, uvs, md.faces, md.bones, md.filename, mesh_version=md.version)

    info = {
        'verts': len(md.verts),
        'uvs': len(uvs),
        'faces': len(md.faces),
        'bones': len(md.bones),
        'version': md.version,
    }
    print(f"  Mesh→OBJ: {info['verts']}顶点, {info['faces']}三角形, "
          f"{info['bones']}骨骼, 版本{info['version']}")
    print(f"  输出: {obj_path}")
    return obj_path, info


# ============================================================
# OBJ → Mesh
# ============================================================

def obj_to_mesh(obj_path, template_mesh_path, output_path=None, version=None):
    """
    OBJ 转 Mesh（模板模式）。
    用 template_mesh_path 作为模板，替换顶点/UV/索引数据。
    """
    verts, uvs, faces = parse_obj_file(obj_path)
    if not verts or not faces:
        raise ValueError("OBJ 数据无效：无顶点或无面")

    if not template_mesh_path:
        raise ValueError("OBJ→Mesh 需要提供模板 mesh 文件")

    if output_path is None:
        base = os.path.splitext(template_mesh_path)[0]
        output_path = base + "_from_obj.mesh"
    md = MeshData(verts=verts, faces=faces, uv_layers=[uvs])
    pack_mesh(template_mesh_path, md, output_path)
    mode = "模板模式"

    info = {
        'verts': len(verts),
        'uvs': len(uvs),
        'faces': len(faces),
        'output': output_path,
        'mode': mode,
    }
    print(f"  OBJ→Mesh [{mode}]: {info['verts']}顶点, {info['faces']}三角形")
    print(f"  输出: {output_path}")
    return output_path, info


# ============================================================
# Mesh → FBX
# ============================================================

def mesh_to_fbx(mesh_path, fbx_path=None, version=FBX_VERSION_7400):
    """Mesh 转 FBX（含骨骼权重）"""
    md = parse_mesh(mesh_path)
    if fbx_path is None:
        base = os.path.splitext(mesh_path)[0]
        fbx_path = base + ".fbx"

    name = os.path.splitext(md.filename)[0]
    ids = FbxIdGenerator(1000)

    # ---- 构建根节点 ----
    root = FbxNode()

    # FBXHeaderExtension
    fhe = FbxNode("FBXHeaderExtension")
    fhe.add_child(FbxNode("FBXHeaderVersion", [p_int32(1003)]))
    fhe.add_child(FbxNode("FBXVersion", [p_int32(version)]))
    fhe.add_child(FbxNode("Creator", [p_string("Mesh-FBX Converter")]))
    fhe.add_child(FbxNode("CreationTimeStamp", [
        p_int32(2024), p_int32(1), p_int32(1), p_int32(0), p_int32(0), p_int32(0), p_int32(0)
    ]))
    root.add_child(fhe)

    # GlobalSettings
    gs = FbxNode("GlobalSettings")
    gs.add_child(FbxNode("Version", [p_int32(1000)]))
    pset = FbxNode("Properties70")
    pset.add_child(FbxNode("P", [p_string("UpAxis"), p_string("int"), p_string("Integer"), p_int32(1)]))
    pset.add_child(FbxNode("P", [p_string("UpAxisSign"), p_string("int"), p_string("Integer"), p_int32(1)]))
    pset.add_child(FbxNode("P", [p_string("FrontAxis"), p_string("int"), p_string("Integer"), p_int32(2)]))
    pset.add_child(FbxNode("P", [p_string("FrontAxisSign"), p_string("int"), p_string("Integer"), p_int32(1)]))
    pset.add_child(FbxNode("P", [p_string("CoordAxis"), p_string("int"), p_string("Integer"), p_int32(0)]))
    pset.add_child(FbxNode("P", [p_string("CoordAxisSign"), p_string("int"), p_string("Integer"), p_int32(1)]))
    pset.add_child(FbxNode("P", [p_string("OriginalUpAxis"), p_string("int"), p_string("Integer"), p_int32(-1)]))
    pset.add_child(FbxNode("P", [p_string("OriginalUpAxisSign"), p_string("int"), p_string("Integer"), p_int32(1)]))
    pset.add_child(FbxNode("P", [p_string("UnitScaleFactor"), p_string("double"), p_string("Number"), p_float64(1.0)]))
    pset.add_child(FbxNode("P", [p_string("OriginalUnitScaleFactor"), p_string("double"), p_string("Number"), p_float64(1.0)]))
    gs.add_child(pset)
    root.add_child(gs)

    # Documents
    doc_id = ids.new_id()
    docs = FbxNode("Documents", [p_int32(1)])
    d = FbxNode("Document", [p_int64(doc_id), p_string("Scene"), p_string("Scene")])
    d.add_child(FbxNode("Properties", []))
    d.add_child(FbxNode("Root", [p_int64(doc_id)]))
    docs.add_child(d)
    root.add_child(docs)

    # Definitions
    defs = FbxNode("Definitions", [p_int32(3 + (1 if md.bones else 0))])
    defs.add_child(FbxNode("Count", []))  # placeholder
    def_global = FbxNode("ObjectType", [p_string("GlobalSettings")])
    def_global.add_child(FbxNode("Count", [p_int32(1)]))
    defs.add_child(def_global)
    def_model = FbxNode("ObjectType", [p_string("Model")])
    def_model.add_child(FbxNode("Count", [p_int32(1 + len(md.bones))]))
    def_model.add_child(FbxNode("PropertyTemplate", [p_string("FbxNode")]))
    defs.add_child(def_model)
    def_geom = FbxNode("ObjectType", [p_string("Geometry")])
    def_geom.add_child(FbxNode("Count", [p_int32(1)]))
    defs.add_child(def_geom)
    if md.bones:
        def_skin = FbxNode("ObjectType", [p_string("Deformer")])
        def_skin.add_child(FbxNode("Count", [p_int32(len(md.bones) + 1)]))
        defs.add_child(def_skin)
    root.add_child(defs)

    # Objects
    objects = FbxNode("Objects")

    # -- Geometry --
    geom_id = ids.new_id()
    geom = FbxNode("Geometry", [p_int64(geom_id), p_string(f"{name}_Geometry"), p_string("Mesh")])

    # Vertices (double array)
    vert_data = []
    for x, y, z in md.verts:
        vert_data.extend([float(x), float(y), float(z)])
    geom.add_child(FbxNode("Vertices", [p_float64_array(vert_data)]))

    # PolygonVertexIndex (FBX 用负值标记多边形结束)
    pvi = []
    for tri in md.faces:
        for j in range(3):
            idx = tri[j]
            if j == 2:
                pvi.append(~idx)  # 按位取反，最后一个为负值
            else:
                pvi.append(idx)
    geom.add_child(FbxNode("PolygonVertexIndex", [p_int32_array(pvi)]))
    geom.add_child(FbxNode("GeometryVersion", [p_int32(124)]))

    # Edges (空)
    geom.add_child(FbxNode("Edges", [p_int32_array([])]))

    # LayerElementUV
    if md.uv_layers and md.uv_layers[0]:
        le_uv = FbxNode("LayerElementUV", [p_int32(0)])
        le_uv.add_child(FbxNode("UVSet", [p_string("map1")]))
        le_uv.add_child(FbxNode("UVIndex", [p_int32_array([i for i in range(len(md.uv_layers[0]))])]))
        uv_data = []
        for u, v in md.uv_layers[0]:
            uv_data.extend([float(u), float(v)])
        le_uv.add_child(FbxNode("UV", [p_float64_array(uv_data)]))
        le_uv.add_child(FbxNode("MappingInformationType", [p_string("ByVertex" if len(uv_data)//2 == len(md.verts) else "ByPolygonVertex")]))
        le_uv.add_child(FbxNode("ReferenceInformationType", [p_string("IndexToDirect")]))
        geom.add_child(le_uv)

    # LayerElementNormal (如果法线可用)
    if md.normals and len(md.normals) == len(md.verts):
        le_n = FbxNode("LayerElementNormal", [p_int32(0)])
        n_data = []
        for nx, ny, nz in md.normals:
            n_data.extend([float(nx), float(ny), float(nz)])
        le_n.add_child(FbxNode("Normals", [p_float64_array(n_data)]))
        le_n.add_child(FbxNode("MappingInformationType", [p_string("ByVertex")]))
        le_n.add_child(FbxNode("ReferenceInformationType", [p_string("Direct")]))
        geom.add_child(le_n)

    # Layer
    layer = FbxNode("Layer", [p_int32(0)])
    layer.add_child(FbxNode("LayerElement", [
        p_string("LayerElementNormal"), p_string("MappedTextureName"),
        p_string("Normals") if md.normals else p_string("")
    ]))
    layer.add_child(FbxNode("LayerElement", [
        p_string("LayerElementUV"), p_string(""), p_string("UVSet")
    ]))
    geom.add_child(layer)

    objects.add_child(geom)

    # -- Model --
    model_id = ids.new_id()
    model = FbxNode("Model", [p_int64(model_id), p_string(name), p_string("Mesh")])
    mprops = FbxNode("Properties70")
    mprops.add_child(FbxNode("P", [p_string("Lcl Translation"), p_string("Lcl Translation"), p_string(""), p_float64(0), p_float64(0), p_float64(0)]))
    mprops.add_child(FbxNode("P", [p_string("Lcl Rotation"), p_string("Lcl Rotation"), p_string(""), p_float64(0), p_float64(0), p_float64(0)]))
    mprops.add_child(FbxNode("P", [p_string("Lcl Scaling"), p_string("Lcl Scaling"), p_string(""), p_float64(1), p_float64(1), p_float64(1)]))
    model.add_child(mprops)
    model.add_child(FbxNode("ModelVersion", [p_string("232")]))
    objects.add_child(model)

    # -- 骨骼与权重 --
    bone_ids = []  # 对应 md.bones 的 FBX object id
    if md.bones:
        # 创建骨骼 Model 节点
        for i, bone in enumerate(md.bones):
            bid = ids.new_id()
            bone_ids.append(bid)
            bnode = FbxNode("Model", [p_int64(bid), p_string(bone.name), p_string("LimbNode")])
            bprops = FbxNode("Properties70")
            # 从矩阵提取平移和旋转
            m = bone.matrix
            tx, ty, tz = m[12], m[13], m[14]
            bprops.add_child(FbxNode("P", [p_string("Lcl Translation"), p_string("Lcl Translation"), p_string(""), p_float64(tx), p_float64(ty), p_float64(tz)]))
            # 提取欧拉角
            rx, ry, rz = _matrix_to_euler(m)
            bprops.add_child(FbxNode("P", [p_string("Lcl Rotation"), p_string("Lcl Rotation"), p_string(""), p_float64(rx), p_float64(ry), p_float64(rz)]))
            bprops.add_child(FbxNode("P", [p_string("Lcl Scaling"), p_string("Lcl Scaling"), p_string(""), p_float64(1), p_float64(1), p_float64(1)]))
            bnode.add_child(bprops)
            bnode.add_child(FbxNode("ModelVersion", [p_string("232")]))
            objects.add_child(bnode)

        # 创建 Skin Deformer
        skin_id = ids.new_id()
        skin = FbxNode("Deformer", [p_int64(skin_id), p_string(f"{name}_Skin"), p_string("Skin")])
        skin.add_child(FbxNode("MultiLayer", [p_int32(0)]))
        skin.add_child(FbxNode("SkinningType", [p_string("Linear")]))
        objects.add_child(skin)

        # 为每根骨骼创建 Cluster
        for i, bone in enumerate(md.bones):
            cluster_id = ids.new_id()
            cluster = FbxNode("Deformer", [p_int64(cluster_id), p_string(f"SubDeformer_{i}"), p_string("Cluster")])

            # 收集受此骨骼影响的顶点
            v_indices = []
            v_weights = []
            for vi, (bids_list, bws_list) in enumerate(md.weights):
                for j, bi in enumerate(bids_list):
                    if bi == i:
                        v_indices.append(vi)
                        v_weights.append(float(bws_list[j]))
                        break

            cluster.add_child(FbxNode("Indexes", [p_int32_array(v_indices)]))
            cluster.add_child(FbxNode("Weights", [p_float64_array(v_weights)]))
            cluster.add_child(FbxNode("Transform", [p_float64_array(bone.matrix)]))
            cluster.add_child(FbxNode("TransformLink", [p_float64_array(bone.matrix)]))
            objects.add_child(cluster)

    root.add_child(objects)

    # -- Connections --
    connections = FbxNode("Connections")
    # Model -> Geometry (OO)
    connections.add_child(FbxNode("C", [p_string("OO"), p_int64(geom_id), p_int64(model_id)]))
    if md.bones:
        # Skin -> Geometry (OO)
        connections.add_child(FbxNode("C", [p_string("OO"), p_int64(skin_id), p_int64(geom_id)]))
        # Cluster -> Skin (OO)
        # 重新遍历 cluster id（需要和创建时一致的 id）
        # 由于 ids 是递增的，cluster id 从 skin_id+1 开始
        for i in range(len(md.bones)):
            cid = skin_id + 1 + i
            connections.add_child(FbxNode("C", [p_string("OO"), p_int64(cid), p_int64(skin_id)]))
            # Cluster -> Bone Model (OO)
            connections.add_child(FbxNode("C", [p_string("OO"), p_int64(cid), p_int64(bone_ids[i])]))
        # 骨骼父子关系
        for i, bone in enumerate(md.bones):
            if bone.parent >= 0 and bone.parent < len(bone_ids):
                connections.add_child(FbxNode("C", [p_string("OO"), p_int64(bone_ids[i]), p_int64(bone_ids[bone.parent])]))
    root.add_child(connections)

    # Takes
    root.add_child(FbxNode("Takes"))

    write_fbx(fbx_path, root, version)

    info = {
        'verts': len(md.verts),
        'faces': len(md.faces),
        'uvs': len(md.uv_layers[0]) if md.uv_layers else 0,
        'bones': len(md.bones),
        'version': md.version,
    }
    print(f"  Mesh→FBX: {info['verts']}顶点, {info['faces']}三角形, "
          f"{info['bones']}骨骼, 版本{info['version']}")
    print(f"  输出: {fbx_path}")
    return fbx_path, info


def _matrix_to_euler(m):
    """从 4x4 矩阵(16 floats, row-major)提取欧拉角(度)"""
    # 提取 3x3 旋转部分
    r00, r01, r02 = m[0], m[1], m[2]
    r10, r11, r12 = m[4], m[5], m[6]
    r20, r21, r22 = m[8], m[9], m[10]

    # 假设 Y 轴向上
    rx = math.degrees(math.atan2(r21, r22))
    ry = math.degrees(math.atan2(-r20, math.sqrt(r21*r21 + r22*r22)))
    rz = math.degrees(math.atan2(r10, r00))
    return rx, ry, rz


# ============================================================
# FBX → Mesh
# ============================================================

def fbx_to_mesh(fbx_path, template_mesh_path=None, output_path=None, version=None):
    """
    FBX 转 Mesh。
    - 有 template_mesh_path: 用模板打包（保留原始头部字段）
    """
    root, fbx_version = read_fbx(fbx_path)

    # 提取 Geometry
    objects = root.find("Objects")
    if not objects:
        raise ValueError("FBX 中未找到 Objects 节点")

    geom = None
    for child in objects.children:
        if child.name == "Geometry":
            geom = child
            break
    if not geom:
        raise ValueError("FBX 中未找到 Geometry 节点")

    # 提取顶点
    verts_node = geom.find("Vertices")
    if not verts_node or not verts_node.properties:
        raise ValueError("FBX 中未找到顶点数据")
    vert_data = verts_node.properties[0].value
    verts = [(vert_data[i], vert_data[i+1], vert_data[i+2]) for i in range(0, len(vert_data), 3)]

    # 提取面索引（FBX 用负值标记多边形结束，取反恢复）
    pvi_node = geom.find("PolygonVertexIndex")
    if not pvi_node or not pvi_node.properties:
        raise ValueError("FBX 中未找到面索引数据")
    pvi = pvi_node.properties[0].value

    # 将 FBX 多边形索引转为三角形
    faces = []
    poly_starts = []
    poly_indices = []
    current_poly = []
    for idx in pvi:
        if idx < 0:
            current_poly.append(~idx)
            poly_indices.append(current_poly)
            current_poly = []
        else:
            current_poly.append(idx)

    # 扇形三角化
    for poly in poly_indices:
        if len(poly) < 3:
            continue
        for i in range(1, len(poly) - 1):
            faces.append((poly[0], poly[i], poly[i+1]))

    # 提取 UV
    uvs = []
    uv_layers = []
    le_uv = geom.find("LayerElementUV")
    if le_uv:
        uv_node = le_uv.find("UV")
        uv_idx_node = le_uv.find("UVIndex")
        mapping_type = le_uv.find("MappingInformationType")
        ref_type = le_uv.find("ReferenceInformationType")

        if uv_node and uv_node.properties:
            raw_uvs = uv_node.properties[0].value
            all_uvs = [(raw_uvs[i], raw_uvs[i+1]) for i in range(0, len(raw_uvs), 2)]

            mapping_str = mapping_type.properties[0].value if mapping_type and mapping_type.properties else "ByVertex"
            ref_str = ref_type.properties[0].value if ref_type and ref_type.properties else "Direct"

            if ref_str == "IndexToDirect" and uv_idx_node and uv_idx_node.properties:
                uv_indices = uv_idx_node.properties[0].value
                # 按面顶点展开
                uvs = []
                for idx in uv_indices:
                    if 0 <= idx < len(all_uvs):
                        uvs.append(all_uvs[idx])
                    else:
                        uvs.append((0.0, 0.0))
            elif mapping_str == "ByVertex" or ref_str == "Direct":
                uvs = all_uvs[:len(verts)]
                while len(uvs) < len(verts):
                    uvs.append((0.0, 0.0))
            else:
                uvs = all_uvs[:len(verts)]
                while len(uvs) < len(verts):
                    uvs.append((0.0, 0.0))

        if uvs:
            uv_layers = [uvs]

    # 提取法线
    normals = []
    le_n = geom.find("LayerElementNormal")
    if le_n:
        n_node = le_n.find("Normals")
        if n_node and n_node.properties:
            n_data = n_node.properties[0].value
            normals = [(n_data[i], n_data[i+1], n_data[i+2]) for i in range(0, len(n_data), 3)]

    # 提取骨骼与权重
    bones = []
    weights = [[] for _ in range(len(verts))]

    # 找到所有 Deformer
    deformers = [c for c in objects.children if c.name == "Deformer"]
    skin_deformer = None
    clusters = []
    for d in deformers:
        st = d.find("SkinningType")
        if st:
            skin_deformer = d
        else:
            # Cluster
            indexes_node = d.find("Indexes")
            weights_node = d.find("Weights")
            if indexes_node and weights_node:
                clusters.append(d)

    # 找到所有骨骼 Model
    bone_models = [c for c in objects.children if c.name == "Model"]
    # 找 Connections 确定骨骼层级
    connections_node = root.find("Connections")
    conn_map = {}  # child_id -> [parent_id, ...]
    if connections_node:
        for c in connections_node.children:
            if c.name == "C" and len(c.properties) >= 3:
                ctype = c.properties[0].value
                child_id = c.properties[1].value
                parent_id = c.properties[2].value
                if ctype == "OO":
                    conn_map.setdefault(child_id, []).append(parent_id)

    # 构建 cluster -> bone_id 映射
    cluster_to_bone = {}
    for cl in clusters:
        cl_id = cl.properties[0].value
        parents = conn_map.get(cl_id, [])
        for pid in parents:
            for bm in bone_models:
                if bm.properties[0].value == pid:
                    cluster_to_bone[cl_id] = pid
                    break

    # 收集骨骼信息
    bone_id_list = []  # FBX id 列表
    bone_name_list = []
    bone_parent_list = []
    bone_matrix_list = []

    for cl in clusters:
        cl_id = cl.properties[0].value
        bone_fbx_id = cluster_to_bone.get(cl_id)
        if bone_fbx_id is None:
            continue

        # 找骨骼 Model
        bone_model = None
        for bm in bone_models:
            if bm.properties[0].value == bone_fbx_id:
                bone_model = bm
                break
        if not bone_model:
            continue

        bone_name = bone_model.properties[1].value if len(bone_model.properties) > 1 else f"bone_{len(bone_id_list)}"
        # 提取变换
        props = bone_model.find("Properties70")
        tx, ty, tz = 0, 0, 0
        if props:
            for p in props.children:
                if p.name == "P" and len(p.properties) >= 7:
                    pname = p.properties[0].value
                    if pname == "Lcl Translation":
                        tx, ty, tz = p.properties[4].value, p.properties[5].value, p.properties[6].value
                    elif pname == "Lcl Rotation":
                        pass  # 可选

        # 矩阵（从 Transform/TransformLink 或位置构建）
        tl = cl.find("TransformLink")
        matrix = None
        if tl and tl.properties:
            matrix = list(tl.properties[0].value)
        else:
            matrix = [1,0,0,0, 0,1,0,0, 0,0,1,0, tx,ty,tz,1]

        # 父骨骼
        parent_idx = -1
        bone_parents = conn_map.get(bone_fbx_id, [])
        for pid in bone_parents:
            if pid in bone_id_list:
                parent_idx = bone_id_list.index(pid)
                break

        local_idx = len(bone_id_list)
        bone_id_list.append(bone_fbx_id)
        bone_name_list.append(bone_name)
        bone_parent_list.append(parent_idx)
        bone_matrix_list.append(matrix)
        bones.append(BoneInfo(name=bone_name, parent=parent_idx, matrix=matrix))

        # 提取权重
        indexes_node = cl.find("Indexes")
        weights_node = cl.find("Weights")
        if indexes_node and weights_node and indexes_node.properties and weights_node.properties:
            idxs = indexes_node.properties[0].value
            wts = weights_node.properties[0].value
            for vi, w in zip(idxs, wts):
                if 0 <= vi < len(weights):
                    weights[vi].append((local_idx, float(w)))

    # 归一化权重
    final_weights = []
    for vi in range(len(verts)):
        vw = weights[vi]
        if not vw:
            final_weights.append(([], []))
            continue
        bids = [b for b, _ in vw]
        bws = [w for _, w in vw]
        s = sum(bws)
        if s > 0:
            bws = [w / s for w in bws]
        # 最多保留4个
        if len(bids) > 4:
            paired = sorted(zip(bws, bids), reverse=True)[:4]
            bws = [p[0] for p in paired]
            bids = [p[1] for p in paired]
            s = sum(bws)
            if s > 0:
                bws = [w / s for w in bws]
        final_weights.append((bids, bws))

    # 构建 MeshData
    md = MeshData(
        verts=verts,
        faces=faces,
        uv_layers=uv_layers,
        normals=normals,
        weights=final_weights,
        bones=bones,
    )

    if not template_mesh_path:
        raise ValueError("FBX→Mesh 需要提供模板 mesh 文件")
    if output_path is None:
        base = os.path.splitext(template_mesh_path)[0]
        output_path = base + "_from_fbx.mesh"
    pack_mesh(template_mesh_path, md, output_path)
    mode = "模板模式"

    info = {
        'verts': len(verts),
        'faces': len(faces),
        'uvs': len(uvs),
        'bones': len(bones),
        'fbx_version': fbx_version,
        'mode': mode,
    }
    print(f"  FBX→Mesh [{mode}]: {info['verts']}顶点, {info['faces']}三角形, "
          f"{info['bones']}骨骼, FBX版本{fbx_version}")
    print(f"  输出: {output_path}")
    return output_path, info


# ============================================================
# FBX → OBJ（便捷功能）
# ============================================================

def fbx_to_obj(fbx_path, obj_path=None):
    """FBX 转 OBJ"""
    root, fbx_version = read_fbx(fbx_path)
    objects = root.find("Objects")
    if not objects:
        raise ValueError("FBX 中未找到 Objects 节点")

    geom = None
    for child in objects.children:
        if child.name == "Geometry":
            geom = child
            break
    if not geom:
        raise ValueError("FBX 中未找到 Geometry 节点")

    # 顶点
    verts_node = geom.find("Vertices")
    vert_data = verts_node.properties[0].value
    verts = [(vert_data[i], vert_data[i+1], vert_data[i+2]) for i in range(0, len(vert_data), 3)]

    # 面
    pvi_node = geom.find("PolygonVertexIndex")
    pvi = pvi_node.properties[0].value
    faces = []
    current_poly = []
    for idx in pvi:
        if idx < 0:
            current_poly.append(~idx)
            for i in range(1, len(current_poly) - 1):
                faces.append((current_poly[0], current_poly[i], current_poly[i+1]))
            current_poly = []
        else:
            current_poly.append(idx)

    # UV
    uvs = []
    le_uv = geom.find("LayerElementUV")
    if le_uv:
        uv_node = le_uv.find("UV")
        if uv_node and uv_node.properties:
            raw = uv_node.properties[0].value
            uvs = [(raw[i], raw[i+1]) for i in range(0, len(raw), 2)]
        while len(uvs) < len(verts):
            uvs.append((0.0, 0.0))

    if obj_path is None:
        base = os.path.splitext(fbx_path)[0]
        obj_path = base + ".obj"

    name = os.path.splitext(os.path.basename(fbx_path))[0]
    write_obj(obj_path, verts, uvs, faces, name=name)

    info = {
        'verts': len(verts),
        'faces': len(faces),
        'uvs': len(uvs),
    }
    print(f"  FBX→OBJ: {info['verts']}顶点, {info['faces']}三角形")
    print(f"  输出: {obj_path}")
    return obj_path, info


# ============================================================
# 批量转换
# ============================================================

import glob


def _scan_files(input_path, ext):
    """扫描输入路径，返回匹配扩展名的文件列表。
    input_path 可以是目录、单个文件、或通配符。
    """
    if os.path.isdir(input_path):
        files = glob.glob(os.path.join(input_path, f"*.{ext}"))
        return sorted(files)
    elif os.path.isfile(input_path):
        return [input_path]
    else:
        # 通配符
        files = glob.glob(input_path)
        files = [f for f in files if f.lower().endswith(f".{ext}")]
        return sorted(files)


def _batch_convert(conv_func, files, label, output_dir=None, **kwargs):
    """批量转换通用引擎"""
    total = len(files)
    ok = 0
    fail = 0
    errors = []

    print(f"\n{'='*60}")
    print(f"  批量{label}: 共 {total} 个文件")
    print(f"{'='*60}")

    for i, f in enumerate(files, 1):
        fname = os.path.basename(f)
        print(f"\n[{i}/{total}] {fname}")

        # 构建输出路径
        base = os.path.splitext(f)[0]
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            out = os.path.join(output_dir, os.path.basename(base))
        else:
            out = base

        # 根据 conv_func 确定输出扩展名和参数
        try:
            if conv_func in (mesh_to_obj, mesh_to_fbx, fbx_to_obj):
                ext_map = {mesh_to_obj: ".obj", mesh_to_fbx: ".fbx", fbx_to_obj: ".obj"}
                conv_func(f, out + ext_map[conv_func])
            elif conv_func in (obj_to_mesh, fbx_to_mesh):
                tpl = kwargs.get('template')
                if not tpl:
                    raise ValueError("缺少模板 mesh 文件")
                conv_func(f, tpl, out + ".mesh")
            ok += 1
        except Exception as e:
            fail += 1
            errors.append((fname, str(e)))
            print(f"  ✗ 失败: {e}")

    # 汇总
    print(f"\n{'='*60}")
    print(f"  批量{label}完成: 成功 {ok}/{total}, 失败 {fail}")
    if errors:
        print(f"  失败文件:")
        for fname, err in errors:
            print(f"    {fname}: {err}")
    print(f"{'='*60}")
    return ok, fail


def batch_mesh_to_obj(input_path, output_dir=None):
    """批量 Mesh → OBJ"""
    files = _scan_files(input_path, "mesh")
    if not files:
        print(f"未找到 .mesh 文件: {input_path}")
        return 0, 0
    return _batch_convert(mesh_to_obj, files, "Mesh→OBJ", output_dir)


def batch_mesh_to_fbx(input_path, output_dir=None):
    """批量 Mesh → FBX"""
    files = _scan_files(input_path, "mesh")
    if not files:
        print(f"未找到 .mesh 文件: {input_path}")
        return 0, 0
    return _batch_convert(mesh_to_fbx, files, "Mesh→FBX", output_dir)


def batch_obj_to_mesh(input_path, template_mesh_path, output_dir=None):
    """批量 OBJ → Mesh（模板模式）"""
    files = _scan_files(input_path, "obj")
    if not files:
        print(f"未找到 .obj 文件: {input_path}")
        return 0, 0
    return _batch_convert(obj_to_mesh, files, "OBJ→Mesh", output_dir,
                          template=template_mesh_path)


def batch_fbx_to_mesh(input_path, template_mesh_path, output_dir=None):
    """批量 FBX → Mesh（模板模式）"""
    files = _scan_files(input_path, "fbx")
    if not files:
        print(f"未找到 .fbx 文件: {input_path}")
        return 0, 0
    return _batch_convert(fbx_to_mesh, files, "FBX→Mesh", output_dir,
                          template=template_mesh_path)


def batch_fbx_to_obj(input_path, output_dir=None):
    """批量 FBX → OBJ"""
    files = _scan_files(input_path, "fbx")
    if not files:
        print(f"未找到 .fbx 文件: {input_path}")
        return 0, 0
    return _batch_convert(fbx_to_obj, files, "FBX→OBJ", output_dir)
