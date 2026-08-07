# Deploying with mTLS

The admin panel is protected by requiring a client certificate signed by your
own root CA. This directory holds the server config and the steps to create the
certificates.

## What protects what

| Thing | What it does |
| --- | --- |
| `login.html` | Nothing. It is a prop. The fields are not checked against anything. |
| `nginx-mtls.conf` | Everything. The server refuses `/admin-panel/` to anyone without a valid client certificate. |

This split is deliberate. Any credential check written in JavaScript ships to
the visitor, who can read it and skip it. The only checks that count are the
ones on the server, which is why the login page was stripped of its credentials
rather than given better ones. The page also says nothing about *how* access is
granted: naming the mechanism only tells someone what to go after.

## What "dropping the login traffic" actually means

The goal is that someone without a certificate can hammer the login button
without ever reaching anything. That works, with one correction worth knowing.

A firewall cannot do it. Whether a client holds a valid certificate is decided
inside the TLS handshake, and `nftables` or `iptables` cannot see inside TLS —
by the time the certificate is known, the packets have already arrived. So
there is no rule that drops "login packets from people without certificates" at
the network layer.

What the config here does instead:

1. The TLS handshake happens with `ssl_verify_client optional`, so the public
   site loads for everyone and `$ssl_client_verify` is populated.
2. `location = /login` returns 403 immediately when that variable is not
   `SUCCESS`. **nginx never proxies the request**, so the backend never sees
   the username or password and runs no code on the attempt.
3. `limit_req` caps how often a single address can even reach that check.

Step 2 is what makes an attempt pointless; step 3 is what makes flooding
pointless. Rejecting a request is cheap but not free, which is why the rate
limit matters — without it, "the app never sees it" still leaves nginx doing a
TLS handshake per attempt.

The backend re-checks the same header (`X-SSL-Client-Verify`) before answering
`/login`, so it is still safe if it is ever exposed without nginx in front.

## 1. Create the root CA, once

Do this somewhere safe. The CA key is what lets you mint certificates for your
devices, so anyone who takes a copy can mint their own and walk into the admin
panel. Keep it off the web server.

```sh
openssl genrsa -out root-ca.key 4096
chmod 600 root-ca.key

openssl req -x509 -new -nodes -sha256 -days 3650 \
  -key root-ca.key \
  -subj "/CN=Screwhead Networks Root CA" \
  -out root-ca.crt
```

Only `root-ca.crt` goes on the web server. `root-ca.key` never does.

## 2. Issue a certificate per device

Repeat per laptop, phone, or workstation. Give each one its own key and its own
CN, so a single device can be revoked later without reissuing everything.

```sh
DEVICE=preston-laptop

openssl genrsa -out "$DEVICE.key" 2048
openssl req -new -key "$DEVICE.key" -subj "/CN=$DEVICE" -out "$DEVICE.csr"

printf "extendedKeyUsage = clientAuth\n" > client.ext

openssl x509 -req -sha256 -days 825 \
  -in "$DEVICE.csr" \
  -CA root-ca.crt -CAkey root-ca.key -CAcreateserial \
  -extfile client.ext \
  -out "$DEVICE.crt"
```

`extendedKeyUsage = clientAuth` matters: without it some clients will refuse to
offer the certificate at all.

## 3. Bundle it for the browser

Browsers import PKCS#12, not loose PEM files. You will be asked for an export
password; you need it again at import.

```sh
openssl pkcs12 -export \
  -inkey "$DEVICE.key" -in "$DEVICE.crt" -certfile root-ca.crt \
  -out "$DEVICE.p12"
```

Import `$DEVICE.p12` into the OS or browser certificate store. Firefox keeps
its own store: Settings, Privacy & Security, Certificates, View Certificates,
Your Certificates, Import.

## 4. Install the server config

Copy `root-ca.crt` and your server certificate to the box, put
`nginx-mtls.conf` in place, then check and reload:

```sh
sudo install -m 644 root-ca.crt /etc/ssl/screwhead/root-ca.crt
sudo cp nginx-mtls.conf /etc/nginx/sites-available/screwhead
sudo ln -sf /etc/nginx/sites-available/screwhead /etc/nginx/sites-enabled/screwhead
sudo nginx -t && sudo systemctl reload nginx
```

The server certificate itself is ordinary TLS. Use Let's Encrypt or whatever
you already have; the client CA is a separate thing from it.

## 5. Verify the gate, do not assume it

Test the failure case first. A gate you have only seen succeed is a gate you
have not tested.

```sh
# No certificate: must print 403
curl -sS -o /dev/null -w '%{http_code}\n' \
  https://www.screwheadnetworks.com/admin-panel/

# With a certificate: must print 200
curl -sS -o /dev/null -w '%{http_code}\n' \
  --cert preston-laptop.crt --key preston-laptop.key \
  https://www.screwheadnetworks.com/admin-panel/

# The public site must stay open with no certificate: must print 200
curl -sS -o /dev/null -w '%{http_code}\n' \
  https://www.screwheadnetworks.com/
```

If the first command prints 200, the panel is exposed. Stop and fix it before
going further.

## Revoking a device

Generate a CRL and point nginx at it with `ssl_crl`, or, for a homelab where
the device list is short, reissue the root CA and hand out fresh certificates.
The second is blunt but it is honest about what actually happened, and with a
handful of devices it takes minutes.

## Caddy instead of nginx

Caddy applies client authentication per site, not per path, so the tidy way is
to put the admin panel on its own hostname:

```
admin.screwheadnetworks.com {
    tls {
        client_auth {
            mode       require_and_verify
            trust_pool file /etc/ssl/screwhead/root-ca.crt
        }
    }
    root * /var/www/screwhead
    file_server
}
```

Check this against your Caddy version before relying on it: the `client_auth`
block was reworked in 2.8, and older builds spell the trust store
`trusted_ca_cert_file` instead of a `trust_pool` block. Verify with the curl
checks above either way.

## A note on the topology feed

If you enable the `/topology.json` proxy so the Layout page shows real traffic,
remember that **the Layout page is public**. Device names and bandwidth served
there are readable by anyone who visits the site, whether or not they have a
certificate.

Three ways to handle that, pick one deliberately:

- Publish only what you are happy being public: generic names, coarse numbers.
- Put the live map in the admin panel instead, and leave the public Layout page
  on synthetic data.
- Serve the feed only to certificate holders by moving it under a gated
  location, and accept that the public map shows nothing.
