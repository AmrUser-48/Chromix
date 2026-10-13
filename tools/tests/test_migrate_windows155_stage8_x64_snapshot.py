"""Pinned selection and real Git scratch/transaction tests; no donor code executes."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import migrate_windows155_stage8_x64_snapshot as migration

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="host Git required")


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def patch(name, old, new):
    return (f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n"
            f"@@ -1 +1 @@\n-{old}\n+{new}\n").encode()


def created(name, text):
    return (f"diff --git a/{name} b/{name}\nnew file mode 100644\n--- /dev/null\n+++ b/{name}\n"
            f"@@ -0,0 +1 @@\n+{text}\n").encode()


def git(*args):
    return subprocess.run([shutil.which("git"), *map(str, args)], check=True, capture_output=True).stdout


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


class Fixture:
    """Synthetic transaction fixture: real Git patching, not a native build attestation."""
    def __init__(self, tmp_path, monkeypatch):
        self.work, self.donor, self.target = (tmp_path / n for n in ("chromix", "donor", "target"))
        self.src = self.work / "src"
        self.core = self.work / "tooling/ungoogled-chromium"
        self.tooling = self.work / "tooling/ungoogled-chromium-windows"
        self.old_names = [f"patches/{n:04d}-fixture.patch" for n in range(1, 226)]
        self.names = self.old_names + list(migration.ADDITIONS)
        self.raw = []
        self.sources = ("source.cc", *(f"timezone-{i}.cc" for i in range(1, 5)))
        previous = "v0"
        for number, name in enumerate(self.names, 1):
            if number <= 226:
                data = patch("source.cc", previous, f"v{number}")
                previous = f"v{number}"
            else:
                source = self.sources[number - 226]
                data = patch(source, "base", "timezone")
                put(self.src, source, "base\r\n")
            self.raw.append((name, data))
            put(self.target, name, data)
            if number <= 225:
                put(self.donor, name, data)
        for repo, names in ((self.donor, self.old_names), (self.target, self.names)):
            put(repo, "patches/series", "\n".join(names) + "\n")
            (repo / "patches/chromium155").mkdir()
            put(repo, migration.arp.LITE + "/lite.txt", "lite\n")
        put(self.core, "chromium_version.txt", migration.VERSION + "\n")
        put(self.core, "domain_regex.list", b"")
        put(self.tooling, "domain_substitution.list", b"")
        put(self.src, "source.cc", "v225\r\n")
        put(self.src, "lite.txt", "lite\n")
        put(self.src, migration.SOURCE_VERSION, "MAJOR=155\nMINOR=0\nBUILD=8059\nPATCH=39\n")
        for name in migration.upstream.REQUIRED:
            if not (self.src / name).exists():
                put(self.src, name, "unchanged\n")
        gn = 'target_cpu = "x64"\ntarget_os = "win"\nchrome_pgo_phase = 2\n'
        put(self.src, "out/Default/args.gn", gn)
        put(self.target, "tools/merge_gn_args.py", (ROOT / "tools/merge_gn_args.py").read_bytes())
        for root, name in ((self.core, "flags.gn"), (self.tooling, "flags.windows.gn"),
                           (self.target, "build/args.windows.gn")):
            put(root, name, gn)
        put(self.src, "out/Default/chrome.exe", b"compiled fixture")
        put(self.src, "out/Default/obj/cache.obj", b"cached object\x00")
        put(self.src, migration.upstream.MARKER, '{"immutable": true}\n')
        self.selected = {"series_sha256": migration.arp._sha((self.target / "patches/series").read_bytes()),
            "patches": [{"path": n, "sha256": migration.arp._sha(d)} for n, d in self.raw],
            "selection": {"schema_version": 1, "version": migration.VERSION, "platform": "windows",
                          "core_commit": migration.CORE, "platform_commit": migration.WINDOWS,
                          "base_series_sha256": migration.arp._sha((self.target / "patches/series").read_bytes())}}
        monkeypatch.setattr(migration, "TRUSTED_REPO", self.target)
        monkeypatch.setattr(migration, "_repository_inputs", lambda *a: ((migration.DONOR_SHA, {}, {}),
                                                                    (migration.TARGET_BASE_SHA, {}, {})))
        monkeypatch.setattr(migration.safe, "_tree", lambda git, root, pin: (pin, {}))
        monkeypatch.setattr(migration.upstream, "verify_restored", lambda *a, **kw:
            {"immutable": True, "original_args": {"assignments": migration.upstream.parse_gn_assignments(gn),
                "text": gn, "sha256": migration.ORIGINAL_ARGS_SHA256}})
        old_selected = copy.deepcopy(self.selected)
        old_selected["patches"] = old_selected["patches"][:225]
        old_selected["series_sha256"] = migration.arp._sha((self.donor / "patches/series").read_bytes())
        self.selected["selection"]["additions"] = {
            "schema_version": 1, "kind": "windows155-compiled-png-recovery",
            "manifest": {"path": migration.MANIFEST, "sha256": migration.selection.WINDOWS155_ADDITIONS_SHA256},
            "patches": [{"path": name, "sha256": migration.arp._sha(data)} for name, data in self.raw[225:]],
        }
        put(self.donor, "patches/series", (self.target / "patches/series").read_bytes())
        monkeypatch.setattr(migration, "OLD_IDENTITY", migration.arp._sha(migration.arp._json(old_selected)))
        monkeypatch.setattr(migration, "SOURCES", self.sources)
        monkeypatch.setattr(migration.selection, "select", lambda repo, *a, **kw:
            (copy.deepcopy(old_selected), self.raw[:225]) if repo == self.donor
            else (copy.deepcopy(self.selected), self.raw))
        monkeypatch.setattr(migration, "_donor_selection", lambda *args: (copy.deepcopy(old_selected), self.raw[:225]))
        current, patches, lite = migration.arp._load(self.target, self.core, self.tooling, "windows")
        old_selected, old_raw = migration._donor_selection(self.donor, self.target, self.selected, self.raw)
        previous_identity = migration._old_identity(current, old_selected)
        self.old_key = migration._key(self.donor, old_selected, old_raw)
        self.key = migration._key(self.target, self.selected, self.raw)
        values = {".chromix-source-unpacked": migration.VERSION, ".chromix-ungoogled-core": migration.CORE,
            ".chromix-ungoogled-windows": migration.WINDOWS, ".chromix-binaries-pruned": migration.CORE,
            ".chromix-domain-substituted": migration.CORE,
            ".chromix-toolchain-ready": migration.CORE + "|" + migration.WINDOWS,
            ".chromix-patches": self.old_key.rsplit("|", 1)[1],
            ".chromix-patch-selection": self.old_key.rsplit("|", 1)[1], migration.READY: self.old_key}
        for n, v in values.items():
            put(self.src, n, v + "\r\n")
        outputs = {n: migration.arp._sha((self.src / n).read_bytes())
                   for n in migration._names(patches[:225], lite)}
        put(self.src, migration.arp.MARKER, migration.arp._json({"schema_version": 1,
            "identity": previous_identity, "identity_sha256": migration.arp._sha(migration.arp._json(previous_identity)),
            "outputs": outputs}))

        put(self.target, "tools/patch_selection.py", (ROOT / "tools/patch_selection.py").read_bytes())
        put(self.src, "out/Default/chrome.dll", b"compiled DLL fixture")
        completion = json.loads((self.src / migration.arp.MARKER).read_bytes())
        prior = {"target_sha": migration.DONOR_SHA, "patch_count": 225,
            "key": self.old_key, "prepareSourceKey": self.old_key, "identity": previous_identity,
            "upstream_receipt_sha256": migration.arp._sha((self.src / migration.upstream.MARKER).read_bytes()),
            "source_proof": {"method": "complete-reverse-forward-in-private-scratch",
                "target_outputs": {n: migration.arp._sha(data.replace(b"\r\n", b"\n"))
                    if data is not None else None for n, data in migration._bytes(
                        self.src, set(completion["outputs"]) | {migration.SOURCE_VERSION}).items()}}}
        payload = migration.arp._json(prior)
        put(self.src, migration.PRIOR_RECEIPT, payload)
        monkeypatch.setattr(migration, "PRIOR_RECEIPT_SHA256", migration.arp._sha(payload))
        monkeypatch.setattr(migration, "DONOR_SOURCE_RECEIPT_SHA256", migration.arp._sha(migration.arp._json(completion)))
        monkeypatch.setattr(migration.selection, "preparation_key", lambda *args: self.key.rsplit("|", 1)[1])

    def run(self):
        return migration.migrate(self.work, self.donor, self.target, migration.DONOR_SHA)


@pytest.mark.parametrize("marker", [None, "x64\r\n"])
def test_full_migration_preserves_ninja_receipt_version_and_advances_markers(tmp_path, monkeypatch, marker):
    fx = Fixture(tmp_path, monkeypatch)
    if marker is not None:
        put(fx.work, ".chromix-target-arch", marker)
    arch_state = migration.safe._capture(fx.work, {".chromix-target-arch"})
    ninja = snapshot(fx.src / "out")
    immutable = {n: snapshot(fx.src)[n] for n in (migration.upstream.MARKER, migration.SOURCE_VERSION)}
    before = (fx.src / "source.cc").stat().st_mtime_ns
    report = fx.run()
    assert report["changed_files"] == sorted(fx.sources)
    assert report["changed_patches"] == list(migration.ADDITIONS)
    assert report["prepareSourceKey"] == report["key"] == fx.key
    assert report["old_patch_count"] == 225 and report["patch_count"] == 230
    assert (fx.src / "source.cc").read_bytes() == b"v226\r\n"
    assert (fx.src / "source.cc").stat().st_mtime_ns >= before + 1_000_000_000
    assert snapshot(fx.src / "out") == ninja
    assert migration.safe._capture(fx.work, {".chromix-target-arch"}) == arch_state
    assert all(snapshot(fx.src)[n] == value for n, value in immutable.items())
    assert (fx.src / migration.READY).read_text().strip() == fx.key
    assert (fx.src / ".chromix-patch-selection").read_text().strip() == fx.key.rsplit("|", 1)[1]
    assert not (fx.work / migration.BLOCKER).exists()
    current, patches, lite = migration.arp._load(fx.target, fx.core, fx.tooling, "windows", src=fx.src)
    assert migration.arp._completed(fx.src, current, migration._names(patches, lite))
    after = snapshot(fx.src)
    with pytest.raises(RuntimeError, match="repeat/prior"):
        fx.run()
    assert snapshot(fx.src) == after


@pytest.mark.parametrize("mutation", ["receipt", "source", "arch", "version", "blocker", "mixed", "gn"])
def test_preflight_fail_closed_without_source_writes(tmp_path, monkeypatch, mutation):
    fx = Fixture(tmp_path, monkeypatch)
    if mutation == "receipt":
        path = fx.src / migration.arp.MARKER
        data = json.loads(path.read_bytes())
        data["identity"]["selection"]["version"] = "154.0.8037.57"
        path.write_bytes(migration.arp._json(data))
    elif mutation == "source":
        put(fx.src, "source.cc", "forged\n")
    elif mutation == "arch":
        put(fx.work, ".chromix-target-arch", "arm64\n")
    elif mutation == "version":
        put(fx.src, migration.SOURCE_VERSION, "MAJOR=153\nMINOR=0\nBUILD=8010\nPATCH=47\n")
    elif mutation == "blocker":
        (fx.work / migration.BLOCKER).mkdir()
    elif mutation == "mixed":
        put(fx.src, "source.cc", "v225\r\nextra\n")
    else:
        put(fx.src, "out/Default/args.gn", 'target_cpu = "x64"\ntarget_cpu = "x64"\n')
    before = snapshot(fx.src)
    with pytest.raises((RuntimeError, ValueError)):
        fx.run()
    assert snapshot(fx.src) == before


def test_scratch_failure_leaves_blocker_and_original_source(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    before = snapshot(fx.src)
    original_apply = migration._apply

    def fail_append(stage, patches, git, reverse=False):
        if patches[0][0] == migration.ADDITIONS[0]:
            raise RuntimeError("injected append failure")
        return original_apply(stage, patches, git, reverse)

    monkeypatch.setattr(migration, "_apply", fail_append)
    with pytest.raises(RuntimeError, match="injected append"):
        fx.run()
    assert snapshot(fx.src) == before
    assert (fx.work / migration.BLOCKER / "transaction.json").is_file()
    with pytest.raises(RuntimeError, match="unfinished transaction"):
        fx.run()


def test_publish_failure_cannot_mark_target_ready(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    write = migration.safe._write

    def fail_source(path, data, **kwargs):
        if path == fx.src / "source.cc":
            raise OSError("injected publication failure")
        return write(path, data, **kwargs)

    monkeypatch.setattr(migration.safe, "_write", fail_source)
    with pytest.raises(OSError, match="publication failure"):
        fx.run()
    assert (fx.work / migration.BLOCKER).is_dir()
    assert (fx.src / migration.READY).read_text().strip() == fx.old_key
    assert not (fx.src / migration.RECEIPT).exists()


@pytest.mark.parametrize("name", ["out/Default/cache.obj", "chrome/VERSION", ".ninja_log"])
def test_patch_targets_cannot_touch_immutable_or_cache_paths(name):
    with pytest.raises(RuntimeError, match="VERSION/Ninja"):
        migration._names([("bad.patch", b"", [(name, "modify", None)])], {})


def test_wrong_sha_rejected_before_access():
    with pytest.raises(RuntimeError, match="fixed stage8 compiled donor"):
        migration.migrate("missing", "missing", "missing", "0" * 40)


def test_source_race_before_publication_preserves_blocker(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    original = migration._roundtrip

    def race(stage, patches, git_program, names):
        result = original(stage, patches, git_program, names)
        if len(patches) == 230:
            put(fx.src, "source.cc", "concurrent edit\n")
        return result

    monkeypatch.setattr(migration, "_roundtrip", race)
    with pytest.raises(RuntimeError, match="changed concurrently"):
        fx.run()
    assert (fx.src / "source.cc").read_text() == "concurrent edit\n"
    assert (fx.src / migration.READY).read_text().strip() == fx.old_key
    assert not (fx.src / "new-header.h").exists()
    assert (fx.work / migration.BLOCKER).is_dir()


@pytest.mark.parametrize("mutation", ["missing", "bytes", "wrong-sha", "wrong-key", "source-manifest", "source-receipt"])
def test_prior_stage7_migration_is_required_and_immutable(tmp_path, monkeypatch, mutation):
    fx = Fixture(tmp_path, monkeypatch)
    prior = fx.src / migration.PRIOR_RECEIPT
    if mutation == "missing":
        prior.unlink()
    elif mutation == "bytes":
        prior.write_bytes(prior.read_bytes() + b"\n")
    elif mutation == "source-receipt":
        receipt = fx.src / migration.arp.MARKER
        value = json.loads(receipt.read_bytes())
        value["unreviewed"] = True
        receipt.write_bytes(migration.arp._json(value))
    else:
        value = json.loads(prior.read_bytes())
        value[{"wrong-sha": "target_sha", "wrong-key": "key", "source-manifest": "source_proof"}[mutation]] = "forged"
        prior.write_bytes(migration.arp._json(value))
        monkeypatch.setattr(migration, "PRIOR_RECEIPT_SHA256", migration.arp._sha(prior.read_bytes()))
    before = snapshot(fx.work)
    with pytest.raises((RuntimeError, ValueError)):
        fx.run()
    assert snapshot(fx.work) == before
    assert not (fx.work / migration.BLOCKER).exists()


def test_prior_receipt_is_preserved_and_chained_in_new_receipt(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    before = snapshot(fx.src)[migration.PRIOR_RECEIPT]
    result = fx.run()
    assert snapshot(fx.src)[migration.PRIOR_RECEIPT] == before
    assert result["prior_migration_receipt_sha256"] == migration.PRIOR_RECEIPT_SHA256
    assert migration.RECEIPT != migration.PRIOR_RECEIPT


def test_full_stack_donor_failure_is_not_replaced_with_append_only_check(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    original = migration._apply
    seen = []

    def fail(stage, patches, program, reverse=False):
        seen.append((len(patches), reverse))
        if len(patches) == 225 and reverse:
            raise RuntimeError("donor reverse failure")
        return original(stage, patches, program, reverse)

    monkeypatch.setattr(migration, "_apply", fail)
    before = snapshot(fx.src)
    with pytest.raises(RuntimeError, match="donor reverse"):
        fx.run()
    assert seen == [(225, True)]
    assert snapshot(fx.src) == before
    assert (fx.work / migration.BLOCKER).is_dir()


def donor_archive(root):
    root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(git("-C", ROOT, "archive", migration.DONOR_SHA))) as archive:
        archive.extractall(root, filter="data")
    return root


def test_real_225_prefix_reconstruction_does_not_execute_donor_selector(tmp_path):
    donor = donor_archive(tmp_path / "donor")
    selected, raw = migration.selection.select(ROOT, "windows")
    old, prefix = migration._donor_selection(donor, ROOT, selected, raw)
    assert len(prefix) == 225 and len(raw) == 230
    assert migration.arp._sha(migration.arp._json(old)) == migration.OLD_IDENTITY
    assert raw[225:] == [(name, (ROOT / name).read_bytes()) for name in migration.ADDITIONS]
    assert old["patches"] == selected["patches"][:225]
    assert old["patches"][-1]["path"] == "patches/chromium155-windows/0227-opaque-png-export-consistency.patch"
    put(donor, prefix[0][0], b"forged")
    with pytest.raises(RuntimeError, match="donor patch bytes changed"):
        migration._donor_selection(donor, ROOT, selected, raw)


@pytest.mark.parametrize("relative", ["out/Default/obj/cache.obj", "chrome/build/pgo_profiles/native.profdata"])
def test_object_and_pgo_races_refuse_publication(tmp_path, monkeypatch, relative):
    fx = Fixture(tmp_path, monkeypatch)
    put(fx.src, relative, b"original")
    roundtrip = migration._roundtrip

    def race(*args):
        result = roundtrip(*args)
        put(fx.src, relative, b"concurrent write")
        return result

    monkeypatch.setattr(migration, "_roundtrip", race)
    with pytest.raises(RuntimeError, match="cached outputs or PGO"):
        fx.run()
    assert (fx.src / "source.cc").read_bytes() == b"v225\r\n"
    assert (fx.src / migration.READY).read_text().strip() == fx.old_key
    assert (fx.work / migration.BLOCKER).is_dir()


def test_expanded_target_inventory_retains_exact_donor_completion(tmp_path, monkeypatch):
    import validate_windows155_stage8_checkpoint as validator
    fx = Fixture(tmp_path, monkeypatch)
    old_completion = (fx.src / migration.arp.MARKER).read_bytes()
    result = fx.run()
    assert migration.arp._json(result["previous_completion"]) == old_completion
    assert set(result["previous_completion"]["outputs"]) == {"source.cc", "lite.txt"}
    assert all(name in result["source_proof"]["donor_outputs"] for name in fx.sources)
    receipt = tmp_path / "migration.json"
    receipt.write_bytes((fx.src / migration.RECEIPT).read_bytes())
    assert validator.verify_prepared(fx.work.parent, fx.donor, fx.target,
                                     migration.TARGET_BASE_SHA, receipt)["status"] == "verified"
    assert all((fx.src / name).read_bytes() == b"timezone\r\n" for name in fx.sources[1:])


@pytest.mark.parametrize("mutation", [None, "selector", "gn", "root-patch", "extra"])
def test_repository_gate_allows_only_exact_selector_digest_replacement(tmp_path, monkeypatch, mutation):
    donor, target = tmp_path / "donor", tmp_path / "target"
    for root, revision in ((donor, migration.DONOR_SHA), (target, "HEAD")):
        git("clone", "--quiet", "--no-hardlinks", "--no-checkout", ROOT, root)
        git("-C", root, "checkout", "--quiet", "--detach", revision)
    for name in (*migration.ADDITIONS, migration.MANIFEST, "tools/patch_selection.py"):
        put(target, name, (ROOT / name).read_bytes())
    if mutation == "selector":
        path = target / "tools/patch_selection.py"
        path.write_bytes(path.read_bytes() + b"\n# unrelated change\n")
    elif mutation in ("gn", "root-patch"):
        name = "build/args.windows.gn" if mutation == "gn" else "patches/series"
        path = target / name
        path.write_bytes(path.read_bytes() + b"\n# unreviewed change\n")
    git("-C", target, "add", ".")
    git("-C", target, "-c", "user.name=Recovery test", "-c", "user.email=recovery@example.invalid",
        "commit", "--quiet", "--allow-empty", "-m", "Integrated five-patch recovery inputs")
    monkeypatch.setattr(migration.safe, "WINDOWS", True)
    if mutation == "extra":
        put(target, "patches/untracked.patch", b"unreviewed")
    if mutation:
        with pytest.raises(RuntimeError, match="selector changed|pinned target ref|extra/missing"):
            migration._repository_inputs(shutil.which("git"), donor, target)
    else:
        old, new = migration._repository_inputs(shutil.which("git"), donor, target)
        assert old[0] == migration.DONOR_SHA
        assert new[0] == git("-C", target, "rev-parse", "HEAD").decode().strip()
        selected, raw = migration.selection.select(target, "windows")
        assert len(migration._donor_selection(donor, target, selected, raw)[1]) == 225

@pytest.mark.parametrize("index", range(5))
def test_each_append_failure_keeps_entire_live_batch_unpublished(tmp_path, monkeypatch, index):
    fx = Fixture(tmp_path, monkeypatch)
    apply = migration._apply

    def fail(stage, patches, program, reverse=False):
        if [name for name, _, _ in patches] == list(migration.ADDITIONS) and not reverse:
            apply(stage, patches[:index], program)
            raise RuntimeError("injected batch append failure")
        return apply(stage, patches, program, reverse)

    monkeypatch.setattr(migration, "_apply", fail)
    before = snapshot(fx.src)
    with pytest.raises(RuntimeError, match="batch append failure"):
        fx.run()
    assert snapshot(fx.src) == before
    assert (fx.work / migration.BLOCKER).is_dir()


@pytest.mark.parametrize("index", range(5))
def test_partial_publication_never_commits_batch_receipts_or_readiness(tmp_path, monkeypatch, index):
    fx = Fixture(tmp_path, monkeypatch)
    write = migration.safe._write
    before = snapshot(fx.src)

    def fail(path, data, **kwargs):
        if path == fx.src / sorted(fx.sources)[index]:
            raise OSError("injected batch publication failure")
        return write(path, data, **kwargs)

    monkeypatch.setattr(migration.safe, "_write", fail)
    with pytest.raises(OSError, match="batch publication"):
        fx.run()
    after = snapshot(fx.src)
    for name in (migration.READY, ".chromix-patches", ".chromix-patch-selection",
                 migration.arp.MARKER, migration.PRIOR_RECEIPT):
        assert after[name] == before[name]
    assert not (fx.src / migration.RECEIPT).exists()
    assert (fx.work / migration.BLOCKER).is_dir()
    with pytest.raises(RuntimeError, match="unfinished transaction"):
        fx.run()


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "order", "prefix-order", "prefix-identity"])
def test_integrated_selection_rejects_incomplete_or_reordered_batch(tmp_path, mutation):
    donor = donor_archive(tmp_path / "donor")
    selected, raw = migration.selection.select(ROOT, "windows")
    if mutation == "missing":
        raw.pop()
    elif mutation == "duplicate":
        raw[-1] = raw[-2]
    elif mutation == "order":
        raw[-1], raw[-2] = raw[-2], raw[-1]
    elif mutation == "prefix-order":
        raw[0], raw[1] = raw[1], raw[0]
        selected["patches"][0], selected["patches"][1] = selected["patches"][1], selected["patches"][0]
    else:
        selected["patches"][0]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="230 target patches|historical 225-patch"):
        migration._donor_selection(donor, ROOT, selected, raw)


@pytest.mark.parametrize("index", range(5))
def test_integrated_patch_bytes_cannot_change_even_with_preserved_selection(tmp_path, index):
    donor = donor_archive(tmp_path / "donor")
    target = tmp_path / "target"
    for name in (*migration.ADDITIONS, migration.MANIFEST, "patches/series"):
        put(target, name, (ROOT / name).read_bytes())
    selected, raw = migration.selection.select(ROOT, "windows")
    name = migration.ADDITIONS[index]
    put(target, name, (target / name).read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match="timezone patch hash mismatch"):
        migration._donor_selection(donor, target, selected, raw)


def test_real_five_patch_batch_roundtrips_exact_155_source(tmp_path):
    import gzip
    evidence = json.loads(gzip.decompress((Path(__file__).with_name("fixtures") /
        "windows155_timezone_sources.json.gz").read_bytes()))
    stage = tmp_path / "src"
    for name, entry in evidence["sources"].items():
        raw = entry["text"].encode()
        assert migration.arp._sha(raw) == entry["sha256"]
        put(stage, name, raw)
    program = shutil.which("git")
    for number in (5, 7, 19):
        path = next((ROOT / "patches").glob(f"{number:04d}-*.patch"))
        data, entries = migration.arp.transform_patch(path.read_bytes(), set(), [])
        migration._apply(stage, [(path.name, data, entries)], program)
    before = migration._bytes(stage, migration.SOURCES)
    patches = []
    for name, digest, source in migration.PATCH_CONTRACT:
        raw = (ROOT / name).read_bytes()
        assert migration.arp._sha(raw) == digest
        data, entries = migration.arp.transform_patch(raw, set(), [])
        assert entries == [(source, "modify", None)]
        patches.append((name, data, entries))
    migration._apply(stage, patches, program)
    assert all(data != before[name] for name, data in migration._bytes(stage, migration.SOURCES).items())
    migration._roundtrip(stage, patches, program, set(migration.SOURCES))
    migration._apply(stage, patches, program, reverse=True)
    assert migration._bytes(stage, migration.SOURCES) == before

@pytest.mark.parametrize("field", ["series", "last-patch", "manifest", "additions"])
def test_integrated_target_identity_cannot_drift_from_batch(tmp_path, field):
    donor = donor_archive(tmp_path / "donor")
    selected, raw = migration.selection.select(ROOT, "windows")
    if field == "series":
        selected["series_sha256"] = "0" * 64
    elif field == "last-patch":
        selected["patches"][-1]["sha256"] = "0" * 64
    elif field == "manifest":
        selected["selection"]["additions"]["manifest"]["sha256"] = "0" * 64
    else:
        selected["selection"]["additions"]["patches"].pop()
    with pytest.raises(RuntimeError, match="exact five-patch append identity"):
        migration._donor_selection(donor, ROOT, selected, raw)
