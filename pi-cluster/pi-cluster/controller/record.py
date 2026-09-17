"""Append-only event log and experiment record (JSON files)."""

import datetime
import json
import os
import pathlib
import subprocess
import threading
import time

from .model import ROOT


def git_state(root=ROOT):
    def git(*a):
        r = subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    # Untracked files count as dirty: an uncommitted scenario is not reproducible.
    status = git("status", "--porcelain")
    return {"commit": git("rev-parse", "--verify", "-q", "HEAD"),
            "dirty": status is None or bool(status)}


class Recorder:
    def __init__(self, experiments_dir):
        now = datetime.datetime.now(datetime.timezone.utc)
        self.id = now.strftime("exp-%Y%m%dT%H%M%SZ-") + os.urandom(2).hex()
        self.dir = pathlib.Path(experiments_dir) / self.id
        self.dir.mkdir(parents=True)
        self.t0 = time.monotonic()
        self.started = now.isoformat()
        self.events = []
        self._lock = threading.Lock()
        self._log = open(self.dir / "events.jsonl", "a")

    def close(self):
        if not self._log.closed:
            self._log.close()

    def now(self):
        return round(time.monotonic() - self.t0, 4)

    def emit(self, event, **fields):
        e = {"t": self.now(), "event": event, **fields}
        with self._lock:
            self.events.append(e)
            self._log.write(json.dumps(e) + "\n")
            self._log.flush()
        return e

    def write(self, record):
        if not self._log.closed:
            self._log.close()
        full = {"experiment_id": self.id, "started": self.started, **record,
                "events": self.events}
        record.clear()
        record.update(full)
        with open(self.dir / "record.json", "w") as f:
            json.dump(record, f, indent=2)
        return self.dir / "record.json"
