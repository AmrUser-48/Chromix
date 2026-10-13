"""Fail-closed contracts for the fixed Windows 155 x64 stage-8 donor."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import download_windows_snapshot as downloader
import validate_windows155_stage8_api_checkpoint as checkpoint


class Metadata:
    def __init__(self):
        self.calls = []
        self.run = {"id": checkpoint.RUN_ID, "path": f".github/workflows/{checkpoint.WORKFLOW}.yml",
                    "event": "workflow_dispatch", "workflow_id": 383114923, "head_branch": checkpoint.BRANCH, "head_sha": checkpoint.DONOR_SHA,
                    "status": "completed", "conclusion": "failure", "run_attempt": 1,
                    "repository": {"full_name": checkpoint.REPOSITORY},
                    "head_repository": {"full_name": checkpoint.REPOSITORY}}
        self.jobs = [{"id": checkpoint.JOB_ID, "name": "stage 8 (resume compile)",
                      "status": "completed", "conclusion": "failure", "run_id": checkpoint.RUN_ID,
                      "run_attempt": 1, "head_sha": checkpoint.DONOR_SHA,
                      "steps": [{"name": name, "status": "completed", "conclusion": "success"}
                                for name in ("Ensure build tree snapshot", "Upload tree part 1", "Upload tree part 2")]}]
        self.jobs[0]["steps"].append({"name": "Run stage 8", "status": "completed", "conclusion": "failure"})
        self.artifacts = [{**item, "workflow_run": {"id": checkpoint.RUN_ID,
                           "head_sha": checkpoint.DONOR_SHA, "head_branch": checkpoint.BRANCH}}
                          for item in checkpoint.ARTIFACTS]

    def get(self, path):
        self.calls.append(path)
        assert path == "/actions/runs/38387174079"
        return self.run

    def items(self, path, key):
        self.calls.append(path)
        assert path == {"jobs": "/actions/runs/38387174079/attempts/1/jobs",
                        "artifacts": "/actions/runs/38387174079/artifacts"}[key]
        return getattr(self, key)


def test_fixed_failure_checkpoint_is_download_manifest(tmp_path):
    client = Metadata()
    manifest = checkpoint.validate_metadata(client)
    assert manifest["job_id"] == 115261724792
    assert manifest["artifacts"] == list(checkpoint.ARTIFACTS)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert downloader.load_manifest(path)["run_id"] == 38387174079
    assert client.calls == ["/actions/runs/38387174079", "/actions/runs/38387174079/attempts/1/jobs",
                            "/actions/runs/38387174079/artifacts"]


@pytest.mark.parametrize("field,value", [
    ("id", 1), ("path", ".github/workflows/build-win-arm64-stage8-recovery.yml"),
    ("head_branch", "recovery"), ("head_sha", "0" * 40), ("run_attempt", 2),
    ("run_attempt", True), ("status", "in_progress"), ("conclusion", "success"),
    ("event", "pull_request"), ("repository", {"full_name": "fork/Chromix"}),
    ("head_repository", {"full_name": "fork/Chromix"}), ("repository", None),
])
def test_run_identity_cannot_be_substituted(field, value):
    client = Metadata()
    client.run[field] = value
    with pytest.raises(ValueError):
        checkpoint.validate_metadata(client)


@pytest.mark.parametrize("field,value", [
    ("id", 115019936454), ("name", "stage 11 (resume compile)"), ("run_id", 38175278760),
    ("run_attempt", True), ("run_attempt", 2), ("head_sha", "0" * 40),
    ("status", "in_progress"), ("conclusion", "success"),
])
def test_job_identity_cannot_be_substituted(field, value):
    client = Metadata()
    client.jobs[0][field] = value
    with pytest.raises(ValueError):
        checkpoint.validate_metadata(client)


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("mutation", ("missing", "duplicate", "failed", "incomplete"))
def test_required_checkpoint_steps_are_unambiguous_successes(index, mutation):
    client = Metadata()
    steps = client.jobs[0]["steps"]
    if mutation == "missing":
        del steps[index]
    elif mutation == "duplicate":
        steps.append(copy.deepcopy(steps[index]))
    elif mutation == "failed":
        steps[index]["conclusion"] = "failure"
    else:
        steps[index]["status"] = "in_progress"
    with pytest.raises(ValueError):
        checkpoint.validate_metadata(client)


@pytest.mark.parametrize("field,value", [
    ("id", 1), ("name", checkpoint.PREFIX + "3"), ("expired", True), ("expired", 0),
    ("size_in_bytes", 1), ("size_in_bytes", "9663676664"), ("digest", "sha256:" + "0" * 64),
    ("workflow_run", {"id": checkpoint.RUN_ID, "head_sha": "0" * 40, "head_branch": checkpoint.BRANCH}),
    ("workflow_run", {"id": checkpoint.RUN_ID, "head_sha": checkpoint.DONOR_SHA, "head_branch": "fork"}),
])
@pytest.mark.parametrize("index", (0, 1))
def test_artifact_identity_is_immutable(index, field, value):
    client = Metadata()
    client.artifacts[index][field] = value
    with pytest.raises(ValueError):
        checkpoint.validate_metadata(client)


@pytest.mark.parametrize("collection", ("jobs", "artifacts"))
@pytest.mark.parametrize("mutation", ("missing", "duplicate", "invalid"))
def test_metadata_sets_fail_closed(collection, mutation):
    client = Metadata()
    if mutation == "missing":
        getattr(client, collection).pop()
    elif mutation == "duplicate":
        getattr(client, collection).append(copy.deepcopy(getattr(client, collection)[0]))
    else:
        setattr(client, collection, [None])
    with pytest.raises(ValueError):
        checkpoint.validate_metadata(client)


def test_repository_argument_cannot_redirect_metadata():
    client = Metadata()
    with pytest.raises(ValueError, match="repository"):
        checkpoint.validate_metadata(client, "fork/Chromix")
    assert client.calls == []


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    from tools.tests.test_migrate_windows155_stage8_api_x64_snapshot import Fixture
    import migrate_windows155_stage8_api_x64_snapshot as migration
    fx = Fixture(tmp_path, monkeypatch)
    report = fx.run()
    path = tmp_path / "migration.json"
    path.write_bytes((fx.src / migration.RECEIPT).read_bytes())
    return fx, report, path, migration


def test_prepared_stage8_verifies_current_target_not_historical_prepare_sha(migrated):
    fx, report, path, migration = migrated
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in fx.src.rglob("*") if p.is_file()}
    verified = checkpoint.verify_prepared(fx.work.parent, fx.donor, fx.target, migration.TARGET_BASE_SHA, path)
    assert verified["patch_count"] == 231
    assert verified["donor_run_id"] == 38387174079
    assert verified["prepareSourceKey"] == report["key"]
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before


@pytest.mark.parametrize("field", ["previous_sha", "target_sha", "donor_run_id", "patch_count", "previous_key",
    "prior_migration_receipt_sha256", "source_proof", "previous_completion", "source_changes"])
def test_prepared_receipt_tampering_fails_closed(migrated, field):
    fx, report, path, migration = migrated
    report[field] = "forged"
    path.write_bytes(migration.arp._json(report))
    (fx.src / migration.RECEIPT).write_bytes(path.read_bytes())
    with pytest.raises((RuntimeError, ValueError, TypeError)):
        checkpoint.verify_prepared(fx.work.parent, fx.donor, fx.target, migration.TARGET_BASE_SHA, path)


def test_prepared_target_hash_manifest_is_not_a_substitute_for_full_stack_proof(migrated, monkeypatch):
    fx, report, path, migration = migrated
    def fail(*args, **kwargs):
        raise RuntimeError("complete stack proof failed")
    monkeypatch.setattr(migration, "_roundtrip", fail)
    with pytest.raises(RuntimeError, match="complete stack"):
        checkpoint.verify_prepared(fx.work.parent, fx.donor, fx.target, migration.TARGET_BASE_SHA, path)


def listing(extra=()):
    files = {name: b"fixture" for name in checkpoint.MARKERS}
    files.update({"src/out/Default/build.ninja": b"rule fixture", "src/out/Default/chrome.exe": b"exe",
                  "src/out/Default/chrome.dll": b"dll"})
    return "\n\n".join([f"Path = chromix/{name}\nSize = {len(data)}\nFolder = -\nAttributes = A"
                         for name, data in files.items()] + list(extra)) + "\n"


def test_archive_requires_prior_migration_but_forbids_new_or_interrupted_migration():
    assert checkpoint.validate_archive_listing(listing())
    for path in ["src/" + checkpoint.RECEIPT, "src/.CHROMIX-layer-in-progress",
                 ".CHROMIX-upstream-restore-transaction/lock", "src/out/Chromix/cache",
                 "src/../outside", "src/CON.txt"]:
        with pytest.raises(ValueError):
            checkpoint.validate_archive_listing(listing([f"Path = chromix/{path}\nSize = 1\nFolder = -"]))


def test_recorded_github_api_metadata_matches_fixed_contract():
    data = json.loads((Path(__file__).with_name("fixtures") /
                       "windows155_stage8_api_metadata.json").read_text())
    client = Metadata()
    client.run = data["run"]
    client.jobs = data["jobs"]["jobs"]
    client.artifacts = data["artifacts"]["artifacts"]
    assert checkpoint.validate_metadata(client)["artifacts"] == list(checkpoint.ARTIFACTS)


def test_recorded_receipt_chain_and_completion_hash(tmp_path):
    import gzip
    import migrate_windows155_stage8_api_x64_snapshot as migration
    data = json.loads(gzip.decompress((Path(__file__).with_name("fixtures") /
                     "windows155_stage8_api_receipts.json.gz").read_bytes()))
    for label, filename, digest in (("stage7", migration.HISTORY_RECEIPT, migration.HISTORY_RECEIPT_SHA256),
                                   ("stage8", migration.PRIOR_RECEIPT, migration.PRIOR_RECEIPT_SHA256)):
        payload = migration.arp._json(data[label])
        assert migration.arp._sha(payload) == digest
        (tmp_path / filename).write_bytes(payload)
    migration._history_chain(tmp_path, data["stage8"])
    prior = data["stage8"]
    outputs = dict(prior["previous_completion"]["outputs"])
    outputs.update({item["path"]: item["after_sha256"] for item in prior["source_changes"]})
    completion = {"schema_version": 1, "identity": prior["identity"],
        "identity_sha256": migration.arp._sha(migration.arp._json(prior["identity"])), "outputs": outputs}
    assert migration.arp._sha(migration.arp._json(completion)) == migration.DONOR_SOURCE_RECEIPT_SHA256
    assert data["prepared"]["source_receipt_sha256"] == migration.DONOR_SOURCE_RECEIPT_SHA256
    assert len(completion["identity"]["series"]["patches"]) == 230


def test_snapshot_requires_both_historical_receipts_and_exact_completion(tmp_path, monkeypatch):
    from tools.tests.test_migrate_windows155_stage8_api_x64_snapshot import Fixture
    import migrate_windows155_stage8_api_x64_snapshot as migration
    fx = Fixture(tmp_path, monkeypatch)
    encode = migration.arp._json
    upstream = encode({"status": "restored", "platform": "windows", "arch": "x64",
                       "identity": {"platform": "windows", "arch": "x64"}})
    files = {name: (fx.work / name).read_bytes() for name in checkpoint.MARKERS}
    files["src/.chromix-upstream-restored.json"] = upstream
    for filename, constant in ((migration.HISTORY_RECEIPT, "HISTORY_RECEIPT_SHA256"),
                               (migration.PRIOR_RECEIPT, "PRIOR_RECEIPT_SHA256")):
        receipt = json.loads(files["src/" + filename])
        receipt["upstream_receipt_sha256"] = migration.arp._sha(upstream)
        if filename == migration.PRIOR_RECEIPT:
            receipt["prior_migration_receipt_sha256"] = migration.HISTORY_RECEIPT_SHA256
        files["src/" + filename] = encode(receipt)
        monkeypatch.setattr(migration, constant, migration.arp._sha(encode(receipt)))
    assert checkpoint.validate_snapshot_files(files)["restored_patches"] == 230
    for name in (migration.HISTORY_RECEIPT, migration.PRIOR_RECEIPT, migration.arp.MARKER):
        corrupted = dict(files)
        corrupted["src/" + name] += b"\n"
        with pytest.raises(ValueError, match="digest"):
            checkpoint.validate_snapshot_files(corrupted)
        del corrupted["src/" + name]
        with pytest.raises(ValueError, match="markers missing"):
            checkpoint.validate_snapshot_files(corrupted)


def test_old_recovery_routes_are_unchanged_and_not_imported():
    import validate_windows155_stage7_checkpoint as stage7
    import validate_windows155_stage8_checkpoint as stage8
    import migrate_windows155_stage8_x64_snapshot as old_migration
    assert stage7.RUN_ID == 38175278759
    assert stage8.RUN_ID == 38355909882
    assert old_migration.PROFILE == "windows155-x64-stage8-225-to-230"
    assert "src/" + checkpoint.PRIOR_RECEIPT in stage8.FORBIDDEN
    assert "src/" + checkpoint.PRIOR_RECEIPT in checkpoint.MARKERS
    assert "import migrate_windows155_stage8_x64_snapshot" not in Path(checkpoint.__file__).read_text()
