import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { getJSON } from './static/map-request.mjs';

// Real HTTP responses exercise retry/abort paths without replacing fetch.
async function serverFor(t, handler) {
  const server = http.createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  return `http://127.0.0.1:${server.address().port}/`;
}
const options = { timeout: 200, delay: 1, retries: 1 };

test('retries transient HTTP failure then returns JSON', async t => {
  let requests = 0;
  const url = await serverFor(t, (req, res) => {
    requests++;
    res.writeHead(requests === 1 ? 502 : 200);
    res.end(requests === 1 ? 'proxy unavailable' : '{"cell":92}');
  });
  assert.deepEqual(await getJSON(url, options), { cell: 92 });
  assert.equal(requests, 2);
});

test('does not retry invalid geometry', async t => {
  let requests = 0;
  const url = await serverFor(t, (req, res) => {
    requests++;
    res.writeHead(422);
    res.end('{"error":"invalid geometry","report":{"errors":["bad index"]}}');
  });
  await assert.rejects(getJSON(url, options), /bad index/);
  assert.equal(requests, 1);
});

test('aborts a stalled response body and retries with fresh signal', async t => {
  let requests = 0;
  const url = await serverFor(t, (req, res) => {
    requests++;
    res.writeHead(200);
    if (requests === 1) { res.write('{'); return; }
    res.end('{"ok":true}');
  });
  assert.deepEqual(await getJSON(url, options), { ok: true });
  assert.equal(requests, 2);
});

test('limits retries when service stays busy', async t => {
  let requests = 0;
  const url = await serverFor(t, (req, res) => {
    requests++;
    res.writeHead(429);
    res.end('{"error":"busy"}');
  });
  await assert.rejects(getJSON(url, options), /busy/);
  assert.equal(requests, 2);
});
