#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Local manager backend. Inspection never executes inputs; building requires an exact approval.

Only the trusted manager invokes this program. Game/helper messages are not commands to it.
The CLI exchanges JSON with the Qt manager and is also useful for reproducible diagnostics.
"""
import argparse
import contextlib
import ctypes
import functools
import time
import pwd
import unicodedata
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
import re

VERSION = 1
ADAPTER_VERSION = 1
PROFILE = "adhoc-inherited-jvm-v1"
PLANNER_VERSION = 3
MAX_ENTRY = 512 * 1024 * 1024
MAX_ARCHIVE = 4 * 1024 * 1024 * 1024


class SandboxError(Exception):
    pass


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_hash(path):
    with open_regular(path) as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
        return result.hexdigest()


@contextlib.contextmanager
def open_regular(path):
    # O_NOFOLLOW on the final file alone does not stop a replaced parent directory.
    # Keep every ancestor anchored to an opened descriptor, including during races.
    absolute = os.path.abspath(path)
    if absolute.startswith(("/var/", "/tmp/")):
        absolute = "/private" + absolute
    parts = Path(absolute).parts[1:]
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            os.close(parent)
            parent = following
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SandboxError("Expected a regular file: " + str(path))
        with os.fdopen(fd, "rb", closefd=False) as stream:
            yield stream
    finally:
        os.close(fd)


def safe_relative(value):
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise SandboxError("Invalid relative path")
    parts = value.split("/")
    if value.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise SandboxError("Unsafe relative path: " + value)
    return value


def strict_directory(path):
    """Reject links at every component, after normalizing the system's /var alias."""
    absolute = Path(os.path.abspath(path))
    if str(absolute).startswith("/var/"):
        absolute = Path("/private" + str(absolute))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise SandboxError("Directory is not a real directory: " + str(current))
    return absolute


def mkdir_private(path):
    path = Path(path)
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
    strict_directory(path)
    if path.stat().st_uid != os.getuid():
        raise SandboxError("Manager directory is not owned by the current user")
    if path.stat().st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def read_json(path):
    with open_regular(path) as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise SandboxError("Unsupported record schema: " + str(path))
    return value


