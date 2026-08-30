import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FarmctlDevelopmentTest(unittest.TestCase):
    def test_standard_development_image_is_source_independent(self) -> None:
        dockerfile = (ROOT / "Dockerfile.dev").read_text()

        self.assertIn("WORKDIR /workspace", dockerfile)
        self.assertIn("CARGO_HOME=/home/build-farm/.cargo", dockerfile)
        self.assertIn("CARGO_TARGET_DIR=/home/build-farm/target", dockerfile)
        self.assertIn("GRADLE_USER_HOME=/home/build-farm/.gradle", dockerfile)
        self.assertIn("HOME=/home/build-farm", dockerfile)
        self.assertIn("NPM_CONFIG_CACHE=/home/build-farm/.npm", dockerfile)
        self.assertIn("ANDROID_HOME=/opt/android-sdk", dockerfile)
        self.assertIn("ANDROID_USER_HOME=/home/build-farm/.android", dockerfile)
        self.assertIn("aarch64-linux-android", dockerfile)
        self.assertIn("chromium", dockerfile)
        self.assertIn("mkdir -p /home/build-farm", dockerfile)

        for line in dockerfile.splitlines():
            stripped = line.strip()
            if not re.match(r"^(COPY|ADD)\b", stripped):
                continue
            self.assertIn(
                "--from=",
                stripped,
                msg="Dockerfile.dev may copy toolchain files only; repository inputs stay mounted",
            )

    def test_repository_native_check_entrypoint_is_docker_independent(self) -> None:
        script_path = ROOT / "scripts" / "check.sh"
        script = script_path.read_text()

        self.assertNotIn("docker compose", script)
        self.assertNotIn("farmctl", script)
        self.assertIn("cargo build --locked --release", script)
        self.assertIn("python -m unittest discover -s tests -v", script)
        self.assertIn("npm ci", script)
        self.assertIn("npm run check", script)
        self.assertIn(
            "./gradlew --no-daemon --dependency-verification strict testDebugUnitTest lintDebug assembleDebug",
            script,
        )

        invalid = subprocess.run(
            ["sh", str(script_path), "not-a-check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, invalid.returncode)
        self.assertIn("usage:", invalid.stderr.lower())

    def test_compose_exposes_the_same_standard_development_image(self) -> None:
        compose = (ROOT / "compose.yaml").read_text()
        dev_service = compose.split("\n  dev:\n", 1)[1].split("\n  canonical:\n", 1)[0]
        self.assertIn("    image: medicine/dev:local", dev_service)
        self.assertIn("      dockerfile: Dockerfile.dev", dev_service)
        self.assertIn("      - .:/workspace", dev_service)


if __name__ == "__main__":
    unittest.main()
