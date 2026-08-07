#!/usr/bin/env python3
"""
Offline tests for the NOC bridge.

Nothing here touches the network or needs nfdump installed: LibreNMS is a fake
requests session, and nfdump is a canned string. Run with:

    python3 -m unittest discover -s backend -p 'test_*.py'
"""

import json
import unittest

import config as config_module
import librenms as librenms_module
import netflow as netflow_module
import noc_bridge


ENV = {
    "LIBRENMS_URL": "https://librenms.example/",
    "LIBRENMS_TOKEN": "not-a-real-token",
    "NOC_DEVICE_MAP": "core=asr1006x,edge=asr1002x,lab=vxr",
    "NOC_NODE_IPS": "core=10.0.0.1,edge=10.0.0.2,lab=10.0.0.3,inet=198.51.100.1",
    "NOC_NODE_NAMES": "core=ASR 1006X,edge=ASR 1002X,lab=7206 VXR,inet=Internet",
    "NOC_NODE_ROLES": "core=core router,edge=edge router,lab=lab router,inet=upstream",
    "NETFLOW_DIR": "/var/cache/nfdump",
    "NETFLOW_WINDOW_SECONDS": "300",
}


class FakeResponse(object):
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


class FakeSession(object):
    """Stands in for requests.Session, recording what was asked for."""

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def get(self, url, headers=None, timeout=None, verify=None):
        self.requests.append((url, headers or {}))
        for path, response in self.responses.items():
            if url.endswith(path):
                return response
        raise AssertionError("unexpected URL %s" % url)


DEVICES = {
    "status": "ok",
    "count": 3,
    "devices": [
        {"device_id": "1", "hostname": "asr1006x"},
        {"device_id": "2", "hostname": "asr1002x"},
        {"device_id": "3", "hostname": "vxr"},
    ],
}

BGP = {
    "status": "ok",
    "count": 4,
    "bgp_sessions": [
        {"device_id": "1", "bgpPeerRemoteAddr": "10.0.0.2", "bgpPeerIdentifier": "10.0.0.2",
         "bgpPeerRemoteAs": 65002, "bgpPeerState": "established", "bgpPeerDescr": "to edge"},
        {"device_id": "2", "bgpPeerRemoteAddr": "10.0.0.3", "bgpPeerIdentifier": "10.0.0.3",
         "bgpPeerRemoteAs": 65003, "bgpPeerState": "idle", "bgpPeerDescr": "to lab"},
        {"device_id": "3", "bgpPeerRemoteAddr": "10.0.0.1", "bgpPeerIdentifier": "10.0.0.1",
         "bgpPeerRemoteAs": 65001, "bgpPeerState": "ESTABLISHED", "bgpPeerDescr": "to core"},
        {"device_id": "2", "bgpPeerRemoteAddr": "198.51.100.1", "bgpPeerIdentifier": "198.51.100.1",
         "bgpPeerRemoteAs": 64500, "bgpPeerState": "established", "bgpPeerDescr": "transit"},
    ],
}

NFDUMP_CSV = """ts,te,td,sa,da,sp,dp,pr,flg,fwd,stos,ipkt,ibyt,opkt,obyt
2026-08-07 22:00:00,2026-08-07 22:05:00,300.0,10.0.0.1,10.0.0.2,0,0,TCP,........,0,0,100,37500,0,0
2026-08-07 22:00:00,2026-08-07 22:05:00,300.0,10.0.0.2,10.0.0.1,0,0,TCP,........,0,0,100,7500,0,0
2026-08-07 22:00:00,2026-08-07 22:05:00,300.0,10.0.0.2,198.51.100.1,0,0,TCP,........,0,0,50,1000,0,0
2026-08-07 22:00:00,2026-08-07 22:05:00,300.0,10.0.0.2,198.51.100.1,0,0,UDP,........,0,0,50,1000,0,0

Summary: total flows: 4, total bytes: 47000, total packets: 300
"""


def make_config(**overrides):
    env = dict(ENV)
    env.update(overrides)
    return config_module.Config(env)


class ConfigTests(unittest.TestCase):

    def test_parses_maps_and_links(self):
        cfg = make_config()
        self.assertEqual(cfg.device_map["core"], "asr1006x")
        self.assertEqual(cfg.node_ips["inet"], "198.51.100.1")
        self.assertIn(("edge", "inet"), cfg.links)

    def test_rejects_malformed_pairs(self):
        with self.assertRaises(config_module.ConfigError):
            make_config(NOC_DEVICE_MAP="core")
        with self.assertRaises(config_module.ConfigError):
            make_config(NOC_NODE_IPS="=10.0.0.1")
        with self.assertRaises(config_module.ConfigError):
            make_config(NOC_LINKS="coreedge")

    def test_requires_url_and_token(self):
        with self.assertRaises(config_module.ConfigError):
            make_config(LIBRENMS_TOKEN="").require_librenms()
        with self.assertRaises(config_module.ConfigError):
            make_config(LIBRENMS_URL="").require_librenms()

    def test_no_token_is_ever_defaulted(self):
        cfg = config_module.Config({})
        self.assertEqual(cfg.librenms_token, "")
        self.assertEqual(cfg.librenms_url, "")


