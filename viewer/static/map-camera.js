import * as THREE from 'three';

const CONTROL_KEYS = ['minDistance', 'maxDistance', 'minPolarAngle', 'maxPolarAngle', 'zoomSpeed', 'zoomToCursor'];

export function createMapNavigation({ camera, controls, element, getBounds, getObjects }) {
  let enabled = false;
  let saved = null;
  const raycaster = new THREE.Raycaster();

  function flushDamping() {
    // Clear pending orbit/pan deltas before installing a new camera/target pair.
    const damping = controls.enableDamping;
    controls.enableDamping = false;
    controls.update();
    controls.enableDamping = damping;
  }

  function dimensions() {
    const box = getBounds();
    if (!box || box.isEmpty()) return null;
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1);
    if (!Number.isFinite(radius)) return null;
    const halfAngle = Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * Math.min(1, camera.aspect));
    return { center: box.getCenter(new THREE.Vector3()), radius, distance: radius / Math.sin(halfAngle) * 1.1 };
  }

  function limits(size) {
    controls.minDistance = Math.max(2, size.radius * 0.001);
    controls.maxDistance = Math.max(controls.minDistance * 10, size.distance * 4);
    controls.minPolarAngle = 0.04;
    controls.maxPolarAngle = Math.PI / 2 - 0.02;
    controls.zoomSpeed = 0.65;
    controls.zoomToCursor = true;
    camera.near = controls.minDistance / 20;
    camera.far = Math.max(100, controls.maxDistance + size.radius * 4);
    camera.updateProjectionMatrix();
  }

  function frame() {
    if (!enabled) return;
    const size = dimensions();
    if (!size) return;
    flushDamping();
    limits(size);
    controls.target.copy(size.center);
    camera.position.copy(size.center).add(new THREE.Vector3(1, 0.85, 1).normalize().multiplyScalar(Math.max(size.distance, controls.minDistance * 2)));
    controls.update();
  }

  function focus(event) {
    if (!enabled) return;
    const size = dimensions();
    const rect = element.getBoundingClientRect();
    if (!size || !rect.width || !rect.height) return;
    camera.updateMatrixWorld();
    const objects = getObjects();
    for (const object of objects) object.updateWorldMatrix(true, true);
    raycaster.setFromCamera(new THREE.Vector2(
      (event.clientX - rect.left) / rect.width * 2 - 1,
      -(event.clientY - rect.top) / rect.height * 2 + 1,
    ), camera);
    const hit = raycaster.intersectObjects(objects, true)[0];
    if (!hit) return;
    flushDamping();
    limits(size);
    const direction = camera.position.clone().sub(controls.target).normalize();
    const distance = Math.max(controls.minDistance * 8, Math.min(controls.getDistance(), size.radius * 0.03));
    controls.target.copy(hit.point);
    camera.position.copy(hit.point).addScaledVector(direction, distance);
    controls.update();
  }

  element.addEventListener('dblclick', focus);
  return {
    frame,
    setEnabled(value) {
      if (value === enabled) return;
      if (value) {
        saved = {
          settings: Object.fromEntries(CONTROL_KEYS.map(key => [key, controls[key]])),
          position: camera.position.clone(), target: controls.target.clone(), near: camera.near, far: camera.far,
        };
        enabled = true;
        frame();
      } else {
        flushDamping();
        Object.assign(controls, saved.settings);
        camera.position.copy(saved.position);
        controls.target.copy(saved.target);
        camera.near = saved.near;
        camera.far = saved.far;
        camera.updateProjectionMatrix();
        controls.update();
        enabled = false;
      }
    },
    dispose() {
      if (enabled) this.setEnabled(false);
      element.removeEventListener('dblclick', focus);
    },
  };
}
