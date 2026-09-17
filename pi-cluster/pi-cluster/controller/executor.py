"""Deterministic executor.  Knows interfaces and capabilities only; never
branches on backend, service or check names."""

import fcntl
import hashlib
import pathlib
import time

from . import fidelity
from .agenthub import AgentHub
from .artifacts import ArtifactServer, load_manifest, sha256_file
from .model import (ROOT, ModelError, allowed_relaxations, duration, load_scenario,
                    load_yaml, resolve_impl, scenario_actions, service_order)
from .record import Recorder, git_state
from .selector import resolve

LOCK = "/run/picluster.lock"


class CheckContext:
    """What a check may see.

    `faulted` is the list of executed faults; each entry carries the semantic
    observations of every node taken immediately before and immediately after
    that fault.  A check must use those, not `observe`, for anything about the
    moment of the fault: by evaluation time a node that was restarted is alive
    again.  `observe` is the state now, at the end of the run.  `probes` holds
    service probes before the first fault and at the end; their contents are
    opaque to the core."""

    def __init__(self, observe, inventory, faulted, probes, workload, services):
        self.observe, self.inventory = observe, inventory
        self.faulted, self.probes, self.workload = faulted, probes, workload
        self.services = services       # name -> {members, role_source, initial}

    def members(self, role_source=None):
        """Nodes hosting a service, optionally only those whose roles come
        from `role_source`.  A check must scope itself to the service it is
        about: the inventory also holds nodes running something else."""
        return [m for svc in self.services.values() for m in svc["members"]
                if role_source in (None, svc["role_source"])]

    def for_check(self, check):
        """A view holding only the semantics this check declared in
        `observes`.  An undeclared read then hits a missing key instead of
        silently skipping the guard that depends on it."""
        want = set(check["observes"])

        def keep(snap):
            return {n: {s: o for s, o in obs.items() if s in want}
                    for n, obs in snap.items()}

        faulted = [{**f, "before": keep(f["before"]), "after": keep(f["after"])}
                   for f in self.faulted]
        return CheckContext(self.observe, self.inventory, faulted, self.probes,
                            self.workload, self.services)


def prepare(scenario_path, backend_name, catalog):
    """Everything that must hold before any side effect."""
    sc = load_scenario(scenario_path, catalog)
    b = catalog.require_implemented(backend_name)   # stub -> StubBackend, no fallback
    unsupported = scenario_actions(sc) - set(b["actions"])
    if unsupported:
        raise ModelError(f"backend {backend_name} does not implement {sorted(unsupported)}")
    art = {}
    for name, svc in sc["services"].items():
        m = load_manifest(svc["artifact"])
        if svc["type"] not in m["service_types"]:
            raise ModelError(f"artifact {m['name']} serves {m['service_types']}, "
                             f"not {svc['type']}")
        art[svc["artifact"]] = m
    return sc, b, art


