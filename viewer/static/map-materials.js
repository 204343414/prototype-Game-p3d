import * as THREE from 'three';

export function makeMapTexture(descriptor) {
  const formats = { DXT1: THREE.RGBA_S3TC_DXT1_Format, DXT3: THREE.RGBA_S3TC_DXT3_Format, DXT5: THREE.RGBA_S3TC_DXT5_Format };
  const format = formats[descriptor.format];
  if (!format) throw new Error(`Unsupported map texture format: ${descriptor.format}`);
  const mipmaps = (descriptor.mips || descriptor.mipmaps).map(m => ({ width: m.width, height: m.height,
    data: Uint8Array.from(atob(m.data), c => c.charCodeAt(0)) }));
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

export function mapBucketKey(mesh, cell) {
  const identity = JSON.stringify([cell, mesh.geometry, mesh.group]);
  if (!mesh.texture) return `gray:${identity}`;
  // Keep alpha groups separate for object-level sorting, even with identical texture bytes.
  return mesh.preview_render_mode === 'source_alpha' ? `alpha:${identity}` : `opaque:${mesh.texture}`;
}

export function makeMapMaterial(texture, mode, wireframe) {
  const alpha = Boolean(texture) && mode === 'source_alpha';
  return new THREE.MeshStandardMaterial({
    color: texture ? 0xffffff : 0x9aaab8, map: texture || null,
    roughness: 0.9, metalness: 0, flatShading: true, side: THREE.DoubleSide,
    transparent: alpha, depthWrite: !alpha, wireframe,
  });
}
