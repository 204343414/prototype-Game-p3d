import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { makeMapTexture } from './map-materials.js';

const POWER_IDS = {
  AlexBlades:'alex_blades', AlexClaws:'alex_claws', AlexHammerFist:'alex_hammerfist',
  AlexWhipFist:'alex_whipfist', AlexMuscleMass:'alex_musclemass', AlexShield:'alex_shield',
  AlexArmour:'alex_armour', AlexSpines:'alex_spines'
};
const ARM_REPLACEMENTS = new Set(['AlexBlades','AlexClaws','AlexHammerFist','AlexWhipFist','AlexMuscleMass']);

function floats(b64, Type=Float32Array) {
  if (!b64) return new Type(0);
  const bin=atob(b64), bytes=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);
  return new Type(bytes.buffer);
}
function disposeObject(root) {
  root.traverse(o=>{
    if(o.geometry)o.geometry.dispose();
    if(o.material){const ms=Array.isArray(o.material)?o.material:[o.material];for(const m of ms){if(m.map)m.map.dispose();m.dispose();}}
  });
  root.removeFromParent();
}

export function createAlex3DRuntime({container,onStatus=()=>{},onDiagnostic=()=>{}}) {
  const scene=new THREE.Scene();
  scene.background=new THREE.Color(0x080c0d);
  scene.fog=new THREE.FogExp2(0x080c0d,.045);
  const camera=new THREE.PerspectiveCamera(42,1,.01,3000);
  camera.position.set(2.8,2.0,3.6);
  const renderer=new THREE.WebGLRenderer({antialias:true,alpha:false});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));
  renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.shadowMap.enabled=true;
  container.appendChild(renderer.domElement);
  const controls=new OrbitControls(camera,renderer.domElement);
  controls.enableDamping=true;controls.dampingFactor=.08;controls.target.set(0,1,0);
  scene.add(new THREE.HemisphereLight(0xeafcff,0x17201c,2.0));
  const key=new THREE.DirectionalLight(0xffffff,2.4);key.position.set(3,7,4);key.castShadow=true;scene.add(key);
  const rim=new THREE.DirectionalLight(0x7fd4ff,1.2);rim.position.set(-4,3,-3);scene.add(rim);
  const floor=new THREE.Mesh(new THREE.CircleGeometry(8,64),new THREE.MeshStandardMaterial({color:0x111918,roughness:1,metalness:0}));
  floor.rotation.x=-Math.PI/2;floor.receiveShadow=true;scene.add(floor);
  const grid=new THREE.GridHelper(16,32,0x314340,0x1a2624);grid.position.y=.002;scene.add(grid);
  const actorRoot=new THREE.Group();scene.add(actorRoot);

  let catalog=null,base=null,power=null,currentTransformation='Alex',disposed=false,powerLoadName=null,powerLoadPromise=null,powerGeneration=0,latestState=null,lastStateTime=null;
  const renderedPosition=new THREE.Vector3(),lastFollowPosition=new THREE.Vector3();
  const active=new Map(); // decoded track identity -> action records
  const missing=new Set();
  const clock=new THREE.Clock();

  function resize(){const w=Math.max(1,container.clientWidth),h=Math.max(1,container.clientHeight);renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();}
  const ro=new ResizeObserver(resize);ro.observe(container);resize();

  function buildRigs(skeletons,rigParent,prefix){
    const rigs=new Map();
    for(const skel of skeletons||[]){
      if(!skel?.name||!skel.joints?.length)continue;
      const root=new THREE.Group();root.name=`rig:${prefix}:${skel.name}`;rigParent.add(root);
      const bones=skel.joints.map((j,i)=>{const b=new THREE.Bone();b.name=j.name||`Joint_${i}`;const m=new THREE.Matrix4();if(j.matrix?.length===16)m.fromArray(j.matrix);m.decompose(b.position,b.quaternion,b.scale);return b;});
      skel.joints.forEach((j,i)=>{const p=j.parent;if(p>=0&&p<bones.length&&p!==i)bones[p].add(bones[i]);else root.add(bones[i]);});
      root.updateMatrixWorld(true);
      const skeleton=new THREE.Skeleton(bones,bones.map(b=>b.matrixWorld.clone().invert()));
      rigs.set(skel.name,{key:`${prefix}:${skel.name}`,name:skel.name,root,bones,skeleton,mixer:new THREE.AnimationMixer(root)});
    }
    return rigs;
  }

  function buildPackage(data,prefix){
    const root=new THREE.Group();root.name=`package:${prefix}`;actorRoot.add(root);
    const rigsParent=new THREE.Group(),meshesParent=new THREE.Group();root.add(rigsParent,meshesParent);
    const rigs=buildRigs(data.skeletons,rigsParent,prefix),textures=new Map();
    for(const d of data.textures||[]){try{textures.set(d.key,makeMapTexture(d));}catch(e){onDiagnostic(`纹理解码失败 ${d.key}: ${e.message}`);}}
    const meshes=[];
    for(const m of data.meshes||[]){
      const pos=floats(m.positions),idx=floats(m.indices,Uint16Array);if(pos.length<3||idx.length<3)continue;
      const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(pos,3));g.setIndex(new THREE.BufferAttribute(idx,1));
      const uv=floats(m.uv);if(uv.length)g.setAttribute('uv',new THREE.BufferAttribute(uv,2));g.computeVertexNormals();
      const tex=textures.get(m.texture_key)||null;
      const mat=new THREE.MeshStandardMaterial({color:tex?0xffffff:0x87948d,map:tex,roughness:.82,metalness:.02,side:THREE.DoubleSide});
      const rig=rigs.get(m.skeleton_name);let obj;
      if(rig&&m.skin_indices&&m.skin_weights){
        g.setAttribute('skinIndex',new THREE.Uint16BufferAttribute(floats(m.skin_indices,Uint16Array),4));
        g.setAttribute('skinWeight',new THREE.Float32BufferAttribute(floats(m.skin_weights),4));
        obj=new THREE.SkinnedMesh(g,mat);meshesParent.add(obj);obj.updateMatrixWorld(true);obj.bind(rig.skeleton,obj.matrixWorld);
      }else{obj=new THREE.Mesh(g,mat);meshesParent.add(obj);}
      obj.name=m.geometry_name||m.skin_name||'mesh';obj.frustumCulled=false;obj.castShadow=true;obj.receiveShadow=true;meshes.push(obj);
    }
    const animations=new Map((data.animations||[]).map(a=>[a.name,a]));
    return {prefix,root,rigs,meshes,animations,data};
  }

  async function fetchPackage(entry,prefix){
    onStatus(`正在解码 ${prefix} 的原始网格、骨骼与动画…`);
    const r=await fetch(`/api/entity_mesh?${new URLSearchParams({entry})}`);const d=await r.json();
    if(!r.ok||d.error)throw new Error(d.error||`HTTP ${r.status}`);
    return buildPackage(d,prefix);
  }
  function allRigs(){return [...(base?.rigs.values()||[]),...(power?.rigs.values()||[])];}
  function applyBaseVisibility(assembly=latestState?.assembly||{}){
    if(!base)return;const replacesBody=!!assembly.body_template&&!!power,hideRegularArms=(ARM_REPLACEMENTS.has(assembly.transformation)||replacesBody)&&!!power;
    for(const m of base.meshes){const n=m.name.toLowerCase();
      if(n.startsWith('groundspike_'))m.visible=false;
      else if(n.includes('alex_reg_left_arm'))m.visible=!replacesBody&&!!assembly.left_arm_needed;
      else if(n.includes('alex_reg_arms'))m.visible=!replacesBody&&!hideRegularArms;
      else if(n.includes('alex_reg_body'))m.visible=!replacesBody;
      else m.visible=false;
    }
  }
  function animationByName(name){return power?.animations.get(name)||base?.animations.get(name)||null;}
  function clipFor(anim,rig,sourceStart=0){
    const boneMap=new Map();for(const b of rig.bones){boneMap.set(b.name,b);boneMap.set(b.name.toLowerCase(),b);}
    const rootBones=new Set(rig.bones.filter(b=>!b.parent?.isBone));
    const tracks=[];
    for(const group of anim.groups||[]){const b=boneMap.get(group.name)||boneMap.get((group.name||'').toLowerCase());if(!b)continue;
      if(group.rot){const times=group.rot.times,values=group.rot.values.map(v=>v.slice());
        // Motion_Root rotation belongs to actor root motion just like its
        // translation; leave the skeleton root at its decoded bind transform.
        if(rootBones.has(b)&&times.length){for(const v of values){v[0]=b.quaternion.x;v[1]=b.quaternion.y;v[2]=b.quaternion.z;v[3]=b.quaternion.w;}}
        const t=Float32Array.from(times),v=Float32Array.from(values.flat());if(t.length&&v.length===t.length*4)tracks.push(new THREE.QuaternionKeyframeTrack(`${b.name}.quaternion`,t,v));}
      if(group.pos){const times=group.pos.times,values=group.pos.values.map(v=>v.slice());
        if(rootBones.has(b)&&times.length){for(const v of values){v[0]=b.position.x;v[1]=b.position.y;v[2]=b.position.z;}}
        const t=Float32Array.from(times),v=Float32Array.from(values.flat());if(t.length&&v.length===t.length*3)tracks.push(new THREE.VectorKeyframeTrack(`${b.name}.position`,t,v));}
      if(group.scale){const t=Float32Array.from(group.scale.times),v=Float32Array.from(group.scale.values.flat());if(t.length&&v.length===t.length*3)tracks.push(new THREE.VectorKeyframeTrack(`${b.name}.scale`,t,v));}
    }
    return tracks.length?new THREE.AnimationClip(anim.name,anim.duration,tracks):null;
  }
  function stopRecord(rec){for(const x of rec.actions){const clip=x.action.getClip();x.action.stop();x.mixer.uncacheAction(clip);}}
  function clearActions(){for(const r of active.values())stopRecord(r);active.clear();for(const rig of allRigs())rig.mixer.stopAllAction();}

  function makeRootSampler(anim){
    const group=(anim.groups||[]).find(g=>(g.name||'').toLowerCase()==='motion_root'&&g.pos?.times?.length&&g.pos?.values?.length);if(!group)return null;
    const times=group.pos.times,values=group.pos.values,duration=anim.duration||times.at(-1)||0;
    const samplePosition=t=>{if(t<=times[0])return new THREE.Vector3().fromArray(values[0]);let hi=times.findIndex(x=>x>=t);if(hi<0)hi=times.length-1;if(hi===0)return new THREE.Vector3().fromArray(values[0]);const lo=hi-1,d=times[hi]-times[lo],a=d>0?THREE.MathUtils.clamp((t-times[lo])/d,0,1):0;return new THREE.Vector3().fromArray(values[lo]).lerp(new THREE.Vector3().fromArray(values[hi]),a);};
    const rt=group.rot?.times||[],rv=group.rot?.values||[];
    const sampleRotation=t=>{if(!rt.length||!rv.length)return new THREE.Quaternion();if(t<=rt[0])return new THREE.Quaternion().fromArray(rv[0]).normalize();let hi=rt.findIndex(x=>x>=t);if(hi<0)hi=rt.length-1;if(hi===0)return new THREE.Quaternion().fromArray(rv[0]).normalize();const lo=hi-1,d=rt[hi]-rt[lo],a=d>0?THREE.MathUtils.clamp((t-rt[lo])/d,0,1):0;return new THREE.Quaternion().fromArray(rv[lo]).normalize().slerp(new THREE.Quaternion().fromArray(rv[hi]).normalize(),a);};
    return {samplePosition,sampleRotation,duration};
  }
  function rootSegmentDelta(sampler,from,to){
    const q0=sampler.sampleRotation(from),q1=sampler.sampleRotation(to),translation=sampler.samplePosition(to).sub(sampler.samplePosition(from)).applyQuaternion(q0.clone().invert());
    return {translation,rotation:q0.clone().invert().multiply(q1).normalize()};
  }
  function composeRootDelta(a,b){return {translation:a.translation.clone().add(b.translation.clone().applyQuaternion(a.rotation)),rotation:a.rotation.clone().multiply(b.rotation).normalize()};}
  function rootDelta(sampler,from,to,cyclic){
    const zero={translation:new THREE.Vector3(),rotation:new THREE.Quaternion()};if(!sampler)return zero;
    if(cyclic&&sampler.duration>0&&to+1e-6<from)return composeRootDelta(rootSegmentDelta(sampler,from,sampler.duration),rootSegmentDelta(sampler,0,to));
    if(!cyclic&&to+1e-6<from)return zero;return rootSegmentDelta(sampler,from,to);
  }
  function startDecodedTrack(row){
    const tr=row.track,anim=animationByName(tr.animation);if(!anim||anim.playable===false){
      const miss=tr.animation||`track@${tr.offset}`;if(!missing.has(miss)){missing.add(miss);onDiagnostic(`状态树动画未在当前 Alex/能力包中找到可播放记录：${miss}`);}return null;
    }
    const actions=[];const elapsed=Math.max(0,(row.owner_time||0)-(tr.time_begin||0));const sourceStart=Math.max(0,(tr.start_frame||0)/(anim.fps||30));
    for(const rig of allRigs()){
      const clip=clipFor(anim,rig,sourceStart);if(!clip)continue;
      const action=rig.mixer.clipAction(clip);action.enabled=true;action.setEffectiveWeight(actionWeight(row));action.setEffectiveTimeScale(1);
      const cyclic=tr.animation_type==='Cyclic'||tr.animation_type_hash==='0x25f64ae2e13c4a85'||
       ((tr.animation_type==='From Animation'||tr.animation_type_hash==='0xa43298b89a5bf3c6')&&anim.cyclic===true);
      action.setLoop(cyclic?THREE.LoopRepeat:THREE.LoopOnce,cyclic?Infinity:1);action.clampWhenFinished=!cyclic;action.play();
      const initial=sourceStart+elapsed;action.time=cyclic&&clip.duration>0?initial%clip.duration:Math.min(Math.max(0,initial),Math.max(0,clip.duration-1e-6));rig.mixer.update(0);actions.push({action,mixer:rig.mixer,sourceStart,cyclic});
    }
    if(!actions.length){onDiagnostic(`动画 ${anim.name} 存在，但与当前骨架没有精确同名关节轨道`);return null;}
    const rootSampler=makeRootSampler(anim);return {row,anim,actions,rootSampler,rootTime:actions[0].action.time};
  }
  function animationRows(state){
    const rows=[...(state.active_animation_tracks||[])],speed=Number(state.blackboard?.velocities?.['Locomotion/XZ'])||0;
    for(const row of state.active_locomotion_tracks||[]){const tr=row.track;if(tr.kind!=='locoCrowd'||!tr.animation_slots)continue;
      const family=speed>=3?'run':speed>.05?'walk':'idle',slot=tr.animation_slots[family]?.[0];if(!slot)continue;
      rows.push({...row,layer:'locomotionDriver',track:{kind:'animation',offset:`${tr.offset}:${family}`,time_begin:0,time_end:-1,
       animation:slot.animation,animation_hash:slot.animation_hash,start_frame:0,end_frame:-1,priority:0,
       partition_hash:'0x2e7c5ad2600aeb45',animation_type:'Cyclic',animation_type_hash:'0x25f64ae2e13c4a85',
       blend_in_time:.1,blend_out_time:.1,evidence:'decoded LocoCrowdAction animation slot selected from blackboard locomotion speed'}});
    }
    const byPartition=new Map();for(const row of rows){const p=row.track.partition_hash||`unpartitioned:${row.track.offset}`;(byPartition.get(p)||byPartition.set(p,[]).get(p)).push(row);}
    const selected=[];for(const group of byPartition.values()){
      // A scheduled structural AnimationAction owns the Legacy partition over
      // the ambient LocoCrowd driver. Native state output emits both during
      // sprint/attack transitions; averaging them caused invented mixed poses.
      const structural=group.filter(r=>r.layer!=='locomotionDriver');
      const fight=structural.filter(r=>r.layer==='fight'),chosen=fight.length?fight:(structural.length?structural:group);
      // Native blend windows are relative weights inside an animation partition,
      // not weights against the skeleton bind pose. Keep the selected partition
      // fully posed while preserving the decoded relative blend envelopes.
      const raw=chosen.map(sourceBlendWeight),sum=raw.reduce((a,b)=>a+b,0)||1;
      chosen.forEach((r,i)=>{r.runtime_weight=raw[i]/sum;});selected.push(...chosen);
    }
    return selected;
  }
  function sourceBlendWeight(row){const tr=row.track,elapsed=Math.max(0,(row.owner_time||0)-(tr.time_begin||0));let w=1,bi=Number(tr.blend_in_time)||0,bo=Number(tr.blend_out_time)||0;if(bi>0)w=Math.min(w,elapsed/bi);if(bo>0&&Number.isFinite(tr.time_end)&&tr.time_end>=0)w=Math.min(w,Math.max(0,(tr.time_end-(row.owner_time||0))/bo));return Math.max(.001,Math.min(1,w));}
  function actionWeight(row){return Number.isFinite(row.runtime_weight)?row.runtime_weight:1;}
  function reconcile(state){
    const desiredRows=animationRows(state),desired=new Map(desiredRows.map(r=>[`${r.layer}:${r.executor_id}:${r.owner_offset}:${r.track.offset}`,r]));
    for(const [id,rec] of [...active])if(!desired.has(id)){
      const ownerStillActive=(rec.row.layer==='fight'?state.fight?.active_offset:state.locomotion?.active_offset)===rec.row.owner_offset;
      const sameLayerReplacement=desiredRows.some(r=>r.layer===rec.row.layer&&(r.track.partition_hash||'')===(rec.row.track.partition_hash||''));
      const chargedHold=ownerStillActive&&!sameLayerReplacement&&Object.values(state.blackboard?.charge_seconds||{}).some(v=>v>0);
      const holdEnd=ownerStillActive&&!sameLayerReplacement&&(rec.row.track.animation_type==='Hold End Frame'||rec.row.track.animation_type_hash==='0x1b729cb6529f2e87');
      if(chargedHold||holdEnd){
        for(const x of rec.actions)x.action.paused=true;rec.latched=true;
        // The retained source action still owns this partition. Do not start
        // the ambient locomotion driver underneath it and create a mixed pose.
        for(const [otherId,other] of desired)if(other.layer==='locomotionDriver'&&(other.track.partition_hash||'')===(rec.row.track.partition_hash||''))desired.delete(otherId);
        continue;
      }
      stopRecord(rec);active.delete(id);
    }
    for(const [id,row] of desired){
      let rec=active.get(id);if(!rec){rec=startDecodedTrack(row);if(rec)active.set(id,rec);continue;}
      const elapsed=Math.max(0,(row.owner_time||0)-(row.track.time_begin||0));
      for(const x of rec.actions){x.action.paused=false;x.action.setEffectiveWeight(actionWeight(row));const clip=x.action.getClip(),raw=x.sourceStart+elapsed,target=x.cyclic&&clip.duration>0?raw%clip.duration:raw;if(Math.abs(x.action.time-target)>.18)x.action.time=x.cyclic?target:Math.min(target,Math.max(0,clip.duration-1e-6));}
      rec.latched=false;rec.row=row;
    }
  }

  async function setTransformation(name,assembly=latestState?.assembly||{}){
    if(!base)return;
    const id=POWER_IDS[name];
    if(name===currentTransformation&&((!id&&!power)||(power?.prefix===id)))return;
    if(powerLoadName===name&&powerLoadPromise)return powerLoadPromise;
    currentTransformation=name;const generation=++powerGeneration;clearActions();if(power){disposeObject(power.root);power=null;}
    const task=(async()=>{
      let loaded=null;
      if(id){const item=catalog.categories.powers.find(x=>x.id===id);if(item){try{loaded=await fetchPackage(item.entry_path,id);}catch(e){onDiagnostic(`能力组件 ${name} 加载失败：${e.message}`);}}}
      if(generation!==powerGeneration){if(loaded)disposeObject(loaded.root);return;}
      power=loaded;applyBaseVisibility(assembly);
      if(power&&power.meshes.length>1)onDiagnostic(`${name} 已按 TOD drawable/body package 组装；包内更细的组件可见性轨道尚未全部恢复`);
      onStatus(power?`已按 TransformationDescription 组装 ${assembly.drawable||name}${assembly.body_template?` + ${assembly.body_template}`:''}`:`已组装 Alex 基础身体（${name} 没有常驻替换网格或尚未解码）`);
    })();
    powerLoadName=name;powerLoadPromise=task;
    try{await task;}finally{if(powerLoadPromise===task){powerLoadName=null;powerLoadPromise=null;}}
  }

  async function init(){
    catalog=await (await fetch('/api/entities')).json();const item=catalog.categories.powers.find(x=>x.id==='alex');if(!item)throw new Error('Alex entity missing');
    base=await fetchPackage(item.entry_path,'alex');applyBaseVisibility({transformation:'Alex',left_arm_needed:0,body_template:''});
    const box=new THREE.Box3();for(const m of base.meshes){if(!m.visible)continue;m.geometry.computeBoundingBox();box.union(m.geometry.boundingBox);}
    const center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3()).length()||2;
    controls.target.copy(center).add(new THREE.Vector3(0,size*.12,0));camera.position.set(center.x,center.y+size*.48,center.z+size*1.15);controls.minDistance=size*.28;controls.maxDistance=size*3;controls.maxPolarAngle=Math.PI*.48;controls.update();
    onStatus(`真实 Alex 已加载：${base.meshes.length} 个网格、${base.rigs.size} 套骨架、${base.animations.size} 个动画记录`);
  }
  function applyApproximateKinematics(state){
    const now=Number(state?.time);if(!Number.isFinite(now)){lastStateTime=null;return;}
    if(lastStateTime===null){lastStateTime=now;return;}
    if(now<lastStateTime){lastStateTime=now;renderedPosition.set(0,0,0);actorRoot.position.copy(renderedPosition);actorRoot.rotation.y=0;return;}
    lastStateTime=now;
    const speed=Number(state?.blackboard?.velocities?.['Locomotion/XZ'])||0,angle=Number(state?.blackboard?.axis_degrees?.Movement)||0;
    // Blackboard Movement uses +90 for D/right; Three.js positive Y rotation
    // turns the animation's native -Z forward toward -X, so convert handedness.
    if(speed>0)actorRoot.rotation.y=controls.getAzimuthalAngle()-THREE.MathUtils.degToRad(angle);
  }
  function syncState(state){
    applyApproximateKinematics(state);latestState=state;const t=state?.assembly?.transformation||'Alex',id=POWER_IDS[t];applyBaseVisibility(state?.assembly);
    const needs=t!==currentTransformation||(id&&!power&&powerLoadName!==t)||(!id&&!!power);
    if(needs){void setTransformation(t,state.assembly).then(()=>{if(latestState?.assembly?.transformation===t)reconcile(latestState);});return;}
    if(powerLoadName===t)return;
    reconcile(state);
  }
  function frame(){if(disposed)return;requestAnimationFrame(frame);const dt=Math.min(clock.getDelta(),.05);for(const rig of allRigs()){rig.mixer.update(dt);rig.skeleton.update();}
    const worldDelta=new THREE.Vector3(),rootTurn=new THREE.Quaternion(),rootRecords=[...active.values()].filter(r=>r.rootSampler&&!r.latched&&r.actions.length),weightTotal=rootRecords.reduce((s,r)=>s+r.actions[0].action.getEffectiveWeight(),0),normalizer=Math.max(1,weightTotal);
    for(const rec of rootRecords){const x=rec.actions[0],now=x.action.time,d=rootDelta(rec.rootSampler,rec.rootTime,now,x.cyclic),w=x.action.getEffectiveWeight()/normalizer;rec.rootTime=now;d.translation.multiplyScalar(w).applyQuaternion(actorRoot.quaternion);worldDelta.add(d.translation);rootTurn.multiply(new THREE.Quaternion().slerp(d.rotation,w));}
    if(worldDelta.lengthSq()){actorRoot.position.add(worldDelta);renderedPosition.copy(actorRoot.position);}if(Math.abs(rootTurn.w-1)>1e-7)actorRoot.quaternion.multiply(rootTurn).normalize();
    const followDelta=actorRoot.position.clone().sub(lastFollowPosition);if(followDelta.lengthSq()){camera.position.add(followDelta);controls.target.add(followDelta);lastFollowPosition.copy(actorRoot.position);}controls.update();renderer.render(scene,camera);}
  frame();
  return {init,syncState,setTransformation,resize,dispose(){disposed=true;ro.disconnect();clearActions();if(base)disposeObject(base.root);if(power)disposeObject(power.root);renderer.dispose();renderer.domElement.remove();}};
}
