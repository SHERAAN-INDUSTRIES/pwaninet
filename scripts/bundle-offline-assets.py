"""Bundle runtime assets for the ORIGINAL saved pages, never alternative page layouts."""
from pathlib import Path
import shutil
import sys
ROOT = Path(__file__).resolve().parents[1]
for name in ('js/offline-view.js', 'js/bootstrap.bundle.min.js'):
    target = ROOT / 'www/offline-assets' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'static' / name, target)
if '--android' in sys.argv:
    target = ROOT / 'android/app/src/main/assets/public'
    # Remove only the discarded reader files created during this change.
    for name in ('reader.js', 'reader.css'):
        (target / name).unlink(missing_ok=True)
    shutil.copytree(ROOT / 'www', target, dirs_exist_ok=True)
print('Prepared snapshot runtime assets.')
