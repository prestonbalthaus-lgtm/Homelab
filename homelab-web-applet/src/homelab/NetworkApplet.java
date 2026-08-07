package homelab;

import java.awt.BorderLayout;
import java.awt.Point;
import java.awt.Rectangle;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.awt.event.ComponentAdapter;
import java.awt.event.ComponentEvent;
import java.util.Locale;

import javax.swing.BorderFactory;
import javax.swing.JApplet;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.SwingConstants;
import javax.swing.SwingUtilities;
import javax.swing.Timer;

/**
 * The Layout page applet: a live traffic map of the router mesh.
 *
 * BorderLayout at the root, with the Graphics2D map in the centre inside a
 * scroll pane, so the canvas can be panned with the scrollbars or by dragging.
 *
 * One timer drives the animation; it advances the arrows every frame, and
 * every seventeenth frame (about a second) it resamples the placeholder
 * bandwidth and refreshes the status bar. A second, slower timer polls the
 * topology feed when one is configured.
 *
 * Applet parameters, both optional:
 *
 * <pre>
 * &lt;param name="feed" value="/topology.json"&gt;
 * &lt;param name="feedInterval" value="5"&gt;
 * </pre>
 *
 * With no "feed" parameter the map runs on synthetic numbers and never touches
 * the network. See {@link TopologyFeed} for the document shape and for why the
 * URL has to be same-origin.
 */
public class NetworkApplet extends JApplet {

    private static final long serialVersionUID = 1L;

    /* ~16fps. Fast enough to read as motion, slow enough to stay cheap when the
       whole map is being repainted through CheerpJ. */
    private static final int FRAME_MS = 60;
    private static final int FRAMES_PER_SECOND_TICK = 17;

    private static final int DEFAULT_FEED_SECONDS = 5;
    private static final int MIN_FEED_SECONDS = 2;

    private NetworkPanel map;
    private JScrollPane scroll;
    private JLabel aggregateLabel;
    private JLabel feedLabel;
    private JLabel fpsLabel;

    private Timer animationTimer;
    private Timer feedTimer;
    private int frameTick = 0;

    private TopologyFeed feed;
    private boolean feedInFlight = false;
    private boolean feedEverSucceeded = false;
    private boolean viewCentred = false;

    @Override
    public void init() {
        MotifKit.applyLookAndFeel();
        Runnable build = new Runnable() {
            public void run() {
                buildUserInterface();
            }
        };
        try {
            if (SwingUtilities.isEventDispatchThread()) {
                build.run();
            } else {
                SwingUtilities.invokeAndWait(build);
            }
        } catch (Exception e) {
            /* Last resort: build inline rather than show an empty applet. */
            build.run();
        }
    }

    private void buildUserInterface() {
        JPanel root = new JPanel(new BorderLayout(0, 0));
        root.setBackground(MotifKit.BG);
        root.setBorder(MotifKit.raised());

        map = new NetworkPanel();

        scroll = new JScrollPane(map,
                JScrollPane.VERTICAL_SCROLLBAR_AS_NEEDED,
                JScrollPane.HORIZONTAL_SCROLLBAR_AS_NEEDED);
        scroll.setBorder(BorderFactory.createEmptyBorder(3, 4, 3, 4));
        scroll.getViewport().setBackground(MotifKit.BG);
        scroll.getHorizontalScrollBar().setUnitIncrement(24);
        scroll.getVerticalScrollBar().setUnitIncrement(24);

        /* Centre on the mesh once the viewport has a real size, so the map is
           not parked in the top-left corner of a canvas larger than the view. */
        scroll.getViewport().addComponentListener(new ComponentAdapter() {
            @Override
            public void componentResized(ComponentEvent e) {
                centreOnMap();
            }
        });

        root.add(buildTitleStrip(), BorderLayout.NORTH);
        root.add(scroll, BorderLayout.CENTER);
        root.add(buildStatusBar(), BorderLayout.SOUTH);

        setContentPane(root);
        setBackground(MotifKit.BG);

        configureFeed();

        animationTimer = new Timer(FRAME_MS, new ActionListener() {
            public void actionPerformed(ActionEvent e) {
                onFrame();
            }
        });
    }

    private JPanel buildTitleStrip() {
        JPanel strip = new JPanel(new BorderLayout(0, 0));
        strip.setBackground(MotifKit.BG);
        strip.setBorder(BorderFactory.createEmptyBorder(2, 2, 2, 2));

        JLabel note = new JLabel("  drag to pan  ");
        note.setFont(MotifKit.UI_FONT);
        note.setForeground(MotifKit.TEXT);
        note.setBorder(MotifKit.lowered());

        strip.add(MotifKit.titleStrip("SCREWHEAD NETWORKS -- TRAFFIC MAP"), BorderLayout.CENTER);
        strip.add(note, BorderLayout.EAST);
        return strip;
    }

