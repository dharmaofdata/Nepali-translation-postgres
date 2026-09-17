"""laptop-nsnode: one netns, one cgroup v2 and one mount+ipc namespace per node.

Bench primitives:  port.down = host-side veth down;  power.cut = cgroup.kill;
power.on = recreate the namespaces and start the agent again;  freeze/thaw =
cgroup.freeze, which stops the node's processes while its kernel keeps
answering - the node is neither dead nor making progress.

The mount and ipc namespaces exist so that death and restart mean the same
thing here as on hardware: the node's /dev/shm is a private tmpfs that dies
with it, and its SysV segments die with its ipc namespace.  Three postmasters
survive a shared ipc namespace today only because the SysV key is derived from
the PGDATA inode (sysv_shmem.c) - that is accidental coexistence, not a model
of a reboot.

Node storage lives on the host under <node_root>/<node>/disk and outlives
node.off, so node.on returns to the same PGDATA.  It is visible in the node's
mount namespace because that namespace starts as a copy of the host's; no
bind mount is involved.

Still not isolated (not in `provides`): pid and uts namespaces, the root
filesystem, resource limits, clocks.
"""

import ipaddress
import os
import pathlib
import shutil
import subprocess
import sys
import time

from ..model import ROOT, ModelError, duration
from .base import Backend

PREFIX = "pic-"


def sh(*args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(args)}: {r.stderr.strip()}")
    return r


def cgroup2_mount():
    with open("/proc/mounts") as f:
        for line in f:
            _, mnt, typ, *_ = line.split()
            if typ == "cgroup2":
                return pathlib.Path(mnt)
    raise ModelError("laptop-nsnode: cgroup2 is not mounted")


def probe():
    """None if netns, veth and a killable cgroup can be created here, else a
    reason.  Root alone is not enough: containers/CI often lack CAP_NET_ADMIN."""
    if os.geteuid() != 0:
        return "must run as root"
    for tool, pkg in (("ip", "iproute2"), ("ping", "iputils"), ("unshare", "util-linux")):
        if not shutil.which(tool):
            return f"{tool} not found ({pkg})"
    try:
        cg = cgroup2_mount() / "picluster" / "probe"
    except ModelError as e:
        return str(e)
    ns, a, b = f"{PREFIX}probe", f"{PREFIX}probe-a", f"{PREFIX}probe-b"
    try:
        sh("ip", "netns", "add", ns)
        sh("ip", "link", "add", a, "type", "veth", "peer", "name", b)
        sh("ip", "link", "set", b, "netns", ns)
        cg.mkdir(parents=True, exist_ok=True)
        if not (cg / "cgroup.kill").exists():
            return "cgroup.kill missing (kernel < 5.14)"
        return None
    except (RuntimeError, OSError) as e:
        return f"cannot create netns/veth/cgroup: {e}"
    finally:
        sh("ip", "link", "del", a, check=False)
        sh("ip", "netns", "del", ns, check=False)
        if cg.is_dir():
            cg.rmdir()


