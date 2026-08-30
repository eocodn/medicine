import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FarmctlDevelopmentTest(unittest.TestCase):
    def test_standard_development_image_bakes_dependencies_without_source(self) -> None:
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
        self.assertIn(
            "COPY ui/package.json ui/package-lock.json /opt/medicine-ui-tools/",
            dockerfile,
        )
        self.assertIn("npm ci", dockerfile)
        self.assertIn(
            "COPY rust/medicine_core/Cargo.toml rust/medicine_core/Cargo.lock /opt/medicine-rust/",
            dockerfile,
        )
        self.assertIn("cargo fetch --locked", dockerfile)
        self.assertNotIn("MEDICINE_CARGO_TARGET_SEED", dockerfile)
        self.assertIn("android/app/gradle.lockfile", dockerfile)
        self.assertIn("android/gradle/prefetch-dependencies.init.gradle.kts", dockerfile)
        self.assertIn("prefetchLockedDependencies prefetchMedicineAapt2", dockerfile)

        for source_path in (
            "ui/src",
            "ui/public",
            "rust/medicine_core/src",
            "android/app/src",
            "medicine_canonical",
            "medicine_reference",
            "browser_ocr",
        ):
            self.assertNotIn(
                f"COPY {source_path}",
                dockerfile,
                msg=f"Dockerfile.dev must mount source instead of copying {source_path}",
            )

    def test_android_dependency_prefetch_materializes_external_artifacts(self) -> None:
        script = (ROOT / "android" / "gradle" / "prefetch-dependencies.init.gradle.kts").read_text()

        self.assertIn("ModuleComponentIdentifier", script)
        self.assertIn("prefetchLockedDependencies", script)
        self.assertIn("configuration.incoming.artifactView", script)
        self.assertIn("prefetchMedicineAapt2", script)
        self.assertIn("verification-metadata.xml", script)
        self.assertIn('"com.android.tools.build:aapt2:$aapt2Version:linux"', script)
        self.assertNotIn("aapt2:9.2.0-15009934:linux", script)
        self.assertIn("deactivateDependencyLocking", script)

    def test_repository_native_check_entrypoint_is_docker_independent(self) -> None:
        script_path = ROOT / "scripts" / "check.sh"
        script = script_path.read_text()

        self.assertNotIn("docker compose", script)
        self.assertNotIn("farmctl", script)
        self.assertIn("cargo build --locked --release", script)
        self.assertIn("python -m unittest discover -s tests -v", script)
        self.assertNotIn("npm ci", script)
        self.assertIn("npm run check", script)
        self.assertIn("MEDICINE_TSC_BINARY", script)
        self.assertIn("CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-1}", script)
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
        self.assertIn("target: /home/build-farm", dev_service)
        self.assertIn("create_host_path: false", dev_service)

    def test_readme_keeps_farmctl_operations_out_of_repository_docs(self) -> None:
        readme = (ROOT / "README.md").read_text()

        self.assertNotIn("farmctl", readme.lower())


if __name__ == "__main__":
    unittest.main()
