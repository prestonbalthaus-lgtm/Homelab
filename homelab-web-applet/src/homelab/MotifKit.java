package homelab;

import java.awt.Color;
import java.awt.Font;
import java.awt.Insets;
import java.awt.event.ActionListener;

import javax.swing.BorderFactory;
import javax.swing.JButton;
import javax.swing.JLabel;
import javax.swing.UIManager;
import javax.swing.border.BevelBorder;
import javax.swing.border.Border;

/**
 * Shared CDE/Motif palette, fonts and widget factories for the Screwhead
 * Networks applets, so every applet in the jar looks like the same workstation.
 */
final class MotifKit {

    static final Color BG      = new Color(0xC0, 0xC0, 0xC0);
    static final Color SHADOW  = new Color(0x80, 0x80, 0x80);
    static final Color HILITE  = new Color(0xFF, 0xFF, 0xFF);
    static final Color SELECT  = new Color(0x00, 0x00, 0x80);
    static final Color TEXT    = Color.BLACK;
    static final Color TERM_BG = new Color(0x00, 0x00, 0x00);
    static final Color TERM_FG = new Color(0xC8, 0xC8, 0xC8);

    static final Font UI_FONT = new Font("Dialog", Font.PLAIN, 12);
    static final Font UI_BOLD = new Font("Dialog", Font.BOLD, 12);
    static final Font MONO    = new Font("Monospaced", Font.PLAIN, 12);

    private MotifKit() {
        /* static helpers only */
    }

    /**
     * The CDE/Motif look and feel ships with the Java 8 runtime CheerpJ uses.
     * If it is ever unavailable, the explicit colours, fonts and bevel borders
     * applied by the factories below still carry the workstation styling.
     */
    static void applyLookAndFeel() {
        try {
            System.setProperty("awt.useSystemAAFontSettings", "off");
            System.setProperty("swing.aatext", "false");
        } catch (SecurityException ignored) {
            /* Sandboxed: keep going, the look and feel matters more. */
        }
        try {
            UIManager.setLookAndFeel("com.sun.java.swing.plaf.motif.MotifLookAndFeel");
        } catch (Exception e) {
            try {
                UIManager.setLookAndFeel(UIManager.getCrossPlatformLookAndFeelClassName());
            } catch (Exception ignored) {
                /* Fall through to the platform default. */
            }
        }
        UIManager.put("control", BG);
        UIManager.put("Panel.background", BG);
        UIManager.put("Button.background", BG);
        UIManager.put("Label.foreground", TEXT);
        UIManager.put("TextField.background", BG);
        UIManager.put("ComboBox.background", BG);
        UIManager.put("ComboBox.selectionBackground", SELECT);
        UIManager.put("ComboBox.selectionForeground", HILITE);
    }

    static Border raised() {
        return BorderFactory.createBevelBorder(BevelBorder.RAISED, HILITE, BG, SHADOW, BG);
    }

    static Border lowered() {
        return BorderFactory.createBevelBorder(BevelBorder.LOWERED, HILITE, BG, SHADOW, BG);
    }

    /** Navy title strip, the applet's equivalent of a dtwm title bar. */
    static JLabel titleStrip(String text) {
        JLabel label = new JLabel("  " + text + "  ");
        label.setFont(UI_BOLD);
        label.setForeground(HILITE);
        label.setBackground(SELECT);
        label.setOpaque(true);
        label.setBorder(raised());
        return label;
    }

    /** Sunken monospaced cell, used for status bars and read-only readouts. */
    static JLabel statusCell(String text) {
        JLabel label = new JLabel(" " + text + " ");
        label.setFont(MONO);
        label.setForeground(TEXT);
        label.setBackground(BG);
        label.setOpaque(true);
        label.setBorder(lowered());
        return label;
    }

    static JLabel boldLabel(String text) {
        JLabel label = new JLabel(text);
        label.setFont(UI_BOLD);
        label.setForeground(TEXT);
        label.setBorder(BorderFactory.createEmptyBorder(0, 0, 0, 4));
        return label;
    }

    static JLabel plainLabel(String text) {
        JLabel label = new JLabel(text);
        label.setFont(UI_FONT);
        label.setForeground(TEXT);
        return label;
    }

    static JButton button(String text, ActionListener listener) {
        JButton button = new JButton(text);
        button.setFont(UI_BOLD);
        button.setForeground(TEXT);
        button.setBackground(BG);
        button.setFocusPainted(false);
        button.setMargin(new Insets(1, 10, 1, 10));
        button.addActionListener(listener);
        return button;
    }
}
