#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Run an already approved/built authored fixture. This program never approves or signs code."""
import argparse
import base64
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import time
import uuid


def exercise(report):
    app = Path(report["app"])
    manifest = report["manifest"]
    instance = Path(manifest["instanceRoot"])
    run = str(uuid.uuid4())
    control = instance / ".prism-control" / run
    control.mkdir(mode=0o700, parents=True)
    for name in ("to-helper", "to-manager"):
        os.mkfifo(control / name, 0o600)
    outgoing = os.open(control / "to-helper", os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
    incoming = os.open(control / "to-manager", os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
    root = instance.parent.parent
    sentinels = [root / "unrelated-sentinel", root / "manager" / "account-sentinel",
                 root / "instances" / "sibling" / "world-sentinel", app / "Contents" / "write-sentinel"]
    # Never alter the sealed app. Use its existing manifest as the read sentinel, and a new path for writes.
    sentinels[-1] = app / "Contents/Resources/bundle-manifest.json"
    for path in sentinels[:-1]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("authored sentinel")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    port = listener.getsockname()[1]
    native = str(app / "Contents/Resources/natives/libSandboxProbe.dylib")
    java_args = ["-jar", str(app / "Contents/Resources/launchers/SandboxProbe.jar"), native, str(port),
                 str(instance)] + [str(p) for p in sentinels]
    descriptor = {"version": 1, "generation": manifest["generation"], "workingDirectory": {"root": "instance", "path": "."},
                  "arguments": java_args, "environment": {}}
    sequence = 0

    def send(kind, payload):
        nonlocal sequence
        sequence += 1
        value = {"version": 1, "run": run, "seq": sequence, "type": kind, "payload": payload}
        data = json.dumps(value).encode() + b"\n"
        assert len(data) < 65536
        offset = 0
        while offset < len(data):
            try:
                offset += os.write(outgoing, data[offset:])
            except BlockingIOError:
                select.select([], [outgoing], [], 1)

    opener = Path(__file__).resolve().parents[2] / "build-macos-sandbox-foundation/OpenProbe"
    if opener.exists():
        opened = subprocess.run([str(opener), str(app), run, manifest["architecture"]], check=True, capture_output=True, text=True)
        print(opened.stdout.strip(), flush=True)
    else:
        subprocess.run(["/usr/bin/open", "-n", str(app), "--args", run], check=True, capture_output=True)
    buffer = b""
    output, errors, events = bytearray(), bytearray(), []
    exited = None
    deadline = time.monotonic() + 45
    try:
        while time.monotonic() < deadline and exited is None:
            ready, _, _ = select.select([incoming], [], [], 0.2)
            if not ready:
                continue
            buffer += os.read(incoming, 65536)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                message = json.loads(line)
                assert message["version"] == 1 and message["run"] == run
                kind, payload = message["type"], message["payload"]
                events.append(kind)
                if kind == "Hello":
                    send("Configure", descriptor)
                elif kind == "Ready":
                    send("Proceed", {})
                elif kind in ("Stdout", "Stderr"):
                    (output if kind == "Stdout" else errors).extend(base64.b64decode(payload["data"]))
                elif kind == "Error":
                    raise RuntimeError(str(payload))
                elif kind == "Exited":
                    exited = payload
        if exited is None:
            send("Stop", {})
            raise RuntimeError("Helper timed out; events=" + repr(events) + "; stderr=" + errors.decode(errors="replace"))
        text = output.decode(errors="replace")
        values = dict(line.strip().split("=", 1) for line in text.splitlines() if "=" in line)
        expected = {"java.inside": "true", "jni.inside": "true", "child.outside": "false", "interpreter.outside": "false", "probe.complete": "true"}
        for i in range(3, 7):
            for key in ("java.outside.", "jni.outside.", "java.read.", "jni.read."):
                # A process is allowed to read its own sealed app, but cannot modify it.
                expected[key + str(i)] = "true" if i == 6 and key in ("java.read.", "jni.read.") else "false"
        for key in ("java.connect", "jni.connect", "java.listen", "java.dns", "jni.listen", "jni.dns"):
            expected[key] = "true" if manifest["network"] == "allowed" else "false"
        failures = {k: {"expected": v, "actual": values.get(k)} for k, v in expected.items() if values.get(k) != v}
        if exited["exitCode"] != 0:
            failures["exit"] = exited
        return {"version": 1, "network": manifest["network"], "architecture": manifest["architecture"],
                "app": str(app), "passed": not failures, "failures": failures, "values": values,
                "events": events, "stderr": errors.decode(errors="replace")}
    finally:
        os.close(incoming)
        os.close(outgoing)
        listener.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = exercise(json.loads(args.report.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
