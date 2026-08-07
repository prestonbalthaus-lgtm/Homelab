# NOC bridge

A small Flask service that merges two sources into one document the Layout
page's Java applet can read:

- **LibreNMS** supplies BGP peer state per session (`established`, `idle`, ...)
  from `/api/v0/bgp`, joined against `/api/v0/devices` to turn `device_id` into
  a hostname.
- **NetFlow**, read through `nfdump`, supplies bandwidth per direction in kbps.

It also serves the login endpoint, which is gated on the client certificate.

## Setup

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp env.example .env      # then fill it in
set -a; . ./.env; set +a
.venv/bin/python noc_bridge.py
```

`.env` is gitignored. Every setting is an environment variable — there is no
config file with a token in it, by design.

The only two that are mandatory are `LIBRENMS_URL` and `LIBRENMS_TOKEN`; the
service refuses to start without them rather than failing on the first request.
Everything else has a working default, listed in `env.example`.

Generate the token in LibreNMS under **Settings, API, API Tokens**. It is
passed as the `X-Auth-Token` header, which is what LibreNMS expects.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /status.json` | The merged document. The applet reads this. |
| `POST /login` | Returns 200 only when the client certificate verified, 403 otherwise. Never reads the username or password. |
| `GET /healthz` | Liveness. |

`/status.json` looks like this:

```json
{
  "status": "ok",
  "generated": "2026-08-07T22:40:00Z",
  "stale": false,
  "errors": [],
  "nodes": [{"id": "core", "name": "ASR 1006X", "role": "core router", "detail": "mgmt 10.0.0.1"}],
  "links": [{"from": "core", "to": "edge", "kbps": 812.4, "state": "established"}],
  "peers": [{"device": "asr1006x", "peer": "10.0.0.2", "remote_as": 65002, "state": "established"}]
}
```

`nodes` and `links` drive the map. `peers` is the flat "peer name to
operational state" list, for anything that wants the BGP view without caring
about the drawing.

## Behaviour when something is down

Neither source failing takes the endpoint down:

- LibreNMS unreachable: links keep their bandwidth, every state becomes
  `unknown`, and the failure is named in `errors` with `status: degraded`. It
  never guesses `established`, because a dead link drawn in blue is worse than
  no answer.
- NetFlow unreachable: states still come through, bandwidth reads 0.
- Both down: the last good document is served with `"stale": true`. If there
  has never been a good one, `/status.json` returns 503 with a structured error
  rather than a stack trace.

Only documents that actually carry data get cached, so a total outage does not
get pinned in the cache for its whole TTL.

## Matching flows to links

Bandwidth is matched by address pair: a flow from `NOC_NODE_IPS[a]` to
`NOC_NODE_IPS[b]` counts as traffic on link a to b. That suits router-to-router
traffic.

If you would rather chart interface utilisation, aggregate by exporter and
input/output interface instead: change the `-A` argument and the key built in
`_accumulate` in `netflow.py`. The rest of the module is unaffected.

## Tests

```sh
cd backend
.venv/bin/python -m unittest test_noc_bridge -v
```

33 tests, none of which touch the network or need `nfdump` installed —
LibreNMS is a fake session and `nfdump` is a canned string. They cover the auth
header, the device join, HTTP and JSON failures, byte-to-kbps conversion, CSV
parsing including nfdump's trailing summary lines, the degraded paths, and the
login gate.

## Running it for real

Behind nginx, on the same origin as the site — CheerpJ only allows the applet
same-origin requests. `deploy/nginx-mtls.conf` has the `location` blocks for
`/status.json` and `/login`.

A minimal unit:

```ini
[Unit]
Description=Screwhead Networks NOC bridge
After=network-online.target

[Service]
User=nocbridge
WorkingDirectory=/opt/screwhead/backend
EnvironmentFile=/etc/screwhead/noc-bridge.env
ExecStart=/opt/screwhead/backend/.venv/bin/python noc_bridge.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Keep `EnvironmentFile` outside the repo and `chmod 600` it: it holds the API
token.

For anything beyond a homelab, put a real WSGI server in front
(`gunicorn noc_bridge:create_app()`), since Flask's built-in server is not
meant to face traffic.
