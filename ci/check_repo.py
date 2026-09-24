#!/usr/bin/env python3
from pathlib import Path
import sys

failures = []

def require(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)

cargo = Path("src/Cargo.toml").read_text()
configd = Path("src/bin/omarchy-configd.rs").read_text()
cgroupd = Path("src/bin/omarchy-cgroupd.rs").read_text()
rollback = Path("src/rollback.rs").read_text()

require("ctrlc =" in cargo, "ctrlc dependency must be declared")
require("read_events_timeout" not in cgroupd, "unsupported inotify timeout API must not be used")
require("Type=notify" not in Path("systemd/omarchy-configd.service").read_text(), "configd must not claim sd_notify readiness")
require("Type=notify" not in Path("systemd/omarchy-cgroupd.service").read_text(), "cgroupd must not claim sd_notify readiness")
require("rename(&live_tmp, &live_file)" in configd, "config publish must end in same-filesystem atomic rename")
require("MAX_PAYLOAD_BYTES" in configd, "config socket must bound payload size")
require("/api/chat/v2/sessions/" not in rollback, "unsupported Ollama session-delete API must not be used")
require('"keep_alive": 0' in rollback and "/api/generate" in rollback, "rollback must use documented Ollama unload behavior")

if failures:
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    raise SystemExit(1)
print("repository policy checks passed")
