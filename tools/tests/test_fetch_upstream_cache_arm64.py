import copy
import io
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tools import fetch_upstream_cache as cache
from tools.tests import test_fetch_upstream_cache as fixtures

NOW = datetime(2026, 9, 27, 8, 17, 32, tzinfo=timezone.utc)
# A prior checkpoint fixture; this test never selects a live donor.
ARM64 = {
    "id": 10922899704,
    "name": "build-artifact-arm",
    "size_in_bytes": 15465670797,
    "digest": "sha256:683e3ae6d148f22cc843f779b36ad920664bd8b2ff1f52acfecdd64d186fcf6b",
    "expires_at": "2026-10-01T04:19:43Z",
    "inner_archive": "artifacts.zip",
    "run_id": 36093095856,
    "workflow_path": ".github/workflows/build-arm.yml",
    "run_attempt": 1,
    "producer_job_id": 108504640745,
    "producer_job_name": "build / build-10",
}


def checkpoint_metadata(pin):
    run, artifact = fixtures.metadata(pin)
    run.update(status="in_progress", conclusion=None, run_attempt=1,
               run_started_at="2026-09-25T04:06:43Z")
    artifact.update(created_at="2026-09-27T04:25:56Z")
    producer = {
        "id": ARM64["producer_job_id"], "name": ARM64["producer_job_name"], "run_id": ARM64["run_id"],
        "run_attempt": 1, "head_sha": pin["head_sha"], "head_branch": pin["head_branch"],
        "workflow_name": "build-arm", "status": "completed", "conclusion": "success",
        "started_at": "2026-09-26T23:01:03Z", "completed_at": "2026-09-27T04:26:22Z",
        "steps": [{"name": "Run Stage", "number": 4, "status": "completed", "conclusion": "success",
                   "started_at": "2026-09-26T23:05:29Z", "completed_at": "2026-09-27T04:26:00Z"}],
    }
    return run, artifact, producer


