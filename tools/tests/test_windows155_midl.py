"""Windows155 x64 byte identity, once-only invalidation, and154 ARM64 coexistence."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest

from tools import repair_windows_midl as repair
from tools.tests import test_windows154_midl as previous

IDENTITY = {
    "chromium_version": "155.0.8059.39",
    "ungoogled_commit": "ffeb45a2bb88bc56a1dfed31410aeb10e9efcd5a",
    "head_sha": "989b4382364baaddb8b3180e1eb1030ca96a7273",
    "platform": "windows", "arch": "x64",
    "repository": "ungoogled-software/ungoogled-chromium-windows", "repository_id": 177210827,
    "head_branch": "155.0.8059.39-1.1", "event": "push",
    "workflow_path": ".github/workflows/build-x64.yml", "run_id": 37831345909,
    "artifact_id": 11670281902, "artifact_name": "build-artifact",
    "artifact_digest": "sha256:4648e4426093106521986dc5a4dc66b47d861250710b3391f5ed7cf73d11db5c",
    "artifact_size_in_bytes": 15705197543,
}


class Windows155MidlTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.src = Path(temporary.name).resolve()
        self.script = self.src / repair.SCRIPT
        self.script.parent.mkdir(parents=True)
        self.original = previous.FIXTURE.read_bytes()
        self.script.write_bytes(self.original)
        self.out = self.src / "out/Default"
        self.out.mkdir(parents=True)
        self.output = self.out / "gen/midl/a.tlb"
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"stale")
        (self.out / "build.ninja").write_text(previous.graph(["gen/midl/a.tlb"]))

    def apply(self, identity=IDENTITY, arch="x64", **kwargs):
        return repair.apply(self.src, "windows", arch, identity, **kwargs)

    def test_verified_donor_transform_and_idempotent_output_invalidation(self):
        self.assertEqual(dict(repair.WINDOWS155_RECORDS[0]["identity"]), IDENTITY)
        self.assertEqual(hashlib.sha256(self.original).hexdigest(), repair.WINDOWS155_ORIGINAL_SHA256)
        source = self.src / "third_party/win_build_output/midl/x64/source.tlb"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"")
        unrelated = self.out / "gen/unrelated.obj"
        unrelated.write_bytes(b"retain")
        first = self.apply()
        self.assertEqual(first["status"], "repaired")
        self.assertEqual(first["invalidated_outputs"], ["gen/midl/a.tlb"])
        self.assertEqual(self.script.read_bytes(), repair.transform155(self.original))
        self.assertEqual(hashlib.sha256(self.script.read_bytes()).hexdigest(), repair.WINDOWS155_REPAIRED_SHA256)
        self.output.write_bytes(b"regenerated")
        states = {p: previous.snapshot(p) for p in (source, unrelated, self.script, self.output)}
        self.assertEqual(self.apply()["status"], "verified")
        for path, state in states.items():
            self.assertEqual(previous.snapshot(path), state)
        self.assertEqual(json.loads((self.src / repair.RECORD).read_text())["identity"], IDENTITY)

    def test_all_identity_fields_and_types_fail_before_deletion(self):
        for key, value in IDENTITY.items():
            bad = "155.0.8059.40" if key == "chromium_version" else "unknown"
            for alternate in ((bad, float(value)) if type(value) is int else (bad,)):
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, "identity"):
                    self.apply(dict(IDENTITY, **{key: alternate}))
        for identity in ({"chromium_version": "155.0.8059.39"}, dict(IDENTITY, extra=True),
                         dict(IDENTITY, arch="arm64")):
            with self.assertRaisesRegex(ValueError, "identity"):
                self.apply(identity)
        with self.assertRaisesRegex(ValueError, "identity"):
            self.apply(dict(IDENTITY, arch="arm64"), arch="arm64")
        self.assertEqual(self.output.read_bytes(), b"stale")
        self.assertEqual(self.script.read_bytes(), self.original)

    def test_unknown_partial_or_crlf_bytes_cannot_delete_outputs(self):
        for payload in (b"", self.original + b"# unknown", self.original.replace(b"\n", b"\r\n"),
                        self.original.replace(repair.BEFORE, repair.AFTER)):
            self.script.write_bytes(payload)
            with self.assertRaises(ValueError):
                self.apply()
            self.assertEqual(self.output.read_bytes(), b"stale")
            self.assertEqual(self.script.read_bytes(), payload)

    def test_immutable_records_and_cross_version_proofs(self):
        record = repair.WINDOWS155_RECORDS[0]
        with self.assertRaises(TypeError):
            record["identity"]["artifact_id"] = 1
        for records in ((), [record], (record, record), (dict(record),),
                        (MappingProxyType(dict(record, before_sha256="0" * 64)),)):
            with self.assertRaises(ValueError):
                self.apply(records155=records)
        repair.apply(self.src, "windows", "x64", previous.IDENTITY)
        self.output.write_bytes(b"regenerated")
        with self.assertRaisesRegex(ValueError, "invalidation record"):
            self.apply()
        self.assertEqual(self.output.read_bytes(), b"regenerated")

    def test_existing154_arm64_and_nonwindows_remain_separate(self):
        self.assertEqual(dict(repair.WINDOWS154_RECORDS[1]["identity"]), previous.ARM_IDENTITY)
        for platform in ("linux", "macos", "off"):
            self.assertEqual(repair.apply(self.src, platform, "x64", IDENTITY)["status"], "not_applicable")
        result = repair.apply(self.src, "windows", "arm64", previous.ARM_IDENTITY)
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(json.loads((self.src / repair.RECORD).read_text())["identity"], previous.ARM_IDENTITY)


if __name__ == "__main__":
    unittest.main()
