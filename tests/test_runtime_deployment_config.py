import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

class RuntimeDeploymentConfigTest(unittest.TestCase):
    def test_development_dockerfiles_do_not_copy_source_code(self) -> None:
        dockerfiles = (
            "Dockerfile",
            "Dockerfile.app",
            "Dockerfile.test",
            "Dockerfile.ui",
            "Dockerfile.web",
            "Dockerfile.android",
            "ocr_runtime/Dockerfile",
        )
        forbidden_source_patterns = (
            r"(?m)^COPY\s+medicine_(?:canonical|reference)\b",
            r"(?m)^COPY\s+rust/medicine_core/src\b",
            r"(?m)^COPY\s+ui/src\b",
            r"(?m)^COPY\s+ui/public\b",
            r"(?m)^COPY\s+android/app\b",
        )

        for dockerfile in dockerfiles:
            contents = Path(dockerfile).read_text()
            for pattern in forbidden_source_patterns:
                self.assertIsNone(
                    re.search(pattern, contents),
                    msg=f"{dockerfile} must use bind-mounted source instead of COPY: {pattern}",
                )

    def test_android_and_developer_controls_use_rust_without_python_runtime(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        compose = Path("compose.yaml").read_text()
        rust_build = Path("scripts/build_android_rust.sh").read_text()

        self.assertNotIn("chaquopy", gradle.lower())
        self.assertNotIn("medicine_canonical", gradle)
        self.assertTrue(Path("rust/medicine_core/src/bin/medicine_agentctl.rs").is_file())
        self.assertIn("--lib", rust_build)
        app_service = compose.split("\n  app:\n", 1)[1].split("\n  web:\n", 1)[0]
        app_dockerfile = Path("Dockerfile.app").read_text()
        ui_dockerfile = Path("Dockerfile.ui").read_text()
        self.assertIn("dockerfile: Dockerfile.app", app_service)
        self.assertIn("- .:/app", app_service)
        self.assertIn('ENTRYPOINT ["sh", "/app/scripts/run_app.sh"]', app_dockerfile)
        app_runner = Path("scripts/run_app.sh").read_text()
        self.assertIn("cargo build --locked --release", app_runner)
        self.assertIn("--features agentctl", app_runner)
        self.assertIn("--bin medicine-agentctl", app_runner)
        self.assertNotIn("python", app_dockerfile.lower())
        ui_runner = Path("scripts/run_ui.sh").read_text()
        self.assertIn("cargo build --locked --release", ui_runner)
        self.assertIn("--features agentctl,web", ui_runner)
        self.assertIn("--bin medicine-agentctl", ui_runner)
        self.assertIn("--bin medicine-core-web", ui_runner)
        self.assertIn("npm run build", ui_runner)
        self.assertNotIn("python", ui_dockerfile.lower())
    def test_shared_ui_is_typescript_authoritative_and_consumes_one_dist(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        android_dockerfile = Path("Dockerfile.android").read_text()
        web_dockerfile = Path("Dockerfile.web").read_text()
        ui_dockerfile = Path("Dockerfile.ui").read_text()

        self.assertTrue(Path("ui/package.json").is_file())
        self.assertTrue(Path("ui/tsconfig.json").is_file())
        self.assertTrue(Path("ui/src/app.ts").is_file())
        self.assertTrue(Path("ui/src/ocr-intake.ts").is_file())
        self.assertIn("PrepareSharedUiAssets", gradle)
        self.assertIn("assets.addGeneratedSourceDirectory", gradle)
        self.assertNotIn('rootProject.file("../ui/dist").absolutePath', gradle)
        self.assertIn("AS ui-toolchain", android_dockerfile)
        self.assertIn("MEDICINE_TSC_BINARY", android_dockerfile)
        for dockerfile, runner in (
            (web_dockerfile, Path("scripts/build_web.sh").read_text()),
            (ui_dockerfile, Path("scripts/run_ui.sh").read_text()),
        ):
            self.assertIn("COPY ui/package.json ui/package-lock.json /opt/medicine-ui-tools/", dockerfile)
            self.assertIn("npm ci", dockerfile)
            self.assertIn("npm run build", runner)
            self.assertIn("MEDICINE_STATIC_DIR=/app/ui/dist", dockerfile)
            self.assertNotIn("COPY ui/dist", dockerfile)
    def test_rust_runtime_contract_matches_python_reference_contract(self) -> None:
        rust_runtime = Path("rust/medicine_core/src/reference_contract.rs").read_text()
        python_runtime = Path("medicine_reference/reference_contracts/v1.py").read_text()

        rust_contract = re.search(r'REFERENCE_CONTRACT_MAJOR:\s*i32\s*=\s*([0-9]+)', rust_runtime)
        runtime_contract = re.search(r'REFERENCE_CONTRACT_MAJOR = ([0-9]+)', python_runtime)
        self.assertIsNotNone(rust_contract)
        self.assertIsNotNone(runtime_contract)
        self.assertEqual(rust_contract.group(1), runtime_contract.group(1))
    def test_canonical_reviewed_corpora_are_included_in_built_package(self) -> None:
        config = tomllib.loads(Path("pyproject.toml").read_text())
        package_data = config["tool"]["setuptools"]["package-data"]
        self.assertIn("medicine_canonical", package_data)
        self.assertIn("data/*.tsv", package_data["medicine_canonical"])
    def test_r2_smoke_workflow_verifies_private_bucket_write_path(self) -> None:
        workflow = Path(".github/workflows/r2-smoke.yml").read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("actions/setup-python@v5", workflow)
        self.assertIn("python-version: '3.13'", workflow)
        for secret in (
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_ENDPOINT",
            "R2_BUCKET",
        ):
            self.assertIn(f"secrets.{secret}", workflow)
        self.assertIn("put_object", workflow)
        self.assertIn("head_object", workflow)
        self.assertIn("delete_object", workflow)
        self.assertIn("medicine-r2-smoke/", workflow)
        self.assertIn("r2-public-audit", workflow)
    def test_android_excludes_mfds_remark_registry_and_requires_materialized_semantics(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        verifier = Path("medicine_reference/reference_contracts/v1.py").read_text()
        self.assertNotIn('include("medicine_reference/**/*.py")', gradle)
        self.assertNotIn("mfds_remark_registry.tsv", gradle)
        self.assertIn('"reference_criterion_semantics"', verifier)
        self.assertIn("_verify_schema(con)", verifier)
    def test_ui_service_runs_as_the_host_user_for_bind_mounted_screenshots(self) -> None:
        compose = Path("compose.yaml").read_text()
        ui_service = compose.split("\n  ui:\n", 1)[1].split("\n  test:\n", 1)[0]

        self.assertIn('user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"', ui_service)
    def test_writable_bind_mount_services_run_as_the_host_user(self) -> None:
        compose = Path("compose.yaml").read_text()
        service_names = (
            "canonical",
            "app",
            "web",
            "ui",
            "test",
            "browser-test",
            "android",
        )
        service_starts = [match.start() for match in re.finditer(r"(?m)^  [a-z0-9-]+:\n", compose)]

        for name in service_names:
            start = compose.index(f"  {name}:\n")
            later_starts = [candidate for candidate in service_starts if candidate > start]
            end = min(later_starts, default=len(compose))
            service = compose[start:end]
            self.assertIn(
                'user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"',
                service,
                msg=f"{name} must not write to the repository bind mount as root",
            )

        self.assertNotIn("${MEDICINE_UID:-1000}:${MEDICINE_GID:-1000}", compose)
    def test_android_uses_per_worktree_host_owned_gradle_state(self) -> None:
        compose = Path("compose.yaml").read_text()
        android_service = compose.split("\n  android:\n", 1)[1]

        self.assertIn('user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"', android_service)
        self.assertIn("HOME: /tmp", android_service)
        self.assertIn("GRADLE_USER_HOME: /workspace/.android-gradle-cache", android_service)
        self.assertNotIn("android-gradle-cache:/opt/gradle-cache", android_service)
        self.assertNotIn("\nvolumes:\n  android-gradle-cache:\n", compose)
    def test_android_default_compose_build_runs_gradle_without_mobile_builder(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        compose = (repo_root / "compose.yaml").read_text()
        android_service = compose.split("\n  android:\n", 1)[1]
        build_script = repo_root / "scripts" / "android_debug_build.sh"

        self.assertIn('command: ["sh", "/workspace/scripts/android_debug_build.sh"]', android_service)

        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            log_path = Path(temp_dir) / "calls.log"
            gradle_stub = bin_dir / "gradle"
            gradle_stub.write_text(
                "#!/bin/sh\n"
                "printf 'gradle:%s\\n' \"$*\" >> \"$ANDROID_BUILD_TEST_LOG\"\n"
            )
            gradle_stub.chmod(0o755)

            env = os.environ.copy()
            env["ANDROID_BUILD_TEST_LOG"] = str(log_path)
            env["MEDICINE_GRADLE_BIN"] = str(gradle_stub)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["sh", str(build_script)],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log_path.read_text().splitlines()

        self.assertEqual(
            calls,
            ["gradle:--no-daemon --dependency-verification strict testDebugUnitTest assembleDebug"],
        )
    def test_local_web_keeps_ocr_as_an_external_runtime_boundary(self) -> None:
        compose = Path("compose.yaml").read_text()
        web_service = compose.split("\n  web:\n", 1)[1].split("\n  ui:\n", 1)[0]
        dockerfile = Path("Dockerfile.web").read_text()
        web_binary = Path("rust/medicine_core/src/bin/medicine_core_web.rs").read_text()

        self.assertIn("dockerfile: Dockerfile.web", web_service)
        self.assertIn('user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"', web_service)
        self.assertIn("HOME: /tmp", web_service)
        self.assertNotIn("PYTHONDONTWRITEBYTECODE", web_service)
        self.assertIn("AS rust-toolchain", dockerfile)
        builder = Path("scripts/build_web.sh").read_text()
        runner = Path("scripts/run_web.sh").read_text()
        dev_runner = Path("scripts/run_web_dev.sh").read_text()
        self.assertIn("cargo build --locked --release", builder)
        self.assertIn("--features web", builder)
        self.assertIn("--bin medicine-core-web", builder)
        self.assertNotIn("cargo build", runner)
        self.assertIn("medicine-core-web", runner)
        self.assertIn("build_web.sh", dev_runner)
        self.assertIn("run_web.sh", dev_runner)
        self.assertNotIn("AS ocr-assets", dockerfile)
        self.assertNotIn("medicine-ocr-assets", dockerfile)
        self.assertIn("MEDICINE_OCR_ASSETS_DIR", web_binary)
        self.assertIn("--ocr-assets-dir", web_binary)
        self.assertIn("MEDICINE_REFERENCE_TRUST_MANIFEST=/opt/medicine-reference-trust.json", dockerfile)
        self.assertIn("COPY deploy/reference-signing-trusted-keys.json /opt/medicine-reference-trust.json", dockerfile)
        self.assertIn("MEDICINE_STATIC_DIR=/app/ui/dist", dockerfile)
        self.assertNotIn("COPY ui/src", dockerfile)
        self.assertNotIn("COPY rust/medicine_core/src", dockerfile)
        self.assertIn('ENTRYPOINT ["sh", "/app/scripts/run_web_dev.sh"]', dockerfile)
        self.assertNotIn("python", dockerfile.lower())

    def test_compose_named_runner_scripts_are_retired(self) -> None:
        for retired in (
            "scripts/app_compose_run.sh",
            "scripts/test_compose_run.sh",
            "scripts/ui_compose_run.sh",
            "scripts/web_compose_run.sh",
            "scripts/android_compose_build.sh",
        ):
            self.assertFalse(Path(retired).exists(), retired)
    def test_development_runtime_uses_signed_reference_channel_by_default(self) -> None:
        compose = Path("compose.yaml").read_text()
        web_service = compose.split("\n  web:\n", 1)[1].split("\n  ui:\n", 1)[0]
        web_binary = Path("rust/medicine_core/src/bin/medicine_core_web.rs").read_text()
        web_runtime = Path("rust/medicine_core/src/web.rs").read_text()
        app_commands = Path(
            "rust/medicine_core/src/bin/agentctl/app_commands/support.rs"
        ).read_text()

        self.assertIn(
            "MEDICINE_CANONICAL_DB: ${MEDICINE_CANONICAL_DB:-}",
            web_service,
        )
        self.assertIn("MEDICINE_REFERENCE_DIR: ${MEDICINE_REFERENCE_DIR:-data/reference}", web_service)
        self.assertIn("MEDICINE_REFERENCE_UPDATE_BASE_URL", web_service)
        self.assertIn("open_reference_channel", web_binary)
        self.assertIn("prepare_runtime.prepare()", web_binary)
        self.assertIn("schedule_reference_update", web_binary)
        self.assertIn("update_runtime.check_for_update()", web_runtime)
        self.assertIn('const DEFAULT_REFERENCE_DIR: &str = "data/reference";', web_binary)
        self.assertNotIn('const DEFAULT_CANONICAL_DB: &str = "data/db/mobile.sqlite";', web_binary)
        self.assertIn('const DEFAULT_CANONICAL_DB: &str = "data/db/mobile.sqlite";', app_commands)
        self.assertNotIn('const DEFAULT_CANONICAL_DB: &str = "data/db/canonical.sqlite";', app_commands)
    def test_retired_legacy_etl_is_not_packaged_or_exposed(self) -> None:
        compose = Path("compose.yaml").read_text()
        dockerfile = Path("Dockerfile").read_text()
        ui_dockerfile = Path("Dockerfile.ui").read_text()
        pyproject = Path("pyproject.toml").read_text()

        self.assertNotIn("  dur:\n", compose)
        self.assertNotIn("  catalog:\n", compose)
        self.assertNotIn("COPY medicine_dur", dockerfile)
        self.assertNotIn("COPY medicine_catalog", dockerfile)
        self.assertNotIn("COPY medicine_dur", ui_dockerfile)
        self.assertNotIn("COPY medicine_catalog", ui_dockerfile)
        self.assertNotIn('medicine-dur =', pyproject)
        self.assertNotIn('name = "medicine-dur"', pyproject)
        self.assertNotIn('medicine-catalog =', pyproject)
        self.assertNotIn('medicine_dur*', pyproject)
        self.assertNotIn('medicine_catalog*', pyproject)
