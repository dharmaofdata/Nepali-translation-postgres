"""Runtime target resolution against observed state."""

from .model import ModelError


def resolve(selector, observed, target_state="available", faulted=()):
    """selector: {'role': r} | {'node': id} | {'node_of': fault index};
    observed: {node: {'role': ..., 'available': bool}}.  Exactly one node in
    `target_state` must match."""
    (key, value), = selector.items()
    if key == "node_of":
        if len(faulted) < value:
            raise ModelError(f"selector {selector}: fault {value} has not run")
        match = [faulted[value - 1]["node"]]
    elif key == "node":
        match = [n for n in observed if n == value]
    else:
        match = [n for n, st in observed.items() if st.get("role") == value]
    if target_state != "any":
        want = target_state == "available"
        match = [n for n in match if bool(observed[n].get("available")) == want]
    match = sorted(match)
    if len(match) != 1:
        raise ModelError(f"selector {selector} matched {match} in state "
                         f"{target_state}, need exactly one")
    return match[0]
