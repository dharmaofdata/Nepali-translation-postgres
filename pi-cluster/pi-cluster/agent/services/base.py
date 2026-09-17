"""Service adapter interface.  Adapters run inside the node, next to the
service; the controller only passes role, port, params and peer addresses."""


class Service:
    def __init__(self, node_id, state_dir, bindir, port, role, params, peers):
        self.node_id, self.state_dir, self.bindir = node_id, state_dir, bindir
        self.port, self.role, self.params, self.peers = port, role, params, peers

    def deploy(self):
        """Create node-local state and render effective config.  Returns a
        dict for the experiment record (effective config, versions)."""
        raise NotImplementedError

    def start(self):
        raise NotImplementedError

    def status(self):
        """Cheap, called on every heartbeat: {state, role, port, ...}.
        `role` is observed, not the role we were asked to take."""
        raise NotImplementedError

    def probe(self):
        """Service-specific observation for checks.  Opaque to the core."""
        return {}
