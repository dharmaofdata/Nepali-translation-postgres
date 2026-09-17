"""Every node.on target is back and serving its assigned role at the end."""

from controller.fidelity import FAIL, PASS


def evaluate(ctx):
    ons = [f for f in ctx.faulted if f["action"] == "node.on"]
    if not ons:
        return FAIL, {"reason": "no node.on executed (vacuous)"}
    ev, reasons = {"nodes": {}}, []
    for f in ons:
        n = f["node"]
        # It must have been down when it was powered on, or "returns" is
        # not what happened.
        obs = f["before"][n].get("processes_gone")
        was_down = obs["holds"] if obs else None
        probe = ctx.probes["end"].get(n)
        ev["nodes"][n] = {"was_down": was_down, "end_probe": probe}
        if was_down is None:
            reasons.append(f"{n}: processes_gone was not observed, cannot tell "
                           f"whether it was down")
        elif not was_down:
            reasons.append(f"{n}: was still running when node.on was applied")
        if probe is None:
            reasons.append(f"{n}: not reachable at the end of the run")
        elif probe.get("error"):
            reasons.append(f"{n}: probe failed: {probe['error']}")
        elif not probe.get("role"):
            reasons.append(f"{n}: service not running at the end")
    if reasons:
        ev["reasons"] = reasons
    return (FAIL if reasons else PASS), ev
