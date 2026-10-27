// SPDX-License-Identifier: GPL-3.0-only
import java.awt.*;
import java.nio.file.*;
import javax.swing.*;

/** Authored client for manifest dependencies, explicit main classes, graphics and log redaction. */
public final class StandaloneClient {
    public static void main(String[] args) throws Exception {
        System.out.println("dependency=" + StandaloneDependency.message());
        Files.write(Paths.get("standalone-result.txt"), "authored client ran\n".getBytes("UTF-8"));
        for (int i = 0; i + 1 < args.length; i++) {
            if (args[i].equals("--secret")) {
                String value = args[++i];
                System.out.print(value.substring(0, value.length() / 2));
                System.out.flush();
                Thread.sleep(100);
                System.out.println(value.substring(value.length() / 2));
            }
        }
        JFrame window = new JFrame("Prism sandbox authored client");
        SwingUtilities.invokeAndWait(() -> {
            window.setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE);
            window.add(new JLabel("Sandboxed Java graphics and dependency loading", SwingConstants.CENTER));
            window.setSize(560, 180);
            window.setLocationRelativeTo(null);
            window.setVisible(true);
        });
        System.out.println("graphics.ready=true");
        Thread.sleep(8000);
        SwingUtilities.invokeAndWait(window::dispose);
        System.out.println("client.complete=true");
        System.exit(0);
    }
}