class LaptopNsnode(Backend):
    @staticmethod
    def preflight(hw):
        return probe()

    def __init__(self, descriptor, hw, emit):
        super().__init__(descriptor, hw, emit)
        net = hw["network"]
        self.net = ipaddress.ip_network(net["subnet"])
        self.bench_addr = net["bench_addr"]
        self.addr_base = int(net["node_addr_base"])
        self.bridge = net["bridge"]
        if not self.bridge.startswith(PREFIX):
            raise ModelError(f"bridge name must start with {PREFIX}")
        self.ctl_port = int(hw["controller"]["port"])
        self.art_port = int(hw["controller"]["artifact_port"])
        self.kill_settle = duration(hw["timing"]["kill_settle_timeout"], "kill_settle_timeout")
        self.node_root = pathlib.Path(hw["storage"]["node_root"])
        self.cg_root = cgroup2_mount() / "picluster"
        self.shm_size = hw["storage"].get("dev_shm_size", "64m")
        self.tmp_size = hw["storage"].get("tmp_size", "256m")
        self.nodes = {}
        self.controller = None
        self.host_baseline = None
        self.actions = {"node.off": self.node_off, "node.on": self.node_on,
                        "process.pause": self.process_pause,
                        "process.resume": self.process_resume}
        self.observers = {"processes_gone": self._observe_processes_gone,
                          "peers_see_no_progress": self._observe_paused,
                          "private_ipc_namespace": self._observe_ipc,
                          "private_dev_shm": self._observe_shm}

    # --- bench setup -------------------------------------------------------

    def cleanup_stale(self):
        removed = []
        if self.cg_root.is_dir():
            for cg in sorted(p for p in self.cg_root.iterdir() if p.is_dir()):
                self._kill_cgroup(cg)
                cg.rmdir()
                removed.append(f"cgroup:{cg.name}")
        # Delete host-side veths explicitly: a netns with orphaned sockets
        # (e.g. FIN_WAIT after node.off) outlives `ip netns del`, and so
        # would its veth pair.
        for line in sh("ip", "-o", "link", "show").stdout.splitlines():
            ifname = line.split(":")[1].strip().split("@")[0]
            if ifname.startswith(PREFIX) and ifname != self.bridge:
                sh("ip", "link", "del", ifname, check=False)
                removed.append(f"link:{ifname}")
        for line in sh("ip", "netns", "list").stdout.splitlines():
            name = line.split()[0] if line.strip() else ""
            if name.startswith(PREFIX):
                sh("ip", "netns", "del", name)
                removed.append(f"netns:{name}")
        if sh("ip", "link", "show", self.bridge, check=False).returncode == 0:
            sh("ip", "link", "del", self.bridge)
            removed.append(f"bridge:{self.bridge}")
        if self.node_root.exists():
            shutil.rmtree(self.node_root)
            removed.append(f"dir:{self.node_root}")
        return removed

    @staticmethod
    def _host_sysv_segments():
        """key:shmid of every SysV shared memory segment visible on the host.
        The key identifies the segment (PostgreSQL derives it from the PGDATA
        inode); the shmid alone is a counter the kernel reuses."""
        out = sh("ipcs", "-m").stdout.splitlines()
        return sorted(f"{f[0]}:{f[1]}" for f in (line.split() for line in out)
                      if len(f) > 1 and f[0].startswith("0x"))

    @staticmethod
    def _host_shm_entries():
        return sorted(p.name for p in pathlib.Path("/dev/shm").iterdir())

    def setup_bench(self):
        plen = self.net.prefixlen
        sh("ip", "link", "add", self.bridge, "type", "bridge")
        sh("ip", "addr", "add", f"{self.bench_addr}/{plen}", "dev", self.bridge)
        sh("ip", "link", "set", self.bridge, "up")
        self.cg_root.mkdir(exist_ok=True)
        self.node_root.mkdir(parents=True, exist_ok=True)
        # Leak detection is a difference against the host state at bench
        # setup: anything of ours that outlives a node shows up here.
        self.host_baseline = {"ipc": self._host_sysv_segments(),
                              "shm": self._host_shm_entries()}
        self.emit("bench_ready", bridge=self.bridge, bench_addr=self.bench_addr)
        return self.bench_addr, self.ctl_port, self.art_port

    def create_nodes(self, count, controller_endpoint):
        self.controller = controller_endpoint
        plen = self.net.prefixlen
        for i in range(1, count + 1):
            nid = f"n{i}"
            ns, vh, vn = f"{PREFIX}{nid}", f"{PREFIX}{nid}-h", f"{PREFIX}{nid}-n"
            addr = str(self.net.network_address + self.addr_base + i)
            sh("ip", "netns", "add", ns)
            sh("ip", "link", "add", vh, "type", "veth", "peer", "name", vn)
            sh("ip", "link", "set", vn, "netns", ns)
            sh("ip", "-n", ns, "link", "set", vn, "name", "eth0")
            sh("ip", "-n", ns, "addr", "add", f"{addr}/{plen}", "dev", "eth0")
            sh("ip", "-n", ns, "link", "set", "lo", "up")
            sh("ip", "-n", ns, "link", "set", "eth0", "up")
            sh("ip", "link", "set", vh, "master", self.bridge)
            sh("ip", "link", "set", vh, "up")
            cg = self.cg_root / nid
            cg.mkdir()
            if not (cg / "cgroup.kill").exists():
                raise ModelError("laptop-nsnode: cgroup.kill missing (kernel < 5.14)")
            ndir = self.node_root / nid
            disk = ndir / "disk"                     # outlives node.off
            disk.mkdir(parents=True)
            self.nodes[nid] = {"netns": ns, "veth_host": vh, "addr": addr,
                               "cgroup": cg, "dir": ndir, "disk": disk,
                               "proc": None, "console": None, "boots": 0}
            self.power_on(nid)

    def power_on(self, node):
        """Start the node's agent in a fresh mount+ipc namespace."""
        n = self.nodes[node]
        if n["console"] is not None:
            n["console"].close()
        n["boots"] += 1
        n["console"] = open(n["dir"] / "console.log", "ab")
        caddr, cport = self.controller
        # Two scripts instead of nested shell quoting: the outer one joins the
        # cgroup (so every descendant inherits it) and enters the namespaces,
        # the inner one mounts the node's own /dev/shm and becomes the agent.
        # The agent is installed on the node rather than executed from the
        # host tree: the node has its own /tmp and its own mount namespace,
        # so a host path is not something it can be relied on to see.
        agent = n["dir"] / "agent"
        shutil.rmtree(agent, ignore_errors=True)
        shutil.copytree(ROOT / "agent", agent)
        boot = n["dir"] / "boot.sh"
        inner = n["dir"] / "boot-inner.sh"
        inner.write_text(
            "#!/bin/sh\n"
            f"mount -t tmpfs -o size={self.shm_size},nosuid,nodev tmpfs /dev/shm || exit 1\n"
            # A machine has its own socket directory.  Without this every node
            # would share the host's /run/postgresql while all of them listen
            # on the same port, and the second one to start would collide.
            "mkdir -p /run/postgresql || exit 1\n"
            "mount -t tmpfs -o size=1m,nosuid,nodev,mode=1777 tmpfs /run/postgresql "
            "|| exit 1\n"
            # And its own /tmp.  Sharing the host's means sharing whatever
            # runtime state services keep there, across nodes and across
            # runs: pg_autoctl found a pid file of a node from an earlier run
            # and refused to start.
            f"mount -t tmpfs -o size={self.tmp_size},nosuid,nodev,mode=1777 tmpfs /tmp "
            "|| exit 1\n"
            f'exec "{sys.executable}" "{agent}/agent.py" --node-id {node} '
            f'--controller {caddr}:{cport} --state-dir "{n["disk"]}"\n')
        boot.write_text(
            "#!/bin/sh\n"
            f'echo $$ > "{n["cgroup"]}/cgroup.procs" || exit 1\n'
            f'exec ip netns exec {n["netns"]} '
            f'unshare --mount --ipc --propagation private "{inner}"\n')
        for f in (boot, inner):
            f.chmod(0o755)
        n["proc"] = subprocess.Popen([str(boot)], stdout=n["console"],
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     start_new_session=True, cwd=n["disk"])
        self.emit("bench_node_powered_on", node=node, addr=n["addr"],
                  boot=n["boots"])

    # --- primitives ----------------------------------------------------------

    def _procs(self, cg):
        try:
            return [int(x) for x in (cg / "cgroup.procs").read_text().split()]
        except FileNotFoundError:
            return []

    def _kill_cgroup(self, cg):
        # cgroup.kill does reach frozen tasks, but thawing first keeps
        # teardown independent of that behaviour.
        freeze = cg / "cgroup.freeze"
        if freeze.exists():
            freeze.write_text("0")
        (cg / "cgroup.kill").write_text("1")
        deadline = time.monotonic() + self.kill_settle
        while self._procs(cg) and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self._procs(cg)

    def port_down(self, node):
        sh("ip", "link", "set", self.nodes[node]["veth_host"], "down")
        self.emit("bench_primitive", prim="port.down", node=node)

    def power_cut(self, node):
        n = self.nodes[node]
        self.emit("bench_primitive", prim="power.cut", node=node)
        settled = self._kill_cgroup(n["cgroup"])
        try:
            n["proc"].wait(timeout=self.kill_settle)   # reap our direct child
        except subprocess.TimeoutExpired:
            settled = False
        self.emit("bench_power_cut_settled", node=node, settled=settled)
        return settled

    def port_up(self, node):
        sh("ip", "link", "set", self.nodes[node]["veth_host"], "up")
        self.emit("bench_primitive", prim="port.up", node=node)

    def node_off(self, node):
        # Order matters: with the link still up, killing the processes lets
        # the node's kernel send FIN/RST and peers would learn of the failure
        # immediately (breaks peers_see_silence).
        self.port_down(node)
        settled = self.power_cut(node)
        return {"primitives": ["port.down", "power.cut"], "settled": settled,
                "expected_up": False}

    def freeze(self, node, on):
        cg = self.nodes[node]["cgroup"]
        (cg / "cgroup.freeze").write_text("1" if on else "0")
        deadline = time.monotonic() + self.kill_settle
        while self._frozen(cg) != on and time.monotonic() < deadline:
            time.sleep(0.01)
        self.emit("bench_primitive", prim="freeze" if on else "thaw", node=node)
        return self._frozen(cg) == on

    @staticmethod
    def _frozen(cg):
        for line in (cg / "cgroup.events").read_text().splitlines():
            if line.startswith("frozen "):
                return line.split()[1] == "1"
        return False

    def process_pause(self, node):
        """The node stops making progress but stays on the network: its
        kernel still answers, so peers see liveness without service."""
        settled = self.freeze(node, True)
        return {"primitives": ["freeze"], "settled": settled, "expected_up": False}

    def process_resume(self, node):
        settled = self.freeze(node, False)
        # The agent and the service were never gone, only stopped: nothing to
        # deploy, the node simply starts answering again.
        return {"primitives": ["thaw"], "settled": settled, "expected_up": True,
                "service_lost": False}

    def node_on(self, node):
        """Power the node back on: same netns, same cgroup, same disk, new
        mount and ipc namespaces - as after a reboot."""
        self.power_on(node)
        self.port_up(node)
        return {"primitives": ["power.on", "port.up"], "expected_up": True,
                "service_lost": True,
                "boot": self.nodes[node]["boots"], "disk": str(self.nodes[node]["disk"])}

    def fault(self, action, node):
        return self.actions[action](node)

    def observe(self, node, semantic):
        fn = self.observers.get(semantic)
        if fn is None:
            raise LookupError(f"{self.descriptor['name']} cannot observe {semantic}")
        return fn(node)

    def _observe_ipc(self, node):
        # Host-wide, not attributed to a node: anything created on this host
        # since bench setup counts, including another PostgreSQL of the
        # developer's own.  See README.
        new = sorted(set(self._host_sysv_segments()) - set(self.host_baseline["ipc"]))
        return {"holds": not new, "evidence": {"scope": "host",
                                               "new_host_sysv_segments": new}}

    def _observe_shm(self, node):
        new = sorted(set(self._host_shm_entries()) - set(self.host_baseline["shm"]))
        return {"holds": not new, "evidence": {"scope": "host",
                                               "new_host_dev_shm_entries": new[:20],
                                               "count": len(new)}}

    def _observe_paused(self, node):
        """Frozen processes, a kernel that still answers: what a pause looks
        like from outside.  The ping goes over the bench bridge, which is the
        same path a peer would use."""
        n = self.nodes[node]
        pids = self._procs(n["cgroup"])
        frozen = self._frozen(n["cgroup"])
        pings = sh("ping", "-c", "1", "-W", "1", n["addr"], check=False).returncode == 0
        return {"holds": bool(pids) and frozen and pings,
                "evidence": {"pids": len(pids), "frozen": frozen,
                             "answers_ping": pings}}

    def _observe_processes_gone(self, node):
        cg = self.nodes[node]["cgroup"]
        pids = self._procs(cg)
        return {"holds": not pids, "evidence": {"cgroup": str(cg), "pids": pids}}

    # --- teardown ------------------------------------------------------------

    def teardown(self, console_dest=None):
        for nid, n in self.nodes.items():
            self._kill_cgroup(n["cgroup"])
            try:
                n["proc"].wait(timeout=self.kill_settle)
            except subprocess.TimeoutExpired:
                pass
            n["console"].close()
            if console_dest:
                pathlib.Path(console_dest).mkdir(parents=True, exist_ok=True)
                shutil.copy(n["dir"] / "console.log",
                            pathlib.Path(console_dest) / f"{nid}.log")
        self.cleanup_stale()
        self.nodes.clear()
