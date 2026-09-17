"""fake-postgres adapter: runs the artifact script, role is what it was told."""

import socket
import subprocess
import sys
import time

from .base import Service


class FakeService(Service):
    proc = None

    def deploy(self):
        return {"artifact_entry": str(self.bindir)}

    def start(self):
        self.proc = subprocess.Popen([sys.executable, str(self.bindir),
                                      "--port", str(self.port), "--role", self.role])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return {}
            except OSError:
                time.sleep(0.05)
        raise TimeoutError("service did not start listening")

    def status(self):
        if self.proc is None:
            return {"state": "stopped"}
        if self.proc.poll() is not None:
            return {"state": "exited", "code": self.proc.returncode, "role": self.role}
        return {"state": "running", "role": self.role, "port": self.port, "pid": self.proc.pid}
