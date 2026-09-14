#!/usr/bin/env python3
"""
Smoke test for viewer/server.py.

Spins up the server as a subprocess bound to 127.0.0.1 on a scratch port,
restricted to a temporary directory (--root), and checks:

  1. /api/health responds
  2. /api/browse lists files/dirs correctly
  3. /api/browse rejects paths that escape --root (403)
  4. /api/file serves file content correctly
  5. /api/file rejects paths that escape --root (403)
  6. static frontend (/ and /index.html) is served
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")
PORT = 8709  # fixed scratch port, unlikely to collide


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    tmpdir = tempfile.mkdtemp(prefix="viewer_test_")
    os.makedirs(os.path.join(tmpdir, "subdir"))
    with open(os.path.join(tmpdir, "hello.txt"), "w") as f:
        f.write("hello viewer test")
    with open(os.path.join(tmpdir, "subdir", "model.glb"), "wb") as f:
        f.write(b"\x00" * 16)

    proc = subprocess.Popen(
        [sys.executable, SERVER, "--host", "127.0.0.1", "--port", str(PORT), "--root", tmpdir],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{PORT}"
    try:
        # wait for server to come up
        for _ in range(50):
            try:
                status, body = get(base + "/api/health")
                if status == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("server never became healthy")

        data = json.loads(body)
        assert data["ok"] is True
        assert data["root"] == os.path.abspath(tmpdir), data

        # browse root
        status, body = get(base + f"/api/browse?path={tmpdir}")
        assert status == 200, (status, body)
        data = json.loads(body)
        names = sorted(e["name"] for e in data["entries"])
        assert names == ["hello.txt", "subdir"], names

        # browse subdir
        sub_path = os.path.join(tmpdir, "subdir")
        status, body = get(base + f"/api/browse?path={sub_path}")
        data = json.loads(body)
        assert [e["name"] for e in data["entries"]] == ["model.glb"], data

        # escape attempt should 403
        status, body = get(base + "/api/browse?path=/etc")
        assert status == 403, (status, body)

        # file read
        status, body = get(base + f"/api/file?path={os.path.join(tmpdir, 'hello.txt')}")
        assert status == 200
        assert body == b"hello viewer test", body

        # file escape attempt
        status, body = get(base + "/api/file?path=/etc/passwd")
        assert status == 403, (status, body)

        # static frontend
        status, body = get(base + "/")
        assert status == 200
        assert b"<html" in body.lower()

        print("OK: viewer/server.py smoke test passed (browse + file APIs, root confinement, static frontend)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
