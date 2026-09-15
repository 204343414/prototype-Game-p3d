// Browser regression using actual Three.js/OrbitControls and trusted CDP input.
// Start viewer on a loopback port, then set VIEWER_URL and CDP_URL to run.
import assert from 'node:assert/strict';
const endpoint = process.env.CDP_URL;
const url = process.env.VIEWER_URL;
if (!endpoint || !url) throw new Error('Set CDP_URL and VIEWER_URL for the loopback test servers');
const tab = await (await fetch(`${endpoint}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' })).json();
const socket = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
let next = 0;
const pending = new Map();
socket.onmessage = ({ data }) => {
  const response = JSON.parse(data);
  const waiter = pending.get(response.id);
  if (!waiter) return;
  pending.delete(response.id);
  clearTimeout(waiter.timer);
  response.error ? waiter.reject(new Error(JSON.stringify(response.error))) : waiter.resolve(response.result);
};
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++next;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 20000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function pointer(type, x, y, button = 'left', buttons = 1) {
  await send('Input.dispatchMouseEvent', { type, x, y, button, buttons, clickCount: type === 'mouseMoved' ? 0 : 1 });
}
try {
  await send('Page.enable');
  await send('Page.bringToFront');
  await new Promise(resolve => setTimeout(resolve, 1500));
  await evaluate(`(async () => {
    const THREE = await import('three');
    const { OrbitControls } = await import('three/addons/controls/OrbitControls.js');
    const { createMapNavigation } = await import('/map-camera.js');
    const host = document.createElement('div');
    host.style.cssText = 'position:fixed;inset:0;z-index:99999;background:#222';
    const canvas = document.createElement('canvas'); canvas.width=800; canvas.height=600;
    canvas.style.cssText='width:800px;height:600px;display:block';host.append(canvas);document.body.append(host);
    const camera = new THREE.PerspectiveCamera(50,800/600,.01,100000);
    camera.position.set(2000,2000,2000);
    const controls = new OrbitControls(camera,canvas); controls.enableDamping=true;
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(4000,100,4000),new THREE.MeshBasicMaterial());
    mesh.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(mesh);
    const navigation = createMapNavigation({ camera, controls, element:canvas, getBounds:()=>box, getObjects:()=>[mesh] });
    navigation.setEnabled(true); navigation.frame();
    window.testCamera = {camera,controls,navigation,mesh};
  })()`);
  if (process.env.CAMERA_BASELINE === '1') await evaluate('testCamera.navigation.setEnabled(false)');
  for (let i = 0; i < 240; i++) {
    await send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: 400, y: 300, deltaX: 0, deltaY: -100 });
  }
  const close = await evaluate(`(() => { const {camera,controls}=testCamera; controls.update(); return {distance:controls.getDistance(),min:controls.minDistance,position:camera.position.toArray(),target:controls.target.toArray(),phi:controls.getPolarAngle()}; })()`);
  assert(close.min >= 1 && close.distance >= close.min * .999, JSON.stringify(close));
  assert(close.position.every(Number.isFinite));
  await pointer('mousePressed', 400, 300);
  await pointer('mouseMoved', 600, 380);
  await pointer('mouseReleased', 600, 380, 'left', 0);
  const rotated = await evaluate(`(() => {const {camera,controls}=testCamera; for(let i=0;i<40;i++)controls.update(); return camera.position.toArray();})()`);
  assert(rotated.some((v, i) => Math.abs(v - close.position[i]) > .01), 'rotation stopped after close zoom');
  const oldTarget = await evaluate('testCamera.controls.target.toArray()');
  await pointer('mousePressed', 400, 300, 'right', 2);
  await pointer('mouseMoved', 550, 330, 'right', 2);
  await pointer('mouseReleased', 550, 330, 'right', 0);
  const panned = await evaluate(`(() => {for(let i=0;i<40;i++)testCamera.controls.update(); return testCamera.controls.target.toArray();})()`);
  assert(panned.some((v, i) => Math.abs(v - oldTarget[i]) > .01), 'panning stopped after close zoom');
  const reset = await evaluate(`(() => {const {controls,navigation}=testCamera; navigation.frame(); return {distance:controls.getDistance(),phi:controls.getPolarAngle()};})()`);
  assert(reset.distance > close.distance * 100);
  await send('Input.dispatchMouseEvent', { type:'mousePressed', x:400, y:300, button:'left', buttons:1, clickCount:2 });
  await send('Input.dispatchMouseEvent', { type:'mouseReleased', x:400, y:300, button:'left', buttons:0, clickCount:2 });
  const focused = await evaluate('testCamera.controls.getDistance()');
  assert(focused >= close.min && focused < reset.distance / 2, 'double-click did not focus picked surface');
  const restored = await evaluate(`(() => {testCamera.navigation.setEnabled(false);return {min:testCamera.controls.minDistance,maxPhi:testCamera.controls.maxPolarAngle,zoom:testCamera.controls.zoomToCursor};})()`);
  assert.equal(restored.min, 0); assert.equal(restored.maxPhi, Math.PI); assert.equal(restored.zoom, false);
  console.log('PASS: 240 wheel zooms, finite bounded camera, left rotation, right pan, overview reset, double-click focus, original controls restored.');
  console.log(JSON.stringify({ closestDistance: close.distance, minimumDistance: close.min, resetDistance: reset.distance }));
} finally {
  socket.close();
  await fetch(`${endpoint}/json/close/${tab.id}`);
}
