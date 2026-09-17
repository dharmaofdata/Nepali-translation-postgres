"""A node that died left nothing of itself on the host.

Observed right after node.off, while the node is down: its SysV segments went
with its ipc namespace, its POSIX segments with its private /dev/shm.  These
are host-wide observations, so they also catch leaks from the nodes that are
still running."""

from controller.fidelity import FAIL, PASS


def _evaluate(ctx, semantic):
    offs = [f for f in ctx.faulted if f["action"] == "node.off"]
    if not offs:
        return FAIL, {"reason": "no node.off executed (vacuous)"}
    ev, reasons = {"after_fault": {}, "now": ctx.observe(offs[-1]["node"], semantic)}, []
    for f in offs:
        obs = f["after"][f["node"]][semantic]
        ev["after_fault"][f["node"]] = obs
        if not obs["holds"]:
            reasons.append(f"after node.off({f['node']}): {obs['evidence']}")
    if not ev["now"]["holds"]:
        reasons.append(f"at the end of the run: {ev['now']['evidence']}")
    if reasons:
        ev["reasons"] = reasons
    return (FAIL if reasons else PASS), ev


def ipc(ctx):
    return _evaluate(ctx, "private_ipc_namespace")


def dev_shm(ctx):
    return _evaluate(ctx, "private_dev_shm")
