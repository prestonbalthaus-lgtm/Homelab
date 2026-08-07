package homelab;

import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Cursor;
import java.awt.Dimension;
import java.awt.FontMetrics;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.Point;
import java.awt.Polygon;
import java.awt.Rectangle;
import java.awt.RenderingHints;
import java.awt.Stroke;
import java.awt.event.MouseEvent;
import java.awt.geom.Point2D;
import java.util.Locale;
import java.util.Random;

import javax.swing.JPanel;
import javax.swing.JViewport;
import javax.swing.Scrollable;
import javax.swing.SwingConstants;
import javax.swing.event.MouseInputAdapter;

/**
 * The traffic map on the Layout page: routers drawn with Graphics2D, with
 * white arrows crawling along blue links to show which way traffic is flowing
 * and how much of it.
 *
 * Every link carries two lanes, one per direction. A lane's arrows travel at a
 * speed proportional to its current bandwidth, so a busy lane visibly runs
 * faster than a quiet one.
 *
 * The canvas is deliberately larger than the applet's viewport, so the map can
 * be scrolled with the scrollbars or dragged with the mouse. That leaves room
 * to spread the topology out as more devices are added.
 *
 * Bandwidth is a random walk in the kilobit range until a feed is attached,
 * at which point {@link #applyFeed} takes over and the random walk stops.
 */
final class NetworkPanel extends JPanel implements Scrollable {

    private static final long serialVersionUID = 1L;

    /* Antialiasing stays off on purpose: chunky diagonals are the period look,
       and they are cheaper to push through CheerpJ's software pipeline. */
    private static final Color LINK_BLUE = new Color(0x00, 0x33, 0xCC);
    /* A link is only blue when its BGP session is up. Down and in-between get
       their own colours so a dead link cannot read as a healthy one. */
    private static final Color LINK_DOWN = new Color(0x99, 0x00, 0x00);
    private static final Color LINK_BUSY = new Color(0x80, 0x60, 0x00);
    private static final Color LINK_UNKNOWN = new Color(0x50, 0x50, 0x50);

    /* The scrollable world. Bigger than the viewport so there is somewhere to
       pan to; the mesh below sits in the middle of it. */
    private static final int CANVAS_W = 1700;
    private static final int CANVAS_H = 1300;

    private static final int BOX_W = 220;
    private static final int BOX_H = 78;
    private static final int TITLE_H = 18;

    private static final double LANE_OFFSET = 6.0;   /* lane separation from centre line */
    private static final double LABEL_OFFSET = 20.0; /* bandwidth label, further out     */
    private static final double EDGE_GAP = 8.0;      /* stop links short of the box      */
    private static final double ARROW_SPACING = 58.0;
    private static final double MAX_ARROW_SPEED = 12.0;

    private static final double KBPS_MIN = 64.0;
    private static final double KBPS_MAX = 999.0;

    /** A device on the map. Position is fractional so the mesh scales. */
    private static final class Node {
        final String id;
        final double fx;
        final double fy;
        String name;
        String role;
        String detail;
        final Rectangle bounds = new Rectangle(0, 0, BOX_W, BOX_H);

        Node(String id, String name, String role, String detail, double fx, double fy) {
            this.id = id;
            this.name = name;
            this.role = role;
            this.detail = detail;
            this.fx = fx;
            this.fy = fy;
        }
    }

    /** One direction of one link. */
    private static final class Lane {
        final Node from;
        final Node to;
        double kbps;
        double phase;
        /** BGP session state from LibreNMS; null until a feed says otherwise. */
        String state;

        Lane(Node from, Node to, double kbps) {
            this.from = from;
            this.to = to;
            this.kbps = kbps;
        }
    }

    private final Node[] nodes;
    private final Lane[] lanes;
    private final Random random = new Random();

    private boolean synthetic = true;
    private int frames = 0;

