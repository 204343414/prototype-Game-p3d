/**
 * Prototype 1 Pure3D Entity & Asset Shelf Viewer
 * Full 4-Category Catalog, 3D Mesh Inspection, Skeleton_2 Bind Pose & GPU Animation Playback
 */

let shelfInitialized = false;
let currentShelfData = null;
let currentCategory = 'all';
let currentEntity = null;
let currentShape = null;

let activeBones = [];
let activeSkeleton = null;
let activeMixer = null;
let activeAction = null;
let currentAnimations = [];
let currentAnimIdx = -1;
let currentAnimTrack = null;
let isPlayingAnim = true;
let animClock = new THREE.Clock();
let boneLinesMesh = null;
let bonePointsMesh = null;
let boneLinePairs = [];
let renderedMeshList = [];
let showSkeleton = false;

const DB_NAME = 'PrototypeEntitySnapshotsDB';
const STORE_NAME = 'snapshots';
const THUMB_CACHE = new Map();

function openSnapshotDB() {
  return new Promise((resolve) => {
    if (!window.indexedDB) return resolve(null);
    const req = window.indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
  });
}

async function getStoredSnapshot(id) {
  if (THUMB_CACHE.has(id)) return THUMB_CACHE.get(id);
  const db = await openSnapshotDB();
  if (!db) return null;
  return new Promise((resolve) => {
    const tx = db.transaction([STORE_NAME], 'readonly');
    const store = tx.objectStore(STORE_NAME);
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
    const tx = db.transaction([STORE_NAME], 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    store.put({ id, dataUrl, updated: Date.now() });
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => resolve(false);
  });
}

export function initEntityShelf({
  container,
  viewerScene,
  viewerCamera,
  viewerControls,
  viewerRenderer,
  onSelectEntity,
  onStatus,
  makeMapTexture,
  makeMapMaterial
}) {
  if (shelfInitialized) return;
  shelfInitialized = true;

  const shelfContainer = document.getElementById('entity-shelf-container');
  const entityGroup = new THREE.Group();
  const skeletonGroup = new THREE.Group();
  skeletonGroup.renderOrder = 999;
  viewerScene.add(entityGroup);
  viewerScene.add(skeletonGroup);

  const scene = viewerScene;
  const camera = viewerCamera;
  const controls = viewerControls;
  const renderer = viewerRenderer;

  let allEntities = [];

  // 1. Fetch Catalog
  async function loadCatalog() {
    try {
      const artPath = document.getElementById('map-shared')?.value || '';
      const params = new URLSearchParams(artPath ? { path: artPath } : {});
      const resp = await fetch(`/api/entity_catalog?${params}`);
      const data = await resp.json();
      if (data.error) {
        onStatus('加载实体清单失败：' + data.error);
        return;
      }
      currentShelfData = data;
      allEntities = data.categories.flatMap(c => c.items);
      renderCategories(data.categories);
      renderGrid();
      updateStatsBadge();
    } catch (e) {
      onStatus('实体清单请求异常：' + e.message);
    }
  }

  function updateStatsBadge() {
    const badge = document.getElementById('shelf-stats-badge');
    if (!badge || !currentShelfData) return;
    const count = currentShelfData.total_items || allEntities.length;
    const cachedCount = THUMB_CACHE.size;
    badge.innerHTML = `已归档 <b>${count}</b> 款实体 · 快照 <b>${cachedCount}/${count}</b>`;
  }

  function renderCategories(categories) {
    const bar = document.getElementById('entity-category-tabs');
    if (!bar) return;
    bar.innerHTML = '';

    const allBtn = document.createElement('button');
    allBtn.className = 'shelf-tab active';
    allBtn.dataset.cat = 'all';
    allBtn.innerHTML = `🌟 全部 <span class="tab-count">${allEntities.length}</span>`;
    allBtn.onclick = () => switchCategory('all');
    bar.appendChild(allBtn);

    for (const cat of categories) {
      const btn = document.createElement('button');
      btn.className = 'shelf-tab';
      btn.dataset.cat = cat.id;
      btn.innerHTML = `${cat.icon} ${cat.name} <span class="tab-count">${cat.count}</span>`;
      btn.onclick = () => switchCategory(cat.id);
      bar.appendChild(btn);
    }
  }

  function switchCategory(catId) {
    currentCategory = catId;
    document.querySelectorAll('.shelf-tab').forEach(b => {
      b.classList.toggle('active', b.dataset.cat === catId);
    });
    renderGrid();
  }

  function getCategoryColor(catId) {
    switch (catId) {
      case 'powers': return '#ef4444';
      case 'vehicles': return '#3b82f6';
      case 'characters': return '#10b981';
      case 'pedestrians': return '#f59e0b';
      case 'props': return '#8b5cf6';
      default: return '#6b7280';
    }
  }

  function renderCard(item, isDrawer = false) {
    const card = document.createElement('div');
    card.className = `entity-card ${currentEntity?.id === item.id ? 'active' : ''}`;
    card.dataset.id = item.id;
    card.title = `${item.name} (${item.entry_path})\n分类: ${item.category}\n点击进入 3D 骨骼与网格检视`;

    const catColor = getCategoryColor(item.category);

    card.innerHTML = `
      <div class="entity-thumb-wrapper" style="border-top: 2px solid ${catColor};">
        <div class="entity-thumb-placeholder" id="thumb-ph-${item.id}">
          <span style="font-size: 26px; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));">${item.icon || '📦'}</span>
        </div>
        <img class="entity-thumb-img" id="thumb-img-${item.id}" alt="${item.name}" style="display:none;" />
        <span class="entity-badge" style="background:${catColor}cc;">${item.category}</span>
        ${item.shapes ? `<span class="entity-shapes-tag">${item.shapes.length} 形态</span>` : ''}
      </div>
      <div class="entity-meta">
        <div class="entity-title">${item.name}</div>
        <div class="entity-sub">${item.id}</div>
      </div>
    `;

    getStoredSnapshot(item.id).then(dataUrl => {
      if (dataUrl) {
        const img = card.querySelector(`#thumb-img-${item.id}`);
        const ph = card.querySelector(`#thumb-ph-${item.id}`);
        if (img && ph) {
          img.src = dataUrl;
          img.style.display = 'block';
          ph.style.display = 'none';
        }
      }
    });

    card.onclick = () => {
      selectEntity(item);
    };

    return card;
  }

  function renderGrid() {
    const grid = document.getElementById('entity-grid-items');
    if (!grid) return;
    grid.innerHTML = '';

    const filtered = currentCategory === 'all'
      ? allEntities
      : allEntities.filter(e => e.category === currentCategory);

    if (filtered.length === 0) {
      grid.innerHTML = `<div style="grid-column: 1/-1; padding: 40px; text-align: center; color: #94a3b8; font-size: 13px;">当前分类暂无实体项</div>`;
      return;
    }

    for (const item of filtered) {
      grid.appendChild(renderCard(item));
    }
  }

  function floats(b64, type = Float32Array, stride = 4) {
    if (!b64) return new type(0);
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new type(bytes.buffer);
  }

  // Build Real Three.js Bone Hierarchy & Skeleton Instance
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

    // 1. Instantiate THREE.Bone instances with decomposed local TRS
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

    // 2. Build Tree Hierarchy
    for (let i = 0; i < joints.length; i++) {
      const pIdx = joints[i].parent;
      if (pIdx >= 0 && pIdx < bones.length && pIdx !== i) {
        bones[pIdx].add(bones[i]);
        boneLinePairs.push({ child: bones[i], parent: bones[pIdx] });
      } else {
        skeletonGroup.add(bones[i]);
      }
    }

    // 3. Update world matrices before computing boneInverses
    skeletonGroup.updateMatrixWorld(true);

    // 4. Compute correct inverse bind matrices
    const boneInverses = bones.map(bone => bone.matrixWorld.clone().invert());

    activeBones = bones;
    activeSkeleton = new THREE.Skeleton(bones, boneInverses);

    // Create Dynamic Line Segments & Glowing Joint Nodes for Wireframe Debug
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

  // Play animation clip via Three.js AnimationMixer
  function playAnimationClip(animTrack) {
    if (!animTrack || !activeBones.length) return;

    if (activeMixer) {
      activeMixer.stopAllAction();
      activeMixer.uncacheRoot(entityGroup);
    }

    activeMixer = new THREE.AnimationMixer(entityGroup);
    const tracks = [];

    // Map each group to bone name
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
    onStatus(`正在播放真实动画 [${idx + 1}/${currentAnimations.length}]：${currentAnimTrack.name} (${currentAnimTrack.frames} 帧 @ ${currentAnimTrack.fps} fps)`);
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
        <span>🎬 动画片段 (${currentAnimations.length} 个)</span>
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
          if (activeAction) {
            activeAction.paused = !isPlayingAnim;
          }
        }
      };

      currentAnimations.forEach((anim, aidx) => {
        const aRow = document.createElement('div');
        aRow.className = 'anim-row';
        aRow.style.cursor = 'pointer';
        aRow.title = '点击播放此真实骨骼动画 (上下键快速切换)';
        aRow.innerHTML = `
          <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:150px;">${anim.name}</span>
          <span style="opacity:0.8;">${anim.frames}f · ${anim.duration}s</span>
        `;
        aRow.onclick = () => {
          playAnimationIndex(aidx);
        };
        listEl.appendChild(aRow);
      });

      // Auto play first animation
      playAnimationIndex(0);
    }
  }

  async function selectEntity(item, shapeName = null) {
    currentEntity = item;
    currentShape = shapeName;
    renderGrid();
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

      // 1. Build Real Three.js Skeleton Hierarchy First
      buildSkeletonHierarchy(data.skeletons);

      let hasMeshes = false;
      const bounds = new THREE.Box3();

      // 2. Build Meshes and Bind to Skeleton
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

        // Bind GPU SkinnedMesh if bones and skeleton exist
        if (activeSkeleton && activeBones.length > 0 && mesh.skin_indices && mesh.skin_weights) {
          const sIndices = floats(mesh.skin_indices, Uint16Array, 2);
          const sWeights = floats(mesh.skin_weights, Float32Array, 4);

          geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(sIndices, 4));
          geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(sWeights, 4));

          const skinnedMesh = new THREE.SkinnedMesh(geometry, material);
          entityGroup.add(skinnedMesh);

          // Add root bone into mesh hierarchy & bind
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
          }, 200);
        }

        onStatus(`已加载实体：${item.name} (${renderedMeshList.length} 个部件，${data.skeletons?.[0]?.joints?.length || 0} 骨骼，${data.animations?.length || 0} 动画)`);
      } else {
        onStatus(`实体 ${item.name} 无可见几何体网格`);
      }
    } catch (e) {
      console.error(e);
      onStatus('实体网格加载异常：' + e.message);
    }
  }

  function captureCurrentEntitySnapshot() {
    try {
      const origSize = new THREE.Vector2();
      renderer.getSize(origSize);
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
    document.querySelectorAll(`.entity-card[data-id="${id}"]`).forEach(card => {
      const img = card.querySelector(`#thumb-img-${id}`);
      const ph = card.querySelector(`#thumb-ph-${id}`);
      if (img && ph) {
        img.src = dataUrl;
        img.style.display = 'block';
        ph.style.display = 'none';
      }
    });
  }

  // Animation render loop hook
  function tick() {
    requestAnimationFrame(tick);
    const delta = animClock.getDelta();

    if (activeMixer && isPlayingAnim) {
      activeMixer.update(delta);
      if (activeSkeleton) {
        activeSkeleton.update();
      }
    }

    if (showSkeleton) {
      updateSkeletonVisualizerPositions();
    }
  }
  tick();

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
    } else if (e.code === 'ArrowDown') {
      e.preventDefault();
      if (currentAnimations.length > 0) {
        const nextIdx = (currentAnimIdx + 1) % currentAnimations.length;
        playAnimationIndex(nextIdx);
      }
    } else if (e.code === 'ArrowUp') {
      e.preventDefault();
      if (currentAnimations.length > 0) {
        const prevIdx = (currentAnimIdx - 1 + currentAnimations.length) % currentAnimations.length;
        playAnimationIndex(prevIdx);
      }
    }
  });

  loadCatalog();
}
