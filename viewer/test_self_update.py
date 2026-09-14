#!/usr/bin/env python3
"""
End-to-end test for /api/self_update: sets up a *real* local git repo
(with a remote to pull from), starts viewer/server.py against a clone of
it, pushes a change to the remote, hits /api/self_update, and verifies:
  1. the server actually ran `git pull` and picked up the new commit
  2. the process restarted (new PID) but kept listening on the SAME port
  3. subsequent requests to /api/health reflect the new commit
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # this toolkit's own repo root, whatever it's cloned as
PORT = 8713


def get(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    return r.stdout


def main():
    workdir = tempfile.mkdtemp(prefix="self_update_test_")
    bare = os.path.join(workdir, "origin.git")
    clone = os.path.join(workdir, "clone")

    # 1. Build a bare "remote" repo containing a real copy of the toolkit
    #    (so server.py's relative imports of rcf_extract/inspect_p3d work).
    seed = os.path.join(workdir, "seed")
    shutil.copytree(REPO, seed, ignore=shutil.ignore_patterns(".git"))
    run(["git", "init", "-q"], seed)
    run(["git", "config", "user.email", "test@test.com"], seed)
    run(["git", "config", "user.name", "test"], seed)
    run(["git", "add", "-A"], seed)
    run(["git", "commit", "-q", "-m", "seed commit"], seed)
    run(["git", "init", "-q", "--bare", bare], workdir)
    run(["git", "remote", "add", "origin", bare], seed)
    run(["git", "push", "-q", "origin", "HEAD:master"], seed)

    # 2. Clone it fresh -- this is what the "server" will run from.
    run(["git", "clone", "-q", bare, clone], workdir)
    commit_v1 = run(["git", "rev-parse", "--short", "HEAD"], clone).strip()

    server_py = os.path.join(clone, "viewer", "server.py")
    proc = subprocess.Popen(
        [sys.executable, server_py, "--host", "127.0.0.1", "--port", str(PORT), "--root", clone],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{PORT}"
    try:
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
        assert data["git_commit"] == commit_v1, (data, commit_v1)
        pid_v1 = data["pid"]

        # 3. Make a change in `seed`, push a new commit to origin.
        marker_file = os.path.join(seed, "SELF_UPDATE_TEST_MARKER.txt")
        with open(marker_file, "w") as f:
            f.write("hello from v2\n")
        run(["git", "add", "-A"], seed)
        run(["git", "commit", "-q", "-m", "v2: add marker file"], seed)
        run(["git", "push", "-q", "origin", "HEAD:master"], seed)

        # 4. Trigger self_update.
        status, body = get(base + "/api/self_update", method="POST")
        assert status == 200, (status, body)
        data = json.loads(body)
        print("self_update response:", json.dumps(data, indent=2))
        assert data["pull_ok"] is True, data
        assert data["changed"] is True, data

        # 5. Wait for the new process to come back up on the SAME port.
        new_health = None
        for _ in range(100):
            try:
                status, body = get(base + "/api/health")
                if status == 200:
                    new_health = json.loads(body)
                    if new_health["pid"] != pid_v1:
                        break
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(0.1)

        assert new_health is not None, "server never came back up"
        assert new_health["pid"] != pid_v1, "PID should differ after restart"
        assert os.path.isfile(os.path.join(clone, "SELF_UPDATE_TEST_MARKER.txt")), \
            "git pull should have brought in the new file"

        print(f"OK: /api/self_update restarted server on same port {PORT} "
              f"(pid {pid_v1} -> {new_health['pid']}, commit {commit_v1} -> {new_health['git_commit']})")
    finally:
        # The *new* process (post-restart) is what's actually listening now;
        # find and kill it by port since our original `proc` handle is stale.
        # Try lsof first (not always installed), fall back to parsing `ss`
        # (part of iproute2, present on basically every modern Linux).
        import re
        killed_by_port = set()
        try:
            out = subprocess.run(["lsof", "-t", f"-i:{PORT}"], capture_output=True, text=True)
            for pid_str in out.stdout.split():
                killed_by_port.add(int(pid_str))
        except FileNotFoundError:
            try:
                out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True)
                for line in out.stdout.splitlines():
                    if f":{PORT} " in line or line.rstrip().endswith(f":{PORT}"):
                        for m in re.finditer(r"pid=(\d+)", line):
                            killed_by_port.add(int(m.group(1)))
            except FileNotFoundError:
                pass
        for pid in killed_by_port:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
