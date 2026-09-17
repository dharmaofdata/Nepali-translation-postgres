"""laptop-nsnode primitives against real netns/veth/cgroups."""

import os
import pathlib
import socket
import subprocess
import sys
import unittest

from controller.agenthub import AgentHub
from controller.artifacts import ArtifactServer, load_manifest
from controller.executor import _wait_registered_again
from controller.model import ROOT, load_yaml, resolve_impl
from tests.util import catalog, needs_nsnode

# Peer probe run inside another node's netns: connect, one tx, wait for "go",
# one more tx, report what the connection did.
PEER = r"""
import socket, sys
s = socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=1.5)
f = s.makefile("rwb")
f.write(b"tx\n"); f.flush()
assert f.readline() == b"ok\n"
print("READY", flush=True)
sys.stdin.readline()
f.write(b"tx\n"); f.flush()
try:
    print("eof" if f.readline() == b"" else "data")
except socket.timeout:
    print("timeout")
except ConnectionResetError:
    print("reset")
"""


def tx(f):
    f.write(b"tx\n")
    f.flush()
    return f.readline()


@needs_nsnode
class Nsnode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cat = catalog()
        d = cat.backend("laptop-nsnode")
        cls.hw = load_yaml(ROOT / d["hw_overlay"])
        cls.be = resolve_impl(d["implementation"])(d, cls.hw, lambda *a, **k: None)
        cls.be.cleanup_stale()
        addr, port, aport = cls.be.setup_bench()
        cls.srv = ArtifactServer(addr, aport)
        cls.hub = AgentHub(addr, port, lambda *a, **k: None, 0.2, 1.0)
        cls.be.create_nodes(3, (addr, port))
        cls.nodes = cls.hub.wait_registered(3, 10)
        art = load_manifest("fake-service")
        svc = cat.services["fake"]
        cls.port = svc["port"]
        for n in cls.nodes:
            cls.hub.request(n, "fetch", name=art["name"], file=art["file"],
                            url=f"{cls.srv.url}/{art['path']}", sha256=art["sha256"])
            cls.hub.request(n, "service.deploy", artifact=art["name"],
                            adapter=svc["adapter"], entrypoint=art["entrypoint"],
                            role="replica", port=cls.port, params=svc["params"], peers={})
            cls.hub.request(n, "service.start")

    @classmethod
    def tearDownClass(cls):
        cls.hub.close()
        cls.srv.close()
        cls.be.teardown()

    def addr(self, node):
        return self.hub.snapshot()[node]["addr"]

    def host_peer(self, node):
        s = socket.create_connection((self.addr(node), self.port), timeout=1.5)
        f = s.makefile("rwb")
        self.assertEqual(tx(f), b"ok\n")
        return s, f

    def host_peer_result(self, s, f):
        f.write(b"tx\n")
        f.flush()
        try:
            return "eof" if f.readline() == b"" else "data"
        except socket.timeout:
            return "timeout"
        except ConnectionResetError:
            return "reset"
        finally:
            s.close()

    def node_peer(self, peer, target):
        p = subprocess.Popen(["ip", "netns", "exec", self.be.nodes[peer]["netns"],
                              sys.executable, "-c", PEER, self.addr(target), str(self.port)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "READY")
        return p

    def node_peer_result(self, p):
        out, _ = p.communicate("go\n", timeout=10)
        return out.strip()

    def processes_gone(self, node):
        return self.be.observe(node, "processes_gone")["holds"]

    def test_1_fixed_addresses_same_port(self):
        net = self.hw["network"]
        base = net["subnet"].rsplit(".", 1)[0]
        want = {f"n{i}": f"{base}.{net['node_addr_base'] + i}" for i in (1, 2, 3)}
        got = {n: st["addr"] for n, st in self.hub.snapshot().items()}
        self.assertEqual(got, want)            # address reported by the agent itself
        for n in self.nodes:
            s, _ = self.host_peer(n)           # same service port on every node
            s.close()

    def test_2_node_off_silence_and_processes_gone(self):
        # Peers on the bench host and on another node (n2 -> n1).
        hs, hf = self.host_peer("n1")
        np = self.node_peer("n2", "n1")
        self.assertFalse(self.processes_gone("n1"))
        details = self.be.fault("node.off", "n1")
        self.assertEqual(details["primitives"], ["port.down", "power.cut"])
        self.assertTrue(self.processes_gone("n1"))
        self.assertFalse(self.processes_gone("n2"))
        got = {"host": self.host_peer_result(hs, hf), "node": self.node_peer_result(np)}
        self.assertEqual(got, {"host": "timeout", "node": "timeout"})

    def test_3_control_kill_without_port_down_is_not_silent(self):
        # Proves test_2 is not vacuous for both peer kinds: the same harness
        # sees FIN/RST when the node's kernel is allowed to answer (n3 -> n2).
        hs, hf = self.host_peer("n2")
        np = self.node_peer("n3", "n2")
        self.be.power_cut("n2")
        got = {"host": self.host_peer_result(hs, hf), "node": self.node_peer_result(np)}
        for peer, res in got.items():
            self.assertIn(res, ("eof", "reset"), f"{peer} peer: {got}")

    def test_4_unobservable_semantics_rejected(self):
        with self.assertRaises(LookupError):
            self.be.observe("n3", "hw_timing")


if __name__ == "__main__":
    unittest.main()


@needs_nsnode
class NodeLifecycle(unittest.TestCase):
    """node.off / node.on: what dies with the node and what outlives it."""

    @classmethod
    def setUpClass(cls):
        cat = catalog()
        d = cat.backend("laptop-nsnode")
        hw = load_yaml(ROOT / d["hw_overlay"])
        cls.be = resolve_impl(d["implementation"])(d, hw, lambda *a, **k: None)
        cls.be.cleanup_stale()
        addr, port, aport = cls.be.setup_bench()
        cls.hub = AgentHub(addr, port, lambda *a, **k: None, 0.2, 1.0)
        cls.be.create_nodes(2, (addr, port))
        cls.hub.wait_registered(2, 10)

    @classmethod
    def tearDownClass(cls):
        cls.hub.close()
        cls.be.teardown()

    def ns_ids(self, node):
        """The node's namespace identities, as seen from its agent process."""
        pid = self.be.nodes[node]["proc"].pid
        return {k: os.readlink(f"/proc/{pid}/ns/{k}") for k in ("mnt", "ipc", "net")}

    def node_shm(self, node):
        """The node's own /dev/shm, reachable from the host through its
        mount namespace root."""
        return pathlib.Path(f"/proc/{self.be.nodes[node]['proc'].pid}/root/dev/shm")

    def test_1_private_shm_and_ipc(self):
        n = "n1"
        pid = self.be.nodes[n]["proc"].pid
        mounts = pathlib.Path(f"/proc/{pid}/mounts").read_text()
        self.assertIn("tmpfs /dev/shm tmpfs", mounts)
        for k in ("mnt", "ipc"):
            self.assertNotEqual(self.ns_ids("n1")[k], self.ns_ids("n2")[k])
            self.assertNotEqual(self.ns_ids("n1")[k], os.readlink(f"/proc/self/ns/{k}"))
        # A file in one node's /dev/shm is invisible in the other's and on
        # the host: the tmpfs really is private.
        (self.node_shm("n1") / "only-n1").touch()
        self.assertFalse((self.node_shm("n2") / "only-n1").exists())
        self.assertFalse(pathlib.Path("/dev/shm/only-n1").exists())

    def test_2_node_off_takes_shm_and_ipc_leaves_disk(self):
        n = "n1"
        disk = self.be.nodes[n]["disk"]
        (disk / "marker").write_text("x")
        (self.node_shm(n) / "before-the-crash").touch()
        before = self.ns_ids(n)
        self.be.fault("node.off", n)
        self.assertTrue(self.be.observe(n, "processes_gone")["holds"])
        self.assertTrue(self.be.observe(n, "private_dev_shm")["holds"])
        self.assertTrue(self.be.observe(n, "private_ipc_namespace")["holds"])
        self.assertTrue((disk / "marker").exists(), "node storage must outlive node.off")

        self.be.fault("node.on", n)
        # The boot script execs in place, so the pid is stable, but its
        # namespaces are only correct once the new agent has registered.
        _wait_registered_again(self.hub, n, 15)
        after = self.ns_ids(n)
        self.assertEqual(before["net"], after["net"])        # same network identity
        # Namespace inode numbers are reused once a namespace is freed, so
        # freshness is judged by content, not identity.
        self.assertFalse((self.node_shm(n) / "before-the-crash").exists(),
                         "/dev/shm survived node.off")
        self.assertTrue((disk / "marker").exists())
        self.assertEqual(self.be.nodes[n]["boots"], 2)
