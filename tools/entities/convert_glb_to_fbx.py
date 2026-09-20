#!/usr/bin/env python3
"""Blender background script: convert source-faithful GLB to binary FBX."""
import bpy, hashlib, json, os, re, shutil, sys, traceback

def args_after_dash():
    return sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []

def main():
    args=args_after_dash()
    if len(args)!=2: raise SystemExit('usage: blender -b --python convert_glb_to_fbx.py -- input.glb output.fbx')
    source,output=map(os.path.abspath,args);os.makedirs(os.path.dirname(output),exist_ok=True)
    # Unity-targeted left/right mirror. Prototype's rig names land on the
    # visually opposite side when the right-handed glTF is consumed by
    # Unity's left-handed importer; mirroring the whole scene (meshes,
    # skins, nodes, every animation curve) keeps names intact while
    # Hip_L again lives on the left. Enabled with --mirror-lr or
    # PROTOTYPE_FBX_MIRROR_LR=1. GLB viewer previews stay source-faithful.
    mirror_flag='--mirror-lr' in sys.argv or os.environ.get('PROTOTYPE_FBX_MIRROR_LR')=='1'
    if mirror_flag:
        import io as _io
        script_dir=os.path.dirname(os.path.abspath(__file__))
        if script_dir not in sys.path:sys.path.insert(0,script_dir)
        import glb_mirror_lr
        mirrored=source+'.mirror-lr.glb'
        stats=glb_mirror_lr.mirror_file(source,mirrored)
        source=mirrored
        print('MIRROR_LR_APPLIED '+json.dumps(stats,ensure_ascii=False))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=source,import_pack_images=True,merge_vertices=False,import_shading='NORMALS')
    # 全部网格强制平滑着色（use_smooth）：源数据按平直着色导出时 FBX 里
    # LayerElementSmoothing 全是 0，Unity/Maya 端呈现为硬边。Blender 导出
    # mesh_smooth_type='OFF' 时以多边形 use_smooth 为真理，这里从产品需求规定
    # 全量平滑（后续如需要硬边再按 mesh 白名单加细分）。
    for _mesh_obj in bpy.context.scene.objects:
        if _mesh_obj.type!='MESH':continue
        for _p in _mesh_obj.data.polygons:
            _p.use_smooth=True
    armatures=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
    has_armatures=len(armatures)>0
    # Blender's glTF importer creates helper Icospheres in a
    # `glTF_not_exported` collection and assigns them as pose-bone custom
    # shapes. They are editor helpers, not source geometry or FBX bones.
    bind_action=next((action for action in bpy.data.actions if action.name == '000_A_POSE_BIND'),None)
    for obj in armatures:
        # Export in POSE mode. REST mode makes Blender's FBX baker write every
        # source Action as the same static bind pose.
        obj.data.pose_position='POSE'
        obj.data.display_type='OCTAHEDRAL'
        for bone in obj.pose.bones:
            bone.custom_shape=None
        obj.animation_data_create()
        obj.animation_data.action=bind_action
        # Blender 4.5 glTF Actions are slotted. The FBX exporter's legacy
        # "all actions" path creates named takes but evaluates them as the
        # active bind action. Explicit NLA strips preserve each source Action.
        while obj.animation_data.nla_tracks:
            obj.animation_data.nla_tracks.remove(obj.animation_data.nla_tracks[0])
        for action in bpy.data.actions:
            start,end=action.frame_range
            track=obj.animation_data.nla_tracks.new();track.name=action.name
            strip=track.strips.new(action.name,int(start),action)
            strip.action_frame_start=start;strip.action_frame_end=end
        # The first NLA strip is the bind pose/default take. Keeping it also
        # as the active Action would override every strip during FBX baking.
        obj.animation_data.action=None
    for collection in list(bpy.data.collections):
        if collection.name.startswith('glTF_not_exported'):
            for helper in list(collection.objects):
                bpy.data.objects.remove(helper,do_unlink=True)
            bpy.data.collections.remove(collection)

    # Unity does not reliably extract Blender FBX embedded media. Write real
    # PNG files and keep relative FBX texture references instead.
    texture_dir=os.path.dirname(output)
    os.makedirs(texture_dir,exist_ok=True);written_textures=[];written_hashes={};used=set()
    for image in bpy.data.images:
        if image.name in {'Render Result','Viewer Node'} or image.size[0] == 0:
            continue
        stem=re.sub(r'[^A-Za-z0-9._-]+','_',os.path.splitext(image.name)[0]).strip('._') or 'texture'
        name=stem+'.png';n=2
        while name.lower() in used:name=f'{stem}_{n}.png';n+=1
        path=os.path.join(texture_dir,name)
        image.filepath_raw=path;image.file_format='PNG';image.save()
        if image.packed_file is not None:
            image.unpack(method='USE_ORIGINAL')
        image.filepath=path
        # 源贴图名可能保留 .dds/.tga 扩展名（如 weapons001_diffuse.dds），
        # FBX Texture 节点会照这个名记引用；Unity 按它去找 .dds 文件必然 miss。
        # 强制把图名改成最终写出的 PNG 文件命名，保持 Texture 引用与实际文件一致。
        image.name=stem
        if os.path.isfile(path):
            with open(path,'rb') as f:
                h=hashlib.sha256(f.read()).hexdigest()
            if h in written_hashes:
                try:os.remove(path)
                except OSError:pass
                image.filepath=os.path.join(texture_dir,written_hashes[h])
                image.filepath_raw=image.filepath
                continue
            written_hashes[h]=name
            used.add(name.lower())
            written_textures.append(name)

    # Keep source images that are intentionally provenance-only in generic
    # PBR (for example Prototype's specular maps) even when Blender does not
    # instantiate an Image datablock for an unbound glTF texture.
    source_texture_dir=os.path.join(os.path.dirname(source),'textures')
    if os.path.isdir(source_texture_dir):
        for source_name in sorted(os.listdir(source_texture_dir)):
            if not source_name.lower().endswith('.png'):
                continue
            src_path=os.path.join(source_texture_dir,source_name)
            if not os.path.isfile(src_path):
                continue
            with open(src_path,'rb') as f:
                h=hashlib.sha256(f.read()).hexdigest()
            if h in written_hashes:
                continue
            dst_path=os.path.join(texture_dir,source_name)
            if not os.path.exists(dst_path):
                shutil.copy2(src_path,dst_path)
            written_hashes[h]=source_name
            written_textures.append(source_name)
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action='SELECT')
    result=bpy.ops.export_scene.fbx(
        filepath=output,use_selection=False,object_types={'EMPTY','ARMATURE','MESH'},
        apply_unit_scale=True,apply_scale_options='FBX_SCALE_ALL',global_scale=1.0,
        axis_forward='-Z',axis_up='Y',use_space_transform=True,bake_space_transform=False,
        add_leaf_bones=False,primary_bone_axis='Y',secondary_bone_axis='X',
        use_armature_deform_only=False,armature_nodetype='NULL',
        bake_anim=has_armatures,bake_anim_use_all_bones=has_armatures,bake_anim_use_nla_strips=has_armatures,
        bake_anim_use_all_actions=False,bake_anim_force_startend_keying=has_armatures,
        bake_anim_step=1.0,bake_anim_simplify_factor=0.0,
        path_mode='STRIP',embed_textures=False,use_custom_props=True,
        mesh_smooth_type='FACE',use_triangles=True,use_mesh_modifiers=True)
    if 'FINISHED' not in result or not os.path.isfile(output):raise RuntimeError(f'FBX export failed: {result}')
    report_mirror=mirror_flag
    report={'source_glb':source,'output_fbx':output,'bytes':os.path.getsize(output),'mirror_lr':report_mirror,
            'objects':len(bpy.context.scene.objects),'meshes':sum(o.type=='MESH' for o in bpy.context.scene.objects),
            'armatures':len(armatures),'default_action':bind_action.name if bind_action else None,
            'actions':[a.name for a in bpy.data.actions],'textures':written_textures}
    with open(output+'.conversion.json','w',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print('FBX_CONVERSION_OK '+json.dumps(report,ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except Exception:
        traceback.print_exc();raise
