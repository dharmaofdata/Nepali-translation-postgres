"""Controller side of the agent protocol: JSON lines over TCP.  Inventory
and observed state come only from agent registration and heartbeats."""

import json
import queue
import socket
import threading
import time


class AgentConn:
    def __init__(self, sock):
        self.sock, self.f = sock, sock.makefile("rwb")
        self.replies = queue.Queue()
        self.lock = threading.Lock()

    def send(self, msg):
        with self.lock:
            self.f.write((json.dumps(msg) + "\n").encode())
            self.f.flush()


class AgentHub:
    def __init__(self, addr, port, emit, heartbeat_interval, unavailable_after):
        self.emit = emit
        self.hb_interval, self.unavailable_after = heartbeat_interval, unavailable_after
        self.sock = socket.create_server((addr, port))
        self.conns, self.observed = {}, {}
        self.lock = threading.Lock()
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()
        threading.Thread(target=self._monitor, daemon=True).start()

    def _accept(self):
        while self.running:
            try:
                s, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(s,), daemon=True).start()

    def _serve(self, s):
        c = AgentConn(s)
        try:
            reg = json.loads(c.f.readline())
            if reg.get("type") != "register":
                return
            node = reg["node"]
            with self.lock:
                old = self.conns.get(node)
                self.conns[node] = c
                self.observed[node] = {"available": True, "role": None,
                                       "addr": reg["addr"], "service": None,
                                       "last_hb": time.monotonic()}
            if old is not None:      # a node that rebooted; its old socket is dead
                try:
                    old.sock.close()
                except OSError:
                    pass
            self.emit("node_registered", node=node, addr=reg["addr"], hw=reg.get("hw"))
            c.send({"cmd": "configure", "heartbeat_interval": self.hb_interval})
            for line in c.f:
                msg = json.loads(line)
                if msg.get("type") == "hb":
                    with self.lock:
                        st = self.observed[node]
                        st["last_hb"] = time.monotonic()
                        st["service"] = msg.get("service")
                        st["role"] = (msg.get("service") or {}).get("role")
                elif msg.get("type") == "reply":
                    c.replies.put(msg)
        except (OSError, ValueError):
            pass

    def _monitor(self):
        """Availability is observed from heartbeats in both directions: a node
        whose processes were stopped and started again begins answering on the
        same connection, without registering anew."""
        while self.running:
            now = time.monotonic()
            with self.lock:
                lost, back = [], []
                for n, st in self.observed.items():
                    silent = now - st["last_hb"] > self.unavailable_after
                    if st["available"] and silent:
                        st["available"] = False
                        lost.append(n)
                    elif not st["available"] and not silent:
                        st["available"] = True
                        back.append(n)
            for n in lost:
                self.emit("node_unavailable", node=n, observed_by="heartbeat_timeout")
            for n in back:
                self.emit("node_available", node=n, observed_by="heartbeat")
            time.sleep(0.05)

    def wait_registered(self, count, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.observed) >= count:
                    return sorted(self.observed)
            time.sleep(0.05)
        raise TimeoutError(f"only {sorted(self.observed)} registered, need {count}")

    def snapshot(self):
        with self.lock:
            return {n: dict(st) for n, st in self.observed.items()}

    def request(self, node, cmd, timeout=10.0, **args):
        c = self.conns[node]
        c.send({"cmd": cmd, **args})
        try:
            r = c.replies.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"{node}: no reply to {cmd} in {timeout}s")
        if r.get("cmd") != cmd or not r.get("ok"):
            raise RuntimeError(f"{node}: {cmd} failed: {r}")
        return r

    def close(self):
        self.running = False
        # shutdown() wakes the thread blocked in accept(); close() alone
        # leaves the socket listening until accept returns.
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        for c in self.conns.values():
            try:
                c.sock.close()
            except OSError:
                pass
