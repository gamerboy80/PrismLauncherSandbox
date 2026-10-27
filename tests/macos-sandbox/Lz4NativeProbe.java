// SPDX-License-Identifier: GPL-3.0-only
import java.util.Arrays;
import net.jpountz.lz4.LZ4Factory;

/** Requires the JNI implementation, so a Java fallback cannot hide a native loading failure. */
public final class Lz4NativeProbe {
    public static void main(String[] args) throws Exception {
        byte[] original = "sandboxed native LZ4 round trip".getBytes("UTF-8");
        LZ4Factory factory = LZ4Factory.nativeInstance();
        byte[] compressed = factory.fastCompressor().compress(original);
        byte[] restored = factory.safeDecompressor().decompress(compressed, original.length);
        if (!Arrays.equals(original, restored)) throw new AssertionError("LZ4 round trip failed");
        System.out.println("lz4.factory=" + factory);
        System.out.println("lz4.native.roundtrip=true");
    }
}
