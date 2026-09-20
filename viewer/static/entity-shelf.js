/**
 * Prototype 1 Pure3D Entity & Asset Shelf Viewer
 * Full 5-Category Catalog, 3D Mesh Inspection, Skeleton_2 Bind Pose & Real GPU Animation Playback
 */

import * as THREE from 'three';
import { makeMapTexture, makeMapMaterial } from './map-materials.js';

const CAT_ICONS = {
  powers: '⚡',
  vehicles: '🚗',
  characters: '🧟',
  pedestrians: '🚶',
  props: '🏢'
};

const CATEGORIES = [
  { id: 'powers', name: '主角形态与生化武装', icon: '⚡', desc: 'Alex Mercer 原型体、利爪、刀锋、重锤、充能装甲、地刺与军方/特工伪装全形态' },
  { id: 'vehicles', name: '载具与重装武备系统', icon: '🚗', desc: 'M1A2 艾布拉姆斯主战坦克、黑鹰直升机、阿帕奇武装直升机、装甲运兵车、警车与民用车系' },
  { id: 'characters', name: '剧情角色、变异体与守望军团', icon: '🧟', desc: '达娜·墨瑟、伊丽莎白·格林、克罗斯队长、超级士兵、猎手、九头蛇、至尊猎手及黑色守望军团' },
  { id: 'pedestrians', name: '曼哈顿市民与路人 NPC', icon: '🚶', desc: '商务西装、大衣冬装、休闲便服各年龄段男女市民及初期轻度感染市民' },
  { id: 'props', name: '曼哈顿环境与可破坏道具', icon: '🏢', desc: '屋顶水塔、大型变压器、空调外机、路障、消防栓、垃圾箱与核动力航母等城市动态道具' }
];

const THUMB_CACHE = new Map();

