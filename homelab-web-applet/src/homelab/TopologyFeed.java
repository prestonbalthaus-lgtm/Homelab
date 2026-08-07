package homelab;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URL;
import java.net.URLConnection;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Reads the network topology feed.
 *
 * This performs one plain GET against a URL that already exposes the data and
 * reads what comes back. It never logs in and never sends credentials. The
 * bridge in backend/ produces that URL, merging NetFlow bandwidth with BGP
 * session state from LibreNMS; a static file written by a cron job works just
 * as well.
 *
 * CheerpJ only permits same-origin HTTP(S) from inside the browser, so the
 * feed URL has to be served from the same scheme, host and port as the page.
 * In practice that means a relative path such as "/topology.json", reverse
 * proxied to wherever the data really lives.
 *
 * Expected shape, all fields optional except the ids:
 *
 * <pre>
 * {
 *   "nodes": [
 *     {"id": "core", "name": "ASR 1006X", "role": "core router", "detail": "mgmt 10.0.0.1"}
 *   ],
 *   "links": [
 *     {"from": "core", "to": "edge", "kbps": 812.4, "state": "established"}
 *   ]
 * }
 * </pre>
 *
 * Only names, bandwidth and session state are taken from the feed. The set of
 * nodes and the shape of the mesh stay as they are drawn in
 * {@link NetworkPanel}; ids in the feed that the map does not know about are
 * ignored rather than added.
 */
final class TopologyFeed {

    private static final int TIMEOUT_MS = 5000;
    private static final int MAX_BYTES = 512 * 1024;

    /** A rename for one node on the map. */
    static final class NodeUpdate {
        final String id;
        final String name;
        final String role;
        final String detail;

        NodeUpdate(String id, String name, String role, String detail) {
            this.id = id;
            this.name = name;
            this.role = role;
            this.detail = detail;
        }
    }

    /**
     * A reading for one direction of one link: bandwidth from NetFlow, session
     * state from LibreNMS. Either may be absent, in which case the map keeps
     * what it already had rather than inventing a value.
     */
    static final class LinkUpdate {
        final String from;
        final String to;
        final Double kbps;
        final String state;

        LinkUpdate(String from, String to, Double kbps, String state) {
            this.from = from;
            this.to = to;
            this.kbps = kbps;
            this.state = state;
        }
    }

    static final class Snapshot {
        final List<NodeUpdate> nodes;
        final List<LinkUpdate> links;

        Snapshot(List<NodeUpdate> nodes, List<LinkUpdate> links) {
            this.nodes = nodes;
            this.links = links;
        }
    }

    private final URL url;

    TopologyFeed(String spec) throws IOException {
        this.url = new URL(spec);
    }

    /** One GET, no credentials, no caching. Callers must be off the EDT. */
    Snapshot fetch() throws IOException {
        URLConnection connection = url.openConnection();
        connection.setConnectTimeout(TIMEOUT_MS);
        connection.setReadTimeout(TIMEOUT_MS);
        connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "application/json");

        InputStream in = connection.getInputStream();
        try {
            return parse(readAll(in));
        } finally {
            in.close();
        }
    }

    private static String readAll(InputStream in) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        int read;
        while ((read = in.read(chunk)) != -1) {
            buffer.write(chunk, 0, read);
            if (buffer.size() > MAX_BYTES) {
                throw new IOException("feed larger than " + MAX_BYTES + " bytes");
            }
        }
        return new String(buffer.toByteArray(), "UTF-8");
    }

    /** Split out from {@link #fetch()} so it can be exercised without a server. */
    static Snapshot parse(String text) {
        Object root = Json.parse(text);
        if (!(root instanceof Map)) {
            throw new IllegalArgumentException("feed root must be a JSON object");
        }
        Map<?, ?> map = (Map<?, ?>) root;

        List<NodeUpdate> nodes = new ArrayList<NodeUpdate>();
        Object rawNodes = map.get("nodes");
        if (rawNodes instanceof List) {
            for (Object entry : (List<?>) rawNodes) {
                if (!(entry instanceof Map)) {
                    continue;
                }
                Map<?, ?> node = (Map<?, ?>) entry;
                String id = string(node.get("id"));
                if (id == null) {
                    continue;
                }
                nodes.add(new NodeUpdate(id, string(node.get("name")),
                        string(node.get("role")), string(node.get("detail"))));
            }
        }

        List<LinkUpdate> links = new ArrayList<LinkUpdate>();
        Object rawLinks = map.get("links");
        if (rawLinks instanceof List) {
            for (Object entry : (List<?>) rawLinks) {
                if (!(entry instanceof Map)) {
                    continue;
                }
                Map<?, ?> link = (Map<?, ?>) entry;
                String from = string(link.get("from"));
                String to = string(link.get("to"));
                if (from == null || to == null) {
                    continue;
                }
                links.add(new LinkUpdate(from, to, number(link.get("kbps")),
                        string(link.get("state"))));
            }
        }

        return new Snapshot(nodes, links);
    }

    private static String string(Object value) {
        if (value instanceof String) {
            String text = ((String) value).trim();
            return text.length() == 0 ? null : text;
        }
        return null;
    }

    private static Double number(Object value) {
        if (value instanceof Double) {
            Double d = (Double) value;
            return d.isNaN() || d.isInfinite() ? null : d;
        }
        if (value instanceof String) {
            try {
                return Double.valueOf(((String) value).trim());
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return null;
    }
}
