// SPDX-License-Identifier: GPL-3.0-only
package ca.weblite.nativeutils;

import java.io.File;
import java.io.IOException;

/** Bounded adapter, activated only for a fingerprinted java-objc-bridge artifact. */
public final class NativeUtils {
    private NativeUtils() {}
    public static File loadFileFromJar(String path, Class<?> source) throws IOException {
        if (!"/libjcocoa.dylib".equals(path))
            throw new IOException("Unsupported native resource in sandbox bridge: " + path);
        String configured = System.getProperty("prism.sandbox.jcocoa");
        if (configured == null) throw new IOException("No prepared Objective-C bridge was configured");
        File file = new File(configured).getCanonicalFile();
        if (!file.isFile() || !"libjcocoa.dylib".equals(file.getName()))
            throw new IOException("Prepared Objective-C bridge is missing");
        return file;
    }
    public static void loadLibraryFromJar(String path) throws IOException {
        loadLibraryFromJar(path, NativeUtils.class);
    }
    public static void loadLibraryFromJar(String path, Class<?> source) throws IOException {
        System.load(loadFileFromJar(path, source).getPath());
    }
}
