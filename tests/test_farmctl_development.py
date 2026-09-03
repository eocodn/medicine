import os
import subprocess
import tempfile
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
        self.assertIn("npm test", script)
        self.assertIn("MEDICINE_TSC_BINARY", script)
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

    def test_android_build_parallelism_is_bounded_by_repository_policy(self) -> None:
        gradle_properties = (ROOT / "android" / "gradle.properties").read_text()

        self.assertIn("org.gradle.workers.max=2", gradle_properties)
        self.assertIn("kotlin.compiler.execution.strategy=in-process", gradle_properties)

    def test_core_test_profile_avoids_full_debug_link_memory(self) -> None:
        cargo_manifest = (ROOT / "rust" / "medicine_core" / "Cargo.toml").read_text()

        self.assertIn("[profile.test]", cargo_manifest)
        self.assertIn("debug = 0", cargo_manifest)

    def test_rust_entrypoints_share_standard_image_dependency_seed_setup(self) -> None:
        helper = ROOT / "scripts" / "dev_dependencies.sh"
        self.assertTrue(helper.is_file())
        helper_text = helper.read_text()
        self.assertIn("prepare_rust_dependencies()", helper_text)
        self.assertIn("prepare_gradle_dependencies()", helper_text)
        self.assertIn("CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-1}", helper_text)

        for relative_path in (
            "scripts/check.sh",
            "scripts/run_app.sh",
            "scripts/run_ui.sh",
            "scripts/build_web.sh",
            "scripts/android_debug_build.sh",
        ):
            script = (ROOT / relative_path).read_text()
            self.assertIn(
                '. "$root/scripts/dev_dependencies.sh"',
                script,
                msg=f"{relative_path} must use the shared dependency seed helper",
            )

        for relative_path in (
            "scripts/run_app.sh",
            "scripts/run_ui.sh",
            "scripts/build_web.sh",
            "scripts/android_debug_build.sh",
        ):
            script = (ROOT / relative_path).read_text()
            self.assertIn("prepare_rust_dependencies", script)

        android = (ROOT / "scripts" / "android_debug_build.sh").read_text()
        self.assertIn("prepare_gradle_dependencies", android)

    def test_local_env_launcher_loads_only_allowlisted_secrets_from_private_file(self) -> None:
        launcher = ROOT / "scripts" / "with_local_env.py"
        self.assertTrue(launcher.is_file())

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            env_file = home / ".config" / "medicine" / "dev.env"
            env_file.parent.mkdir(parents=True)
            env_file.write_text("DATA_GO_KR_SERVICE_KEY=secret-value\n")
            env_file.chmod(0o600)

            env = os.environ.copy()
            env["HOME"] = str(home)
            env.pop("DATA_GO_KR_SERVICE_KEY", None)
            result = subprocess.run(
                [
                    "python",
                    str(launcher),
                    "--require",
                    "DATA_GO_KR_SERVICE_KEY",
                    "--",
                    "python",
                    "-c",
                    "import os; print(os.environ['DATA_GO_KR_SERVICE_KEY'])",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("secret-value", result.stdout.strip())

            env_file.write_text("UNEXPECTED_SECRET=must-not-load\n")
            env_file.chmod(0o600)
            unexpected = subprocess.run(
                ["python", str(launcher), "--", "python", "-c", "pass"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, unexpected.returncode)
            self.assertIn("unsupported local environment key", unexpected.stderr.lower())

            env_file.write_text("DATA_GO_KR_SERVICE_KEY=secret-value\n")
            env_file.chmod(0o644)
            public = subprocess.run(
                ["python", str(launcher), "--", "python", "-c", "pass"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, public.returncode)
            self.assertIn("must not be accessible by group or other users", public.stderr.lower())

    def test_compose_uses_one_standard_development_image(self) -> None:
        compose = (ROOT / "compose.yaml").read_text()
        service_names = [
            line.strip()[:-1]
            for line in compose.splitlines()
            if line.startswith("  ") and not line.startswith("    ") and line.rstrip().endswith(":")
        ]
        self.assertEqual(service_names, ["dev", "web"])
        self.assertEqual(compose.count("image: medicine/dev:local"), 2)
        self.assertEqual(compose.count("dockerfile: Dockerfile.dev"), 2)
        self.assertEqual(compose.count("- .:/workspace"), 2)
        self.assertEqual(compose.count("target: /home/build-farm"), 2)
        self.assertEqual(compose.count("create_host_path: false"), 2)
        web_service = compose.split("\n  web:\n", 1)[1]
        self.assertIn("scripts/run_web_dev.sh", web_service)

    def test_readme_keeps_farmctl_operations_out_of_repository_docs(self) -> None:
        readme = (ROOT / "README.md").read_text()
        development = (ROOT / "docs" / "development.md").read_text()

        self.assertNotIn("farmctl", readme.lower())
        self.assertNotIn("farmctl", development.lower())


if __name__ == "__main__":
    unittest.main()
