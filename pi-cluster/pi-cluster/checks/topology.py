"""Who is supposed to be up, and which nodes a check is about.

Derived from the scenario's placement and the faults executed so far, never
from who answered a probe."""


def down_after(faulted, upto=None):
    down = set()
    for f in faulted[:upto]:
        if f["action"] in ("node.off", "process.pause"):
            down.add(f["node"])
        elif f["action"] in ("node.on", "process.resume"):
            down.discard(f["node"])
    return down


def service_members(ctx, role_source=None):
    return {name: svc["members"] for name, svc in ctx.services.items()
            if role_source in (None, svc["role_source"])}


def ha_members(ctx):
    """Members of the services whose roles are decided outside the executor;
    falls back to the declared ones so the checks also work on a static
    scenario."""
    observed = service_members(ctx, "observed")
    if observed:
        return sorted(m for ms in observed.values() for m in ms)
    return sorted(ctx.members("declared"))


def writable(probe):
    if probe.get("writable") is not None:
        return probe["writable"] is True
    return probe.get("role") == "primary"
