"""Exercise debug-script cleanup after its working directory changes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DebugBuildRestoreTests(unittest.TestCase):
    def run_build(self, fail):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            android = root / 'android'
            android.mkdir()
            shutil.copy(ROOT / 'android/build-app.sh', android / 'build-app.sh')
            files = {
                'capacitor.config.json': json.dumps({'appId': 'com.vasto.app', 'server': {'cleartext': False}}),
                'android/app/src/main/AndroidManifest.xml': '<application android:usesCleartextTraffic="false"/>',
                'android/app/src/main/assets/capacitor.config.json': '{"original": true}',
                'android/capacitor-cordova-android-plugins/src/main/AndroidManifest.xml': '<application/>',
            }
            for name, content in files.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            binaries = root / 'bin'
            binaries.mkdir()
            commands = {
                binaries / 'npx': '#!/bin/bash\necho changed > android/app/src/main/assets/capacitor.config.json\necho changed > android/capacitor-cordova-android-plugins/src/main/AndroidManifest.xml\n',
                binaries / 'adb': '#!/bin/bash\n[ "$1" != uninstall ] || exit 99\nexit 0\n',
                android / 'gradlew': '#!/bin/bash\nexit ' + ('7' if fail else '0') + '\n',
            }
            for path, content in commands.items():
                path.write_text(content)
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(binaries) + ':' + os.environ['PATH'], ADB=str(binaries / 'adb'))
            result = subprocess.run(['bash', 'build-app.sh'], cwd=android, env=env, input='\n', text=True, capture_output=True)
            self.assertEqual(result.returncode, 7 if fail else 0, result.stderr)
            for name, content in files.items():
                self.assertEqual((root / name).read_text(), content, name)
            self.assertFalse((android / 'capacitor.config.json').exists())

    def test_success_restores_from_android_directory(self):
        self.run_build(False)

    def test_failed_build_restores_from_android_directory(self):
        self.run_build(True)


if __name__ == '__main__':
    unittest.main()
