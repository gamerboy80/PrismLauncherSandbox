# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location("sandbox_manager", Path(__file__).parents[2] / "scripts/macos-sandbox/manager.py")
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


def fake_macho(architecture=0x0100000C):
    return b"\xcf\xfa\xed\xfe" + struct.pack("<I", architecture) + b"\0" * 80


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "instances/first").mkdir(parents=True)
        (self.root / "instances/second").mkdir()
        self.registry = manager.SandboxInstanceRegistry(self.root)
        self.instance = self.registry.register(self.root / "instances/first")
        self.runtime = self.root / "test-runtime"
        (self.runtime / "bin").mkdir(parents=True)
        (self.runtime / "bin/java").write_bytes(fake_macho())
        (self.runtime / "bin/java").chmod(0o755)
        (self.runtime / "release").write_text('JAVA_VERSION="21.0.1"\n')
        self.helper = self.root / "helper"
        self.helper.write_bytes(fake_macho())

    def tearDown(self):
        self.temporary.cleanup()

    def plan(self, archives=()):
        return manager.SandboxArtifactPlanner(self.registry).inspect(self.instance["id"], self.runtime, archives,
                                                                   helper=self.helper)

    def archive(self, name, entries):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as z:
            for entry, contents in entries.items():
                z.writestr(entry, contents)
        return path

    def test_instance_identities_and_authoritative_policy(self):
        first = self.registry.register(self.root / "instances/first")
        second = self.registry.register(self.root / "instances/second", custom=True)
        self.assertEqual(first["id"], self.instance["id"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["network"], "allowed")
        self.assertEqual(second["account"], "offline")
        (self.root / "instances/second/instance.cfg").write_text("SandboxNetwork=blocked\nSandboxAccount=selected\n")
        self.assertEqual(self.registry.get(second["id"])["account"], "offline")

    def test_manager_root_is_normalized_before_authority_checks(self):
        registry = manager.SandboxInstanceRegistry(self.root / "manager/..")
        self.assertEqual(registry.root, self.root)
        self.assertEqual(registry.private, self.root / "manager")

    def test_reject_external_and_linked_instance_roots(self):
        with self.assertRaises(manager.SandboxError):
            self.registry.register(self.runtime)
        (self.root / "instances/link").symlink_to(self.runtime)
        with self.assertRaises(manager.SandboxError):
            self.registry.register(self.root / "instances/link")

    def test_identity_collision_cannot_reset_private_network_or_account_policy(self):
        identity = "a" * 32
        directory = self.root / "instances" / identity
        directory.mkdir()
        value = self.registry.register(directory, custom=True)
        value["network"] = "blocked"
        value["instanceRoot"] = str(self.root / "old-location" / identity)
        self.registry.save()
        with self.assertRaisesRegex(manager.SandboxError, "identity"):
            self.registry.register(directory)
        self.assertEqual(self.registry.data["instances"][identity]["network"], "blocked")
        self.assertEqual(self.registry.data["instances"][identity]["account"], "offline")

    def test_directory_replacement_is_detected_before_preparation(self):
        directory = self.root / "instances/first"
        directory.rename(self.root / "instances/old-first")
        directory.mkdir()
        with self.assertRaisesRegex(manager.SandboxError, "identity"):
            self.registry.get(self.instance["id"])

    def test_private_root_catalog_rejects_game_writable_and_bundle_locations(self):
        anchor = self.root / "trusted-catalog"
        manager.validate_manager_root(self.root, anchor)
        for path in (self.root / "instances/first/forged-manager", self.root / "apps/game/forged-manager",
                     self.root / "INSTANCES/first/forged-manager", self.root / "Game.APP/private-manager"):
            with self.subTest(path=path), self.assertRaises(manager.SandboxError):
                manager.validate_manager_root(path, anchor)

    def test_cleanup_retains_saves_current_previous_and_active_generations(self):
        import time
        directory = self.root / "apps" / self.instance["id"]
        directory.mkdir(parents=True)
        for i in range(4):
            generation = directory / (str(i) * 24)
            generation.mkdir()
            os.utime(generation, (time.time() - (20-i)*86400,) * 2)
        self.registry.data["instances"][self.instance["id"]]["generation"] = "3" * 24
        sentinel = self.root / "instances/first/world-save"
        sentinel.write_text("keep")
        preview = manager.collect_unused(self.registry)
        self.assertEqual(len(preview["candidates"]), 2)
        self.assertTrue(all(Path(p).exists() for p in preview["candidates"]))
        active = self.registry.private / "active"
        active.mkdir()
        manager.atomic_json(active / (self.instance["id"] + ".json"), {"version": 1})
        self.assertEqual(manager.collect_unused(self.registry)["candidates"], [])
        (active / (self.instance["id"] + ".json")).unlink()
        manager.collect_unused(self.registry, apply=True)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertTrue((directory / ("3"*24)).exists())
        self.assertTrue((directory / ("2"*24)).exists())

    def test_java_class_is_not_fat_macho(self):
        self.assertEqual(manager.mach_architectures(bytes.fromhex("cafebabe0000004101000000")), [])
        self.assertEqual(manager.mach_architectures(fake_macho()), ["arm64"])
        self.assertEqual(manager.mach_architectures(fake_macho(0x01000007)), ["x86_64"])

    def test_no_approval_created_by_inspection(self):
        plan = self.plan()
        self.assertFalse(manager.SandboxApprovalStore(self.registry).approved(plan))
        self.assertFalse((Path(plan["transaction"]) / "build").exists())

    def test_exact_approval_and_invalidation(self):
        plan = self.plan()
        approvals = manager.SandboxApprovalStore(self.registry)
        with self.assertRaises(manager.SandboxError):
            approvals.approve(plan, "not-the-reviewed-hash")
        approvals.approve(plan, plan["approvalFingerprint"])
        self.assertTrue(approvals.approved(plan))
        (self.runtime / "bin/java").write_bytes(fake_macho() + b"new native bytes")
        changed = self.plan()
        self.assertFalse(approvals.approved(changed))

    def test_approval_reuse_does_not_require_bytecode_approval(self):
        jar = self.archive("client.jar", {"Client.class": b"first"})
        plan = self.plan([jar])
        approvals = manager.SandboxApprovalStore(self.registry)
        approvals.approve(plan, plan["approvalFingerprint"])
        jar.unlink()
        self.archive("client.jar", {"Client.class": b"changed bytecode"})
        self.assertTrue(approvals.approved(self.plan([jar])))

    def test_review_records_parent_jar_and_native_bytes(self):
        jar = self.archive("jna-5.13.0.jar", {"Client.class": b"class", "macos/libexample.dylib": fake_macho()})
        plan = self.plan([jar])
        native = plan["review"]["nativeFiles"][0]
        self.assertEqual(native["entry"], "macos/libexample.dylib")
        self.assertEqual(native["source"], str(jar))
        self.assertTrue(plan["artifacts"][0]["hasClasses"])

    def test_unknown_packaged_native_loader_stops_before_signing(self):
        jar = self.archive("unknown-client.jar", {"Client.class": b"class", "libexample.dylib": fake_macho()})
        with self.assertRaisesRegex(manager.SandboxError, "Unsupported native loading mechanism.*unknown-client.jar"):
            self.plan([jar])
        self.assertFalse(any((self.root / "staging").glob("inventory-*/build")))
        self.assertFalse(any((self.root / "manager/approvals").glob("*.json")))

    def test_native_module_descriptor_does_not_prevent_resource_only_detection(self):
        jar = self.archive("lwjgl-native.jar", {"META-INF/versions/9/module-info.class": b"module descriptor",
                                               "libexample.dylib": fake_macho()})
        plan = self.plan([jar])
        self.assertFalse(plan["artifacts"][0]["hasClasses"])
        self.assertTrue(plan["artifacts"][0]["hasNatives"])

    def test_archive_traversal_and_links_rejected(self):
        for name in ("../escape", "/absolute", "a/../../escape", "a\\escape", "a//escape"):
            with self.subTest(name=name):
                jar = self.archive("unsafe.jar", {name: b"bad"})
                with self.assertRaises(manager.SandboxError):
                    self.plan([jar])
                jar.unlink()
        jar = self.root / "link.jar"
        with zipfile.ZipFile(jar, "w") as z:
            info = zipfile.ZipInfo("link")
            info.external_attr = 0o120777 << 16
            z.writestr(info, "../../outside")
        with self.assertRaises(manager.SandboxError):
            self.plan([jar])

    def test_conflicting_native_names_rejected(self):
        jar = self.archive("collision.jar", {"a/libbad.dylib": fake_macho(), "b/libbad.dylib": fake_macho() + b"other"})
        with self.assertRaisesRegex(manager.SandboxError, "Ambiguous"):
            self.plan([jar])

    def test_incompatible_required_native_architecture_fails_before_signing(self):
        jar = self.archive("intel-only-client.jar", {"native/libclient.dylib": fake_macho(0x01000007)})
        with self.assertRaisesRegex(manager.SandboxError, "architecture mismatch"):
            self.plan([jar])

    def test_runtime_escaping_link_rejected_internal_link_preserved(self):
        (self.runtime / "escape").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(manager.SandboxError, "escapes"):
            self.plan()
        (self.runtime / "escape").unlink()
        (self.runtime / "java-link").symlink_to("bin/java")
        plan = self.plan()
        self.assertTrue((Path(plan["transaction"]) / "runtime/java-link").is_symlink())

    def test_snapshot_stays_unchanged_after_source_changes(self):
        plan = self.plan()
        original = (Path(plan["transaction"]) / "runtime/bin/java").read_bytes()
        (self.runtime / "bin/java").write_bytes(b"mutated")
        self.assertEqual((Path(plan["transaction"]) / "runtime/bin/java").read_bytes(), original)

    def test_source_parent_symlink_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.jar").write_bytes(b"private")
        (self.root / "instances/first/linked").symlink_to(outside)
        with self.assertRaises(OSError):
            with manager.open_regular(self.root / "instances/first/linked/private.jar"):
                self.fail("Source parent symlink was followed")

    def test_manifest_launch_and_local_dependencies(self):
        dependency = self.archive("dependency.jar", {"Dependency.class": b"class"})
        jar = self.archive("client.jar", {"META-INF/MANIFEST.MF":
                           "Manifest-Version: 1.0\r\nMain-Class: example.Client\r\nClass-Path: dependency.jar\r\n\r\n"})
        result = manager.inspect_client(jar)
        self.assertEqual(result["mainClass"], "example.Client")
        self.assertEqual(result["dependencies"][0]["source"], str(dependency))
        dependency.unlink()
        with self.assertRaises((OSError, manager.SandboxError)):
            manager.inspect_client(jar)

    def test_remote_manifest_dependency_is_rejected(self):
        jar = self.archive("client.jar", {"META-INF/MANIFEST.MF": "Main-Class: Client\nClass-Path: https://example.invalid/client.jar\n"})
        with self.assertRaises(manager.SandboxError):
            manager.inspect_client(jar)

    def test_volume_alias_cannot_hide_an_instance_grant_from_root_validation(self):
        from unittest.mock import patch
        anchor = self.root / "catalog"
        manager.validate_manager_root(self.root, catalog_anchor=anchor)
        alias = self.root / "volume-alias"
        (alias / "first").mkdir(parents=True)
        original = Path.lstat
        same_directory = (self.root / "instances").lstat()
        with patch.object(Path, "lstat", lambda path: same_directory if path == alias else original(path)):
            with self.assertRaisesRegex(manager.SandboxError, "instance grant"):
                manager.validate_manager_root(alias / "first/new-manager", catalog_anchor=anchor)
        self.assertFalse((alias / "first/new-manager").exists())

    def test_relocating_root_preserves_isolation_identity_and_policy(self):
        import shutil
        moved = self.root / "relocated"
        (moved / "manager").mkdir(parents=True)
        shutil.copytree(self.root / "instances", moved / "instances")
        self.instance["network"] = "blocked"
        self.instance["generation"] = "old-generation"
        self.registry.save()
        shutil.copyfile(self.registry.path, moved / "manager/registry.json")
        registry = manager.SandboxInstanceRegistry(moved)
        manager.relocate_registry(registry, self.root)
        value = registry.get(self.instance["id"])
        self.assertEqual(value["network"], "blocked")
        self.assertEqual(value["bundleID"], self.instance["bundleID"])
        self.assertIsNone(value["generation"])
        self.assertEqual(value["instanceRoot"], str(moved / "instances/first"))
        self.assertEqual((self.root / "instances/first").is_dir(), True)
        with self.assertRaises(manager.SandboxError):
            manager.relocate_registry(registry, self.root)

    def test_native_load_paths_cannot_resolve_to_writable_directories(self):
        app = self.root / "Game.app"
        binary = app / "Contents/Runtime/lib/libfixture.dylib"
        executable = app / "Contents/Runtime/bin/java"
        for value in ("@loader_path/../lib", "@executable_path/../lib", "/usr/lib/libSystem.B.dylib", "@rpath/libfixture.dylib"):
            manager.validate_load_path(value, binary, app, executable)
        for value in ("@loader_path/../../../../outside", "/tmp/libfixture.dylib", "libfixture.dylib", "@rpath/../escape", "/System/Library/../../Volumes/Data/game/libfixture.dylib"):
            with self.assertRaises(manager.SandboxError):
                manager.validate_load_path(value, binary, app, executable)
        with self.assertRaises(manager.SandboxError):
            manager.validate_load_path("@rpath/unknown", binary, app, executable, rpath=True)

    def test_large_signed_manifest_only_reads_main_section(self):
        jar = self.archive("signed-client.jar", {"META-INF/MANIFEST.MF": b"Main-Class: Authored\r\n\r\n" + b"Name: entry\r\nSHA-256-Digest: hash\r\n\r\n" * 50000})
        self.assertEqual(manager.inspect_client(jar)["mainClass"], "Authored")
        invalid = self.archive("invalid-manifest.jar", {"META-INF/MANIFEST.MF": b"Main-Class: " + b"x" * (1024 * 1024)})
        with self.assertRaisesRegex(manager.SandboxError, "main section"):
            manager.inspect_client(invalid)

    def test_versioned_custom_client_spec_preserves_arguments_and_identity(self):
        client = self.archive("client.jar", {"META-INF/MANIFEST.MF": b"Main-Class: Authored\nClass-Path: dependency.jar\n"})
        dependency = self.archive("dependency.jar", {"Authored.class": b"bytecode"})
        instance = self.root / "instances/first"
        import shutil
        shutil.copyfile(client, instance / "client.jar")
        shutil.copyfile(dependency, instance / "dependency.jar")
        supplied = {"version": 1, "launchKind": "StandaloneJar", "jarPath": "client.jar",
                    "applicationArguments": ["one argument", "${auth_access_token}"], "jvmArguments": ["-Xmx512m"]}
        result = manager.validate_client_spec(instance, supplied)
        self.assertEqual(result["entryMode"], "Manifest")
        self.assertEqual(result["entryPoint"], "Authored")
        self.assertEqual(result["dependencies"][0]["path"], "dependency.jar")
        self.assertEqual(result["applicationArguments"], supplied["applicationArguments"])
        self.assertEqual(result["jarSHA256"], manager.file_hash(client))
        for change in ({"version": 99}, {"jarPath": "../client.jar"}, {"applicationArguments": "shell string"}):
            with self.assertRaises(manager.SandboxError):
                manager.validate_client_spec(instance, dict(supplied, **change))

    def test_unsupported_schema_and_external_plan_rejected(self):
        record = self.root / "record.json"
        record.write_text('{"version":99}')
        with self.assertRaises(manager.SandboxError):
            manager.read_json(record)
        with self.assertRaises(manager.SandboxError):
            manager.SandboxBundleBuilder(self.registry).load_plan(record)


if __name__ == "__main__":
    unittest.main()