class Deployment:
    """Brings services up and keeps them there.

    Two reconcile rules, chosen by the service's role_source:

      declared  the scenario owns the roles, so a node that comes back gets
                its role back and a mismatch fails the run;
      observed  something outside the executor decides roles, so the
                executor restores membership only and records whatever role
                the service reports.
    """

    def __init__(self, sc, catalog, art, url, hub, rec, t, record):
        self.sc, self.catalog, self.art, self.url = sc, catalog, art, url
        self.hub, self.rec, self.t, self.record = hub, rec, t, record
        self.services = sc["services"]
        self.order = service_order(self.services)
        self.owner = {m: name for name, svc in self.services.items()
                      for m in svc["members"]}
        self.roles = {}          # bootstrap intent, not steady state
        for name, svc in self.services.items():
            # The first role the service declares is what the initial primary
            # gets; the rest get the second, if the service has one.  A
            # single-role service (a monitor) gives every member that role.
            order = catalog.services[svc["type"]]["role_order"]
            first, rest = order[0], order[1] if len(order) > 1 else order[0]
            for m in svc["members"]:
                self.roles[m] = first if m == svc["initial"]["primary"] else rest
        record["services"] = {
            name: {"type": svc["type"], "members": svc["members"],
                   "role_source": svc["role_source"], "initial": svc["initial"],
                   "params": catalog.services[svc["type"]]["params"],
                   "effective": {}}
            for name, svc in self.services.items()}

    def service_of(self, node):
        return self.services[self.owner[node]]

    def spec(self, node):
        return self.catalog.services[self.service_of(node)["type"]]

    def peers(self, node):
        """What the adapter needs to know about the other services: their
        member addresses and, where roles are declared, the primary."""
        out = {}
        snap = self.hub.snapshot()
        for name, svc in self.services.items():
            addrs = {m: snap[m]["addr"] for m in svc["members"] if m in snap}
            out[name] = {"nodes": addrs}
            if svc["role_source"] == "declared":
                p = svc["initial"]["primary"]
                out[name]["primary"] = addrs.get(p)
            else:
                observed = [m for m in svc["members"]
                            if snap.get(m, {}).get("role") == "primary"]
                out[name]["primary"] = addrs.get(observed[0]) if observed else None
        # The service's own peers stay reachable under their own name, and
        # a single-service scenario keeps the flat {"primary": addr} shape.
        own = out[self.owner[node]]
        return {**out, "primary": own.get("primary"), "self": snap[node]["addr"]}

    def bring_up(self, node, want):
        name, svc, spec = self.owner[node], self.service_of(node), self.spec(node)
        # Fetch every time: a node that rebooted has an artifact cache on
        # disk but no memory of it, and the run must not depend on whether
        # the artifact was already cached.
        art = self.art[svc["artifact"]]
        f = self.hub.request(node, "fetch", name=art["name"], file=art["file"],
                             url=f"{self.url}/{art['path']}", sha256=art["sha256"],
                             timeout=self.t["service_deploy_timeout"])
        self.rec.emit("artifact_fetched", node=node, sha256=art["sha256"],
                      cached=f["cached"])
        r = self.hub.request(node, "service.deploy", artifact=art["name"],
                             adapter=spec["adapter"], entrypoint=art["entrypoint"],
                             role=want, port=spec["port"], params=spec["params"],
                             peers=self.peers(node),
                             timeout=self.t["service_deploy_timeout"])
        self.record["services"][name]["effective"][node] = r["deploy"]
        self.rec.emit("service_deployed", node=node, service=name, role=want,
                      reused_state=bool(r["deploy"].get("reused_pgdata")))
        self.hub.request(node, "service.start",
                         timeout=self.t["service_deploy_timeout"])
        self.rec.emit("service_started", node=node, service=name, role=want,
                      port=spec["port"])
        observed = self.wait_running(node, want)
        self.rec.emit("service_running", node=node, service=name, role=observed)
        return observed

    def wait_running(self, node, want):
        """Wait for the service to run; enforce the role only where the
        scenario owns it."""
        declared = self.service_of(node)["role_source"] == "declared"
        deadline = time.monotonic() + self.t["service_ready_timeout"]
        while time.monotonic() < deadline:
            st = self.hub.snapshot()[node]
            if (st.get("service") or {}).get("state") == "running":
                if declared and st["role"] != want:
                    raise ModelError(f"{node}: observed role {st['role']!r}, "
                                     f"wanted {want!r}")
                return st["role"]
            time.sleep(0.05)
        raise TimeoutError(f"{node}: service not running: {self.hub.snapshot()[node]}")

    def bootstrap(self):
        for name in self.order:
            svc = self.services[name]
            order = self.spec(svc["members"][0])["role_order"]
            todo = [m for m in svc["members"]]
            for want in order:
                for node in [m for m in svc["members"] if self.roles[m] == want]:
                    self.bring_up(node, want)
                    todo.remove(node)
            if todo:
                raise ModelError(f"services.{name}: no role for {todo}")
            self.rec.emit("service_ready", service=name,
                          roles={m: self.hub.snapshot()[m]["role"]
                                 for m in svc["members"]})
        self.rec.emit("services_ready", bootstrap_roles=dict(self.roles))

    def restore(self, node):
        """A node came back with no service on it.  Membership is restored in
        both models; the role only where the scenario owns it."""
        svc = self.service_of(node)
        if svc["role_source"] == "declared":
            want = self.roles[node]
        else:
            # Never push a node back into the role it used to hold: after a
            # failover that is exactly the wrong thing to do.
            want = "member"
        self.rec.emit("node_returned", node=node, service=self.owner[node],
                      restoring=want)
        return self.bring_up(node, want)


