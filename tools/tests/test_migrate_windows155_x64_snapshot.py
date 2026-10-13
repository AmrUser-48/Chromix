"""Pinned selection and real Git scratch/transaction tests; no donor code executes."""
from __future__ import annotations

import copy
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import migrate_windows155_x64_snapshot as migration

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_TARGET = "b8821cd2f782ca4da9b998b437fc3acb09cb3fea"
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


@pytest.fixture(scope="module")
def historical_selector():
    raw = git("-C", ROOT, "show", HISTORICAL_TARGET + ":tools/patch_selection.py")
    assert migration.arp._sha(raw) == migration.NEW_SELECTOR
    selector = types.ModuleType("historical_windows155_selection")
    exec(compile(raw, HISTORICAL_TARGET + ":tools/patch_selection.py", "exec"), selector.__dict__)
    return selector


@pytest.fixture(autouse=True)
def use_historical_selector(monkeypatch, historical_selector):
    # Stage7 uses the authenticated 225-patch target, not the current stage8 selector.
    monkeypatch.setattr(migration, "selection", historical_selector)
    monkeypatch.setattr(migration.arp, "patch_selection", historical_selector)


def repository_archive(root, revision):
    root.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(git("-C", ROOT, "archive", revision))) as archive:
        archive.extractall(root, filter="data")
    return root


def historical_selection(tmp_path):
    donor = repository_archive(tmp_path / "donor", migration.DONOR_SHA)
    target = repository_archive(tmp_path / "target", HISTORICAL_TARGET)
    assert migration.arp._sha((donor / "tools/patch_selection.py").read_bytes()) == migration.OLD_SELECTOR
    return donor, target


def test_real_pinned_224_prefix_and_single_png_append(tmp_path):
    donor, target = historical_selection(tmp_path)
    selected, raw = migration.selection.select(target, "windows")
    old, prefix = migration._donor_selection(donor, target, selected, raw)
    assert len(prefix) == len(old["patches"]) == 224
    assert selected["patches"][:224] == old["patches"]
    assert migration.arp._sha(migration.arp._json(old)) == migration.OLD_IDENTITY
    assert migration.selection.preparation_key(donor, "windows", "windows") == (
        "225dd1606daeaecb234598db5e5dc4c20b4665823d4cc3cf0105cba6436716bd")
    assert migration._key(target, selected, raw).rsplit("|", 1)[1] == (
        migration.selection.preparation_key(target, "windows", "windows"))
    assert len(raw) == len(selected["patches"]) == 225
    assert raw[-1][0] == migration.PATCH
    assert migration.arp._sha(migration.arp._json(selected)) == (
        "0474128cc29677a220c1138f6ea45e77b798a81e657a14c49fcdb242fe014725")
    assert migration.selection.preparation_key(target, "windows", "windows") == (
        "8bb13f960d706837d90bbedd76cc4144430ef8c518ba7fdf34694792b2881667")
    assert migration.arp._sha((target / "tools/patch_selection.py").read_bytes()) == migration.NEW_SELECTOR
    assert migration.arp._sha((target / migration.PATCH).read_bytes()) == migration.PATCH_SHA256
    for platform in ("linux", "macos"):
        assert migration.selection.select(target, platform) == migration.selection.select(donor, platform)
        assert len(migration.selection.select(target, platform)[1]) == 224
    put(donor, prefix[0][0], prefix[0][1] + b"tampered\n")
    with pytest.raises(RuntimeError, match="historical 224-patch"):
        migration._donor_selection(donor, target, selected, raw)


