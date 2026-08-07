"""Read BGP peer state out of LibreNMS.

Two calls are needed. /api/v0/bgp returns sessions keyed by device_id, and
/api/v0/devices maps those ids to hostnames, so the two are joined here to get
something a human (or a dashboard) can read.

Authentication is the X-Auth-Token header. The token comes from the
environment; see config.py.
"""

import requests


class LibreNMSError(RuntimeError):
    """Any failure talking to LibreNMS, so callers catch one thing."""


class Peer(object):
    """One BGP session, flattened."""

    __slots__ = ("device", "peer_ip", "peer_id", "remote_as", "state", "description")

    def __init__(self, device, peer_ip, peer_id, remote_as, state, description):
        self.device = device
        self.peer_ip = peer_ip
        self.peer_id = peer_id
        self.remote_as = remote_as
        self.state = state
        self.description = description

    def as_dict(self):
        return {
            "device": self.device,
            "peer": self.peer_ip,
            "peer_id": self.peer_id,
            "remote_as": self.remote_as,
            "state": self.state,
            "description": self.description,
        }

    def __repr__(self):
        return "Peer(%s -> %s, %s)" % (self.device, self.peer_ip, self.state)


class LibreNMSClient(object):

    def __init__(self, base_url, token, timeout=5.0, verify_tls=True, session=None):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.session = session or requests.Session()

    def _get(self, path):
        url = "%s%s" % (self.base_url, path)
        try:
            response = self.session.get(
                url,
                headers={"X-Auth-Token": self.token, "Accept": "application/json"},
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        except requests.RequestException as exc:
            raise LibreNMSError("%s unreachable: %s" % (url, exc))

        if response.status_code != 200:
            raise LibreNMSError("%s returned HTTP %s" % (url, response.status_code))

        try:
            payload = response.json()
        except ValueError as exc:
            raise LibreNMSError("%s returned invalid JSON: %s" % (url, exc))

        if not isinstance(payload, dict):
            raise LibreNMSError("%s returned %s, expected an object"
                                % (url, type(payload).__name__))

        status = payload.get("status")
        if status is not None and status != "ok":
            raise LibreNMSError("%s reported status %r: %s"
                                % (url, status, payload.get("message", "")))
        return payload

    def devices(self):
        """device_id (as str) -> hostname."""
        payload = self._get("/api/v0/devices")
        devices = payload.get("devices")
        if not isinstance(devices, list):
            raise LibreNMSError("/api/v0/devices had no devices array")

        mapping = {}
        for entry in devices:
            if not isinstance(entry, dict):
                continue
            device_id = entry.get("device_id")
            if device_id is None:
                continue
            hostname = entry.get("hostname") or entry.get("sysName")
            if hostname:
                mapping[str(device_id)] = hostname
        return mapping

    def bgp_sessions(self):
        payload = self._get("/api/v0/bgp")
        sessions = payload.get("bgp_sessions")
        if not isinstance(sessions, list):
            raise LibreNMSError("/api/v0/bgp had no bgp_sessions array")
        return sessions

    def peers(self):
        """Every BGP session, joined to its device hostname."""
        hostnames = self.devices()
        peers = []
        for session in self.bgp_sessions():
            if not isinstance(session, dict):
                continue
            device_id = session.get("device_id")
            device = hostnames.get(str(device_id), str(device_id))
            state = session.get("bgpPeerState")
            peers.append(Peer(
                device=device,
                # bgpPeerRemoteAddr is the session address; bgpPeerIdentifier is
                # the peer's router id, which is not always the same thing.
                peer_ip=session.get("bgpPeerRemoteAddr") or session.get("bgpPeerIdentifier"),
                peer_id=session.get("bgpPeerIdentifier"),
                remote_as=session.get("bgpPeerRemoteAs"),
                state=(state or "unknown").lower(),
                description=session.get("bgpPeerDescr") or "",
            ))
        return peers


def state_for_link(peers, device, peer_ip):
    """
    The session state between a device and a peer address.

    Returns None when LibreNMS has no session for that pair, which the caller
    reports as "unknown" rather than inventing an "established".
    """
    if not device or not peer_ip:
        return None
    for peer in peers:
        if peer.device == device and peer.peer_ip == peer_ip:
            return peer.state
    return None
