"""At the end of the run exactly one member that should be up is writable, and
every member that should be up answered."""

from checks.topology import down_after, ha_members, writable
from controller.fidelity import FAIL, PASS


def evaluate(ctx):
    expected = [m for m in ha_members(ctx) if m not in down_after(ctx.faulted)]
    probes = ctx.probes.get("end", {})
    ev, reasons = {"expected_up": expected}, []
    missing = [m for m in expected if m not in probes or probes[m].get("error")]
    seen = {m: probes[m] for m in expected if m in probes}
    w = sorted(m for m, p in seen.items() if writable(p))
    ev.update({"writable": w, "unreachable": missing,
               "roles": {m: p.get("role") for m, p in seen.items()}})
    if not expected:
        reasons.append("no member is expected to be up (vacuous)")
    if missing:
        reasons.append(f"could not read {missing}")
    if len(w) != 1:
        reasons.append(f"{len(w)} writable members: {w}")
    if reasons:
        ev["reasons"] = reasons
    return (FAIL if reasons else PASS), ev
