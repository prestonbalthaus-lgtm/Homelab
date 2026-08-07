"""Configuration for the NOC bridge.

Everything comes from environment variables. Nothing with a secret in it is
ever written to a file in this repository, and the API token in particular is
only ever read from the environment.
"""

import os


class ConfigError(RuntimeError):
    """Raised when the environment is missing something the bridge needs."""


def _parse_pairs(raw, label):
    """Parse "core=a,edge=b" into {"core": "a", "edge": "b"}."""
    pairs = {}
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ConfigError(
                "%s entry %r is not in id=value form" % (label, chunk))
        key, value = chunk.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ConfigError("%s entry %r has an empty side" % (label, chunk))
        pairs[key] = value
    return pairs


def _parse_links(raw):
    """Parse "core-edge,edge-lab" into [("core", "edge"), ("edge", "lab")]."""
    links = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" not in chunk:
            raise ConfigError("NOC_LINKS entry %r is not in a-b form" % chunk)
        left, right = chunk.split("-", 1)
        left = left.strip()
        right = right.strip()
        if not left or not right:
            raise ConfigError("NOC_LINKS entry %r has an empty side" % chunk)
        links.append((left, right))
    return links


def _parse_bool(raw, default):
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


DEFAULT_LINKS = "core-edge,edge-lab,lab-core,edge-inet"


class Config(object):
    """Reads the environment once, so failures surface at startup."""

    def __init__(self, env=None):
        env = os.environ if env is None else env

        # --- LibreNMS -----------------------------------------------------
        self.librenms_url = (env.get("LIBRENMS_URL") or "").rstrip("/")
        self.librenms_token = env.get("LIBRENMS_TOKEN") or ""
        self.librenms_timeout = float(env.get("LIBRENMS_TIMEOUT") or 5.0)
        self.librenms_verify_tls = _parse_bool(env.get("LIBRENMS_VERIFY_TLS"), True)

        # --- NetFlow ------------------------------------------------------
        self.nfdump_bin = env.get("NETFLOW_NFDUMP") or "nfdump"
        self.flow_dir = env.get("NETFLOW_DIR") or ""
        self.flow_window = int(env.get("NETFLOW_WINDOW_SECONDS") or 300)
        self.nfdump_timeout = float(env.get("NETFLOW_TIMEOUT") or 15.0)

        # --- topology -----------------------------------------------------
        # Which LibreNMS device backs each node id on the map.
        self.device_map = _parse_pairs(env.get("NOC_DEVICE_MAP"), "NOC_DEVICE_MAP")
        # The address each node is peered on, used to match BGP sessions and
        # NetFlow records to a link.
        self.node_ips = _parse_pairs(env.get("NOC_NODE_IPS"), "NOC_NODE_IPS")
        self.node_names = _parse_pairs(env.get("NOC_NODE_NAMES"), "NOC_NODE_NAMES")
        self.node_roles = _parse_pairs(env.get("NOC_NODE_ROLES"), "NOC_NODE_ROLES")
        self.links = _parse_links(env.get("NOC_LINKS") or DEFAULT_LINKS)

        # --- service ------------------------------------------------------
        self.bind = env.get("NOC_BIND") or "127.0.0.1"
        self.port = int(env.get("NOC_PORT") or 8090)
        self.cache_seconds = int(env.get("NOC_CACHE_SECONDS") or 30)
        # nginx passes the result of client certificate verification through.
        # The bridge refuses the login endpoint unless it says SUCCESS.
        self.client_verify_header = env.get("NOC_CLIENT_VERIFY_HEADER") or "X-SSL-Client-Verify"

    def require_librenms(self):
        """Fail loudly at startup rather than on the first request."""
        missing = []
        if not self.librenms_url:
            missing.append("LIBRENMS_URL")
        if not self.librenms_token:
            missing.append("LIBRENMS_TOKEN")
        if missing:
            raise ConfigError(
                "missing required environment variable(s): %s" % ", ".join(missing))

    @property
    def netflow_enabled(self):
        return bool(self.flow_dir)

    def node_ids(self):
        """Every node id mentioned anywhere, in a stable order."""
        seen = []
        for source in (self.device_map, self.node_ips, self.node_names, self.node_roles):
            for key in source:
                if key not in seen:
                    seen.append(key)
        for left, right in self.links:
            for key in (left, right):
                if key not in seen:
                    seen.append(key)
        return seen