    NetworkPanel() {
        Node core = new Node("core", "ASR 1006X", "core router", "mgmt 10.0.0.1", 0.50, 0.20);
        Node edge = new Node("edge", "ASR 1002X", "edge router", "mgmt 10.0.0.2", 0.28, 0.72);
        Node lab  = new Node("lab",  "7206 VXR",  "lab router",  "mgmt 10.0.0.3", 0.74, 0.72);
        Node inet = new Node("inet", "Internet",  "upstream",    "transit",       0.16, 0.36);

        nodes = new Node[] { core, edge, lab, inet };

        /* Full mesh across the three routers, plus the upstream hanging off the
           edge router. Two lanes per link, one each way. */
        lanes = new Lane[] {
            new Lane(core, edge, seedKbps()),
            new Lane(edge, core, seedKbps()),
            new Lane(edge, lab,  seedKbps()),
            new Lane(lab,  edge, seedKbps()),
            new Lane(lab,  core, seedKbps()),
            new Lane(core, lab,  seedKbps()),
            new Lane(edge, inet, seedKbps()),
            new Lane(inet, edge, seedKbps())
        };

        setBackground(MotifKit.BG);
        setBorder(MotifKit.lowered());
        setPreferredSize(new Dimension(CANVAS_W, CANVAS_H));
        setAutoscrolls(true);
        installPanning();
    }

    private double seedKbps() {
        return KBPS_MIN + random.nextDouble() * (KBPS_MAX - KBPS_MIN);
    }

    /** Click and drag anywhere on the map to pan it, like a hand tool. */
    private void installPanning() {
        MouseInputAdapter panner = new MouseInputAdapter() {
            private final Point origin = new Point();

            @Override
            public void mousePressed(MouseEvent e) {
                origin.setLocation(e.getPoint());
                setCursor(Cursor.getPredefinedCursor(Cursor.MOVE_CURSOR));
            }

            @Override
            public void mouseReleased(MouseEvent e) {
                setCursor(Cursor.getDefaultCursor());
            }

            @Override
            public void mouseDragged(MouseEvent e) {
                if (!(getParent() instanceof JViewport)) {
                    return;
                }
                /* The panel moves under the pointer, so the origin stays fixed
                   in panel coordinates and the delta keeps working. */
                Rectangle view = ((JViewport) getParent()).getViewRect();
                view.x += origin.x - e.getX();
                view.y += origin.y - e.getY();
                scrollRectToVisible(view);
            }
        };
        addMouseListener(panner);
        addMouseMotionListener(panner);
    }

    /* ---------------------------------------------------------------- feed */

    /**
     * Apply a feed reading. Only names and bandwidth move; ids the map does
     * not know about are ignored rather than added, so the drawn topology
     * stays under this file's control.
     */
    void applyFeed(TopologyFeed.Snapshot snapshot) {
        for (int i = 0; i < snapshot.nodes.size(); i++) {
            TopologyFeed.NodeUpdate update = snapshot.nodes.get(i);
            Node node = nodeById(update.id);
            if (node == null) {
                continue;
            }
            if (update.name != null) {
                node.name = update.name;
            }
            if (update.role != null) {
                node.role = update.role;
            }
            if (update.detail != null) {
                node.detail = update.detail;
            }
        }

        for (int i = 0; i < snapshot.links.size(); i++) {
            TopologyFeed.LinkUpdate update = snapshot.links.get(i);
            Lane lane = laneFor(update.from, update.to);
            if (lane == null) {
                continue;
            }
            if (update.kbps != null && update.kbps.doubleValue() >= 0.0) {
                lane.kbps = update.kbps.doubleValue();
            }
            if (update.state != null) {
                lane.state = update.state;
            }
        }

        repaint();
    }

    /** False once a feed is driving the numbers, which stops the random walk. */
    void setSynthetic(boolean value) {
        synthetic = value;
    }

    private Node nodeById(String id) {
        for (int i = 0; i < nodes.length; i++) {
            if (nodes[i].id.equals(id)) {
                return nodes[i];
            }
        }
        return null;
    }

    private Lane laneFor(String fromId, String toId) {
        for (int i = 0; i < lanes.length; i++) {
            if (lanes[i].from.id.equals(fromId) && lanes[i].to.id.equals(toId)) {
                return lanes[i];
            }
        }
        return null;
    }

    /* ----------------------------------------------------------- animation */

    /** Advance the arrow animation one frame. */
    void advance() {
        for (int i = 0; i < lanes.length; i++) {
            Lane lane = lanes[i];
            /* Busier lanes move their arrows faster, capped so a large reading
               from a live feed cannot make them fly. */
            double speed = Math.min(1.1 + lane.kbps / 260.0, MAX_ARROW_SPEED);
            lane.phase = (lane.phase + speed) % ARROW_SPACING;
        }
        repaint();
    }

