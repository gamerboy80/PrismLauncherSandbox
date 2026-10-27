#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise two approved offline instances through the real manager. Never approve inputs."""
import argparse
import json
from pathlib import Path
import subprocess
import time


def wait_for(condition, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(.25)
    raise RuntimeError("Lifecycle condition timed out")


def exercise(manager, root, stopper, identities, output):
    processes, logs, runs = [], [], {}

    def launch(identity=None, label="manager"):
        log = (output / (label + ".log")).open("w")
        logs.append(log)
        args = [str(manager), "--dir", str(root), "--show-window"]
        if identity:
            args += ["--launch", identity, "--offline", "SandboxTest"]
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def active(identity):
        path = root / "manager/active" / (identity + ".json")
        if not path.exists() or subprocess.run([str(stopper), str(path), "--check"]).returncode:
            return None
        return json.loads(path.read_text())

    def console_ready(identity, record):
        path = root / "instances" / identity / ".prism-control" / record["run"] / "console.log"
        return path.exists() and "Created:" in path.read_text(errors="replace")

    try:
        first = launch(identities[0], "first")
        for index, identity in enumerate(identities):
            if index:
                launch(identity, "concurrent-request").wait(timeout=15)
            runs[identity] = wait_for(lambda: active(identity))
            wait_for(lambda: console_ready(identity, runs[identity]))
        assert all(active(i) for i in identities)
        launch(identities[0], "duplicate-request").wait(timeout=15)
        time.sleep(2)
        assert active(identities[0])["run"] == runs[identities[0]]["run"]
        first.terminate()
        first.wait(timeout=10)
        assert all(active(i) for i in identities), "Manager exit terminated a game"
        launch(label="restarted-manager")
        time.sleep(4)
        assert all(active(i) for i in identities)
        launch(identities[0], "reattachment-request").wait(timeout=15)
        assert active(identities[0])["run"] == runs[identities[0]]["run"]
        for identity in identities:
            subprocess.run([str(stopper), str(root / "manager/active" / (identity + ".json"))], check=True)
        exits = {}
        for identity in identities:
            path = root / "instances" / identity / ".prism-control" / runs[identity]["run"] / "exit.json"
            wait_for(path.exists, timeout=20)
            exits[identity] = json.loads(path.read_text())
            assert exits[identity]["stopped"]
        result = {"version": 1, "passed": True, "concurrentInstances": identities,
                  "duplicateRunPrevented": True, "gamesSurvivedManagerExit": True,
                  "restartRecognizedSameRun": True, "verifiedDetachedStop": True, "exits": exits}
        (output / "lifecycle.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    finally:
        for identity in runs:
            if active(identity):
                subprocess.run([str(stopper), str(root / "manager/active" / (identity + ".json"))])
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        for log in logs:
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manager", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stopper", type=Path, required=True)
    parser.add_argument("--instance", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert len(args.instance) == 2
    args.output.mkdir(parents=True, exist_ok=True)
    exercise(args.manager.resolve(), args.root.resolve(), args.stopper.resolve(), args.instance, args.output.resolve())
