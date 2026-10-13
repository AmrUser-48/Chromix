#!/usr/bin/env python3
"""Validate the immutable run-38175278759 Windows x64 stage-7 checkpoint."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

if __package__:
    from .restore_upstream_cache import parse_gn_assignments
    from .validate_windows_snapshot import Client
else:
    from restore_upstream_cache import parse_gn_assignments
    from validate_windows_snapshot import Client

REPOSITORY = "xiaozhou26/Chromix"
WORKFLOW = "build-win-x64-github"
RUN_ID = 38175278759
JOB_ID = 115019936453
DONOR_SHA = "f06673f68e0ca6dc55e81102f0331ab7b6316bcf"
BRANCH = "build/windows155-stage2-metagen-cli-recovery"
ATTEMPT = 1
STAGE = 7
VERSION = "155.0.8059.39"
CORE = "ffeb45a2bb88bc56a1dfed31410aeb10e9efcd5a"
WINDOWS = "989b4382364baaddb8b3180e1eb1030ca96a7273"
PROFILE = "windows155-x64-stage7-224-to-225"
RECEIPT = ".chromix-windows155-x64-migration.json"
PREFIX = "tree-s7-attempt-1-part"
ARTIFACTS = (
    {"id": 11784517650, "name": PREFIX + "1", "size_in_bytes": 9663676664,
     "expired": False, "digest": "sha256:0586764e680978b54e3e26ee9c77875d3505fae80f72cab9c450c1c58d982827"},
    {"id": 11785560392, "name": PREFIX + "2", "size_in_bytes": 3398865370,
     "expired": False, "digest": "sha256:0384eb4b99b3224b8ea895ac937be09a661a905e21ee22333b08ca1aa9e6c386"},
)
# The pinned donor initializes this marker for ARM64, not unmarked x64 trees.
OPTIONAL_MARKERS = (".chromix-target-arch",)
MARKERS = (
    "src/chrome/VERSION",
    "src/.chromix-upstream-restored.json", "src/.chromix-restored-patches.json",
    "src/.chromix-source-ready", "src/.chromix-source-unpacked", "src/out/Default/args.gn",
)
FORBIDDEN = {
    "src/" + RECEIPT,
    "src/.chromix-windows-snapshot-migration.json",
    "src/.chromix-windows154-arm64-migration.json",
    "src/.chromix-domain-substitution-in-progress",
    "src/.chromix-restored-patches-in-progress",
    "src/.chromix-layer-in-progress", "src/.chromix-patch-in-progress",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def exact(data: dict, expected: dict, label: str) -> None:
    require(isinstance(data, dict), label + " is not an object")
    require(all(type(data.get(key)) is type(value) and data[key] == value
                for key, value in expected.items()), label + " mismatch")


def objects(items, label: str) -> list[dict]:
    require(isinstance(items, list) and all(isinstance(item, dict)
            and isinstance(item.get("name"), str) for item in items), "invalid " + label)
    return items


def validate_metadata(client, repository: str = REPOSITORY) -> dict:
    require(repository == REPOSITORY, "checkpoint repository mismatch")
    run = client.get(f"/actions/runs/{RUN_ID}")
    exact(run, {"id": RUN_ID, "path": f".github/workflows/{WORKFLOW}.yml",
                "head_branch": BRANCH, "head_sha": DONOR_SHA, "run_attempt": ATTEMPT,
                "status": "completed", "conclusion": "failure"}, "checkpoint run identity")
    exact(run, {"event": "workflow_dispatch", "workflow_id": 339967931}, "checkpoint workflow event")
    for key in ("repository", "head_repository"):
        exact(run.get(key), {"full_name": REPOSITORY}, "checkpoint " + key)
    jobs = objects(client.items(f"/actions/runs/{RUN_ID}/attempts/{ATTEMPT}/jobs", "jobs"), "jobs")
    candidates = [job for job in jobs if job.get("id") == JOB_ID
                  or job["name"] == "stage 7 (resume compile)"]
    require(len(candidates) == 1, "exact checkpoint job is missing or ambiguous")
    job = candidates[0]
    exact(job, {"id": JOB_ID, "name": "stage 7 (resume compile)", "run_id": RUN_ID,
                "run_attempt": ATTEMPT, "head_sha": DONOR_SHA, "status": "completed",
                "conclusion": "failure"}, "checkpoint job identity")
    steps = objects(job.get("steps"), "checkpoint steps")
    for name in ("Ensure build tree snapshot", "Upload tree part 1", "Upload tree part 2"):
        matches = [step for step in steps if step["name"] == name]
        require(len(matches) == 1, "checkpoint step missing or ambiguous: " + name)
        exact(matches[0], {"status": "completed", "conclusion": "success"}, "checkpoint step " + name)
    failed = [step for step in steps if step["name"] == "Run stage 7"]
    require(len(failed) == 1, "failed compilation step missing or ambiguous")
    exact(failed[0], {"status": "completed", "conclusion": "failure"}, "donor failed compilation")
    listed = objects(client.items(f"/actions/runs/{RUN_ID}/artifacts", "artifacts"), "artifacts")
    selected = sorted([item for item in listed if item["name"].startswith(PREFIX)
                       or item.get("id") in {part["id"] for part in ARTIFACTS}], key=lambda item: item["name"])
    require(len(selected) == len(ARTIFACTS), "checkpoint artifact set missing or ambiguous")
    for item, expected in zip(selected, ARTIFACTS):
        exact(item, expected, "checkpoint artifact identity")
        exact(item.get("workflow_run"), {"id": RUN_ID, "head_sha": DONOR_SHA,
                                          "head_branch": BRANCH}, "checkpoint artifact origin")
    return {"repository": REPOSITORY, "platform": "windows", "arch": "x64", "workflow": WORKFLOW,
            "run_id": RUN_ID, "job_id": JOB_ID, "attempt": ATTEMPT, "stage": STAGE,
            "head_branch": BRANCH, "head_sha": DONOR_SHA, "pattern": PREFIX + "*",
            "artifacts": [dict(item) for item in ARTIFACTS]}


def validate_snapshot_files(files: dict[str, bytes]) -> dict:
    require(set(MARKERS) <= files.keys(), "snapshot markers missing")
    try:
        text = {name: files[name].decode("utf-8-sig").strip()
                for name in (*MARKERS, *OPTIONAL_MARKERS) if name in files}
    except UnicodeDecodeError:
        raise ValueError("snapshot marker is not UTF-8") from None
    require(".chromix-target-arch" not in text or text[".chromix-target-arch"] == "x64",
            "snapshot architecture mismatch")
    version = text["src/chrome/VERSION"]
    fields = re.findall(r"(?m)^(MAJOR|MINOR|BUILD|PATCH)=(\d+)\s*$", version)
    require(len(fields) == 4 and len(dict(fields)) == 4
            and ".".join(dict(fields).get(key, "") for key in ("MAJOR", "MINOR", "BUILD", "PATCH")) == VERSION,
            "snapshot Chromium VERSION mismatch")
    receipt = json.loads(text["src/.chromix-upstream-restored.json"])
    exact(receipt, {"status": "restored", "platform": "windows", "arch": "x64"}, "upstream receipt")
    exact(receipt.get("identity"), {"platform": "windows", "arch": "x64"}, "upstream receipt identity")
    patches = json.loads(text["src/.chromix-restored-patches.json"])
    exact(patches, {"schema_version": 1}, "patch receipt schema")
    require(isinstance(patches.get("identity_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", patches["identity_sha256"])
            and isinstance(patches.get("outputs"), dict) and bool(patches["outputs"]), "patch receipt is incomplete")
    identity = patches.get("identity")
    exact(identity, {"platform": "windows"}, "patch receipt identity")
    exact(identity.get("selection"), {"version": VERSION}, "patch receipt selection")
    series = identity.get("series")
    require(isinstance(series, dict) and isinstance(series.get("patches"), list)
            and len(series["patches"]) == 224, "patch receipt must contain exactly 224 donor patches")
    canonical = (json.dumps(identity, sort_keys=True, indent=2) + "\n").encode()
    require(patches["identity_sha256"] == hashlib.sha256(canonical).hexdigest(), "patch identity digest mismatch")
    require(re.fullmatch(re.escape("|".join((VERSION, CORE, WINDOWS))) + r"\|[0-9a-f]{64}",
                         text["src/.chromix-source-ready"]) is not None
            and text["src/.chromix-source-unpacked"] == VERSION, "snapshot ready/version markers mismatch")
    args = text["src/out/Default/args.gn"]
    keys = re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", args)
    assignments = parse_gn_assignments(args)
    require(len(keys) == len(set(keys)) and assignments.get("target_cpu") == '"x64"'
            and assignments.get("target_os") == '"win"'
            and assignments.get("v8_target_cpu", '"x64"') == '"x64"',
            "snapshot GN architecture mismatch")
    return {"status": "verified", "platform": "windows", "arch": "x64", "version": VERSION,
            "restored_patches": len(series["patches"]), "restored_outputs": len(patches["outputs"])}


def validate_archive_listing(listing: str) -> dict[str, dict]:
    members = {}
    for block in re.split(r"\r?\n\s*\r?\n", listing.strip()):
        record = {}
        for line in block.splitlines():
            if " = " in line:
                key, value = line.split(" = ", 1)
                require(key not in record, "ambiguous archive listing field")
                record[key] = value
        require("Path" in record, "invalid archive member listing")
        name = record["Path"].replace("\\", "/")
        parts = name.split("/")
        require(parts[0] == "chromix" and all(part not in ("", ".", "..")
                and not any(ord(char) < 32 or char in ':<>"|?*' for char in part)
                and not part.endswith((" ", "."))
                and not re.fullmatch(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                for part in parts), "unsafe archive path")
        require(not any(key in record for key in ("Symbolic Link", "Hard Link", "Reparse"))
                and not re.search(r"(?:^|\s)l[rwx-]{9}(?:\s|$)", record.get("Attributes", "")),
                "archive links are forbidden")
        require(record.get("Anti", "-") == "-", "archive anti-items are forbidden")
        folded = name.casefold()
        require(folded not in members, "duplicate archive path")
        require(not folded.startswith("chromix/src/out/chromix")
                and folded not in {"chromix/" + name for name in FORBIDDEN}
                and not any(part.startswith(".chromix-upstream-restore-") for part in parts),
                "snapshot contains cold output or interrupted migration state")
        members[folded] = record
    for marker in (*MARKERS, *OPTIONAL_MARKERS):
        record = members.get("chromix/" + marker.lower())
        if record is None and marker in OPTIONAL_MARKERS:
            continue
        require(record is not None and record.get("Folder", "-") == "-"
                and re.fullmatch(r"[0-9]+", record.get("Size", ""))
                and 0 < int(record["Size"]) <= 32 * 1024 * 1024, "snapshot marker missing or oversized: " + marker)
    for payload in ("src/out/Default/build.ninja", "src/out/Default/chrome.exe"):
        record = members.get("chromix/" + payload.lower())
        require(record is not None and record.get("Folder", "-") == "-"
                and re.fullmatch(r"[0-9]+", record.get("Size", ""))
                and int(record["Size"]) > 0, "compiled checkpoint payload missing: " + payload)
    return members


def verify_snapshot_archive(archive: Path, seven_zip: str | None = None) -> dict:
    command = seven_zip or shutil.which("7z.exe") or shutil.which("7z") or shutil.which("7zz")
    require(bool(command), "7z is required to inspect the checkpoint")
    listing = subprocess.run([command, "l", "-slt", "-ba", "-sccUTF-8", str(archive)],
                             check=True, capture_output=True).stdout.decode("utf-8", "strict")
    members = validate_archive_listing(listing)
    files = {}
    for marker in (*MARKERS, *OPTIONAL_MARKERS):
        record = members.get("chromix/" + marker.lower())
        if record is not None:
            files[marker] = subprocess.run([command, "e", str(archive), record["Path"], "-so"],
                                           check=True, capture_output=True).stdout
    result = validate_snapshot_files(files)
    result["archive"] = str(archive)
    return result


def verify_prepared(work_dir: Path, donor_repo: Path, repo: Path,
                    target_sha: str, migration_receipt: Path) -> dict:
    import apply_restored_patches as arp
    import migrate_windows155_x64_snapshot as migration
    import migrate_windows_snapshot as safe
    import patch_selection
    import restore_upstream_cache as upstream

    require(bool(re.fullmatch(r"[0-9a-f]{40}", target_sha)), "invalid target SHA")
    root = safe._safe(work_dir, directory=True)
    work = safe._safe(root / "chromix", directory=True)
    src = safe._safe(work / "src", directory=True)
    donor = safe._safe(donor_repo, directory=True)
    repo = safe._safe(repo, directory=True)
    git = safe._host_git((work, donor, repo))
    old_state, new_state = migration._repository_inputs(git, donor, repo)
    require(old_state[0] == DONOR_SHA and new_state[0] == target_sha, "source/target Git SHA mismatch")
    for directory in (work, src):
        require(not any(path.name.startswith(".chromix-upstream-restore-") or
                        path.name.startswith(".chromix") and re.search(r"in[-_]?progress", path.name)
                        for path in directory.iterdir()), "interrupted migration/preparation")
    payload = safe._safe(migration_receipt).read_bytes()
    require(payload == safe._file(src, RECEIPT).read_bytes(), "migration receipt copies differ")
    receipt = json.loads(payload)
    exact(receipt, {"schema_version": 1, "status": "migrated", "profile": PROFILE,
                   "operation": "windows155-x64-snapshot-migration", "platform": "windows", "arch": "x64",
                   "version": VERSION, "previous_sha": DONOR_SHA, "target_sha": target_sha,
                   "donor_run_id": RUN_ID, "donor_job_id": JOB_ID, "donor_attempt": ATTEMPT,
                   "old_patch_count": 224, "patch_count": 225,
                   "changed_patches": [migration.PATCH], "changed_files": [migration.SOURCE]},
          "migration receipt identity")
    core = safe._safe(work / "tooling/ungoogled-chromium", directory=True)
    tooling = safe._safe(work / "tooling/ungoogled-chromium-windows", directory=True)
    selected, raw = patch_selection.select(repo, "windows", src=src, core=core)
    old_selected, old_raw = migration._donor_selection(donor, repo, selected, raw)
    key = migration._key(repo, selected, raw)
    previous_key = migration._key(donor, old_selected, old_raw)
    exact(receipt, {"previous_key": previous_key, "key": key, "prepareSourceKey": key}, "migration preparation keys")
    require(key.rsplit("|", 1)[1] == patch_selection.preparation_key(repo, "windows", "windows"),
            "preparation key disagrees with target selector")
    migration._markers(work, src, key)
    restored = upstream.verify_restored(work, "windows", "x64", repo=repo)
    require(upstream.verify_restored(work, "windows", "x64", repo=donor) == restored,
            "upstream receipt differs between donor and target")
    upstream_hash = hashlib.sha256(safe._file(src, upstream.MARKER).read_bytes()).hexdigest()
    require(receipt.get("upstream_receipt_sha256") == upstream_hash, "upstream receipt hash mismatch")
    migration._native_args(src, repo, core, tooling, restored)
    require(receipt.get("gn_args_sha256") == hashlib.sha256(
        safe._file(src, "out/Default/args.gn").read_bytes()).hexdigest(), "GN argument hash mismatch")
    identity, patches, lite = arp._load(repo, core, tooling, "windows", src=src)
    require(receipt.get("identity") == identity, "target source identity mismatch")
    names = migration._names(patches, lite)
    require(arp._completed(src, identity, names), "target source output hashes do not match")
    completion = json.loads(safe._file(src, arp.MARKER).read_bytes())
    proof = receipt.get("source_proof")
    normalized = {}
    for name in names | {migration.SOURCE_VERSION}:
        path = safe._file(src, name, missing=True)
        normalized[name] = arp._sha(path.read_bytes().replace(b"\r\n", b"\n")) if path.exists() else None
    exact(proof, {"method": "complete-reverse-forward-in-private-scratch",
                  "target_outputs": normalized}, "migration source proof")
    previous = receipt.get("previous_completion")
    require(isinstance(previous, dict) and isinstance(previous.get("outputs"), dict)
            and set(previous["outputs"]) == names, "donor completion proof missing")
    previous_identity = previous.get("identity")
    require(isinstance(previous_identity, dict)
            and previous.get("identity_sha256") == arp._sha(arp._json(previous_identity)),
            "donor source identity digest mismatch")
    expected_previous = migration._old_identity(identity, old_selected)
    relocated = dict(previous_identity)
    for field in ("regex", "list"):
        saved = previous_identity.get(field)
        require(isinstance(saved, dict), "donor domain identity missing")
        relocated[field] = dict(saved, path=expected_previous[field]["path"])
    require(relocated == expected_previous, "donor source selection identity mismatch")
    donor_outputs = proof.get("donor_outputs")
    require(isinstance(donor_outputs, dict) and set(donor_outputs) == set(normalized)
            and all(value is None or isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                    for value in donor_outputs.values()), "donor source proof missing")
    require(all(donor_outputs[name] == value for name, value in normalized.items()
                if name != migration.SOURCE), "unchanged donor source proof mismatch")
    changes = receipt.get("source_changes")
    require(isinstance(changes, list) and len(changes) == 1, "source delta must contain only PNG fix")
    exact(changes[0], {"path": migration.SOURCE,
                       "before_sha256": previous["outputs"][migration.SOURCE],
                       "after_sha256": completion["outputs"][migration.SOURCE]}, "PNG source hashes")
    return {"schema_version": 1, "status": "verified", "profile": PROFILE,
            "platform": "windows", "arch": "x64", "version": VERSION,
            "previous_sha": DONOR_SHA, "target_sha": target_sha,
            "donor_run_id": RUN_ID, "donor_job_id": JOB_ID, "donor_attempt": ATTEMPT,
            "previous_key": previous_key, "prepareSourceKey": key, "patch_count": 225,
            "source_version": patch_selection.source_version(src),
            "migration_receipt_sha256": hashlib.sha256(payload).hexdigest(),
            "upstream_receipt_sha256": upstream_hash,
            "source_receipt_sha256": hashlib.sha256(safe._file(src, arp.MARKER).read_bytes()).hexdigest(),
            "work_dir": str(root), "donor_repo": str(donor), "target_repo": str(repo),
            "qualification": "source receipts verified; native acceptance still mandatory"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--metadata-report", type=Path)
    modes.add_argument("--snapshot", type=Path)
    modes.add_argument("--prepare-report", type=Path)
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--seven-zip")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--donor-repo", type=Path)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--target-sha")
    parser.add_argument("--migration-receipt", type=Path)
    args = parser.parse_args(argv)
    require(args.repository == REPOSITORY, "checkpoint repository mismatch")
    report = args.metadata_report or args.prepare_report or args.report
    require(report is not None, "a report path is required")
    result = {"status": "failed", "profile": PROFILE}
    try:
        if args.metadata_report:
            result = validate_metadata(Client(REPOSITORY, os.environ.get("GH_TOKEN", "")), args.repository)
        elif args.snapshot:
            result = verify_snapshot_archive(args.snapshot, args.seven_zip)
        else:
            require(all((args.work_dir, args.donor_repo, args.repo, args.target_sha, args.migration_receipt)),
                    "prepare verification requires work-dir, donor-repo, repo, target-sha and migration-receipt")
            result = verify_prepared(args.work_dir, args.donor_repo, args.repo, args.target_sha, args.migration_receipt)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
        raise
    finally:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
