"""At the end of the run the primary is streaming to every replica that is up.

Reconnection is the standby's own business (walreceiver retries); nothing in
the controller pushes it."""

from checks.replication_streaming import live_nodes, streaming_state


def evaluate(ctx):
    up, down = live_nodes(ctx)
    res, ev = streaming_state(ctx.probes["end"], "end", up)
    ev["down_at_end"] = down
    return res, ev
