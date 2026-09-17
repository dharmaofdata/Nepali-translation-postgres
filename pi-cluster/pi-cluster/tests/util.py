import functools
import pathlib
import unittest

from controller.model import ROOT, Catalog

FIXTURES = ROOT / "tests" / "fixtures"
SMOKE = ROOT / "scenarios" / "smoke-node-off.yaml"


def catalog(**kw):
    return Catalog(check_dirs=[FIXTURES / "checks"], **kw)


def write(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@functools.lru_cache(maxsize=None)
def nsnode_unavailable():
    # The backend's own preflight probe: creates and removes a netns, a veth
    # and a cgroup.  Root without CAP_NET_ADMIN (typical CI container) skips.
    from controller.backends.laptop_nsnode import probe
    return probe()


def check_ctx(observe, inventory, faulted, probes, workload, services=None):
    """A CheckContext for unit tests.  Without `services` it describes one
    service with declared roles over the whole inventory."""
    from controller.executor import CheckContext
    if services is None:
        services = {"svc": {"members": list(inventory), "role_source": "declared",
                            "initial": {"primary": inventory[0] if inventory else None}}}
    return CheckContext(observe, list(inventory), faulted, probes, workload, services)


@functools.lru_cache(maxsize=None)
def postgres_unavailable():
    try:
        import psycopg2  # noqa: F401
    except ImportError as e:
        return str(e)
    from controller.artifacts import load_manifest
    from controller.model import ModelError
    try:
        load_manifest("postgres-16")
    except ModelError as e:
        return str(e)
    return None


def needs_postgres(cls):
    reason = postgres_unavailable()
    return unittest.skip(f"postgres artifact unavailable: {reason}")(cls) if reason else cls


def needs_nsnode(cls):
    reason = nsnode_unavailable()
    return unittest.skip(f"laptop-nsnode unavailable: {reason}")(cls) if reason else cls
