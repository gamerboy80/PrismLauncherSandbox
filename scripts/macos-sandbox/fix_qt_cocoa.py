#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build a workspace-local Qt 6.11.2 Cocoa plugin with QTBUG-149612 repaired."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import urllib.request

SOURCES = (
    ("qtbase-v6.11.2", "qtbase-6.11.2", "qt/qtbase", "v6.11.2", "06cd7aa6b3ab19cb5a1664a2891ef0df960f78c97fa31b80b1d6d7ee70688414"),
    ("MoltenVK-v1.4.0", "MoltenVK-1.4.0", "KhronosGroup/MoltenVK", "v1.4.0", "fc74aef926ee3cd473fe260a93819c09fdc939bff669271a587e9ebaa43d4306"),
    ("Vulkan-Headers-v1.4.321", "Vulkan-Headers-1.4.321", "KhronosGroup/Vulkan-Headers", "v1.4.321", "7c1874f9186d9e7d5245e279cdccaa77a66ed8f52871b428630aeb771dc7623c"),
)


def build(directory, qt_prefix):
    directory.mkdir(parents=True, exist_ok=True)
    for name, extracted, repo, tag, expected in SOURCES:
        archive = directory / (name + ".tar.gz")
        if not archive.exists():
            request = urllib.request.Request("https://github.com/" + repo + "/archive/refs/tags/" + tag + ".tar.gz")
            with urllib.request.urlopen(request, timeout=180) as response, archive.open("xb") as out:
                shutil.copyfileobj(response, out)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Source archive hash mismatch: " + str(archive))
        if not (directory / extracted).exists():
            subprocess.run(["/usr/bin/tar", "-xzf", str(archive), "-C", str(directory)], check=True)
    source = directory / "qtbase-6.11.2/src/plugins/platforms/cocoa/qcocoaaccessibilityelement.mm"
    text = source.read_text()
    old = "if (cell->axid) { // it's a proper cell, remove from cache"
    new = "if (cell->axid && !cell.isManagedByParent) { // synthesized cells borrow the parent ID"
    if old in text:
        assert text.count(old) == 1
        text = text.replace(old, new)
        old_dealloc = "    QAccessibleCache::instance()->deleteInterface(axid);\n    [super dealloc];"
        assert text.count(old_dealloc) == 1
        text = text.replace(old_dealloc, "    if (axid && !self.isManagedByParent)\n        QAccessibleCache::instance()->deleteInterface(axid);\n    [super dealloc];")
        source.write_text(text)
    assert new in text and "if (axid && !self.isManagedByParent)" in text
    headers = directory / "headers/MoltenVK"
    headers.mkdir(parents=True, exist_ok=True)
    for header in (directory / "MoltenVK-1.4.0/MoltenVK/MoltenVK/API").glob("*.h"):
        shutil.copyfile(header, headers / header.name)
    project = Path(__file__).resolve().with_name("qt-cocoa")
    output = directory / "plugin-build"
    subprocess.run(["cmake", "-S", str(project), "-B", str(output), "-G", "Ninja",
                    "-DCMAKE_PREFIX_PATH=" + str(qt_prefix), "-DQTBASE_SOURCE=" + str(directory / "qtbase-6.11.2"),
                    "-DQT_NATIVE_HEADERS=" + str(headers.parent), "-DVULKAN_HEADERS=" + str(directory / "Vulkan-Headers-1.4.321"),
                    "-DCMAKE_BUILD_TYPE=RelWithDebInfo", "-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0"], check=True)
    subprocess.run(["cmake", "--build", str(output), "--parallel", "8"], check=True)
    return output / "libqcocoa.dylib"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--qt-prefix", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.build_dir.resolve(), args.qt_prefix.resolve()))
