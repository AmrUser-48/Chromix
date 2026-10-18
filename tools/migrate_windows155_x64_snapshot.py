#!/usr/bin/env python3
"""One-shot 224-to-225 migration of the pinned Windows 155 x64 checkpoint.

Authenticate run 38175278759/job 115019936453 and extract it separately. Stop all
build processes before invoking this trusted target helper. Donor code never runs.
Failures after claiming the transaction require a fresh extraction, not a retry.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

import apply_restored_patches as arp
import migrate_windows_snapshot as safe
import install_windows_metagen_bindings as metagen
import patch_selection as selection
from platform_pins import load_pins
import restore_upstream_cache as upstream

DONOR_SHA = "f06673f68e0ca6dc55e81102f0331ab7b6316bcf"
TARGET_BASE_SHA = DONOR_SHA
VERSION = "155.0.8059.39"
CORE = "ffeb45a2bb88bc56a1dfed31410aeb10e9efcd5a"
WINDOWS = "989b4382364baaddb8b3180e1eb1030ca96a7273"
PROFILE = "windows155-x64-stage7-224-to-225"
TRUSTED_REPO = Path(__file__).resolve().parents[1]
BLOCKER = ".chromix-upstream-restore-windows155-x64-migration"
RECEIPT = ".chromix-windows155-x64-migration.json"
READY = ".chromix-source-ready"
SOURCE_VERSION = "chrome/VERSION"
PATCH = "patches/chromium155-windows/0227-opaque-png-export-consistency.patch"
SOURCE = "third_party/blink/renderer/core/html/canvas/canvas_async_blob_creator.cc"
ADDITIONS = (PATCH,)
MANIFEST = "build/patch-additions-windows155.json"
OLD_SELECTOR = "87d06a9ed672af8bfe1065d43d69140c142d1770287d1b82fb9c0e5d2fad4f11"
NEW_SELECTOR = "4c2392a036f0e3c754ed887b41e67e624d80350f53d0059f7308205d05ca5185"
OLD_IDENTITY = "1183c611fc1d2ab2945af44830c8d3f6084b4842cd0f0ca9b7636680ccdbe38d"
PATCH_SHA256 = "4c048a50930c3541a3bb9e4442ad7f18e94c04ebccf86c0cfbfc286b15a70751"
ORIGINAL_ARGS_SHA256 = "3a389e4125781ebf668790df4d58d1dc11d384518fe0fd9696e1c35417d05d3a"
# These host entry points do not supply patch, GN, or preparation-layer bytes.
HOST_CHANGES = frozenset({"build/windows/ci-stage.ps1"})
INPUT_FILES = frozenset({
    ".gitattributes", "CHROMIUM_VERSION", "CHROMIUM_WINDOWS_VERSION",
    "UNGOOGLED_VERSION", "UNGOOGLED_WINDOWS_VERSION",
    "tools/apply_restored_patches.py", "tools/patch_selection.py",
    "tools/platform_pins.py", "tools/prepare_restored_build.py",
    "tools/restore_upstream_cache.py", "tools/fetch_upstream_cache.py",
    "tools/upstream_script_identity.py", "tools/merge_gn_args.py",
    "tools/restore_ninja.py", "tools/verify_patch_stack.py",
    "tools/install_windows_metagen_bindings.py", "tools/repair_windows_midl.py",
    "tools/migrate_windows_snapshot.py",
})


def _require(condition, message):
    if not condition:
        raise arp.ApplyError(message)


def _protected(name):
    return name not in HOST_CHANGES and (name in INPUT_FILES or
        name.startswith(("build/", "assets/", "patches/")))


def _pins(repo):
    pins = load_pins(repo, "windows")
    expected = {"ChromiumVersion": VERSION, "UngoogledVersion": VERSION + "-1",
                "UngoogledCommit": CORE, "UngoogledWindowsVersion": VERSION + "-1.1",
                "UngoogledWindowsCommit": WINDOWS}
    _require(all(pins.get(k) == v for k, v in expected.items()),
             "requires identical reviewed Windows 155.0.8059.39 core/platform pins")
    return pins


def _repository_inputs(git, donor, target):
    old_head, old_tree = safe._tree(git, donor, DONOR_SHA)
    head, tree = safe._tree(git, target)
    safe._git(git, target, "merge-base", "--is-ancestor", TARGET_BASE_SHA, head)
    baseline = {}
    for record in safe._git(git, target, "ls-tree", "-rz", TARGET_BASE_SHA).split(b"\0"):
        if record:
            metadata, name = record.split(b"\t", 1)
            baseline[name.decode()] = tuple(metadata.decode().split())
    _require(all(tree.get(n) == baseline.get(n) for n in set(tree) | set(baseline)
                 if _protected(n) and n not in {PATCH, MANIFEST, "tools/patch_selection.py"}), "target preparation inputs differ from the pinned target ref")
    old_state, new_state = safe._tracked(donor, old_tree), safe._tracked(target, tree)
    for root, entries in ((donor, old_tree), (target, tree)):
        for directory in ("build", "assets", "patches"):
            _require(safe._walk_files(root, directory) ==
                     {n for n in entries if n.startswith(directory + "/")},
                     "extra/missing preparation input: " + directory)
    allowed = {PATCH, MANIFEST, "tools/patch_selection.py"}
    for name in set(old_tree) | set(tree):
        if _protected(name) and name not in allowed:
            _require(old_tree.get(name) == tree.get(name),
                     "donor preparation input changed outside fixed profile: " + name)
    for name in (PATCH, MANIFEST):
        _require(name not in old_tree and tree.get(name, ())[:2] == ("100644", "blob"),
                 "new input must be an addition: " + name)
    for root, expected in ((donor, OLD_SELECTOR), (target, NEW_SELECTOR)):
        _require(arp._sha(safe._file(root, "tools/patch_selection.py").read_bytes()) == expected,
                 "selector hash mismatch")
    _require(_pins(donor) == _pins(target), "donor/target Windows pins differ")
    return (old_head, old_tree, old_state), (head, tree, new_state)


def _donor_selection(donor, target, selected, raw):
    """Interpret the pinned donor as data using only the trusted current selector."""
    old, prefix = selection.select(donor, "windows")
    addition = safe._file(target, PATCH).read_bytes()
    _require(arp._sha(addition) == PATCH_SHA256, "PNG patch hash mismatch")
    _require(arp.transform_patch(addition, set(), [])[1] == [(SOURCE, "modify", None)],
             "PNG addition must only modify async canvas")
    _require(len(prefix) == 224 and len(raw) == 225 and raw == [*prefix, (PATCH, addition)]
             and arp._sha(arp._json(old)) == OLD_IDENTITY,
             "expected exact historical 224-patch identity and one PNG addition")
    _require(safe._file(donor, "patches/series").read_bytes() ==
             safe._file(target, "patches/series").read_bytes(), "root series changed")
    expected = dict(old["selection"], additions={
        "schema_version": 1, "kind": "windows155-compiled-png-recovery",
        "manifest": {"path": MANIFEST, "sha256": selection.WINDOWS155_ADDITIONS_SHA256},
        "patches": [{"path": PATCH, "sha256": PATCH_SHA256}],
    })
    _require(selected["selection"] == expected, "target selection identity changed")
    return old, prefix


def _key(repo, selected, raw):
    digest = hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode())
    for _, data in raw:
        digest.update(data)
    for name in sorted(safe._walk_files(repo, arp.LITE)):
        digest.update(name[len(arp.LITE) + 1:].encode())
        digest.update(safe._file(repo, name).read_bytes())
    return "|".join((VERSION, CORE, WINDOWS, digest.hexdigest()))


def _old_identity(current, selected):
    identity = copy.deepcopy(current)
    identity["series"] = {"path": "patches/series", "sha256": selected["series_sha256"],
                          "patches": selected["patches"]}
    identity["selection"] = selected["selection"]
    return identity


def _completed(src, expected, names):
    saved = safe._load_receipt(safe._file(src, arp.MARKER))
    original = saved.get("identity")
    _require(isinstance(original, dict), "missing restored patch identity")
    relocated = copy.deepcopy(original)
    roots = []
    for field, suffix in (("regex", "/tooling/ungoogled-chromium/domain_regex.list"),
                          ("list", "/tooling/ungoogled-chromium-windows/domain_substitution.list")):
        path = original.get(field, {}).get("path", "").replace("\\", "/")
        _require(path.endswith(suffix) and (path.startswith("/") or re.match(r"^[A-Za-z]:/", path))
                 and not any(p in (".", "..") for p in path.split("/")), "invalid historical tooling path")
        roots.append(path[:-len(suffix)])
        relocated[field]["path"] = expected[field]["path"]
    _require(roots[0] == roots[1] and relocated == expected, "donor receipt selection/domain/lite identity differs")
    _require(arp._completed(src, original, names), "missing donor completion receipt")
    return saved


def _blockers(work, src, owned=False):
    for root in (work, src):
        for path in root.iterdir():
            name = path.name.casefold()
            if owned and root == work and name == BLOCKER:
                continue
            _require(not (name.startswith(".chromix-upstream-restore-") or
                name.startswith(".chromix") and re.search(r"in[-_]?progress", name)),
                "unfinished transaction: " + str(path))
    for name in (RECEIPT, safe.RECEIPT):
        _require(not safe._file(src, name, missing=True).exists(), "repeat/prior migration refused")


def _markers(work, src, key):
    # This fixed donor left x64 unmarked; strict GN and verified receipts bind the architecture.
    arch = safe._file(work, ".chromix-target-arch", missing=True)
    _require(not arch.exists() or safe._text(arch) == "x64", "architecture must be x64")
    _require(not (src / "out/Chromix").exists(), "cold out/Chromix snapshot is unsupported")
    values = {".chromix-source-unpacked": VERSION, ".chromix-ungoogled-core": CORE,
              ".chromix-ungoogled-windows": WINDOWS, ".chromix-binaries-pruned": CORE,
              ".chromix-domain-substituted": CORE, ".chromix-toolchain-ready": CORE + "|" + WINDOWS,
              ".chromix-patches": key.rsplit("|", 1)[1],
              ".chromix-patch-selection": key.rsplit("|", 1)[1], READY: key}
    for name, value in values.items():
        _require(safe._text(safe._file(src, name)) == value, "prepared marker mismatch: " + name)
    _require(selection.source_version(src) == VERSION, "source VERSION mismatch")
    text = safe._text(safe._file(src, "out/Default/args.gn"))
    keys = re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", text)
    args = upstream.parse_gn_assignments(text)
    _require(len(keys) == len(set(keys)) and args.get("target_cpu") == '"x64"'
             and args.get("target_os") == '"win"' and args.get("v8_target_cpu", '"x64"') == '"x64"',
             "ambiguous/non-x64 Windows GN configuration")
    _require(safe._file(src, "out/Default/chrome.exe").stat().st_size > 0,
             "requires a compiled native snapshot, not a cold source tree")
    return safe._capture(src, values)


def _native_args(src, target, core, tooling, receipt):
    original = receipt["original_args"]
    _require(original["assignments"].get("chrome_pgo_phase") == "2"
             and original.get("sha256") == ORIGINAL_ARGS_SHA256,
             "requires the exact original Windows155 native PGO2 configuration")
    # Replay the pinned ci-stage native merge and post-merge configure in scratch only.
    with tempfile.TemporaryDirectory(prefix="chromix-windows155-x64-gn-") as directory:
        out = Path(directory)
        args = out / "args.gn"
        args.write_text(original["text"], encoding="utf-8")
        inputs = [safe._file(root, name) for root, name in (
            (core, "flags.gn"), (tooling, "flags.windows.gn"), (target, "build/args.windows.gn"))]
        result = subprocess.run([sys.executable, "-I", str(safe._file(target, "tools/merge_gn_args.py")),
            str(args), "--preserve-pgo-from", str(args), str(args), *map(str, inputs)],
            cwd=out, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        _require(result.returncode == 0,
                 "pinned native GN merge failed: " + result.stderr.decode(errors="replace"))
        metagen.configure(src, out, VERSION)
        expected = upstream.parse_gn_assignments(args.read_text(encoding="utf-8"))
    actual = upstream.parse_gn_assignments(safe._text(safe._file(src, "out/Default/args.gn")))
    difference = {
        "missing": {key: expected[key] for key in sorted(expected.keys() - actual.keys())},
        "unexpected": {key: actual[key] for key in sorted(actual.keys() - expected.keys())},
        "changed": {key: {"expected": expected[key], "actual": actual[key]}
                    for key in sorted(expected.keys() & actual.keys()) if expected[key] != actual[key]},
    }
    _require(actual == expected, "GN args differ from pinned native x64 inputs: "
             + json.dumps(difference, sort_keys=True, separators=(",", ":")))


def _names(patches, lite):
    names = set(lite) | {name for _, _, entries in patches for name, _, _ in entries}
    _require(not any(name.split("/")[0].casefold() == "out" or name.startswith(".ninja")
                     or name == SOURCE_VERSION for name in names), "patches may not touch VERSION/Ninja outputs")
    _require(len({name.casefold() for name in names}) == len(names), "case-colliding source paths")
    return names


def _bytes(src, names):
    return {n: safe._file(src, n).read_bytes() if safe._file(src, n, missing=True).exists() else None
            for n in sorted(names)}


def _apply(stage, patches, git, reverse=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CEILING_DIRECTORIES=str(stage.parent), GIT_NO_REPLACE_OBJECTS="1", LC_ALL="C")
    for name, data, entries in reversed(patches) if reverse else patches:
        patch = stage.parent / "current.patch"
        patch.write_bytes(data)
        command = [git, "-c", "core.autocrlf=false", "-c", "core.hooksPath=" + os.devnull,
                   "apply", "--no-index", "--whitespace=nowarn"]
        if reverse:
            command.append("--reverse")
        result = subprocess.run([*command, str(patch)], cwd=stage, env=env,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        _require(result.returncode == 0, "scratch patch failed: " + name + ": " + result.stderr.decode(errors="replace"))
        for target, action, _ in entries:
            removed = action == ("create" if reverse else "delete")
            _require(safe._file(stage, target, missing=True).exists() != removed,
                     "unexpected scratch patch file state: " + target)


def _roundtrip(stage, patches, git, names):
    before = _bytes(stage, names)
    _apply(stage, patches, git, reverse=True)
    _apply(stage, patches, git)
    _require(_bytes(stage, names) == before, "complete stack roundtrip changed source")
    return {n: arp._sha(data) if data is not None else None for n, data in before.items()}


def migrate(work_dir, donor_repo, target_repo, donor_sha, *, report_stream=None):
    _require(donor_sha == DONOR_SHA, "donor-sha must match the fixed stage7 compiled donor")
    work, donor, target = (safe._safe(Path(p), directory=True) for p in (work_dir, donor_repo, target_repo))
    _require(target == TRUSTED_REPO, "target-repo must be the running trusted helper checkout")
    roots = (work, donor, target)
    _require(len(set(roots)) == 3 and not any(a.is_relative_to(b) for a in roots for b in roots if a != b),
             "work and repositories must be separate non-nested directories")
    temporary = safe._safe(Path(tempfile.gettempdir()).resolve(), directory=True)
    _require(not any(temporary.is_relative_to(root) for root in roots), "scratch must be outside work/repositories")
    src = safe._safe(work / "src", directory=True)
    _blockers(work, src)
    git = safe._host_git(roots)
    old_repo_state, repo_state = _repository_inputs(git, donor, target)
    core = safe._safe(work / "tooling/ungoogled-chromium", directory=True)
    tooling = safe._safe(work / "tooling/ungoogled-chromium-windows", directory=True)
    tools = []
    for root, pin, submodule in ((core, CORE, None), (tooling, WINDOWS, CORE)):
        head, tree = safe._tree(git, root, pin)
        tools.append((root, head, tree, safe._tracked(root, tree, core_pin=submodule)))
    _require(safe._text(safe._file(core, "chromium_version.txt")) == VERSION,
             "core source version mismatch")
    selected, raw = selection.select(target, "windows", src=src, core=core)
    old_selected, old_raw = _donor_selection(donor, target, selected, raw)
    current, patches, lite = arp._load(target, core, tooling, "windows", src=src)
    previous = _old_identity(current, old_selected)
    old_patches = patches[:224]
    old_names, names = _names(old_patches, lite), _names(patches, lite)
    _require(not (set(lite) & {n for _, _, es in patches for n, _, _ in es}), "patch/lite overlap")
    for name, (data, _) in lite.items():
        _require(safe._file(src, name).read_bytes() == data, "source lite mismatch: " + name)
    old_key, key = _key(donor, old_selected, old_raw), _key(target, selected, raw)
    _require(key.rsplit("|", 1)[1] == selection.preparation_key(target, "windows", "windows"),
             "target preparation key disagrees with trusted selector")
    markers = _markers(work, src, old_key)
    immutable_names = {upstream.MARKER, SOURCE_VERSION, *upstream.REQUIRED,
                       "out/Default/chrome.exe"}
    immutable = safe._capture(src, immutable_names)
    arch_state = safe._capture(work, {".chromix-target-arch"})
    old_completion = _completed(src, previous, old_names)
    upstream_receipt = upstream.verify_restored(work, "windows", "x64", repo=target)
    _require(upstream.verify_restored(work, "windows", "x64", repo=donor) == upstream_receipt,
             "upstream receipt differs across refs")
    _native_args(src, target, core, tooling, upstream_receipt)
    before = safe._capture(src, names | {SOURCE_VERSION, arp.MARKER})
    originals = _bytes(src, names | {SOURCE_VERSION})
    _require(_completed(src, previous, old_names) == old_completion,
             "donor completion changed during source capture")
    safe._unchanged(src, before | markers)
    for data in originals.values():
        _require(data is None or not (b"\r\n" in data and b"\n" in data.replace(b"\r\n", b"")),
                 "mixed source line endings are unsupported")

    def recheck():
        _require(_repository_inputs(git, donor, target) == (old_repo_state, repo_state),
                 "repository inputs changed concurrently")
        for root, head, tree, captured in tools:
            _require(safe._tree(git, root, head) == (head, tree), "tooling ref changed concurrently")
            safe._unchanged(root, captured)
        safe._unchanged(src, immutable)
        safe._unchanged(work, arch_state)
        _require(upstream.verify_restored(work, "windows", "x64", repo=target) == upstream_receipt,
                 "immutable upstream receipt changed")

    recheck()
    blocker = work / BLOCKER
    blocker.mkdir()
    state = blocker / "transaction.json"
    safe._write(state, arp._json({"profile": PROFILE, "previous_key": old_key, "key": key}))
    lock = safe._fingerprint(state)
    with tempfile.TemporaryDirectory(prefix="chromix-windows155-x64-", dir=temporary) as directory:
        stage = Path(directory) / "src"
        stage.mkdir()
        for name, data in originals.items():
            if data is not None:
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data.replace(b"\r\n", b"\n"))
        old_proof = _roundtrip(stage, old_patches, git, names | {SOURCE_VERSION})
        _apply(stage, patches[224:], git)
        target_proof = _roundtrip(stage, patches, git, names | {SOURCE_VERSION})
        candidates = _bytes(stage, names | {SOURCE_VERSION})
        affected = {n for _, _, entries in patches[224:] for n, _, _ in entries}
        changed = {}
        for name, data in candidates.items():
            original = originals[name]
            if data is not None and original is not None and b"\r\n" in original:
                data = data.replace(b"\n", b"\r\n")
            if data != original:
                _require(name in affected and data is not None, "change outside append-only patch targets: " + name)
                changed[name] = data
        _require(changed, "new patches produced no changes")
        recheck()
        safe._unchanged(src, before | markers)
        _blockers(work, src, owned=True)
        _require(safe._fingerprint(state) == lock, "transaction blocker changed")
        published = {}
        changes = []
        for name, data in sorted(changed.items()):
            safe._unchanged(src, before | markers | published)
            path = safe._file(src, name, missing=True)
            info = path.stat() if path.exists() else None
            mtime = ((max(time.time_ns(), info.st_mtime_ns + 1_000_000_000 if info else 0)
                      + 999_999_999) // 1_000_000_000 * 1_000_000_000)
            path.parent.mkdir(parents=True, exist_ok=True)
            safe._write(path, data, mode=stat.S_IMODE(info.st_mode) if info else 0o644, mtime_ns=mtime)
            published.update(safe._capture(src, {name}))
            changes.append({"path": name, "before_sha256": before[name][0] if before[name] else None,
                            "after_sha256": arp._sha(data), "after_mtime_ns": path.stat().st_mtime_ns})
        safe._unchanged(src, before | markers | published)
        outputs = {n: arp._sha(data) if data is not None else None for n, data in _bytes(src, names).items()}
        expected_outputs = {n: arp._sha(changed.get(n, originals[n]))
                            if changed.get(n, originals[n]) is not None else None for n in names}
        _require(outputs == expected_outputs, "published source differs from scratch candidate")
        recheck()
        report = {"schema_version": 1, "status": "migrated", "profile": PROFILE,
            "operation": "windows155-x64-snapshot-migration", "platform": "windows", "arch": "x64",
            "version": VERSION, "previous_sha": DONOR_SHA, "target_sha": repo_state[0],
            "donor_run_id": 38175278759, "donor_job_id": 115019936453, "donor_attempt": 1,
            "recipe_delta": {"scripts": ["tools/patch_selection.py"],
                             "previous_selector_sha256": OLD_SELECTOR, "selector_sha256": NEW_SELECTOR},
            "gn_args_sha256": immutable["out/Default/args.gn"][0],
            "previous_key": old_key, "key": key, "prepareSourceKey": key,
            "old_patch_count": 224, "patch_count": 225, "changed_patches": [n for n, _ in raw[224:]],
            "changed_files": sorted(changed), "source_changes": changes,
            "previous_completion": old_completion, "identity": current,
            "upstream_receipt_sha256": immutable[upstream.MARKER][0],
            "source_proof": {"method": "complete-reverse-forward-in-private-scratch",
                             "donor_outputs": old_proof, "target_outputs": target_proof},
            "qualification": "patch structure verified; not compilation or full upstream attestation"}
        safe._write(safe._file(src, arp.MARKER), arp._json({"schema_version": 1,
            "identity": current, "identity_sha256": arp._sha(arp._json(current)), "outputs": outputs}))
        for name in (".chromix-patches", ".chromix-patch-selection", READY):
            safe._write(safe._file(src, name), ((key if name == READY else key.rsplit("|", 1)[1]) + "\r\n").encode())
        safe._write(safe._file(src, RECEIPT, missing=True), arp._json(report))
        _require(arp._completed(src, current, names), "target completion publication failed")
        _markers(work, src, key)
        recheck()
        safe._unchanged(src, {n: v for n, v in before.items() if n != arp.MARKER} | published)
        _require(safe._fingerprint(state) == lock, "transaction blocker changed")
        if report_stream is not None:
            report_stream.write(arp._json(report))
            report_stream.flush()
            os.fsync(report_stream.fileno())
    state.unlink()
    blocker.rmdir()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("work-dir", "donor-repo", "target-repo", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--donor-sha", required=True)
    args = parser.parse_args(argv)
    result = {"schema_version": 1, "status": "failed", "profile": PROFILE}
    try:
        output = safe._safe(args.output, missing=True)
        _require(not output.exists() and not any(output.is_relative_to(Path(p).absolute()) for p in
            (args.work_dir / "src", args.work_dir / "tooling", args.donor_repo, args.target_repo)),
            "output must be a new file outside source/tooling/repositories")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            try:
                result = migrate(args.work_dir, args.donor_repo, args.target_repo, args.donor_sha, report_stream=stream)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
                result["error"] = str(exc) + "; restore a fresh donor snapshot; never delete the blocker"
                stream.seek(0)
                stream.truncate()
                stream.write(arp._json(result))
                stream.flush()
                os.fsync(stream.fileno())
    except (OSError, ValueError, RuntimeError) as exc:
        result["error"] = str(exc)
    print(json.dumps({k: result[k] for k in ("status", "error") if k in result}))
    return int(result["status"] != "migrated")


if __name__ == "__main__":
    raise SystemExit(main())
