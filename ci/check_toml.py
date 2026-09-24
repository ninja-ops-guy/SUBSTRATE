#!/usr/bin/env python3
from pathlib import Path
import tomllib

for path in [*sorted(Path("config").glob("*.toml")), Path("src/Cargo.toml")]:
    with path.open("rb") as handle:
        tomllib.load(handle)
    print(f"OK {path}")
