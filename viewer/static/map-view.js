import * as THREE from 'three';
import { createMapNavigation } from './map-camera.js';
import { getJSON } from './map-request.mjs';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const MEMORY_LIMIT = 512 * 1024 * 1024;
const TRIANGLE_LIMIT = 10000000;

function bytes(encoded) {
  return Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
}

function floats(encoded, Type, size) {
  const raw = bytes(encoded);
  if (raw.length % size) throw new Error('几何缓冲区长度不合法');
  const view = new DataView(raw.buffer);
  const values = new Type(raw.length / size);
  for (let i = 0; i < values.length; i++) {
    values[i] = size === 4 ? view.getFloat32(i * size, true) : view.getUint16(i * size, true);
  }
  return values;
}

function dispose(group) {
  group.traverse(mesh => {
    if (mesh.isMesh) {
      mesh.geometry.dispose();
      mesh.material.dispose();
    }
  });
}

export function createMapWorkbench({ scene, camera, controls, renderer }) {
  const byId = id => document.getElementById(id);
  const archive = byId('map-archive');
  const status = byId('map-status');
  const summary = byId('map-summary');
  const list = byId('celllist');
  const layer = new THREE.Group();
  layer.name = 'Manhattan world core';
  layer.visible = false;
  scene.add(layer);
  const loaded = new Map();
  const states = new Map();
  const textures = new Map();
  const s3tc = renderer.extensions.has('WEBGL_compressed_texture_s3tc');
  let entries = [];
  let source = '';
  let busy = false;
  let stop = false;
  let renderRequested = true;
  let userNavigated = false;
  controls.addEventListener('change', () => { renderRequested = true; });
  controls.addEventListener('start', () => { if (layer.visible) userNavigated = true; });
  window.addEventListener('resize', () => { renderRequested = true; });
  const navigation = createMapNavigation({ camera, controls, element: renderer.domElement,
    getBounds: () => new THREE.Box3().setFromObject(layer), getObjects: () => layer.children });
  let geometryBytes = 0;
  let textureBytes = 0;
  let sessionMaterials = null;
  let sessionShared = '';

  function frame() {
    navigation.frame();
    renderRequested = true;
  }

  function totals() {
    const items = [...loaded.values()];
    return {
      triangles: items.reduce((n, v) => n + v.report.accepted_triangle_count, 0),
      texturedGroups: items.reduce((n, v) => n + v.texturedGroups, 0),
      groups: items.reduce((n, v) => n + v.report.accepted_group_count, 0),
    };
  }

  function updateSummary() {
    const total = totals();
    const completed = [...states.values()];
    const empty = completed.filter(v => v.status === 'empty').length;
    const errors = completed.filter(v => v.status === 'error').length;
    const sharedErrors = [...loaded.values()].filter(v => v.materials?.shared_error).length;
    summary.textContent = `${loaded.size} 个区块 · ${total.triangles.toLocaleString()} 三角形 · ${textures.size} 张去重贴图\n已贴图 ${total.texturedGroups} / ${total.groups} 组 · ${((geometryBytes + textureBytes) / 1048576).toFixed(1)} MiB 驻留估算\n左键旋转 · 滚轮指向缩放 · 右键平移 · 双击聚焦 | 仅城市主体；未验证布局 / 未解析贴图为灰色，尚非完整游戏材质${sharedErrors ? `\n${sharedErrors} 个区块的共享资源读取失败，请查看报告` : ''}`;
    byId('map-progress').max = Math.max(entries.length, 1);
    byId('map-progress').value = states.size;
    byId('map-progress-text').textContent = `${states.size}/${entries.length} 已处理 · ${loaded.size} 已加载 · ${empty} 无主体 · ${errors} 失败`;
    byId('map-frame').disabled = !loaded.size;
    byId('map-clear').disabled = busy || !states.size;
    byId('map-all').disabled = busy || !entries.length;
    byId('map-stop').disabled = !busy;
  }

  function clear() {
    for (const { group } of loaded.values()) {
      layer.remove(group);
      dispose(group);
    }
    for (const value of textures.values()) value.texture.dispose();
    renderRequested = true;
    loaded.clear();
    textures.clear();
    states.clear();
    geometryBytes = textureBytes = 0;
    sessionMaterials = null;
    sessionShared = '';
    updateSummary();
    renderList();
  }

  function renderList() {
    list.replaceChildren();
    const search = byId('map-search').value.trim();
    for (const entry of entries.filter(e => String(e.cell).includes(search))) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'cell-item';
      button.setAttribute('aria-pressed', String(loaded.has(entry.cell)));
      button.disabled = busy;
      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = `Cell ${entry.cell}`;
      const info = document.createElement('span');
      info.className = 'info';
      const state = states.get(entry.cell);
      info.textContent = `${(entry.size / 1024).toFixed(1)} KiB · ${state?.label || '按需解析'}`;
      if (state?.error) button.title = state.error;
      button.append(name, info);
      button.onclick = () => loadOne(entry.cell);
      list.appendChild(button);
    }
    if (!list.childElementCount) list.textContent = entries.length ? '没有匹配的 Cell。' : '没有基础 Cell 条目。';
  }

  function setBusy(value) {
    busy = value;
    archive.disabled = value;
    for (const id of ['map-list-load', 'map-append', 'map-materials', 'map-shared']) byId(id).disabled = value;
    renderList();
    updateSummary();
  }

  function checkSource() {
    if (!source || archive.value.trim() !== source) {
      status.textContent = '请先读取当前归档的 Cell 列表。';
      return false;
    }
    return true;
  }

  function materialMode() {
    const requested = byId('map-materials').checked;
    const shared = byId('map-shared').value.trim();
    if (sessionMaterials !== null && (requested !== sessionMaterials || shared !== sessionShared)) clear();
    sessionMaterials = requested;
    sessionShared = shared;
    return requested;
  }

  function makeTexture(descriptor) {
    const mipmaps = descriptor.mips.map(m => ({ width: m.width, height: m.height, data: bytes(m.data) }));
    const format = descriptor.format === 'DXT1' ? THREE.RGBA_S3TC_DXT1_Format : THREE.RGBA_S3TC_DXT5_Format;
    const texture = new THREE.CompressedTexture(mipmaps, mipmaps[0].width, mipmaps[0].height, format);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.flipY = false;
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.magFilter = THREE.LinearFilter;
    const last = mipmaps[mipmaps.length - 1];
    texture.minFilter = last.width === 1 && last.height === 1 ? THREE.LinearMipmapLinearFilter : THREE.LinearFilter;
    texture.needsUpdate = true;
    return texture;
  }

  async function loadCell(cell, materials) {
    if (loaded.has(cell)) return;
    status.textContent = `正在解析 Cell ${cell}…${materials && !s3tc ? '\n此浏览器不支持 S3TC，保留灰模。' : ''}`;
    const data = await getJSON('/api/rcf_cell_preview?' + new URLSearchParams({ path: source, cell, materials: materials && s3tc ? 1 : 0, shared_path: materials && s3tc ? sessionShared : '' }));
    if (data.status === 'empty') {
      states.set(cell, { status: 'empty', label: '无合并城市主体' });
      return;
    }
    const newTextures = (data.textures || []).filter(t => !textures.has(t.key));
    const additionalTextures = newTextures.reduce((n, t) => n + t.bytes, 0);
    // Includes POSITION, computed normal, UV and conservative uint32 merged indices.
    const additionalGeometry = data.report.accepted_vertex_count * 32 + data.report.accepted_index_count * 4;
    if (geometryBytes + textureBytes + additionalTextures + additionalGeometry > MEMORY_LIMIT ||
        totals().triangles + data.report.accepted_triangle_count > TRIANGLE_LIMIT) {
      const error = new Error('已达驻留预算（512 MiB / 1000 万三角形），暂停加载；可卸载后仅选出生点附近区域');
      error.budget = true;
      throw error;
    }
    const group = new THREE.Group();
    const buckets = new Map();
    const pendingTextures = new Map();
    try {
      for (const descriptor of newTextures) {
        pendingTextures.set(descriptor.key, { texture: makeTexture(descriptor), bytes: descriptor.bytes });
      }
      for (const mesh of data.meshes) {
        const positions = floats(mesh.p, Float32Array, 4);
        const indices = floats(mesh.i, Uint16Array, 2);
        const uv = mesh.uv ? floats(mesh.uv, Float32Array, 4) : new Float32Array(mesh.vertices * 2);
        if (positions.length !== mesh.vertices * 3 || indices.length !== mesh.triangles * 3 || uv.length !== mesh.vertices * 2 ||
            !positions.every(Number.isFinite) || !uv.every(Number.isFinite) || !indices.every(index => index < mesh.vertices)) {
          throw new Error('几何响应未通过长度/索引检查');
        }
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
        geometry.setIndex(new THREE.BufferAttribute(indices, 1));
        geometry.computeVertexNormals();
        const key = mesh.texture || null;
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(geometry);
      }
      for (const [key, geometries] of buckets) {
        const texture = textures.get(key)?.texture || pendingTextures.get(key)?.texture;
        const geometry = mergeGeometries(geometries, false);
        if (!geometry) throw new Error('合并几何失败');
        const material = new THREE.MeshStandardMaterial({
          color: texture ? 0xffffff : 0x9aaab8, map: texture || null,
          roughness: 0.9, metalness: 0, flatShading: true, side: THREE.DoubleSide,
          wireframe: byId('map-wireframe').checked,
        });
        group.add(new THREE.Mesh(geometry, material));
        geometry.computeBoundingSphere();
      }
      for (const [key, value] of pendingTextures) textures.set(key, value);
      textureBytes += additionalTextures;
      geometryBytes += additionalGeometry;
      layer.add(group);
      renderRequested = true;
      const texturedGroups = data.meshes.filter(m => m.texture).length;
      loaded.set(cell, { group, report: data.report, materials: data.materials, texturedGroups });
      states.set(cell, { status: 'ready', label: `已加载 · 材质 ${texturedGroups}/${data.meshes.length}` });
    } catch (error) {
      dispose(group);
      for (const value of pendingTextures.values()) value.texture.dispose();
      throw error;
    } finally {
      for (const geometries of buckets.values()) for (const geometry of geometries) geometry.dispose();
    }
  }

  async function loadOne(cell) {
    if (busy || !checkSource()) return;
    const materials = materialMode();
    if (!byId('map-append').checked) clear();
    sessionMaterials = materials;
    sessionShared = byId('map-shared').value.trim();
    setBusy(true);
    try {
      await loadCell(cell, materials);
      frame();
      status.textContent = `Cell ${cell}：${states.get(cell)?.label}\n只绑定已验证的本地 / 共享 color 贴图，灰色部分不代表原游戏外观。`;
    } catch (error) {
      status.textContent = error.message;
      states.set(cell, { status: 'error', label: '加载失败', error: error.message });
    } finally { setBusy(false); }
  }

  byId('map-all').onclick = async () => {
    if (busy || !checkSource()) return;
    const materials = materialMode();
    stop = false;
    userNavigated = false;
    setBusy(true);
    let budget = '';
    try {
      for (const entry of entries) {
        if (stop) break;
        const previous = states.get(entry.cell);
        if (previous && previous.status !== 'error') continue;
        try {
          await loadCell(entry.cell, materials);
        } catch (error) {
          if (error.budget) { budget = error.message; break; }
          states.set(entry.cell, { status: 'error', label: '解析失败', error: error.message });
        }
        updateSummary();
        if (loaded.size === 1 && !userNavigated) frame();
        // Yield between Cells so pause/navigation stay responsive during a city load.
        await new Promise(resolve => setTimeout(resolve, 30));
      }
    } finally {
      setBusy(false);
      if (!userNavigated) frame();
      status.textContent = budget || (stop ? '已暂停；点击加载整城可继续，并重试失败项。' : '本轮整城队列结束；请查看已加载/无主体/失败数量，不等同于完整地图验收。');
      if (materials && !s3tc) status.textContent += '\n浏览器不支持 S3TC，本次仅灰模。';
    }
  };
  byId('map-stop').onclick = () => { stop = true; status.textContent = '将在当前 Cell 完成后暂停。'; };

  byId('map-list-load').onclick = async () => {
    if (busy) return;
    const path = archive.value.trim();
    if (!path) { status.textContent = '请填写服务器上的 cells.rcf 路径。'; return; }
    setBusy(true);
    status.textContent = '正在读取 RCF 元数据，不解压 Cell…';
    try {
      const data = await getJSON('/api/rcf_manifest?' + new URLSearchParams({ path, limit: 0 }));
      const found = [];
      for (const entry of data.entries) {
        const match = entry.name?.match(/^\\art\\locations\\manhattan\\manhattan_Cell_(\d+)\.p3d\.rz$/i);
        if (match && Number(match[1]) <= 259) found.push({ ...entry, cell: Number(match[1]) });
      }
      clear();
      source = path;
      entries = found.sort((a, b) => a.cell - b.cell);
      status.textContent = `${entries.length} 个基础 Cell；可单选或加载整城。未导入 _ft/局部实例。`;
    } catch (error) { status.textContent = `读取失败：${error.message}`; }
    finally { setBusy(false); }
  };
  byId('map-search').oninput = renderList;
  byId('map-clear').onclick = clear;
  byId('map-frame').onclick = frame;
  byId('map-wireframe').onchange = event => {
    renderRequested = true;
    layer.traverse(mesh => { if (mesh.isMesh) mesh.material.wireframe = event.target.checked; });
  };
  byId('map-report').onclick = () => {
    const records = entries.map(entry => ({ cell: entry.cell, ...states.get(entry.cell),
      geometry: loaded.get(entry.cell)?.report, materials: loaded.get(entry.cell)?.materials }));
    const blob = new Blob([JSON.stringify({ source, generated_at: new Date().toISOString(),
      scope: 'world-core only; exact local/shared color references, verified layout; no final shader or VRChat readiness claim',
      s3tc, shared_path: sessionShared, records }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = 'manhattan-load-report.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  updateSummary();
  return {
    needsRender() {
      const result = renderRequested;
      renderRequested = false;
      return result;
    },
    setVisible(visible) {
      if (!visible) stop = true;
      navigation.setEnabled(visible);
      layer.visible = visible;
      renderRequested = true;
      byId('map-toolbar').classList.toggle('hidden', !visible);
      summary.classList.toggle('hidden', !visible);
    },
  };
}
