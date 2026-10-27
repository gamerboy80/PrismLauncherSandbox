#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Run an authored client preflight through an already approved, sealed game app."""
import base64
import json
import os
from pathlib import Path
import select
import subprocess
import time
import uuid


def run(report, arguments, private_values=(), chunked=False, descriptor_version=1, descriptor_overrides=None, stop_on_started=False):
    manifest = report["manifest"]
    run_id = str(uuid.uuid4())
    root = Path(manifest["instanceRoot"])
    control = root / ".prism-control" / run_id
    control.mkdir(parents=True, mode=0o700)
    for name in ("to-helper", "to-manager"):
        os.mkfifo(control / name, 0o600)
    outgoing = os.open(control / "to-helper", os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
    incoming = os.open(control / "to-manager", os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
    sequence = 0

    def send(kind, payload):
        nonlocal sequence
        sequence += 1
        data = json.dumps({"version": 1, "run": run_id, "seq": sequence, "type": kind, "payload": payload}).encode() + b"\n"
        assert len(data) < 65536
        while data:
            try:
                data = data[os.write(outgoing, data):]
            except BlockingIOError:
                select.select([], [outgoing], [], 1)

    descriptor = {"version": descriptor_version, "generation": manifest["generation"], "workingDirectory": {"root": "instance", "path": "."},
                  "arguments": arguments, "environment": {}, "privateValues": list(private_values)}
    descriptor.update(descriptor_overrides or {})
    opener = Path(__file__).resolve().parents[2] / "build-macos-sandbox-foundation/OpenProbe"
    output, errors, events, buffer = bytearray(), bytearray(), [], b""
    try:
        subprocess.run([str(opener), report["app"], run_id, manifest["architecture"]], check=True, capture_output=True, timeout=20)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not select.select([incoming], [], [], .1)[0]:
                continue
            buffer += os.read(incoming, 65536)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                message = json.loads(line)
                assert message["version"] == 1 and message["run"] == run_id
                kind, payload = message["type"], message["payload"]
                events.append(kind)
                if kind == "Hello":
                    if not chunked:
                        send("Configure", descriptor)
                    else:
                        raw = json.dumps(descriptor).encode()
                        send("ConfigureStart", {"length": len(raw)})
                        for offset in range(0, len(raw), 12000):
                            send("ConfigureChunk", {"data": base64.b64encode(raw[offset:offset+12000]).decode()})
                        send("ConfigureEnd", {})
                elif kind == "Ready":
                    send("Ping", {})
                    send("Proceed", {})
                elif kind == "Started" and stop_on_started:
                    send("Stop", {})
                elif kind in ("Stdout", "Stderr"):
                    (output if kind == "Stdout" else errors).extend(base64.b64decode(payload["data"]))
                    assert len(output) + len(errors) < 4 * 1024 * 1024
                elif kind == "Error":
                    raise RuntimeError(str(payload))
                elif kind == "Exited":
                    return {"version": 1, "run": run_id, "exit": payload, "events": events,
                            "stdout": output.decode(errors="replace"), "stderr": errors.decode(errors="replace"),
                            "control": str(control), "app": report["app"]}
        send("Stop", {})
        raise RuntimeError("Authored client preflight timed out: " + repr(events))
    finally:
        os.close(incoming)
        os.close(outgoing)