class Arm64UpstreamCacheTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "build").mkdir()
        for name in ("CHROMIUM_VERSION", "CHROMIUM_LINUX_VERSION", "CHROMIUM_MACOS_VERSION",
                     "CHROMIUM_WINDOWS_VERSION", "build/ungoogled-revisions.psd1", "build/upstream-cache.json"):
            if (cache.ROOT / name).is_file():
                shutil.copyfile(cache.ROOT / name, self.root / name)
        pins = cache.load_shared_pins(self.root)
        pins.update(WindowsChromiumVersion="154.0.8037.57", WindowsUngoogledVersion="154.0.8037.57-1",
                    WindowsUngoogledCommit="e" * 40, UngoogledWindowsVersion="154.0.8037.57-1.1",
                    UngoogledWindowsCommit="fc387c7527f875ca73c82ed4907fccaa86808c9a")
        (self.root / "build/ungoogled-revisions.psd1").write_text(
            "@{\n" + "".join(f'  {key} = "{value}"\n' for key, value in pins.items()) + "}\n")
        (self.root / "CHROMIUM_WINDOWS_VERSION").write_text("154.0.8037.57\n")
        self.manifest = json.loads((self.root / "build/upstream-cache.json").read_text())
        source = fixtures.synthetic_windows_source(self.root)
        source["run_id"] = 36093095228
        source["artifacts"]["x64"].update(
            id=10915484727, name="build-artifact", size_in_bytes=15716545319,
            digest="sha256:7a6ba27fa2d056759d1e635f486e68cbfed36ef2d73ee201527e1ddb52d0d4a4",
            expires_at="2026-09-30T21:34:19Z")
        source["artifacts"]["arm64"] = copy.deepcopy(ARM64)
        self.manifest["sources"]["windows"] = source
        self.save(self.manifest)
        self.destination = self.root / "cache"
        self.pin, self.identity = cache.load_manifest("windows", "arm64", root=self.root)
        clock = mock.patch.object(cache, "datetime", wraps=datetime)
        clock.start().now.return_value = NOW
        self.addCleanup(clock.stop)
        disk = mock.patch.object(cache.shutil, "disk_usage", return_value=mock.Mock(free=1024**4))
        disk.start()
        self.addCleanup(disk.stop)
        output = mock.patch("sys.stderr", new=io.StringIO())
        output.start()
        self.addCleanup(output.stop)

    def save(self, manifest):
        (self.root / "build/upstream-cache.json").write_text(json.dumps(manifest))

    def validate(self, run, artifact, producer):
        cache.validate_metadata(self.pin, run, artifact, NOW, producer=producer)

    def test_exact_arm64_pin_is_flattened_without_changing_x64(self):
        self.assertEqual(self.pin["artifact"], ARM64)
        self.assertEqual(self.manifest["schema_version"], 1)
        for key in cache.ARTIFACT_OVERRIDES | cache.CHECKPOINT_FIELDS:
            self.assertEqual(self.pin[key], ARM64[key])
            self.assertEqual(self.identity[key], ARM64[key])
        self.assertEqual(self.pin["chromium_version"], "154.0.8037.57")
        self.assertEqual(self.pin["ungoogled_commit"], "e" * 40)
        self.assertEqual(self.pin["head_sha"], "fc387c7527f875ca73c82ed4907fccaa86808c9a")
        self.assertEqual(self.pin["head_branch"], "154.0.8037.57-1.1")
        self.assertEqual(self.identity["target"], "windows-arm64")
        self.assertEqual(self.identity["artifact_id"], ARM64["id"])
        self.assertEqual(self.identity["artifact_digest"], ARM64["digest"])
        x64_pin, x64_identity = cache.load_manifest("windows", "x64", root=self.root)
        legacy = copy.deepcopy(self.manifest)
        del legacy["sources"]["windows"]["artifacts"]["arm64"]
        self.save(legacy)
        legacy_pin, legacy_identity = cache.load_manifest("windows", "x64", root=self.root)
        self.assertEqual(x64_pin, legacy_pin)
        self.assertEqual({k: v for k, v in x64_identity.items() if k != "sha256"},
                         {k: v for k, v in legacy_identity.items() if k != "sha256"})
        self.assertEqual(x64_pin["run_id"], 36093095228)
        self.assertEqual(x64_pin["workflow_path"], ".github/workflows/build-x64.yml")
        self.assertFalse(cache.CHECKPOINT_FIELDS.intersection(x64_pin))

    def test_all_six_targets_and_exact_manual_run_selection(self):
        for platform in cache.SOURCES:
            for arch in ("x64", "arm64"):
                with self.subTest(platform=platform, arch=arch):
                    pin, identity = cache.load_manifest(platform, arch, root=self.root)
                    self.assertEqual(identity["artifact_digest"], pin["artifact"]["digest"])
        cache.load_manifest("windows", "arm64", ARM64["run_id"], root=self.root)
        for arch, run_id in (("arm64", 36093095228), ("x64", ARM64["run_id"]), ("arm64", 1)):
            with self.subTest(arch=arch, run_id=run_id):
                client = mock.Mock()
                result = cache.fetch("windows", arch, self.destination, run_id, self.root, client)
                self.assertEqual(result["reason"], "run_id_mismatch")
                self.assertEqual(result["download_bytes"], 0)
                self.assertEqual(client.mock_calls, [])

    def test_arm64_artifact_shape_and_override_types_are_strict(self):
        variants = [{k: v for k, v in ARM64.items() if k != missing} for missing in ARM64]
        variants += [dict(ARM64, **{key: value}) for key, value in (
            ("run_id", 36093095228), ("run_id", 0), ("run_id", True), ("run_id", "36093095856"),
            ("run_id", None), ("workflow_path", ".github/workflows/build-x64.yml"),
            ("workflow_path", ".github/workflows/reusable-build.yml"), ("workflow_path", None),
            ("name", "build-artifact"), ("name", "build-artifact-x86"),
            ("run_attempt", True), ("run_attempt", "5"), ("run_attempt", 0), ("run_attempt", None),
            ("producer_job_id", True), ("producer_job_id", "108504640745"), ("producer_job_id", -1),
            ("producer_job_name", ""), ("producer_job_name", None), ("producer_job_name", "build / build-0"),
            ("producer_job_name", "build / build-13\n"), ("producer_job_name", "build-x64 / build-11"),
            ("producer", {}), ("arch", "x64"), ("head_sha", "0" * 40),
            ("allow_in_progress", True), ("checkpoint", True), ("unexpected", 1),
        )]
        for index, artifact in enumerate(variants):
            manifest = copy.deepcopy(self.manifest)
            manifest["sources"]["windows"]["artifacts"]["arm64"] = artifact
            self.save(manifest)
            for platform in cache.SOURCES:
                with self.subTest(variant=index, platform=platform), self.assertRaises(cache.CacheMiss):
                    cache.load_manifest(platform, "x64", root=self.root)

    def test_checkpoint_cannot_be_moved_to_another_arch_or_source(self):
        for platform, arch in (("windows", "x64"), ("linux", "arm64"), ("macos", "arm64")):
            manifest = copy.deepcopy(self.manifest)
            artifact = manifest["sources"][platform]["artifacts"][arch]
            artifact.update({key: ARM64[key] for key in cache.ARTIFACT_OVERRIDES | cache.CHECKPOINT_FIELDS})
            self.save(manifest)
            with self.subTest(platform=platform, arch=arch), self.assertRaises(cache.CacheMiss):
                cache.load_manifest(platform, arch, root=self.root)
        for key in cache.CHECKPOINT_FIELDS:
            manifest = copy.deepcopy(self.manifest)
            manifest["sources"]["windows"][key] = ARM64[key]
            self.save(manifest)
            with self.subTest(source_field=key), self.assertRaisesRegex(cache.CacheMiss, "manifest_checkpoint"):
                cache.load_manifest("windows", "x64", root=self.root)

    def test_completed_arm64_without_checkpoint_still_requires_run_success(self):
        arm = self.manifest["sources"]["windows"]["artifacts"]["arm64"]
        for key in cache.CHECKPOINT_FIELDS:
            del arm[key]
        self.save(self.manifest)
        pin, _ = cache.load_manifest("windows", "arm64", root=self.root)
        run, artifact = fixtures.metadata(pin)
        cache.validate_metadata(pin, run, artifact, NOW)
        run.update(status="in_progress", conclusion=None)
        with self.assertRaisesRegex(cache.CacheMiss, "untrusted_run"):
            cache.validate_metadata(pin, run, artifact, NOW, producer=checkpoint_metadata(self.pin)[2])

    def test_exact_successful_checkpoint_accepts_only_in_progress_or_successful_run(self):
        for status, conclusion in (("in_progress", None), ("completed", "success")):
            run, artifact, producer = checkpoint_metadata(self.pin)
            run.update(status=status, conclusion=conclusion)
            with self.subTest(status=status):
                self.validate(run, artifact, producer)
                with self.assertRaisesRegex(cache.CacheMiss, "untrusted_producer"):
                    cache.validate_metadata(self.pin, run, artifact, NOW)
        for status, conclusion in (("queued", None), ("waiting", None), ("cancelled", None),
                                   ("completed", "failure"), ("completed", "cancelled"),
                                   ("completed", "skipped"), ("completed", None),
                                   ("in_progress", "success"), ("in_progress", "failure")):
            run, artifact, producer = checkpoint_metadata(self.pin)
            run.update(status=status, conclusion=conclusion)
            with self.subTest(status=status, conclusion=conclusion), self.assertRaisesRegex(
                    cache.CacheMiss, "untrusted_run"):
                self.validate(run, artifact, producer)

    def test_wrong_run_attempt_arch_workflow_and_provenance_are_rejected(self):
        for key, value in (("id", 36093095228), ("path", ".github/workflows/build-x64.yml"),
                           ("path", ".github/workflows/reusable-build.yml"), ("event", "pull_request"),
                           ("head_sha", "0" * 40), ("head_branch", "main"),
                           ("run_attempt", 4), ("run_attempt", 6), ("run_attempt", "5"),
                           ("run_attempt", True), ("run_attempt", None)):
            run, artifact, producer = checkpoint_metadata(self.pin)
            run[key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(cache.CacheMiss, "untrusted_run"):
                self.validate(run, artifact, producer)
        for field in ("repository", "head_repository"):
            for key, value in (("full_name", "attacker/fork"), ("id", 1), ("private", True)):
                run, artifact, producer = checkpoint_metadata(self.pin)
                run[field][key] = value
                with self.subTest(field=field, key=key), self.assertRaisesRegex(
                        cache.CacheMiss, "untrusted_repository"):
                    self.validate(run, artifact, producer)
        for key, value in (("id", 10915484727), ("name", "build-artifact"), ("size_in_bytes", 1),
                           ("digest", fixtures.digest(b"replaced"))):
            run, artifact, producer = checkpoint_metadata(self.pin)
            artifact[key] = value
            with self.subTest(artifact=key), self.assertRaisesRegex(cache.CacheMiss, "artifact_mismatch"):
                self.validate(run, artifact, producer)
        for key in ("id", "head_sha", "head_branch", "repository_id", "head_repository_id"):
            run, artifact, producer = checkpoint_metadata(self.pin)
            artifact["workflow_run"][key] = "wrong"
            with self.subTest(provenance=key), self.assertRaisesRegex(cache.CacheMiss, "artifact_provenance"):
                self.validate(run, artifact, producer)

    def test_wrong_or_unsuccessful_producer_is_rejected_even_after_run_success(self):
        patches = [("id", 108504640746), ("name", "build / build-12"), ("run_id", 36093095228),
                   ("run_attempt", 4), ("run_attempt", 6), ("run_attempt", "5"), ("run_attempt", True),
                   ("head_sha", "0" * 40), ("head_branch", "main"), ("workflow_name", "build-x64"),
                   ("status", "in_progress"), ("status", "queued"), ("conclusion", "failure"),
                   ("conclusion", "cancelled"), ("conclusion", "skipped"), ("conclusion", None)]
        for status, conclusion in (("in_progress", None), ("completed", "success")):
            for key, value in patches:
                run, artifact, producer = checkpoint_metadata(self.pin)
                run.update(status=status, conclusion=conclusion)
                producer[key] = value
                with self.subTest(status=status, key=key), self.assertRaisesRegex(
                        cache.CacheMiss, "untrusted_producer"):
                    self.validate(run, artifact, producer)
            for invalid in (None, [], "wrong", {}):
                with self.subTest(producer=invalid), self.assertRaisesRegex(cache.CacheMiss, "untrusted_producer"):
                    self.validate(run, artifact, invalid)

    def test_missing_ambiguous_or_unsuccessful_stage_is_rejected(self):
        run, artifact, producer = checkpoint_metadata(self.pin)
        stage = producer["steps"][0]
        variants = [None, [], {}, [None], [stage, stage],
                    [dict(stage, name="Run Other Stage")], [dict(stage, status="in_progress")],
                    [dict(stage, conclusion="failure")], [dict(stage, conclusion="skipped")]]
        for steps in variants:
            with self.subTest(steps=steps), self.assertRaisesRegex(cache.CacheMiss, "untrusted_producer_steps"):
                self.validate(run, artifact, dict(producer, steps=steps))

    def test_creation_window_and_timestamp_shapes_are_verified(self):
        patches = [
            ("run", "run_started_at", "2026-09-26T23:01:04Z"),
            ("producer", "started_at", "2026-09-26T23:05:30Z"),
            ("producer", "completed_at", "2026-09-27T04:25:55Z"),
            ("producer", "completed_at", "2026-09-27T08:17:33Z"),
            ("artifact", "created_at", "2026-09-26T23:01:02Z"),
            ("artifact", "created_at", "2026-09-26T23:05:28Z"),
            ("artifact", "created_at", "2026-09-27T04:26:23Z"),
            ("artifact", "created_at", "2026-09-27T04:26:01Z"),
            ("stage", "started_at", "2026-09-26T23:01:02Z"),
            ("stage", "started_at", "2026-09-27T04:26:01Z"),
            ("stage", "completed_at", "2026-09-27T04:25:55Z"),
            ("stage", "completed_at", "2026-09-27T04:26:23Z"),
        ]
        for target, field in (("run", "run_started_at"), ("producer", "started_at"),
                              ("producer", "completed_at"), ("artifact", "created_at"),
                              ("stage", "started_at"), ("stage", "completed_at")):
            patches += [(target, field, value) for value in (None, "bad", 1, "2026-09-27T04:25:56")]
        for target, key, value in patches:
            run, artifact, producer = checkpoint_metadata(self.pin)
            {"run": run, "artifact": artifact, "producer": producer,
             "stage": producer["steps"][0]}[target][key] = value
            with self.subTest(target=target, key=key, value=value), self.assertRaisesRegex(
                    cache.CacheMiss, "artifact_creation_window"):
                self.validate(run, artifact, producer)

    def test_checkpoint_never_bypasses_expiration_or_x64_run_success(self):
        run, artifact, producer = checkpoint_metadata(self.pin)
        for patch in ({"expired": True}, {"expires_at": NOW.isoformat()}, {"expires_at": None}):
            with self.subTest(patch=patch), self.assertRaises(cache.CacheMiss):
                self.validate(run, dict(artifact, **patch), producer)
        for platform in cache.SOURCES:
            pin, _ = cache.load_manifest(platform, "x64", root=self.root)
            run, artifact = fixtures.metadata(pin)
            cache.validate_metadata(pin, run, artifact, NOW, producer=producer)
            for status, conclusion in (("in_progress", None), ("completed", "failure"),
                                       ("completed", "cancelled"), ("queued", None)):
                run.update(status=status, conclusion=conclusion)
                with self.subTest(platform=platform, status=status), self.assertRaisesRegex(
                        cache.CacheMiss, "untrusted_run"):
                    cache.validate_metadata(pin, run, artifact, NOW, producer=producer)

    def test_fetch_uses_exact_attempt_and_job_then_verifies_archive(self):
        version = "".join(f"{key}={value}\n" for key, value in zip(
            ("MAJOR", "MINOR", "BUILD", "PATCH"), self.pin["chromium_version"].split("."))).encode()
        inner = fixtures.zip_bytes([
            ("src", "dir", b""), ("src/BUILD.gn", "file", b"build"),
            ("src/chrome/VERSION", "file", version),
            ("src/out/Default/args.gn", "file", b'target_cpu="arm64"'),
            ("src/out/Default/build.ninja", "file", b"ninja"),
            ("src/out/Default/.ninja_log", "file", b"log"),
            ("src/out/Default/.ninja_deps", "file", b"deps"),
            ("src/out/Default/obj/file.obj", "file", b"object"),
        ])
        outer = fixtures.zip_bytes([("artifacts.zip", "file", inner)])
        self.manifest["sources"]["windows"]["artifacts"]["arm64"].update(
            size_in_bytes=len(outer), digest=fixtures.digest(outer))
        self.save(self.manifest)
        self.pin, _ = cache.load_manifest("windows", "arm64", root=self.root)
        client = cache.GitHub("fixture-token")
        client.json = mock.Mock(side_effect=checkpoint_metadata(self.pin))
        client.open = mock.Mock(return_value=fixtures.Response(outer))
        result = cache.fetch("windows", "arm64", self.destination, ARM64["run_id"], self.root, client)
        self.assertEqual(result["status"], "hit", result)
        base = "/repos/ungoogled-software/ungoogled-chromium-windows/actions"
        self.assertEqual(client.json.call_args_list, [
            mock.call(base + "/runs/36093095856/attempts/1"),
            mock.call(base + "/artifacts/10922899704"),
            mock.call(base + "/jobs/108504640745"),
        ])
        self.assertEqual(result["download_bytes"], len(outer))
        self.assertEqual(result["manifest"]["run_id"], ARM64["run_id"])
        self.assertEqual(result["manifest"]["producer_job_id"], ARM64["producer_job_id"])
        self.assertEqual((Path(result["source"]) / "out/Default/obj/file.obj").read_bytes(), b"object")
        self.assertEqual(json.loads((self.destination / "result.json").read_text()), result)

    def test_fetch_fails_closed_before_download_for_bad_checkpoint_metadata(self):
        for target, field, value, reason in (
            (0, "path", ".github/workflows/build-x64.yml", "untrusted_run"),
            (0, "run_attempt", 6, "untrusted_run_attempt"),
            (1, "name", "build-artifact", "artifact_mismatch"),
            (1, "created_at", "2026-09-27T04:26:23Z", "artifact_creation_window"),
            (2, "id", 108504640746, "untrusted_producer"),
            (2, "conclusion", "failure", "untrusted_producer"),
        ):
            responses = checkpoint_metadata(self.pin)
            responses[target][field] = value
            client = mock.Mock()
            client.json.side_effect = responses
            result = cache.fetch("windows", "arm64", self.destination, root=self.root, client=client)
            with self.subTest(target=target, field=field):
                self.assertEqual(result["status"], "miss")
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["download_bytes"], 0)
                client.download.assert_not_called()
                self.assertEqual(list(self.destination.iterdir()), [self.destination / "result.json"])
        for response in range(3):
            responses = list(checkpoint_metadata(self.pin))
            responses[response] = cache.CacheMiss("github_http_404")
            client = mock.Mock()
            client.json.side_effect = responses
            result = cache.fetch("windows", "arm64", self.destination, root=self.root, client=client)
            self.assertEqual(result["reason"], "github_http_404")
            client.download.assert_not_called()