def _wait_available(hub, node, timeout):
    """Wait for heartbeats to resume from the agent that was already there."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = hub.snapshot()[node]
        if st["available"] and (st.get("service") or {}).get("state") == "running":
            return
        time.sleep(0.05)
    raise TimeoutError(f"{node}: did not resume: {hub.snapshot()[node]}")


def _wait_registered_again(hub, node, timeout):
    """Wait for the node's new agent to register and report no service."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = hub.snapshot()[node]
        if st["available"] and (st.get("service") or {}).get("state") == "absent":
            return
        time.sleep(0.05)
    raise TimeoutError(f"{node}: did not come back: {hub.snapshot()[node]}")


def _observe_all(backend, nodes, semantics):
    return {n: {s: backend.observe(n, s) for s in semantics} for n in nodes}


def _reachable(hub, inventory):
    snap = hub.snapshot()
    return [n for n in inventory if snap[n]["available"]]


def _probe_all(hub, nodes, rec, phase):
    out = {}
    for n in nodes:
        try:
            out[n] = hub.request(n, "service.probe")["probe"]
        except Exception as e:
            out[n] = {"error": f"{type(e).__name__}: {e}"}
        rec.emit("service_probed", node=n, phase=phase)
    return out


def _sleep_until(t_mono):
    d = t_mono - time.monotonic()
    if d > 0:
        time.sleep(d)


