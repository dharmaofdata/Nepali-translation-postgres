"""Every replica that should be up is streaming from the primary.

The set of replicas comes from the run's inventory and the faults executed so
far, not from the probes: a standby whose probe failed would otherwise vanish
from both sides of the comparison and the check would pass with it dead."""

from controller.fidelity import FAIL, PASS


def live_nodes(ctx):
    down = set()
    for f in ctx.faulted:
        if f["action"] == "node.off":
            down.add(f["node"])
        elif f["action"] == "node.on":
            down.discard(f["node"])
    return [n for n in ctx.inventory if n not in down], sorted(down)


def evaluate(ctx):
    # Before the first fault every node is up, so the expected set is the
    # whole inventory.
    return streaming_state(ctx.probes["before_fault"], "before_fault",
                           list(ctx.inventory))


def streaming_state(probes, phase, expected_up):
    ev = {"phase": phase, "expected_up": sorted(expected_up), "reasons": []}
    missing = [n for n in expected_up if n not in probes]
    failed = sorted(n for n in expected_up if (probes.get(n) or {}).get("error"))
    ev["missing_probe"], ev["failed_probe"] = sorted(missing), failed
    if not probes:
        ev["reasons"].append(f"no {phase} probe")
        return FAIL, ev
    if missing:
        ev["reasons"].append(f"no probe for {missing}, which should be up")
    if failed:
        ev["reasons"].append(f"probe failed on {failed}")

    primaries = [n for n in expected_up if (probes.get(n) or {}).get("role") == "primary"]
    replicas = [n for n in expected_up if (probes.get(n) or {}).get("role") == "replica"]
    ev["primary"], ev["replicas"] = primaries, replicas
    if len(primaries) != 1:
        ev["reasons"].append(f"expected exactly one primary among the nodes that "
                             f"should be up, got {primaries}")
        return FAIL, ev
    # Every node that is up and is not the primary must be a streaming replica.
    want = [n for n in expected_up if n != primaries[0]]
    if not want:
        ev["reasons"].append("no replica in the run (vacuous)")
    if sorted(replicas) != sorted(want):
        ev["reasons"].append(f"nodes {sorted(set(want) - set(replicas))} are up but "
                             f"not replicas")
    streaming = [r for r in probes[primaries[0]].get("replication", [])
                 if r.get("state") == "streaming"]
    ev["streaming"] = streaming
    if len(streaming) != len(want):
        ev["reasons"].append(f"{len(streaming)} walsenders streaming, "
                             f"{len(want)} replicas expected up")
    return (FAIL if ev["reasons"] else PASS), ev