    /** Random walk the placeholder bandwidth figures, once per second. */
    void resample() {
        if (!synthetic) {
            return;
        }
        for (int i = 0; i < lanes.length; i++) {
            Lane lane = lanes[i];
            double next = lane.kbps + (random.nextDouble() - 0.5) * 160.0;
            if (next < KBPS_MIN) {
                next = KBPS_MIN;
            } else if (next > KBPS_MAX) {
                next = KBPS_MAX;
            }
            lane.kbps = next;
        }
    }

    double aggregateKbps() {
        double total = 0.0;
        for (int i = 0; i < lanes.length; i++) {
            total += lanes[i].kbps;
        }
        return total;
    }

    int nodeCount() {
        return nodes.length;
    }

    int laneCount() {
        return lanes.length;
    }

    /** Frames drawn since the last call, for the fps readout. */
    int consumeFrameCount() {
        int drawn = frames;
        frames = 0;
        return drawn;
    }

    /** The area the mesh actually occupies, so the view can be centred on it. */
    Rectangle mapBounds() {
        layoutNodes();
        Rectangle union = null;
        for (int i = 0; i < nodes.length; i++) {
            union = (union == null) ? new Rectangle(nodes[i].bounds) : union.union(nodes[i].bounds);
        }
        return union == null ? new Rectangle() : union;
    }

    /* -------------------------------------------------------------- paint */

    @Override
    protected void paintComponent(Graphics g) {
        super.paintComponent(g);
        frames++;

        Graphics2D g2 = (Graphics2D) g.create();
        try {
            g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING,
                                RenderingHints.VALUE_ANTIALIAS_OFF);
            g2.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING,
                                RenderingHints.VALUE_TEXT_ANTIALIAS_OFF);

            layoutNodes();

