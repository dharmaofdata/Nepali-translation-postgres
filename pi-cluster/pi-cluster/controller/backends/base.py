"""Backend implementation interface.  The executor talks only to this;
the backend maps actions to its own bench primitives."""


class Backend:
    def __init__(self, descriptor, hw, emit):
        self.descriptor, self.hw, self.emit = descriptor, hw, emit

    @staticmethod
    def preflight(hw):
        """None if this host can run the backend, else a reason.  Called
        before any experiment record exists."""
        return None

    def cleanup_stale(self):
        """Remove leftovers of a previous crashed run owned by this backend."""
        raise NotImplementedError

    def setup_bench(self):
        """Prepare the bench; return (addr, port, artifact_port) the controller
        must listen on so that agents can reach it.  Addressing is backend
        data, not scenario data."""
        raise NotImplementedError

    def create_nodes(self, count, controller_endpoint):
        """Bench: create/power on `count` dev nodes running the agent.  The
        controller learns them only through agent registration."""
        raise NotImplementedError

    def fault(self, action, node):
        """Execute a fault action on a node; return details for the record."""
        raise NotImplementedError

    def observe(self, node, semantic):
        """Observe whether `semantic` holds for `node` right now.
        Returns {"holds": bool, "evidence": {...}}; evidence is backend
        specific (pids on a shared-kernel bench, relay state on hardware).
        Raises LookupError if the backend cannot observe `semantic`."""
        raise NotImplementedError

    def teardown(self, console_dest=None):
        raise NotImplementedError
