import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERIFY_PATH = ROOT / "scripts" / "verify.py"
SPEC = importlib.util.spec_from_file_location("release_verify", VERIFY_PATH)
verify = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verify)


def canonical_payload():
    entries = verify._snapshot_package_sources()
    return entries, verify._canonical_archive_bytes(entries)


def runtime_report():
    artifacts = {
        name: {"sha256": "0" * 64, "bytes": index + 1}
        for index, name in enumerate(sorted(verify.EXPECTED_RUNTIME_ARTIFACTS))
    }
    return {
        "schema_version": 1,
        "fixture": "tests/fixtures/observations.json",
        "artifacts": artifacts,
    }


class CanonicalArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries, cls.payload = canonical_payload()

    def assert_noncanonical(self, payload, pattern="canonical"):
        with self.assertRaisesRegex(verify.VerifyError, pattern):
            verify._validate_archive_bytes(
                payload,
                source_entries=self.entries,
                smoke=False,
            )

    def test_canonical_archive_is_accepted(self):
        self.assertEqual(
            verify._validate_archive_bytes(
                self.payload,
                source_entries=self.entries,
                smoke=False,
            ),
            self.payload,
        )

    def test_global_comment_is_rejected(self):
        stream = io.BytesIO(self.payload)
        with zipfile.ZipFile(stream, "a") as archive:
            archive.comment = b"comment"
        self.assert_noncanonical(stream.getvalue(), "global comment")

    def test_prepended_and_trailing_bytes_are_rejected(self):
        for label, payload in (
            ("prepended", b"MZ-STUB" + self.payload),
            ("trailing", self.payload + b"TRAILER"),
        ):
            with self.subTest(label=label):
                self.assert_noncanonical(payload)

    def test_local_header_metadata_drift_is_rejected(self):
        payload = bytearray(self.payload)
        self.assertEqual(payload[:4], b"PK\x03\x04")
        payload[10] ^= 1
        self.assert_noncanonical(bytes(payload))

    def test_validation_and_smoke_share_one_immutable_snapshot(self):
        with tempfile.TemporaryDirectory() as raw:
            candidate = Path(raw) / "candidate.zip"
            candidate.write_bytes(self.payload)
            replacement = b"unvalidated replacement"
            seen = []

            def replace_then_smoke(payload):
                candidate.write_bytes(replacement)
                seen.append(payload)

            with mock.patch.object(verify, "_smoke_archive", side_effect=replace_then_smoke):
                returned = verify.validate_archive(candidate, smoke=True)

            self.assertEqual(seen, [self.payload])
            self.assertEqual(returned, self.payload)
            self.assertEqual(candidate.read_bytes(), replacement)


class ManifestAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            verify.MANIFEST_PATH.read_text(encoding="utf-8")
        )

    def test_brand_assets_require_exact_package_allowlist(self):
        bad_values = (
            "./assets/../outside.png",
            "./assets/../../outside.png",
            "./assets\\..\\outside.png",
            "./assets/unlisted.png",
        )
        for field in verify.EXPECTED_BRANDING_ASSETS:
            for value in bad_values:
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(
                        verify.VerifyError,
                        "exactly match",
                    ):
                        verify._branding_asset_source(field, value)

    def test_manifest_validation_rejects_asset_traversal_and_unlisted_assets(self):
        for field in verify.EXPECTED_BRANDING_ASSETS:
            for value in ("./assets/../../outside.png", "./assets/unlisted.png"):
                manifest = copy.deepcopy(self.manifest)
                manifest["interface"][field] = value
                with self.subTest(field=field, value=value):
                    with mock.patch.object(
                        verify,
                        "_load_json_file",
                        return_value=manifest,
                    ):
                        with self.assertRaisesRegex(verify.VerifyError, "exactly match"):
                            verify.validate_manifest()

    def test_expected_brand_assets_are_regular_packaged_files(self):
        for field, (relative, archive_name) in verify.EXPECTED_BRANDING_ASSETS.items():
            with self.subTest(field=field):
                self.assertEqual(
                    verify._branding_asset_source(field, relative),
                    verify.PACKAGE_SOURCES[archive_name],
                )

    def test_manifest_rejects_malformed_https_urls(self):
        bad_values = (
            "http://example.com/policy",
            "https:///missing-host",
            "https://user@example.com/policy",
            "https://example.com/policy#fragment",
            "https://example.com/white space",
            "https://example.com/back\\slash",
            "https://example.com/bad%escape",
            "https://example.com/truncated%2",
        )
        for value in bad_values:
            manifest = copy.deepcopy(self.manifest)
            manifest["interface"]["privacyPolicyURL"] = value
            with self.subTest(value=value):
                with mock.patch.object(
                    verify,
                    "_load_json_file",
                    return_value=manifest,
                ):
                    with self.assertRaisesRegex(verify.VerifyError, "valid HTTPS"):
                        verify.validate_manifest()

    def test_https_url_rejects_backslashes_and_invalid_percent_escapes(self):
        for value in (
            "https://example.com/a\\b",
            "https://example.com/%",
            "https://example.com/%0",
            "https://example.com/%GG",
        ):
            with self.subTest(value=value):
                self.assertFalse(verify._is_valid_https_url(value))
        self.assertTrue(verify._is_valid_https_url("https://example.com/a%20b"))


