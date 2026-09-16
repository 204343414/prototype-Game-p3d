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

export function createEntityShelf({ scene, camera, controls, renderer, onStatus }) {
  let activeCategory = 'powers';
  let entityData = null;
  let currentEntity = null;
  let currentShape = null;
  const entityGroup = new THREE.Group();
  entityGroup.name = 'Entity Viewer Object';
  scene.add(entityGroup);

  let skeletonHelper = null;

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
  }

  function renderGrid() {
    const gridEl = document.getElementById('entity-grid');
    if (!gridEl) return;
    gridEl.innerHTML = '';

    if (!entityData) {
      gridEl.innerHTML = `<div style="grid-column:1/-1; padding:20px; text-align:center; color:#6b7280; font-size:12px;">正在加载实体清单…</div>`;
      return;
    }

    const items = (entityData.categories && entityData.categories[activeCategory]) || [];
    const search = (document.getElementById('entity-search')?.value || '').trim().toLowerCase();
    const filtered = items.filter(item => 
      !search || 
      item.name.toLowerCase().includes(search) || 
      item.id.toLowerCase().includes(search)
    );

    if (!filtered.length) {
      gridEl.innerHTML = `<div style="grid-column:1/-1; padding:20px; text-align:center; color:#6b7280; font-size:12px;">未找到匹配的实体</div>`;
      return;
    }

    for (const item of filtered) {
      const card = document.createElement('div');
      card.className = `entity-card ${currentEntity?.id === item.id ? 'active' : ''}`;
      
      const icon = CAT_ICONS[item.category] || '📦';
      const shapeCount = item.shape_count || item.geometry_count || 1;
      const skelCount = item.skeleton_count || 0;
      const animCount = item.animation_count || 0;

      card.innerHTML = `
        <div class="card-preview-thumb">
          <div class="iso-box"></div>
          <span class="cat-icon">${icon}</span>
        </div>
        <div class="card-title" title="${item.name}">${item.name}</div>
        <div class="card-meta">
          <span class="card-badge geom" title="网格/形态数量">🧊 ${shapeCount}</span>
          ${skelCount > 0 ? `<span class="card-badge skel" title="骨骼数量">🦴 ${skelCount}</span>` : ''}
          ${animCount > 0 ? `<span class="card-badge anim" title="动画片段">🎬 ${animCount}</span>` : ''}
        </div>
      `;

      card.onclick = () => selectEntity(item);
      gridEl.appendChild(card);
    }
  }

  async function selectEntity(item, shapeName = null) {
    currentEntity = item;
    currentShape = shapeName;
    renderGrid();
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
    if (skeletonHelper) {
      scene.remove(skeletonHelper);
      skeletonHelper = null;
    }

    try {
      const artPath = document.getElementById('map-shared')?.value || '';
      const params = new URLSearchParams({
        path: artPath,
        entry: item.entry_path,
        shape: shapeName || ''
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
      }

      if (hasMeshes) {
        const center = bounds.getCenter(new THREE.Vector3());
        const size = bounds.getSize(new THREE.Vector3()).length() || 2.0;

        // Position camera at classic 45-degree angled snapshot perspective!
        controls.target.copy(center);
        camera.position.set(center.x + size * 0.9, center.y + size * 0.7, center.z + size * 0.9);
        camera.near = Math.max(0.01, size / 100);
        camera.far = Math.max(100, size * 100);
        camera.updateProjectionMatrix();
        controls.update();

        const shapesList = item.shapes ? ` · 可切换形态: ${item.shapes.join(', ')}` : '';
        onStatus(`已呈现 3D 实体：${item.name} (${data.meshes.length} 个网格, ${data.textures.length} 张贴图${shapesList})`);
      } else {
        onStatus(`实体 ${item.name} 结构已解析 (无直接网格数据)`);
      }

    } catch (err) {
      onStatus('渲染 3D 实体失败：' + err.message);
    }
  }

  // Setup UI tabs & search listeners
  const tabs = document.querySelectorAll('.cat-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', (e) => {
      const btn = e.target.closest('.cat-tab') || tab;
      tabs.forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      activeCategory = btn.dataset.cat;
      renderGrid();
    });
  });

  const searchInput = document.getElementById('entity-search');
  if (searchInput) {
    searchInput.addEventListener('input', () => renderGrid());
  }

  return {
    loadCatalog,
    selectEntity,
    renderGrid,
    setVisible(visible) {
      entityGroup.visible = visible;
      if (skeletonHelper) skeletonHelper.visible = visible;
    }
  };
}
