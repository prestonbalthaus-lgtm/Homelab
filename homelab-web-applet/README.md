# Screwhead Networks

The Screwhead Networks public site, built as a late-90s UNIX CDE/Motif
workstation, with real Java applets running in modern browsers.

[CheerpJ 4.3](https://cheerpj.com/) supplies a Java 8 runtime compiled to
WebAssembly, picks up the plain `<applet>` tags, and executes `HelloApplet.jar`
client side. The applets set `com.sun.java.swing.plaf.motif.MotifLookAndFeel`,
so their widgets are drawn by Swing's genuine CDE/Motif look and feel rather
than imitated in CSS.

## Quick start

```sh
./build.sh     # compiles src/ -> HelloApplet.jar (fetches a JDK 8 if needed)
./serve.sh     # serves this folder at http://localhost:8080/
```

Then open <http://localhost:8080/>. First load pulls the CheerpJ runtime from
`cjrtnc.leaningtech.com`, so it takes a couple of seconds and needs network
access; the status strip under the applet reports progress and elapsed time.

`file://` will not work. CheerpJ needs real HTTP to fetch the jar and its
WebAssembly runtime, which is all `serve.sh` does.

## Pages

| Page | State |
| --- | --- |
| `index.html` | **Home.** What visitors land on. Runs `homelab.WelcomeApplet`, which gives a generic greeting. |
| `peering.html` | **Peering.** Flashing "Currently Under Construction" banner over a full-size empty work area. |
| `layout.html` | **Layout.** Runs `homelab.NetworkApplet`: a scrollable Graphics2D traffic map of three routers plus the upstream. |
| `server-info.html` | **Server Info.** Equipment inventory table. Placeholder kit list. |
| `login.html` | **Login.** Centred dtlogin-style dialog. Cosmetic, see below. |
| `admin-panel/index.html` | The homelab dashboard: hosts, service status, boot transcript, operator console applet. Not linked from the public nav. |

Every page carries the same chrome: site header with the Java mark and the
business name, dtwm title bar, the Home / Peering / Layout / Server Info / Login
menu bar, fake browser toolbar, status bar, and the footer.

## Applet canvas size

Every applet is 1280x1024, the classic 5:4 workstation resolution. Applets that
do not fill it leave the rest empty, which is what a fixed-size applet has
always done.

That size drives the page width. `--page-max` in `style.css` is 1320px, enough
for a 1280px applet plus the window chrome. The admin panel sets
`<body class="wide">`, which raises it to 1500px, because that page also carries
a 176px sidebar next to its applet. Change the applet size and both numbers need
to move with it.

## Protecting the admin panel

The login page is decoration. It holds no credentials, checks nothing in the
browser, and says nothing about how access is actually granted. Putting the
pages under `admin-panel/` likewise hides them from the navigation without
restricting anything: that URL is served to whoever asks for it.

The actual control is **`deploy/nginx-mtls.conf`**, which refuses
`/admin-panel/` and `/login` to any client that does not present a certificate
signed by the Screwhead Networks root CA, while leaving the public pages open
to everyone. Submitting the form without one gets a 403 from nginx before the
backend sees the request at all, and a rate limit caps how often anyone can
even reach that check. `deploy/README.md` covers creating the CA, issuing a
certificate per device, what "dropping the login traffic" can and cannot mean
at the network layer, and verifying that an uncertificated request really does
get a 403 rather than assuming it.

The admin page also carries `<meta name="robots" content="noindex, nofollow">`
so honest crawlers skip it. There is deliberately **no** `robots.txt` entry,
because a `Disallow: /admin-panel/` line publishes the exact path to anyone who
reads the file. Neither of those is a control; the certificate is.

Until that config is deployed the admin panel is open to anyone who types the
URL. It currently shows made-up sample data, so nothing real leaks today. That
stops being true the moment it is wired to live hosts.

## The topology feed

The traffic map runs on synthetic numbers by default and makes no network
requests. Point it at real data with two applet parameters:

```html
<param name="feed" value="/status.json">
<param name="feedInterval" value="5">
```

They are already in `layout.html`, commented out.

That endpoint is served by **`backend/noc_bridge.py`**, a small Flask service
that merges two sources: NetFlow (read through `nfdump`) for bandwidth, and
LibreNMS `/api/v0/bgp` for BGP session state. The applet's side of it is one
plain GET that never logs in and never sends credentials.
`deploy/topology.example.json` is a worked example of the document:

```json
{
  "nodes": [{"id": "core", "name": "ASR 1006X", "role": "core router", "detail": "mgmt 10.0.0.1"}],
  "links": [{"from": "core", "to": "edge", "kbps": 812.4, "state": "established"}]
}
```

Only names, bandwidth and session state come from the feed. Node ids are
`core`, `edge`, `lab` and `inet`; unrecognised ids are ignored rather than
added, so the drawn topology stays under `NetworkPanel`'s control. Malformed
entries are skipped rather than throwing away the whole document, and a link
with no `kbps` or no `state` keeps what it already had.

Link colour follows the session state, so a dead link cannot read as a healthy
one: blue for `established`, dark red for `idle` / `down`, olive for the
in-between states (`connect`, `active`, `opensent`, `openconfirm`), grey for
`unknown`. With no feed attached the state is null and links stay blue, so the
default synthetic view is not a wall of red.

Two constraints worth knowing before wiring it up:

- **Same origin only.** CheerpJ permits HTTP(S) from Java only when the scheme,
  host and port match the page, so the feed has to be a path on the same server.
  `deploy/nginx-mtls.conf` has the `location = /status.json` block that proxies
  it to the bridge.
- **The Layout page is public.** Anything served through that feed — device
  names, bandwidth, which sessions are down — is readable by every visitor,
  certificate or not. See the note at the end of `deploy/README.md` for the
  three sane ways to handle that.

When a feed is configured the synthetic random walk switches off immediately,
before the first reading lands, so the map never shows convincing fake numbers
while a real feed is failing. The status bar reports `off (synthetic)`,
`waiting`, `live`, `stale` or `error`.

## Files

| Path | What it is |
| --- | --- |
| `style.css` | All page chrome. Vanilla CSS, no framework, no utility classes. |
| `site.js` | Status bar clock, and the CheerpJ boot/status wiring on applet pages. |
| `src/homelab/MotifKit.java` | Shared Motif palette, fonts, bevels and widget factories. |
| `src/homelab/WelcomeApplet.java` | Home page greeting applet. |
| `src/homelab/NetworkApplet.java` | Layout page applet: title strip, status bar, animation and feed timers. |
| `src/homelab/NetworkPanel.java` | The Graphics2D traffic map itself. Scrollable and drag-pannable. |
| `src/homelab/TopologyFeed.java` | Reads the topology feed. One plain GET, no credentials. |
| `src/homelab/Json.java` | Small hand-rolled JSON reader; Java 8 has none built in. |
| `src/homelab/HelloApplet.java` | Admin panel operator console applet. |
| `backend/noc_bridge.py` | Flask service merging NetFlow and LibreNMS into `/status.json`, plus the certificate-gated `/login`. |
| `backend/librenms.py` | LibreNMS client: BGP sessions joined to device hostnames. |
| `backend/netflow.py` | Reads `nfdump` CSV and turns bytes into kbps per link. |
| `backend/config.py` | Environment-variable configuration; no secrets on disk. |
| `backend/test_noc_bridge.py` | 33 offline tests; no network, no `nfdump` needed. |
| `deploy/nginx-mtls.conf` | The real access control: client-certificate gate on `/admin-panel/` and `/login`. |
| `deploy/README.md` | Creating the root CA, issuing device certificates, verifying the gate. |
| `deploy/topology.example.json` | A worked example of the feed document. |
| `build.sh` | Compiles and jars. Prefers a JDK 8 already on the box. |
| `serve.sh` | `python3 -m http.server` on 127.0.0.1:8080. |
| `HelloApplet.jar` | Build output, committed so the pages work without a build. |
| `build/` | Intermediate `.class` files. Not committed. |

Two placeholders to swap when the site goes live: the fake browser toolbar shows
`http://www.screwheadnetworks.com/...`, and the header mark is a hand-drawn
steaming-cup SVG rather than Oracle's trademarked Java logo. If you want the
official one, Oracle's "Java Powered" brand assets have their own usage terms.

## Why the build pins JDK 8

CheerpJ runs a Java 8 runtime and **applets do not load under Java 11 or
newer**. Compiling with a modern JDK would also be a problem on its own, since
the applet API is deprecated for removal and the Motif look and feel is no
longer guaranteed to be present. `build.sh` therefore looks for a JDK 8 on
`PATH`, then `JAVA_HOME`, then `~/.cache/homelab-applet`, and downloads Temurin
8 there if it finds none. The committed jar is class file major version 52.

## Design rules

The site is deliberately a late-90s UNIX CDE / Motif workstation, not a modern
dashboard. Anything added later should hold the line:

- `border-radius: 0` everywhere, enforced in the global reset.
- No drop shadows, glows, glassmorphism, or gradients of any kind. Depth comes
  only from 2px bevels: `#ffffff` on the top and left, `#808080` on the bottom
  and right, inverted for sunken elements (`.out` / `.in` in `style.css`).
- `#c0c0c0` system gray throughout, with `#000080` Motif navy for selection and
  table headers, and maroon / teal / olive for status. No pastels, no indigo.
- Fonts are Helvetica for chrome and a Fixedsys-first fixed stack for data.
  No Inter, Roboto, or Open Sans. Font smoothing is switched off.
- Dense by default: 1-3px padding, 12px type, no floating cards, no centered
  hero text, everything left aligned and packed.
- Links in the footer are 1999 defaults: `#0000ee` underlined, `#551a8b` once
  visited, red while active.

The one flashing element, the construction banner, cycles at about 0.9Hz. That
is deliberately well under the 3Hz photosensitive-seizure threshold, and it
stops entirely for visitors with `prefers-reduced-motion: reduce`. Keep both
properties if you restyle it.

## Embedding an applet on a new page

Three things are required:

```html
<script src="https://cjrtnc.leaningtech.com/4.3/loader.js"></script>

<applet archive="HelloApplet.jar" code="homelab.WelcomeApplet"
        width="1280" height="1024"></applet>

<script src="site.js"></script>
```

`cheerpjInit()`, which `site.js` calls, must run after the `<applet>` tag exists
in the DOM. Note that CheerpJ **replaces** the `<applet>` element with its own
display during init, so read any attributes off the tag before init rather than
after. Also size the applet in CSS: `<applet>` is an unknown element to a modern
parser, so it defaults to `display: inline` with no intrinsic size and its
`width`/`height` attributes are ignored for layout.

## Extending the applets

Each applet's `init()` applies the look and feel through `MotifKit`, then builds
its UI on the EDT. Layout is `BorderLayout` at the root with a `GridBagLayout`
for the body. Keep new widgets on those two layout managers, and keep building
them through `MotifKit` so the palette, fonts and bevels stay consistent.

### The traffic map

`NetworkPanel` holds four nodes at fractional coordinates, so the mesh rescales
with the canvas, and eight lanes: one per direction per link. The canvas is
1700x1300, larger than the applet's viewport, so there is somewhere to pan to;
`NetworkApplet` centres the view on the mesh once the viewport has a real size.
Drag anywhere on the map to pan it, or use the scrollbars. `Scrollable` is
implemented so the panel fills the viewport instead of scrolling whenever the
viewport is the larger of the two.

Some notes if you extend it:

- A lane's perpendicular offset is derived from its own direction, so the two
  halves of a link land on opposite sides of the centre line automatically. The
  bandwidth labels sit at 42% along each lane for the same reason: the pair ends
  up at 42% and 58%, and never overlaps.
- Arrow speed is proportional to that lane's bandwidth, so a busy lane visibly
  runs faster.
- One timer drives everything at ~16fps, resampling bandwidth every 17th frame.
  Antialiasing is off, which suits the period look and keeps a full 1280x1024
  repaint cheap enough for CheerpJ. Measured at 17-18fps in Firefox.
- `start()` and `stop()` drive both timers, so neither the animation nor the
  feed polling runs when the applet is not showing.
- Feed fetches happen on a daemon thread, never the EDT: a blocking read there
  would freeze the animation. Results are applied back on the EDT, and a poll is
  skipped if the previous one is still in flight.

Without a feed the bandwidth figures are a random walk in the kilobit range.
They are placeholder telemetry and are not measuring anything.

After editing the Java, re-run `./build.sh` and hard refresh the browser.
