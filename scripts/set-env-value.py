#!/usr/bin/env python3
"""Safely update one key from stdin without exposing its value in arguments."""
import os
import re
import sys
import tempfile
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit("Usage: set-env-value.py ENV_FILE KEY")
env_path, key = Path(sys.argv[1]), sys.argv[2]
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
    raise SystemExit("Invalid environment variable name.")
value = sys.stdin.read().strip()
if not value or "\n" in value or "\r" in value:
    raise SystemExit("A non-empty, single-line value is required.")
current = env_path.read_text() if env_path.exists() else ""
pattern = re.compile(rf"^{re.escape(key)}=")
lines, replaced = [], False
for line in current.splitlines():
    if pattern.match(line):
        if not replaced:
            lines.append(f"{key}={value}")
            replaced = True
    else:
        lines.append(line)
if not replaced:
    lines.extend(("", f"{key}={value}"))
mode = (env_path.stat().st_mode & 0o777) if env_path.exists() else 0o600
fd, temp_name = tempfile.mkstemp(prefix=f".{env_path.name}.", dir=env_path.parent)
try:
    with os.fdopen(fd, "w") as stream:
        stream.write("\n".join(lines).rstrip() + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temp_name, mode & 0o600)
    os.replace(temp_name, env_path)
finally:
    if os.path.exists(temp_name):
        os.unlink(temp_name)
