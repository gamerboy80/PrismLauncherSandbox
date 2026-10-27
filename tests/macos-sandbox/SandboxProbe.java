// SPDX-License-Identifier: GPL-3.0-only
import java.io.*;
import java.net.*;
import java.nio.file.*;

/** Authored fixture: no Minecraft or third-party client code is executed. */
public final class SandboxProbe {
    private static native boolean nativeWrite(String path);
    private static native boolean nativeConnect(int port);
    private static native boolean nativeRead(String path);
    private static native boolean nativeListen();
    private static native boolean nativeDNS();
    private static boolean write(String path) {
        try { Files.write(Paths.get(path), new byte[]{42}); return true; }
        catch (Exception expected) { return false; }
    }
    private static boolean read(String path) {
        try (InputStream stream = Files.newInputStream(Paths.get(path))) { stream.read(); return true; }
        catch (Exception expected) { return false; }
    }
    private static boolean connect(int port) {
        try (Socket socket = new Socket()) { socket.connect(new InetSocketAddress("127.0.0.1", port), 1500); return true; }
        catch (Exception expected) { return false; }
    }
    private static boolean listen() {
        try (ServerSocket socket = new ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))) { return true; }
        catch (Exception expected) { return false; }
    }
    private static boolean dns() {
        try { return InetAddress.getAllByName("example.com").length > 0; }
        catch (Exception expected) { return false; }
    }
    public static void main(String[] args) throws Exception {
        System.load(args[0]);
        int port = Integer.parseInt(args[1]);
        System.out.println("java.inside=" + write(args[2] + "/java-inside"));
        System.out.println("jni.inside=" + nativeWrite(args[2] + "/jni-inside"));
        for (int i = 3; i < args.length; ++i) {
            System.out.println("java.outside." + i + "=" + write(args[i] + "-java-write"));
            System.out.println("jni.outside." + i + "=" + nativeWrite(args[i] + "-jni-write"));
            System.out.println("java.read." + i + "=" + read(args[i]));
            System.out.println("jni.read." + i + "=" + nativeRead(args[i]));
        }
        Process cat = new ProcessBuilder("/bin/cat", args[3]).start();
        System.out.println("child.outside=" + (cat.waitFor() == 0));
        Process interpreter = new ProcessBuilder("/bin/sh", "-c", "cat \"$1\"", "sandbox-probe", args[3]).start();
        System.out.println("interpreter.outside=" + (interpreter.waitFor() == 0));
        System.out.println("java.connect=" + connect(port));
        System.out.println("jni.connect=" + nativeConnect(port));
        System.out.println("java.listen=" + listen());
        System.out.println("java.dns=" + dns());
        System.out.println("jni.listen=" + nativeListen());
        System.out.println("jni.dns=" + nativeDNS());
        System.out.println("probe.complete=true");
    }
}
