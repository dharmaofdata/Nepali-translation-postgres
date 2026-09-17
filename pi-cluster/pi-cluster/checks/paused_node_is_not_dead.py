"""A paused node is stopped, not gone.

The point of the primitive is the difference from node.off: the processes are
still there and the kernel still answers, so peers see liveness while nothing
progresses.  A pause that killed the node, or that peers could not tell from a
dead node, would be the wrong fault."""

from controller.fidelity import FAIL, PASS

PAUSED, GONE = "peers_see_no_progress", "processes_gone"


def evaluate(ctx):
    pauses = [f for f in ctx.faulted if f["action"] == "process.pause"]
    if not pauses:
        return FAIL, {"reason": "no process.pause executed (vacuous)"}
    ev, reasons = {"faults": []}, []
    for f in pauses:
        n = f["node"]
        before, after = f["before"][n], f["after"][n]
        ev["faults"].append({"node": n, "before": before, "after": after})
        if before[PAUSED]["holds"]:
            reasons.append(f"{n}: already paused before the fault (vacuous)")
        if not after[PAUSED]["holds"]:
            reasons.append(f"{n}: not paused after process.pause: "
                           f"{after[PAUSED]['evidence']}")
        if after[GONE]["holds"]:
            reasons.append(f"{n}: pause killed the node instead of stopping it")
    # Resuming must undo it, or the primitive is one-way.
    for f in [f for f in ctx.faulted if f["action"] == "process.resume"]:
        n = f["node"]
        ev["faults"].append({"node": n, "action": "process.resume",
                             "before": f["before"][n], "after": f["after"][n]})
        if not f["before"][n][PAUSED]["holds"]:
            reasons.append(f"{n}: was not paused when process.resume was applied")
        if f["after"][n][PAUSED]["holds"]:
            reasons.append(f"{n}: still paused after process.resume")
    if reasons:
        ev["reasons"] = reasons
    return (FAIL if reasons else PASS), ev
