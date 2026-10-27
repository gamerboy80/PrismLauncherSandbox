# Resolved classpath inventory review

Status: explicitly approved by the user in this implementation chat. Later exact LZ4 and legacy adapter refinements are recorded in their separate review files.

The real Prism pipeline resolves a smaller classpath than the input harness for modern Minecraft. Adapter entries for unused classifier JARs were removed. Native bytes, origins, architecture and signing exceptions in the five modern profiles are unchanged. The exact-fingerprint rule correctly refused to reuse the earlier approvals.

Legacy Forge additionally resolves `java-objc-bridge-1.0.0-natives-osx.jar` (SHA-256 `b490a92062fc482c04f50395d7bbbd3dff22c32eaab45f04478f14d1501af6fa`). Its Intel `libjcocoa.dylib` is SHA-256 `1cc8865d7f4adb708d25be5d311f37136ad7b6bb07d6c06de1af84eeadd7537e`, 22,060 bytes. It comes from Minecraft’s official library repository (`https://libraries.minecraft.net/ca/weblite/java-objc-bridge/1.0.0/java-objc-bridge-1.0.0-natives-osx.jar`). No runtime or signing exceptions changed.

Full exact inventories are in [resolved-inventories.json](resolved-inventories.json).

| Profile | Exact resolved fingerprint |
|---|---|
| vanilla-26.3 | `34ddd4106072323918eb6f5b914f83479f24f5801300d3ae1943c821f79d6fba` |
| fabric-1.20.1 | `f533d2cfaa15b1c1cb6b46b91a385ad016762865a94fbf1c15e2bdc14cb17fbc` |
| forge-1.20.1 | `f533d2cfaa15b1c1cb6b46b91a385ad016762865a94fbf1c15e2bdc14cb17fbc` |
| fabric-1.21.1 | `454524b2ae332adfbcc8cf50df7b8a9a80799137c976dbfe7966cd5798ee2708` |
| neoforge-1.21.1 | `454524b2ae332adfbcc8cf50df7b8a9a80799137c976dbfe7966cd5798ee2708` |
| forge-1.12.2-intel | `fbd935eff8bbf6b44200ab019164c11298b2508e8429ee56b6cdad21518bf84d` |
