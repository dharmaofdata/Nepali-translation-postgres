"""Every acknowledged write is still there after the crash and restart.

The primary is killed, so the id after the last acknowledged one may or may
not have committed: one extra id is allowed, a missing one is not."""

from controller.fidelity import FAIL, PASS


def evaluate(ctx):
    acked = ctx.workload.get("acked", 0)
    ons = [f["node"] for f in ctx.faulted if f["action"] == "node.on"]
    ev, reasons = {"acked": acked, "nodes": {}, "crashed": {}}, []
    if acked <= 0:
        reasons.append("no acknowledged writes (vacuous)")
    if not ons:
        reasons.append("no node.on executed (vacuous)")
    # The data must have survived a real crash, not a tidy shutdown that
    # never happened.
    for f in [f for f in ctx.faulted if f["action"] == "node.off"]:
        obs = f["after"][f["node"]]["processes_gone"]
        ev["crashed"][f["node"]] = obs["holds"]
        if not obs["holds"]:
            reasons.append(f"{f['node']}: did not actually crash")
    for n in ons:
        log = (ctx.probes["end"].get(n) or {}).get("log") or {}
        ev["nodes"][n] = log
        if "error" in log or not log:
            reasons.append(f"{n}: cannot read the table: {log.get('error', 'no probe')}")
            continue
        if log["max_id"] < acked:
            reasons.append(f"{n}: lost acknowledged writes: has {log['max_id']}, "
                           f"acked {acked}")
        if log["max_id"] > acked + 1:
            reasons.append(f"{n}: has id {log['max_id']}, more than one beyond "
                           f"acked {acked}")
        if log["count"] != log["max_id"]:
            reasons.append(f"{n}: gap — max_id {log['max_id']}, count {log['count']}")
    if reasons:
        ev["reasons"] = reasons
    return (FAIL if reasons else PASS), ev
