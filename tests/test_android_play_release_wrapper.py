import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


class AndroidPlayReleaseWrapperTest(unittest.TestCase):
    def test_play_release_source_state_requires_clean_unchanged_commit(self) -> None:
        verifier = Path("scripts/verify-release-source-state.sh")
        self.assertTrue(verifier.is_file())
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            git = bin_dir / "git"
            git.write_text(
                "#!/bin/sh\n"
                "case \" $* \" in\n"
                "  *' rev-parse --is-inside-work-tree '*) printf 'true\\n' ;;\n"
                "  *' rev-parse HEAD '*) printf '%s\\n' \"$GIT_TEST_HEAD\" ;;\n"
                "  *' status --porcelain --untracked-files=all '*) printf '%s' \"$GIT_TEST_STATUS\" ;;\n"
                "  *) exit 9 ;;\n"
                "esac\n"
            )
            git.chmod(0o755)
            head = "a" * 40
            base_env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "GIT_TEST_HEAD": head,
                "GIT_TEST_STATUS": "",
            }

            clean = subprocess.run(
                ["sh", str(verifier), str(repo)],
                env=base_env,
                capture_output=True,
                text=True,
                check=False,
            )
            unchanged = subprocess.run(
                ["sh", str(verifier), str(repo), head],
                env=base_env,
                capture_output=True,
                text=True,
                check=False,
            )
            dirty = subprocess.run(
                ["sh", str(verifier), str(repo), head],
                env={**base_env, "GIT_TEST_STATUS": "?? untracked.txt\n"},
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertEqual(clean.stdout.strip(), head)
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn("not clean", dirty.stderr)

    def test_play_release_source_materializer_uses_exact_commit_bytes(self) -> None:
        materializer = Path("scripts/materialize-release-source.sh")
        self.assertTrue(materializer.is_file())
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Medicine Test"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "medicine-test@example.invalid"],
                check=True,
            )
            tracked = repo / "tracked.txt"
            tracked.write_text("committed-source\n")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
            commit = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()
            tracked.write_text("dirty-live-source\n")
            snapshot = root / "snapshot"

            result = subprocess.run(
                ["sh", str(materializer.resolve()), str(repo), commit, str(snapshot)],
                capture_output=True,
                text=True,
                check=False,
            )

            snapshot_text = (snapshot / "tracked.txt").read_text() if snapshot.exists() else ""

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot_text, "committed-source\n")

    def test_play_release_wrapper_binds_standard_image_build_to_clean_source_commit(self) -> None:
        wrapper = Path("scripts/android_play_release.sh")
        self.assertTrue(wrapper.is_file())
        text = wrapper.read_text()
        self.assertIn("verify-release-source-state.sh", text)
        self.assertIn("materialize-release-source.sh", text)
        self.assertIn("source_commit=", text)
        self.assertGreaterEqual(text.count("verify-release-source-state.sh"), 2)
        self.assertIn("medicine-android:latest", text)
        self.assertIn("MEDICINE_RELEASE_SOURCE_COMMIT", text)
        self.assertIn("MEDICINE_ARTIFACTS_DIR", text)
        self.assertIn("/artifacts/android-play/", text)
        self.assertIn(".pending-", text)
        self.assertIn("flock -n", text)
        self.assertIn('mv -T "$staging_dir" "$output_dir"', text)
        self.assertIn('"$source_dir:/workspace"', text)
        self.assertNotIn('"$workspace:/workspace"', text)
        self.assertNotIn("docker compose", text)
        self.assertLess(text.index("verify-release-source-state.sh"), text.index("docker run"))
        self.assertLess(text.index("docker run"), text.rindex("verify-release-source-state.sh"))
        self.assertLess(
            text.rindex("verify-release-source-state.sh"),
            text.index('mv -T "$staging_dir" "$output_dir"'),
        )

    def test_play_release_wrapper_publishes_only_after_post_build_source_check(self) -> None:
        wrapper_text = Path("scripts/android_play_release.sh").read_text()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            scripts = workspace / "scripts"
            bin_dir = root / "bin"
            scripts.mkdir(parents=True)
            bin_dir.mkdir()
            wrapper = scripts / "android_play_release.sh"
            wrapper.write_text(wrapper_text)
            wrapper.chmod(0o755)

            source_gate = scripts / "verify-release-source-state.sh"
            source_gate.write_text(
                "#!/bin/sh\n"
                "count=0\n"
                "if [ -f \"$SOURCE_TEST_COUNT\" ]; then count=$(cat \"$SOURCE_TEST_COUNT\"); fi\n"
                "count=$((count + 1))\n"
                "printf '%s' \"$count\" > \"$SOURCE_TEST_COUNT\"\n"
                "if [ \"$count\" -gt 1 ] && [ \"${SOURCE_TEST_FAIL_POST:-0}\" = 1 ]; then\n"
                "  echo 'post-build source changed' >&2\n"
                "  exit 3\n"
                "fi\n"
                "printf '%s\\n' \"$SOURCE_TEST_HEAD\"\n"
            )
            source_gate.chmod(0o755)

            materializer = scripts / "materialize-release-source.sh"
            materializer.write_text(
                "#!/bin/sh\n"
                "mkdir -p \"$3\"\n"
                "printf 'snapshot' > \"$3/source.txt\"\n"
            )
            materializer.chmod(0o755)

            docker = bin_dir / "docker"
            docker.write_text(
                "#!/bin/sh\n"
                "last=''\n"
                "for arg in \"$@\"; do last=$arg; done\n"
                "name=${last##*/}\n"
                "dir=\"$WRAPPER_TEST_ARTIFACT_ROOT/android-play/$name\"\n"
                "mkdir -p \"$dir\"\n"
                "printf 'bundle' > \"$dir/yakbom.aab\"\n"
            )
            docker.chmod(0o755)
            keystore = root / "upload.jks"
            keystore.write_bytes(b"keystore")
            head = "ef" * 20

            def run(artifact_root: Path, count_file: Path, *, fail_post: bool) -> subprocess.CompletedProcess[str]:
                env = {
                    **os.environ,
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "MEDICINE_ARTIFACTS_DIR": str(artifact_root),
                    "MEDICINE_ANDROID_KEYSTORE_PATH": str(keystore),
                    "MEDICINE_ANDROID_VERSION_CODE": "23",
                    "MEDICINE_ANDROID_VERSION_NAME": "1.4.0",
                    "MEDICINE_ANDROID_KEYSTORE_PASSWORD": "store-secret",
                    "MEDICINE_ANDROID_KEY_ALIAS": "upload",
                    "MEDICINE_ANDROID_KEY_PASSWORD": "key-secret",
                    "MEDICINE_ANDROID_UPLOAD_CERT_SHA256": "ab" * 32,
                    "MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL": "https://reference.yakbom.example/",
                    "SOURCE_TEST_COUNT": str(count_file),
                    "SOURCE_TEST_HEAD": head,
                    "SOURCE_TEST_FAIL_POST": "1" if fail_post else "0",
                    "WRAPPER_TEST_ARTIFACT_ROOT": str(artifact_root),
                }
                return subprocess.run(
                    ["sh", str(wrapper)],
                    cwd=workspace,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            success_root = root / "artifacts-success"
            failed_root = root / "artifacts-failed"
            success = run(success_root, root / "success-count", fail_post=False)
            failed = run(failed_root, root / "failed-count", fail_post=True)
            success_final = success_root / "android-play" / head
            failed_final = failed_root / "android-play" / head
            failed_pending = list((failed_root / "android-play").glob(".pending-*"))
            success_artifact_exists = (success_final / "yakbom.aab").is_file()
            failed_final_exists = failed_final.exists()

        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertTrue(success_artifact_exists)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("post-build source changed", failed.stderr)
        self.assertFalse(failed_final_exists)
        self.assertEqual(failed_pending, [])

    def test_play_release_wrapper_builds_from_snapshot_when_live_source_changes_transiently(self) -> None:
        wrapper_text = Path("scripts/android_play_release.sh").read_text()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            scripts = workspace / "scripts"
            bin_dir = root / "bin"
            scripts.mkdir(parents=True)
            bin_dir.mkdir()
            wrapper = scripts / "android_play_release.sh"
            wrapper.write_text(wrapper_text)
            wrapper.chmod(0o755)
            tracked = workspace / "tracked.txt"
            tracked.write_text("committed-source")

            source_gate = scripts / "verify-release-source-state.sh"
            source_gate.write_text("#!/bin/sh\nprintf '%s\\n' \"$SOURCE_TEST_HEAD\"\n")
            source_gate.chmod(0o755)
            materializer = scripts / "materialize-release-source.sh"
            materializer.write_text(
                "#!/bin/sh\n"
                "mkdir -p \"$3\"\n"
                "cp \"$1/tracked.txt\" \"$3/tracked.txt\"\n"
                "mkdir -p \"$3/scripts\"\n"
                "printf '#!/bin/sh\\n' > \"$3/scripts/android_play_bundle.sh\"\n"
            )
            materializer.chmod(0o755)

            docker = bin_dir / "docker"
            docker.write_text(
                "#!/bin/sh\n"
                "source_mount=''\n"
                "last=''\n"
                "for arg in \"$@\"; do\n"
                "  case \"$arg\" in *:/workspace) source_mount=${arg%:/workspace} ;; esac\n"
                "  last=$arg\n"
                "done\n"
                "if [ -z \"$source_mount\" ]; then echo 'workspace mount missing' >&2; exit 9; fi\n"
                "printf 'temporary-build-source' > \"$WRAPPER_TEST_LIVE_WORKSPACE/tracked.txt\"\n"
                "payload=$(cat \"$source_mount/tracked.txt\")\n"
                "printf 'committed-source' > \"$WRAPPER_TEST_LIVE_WORKSPACE/tracked.txt\"\n"
                "name=${last##*/}\n"
                "dir=\"$WRAPPER_TEST_ARTIFACT_ROOT/android-play/$name\"\n"
                "mkdir -p \"$dir\"\n"
                "printf '%s' \"$payload\" > \"$dir/yakbom.aab\"\n"
            )
            docker.chmod(0o755)
            keystore = root / "upload.jks"
            keystore.write_bytes(b"keystore")
            artifact_root = root / "artifacts"
            head = "12" * 20
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "MEDICINE_ARTIFACTS_DIR": str(artifact_root),
                "MEDICINE_ANDROID_KEYSTORE_PATH": str(keystore),
                "MEDICINE_ANDROID_VERSION_CODE": "23",
                "MEDICINE_ANDROID_VERSION_NAME": "1.4.0",
                "MEDICINE_ANDROID_KEYSTORE_PASSWORD": "store-secret",
                "MEDICINE_ANDROID_KEY_ALIAS": "upload",
                "MEDICINE_ANDROID_KEY_PASSWORD": "key-secret",
                "MEDICINE_ANDROID_UPLOAD_CERT_SHA256": "ab" * 32,
                "MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL": "https://reference.yakbom.example/",
                "SOURCE_TEST_HEAD": head,
                "WRAPPER_TEST_ARTIFACT_ROOT": str(artifact_root),
                "WRAPPER_TEST_LIVE_WORKSPACE": str(workspace),
            }

            result = subprocess.run(
                ["sh", str(wrapper)],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            artifact = artifact_root / "android-play" / head / "yakbom.aab"
            payload = artifact.read_text() if artifact.is_file() else ""
            live_source = tracked.read_text()
            source_snapshots = list((artifact_root / "android-play").glob(".source-*"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload, "committed-source")
        self.assertEqual(live_source, "committed-source")
        self.assertEqual(source_snapshots, [])

    def test_play_release_wrapper_serializes_same_commit_publication(self) -> None:
        wrapper_text = Path("scripts/android_play_release.sh").read_text()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            scripts = workspace / "scripts"
            bin_dir = root / "bin"
            scripts.mkdir(parents=True)
            bin_dir.mkdir()
            wrapper = scripts / "android_play_release.sh"
            wrapper.write_text(wrapper_text)
            wrapper.chmod(0o755)

            source_gate = scripts / "verify-release-source-state.sh"
            source_gate.write_text("#!/bin/sh\nprintf '%s\\n' \"$SOURCE_TEST_HEAD\"\n")
            source_gate.chmod(0o755)
            materializer = scripts / "materialize-release-source.sh"
            materializer.write_text(
                "#!/bin/sh\n"
                "mkdir -p \"$3/scripts\"\n"
                "printf '#!/bin/sh\\n' > \"$3/scripts/android_play_bundle.sh\"\n"
            )
            materializer.chmod(0o755)

            started = root / "docker-started"
            release = root / "release-docker"
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/bin/sh\n"
                "last=''\n"
                "for arg in \"$@\"; do last=$arg; done\n"
                "if [ \"${WRAPPER_TEST_BLOCK_DOCKER:-0}\" = 1 ]; then\n"
                "  : > \"$WRAPPER_TEST_DOCKER_STARTED\"\n"
                "  while [ ! -e \"$WRAPPER_TEST_DOCKER_RELEASE\" ]; do sleep 0.02; done\n"
                "fi\n"
                "name=${last##*/}\n"
                "dir=\"$WRAPPER_TEST_ARTIFACT_ROOT/android-play/$name\"\n"
                "mkdir -p \"$dir\"\n"
                "printf 'bundle' > \"$dir/yakbom.aab\"\n"
            )
            docker.chmod(0o755)
            keystore = root / "upload.jks"
            keystore.write_bytes(b"keystore")
            artifact_root = root / "artifacts"
            head = "34" * 20
            base_env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "MEDICINE_ARTIFACTS_DIR": str(artifact_root),
                "MEDICINE_ANDROID_KEYSTORE_PATH": str(keystore),
                "MEDICINE_ANDROID_VERSION_CODE": "23",
                "MEDICINE_ANDROID_VERSION_NAME": "1.4.0",
                "MEDICINE_ANDROID_KEYSTORE_PASSWORD": "store-secret",
                "MEDICINE_ANDROID_KEY_ALIAS": "upload",
                "MEDICINE_ANDROID_KEY_PASSWORD": "key-secret",
                "MEDICINE_ANDROID_UPLOAD_CERT_SHA256": "ab" * 32,
                "MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL": "https://reference.yakbom.example/",
                "SOURCE_TEST_HEAD": head,
                "WRAPPER_TEST_ARTIFACT_ROOT": str(artifact_root),
                "WRAPPER_TEST_DOCKER_STARTED": str(started),
                "WRAPPER_TEST_DOCKER_RELEASE": str(release),
            }
            first = subprocess.Popen(
                ["sh", str(wrapper)],
                cwd=workspace,
                env={**base_env, "WRAPPER_TEST_BLOCK_DOCKER": "1"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(250):
                if started.exists():
                    break
                if first.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertTrue(started.exists(), "first wrapper did not reach the build step")

            second = subprocess.run(
                ["sh", str(wrapper)],
                cwd=workspace,
                env={**base_env, "WRAPPER_TEST_BLOCK_DOCKER": "0"},
                capture_output=True,
                text=True,
                check=False,
            )
            release.touch()
            first_stdout, first_stderr = first.communicate(timeout=10)
            final = artifact_root / "android-play" / head
            nested_pending = list(final.rglob(".pending-*")) if final.exists() else []
            root_pending = list((artifact_root / "android-play").glob(".pending-*"))
            final_artifact_exists = (final / "yakbom.aab").is_file()

        self.assertEqual(first.returncode, 0, first_stderr or first_stdout)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already in progress", second.stderr)
        self.assertTrue(final_artifact_exists)
        self.assertEqual(nested_pending, [])
        self.assertEqual(root_pending, [])



if __name__ == "__main__":
    unittest.main()
