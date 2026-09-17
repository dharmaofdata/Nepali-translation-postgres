"""Local artifact repository: a directory, served read-only over HTTP to
agents so that artifact delivery goes through fetch + sha256, not the host
filesystem."""

import functools
import hashlib
import http.server
import json
import pathlib
import threading

from .model import ROOT, ModelError

REPO = ROOT / "artifacts" / "repo"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def load_manifest(name, repo=REPO):
    p = pathlib.Path(repo) / name / "manifest.json"
    if not p.exists():
        raise ModelError(f"artifact {name} not published; run tools/publish_artifacts.py")
    m = json.loads(p.read_text())
    blob = pathlib.Path(repo) / m["path"]
    if sha256_file(blob) != m["sha256"]:
        raise ModelError(f"artifact {name}: repo blob does not match manifest sha256")
    return m


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class ArtifactServer:
    def __init__(self, addr, port, repo=REPO):
        handler = functools.partial(_Quiet, directory=str(repo))
        self.httpd = http.server.ThreadingHTTPServer((addr, port), handler)
        self.url = f"http://{addr}:{port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
