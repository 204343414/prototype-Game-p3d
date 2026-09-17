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

  let entityData = null;
  let currentCategory = 'all';
  let currentSearchQuery = '';
  let currentSelectedEntity = null;
  let currentShapeName = null;

  let renderedMeshList = [];
  let currentAnimations = [];
  let currentAnimIdx = -1;
  let currentAnimTrack = null;
  let isPlayingAnim = false;
  let animTime = 0;
  let showSkeleton = false;
  let focusMode = 'entity';

  let activeBones = [];
  let activeSkeleton = null;
  let activeMixer = null;
  let activeAction = null;
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

  // Build Real Three.js Bone Hierarchy & Skeleton Instance (Skeleton_2)
  function buildSkeletonHierarchy(skeletons) {
    while (skeletonGroup.children.length > 0) {
      const c = skeletonGroup.children[0];
      skeletonGroup.remove(c);
      if (c.geometry) c.geometry.dispose();
      if (c.material) c.material.dispose();
    }
    activeBones = [];
    activeSkeleton = null;
    boneLinesMesh = null;
    bonePointsMesh = null;
    boneLinePairs = [];

    if (!skeletons || !skeletons.length) return;

    const skel = skeletons[0];
    const joints = skel.joints || [];
    if (!joints.length) return;

    const bones = [];
    for (let i = 0; i < joints.length; i++) {
      const j = joints[i];
      const bone = new THREE.Bone();
      bone.name = j.name || `Joint_${i}`;

      const m = new THREE.Matrix4();
      if (j.matrix && j.matrix.length === 16) {
        m.fromArray(j.matrix);
      }
      m.decompose(bone.position, bone.quaternion, bone.scale);

      bone.userData = {
        origPos: bone.position.clone(),
        origRot: bone.quaternion.clone(),
        origScale: bone.scale.clone(),
        parentIdx: j.parent,
        index: i,
        name: (j.name || '').toLowerCase()
      };
      bones.push(bone);
    }

    for (let i = 0; i < joints.length; i++) {
      const pIdx = joints[i].parent;
      if (pIdx >= 0 && pIdx < bones.length && pIdx !== i) {
        bones[pIdx].add(bones[i]);
        boneLinePairs.push({ child: bones[i], parent: bones[pIdx] });
      } else {
        skeletonGroup.add(bones[i]);
      }
    }

    // Update World Matrix before computing bone inverses
    skeletonGroup.updateMatrixWorld(true);

    const boneInverses = bones.map(bone => bone.matrixWorld.clone().invert());
    activeBones = bones;
    activeSkeleton = new THREE.Skeleton(bones, boneInverses);

    // Create Line Segments for skeleton visualizer
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
      boneLinesMesh.renderOrder = 999;
      skeletonGroup.add(boneLinesMesh);
    }

    const nodePositions = new Float32Array(bones.length * 3);
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
    bonePointsMesh.renderOrder = 1000;
    skeletonGroup.add(bonePointsMesh);

    skeletonGroup.visible = showSkeleton;
  }

  function updateSkeletonVisualizerPositions() {
    if (!activeBones.length) return;

    if (boneLinesMesh && boneLinePairs.length > 0) {
      const posAttr = boneLinesMesh.geometry.getAttribute('position');
      const arr = posAttr.array;
      const v0 = new THREE.Vector3();
      const v1 = new THREE.Vector3();

      for (let i = 0; i < boneLinePairs.length; i++) {
        const pair = boneLinePairs[i];
        pair.parent.getWorldPosition(v0);
        pair.child.getWorldPosition(v1);

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
        arr[i * 3 + 0] = v.x;
        arr[i * 3 + 1] = v.y;
        arr[i * 3 + 2] = v.z;
      }
      posAttr.needsUpdate = true;
    }
  }

  function playAnimationClip(animTrack) {
    if (!animTrack || !activeBones.length) return;

    if (activeMixer) {
      activeMixer.stopAllAction();
      activeMixer.uncacheRoot(entityGroup);
    }

    activeMixer = new THREE.AnimationMixer(entityGroup);
    const tracks = [];

    const boneMap = new Map();
    for (const b of activeBones) {
      boneMap.set(b.name, b);
      boneMap.set(b.name.toLowerCase(), b);
    }

    for (const g of animTrack.groups || []) {
      const b = boneMap.get(g.name) || boneMap.get(g.name.toLowerCase());
      if (!b || !g.rot) continue;

      const times = Float32Array.from(g.rot.times);
      const values = Float32Array.from(g.rot.values.flat());

      if (times.length > 0 && values.length === times.length * 4) {
        tracks.push(
          new THREE.QuaternionKeyframeTrack(
            `${b.name}.quaternion`,
            times,
            values
          )
        );
      }
    }

    if (tracks.length > 0) {
      const clip = new THREE.AnimationClip(animTrack.name, animTrack.duration, tracks);
      activeAction = activeMixer.clipAction(clip);
      activeAction.setLoop(animTrack.cyclic ? THREE.LoopRepeat : THREE.LoopRepeat);
      activeAction.play();
      isPlayingAnim = true;
    }
  }

  function playAnimationIndex(idx) {
    if (!currentAnimations || idx < 0 || idx >= currentAnimations.length) return;
    currentAnimIdx = idx;
    currentAnimTrack = currentAnimations[idx];
    focusMode = 'animation';

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

  function updateMeshInspector(meshes, animations, skeletons) {
    const listEl = document.getElementById('inspector-mesh-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    const jointCount = skeletons?.[0]?.joints?.length || 0;
    const skelRow = document.createElement('div');
    skelRow.className = 'mesh-row';
    skelRow.style.borderLeft = '3px solid #fb923c';
    skelRow.innerHTML = `
      <div class="mesh-info">
        <div class="mesh-name" style="color:#7fd4ff; font-weight:600;">🦴 骨骼系统 (${jointCount} 个关节)</div>
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

    meshes.forEach((item, idx) => {
      const row = document.createElement('div');
      row.className = 'mesh-row';
      row.innerHTML = `
        <div class="mesh-info">
          <div class="mesh-name" title="${item.meshData.geometry_name}">${item.meshData.geometry_name || `Part_${idx + 1}`}</div>
          <div class="mesh-counts">${item.meshData.vertex_count} 顶 · ${item.meshData.triangle_count} 面</div>
        </div>
        <input type="checkbox" class="mesh-toggle" checked id="toggle-mesh-${idx}" title="显示/隐藏此部件" />
      `;
      const toggle = row.querySelector(`#toggle-mesh-${idx}`);
      toggle.onchange = (e) => {
        item.threeMesh.visible = e.target.checked;
      };
      listEl.appendChild(row);
    });

    currentAnimations = animations || [];
    currentAnimIdx = -1;
    currentAnimTrack = currentAnimations[0] || null;

    if (currentAnimations.length > 0) {
      const animHeader = document.createElement('div');
      animHeader.className = 'anim-section-title';
      animHeader.style.display = 'flex';
      animHeader.style.justifyContent = 'space-between';
      animHeader.style.alignItems = 'center';
      animHeader.innerHTML = `
        <span>🎬 真实动画 (${currentAnimations.length} 个)</span>
        <button id="anim-play-btn" class="inspector-btn" style="color:#a78bfa; border-color:#8b5cf6;">▶ 播放 (Space)</button>
      `;
      listEl.appendChild(animHeader);

      const playBtn = animHeader.querySelector('#anim-play-btn');
      playBtn.onclick = () => {
        if (!currentAnimations.length) return;
        if (currentAnimIdx < 0) playAnimationIndex(0);
        else {
          isPlayingAnim = !isPlayingAnim;
          playBtn.textContent = isPlayingAnim ? '⏸ 暂停' : '▶ 播放 (Space)';
          playBtn.style.background = isPlayingAnim ? '#4c1d95' : '#1a2230';
          if (activeAction) activeAction.paused = !isPlayingAnim;
        }
      };

      currentAnimations.forEach((anim, aidx) => {
        const aRow = document.createElement('div');
        aRow.className = 'anim-row';
        aRow.style.cursor = 'pointer';
        aRow.title = '点击播放此动作 (上下键快速切换)';
        aRow.innerHTML = `
          <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:150px;">${anim.name}</span>
          <span style="opacity:0.8;">${anim.frames}f · ${anim.duration}s</span>
        `;
        aRow.onclick = () => {
          playAnimationIndex(aidx);
        };
        listEl.appendChild(aRow);
      });

      playAnimationIndex(0);
    }
  }

  async function selectEntity(item, shapeName = null) {
    currentSelectedEntity = item;
    currentShapeName = shapeName;
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
    if (activeMixer) {
      activeMixer.stopAllAction();
      activeMixer = null;
    }

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

        if (activeSkeleton && activeBones.length > 0 && mesh.skin_indices && mesh.skin_weights) {
          const sIndices = floats(mesh.skin_indices, Uint16Array, 2);
          const sWeights = floats(mesh.skin_weights, Float32Array, 4);

          geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(sIndices, 4));
          geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(sWeights, 4));

          const skinnedMesh = new THREE.SkinnedMesh(geometry, material);
          entityGroup.add(skinnedMesh);

          skinnedMesh.add(activeBones[0]);
          skinnedMesh.updateMatrixWorld(true);
          skinnedMesh.bind(activeSkeleton, skinnedMesh.matrixWorld);

          rendered = skinnedMesh;
        } else {
          rendered = new THREE.Mesh(geometry, material);
          entityGroup.add(rendered);
        }

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

        updateMeshInspector(renderedMeshList, data.animations, data.skeletons);

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

        const animInfo = data.animations?.length ? ` · ${data.animations.length} 个骨骼动作` : '';
        onStatus(`已呈现 3D 实体：${item.name} (${data.meshes.length} 个部件, ${data.skeletons?.[0]?.joints?.length || 0} 骨骼${animInfo})`);
      } else {
        onStatus(`实体 ${item.name} 结构已解析 (无直接网格数据)`);
      }
    } catch (e) {
      console.error(e);
      onStatus('实体网格加载异常：' + e.message);
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
    `;

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

  function updateAnimation(dt) {
    if (!isPlayingAnim) return;
    animTime += dt;

    if (activeMixer) {
      activeMixer.update(dt);
      if (activeSkeleton) {
        activeSkeleton.update();
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

  // Keyboard Shortcuts
  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

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
    } else if (e.code === 'ArrowDown' || e.code === 'ArrowRight' || e.code === 'KeyS' || e.code === 'KeyD') {
      e.preventDefault();
      if (focusMode === 'animation' && currentAnimations && currentAnimations.length > 0) {
        const nextIdx = (currentAnimIdx + 1) % currentAnimations.length;
        playAnimationIndex(nextIdx);
      }
    } else if (e.code === 'ArrowUp' || e.code === 'ArrowLeft' || e.code === 'KeyW' || e.code === 'KeyA') {
      e.preventDefault();
      if (focusMode === 'animation' && currentAnimations && currentAnimations.length > 0) {
        const prevIdx = (currentAnimIdx - 1 + currentAnimations.length) % currentAnimations.length;
        playAnimationIndex(prevIdx);
      }
    }
  });

  return {
    loadCatalog,
    setVisible,
    updateAnimation
  };
}