function openSnapshotDB() {
  return new Promise((resolve) => {
    if (!window.indexedDB) return resolve(null);
    const req = window.indexedDB.open('PrototypeEntitySnapshotsDB', 1);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains('snapshots')) {
        db.createObjectStore('snapshots', { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
  });
}

async function loadAllCachedSnapshots() {
  const db = await openSnapshotDB();
  if (!db) return;
  return new Promise((resolve) => {
    const tx = db.transaction(['snapshots'], 'readonly');
    const store = tx.objectStore('snapshots');
    const req = store.getAll();
    req.onsuccess = () => {
      for (const item of req.result || []) {
        if (item.id && item.dataUrl) {
          THUMB_CACHE.set(item.id, item.dataUrl);
        }
      }
      resolve();
    };
    req.onerror = () => resolve();
  });
}

async function getStoredSnapshot(id) {
  if (THUMB_CACHE.has(id)) return THUMB_CACHE.get(id);
  const db = await openSnapshotDB();
  if (!db) return null;
  return new Promise((resolve) => {
    const tx = db.transaction(['snapshots'], 'readonly');
    const store = tx.objectStore('snapshots');
    const req = store.get(id);
    req.onsuccess = () => {
      if (req.result && req.result.dataUrl) {
        THUMB_CACHE.set(id, req.result.dataUrl);
        resolve(req.result.dataUrl);
      } else {
        resolve(null);
      }
    };
    req.onerror = () => resolve(null);
  });
}

async function persistSnapshot(id, dataUrl) {
  THUMB_CACHE.set(id, dataUrl);
  const db = await openSnapshotDB();
  if (!db) return;
  return new Promise((resolve) => {
    const tx = db.transaction(['snapshots'], 'readwrite');
    const store = tx.objectStore('snapshots');
    store.put({ id, dataUrl, updated: Date.now() });
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => resolve(false);
  });
}

async function clearAllSnapshots() {
  THUMB_CACHE.clear();
  const db = await openSnapshotDB();
  if (!db) return;
  return new Promise((resolve) => {
    const tx = db.transaction(['snapshots'], 'readwrite');
    const store = tx.objectStore('snapshots');
    store.clear();
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => resolve(false);
  });
}

export function createEntityShelf({ scene, camera, controls, renderer, onStatus, onSelectEntity }) {
  const entityGroup = new THREE.Group();
  const skeletonGroup = new THREE.Group();
  skeletonGroup.renderOrder = 999;
  scene.add(entityGroup);
  scene.add(skeletonGroup);
  // World-space transient fx (trail ribbons, fx sprites). MUST stay at the
  // scene root with identity transform: its children carry world coordinates
  // computed from bone.getWorldPosition()/localToWorld(), so any parent
  // offset (e.g. clip-continuity shifts applied to skeletonGroup) would
  // displace them off the actor.
  const fxGroup = new THREE.Group();
  fxGroup.name = 'transient-fx';
  scene.add(fxGroup);

  let entityData = null;
  let currentCategory = 'all';
  let currentSearchQuery = '';
  let currentSelectedEntity = null;
  let currentShapeName = null;
  let entityLoadGeneration = 0;

  let renderedMeshList = [];
  let currentAnimations = [];
  let currentAnimIdx = -1;
  let currentAnimTrack = null;
  let isPlayingAnim = false;
  let animTime = 0;
  let showSkeleton = false;
  let focusMode = 'entity';
  // Optional editor-style camera lock. It follows the exact source bone named
  // Pelvis while preserving the viewer's current orbit offset.
  let followPelvisWithCamera = false;
  let lastFollowPelvisWorld = null;

  // A package can assemble several independently named Skeleton_2 rigs.
  // Keep a rig per exact Skin.skeleton_name; never let skeleton list order
  // decide which rig deforms a mesh.
  let activeBones = [];
  let activeSkeleton = null; // first rig only, retained for visualizer compatibility
  let activeRigs = new Map();
  let activeMixers = [];
  let activeActions = [];
  // Kept as the primary action for the existing pause/key-control paths;
  // multi-rig playback itself uses activeActions above.
  let activeAction = null;
  // --- FIG move timeline runtime (see /api/move_timeline provenance docs) ---
  let moveTimeline = null;          // fetched timeline for currentAnimTrack
  let moveTimelineAnim = null;      // animation name the timeline belongs to
  let timelineFired = new Set();    // "tracksOffset:index" fired this loop
  let timelineLastT = 0;            // to detect loop wrap
  const audioUrlCache = new Map();  // event name -> object URL promise
  let fxGeneration = 0;             // bumps on clip switch; stale async audio is dropped
  let combatBanner = null;          // persistent visible state pill
  const moveRunner = { active: false, stages: [], si: 0, frame: 0, fps: 30,
    holdBtn: null, fired: new Set(), graphCache: new Map(), label: '' };
  const fxTextureCache = new Map();  // trail def name -> THREE.Texture promise
  let activeTrails = [];            // { bone, points[], line, untilT }
  let activeFxSprites = [];         // { sprite, untilMs }
  let shakeUntilMs = 0;
  let moveFxEnabled = true;         // toggle: FIG sound/effect playback
  let forcedLoop = null;            // null = follow source cyclic flag
  let mouseCombat = { enabled: false, block: null, moves: null, chain: [], chainIdx: -1, lastClickT: 0 };
  const sandbox = { enabled: false, meshes: [], keys: new Set(), velY: 0,
    grounded: true, hud: null, jumpQueued: false, pos: null,
    cube: { cx: 0, cy: 3, cz: -10, half: 3 }, radius: 0.6 };
  let lastRibbonT = -1;             // ribbon points appended only when time advances
  let shakeUntilAnimT = 0;          // camera shake deadline on animation clock
  let shakeAmpScale = 1;            // decoded preset small/medium/large -> amp
  let boneLinesMesh = null;
  let bonePointsMesh = null;
  let boneLinePairs = [];

  let isBatchScanning = false;

  const offCanvas = document.createElement('canvas');
  offCanvas.width = 256;
  offCanvas.height = 256;
  const offRenderer = new THREE.WebGLRenderer({
    canvas: offCanvas,
    antialias: true,
    alpha: true,
    preserveDrawingBuffer: true
  });
  offRenderer.setSize(256, 256);

  function setVisible(visible) {
    entityGroup.visible = visible;
    skeletonGroup.visible = visible && showSkeleton;
  }

  async function loadCatalog(artPath) {
    onStatus('正在从 art.rcf 读取五大分类实体清单…');
    try {
      await loadAllCachedSnapshots();
      const resp = await fetch(`/api/entities?path=${encodeURIComponent(artPath || '')}`);
      const data = await resp.json();
      if (data.error) {
        onStatus('读取实体清单失败：' + data.error);
        return;
      }
      if (data.categories && typeof data.categories === 'object') {
        for (const [k, v] of Object.entries(data.categories)) {
          data[k] = v;
        }
      }
      entityData = data;
      renderCounts(data.counts);
      updateStatsBadge();
      renderGrid();
      onStatus(`已索引五大分类共 ${data.total_entities} 个实体 (已恢复 ${THUMB_CACHE.size} 个持久化快照)`);
    } catch (err) {
      onStatus('请求实体清单失败：' + err.message);
    }
  }

  function renderCounts(counts) {
    if (!counts) return;
    for (const [cat, count] of Object.entries(counts)) {
      const badge = document.getElementById(`sec-badge-${cat}`);
      if (badge) badge.textContent = `${count} 款`;
      const countEl = document.getElementById(`count-${cat}`);
      if (countEl) countEl.textContent = `${count}`;
    }
  }

  function updateStatsBadge() {
    const badge = document.getElementById('snapshot-stats-badge');
    if (!badge || !entityData) return;
    const total = entityData.total_entities || 0;
    const cached = THUMB_CACHE.size;
    badge.textContent = `已缓存: ${cached} / ${total}`;
    badge.style.color = cached >= total && total > 0 ? '#4ade80' : '#94a3b8';
  }

  function floats(b64, type = Float32Array, stride = 4) {
    if (!b64) return new type(0);
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new type(bytes.buffer);
  }

  // Build one real Three.js hierarchy per exact Skeleton_2 declaration.
  // Skin binding is by mesh.skeleton_name, not by list position or joint count.
  function buildSkeletonHierarchy(skeletons) {
    while (skeletonGroup.children.length > 0) {
      const c = skeletonGroup.children[0];
      skeletonGroup.remove(c);
      if (c.geometry) c.geometry.dispose();
      if (c.material) c.material.dispose();
    }
    activeBones = [];
    activeSkeleton = null;
    activeRigs = new Map();
    boneLinesMesh = null;
    bonePointsMesh = null;
    boneLinePairs = [];

    if (!skeletons || !skeletons.length) return;

    for (const skel of skeletons) {
      const skeletonName = skel?.name;
      const joints = skel?.joints || [];
      if (!skeletonName || !joints.length || activeRigs.has(skeletonName)) continue;

      const rigRoot = new THREE.Group();
      rigRoot.name = `rig:${skeletonName}`;
      rigRoot.userData.skeletonName = skeletonName;
      skeletonGroup.add(rigRoot);

      const bones = [];
      for (let i = 0; i < joints.length; i++) {
        const j = joints[i];
        const bone = new THREE.Bone();
        bone.name = j.name || `Joint_${i}`;

        // Skeleton_Joint_2 stores the row-major / row-vector P3D matrix.
        // Matrix4.fromArray's column-major representation is its exact
        // transpose, which is the equivalent Three.js column-vector affine
        // transform. No numeric conversion or display rounding is applied.
        const m = new THREE.Matrix4();
        if (j.matrix && j.matrix.length === 16) m.fromArray(j.matrix);
        m.decompose(bone.position, bone.quaternion, bone.scale);

        bone.userData = {
          origPos: bone.position.clone(),
          origRot: bone.quaternion.clone(),
          origScale: bone.scale.clone(),
          parentIdx: j.parent,
          index: i,
          name: (j.name || '').toLowerCase(),
          skeletonName
        };
        bones.push(bone);
      }

      for (let i = 0; i < joints.length; i++) {
        const parentIndex = joints[i].parent;
        if (parentIndex >= 0 && parentIndex < bones.length && parentIndex !== i) {
          bones[parentIndex].add(bones[i]);
          boneLinePairs.push({ child: bones[i], parent: bones[parentIndex] });
        } else {
          rigRoot.add(bones[i]);
        }
      }

      rigRoot.updateMatrixWorld(true);
      const boneInverses = bones.map(bone => bone.matrixWorld.clone().invert());
      const skeleton = new THREE.Skeleton(bones, boneInverses);
      const rig = { name: skeletonName, root: rigRoot, bones, skeleton, sourceEntry: skel.source_entry || null };
      activeRigs.set(skeletonName, rig);
      activeBones.push(...bones);
      if (!activeSkeleton) activeSkeleton = skeleton;
    }

    // Create one overlay for all rigs.  Its position buffers are updated from
    // the actual bones, so a donor skeleton stays inspectable without being
    // reparented under a mesh.
    if (boneLinePairs.length > 0) {
      const linePositions = new Float32Array(boneLinePairs.length * 6);
      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.BufferAttribute(linePositions, 3));
      const mat = new THREE.LineBasicMaterial({
        color: 0xff7722,
        linewidth: 2,
        depthTest: false,
        transparent: true,
        opacity: 0.95
      });
      boneLinesMesh = new THREE.LineSegments(geom, mat);
      // Root motion can move a rig outside its bind-pose bounding box. The
      // inspection overlay must never vanish due to Three's frustum shortcut.
      boneLinesMesh.frustumCulled = false;
      boneLinesMesh.renderOrder = 999;
      skeletonGroup.add(boneLinesMesh);
    }

    const nodePositions = new Float32Array(activeBones.length * 3);
    const nodeGeom = new THREE.BufferGeometry();
    nodeGeom.setAttribute('position', new THREE.BufferAttribute(nodePositions, 3));
    const nodeMat = new THREE.PointsMaterial({
      color: 0x7fd4ff,
      size: 6,
      sizeAttenuation: false,
      depthTest: false,
      transparent: true,
      opacity: 0.9
    });
    bonePointsMesh = new THREE.Points(nodeGeom, nodeMat);
    bonePointsMesh.frustumCulled = false;
    bonePointsMesh.renderOrder = 1000;
    skeletonGroup.add(bonePointsMesh);

    skeletonGroup.visible = showSkeleton;
  }

  function updateSkeletonVisualizerPositions() {
    if (!activeBones.length) return;

    // The overlay meshes are direct children of skeletonGroup. Bones report
    // world coordinates, but those must never be written straight into an
    // overlay buffer: whenever clip continuity moves skeletonGroup, its own
    // transform would be applied a second time and leave the lines behind (or
    // ahead of) the SkinnedMesh. Convert every sampled bone point back into
    // the overlay parent's local space before storing it.
    skeletonGroup.updateMatrixWorld(true);
    const toOverlayLocal = (worldPoint) => skeletonGroup.worldToLocal(worldPoint);

    if (boneLinesMesh && boneLinePairs.length > 0) {
      const posAttr = boneLinesMesh.geometry.getAttribute('position');
      const arr = posAttr.array;
      const v0 = new THREE.Vector3();
      const v1 = new THREE.Vector3();

      for (let i = 0; i < boneLinePairs.length; i++) {
        const pair = boneLinePairs[i];
        pair.parent.getWorldPosition(v0);
        pair.child.getWorldPosition(v1);
        toOverlayLocal(v0);
        toOverlayLocal(v1);

        arr[i * 6 + 0] = v0.x;
        arr[i * 6 + 1] = v0.y;
        arr[i * 6 + 2] = v0.z;
        arr[i * 6 + 3] = v1.x;
        arr[i * 6 + 4] = v1.y;
        arr[i * 6 + 5] = v1.z;
      }
      posAttr.needsUpdate = true;
    }

    if (bonePointsMesh) {
      const posAttr = bonePointsMesh.geometry.getAttribute('position');
      const arr = posAttr.array;
      const v = new THREE.Vector3();

      for (let i = 0; i < activeBones.length; i++) {
        activeBones[i].getWorldPosition(v);
        toOverlayLocal(v);
        arr[i * 3 + 0] = v.x;
        arr[i * 3 + 1] = v.y;
        arr[i * 3 + 2] = v.z;
      }
      posAttr.needsUpdate = true;
    }
  }

  function getPelvisWorldPosition() {
    // "Pelvis" is a directly declared joint in the currently audited player,
    // disguise and character skeletons. Do not substitute a nearby root or
    // an index when a package has no such joint.
    for (const rig of activeRigs.values()) {
      const pelvis = rig.bones.find(bone => bone.name === 'Pelvis');
      if (!pelvis) continue;
      skeletonGroup.updateMatrixWorld(true);
      return pelvis.getWorldPosition(new THREE.Vector3());
    }
    return null;
  }

  function shiftActorWorldPosition(delta) {
    if (!delta || delta.lengthSq() === 0) return;
    // Meshes and skeleton roots are siblings; move both by precisely the same
    // delta so the source skin bind remains unchanged. This translation is an
    // internal clip-continuity correction: after it, Pelvis is deliberately at
    // the same world position as before the switch. It must therefore NOT also
    // translate camera/target; ordinary per-frame Pelvis motion is handled by
    // updatePelvisCameraFollow().
    entityGroup.position.add(delta);
    skeletonGroup.position.add(delta);
    entityGroup.updateMatrixWorld(true);
    skeletonGroup.updateMatrixWorld(true);
    // A switch can happen while playback is paused, so do not wait for the
    // next animation tick before realigning an already-visible skeleton.
    updateSkeletonVisualizerPositions();
  }

  function setPelvisCameraFollow(enabled, announce = true) {
    const pelvisWorld = getPelvisWorldPosition();
    if (enabled && !pelvisWorld) {
      followPelvisWithCamera = false;
      lastFollowPelvisWorld = null;
      if (announce) onStatus('当前实体没有名为 Pelvis 的胯骨，无法开启镜头跟随。');
      return false;
    }
    followPelvisWithCamera = enabled;
    lastFollowPelvisWorld = enabled ? pelvisWorld : null;
    if (announce) onStatus(enabled ? '镜头已跟随胯骨；可继续用鼠标绕角色观察。' : '已关闭胯骨镜头跟随。');
    return true;
  }

  function updatePelvisCameraFollow() {
    if (!followPelvisWithCamera) return;
    const pelvisWorld = getPelvisWorldPosition();
    if (!pelvisWorld) {
      followPelvisWithCamera = false;
      lastFollowPelvisWorld = null;
      const checkbox = document.getElementById('toggle-pelvis-camera-follow');
      if (checkbox) checkbox.checked = false;
      return;
    }
    if (lastFollowPelvisWorld) {
      const delta = pelvisWorld.clone().sub(lastFollowPelvisWorld);
      if (delta.lengthSq() > 0) {
        camera.position.add(delta);
        controls.target.add(delta);
      }
    }
    lastFollowPelvisWorld = pelvisWorld;
    controls.update();
  }

  function stopActiveAnimation() {
    moveRunner.active = false;
    for (const mixer of activeMixers) {
      mixer.stopAllAction();
      mixer.uncacheRoot(mixer.getRoot());
    }
    activeMixers = [];
    activeActions = [];
    activeAction = null;
    isPlayingAnim = false;
    clearMoveTimelineRuntime();
  }

  function clearMoveTimelineRuntime() {
    fxGeneration += 1;
    timelineFired = new Set();
    timelineLastT = 0;
    for (const tr of activeTrails) {
      const obj = tr.mesh || tr.line;
      if (obj) { fxGroup.remove(obj); obj.geometry.dispose(); obj.material.dispose(); }
    }
    activeTrails = [];
    for (const fx of activeFxSprites) {
      if (fx.sprite) { fxGroup.remove(fx.sprite); fx.sprite.material.dispose(); }
    }
    activeFxSprites = [];
    shakeUntilMs = 0;
    shakeUntilAnimT = 0;
    lastRibbonT = -1;
  }

  function findBoneByName(name) {
    if (!name) return null;
    for (const rig of activeRigs.values()) {
      for (const bone of rig.bones) {
        if (bone.name === name) return bone;
      }
    }
    return null;
  }

  async function loadMoveTimeline(animName) {
    moveTimeline = null;
    moveTimelineAnim = animName;
    const gen = fxGeneration;
    try {
      const res = await fetch(`/api/move_timeline?anim=${encodeURIComponent(animName)}`);
      if (!res.ok) return;
      const data = await res.json();
      // A newer selection may have raced this fetch; only accept if current.
      if (moveTimelineAnim !== animName || gen !== fxGeneration) return;
      moveTimeline = (data && data.found) ? data : null;
      if (moveTimeline) {
        const n = moveTimeline.usages.reduce((a, u) => a + u.events.length, 0);
        onStatus(`已加载 FIG 招式时间轴：${animName}（${moveTimeline.usages.length} 个阶段，${n} 个事件；音效/特效标记按原始帧时刻触发）`);
      }
    } catch (err) { /* timeline is optional; playback continues without it */ }
  }

  function playAudioEvent(eventName) {
    if (!eventName || eventName.startsWith('0x')) return; // unresolved hash: skip, never guess
    const gen = fxGeneration; // drop if the clip changed before decode finished
    const variant = Math.floor(Math.random() * 3); // Patch files list has 1-3 variants
    const key = `${eventName}:${variant}`;
    let urlPromise = audioUrlCache.get(key);
    if (!urlPromise) {
      urlPromise = fetch(`/api/audio_event?event=${encodeURIComponent(eventName)}&variant=${variant}`)
        .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.blob(); })
        .then(b => URL.createObjectURL(b));
      audioUrlCache.set(key, urlPromise);
    }
    urlPromise.then(url => {
      if (gen !== fxGeneration) return; // stale: user switched clips
      const a = new Audio(url); a.volume = 0.9; void a.play();
    }).catch(() => audioUrlCache.delete(key));
  }

  function loadFxTexture(defName) {
    let prom = fxTextureCache.get(defName);
    if (!prom) {
      prom = new Promise((resolve, reject) => {
        const loader = new THREE.TextureLoader();
        loader.load(`/api/fx_texture?name=${encodeURIComponent(defName)}`, (tex) => {
          tex.wrapS = THREE.RepeatWrapping;
          tex.wrapT = THREE.ClampToEdgeWrapping;
          resolve(tex);
        }, undefined, reject);
      });
      fxTextureCache.set(defName, prom);
    }
    return prom;
  }

  function spawnTrail(item, untilT) {
    // item: { bone, edge_a, edge_b, def, template, texture } from the FIG
    // motionTrail record (edge offsets are bone-local; template/texture from
    // the resolved 0x11015 shader chain in startup_effects.p3d).
    const bone = findBoneByName(item.bone || item);
    if (!bone) return;
    const edgeA = new THREE.Vector3(...(item.edge_a || [0, 0.2, 0]));
    const edgeB = new THREE.Vector3(...(item.edge_b || [0, -0.2, 0]));
    const maxSeg = 40;
    const geom = new THREE.BufferGeometry();
    const positions = new Float32Array(maxSeg * 2 * 3);
    const uvs = new Float32Array(maxSeg * 2 * 2);
    geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geom.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
    const idx = [];
    for (let i = 0; i < maxSeg - 1; i++) {
      const a = i * 2, b = i * 2 + 1, c = i * 2 + 2, d = i * 2 + 3;
      idx.push(a, b, c, b, d, c);
    }
    geom.setIndex(idx);
    geom.setDrawRange(0, 0);
    const isAdd = item.template === 'fx_add';
    const mat = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      side: THREE.DoubleSide,
      transparent: true,
      depthWrite: false,
      blending: isAdd ? THREE.AdditiveBlending : THREE.NormalBlending,
      opacity: 0.92,
    });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.frustumCulled = false;
    fxGroup.add(mesh);
    const trail = { bone, edgeA, edgeB, pairs: [], mesh, geom, maxSeg, untilT };
    activeTrails.push(trail);
    if (item.def) {
      loadFxTexture(item.def).then(tex => { mat.map = tex; mat.needsUpdate = true; })
        .catch(() => {});
    }
  }

  function spawnFxSprite(boneName, label) {
    const bone = findBoneByName(boneName) || findBoneByName('Pelvis');
    if (!bone) return;
    const cv = document.createElement('canvas');
    cv.width = 128; cv.height = 128;
    const ctx = cv.getContext('2d');
    const grad = ctx.createRadialGradient(64, 64, 4, 64, 64, 60);
    grad.addColorStop(0, 'rgba(255,200,120,0.95)');
    grad.addColorStop(1, 'rgba(255,80,20,0)');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 128, 128);
    const tex = new THREE.CanvasTexture(cv);
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.setScalar(0.45);
    bone.getWorldPosition(sprite.position);
    fxGroup.add(sprite);
    activeFxSprites.push({ sprite, bone, untilAnimT: animTime + 0.65 });
    if (label) onStatus(`💥 特效触发（占位标记，粒子参数未解码）：${label}`);
  }

  function fireTimelineEvent(ev) {
    if (ev.kind === 'sound') {
      playAudioEvent(ev.event);
      if (ev.event && !ev.event.startsWith('0x')) onStatus(`🔊 音效事件：${ev.event}（原始 RADP 解码）`);
    } else if (ev.kind === 'spawn' && ev.payload) {
      let trailUntil = (currentAnimTrack && currentAnimTrack.duration) || 2.0;
      for (const item of ev.payload) {
        if (item.kind === 'motionTrail') spawnTrail(item, trailUntil);
        else if (item.kind === 'effect') spawnFxSprite(item.bone, `${item.emitter}${item.bone ? ' @ ' + item.bone : ''}`);
        else if (item.kind === 'sound') playAudioEvent(item.event);
      }
    } else if (ev.kind === 'cameraShake') {
      const scale = ev.preset === 'large' ? 2.2 : (ev.preset === 'medium' ? 1.4 : 1.0);
      shakeUntilAnimT = animTime + 0.32 * scale;
      shakeAmpScale = scale;
    } else if (ev.kind === 'hit') {
      onStatus(`👊 命中判定帧（hit record，判定盒参数未解码）`);
    }
  }

  function updateMoveTimeline() {
    if (!moveFxEnabled || !moveTimeline || !activeAction || !currentAnimTrack) return;
    if (moveTimeline.animation !== currentAnimTrack.name) return;
    const fps = moveTimeline.fps || 30;
    const t = activeAction.time;
    if (t < timelineLastT - 0.05) timelineFired = new Set(); // loop wrapped
    timelineLastT = t;
    for (const usage of moveTimeline.usages) {
      const stageStart = (usage.start_frame > 0 ? usage.start_frame : 0) / fps;
      usage.events.forEach((ev, idx) => {
        if (ev.kind === 'execute' || ev.kind === 'motionState') return; // engine-internal, no viewer action
        const abs = stageStart + (ev.t || 0);
        const key = `${usage.tracks_offset}:${idx}`;
        if (t >= abs && t - abs < 0.25 && !timelineFired.has(key)) {
          timelineFired.add(key);
          fireTimelineEvent(ev);
        }
      });
    }
    // advance trails: append the two edge points (bone-local offsets from the
    // FIG record transformed to world space) and rebuild the ribbon strip.
    // Points are only appended while the action time advances; a clamped
    // (finished) one-shot clip must not collapse the ribbon into a blob.
    const timeAdvanced = t !== lastRibbonT;
    lastRibbonT = t;
    for (const tr of activeTrails) {
      if (!timeAdvanced) break;
      const pa = tr.edgeA.clone();
      const pb = tr.edgeB.clone();
      tr.bone.localToWorld(pa);
      tr.bone.localToWorld(pb);
      tr.pairs.push([pa, pb]);
      if (tr.pairs.length > tr.maxSeg) tr.pairs.shift();
      const n = tr.pairs.length;
      if (n >= 2) {
        const pos = tr.geom.attributes.position.array;
        const uv = tr.geom.attributes.uv.array;
        for (let i = 0; i < n; i++) {
          const [a, b] = tr.pairs[i];
          pos[i * 6 + 0] = a.x; pos[i * 6 + 1] = a.y; pos[i * 6 + 2] = a.z;
          pos[i * 6 + 3] = b.x; pos[i * 6 + 4] = b.y; pos[i * 6 + 5] = b.z;
          const u = i / (n - 1);
          uv[i * 4 + 0] = u; uv[i * 4 + 1] = 0;
          uv[i * 4 + 2] = u; uv[i * 4 + 3] = 1;
        }
        tr.geom.attributes.position.needsUpdate = true;
        tr.geom.attributes.uv.needsUpdate = true;
        tr.geom.setDrawRange(0, (n - 1) * 6);
      }
    }
    activeTrails = activeTrails.filter(tr => {
      if (t > tr.untilT + 0.3 || !isPlayingAnim) {
        fxGroup.remove(tr.mesh); tr.geom.dispose(); tr.mesh.material.dispose();
        return false;
      }
      return true;
    });
    // fx sprites follow their bone briefly, then fade out on the animation
    // clock so pausing freezes them in place.
    activeFxSprites = activeFxSprites.filter(fx => {
      if (animTime > fx.untilAnimT) {
        fxGroup.remove(fx.sprite); fx.sprite.material.dispose();
        return false;
      }
      fx.bone.getWorldPosition(fx.sprite.position);
      fx.sprite.material.opacity = Math.max(0, (fx.untilAnimT - animTime) / 0.65);
      return true;
    });
    // camera shake on the animation clock (paused animation = no shake)
    if (animTime < shakeUntilAnimT) {
      const amp = 0.035 * shakeAmpScale * ((shakeUntilAnimT - animTime) / 0.32);
      camera.position.x += (Math.random() - 0.5) * amp;
      camera.position.y += (Math.random() - 0.5) * amp;
      camera.position.z += (Math.random() - 0.5) * amp;
    }
  }

  function playAnimationClip(animTrack) {
    if (!animTrack || activeRigs.size === 0) return;
    // When moving from one playable clip to another, keep the source Pelvis at
    // its current world location. The new clip remains completely unmodified;
    // only the viewer's enclosing actor position receives the transition delta.
    // On first load there is no prior action, so the source clip starts at its
    // own raw first key rather than being silently re-based.
    const previousPelvisWorld = activeActions.length > 0 ? getPelvisWorldPosition() : null;
    stopActiveAnimation();

    // A separate mixer per rig makes repeated bone names deterministic: a
    // QuaternionKeyframeTrack resolves inside that rig's root only.  Applying
    // a group to another rig requires an exact joint-name match; no group ID
    // offset or closest-bone fallback is permitted.
    for (const rig of activeRigs.values()) {
      const boneMap = new Map();
      for (const bone of rig.bones) {
        if (!boneMap.has(bone.name)) boneMap.set(bone.name, bone);
        const lower = bone.name.toLowerCase();
        if (!boneMap.has(lower)) boneMap.set(lower, bone);
      }

      const tracks = [];
      for (const group of animTrack.groups || []) {
        const bone = boneMap.get(group.name) || boneMap.get(group.name.toLowerCase());
        if (!bone) continue;
        if (group.rot) {
          const times = Float32Array.from(group.rot.times);
          const values = Float32Array.from(group.rot.values.flat());
          if (times.length > 0 && values.length === times.length * 4) {
            tracks.push(new THREE.QuaternionKeyframeTrack(`${bone.name}.quaternion`, times, values));
          }
        }
        // ``pos`` is the decoded animation translation channel, including
        // Motion_Root. It is deliberately played as source values: no root
        // lock, initial-sample subtraction, axis conversion, or clipping.
        if (group.pos) {
          const positionTimes = Float32Array.from(group.pos.times);
          const positionValues = Float32Array.from(group.pos.values.flat());
          if (positionTimes.length > 0 && positionValues.length === positionTimes.length * 3) {
            tracks.push(new THREE.VectorKeyframeTrack(`${bone.name}.position`, positionTimes, positionValues));
          }
        }
        if (group.scale) {
          const scaleTimes = Float32Array.from(group.scale.times);
          const scaleValues = Float32Array.from(group.scale.values.flat());
          if (scaleTimes.length > 0 && scaleValues.length === scaleTimes.length * 3) {
            tracks.push(new THREE.VectorKeyframeTrack(`${bone.name}.scale`, scaleTimes, scaleValues));
          }
        }
      }

      if (tracks.length > 0) {
        const clip = new THREE.AnimationClip(animTrack.name, animTrack.duration, tracks);
        const mixer = new THREE.AnimationMixer(rig.root);
        const action = mixer.clipAction(clip);
        const loopOn = (forcedLoop === null) ? !!animTrack.cyclic : forcedLoop;
        action.setLoop(loopOn ? THREE.LoopRepeat : THREE.LoopOnce);
        action.clampWhenFinished = !loopOn;
        action.play();
        activeMixers.push(mixer);
        activeActions.push(action);
      }
    }

    // Apply the new clip's exact t=0 keys before comparing its Pelvis to the
    // outgoing pose. This prevents changing clips from snapping the actor back
    // to the viewer's world origin while retaining raw root motion afterwards.
    for (const mixer of activeMixers) mixer.update(0);
    for (const rig of activeRigs.values()) rig.skeleton.update();
    if (previousPelvisWorld && activeActions.length > 0) {
      const newPelvisWorld = getPelvisWorldPosition();
      if (newPelvisWorld) shiftActorWorldPosition(previousPelvisWorld.clone().sub(newPelvisWorld));
    }
    // Always refresh the world-space overlay here as well. In particular,
    // switching to a non-looping/paused clip must not leave its prior line
    // buffer on screen until another frame happens to animate.
    updateSkeletonVisualizerPositions();
    if (followPelvisWithCamera) lastFollowPelvisWorld = getPelvisWorldPosition();

    activeAction = activeActions[0] || null;
    isPlayingAnim = activeActions.length > 0;

    // FIG move timeline: fetch (or reuse) the byte-verified event schedule
    // for this exact animation name. Optional; playback works without it.
    clearMoveTimelineRuntime();
    if (isPlayingAnim && animTrack.name) {
      if (moveTimelineAnim !== animTrack.name || !moveTimeline) {
        void loadMoveTimeline(animTrack.name);
      }
    }
  }

  function setPlayButtonState(playing) {
    const playBtn = document.getElementById('anim-play-btn');
    if (playBtn) {
      playBtn.textContent = playing ? '⏸ 暂停' : '▶ 播放 (Space)';
      playBtn.style.background = playing ? '#4c1d95' : '#1a2230';
    }
  }

  function currentClipDuration() {
    return (currentAnimTrack && currentAnimTrack.duration) || 0;
  }

  function seekAnimationTo(t) {
    if (!activeActions.length) return;
    // Dragging the bar pauses at that exact frame; effects freeze with it.
    isPlayingAnim = false;
    for (const action of activeActions) { action.paused = false; action.time = t; }
    for (const mixer of activeMixers) mixer.update(0);
    for (const action of activeActions) action.paused = true;
    for (const rig of activeRigs.values()) rig.skeleton.update();
    updateSkeletonVisualizerPositions();
    if (followPelvisWithCamera) lastFollowPelvisWorld = getPelvisWorldPosition();
    setPlayButtonState(false);
    // Rebuild the fired-set: everything scheduled before the seek point is
    // marked as already fired so resuming does not burst-replay old events.
    timelineFired = new Set();
    timelineLastT = t;
    lastRibbonT = -1;
    if (moveTimeline) {
      const fps = moveTimeline.fps || 30;
      for (const usage of moveTimeline.usages) {
        const stageStart = (usage.start_frame > 0 ? usage.start_frame : 0) / fps;
        usage.events.forEach((ev, idx) => {
          if (stageStart + (ev.t || 0) <= t) timelineFired.add(`${usage.tracks_offset}:${idx}`);
        });
      }
    }
    // transient fx do not survive an arbitrary seek
    for (const tr of activeTrails) { fxGroup.remove(tr.mesh); tr.geom.dispose(); tr.mesh.material.dispose(); }
    activeTrails = [];
    for (const fx of activeFxSprites) { fxGroup.remove(fx.sprite); fx.sprite.material.dispose(); }
    activeFxSprites = [];
    shakeUntilAnimT = 0;
    updateProgressBar(t);
  }

  function updateProgressBar(t) {
    const bar = document.getElementById('anim-progress');
    const label = document.getElementById('anim-time-label');
    const dur = currentClipDuration();
    if (bar && dur > 0 && document.activeElement !== bar) {
      bar.value = String(Math.min(1000, Math.round((t / dur) * 1000)));
    }
    if (label && dur > 0) {
      label.textContent = `${t.toFixed(2)} / ${dur.toFixed(2)}s`;
    }
  }

  function figBlockForEntity() {
    // Generic mapping: alex_<power> package -> prototype_<power> FIG block;
    // base alex -> prototype. Returns null when no块 exists (UI says so).
    const path = (currentSelectedEntity && currentSelectedEntity.entry_path || '').toLowerCase();
    const m = path.match(/alex_([a-z]+)/);
    if (m) return 'prototype_' + m[1];
    if (path.includes('\\alex\\') || path.endsWith('alex.p3d.rz')) return 'prototype';
    return null;
  }

  async function enableMouseCombat(btn) {
    const block = figBlockForEntity();
    if (!block) {
      onStatus('🖱 此实体没有可推导的 FIG 招式块（仅主角能力形态支持招式模拟）');
      return;
    }
    btn.textContent = '⏳';
    try {
      const res = await fetch(`/api/input_moves?block=${encodeURIComponent(block)}`);
      const data = await res.json();
      if (!data.found || !data.moves.length) {
        onStatus(`🖱 FIG 块 ${block} 中没有输入触发的招式记录`);
        btn.textContent = '🖱';
        return;
      }
      mouseCombat.block = block;
      mouseCombat.moves = data.moves;
      mouseCombat.chain = data.moves
        .filter(m => m.button === 'Attack' && m.state === 'Pressed' && m.sequence_id != null)
        .sort((x, y) => x.sequence_id - y.sequence_id);
      mouseCombat.chainIdx = -1;
      mouseCombat.enabled = true;
      updateCombatBanner();
      btn.textContent = '🖱✓';
      btn.style.color = '#7fd4ff';
      btn.style.borderColor = '#7fd4ff';
      const nAtk = mouseCombat.chain.length;
      const nSpc = data.moves.filter(m => m.button === 'Special').length;
      const nAct = data.moves.filter(m => m.button === 'Action').length;
      onStatus(`🖱 状态树驱动已开启（${block}）：左键=Attack 连段（${nAtk} 式）· 右键=Special（${nSpc} 式）· E=Action（${nAct} 式）· 按住≥0.25s 触发蓄力式（按解码的 hold 阈值匹配）。过渡按 FIG hit 帧门控，早按会缓冲——近似 playbackState(final) 仲裁。`);
    } catch (err) {
      onStatus('🖱 招式列表加载失败：' + err);
      btn.textContent = '🖱';
    }
  }

  function playMoveAnim(animName, label) {
    const idx = (currentAnimations || []).findIndex(t => t.name === animName && t.playable !== false);
    if (idx < 0) {
      onStatus(`🖱 ${label}: 该形态包中找不到动画 ${animName}（FIG 引用了但当前实体没有此动画轨道）`);
      return false;
    }
    forcedLoop = false; // moves are one-shot; playAnimationClip will honour this
    playAnimationIndex(idx);
    onStatus(`🖱 ${label} → ${animName}`);
    return true;
  }

  function canTransitionNow() {
    if (moveRunner.active) {
      // allow chaining once the current stage set has passed its hit event
      // (any stage with a hit already fired), or when in the final stage.
      if (moveRunner.si >= moveRunner.stages.length - 1) return true;
      for (const k of moveRunner.fired) return true; // some event fired
      const stg = moveRunner.stages[moveRunner.si];
      const hits = stg.events.filter(e => e.kind === 'hit');
      if (!hits.length) return moveRunner.si > 0;
      return false;
    }
    // State-tree gate: a new move may start once the current one passed its
    // FIG hit-event time (impact frame). Approximates the engine's
    // playbackState(final)/opportunity arbitration; declared as such in UI.
    if (!isPlayingAnim || !activeAction || !currentAnimTrack) return true;
    const dur = currentClipDuration();
    if (dur <= 0) return true;
    let gate = dur * 0.6;
    if (moveTimeline && moveTimeline.animation === currentAnimTrack.name) {
      const fps = moveTimeline.fps || 30;
      for (const u of moveTimeline.usages) {
        for (const ev of u.events) {
          if (ev.kind === 'hit') {
            gate = Math.min(gate, (u.start_frame > 0 ? u.start_frame : 0) / fps + (ev.t || 0));
          }
        }
      }
    }
    return activeAction.time >= gate;
  }

  function combatTrigger(btnName, held) {
    if (!mouseCombat.enabled || !mouseCombat.moves) return false;
    let mv = null;
    let label = '';
    if (held >= 0.25) {
      // charged press: if the runner is already looping the hold stage for
      // this button, releasing simply lets it proceed to the release stage.
      if (moveRunner.active && moveRunner.holdBtn === btnName) {
        updateCombatBanner('释放！');
        return true;
      }
      const ups = mouseCombat.moves
        .filter(m => m.button === btnName && (m.state === 'Up' || m.state === 'Released')
                && held >= (m.hold_seconds || 0))
        .sort((x, y) => (y.hold_seconds || 0) - (x.hold_seconds || 0));
      if (ups.length) {
        mv = ups[0];
        label = `${btnName} 蓄力${held.toFixed(2)}s (seq ${mv.sequence_id ?? '-'})`;
      }
    }
    if (!mv) {
      const chain = mouseCombat.moves
        .filter(m => m.button === btnName && m.state === 'Pressed' && m.sequence_id != null)
        .sort((x, y) => x.sequence_id - y.sequence_id);
      if (!chain.length) return false;
      const now = performance.now();
      mouseCombat.chainIdxMap = mouseCombat.chainIdxMap || {};
      const cont = isPlayingAnim && mouseCombat.lastBtn === btnName
        && (now - (mouseCombat.lastClickT || 0)) < 2600;
      let idx = cont ? ((mouseCombat.chainIdxMap[btnName] ?? -1) + 1) : 0;
      if (idx >= chain.length) idx = 0;
      mouseCombat.chainIdxMap[btnName] = idx;
      mv = chain[idx];
      label = `${btnName} 连段 ${idx + 1}/${chain.length} (seq ${mv.sequence_id})`;
    }
    mouseCombat.lastClickT = performance.now();
    mouseCombat.lastBtn = btnName;
    if (canTransitionNow() || !moveRunner.active) {
      void startMoveRun(mv, label, btnName);
      return true;
    }
    mouseCombat.pending = { mv, label: label + ' [已缓冲，至过渡帧触发]' };
    onStatus(`⏳ ${label} 已缓冲：等待当前招式到过渡帧（FIG hit 时刻）`);
    return true;
  }

  async function fetchMoveGraph(block, bankOffset) {
    const key = `${block}:${bankOffset}`;
    let prom = moveRunner.graphCache.get(key);
    if (!prom) {
      prom = fetch(`/api/move_graph?block=${encodeURIComponent(block)}&bank=${bankOffset}`)
        .then(r => r.json());
      moveRunner.graphCache.set(key, prom);
    }
    return prom;
  }

  function runnerSetPose(frame) {
    // Drive the existing clip action directly by time; mixers update in the
    // normal per-frame path with dt=0 handled by paused actions.
    if (!activeAction) return;
    activeAction.paused = true;
    activeAction.time = Math.min(frame / moveRunner.fps,
      Math.max(0, (currentClipDuration() || 1e9) - 1e-4));
  }

  async function startMoveRun(mv, label, holdBtn) {
    const graph = await fetchMoveGraph(mouseCombat.block, mv.bank_offset);
    if (!graph.found || !graph.stages.length) {
      // no stage data -> fall back to plain clip playback
      return playMoveAnim(mv.primary_anim, label);
    }
    // load the clip (paused; runner drives time)
    const idx = (currentAnimations || []).findIndex(t => t.name === graph.stages[0].anim && t.playable !== false);
    if (idx < 0) return playMoveAnim(mv.primary_anim, label);
    forcedLoop = false;
    playAnimationIndex(idx);
    moveRunner.active = true;
    moveRunner.stages = graph.stages;
    moveRunner.si = 0;
    moveRunner.fps = graph.stages[0].fps || 30;
    moveRunner.frame = graph.stages[0].start_frame > 0 ? graph.stages[0].start_frame : 0;
    moveRunner.holdBtn = holdBtn || null;
    moveRunner.fired = new Set();
    moveRunner.label = label;
    isPlayingAnim = true; // keep update loop alive; action itself is paused
    runnerSetPose(moveRunner.frame);
    onStatus(`▶ ${label}：阶段机启动（${graph.stages.length} 段，帧窗驱动）`);
  }

  function runnerFireStageEvents(stg, stageT, prevT) {
    for (let i = 0; i < stg.events.length; i++) {
      const ev = stg.events[i];
      if (ev.kind === 'execute') continue;
      const key = `${moveRunner.si}:${i}`;
      if (ev.t > prevT && ev.t <= stageT && !moveRunner.fired.has(key)) {
        moveRunner.fired.add(key);
        if (ev.kind === 'sound' && ev.verb === 'stop') continue; // stop handled implicitly by scope
        fireTimelineEvent(ev);
      }
    }
  }

  function runnerUpdate(dt) {
    if (!moveRunner.active || !moveRunner.stages.length) return;
    const stg = moveRunner.stages[moveRunner.si];
    const f0 = stg.start_frame > 0 ? stg.start_frame : 0;
    const f1 = stg.end_frame > f0 ? stg.end_frame : (stg.total_frames || f0 + 1);
    const prevT = (moveRunner.frame - f0) / moveRunner.fps;
    moveRunner.frame += dt * moveRunner.fps;
    let stageT = (moveRunner.frame - f0) / moveRunner.fps;
    const holding = moveRunner.holdBtn &&
      ((moveRunner.holdBtn === 'Action') ? !!mouseCombat.eDownT
        : (mouseCombat.chargeStart && mouseCombat.chargeStart.btn === moveRunner.holdBtn));
    if (moveRunner.frame >= f1) {
      if (stg.hold_loop && holding) {
        // charge stage loops while held — the in-game "slowed" charge visual
        moveRunner.frame = f0 + (moveRunner.frame - f1) % Math.max(0.001, (f1 - f0));
        updateCombatBanner(`蓄力段循环 [${f0}..${f1}]`);
      } else {
        runnerFireStageEvents(stg, (f1 - f0) / moveRunner.fps + 1, prevT);
        moveRunner.si += 1;
        moveRunner.fired = new Set();
        if (moveRunner.si >= moveRunner.stages.length) {
          moveRunner.active = false;
          isPlayingAnim = false;
          setPlayButtonState(false);
          updateCombatBanner('招式结束');
          return;
        }
        const nxt = moveRunner.stages[moveRunner.si];
        moveRunner.fps = nxt.fps || moveRunner.fps;
        moveRunner.frame = nxt.start_frame > 0 ? nxt.start_frame : 0;
        // if next stage requires button release and we are still holding,
        // stay parked at the END of the loop stage instead (handled above by
        // hold_loop; this is a safety for data without hold_loop)
        updateCombatBanner(`阶段 ${moveRunner.si + 1}/${moveRunner.stages.length}`);
      }
      stageT = (moveRunner.frame - f0) / moveRunner.fps;
    } else {
      runnerFireStageEvents(stg, stageT, prevT);
    }
    runnerSetPose(moveRunner.frame);
    for (const mixer of activeMixers) mixer.update(0);
    for (const rig of activeRigs.values()) rig.skeleton.update();
  }

  function combatPump() {
    if (mouseCombat.pending && (!moveRunner.active || canTransitionNow())) {
      const pdg = mouseCombat.pending;
      mouseCombat.pending = null;
      void startMoveRun(pdg.mv, pdg.label, pdg.holdBtn || null);
    }
    // live charge feedback while a combat button is held
    const cs = mouseCombat.chargeStart;
    if (cs) {
      const held = (performance.now() - cs.t) / 1000;
      if (held >= 0.25) {
        updateCombatBanner(`蓄力 ${cs.btn} ${held.toFixed(2)}s`);
        if (!cs.holding) {
          cs.holding = true;
          // start the charged move's runner immediately: it will play windup
          // then LOOP the hold stage until release (real FIG frame windows)
          const ups = (mouseCombat.moves || [])
            .filter(m => m.button === cs.btn && (m.state === 'Up' || m.state === 'Released'))
            .sort((x, y) => (x.hold_seconds || 0) - (y.hold_seconds || 0));
          if (ups.length) {
            void startMoveRun(ups[0], `蓄力 (${cs.btn})`, cs.btn);
          }
        }
      }
    } else if (mouseCombat.enabled) {
      updateCombatBanner();
    }
  }

  // pointer press/hold discrimination on the renderer canvas
  {
    const el = renderer.domElement;
    let downX = 0, downY = 0, downBtn = -1, downT = 0;
    const BTN = { 0: 'Attack', 2: 'Special' };
    el.addEventListener('pointerdown', (e) => {
      downX = e.clientX; downY = e.clientY; downBtn = e.button; downT = performance.now();
      if (mouseCombat.enabled && (e.button in BTN) && (lockActive() || sandbox.enabled)) {
        mouseCombat.chargeStart = { btn: BTN[e.button], t: downT, holding: false };
      }
    });
    el.addEventListener('pointerup', (e) => {
      if (mouseCombat.chargeStart && (e.button in BTN) && BTN[e.button] === mouseCombat.chargeStart.btn) {
        mouseCombat.chargeStart = null;
      }
      // Sandbox: first plain left-click enters pointer-lock control mode.
      if (sandbox.enabled && !lockActive() && e.button === 0) {
        const moved0 = Math.hypot(e.clientX - downX, e.clientY - downY);
        if (moved0 < 6) { renderer.domElement.requestPointerLock(); return; }
      }
      if (!mouseCombat.enabled) return;
      if (e.button !== downBtn || !(e.button in BTN)) return;
      const moved = lockActive() ? 0 : Math.hypot(e.clientX - downX, e.clientY - downY);
      if (moved < 6) {
        const held = (performance.now() - downT) / 1000;
        if (combatTrigger(BTN[e.button], held)) { e.preventDefault(); e.stopPropagation(); }
      }
    });
    el.addEventListener('contextmenu', (e) => {
      if (mouseCombat.enabled) e.preventDefault();
    });
  }

  function buildSandbox() {
    // Unity-style blockout: big white ground plane + one large cube.
    const floorGeom = new THREE.PlaneGeometry(4000, 4000);
    const floorMat = new THREE.MeshBasicMaterial({ color: 0xdadde2 });
    const floor = new THREE.Mesh(floorGeom, floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -0.01;
    const gridH = new THREE.GridHelper(400, 200, 0xb9bec7, 0xcfd3da);
    const c = sandbox.cube;
    const cubeGeom = new THREE.BoxGeometry(c.half * 2, c.half * 2, c.half * 2);
    const cubeMat = new THREE.MeshBasicMaterial({ color: 0x93a1b2 });
    const cube = new THREE.Mesh(cubeGeom, cubeMat);
    cube.position.set(c.cx, c.cy, c.cz);
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(cubeGeom),
      new THREE.LineBasicMaterial({ color: 0x3b4656 }));
    edges.position.copy(cube.position);
    for (const m of [floor, gridH, cube, edges]) { scene.add(m); sandbox.meshes.push(m); }

    sandbox.hud = document.createElement('div');
    sandbox.hud.style.cssText = 'position:fixed;right:14px;bottom:14px;background:rgba(10,14,20,.88);border:1px solid #2d3848;border-radius:8px;padding:8px 12px;font:11px/1.6 monospace;color:#7fd4ff;z-index:99;white-space:pre;';
    document.body.appendChild(sandbox.hud);
    // Anchor the physics tracker to where the ACTOR actually stands (Pelvis
    // world XZ), not entityGroup's origin - fixes cube collision offset.
    const pw = getPelvisWorldPosition();
    sandbox.pos = new THREE.Vector3(pw ? pw.x : entityGroup.position.x, 0,
                                    pw ? pw.z : entityGroup.position.z);
  }

  // --- pointer lock steering (mouse "sinks into" the viewport) ---
  function lockActive() { return document.pointerLockElement === renderer.domElement; }

  function ensureCombatBanner() {
    if (combatBanner) return combatBanner;
    combatBanner = document.createElement('div');
    combatBanner.style.cssText = 'position:fixed;top:52px;left:50%;transform:translateX(-50%);background:rgba(20,12,40,.92);border:1px solid #8b5cf6;border-radius:20px;padding:5px 18px;font:12px/1.5 sans-serif;color:#d8ceff;z-index:99;display:none;white-space:nowrap;';
    document.body.appendChild(combatBanner);
    return combatBanner;
  }

  function updateCombatBanner(extra) {
    const el = ensureCombatBanner();
    if (!mouseCombat.enabled) { el.style.display = 'none'; return; }
    const idx = (mouseCombat.chainIdxMap && mouseCombat.chainIdxMap.Attack != null)
      ? mouseCombat.chainIdxMap.Attack + 1 : 0;
    const total = mouseCombat.chain ? mouseCombat.chain.length : 0;
    el.style.display = 'block';
    el.textContent = `⚔ 状态树驱动 · ${mouseCombat.block || '?'} · 连段 ${idx}/${total}` + (extra ? ' · ' + extra : '');
  }
  document.addEventListener('mousemove', (e) => {
    if (!lockActive()) return;
    const offset = camera.position.clone().sub(controls.target);
    const sph = new THREE.Spherical().setFromVector3(offset);
    sph.theta -= e.movementX * 0.0025;
    sph.phi -= e.movementY * 0.0025;
    sph.phi = Math.max(0.15, Math.min(Math.PI - 0.15, sph.phi));
    offset.setFromSpherical(sph);
    camera.position.copy(controls.target).add(offset);
    camera.lookAt(controls.target);
  });
  document.addEventListener('pointerlockchange', () => {
    controls.enabled = !lockActive();
    if (sandbox.hud) {
      sandbox.hud.style.borderColor = lockActive() ? '#7fd4ff' : '#2d3848';
    }
    onStatus(lockActive()
      ? '🎯 已进入操控模式：鼠标转视角 · WASD 移动 · 左键 Attack / 右键 Special / E Action · Esc 退出'
      : (sandbox.enabled ? '🏗 已退出操控模式（点击视口可重新进入）' : ''));
  });

  function teardownSandbox() {
    for (const m of sandbox.meshes) {
      scene.remove(m);
      if (m.geometry) m.geometry.dispose();
      if (m.material) m.material.dispose();
    }
    sandbox.meshes = [];
    if (sandbox.hud) { sandbox.hud.remove(); sandbox.hud = null; }
    sandbox.keys.clear();
    sandbox.velY = 0;
    sandbox.grounded = true;
  }

  function sandboxUpdate(dt) {
    if (!sandbox.enabled || dt <= 0) return;
    dt = Math.min(dt, 0.05);
    // --- horizontal input (camera-relative) ---
    let f = 0, r = 0;
    if (sandbox.keys.has('KeyW')) f += 1;
    if (sandbox.keys.has('KeyS')) f -= 1;
    if (sandbox.keys.has('KeyD')) r += 1;
    if (sandbox.keys.has('KeyA')) r -= 1;
    const running = sandbox.keys.has('ShiftLeft') || sandbox.keys.has('ShiftRight');
    const speed = running ? 9.0 : 4.0;
    const fwd = new THREE.Vector3();
    camera.getWorldDirection(fwd); fwd.y = 0;
    if (fwd.lengthSq() < 1e-6) fwd.set(0, 0, -1);
    fwd.normalize();
    const right = new THREE.Vector3().crossVectors(fwd, new THREE.Vector3(0, 1, 0));
    const move = new THREE.Vector3().addScaledVector(fwd, f).addScaledVector(right, r);
    if (move.lengthSq() > 0) move.normalize().multiplyScalar(speed * dt);

    // --- vertical: gravity + jump ---
    if (sandbox.jumpQueued && sandbox.grounded) { sandbox.velY = 7.5; sandbox.grounded = false; }
    sandbox.jumpQueued = false;
    sandbox.velY -= 20.0 * dt;
    const pos = sandbox.pos; // actor feet tracker (anchored to Pelvis XZ)
    let nx = pos.x + move.x, nz = pos.z + move.z;
    let ny = pos.y + sandbox.velY * dt;

    // --- cube AABB collision (expanded by capsule radius) ---
    const c = sandbox.cube, R = sandbox.radius;
    const top = c.cy + c.half;
    const inX = nx > c.cx - c.half - R && nx < c.cx + c.half + R;
    const inZ = nz > c.cz - c.half - R && nz < c.cz + c.half + R;
    const feetBelowTop = ny < top - 0.02;
    if (inX && inZ && feetBelowTop && ny > -0.5) {
      // side push-out along the smallest penetration axis
      const pxr = (c.cx + c.half + R) - nx, pxl = nx - (c.cx - c.half - R);
      const pzr = (c.cz + c.half + R) - nz, pzl = nz - (c.cz - c.half - R);
      const minPen = Math.min(pxr, pxl, pzr, pzl);
      if (minPen === pxr) nx = c.cx + c.half + R;
      else if (minPen === pxl) nx = c.cx - c.half - R;
      else if (minPen === pzr) nz = c.cz + c.half + R;
      else nz = c.cz - c.half - R;
    }
    // --- ground / cube-top landing ---
    let groundY = 0;
    if (nx > c.cx - c.half - R && nx < c.cx + c.half + R &&
        nz > c.cz - c.half - R && nz < c.cz + c.half + R && pos.y >= top - 0.4) {
      groundY = top;
    }
    if (ny <= groundY) { ny = groundY; sandbox.velY = 0; sandbox.grounded = true; }
    else if (sandbox.velY < -0.01) { sandbox.grounded = false; }

    const delta = new THREE.Vector3(nx - pos.x, ny - pos.y, nz - pos.z);
    if (delta.lengthSq() > 0) {
      shiftActorWorldPosition(delta);
      camera.position.add(delta);
      controls.target.add(delta);
    }
    sandbox.pos.set(nx, ny, nz);
    // --- HUD: the exact runtime quantities FIG conditions consume ---
    const vXZ = move.length() / dt;
    if (sandbox.hud) {
      sandbox.hud.textContent =
        `沙盒物理（查看器近似，原引擎运动代码未解码）\n` +
        `velocity XZ  : ${vXZ.toFixed(2)} m/s  (FIG velocity 条件用量)\n` +
        `supportingSurface : ${sandbox.grounded}  (FIG 条件用量)\n` +
        `feet Y : ${ny.toFixed(2)}  ${ny >= top - 0.02 && groundY === top ? '[站在 Cube 顶]' : ''}\n` +
        `WASD 移动 · Shift 跑 · Space 跳 · 🏗 再点关闭`;
    }
  }

  function playAnimationIndex(idx) {
    if (!currentAnimations || idx < 0 || idx >= currentAnimations.length) return;
    currentAnimIdx = idx;
    currentAnimTrack = currentAnimations[idx];
    focusMode = 'animation';

    if (currentAnimTrack.playable === false) {
      stopActiveAnimation();
      const playBtn = document.getElementById('anim-play-btn');
      if (playBtn) {
        playBtn.textContent = '源记录（非骨骼变换）';
        playBtn.style.background = '#1a2230';
      }
      onStatus(`已保留原始动作记录：${currentAnimTrack.name}；未发现可证明映射到骨骼变换的通道，未伪造播放。`);
      return;
    }

    const playBtn = document.getElementById('anim-play-btn');
    if (playBtn) {
      playBtn.textContent = '⏸ 暂停';
      playBtn.style.background = '#4c1d95';
    }

    document.querySelectorAll('.anim-row').forEach((r, i) => {
      r.style.background = (i === idx) ? '#2e1065' : '#151a24';
      r.style.borderColor = (i === idx) ? '#8b5cf6' : 'transparent';
      if (i === idx) r.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });

    playAnimationClip(currentAnimTrack);
    onStatus(`正在播放真实骨骼动画 [${idx + 1}/${currentAnimations.length}]：${currentAnimTrack.name} (${currentAnimTrack.frames} 帧 @ ${currentAnimTrack.fps} fps)`);
  }

  function updateMeshInspector(meshes, animations, skeletons, assemblies = []) {
    const listEl = document.getElementById('inspector-mesh-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    // The source-component selector can intentionally hide large unrelated
    // collection meshes (for example the PermanentCharactersPackage Brawler
    // Skin). Reframe from the actual visible objects rather than retaining a
    // camera fit calculated over the whole package.
    const frameVisibleMeshes = () => {
      const visibleBounds = new THREE.Box3();
      let hasVisibleMesh = false;
      for (const item of meshes) {
        if (!item.threeMesh.visible) continue;
        item.threeMesh.updateMatrixWorld(true);
        visibleBounds.expandByObject(item.threeMesh);
        hasVisibleMesh = true;
      }
      if (!hasVisibleMesh || visibleBounds.isEmpty()) return;
      const center = visibleBounds.getCenter(new THREE.Vector3());
      const size = visibleBounds.getSize(new THREE.Vector3()).length() || 2.0;
      controls.target.copy(center);
      camera.position.set(center.x + size * 0.9, center.y + size * 0.7, center.z + size * 0.9);
      controls.minDistance = Math.max(0.3, size * 0.35);
      controls.maxDistance = Math.max(3.0, size * 2.5);
      camera.updateProjectionMatrix();
      controls.update();
    };

    const rigSummary = (skeletons || []).map(s => `${s.name} (${s.joints?.length || 0})`).join(' · ');
    const skelRow = document.createElement('div');
    skelRow.className = 'mesh-row';
    skelRow.style.borderLeft = '3px solid #fb923c';
    skelRow.innerHTML = `
      <div class="mesh-info">
        <div class="mesh-name" style="color:#7fd4ff; font-weight:600;" title="${rigSummary}">🦴 骨骼系统 (${(skeletons || []).length} 组；${rigSummary || '无'})</div>
      </div>
      <input type="checkbox" class="mesh-toggle" ${showSkeleton ? 'checked' : ''} id="toggle-skeleton-lines" title="开启/关闭骨骼线条 (快捷键 B)" />
    `;
    const skelToggle = skelRow.querySelector('#toggle-skeleton-lines');
    if (skelToggle) {
      skelToggle.onchange = (e) => {
        showSkeleton = e.target.checked;
        skeletonGroup.visible = showSkeleton;
      };
    }
    listEl.appendChild(skelRow);

    const followRow = document.createElement('div');
    followRow.className = 'mesh-row';
    followRow.style.borderLeft = '3px solid #a78bfa';
    followRow.innerHTML = `
      <div class="mesh-info">
        <div class="mesh-name" style="color:#c4b5fd;font-weight:600;">🎥 跟随角色胯骨</div>
        <div class="mesh-counts">切动作时保持角色当前位置；鼠标仍可绕行</div>
      </div>
      <input type="checkbox" class="mesh-toggle" ${followPelvisWithCamera ? 'checked' : ''} id="toggle-pelvis-camera-follow" title="镜头跟随 Pelvis 胯骨移动" />
    `;
    const followToggle = followRow.querySelector('#toggle-pelvis-camera-follow');
    followToggle.onchange = () => {
      const active = setPelvisCameraFollow(followToggle.checked);
      followToggle.checked = active;
    };
    listEl.appendChild(followRow);
    // Preserve the selected mode when loading the next entity, but establish
    // a fresh anchor from that entity's actual Pelvis after its rig exists.
    if (followPelvisWithCamera) setPelvisCameraFollow(true, false);

    // An assembly is a source-proven Composite_Drawable_2 -> Skin-name edge,
    // not a filename-prefix guess.  Soldier packages deliberately contain
    // several complete bodies and weapon drawables; show one exact assembly by
    // default instead of presenting their overlapped meshes as one character.
    const assemblyViews = [];
    for (const assembly of assemblies || []) {
      const memberNames = new Set((assembly.primitives || [])
        .map(primitive => primitive.skin_name)
        .filter(name => typeof name === 'string' && name.length > 0));
      if (!memberNames.size) continue;
      const meshIndexes = new Set();
      meshes.forEach((item, idx) => {
        const meshName = item.meshData.skin_name || item.meshData.geometry_name;
        if (memberNames.has(meshName)) meshIndexes.add(idx);
      });
      if (meshIndexes.size) assemblyViews.push({
        name: assembly.name,
        meshIndexes,
        provenance: 'Composite_Drawable_2 → Skin 直接引用'
      });
    }
    // Some story libraries have no Composite_Drawable_2 at all, yet do have
    // a direct Skin → skeleton_name source edge beside unrelated static
    // geometry. Fall back only to that exact binding, never to similar names.
    let sourceViews = assemblyViews;
    if (sourceViews.length === 0) {
      const bySkeleton = new Map();
      meshes.forEach((item, index) => {
        const skeletonName = item.meshData.skeleton_name;
        if (!skeletonName) return;
        if (!bySkeleton.has(skeletonName)) bySkeleton.set(skeletonName, new Set());
        bySkeleton.get(skeletonName).add(index);
      });
      sourceViews = [...bySkeleton.entries()].map(([name, meshIndexes]) => ({
        name,
        meshIndexes,
        provenance: 'Skin → skeleton_name 直接绑定'
      }));
    }
    // A single exact Skin group is still a meaningful source view when it
    // isolates an actor from unrelated meshes in a collection package.
    if (sourceViews.length > 1 || (sourceViews.length === 1 && sourceViews[0].meshIndexes.size < meshes.length)) {
      const assemblyRow = document.createElement('div');
      assemblyRow.className = 'mesh-row';
      assemblyRow.style.borderLeft = '3px solid #34d399';
      const select = document.createElement('select');
      select.id = 'assembly-visibility';
      select.className = 'mesh-toggle';
      select.style.width = '170px';
      select.title = '仅显示经原始引用链验证的实体组件';
      const allOption = document.createElement('option');
      allOption.value = '-1';
      allOption.textContent = `全部已解码部件 (${meshes.length})`;
      select.appendChild(allOption);
      sourceViews.forEach((view, index) => {
        const option = document.createElement('option');
        option.value = String(index);
        option.textContent = `${view.name} (${view.meshIndexes.size} 部件)`;
        select.appendChild(option);
      });
      const provenance = [...new Set(sourceViews.map(view => view.provenance))].join('；');
      assemblyRow.innerHTML = `<div class="mesh-info"><div class="mesh-name" style="color:#6ee7b7;font-weight:600;">◫ 源实体组件</div><div class="mesh-counts">${provenance}</div></div>`;
      assemblyRow.appendChild(select);
      const applyAssemblyView = (selectedIndex) => {
        const selected = selectedIndex >= 0 ? sourceViews[selectedIndex] : null;
        meshes.forEach((item, meshIndex) => {
          const visible = !selected || selected.meshIndexes.has(meshIndex);
          item.threeMesh.visible = visible;
          const toggle = document.getElementById(`toggle-mesh-${meshIndex}`);
          if (toggle) toggle.checked = visible;
        });
      };
      // The first materialised source view is deliberately chosen instead of
      // an inferred "main" mesh. The user can select All to inspect every
      // independently declared component.
      select.value = '0';
      applyAssemblyView(0);
      frameVisibleMeshes();
      select.onchange = () => {
        applyAssemblyView(Number(select.value));
        frameVisibleMeshes();
      };
      listEl.appendChild(assemblyRow);
    }

    meshes.forEach((item, idx) => {
      const row = document.createElement('div');
      row.className = 'mesh-row';
      row.innerHTML = `
        <div class="mesh-info">
          <div class="mesh-name" title="${item.meshData.skin_name || item.meshData.geometry_name}">${item.meshData.skin_name || item.meshData.geometry_name || `Part_${idx + 1}`}</div>
          <div class="mesh-counts">${item.meshData.vertex_count} 顶 · ${item.meshData.triangle_count} 面${item.meshData.skeleton_name ? ` · ${item.meshData.skeleton_name}` : ''}</div>
        </div>
        <input type="checkbox" class="mesh-toggle" ${item.threeMesh.visible ? 'checked' : ''} id="toggle-mesh-${idx}" title="显示/隐藏此部件" />
      `;
      const toggle = row.querySelector(`#toggle-mesh-${idx}`);
      toggle.onchange = (e) => {
        item.threeMesh.visible = e.target.checked;
      };
      listEl.appendChild(row);
    });

    currentAnimations = animations || [];
    currentAnimIdx = -1;
    const firstPlayableAnimation = currentAnimations.findIndex(anim => anim.playable !== false);
    currentAnimTrack = firstPlayableAnimation >= 0 ? currentAnimations[firstPlayableAnimation] : (currentAnimations[0] || null);

    if (currentAnimations.length > 0) {
      const playableCount = currentAnimations.filter(anim => anim.playable !== false).length;
      const sourceOnlyCount = currentAnimations.length - playableCount;
      const animHeader = document.createElement('div');
      animHeader.className = 'anim-section-title';
      animHeader.style.display = 'flex';
      animHeader.style.justifyContent = 'space-between';
      animHeader.style.alignItems = 'center';
      animHeader.innerHTML = `
        <span>🎬 原始动作记录 (${currentAnimations.length}；${playableCount} 个骨骼轨道${sourceOnlyCount ? ` · ${sourceOnlyCount} 个非骨骼记录` : ''})<small style="display:block;color:#94a3b8;font-size:10px;">↑↓ 切动作 · ←→ 切同类相邻组</small></span>
        <button id="anim-play-btn" class="inspector-btn" style="color:#a78bfa; border-color:#8b5cf6;" title="Space 播放/暂停；上下切动作，左右切同类相邻组">▶ 播放 (Space)</button>
      `;
      listEl.appendChild(animHeader);

      const playBtn = animHeader.querySelector('#anim-play-btn');
      playBtn.onclick = () => {
        if (!currentAnimations.length) return;
        if (currentAnimIdx < 0 || currentAnimations[currentAnimIdx]?.playable === false) {
          const idx = currentAnimations.findIndex(anim => anim.playable !== false);
          if (idx >= 0) playAnimationIndex(idx);
          return;
        }
        const dur = currentClipDuration();
        const atEnd = activeAction && dur > 0 && activeAction.time >= dur - 1e-3;
        if (!isPlayingAnim && atEnd) {
          // One-shot clip finished: restart from frame 0 with a fresh FIG
          // event schedule, instead of resuming a clamped action.
          for (const action of activeActions) { action.reset(); action.paused = false; action.play(); }
          timelineFired = new Set();
          timelineLastT = 0;
          lastRibbonT = -1;
          isPlayingAnim = true;
        } else {
          isPlayingAnim = !isPlayingAnim;
          for (const action of activeActions) action.paused = !isPlayingAnim;
        }
        setPlayButtonState(isPlayingAnim);
      };

      // Progress bar + loop toggle: generic playback controls for every clip.
      const ctrlRow = document.createElement('div');
      ctrlRow.style.cssText = 'display:flex;align-items:center;gap:8px;padding:4px 2px 8px;';
      ctrlRow.innerHTML = `
        <button id="anim-loop-btn" class="inspector-btn" style="min-width:34px;" title="循环播放开关（默认跟随动画源的 cyclic 标记）">🔁</button>
        <button id="anim-mouse-btn" class="inspector-btn" style="min-width:34px;" title="招式模拟：开启后在 3D 视口 左键=Attack 连段 / 右键=Special（数据来自 FIG input 条件记录）">🖱</button>
        <button id="anim-sandbox-btn" class="inspector-btn" style="min-width:34px;" title="沙盒：白色地面+碰撞立方体，WASD/Shift/Space 控制角色（物理为查看器近似，HUD 明示）">🏗</button>
        <input type="range" id="anim-progress" min="0" max="1000" value="0" style="flex:1;accent-color:#8b5cf6;" title="拖动跳转到该帧并暂停（特效随之冻结）"/>
        <span id="anim-time-label" style="font-size:10px;color:#94a3b8;min-width:86px;text-align:right;">0.00 / 0.00s</span>
      `;
      listEl.appendChild(ctrlRow);
      const mouseBtn = ctrlRow.querySelector('#anim-mouse-btn');
      // Disabled after Blade attack-B and Hammerfist hold counterexamples
      // disproved the provisional document-order runner. Keep no broken
      // simulator exposed while the real graph evaluator is reconstructed.
      mouseCombat.enabled = false;
      mouseBtn.disabled = true;
      mouseBtn.style.display = 'none';
      mouseBtn.onclick = null;
      const sandboxBtn = ctrlRow.querySelector('#anim-sandbox-btn');
      sandboxBtn.onclick = () => {
        if (sandbox.enabled) {
          sandbox.enabled = false;
          teardownSandbox();
          sandboxBtn.style.color = '';
          sandboxBtn.style.borderColor = '';
          onStatus('🏗 沙盒已关闭');
        } else {
          sandbox.enabled = true;
          buildSandbox();
          sandboxBtn.style.color = '#7fd4ff';
          sandboxBtn.style.borderColor = '#7fd4ff';
          onStatus('🏗 沙盒开启：WASD 移动 · Shift 跑 · Space 跳。物理为查看器近似（HUD 有说明）；招式模拟 🖱 可同时开启。');
        }
      };
      const loopBtn = ctrlRow.querySelector('#anim-loop-btn');
      const syncLoopBtn = () => {
        const effective = (forcedLoop === null) ? !!(currentAnimTrack && currentAnimTrack.cyclic) : forcedLoop;
        loopBtn.style.color = effective ? '#7fd4ff' : '#6b7280';
        loopBtn.style.borderColor = effective ? '#7fd4ff' : '#2d3848';
        loopBtn.title = forcedLoop === null
          ? `循环：跟随源数据 cyclic=${effective}（点击强制${effective ? '关' : '开'}）`
          : `循环：已强制${effective ? '开' : '关'}（点击恢复跟随源数据）`;
      };
      loopBtn.onclick = () => {
        const effective = (forcedLoop === null) ? !!(currentAnimTrack && currentAnimTrack.cyclic) : forcedLoop;
        forcedLoop = (forcedLoop === null) ? !effective : null;
        const newEffective = (forcedLoop === null) ? !!(currentAnimTrack && currentAnimTrack.cyclic) : forcedLoop;
        for (const action of activeActions) {
          action.setLoop(newEffective ? THREE.LoopRepeat : THREE.LoopOnce);
          action.clampWhenFinished = !newEffective;
        }
        syncLoopBtn();
      };
      syncLoopBtn();
      const bar = ctrlRow.querySelector('#anim-progress');
      bar.oninput = () => {
        const dur = currentClipDuration();
        if (dur > 0) seekAnimationTo((Number(bar.value) / 1000) * dur);
      };

      currentAnimations.forEach((anim, aidx) => {
        const aRow = document.createElement('div');
        aRow.className = 'anim-row';
        aRow.style.cursor = 'pointer';
        const sourceOnly = anim.playable === false;
        aRow.title = sourceOnly
          ? '源 0x121000 记录存在；未获可映射骨骼变换的证据，点击查看但不会伪造播放'
          : '点击播放此动作 (上下键快速切换)';
        aRow.innerHTML = `
          <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:150px;${sourceOnly ? ' color:#94a3b8;' : ''}">${anim.name}</span>
          <span style="opacity:0.8;">${anim.frames}f · ${anim.duration}s${sourceOnly ? ' · 源记录' : ''}</span>
        `;
        aRow.onclick = () => {
          playAnimationIndex(aidx);
        };
        listEl.appendChild(aRow);
      });

      if (firstPlayableAnimation >= 0) playAnimationIndex(firstPlayableAnimation);
    }
  }

  async function selectEntity(item, shapeName = null) {
    const loadGeneration = ++entityLoadGeneration;
    currentSelectedEntity = item;
    currentShapeName = shapeName;
    // Continuity is meaningful only while switching actions on one actor.
    // A newly selected catalogue entity deliberately starts in its own source
    // world space instead of inheriting the previous actor's displacement.
    entityGroup.position.set(0, 0, 0);
    skeletonGroup.position.set(0, 0, 0);
    lastFollowPelvisWorld = null;
    if (onSelectEntity) onSelectEntity(item);

    onStatus(`正在解析 3D 实体模型与骨骼系统：${item.name}…`);

    while (entityGroup.children.length > 0) {
      const child = entityGroup.children[0];
      entityGroup.remove(child);
      if (child.geometry) child.geometry.dispose();
      if (child.material) {
        if (Array.isArray(child.material)) child.material.forEach(m => m.dispose());
        else child.material.dispose();
      }
    }
    renderedMeshList = [];
    stopActiveAnimation();

    try {
      const artPath = document.getElementById('map-shared')?.value || '';
      const targetShape = shapeName || (item.category === 'props' ? (item.shapes?.[0] || item.id) : '');
      const params = new URLSearchParams({
        path: artPath,
        entry: item.entry_path,
        shape: targetShape
      });
      const resp = await fetch(`/api/entity_mesh?${params}`);
      const data = await resp.json();
      // Arrow-key group switching may start another request before this one
      // returns. Never let an older response replace the selected entity.
      if (loadGeneration !== entityLoadGeneration) return;
      if (data.error) {
        onStatus('加载实体网格失败：' + data.error);
        return;
      }

      const textures = new Map();
      for (const desc of data.textures || []) {
        try {
          textures.set(desc.key, makeMapTexture(desc));
        } catch (e) {
          console.warn('Texture parse failed:', desc.key, e);
        }
      }

      // Build Skeleton Hierarchy
      buildSkeletonHierarchy(data.skeletons);

      let hasMeshes = false;
      const bounds = new THREE.Box3();

      for (const mesh of data.meshes || []) {
        const positions = floats(mesh.positions, Float32Array, 4);
        const indices = floats(mesh.indices, Uint16Array, 2);
        const uv = mesh.uv ? floats(mesh.uv, Float32Array, 4) : new Float32Array(mesh.vertex_count * 2);

        if (positions.length < 3 || indices.length < 3) continue;

        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
        geometry.setIndex(new THREE.BufferAttribute(indices, 1));
        geometry.computeVertexNormals();

        const tex = textures.get(mesh.texture_key);
        const material = makeMapMaterial(tex, null, false);

        let rendered = null;

        const meshRig = mesh.skeleton_name ? activeRigs.get(mesh.skeleton_name) : null;
        if (meshRig && mesh.skin_indices && mesh.skin_weights) {
          const sIndices = floats(mesh.skin_indices, Uint16Array, 2);
          const sWeights = floats(mesh.skin_weights, Float32Array, 4);

          geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(sIndices, 4));
          geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(sWeights, 4));

          const skinnedMesh = new THREE.SkinnedMesh(geometry, material);
          entityGroup.add(skinnedMesh);

          // Bones remain in meshRig.root (a sibling of entityGroup).  Moving a
          // shared root under each SkinnedMesh would detach it from earlier
          // meshes and makes multi-Skin binding order-dependent.
          skinnedMesh.updateMatrixWorld(true);
          skinnedMesh.bind(meshRig.skeleton, skinnedMesh.matrixWorld);

          rendered = skinnedMesh;
        } else {
          rendered = new THREE.Mesh(geometry, material);
          entityGroup.add(rendered);
        }

        // Disable only Three.js' stale bind-pose frustum shortcut for entity
        // inspection. This viewer has no Unity-style occlusion system; moving
        // roots and bones must remain rendered whenever they face the camera.
        rendered.frustumCulled = false;
        geometry.computeBoundingBox();
        bounds.union(geometry.boundingBox);
        hasMeshes = true;

        renderedMeshList.push({
          meshData: mesh,
          threeMesh: rendered,
          origY: rendered.position.y
        });
      }

      if (hasMeshes) {
        const center = bounds.getCenter(new THREE.Vector3());
        const size = bounds.getSize(new THREE.Vector3()).length() || 2.0;

        controls.target.copy(center);
        camera.position.set(center.x + size * 0.9, center.y + size * 0.7, center.z + size * 0.9);

        controls.zoomSpeed = 0.35;
        controls.dampingFactor = 0.1;
        controls.minDistance = Math.max(0.3, size * 0.35);
        controls.maxDistance = Math.max(3.0, size * 2.5);
        camera.near = 0.01;
        camera.far = 3000;
        camera.updateProjectionMatrix();
        controls.update();

        updateMeshInspector(renderedMeshList, data.animations, data.skeletons, data.assemblies);

        if (!THUMB_CACHE.has(item.id)) {
          setTimeout(async () => {
            const snap = captureCurrentEntitySnapshot();
            if (snap) {
              await persistSnapshot(item.id, snap);
              updateCardThumbDOM(item.id, snap);
              updateStatsBadge();
            }
          }, 80);
        }

        const actionTotal = data.animations?.length || 0;
        const actionPlayable = (data.animations || []).filter(animation => animation.playable !== false).length;
        const animInfo = actionTotal ? ` · ${actionTotal} 个动作记录（${actionPlayable} 个可播放）` : '';
        onStatus(`已呈现 3D 实体：${item.name} (${data.meshes.length} 个部件, ${(data.skeletons || []).map(s => `${s.name}:${s.joints?.length || 0}`).join(' / ') || '无骨骼'}${animInfo})`);
      } else {
        onStatus(`实体 ${item.name} 结构已解析 (无直接网格数据)`);
      }
    } catch (e) {
      console.error(e);
      if (loadGeneration === entityLoadGeneration) onStatus('实体网格加载异常：' + e.message);
    }
  }

  function captureCurrentEntitySnapshot() {
    try {
      const snapCanvas = document.createElement('canvas');
      snapCanvas.width = 256;
      snapCanvas.height = 256;
      const snapRenderer = new THREE.WebGLRenderer({
        canvas: snapCanvas,
        alpha: true,
        antialias: true,
        preserveDrawingBuffer: true
      });
      snapRenderer.setSize(256, 256);
      snapRenderer.render(scene, camera);
      const dataUrl = snapCanvas.toDataURL('image/webp', 0.85);
      snapRenderer.dispose();
      return dataUrl;
    } catch (e) {
      return null;
    }
  }

  function updateCardThumbDOM(id, dataUrl) {
    document.querySelectorAll(`.entity-card[data-id="${id}"] .card-preview-thumb`).forEach(el => {
      el.style.backgroundImage = `url("${dataUrl}")`;
      el.innerHTML = '';
    });
  }

  function renderCard(item, isDrawer = false) {
    const card = document.createElement('div');
    card.className = 'entity-card';
    card.dataset.id = item.id;
    card.dataset.cat = item.category;

    const icon = CAT_ICONS[item.category] || '📦';
    const cachedSnap = THUMB_CACHE.get(item.id);

    let thumbHtml = '';
    if (cachedSnap) {
      thumbHtml = `<div class="card-preview-thumb" style="background-image: url('${cachedSnap}');"></div>`;
    } else {
      thumbHtml = `
        <div class="card-preview-thumb">
          <div class="iso-box"></div>
          <span class="cat-icon">${icon}</span>
        </div>
      `;
    }

    const shapeCount = item.shapes ? item.shapes.length : (item.shape_count || 1);
    const badgeText = item.category === 'props' ? `${shapeCount} 破坏形态` : `${item.entry_path.split('\\').pop()}`;

    card.innerHTML = `
      ${thumbHtml}
      <div class="card-title" title="${item.name}">${item.name}</div>
      <div class="card-meta">
        <span class="card-badge geom">${badgeText}</span>
      </div>
      <button class="entity-export-btn" type="button">导出 FBX 资源包（贴图＋源动画）</button>
    `;

    const exportButton = card.querySelector('.entity-export-btn');
    exportButton.onclick = event => {
      event.stopPropagation();
      const oldText = exportButton.textContent;
      exportButton.disabled = true;
      exportButton.textContent = '正在生成 FBX…';
      const query = new URLSearchParams({ entry: item.entry_path, name: item.id || item.name || 'prototype-entity' });
      const link = document.createElement('a');
      link.href = '/api/export_entity?' + query;
      link.download = `${item.id || 'prototype-entity'}-FBX.zip`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => { exportButton.disabled = false; exportButton.textContent = oldText; }, 1800000);
    };

    card.onclick = () => {
      focusMode = 'entity';
      document.querySelectorAll('.entity-card').forEach(c => c.classList.remove('active'));
      card.classList.add('active');
      selectEntity(item);
    };

    return card;
  }

  function renderGrid() {
    if (!entityData) return;

    for (const cat of CATEGORIES) {
      const grid = document.getElementById(`grid-${cat.id}`);
      const sec = document.getElementById(`entity-group-${cat.id}`);
      if (!grid || !sec) continue;

      grid.innerHTML = '';

      if (currentCategory !== 'all' && currentCategory !== cat.id) {
        sec.style.display = 'none';
        continue;
      }

      const items = (entityData.categories && entityData.categories[cat.id]) || entityData[cat.id] || [];
      const filtered = items.filter(item => {
        if (!currentSearchQuery) return true;
        const q = currentSearchQuery.toLowerCase();
        return item.name.toLowerCase().includes(q) || item.id.toLowerCase().includes(q) || item.entry_path.toLowerCase().includes(q);
      });

      if (filtered.length === 0 && currentSearchQuery) {
        sec.style.display = 'none';
        continue;
      }

      sec.style.display = 'block';
      for (const item of filtered) {
        grid.appendChild(renderCard(item));
      }
    }

    renderDrawerGrid();
  }

  function renderDrawerGrid() {
    const gridDrawer = document.getElementById('entity-drawer-grid');
    if (!gridDrawer || !entityData) return;
    gridDrawer.innerHTML = '';

    let totalDrawerMatches = 0;

    for (const cat of CATEGORIES) {
      if (currentCategory !== 'all' && currentCategory !== cat.id) continue;

      const items = (entityData.categories && entityData.categories[cat.id]) || entityData[cat.id] || [];
      const filtered = items.filter(item => {
        if (!currentSearchQuery) return true;
        const q = currentSearchQuery.toLowerCase();
        return item.name.toLowerCase().includes(q) || item.id.toLowerCase().includes(q);
      });

      totalDrawerMatches += filtered.length;

      if (filtered.length > 0) {
        if (currentCategory === 'all') {
          const header = document.createElement('div');
          header.style.gridColumn = '1 / -1';
          header.style.fontSize = '11px';
          header.style.fontWeight = '600';
          header.style.color = '#7fd4ff';
          header.style.background = '#182232';
          header.style.padding = '5px 8px';
          header.style.borderRadius = '4px';
          header.style.borderLeft = '3px solid #3b82f6';
          header.style.marginTop = '6px';
          header.style.display = 'flex';
          header.style.justifyContent = 'space-between';
          header.innerHTML = `<span>${cat.icon} ${cat.name}</span><span style="color:#94a3b8; font-size:10px;">${filtered.length} 款</span>`;
          gridDrawer.appendChild(header);
        }

        for (const item of filtered) {
          gridDrawer.appendChild(renderCard(item, true));
        }
      }
    }

    if (totalDrawerMatches === 0) {
      gridDrawer.innerHTML = `<div style="grid-column:1/-1; padding:30px; text-align:center; color:#6b7280; font-size:12px;">无匹配项</div>`;
    }
  }

  function currentFilteredEntries() {
    if (!entityData) return [];
    const categories = currentCategory === 'all' ? CATEGORIES.map(c => c.id) : [currentCategory];
    const query = currentSearchQuery.toLowerCase();
    const unique = new Map();
    for (const category of categories) {
      const items = (entityData.categories && entityData.categories[category]) || entityData[category] || [];
      for (const item of items) {
        if (query && !item.name.toLowerCase().includes(query) && !item.id.toLowerCase().includes(query) && !item.entry_path.toLowerCase().includes(query)) continue;
        if (!unique.has(item.entry_path)) unique.set(item.entry_path, {entry:item.entry_path,name:item.id || item.name});
      }
    }
    return [...unique.values()];
  }

  async function pollExportJob(jobId, statusEl, button) {
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      const response = await fetch('/api/export_job?' + new URLSearchParams({id:jobId}));
      const job = await response.json();
      if (!response.ok) throw new Error(job.error || `HTTP ${response.status}`);
      statusEl.textContent = `${job.status} · ${job.completed}/${job.total}${job.current ? ` · ${job.current}` : ''} · 成功 ${job.results.length} · 错误 ${job.errors.length}`;
      if (!['queued','running'].includes(job.status)) {
        button.disabled=false;button.textContent='批量导出当前筛选结果';
        if (job.status === 'completed' || job.status === 'completed_with_errors') statusEl.textContent += `\n输出：${job.output_dir}`;
        return;
      }
    }
  }

  async function startBatchExport(items) {
    const pathInput=document.getElementById('entity-export-path');
    const overwrite=document.getElementById('entity-export-overwrite');
    const button=document.getElementById('entity-batch-export');
    const statusEl=document.getElementById('entity-batch-status');
    if (!items.length) { statusEl.textContent='当前筛选没有可导出的实体。'; return; }
    if (!pathInput.value.trim()) { statusEl.textContent='必须填写服务器导出路径。'; return; }
    button.disabled=true;button.textContent=`正在提交 ${items.length} 个实体…`;
    try {
      const response=await fetch('/api/export_entities_batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entries:items,output_dir:pathInput.value.trim(),overwrite:overwrite.checked})});
      const result=await response.json();
      if(!response.ok)throw new Error(result.error||`HTTP ${response.status}`);
      statusEl.textContent=`任务 ${result.job_id} 已创建；输出：${result.output_dir}`;
      await pollExportJob(result.job_id,statusEl,button);
    } catch(error) {
      button.disabled=false;button.textContent='批量导出当前筛选结果';statusEl.textContent='批量导出失败：'+error.message;
    }
  }

  function updateAnimation(dt) {
    sandboxUpdate(dt);
    combatPump();
    runnerUpdate(dt);
    if (!isPlayingAnim) return;
    animTime += dt;

    if (!moveRunner.active) for (const mixer of activeMixers) mixer.update(dt);
    for (const rig of activeRigs.values()) rig.skeleton.update();
    updatePelvisCameraFollow();
    updateMoveTimeline();
    if (activeAction) {
      updateProgressBar(activeAction.time);
      const dur = currentClipDuration();
      if (dur > 0 && activeAction.loop === THREE.LoopOnce && activeAction.time >= dur - 1e-4) {
        // One-shot clip reached its end: reflect the stopped state in the UI
        // so the next play press restarts cleanly.
        isPlayingAnim = false;
        setPlayButtonState(false);
      }
    }

    if (showSkeleton) {
      updateSkeletonVisualizerPositions();
    }
  }

  // Category Tab Switching
  document.querySelectorAll('.cat-tab').forEach(tab => {
    tab.onclick = () => {
      document.querySelectorAll('.cat-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      currentCategory = tab.dataset.cat;
      renderGrid();
    };
  });

  const searchInput = document.getElementById('entity-search-full') || document.getElementById('entity-search-input');
  if (searchInput) {
    searchInput.oninput = (e) => {
      currentSearchQuery = e.target.value.trim();
      renderGrid();
    };
  }

  const drawerSearchInput = document.getElementById('entity-drawer-search');
  if (drawerSearchInput) {
    drawerSearchInput.oninput = (e) => {
      currentSearchQuery = e.target.value.trim();
      renderDrawerGrid();
    };
  }

  const batchExportBtn = document.getElementById('entity-batch-export');
  if (batchExportBtn) batchExportBtn.onclick = () => startBatchExport(currentFilteredEntries());

  const clearBtn = document.getElementById('clear-snapshot-btn');
  if (clearBtn) {
    clearBtn.onclick = async () => {
      if (!confirm('确定要清空所有已缓存的 3D 快照缩略图吗？')) return;
      await clearAllSnapshots();
      updateStatsBadge();
      renderGrid();
      onStatus('已清空本地快照缓存数据库');
    };
  }

  const showAllBtn = document.getElementById('mesh-all-show');
  if (showAllBtn) {
    showAllBtn.onclick = () => {
      renderedMeshList.forEach(m => { m.threeMesh.visible = true; });
      document.querySelectorAll('.mesh-toggle').forEach(t => { t.checked = true; });
    };
  }

  const hideAllBtn = document.getElementById('mesh-all-hide');
  if (hideAllBtn) {
    hideAllBtn.onclick = () => {
      renderedMeshList.forEach(m => { m.threeMesh.visible = false; });
      document.querySelectorAll('.mesh-toggle').forEach(t => { t.checked = false; });
    };
  }

  function adjacentPlayableAnimationIndex(step) {
    if (!currentAnimations || !currentAnimations.length) return -1;
    // Source-only records remain visibly inspectable by clicking their row,
    // but arrows are a playback control and therefore never stop on a record
    // the viewer has explicitly marked non-playable.
    const start = currentAnimIdx >= 0 ? currentAnimIdx : 0;
    for (let distance = 1; distance <= currentAnimations.length; distance++) {
      const index = (start + step * distance + currentAnimations.length) % currentAnimations.length;
      if (currentAnimations[index]?.playable !== false) return index;
    }
    return -1;
  }

  function selectAdjacentEntityGroup(step) {
    if (!entityData || !currentSelectedEntity?.category) return;
    const category = currentSelectedEntity.category;
    const allItems = (entityData.categories && entityData.categories[category]) || entityData[category] || [];
    const query = currentSearchQuery.toLowerCase();
    const items = allItems.filter(item => !query
      || item.name.toLowerCase().includes(query)
      || item.id.toLowerCase().includes(query)
      || item.entry_path.toLowerCase().includes(query));
    if (!items.length) return;
    let currentIndex = items.findIndex(item => item.entry_path === currentSelectedEntity.entry_path && item.id === currentSelectedEntity.id);
    if (currentIndex < 0) currentIndex = 0;
    const nextIndex = (currentIndex + step + items.length) % items.length;
    const next = items[nextIndex];
    focusMode = 'entity';
    document.querySelectorAll('.entity-card').forEach(card => {
      card.classList.toggle('active', card.dataset.id === next.id && card.dataset.cat === next.category);
    });
    onStatus(`切换相邻${category === 'powers' ? '能力形态' : '实体组'}：${next.name}`);
    void selectEntity(next);
  }

  // Keyboard Shortcuts: ↑/↓ change playable actions; ←/→ changes the
  // adjacent entity/ability group in the same shelf category.
  window.addEventListener('keyup', (e) => {
    sandbox.keys.delete(e.code);
    if (mouseCombat.enabled && e.code === 'KeyE' && mouseCombat.eDownT) {
      const held = (performance.now() - mouseCombat.eDownT) / 1000;
      mouseCombat.eDownT = 0;
      combatTrigger('Action', held);
    }
  });

  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    // Movement keys are swallowed while sandbox is on OR pointer lock active,
    // so WASD never falls through to orbit/clip-switch handlers.
    if ((sandbox.enabled || lockActive()) && ['KeyW', 'KeyA', 'KeyS', 'KeyD', 'Space', 'ShiftLeft', 'ShiftRight'].includes(e.code)) {
      e.preventDefault();
      if (e.code === 'Space' && !e.repeat) sandbox.jumpQueued = true;
      sandbox.keys.add(e.code);
      return;
    }

    if (mouseCombat.enabled && e.code === 'KeyE') {
      e.preventDefault();
      if (!e.repeat) mouseCombat.eDownT = performance.now();
      return;
    }

    if (e.code === 'Space') {
      e.preventDefault();
      const playBtn = document.getElementById('anim-play-btn');
      if (playBtn) playBtn.click();
    } else if (e.code === 'KeyB') {
      e.preventDefault();
      const skelToggle = document.getElementById('toggle-skeleton-lines');
      if (skelToggle) {
        skelToggle.checked = !skelToggle.checked;
        showSkeleton = skelToggle.checked;
        skeletonGroup.visible = showSkeleton;
      }
    } else if (e.code === 'ArrowDown' || e.code === 'KeyS') {
      e.preventDefault();
      if (focusMode === 'animation') {
        const nextIdx = adjacentPlayableAnimationIndex(1);
        if (nextIdx >= 0) playAnimationIndex(nextIdx);
      }
    } else if (e.code === 'ArrowUp' || e.code === 'KeyW') {
      e.preventDefault();
      if (focusMode === 'animation') {
        const prevIdx = adjacentPlayableAnimationIndex(-1);
        if (prevIdx >= 0) playAnimationIndex(prevIdx);
      }
    } else if (e.code === 'ArrowRight' || e.code === 'KeyD') {
      e.preventDefault();
      selectAdjacentEntityGroup(1);
    } else if (e.code === 'ArrowLeft' || e.code === 'KeyA') {
      e.preventDefault();
      selectAdjacentEntityGroup(-1);
    }
  });

  return {
    loadCatalog,
    setVisible,
    updateAnimation
  };
}
