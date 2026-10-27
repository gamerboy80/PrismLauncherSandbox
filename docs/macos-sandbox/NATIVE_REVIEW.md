# Native preparation review — compatibility tests

Status: the exact inventories were explicitly approved by the user in this implementation chat. Local signing and offline tests were performed on copies.

Scope: local ad hoc signing of copies for the eight authored/public compatibility profiles below. Original runtime and JAR files are preserved. This permits preparation and sandboxed tests; it does not authorize authenticated account use.

The complete original native SHA-256 hashes, architectures, source paths, parent artifacts, adapters and signing profile appear in [reviewed-inventories.json](reviewed-inventories.json). Public artifact URLs and upstream checksums are included there. The Intel Java 8 archive matches the vendor SHA-256; the vendor API rounds its size (40,960,400 reported; 40,960,438 actual).

| Profiles | Fingerprint | Runtime Mach-O files | Other native files | Architecture |
|---|---|---:|---:|---|
| vanilla-26.3 | `285e87d01f8e7408f659d3389424cc2867962222cf84b21b7436018c65072577` | 73 | 16 | arm64 |
| fabric-1.20.1, forge-1.20.1 | `df1264a4283e7402048506b1109c5da0e77da7ae72cc31f31e9f1641197fc280` | 70 | 10 | arm64 |
| fabric-1.21.1, neoforge-1.21.1 | `8aed1a3533973140876c6573f19560bb89a94c31b39e823dbf005cc1dd8bf051` | 72 | 12 | arm64 |
| forge-1.12.2-intel | `57a4cd0c8d0341a0d1919d4db23eccbfd2d7aadbaf60a7df1b919a39c1d4d694` | 53 | 5 | x86_64 |
| standalone-manifest, standalone-explicit | `e851bb2f56e7c14c70b7c2bc03fc908a46c50c2185feb9927524c34422010d95` | 72 | 0 | arm64 |

Signing profile: `adhoc-inherited-jvm-v1`. Each game app has App Sandbox, one exact instance directory grant and the instance’s selected network entitlements. JVM executables inherit the sandbox and receive JIT, unsigned executable memory and disabled library validation. Dylibs receive no executable entitlements. The first-party native host receives no JIT exceptions.

Approvals are valid only for each exact fingerprint. Changed bytes, origins, architecture, adapter or signing profile require another review. Identical fingerprints can be reused across the listed profiles.

Inventory report SHA-256: `809bf716a43e09a893346c57eb16a80fdc4057ff18631bea1d1db0eef85efdab`.
