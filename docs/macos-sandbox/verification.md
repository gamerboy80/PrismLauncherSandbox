# Verification status

This is a locally runnable development build. The sandbox boundary and the listed startup profiles have been exercised on this Mac. The agreed interactive acceptance run is complete, with authenticated launch and multiplayer checks explicitly skipped at the user’s request. Those checks remain unverified; this is not an unconditional pass of the original full acceptance standard.

## Build and automated checks

- Host: Apple Silicon macOS 27.0.1; existing Rosetta; AppleClang 21; Qt 6.11.2; installed Temurin 21 compiler.
- Feature Debug build succeeds. Java launcher fixtures target Java 8. PrismGameHost contains ARM64 and x86_64 slices and does not link Qt.
- CTest: 25/25 pass, including anchored file operations, download publication during a parent-link swap, managed trash safety, Maven traversal validation, startup plugin-injection rejection, anchored resource renames, and asset/modpack metadata traversal rejection. Backend: 28/28 pass, including inventory approval reuse/invalidation, archive/path validation, independent snapshots, manifest dependencies, signed-manifest header handling, client records, root authority, relocation and conservative cleanup.
- The packaged manager has bundle ID `org.prismlauncher.PrismLauncher.LocalSandboxManager`, passes strict recursive signature verification, and has no non-system absolute linked dependencies. See [manager-package.json](reports/manager-package.json) and `install-macos-sandbox-manager/manager-package-report.json`.
- The manager's own startup rejects Qt plugin/platform override arguments and removes environment overrides for Qt plugins, dynamic loading, signing tools, OpenSSL modules and environment proxies. Python backend calls use isolated mode without site initialization.

The final installed app also passed [standalone and explicit replacement launch tests](reports/packaged-manager-smoke.json), both exit 0, using its bundled helper/backend/JAR resources.

## Isolation gate

The authored Java/JNI fixture passes in both networking modes. See [allowed](reports/isolation-allowed.json) and [blocked](reports/isolation-blocked.json).

Both Java and direct JNI:

- Can read/write their own dedicated instance.
- Cannot read or write the private manager sentinel, sibling instance sentinel, or unrelated user sentinel.
- Can read the sealed bundle but cannot modify it.
- Follow the selected policy for outbound sockets, listening sockets and DNS.

Native subprocesses and `/bin/sh` inherit the boundary. The offline FIFO carries launch configuration, logs and exit messages without network entitlements. Tests use authored sentinels and code, not an untrusted real client. These results are not malware analysis or proof against every possible sandbox escape.

Protocol tests cover chunked Configure, Ping/Pong, an unsupported descriptor version, fragmented secret redaction in forwarded and durable logs, and stopping a command that ignores a 512 KiB stdin payload. See [protocol](reports/client-protocol-probe.json), [schema rejection](reports/invalid-version-probe.json) and [stop](reports/nonblocking-stop-probe.json).

## Compatibility startup matrix

The report [compatibility-startup.json](reports/compatibility-startup.json) records exact approval fingerprints and outcomes. Full artifact versions, original native SHA-256 hashes and origins are in the reviewed/resolved inventory JSON files alongside this document.

| Profile | Runtime / architecture | Verified outcome |
|---|---|---|
| Vanilla 26.3 | Microsoft/Mojang Java 25.0.1, ARM64 | SDL Cocoa window, LWJGL 3.4.3+4, OpenAL and texture atlases; intentional stop |
| Fabric 1.20.1, loader 0.16.14 | Mojang Java 17.0.15, ARM64 | Graphics/audio initialization and intentional stop |
| Forge 1.20.1, 47.4.0 | Mojang Java 17.0.15, ARM64 | Graphics/audio initialization and intentional stop |
| Fabric 1.21.1, loader 0.16.14 | Mojang Java 21.0.7, ARM64 | Graphics/audio initialization and intentional stop |
| NeoForge 1.21.1, 21.1.234 | Mojang Java 21.0.7, ARM64 | Graphics/audio initialization and intentional stop |
| Forge 1.12.2, 14.23.5.2860 | Azul Zulu Java 8u504, Intel/Rosetta | Mod loading, texture atlases and sound-engine startup; intentional stop. Old AudioUnit initialization warnings remain; audible output unverified |
| Official 1.20.1 client imported as replacement | Mojang Java 17.0.15, ARM64 | Original JAR bytes preserved; graphics/audio initialization and intentional stop |
| Authored manifest JAR + local dependency | Temurin 21.0.12.1, ARM64 | Dependency loaded, Swing window initialized, exit 0 |
| Authored Minecraft replacement, explicit main class and additional library | Mojang Java 17.0.15, ARM64 | Spaced arguments and offline session substitution verified; exit 0 |
| Authored explicit main class + classpath | Temurin 21.0.12.1, ARM64 | Dependency loaded, Swing window initialized, exit 0 |

