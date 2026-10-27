# Extended authored network probe review

Status: explicitly approved by the user in this implementation chat on 2026-10-27.

Fingerprint: `50776391bccedf482dec1a2948c20d2b47a91b5171b64739519d9b91ddc3bc09`

The same installed Temurin 21 runtime bytes, origin and signing profile from the prior authored isolation fixture. Only the authored universal JNI dylib changed: it now tests direct POSIX DNS and listening sockets in addition to filesystem and outbound sockets. Original fixture binary is retained. No third-party native inputs or new signing exceptions.

Full input hashes and origins: [network-probe-inventory.json](network-probe-inventory.json). Approval permits signing copies and running both network modes.