class Fixture:
    """Synthetic transaction fixture: real Git patching, not a native build attestation."""
    def __init__(self, tmp_path, monkeypatch):
        self.work, self.donor, self.target = (tmp_path / n for n in ("work", "donor", "target"))
        self.src = self.work / "src"
        self.core = self.work / "tooling/ungoogled-chromium"
        self.tooling = self.work / "tooling/ungoogled-chromium-windows"
        self.old_names = [f"patches/{n:04d}-fixture.patch" for n in range(1, 225)]
        self.names = self.old_names + list(migration.ADDITIONS)
        self.raw = []
        previous = "v0"
        for number, name in enumerate(self.names, 1):
            data = patch("source.cc", previous, f"v{number}")
            previous = f"v{number}"
            self.raw.append((name, data))
            put(self.target, name, data)
            if number <= 224:
                put(self.donor, name, data)
        for repo, names in ((self.donor, self.old_names), (self.target, self.names)):
            put(repo, "patches/series", "\n".join(names) + "\n")
            (repo / "patches/chromium155").mkdir()
            put(repo, migration.arp.LITE + "/lite.txt", "lite\n")
        put(self.core, "chromium_version.txt", migration.VERSION + "\n")
        put(self.core, "domain_regex.list", b"")
        put(self.tooling, "domain_substitution.list", b"")
        put(self.src, "source.cc", "v224\r\n")
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
        old_selected["patches"] = old_selected["patches"][:224]
        old_selected["series_sha256"] = migration.arp._sha((self.donor / "patches/series").read_bytes())
        self.selected["selection"]["additions"] = {
            "schema_version": 1, "kind": "windows155-compiled-png-recovery",
            "manifest": {"path": migration.MANIFEST, "sha256": migration.selection.WINDOWS155_ADDITIONS_SHA256},
            "patches": [{"path": migration.PATCH, "sha256": migration.arp._sha(self.raw[-1][1])}],
        }
        put(self.donor, "patches/series", (self.target / "patches/series").read_bytes())
        monkeypatch.setattr(migration, "OLD_IDENTITY", migration.arp._sha(migration.arp._json(old_selected)))
        monkeypatch.setattr(migration, "PATCH_SHA256", migration.arp._sha(self.raw[-1][1]))
        monkeypatch.setattr(migration, "SOURCE", "source.cc")
        monkeypatch.setattr(migration.selection, "select", lambda repo, *a, **kw:
            (copy.deepcopy(old_selected), self.raw[:224]) if repo == self.donor
            else (copy.deepcopy(self.selected), self.raw))
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
                   for n in migration._names(patches[:224], lite)}
        put(self.src, migration.arp.MARKER, migration.arp._json({"schema_version": 1,
            "identity": previous_identity, "identity_sha256": migration.arp._sha(migration.arp._json(previous_identity)),
            "outputs": outputs}))

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
    assert report["changed_files"] == ["source.cc"]
    assert report["prepareSourceKey"] == report["key"] == fx.key
    assert report["old_patch_count"] == 224 and report["patch_count"] == 225
    assert (fx.src / "source.cc").read_bytes() == b"v225\r\n"
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
        put(fx.src, "source.cc", "v224\r\nextra\n")
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
    with pytest.raises(RuntimeError, match="fixed stage7 compiled donor"):
        migration.migrate("missing", "missing", "missing", "0" * 40)


