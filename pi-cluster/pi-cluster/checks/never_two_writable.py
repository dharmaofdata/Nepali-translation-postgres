"""At no point we probed were two members writable at once.

This is the safety property a failover must not break: availability is a
separate question, and a promoted primary that accepts no writes yet does not
violate this one."""

from checks.topology import ha_members, writable
from controller.fidelity import FAIL, PASS


def evaluate(ctx):
    members = ha_members(ctx)
    ev, reasons = {"phases": {}}, []
    for phase, probes in sorted(ctx.probes.items()):
        seen = {m: probes[m] for m in members if m in probes}
        w = sorted(m for m, p in seen.items() if writable(p))
        ev["phases"][phase] = {"writable": w,
                               "roles": {m: p.get("role") for m, p in seen.items()}}
        if len(w) > 1:
            reasons.append(f"{phase}: {w} are all writable")
    if not ev["phases"]:
        reasons.append("nothing was probed (vacuous)")
    if len(ctx.probes) < 2:
        reasons.append("only one phase probed; a failover cannot be observed "
                       "in it (vacuous)")
    if reasons:
        ev["reasons"] = reasons
    return (FAIL if reasons else PASS), ev