def run(scenario_path, backend_name, catalog, experiments_dir=ROOT / "experiments"):
    sc, bdesc, art = prepare(scenario_path, backend_name, catalog)
    hw = load_yaml(ROOT / bdesc["hw_overlay"])
    impl = resolve_impl(bdesc["implementation"])
    reason = impl.preflight(hw)      # before lock and record: nothing to clean up
    if reason:
        raise ModelError(f"backend {backend_name}: {reason}")

    lockf = open(LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lockf.close()
        raise ModelError("another picluster run holds " + LOCK)
    try:
        return _run_locked(scenario_path, sc, bdesc, art, hw, backend_name,
                           catalog, experiments_dir)
    finally:
        lockf.close()          # released even if setup fails before the record


def _run_locked(scenario_path, sc, bdesc, art, hw, backend_name, catalog,
                experiments_dir):
    t = {k: duration(v, f"timing.{k}") for k, v in hw["timing"].items()}
    allowed = allowed_relaxations(sc, backend_name)
    effective = []          # this implementation applies no relaxation
    eff_provides = fidelity.effective_provides(bdesc, effective, catalog)

    rec = Recorder(experiments_dir)
    record = {
        "git_commit": git_state(),
        "scenario": {"path": str(pathlib.Path(scenario_path)),
                     "sha256": sha256_file(scenario_path), "content": sc},
        "backend_placement": {"default": backend_name, "nodes": {}},
        "backend_provides": bdesc["provides"],
        "effective_provides": sorted(eff_provides),
        "allowed_relaxations": allowed,
        "effective_relaxations": effective,
        "instruments": [],
        "hw_overlay": {"path": bdesc["hw_overlay"], "content": hw},
        "artifacts": {name: {k: v for k, v in m.items() if k != "path"}
                      for name, m in art.items()},
    }
    backend = resolve_impl(bdesc["implementation"])(bdesc, hw, rec.emit)
    # Observations needed by checks that will actually be evaluated.
    observes = sorted({o for c in sc["checks"]
                       if not fidelity.missing_semantics(catalog.checks[c["name"]], eff_provides)
                       for o in catalog.checks[c["name"]]["observes"]})
    hub = srv = wl = None
    results, verdict, status, error = [], None, "error", None
    try:
        rec.emit("experiment_start", scenario=sc["name"], backend=backend_name)
        removed = backend.cleanup_stale()
        if removed:
            rec.emit("stale_cleanup", removed=removed)
        addr, port, aport = backend.setup_bench()
        srv = ArtifactServer(addr, aport)
        hub = AgentHub(addr, port, rec.emit, t["heartbeat_interval"], t["unavailable_after"])
        backend.create_nodes(sc["nodes"]["count"], (addr, port))

        inventory = hub.wait_registered(sc["nodes"]["count"], t["register_timeout"])
        record["inventory"] = inventory
        record["backend_placement"]["nodes"] = {n: backend_name for n in inventory}

        deployment = Deployment(sc, catalog, art, srv.url, hub, rec, t, record)
        deployment.bootstrap()

        # The workload follows the observed primary of the service it drives,
        # so after a failover it reconnects to whoever holds the role now.
        driven = sc["workload"]["service"]
        members = sc["services"][driven]["members"]

        def primary_endpoint():
            snap = {n: st for n, st in hub.snapshot().items() if n in members}
            try:
                n = resolve({"role": "primary"}, snap)
            except ModelError as e:
                raise LookupError(str(e))
            return snap[n]["addr"], snap[n]["service"]["port"]

        wl = resolve_impl(catalog.workloads[sc["workload"]["type"]]["impl"])(
            primary_endpoint, t["workload_request_timeout"], rec.emit)
        wl.setup()
        # Probed around every fault, not only before the first one: a role
        # that changes in the middle is the thing being studied.
        probes = {}
        t_wl = time.monotonic()
        wl.start()
        rec.emit("workload_started")

        faulted = []
        for f in sorted(sc["faults"], key=lambda f: f["at"]):
            _sleep_until(t_wl + f["at"])
            phase = f"before_fault_{len(faulted) + 1}"
            probes[phase] = _probe_all(hub, _reachable(hub, inventory), rec, phase)
            if "before_fault" not in probes:
                probes["before_fault"] = probes[phase]     # the pre-fault state
            node = resolve(f["target"], hub.snapshot(),
                           bdesc["actions"][f["action"]]["target_state"], faulted)
            rec.emit("fault_resolved", action=f["action"], selector=f["target"], node=node)
            before = _observe_all(backend, inventory, observes)
            details = backend.fault(f["action"], node)
            rec.emit("fault_injected", action=f["action"], node=node, details=details)
            faulted.append({"action": f["action"], "node": node, "before": before,
                            "after": _observe_all(backend, inventory, observes)})
            # A primitive that powers a node back on says so.  What is
            # restored then depends on the service: a declared role, or
            # membership alone (see Deployment).
            phase = f"after_fault_{len(faulted)}"
            probes[phase] = _probe_all(hub, _reachable(hub, inventory), rec, phase)
            if details.get("expected_up"):
                if details.get("service_lost"):
                    # The node rebooted: a new agent, no service on it.
                    _wait_registered_again(hub, node, t["node_return_timeout"])
                    deployment.restore(node)
                else:
                    # It was only stopped: the same agent and the same service
                    # resume, and redeploying would destroy what we are testing.
                    _wait_available(hub, node, t["node_return_timeout"])
                    rec.emit("node_resumed", node=node,
                             service=hub.snapshot()[node].get("service"))

        _sleep_until(t_wl + sc["workload"]["duration"])
        wl.stop()
        probes["end"] = _probe_all(hub, _reachable(hub, inventory), rec, "end")
        record["workload"] = wl.summary()
        record["probes"] = probes
        rec.emit("workload_stopped", ok=wl.ok,
                 per_second=[wl.per_second.get(s, 0)
                             for s in range(int(sc["workload"]["duration"]))])

        record["faults"] = faulted
        ctx = CheckContext(backend.observe, inventory, faulted, probes, wl.summary(),
                           sc["services"])
        for c in sc["checks"]:
            cd = catalog.checks[c["name"]]
            missing = fidelity.missing_semantics(cd, eff_provides)
            if missing:
                res, ev = fidelity.NOT_MEANINGFUL, {"missing_semantics": missing}
            else:
                res, ev = resolve_impl(cd["impl"])(ctx.for_check(cd))
            results.append({"name": c["name"], "mandatory": c["mandatory"],
                            "requires": cd["requires"], "result": res, "evidence": ev})
            rec.emit("check_evaluated", name=c["name"], result=res)
        verdict = fidelity.verdict(results)
        rec.emit("verdict", verdict=verdict)
        status = "completed"
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        rec.emit("run_error", error=error)
    finally:
        if wl:
            wl.stop()
        if hub:
            hub.close()
        if srv:
            srv.close()
        try:
            backend.teardown(console_dest=rec.dir / "console")
            rec.emit("teardown_done")
        except Exception as e:
            rec.emit("teardown_error", error=f"{type(e).__name__}: {e}")
            status, error = "error", error or f"teardown: {e}"
        record.update({"run_status": status, "error": error,
                       "checks": results, "verdict": verdict})
        path = rec.write(record)
    return rec.id, path, record
