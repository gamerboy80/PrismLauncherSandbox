// SPDX-License-Identifier: GPL-3.0-only
import java.nio.file.*;

/** Authored replacement entry point: verifies additional libraries and argument boundaries. */
public final class MinecraftReplacementProbe {
    public static void main(String[] args) throws Exception {
        boolean argument = false, offline = false;
        for (int i = 0; i + 1 < args.length; ++i) {
            if (args[i].equals("--probe")) argument = args[i + 1].equals("one argument with spaces");
            if (args[i].equals("--probe-session")) offline = args[i + 1].equals("0");
        }
        if (!argument || !offline) throw new AssertionError("Custom replacement arguments were not preserved");
        String result = "dependency=" + StandaloneDependency.message() + "\nreplacement.arguments=true\nreplacement.offline=true\n";
        Files.write(Paths.get("replacement-probe.txt"), result.getBytes("UTF-8"));
        System.out.print(result);
        System.exit(0);
    }
}
