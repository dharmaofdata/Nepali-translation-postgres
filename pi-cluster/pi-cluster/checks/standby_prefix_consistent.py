"""After the primary is gone, each surviving standby holds a prefix of the
acknowledged writes: ids 1..M with no gaps, M <= acked.

This is the property asynchronous replication does promise.  It says nothing
about how much was lost; the lost window is a measurement, not a check.  The
workload acknowledges one id at a time, so the acked set is 1..acked."""

from controller.fidelity import FAIL, PASS

SEM = "processes_gone"


def evaluate(ctx):
    acked = ctx.workload.get("acked", 0)
    targets = [f["node"] for f in ctx.faulted if f["action"] == "node.off"]
    ev = {"acked": acked, "standbys": {}, "reasons": []}
    if acked <= 0:
        ev["reasons"].append("no acknowledged writes (vacuous)")
    for f in [f for f in ctx.faulted if f["action"] == "node.off"]:
        if not f["after"][f["node"]][SEM]["holds"]:
            ev["reasons"].append(f"{f['node']}: node.off target still running")
    standbys = {n: p for n, p in ctx.probes["end"].items()
                if p.get("role") == "replica" and n not in targets}
    if not standbys:
        ev["reasons"].append("no surviving standby probed (vacuous)")
    for n, p in sorted(standbys.items()):
        log = p.get("log") or {}
        ev["standbys"][n] = log
        if "error" in log:
            ev["reasons"].append(f"{n}: cannot read table: {log['error']}")
            continue
        if log["max_id"] != log["count"]:
            ev["reasons"].append(f"{n}: gap — max_id {log['max_id']}, "
                                 f"count {log['count']}")
        if log["max_id"] > acked:
            ev["reasons"].append(f"{n}: has id {log['max_id']} > acked {acked}")
    return (FAIL if ev["reasons"] else PASS), ev
