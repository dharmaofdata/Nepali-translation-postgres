"""Catalog and scenario model.  Pure data loading and validation, no I/O
beyond reading repository files."""

import importlib
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


class ModelError(Exception):
    pass


class StubBackend(ModelError):
    pass


def load_yaml(path):
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ModelError(f"{path}: top level must be a mapping")
    return data


_DUR = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m)?$")


def duration(v, where):
    """'5s' | '500ms' | '1m' | number of seconds -> float seconds."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = _DUR.match(str(v))
    if not m:
        raise ModelError(f"{where}: bad duration {v!r}")
    n, unit = float(m.group(1)), m.group(2) or "s"
    return n / 1000 if unit == "ms" else n * 60 if unit == "m" else n


def _names(v, where):
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ModelError(f"{where}: must be a list of names")
    return list(v)


TARGET_STATES = ("available", "unavailable", "any")


def _actions(v, where):
    """action name -> {target_state}: which node state the primitive applies
    to.  node.on targets a node that is down, so the selector must not filter
    it out as unavailable."""
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ModelError(f"{where}: must be a mapping of action -> properties")
    out = {}
    for name, spec in v.items():
        spec = spec or {}
        _only_keys(spec, {"target_state"}, f"{where}:{name}")
        state = spec.get("target_state", "available")
        if state not in TARGET_STATES:
            raise ModelError(f"{where}:{name}: target_state must be one of "
                             f"{list(TARGET_STATES)}")
        out[name] = {"target_state": state}
    return out


def _only_keys(d, allowed, where):
    extra = set(d) - set(allowed)
    if extra:
        raise ModelError(f"{where}: unknown keys {sorted(extra)}")


def resolve_impl(ref):
    """'package.module:attr' -> object.  The only way code is attached to
    data; no dispatch on names anywhere else."""
    mod, _, attr = ref.partition(":")
    if not mod or not attr:
        raise ModelError(f"bad implementation reference {ref!r}")
    return getattr(importlib.import_module(mod), attr)


BACKEND_KEYS = {"name", "status", "kernel", "provides", "planned_provides",
                "actions", "planned_actions", "implementation", "hw_overlay",
                "constraints"}


class Catalog:
    def __init__(self, root=ROOT, backend_dirs=None, check_dirs=()):
        self.root = pathlib.Path(root)
        self.semantics = set(_names(load_yaml(self.root / "semantics.yaml")["semantics"],
                                    "semantics.yaml"))
        self.relaxations = {}
        for name, d in load_yaml(self.root / "relaxations.yaml")["relaxations"].items():
            where = f"relaxations.yaml:{name}"
            _only_keys(d, {"class", "breaks"}, where)
            if d.get("class") not in ("A", "B"):
                raise ModelError(f"{where}: class must be A or B")
            self.relaxations[name] = {"class": d["class"],
                                      "breaks": self._sem(d.get("breaks"), where)}
        self.services = load_yaml(self.root / "services.yaml")["services"]
        self.workloads = load_yaml(self.root / "workloads.yaml")["workloads"]
        for name, w in self.workloads.items():
            if w["service"] not in self.services:
                raise ModelError(f"workloads.yaml:{name}: unknown service {w['service']}")
        self.backends = {}
        for d in backend_dirs or [self.root / "backends"]:
            for p in sorted(pathlib.Path(d).glob("*.yaml")):
                b = self._backend(p)
                if b["name"] in self.backends:
                    raise ModelError(f"{p}: duplicate backend {b['name']}")
                self.backends[b["name"]] = b
        self.checks = {}
        for d in [self.root / "checks", *check_dirs]:
            for p in sorted(pathlib.Path(d).glob("*.yaml")):
                c = load_yaml(p)
                _only_keys(c, {"name", "requires", "observes", "impl"}, str(p))
                if c.get("name") != p.stem or "impl" not in c:
                    raise ModelError(f"{p}: need name == file stem and impl")
                c["requires"] = self._sem(c.get("requires"), str(p))
                # A check may only observe what it requires: an observation
                # of semantics the backend does not provide means nothing.
                c["observes"] = self._sem(c.get("observes"), str(p))
                if not set(c["observes"]) <= set(c["requires"]):
                    raise ModelError(f"{p}: observes must be a subset of requires")
                self.checks[c["name"]] = c

    def _sem(self, v, where):
        names = _names(v, where)
        unknown = set(names) - self.semantics
        if unknown:
            raise ModelError(f"{where}: unknown semantics {sorted(unknown)}")
        return names

    def _backend(self, p):
        b = load_yaml(p)
        where = str(p)
        _only_keys(b, BACKEND_KEYS, where)
        if b.get("name") != p.stem:
            raise ModelError(f"{where}: name must equal file stem")
        status = b.get("status")
        for k in ("provides", "planned_provides"):
            b[k] = self._sem(b.get(k), f"{where}:{k}")
        for k in ("actions", "planned_actions"):
            b[k] = _actions(b.get(k), f"{where}:{k}")
        if status == "stub":
            if b["provides"] or b["actions"] or "implementation" in b:
                raise ModelError(f"{where}: stub must have empty provides/actions "
                                 "and no implementation")
        elif status == "implemented":
            if "implementation" not in b or "hw_overlay" not in b:
                raise ModelError(f"{where}: implemented backend needs "
                                 "implementation and hw_overlay")
        else:
            raise ModelError(f"{where}: status must be stub or implemented")
        b.setdefault("constraints", {})
        return b

    def backend(self, name):
        if name not in self.backends:
            raise ModelError(f"unknown backend {name}")
        return self.backends[name]

    def require_implemented(self, name):
        b = self.backend(name)
        if b["status"] != "implemented":
            raise StubBackend(f"backend {name} is declared but not implemented")
        return b


SCENARIO_KEYS = {"name", "nodes", "services", "workload", "faults", "checks", "fidelity"}
SERVICE_KEYS = {"type", "artifact", "placement", "role_source", "initial", "depends_on"}
ROLE_SOURCES = ("declared", "observed")
SELECTOR_KEYS = {"role", "node", "node_of"}


def load_scenario(path, catalog):
    s = load_yaml(path)
    _only_keys(s, SCENARIO_KEYS, str(path))
    if not isinstance(s.get("name"), str):
        raise ModelError("scenario: name required")
    n = (s.get("nodes") or {}).get("count")
    if not isinstance(n, int) or n < 1:
        raise ModelError("scenario: nodes.count must be a positive integer")

    s["services"] = services = _services(s.get("services"), n, catalog)
    driven = [name for name, svc in services.items() if svc["workload_target"]]

    wl = s.get("workload") or {}
    _only_keys(wl, {"type", "duration"}, "workload")
    w = catalog.workloads.get(wl.get("type"))
    if w is None:
        raise ModelError(f"workload.type {wl.get('type')!r} unknown")
    target = [name for name in driven if catalog.services[services[name]["type"]]
              ["driven_by"] == wl["type"]]
    if len(target) != 1:
        raise ModelError(f"workload {wl['type']} needs exactly one service it can "
                         f"drive, found {target}")
    wl["service"] = target[0]
    wl["duration"] = duration(wl.get("duration", "10s"), "workload.duration")

    faults = s.get("faults") or []
    for i, f in enumerate(faults):
        where = f"faults[{i}]"
        _only_keys(f, {"at", "action", "target"}, where)
        f["at"] = duration(f.get("at"), f"{where}.at")
        if not isinstance(f.get("action"), str):
            raise ModelError(f"{where}: action required")
        t = f.get("target")
        if not isinstance(t, dict) or len(t) != 1 or not set(t) <= SELECTOR_KEYS:
            raise ModelError(f"{where}: target must be one of {sorted(SELECTOR_KEYS)}")
        # {node_of: k} is "whichever node fault k hit", 1-based: it lets a
        # scenario act on a node it never names.
        if "node_of" in t:
            k = t["node_of"]
            if not isinstance(k, int) or not 1 <= k <= i:
                raise ModelError(f"{where}: node_of must be the 1-based index "
                                 f"of an earlier fault")
        if f["at"] >= wl["duration"]:
            raise ModelError(f"{where}: at must be before workload end")

    checks = s.get("checks") or []
    for i, c in enumerate(checks):
        _only_keys(c, {"name", "mandatory"}, f"checks[{i}]")
        if c.get("name") not in catalog.checks:
            raise ModelError(f"checks[{i}]: unknown check {c.get('name')!r}")
        c["mandatory"] = bool(c.get("mandatory", False))
    if not any(c["mandatory"] for c in checks):
        # A verdict over zero mandatory checks would be a vacuous PASS.
        raise ModelError("scenario: at least one mandatory check required")

    fid = s.get("fidelity") or {}
    for bname, pol in fid.items():
        catalog.backend(bname)
        if pol == "strict":
            continue
        if not isinstance(pol, dict):
            raise ModelError(f"fidelity.{bname}: 'strict' or {{allow: [...]}}")
        _only_keys(pol, {"allow"}, f"fidelity.{bname}")
        for r in _names(pol.get("allow"), f"fidelity.{bname}.allow"):
            if r not in catalog.relaxations:
                raise ModelError(f"fidelity.{bname}: unknown relaxation {r}")
    s["faults"], s["checks"], s["fidelity"] = faults, checks, fid
    return s


def _services(v, node_count, catalog):
    """Several services per scenario, each placed on named nodes.

    `role_source` is the executor's only interest in how roles come about:
    `declared` means the scenario owns them and a returning node gets its role
    back; `observed` means something outside decides and the executor only
    keeps membership, never a role."""
    if not isinstance(v, dict) or not v:
        raise ModelError("scenario: services must be a non-empty mapping")
    nodes = [f"n{i}" for i in range(1, node_count + 1)]
    out = {}
    for name, svc in v.items():
        where = f"services.{name}"
        svc = dict(svc or {})
        _only_keys(svc, SERVICE_KEYS, where)
        if svc.get("type") not in catalog.services:
            raise ModelError(f"{where}.type {svc.get('type')!r} unknown")
        if not isinstance(svc.get("artifact"), str):
            raise ModelError(f"{where}.artifact required")
        members = _names((svc.get("placement") or {}).get("nodes"),
                         f"{where}.placement.nodes")
        _only_keys(svc.get("placement") or {}, {"nodes"}, f"{where}.placement")
        if not members:
            raise ModelError(f"{where}.placement.nodes required")
        unknown = [m for m in members if m not in nodes]
        if unknown:
            raise ModelError(f"{where}.placement.nodes: {unknown} outside "
                             f"nodes.count {node_count}")
        if len(set(members)) != len(members):
            raise ModelError(f"{where}.placement.nodes: repeated node")
        svc["members"] = members
        svc["role_source"] = svc.get("role_source", "declared")
        if svc["role_source"] not in ROLE_SOURCES:
            raise ModelError(f"{where}.role_source must be one of "
                             f"{list(ROLE_SOURCES)}")
        initial = dict(svc.get("initial") or {})
        _only_keys(initial, {"primary"}, f"{where}.initial")
        primary = initial.get("primary", members[0])
        if primary not in members:
            raise ModelError(f"{where}.initial.primary {primary!r} is not a member")
        svc["initial"] = {"primary": primary}
        svc["depends_on"] = _names(svc.get("depends_on"), f"{where}.depends_on")
        svc["workload_target"] = bool(catalog.services[svc["type"]]["driven_by"])
        out[name] = svc
    placed = {}
    for name, svc in out.items():
        for m in svc["members"]:
            if m in placed:
                raise ModelError(f"node {m} hosts both {placed[m]} and {name}; "
                                 f"one service per node")
            placed[m] = name
    for name, svc in out.items():
        for dep in svc["depends_on"]:
            if dep not in out:
                raise ModelError(f"services.{name}.depends_on: unknown service {dep}")
    return out


def service_order(services):
    """Declaration order, with dependencies first."""
    done, order = set(), []

    def visit(name, seen):
        if name in done:
            return
        if name in seen:
            raise ModelError(f"services: dependency cycle at {name}")
        for dep in services[name]["depends_on"]:
            visit(dep, seen | {name})
        done.add(name)
        order.append(name)

    for name in services:
        visit(name, set())
    return order


def allowed_relaxations(scenario, backend_name):
    pol = scenario["fidelity"].get(backend_name, "strict")
    return [] if pol == "strict" else list(pol.get("allow") or [])


def scenario_requirements(scenario, catalog):
    req = set()
    for c in scenario["checks"]:
        req |= set(catalog.checks[c["name"]]["requires"])
    return req


def scenario_actions(scenario):
    return {f["action"] for f in scenario["faults"]}
