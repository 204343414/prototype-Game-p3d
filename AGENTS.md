# Prototype P3D — current handoff memory

## Read first
- Read the newest sections of `HANDOFF.md`; format evidence lives in `docs/world-map-foundation.md`, `docs/vertex_format.md`, and `docs/animation_format.md`. `docs/roadmap.md` and older dated milestones are historical.
- User wants three resource categories: characters/items/vehicles with animations, Manhattan map, and audio. Tool-like dark workbench, not a decorative landing page. Reuse existing samples; avoid repeated fine-detail experiments.
- Latest user visual confirmation (2026-09-15): most visible materials/textures are present; roads are still gray/white. NEXT: trace a representative road group's layout, shader/color reference and texture source; verify before extending UV rules. Long-term asset cache is deferred until major map gaps are resolved.

## Verified state and limits
- Full base-Cell API audit and browser queue completed: 260 processed, 143 world-core loaded, 117 no-core (111 placeholders + 6 special Cells), zero API parse failures; 4,675,907 triangles / 7,784,242 vertices.
- `viewer/static/map-view.js` uses `/api/rcf_cell_preview?path=<cells.rcf>&cell=N&materials=1&shared_path=<art.rcf>`. Strict mergedDrawableRoot triangles only, no local props or `_ft` instances. Never reinterpret arbitrary P3D bytes as a heightmap.
- `tools/world/cell_materials.py` allows only the evidenced 68-byte layout. Exact NewShader color references resolve local Texture first, then Manhattan textures.p3d.rz in art.rcf. 9,391/16,898 groups linked, 559 deduplicated compressed textures; remaining 7,125 UV-unverified groups, 381 unresolved/unsupported textures, 1 missing binding. Group coverage is not screen-area coverage.
- Shared texture reuse is process-only, one archive keyed by path/mtime/size. No durable geometry/texture cache. Browser budget 512 MiB / 10M triangles; final estimate 316.1 MiB. Queue supports pause/resume/failed-item retry; requests have bounded timeouts/retries, responses support gzip.
- Alex static/skinning/ROT-only playback is an existing baseline. Full TRAN/root motion/inline channels/all-character coverage remain incomplete; don't restart completed Alex work. Orange skeleton-line changes from earlier work are NOT reliably certified.

## Environments and preservation
- Agent checkout: `/workspace/project/prototype-Game-p3d`, shallow baseline `d35bf87`; archive branch is `openhands/manhattan-workbench-archive`, first checkpoint `4cf4f84`. Select this branch in a new chat; main does not contain these changes.
- Authorized user host: `liang@8700k.top` via cloudflared SSH. Game root `/mnt/hdd/新建文件夹/steamapps/common/Prototype`, containing cells.rcf and art.rcf. These paths are NOT inside the agent container. Inspect existing private-key availability before regenerating; never record secrets here.
- User original checkout `/home/liang/prototype-Game-p3d` / port 8420 remains at `8c3bacc`, with user changes to run_viewer.sh. Preserve it.
- Latest isolated preview: user machine `/tmp/prototype-map-preview.LRlPDWCg`, loopback port 8421, URL `http://127.0.0.1:8421/?v=shared-recovery#map`. Its server.pid and server.log are in that directory; whole-city-audit.json is metadata-only. Temporary files/processes may not survive reboot.
- Agent loopback 12001 was SSH-forwarded to user 8421. The old work-1 sandbox port 12000 is not the user's game server and was stopped. Do not expose an unrestricted filesystem root for testing.
- Save handoff with source before changing sessions. Don't push/deploy/restart main service without authorization. Keep game-derived assets and credentials out of Git. Third-party paid viewer screenshots are reference goals, not our output.

## Checks
- `python3 viewer/test_server.py` — standalone synthetic HTTP smoke test, including root confinement, strict Cell parsing, materials, shared sources and gzip.
- `python3 viewer/test_self_update.py` — isolated temporary Git/server fixture, not the user's service.
- `python3 -m unittest discover -s tools/world -p test_cell_materials.py` — 7 tests, actual synthetic DDS/P3D (no decoder mocks).
- `node --test viewer/test_map_request.mjs` — 4 real HTTP recovery tests, no mocked fetch.
- `node --input-type=module --check < viewer/static/map-view.js`; `git diff --check`.
- Viewer scripts are NOT unittest-discoverable; zero discovered tests is not a passing run. Deploy map-request.mjs with map-view.js and the matching server/material modules.

## Map camera follow-up
- `map-camera.js` provides nonzero zoom limits, bounded polar angles, cursor-directed zoom, double-click surface focus and overview reset; switching away restores previous camera/control settings. This is an orbit inspector, not collision-aware first-person navigation.
- Old default OrbitControls collapsed a city-scale test to distance 0.0156 after 240 wheel steps. Browser regression now stops at 2.8289 and verifies left rotation, right pan, reset and double-click. Test uses real Three.js/OrbitControls and CDP input: `CDP_URL=http://127.0.0.1:<debug-port> VIEWER_URL=http://127.0.0.1:<test-viewer-port>/ node viewer/test_map_camera.mjs`. No dependency install or mock controls.
- Static map redraw is on demand, with controls-change/resize/geometry/wireframe invalidation; user navigation is not overwritten when a city queue completes. Character animation retains its render loop.
- Current user preview URL: `http://127.0.0.1:8421/?v=camera-navigation#map`. The new module must be deployed with index.html/map-view.js. Original 8420 remains untouched.
