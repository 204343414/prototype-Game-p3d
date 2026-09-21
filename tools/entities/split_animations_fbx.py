#!/usr/bin/env python3
"""Blender background script: split a source GLB into Unity-ready body + @animation FBX files.

一个实体拆成：
  <outdir>/<name>.fbx            本体（网格+骨骼，不含任何动画 Take，Unity 导入秒过）
  <outdir>/<name>-anims/<name>@<action>.fbx  每个源 Action 一条纯骨骼动画（无网格），
                                  按 Unity "model@animation" 约定自动并入本体。
  <outdir>/*.png                 本体引用的贴图（与 convert_glb_to_fbx 同名规则）

动机：单个 FBX 塞几百条动画 take（如 alex.p3d 534 条）会让 Unity 逐 take 建剪辑，
导入数小时起步；拆开后每个文件只有一个 take，Unity 直接并行解，问题消失。

用法:
  blender -b --python split_animations_fbx.py -- input.glb outdir --name alex.p3d
环境:
  PROTOTYPE_FBX_MIRROR_LR=1  先整体镜像（与 convert_glb_to_fbx 同一规则），使动画与
                             已经交付的镜像本体骨骼左右一致。
"""
import bpy, hashlib, json, os, re, shutil, sys, time, traceback

BIND_ACTION = '000_A_POSE_BIND'

def args_after_dash():
    return sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []


def sanitize_filename(text):
    cleaned = re.sub(r'[\\/:*?"<>|]+', '_', text).strip().strip('.')
    return cleaned or 'unnamed'


def export_fbx(filepath, has_anim, object_types):
    # has_anim=False  → 本体，无 take
    # has_anim='nla'  → 多 strip 一次性导出（convert_glb_to_fbx.py 用）
    # has_anim='action' → 单 action 烘焙（本脚本拆动画用；此时无 NLA strip，
    #   必须 bake_anim_use_nla_strips=False 否则烤箱空转，FBX 里一条曲线都没有）
    nla = has_anim == 'nla'
    anim = has_anim is not False
    return bpy.ops.export_scene.fbx(
        filepath=filepath, use_selection=False, object_types=object_types,
        apply_unit_scale=True, apply_scale_options='FBX_SCALE_ALL', global_scale=1.0,
        axis_forward='-Z', axis_up='Y', use_space_transform=True, bake_space_transform=False,
        add_leaf_bones=False, primary_bone_axis='Y', secondary_bone_axis='X',
        use_armature_deform_only=False, armature_nodetype='NULL',
        bake_anim=anim, bake_anim_use_all_bones=anim,
        bake_anim_use_nla_strips=nla, bake_anim_use_all_actions=False,
        bake_anim_force_startend_keying=anim,
        bake_anim_step=1.0, bake_anim_simplify_factor=0.0,
        path_mode='STRIP', embed_textures=False, use_custom_props=True,
        mesh_smooth_type='FACE', use_triangles=True, use_mesh_modifiers=True)


