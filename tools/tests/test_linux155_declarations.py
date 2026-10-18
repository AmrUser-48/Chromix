"""Exact Linux155 declaration repairs with optional pinned TypeScript execution."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import linux_restored_generators as generators
from tools.tests import test_linux155_generators as generator_tests
from tools.tests.test_linux155_generators import DECLARATIONS, DECLARATIONS_FIXTURE_BYTES, SCRIPTS


FLAGS = ["--ignoreConfig", "--declaration", "--emitDeclarationOnly",
         "--isolatedDeclarations", "--noCheck", "--noResolve"]
STRICT = ["--ignoreConfig", "--declaration", "--isolatedDeclarations", "--strict", "--target", "es2023"]


class Linux155DeclarationsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.src = Path(temporary.name).resolve()
        for name, text in SCRIPTS.items():
            self.write(generators.DEVTOOLS + "/" + name, text)
        lock = b"independent declaration fixture lock\n"
        (self.src / generators.LOCK).write_bytes(lock)
        patch = mock.patch.object(generators, "LOCK155_SHA256", hashlib.sha256(lock).hexdigest())
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, name, text):
        path = self.src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def repaired_sources(self):
        _, changes = generators.source_plan(self.src, pins=generators.PINS155)
        return {name.removeprefix(generators.DEVTOOLS + "/"): after
                for name, _, after, _ in changes if name.endswith(".ts")}

    def compiler(self):
        node, tsc = os.environ.get("CHROMIX_TEST_NODE"), os.environ.get("CHROMIX_TEST_TSC")
        if not node or not tsc:
            self.skipTest("set CHROMIX_TEST_NODE and CHROMIX_TEST_TSC to pinned TypeScript 6.0.2 lib/tsc.js")
        self.command = [str(Path(node).resolve()), str(Path(tsc).resolve())]
        result = self.run_compiler(["--version"])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.strip(), "Version 6.0.2")
        package = Path(self.command[1]).parent.parent
        self.assertEqual(hashlib.sha256((package / "package.json").read_bytes()).hexdigest(),
                         DECLARATIONS["typescript_package_sha256"])
        digest = hashlib.sha256()
        for directory, dirs, files in os.walk(package):
            dirs.sort()
            for name in sorted(files):
                path = Path(directory) / name
                digest.update(json.dumps([path.relative_to(package).as_posix(),
                                          hashlib.sha256(path.read_bytes()).hexdigest()]).encode())
        self.assertEqual(digest.hexdigest(), DECLARATIONS["typescript_tree_sha256"])

    def run_compiler(self, args):
        return subprocess.run(self.command + args, cwd=self.src, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30, check=False)

    def test_independent_source_bytes_repair_hashes_and_version_scope(self):
        self.assertEqual(hashlib.sha256(DECLARATIONS_FIXTURE_BYTES).hexdigest(),
                         "ba8467eee5d63e00f3afc6a90ea943f493e18c6a6865fd7783136a4fb2a612aa")
        self.assertEqual(set(DECLARATIONS["sources"]), {
            "front_end/ui/legacy/ARIAUtils.ts", "front_end/ui/legacy/Widget.ts"})
        self.assertEqual(DECLARATIONS["revision"], "cd7bfad9bba968ac23ba67e538f9e1d499991d25")
        repaired = self.repaired_sources()
        self.assertEqual(set(repaired), set(DECLARATIONS["sources"]))
        for name, text in DECLARATIONS["sources"].items():
            with self.subTest(name=name):
                identity = DECLARATIONS["identities"][name]
                before = text.encode()
                self.assertEqual(len(before), identity["before_bytes"])
                self.assertEqual(hashlib.sha256(before).hexdigest(), identity["before_sha256"])
                self.assertEqual(before.count(identity["old"].encode()), 1)
                self.assertEqual(repaired[name], before.replace(identity["old"].encode(), identity["new"].encode()))
                self.assertEqual(len(repaired[name]), identity["after_bytes"])
                self.assertEqual(hashlib.sha256(repaired[name]).hexdigest(), identity["after_sha256"])
                self.assertNotIn(name, generators.generator_profile(generators.PINS)["sources"])
        self.assertEqual(generators.TYPESCRIPT155_TREE, DECLARATIONS["typescript_tree_sha256"])

    def test_unknown_original_or_repaired_source_bytes_fail_without_execution(self):
        repaired = self.repaired_sources()
        for name, text in DECLARATIONS["sources"].items():
            path = self.src / generators.DEVTOOLS / name
            for payload in (text.encode(), repaired[name]):
                with self.subTest(name=name, repaired=payload == repaired[name]):
                    unknown = payload + b"\n// unknown bytes\n"
                    path.write_bytes(unknown)
                    with mock.patch.object(generators.subprocess, "run") as run, \
                            self.assertRaisesRegex(ValueError, "unknown Linux esbuild source"):
                        generators.prepare_esbuild(self.src, host_arch="x64", repair=True,
                                                   pins=generators.PINS155)
                    run.assert_not_called()
                    self.assertEqual(path.read_bytes(), unknown)
            path.write_text(text)

    def test_real_compiler_full_sources_declarations_and_unchanged_javascript(self):
        self.compiler()
        repaired = self.repaired_sources()
        sources = [self.src / generators.DEVTOOLS / name for name in DECLARATIONS["sources"]]
        for fixed in (False, True):
            if fixed:
                for name, payload in repaired.items():
                    (self.src / generators.DEVTOOLS / name).write_bytes(payload)
            output = self.src / ("fixed" if fixed else "original")
            result = self.run_compiler(FLAGS + ["--outDir", str(output), "--rootDir", str(sources[0].parent)] +
                                       [str(path) for path in sources])
            self.assertEqual(result.returncode, 0 if fixed else 1, result.stdout)
            if not fixed:
                self.assertEqual(result.stdout.count("error TS9025:"), 2, result.stdout)
                self.assertIn("ARIAUtils.ts(423,38)", result.stdout)
                self.assertIn("Widget.ts(87,61)", result.stdout)
            js_output = self.src / ("fixed-js" if fixed else "original-js")
            result = self.run_compiler([flag for flag in FLAGS if flag != "--emitDeclarationOnly"] +
                                       ["--target", "esnext", "--module", "esnext", "--outDir", str(js_output),
                                        "--rootDir", str(sources[0].parent)] + [str(path) for path in sources])
            self.assertEqual(result.returncode, 0 if fixed else 1, result.stdout)
        for name, identity in DECLARATIONS["identities"].items():
            stem = Path(name).stem
            with self.subTest(name=name):
                declaration = (self.src / "fixed" / (stem + ".d.ts")).read_bytes()
                self.assertEqual(len(declaration), identity["dts"]["bytes"])
                self.assertEqual(hashlib.sha256(declaration).hexdigest(), identity["dts"]["sha256"])
                before = (self.src / "original-js" / (stem + ".js")).read_bytes()
                after = (self.src / "fixed-js" / (stem + ".js")).read_bytes()
                self.assertEqual(before, after)
                self.assertEqual(len(after), identity["js"]["bytes"])
                self.assertEqual(hashlib.sha256(after).hexdigest(), identity["js"]["before_after_sha256"])

    def test_real_strict_mini_and_generated_declaration_consumers(self):
        self.compiler()
        mini = self.write("mini.ts", DECLARATIONS["mini"])
        result = self.run_compiler(STRICT + ["--emitDeclarationOnly", "--outDir", "mini-original", str(mini)])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout.count("error TS9025:"), 2, result.stdout)
        fixed = DECLARATIONS["mini"]
        for identity in DECLARATIONS["identities"].values():
            self.assertEqual(fixed.count(identity["old"]), 1)
            fixed = fixed.replace(identity["old"], identity["new"])
        mini.write_text(fixed)
        result = self.run_compiler(STRICT + ["--emitDeclarationOnly", "--outDir", "mini-fixed", str(mini)])
        self.assertEqual(result.returncode, 0, result.stdout)
        for kind, text in DECLARATIONS["consumers"].items():
            consumer = self.write(kind + ".ts", "import {LiveAnnouncer, AnnouncerRole, Widget, WidgetConfig} "
                                  "from './mini-fixed/mini.js';\n" + text)
            result = self.run_compiler(STRICT + ["--noEmit", str(consumer)])
            if kind == "positive":
                self.assertEqual(result.returncode, 0, result.stdout)
            else:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                for diagnostic in ("TS2345", "TS2554", "TS2559"):
                    self.assertIn(diagnostic, result.stdout)

    def test_real_gn_ninja_declaration_action_original_fails_repaired_passes(self):
        self.compiler()
        ninja = os.environ.get("NINJA") or shutil.which("ninja")
        if not ninja:
            self.skipTest("set NINJA for real declaration action coverage")
        repaired = self.repaired_sources()
        fixture = generator_tests.Linux155GeneratorsTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture._gn_fixture()
        fixture._gn_host("linux", "x64")
        root = fixture.src
        build = root / "BUILD.gn"
        text = build.read_text()
        source_names = [generators.DEVTOOLS + "/" + name for name in DECLARATIONS["sources"]]
        build.write_text(text.replace('sources = [ "emit.ts" ]',
                                      "sources = " + json.dumps(source_names)))
        local_node = root / "third_party/node/linux/node-linux-x64/bin/node"
        local_node.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.command[0], local_node)
        package = root / generators.DEVTOOLS / "node_modules/typescript"
        shutil.copytree(Path(self.command[1]).parent.parent, package)
        result = fixture._run_gn("gen", "out/declarations")
        self.assertEqual(result.returncode, 0, result.stdout)
        result = fixture._run_gn("desc", "out/declarations", "//:emit-dts", "--format=json")
        self.assertEqual(result.returncode, 0, result.stdout)
        action = json.loads(result.stdout)["//:emit-dts"]
        for flag in FLAGS:
            self.assertIn(flag, action["response_file_contents"])
        for name in action["inputs"]:
            path = root / name.removeprefix("//")
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
        output = root / "out/declarations"
        targets = [(root / name.removeprefix("//")).relative_to(output).as_posix()
                   for name in action["outputs"]]
        for fixed in (False, True):
            for name, original in DECLARATIONS["sources"].items():
                payload = repaired[name] if fixed else original.encode()
                (root / generators.DEVTOOLS / name).write_bytes(payload)
            result = subprocess.run([str(Path(ninja).resolve()), "-C", str(output), "-v", *targets],
                                    cwd=root, env=dict(os.environ, PATH=""), text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30, check=False)
            if not fixed:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(result.stdout.count("error TS9025:"), 2, result.stdout)
            else:
                self.assertEqual(result.returncode, 0, result.stdout)
                for name in action["outputs"]:
                    path = root / name.removeprefix("//")
                    identity = DECLARATIONS["identities"]["front_end/ui/legacy/" + path.name.replace(".d.ts", ".ts")]
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), identity["dts"]["sha256"])
