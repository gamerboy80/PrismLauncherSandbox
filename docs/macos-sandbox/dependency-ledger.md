# Local dependency and installation ledger

Baseline: upstream checkout `390a240abaefe389d1ab8510cc0ba5c2692a65be`, develop branch; Apple Silicon macOS 27.0.1.

Existing tools used: Apple Command Line Tools/AppleClang 21, CMake, Ninja, Homebrew Qt 6.11.2 and its existing dependencies, libarchive, qrencode, tomlplusplus, extra-cmake-modules, the Command Line Tools Python 3, installed Temurin 21.0.12.1, and existing Rosetta.

Existing public Prism caches reused as independent APFS clones: Minecraft metadata, libraries, assets, and Mojang Java 17.0.15, 21.0.7 and 25.0.1 runtimes. No account database or worlds were copied. Stock Prism files were not modified.

Repository addition: initialized the existing `libraries/libnbtplusplus` submodule at its pinned commit `3538933614059f0f44388a2b16f3db25ce42285b`.

New downloads: public compatibility metadata/JARs not already cached, and the official Azul Zulu Intel Java 8u504 JRE archive into the acceptance cache. No runtime was installed system-wide. No Homebrew/system packages were installed, no Rosetta installation was attempted, and no system Java paths/symlinks were changed.

Intel runtime source: Azul package UUID `3fc43981-2316-4e56-b93b-962ec7695031`, `zulu8.96.0.205-ca-jre8.0.504-macosx_x64.zip`, SHA-256 `b2a195c418fe926e5c2032bfd3fb23ed93b1133f50b99cb2c0e47dc618130e5d`. The exact SHA-256 matches vendor metadata. The vendor API reports a rounded size of 40,960,400 bytes; the matching archive contains 40,960,438 bytes. This discrepancy is recorded rather than silently treated as a size match.

The reviewed inventory JSON files preserve exact public artifact versions, source URLs where supplied by the preparation harness, upstream checksums and local hashes. Native inputs were signed only after the user approved their exact inventories. Signing changed copies in generated bundles; original archives/JARs/runtimes were retained.

No recursive quarantine removal, Gatekeeper disabling, broad sandbox grant, paid signing identity, or system security-setting change was used. The debugger attach was denied by macOS; crash reports were used instead of weakening debugging protections.

Additional authored fixtures were compiled locally for direct JNI DNS/listening tests and a Minecraft replacement entry-point/argument test. The original approved JNI binary remains intact; its expanded replacement was separately inventoried and approved. The pinned legacy Objective-C and LZ4 adapter record changes received separate exact-fingerprint approvals. No extra installed dependencies were needed.

Interactive acceptance additionally downloaded the official Mojang Java 25 runtime through the launcher's automatic Java workflow. Its origin and inventory are recorded in `ui-java-inventory.json`. The user subsequently authorized future inventories for this project's implementation and acceptance work; exact inventory records remain required and product review is retained.

The Qt accessibility repair builds a local Cocoa plugin from official Qt 6.11.2 sources plus the upstream QTBUG-149612 fix (Gerrit change 765434). Pinned MoltenVK 1.4.0 and Vulkan-Headers 1.4.321 sources supply build headers. Source archive URLs and SHA-256 values are fixed in `scripts/macos-sandbox/fix_qt_cocoa.py`; the upstream patch is retained in `scripts/macos-sandbox/qt-cocoa/QTBUG-149612.patch`. No system/Homebrew Qt installation was replaced. The plugin is copied and signed only in the packaged manager app.