def test_forged_output_receipt_still_fails_structural_proof(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    put(fx.src, "source.cc", "not donor source\n")
    path = fx.src / migration.arp.MARKER
    saved = json.loads(path.read_bytes())
    saved["outputs"]["source.cc"] = migration.arp._sha((fx.src / "source.cc").read_bytes())
    path.write_bytes(migration.arp._json(saved))
    before = snapshot(fx.src)
    with pytest.raises(RuntimeError, match="scratch patch failed"):
        fx.run()
    assert snapshot(fx.src) == before
    assert (fx.work / migration.BLOCKER).is_dir()


def test_source_race_before_publication_preserves_blocker(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    original = migration._roundtrip

    def race(stage, patches, git_program, names):
        result = original(stage, patches, git_program, names)
        if len(patches) == 225:
            put(fx.src, "source.cc", "concurrent edit\n")
        return result

    monkeypatch.setattr(migration, "_roundtrip", race)
    with pytest.raises(RuntimeError, match="changed concurrently"):
        fx.run()
    assert (fx.src / "source.cc").read_text() == "concurrent edit\n"
    assert (fx.src / migration.READY).read_text().strip() == fx.old_key
    assert not (fx.src / "new-header.h").exists()
    assert (fx.work / migration.BLOCKER).is_dir()


def test_receipt_relocation_is_read_only(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    path = fx.src / migration.arp.MARKER
    saved = json.loads(path.read_bytes())
    expected = copy.deepcopy(saved["identity"])
    for field, suffix in (("regex", "ungoogled-chromium/domain_regex.list"),
                          ("list", "ungoogled-chromium-windows/domain_substitution.list")):
        saved["identity"][field]["path"] = "D:\\old\\work\\tooling\\" + suffix.replace("/", "\\")
    saved["identity_sha256"] = migration.arp._sha(migration.arp._json(saved["identity"]))
    path.write_bytes(migration.arp._json(saved))
    before = snapshot(fx.src)
    assert migration._completed(fx.src, expected, set(saved["outputs"])) == saved
    assert snapshot(fx.src) == before


@pytest.mark.parametrize("mutation", ["manifest", "missing-manifest", "patch", "missing-patch", "extra", "symlink"])
def test_windows_additions_fail_closed_and_do_not_affect_other_platforms(tmp_path, mutation):
    repo = repository_archive(tmp_path / "target", HISTORICAL_TARGET)
    others = {p: migration.selection.select(repo, p) for p in ("linux", "macos")}
    manifest, patch_path = repo / migration.MANIFEST, repo / migration.PATCH
    if mutation == "manifest":
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    elif mutation == "missing-manifest":
        manifest.unlink()
    elif mutation == "patch":
        patch_path.write_bytes(patch_path.read_bytes() + b"\n")
    elif mutation == "missing-patch":
        patch_path.unlink()
    elif mutation == "extra":
        put(repo, "patches/chromium155-windows/unexpected.patch", "extra")
    else:
        patch_path.unlink()
        patch_path.symlink_to(ROOT / migration.PATCH)
    with pytest.raises(ValueError):
        migration.selection.select(repo, "windows")
    assert {p: migration.selection.select(repo, p) for p in others} == others


def test_real_png_addition_git_roundtrip_on_pinned_source(tmp_path):
    compressed = (Path(__file__).with_name("fixtures") /
                  "linux155_opaque_png_sources.json.gz").read_bytes()
    assert migration.arp._sha(compressed) == "5febb57490ec56c67070bc6aff4daf90a41f94073e09e910233ee719c57efbef"
    data = json.loads(gzip.decompress(compressed))
    BEFORE = "b544a82ed5ab6f125d68604312f214135200e0ce89c0c46f61dee9cc9d6a199d"
    AFTER = "7d78a117324c383003bcda13cd61161cc336d8f498e246f621019ed2f7406945"
    assert migration.arp._sha(data["before"]["text"].encode()) == BEFORE
    stage = tmp_path / "src"
    put(stage, migration.SOURCE, data["before"]["text"])
    raw = git("-C", ROOT, "show", HISTORICAL_TARGET + ":" + migration.PATCH)
    transformed, entries = migration.arp.transform_patch(raw, set(), [])
    patches = [(migration.PATCH, transformed, entries)]
    migration._apply(stage, patches, shutil.which("git"))
    assert migration.arp._sha((stage / migration.SOURCE).read_bytes()) == AFTER
    proof = migration._roundtrip(stage, patches, shutil.which("git"), {migration.SOURCE})
    assert proof == {migration.SOURCE: AFTER}
    migration._apply(stage, patches, shutil.which("git"), reverse=True)
    assert migration.arp._sha((stage / migration.SOURCE).read_bytes()) == BEFORE


@pytest.mark.parametrize("mutation", ["pgo0", "original-pgo", "receipt-hash", "cold", "no-exe"])
def test_compiled_native_pgo2_profile_is_required(tmp_path, monkeypatch, mutation):
    fx = Fixture(tmp_path, monkeypatch)
    if mutation == "pgo0":
        put(fx.src, "out/Default/args.gn", 'target_cpu = "x64"\ntarget_os = "win"\nchrome_pgo_phase = 0\n')
    elif mutation == "original-pgo":
        monkeypatch.setattr(migration.upstream, "verify_restored", lambda *a, **kw:
            {"original_args": {"sha256": migration.ORIGINAL_ARGS_SHA256,
                               "assignments": {"chrome_pgo_phase": "0"}}})
    elif mutation == "receipt-hash":
        monkeypatch.setattr(migration, "ORIGINAL_ARGS_SHA256", "0" * 64)
        # The fixture verifier follows the constant; replace it with an independently wrong receipt.
        monkeypatch.setattr(migration.upstream, "verify_restored", lambda *a, **kw:
            {"original_args": {"sha256": "1" * 64, "assignments": {"chrome_pgo_phase": "2"}}})
    elif mutation == "cold":
        (fx.src / "out/Chromix").mkdir()
    else:
        (fx.src / "out/Default/chrome.exe").unlink()
    before = snapshot(fx.src)
    with pytest.raises((RuntimeError, ValueError)):
        fx.run()
    assert snapshot(fx.src) == before


def native_gn_fixture(tmp_path, monkeypatch):
    fx = Fixture(tmp_path, monkeypatch)
    library = "third_party/llvm-build/Release+Asserts/bin/libclang.dll"
    original = ('target_cpu = "x64"\ntarget_os = "win"\nchrome_pgo_phase = 2\n'
                'thin_lto_enable_optimizations = true\n'
                'v8_metagen_libclang_so = "C:/ungoogled-chromium-windows/build/src/' + library + '"\n'
                'donor_only = ["preserved", 23]\nmerge_precedence = 1\nmerge_precedence = 2 # last wins\n')
    receipt = {"original_args": {"text": original, "sha256": migration.ORIGINAL_ARGS_SHA256,
        "assignments": migration.upstream.parse_gn_assignments(original)}}
    monkeypatch.setattr(migration.upstream, "verify_restored", lambda *a, **kw: receipt)
    put(fx.core, "flags.gn", "merge_precedence = 3\ncore_only = true\nchrome_pgo_phase = 0\n")
    put(fx.tooling, "flags.windows.gn", "merge_precedence = 4\nwindows_only = 23\n")
    put(fx.target, "build/args.windows.gn", (ROOT / "build/args.windows.gn").read_bytes())
    put(fx.src, library, b"source libclang fixture")
    out = fx.src / "out/Default"
    args = put(fx.src, "out/Default/args.gn", original)
    subprocess.run([sys.executable, str(ROOT / "tools/merge_gn_args.py"), str(args),
        "--preserve-pgo-from", str(args), str(args), str(fx.core / "flags.gn"),
        str(fx.tooling / "flags.windows.gn"), str(fx.target / "build/args.windows.gn")],
        check=True, capture_output=True)
    assert migration.metagen.configure(fx.src, out, migration.VERSION)["status"] == "rebased"
    return fx, receipt, library


def test_native_gn_replays_pinned_production_merge_and_metagen_read_only(tmp_path, monkeypatch):
    fx, receipt, library = native_gn_fixture(tmp_path, monkeypatch)
    args = migration.upstream.parse_gn_assignments((fx.src / "out/Default/args.gn").read_text())
    assert args["chrome_pgo_phase"] == "2"
    assert args["merge_precedence"] == "4"
    assert args["donor_only"] == '["preserved",23]'
    assert args["v8_metagen_libclang_so"] == json.dumps((fx.src / library).as_posix())
    before = snapshot(fx.work)
    saved = copy.deepcopy(receipt)
    migration._native_args(fx.src, fx.target, fx.core, fx.tooling, receipt)
    assert snapshot(fx.work) == before
    assert receipt == saved
    report = fx.run()
    assert report["patch_count"] == 225
    assert snapshot(fx.src / "out") == {
        name[len("src/out/"):]: value for name, value in before.items() if name.startswith("src/out/")}


@pytest.mark.parametrize("mutation", ["old-path", "other-path", "bindings-path", "unknown",
    "missing", "precedence", "fast", "pgo", "multiple", "missing-dll"])
def test_native_gn_configure_does_not_allow_arbitrary_parameter_drift(tmp_path, monkeypatch, mutation):
    fx, receipt, library = native_gn_fixture(tmp_path, monkeypatch)
    path = fx.src / "out/Default/args.gn"
    expected = migration.upstream.parse_gn_assignments(path.read_text())
    actual = dict(expected)
    if mutation == "old-path":
        actual["v8_metagen_libclang_so"] = receipt["original_args"]["assignments"]["v8_metagen_libclang_so"]
    elif mutation == "other-path":
        actual["v8_metagen_libclang_so"] = '"C:/unreviewed/libclang.dll"'
    elif mutation == "bindings-path":
        actual["v8_metagen_libclang_bindings_dir"] = '"C:/unreviewed/python"'
    elif mutation == "unknown":
        actual["unreviewed_argument"] = "true"
    elif mutation == "missing":
        del actual["donor_only"]
    elif mutation == "precedence":
        actual["merge_precedence"] = "3"
    elif mutation == "fast":
        actual["thin_lto_enable_optimizations"] = "false"
    elif mutation == "pgo":
        actual["chrome_pgo_phase"] = "0"
    elif mutation == "multiple":
        del actual["donor_only"]
        actual["unreviewed_argument"] = "true"
        actual["v8_metagen_libclang_so"] = '"C:/unreviewed/libclang.dll"'
    else:
        (fx.src / library).unlink()
    path.write_text("\n".join(f"{key} = {value}" for key, value in actual.items()) + "\n")
    before = snapshot(fx.work)
    with pytest.raises((RuntimeError, ValueError)) as error:
        fx.run()
    if mutation == "missing-dll":
        assert "source libclang DLL is missing" in str(error.value)
    else:
        prefix = "GN args differ from pinned native x64 inputs: "
        assert str(error.value).startswith(prefix)
        assert json.loads(str(error.value)[len(prefix):]) == {
            "missing": {key: expected[key] for key in expected.keys() - actual.keys()},
            "unexpected": {key: actual[key] for key in actual.keys() - expected.keys()},
            "changed": {key: {"expected": expected[key], "actual": actual[key]}
                        for key in expected.keys() & actual.keys() if expected[key] != actual[key]},
        }
    assert snapshot(fx.work) == before
    assert not (fx.work / migration.BLOCKER).exists()


def test_native_gn_production_helpers_and_order_are_bound_to_exact_donor():
    for name in ("tools/merge_gn_args.py", "tools/install_windows_metagen_bindings.py",
                 "tools/prepare_restored_build.py", "build/windows/prepare-ungoogled.ps1",
                 "build/args.windows.gn"):
        assert migration._protected(name)
        assert (ROOT / name).read_text(encoding="utf-8") == git(
            "-C", ROOT, "show", migration.DONOR_SHA + ":" + name).decode("utf-8")
    stage = git("-C", ROOT, "show", migration.DONOR_SHA + ":build/windows/ci-stage.ps1").decode()
    assert 'else { "native" }' in stage
    assert 'if ($BuildProfile -in @("fast", "release")) { $mergeArgs += @("--build-profile", $BuildProfile) }' in stage
    steps = [stage.index(text) for text in (
        '& "$PSScriptRoot\\prepare-ungoogled.ps1" -Root $WorkDir',
        '$mergeArgs += @("--preserve-pgo-from", $gnArgs)',
        'if ($RestoredUpstream) { $mergeArgs += $gnArgs }',
        '(Join-Path $UngoogledTooling "flags.gn")',
        '(Join-Path $WindowsTooling "flags.windows.gn")',
        '(Join-Path $Repo "build\\args.windows.gn")',
        'python @mergeArgs',
        'python (Join-Path $Repo "tools\\prepare_restored_build.py") --phase finish',
        '& python -X utf8 $metagenBindings --configure --src $Src --out $OutDir',
        '& $gn gen $OutDir --fail-on-unused-args')]
    assert steps == sorted(steps)


@pytest.mark.parametrize("unexpected", [None, "build/macos/package-macos.sh",
    "build/patch-additions-linux155.json", "patches/chromium155-linux/unreviewed.patch"])
def test_repository_gate_authenticates_real_donor_without_committing(tmp_path, monkeypatch, unexpected):
    donor, target = tmp_path / "donor", tmp_path / "target"
    for root, revision in ((donor, migration.DONOR_SHA), (target, HISTORICAL_TARGET)):
        git("clone", "--quiet", "--no-hardlinks", "--no-checkout", ROOT, root)
        git("-C", root, "checkout", "--quiet", "--detach", revision)
    real_tree = migration.safe._tree
    head, tree = real_tree(shutil.which("git"), target)
    monkeypatch.setattr(migration.safe, "WINDOWS", True)
    old, new = migration._repository_inputs(shutil.which("git"), donor, target)
    assert old[0] == migration.DONOR_SHA and new[0] == head == HISTORICAL_TARGET
    assert migration.TARGET_BASE_SHA == migration.DONOR_SHA
    if unexpected:
        # Model an unapproved committed input while retaining the real donor proof.
        monkeypatch.setattr(migration.safe, "_tree", lambda program, root, expected=None:
            (head, tree) if root == target else real_tree(program, root, expected))
        data = b"unapproved preparation input\n"
        put(target, unexpected, data)
        tree[unexpected] = ("100644", "blob", hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest())
        with pytest.raises(RuntimeError, match="pinned target ref|fixed profile"):
            migration._repository_inputs(shutil.which("git"), donor, target)
        return
    put(target, "patches/untracked.patch", "unapproved\n")
    with pytest.raises(RuntimeError, match="extra/missing preparation"):
        migration._repository_inputs(shutil.which("git"), donor, target)


def test_exact_donor_marker_and_snapshot_packaging_contract():
    def donor_file(name):
        return git("-C", ROOT, "show", migration.DONOR_SHA + ":" + name).decode()

    stage = donor_file("build/windows/ci-stage.ps1")
    guard = donor_file("build/windows/assert-target-arch.ps1")
    parts = donor_file("build/windows/ci-parts.ps1")
    prepare = donor_file("build/windows/prepare-ungoogled.ps1")
    assert '$InitializeTarget = $Arch -eq "arm64" -and' in stage
    assert '-RequireMarker:(($FromArtifact -or $FromSynced) -and $Arch -eq "arm64")' in stage
    assert 'if ($Arch -eq "arm64") {\n      & "$PSScriptRoot\\assert-target-arch.ps1" -WorkDir $WorkDir -Arch $Arch -Initialize' in stage
    for script, root in ((stage, "WorkDir"), (parts, "workDir"), (prepare, "Root")):
        assert f'if ($Arch -eq "arm64" -or (Test-Path (Join-Path ${root} ".chromix-target-arch"))) {{' in script
    assert '-WorkDir $workDir -Arch $Arch -RequireMarker' in parts
    assert 'if ($Initialize -and -not (Test-Path -LiteralPath $marker)) {' in guard
    assert 'if ($saved -cne $Arch) { throw' in guard
    assert '$receipt.identity.platform -cne "windows" -or $receipt.identity.arch -cne $Arch' in guard
    assert '& $SevenZip a -v9g -mx=1 -mtc=on "$PartsDir\\stage\\tree.7z" chromix' in parts
    restored = prepare.split('if (-not (Test-Path $readyMarker)) {', 1)[1].split('return', 1)[0]
    for name in (".chromix-source-unpacked", ".chromix-ungoogled-core", ".chromix-ungoogled-windows",
                 ".chromix-binaries-pruned", ".chromix-patches", ".chromix-patch-selection",
                 ".chromix-domain-substituted", ".chromix-toolchain-ready", migration.READY):
        assert f'Set-Marker "{name}"' in restored
    assert 'Join-Path $fingerprintDiagnostics ("source-" + [Guid]::NewGuid().ToString(\'N\') + ".json")' in stage
    assert "Join-Path $RecoveryDiagnostics 'source-verification.json'" in stage
    assert 'if ($Arch -eq "arm64") {\n    if (-not (Test-Path -LiteralPath $FingerprintSourceReport -PathType Leaf))' in stage
    assert 'Copy-Item -LiteralPath $FingerprintSourceReport -Destination "$Root\\dist\\source-verification.json"' in stage
    assert "source-verification.json" not in parts


@pytest.mark.parametrize("marker", ["", "ARM64", "arm64", "x86", "x64\narm64", b"\xff"])
def test_present_conflicting_arch_marker_never_uses_legacy_fallback(tmp_path, monkeypatch, marker):
    fx = Fixture(tmp_path, monkeypatch)
    put(fx.work, ".chromix-target-arch", marker)
    before = snapshot(fx.work)
    with pytest.raises((ValueError, RuntimeError)):
        fx.run()
    assert snapshot(fx.work) == before
    assert not (fx.work / migration.BLOCKER).exists()


@pytest.mark.parametrize("args", [
    'target_os = "win"\nchrome_pgo_phase = 2\n',
    'target_cpu = "arm64"\ntarget_os = "win"\nchrome_pgo_phase = 2\n',
    'target_cpu = "x64"\ntarget_os = "win"\nv8_target_cpu = "arm64"\nchrome_pgo_phase = 2\n',
    'target_cpu = "x64"\ntarget_os = "win"\nif (true) { target_cpu = "arm64" }\n',
])
def test_unmarked_migration_requires_strict_x64_gn(tmp_path, monkeypatch, args):
    fx = Fixture(tmp_path, monkeypatch)
    put(fx.src, "out/Default/args.gn", args)
    before = snapshot(fx.work)
    with pytest.raises((ValueError, RuntimeError)):
        fx.run()
    assert snapshot(fx.work) == before
    assert not (fx.work / migration.BLOCKER).exists()


@pytest.mark.parametrize("repository", ["donor", "target"])
def test_unmarked_migration_cannot_skip_upstream_receipt_verification(tmp_path, monkeypatch, repository):
    fx = Fixture(tmp_path, monkeypatch)
    verify = migration.upstream.verify_restored

    def reject(work, platform, arch, *, repo):
        assert (work, platform, arch) == (fx.work, "windows", "x64")
        if repo == getattr(fx, repository):
            raise ValueError("receipt manifest/platform/architecture mismatch")
        return verify(work, platform, arch, repo=repo)

    monkeypatch.setattr(migration.upstream, "verify_restored", reject)
    before = snapshot(fx.work)
    with pytest.raises(ValueError, match="receipt manifest"):
        fx.run()
    assert snapshot(fx.work) == before
    assert not (fx.work / migration.BLOCKER).exists()


@pytest.mark.parametrize("marker", ["x64", "arm64"])
def test_arch_marker_appearance_during_migration_is_not_ignored(tmp_path, monkeypatch, marker):
    fx = Fixture(tmp_path, monkeypatch)
    roundtrip = migration._roundtrip

    def race(*args):
        proof = roundtrip(*args)
        put(fx.work, ".chromix-target-arch", marker)
        return proof

    monkeypatch.setattr(migration, "_roundtrip", race)
    before = snapshot(fx.src)
    with pytest.raises(RuntimeError, match="changed concurrently"):
        fx.run()
    assert snapshot(fx.src) == before
    assert (fx.work / migration.BLOCKER).is_dir()


@pytest.mark.parametrize("name", [".chromix-source-unpacked", ".chromix-ungoogled-core",
    ".chromix-ungoogled-windows", ".chromix-binaries-pruned", ".chromix-domain-substituted",
    ".chromix-toolchain-ready", ".chromix-patches", ".chromix-patch-selection", migration.READY])
def test_legacy_arch_exception_does_not_make_source_layer_markers_optional(tmp_path, monkeypatch, name):
    fx = Fixture(tmp_path, monkeypatch)
    (fx.src / name).unlink()
    before = snapshot(fx.work)
    with pytest.raises(RuntimeError):
        fx.run()
    assert snapshot(fx.work) == before
    assert not (fx.work / migration.BLOCKER).exists()


@pytest.mark.parametrize("kind", ["directory", "symlink", "dangling"])
def test_legacy_arch_exception_does_not_accept_unsafe_marker(tmp_path, monkeypatch, kind):
    fx = Fixture(tmp_path, monkeypatch)
    marker = fx.work / ".chromix-target-arch"
    if kind == "directory":
        marker.mkdir()
    else:
        target = fx.work / "arch.txt"
        if kind == "symlink":
            target.write_text("x64")
        marker.symlink_to(target)
    before = snapshot(fx.src)
    with pytest.raises(RuntimeError):
        fx.run()
    assert snapshot(fx.src) == before
    assert not (fx.work / migration.BLOCKER).exists()
