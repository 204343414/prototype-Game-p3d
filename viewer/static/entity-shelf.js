import * as THREE from 'three';
import { makeMapTexture, makeMapMaterial } from './map-materials.js';

const CAT_ICONS = {
  powers: '⚡',
  vehicles: '🚗',
  characters: '🧟',
  props: '🏢'
};

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
  let isPlayingAnim = false;
  let animClock = new THREE.Clock();
  let currentAnimTrack = null;
  let animTime = 0;
  let showSkeleton = false;

  async function loadCatalog(artPath) {
    onStatus('正在从 art.rcf 读取四大分类实体清单…');
    try {
      const resp = await fetch(`/api/entities?path=${encodeURIComponent(artPath || '')}`);
      const data = await resp.json();
      if (data.error) {
        onStatus('读取实体错误：' + data.error);
        return;
      }
      entityData = data;
      renderCounts(data.counts);
      renderGrid();
      onStatus(`已索引四大类共 ${data.total_entities} 个实体`);
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

  function renderCard(item, isDrawer = false) {
    const card = document.createElement('div');
    card.className = `entity-card ${currentEntity?.id === item.id ? 'active' : ''}`;
    
    const icon = CAT_ICONS[item.category] || '📦';
    const shapeCount = item.shape_count || item.geometry_count || 1;
    const jointCount = item.total_joints || (item.skeleton_count > 0 ? (item.skeleton_count * 12) : 0);
    const animCount = item.animation_count || 0;

    card.innerHTML = `
      <div class="card-preview-thumb" style="${isDrawer ? 'height:50px;' : ''}">
        <div class="iso-box" style="${isDrawer ? 'width:24px; height:24px;' : ''}"></div>
        <span class="cat-icon" style="${isDrawer ? 'font-size:18px;' : ''}">${icon}</span>
      </div>
      <div class="card-title" title="${item.name}">${item.name}</div>
      <div class="card-meta">
        <span class="card-badge geom" title="网格/形态数量">🧊 ${shapeCount}</span>
        ${jointCount > 0 ? `<span class="card-badge skel" title="骨骼关节数">🦴 ${jointCount}</span>` : ''}
        ${animCount > 0 ? `<span class="card-badge anim" title="动画片段">🎬 ${animCount}</span>` : ''}
      </div>
    `;

    card.onclick = () => selectEntity(item);
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

  function updateMeshInspector(meshes, animations, skeletons) {
    const listEl = document.getElementById('inspector-mesh-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    const jointCount = skeletons?.[0]?.joint_count || 0;

    // Skeleton toggle header row
    const skelRow = document.createElement('div');
    skelRow.className = 'mesh-row';
    skelRow.style.background = '#1e2838';
    skelRow.style.borderColor = '#3b82f6';
    skelRow.innerHTML = `
      <div class="mesh-info">
        <div class="mesh-name" style="color:#7fd4ff; font-weight:600;">🦴 骨骼线条 (${jointCount} 个关节)</div>
      </div>
      <input type="checkbox" class="mesh-toggle" ${showSkeleton ? 'checked' : ''} id="toggle-skeleton-lines" title="开启/关闭骨骼透视线条" />
    `;
    const skelToggle = skelRow.querySelector('#toggle-skeleton-lines');
    if (skelToggle) {
      skelToggle.onchange = (e) => {
        showSkeleton = e.target.checked;
        skeletonGroup.visible = showSkeleton;
      };
    }
    listEl.appendChild(skelRow);

    // Render Sub-meshes
    meshes.forEach((meshObj, idx) => {
      const row = document.createElement('div');
      row.className = 'mesh-row';

      const shortName = meshObj.meshData.geometry_name.split('_').slice(-2).join('_') || meshObj.meshData.geometry_name;
      row.innerHTML = `
        <div class="mesh-info">
          <div class="mesh-name" title="${meshObj.meshData.geometry_name}">${shortName}</div>
          <div class="mesh-counts">${meshObj.meshData.vertex_count}v · ${meshObj.meshData.triangle_count}△</div>
        </div>
        <input type="checkbox" class="mesh-toggle" checked title="显示/隐藏此网格" />
      `;

      const toggle = row.querySelector('.mesh-toggle');
      toggle.onchange = (e) => {
        meshObj.threeMesh.visible = e.target.checked;
      };

      listEl.appendChild(row);
    });

    // Render Animation Section
    currentAnimations = animations || [];
    if (currentAnimations.length > 0) {
      const animHeader = document.createElement('div');
      animHeader.className = 'anim-section-title';
      animHeader.style.display = 'flex';
      animHeader.style.justifyContent = 'space-between';
      animHeader.style.alignItems = 'center';
      animHeader.innerHTML = `
        <span>🎬 引用动画 (${currentAnimations.length} 个)</span>
        <button id="anim-play-btn" class="inspector-btn" style="color:#a78bfa; border-color:#8b5cf6;">▶ 播放</button>
      `;
      listEl.appendChild(animHeader);

      const playBtn = animHeader.querySelector('#anim-play-btn');
      playBtn.onclick = () => {
        isPlayingAnim = !isPlayingAnim;
        playBtn.textContent = isPlayingAnim ? '⏸ 暂停' : '▶ 播放';
        playBtn.style.background = isPlayingAnim ? '#4c1d95' : '#1a2230';
      };

      currentAnimations.forEach((anim, aidx) => {
        const aRow = document.createElement('div');
        aRow.className = 'anim-row';
        aRow.style.cursor = 'pointer';
        aRow.title = '点击选中此动作';
        aRow.innerHTML = `
          <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:150px;">${anim.name}</span>
          <span style="opacity:0.8;">${anim.frames}f · ${anim.duration}s</span>
        `;
        aRow.onclick = () => {
          currentAnimTrack = anim;
          animTime = 0;
          isPlayingAnim = true;
          playBtn.textContent = '⏸ 暂停';
          playBtn.style.background = '#4c1d95';
          document.querySelectorAll('.anim-row').forEach(r => r.style.background = '#151a24');
          aRow.style.background = '#2e1065';
          onStatus(`正在播放动作：${anim.name} (${anim.frames} 帧 @ ${anim.fps} fps)`);
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

        // Position camera at classic 45-degree angled snapshot perspective!
        controls.target.copy(center);
        camera.position.set(center.x + size * 0.9, center.y + size * 0.7, center.z + size * 0.9);
        
        // Gentle, precise zoom speed and strict bounds
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
    const wobble = Math.sin(animTime * 3.0) * 0.02;
    entityGroup.position.y = wobble;
    skeletonGroup.position.y = wobble;
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

  return {
    loadCatalog,
    selectEntity,
    renderGrid,
    updateAnimation,
    setVisible(visible) {
      entityGroup.visible = visible;
      skeletonGroup.visible = visible && showSkeleton;
    }
  };
}
