# Records and launch protocol, version 1

All persisted backend JSON objects have an integer `version: 1`. Unsupported record versions fail closed. Writes use temporary files, fsync and atomic replacement. Authoritative records are outside instance-writable directories.

| Location | Record |
|---|---|
| `manager/registry.json` | `instances` keyed by UUID: identity, display name, canonical instance root, bundle identifier, network/account policy, published generation, launch kind, selected runtime identity and directory device/inode |
| `manager/clients/<id>.json` | Versioned custom-client snapshot: kind, original JAR hash and relative path, entry point/mode, relative dependency paths/hashes, Minecraft base version when applicable, JVM/application argument arrays |
| `manager/status/<id>.json` | Preparation phase, owner PID, transaction/fingerprint, last generation, structured error and update time |
| `manager/approvals/<fingerprint>.json` | Exact reviewed inventory, fingerprint and approval timestamp |
| `staging/inventory-*/plan.json` | Runtime snapshot records, archive hashes, native inventory/destinations, adapters, checksum resources, trusted launcher/helper hashes and preparation fingerprint |
| `apps/<id>/<generation>/Game.app/Contents/Resources/bundle-manifest.json` | Sealed instance identity, exact grant, network policy, architecture, Java path, approval fingerprint, signed output hashes and adapter requirements |
| `reports/<id>-bundle.json` | Published app location, manifest and resource-only native archives to exclude |
| `manager/active/<id>.json` | Run/generation and independently observed helper identity: executable path, user-verified PID and start timestamp, plus the independently observed primary JVM identity |
| `instances/<id>/.prism-control/<run>/exit.json` | Instance-local observation of exit code, signal, intentional stop and generation; never authority for manager operations |

Client settings currently use the existing INI infrastructure: `SandboxStandalone`, `SandboxCustom`, `SandboxClientJar`, `SandboxMainClass`, `SandboxClientArguments`, and `SandboxJavaMajor`. These do not override private identity, approvals, network or account policy. The backend writes a versioned `manager/clients/<id>.json` snapshot during preparation. Its argument arrays contain configured values before account substitution, not the in-memory session. The actual launch is resolved afresh; these snapshots do not authorize policy changes.

## Transport

The manager creates two owner-only FIFOs under the instance:

```text
.prism-control/<run-uuid>/to-helper
.prism-control/<run-uuid>/to-manager
```

Both sides use no-follow, descriptor-relative operations and validate FIFO type, owner and permissions. LaunchServices receives only the run UUID. The host finds the canonical instance root in its signed manifest and validates its signature and entitlement profile before opening the channel.

Each newline-delimited JSON envelope contains:

```json
{"version":1,"run":"<uuid>","seq":1,"type":"Ping","payload":{}}
```

Sequence numbers increase independently in each direction. Frames must be smaller than 64 KiB. Output is base64-encoded in 4096-byte chunks. The host bounds queued output to 1 MiB and drops complete log frames under pressure. It buffers complete UTF-8 console lines for redaction, omits lines over 64 KiB, and limits its durable console log to 64 MiB.

Manager messages: `Configure`, `Proceed`, `Stop`, `Ping`. Large configuration uses `ConfigureStart` with byte length, ordered base64 `ConfigureChunk` messages, and `ConfigureEnd`; the reassembled JSON is limited to 4 MiB.

Host messages: `Hello`, `Ready`, `Started`, `Stdout`, `Stderr`, `Exited`, `Error`, `Pong`.

The Configure payload requires integer `version: 1` and includes `generation`, argument arrays, a working-directory reference, environment dictionary, optional launch script and in-memory private values to redact. Path references are `{ "root": "instance" | "bundle", "path": "relative/path" }`. Absolute/escaping path references are rejected. Normal JVM launch always uses the sealed manifest's runtime. Command mode additionally validates Apple system executables or prepared bundle executables.

The host runs JavaCheck inside the sandbox before `Ready`. `Proceed` releases the game launch. `Started` supplies a PID for display; it does not authorize arbitrary signaling. Exit status, terminating signal and intentional stop are distinct fields.

Session data remains in FIFO messages and memory. The client necessarily receives session fields selected for its launch. The host redacts known session values before forwarding or persisting console lines. It never receives refresh tokens.

No helper message triggers manager-side file writes, downloads, signing, shell execution or policy changes. Errors reject unsupported schemas/protocols, generation mismatches, invalid states and invalid launch descriptions explicitly.

## Preparation and recovery

The current planner format is `plannerVersion: 3`; an older staged plan must be inspected again. This planner version is independent of the native approval fingerprint, so an unchanged exact reviewed inventory can be reused after first-party preparation fixes.

Journal phases include Unprepared, Resolving, AwaitingApproval, Preparing, Ready, Starting, Running, Exited, Cancelled and Failed. Failures retain the prior valid generation. Root relocation resets generation references and records Unprepared without changing bundle identities or account/network policy.

The OS-home root catalog is versioned JSON at the default root's `manager/root-catalog.json`, protected by a private file lock. `--root` cannot override that catalog's location. It records known manager roots to prevent later selecting an instance-writable directory as manager authority.

The helper bounds the number of FIFO reads per event-loop iteration so continuous input cannot starve process supervision. On child exit, it retains the unreaped process-group leader while terminating remaining group members, escalating after ten seconds, then reports exit. Primary process records for supported wrappers/commands are accepted only after matching the manager-selected executable, helper parent, UID and OS start timestamp.