    private JPanel buildStatusBar() {
        JPanel bar = new JPanel(new BorderLayout(2, 0));
        bar.setBackground(MotifKit.BG);
        bar.setBorder(BorderFactory.createEmptyBorder(2, 3, 3, 3));

        aggregateLabel = MotifKit.statusCell("aggregate: -- kbps");
        feedLabel = MotifKit.statusCell("feed: off");
        fpsLabel = MotifKit.statusCell("-- fps");
        fpsLabel.setHorizontalAlignment(SwingConstants.RIGHT);

        JPanel right = new JPanel(new BorderLayout(2, 0));
        right.setBackground(MotifKit.BG);

        JPanel counts = new JPanel(new BorderLayout(2, 0));
        counts.setBackground(MotifKit.BG);
        counts.add(MotifKit.statusCell(map.nodeCount() + " devices / "
                + (map.laneCount() / 2) + " links / " + map.laneCount() + " lanes"),
                BorderLayout.CENTER);
        counts.add(feedLabel, BorderLayout.EAST);

        right.add(counts, BorderLayout.CENTER);
        right.add(fpsLabel, BorderLayout.EAST);

        bar.add(aggregateLabel, BorderLayout.CENTER);
        bar.add(right, BorderLayout.EAST);
        return bar;
    }

    private void centreOnMap() {
        if (viewCentred || scroll == null) {
            return;
        }
        Rectangle view = scroll.getViewport().getViewRect();
        if (view.width <= 0 || view.height <= 0) {
            return;
        }

        Rectangle bounds = map.mapBounds();
        int canvasW = map.getPreferredSize().width;
        int canvasH = map.getPreferredSize().height;

        int x = bounds.x + bounds.width / 2 - view.width / 2;
        int y = bounds.y + bounds.height / 2 - view.height / 2;
        x = Math.max(0, Math.min(x, Math.max(0, canvasW - view.width)));
        y = Math.max(0, Math.min(y, Math.max(0, canvasH - view.height)));

        scroll.getViewport().setViewPosition(new Point(x, y));
        viewCentred = true;
    }

    /* ---------------------------------------------------------------- feed */

    private void configureFeed() {
        String spec = getParameter("feed");
        if (spec == null || spec.trim().length() == 0) {
            feedLabel.setText(" feed: off (synthetic) ");
            return;
        }

        try {
            feed = new TopologyFeed(new java.net.URL(getDocumentBase(), spec.trim()).toString());
        } catch (Exception e) {
            feed = null;
            feedLabel.setText(" feed: bad url ");
            return;
        }

        /* A feed is expected, so stop inventing numbers even before the first
           reading lands. Otherwise the map would show a convincing random walk
           while the real feed was failing. */
        map.setSynthetic(false);
        feedLabel.setText(" feed: waiting ");

        int seconds = DEFAULT_FEED_SECONDS;
        String interval = getParameter("feedInterval");
        if (interval != null) {
            try {
                seconds = Math.max(MIN_FEED_SECONDS, Integer.parseInt(interval.trim()));
            } catch (NumberFormatException e) {
                seconds = DEFAULT_FEED_SECONDS;
            }
        }

        feedTimer = new Timer(seconds * 1000, new ActionListener() {
            public void actionPerformed(ActionEvent e) {
                pollFeed();
            }
        });
        feedTimer.setInitialDelay(0);
    }

    /** Fetch off the EDT; a blocking read on it would freeze the animation. */
    private void pollFeed() {
        if (feed == null || feedInFlight) {
            return;
        }
        feedInFlight = true;

        Thread worker = new Thread(new Runnable() {
            public void run() {
                TopologyFeed.Snapshot snapshot;
                String failure;
                try {
                    snapshot = feed.fetch();
                    failure = null;
                } catch (Exception e) {
                    snapshot = null;
                    String message = e.getMessage();
                    failure = (message == null || message.length() == 0)
                            ? e.getClass().getSimpleName()
                            : message;
                }
                final TopologyFeed.Snapshot result = snapshot;
                final String error = failure;
                SwingUtilities.invokeLater(new Runnable() {
                    public void run() {
                        onFeedResult(result, error);
                    }
                });
            }
        }, "topology-feed");
        worker.setDaemon(true);
        worker.start();
    }

    private void onFeedResult(TopologyFeed.Snapshot snapshot, String error) {
        feedInFlight = false;

        if (snapshot != null) {
            map.applyFeed(snapshot);
            feedEverSucceeded = true;
            feedLabel.setText(" feed: live ");
            return;
        }

        String detail = (error == null) ? "unavailable" : error;
        if (detail.length() > 38) {
            detail = detail.substring(0, 38);
        }
        feedLabel.setText(feedEverSucceeded ? " feed: stale -- " + detail
                                            : " feed: error -- " + detail);
    }

    /* ----------------------------------------------------------- animation */

    private void onFrame() {
        map.advance();

        frameTick++;
        if (frameTick >= FRAMES_PER_SECOND_TICK) {
            frameTick = 0;
            map.resample();
            aggregateLabel.setText(String.format(Locale.US,
                    " aggregate: %.1f kbps across %d lanes ",
                    map.aggregateKbps(), map.laneCount()));
            fpsLabel.setText(" " + map.consumeFrameCount() + " fps ");
        }
    }

    @Override
    public void start() {
        if (animationTimer != null) {
            animationTimer.start();
        }
        if (feedTimer != null) {
            feedTimer.start();
        }
        SwingUtilities.invokeLater(new Runnable() {
            public void run() {
                centreOnMap();
            }
        });
    }

    @Override
    public void stop() {
        if (animationTimer != null) {
            animationTimer.stop();
        }
        if (feedTimer != null) {
            feedTimer.stop();
        }
    }

    @Override
    public String getAppletInfo() {
        return "NetworkApplet rev 1.1 -- Screwhead Networks traffic map";
    }
}
