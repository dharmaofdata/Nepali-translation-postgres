"""The returned node recovered its own storage instead of being rebuilt.

Evidence: it reused its PGDATA, its system identifier is unchanged and still
matches the rest of the cluster, and the postmaster is a new one."""

from controller.fidelity import FAIL, PASS


def evaluate(ctx):
    ons = [f["node"] for f in ctx.faulted if f["action"] == "node.on"]
    if not ons:
        return FAIL, {"reason": "no node.on executed (vacuous)"}
    before, end = ctx.probes["before_fault"], ctx.probes["end"]
    ev, reasons = {"nodes": {}, "crashed": {}}, []
    for f in [f for f in ctx.faulted if f["action"] == "node.off"]:
        obs = f["after"][f["node"]]["processes_gone"]
        ev["crashed"][f["node"]] = obs["holds"]
        if not obs["holds"]:
            reasons.append(f"{f['node']}: did not actually crash, so there was "
                           f"nothing to recover from")
    ids = {n: p.get("system_identifier") for n, p in end.items() if p.get("role")}
    if len(set(ids.values())) > 1:
        reasons.append(f"cluster split: system identifiers {ids}")
    for n in ons:
        b, e = before.get(n, {}), end.get(n, {})
        ev["nodes"][n] = {"system_identifier": [b.get("system_identifier"),
                                                e.get("system_identifier")],
                          "postmaster_start": [b.get("postmaster_start"),
                                               e.get("postmaster_start")],
                          "reused_pgdata": e.get("reused_pgdata"),
                          "last_checkpoint": e.get("last_checkpoint")}
        if not e:
            reasons.append(f"{n}: no probe after the restart")
            continue
        if e.get("reused_pgdata") is not True:
            reasons.append(f"{n}: PGDATA was rebuilt, not recovered")
        if b.get("system_identifier") != e.get("system_identifier"):
            reasons.append(f"{n}: system identifier changed: "
                           f"{b.get('system_identifier')} -> {e.get('system_identifier')}")
        if b.get("postmaster_start") == e.get("postmaster_start"):
            reasons.append(f"{n}: same postmaster as before the fault (vacuous)")
    if reasons:
        ev["reasons"] = reasons
    return (FAIL if reasons else PASS), ev
