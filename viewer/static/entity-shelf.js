import * as THREE from 'three';
import { makeMapTexture, makeMapMaterial } from './map-materials.js';

const CAT_ICONS = {
  powers: '⚡',
  vehicles: '🚗',
  characters: '🧟',
  props: '🏢'
};

const THUMB_CACHE = new Map();

// IndexedDB persistence for 1:1 snapshots
const DB_NAME = 'PrototypeEntitySnapshotsDB';
const DB_VERSION = 1;
const STORE_NAME = 'snapshots';

function openSnapshotDB() {
  return new Promise((resolve) => {
    if (typeof window === 'undefined' || !window.indexedDB) {
      resolve(null);
      return;
    }
    try {
      const req = window.indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (e) => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: 'id' });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => {
        console.warn('IndexedDB open error:', req.error);
        resolve(null);
      };
    } catch (e) {
      console.warn('IndexedDB init exception:', e);
      resolve(null);
    }
  });
}

async function loadAllCachedSnapshots() {
  const db = await openSnapshotDB();
  if (!db) return 0;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, 'readonly');
      const store = tx.objectStore(STORE_NAME);
      const req = store.getAll();
      req.onsuccess = () => {
        const records = req.result || [];
        for (const rec of records) {
          if (rec.id && rec.dataUrl) {
            THUMB_CACHE.set(rec.id, rec.dataUrl);
          }
        }
        resolve(records.length);
      };
      req.onerror = () => resolve(0);
    } catch (e) {
      console.warn('IndexedDB read error:', e);
      resolve(0);
    }
  });
}

async function persistSnapshot(id, dataUrl) {
  THUMB_CACHE.set(id, dataUrl);
  const db = await openSnapshotDB();
  if (!db) return;
  try {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    store.put({ id, dataUrl, time: Date.now() });
  } catch (e) {
    console.warn('IndexedDB write error:', e);
  }
}

async function clearAllSnapshotsDB() {
  THUMB_CACHE.clear();
  const db = await openSnapshotDB();
  if (!db) return;
  try {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    store.clear();
  } catch (e) {
    console.warn('IndexedDB clear error:', e);
  }
}

function floats(encoded, Type, size) {
  if (!encoded) return new Type(0);
  const raw = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
  const view = new DataView(raw.buffer);
  const count = Math.floor(raw.length / size);
  const values = new Type(count);
  for (let i = 0; i < count; i++) {
    values[i] = size === 4 ? view.getFloat32(i * size, true) : view.getUint16(i * size, true);
  }
  return values;
}

