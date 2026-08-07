package homelab;

import java.awt.BorderLayout;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.text.SimpleDateFormat;
import java.util.Date;

import javax.swing.BorderFactory;
import javax.swing.JApplet;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.SwingConstants;
import javax.swing.SwingUtilities;
import javax.swing.Timer;
import javax.swing.UIManager;

/**
 * The greeting applet on the Screwhead Networks home page.
 *
 * Deliberately generic: it welcomes the visitor and shows that a real Java 8
 * applet is running client side under CheerpJ. Layout is BorderLayout at the
 * root with a GridBagLayout for the greeting block.
 */
public class WelcomeApplet extends JApplet {

    private static final long serialVersionUID = 1L;

    private static final String[] GREETING = {
        "Welcome to the Screwhead Networks web page.",
        "",
        "You have reached the Screwhead Networks public web server.",
        "Everything on this page, including this panel, is served from our own",
        "hardware. This panel is a genuine Java applet: your browser is running",
        "Java 8 bytecode through CheerpJ, with no plugin and no JVM installed.",
        "",
        "Use the menu bar above to move around the site."
    };

    private JLabel clockLabel;

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

        root.add(buildTitleStrip(), BorderLayout.NORTH);
        root.add(buildGreeting(), BorderLayout.CENTER);
        root.add(buildStatusBar(), BorderLayout.SOUTH);

        setContentPane(root);
        setBackground(MotifKit.BG);

        startClock();
    }

    private JPanel buildTitleStrip() {
        JPanel strip = new JPanel(new BorderLayout(0, 0));
        strip.setBackground(MotifKit.BG);
        strip.setBorder(BorderFactory.createEmptyBorder(2, 2, 2, 2));

        JLabel build = new JLabel("  est. 1999  ");
        build.setFont(MotifKit.UI_FONT);
        build.setForeground(MotifKit.TEXT);
        build.setBorder(MotifKit.lowered());

        strip.add(MotifKit.titleStrip("SCREWHEAD NETWORKS"), BorderLayout.CENTER);
        strip.add(build, BorderLayout.EAST);
        return strip;
    }

    private JPanel buildGreeting() {
        JPanel body = new JPanel(new GridBagLayout());
        body.setBackground(MotifKit.BG);
        body.setBorder(BorderFactory.createEmptyBorder(4, 6, 4, 6));

        GridBagConstraints c = new GridBagConstraints();
        c.gridx = 0;
        c.weightx = 1.0;
        c.anchor = GridBagConstraints.WEST;
        c.fill = GridBagConstraints.HORIZONTAL;
        c.insets = new Insets(0, 0, 0, 0);

        for (int i = 0; i < GREETING.length; i++) {
            c.gridy = i;
            JLabel line;
            if (i == 0) {
                line = MotifKit.boldLabel(GREETING[i]);
            } else if (GREETING[i].length() == 0) {
                line = MotifKit.plainLabel(" ");
            } else {
                line = MotifKit.plainLabel(GREETING[i]);
            }
            body.add(line, c);
        }

        /* Soak up the leftover height so the text stays packed at the top. */
        c.gridy = GREETING.length;
        c.weighty = 1.0;
        c.fill = GridBagConstraints.BOTH;
        JPanel filler = new JPanel();
        filler.setBackground(MotifKit.BG);
        body.add(filler, c);

        return body;
    }

    private JPanel buildStatusBar() {
        JPanel bar = new JPanel(new BorderLayout(2, 0));
        bar.setBackground(MotifKit.BG);
        bar.setBorder(BorderFactory.createEmptyBorder(2, 3, 3, 3));

        clockLabel = MotifKit.statusCell("--:--:--");
        clockLabel.setHorizontalAlignment(SwingConstants.RIGHT);

        bar.add(MotifKit.statusCell("java " + System.getProperty("java.version")
                + " -- " + UIManager.getLookAndFeel().getName()), BorderLayout.CENTER);
        bar.add(clockLabel, BorderLayout.EAST);
        return bar;
    }

    private void startClock() {
        final SimpleDateFormat format = new SimpleDateFormat("HH:mm:ss");
        Timer timer = new Timer(1000, new ActionListener() {
            public void actionPerformed(ActionEvent e) {
                clockLabel.setText(" " + format.format(new Date()) + " ");
            }
        });
        timer.setInitialDelay(0);
        timer.start();
    }

    @Override
    public String getAppletInfo() {
        return "WelcomeApplet rev 1.0 -- Screwhead Networks";
    }
}
