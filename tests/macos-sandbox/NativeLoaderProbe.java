// SPDX-License-Identifier: GPL-3.0-only
/** Data-only reflection names keep this authored preflight independent of a particular LWJGL release. */
public final class NativeLoaderProbe {
    public static void main(String[] args) throws Exception {
        System.out.println("native.path=" + System.getProperty("org.lwjgl.librarypath"));
        Class.forName("org.lwjgl.system.Library");
        Class<?> version = Class.forName("org.lwjgl.Version");
        System.out.println("lwjgl.version=" + version.getMethod("getVersion").invoke(null));
        Class.forName("com.sun.jna.Native");
        System.out.println("native.preflight.complete=true");
    }
}