def atomic_json(path, value):
    path = Path(path)
    mkdir_private(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".transaction-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_manager_root(root, catalog_anchor=None):
    """An approval store must never live inside a game's grant or readable app bundle.

    The production anchor comes from the OS account database, never HOME or a
    game-controlled command-line flag. Tests can provide an isolated anchor.
    """
    root = Path("/" + os.path.abspath(root).lstrip("/"))
    if str(root).startswith(("/var/", "/tmp/")):
        root = Path("/private" + str(root))
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    default = home / "Library/Application Support/PrismLauncherSandboxManager"
    anchor = Path(catalog_anchor) if catalog_anchor else default / "manager"
    forbidden = [home / "Library/Containers", home / "Library/Group Containers",
                 home / "Library/Application Support/PrismLauncher", Path("/Applications"), Path("/System"), Path("/usr"), Path("/bin"), Path("/sbin")]
    shared = {home, home / "Library", home / "Library/Application Support", home / "Documents", home / "Downloads", home / "Desktop",
              Path("/"), Path("/private/tmp"), Path("/Volumes"), Path("/Library")}
    def comparison(path):
        # APFS can be case- and normalization-insensitive. Conservative matching
        # also prevents alternate spellings from bypassing the root catalog.
        return unicodedata.normalize("NFD", str(path)).casefold().rstrip("/")

    def beneath(path, base):
        if comparison(path) == comparison(base) or comparison(path).startswith(comparison(base) + "/"):
            return True
        # Mounted volume aliases and firmlinks can name the same directory without symlinks.
        try:
            reference = base.lstat()
            identity = (reference.st_dev, reference.st_ino)
            for ancestor in (path, *path.parents):
                try:
                    candidate = ancestor.lstat()
                    if (candidate.st_dev, candidate.st_ino) == identity:
                        return True
                except FileNotFoundError:
                    continue
        except FileNotFoundError:
            pass
        return False

    if any(beneath(root, p) and beneath(p, root) for p in shared) or any(beneath(root, p) for p in forbidden) or any(p.casefold().endswith((".app", ".framework", ".jdk")) for p in root.parts):
        raise SandboxError("Choose a dedicated manager data directory outside application bundles, containers, stock Prism and shared system folders")
    # A runtime is copied into game apps. It cannot contain manager credentials.
    for parent in (root, *root.parents):
        if (parent / "bin/java").exists() and (parent / "release").is_file():
            raise SandboxError("Manager data cannot be stored inside a Java runtime")
    existing = root
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    strict_directory(existing)
    mkdir_private(anchor)
    lock_fd = os.open(anchor / "root-catalog.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        path = anchor / "root-catalog.json"
        catalog = read_json(path) if path.exists() else {"version": VERSION, "roots": [str(default)]}
        roots = [Path(p) for p in catalog.get("roots", [])]
        for known in roots:
            if beneath(root, known / "instances") or beneath(root, known / "apps"):
                raise SandboxError("Manager data cannot be placed inside an instance grant or a generated game bundle")
        if root.exists() and root not in roots and root != default and any(root.iterdir()) and not (
            (root / "manager/registry.json").is_file() or (root / "manager/prismlauncher.cfg").is_file()
        ):
            raise SandboxError("Use an empty dedicated manager directory; import existing instances rather than adopting an unrelated populated folder")
        mkdir_private(root)
        if root not in roots:
            catalog["roots"] = sorted({str(p) for p in roots} | {str(root)})
            atomic_json(path, catalog)
        elif not path.exists():
            atomic_json(path, catalog)
    finally:
        os.close(lock_fd)
    return root


PHASES = {"Unprepared", "Resolving", "AwaitingApproval", "Preparing", "Ready", "Starting", "Running", "Exited", "Failed", "Cancelled"}


def preparation_state(registry, identity, phase, **details):
    if phase not in PHASES:
        raise SandboxError("Unsupported preparation state")
    value = registry.get(identity)
    state = {"version": VERSION, "instance": identity, "phase": phase, "ownerPID": os.getppid(),
             "generation": value.get("generation"), "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    state.update(details)
    atomic_json(registry.private / "status" / (identity + ".json"), state)
    return state


def tracked_preparation(phase):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(self, subject, *args, **kwargs):
            identity = subject["instance"] if isinstance(subject, dict) else subject
            preparation_state(self.registry, identity, phase)
            try:
                return function(self, subject, *args, **kwargs)
            except (SandboxError, OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
                preparation_state(self.registry, identity, "Failed", error={"code": type(error).__name__, "message": str(error)[:8192]})
                raise
        return wrapped
    return decorate


def mach_architectures(data):
    """Read Mach-O/fat headers without loading code or mistaking Java classes for fat files."""
    if len(data) < 8:
        return []
    names = {0x01000007: "x86_64", 0x0100000C: "arm64", 7: "i386", 12: "arm"}
    magic = data[:4]
    if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return [names.get(struct.unpack_from("<I", data, 4)[0], "unknown")]
    if magic in (b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
        return [names.get(struct.unpack_from(">I", data, 4)[0], "unknown")]
    if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        count = struct.unpack_from(">I", data, 4)[0]
        width = 32 if magic[-1] == 0xBF else 20
        if not 0 < count <= 32 or len(data) < 8 + count * width:
            return []
        return sorted({names.get(struct.unpack_from(">I", data, 8 + i * width)[0], "unknown") for i in range(count)})
    return []


def mach_file_type(path):
    with open_regular(path) as stream:
        header = stream.read(2048)
        if header[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
            # All slices of an executable must have the same role; inspect the first slice.
            wide = header[3] == 0xBF
            offset = struct.unpack_from(">Q" if wide else ">I", header, 16)[0]
            stream.seek(offset)
            header = stream.read(32)
        if len(header) >= 16:
            if header[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
                return struct.unpack_from("<I", header, 12)[0]
            if header[:4] in (b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
                return struct.unpack_from(">I", header, 12)[0]
    return None


def copy_regular(source, destination):
    """Create a new file; never follow a destination link or share writable inodes."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    strict_directory(destination.parent)
    with open_regular(source) as inp:
        parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if not clone_to_directory(inp.fileno(), parent, destination.name):
                fd = os.open(destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                with os.fdopen(fd, "wb") as out:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
        finally:
            os.close(parent)
        os.chmod(destination, stat.S_IMODE(os.fstat(inp.fileno()).st_mode) & 0o755)


def clone_to_directory(source_fd, directory_fd, name):
    """Independent APFS copy-on-write inode; never a writable hard link."""
    if sys.platform != "darwin":
        return False
    library = ctypes.CDLL(None, use_errno=True)
    clone = library.fclonefileat
    clone.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    clone.restype = ctypes.c_int
    return clone(source_fd, directory_fd, os.fsencode(name), 0) == 0


def snapshot_tree(source, destination):
    """Allow only internal runtime symlinks; snapshot every byte before the review."""
    source = strict_directory(source)
    destination.mkdir(parents=True)
    records = []
    for directory, dirs, files in os.walk(source, followlinks=False):
        for name in sorted(dirs + files):
            original = Path(directory) / name
            relative = original.relative_to(source).as_posix()
            safe_relative(relative)
            target = destination / relative
            info = original.lstat()
            if stat.S_ISLNK(info.st_mode):
                link = os.readlink(original)
                resolved = original.resolve(strict=True)
                if os.path.isabs(link) or not resolved.is_relative_to(source):
                    raise SandboxError("Runtime symlink escapes its root: " + relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link)
                records.append({"path": relative, "link": link})
                if name in dirs:
                    dirs.remove(name)
            elif stat.S_ISDIR(info.st_mode):
                target.mkdir(exist_ok=True)
            elif stat.S_ISREG(info.st_mode):
                copy_regular(original, target)
                with open_regular(target) as stream:
                    arches = mach_architectures(stream.read(2048))
                records.append({"path": relative, "sha256": file_hash(target), "size": target.stat().st_size,
                                "architectures": arches, "executable": bool(info.st_mode & 0o111)})
            else:
                raise SandboxError("Unsupported runtime file type: " + relative)
    return sorted(records, key=lambda x: x["path"])


def inspect_archive(path, output, source, selected_arch, depth=0, budget=None):
    if depth > 4:
        raise SandboxError("Nested archive depth exceeds four")
    found, checksums = [], []
    unmatched = set()
    if budget is None:
        budget = [0]
    classes = False
    total = 0
    with open_regular(path) as stream, zipfile.ZipFile(stream) as archive:
        for entry in archive.infolist():
            name = entry.filename
            if entry.is_dir():
                safe_relative(name.rstrip("/"))
                continue
            safe_relative(name)
            if ".dSYM/" in name:
                continue  # Debug symbols are not loadable native libraries.
            mode = entry.external_attr >> 16
            if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise SandboxError("Non-regular archive entry: " + name)
            total += entry.file_size
            budget[0] += entry.file_size
            if entry.file_size > MAX_ENTRY or total > MAX_ARCHIVE or budget[0] > MAX_ARCHIVE:
                raise SandboxError("Archive extraction budget exceeded")
            # Native classifier JARs contain module-info.class on modern LWJGL.
            # A module descriptor has no executable class body and must not make
            # a resource-only native archive look like a Java class library.
            if name.endswith(".class") and PurePosixPath(name).name != "module-info.class":
                classes = True
            with archive.open(entry) as content:
                prefix = content.read(2048)
                arches = mach_architectures(prefix)
                if arches:
                    if selected_arch not in arches:
                        unmatched.update(arches)
                        continue
                    target = output / hashlib.sha256((source + "!" + name).encode()).hexdigest()[:20]
                    with target.open("xb") as out:
                        out.write(prefix)
                        shutil.copyfileobj(content, out)
                    found.append({"source": source, "entry": name, "sha256": file_hash(target),
                                  "size": target.stat().st_size, "architectures": arches, "snapshot": target.name,
                                  "name": PurePosixPath(name).name})
                elif name.endswith(".jar") and prefix[:2] == b"PK":
                    nested = output / (hashlib.sha256((source + "!" + name).encode()).hexdigest() + ".jar")
                    with nested.open("xb") as out:
                        out.write(prefix)
                        shutil.copyfileobj(content, out)
                    child, _, child_checksums = inspect_archive(nested, output, source + "!" + name, selected_arch, depth + 1, budget)
                    found.extend(child)
                    checksums.extend(child_checksums)
                elif name.endswith(".sha1"):
                    checksums.append(name)
    if unmatched and not found:
        raise SandboxError("Native architecture mismatch in " + source + ": contains " + ", ".join(sorted(unmatched)) +
                           ", selected Java is " + selected_arch + ". Select a matching imported Java runtime before preparation.")
    return found, classes, checksums


class SandboxInstanceRegistry:
    def __init__(self, root):
        self.root = strict_directory(mkdir_private(root))
        self.private = mkdir_private(self.root / "manager")
        self.path = self.private / "registry.json"
        self.data = read_json(self.path) if self.path.exists() else {"version": VERSION, "instances": {}}

    def register(self, instance, name=None, custom=False):
        instance = strict_directory(instance)
        parent = strict_directory(self.root / "instances")
        if instance.parent != parent:
            raise SandboxError("Managed instances must be direct children of the manager's instances directory")
        for value in self.data["instances"].values():
            if value["instanceRoot"] == str(instance):
                identity = {"device": instance.stat().st_dev, "inode": instance.stat().st_ino}
                if value.get("directoryIdentity", identity) != identity:
                    raise SandboxError("Registered instance directory was replaced; import or relocate it explicitly")
                value["directoryIdentity"] = identity
                if name:
                    value["name"] = name
                self.save()
                return value
        identity = instance.name if re.fullmatch(r"[0-9a-f]{32}", instance.name) else uuid.uuid4().hex
        if identity in self.data["instances"]:
            raise SandboxError("This identity is registered at another location. Relocate the manager registry explicitly; policy must not reset when a data root moves.")
        value = {"id": identity, "name": name or instance.name, "instanceRoot": str(instance),
                 "bundleID": "org.prismlauncher.PrismLauncher.LocalSandbox.game." + identity,
                 "network": "allowed", "account": "offline" if custom else "selected", "generation": None}
        value["directoryIdentity"] = {"device": instance.stat().st_dev, "inode": instance.stat().st_ino}
        self.data["instances"][identity] = value
        self.save()
        return value

    def get(self, identity):
        if identity not in self.data["instances"]:
            raise SandboxError("Unknown managed instance")
        value = self.data["instances"][identity]
        actual = strict_directory(value["instanceRoot"])
        if actual.parent != self.root / "instances":
            raise SandboxError("Invalid registered instance root")
        identity = {"device": actual.stat().st_dev, "inode": actual.stat().st_ino}
        if value.get("directoryIdentity", identity) != identity:
            raise SandboxError("Registered instance directory identity changed")
        if "directoryIdentity" not in value:
            value["directoryIdentity"] = identity
            self.save()
        return value

    def save(self):
        atomic_json(self.path, self.data)


def relocate_registry(registry, previous_root):
    previous = Path(os.path.abspath(previous_root))
    if str(previous).startswith(("/tmp/", "/var/")):
        previous = Path("/private" + str(previous))
    if previous == registry.root:
        raise SandboxError("Previous and new manager roots are identical")
    if any((registry.private / "active").glob("*.json")):
        raise SandboxError("Stop all instances and reconcile active runs before relocating")
    updated = json.loads(json.dumps(registry.data))
    for value in updated["instances"].values():
        old = Path(value["instanceRoot"])
        if old.parent != previous / "instances":
            raise SandboxError("Registry contains an instance outside the specified previous root")
        directory = strict_directory(registry.root / "instances" / old.name)
        value["instanceRoot"] = str(directory)
        value["directoryIdentity"] = {"device": directory.stat().st_dev, "inode": directory.stat().st_ino}
        value["generation"] = None
        value.pop("runtimeSelection", None)
    atomic_json(registry.private / ("registry-before-relocation-" + uuid.uuid4().hex + ".json"), registry.data)
    registry.data = updated
    registry.save()
    for identity in updated["instances"]:
        preparation_state(registry, identity, "Unprepared", reason="Manager root relocated; prepare new signed folder grants before launching.")
    return {"version": VERSION, "previousRoot": str(previous), "root": str(registry.root),
            "instances": len(updated["instances"]), "requiresPreparation": True}


class SandboxApprovalStore:
    def __init__(self, registry):
        self.directory = mkdir_private(registry.private / "approvals")

    def approved(self, plan):
        path = self.directory / (plan["approvalFingerprint"] + ".json")
        if not path.exists():
            return False
        record = read_json(path)
        return record.get("fingerprint") == plan["approvalFingerprint"] and record.get("inventory") == plan["review"]

    def approve(self, plan, fingerprint):
        if fingerprint != plan["approvalFingerprint"]:
            raise SandboxError("Approval does not match the reviewed inventory")
        atomic_json(self.directory / (fingerprint + ".json"), {
            "version": VERSION, "fingerprint": fingerprint, "inventory": plan["review"],
            "approvedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })

    def changes(self, plan):
        current = plan["review"]
        candidates = []
        for path in self.directory.glob("*.json"):
            record = read_json(path)
            previous = record.get("inventory", {})
            if previous.get("runtimeOrigin") == current["runtimeOrigin"] and previous.get("architecture") == current["architecture"]:
                candidates.append(record)
        previous = max(candidates, key=lambda r: r.get("approvedAt", ""))["inventory"] if candidates else {}

        def index(review):
            files = {("runtime", review.get("runtimeOrigin", ""), r["path"]): r for r in review.get("runtimeFiles", [])}
            files.update({("native", r["source"], r["entry"]): r for r in review.get("nativeFiles", [])})
            return files

        old, new = index(previous), index(current)
        return {"previousApproval": digest(previous) if previous else None,
                "added": [new[k] for k in sorted(new.keys() - old.keys())],
                "changed": [{"before": old[k], "after": new[k]} for k in sorted(old.keys() & new.keys()) if old[k] != new[k]],
                "removed": [old[k] for k in sorted(old.keys() - new.keys())],
                "adapterRequirementsChanged": previous.get("adapters", []) != current.get("adapters", []),
                "signingProfileChanged": previous.get("signingProfile") != current.get("signingProfile")}


@contextlib.contextmanager
def directory_beneath(root, relative):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in safe_relative(relative).split("/"):
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = following
        yield fd
    finally:
        os.close(fd)


def materialize(registry, identity, request):
    """Copy only known cache files into one instance, without trusting its directory entries."""
    value = registry.get(identity)
    instance = Path(value["instanceRoot"])
    permitted = [instance, registry.root / "cache", registry.private / "libraries", registry.private / "assets",
                 registry.private / "mods/minecraftforge/libs"]
    mappings = {}

    def copy_one(source, relative):
        source = Path(os.path.abspath(source))
        source_base = next((p for p in permitted if source.is_relative_to(p)), None)
        if source_base is None:
            raise SandboxError("Dependency is outside managed caches; import it first: " + str(source))
        source_relative = source.relative_to(source_base).as_posix()
        source_parent = str(PurePosixPath(source_relative).parent)
        # Source directory traversal is also anchored; a mod cannot swap a parent for a link.
        base_fd = os.open(source_base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for part in (() if source_parent == "." else safe_relative(source_parent).split("/")):
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=base_fd)
                os.close(base_fd)
                base_fd = following
            src_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=base_fd)
        finally:
            os.close(base_fd)
        try:
            if not stat.S_ISREG(os.fstat(src_fd).st_mode):
                raise SandboxError("Dependency is not a regular file")
            parent, name = str(PurePosixPath(relative).parent), PurePosixPath(relative).name
            with directory_beneath(instance, parent) as target_fd:
                temp_name = ".copy-" + uuid.uuid4().hex
                try:
                    if not clone_to_directory(src_fd, target_fd, temp_name):
                        dest_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target_fd)
                        with os.fdopen(os.dup(src_fd), "rb") as src, os.fdopen(dest_fd, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
                    os.replace(temp_name, name, src_dir_fd=target_fd, dst_dir_fd=target_fd)
                finally:
                    try:
                        os.unlink(temp_name, dir_fd=target_fd)
                    except FileNotFoundError:
                        pass
        finally:
            os.close(src_fd)

    for source in request.get("files", []):
        source = str(Path(os.path.abspath(source)))
        if Path(source).is_relative_to(instance):
            # Preserve manifest Class-Path relationships for already imported client dependencies.
            strict_directory(Path(source).parent)
            with open_regular(source):
                pass
            mappings[source] = source
            continue
        library_root = registry.private / "libraries"
        if Path(source).is_relative_to(library_root):
            relative = ".prism-cache/libraries/" + Path(source).relative_to(library_root).as_posix()
            mappings[str(library_root)] = str(instance / ".prism-cache/libraries")
        else:
            relative = ".prism-cache/libraries/" + hashlib.sha256(source.encode()).hexdigest()[:20] + "/" + Path(source).name
        copy_one(source, relative)
        mappings[source] = str(instance / relative)
    if request.get("assetIndex"):
        assets = registry.private / "assets"
        if assets.is_dir():
            strict_directory(assets)
            index = request["assetIndex"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", index) or index in (".", ".."):
                raise SandboxError("Invalid asset index identifier")
            index_file = assets / "indexes" / (index + ".json")
            with open_regular(index_file) as stream:
                objects = json.load(stream).get("objects", {})
            wanted = [index_file]
            for item in objects.values():
                hash_value = item.get("hash", "")
                if not re.fullmatch(r"[0-9a-f]{40}", hash_value):
                    raise SandboxError("Invalid asset object hash")
                wanted.append(assets / "objects" / hash_value[:2] / hash_value)
            if (assets / "log_configs").exists():
                strict_directory(assets / "log_configs")
                wanted.extend((assets / "log_configs").iterdir())
            for source in wanted:
                relative = ".prism-cache/assets/" + source.relative_to(assets).as_posix()
                copy_one(source, relative)
            mappings[str(assets)] = str(instance / ".prism-cache/assets")
    custom = request.get("customClient")
    value["kind"] = "Minecraft"
    if custom is not None:
        custom = validate_client_spec(instance, custom)
        atomic_json(registry.private / "clients" / (identity + ".json"), custom)
        value["kind"] = custom["launchKind"]
    registry.save()
    return {"version": VERSION, "mapping": mappings}


def inspect_client(path):
    """Inspect launch metadata and local manifest dependencies, never invoking Java."""
    path = Path(path).absolute()
    with open_regular(path) as stream, zipfile.ZipFile(stream) as archive:
        total = 0
        for entry in archive.infolist():
            safe_relative(entry.filename.rstrip("/"))
            total += entry.file_size
            if entry.file_size > MAX_ENTRY or total > MAX_ARCHIVE:
                raise SandboxError("Client archive exceeds inspection budget")
            mode = entry.external_attr >> 16
            if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise SandboxError("Client archive contains a link or special file")
        try:
            info = archive.getinfo("META-INF/MANIFEST.MF")
            # Signed Minecraft manifests have large per-entry digest sections.
            # Only the bounded main section contains launch attributes.
            with archive.open(info) as manifest:
                text = manifest.read(1024 * 1024 + 1).decode("utf-8", errors="replace").replace("\r\n", "\n")
            if "\n\n" not in text and len(text) > 1024 * 1024:
                raise SandboxError("JAR manifest main section is too large")
            text = text.split("\n\n", 1)[0]
        except KeyError:
            text = ""
        text = text.replace("\n ", "")
        fields = {}
        for line in text.split("\n\n", 1)[0].splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                fields[key.lower()] = value
        dependencies = []
        from urllib.parse import unquote, urlparse
        for item in fields.get("class-path", "").split():
            url = urlparse(item)
            if url.scheme or url.netloc or url.query or url.fragment:
                raise SandboxError("Import this manifest dependency explicitly; remote/absolute URLs are unsupported: " + item)
            relative = safe_relative(unquote(url.path))
            target = path.parent / relative
            strict_directory(target.parent)
            with open_regular(target):
                pass
            dependencies.append({"source": str(target), "relative": relative, "sha256": file_hash(target)})
        return {"version": VERSION, "jar": str(path), "sha256": file_hash(path),
                "mainClass": fields.get("main-class", ""), "dependencies": dependencies}


def validate_client_spec(instance, supplied):
    if not isinstance(supplied, dict) or supplied.get("version") != VERSION:
        raise SandboxError("Unsupported custom-client schema version")
    kind = supplied.get("launchKind")
    if kind not in ("StandaloneJar", "MinecraftReplacement"):
        raise SandboxError("Unsupported custom-client launch kind")
    relative = safe_relative(supplied.get("jarPath"))
    metadata = inspect_client(Path(instance) / relative)
    entry = supplied.get("entryPoint", "")
    if not isinstance(entry, str) or "\0" in entry:
        raise SandboxError("Invalid client entry point")
    result = {"version": VERSION, "launchKind": kind, "jarPath": relative, "jarSHA256": metadata["sha256"],
              "entryPoint": entry or metadata["mainClass"],
              "dependencies": [{"path": (PurePosixPath(relative).parent / d["relative"]).as_posix(), "sha256": d["sha256"]} for d in metadata["dependencies"]],
              "entryMode": "Manifest" if kind == "StandaloneJar" and not entry else "MainClass"}
    libraries = Path(instance) / "client/libraries"
    if libraries.exists():
        strict_directory(libraries)
        for library in sorted(libraries.rglob("*.jar")):
            result["dependencies"].append({"path": library.relative_to(instance).as_posix(), "sha256": file_hash(library)})
    if kind == "MinecraftReplacement":
        result["minecraftVersion"] = supplied.get("minecraftVersion", "")
    elif not result["entryPoint"]:
        raise SandboxError("Standalone JAR requires a Main-Class manifest or explicit entry point")
    for key in ("jvmArguments", "applicationArguments"):
        args = supplied.get(key, [])
        if not isinstance(args, list) or any(not isinstance(a, str) or "\0" in a for a in args):
            raise SandboxError("Client arguments must be string arrays")
        result[key] = args
    return result


class SandboxArtifactPlanner:
    def __init__(self, registry):
        self.registry = registry

    @tracked_preparation("Resolving")
    def inspect(self, instance, java_home, archives=(), native_files=(), helper=None, launchers=()):
        value = self.registry.get(instance)
        java_home = strict_directory(java_home)
        native_java = java_home / "bin/java"
        if not native_java.exists():
            raise SandboxError("Java home has no bin/java")
        staging = mkdir_private(self.registry.root / "staging")
        transaction = Path(tempfile.mkdtemp(prefix="inventory-", dir=staging))
        records = snapshot_tree(java_home, transaction / "runtime")
        java_record = next((r for r in records if r["path"] == "bin/java"), None)
        if not java_record or not java_record.get("architectures"):
            raise SandboxError("Java executable is not a regular Mach-O file")
        arches = java_record["architectures"]
        arch = "arm64" if "arm64" in arches else "x86_64" if "x86_64" in arches else None
        if not arch:
            raise SandboxError("Only ARM64 and x86_64 runtimes are supported")
        for record in records:
            if record.get("architectures") and arch not in record["architectures"]:
                raise SandboxError("Runtime has mixed architectures: " + record["path"])
        value["runtimeSelection"] = {"origin": str(java_home), "architecture": arch,
                                     "nativeFingerprint": digest([r for r in records if r.get("architectures")])}
        self.registry.save()
        native_dir = transaction / "native-inputs"
        native_dir.mkdir()
        natives, artifacts, checksums, adapters = [], [], [], []
        for i, archive in enumerate(archives):
            original = Path(archive)
            snapshot = transaction / ("artifact-%d.jar" % i)
            copy_regular(original, snapshot)
            artifact_hash = file_hash(snapshot)
            source = str(original.absolute())
            native, classes, checksum_entries = inspect_archive(snapshot, native_dir, source, arch)
            natives.extend(native)
            artifacts.append({"source": source, "snapshot": snapshot.name, "sha256": artifact_hash,
                              "size": snapshot.stat().st_size, "hasClasses": classes, "hasNatives": bool(native),
                              "architectures": sorted({a for n in native for a in n["architectures"]}),
                              "sourceType": "instance-import" if original.is_relative_to(Path(value["instanceRoot"])) else "cache-or-local-import"})
            origin = self.registry.private / "artifact-origins" / (artifact_hash + ".json")
            if origin.is_file():
                provenance = read_json(origin)
                if provenance.get("sha256") != artifact_hash or provenance.get("size") != snapshot.stat().st_size:
                    raise SandboxError("Artifact provenance does not match the inspected bytes")
                artifacts[-1]["origin"] = provenance
            adapter = NativeLoaderAdapter.classify(artifacts[-1])
            NativeLoaderAdapter.validate(artifacts[-1])
            if adapter:
                adapters.append({"id": adapter, "artifactHash": artifact_hash})
            checksums.extend(checksum_entries)
        for i, native in enumerate(native_files):
            original = Path(native)
            snapshot = native_dir / ("explicit-%d" % i)
            copy_regular(original, snapshot)
            with snapshot.open("rb") as stream:
                native_arches = mach_architectures(stream.read(2048))
            if arch not in native_arches:
                raise SandboxError("Explicit native does not match Java architecture: " + str(original))
            natives.append({"source": str(original.absolute()), "entry": original.name, "sha256": file_hash(snapshot),
                            "architectures": native_arches, "size": snapshot.stat().st_size,
                            "snapshot": snapshot.name, "name": original.name})
        natives.sort(key=lambda n: (n["source"], n["entry"], n["sha256"]))
        adapters.sort(key=lambda a: (a["id"], a["artifactHash"]))
        by_name = {}
        for native in natives:
            existing = by_name.get(native["name"])
            if existing and existing["sha256"] != native["sha256"]:
                raise SandboxError("Ambiguous native filename: " + native["name"])
            by_name[native["name"]] = native
        runtime_natives = [{k: r[k] for k in ("path", "sha256", "size", "architectures")} for r in records if r.get("architectures")]
        review = {"runtimeOrigin": str(java_home), "runtimeFiles": runtime_natives,
                  "nativeFiles": [{k: v for k, v in n.items() if k != "snapshot"} for n in natives],
                  "artifacts": sorted([{k: a[k] for k in ("source", "sha256", "origin") if k in a} for a in artifacts if a["hasNatives"]], key=lambda a: a["source"]),
                  "architecture": arch, "adapterVersion": ADAPTER_VERSION, "signingProfile": PROFILE}
        if adapters:
            review["adapters"] = adapters
        helper = Path(helper) if helper else None
        if not helper or not helper.is_file():
            raise SandboxError("A compiled PrismGameHost is required")
        copy_regular(helper, transaction / "PrismGameHost")
        launcher_records = []
        for i, launcher in enumerate(launchers):
            snapshot = transaction / ("launcher-%d.jar" % i)
            copy_regular(launcher, snapshot)
            launcher_records.append({"name": Path(launcher).name, "snapshot": snapshot.name, "sha256": file_hash(snapshot)})
        plan = {"version": VERSION, "plannerVersion": PLANNER_VERSION, "id": transaction.name, "instance": instance, "instanceRoot": value["instanceRoot"],
                "network": value["network"], "transaction": str(transaction), "runtimeRecords": records,
                "nativeFiles": natives, "artifacts": artifacts, "checksumResources": sorted(set(checksums)),
                "launchers": launcher_records, "helperHash": file_hash(transaction / "PrismGameHost"),
                "review": review, "approvalFingerprint": digest(review)}
        atomic_json(transaction / "plan.json", plan)
        preparation_state(self.registry, instance, "Preparing" if SandboxApprovalStore(self.registry).approved(plan) else "AwaitingApproval",
                          transaction=str(transaction), fingerprint=plan["approvalFingerprint"])
        return plan


class NativeLoaderAdapter:
    """Versioned adapter contract. Only adapters with a declared loading mechanism are configured."""
    version = ADAPTER_VERSION

    @staticmethod
    def properties(native_path):
        return ["-Djava.library.path=" + native_path]

    @staticmethod
    def validate(artifact):
        if artifact.get("hasNatives") and artifact.get("hasClasses") and not NativeLoaderAdapter.classify(artifact):
            raise SandboxError("Unsupported native loading mechanism in " + artifact["source"] +
                               ". This class JAR embeds Mach-O code but has no supported extraction adapter. "
                               "Keep the client bytes unchanged; provide a versioned loader adapter before launching. "
                               "Ordinary System.loadLibrary clients can instead import native files explicitly.")

    @staticmethod
    def classify(artifact):
        pinned = {
            "77e34a13bccffc6c8b1a0e3ed46ddb0ea7dd56f2104ecdfacaaec9e4054ac332": "weblite-1.1",
            "fec83601d2da127234728a8fffb429d0c4a59587452834da203163e6a3f941db": "weblite-1.0.0",
            "a58a84c4271e50df4c96ed916ccb7e48a869f8ed9cdcda1ad5d3d4c33b0214a3": "lz4-java-library-path-1.10.1-v1",
            "d74a3334fb35195009b338a951f918203d6bbca3d1d359033dc33edd1cadc9ef": "lz4-java-library-path-1.8.0-v1",
            "1de8ff91b90fba9d4896b8617ed3594c2e4390d321d5a92c34f80e62e406c1b0": "lz4-java-library-path-1.8.1-v1",
        }
        if artifact.get("sha256") in pinned:
            return pinned[artifact["sha256"]]
        name = Path(artifact["source"]).name
        if re.match(r"lwjgl(?:-[a-z0-9-]+)?-(?:2\.|3\.)", name):
            return "lwjgl-library-path-v1"
        if re.match(r"jna-[0-9]", name):
            return "jna-nounpack-v1"
        return None


def run_tool(arguments):
    result = subprocess.run([str(x) for x in arguments], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=180, check=False)
    if result.returncode:
        raise SandboxError("Tool failed: " + str(arguments[0]) + "\n" + result.stderr.decode(errors="replace")[-8000:])
    return result.stdout


def validate_load_path(value, binary, app, executable, rpath=False):
    if value.startswith("/") and os.path.normpath(value).startswith(("/usr/lib/", "/System/Library/")):
        return
    if value.startswith("@rpath/") and not rpath:
        safe_relative(value[len("@rpath/"):])
        return  # Every LC_RPATH in the prepared code is checked separately.
    for prefix, base in (("@loader_path", Path(binary).parent), ("@executable_path", Path(executable).parent)):
        if value == prefix or value.startswith(prefix + "/"):
            target = (base / value[len(prefix):].lstrip("/")).resolve()
            if target.is_relative_to(Path(app).resolve()):
                return
    raise SandboxError("Native load path escapes the prepared bundle: " + value)


class SandboxBundleBuilder:
    def __init__(self, registry):
        self.registry = registry

    def load_plan(self, path):
        path = Path(path)
        transaction = strict_directory(path.parent)
        if transaction.parent != self.registry.root / "staging" or path.name != "plan.json":
            raise SandboxError("Plan is outside manager staging")
        plan = read_json(path)
        if plan.get("transaction") != str(transaction) or plan.get("approvalFingerprint") != digest(plan.get("review")):
            raise SandboxError("Plan integrity check failed")
        return plan

    @tracked_preparation("Preparing")
    def build(self, plan):
        if plan.get("plannerVersion") != PLANNER_VERSION:
            raise SandboxError("Preparation plan requires fresh inspection with the current native planner")
        if sys.platform != "darwin":
            raise SandboxError("App Sandbox bundles require macOS")
        value = self.registry.get(plan["instance"])
        if value["instanceRoot"] != plan["instanceRoot"] or value["network"] != plan["network"]:
            raise SandboxError("Policy changed after inspection; inspect again")
        if not SandboxApprovalStore(self.registry).approved(plan):
            raise SandboxError("Native approval is required before signing or configuring this plan")
        transaction = Path(plan["transaction"])
        # Verify every runtime byte again, not just the executable's header or mtime.
        for record in plan["runtimeRecords"]:
            source = transaction / "runtime" / safe_relative(record["path"])
            if "link" in record:
                if not source.is_symlink() or os.readlink(source) != record["link"]:
                    raise SandboxError("Runtime symlink changed after review")
            elif file_hash(source) != record["sha256"]:
                raise SandboxError("Runtime changed after review: " + record["path"])
        if file_hash(transaction / "PrismGameHost") != plan["helperHash"]:
            raise SandboxError("Helper changed after inspection")
        for artifact in plan["artifacts"]:
            NativeLoaderAdapter.validate(artifact)
        for artifact in plan["artifacts"] + plan["launchers"]:
            if file_hash(transaction / safe_relative(artifact["snapshot"])) != artifact["sha256"]:
                raise SandboxError("Artifact changed after inspection")
        for native in plan["nativeFiles"]:
            if file_hash(transaction / "native-inputs" / safe_relative(native["snapshot"])) != native["sha256"]:
                raise SandboxError("Native changed after review")
        generation = digest({"plan": {k: v for k, v in plan.items() if k not in ("transaction", "id")}, "bundleID": value["bundleID"]})[:24]
        final_parent = self.registry.root / "apps" / value["id"] / generation
        if final_parent.exists():
            app = final_parent / "Game.app"
            run_tool(["/usr/bin/codesign", "--verify", "--deep", "--strict", app])
            existing = read_json(app / "Contents/Resources/bundle-manifest.json")
            if existing["generation"] != generation or existing["approvalFingerprint"] != plan["approvalFingerprint"]:
                raise SandboxError("Existing generation does not match its inventory")
            value["generation"] = generation
            self.registry.save()
            preparation_state(self.registry, value["id"], "Ready", fingerprint=plan["approvalFingerprint"])
            return {"version": VERSION, "app": str(app), "manifest": existing,
                    "removedNativeJars": [a["source"] for a in plan["artifacts"] if a["hasNatives"] and not a["hasClasses"]]}
        build_root = transaction / "build"
        if build_root.exists():
            raise SandboxError("Interrupted build exists; inspect into a new transaction")
        app = build_root / "Game.app"
        contents = app / "Contents"
        resources = contents / "Resources"
        (contents / "MacOS").mkdir(parents=True)
        (resources / "natives").mkdir(parents=True)
        (resources / "launchers").mkdir()
        shutil.copytree(transaction / "runtime", contents / "Runtime", symlinks=True)
        copy_regular(transaction / "PrismGameHost", contents / "MacOS/PrismGameHost")
        os.chmod(contents / "MacOS/PrismGameHost", 0o755)
        for record in plan["launchers"]:
            copy_regular(transaction / safe_relative(record["snapshot"]), resources / "launchers" / safe_relative(record["name"]))
        for native in plan["nativeFiles"]:
            target = resources / "natives" / native["name"]
            if not target.exists():
                copy_regular(transaction / "native-inputs" / safe_relative(native["snapshot"]), target)
            if target.suffix == ".jnilib":
                alias = target.with_suffix(".dylib")
                if alias.exists() and file_hash(alias) != file_hash(target):
                    raise SandboxError("Conflicting JNI compatibility alias")
                if not alias.exists():
                    copy_regular(target, alias)
        with (contents / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleExecutable": "PrismGameHost", "CFBundleIdentifier": value["bundleID"],
                          "CFBundleName": "Prism Game", "CFBundleDisplayName": value["name"],
                          "CFBundlePackageType": "APPL", "CFBundleVersion": "1", "LSUIElement": True,
                          "NSHighResolutionCapable": True}, stream)
        app_entitlements = {"com.apple.security.app-sandbox": True,
                            "com.apple.security.temporary-exception.files.absolute-path.read-write": [value["instanceRoot"] + "/"]}
        if value["network"] == "allowed":
            app_entitlements.update({"com.apple.security.network.client": True, "com.apple.security.network.server": True})
        child_entitlements = {"com.apple.security.app-sandbox": True, "com.apple.security.inherit": True,
                              "com.apple.security.cs.allow-jit": True, "com.apple.security.cs.allow-unsigned-executable-memory": True,
                              "com.apple.security.cs.disable-library-validation": True}
        nested_entitlements = {"com.apple.security.app-sandbox": True, "com.apple.security.inherit": True,
                               "com.apple.security.cs.disable-library-validation": True}
        for name, entitlements in (("app", app_entitlements), ("child", child_entitlements), ("nested", nested_entitlements)):
            with (build_root / (name + ".entitlements")).open("wb") as stream:
                plistlib.dump(entitlements, stream)
        signed = []
        for path in sorted(contents.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_symlink() or not path.is_file() or path == contents / "MacOS/PrismGameHost":
                continue
            with open_regular(path) as stream:
                arches = mach_architectures(stream.read(2048))
            if not arches:
                continue
            # MH_EXECUTE is 2; dylibs/bundles do not receive JVM entitlements.
            with path.open("rb") as stream:
                header = stream.read(32)
            executable = mach_file_type(path) == 2
            runtime_executable = executable and path.is_relative_to(contents / "Runtime")
            args = ["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", "--timestamp=none"]
            if executable:
                os.chmod(path, 0o755)
                args += ["--entitlements", build_root / ("child.entitlements" if runtime_executable else "nested.entitlements")]
            run_tool(args + [path])
            signed.append({"path": path.relative_to(app).as_posix(), "sha256": file_hash(path), "child": bool(executable)})
        # LWJGL native checksums must describe the signed bytes, without disabling its checks.
        with zipfile.ZipFile(resources / "native-checksums.jar", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for resource in plan["checksumResources"]:
                name = PurePosixPath(resource[:-5]).name
                path = resources / "natives" / name
                if path.is_file():
                    archive.writestr(resource, hashlib.sha1(path.read_bytes()).hexdigest())
        manifest = {"version": VERSION, "instanceID": value["id"], "instanceRoot": value["instanceRoot"],
                    "network": value["network"], "generation": generation, "java": "Contents/Runtime/bin/java",
                    "architecture": plan["review"]["architecture"], "approvalFingerprint": plan["approvalFingerprint"],
                    "signedFiles": signed, "nativeFiles": plan["review"]["nativeFiles"], "adapterVersion": ADAPTER_VERSION}
        manifest["adapters"] = plan["review"].get("adapters", [])
        manifest["plannerVersion"] = PLANNER_VERSION
        manifest["protocolVersion"] = 1
        (resources / "bundle-manifest.json").write_bytes(canonical_json(manifest) + b"\n")
        run_tool(["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", "--timestamp=none",
                  "--entitlements", build_root / "app.entitlements", app])
        run_tool(["/usr/bin/codesign", "--verify", "--deep", "--strict", app])
        actual = plistlib.loads(run_tool(["/usr/bin/codesign", "-d", "--entitlements", ":-", app]))
        if actual != app_entitlements:
            raise SandboxError("Game app entitlements do not match the planned sandbox")
        for record in signed:
            run_tool(["/usr/bin/codesign", "--verify", "--strict", app / record["path"]])
            encoded = run_tool(["/usr/bin/codesign", "-d", "--entitlements", ":-", app / record["path"]])
            actual = plistlib.loads(encoded) if encoded.strip() else {}
            expected = (child_entitlements if record["path"].startswith("Contents/Runtime/") else nested_entitlements) if record["child"] else {}
            if actual != expected:
                raise SandboxError("Nested code entitlement mismatch: " + record["path"])
            with open_regular(app / record["path"]) as stream:
                if manifest["architecture"] not in mach_architectures(stream.read(2048)):
                    raise SandboxError("Signed code lost the selected architecture: " + record["path"])
            identifiers = run_tool(["/usr/bin/otool", "-D", app / record["path"]]).decode(errors="replace").splitlines()[1:]
            dependencies = run_tool(["/usr/bin/otool", "-L", app / record["path"]]).decode(errors="replace").splitlines()
            for line in dependencies:
                if not line.startswith("\t"):
                    continue
                dependency = line.strip().split(" (compatibility version", 1)[0]
                if dependency in identifiers:
                    continue  # LC_ID_DYLIB identifies this file; it is not a loaded dependency.
                executable = app / record["path"] if record["child"] else app / manifest["java"]
                validate_load_path(dependency, app / record["path"], app, executable)
            commands = run_tool(["/usr/bin/otool", "-l", app / record["path"]]).decode(errors="replace")
            for load_path in re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset \d+\)", commands):
                executable = app / record["path"] if record["child"] else app / manifest["java"]
                validate_load_path(load_path, app / record["path"], app, executable, rpath=True)
        mkdir_private(final_parent.parent)
        # A reviewed path must still name the same dedicated directory at publication.
        latest = self.registry.get(plan["instance"])
        if latest["instanceRoot"] != plan["instanceRoot"] or latest["network"] != plan["network"]:
            raise SandboxError("Instance policy changed before bundle publication")
        os.rename(build_root, final_parent)
        value["generation"] = generation
        self.registry.save()
        preparation_state(self.registry, value["id"], "Ready", fingerprint=plan["approvalFingerprint"])
        report = {"version": VERSION, "app": str(final_parent / "Game.app"), "manifest": manifest,
                  "removedNativeJars": [a["source"] for a in plan["artifacts"] if a["hasNatives"] and not a["hasClasses"]]}
        atomic_json(self.registry.root / "reports" / (value["id"] + "-bundle.json"), report)
        return report


def collect_unused(registry, minimum_days=7, apply=False):
    if minimum_days < 1:
        raise SandboxError("Cleanup requires at least one day of retention")
    cutoff = time.time() - minimum_days * 86400
    active = {p.stem for p in (registry.private / "active").glob("*.json")}
    pinned_transactions = set()
    recovered = []
    for path in (registry.private / "status").glob("*.json"):
        state = read_json(path)
        live = state.get("instance") in active
        pid = state.get("ownerPID", 0)
        if isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)  # Existence check only; never signal a PID from a file.
                live = True
            except ProcessLookupError:
                pass
            except PermissionError:
                live = True  # Uncertain liveness retains data.
        if live and state.get("transaction"):
            pinned_transactions.add(state["transaction"])
        elif state.get("phase") in ("Resolving", "AwaitingApproval", "Preparing", "Starting", "Running"):
            recovered.append(str(path))
            if apply:
                state["phase"] = "Failed"
                state["error"] = {"code": "INTERRUPTED", "message": "Previous manager operation ended; the last valid generation is retained."}
                atomic_json(path, state)
    candidates = []
    for folder in (registry.private / "download-staging", registry.private / "archive-staging"):
        for path in folder.glob("*"):
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                candidates.append(path)
    for path in (registry.root / "staging").glob("inventory-*"):
        if path.is_symlink() or not path.is_dir():
            continue
        if path.stat().st_mtime < cutoff and str(path) not in pinned_transactions:
            candidates.append(path)
    for identity, value in registry.data["instances"].items():
        if identity in active:
            continue  # Conservative: even a stale private active record pins all its generations.
        directory = registry.root / "apps" / identity
        generations = sorted((p for p in directory.glob("*") if p.is_dir() and not p.is_symlink()), key=lambda p: p.stat().st_mtime, reverse=True)
        retained = {value.get("generation")}
        retained.update(p.name for p in generations[:2])
        for generation in generations:
            if re.fullmatch(r"[0-9a-f]{24}", generation.name) and generation.name not in retained and generation.stat().st_mtime < cutoff:
                candidates.append(generation)
    if apply:
        if not shutil.rmtree.avoids_symlink_attacks:
            raise SandboxError("This Python runtime lacks descriptor-based safe directory cleanup")
        for path in candidates:
            strict_directory(path.parent)
            if path.is_dir():
                strict_directory(path)
                shutil.rmtree(path)
            else:
                os.unlink(path)
    result = {"version": VERSION, "applied": apply, "minimumAgeDays": minimum_days,
              "candidates": [str(p) for p in candidates], "recoveredStates": recovered,
              "retains": "Instance data, saves, containers, active generations, current/previous generations and input caches"}
    atomic_json(registry.root / "reports/cleanup.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-root")
    relocate = sub.add_parser("relocate")
    relocate.add_argument("--previous-root", required=True)
    register = sub.add_parser("register")
    register.add_argument("--instance", required=True)
    register.add_argument("--name")
    register.add_argument("--custom", action="store_true")
    client = sub.add_parser("inspect-client")
    client.add_argument("--jar", required=True)
    cleanup = sub.add_parser("gc")
    cleanup.add_argument("--apply", action="store_true")
    cleanup.add_argument("--minimum-days", type=int, default=7)
    policy = sub.add_parser("policy")
    policy.add_argument("--id", required=True)
    policy.add_argument("--network", choices=("allowed", "blocked"))
    policy.add_argument("--account", choices=("offline", "selected"))
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--id", required=True)
    inspect.add_argument("--java-home", required=True)
    inspect.add_argument("--helper", required=True)
    inspect.add_argument("--archive", action="append", default=[])
    inspect.add_argument("--native", action="append", default=[])
    inspect.add_argument("--launcher", action="append", default=[])
    materialization = sub.add_parser("materialize")
    materialization.add_argument("--id", required=True)
    materialization.add_argument("--request", required=True)
    for action in ("approve", "build"):
        command = sub.add_parser(action)
        command.add_argument("--plan", required=True)
        if action == "approve":
            command.add_argument("--fingerprint", required=True)
    args = parser.parse_args()
    registry = SandboxInstanceRegistry(validate_manager_root(args.root))
    # Serialize manager record changes; no lock is located inside a game's writable directory.
    with (registry.private / "backend.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        registry = SandboxInstanceRegistry(args.root)
        if args.command == "validate-root":
            result = {"version": VERSION, "root": str(registry.root), "validated": True}
        elif args.command == "relocate":
            result = relocate_registry(registry, args.previous_root)
        elif args.command == "gc":
            result = collect_unused(registry, args.minimum_days, args.apply)
        elif args.command == "inspect-client":
            result = inspect_client(args.jar)
        elif args.command == "register":
            result = dict(registry.register(args.instance, args.name, args.custom))
            status = registry.private / "status" / (result["id"] + ".json")
            if status.is_file():
                result["preparation"] = read_json(status)
        elif args.command == "policy":
            value = registry.get(args.id)
            if args.network:
                value["network"] = args.network
            if args.account:
                value["account"] = args.account
            registry.save()
            result = value
        elif args.command == "materialize":
            request_path = Path(args.request)
            if strict_directory(request_path.parent) != registry.private / "requests":
                raise SandboxError("Materialization request is outside manager requests")
            result = materialize(registry, args.id, read_json(request_path))
        elif args.command == "inspect":
            result = SandboxArtifactPlanner(registry).inspect(args.id, args.java_home, args.archive, args.native,
                                                              args.helper, args.launcher)
            result["approved"] = SandboxApprovalStore(registry).approved(result)
            result["changes"] = SandboxApprovalStore(registry).changes(result)
        else:
            builder = SandboxBundleBuilder(registry)
            plan = builder.load_plan(args.plan)
            if args.command == "approve":
                SandboxApprovalStore(registry).approve(plan, args.fingerprint)
                result = {"approved": True, "fingerprint": args.fingerprint}
            else:
                result = builder.build(plan)
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (SandboxError, OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        sys.exit(1)
