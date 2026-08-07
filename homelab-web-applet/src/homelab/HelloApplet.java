package homelab;

import java.awt.BorderLayout;
import java.awt.Dimension;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.text.SimpleDateFormat;
import java.util.Date;

import javax.swing.BorderFactory;
import javax.swing.JApplet;
import javax.swing.JComboBox;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTextArea;
import javax.swing.JTextField;
import javax.swing.SwingConstants;
import javax.swing.SwingUtilities;
import javax.swing.Timer;
import javax.swing.UIManager;

/**
 * The operator console applet used by the admin panel.
 *
 * Runs under CheerpJ 4.x, which provides a Java 8 runtime in the browser, so the
 * CDE/Motif look and feel is still present in rt.jar.
 *
 * Layout: BorderLayout at the root, GridBagLayout for the operator form.
 */
public class HelloApplet extends JApplet {

    private static final long serialVersionUID = 1L;

    /* Kept in step with the HOSTS list in admin-panel/index.html. */
    private static final String[] NODES = {
        "pve01.homelab.local",
        "vault01.homelab.local",
        "nas00.homelab.local",
        "dns00.homelab.local",
        "k3s-w01.homelab.local",
        "k3s-w02.homelab.local",
        "backup01.homelab.local"
    };

    private JTextField operatorField;
    private JComboBox<String> nodeBox;
    private JTextArea console;
    private JLabel clockLabel;
    private int greetCount = 0;

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
        root.add(buildFormPanel(), BorderLayout.CENTER);
        root.add(buildStatusBar(), BorderLayout.SOUTH);

        setContentPane(root);
        setBackground(MotifKit.BG);

        startClock();
        emit("CheerpJ runtime online -- java " + System.getProperty("java.version"));
        emit("look and feel: " + UIManager.getLookAndFeel().getName());
        emit("awaiting operator input.");
    }

    private JPanel buildTitleStrip() {
        JPanel strip = new JPanel(new BorderLayout(0, 0));
        strip.setBackground(MotifKit.BG);
        strip.setBorder(BorderFactory.createEmptyBorder(2, 2, 2, 2));

        JLabel build = new JLabel("  rev 1.0  ");
        build.setFont(MotifKit.UI_FONT);
        build.setForeground(MotifKit.TEXT);
        build.setBorder(MotifKit.lowered());

        strip.add(MotifKit.titleStrip("SCREWHEAD NETWORKS -- OPERATOR CONSOLE"), BorderLayout.CENTER);
        strip.add(build, BorderLayout.EAST);
        return strip;
    }

    private JPanel buildFormPanel() {
        JPanel form = new JPanel(new GridBagLayout());
        form.setBackground(MotifKit.BG);
        form.setBorder(BorderFactory.createEmptyBorder(4, 4, 4, 4));

        GridBagConstraints c = new GridBagConstraints();
        c.insets = new Insets(1, 2, 1, 2);
        c.anchor = GridBagConstraints.WEST;
        c.fill = GridBagConstraints.HORIZONTAL;

        /* Row 0 -- operator name. */
        c.gridx = 0; c.gridy = 0; c.weightx = 0;
        form.add(MotifKit.boldLabel("Operator:"), c);

        operatorField = new JTextField("root", 16);
        operatorField.setFont(MotifKit.MONO);
        operatorField.setBackground(MotifKit.BG);
        operatorField.setForeground(MotifKit.TEXT);
        operatorField.setBorder(MotifKit.lowered());
        c.gridx = 1; c.gridy = 0; c.weightx = 1.0;
        form.add(operatorField, c);

        /* Row 1 -- target node. */
        c.gridx = 0; c.gridy = 1; c.weightx = 0;
        form.add(MotifKit.boldLabel("Target node:"), c);

        nodeBox = new JComboBox<String>(NODES);
        nodeBox.setFont(MotifKit.MONO);
        nodeBox.setBackground(MotifKit.BG);
        nodeBox.setForeground(MotifKit.TEXT);
        c.gridx = 1; c.gridy = 1; c.weightx = 1.0;
        form.add(nodeBox, c);

        /* Row 2 -- action buttons, packed left, no breathing room. */
        JPanel buttons = new JPanel(new GridBagLayout());
        buttons.setBackground(MotifKit.BG);
        GridBagConstraints b = new GridBagConstraints();
        b.insets = new Insets(0, 0, 0, 4);
        b.gridy = 0;

        b.gridx = 0;
        buttons.add(MotifKit.button("Greet", new ActionListener() {
            public void actionPerformed(ActionEvent e) {
                doGreet();
            }
        }), b);

        b.gridx = 1;
        buttons.add(MotifKit.button("Ping", new ActionListener() {
            public void actionPerformed(ActionEvent e) {
                emit("ping " + nodeBox.getSelectedItem() + " -- 4 packets, 0% loss, 0.4ms avg");
            }
        }), b);

        b.gridx = 2;
        buttons.add(MotifKit.button("Clear", new ActionListener() {
            public void actionPerformed(ActionEvent e) {
                console.setText("");
                greetCount = 0;
            }
        }), b);

        /* No fill on this row: a stretched panel would centre its buttons. */
        c.gridx = 0; c.gridy = 2; c.gridwidth = 2; c.weightx = 1.0;
        c.fill = GridBagConstraints.NONE;
        c.anchor = GridBagConstraints.WEST;
        c.insets = new Insets(4, 2, 3, 2);
        form.add(buttons, c);

        /* Row 3 -- console output, takes the remaining height. */
        console = new JTextArea(7, 40);
        console.setFont(MotifKit.MONO);
        console.setBackground(MotifKit.TERM_BG);
        console.setForeground(MotifKit.TERM_FG);
        console.setCaretColor(MotifKit.TERM_FG);
        console.setEditable(false);
        console.setLineWrap(true);
        console.setWrapStyleWord(false);
        console.setBorder(BorderFactory.createEmptyBorder(2, 3, 2, 3));

        JScrollPane scroll = new JScrollPane(console);
        scroll.setBorder(MotifKit.lowered());
        scroll.getViewport().setBackground(MotifKit.TERM_BG);
        scroll.setPreferredSize(new Dimension(400, 130));

        c.gridx = 0; c.gridy = 3; c.gridwidth = 2;
        c.weightx = 1.0; c.weighty = 1.0;
        c.fill = GridBagConstraints.BOTH;
        c.insets = new Insets(1, 2, 1, 2);
        form.add(scroll, c);

        return form;
    }

    private JPanel buildStatusBar() {
        JPanel bar = new JPanel(new BorderLayout(2, 0));
        bar.setBackground(MotifKit.BG);
        bar.setBorder(BorderFactory.createEmptyBorder(2, 3, 3, 3));

        clockLabel = MotifKit.statusCell("--:--:--");
        clockLabel.setHorizontalAlignment(SwingConstants.RIGHT);

        bar.add(MotifKit.statusCell("host: browser (cheerpj)"), BorderLayout.CENTER);
        bar.add(clockLabel, BorderLayout.EAST);
        return bar;
    }

    private void doGreet() {
        String operator = operatorField.getText().trim();
        if (operator.length() == 0) {
            operator = "anonymous";
        }
        greetCount++;
        emit("HELLO, " + operator.toUpperCase() + " -- connected to " + nodeBox.getSelectedItem()
                + " [session " + greetCount + "]");
    }

    private void emit(String line) {
        console.append(line + "\n");
        console.setCaretPosition(console.getDocument().getLength());
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
        return "HelloApplet rev 1.0 -- Screwhead Networks operator console";
    }
}
