"""node.off targets reach processes_gone; non-targets do not.

Checks the semantics through backend observation only; how it is observed
(pids, relay state, ...) is backend evidence."""

from controller.fidelity import FAIL, PASS

SEM = "processes_gone"


def evaluate(ctx):
    """Evaluated per fault, from the observations taken around it: the node
    may legitimately be alive again by the end of the run."""
    offs = [f for f in ctx.faulted if f["action"] == "node.off"]
    if not offs:
        return FAIL, {"reason": "no node.off executed (vacuous)"}
    ev, reasons = {"faults": []}, []
    for f in offs:
        n = f["node"]
        before, after = f["before"][n][SEM], f["after"][n][SEM]
        controls = {m: f["after"][m][SEM] for m in ctx.inventory if m != n}
        ev["faults"].append({"node": n, "before": before, "after": after,
                             "controls": controls})
        if before["holds"]:
            reasons.append(f"{n}: {SEM} already held before the fault (vacuous)")
        if not after["holds"]:
            reasons.append(f"{n}: {SEM} does not hold after node.off")
        # Control: node.off must be targeted, not global.
        for m, obs in sorted(controls.items()):
            if obs["holds"]:
                reasons.append(f"{m}: {SEM} holds on a non-target node")
    if reasons:
        ev["reasons"] = reasons
    return (FAIL if reasons else PASS), ev