class FinalArm64UpstreamCacheTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "build").mkdir()
        for name in ("CHROMIUM_VERSION", "CHROMIUM_LINUX_VERSION", "CHROMIUM_MACOS_VERSION",
                     "CHROMIUM_WINDOWS_VERSION", "build/ungoogled-revisions.psd1", "build/upstream-cache.json"):
            shutil.copyfile(cache.ROOT / name, self.root / name)
        pins = cache.load_shared_pins(self.root)
        pins.update(WindowsChromiumVersion="154.0.8037.97", WindowsUngoogledVersion="154.0.8037.97-1",
                    WindowsUngoogledCommit="37085e47cf580c815a30402917d350ce97399ded",
                    UngoogledWindowsVersion="154.0.8037.97-1.1",
                    UngoogledWindowsCommit="f03c33d7974af5b40f25b01984ded8418d60fbe4")
        (self.root / "build/ungoogled-revisions.psd1").write_text(
            "@{\n" + "".join(f'  {key} = "{value}"\n' for key, value in pins.items()) + "}\n")
        (self.root / "CHROMIUM_WINDOWS_VERSION").write_text("154.0.8037.97\n")
        manifest = json.loads((self.root / "build/upstream-cache.json").read_text())
        source = fixtures.synthetic_windows_source(self.root)
        source["artifacts"]["arm64"] = {
            "id": 11400140219, "name": "build-artifact-arm", "size_in_bytes": 16217698638,
            "digest": "sha256:51e706c5f85603616ea808eb0a8b8baa9114635bdfd90bcc4135aa591b8d3132",
            "expires_at": "2026-10-10T08:37:38Z", "inner_archive": "artifacts.zip",
            "run_id": 37100793757, "workflow_path": ".github/workflows/build-arm.yml",
        }
        manifest["sources"]["windows"] = source
        (self.root / "build/upstream-cache.json").write_text(json.dumps(manifest))
        self.pin, self.identity = cache.load_manifest("windows", "arm64", 37100793757, root=self.root)
        self.now = datetime(2026, 10, 6, 11, tzinfo=timezone.utc)
        self.run, self.artifact = fixtures.metadata(self.pin)
        self.run.update(run_attempt=2)
        self.artifact.update(created_at="2026-10-06T08:39:51Z")

    def test_final_pin_requires_no_checkpoint_producer(self):
        self.assertEqual(self.pin["artifact"], {
            "id": 11400140219, "name": "build-artifact-arm", "size_in_bytes": 16217698638,
            "digest": "sha256:51e706c5f85603616ea808eb0a8b8baa9114635bdfd90bcc4135aa591b8d3132",
            "expires_at": "2026-10-10T08:37:38Z", "inner_archive": "artifacts.zip",
            "run_id": 37100793757, "workflow_path": ".github/workflows/build-arm.yml",
        })
        for value in (self.pin, self.pin["artifact"], self.identity):
            self.assertFalse(cache.CHECKPOINT_FIELDS.intersection(value))
        cache.validate_metadata(self.pin, self.run, self.artifact, self.now)

    def test_final_pin_rejects_moving_failed_expired_or_old_artifact(self):
        for status, conclusion in (("in_progress", None), ("queued", None),
                                   ("completed", "failure"), ("completed", "cancelled"),
                                   ("completed", None)):
            with self.subTest(status=status, conclusion=conclusion), self.assertRaisesRegex(
                    cache.CacheMiss, "untrusted_run"):
                cache.validate_metadata(self.pin, dict(self.run, status=status, conclusion=conclusion),
                                        self.artifact, self.now)
        for patch, reason in (
            ({"id": 11374949381}, "artifact_mismatch"),
            ({"digest": "sha256:cc18df9a9169f62520fd21f52b27105a49a20a07004688c35f1a4c0a4cd1c66f"},
             "artifact_mismatch"),
            ({"size_in_bytes": 15738033821}, "artifact_mismatch"),
            ({"expired": True}, "artifact_expired"),
            ({"expires_at": self.now.isoformat()}, "artifact_expired"),
        ):
            with self.subTest(patch=patch), self.assertRaisesRegex(cache.CacheMiss, reason):
                cache.validate_metadata(self.pin, self.run, dict(self.artifact, **patch), self.now)

    def test_final_fetch_checks_run_and_artifact_without_checkpoint_api(self):
        client = mock.Mock()
        client.json.side_effect = (self.run, self.artifact)
        client.download.side_effect = cache.CacheMiss("metadata_verified")
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(cache, "datetime", wraps=datetime) as clock, \
                mock.patch.object(cache.shutil, "disk_usage", return_value=mock.Mock(free=1024**4)), \
                mock.patch("sys.stderr", new=io.StringIO()):
            clock.now.return_value = self.now
            result = cache.fetch("windows", "arm64", Path(temporary), 37100793757, root=self.root, client=client)
        self.assertEqual(result["reason"], "metadata_verified")
        base = "/repos/ungoogled-software/ungoogled-chromium-windows/actions"
        self.assertEqual(client.json.call_args_list, [
            mock.call(base + "/runs/37100793757"),
            mock.call(base + "/artifacts/11400140219"),
        ])
        client.download.assert_called_once()


if __name__ == "__main__":
    unittest.main()