All Minecraft tests used offline sessions. Realms/user-property authentication errors are expected in that mode and are not sandbox failures. No `UnsatisfiedLinkError` appeared in the passing startup reports. Startup logs demonstrate window and graphics/audio API initialization; they do not demonstrate world creation or audible speaker output.

The [authored replacement report](reports/replacement-arguments.json) verifies an additional imported library, an explicit Minecraft replacement entry point, a spaced argument and offline-session substitution.

The native-only LWJGL/JNA preflight also confirmed loading from the sealed app path without the native-checksum warning. Native-only JARs containing `module-info.class` are removed from the runtime classpath. Signed-byte checksum overlays follow class JARs. The current vanilla bootstrap no longer receives a read-only directory as its extraction scratch path.

The final [host protocol report](reports/host-protocol-probes.json) also verifies output-flood cancellation and ten-second escalation for a background descendant that ignores termination. The helper retains its group leader until cleanup to prevent PID/group reuse during supervision.

## Lifecycle

Two independently sandboxed Minecraft instances ran concurrently. Duplicate launch preserved the same run UUID. Both games survived manager termination, were recognized after restart, and stopped through independently verified helper identities. See [lifecycle.json](reports/lifecycle.json). The host recorded intentional stop separately from a crash. This does not claim visual review of the reattached console UI.

## Interactive checks

The normal Add Instance workflow created **UI Acceptance Vanilla**, downloaded its official managed Java runtime automatically, resolved libraries/assets, displayed native review, and launched through the independent sandbox app. Cancelling the first review performed no signing or bundle publication. The subsequently approved inventory is recorded in [UI_JAVA_REVIEW.md](UI_JAVA_REVIEW.md).

The user created **Sandbox Acceptance**, saved, reopened it and confirmed it restored correctly. Region files, current/previous `level.dat`, and repeated save/reload log entries corroborate this result. See [interactive-acceptance.json](reports/interactive-acceptance.json).

The custom-client wizard imported an authored runnable JAR and its relative manifest dependency. A repeat import after the accessibility fixes completed without a manager crash, loaded the dependency, initialized its Swing window, and exited 0. Copying through the UI created a fresh isolation identity; its independent launch also loaded the dependency. These are authored fixture tests, not compatibility claims for arbitrary proprietary clients.

Interactive testing exposed two accessibility crashes: Qt Cocoa synthesized cells incorrectly released a borrowed parent interface, and Prism assumed cached accessible cells always survived native bridge updates. The build now includes the upstream QTBUG-149612 Cocoa repair locally; system Qt is unchanged. Prism recreates expired accessible cells. The native Cocoa regression passes 3 checks with the repaired plugin; Prism's accessibility regression is included in the 25 passing CTest tests. An initial crash in the authored native regression itself was corrected by retaining its Objective-C element across event processing.

The local authored `.mrpack` imported its override file and Fabric metadata, automatically downloaded Java 17, passed native review and reached graphics/audio initialization. The real Modrinth catalog downloaded **Fabulously Optimized 5.2.8 for Minecraft 1.20.1** (project `1KVo5zza`, version `LpdvzsNf`). Its original Controlify 1.6.0+1.20 embeds Intel-only hid4java 0.7.0, so ARM preparation rejected it before signing. Disabling that optional mod through the Mods UI allowed startup; the user confirmed the title screen worked with no dylib popup. This is compatibility for the adjusted pack, not the original unmodified pack. Exact mod files are in [modrinth-pack-files.json](reports/modrinth-pack-files.json).

## Interactive acceptance outcome

