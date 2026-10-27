# LZ4 adapter review

Status: explicitly approved by the user in this implementation chat on 2026-10-27.

Only change: explicitly record the pinned LZ4 Java adapter that loads the existing prepared `liblz4-java.dylib` through `System.loadLibrary` before its extraction fallback. Native/runtime bytes, origins and signing exceptions are unchanged. Unknown native-bearing class JARs now fail preparation with an artifact-specific diagnostic rather than silently attempting extraction.

- vanilla-26.3: `529b582addc2226e7b50d2dc1d584e069e9a64a924223c89f2053fc882f0a519` (previous `34ddd4106072323918eb6f5b914f83479f24f5801300d3ae1943c821f79d6fba`)
- fabric-neoforge-1.21.1: `650164e3ae4a1392ba1f86e885d00bf47c5b2cf208e9ce475f9f784bb0f5099a` (previous `454524b2ae332adfbcc8cf50df7b8a9a80799137c976dbfe7966cd5798ee2708`)

Full hashes/origins: [lz4-adapter-inventories.json](lz4-adapter-inventories.json). Approval permits local ad hoc signing and offline sandboxed tests.
