"""The old primary comes back as a replica and is not writable.

The executor restores membership only for an HA-managed service, so whatever
role it ends up in was decided elsewhere.  Two writable nodes here would be
the split brain the whole arrangement exists to prevent."""

from checks.topology import ha_members, writable
from controller.fidelity import FAIL, PASS


def evaluate(ctx):
    members = ha_members(ctx)
    ons = [f for f in ctx.faulted if f["action"] == "node.on" and f["node"] in members]
    if not ons:
        return FAIL, {"reason": "no node.on on a member of the HA service (vacuous)"}
    end = ctx.probes.get("end", {})
    ev, reasons = {"nodes": {}}, []
    for f in ons:
        n = f["node"]
        was_primary = writable(ctx.probes["before_fault"].get(n) or {})
        probe = end.get(n) or {}
        monitor = [p for m, p in end.items() if p.get("role") == "monitor"]
        says = next((e for mp in monitor for e in mp.get("nodes", [])
                     if e.get("node") == n), None)
        ev["nodes"][n] = {"was_primary_before": was_primary, "end": probe,
                          "monitor_says": says}
        if not was_primary:
            reasons.append(f"{n}: was not the primary before the fault (vacuous)")
        if not probe or probe.get("error"):
            reasons.append(f"{n}: did not come back: {probe.get('error', 'no probe')}")
            continue
        if writable(probe):
            reasons.append(f"{n}: came back writable")
        if probe.get("role") != "replica":
            reasons.append(f"{n}: came back as {probe.get('role')!r}")
        if says and says.get("reported") not in (None, "secondary", "catchingup"):
            reasons.append(f"{n}: the decision maker calls it "
                           f"{says['reported']!r}")
    if reasons:
        ev["reasons"] = reasons
    return (FAIL if reasons else PASS), ev
