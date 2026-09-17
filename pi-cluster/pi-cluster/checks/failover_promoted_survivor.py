"""The node that was killed is not the one holding the role afterwards.

Nothing in the executor promotes anything, so a role that moved is a decision
taken outside it."""

from checks.topology import ha_members, writable
from controller.fidelity import FAIL, PASS


def evaluate(ctx):
    offs = [f for f in ctx.faulted if f["action"] == "node.off"
            and f["node"] in ha_members(ctx)]
    if not offs:
        return FAIL, {"reason": "no node.off on a member of the HA service (vacuous)"}
    ev, reasons = {"faults": []}, []
    for f in offs:
        lost = f["node"]
        before = ctx.probes["before_fault"].get(lost) or {}
        end = ctx.probes.get("end", {})
        holder = sorted(m for m, p in end.items()
                        if m in ha_members(ctx) and writable(p))
        ev["faults"].append({"lost": lost, "was_writable": writable(before),
                             "writable_at_end": holder})
        if not f["after"][lost]["processes_gone"]["holds"]:
            reasons.append(f"{lost}: did not actually go away")
        if not writable(before):
            reasons.append(f"{lost}: was not the writable member before the "
                           f"fault (vacuous)")
        if holder == [lost]:
            reasons.append(f"{lost}: still the writable member after being killed")
        if len(holder) != 1:
            reasons.append(f"{len(holder)} writable members at the end: {holder}")
    if reasons:
        ev["reasons"] = reasons
    return (FAIL if reasons else PASS), ev
