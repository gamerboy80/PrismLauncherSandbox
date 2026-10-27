# Legacy Objective-C adapter review

Status: explicitly approved by the user in this implementation chat on 2026-10-27.

Previous fingerprint: `fbd935eff8bbf6b44200ab019164c11298b2508e8429ee56b6cdad21518bf84d`

New fingerprint: `75f277edc80af34cafb10f5184e674fabb0c2a45212f6967d895429daad25195`

Only change: explicitly record the existing prepared Objective-C bridge adapter for exact Java bytecode artifact SHA-256 `fec83601d2da127234728a8fffb429d0c4a59587452834da203163e6a3f941db` (java-objc-bridge 1.0.0). Runtime/native input bytes, origins and signing exceptions are unchanged. The shim is now enabled only for fingerprinted supported bridge artifacts, instead of any LWJGL/JNA instance.

Full inventory: [legacy-adapter-inventory.json](legacy-adapter-inventory.json). Approval permits local signing and sandboxed offline legacy compatibility tests.
