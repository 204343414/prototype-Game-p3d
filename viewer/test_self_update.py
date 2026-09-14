#!/usr/bin/env python3
"""
End-to-end test for POST /api/self_update, exercised through the *actual*
run_viewer.sh wrapper (not by starting viewer/server.py directly), because
the whole point of this feature is "the tunnel process survives a
self-update restart" -- and that guarantee lives in run_viewer.sh's shell
loop, not in server.py itself.

Sets up a real local git repo (with a bare "remote" to pull from), starts
run_viewer.sh (without --tunnel, so there's no real Cloudflare/SSH tunnel
to depend on) against a clone of it, pushes a new commit to the remote,
POSTs /api/self_update, and verifies:

  1. server.py's git pull actually landed the new commit
  2. server.py exits internally with the special SELF_UPDATE_EXIT_CODE (78)
  3. run_viewer.sh's own shell process is NEVER seen exiting in between --
     i.e. its `trap cleanup EXIT` never fires, which is exactly what keeps
     a real tunnel process alive across the restart
  4. a fresh server.py child comes back up on the SAME port afterwards,
     reporting the new commit
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)  # this toolkit's own repo root, whatever it's cloned as
PORT = 8715


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


def pids_listening_on(port):
    found = set()
    try:
        out = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True)
        for pid_str in out.stdout.split():
            found.add(int(pid_str))
        return found
    except FileNotFoundError:
        pass
    try:
        out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True)
        for line in out.stdout.splitlines():
            if re.search(rf"[:.]"+str(port)+r"\b", line.split()[3] if len(line.split()) > 3 else ""):
                for m in re.finditer(r"pid=(\d+)", line):
                    found.add(int(m.group(1)))
    except FileNotFoundError:
        pass
    return found


def main():
    workdir = tempfile.mkdtemp(prefix="self_update_test_")
    bare = os.path.join(workdir, "origin.git")
    clone = os.path.join(workdir, "clone")

    seed = os.path.join(workdir, "seed")
    shutil.copytree(REPO, seed, ignore=shutil.ignore_patterns(".git"))
    run(["git", "init", "-q"], seed)
    run(["git", "config", "user.email", "test@test.com"], seed)
    run(["git", "config", "user.name", "test"], seed)
    run(["git", "add", "-A"], seed)
    run(["git", "commit", "-q", "-m", "seed commit"], seed)
    run(["git", "init", "-q", "--bare", bare], workdir)
    run(["git", "branch", "-M", "main"], seed)
    run(["git", "remote", "add", "origin", bare], seed)
    run(["git", "push", "-q", "origin", "HEAD:main"], seed)
    # Simulate the normal GitHub setup where the remote default is main.
    run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], bare)

    run(["git", "clone", "-q", bare, clone], workdir)
    commit_v1 = run(["git", "rev-parse", "--short", "HEAD"], clone).strip()

    run_viewer = os.path.join(clone, "run_viewer.sh")
    os.chmod(run_viewer, 0o755)

    # NO_TUNNEL isn't even needed since we don't pass --tunnel at all --
    # run_viewer.sh just runs its restart loop around server.py locally.
    wrapper_proc = subprocess.Popen(
        ["bash", run_viewer, str(PORT), clone],
        cwd=clone,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,  # own process group, for clean teardown later
    )
    base = f"http://127.0.0.1:{PORT}"
    try:
        for _ in range(80):
            if wrapper_proc.poll() is not None:
                out = wrapper_proc.stdout.read()
                raise AssertionError(f"run_viewer.sh exited early (code={wrapper_proc.returncode}):\n{out}")
            try:
                status, body = get(base + "/api/health")
                if status == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(0.15)
        else:
            raise AssertionError("server never became healthy")

        data = json.loads(body)
        assert data["git_commit"] == commit_v1, (data, commit_v1)
        pid_v1 = data["pid"]
        wrapper_pid_before = wrapper_proc.pid

        marker_file = os.path.join(seed, "SELF_UPDATE_TEST_MARKER.txt")
        with open(marker_file, "w") as f:
            f.write("hello from v2\n")
        run(["git", "add", "-A"], seed)
        run(["git", "commit", "-q", "-m", "v2: add marker file"], seed)
        run(["git", "push", "-q", "origin", "HEAD:main"], seed)

        status, body = get(base + "/api/self_update", method="POST")
        assert status == 200, (status, body)
        data = json.loads(body)
        print("self_update response:", json.dumps(data, indent=2))
        assert data["pull_ok"] is True, data
        assert data["changed"] is True, data
        assert data["remote_branch"] == "main", data

        # Poll for the new server to come back up, WHILE continuously
        # checking that run_viewer.sh's own process (the thing responsible
        # for a real tunnel's lifetime) never exits in between.
        new_health = None
        for _ in range(150):
            assert wrapper_proc.poll() is None, (
                "run_viewer.sh exited during the self-update restart -- "
                "this is exactly the bug that would kill a real tunnel!"
            )
            try:
                status, body = get(base + "/api/health")
                if status == 200:
                    candidate = json.loads(body)
                    if candidate["pid"] != pid_v1:
                        new_health = candidate
                        break
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(0.1)

        assert new_health is not None, "server never came back up after self_update"
        assert new_health["pid"] != pid_v1, "PID should differ after restart"
        assert new_health["git_commit"] != commit_v1, new_health
        assert wrapper_proc.pid == wrapper_pid_before, "run_viewer.sh PID must be unchanged (same process)"
        assert wrapper_proc.poll() is None, "run_viewer.sh must still be running after the restart"
        assert os.path.isfile(os.path.join(clone, "SELF_UPDATE_TEST_MARKER.txt")), \
            "git pull should have brought in the new file"

        print(f"OK: /api/self_update restarted server.py on same port {PORT} "
              f"WITHOUT run_viewer.sh (the tunnel-owning process) ever exiting "
              f"(wrapper pid {wrapper_pid_before} unchanged, "
              f"server pid {pid_v1} -> {new_health['pid']}, "
              f"commit {commit_v1} -> {new_health['git_commit']})")
    finally:
        # Kill the whole process group (run_viewer.sh + its python3 child +
        # anything else it spawned), then double-check by port as a
        # belt-and-suspenders cleanup.
        try:
            os.killpg(os.getpgid(wrapper_proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        for pid in pids_listening_on(PORT):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        try:
            wrapper_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
