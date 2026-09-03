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


    def test_android_and_developer_controls_use_rust_runtime(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        rust_build = Path("scripts/build_android_rust.sh").read_text()
        app_runner = Path("scripts/run_app.sh").read_text()
        ui_runner = Path("scripts/run_ui.sh").read_text()

        self.assertNotIn("chaquopy", gradle.lower())
        self.assertTrue(Path("rust/medicine_core/src/bin/medicine_agentctl.rs").is_file())
        self.assertIn("--lib", rust_build)
        self.assertIn("cargo build --locked --release", app_runner)
        self.assertIn("--features agentctl", app_runner)
        self.assertIn("--bin medicine-agentctl", app_runner)
        self.assertIn("cargo build --locked --release", ui_runner)
        self.assertIn("--features agentctl-web", ui_runner)
        cargo = tomllib.loads(Path("rust/medicine_core/Cargo.toml").read_text())
        self.assertIn("agentctl", cargo["features"]["agentctl-web"])
        self.assertIn("web", cargo["features"]["agentctl-web"])
        self.assertIn("--bin medicine-agentctl", ui_runner)
        self.assertIn("--bin medicine-core-web", ui_runner)
        self.assertIn("npm run build", ui_runner)

    def test_shared_ui_is_typescript_authoritative_and_built_by_shared_tools(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        dockerfile = Path("Dockerfile.dev").read_text()

        self.assertTrue(Path("ui/package.json").is_file())
        self.assertTrue(Path("ui/tsconfig.json").is_file())
        self.assertTrue(Path("ui/src/app.ts").is_file())
        self.assertTrue(Path("ui/src/ocr-intake.ts").is_file())
        self.assertIn("PrepareSharedUiAssets", gradle)
        self.assertIn("assets.addGeneratedSourceDirectory", gradle)
        self.assertIn("COPY ui/package.json ui/package-lock.json /opt/medicine-ui-tools/", dockerfile)
        self.assertIn("npm ci", dockerfile)
        self.assertIn("MEDICINE_TSC_BINARY", dockerfile)
        self.assertIn("npm run build", Path("scripts/build_web.sh").read_text())
        self.assertIn("npm run build", Path("scripts/run_ui.sh").read_text())

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


    def test_compose_services_share_host_owned_source_and_development_home(self) -> None:
        compose = Path("compose.yaml").read_text()
        for name in ("dev", "web"):
            service = compose.split(f"\n  {name}:\n", 1)[1]
            if name == "dev":
                service = service.split("\n  web:\n", 1)[0]
            self.assertIn('user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"', service)
            self.assertIn("- .:/workspace", service)
            self.assertIn("target: /home/build-farm", service)



    def test_android_debug_build_runs_gradle_without_mobile_builder(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        build_script = repo_root / "scripts" / "android_debug_build.sh"

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

    def test_local_web_uses_standard_development_image_and_external_ocr_boundary(self) -> None:
        compose = Path("compose.yaml").read_text()
        web_service = compose.split("\n  web:\n", 1)[1]
        dockerfile = Path("Dockerfile.dev").read_text()
        web_binary = Path("rust/medicine_core/src/bin/medicine_core_web.rs").read_text()
        builder = Path("scripts/build_web.sh").read_text()
        runner = Path("scripts/run_web.sh").read_text()
        dev_runner = Path("scripts/run_web_dev.sh").read_text()

        self.assertIn("image: medicine/dev:local", web_service)
        self.assertIn("dockerfile: Dockerfile.dev", web_service)
        self.assertIn('user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"', web_service)
        self.assertIn('command: ["sh", "/workspace/scripts/run_web_dev.sh"]', web_service)
        self.assertIn("chromium", dockerfile)
        self.assertIn("cargo build --locked --release", builder)
        self.assertIn("--features web", builder)
        self.assertIn("--bin medicine-core-web", builder)
        self.assertNotIn("cargo build", runner)
        self.assertIn("medicine-core-web", runner)
        self.assertIn("build_web.sh", dev_runner)
        self.assertIn("run_web.sh", dev_runner)
        self.assertIn("MEDICINE_OCR_ASSETS_DIR", web_binary)
        self.assertIn("--ocr-assets-dir", web_binary)
        self.assertIn('const DEFAULT_REFERENCE_TRUST_MANIFEST: &str = "deploy/reference-signing-trusted-keys.json";', web_binary)



    def test_development_runtime_uses_signed_reference_channel_by_default(self) -> None:
        compose = Path("compose.yaml").read_text()
        web_service = compose.split("\n  web:\n", 1)[1]
        web_binary = Path("rust/medicine_core/src/bin/medicine_core_web.rs").read_text()
        web_runtime = Path("rust/medicine_core/src/web.rs").read_text()
        app_commands = Path(
            "rust/medicine_core/src/bin/agentctl/app_commands/support.rs"
        ).read_text()

        self.assertIn("MEDICINE_CANONICAL_DB: ${MEDICINE_CANONICAL_DB:-}", web_service)
        self.assertIn("MEDICINE_REFERENCE_DIR: ${MEDICINE_REFERENCE_DIR:-data/reference}", web_service)
        self.assertIn("MEDICINE_REFERENCE_UPDATE_BASE_URL", web_service)
        self.assertIn("open_reference_channel", web_binary)
        self.assertIn("prepare_runtime.prepare()", web_binary)
        self.assertIn("schedule_reference_update", web_binary)
        self.assertIn("update_runtime.check_for_update()", web_runtime)
        self.assertIn('const DEFAULT_REFERENCE_DIR: &str = "data/reference";', web_binary)
        self.assertIn('const DEFAULT_CANONICAL_DB: &str = "data/db/mobile.sqlite";', app_commands)