            for (int i = 0; i < lanes.length; i++) {
                drawLane(g2, lanes[i]);
            }
            for (int i = 0; i < nodes.length; i++) {
                drawNode(g2, nodes[i]);
            }
            /* Labels last so they are never buried under a link or a box. */
            for (int i = 0; i < lanes.length; i++) {
                drawLaneLabel(g2, lanes[i]);
            }
        } finally {
            g2.dispose();
        }
    }

    private void layoutNodes() {
        int w = getWidth() > 0 ? getWidth() : CANVAS_W;
        int h = getHeight() > 0 ? getHeight() : CANVAS_H;
        for (int i = 0; i < nodes.length; i++) {
            Node n = nodes[i];
            n.bounds.x = (int) Math.round(n.fx * w) - BOX_W / 2;
            n.bounds.y = (int) Math.round(n.fy * h) - BOX_H / 2;
        }
    }

    private void drawLane(Graphics2D g2, Lane lane) {
        Point2D.Double a = edgeOf(lane.from.bounds, lane.to.bounds);
        Point2D.Double b = edgeOf(lane.to.bounds, lane.from.bounds);

        double dx = b.x - a.x;
        double dy = b.y - a.y;
        double len = Math.sqrt(dx * dx + dy * dy);
        if (len < 1.0) {
            return;
        }
        dx /= len;
        dy /= len;

        /* Perpendicular flips with direction, so the two lanes of a link land
           on opposite sides of the centre line without any extra bookkeeping. */
        double px = -dy;
        double py = dx;

        double sx = a.x + dx * EDGE_GAP + px * LANE_OFFSET;
        double sy = a.y + dy * EDGE_GAP + py * LANE_OFFSET;
        double ex = b.x - dx * EDGE_GAP + px * LANE_OFFSET;
        double ey = b.y - dy * EDGE_GAP + py * LANE_OFFSET;

        Stroke old = g2.getStroke();
        g2.setStroke(new BasicStroke(3.0f));
        g2.setColor(stateColour(lane.state));
        g2.drawLine((int) Math.round(sx), (int) Math.round(sy),
                    (int) Math.round(ex), (int) Math.round(ey));
        g2.setStroke(old);

        double laneLen = Math.sqrt((ex - sx) * (ex - sx) + (ey - sy) * (ey - sy));
        for (double t = lane.phase; t < laneLen; t += ARROW_SPACING) {
            drawArrow(g2, sx + dx * t, sy + dy * t, dx, dy);
        }
    }

    private void drawArrow(Graphics2D g2, double x, double y, double dx, double dy) {
        double half = 5.0;
        double reach = 5.0;
        double px = -dy;
        double py = dx;

        Polygon head = new Polygon();
        head.addPoint((int) Math.round(x + dx * reach), (int) Math.round(y + dy * reach));
        head.addPoint((int) Math.round(x - dx * reach + px * half),
                      (int) Math.round(y - dy * reach + py * half));
        head.addPoint((int) Math.round(x - dx * reach - px * half),
                      (int) Math.round(y - dy * reach - py * half));

        g2.setColor(Color.WHITE);
        g2.fillPolygon(head);
        g2.setColor(Color.BLACK);
        g2.drawPolygon(head);
    }

    private void drawLaneLabel(Graphics2D g2, Lane lane) {
        Point2D.Double a = edgeOf(lane.from.bounds, lane.to.bounds);
        Point2D.Double b = edgeOf(lane.to.bounds, lane.from.bounds);

        double dx = b.x - a.x;
        double dy = b.y - a.y;
        double len = Math.sqrt(dx * dx + dy * dy);
        if (len < 1.0) {
            return;
        }
        dx /= len;
        dy /= len;

        double px = -dy;
        double py = dx;

        /* 42% along each lane rather than the midpoint. Because a lane runs in
           its own direction, the two halves of a link land at 42% and 58%, so
           the pair of labels never sits on top of itself. */
        double cx = a.x + dx * (len * 0.42) + px * LABEL_OFFSET;
        double cy = a.y + dy * (len * 0.42) + py * LABEL_OFFSET;

        /* Session state first, in its own colour, then the rate in black. */
        String tag = shortState(lane.state);
        String rate = String.format(Locale.US, "%.1f kbps", lane.kbps);

        g2.setFont(MotifKit.MONO);
        FontMetrics fm = g2.getFontMetrics();
        int tagWidth = fm.stringWidth(tag + " ");
        int th = fm.getAscent();

        int bw = tagWidth + fm.stringWidth(rate) + 10;
        int bh = th + 8;
        int bx = (int) Math.round(cx) - bw / 2;
        int by = (int) Math.round(cy) - bh / 2;

        g2.setColor(MotifKit.BG);
        g2.fillRect(bx, by, bw, bh);
        drawBevel(g2, bx, by, bw, bh, false);

        int baseline = by + 4 + th;
        g2.setColor(stateColour(lane.state));
        g2.drawString(tag, bx + 5, baseline);
        g2.setColor(MotifKit.TEXT);
        g2.drawString(rate, bx + 5 + tagWidth, baseline);
    }

    private void drawNode(Graphics2D g2, Node node) {
        Rectangle r = node.bounds;
        int inner = r.width - 14;

        g2.setColor(MotifKit.BG);
        g2.fillRect(r.x, r.y, r.width, r.height);
        drawBevel(g2, r.x, r.y, r.width, r.height, true);

        /* Mini dtwm title bar, so a node reads as a window on the map. */
        g2.setColor(MotifKit.SELECT);
        g2.fillRect(r.x + 3, r.y + 3, r.width - 6, TITLE_H);
        g2.setColor(MotifKit.HILITE);
        g2.setFont(MotifKit.UI_BOLD);
        FontMetrics fm = g2.getFontMetrics();
        g2.drawString(fit(node.name, fm, inner), r.x + 7,
                      r.y + 3 + TITLE_H - fm.getDescent() - 2);

        g2.setColor(MotifKit.TEXT);
        g2.setFont(MotifKit.MONO);
        FontMetrics mono = g2.getFontMetrics();
        int line = r.y + 3 + TITLE_H + mono.getAscent() + 6;
        g2.drawString(fit(node.role, mono, inner), r.x + 7, line);
        g2.drawString(fit(node.detail, mono, inner), r.x + 7, line + mono.getHeight() + 1);
    }

    /**
     * Colour for a BGP session state. Only "established" is up, so only
     * "established" is blue. A null state means no feed is attached and the
     * map is on synthetic numbers, which stays blue so the default view is
     * not a wall of red.
     */
    private static Color stateColour(String state) {
        if (state == null) {
            return LINK_BLUE;
        }
        String value = state.toLowerCase(Locale.US);
        if (value.equals("established")) {
            return LINK_BLUE;
        }
        if (value.equals("idle") || value.equals("down")) {
            return LINK_DOWN;
        }
        if (value.equals("unknown") || value.length() == 0) {
            return LINK_UNKNOWN;
        }
        /* connect, active, opensent, openconfirm: on the way up or flapping. */
        return LINK_BUSY;
    }

    /** Short tag for the label; BGP state names are too long to sit on a link. */
    private static String shortState(String state) {
        if (state == null) {
            return "SYN";
        }
        String value = state.toLowerCase(Locale.US);
        if (value.equals("established")) {
            return "ESTAB";
        }
        if (value.length() == 0 || value.equals("unknown")) {
            return "UNK";
        }
        if (value.length() > 5) {
            value = value.substring(0, 5);
        }
        return value.toUpperCase(Locale.US);
    }

    /** Clip a feed-supplied label rather than letting it run out of its box. */
    private static String fit(String text, FontMetrics fm, int maxWidth) {
        if (text == null) {
            return "";
        }
        if (fm.stringWidth(text) <= maxWidth) {
            return text;
        }
        int end = text.length();
        while (end > 0 && fm.stringWidth(text.substring(0, end) + "..") > maxWidth) {
            end--;
        }
        return text.substring(0, end) + "..";
    }

    /** Motif bevel: light top-left, dark bottom-right, inverted when sunken. */
    private void drawBevel(Graphics2D g2, int x, int y, int w, int h, boolean raised) {
        Color topLeft = raised ? MotifKit.HILITE : MotifKit.SHADOW;
        Color bottomRight = raised ? MotifKit.SHADOW : MotifKit.HILITE;

        for (int i = 0; i < 2; i++) {
            g2.setColor(topLeft);
            g2.drawLine(x + i, y + i, x + w - 1 - i, y + i);
            g2.drawLine(x + i, y + i, x + i, y + h - 1 - i);
            g2.setColor(bottomRight);
            g2.drawLine(x + w - 1 - i, y + i, x + w - 1 - i, y + h - 1 - i);
            g2.drawLine(x + i, y + h - 1 - i, x + w - 1 - i, y + h - 1 - i);
        }
    }

    /**
     * Where the centre-to-centre line leaves {@code from}'s box, so links start
     * at the edge of a device rather than under it.
     */
    private static Point2D.Double edgeOf(Rectangle from, Rectangle toward) {
        double cx = from.getCenterX();
        double cy = from.getCenterY();
        double dx = toward.getCenterX() - cx;
        double dy = toward.getCenterY() - cy;

        if (dx == 0.0 && dy == 0.0) {
            return new Point2D.Double(cx, cy);
        }

        double hw = from.width / 2.0;
        double hh = from.height / 2.0;
        double sx = (dx == 0.0) ? Double.MAX_VALUE : hw / Math.abs(dx);
        double sy = (dy == 0.0) ? Double.MAX_VALUE : hh / Math.abs(dy);
        double s = Math.min(sx, sy);

        return new Point2D.Double(cx + dx * s, cy + dy * s);
    }

    /* --------------------------------------------------------- scrollable */

    @Override
    public Dimension getPreferredScrollableViewportSize() {
        return getPreferredSize();
    }

    @Override
    public int getScrollableUnitIncrement(Rectangle visibleRect, int orientation, int direction) {
        return 24;
    }

    @Override
    public int getScrollableBlockIncrement(Rectangle visibleRect, int orientation, int direction) {
        return orientation == SwingConstants.VERTICAL
                ? Math.max(32, visibleRect.height - 32)
                : Math.max(32, visibleRect.width - 32);
    }

    /* Fill the viewport when it is bigger than the canvas, scroll when it is
       not. That way the map has no scrollbars until it outgrows the window. */
    @Override
    public boolean getScrollableTracksViewportWidth() {
        return getParent() instanceof JViewport
                && ((JViewport) getParent()).getWidth() > getPreferredSize().width;
    }

    @Override
    public boolean getScrollableTracksViewportHeight() {
        return getParent() instanceof JViewport
                && ((JViewport) getParent()).getHeight() > getPreferredSize().height;
    }
}
