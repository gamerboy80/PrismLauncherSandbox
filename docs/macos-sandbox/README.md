# Local macOS sandbox manager

This opt-in fork keeps the Qt manager unsandboxed and launches each managed game in its own independently sandboxed macOS application. It uses a distinct bundle identifier and data root. It does not migrate the stock Prism account database or worlds.

The implementation is under active acceptance testing. See [verification.md](verification.md) for demonstrated behavior and remaining acceptance work. Successful isolation probes are not malware analysis or proof against every possible sandbox escape.

The final local delivery is `../PrismLauncher.app` beside this source checkout. Generated build trees and duplicate installs were removed after acceptance; the commands below recreate them. Preserved test summaries are in [final-checks.txt](reports/final-checks.txt).

## Build and run

From the repository root:

```sh
bash scripts/build-macos-sandbox-manager.sh
open install-macos-sandbox-manager/PrismLauncher.app
```

For the verified Debug build, set `PRISM_SANDBOX_BUILD_TYPE=Debug`; the default is RelWithDebInfo. The script uses existing Homebrew dependencies, CMake, Ninja, Apple Command Line Tools and JDK 21. It initializes only the required pinned `libnbtplusplus` submodule if absent. It builds the universal ARM64/x86_64 native host and Java 8-compatible launcher JARs, installs Qt dependencies, relocates remaining library references and ad hoc signs the manager. With the installed Qt 6.11.2, it also builds a workspace-local Cocoa plugin carrying the upstream QTBUG-149612 accessibility fix; system Qt is unchanged. The final package report is `install-macos-sandbox-manager/manager-package-report.json`.

The feature flag is `-DLauncher_MACOS_SANDBOX_MANAGER=ON`. It fails configuration on non-macOS targets. Builds without the flag retain the ordinary launch backend. The fork disables the upstream automatic updater. Rebuild this fork to update it.

The default root is:

```text
~/Library/Application Support/PrismLauncherSandboxManager/
  manager/       settings, accounts, registry, approvals, active-run records
  cache/         preparation/download caches
  instances/     one independently writable folder per instance
  apps/          immutable, signed application generations
  staging/       inspection and build transactions
  reports/       preparation reports
```

`--dir /absolute/path` selects a manager root; the manager stores its own settings in that root's `manager/` subdirectory. Prefer Application Support or another ordinary application-data location. macOS privacy checks on Documents, Desktop and Downloads can prevent helper startup. The host reports a startup timeout rather than requesting broader sandbox grants.

Moving a populated root requires explicit registry relocation and new signed bundles: the grants contain absolute paths. A conflicting registered identity is rejected rather than silently resetting its network or account policy. After closing the manager and stopping all instances, copy or move the complete root, then run:

```sh
/usr/bin/python3 -I -S scripts/macos-sandbox/manager.py --root /new/root relocate --previous-root /old/root
```

This preserves instance identities and policy, backs up the registry, and clears generation references so the next Play builds new grants. Re-select a runtime if its configured absolute path was inside the old root. A root containing active-run records is refused until those runs are reconciled. There is no relocation UI.

The manager validates its root before opening settings. A private catalog anchored to the OS account's Application Support directory rejects roots within any known game's files, containers, generated apps, stock Prism data, or shared system directories.

## Everyday workflow

1. Create a Minecraft instance using the normal version/loader picker, or import a modpack/client.
2. Let Prism select/download Java, or choose a complete runtime in Java settings.
3. Click **Play**. Dependency resolution, local file preparation, native inspection, bundle construction, signing and sandboxed launch run automatically.
4. If the native preparation inventory is new, review it and choose **Approve and prepare**. Later identical inventories reuse that approval. Cancelling does not launch or sign that proposal.
5. Set networking/account policy on the instance's **Sandbox and client** page. The manager never falls back to an unsandboxed game launch.

Downloading Java alone does not run or sign it. Preparation happens when Play assembles the runtime and instance's native dependencies together. A changed Java download, native mod or adapter may need a new review. This is separate from macOS's unsigned-dylib dialog; supported loaders use already prepared signed files.

## Create and import

Normal Minecraft creation, Modrinth and local instance/modpack imports use the managed instances directory. External instance layouts and links escaping the instance are refused. Copying an instance assigns a new folder UUID and sandbox identity. Renaming its display name preserves identity.

Use **Add Instance → Import client JAR** for a Minecraft replacement or a standalone Java client. A replacement requires its Minecraft base version. Standalone clients use their manifest's `Main-Class`, or an explicit main class. The import reads JAR metadata without executing it and copies the original bytes and local manifest dependencies.

In **Edit Instance → Sandbox and client**:

- Choose **Allowed** or **Blocked** networking. Stop the run before changing this setting.
- Choose **Offline** or **Selected Minecraft account**. Custom imports default to offline. Selecting an account requires an acknowledgement that the client receives that game session.
- Override the main class and client arguments when required.
- Import additional Java libraries and explicit native libraries/executables.

Additional libraries are under `client/libraries`. Standalone manifest mode retains ordinary `java -jar` semantics; set a main class to include additional libraries on an explicit classpath. Minecraft replacements include imported additional libraries in their classpath.

Use the ordinary Java settings to choose a runtime and JVM arguments. Manual Java paths enter inspection and preparation; they are not executed in the manager. Choose an Intel runtime for Intel-only clients. Required native architecture mismatches fail before signing. Automatic inference and switching for every proprietary custom loader is not provided.

Standalone clients receive no authenticated Minecraft session automatically. For an explicitly selected account, arguments can map `${auth_access_token}`, `${auth_player_name}` and `${auth_uuid}`. No refresh token is placed in the instance, bundle or launch descriptor.

Explicit agents use absolute paths to imported files. Prepared explicit natives can be referenced using `${sandbox_natives}` and the instance using `${instance_directory}`. Pre/post commands and wrappers run through the sandbox host. Apple system tools and prepared bundle executables are supported; external executable paths are refused.

CurseForge's upstream API key is disabled. A derivative key can be supplied with `Launcher_MACOS_SANDBOX_CURSEFORGE_API_KEY`; import formats remain available without that API.

## Native review

The launch pipeline resolves and downloads inputs, materializes instance-local libraries/assets, snapshots the runtime and inventories Mach-O files. It does not run the input JVM while inspecting it.

Click Play to perform preparation automatically; no manual signing commands are needed. New native preparation fingerprints open a review with parent artifacts, source/import paths, original SHA-256 hashes, architectures, adapter requirements and signing profile. **Approve and prepare**, **Cancel**, and **Export inventory** are separate actions. Cancel does not sign the proposed native set.

An approval applies only to identical input bytes, origins, architecture, adapter requirements and signing profile. Pure Java bytecode changes do not alone invalidate a native approval. New natives downloaded by a running game must return through a stopped preparation/review cycle; helper messages cannot authorize manager downloads or signing.

Supported preparation uses LWJGL library paths, JNA's non-extraction configuration, pinned LZ4 library-path loading, and a bounded Objective-C bridge shim for pinned artifacts. Unknown native-bearing class JARs stop with a diagnostic before preparation. Resource-only native JARs are removed from the classpath; Java class JARs and custom clients remain unchanged. `module-info.class` is treated as a module descriptor, not an application class. Generated checksum resources describe signed native bytes and follow class JARs in resource enumeration order.

A dylib warning for a path in a game's container temporary directory means a loader used an extraction path. Do not clear quarantine or approve that temporary file to work around it. Stop the instance and export the console/inventory so the specific loader can be adapted. The prepared natives belong under the sealed `Game.app/Contents/Resources/natives/` directory.

## Running, recovery and cleanup

Each instance permits one active run. Separate instances can run concurrently. The host owns a supervised process group; stop sends termination, then escalates after ten seconds. The manager verifies helper executable path, user, PID and start time before signaling a detached run.

Closing the manager can leave a game running. Restart reconciles active helper identities and allows stopping verified detached runs. The console tails the verified run's redacted log after restart; the host also keeps `console.log` and `exit.json` in `.prism-control/<run-uuid>/`. Instance **Other Logs** includes that location. This reconnects logging and stop controls; it does not replay session configuration or recover a broken FIFO channel.

Bundles publish by atomic rename into a new generation. Failed preparation keeps the prior valid generation. Never manually modify a running generation. A private preparation journal records progress and failures. Startup cleanup recovers abandoned preparation states and removes unreferenced staging/generations after seven days. It retains active records conservatively, the current generation, the two newest generations, input caches, all instance files and containers. A stale private active record can retain old generations until reconciled. Run `gc` for a dry-run report, or `gc --apply` to apply cleanup. Cleanup reports are in `reports/cleanup.json`.

Instance deletes follow the existing confirmation flow. Managed trash operations move entries using descriptor-relative operations into `manager/trash/`; this is separate from Finder's Trash and supports manual recovery. Container data is retained. No cleanup operation automatically deletes worlds or macOS containers. Do not remove active bundles or records by hand.

## Diagnostics and records

See [protocol.md](protocol.md) for transport/record versions, [verification.md](verification.md) for test evidence, and [dependency-ledger.md](dependency-ledger.md) for the local tool/download ledger.

The backend is also a JSON CLI:

```sh
/usr/bin/python3 scripts/macos-sandbox/manager.py --help
```

Inspection, approval and building are separate commands. Do not add automatic `approve` calls to build or launch scripts. The compatibility harnesses download/inspect public inputs and stop at changed fingerprints; they do not approve them.
