#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise prepared acceptance instances. Never approve an inventory or use an account."""
import argparse
import json
from pathlib import Path
import subprocess
import time


def exercise(manager, root, profiles, stopper, output):
    results = []
    for profile in profiles:
        identity = profile["instance"]
        log_path = output / (profile["label"] + "-manager.log")
        started = time.time()
        result = {"label": profile["label"], "instance": identity, "status": "timeout", "log": str(log_path)}
        old = set((root / "staging").glob("inventory-*/plan.json"))
        control = root / "instances" / identity / ".prism-control"
        previous_exits = set(control.glob("*/exit.json"))
        with log_path.open("w") as log:
            process = subprocess.Popen([str(manager), "--dir", str(root), "--launch", identity,
                                        "--offline", "SandboxTest", "--show-window"], stdout=log, stderr=subprocess.STDOUT)
            armed_at = None
            try:
                while time.time() - started < 100:
                    plans = set((root / "staging").glob("inventory-*/plan.json")) - old
                    for path in plans:
                        try:
                            plan = json.loads(path.read_text())
                        except (OSError, ValueError):
                            continue
                        if plan["instance"] != identity:
                            continue
                        result["plan"] = str(path)
                        result["fingerprint"] = plan["approvalFingerprint"]
                        approved = root / "manager/approvals" / (plan["approvalFingerprint"] + ".json")
                        if not approved.exists():
                            result["status"] = "review_required"
                    completed = set(control.glob("*/exit.json")) - previous_exits
                    if completed:
                        summary = next(iter(completed))
                        result["exit"] = json.loads(summary.read_text())
                        result["console"] = summary.with_name("console.log").read_text(errors="replace")[-100000:]
                        result["status"] = "exited"
                        break
                    if result["status"] == "review_required":
                        break
                    active = root / "manager/active" / (identity + ".json")
                    if active.exists() and active.stat().st_mtime >= started:
                        armed_at = armed_at or time.time()
                        if time.time() - armed_at > 25:
                            # StopProbe verifies uid, exact prepared bundle path, PID and start time.
                            result["stopStatus"] = subprocess.run([str(stopper), str(active)]).returncode
                            armed_at = time.time() + 1000
                    if process.poll() is not None:
                        result["status"] = "manager_exited"
                        result["managerExit"] = process.returncode
                        break
                    text = log_path.read_text(errors="replace")
                    if any('Task "LaunchController(' in line and 'failed:' in line for line in text.splitlines()):
                        result["status"] = "preparation_failed"
                        break
                    time.sleep(.25)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
        results.append(result)
        (output / "manager-smoke.json").write_text(json.dumps({"version": 1, "results": results}, indent=2) + "\n")
        print(profile["label"], result["status"], result.get("fingerprint", ""), flush=True)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manager", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--stopper", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profile", action="append")
    args = parser.parse_args()
    profiles = json.loads((args.root / "reports/compatibility-inputs.json").read_text())["profiles"]
    if args.profile:
        profiles = [p for p in profiles if p["label"] in args.profile]
    args.output.mkdir(parents=True, exist_ok=True)
    exercise(args.manager.resolve(), args.root.resolve(), profiles, args.stopper.resolve(), args.output.resolve())
