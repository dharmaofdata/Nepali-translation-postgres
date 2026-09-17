#!/usr/bin/env python3
"""Node agent (skeleton).  Runs inside the node, stdlib only.  Talks to the
controller only over the network: register, heartbeat, fetch, service.start."""

import argparse
import hashlib
import importlib
import json
import os
import pathlib
import socket
import sys
import tarfile
import threading
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def hw_profile():
    mem_kb = None
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                mem_kb = int(line.split()[1])
    # Skeleton: /proc only; cgroup limits are not applied/read yet.
    return {"cpu": {"online": os.cpu_count()}, "memory_kb": mem_kb,
            "source": "proc_only"}


class Agent:
    def __init__(self, node_id, controller, state_dir):
        self.node_id = node_id
        self.state_dir = pathlib.Path(state_dir)
        self.cache = pathlib.Path(state_dir) / "artifact-cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        host, port = controller.rsplit(":", 1)
        for _ in range(200):
            try:
                self.sock = socket.create_connection((host, int(port)), timeout=5)
                break
            except OSError:
                time.sleep(0.05)
        else:
            sys.exit(f"{node_id}: controller {controller} unreachable")
        self.sock.settimeout(None)
        self.f = self.sock.makefile("rwb")
        self.lock = threading.Lock()
        self.artifacts = {}
        self.svc = None

    def send(self, msg):
        with self.lock:
            self.f.write((json.dumps(msg) + "\n").encode())
            self.f.flush()

    def service_status(self):
        if self.svc is None:
            return {"state": "absent"}
        try:
            return self.svc.status()
        except Exception as e:                 # never kill the heartbeat
            return {"state": "unknown", "error": f"{type(e).__name__}: {e}"}

    def heartbeat(self, interval):
        while True:
            time.sleep(interval)
            self.send({"type": "hb", "service": self.service_status()})

    def fetch(self, name, file, url, sha256):
        path = self.cache / sha256 / file
        cached = path.exists() and sha256_file(path) == sha256
        if not cached:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=10) as r, open(tmp, "wb") as out:
                out.write(r.read())
            got = sha256_file(tmp)
            if got != sha256:
                tmp.unlink()
                raise ValueError(f"sha256 mismatch: got {got}")
            tmp.rename(path)
        if file.endswith(".tar.gz"):
            tree = path.parent / "tree"
            if not tree.exists():
                tmp = path.parent / "tree.part"
                with tarfile.open(path) as tf:
                    tf.extractall(tmp)
                tmp.rename(tree)
            path = tree
        self.artifacts[name] = path
        return {"cached": cached}

    def service_deploy(self, artifact, adapter, entrypoint, role, port, params, peers):
        mod, _, attr = adapter.partition(":")
        cls = getattr(importlib.import_module(mod), attr)
        path = self.artifacts[artifact]
        entry = path / entrypoint if path.is_dir() else path   # tree vs script
        if not entry.exists():
            raise FileNotFoundError(f"artifact {artifact}: no entrypoint {entrypoint}")
        self.svc = cls(self.node_id, self.state_dir, entry, port, role, params, peers)
        return {"deploy": self.svc.deploy()}

    def service_start(self):
        self.svc.start()
        return {"status": self.svc.status()}

    def service_probe(self):
        return {"probe": self.svc.probe()}

    def serve(self):
        addr = self.sock.getsockname()[0]
        self.send({"type": "register", "node": self.node_id, "addr": addr,
                   "hw": hw_profile()})
        handlers = {"fetch": self.fetch, "service.deploy": self.service_deploy,
                    "service.start": self.service_start, "service.probe": self.service_probe}
        for line in self.f:
            m = json.loads(line)
            cmd = m.pop("cmd")
            if cmd == "configure":
                threading.Thread(target=self.heartbeat, args=(m["heartbeat_interval"],),
                                 daemon=True).start()
                continue
            try:
                out = handlers[cmd](**m)
                self.send({"type": "reply", "cmd": cmd, "ok": True, **out})
            except Exception as e:
                self.send({"type": "reply", "cmd": cmd, "ok": False,
                           "error": f"{type(e).__name__}: {e}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node-id", required=True)
    ap.add_argument("--controller", required=True)
    ap.add_argument("--state-dir", required=True)
    a = ap.parse_args()
    Agent(a.node_id, a.controller, a.state_dir).serve()


if __name__ == "__main__":
    main()