| Check | Result / evidence |
|---|---|
| Vanilla create, prepare, launch, window/title screen | Passed through normal UI; automatic Java download and native review exercised |
| Vanilla world create, save, exit, reopen | Passed; user confirmation, save files and logs |
| Representative modded world and audible audio | Fabric 1.20.1 passed; user confirmation, save files and logs |
| Runnable JAR with manifest dependency | Wizard import and launch passed, original dependency loaded, graphics initialized, exit 0 |
| Minecraft replacement import | Official 1.20.1 JAR imported through wizard, original hash preserved, title screen/no dylib popup confirmed |
| Local modpack import | Authored Modrinth pack, metadata and override preserved, Java 17 downloaded, graphics/audio initialized |
| Modrinth catalog download | Fabulously Optimized 5.2.8 downloaded; adjusted pack title screen passed after disabling unsupported Controlify |
| Native review cancellation | No signing or publication before approval |
| Copy isolation and transient state | Fresh UUID/bundle identity; no old control directory; links disabled in UI |
| Blocked network mode through UI | Signed helper has neither network entitlement; FIFO/output/client completion/exit 0 passed; Java/JNI denial covered by isolation reports |
| Stop, subsequent launches, console and status | Exercised through UI; intentional stop recorded, Exited state and policy controls verified |
| Supported native dylib loading | No dylib popup in user-observed replacement and adjusted Modrinth runs |
| Final manager package | Strict signature verification, repaired Cocoa plugin, updater hidden, normal UI exercised |
| Authenticated launch and multiplayer allowed/blocked | **Skipped at user request; unverified** |

Detailed identities, generations, hashes and outcomes are in [interactive-acceptance.json](reports/interactive-acceptance.json). The final package also shows the Intel-native mismatch directly in its console before signing; the optional Controlify mod was returned to disabled afterwards. CTest passed 25/25, backend tests 28/28, and the native Qt regression 3/3 after the final changes. The manager remains a development build with the limitations below.

## Remaining acceptance and limitations

- Vanilla single-player save/reopen passed. Fabric 1.20.1 world creation/save/reopen and audible audio also passed by user confirmation with save/log corroboration. Real multiplayer and authenticated account checks were skipped at the user’s request and remain unverified. No account or credentials were invented or imported from stock Prism.
- Custom runnable JAR import, offline settings, and automatic Java download passed through the UI. Local Modrinth-format pack import and a real Modrinth catalog download passed. The official 1.20.1 client also passed replacement import through the wizard, preserved its original SHA-256, and reached its title screen without a dylib popup (user confirmed).
- Full-disk, interrupted download/signing, cancellation at every UI stage, malformed helper messages, fabricated PIDs and every remaining legacy file reader have not all been tested end to end. The focused safety tests and OS process-identity checks are implemented; a complete adversarial audit remains necessary before treating the fork as hardened for unknown clients.
- The supported native adapters cover the tested LWJGL/JNA versions, pinned WebLite bridge 1.0.0/1.1 artifacts, and pinned LZ4 Java 1.8.0/1.10.1 artifacts. Native-bearing class JARs with no recognized loader adapter fail before signing or launch. Unknown self-extracting proprietary loaders, external launcher requirements, conflicting native names and mixed required architectures remain compatibility errors. There is no arbitrary bytecode transformer. Microphone-dependent mods are outside this version.
- Automatic architecture switching for arbitrary custom loaders is not complete; select an Intel runtime when the diagnostic identifies Intel-only dependencies. Additional runtime majors can use the managed catalog/import workflow but are not all tested.
- A native-bearing archive is fingerprinted as an archive, so changing its Java classes can require renewed native review even when embedded Mach-O bytes are unchanged. Pure Java-only artifacts do not enter native approval.
- Root relocation has a CLI, not a UI. Absolute runtime paths inside a moved root need re-selection. Cleanup retains uncertain/stale active records conservatively and does not delete instance files, saves, containers or input caches.
- The helper can continue after manager exit and the restarted console tails its redacted log. It does not reconnect a broken FIFO or recover a live configuration/session exchange.
- App Sandbox does not provide resource quotas or protect an instance from its own client destroying its worlds. An authenticated client necessarily receives its selected game session.

## Reproduce

Build/package from the repository root:

```sh
PRISM_SANDBOX_BUILD_TYPE=Debug bash scripts/build-macos-sandbox-manager.sh
ctest --test-dir build-macos-sandbox-manager --output-on-failure
/usr/bin/python3 -I -S tests/macos-sandbox/test_manager.py
```

The local build/cache directories retain detailed logs and inputs. Acceptance roots and generated game apps are recorded in the JSON reports. Tests intentionally retain instance data and macOS containers. The smoke and lifecycle scripts require already approved prepared profiles; they never create approvals. New or changed native inventories must return through review.