class LibreNMSTests(unittest.TestCase):

    def client(self, responses=None):
        responses = responses or {
            "/api/v0/devices": FakeResponse(DEVICES),
            "/api/v0/bgp": FakeResponse(BGP),
        }
        session = FakeSession(responses)
        client = librenms_module.LibreNMSClient(
            "https://librenms.example", "not-a-real-token", session=session)
        return client, session

    def test_sends_auth_token_header(self):
        client, session = self.client()
        client.peers()
        for _, headers in session.requests:
            self.assertEqual(headers.get("X-Auth-Token"), "not-a-real-token")

    def test_joins_sessions_to_hostnames(self):
        client, _ = self.client()
        peers = client.peers()
        self.assertEqual(len(peers), 4)
        self.assertEqual(peers[0].device, "asr1006x")
        self.assertEqual(peers[0].peer_ip, "10.0.0.2")

    def test_state_is_lowercased(self):
        client, _ = self.client()
        peers = client.peers()
        self.assertEqual(peers[2].state, "established")

    def test_unknown_device_id_falls_back_to_the_id(self):
        bgp = json.loads(json.dumps(BGP))
        bgp["bgp_sessions"][0]["device_id"] = "99"
        client, _ = self.client({
            "/api/v0/devices": FakeResponse(DEVICES),
            "/api/v0/bgp": FakeResponse(bgp),
        })
        self.assertEqual(client.peers()[0].device, "99")

    def test_http_error_raises(self):
        client, _ = self.client({
            "/api/v0/devices": FakeResponse(DEVICES),
            "/api/v0/bgp": FakeResponse({}, status_code=401),
        })
        with self.assertRaises(librenms_module.LibreNMSError):
            client.peers()

    def test_bad_json_raises(self):
        client, _ = self.client({
            "/api/v0/devices": FakeResponse("<html>nope</html>"),
            "/api/v0/bgp": FakeResponse(BGP),
        })
        with self.assertRaises(librenms_module.LibreNMSError):
            client.peers()

    def test_error_status_raises(self):
        client, _ = self.client({
            "/api/v0/devices": FakeResponse(DEVICES),
            "/api/v0/bgp": FakeResponse({"status": "error", "message": "nope"}),
        })
        with self.assertRaises(librenms_module.LibreNMSError):
            client.peers()

    def test_missing_array_raises(self):
        client, _ = self.client({
            "/api/v0/devices": FakeResponse(DEVICES),
            "/api/v0/bgp": FakeResponse({"status": "ok"}),
        })
        with self.assertRaises(librenms_module.LibreNMSError):
            client.peers()


class NetflowTests(unittest.TestCase):

    def rates(self, csv=NFDUMP_CSV):
        return netflow_module.read_pair_rates(
            "nfdump", "/var/cache/nfdump", 300, runner=lambda cmd: csv)

    def test_bytes_become_kbps(self):
        rates = self.rates()
        # 37500 bytes * 8 / 300s / 1000 = 1.0 kbps
        self.assertAlmostEqual(rates[("10.0.0.1", "10.0.0.2")], 1.0, places=6)
        self.assertAlmostEqual(rates[("10.0.0.2", "10.0.0.1")], 0.2, places=6)

    def test_repeated_pairs_are_summed(self):
        rates = self.rates()
        # two 1000-byte rows: 2000 * 8 / 300 / 1000
        self.assertAlmostEqual(
            rates[("10.0.0.2", "198.51.100.1")], 2000 * 8.0 / 300 / 1000, places=6)

    def test_summary_lines_are_skipped(self):
        self.assertEqual(len(self.rates()), 3)

    def test_missing_header_raises(self):
        with self.assertRaises(netflow_module.NetflowError):
            self.rates("no header here\n1,2,3\n")

    def test_missing_dir_raises(self):
        with self.assertRaises(netflow_module.NetflowError):
            netflow_module.read_pair_rates("nfdump", "", 300, runner=lambda cmd: "")

    def test_command_shape(self):
        seen = {}

        def runner(command):
            seen["command"] = command
            return NFDUMP_CSV

        netflow_module.read_pair_rates("nfdump", "/flows", 300, runner=runner)
        command = seen["command"]
        self.assertEqual(command[0], "nfdump")
        self.assertIn("-R", command)
        self.assertIn("/flows", command)
        self.assertIn("csv", command)
        self.assertIn("srcip,dstip", command)

    def test_rate_between_defaults_to_zero(self):
        self.assertEqual(netflow_module.rate_between({}, "10.0.0.1", "10.0.0.2"), 0.0)
        self.assertEqual(netflow_module.rate_between({}, None, "10.0.0.2"), 0.0)


