"""Synthetic workload: request/response against the current primary."""

import socket
import threading
import time


class SyntheticWorkload(threading.Thread):
    def __init__(self, endpoint, timeout, emit):
        super().__init__(daemon=True)
        self.endpoint, self.timeout, self.emit = endpoint, timeout, emit
        self.stop_ev = threading.Event()
        self.ok = 0
        self.per_second = {}
        self.t0 = None

    def run(self):
        self.t0 = time.monotonic()
        stalled, f, s = False, None, None
        while not self.stop_ev.is_set():
            try:
                if f is None:
                    s = socket.create_connection(self.endpoint(), timeout=self.timeout)
                    f = s.makefile("rwb")
                f.write(b"tx\n")
                f.flush()
                line = f.readline()
                if not line:
                    raise EOFError("eof")
                self.ok += 1
                sec = int(time.monotonic() - self.t0)
                self.per_second[sec] = self.per_second.get(sec, 0) + 1
                if stalled:
                    stalled = False
                    self.emit("workload_resumed")
            except (OSError, EOFError, LookupError) as e:
                if not stalled:
                    stalled = True
                    self.emit("workload_stalled", reason=_reason(e))
                for x in (f, s):
                    try:
                        x and x.close()
                    except OSError:
                        pass
                f = s = None
                self.stop_ev.wait(0.2)
        for x in (f, s):
            try:
                x and x.close()
            except OSError:
                pass

    def setup(self):
        pass

    def stop(self):
        self.stop_ev.set()
        if self.ident is not None:
            self.join(timeout=5)

    def summary(self):
        return {}


def _reason(e):
    # The distinction matters: 'timeout' is what peers_see_silence predicts
    # for node.off; 'eof'/'reset' would mean the node's kernel answered.
    if isinstance(e, socket.timeout):
        return "timeout"
    if isinstance(e, EOFError):
        return "eof"
    if isinstance(e, ConnectionResetError):
        return "reset"
    if isinstance(e, ConnectionRefusedError):
        return "refused"
    if isinstance(e, LookupError):
        return f"no_target: {e}"
    return f"{type(e).__name__}: {e}"
