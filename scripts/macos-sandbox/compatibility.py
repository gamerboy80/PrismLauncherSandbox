#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Prepare public compatibility inputs and inventories. Never approves, signs, or executes clients."""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import uuid

spec = importlib.util.spec_from_file_location("sandbox_manager", Path(__file__).with_name("manager.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Inputs:
    def __init__(self, root, stock):
        self.root, self.stock = root, stock
        self.records = []

    def fetch(self, url, target, sha1=None, size=None):
        if target.is_file():
            data = target.read_bytes()
            if (not sha1 or hashlib.sha1(data).hexdigest() == sha1) and (not size or len(data) == size):
                return data
        target.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "PrismSandboxCompatibility/1"})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read()
        if sha1 and hashlib.sha1(data).hexdigest() != sha1:
            raise RuntimeError("Upstream checksum mismatch: " + url)
        if size and len(data) != size:
            raise RuntimeError("Upstream size mismatch: " + url)
        temporary = target.with_name(target.name + ".download-" + uuid.uuid4().hex)
        temporary.write_bytes(data)
        os.replace(temporary, target)
        return data

    def metadata(self, uid, version):
        path = self.root / "manager/meta" / uid / (version + ".json")
        url = "https://meta.prismlauncher.org/v1/" + uid + "/" + version + ".json"
        raw = self.fetch(url, path)
        data = json.loads(raw)
        self.records.append({"kind": "metadata", "url": url, "sha256": hashlib.sha256(raw).hexdigest(), "path": str(path)})
        return data

    @staticmethod
    def applicable(library, architecture):
        rules = library.get("rules", [])
        allowed = not rules
        names = {"osx", "osx-arm64"} if architecture == "arm64" else {"osx"}
        for rule in rules:
            operating = rule.get("os", {})
            if operating.get("name", "osx") not in names:
                continue
            if operating.get("arch") and operating["arch"] not in ({"arm64", "aarch64"} if architecture == "arm64" else {"x86_64", "amd64"}):
                continue
            allowed = rule.get("action") == "allow"
        return allowed

    @staticmethod
    def coordinate(name, classifier=None):
        parts = name.split(":")
        group, artifact, version = parts[:3]
        extra = classifier if classifier is not None else parts[3] if len(parts) > 3 else ""
        filename = artifact + "-" + version + ("-" + extra if extra else "") + ".jar"
        return Path(group.replace(".", "/")) / artifact / version / filename

    def libraries(self, documents, architecture):
        selected = {}
        for doc in sorted(documents, key=lambda d: d.get("order", 0)):
            libraries = doc.get("libraries", []) + doc.get("mavenFiles", [])
            if doc.get("mainJar"):
                libraries.append(doc["mainJar"])
            for library in libraries:
                if not self.applicable(library, architecture):
                    continue
                key = tuple(library["name"].split(":")[:2])
                # Modern Prism native classifiers are distinct artifact names.
                selected[key] = library
        requests = []
        for library in selected.values():
            downloads = library.get("downloads", {})
            natives = library.get("natives", {})
            classifier = natives.get("osx-arm64" if architecture == "arm64" else "osx")
            if natives:
                if not classifier:
                    continue
                artifact = downloads.get("classifiers", {}).get(classifier)
            else:
                artifact = downloads.get("artifact")
            relative = self.coordinate(library["name"], classifier)
            if artifact is None:
                repository = library.get("url", "https://libraries.minecraft.net/")
                artifact = {"url": repository.rstrip("/") + "/" + relative.as_posix()}
            target = self.root / "manager/libraries" / relative
            requests.append((library["name"], artifact, target))

        def download(item):
            name, artifact, target = item
            # Reuse a stock cached artifact only after validating the metadata's checksum below.
            stock = self.stock / "libraries" / target.relative_to(self.root / "manager/libraries")
            if not target.exists() and stock.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["/bin/cp", "-c", str(stock), str(target)], check=True)
            raw = self.fetch(artifact["url"], target, artifact.get("sha1"), artifact.get("size"))
            return {"kind": "artifact", "name": name, "url": artifact["url"], "path": str(target),
                    "sha1": hashlib.sha1(raw).hexdigest(), "sha256": hashlib.sha256(raw).hexdigest(),
                    "upstreamSha1": artifact.get("sha1"), "size": len(raw)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            result = list(pool.map(download, requests))
        self.records.extend(result)
        return [r["path"] for r in result if r["path"].endswith(".jar")]


def prepare(root, stock, project):
    inputs = Inputs(root, stock)
    registry = m.SandboxInstanceRegistry(root)
    profiles = [
        ("vanilla-26.3", "26.3", None, None, "java-runtime-epsilon", "arm64"),
        ("fabric-1.20.1", "1.20.1", "net.fabricmc.fabric-loader", "0.16.14", "java-runtime-gamma", "arm64"),
        ("forge-1.20.1", "1.20.1", "net.minecraftforge", "47.4.0", "java-runtime-gamma", "arm64"),
        ("fabric-1.21.1", "1.21.1", "net.fabricmc.fabric-loader", "0.16.14", "java-runtime-delta", "arm64"),
        ("neoforge-1.21.1", "1.21.1", "net.neoforged", "21.1.234", "java-runtime-delta", "arm64"),
        ("forge-1.12.2-intel", "1.12.2", "net.minecraftforge", "14.23.5.2860", "zulu8-intel", "x86_64"),
    ]
    plans = []
    launchers = sorted((project / "build-macos-sandbox-manager/jars").glob("*.jar"))
    helper = project / "build-macos-sandbox-manager/PrismGameHost"
    for label, version, loader, loader_version, runtime, architecture in profiles:
        minecraft = inputs.metadata("net.minecraft", version)
        documents = [minecraft]
        lwjgl_version = next(r.get("suggests", r.get("equals")) for r in minecraft["requires"] if r["uid"] in ("org.lwjgl3", "org.lwjgl"))
        lwjgl_uid = next(r["uid"] for r in minecraft["requires"] if r["uid"] in ("org.lwjgl3", "org.lwjgl"))
        documents.append(inputs.metadata(lwjgl_uid, lwjgl_version))
        if loader:
            documents.append(inputs.metadata(loader, loader_version))
        artifacts = inputs.libraries(documents, architecture)
        identity = uuid.uuid5(uuid.NAMESPACE_URL, "prism-local-acceptance:" + label).hex
        directory = root / "instances" / identity
        directory.mkdir(parents=True, exist_ok=True)
        value = registry.register(directory, label)
        value["account"] = "offline"
        registry.save()
        components = [{"uid": "net.minecraft", "version": version, "important": True}]
        if loader:
            components.append({"uid": loader, "version": loader_version, "important": True})
        (directory / "mmc-pack.json").write_text(json.dumps({"formatVersion": 1, "components": components}))
        java_home = (next((root / "cache/java/zulu8-intel").rglob("bin/java")).parent.parent
                     if runtime == "zulu8-intel" else stock / "java" / runtime / "jre.bundle/Contents/Home")
        (directory / "instance.cfg").write_text("[General]\nConfigVersion=1.3\nInstanceType=OneSix\nname=" + label +
            "\nOverrideJavaLocation=true\nJavaPath=" + str(java_home / "bin/java") + "\nMaxMemAlloc=2048\n")
        plan = m.SandboxArtifactPlanner(registry).inspect(value["id"], java_home, artifacts, helper=helper, launchers=launchers)
        plans.append({"label": label, "instance": value["id"], "plan": plan["transaction"] + "/plan.json",
                      "fingerprint": plan["approvalFingerprint"], "review": plan["review"]})
        print("Prepared inventory:", label, plan["approvalFingerprint"], flush=True)
        m.atomic_json(root / "reports/compatibility-inputs.json", {"version": 1, "artifacts": inputs.records, "profiles": plans})
    return plans


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stock", type=Path, default=Path.home() / "Library/Application Support/PrismLauncher")
    args = parser.parse_args()
    prepare(args.root.resolve(), args.stock, Path(__file__).resolve().parents[2])
