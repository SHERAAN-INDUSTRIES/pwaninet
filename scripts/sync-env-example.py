#!/usr/bin/env python3
"""Append new .env.example assignments without overwriting local settings."""
import os
import re
import sys
import tempfile
from pathlib import Path

ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")

def main():
    if len(sys.argv) != 3:
        print("Usage: sync-env-example.py EXAMPLE ENV_FILE", file=sys.stderr)
        return 2
    example_path, env_path = map(Path, sys.argv[1:])
    example = example_path.read_text()
    current = env_path.read_text() if env_path.exists() else ""
    existing = {m.group(1) for line in current.splitlines() if (m := ASSIGNMENT.match(line))}
    missing = [line for line in example.splitlines() if (m := ASSIGNMENT.match(line)) and m.group(1) not in existing]
    if not missing:
        print("Environment fields are up to date.")
        return 0
    updated = (current.rstrip() + "\n\n# New fields copied from .env.example; review values before use.\n" + "\n".join(missing) + "\n") if current.strip() else "\n".join(missing) + "\n"
    mode = (env_path.stat().st_mode & 0o777) if env_path.exists() else 0o600
    fd, temp_name = tempfile.mkstemp(prefix=f".{env_path.name}.", dir=env_path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, env_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print(f"Added {len(missing)} missing environment field(s); existing values were preserved.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