def main():
    args = args_after_dash()
    name = 'entity'
    if '--name' in args:
        i = args.index('--name')
        if i + 1 < len(args):
            name = args[i + 1]
            args = [a for j, a in enumerate(args) if j not in (i, i + 1)]
    positional = [a for a in args if not a.startswith('--')]
    if len(positional) != 2:
        raise SystemExit('usage: blender -b --python split_animations_fbx.py -- input.glb outdir [--name entity]')
    source, outdir = map(os.path.abspath, positional)
    os.makedirs(outdir, exist_ok=True)
    anims_dir = os.path.join(outdir, name + '-anims')
    os.makedirs(anims_dir, exist_ok=True)

    mirror_flag = os.environ.get('PROTOTYPE_FBX_MIRROR_LR') == '1'
    if mirror_flag:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        import glb_mirror_lr
        mirrored = source + '.mirror-lr.glb'
        stats = glb_mirror_lr.mirror_file(source, mirrored)
        source = mirrored
        print('MIRROR_LR_APPLIED ' + json.dumps(stats, ensure_ascii=False))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=source, import_pack_images=True,
                              merge_vertices=False, import_shading='NORMALS')

    # 本体平滑着色（与 convert_glb_to_fbx 同一规则）
    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH':
            continue
        for p in obj.data.polygons:
            p.use_smooth = True

    armatures = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
    if not armatures:
        raise RuntimeError('GLB 没有骨骼，不适合拆分动画')
    bind_action = next((a for a in bpy.data.actions if a.name == BIND_ACTION), None)
    for obj in armatures:
        obj.data.pose_position = 'POSE'
        obj.data.display_type = 'OCTAHEDRAL'
        for bone in obj.pose.bones:
            bone.custom_shape = None
        obj.animation_data_create()
        obj.animation_data.action = bind_action
        while obj.animation_data.nla_tracks:
            obj.animation_data.nla_tracks.remove(obj.animation_data.nla_tracks[0])
    for collection in list(bpy.data.collections):
        if collection.name.startswith('glTF_not_exported'):
            for helper in list(collection.objects):
                bpy.data.objects.remove(helper, do_unlink=True)
            bpy.data.collections.remove(collection)

    # 写本体贴图（同 convert_glb_to_fbx 规则：PNG 落盘，image.name=stem 保持引用一致）
    written_textures = []
    written_hashes = {}
    used = set()
    for image in bpy.data.images:
        if image.name in {'Render Result', 'Viewer Node'} or image.size[0] == 0:
            continue
        stem = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.splitext(image.name)[0]).strip('._') or 'texture'
        fname = stem + '.png'
        n = 2
        while fname.lower() in used:
            fname = f'{stem}_{n}.png'
            n += 1
        path = os.path.join(outdir, fname)
        image.filepath_raw = path
        image.file_format = 'PNG'
        image.save()
        if image.packed_file is not None:
            image.unpack(method='USE_ORIGINAL')
        image.filepath = path
        image.name = stem
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                h = hashlib.sha256(f.read()).hexdigest()
            if h in written_hashes:
                os.remove(path)
            else:
                used.add(fname.lower())
                written_hashes[h] = fname
                written_textures.append(fname)

    # 1) 本体：不含任何动画
    body_path = os.path.join(outdir, name + '.fbx')
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='SELECT')
    result = export_fbx(body_path, False, {'EMPTY', 'ARMATURE', 'MESH'})
    if 'FINISHED' not in result or not os.path.isfile(body_path):
        raise RuntimeError(f'body FBX export failed: {result}')
    print('BODY_OK ' + json.dumps({'path': body_path, 'bytes': os.path.getsize(body_path)}, ensure_ascii=False))

    # 2) 拆动画：删网格留骨骼，逐 Action 单独导出
    for obj in list(bpy.context.scene.objects):
        if obj.type != 'ARMATURE':
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.object.select_all(action='DESELECT')
    anim_actions = [a for a in bpy.data.actions if a.name != BIND_ACTION]
    written_anims = []
    skipped = []
    total = len(anim_actions)
    t0 = time.time()
    for index, action in enumerate(anim_actions, 1):
        for obj in armatures:
            while obj.animation_data.nla_tracks:
                obj.animation_data.nla_tracks.remove(obj.animation_data.nla_tracks[0])
            obj.animation_data.action = action
        start, end = action.frame_range
        if end <= start:
            skipped.append(action.name)
            continue
        bpy.context.scene.frame_start = int(start)
        bpy.context.scene.frame_end = int(end) + 1
        bpy.context.scene.frame_set(int(start))
        fname = f'{name}@{sanitize_filename(action.name)}.fbx'
        path = os.path.join(anims_dir, fname)
        bpy.ops.object.select_all(action='SELECT')
        try:
            result = export_fbx(path, 'action', {'ARMATURE'})
        except Exception as exc:
            skipped.append(action.name)
            print(f'ANIM_SKIP {action.name}: {exc}')
            continue
        if 'FINISHED' not in result or not os.path.isfile(path):
            skipped.append(action.name)
            continue
        written_anims.append({'file': fname, 'action': action.name,
                              'bytes': os.path.getsize(path),
                              'frames': [int(start), int(end)]})
        if index % 20 == 0 or index == total:
            rate = index / max(1e-6, time.time() - t0)
            eta = (total - index) / max(rate, 1e-6)
            print(f'ANIM_PROGRESS {index}/{total} eta={eta:.0f}s', flush=True)

    # 恢复本体动作引用并写 manifest
    for obj in armatures:
        obj.animation_data.action = bind_action
    report = {'source_glb': source, 'name': name, 'mirror_lr': mirror_flag,
              'body_fbx': os.path.basename(body_path), 'body_bytes': os.path.getsize(body_path),
              'textures': written_textures, 'animation_count': len(written_anims),
              'animations': written_anims, 'skipped_actions': skipped,
              'seconds': round(time.time() - t0, 1)}
    with open(os.path.join(outdir, 'split-report.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('SPLIT_OK ' + json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
