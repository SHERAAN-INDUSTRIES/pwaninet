#!/usr/bin/env python3
"""Display an existing local service token without running setup or rotating it."""
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else '.env')
if not path.is_file():
    raise SystemExit(f'No token file at {path}; run setup first.')
for line in path.read_text().splitlines():
    if line.startswith('VASTO_SERVICE_TOKEN='):
        token = line.split('=', 1)[1].strip().strip('\"\'')
        if token:
            print('VASTO_SERVICE_TOKEN=' + token)
            break
else:
    raise SystemExit('No VASTO_SERVICE_TOKEN found; complete the database and service-account setup first.')