class DocumentTests(unittest.TestCase):

    def peers(self):
        session = FakeSession({
            "/api/v0/devices": FakeResponse(DEVICES),
            "/api/v0/bgp": FakeResponse(BGP),
        })
        return librenms_module.LibreNMSClient("https://x", "t", session=session).peers()

    def document(self):
        cfg = make_config()
        rates = netflow_module.read_pair_rates(
            "nfdump", "/var/cache/nfdump", 300, runner=lambda cmd: NFDUMP_CSV)
        return cfg, noc_bridge.build_document(cfg, self.peers(), rates, [])

    def test_link_carries_state_and_bandwidth(self):
        _, document = self.document()
        link = [l for l in document["links"] if l["from"] == "core" and l["to"] == "edge"][0]
        self.assertEqual(link["state"], "established")
        self.assertAlmostEqual(link["kbps"], 1.0, places=3)

    def test_idle_session_is_reported_as_idle(self):
        _, document = self.document()
        link = [l for l in document["links"] if l["from"] == "edge" and l["to"] == "lab"][0]
        self.assertEqual(link["state"], "idle")

    def test_state_is_found_from_either_end(self):
        # Only the lab router reports the lab<->core session, as "core".
        _, document = self.document()
        link = [l for l in document["links"] if l["from"] == "lab" and l["to"] == "core"][0]
        self.assertEqual(link["state"], "established")

    def test_both_directions_present(self):
        cfg, document = self.document()
        self.assertEqual(len(document["links"]), len(cfg.links) * 2)

    def test_unmonitored_link_is_unknown_not_established(self):
        # lab<->inet is drawn on the map but neither end reports a session for
        # it, so it must come back "unknown". Guessing "established" here would
        # paint a dead link green.
        cfg = make_config(NOC_LINKS="lab-inet")
        document = noc_bridge.build_document(cfg, self.peers(), {}, [])
        for link in document["links"]:
            self.assertEqual(link["state"], "unknown")

    def test_peers_array_maps_name_to_state(self):
        _, document = self.document()
        states = {(p["device"], p["peer"]): p["state"] for p in document["peers"]}
        self.assertEqual(states[("asr1002x", "10.0.0.3")], "idle")

    def test_errors_mark_the_document_degraded(self):
        cfg = make_config()
        document = noc_bridge.build_document(cfg, [], {}, ["librenms: boom"])
        self.assertEqual(document["status"], "degraded")
        self.assertEqual(document["errors"], ["librenms: boom"])


class EndpointTests(unittest.TestCase):

    def app(self, **overrides):
        cfg = make_config(**overrides)
        session = FakeSession({
            "/api/v0/devices": FakeResponse(DEVICES),
            "/api/v0/bgp": FakeResponse(BGP),
        })
        client = librenms_module.LibreNMSClient(
            cfg.librenms_url, cfg.librenms_token, session=session)
        app = noc_bridge.create_app(
            cfg, client=client, rate_reader=lambda *a, **k: netflow_module.read_pair_rates(
                "nfdump", "/flows", 300, runner=lambda cmd: NFDUMP_CSV))
        return app.test_client()

    def test_status_json_serves_a_document(self):
        response = self.app().get("/status.json")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["links"])
        self.assertTrue(payload["nodes"])

    def test_status_json_survives_librenms_being_down(self):
        cfg = make_config()
        session = FakeSession({
            "/api/v0/devices": FakeResponse({}, status_code=500),
            "/api/v0/bgp": FakeResponse(BGP),
        })
        client = librenms_module.LibreNMSClient(cfg.librenms_url, cfg.librenms_token,
                                                session=session)
        app = noc_bridge.create_app(cfg, client=client,
                                    rate_reader=lambda *a, **k: {}).test_client()
        response = app.get("/status.json")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "degraded")
        self.assertTrue(any("librenms" in e for e in payload["errors"]))
        # every link still present, just without a known state
        self.assertTrue(all(l["state"] == "unknown" for l in payload["links"]))

    def test_login_denied_without_a_verified_certificate(self):
        client = self.app()
        response = client.post("/login", json={"username": "x", "password": "y"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["status"], "denied")

    def test_login_denied_when_verification_failed(self):
        client = self.app()
        response = client.post("/login", headers={"X-SSL-Client-Verify": "FAILED"})
        self.assertEqual(response.status_code, 403)

    def test_login_allowed_with_a_verified_certificate(self):
        client = self.app()
        response = client.post("/login", headers={"X-SSL-Client-Verify": "SUCCESS"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_login_ignores_credentials_entirely(self):
        client = self.app()
        allowed = client.post("/login", headers={"X-SSL-Client-Verify": "SUCCESS"},
                              json={"username": "nonsense", "password": "nonsense"})
        self.assertEqual(allowed.status_code, 200)
        denied = client.post("/login", json={"username": "admin", "password": "admin"})
        self.assertEqual(denied.status_code, 403)

    def test_healthz(self):
        self.assertEqual(self.app().get("/healthz").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
