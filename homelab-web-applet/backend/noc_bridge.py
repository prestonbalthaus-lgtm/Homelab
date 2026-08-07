#!/usr/bin/env python3
"""
NOC bridge: LibreNMS + NetFlow -> /status.json for the Screwhead Networks
traffic map.

Two sources, one document:

  * LibreNMS supplies BGP peer state per session (established, idle, ...).
  * NetFlow, read through nfdump, supplies bandwidth per direction in kbps.

The Java applet on the Layout page reads /status.json directly and draws each
link with its bandwidth and session state. The same document also carries a
flat `peers` array, so anything else that just wants "peer name -> state" can
read that instead without understanding the map.

Run it behind nginx on the same origin as the site: CheerpJ only allows the
applet to make same-origin requests. See deploy/nginx-mtls.conf.

Configuration is entirely environment variables; see README.md in this folder.
"""

import time

from flask import Flask, jsonify, request

import config as config_module
import librenms as librenms_module
import netflow as netflow_module


UNKNOWN_STATE = "unknown"


class Cache(object):
    """Last known good document, so a LibreNMS blip does not blank the map."""

    def __init__(self, ttl_seconds):
        self.ttl_seconds = ttl_seconds
        self.document = None
        self.stored_at = 0.0

    def store(self, document):
        self.document = document
        self.stored_at = time.time()

    def fresh(self):
        if self.document is None:
            return None
        if time.time() - self.stored_at > self.ttl_seconds:
            return None
        return self.document

    def stale(self):
        return self.document


def _node_entry(cfg, node_id):
    entry = {"id": node_id}
    name = cfg.node_names.get(node_id) or cfg.device_map.get(node_id)
    if name:
        entry["name"] = name
    role = cfg.node_roles.get(node_id)
    if role:
        entry["role"] = role
    address = cfg.node_ips.get(node_id)
    if address:
        entry["detail"] = "mgmt %s" % address
    return entry


def _link_state(cfg, peers, left, right):
    """
    Session state for a link, looked at from both ends.

    LibreNMS only knows about a session from the device that reports it, so if
    one end is not monitored the other end still answers.
    """
    state = librenms_module.state_for_link(
        peers, cfg.device_map.get(left), cfg.node_ips.get(right))
    if state is None:
        state = librenms_module.state_for_link(
            peers, cfg.device_map.get(right), cfg.node_ips.get(left))
    return state or UNKNOWN_STATE


def build_document(cfg, peers, rates, errors):
    """Merge both sources into the document the applet consumes."""
    nodes = [_node_entry(cfg, node_id) for node_id in cfg.node_ids()]

    links = []
    for left, right in cfg.links:
        state = _link_state(cfg, peers, left, right)
        for source, destination in ((left, right), (right, left)):
            links.append({
                "from": source,
                "to": destination,
                "kbps": round(netflow_module.rate_between(
                    rates,
                    cfg.node_ips.get(source),
                    cfg.node_ips.get(destination)), 1),
                "state": state,
            })

    return {
        "status": "degraded" if errors else "ok",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stale": False,
        "errors": errors,
        "nodes": nodes,
        "links": links,
        "peers": [peer.as_dict() for peer in peers],
    }


def collect(cfg, client=None, rate_reader=None):
    """
    Gather both sources. Neither failing is fatal: whatever did answer is
    still returned, with the failure named in `errors`.
    """
    errors = []

    peers = []
    try:
        cfg.require_librenms()
        client = client or librenms_module.LibreNMSClient(
            cfg.librenms_url, cfg.librenms_token,
            timeout=cfg.librenms_timeout, verify_tls=cfg.librenms_verify_tls)
        peers = client.peers()
    except (librenms_module.LibreNMSError, config_module.ConfigError) as exc:
        errors.append("librenms: %s" % exc)

    rates = {}
    if cfg.netflow_enabled:
        try:
            reader = rate_reader or netflow_module.read_pair_rates
            rates = reader(cfg.nfdump_bin, cfg.flow_dir, cfg.flow_window)
        except netflow_module.NetflowError as exc:
            errors.append("netflow: %s" % exc)
    else:
        errors.append("netflow: no NETFLOW_DIR configured")

    return build_document(cfg, peers, rates, errors)


def create_app(cfg=None, client=None, rate_reader=None):
    cfg = cfg or config_module.Config()
    app = Flask(__name__)
    cache = Cache(cfg.cache_seconds)

    @app.route("/status.json")
    def status_json():
        cached = cache.fresh()
        if cached is not None:
            return jsonify(cached)

        try:
            document = collect(cfg, client=client, rate_reader=rate_reader)
        except Exception as exc:  # noqa: BLE001 - the endpoint must not 500
            previous = cache.stale()
            if previous is None:
                return jsonify({
                    "status": "error",
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "nodes": [],
                    "links": [],
                    "peers": [],
                }), 503
            fallback = dict(previous)
            fallback["stale"] = True
            fallback["status"] = "degraded"
            return jsonify(fallback)

        # Only cache a document that actually carries data, so a total outage
        # does not get pinned in the cache for its whole TTL.
        if document["peers"] or any(link["kbps"] for link in document["links"]):
            cache.store(document)
        return jsonify(document)

    @app.route("/login", methods=["POST"])
    def login():
        """
        The login endpoint the site posts to.

        It never reads a username or password: there is nothing to check them
        against, and pretending otherwise would just be theatre. What decides
        the answer is the client certificate, which nginx has already verified
        before proxying anything here. This check is the second layer, in case
        the app is ever reachable without nginx in front of it.
        """
        verified = request.headers.get(cfg.client_verify_header, "")
        if verified != "SUCCESS":
            return jsonify({"status": "denied"}), 403
        return jsonify({"status": "ok"}), 200

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    return app


def main():
    cfg = config_module.Config()
    try:
        cfg.require_librenms()
    except config_module.ConfigError as exc:
        raise SystemExit("configuration error: %s" % exc)

    app = create_app(cfg)
    app.run(host=cfg.bind, port=cfg.port)


if __name__ == "__main__":
    main()
