#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Authored protocol/lifecycle checks against an already approved fixture bundle."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from client_probe import run


def exercise(report):
    client = Path(report["manifest"]["instanceRoot"]) / "client/client.jar"
    secret = "authored-fragmented-redaction-fixture-219438"
    results = {}
    result = run(report, ["-jar", str(client), "--secret", secret], private_values=[secret], chunked=True)
    log = (Path(result["control"]) / "console.log").read_text()
    assert result["exit"]["exitCode"] == 0 and "Pong" in result["events"]
    assert secret not in result["stdout"] + log and "<redacted>" in log
    results["chunkingPingRedaction"] = result
    try:
        run(report, ["-version"], descriptor_version=99)
    except RuntimeError as error:
        assert "UNSUPPORTED_DESCRIPTOR_VERSION" in str(error)
        results["invalidDescriptor"] = str(error)
    else:
        raise AssertionError("Unsupported descriptor was accepted")
    results["unreadInputStop"] = run(report, ["60"], chunked=True, stop_on_started=True,
        descriptor_overrides={"mode": "command", "program": {"root": "system", "path": "/bin/sleep"},
                              "launchScript": "x" * 524288})
    assert results["unreadInputStop"]["exit"]["stopped"]
    results["outputFloodStop"] = run(report, [], stop_on_started=True,
        descriptor_overrides={"mode": "command", "program": {"root": "system", "path": "/usr/bin/yes"}})
    assert results["outputFloodStop"]["exit"]["stopped"]
    started = time.monotonic()
    result = run(report, ["-c", '(trap "" TERM; exec /bin/sleep 60) & printf "descendant=%s\\n" "$!"; /bin/sleep 0.3; exit 0'],
        descriptor_overrides={"mode": "command", "program": {"root": "system", "path": "/bin/sh"}})
    elapsed = time.monotonic() - started
    assert result["exit"]["exitCode"] == 0 and elapsed >= 9, (elapsed, result)
    pid = int(next(line.split("=", 1)[1] for line in result["stdout"].splitlines() if line.startswith("descendant=")))
    assert 0 < pid < 2**31
    # Observation only: never signal a PID supplied by a protocol/log message.
    state = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
    assert not state or state.startswith("Z"), (pid, state)
    result["elapsedSeconds"] = elapsed
    result["descendantStateAfterExit"] = state or "absent"
    results["descendantEscalation"] = result
    return {"version": 1, "passed": True, "results": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = exercise(json.loads(args.report.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Authored protocol, log flood, unread stdin and descendant escalation: PASS")