class ArchivePathTests(unittest.TestCase):
    def test_windows_superscript_device_names_are_rejected(self):
        for name in ("COM¹", "COM².txt", "COM³.png", "LPT¹", "LPT².md", "LPT³"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(verify.VerifyError, "reserved"):
                    verify.validate_archive_name(name)

    def test_archive_validation_requires_complete_source_snapshot(self):
        entries, payload = canonical_payload()
        entries.pop(next(iter(entries)))
        with self.assertRaisesRegex(verify.VerifyError, "exact package allowlist"):
            verify._validate_archive_bytes(payload, source_entries=entries, smoke=False)


class OutputPathTests(unittest.TestCase):
    def make_symlink(self, target, link):
        try:
            link.symlink_to(target, target_is_directory=Path(target).is_dir())
        except (NotImplementedError, OSError) as exc:
            self.skipTest("symlink creation is unavailable: %s" % exc)

    def test_intermediate_symlink_is_not_a_regular_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = root / "outside"
            outside.mkdir()
            source = outside / "source.txt"
            source.write_text("sentinel", encoding="utf-8")
            link = root / "linked"
            self.make_symlink(outside, link)
            with self.assertRaisesRegex(verify.VerifyError, "symlink|reparse"):
                verify._assert_regular_file(link / "source.txt", boundary=root)

    def test_windows_reparse_attribute_is_detected(self):
        result = SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o755,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )
        self.assertTrue(verify._is_link_or_reparse(result))

    def test_dist_symlink_is_rejected_without_outside_write(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            outside = root / "outside"
            outside.mkdir()
            dist = root / "dist"
            self.make_symlink(outside, dist)
            with mock.patch.multiple(verify, ROOT=root, DIST=dist):
                with self.assertRaisesRegex(verify.VerifyError, "symlink|reparse|plain"):
                    verify.publish_bytes(dist / "release.zip", b"payload")
            self.assertEqual(list(outside.iterdir()), [])

    def test_intermediate_output_symlink_is_rejected_without_outside_write(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            outside = root / "outside"
            outside.mkdir()
            linked = dist / "linked"
            self.make_symlink(outside, linked)
            with mock.patch.multiple(verify, ROOT=root, DIST=dist):
                with self.assertRaisesRegex(verify.VerifyError, "symlink|reparse"):
                    verify.publish_bytes(linked / "release.zip", b"payload")
            self.assertEqual(list(outside.iterdir()), [])

    def test_existing_output_symlink_is_rejected_and_target_is_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            outside = root / "sentinel.txt"
            outside.write_bytes(b"sentinel")
            output = dist / "release.zip"
            self.make_symlink(outside, output)
            with mock.patch.multiple(verify, ROOT=root, DIST=dist):
                with self.assertRaisesRegex(verify.VerifyError, "symlink|reparse|non-link"):
                    verify.publish_bytes(output, b"replacement")
            self.assertEqual(outside.read_bytes(), b"sentinel")

    def test_lexically_outside_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dist = root / "dist"
            outside = root / "outside.zip"
            with mock.patch.multiple(verify, ROOT=root, DIST=dist):
                with self.assertRaisesRegex(verify.VerifyError, "outside"):
                    verify.publish_bytes(outside, b"payload")
            self.assertFalse(outside.exists())

    def test_plain_dist_publish_replaces_a_regular_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            output = dist / "release.zip"
            output.write_bytes(b"old")
            with mock.patch.multiple(verify, ROOT=root, DIST=dist):
                verify.publish_bytes(output, b"new")
            self.assertEqual(output.read_bytes(), b"new")
            self.assertFalse(
                any(path.name.startswith(".release.zip.tmp-") for path in dist.iterdir())
            )

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-bound publish coverage")
    def test_parent_swap_cannot_publish_outside_dist(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dist = root / "dist"
            parent = dist / "nested"
            parent.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            output = parent / "release.zip"
            real_open = os.open
            swapped = []

            def swap_before_parent_open(path, flags, *args, **kwargs):
                if Path(path) == parent and not swapped:
                    moved = dist / "original-parent"
                    parent.rename(moved)
                    parent.symlink_to(outside, target_is_directory=True)
                    swapped.append(True)
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.multiple(verify, ROOT=root, DIST=dist):
                with mock.patch.object(verify.os, "open", side_effect=swap_before_parent_open):
                    try:
                        verify.publish_bytes(output, b"payload")
                    except verify.VerifyError:
                        pass
            self.assertFalse((outside / "release.zip").exists())
            safe_publish = dist / "original-parent" / "release.zip"
            if safe_publish.exists():
                self.assertEqual(safe_publish.read_bytes(), b"payload")


class ProcessorBoundaryTests(unittest.TestCase):
    def assert_boundary_rejects(self, source, pattern):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            processor = root / "processor.py"
            processor.write_text(source, encoding="utf-8")
            with mock.patch.multiple(verify, ROOT=root, PROCESSOR=processor):
                with self.assertRaisesRegex(verify.VerifyError, pattern):
                    verify.validate_processor_boundary()

    def test_dynamic_import_and_importlib_are_rejected(self):
        cases = (
            ("value = __import__('socket')\n", "dynamic|__import__"),
            (
                "import importlib\nvalue = importlib.import_module('socket')\n",
                "allowlist|dynamic",
            ),
        )
        for source, pattern in cases:
            with self.subTest(source=source):
                self.assert_boundary_rejects(source, pattern)

    def test_lazy_third_party_import_is_rejected(self):
        self.assert_boundary_rejects(
            "import third_party_client\n",
            "stdlib allowlist",
        )

    def test_network_and_process_patterns_are_rejected(self):
        cases = (
            ("import socket\n", "forbidden"),
            ("import subprocess\n", "forbidden"),
            ("import os\nos.system('command')\n", "process API"),
            (
                "import os\nrunner = getattr(os, 'spawnv')\n",
                "dangerous callable",
            ),
        )
        for source, pattern in cases:
            with self.subTest(source=source):
                self.assert_boundary_rejects(source, pattern)

    def test_dangerous_call_aliases_are_rejected(self):
        cases = (
            ("import os\nrun = os.system\nrun('command')\n", "process API"),
            ("loader = __import__\nloader('socket')\n", "__import__|dynamic"),
            (
                "import ctypes\ncreate = ctypes.windll.kernel32.CreateProcessW\n"
                "create(None, None, None, None, False, 0, None, None, None, None)\n",
                "native process API",
            ),
        )
        for source, pattern in cases:
            with self.subTest(source=source):
                self.assert_boundary_rejects(source, pattern)


class MetadataTests(unittest.TestCase):
    def test_agents_yaml_requires_semantic_exact_shape(self):
        path = (
            ROOT
            / "plugins"
            / "screenshot-action-inbox"
            / "skills"
            / "organize-screenshot-inbox"
            / "agents"
            / "openai.yaml"
        )
        valid = path.read_text(encoding="utf-8")
        document = verify._validate_agents_metadata(valid)
        self.assertEqual(
            document["policy"],
            {"allow_implicit_invocation": True},
        )
        bad_values = (
            valid.replace("default_prompt:", "unknown_prompt:"),
            valid.replace("allow_implicit_invocation: true", "allow_implicit_invocation: false"),
            valid + "unexpected:\n  value: true\n",
            valid.replace('display_name: "', 'display_name: unquoted-'),
        )
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(verify.VerifyError):
                    verify._validate_agents_metadata(value)

    def test_evaluations_require_prompts_fixtures_and_expected_outcomes(self):
        cases = json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))
        self.assertIs(verify._validate_evaluations(cases), cases)
        mutations = []
        missing_prompt = copy.deepcopy(cases)
        del missing_prompt["positive"][0]["prompt"]
        mutations.append(missing_prompt)
        empty_fixture = copy.deepcopy(cases)
        empty_fixture["positive"][0]["fixture"] = ""
        mutations.append(empty_fixture)
        empty_expected = copy.deepcopy(cases)
        empty_expected["negative"][0]["expected"] = []
        mutations.append(empty_expected)
        for value in mutations:
            with self.subTest(value=value):
                with self.assertRaises(verify.VerifyError):
                    verify._validate_evaluations(value)


class MatrixEvidenceTests(unittest.TestCase):
    def write_bundle(self, parent, payload, report, checksum=None):
        parent.mkdir()
        archive = parent / verify.ARCHIVE_NAME
        archive.write_bytes(payload)
        digest = verify.sha256_bytes(payload)
        if checksum is None:
            checksum = ("%s  %s\n" % (digest, verify.ARCHIVE_NAME)).encode("ascii")
        (parent / (verify.ARCHIVE_NAME + ".sha256")).write_bytes(checksum)
        (parent / "runtime-artifact-evidence.json").write_bytes(
            verify._runtime_evidence_bytes(report)
        )

    def matrix_patches(self, root, report, publish):
        return (
            mock.patch.multiple(
                verify,
                ROOT=root,
                DIST=root / "dist",
            ),
            mock.patch.object(verify, "validate_source"),
            mock.patch.object(verify, "_snapshot_package_sources", return_value={}),
            mock.patch.object(verify, "_runtime_evidence_report", return_value=report),
            mock.patch.object(verify, "_validate_archive_bytes"),
            mock.patch.object(verify, "_smoke_archive"),
            mock.patch.object(verify, "publish_bytes", publish),
        )

    def test_matrix_rejects_missing_or_extra_bundle_files(self):
        report = runtime_report()
        for mode in ("missing", "extra"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                matrix = root / "matrix"
                matrix.mkdir()
                bundle = matrix / "job"
                self.write_bundle(bundle, b"archive", report)
                if mode == "missing":
                    (bundle / "runtime-artifact-evidence.json").unlink()
                else:
                    (bundle / "unexpected.txt").write_text("extra", encoding="utf-8")
                publish = mock.Mock()
                patches = self.matrix_patches(root, report, publish)
                with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                    with self.assertRaises(verify.VerifyError):
                        verify.compare_matrix(matrix, 1)
                publish.assert_not_called()

    def test_matrix_validates_every_bundle_before_any_publish(self):
        report = runtime_report()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            matrix = root / "matrix"
            matrix.mkdir()
            self.write_bundle(matrix / "job-a", b"archive", report)
            self.write_bundle(matrix / "job-b", b"archive", report, checksum=b"wrong\n")
            publish = mock.Mock()
            patches = self.matrix_patches(root, report, publish)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
                with self.assertRaisesRegex(verify.VerifyError, "checksum"):
                    verify.compare_matrix(matrix, 2)
            publish.assert_not_called()

    def test_runtime_evidence_requires_exact_five_artifacts(self):
        report = runtime_report()
        verify._validate_runtime_evidence_report(report)
        missing = copy.deepcopy(report)
        missing["artifacts"].pop(next(iter(missing["artifacts"])))
        extra = copy.deepcopy(report)
        extra["artifacts"]["extra.txt"] = {"sha256": "0" * 64, "bytes": 1}
        for value in (missing, extra):
            with self.subTest(value=value):
                with self.assertRaisesRegex(verify.VerifyError, "exactly"):
                    verify._validate_runtime_evidence_report(value)


if __name__ == "__main__":
    unittest.main()
