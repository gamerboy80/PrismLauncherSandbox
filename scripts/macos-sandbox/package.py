#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Relocate and sign the trusted manager application, independently of game/native approvals."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

spec = importlib.util.spec_from_file_location("sandbox_manager", Path(__file__).with_name("manager.py"))
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


def tool(args):
    return subprocess.check_output([str(a) for a in args], stderr=subprocess.STDOUT).decode(errors="replace")


def macho(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return bool(manager.mach_architectures(stream.read(2048)))


def dependencies(path):
    return [line.strip().split(" (compatibility", 1)[0] for line in tool(["/usr/bin/otool", "-L", path]).splitlines()[1:]
            if line.startswith("\t")]


def identifier(path):
    lines = tool(["/usr/bin/otool", "-D", path]).splitlines()
    return lines[1].strip() if len(lines) > 1 and not lines[1].endswith(":") else None


def package(app, cocoa_plugin=None):
    app = app.resolve(strict=True)
    if cocoa_plugin:
        shutil.copyfile(cocoa_plugin, app / "Contents/PlugIns/platforms/libqcocoa.dylib")
    obsolete_jars = app / "Contents/MacOS/jars"
    if obsolete_jars.is_dir() and (app / "Contents/Resources/jars").is_dir():
        shutil.rmtree(obsolete_jars)  # Generated artifacts left by an older install layout, never user data.
    frameworks = app / "Contents/Frameworks"
    frameworks.mkdir(exist_ok=True)
    pending = [p for p in app.rglob("*") if macho(p)]
    processed = set()
    copied = {}
    while pending:
        binary = pending.pop()
        if binary in processed:
            continue
        processed.add(binary)
        own_id = identifier(binary)
        for dependency in dependencies(binary):
            if dependency.startswith(("/usr/lib/", "/System/Library/", "@")):
                continue
            original = Path(dependency)
            if not original.is_absolute() or not original.exists():
                raise RuntimeError("Unresolved dependency: " + dependency)
            if ".framework" in dependency:
                parts = original.parts
                start = next(i for i, part in enumerate(parts) if part.endswith(".framework"))
                target = frameworks.joinpath(*parts[start:])
                if not target.exists():
                    shutil.copytree(Path(*parts[:start + 1]), frameworks / parts[start], symlinks=True)
                    pending.extend(p for p in (frameworks / parts[start]).rglob("*") if macho(p))
            else:
                target = frameworks / original.name
            if dependency == own_id:
                replacement = "@rpath/" + str(binary.relative_to(frameworks)) if binary.is_relative_to(frameworks) else "@rpath/" + binary.name
                tool(["/usr/bin/install_name_tool", "-id", replacement, binary])
                continue
            if not target.exists():
                shutil.copy2(original.resolve(), target)
                copied[str(original)] = str(target.relative_to(app))
                tool(["/usr/bin/install_name_tool", "-id", "@rpath/" + target.name, target])
                pending.append(target)
            relative = os.path.relpath(target, binary.parent)
            tool(["/usr/bin/install_name_tool", "-change", dependency, "@loader_path/" + relative, binary])
    # Relocation invalidates vendor signatures. Sign individual files before enclosing frameworks/apps.
    for binary in sorted((p for p in app.rglob("*") if macho(p)), key=lambda p: len(p.parts), reverse=True):
        tool(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", binary])
    for framework in sorted(app.rglob("*.framework"), key=lambda p: len(p.parts), reverse=True):
        if not framework.is_symlink():
            tool(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", framework])
    entitlements = app.parent / "manager.entitlements"
    with entitlements.open("wb") as stream:
        plistlib.dump({"com.apple.security.cs.disable-library-validation": True}, stream)
    tool(["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", "--timestamp=none",
          "--entitlements", entitlements, app])
    tool(["/usr/bin/codesign", "--verify", "--deep", "--strict", app])
    violations = []
    for binary in (p for p in app.rglob("*") if macho(p)):
        for dependency in dependencies(binary):
            if dependency.startswith("/") and not dependency.startswith(("/usr/lib/", "/System/Library/")):
                violations.append({"file": str(binary.relative_to(app)), "dependency": dependency})
    if violations:
        raise RuntimeError(json.dumps(violations))
    report = {"version": 1, "app": str(app), "signatureVerified": True, "externalDependencies": violations,
              "relocatedLibraries": copied, "additionalSystemPackagesInstalled": [],
              "initializedSubmodules": {"libraries/libnbtplusplus": "3538933614059f0f44388a2b16f3db25ce42285b"},
              "qtAccessibilityFix": "QTBUG-149612" if cocoa_plugin else None}
    (app.parent / "manager-package-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--cocoa-plugin", type=Path)
    args = parser.parse_args()
    package(args.app, args.cocoa_plugin)