export function createEntityShelf({ scene, camera, controls, renderer, onStatus, onSelectEntity }) {
  let activeCategory = 'powers';
  let entityData = null;
  let currentEntity = null;
  let currentShape = null;
  
  const entityGroup = new THREE.Group();
  entityGroup.name = 'Entity Viewer Object';
  scene.add(entityGroup);

  const skeletonGroup = new THREE.Group();
  skeletonGroup.name = 'Skeleton Lines';
  scene.add(skeletonGroup);

  let renderedMeshList = [];
  let currentAnimations = [];
  let currentAnimIdx = -1;
  let isPlayingAnim = false;
  let animTime = 0;
  let showSkeleton = false;
  let focusMode = 'entity'; // 'entity' or 'animation'

  // Batch snapshot state
  let isBatchScanning = false;

  // Offscreen 1:1 renderer for taking high-res square 45-degree snapshots (256x256)
  const offCanvas = document.createElement('canvas');
  offCanvas.width = 256;
  offCanvas.height = 256;
  let offRenderer = null;
  try {
    offRenderer = new THREE.WebGLRenderer({
      canvas: offCanvas,
      antialias: true,
      alpha: true,
      preserveDrawingBuffer: true
    });
    offRenderer.setSize(256, 256);
  } catch (e) {
    console.warn('Offscreen renderer unavailable:', e);
  }

  async function loadCatalog(artPath) {
    onStatus('正在从 art.rcf 读取四大分类实体清单…');
    try {
      // 1. Preload any existing persistent snapshots from IndexedDB
      await loadAllCachedSnapshots();

      // 2. Fetch catalog list from backend
      const resp = await fetch(`/api/entities?path=${encodeURIComponent(artPath || '')}`);
      const data = await resp.json();
      if (data.error) {
        onStatus('读取实体错误：' + data.error);
        return;
      }
      entityData = data;
      renderCounts(data.counts);
      updateStatsBadge();
      renderGrid();
      onStatus(`已索引四大类共 ${data.total_entities} 个实体 (已恢复 ${THUMB_CACHE.size} 个持久化快照)`);
    } catch (err) {
      onStatus('请求实体清单失败：' + err.message);
    }
  }

  function renderCounts(counts) {
    for (const [cat, count] of Object.entries(counts || {})) {
      const el = document.getElementById(`count-${cat}`);
      if (el) el.textContent = count;
    }
    const toggleCount = document.getElementById('toggle-count');
    if (toggleCount && entityData) {
      toggleCount.textContent = entityData.total_entities;
    }
  }

  function updateStatsBadge() {
    const badge = document.getElementById('snapshot-stats-badge');
    if (!badge || !entityData) return;
    const total = entityData.total_entities || 0;
    const cached = THUMB_CACHE.size;
    badge.textContent = `已缓存: ${cached} / ${total}`;
    if (cached >= total && total > 0) {
      badge.style.color = '#4ade80';
      badge.style.borderColor = '#22c55e';
    } else {
      badge.style.color = '#94a3b8';
      badge.style.borderColor = '#334155';
    }
  }

  function captureCameraForBounds(bounds) {
    const center = bounds.getCenter(new THREE.Vector3());
    const boxSize = bounds.getSize(new THREE.Vector3());
    const maxDim = Math.max(boxSize.x, boxSize.y, boxSize.z) || 2.0;

    // Strict 1:1 Square Perspective Camera
    const snapCam = new THREE.PerspectiveCamera(45, 1.0, 0.01, 2000);
    const dist = maxDim * 1.5;
    // 45-degree angled isometric position
    snapCam.position.set(center.x + dist * 0.72, center.y + dist * 0.55, center.z + dist * 0.72);
    snapCam.lookAt(center);
    return snapCam;
  }

  function captureCurrentEntitySnapshot() {
    if (!offRenderer || !entityGroup.children.length) return null;
    try {
      const bounds = new THREE.Box3().setFromObject(entityGroup);
      const snapCam = captureCameraForBounds(bounds);

      const snapScene = new THREE.Scene();
      snapScene.background = null;
      snapScene.add(new THREE.HemisphereLight(0xffffff, 0x334455, 1.6));
      const light = new THREE.DirectionalLight(0xffffff, 1.8);
      light.position.set(4, 7, 5);
      snapScene.add(light);

      const snapGroup = entityGroup.clone(true);
      snapScene.add(snapGroup);

      offRenderer.render(snapScene, snapCam);
      const dataUrl = offCanvas.toDataURL('image/webp', 0.85);

      snapGroup.traverse(c => {
        if (c.geometry) c.geometry.dispose();
      });

      return dataUrl;
    } catch (e) {
      console.warn('Snapshot capture failed:', e);
      return null;
    }
  }

  async function generateSnapshotForEntity(item, artPath) {
    if (!offRenderer) return null;
    try {
      const targetShape = (item.category === 'props' ? (item.shapes?.[0] || item.id) : '');
      const params = new URLSearchParams({
        path: artPath || '',
        entry: item.entry_path,
        shape: targetShape
      });
      const resp = await fetch(`/api/entity_mesh?${params}`);
      const data = await resp.json();
      if (data.error || !data.meshes || !data.meshes.length) return null;

      // Build textures
      const textures = new Map();
      for (const desc of data.textures || []) {
        try {
          textures.set(desc.key, makeMapTexture(desc));
        } catch (e) {
          console.warn('Texture parse failed in snap:', desc.key, e);
        }
      }

      const snapScene = new THREE.Scene();
      snapScene.background = null;
      snapScene.add(new THREE.HemisphereLight(0xffffff, 0x334455, 1.6));
      const dirLight = new THREE.DirectionalLight(0xffffff, 1.8);
      dirLight.position.set(4, 7, 5);
      snapScene.add(dirLight);

      const snapGroup = new THREE.Group();
      snapScene.add(snapGroup);

      const bounds = new THREE.Box3();
      const createdGeoms = [];
      const createdMats = [];

      for (const mesh of data.meshes) {
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
        const rendered = new THREE.Mesh(geometry, material);
        snapGroup.add(rendered);

        geometry.computeBoundingBox();
        bounds.union(geometry.boundingBox);

        createdGeoms.push(geometry);
        createdMats.push(material);
      }

      if (!snapGroup.children.length) return null;

      const snapCam = captureCameraForBounds(bounds);
      offRenderer.render(snapScene, snapCam);
      const dataUrl = offCanvas.toDataURL('image/webp', 0.85);

      // Free memory
      createdGeoms.forEach(g => g.dispose());
      createdMats.forEach(m => m.dispose());
      textures.forEach(t => t.dispose());

      return dataUrl;
    } catch (e) {
      console.warn('Error in generateSnapshotForEntity:', item.id, e);
      return null;
    }
  }

  function updateCardThumbDOM(id, dataUrl) {
    document.querySelectorAll(`.entity-card[data-id="${id}"] .card-preview-thumb`).forEach(thumb => {
      thumb.style.backgroundImage = `url(${dataUrl})`;
      thumb.style.backgroundSize = 'contain';
      thumb.style.backgroundPosition = 'center';
      thumb.style.backgroundRepeat = 'no-repeat';
      const isoBox = thumb.querySelector('.iso-box');
      const catIcon = thumb.querySelector('.cat-icon');
      if (isoBox) isoBox.style.display = 'none';
      if (catIcon) catIcon.style.display = 'none';
    });
  }

  function renderCard(item, isDrawer = false) {
    const card = document.createElement('div');
    card.className = `entity-card ${currentEntity?.id === item.id ? 'active' : ''}`;
    card.dataset.id = item.id;
    
    const icon = CAT_ICONS[item.category] || '📦';
    const shapeCount = item.shape_count || item.geometry_count || 1;
    const jointCount = item.total_joints || (item.skeleton_count > 0 ? (item.skeleton_count * 12) : 0);
    const animCount = item.animation_count || 0;

    const cachedThumb = THUMB_CACHE.get(item.id);

    card.innerHTML = `
      <div class="card-preview-thumb" style="${cachedThumb ? `background-image:url(${cachedThumb}); background-size:contain; background-position:center; background-repeat:no-repeat;` : ''}">
        ${!cachedThumb ? `
          <div class="iso-box" style="${isDrawer ? 'width:32px; height:32px;' : ''}"></div>
          <span class="cat-icon" style="${isDrawer ? 'font-size:22px;' : ''}">${icon}</span>
        ` : ''}
      </div>
      <div class="card-title" title="${item.name}">${item.name}</div>
      <div class="card-meta">
        <span class="card-badge geom" title="网格/形态数量">🧊 ${shapeCount}</span>
        ${jointCount > 0 ? `<span class="card-badge skel" title="骨骼关节数">🦴 ${jointCount}</span>` : ''}
        ${animCount > 0 ? `<span class="card-badge anim" title="动画片段">🎬 ${animCount}</span>` : ''}
      </div>
    `;

    card.onclick = () => {
      focusMode = 'entity';
      selectEntity(item);
    };
    return card;
  }

  function renderGrid() {
    const gridFull = document.getElementById('entity-grid-full');
    const gridDrawer = document.getElementById('entity-grid-drawer');
    if (!entityData) return;

    const items = (entityData.categories && entityData.categories[activeCategory]) || [];
    
    // 1. Full-screen Grid
    if (gridFull) {
      gridFull.innerHTML = '';
      const searchFull = (document.getElementById('entity-search-full')?.value || '').trim().toLowerCase();
      const filteredFull = items.filter(item => 
        !searchFull || 
        item.name.toLowerCase().includes(searchFull) || 
        item.id.toLowerCase().includes(searchFull)
      );

      if (!filteredFull.length) {
        gridFull.innerHTML = `<div style="grid-column:1/-1; padding:40px; text-align:center; color:#6b7280; font-size:14px;">未找到匹配的实体</div>`;
      } else {
        for (const item of filteredFull) {
          gridFull.appendChild(renderCard(item, false));
        }
      }
    }

    // 2. Drawer Grid
    if (gridDrawer) {
      gridDrawer.innerHTML = '';
      const searchDrawer = (document.getElementById('entity-search-drawer')?.value || '').trim().toLowerCase();
      const filteredDrawer = items.filter(item => 
        !searchDrawer || 
        item.name.toLowerCase().includes(searchDrawer) || 
        item.id.toLowerCase().includes(searchDrawer)
      );

      if (!filteredDrawer.length) {
        gridDrawer.innerHTML = `<div style="grid-column:1/-1; padding:20px; text-align:center; color:#6b7280; font-size:11px;">无匹配项</div>`;
      } else {
        for (const item of filteredDrawer) {
          gridDrawer.appendChild(renderCard(item, true));
        }
      }
    }
  }

  function buildSkeletonVisualizer(skeletons) {
    while (skeletonGroup.children.length > 0) {
      const c = skeletonGroup.children[0];
      skeletonGroup.remove(c);
      if (c.geometry) c.geometry.dispose();
      if (c.material) c.material.dispose();
    }

    if (!skeletons || !skeletons.length) return;

    const skel = skeletons[0];
    const joints = skel.joints || [];
    if (!joints.length) return;

    // Compute world positions of joints from local matrices
    const worldMatrices = [];
    const jointPositions = [];

    for (let i = 0; i < joints.length; i++) {
      const j = joints[i];
      const m = new THREE.Matrix4();
      if (j.matrix && j.matrix.length === 16) {
        m.fromArray(j.matrix);
      }
      
      const parentIdx = j.parent;
      if (parentIdx >= 0 && parentIdx < i && worldMatrices[parentIdx]) {
        m.multiplyMatrices(worldMatrices[parentIdx], m);
      }
      worldMatrices.push(m);

      const pos = new THREE.Vector3();
      pos.setFromMatrixPosition(m);
      jointPositions.push(pos);
    }

    // Build line segments between joint and parent
    const linePositions = [];
    for (let i = 0; i < joints.length; i++) {
      const pIdx = joints[i].parent;
      if (pIdx >= 0 && pIdx < joints.length) {
        const p1 = jointPositions[i];
        const p0 = jointPositions[pIdx];
        if (p1.distanceTo(p0) < 5.0) {
          linePositions.push(p0.x, p0.y, p0.z);
          linePositions.push(p1.x, p1.y, p1.z);
        }
      }
    }

    if (linePositions.length > 0) {
      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.Float32BufferAttribute(linePositions, 3));
      const mat = new THREE.LineBasicMaterial({
        color: 0xff7722,
        linewidth: 2,
        depthTest: false,
        transparent: true,
        opacity: 0.95
      });
      const lines = new THREE.LineSegments(geom, mat);
      lines.renderOrder = 999;
      skeletonGroup.add(lines);
    }

    // Add glowing joint node spheres
    const nodeGeom = new THREE.BufferGeometry();
    const nodePos = [];
    jointPositions.forEach(p => { nodePos.push(p.x, p.y, p.z); });
    nodeGeom.setAttribute('position', new THREE.Float32BufferAttribute(nodePos, 3));
    const nodeMat = new THREE.PointsMaterial({
      color: 0x7fd4ff,
      size: 6,
      sizeAttenuation: false,
      depthTest: false,
      transparent: true,
      opacity: 0.9
    });
    const nodes = new THREE.Points(nodeGeom, nodeMat);
    nodes.renderOrder = 1000;
    skeletonGroup.add(nodes);

    skeletonGroup.visible = showSkeleton;
  }

  function playAnimationIndex(idx) {
    if (!currentAnimations || idx < 0 || idx >= currentAnimations.length) return;
    currentAnimIdx = idx;
    const anim = currentAnimations[idx];
    animTime = 0;
    isPlayingAnim = true;
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

    onStatus(`正在播放动作 [${idx + 1}/${currentAnimations.length}]：${anim.name} (${anim.frames} 帧 @ ${anim.fps} fps)`);
  }

  function updateMeshInspector(meshes, animations, skeletons) {
    const listEl = document.getElementById('inspector-mesh-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    // Skeleton Line Controller Row
    const jointCount = skeletons?.[0]?.joints?.length || 0;
    const skelRow = document.createElement('div');
    skelRow.className = 'mesh-row';
    skelRow.style.borderLeft = '3px solid #fb923c';
    skelRow.innerHTML = `
      <div class="mesh-info">
        <div class="mesh-name" style="color:#7fd4ff; font-weight:600;">🦴 骨骼线条 (${jointCount} 个关节)</div>
      </div>
      <input type="checkbox" class="mesh-toggle" ${showSkeleton ? 'checked' : ''} id="toggle-skeleton-lines" title="开启/关闭骨骼透视线条 (快捷键 B)" />
    `;
    const skelToggle = skelRow.querySelector('#toggle-skeleton-lines');
    if (skelToggle) {
      skelToggle.onchange = (e) => {
        showSkeleton = e.target.checked;
        skeletonGroup.visible = showSkeleton;
      };
    }
    listEl.appendChild(skelRow);

    // Render Submeshes List
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

    // Render Animation Section
    currentAnimations = animations || [];
    currentAnimIdx = -1;

    if (currentAnimations.length > 0) {
      const animHeader = document.createElement('div');
      animHeader.className = 'anim-section-title';
      animHeader.style.display = 'flex';
      animHeader.style.justifyContent = 'space-between';
      animHeader.style.alignItems = 'center';
      animHeader.innerHTML = `
        <span>🎬 引用动画 (${currentAnimations.length} 个)</span>
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
    }
  }

  async function selectEntity(item, shapeName = null) {
    currentEntity = item;
    currentShape = shapeName;
    renderGrid();
    if (onSelectEntity) onSelectEntity(item);

    onStatus(`正在解析 3D 实体模型：${item.name}…`);

    // Clear previous entity meshes & skeleton
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
    isPlayingAnim = false;

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

      // Build textures
      const textures = new Map();
      for (const desc of data.textures || []) {
        try {
          textures.set(desc.key, makeMapTexture(desc));
        } catch (e) {
          console.warn('Texture parse failed:', desc.key, e);
        }
      }

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
        const rendered = new THREE.Mesh(geometry, material);
        entityGroup.add(rendered);

        geometry.computeBoundingBox();
        bounds.union(geometry.boundingBox);
        hasMeshes = true;

        renderedMeshList.push({
          meshData: mesh,
          threeMesh: rendered,
          origY: rendered.position.y
        });
      }

      // Build Skeleton Line Visualizer
      buildSkeletonVisualizer(data.skeletons);

      if (hasMeshes) {
        const center = bounds.getCenter(new THREE.Vector3());
        const size = bounds.getSize(new THREE.Vector3()).length() || 2.0;

        // Position camera at 45-degree angled perspective
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

        // Update Left-Side Mesh & Animation Inspector
        updateMeshInspector(renderedMeshList, data.animations, data.skeletons);

        // Take snapshot for card thumbnail and persist to IndexedDB
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

        const animInfo = data.animations?.length ? ` · ${data.animations.length} 个动作片段` : '';
        onStatus(`已呈现 3D 实体：${item.name} (${data.meshes.length} 个网格, ${data.textures.length} 张贴图${animInfo})`);
      } else {
        onStatus(`实体 ${item.name} 结构已解析 (无直接网格数据)`);
        updateMeshInspector([], data.animations, data.skeletons);
      }

    } catch (err) {
      onStatus('渲染 3D 实体失败：' + err.message);
    }
  }

  // Animation Update loop hook
  function updateAnimation(dt) {
    if (!isPlayingAnim || !entityGroup.visible) return;
    animTime += dt;
    // Animate subtle idle breath / transform oscillation
    const wobble = Math.sin(animTime * 4.0) * 0.03;
    entityGroup.position.y = wobble;
    skeletonGroup.position.y = wobble;
  }

  // Batch Auto-Capture & Persistent Cache System
  async function startBatchSnapshot() {
    if (!entityData) return;
    if (isBatchScanning) {
      isBatchScanning = false;
      updateBatchUI();
      onStatus('已停止自动快照扫描');
      return;
    }

    isBatchScanning = true;
    updateBatchUI();

    const allCategories = ['powers', 'vehicles', 'characters', 'props'];
    const allItems = [];
    for (const cat of allCategories) {
      const items = entityData.categories?.[cat] || [];
      for (const item of items) {
        allItems.push(item);
      }
    }

    const total = allItems.length;
    let processed = 0;

    const artPath = document.getElementById('map-shared')?.value || '';
    onStatus(`开始批量拍摄全部 ${total} 个实体的 1:1 3D 高清快照…`);

    for (let i = 0; i < total; i++) {
      if (!isBatchScanning) break;
      const item = allItems[i];
      processed++;

      if (THUMB_CACHE.has(item.id)) {
        updateBatchProgress(processed, total, item.name, true);
        continue;
      }

      updateBatchProgress(processed, total, item.name, false);

      try {
        const snap = await generateSnapshotForEntity(item, artPath);
        if (snap) {
          await persistSnapshot(item.id, snap);
          updateCardThumbDOM(item.id, snap);
        }
      } catch (e) {
        console.warn('Batch snap item error:', item.id, e);
      }

      updateStatsBadge();
      await new Promise(r => setTimeout(r, 20));
    }

    const wasScanning = isBatchScanning;
    isBatchScanning = false;
    updateBatchUI();
    updateStatsBadge();

    if (wasScanning) {
      onStatus(`🎉 批量快照处理完成！已持久化缓存 ${THUMB_CACHE.size} / ${total} 个实体缩略图`);
    }
  }

  function updateBatchUI() {
    const btn = document.getElementById('batch-snapshot-btn');
    if (!btn) return;
    if (isBatchScanning) {
      btn.classList.add('running');
      btn.innerHTML = `⏹ 停止拍照`;
      btn.title = '点击停止正在进行的批量拍照';
    } else {
      btn.classList.remove('running');
      btn.innerHTML = `📸 自动拍照并持久缓存`;
      btn.title = '一键后台轮询生成所有实体的 1:1 3D 高清快照并永久缓存到浏览器';
    }
  }

  function updateBatchProgress(current, total, name, isSkipped) {
    const btn = document.getElementById('batch-snapshot-btn');
    const pct = Math.round((current / total) * 100);
    if (btn && isBatchScanning) {
      btn.innerHTML = `⏹ 停止 (${current}/${total} ${pct}%)`;
    }
    const statusMsg = isSkipped 
      ? `[${current}/${total}] ${name} (已从持久缓存读取)`
      : `正在拍摄 1:1 快照 [${current}/${total}]: ${name}…`;
    onStatus(statusMsg);
  }

  // Bind Batch Snapshot & Clear Buttons
  const batchBtn = document.getElementById('batch-snapshot-btn');
  if (batchBtn) {
    batchBtn.onclick = () => startBatchSnapshot();
  }

  const clearBtn = document.getElementById('clear-snapshot-btn');
  if (clearBtn) {
    clearBtn.onclick = async () => {
      if (confirm('确定要清空所有已持久化缓存的实体缩略图吗？')) {
        if (isBatchScanning) {
          isBatchScanning = false;
          updateBatchUI();
        }
        await clearAllSnapshotsDB();
        updateStatsBadge();
        renderGrid();
        onStatus('已清空本地持久缓存的实体快照缩略图');
      }
    };
  }

  // Bind All Show / Hide buttons
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

  // Setup Fullscreen Category Tabs
  document.querySelectorAll('.shelf-header .cat-tab').forEach(tab => {
    tab.addEventListener('click', (e) => {
      const btn = e.target.closest('.cat-tab') || tab;
      document.querySelectorAll('.shelf-header .cat-tab').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      activeCategory = btn.dataset.cat;
      // Sync drawer tabs
      document.querySelectorAll('.drawer-cat-tab').forEach(t => t.classList.toggle('active', t.dataset.cat === activeCategory));
      renderGrid();
    });
  });

  // Setup Drawer Category Tabs
  document.querySelectorAll('.drawer-cat-tab').forEach(tab => {
    tab.addEventListener('click', (e) => {
      const btn = e.target.closest('.drawer-cat-tab') || tab;
      document.querySelectorAll('.drawer-cat-tab').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      activeCategory = btn.dataset.cat;
      // Sync full tabs
      document.querySelectorAll('.shelf-header .cat-tab').forEach(t => t.classList.toggle('active', t.dataset.cat === activeCategory));
      renderGrid();
    });
  });

  const searchFull = document.getElementById('entity-search-full');
  if (searchFull) {
    searchFull.addEventListener('input', () => renderGrid());
  }

  const searchDrawer = document.getElementById('entity-search-drawer');
  if (searchDrawer) {
    searchDrawer.addEventListener('input', () => renderGrid());
  }

  // Global Keyboard Navigation
  window.addEventListener('keydown', (e) => {
    if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;

    if (e.key === 'ArrowUp' || e.key === 'ArrowDown' || e.key === 'ArrowLeft' || e.key === 'ArrowRight' || e.key === 'w' || e.key === 's') {
      e.preventDefault();
      const isUp = (e.key === 'ArrowUp' || e.key === 'ArrowLeft' || e.key === 'w');

      // 1. If in Animation browsing mode
      if (focusMode === 'animation' && currentAnimations && currentAnimations.length > 0) {
        let nextIdx = isUp ? (currentAnimIdx - 1) : (currentAnimIdx + 1);
        if (nextIdx < 0) nextIdx = currentAnimations.length - 1;
        if (nextIdx >= currentAnimations.length) nextIdx = 0;
        playAnimationIndex(nextIdx);
        return;
      }

      // 2. Otherwise in Entity browsing mode
      if (entityData) {
        const items = entityData.categories?.[activeCategory] || [];
        if (!items.length) return;
        const curIdx = items.findIndex(it => it.id === currentEntity?.id);
        let nextIdx = isUp ? (curIdx - 1) : (curIdx + 1);
        if (nextIdx < 0) nextIdx = items.length - 1;
        if (nextIdx >= items.length) nextIdx = 0;
        selectEntity(items[nextIdx]);
      }
    } else if (e.code === 'Space') {
      e.preventDefault();
      if (currentAnimations.length > 0) {
        if (currentAnimIdx < 0) playAnimationIndex(0);
        else {
          isPlayingAnim = !isPlayingAnim;
          const playBtn = document.getElementById('anim-play-btn');
          if (playBtn) {
            playBtn.textContent = isPlayingAnim ? '⏸ 暂停' : '▶ 播放 (Space)';
            playBtn.style.background = isPlayingAnim ? '#4c1d95' : '#1a2230';
          }
        }
      }
    } else if (e.key === 'b' || e.key === 'B') {
      showSkeleton = !showSkeleton;
      skeletonGroup.visible = showSkeleton;
      const toggle = document.getElementById('toggle-skeleton-lines');
      if (toggle) toggle.checked = showSkeleton;
    } else if (e.key === 'r' || e.key === 'R' || e.key === 'Home') {
      if (currentEntity) selectEntity(currentEntity, currentShape);
    }
  });

  return {
    loadCatalog,
    selectEntity,
    renderGrid,
    updateAnimation,
    startBatchSnapshot,
    setVisible(visible) {
      entityGroup.visible = visible;
      skeletonGroup.visible = visible && showSkeleton;
    }
  };
}
