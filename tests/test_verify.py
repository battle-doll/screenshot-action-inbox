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


def runtime_report(provenance=None, archive_payload=b"archive"):
    artifacts = {
        name: {"sha256": "0" * 64, "bytes": index + 1}
        for index, name in enumerate(sorted(verify.EXPECTED_RUNTIME_ARTIFACTS))
    }
    return {
        "schema_version": 1,
        "fixture": "tests/fixtures/observations.json",
        "source_archive_sha256": verify.sha256_bytes(archive_payload),
        "observation_sha256": "2" * 64,
        "provenance": provenance,
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

            observation_payload = verify._read_regular_bytes(
                verify.OBSERVATIONS_PATH, 2 * 1024 * 1024, boundary=verify.ROOT
            )

            def replace_then_smoke(payload, fixture):
                candidate.write_bytes(replacement)
                seen.append((payload, fixture))

            with mock.patch.object(verify, "_smoke_archive", side_effect=replace_then_smoke):
                returned = verify.validate_archive(candidate, smoke=True)

            self.assertEqual(seen, [(self.payload, observation_payload)])
            self.assertEqual(returned, self.payload)
            self.assertEqual(candidate.read_bytes(), replacement)


class ImmutableSnapshotTests(unittest.TestCase):
    def test_mutated_live_processor_cannot_change_archive_or_runtime_evidence(self):
        entries = verify._snapshot_package_sources()
        observation_payload = verify._read_regular_bytes(
            verify.OBSERVATIONS_PATH, 2 * 1024 * 1024, boundary=verify.ROOT
        )
        processor_name = (
            "skills/organize-screenshot-inbox/scripts/screenshot_inbox.py"
        )
        with tempfile.TemporaryDirectory() as raw:
            changed_processor = Path(raw) / "processor.py"
            changed_processor.write_bytes(entries[processor_name])
            changed_sources = dict(verify.PACKAGE_SOURCES)
            changed_sources[processor_name] = changed_processor
            changed_processor.write_text("import socket\n", encoding="utf-8")
            with mock.patch.multiple(
                verify,
                PROCESSOR=changed_processor,
                PACKAGE_SOURCES=changed_sources,
            ), mock.patch.object(
                verify,
                "_read_regular_bytes",
                side_effect=AssertionError("validated snapshot must not be reread"),
            ):
                verify.validate_processor_boundary(entries)
                archive_payload = verify._canonical_archive_bytes(entries)
                report = verify._runtime_evidence_report(
                    entries, observation_payload, provenance=None
                )
            with zipfile.ZipFile(io.BytesIO(archive_payload), "r") as archive:
                self.assertEqual(archive.read(processor_name), entries[processor_name])
            self.assertEqual(
                report["source_archive_sha256"],
                verify.sha256_bytes(archive_payload),
            )

    def test_all_pipeline_takes_package_snapshot_once_and_reuses_same_object(self):
        entries = verify._snapshot_package_sources()
        observation_payload = b"fixture"
        seen = []

        def record(label):
            def recorder(source_entries, fixture=None):
                seen.append((label, source_entries, fixture))
            return recorder

        with mock.patch.object(
            verify,
            "_snapshot_package_sources",
            side_effect=[entries, AssertionError("second snapshot forbidden")],
        ) as snapshot, mock.patch.object(
            verify, "_read_regular_bytes", return_value=observation_payload
        ) as read, mock.patch.object(
            verify, "validate_source", side_effect=record("validate")
        ), mock.patch.object(
            verify, "run_tests"
        ), mock.patch.object(
            verify, "build_release", side_effect=record("build")
        ), mock.patch.object(
            verify, "runtime_evidence", side_effect=record("runtime")
        ):
            verify.verify_all()

        snapshot.assert_called_once_with()
        read.assert_called_once_with(
            verify.OBSERVATIONS_PATH, 2 * 1024 * 1024, boundary=verify.ROOT
        )
        self.assertEqual([row[0] for row in seen], ["validate", "build", "runtime"])
        self.assertTrue(all(row[1] is entries for row in seen))
        self.assertIsNone(seen[0][2])
        self.assertEqual(seen[1][2], observation_payload)
        self.assertEqual(seen[2][2], observation_payload)


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
            "https://example.com/encoded%5cbackslash",
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
            "https://example.com/a%5Cb",
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

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-bound read coverage")
    def test_parent_swap_cannot_redirect_descriptor_bound_read(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            parent = root / "safe"
            parent.mkdir()
            (parent / "source.txt").write_bytes(b"safe")
            outside = root / "outside"
            outside.mkdir()
            (outside / "source.txt").write_bytes(b"outside-secret")
            real_open = os.open
            swapped = []

            def swap_component(path, flags, *args, **kwargs):
                if path == "safe" and kwargs.get("dir_fd") is not None and not swapped:
                    parent.rename(root / "original-safe")
                    parent.symlink_to(outside, target_is_directory=True)
                    swapped.append(True)
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(verify.os, "open", side_effect=swap_component):
                with self.assertRaises((verify.VerifyError, OSError)):
                    verify._read_regular_bytes(
                        parent / "source.txt", 1024, boundary=root
                    )
            self.assertEqual(swapped, [True])

    @unittest.skipUnless(os.name == "nt", "Windows handle-bound read coverage")
    def test_windows_read_uses_handle_bound_opener(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "source.txt"
            path.write_bytes(b"safe")
            real_open = verify._open_windows_regular_file
            with mock.patch.object(
                verify, "_open_windows_regular_file", wraps=real_open
            ) as opener:
                self.assertEqual(
                    verify._read_regular_bytes(path, 1024, boundary=Path(raw)),
                    b"safe",
                )
            opener.assert_called_once()

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
                if path == "nested" and kwargs.get("dir_fd") is not None and not swapped:
                    moved = dist / "original-parent"
                    parent.rename(moved)
                    parent.symlink_to(outside, target_is_directory=True)
                    swapped.append(True)
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.multiple(verify, ROOT=root, DIST=dist):
                with mock.patch.object(verify.os, "open", side_effect=swap_before_parent_open):
                    with self.assertRaises((verify.VerifyError, OSError)):
                        verify.publish_bytes(output, b"payload")
            self.assertEqual(swapped, [True])
            self.assertFalse((outside / "release.zip").exists())
            safe_publish = dist / "original-parent" / "release.zip"
            self.assertFalse(safe_publish.exists())

    @unittest.skipIf(os.name == "nt", "POSIX descriptor identity coverage")
    def test_temp_entry_swap_is_detected_before_publish_success(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            output = dist / "release.zip"
            real_rename = os.rename
            swapped = []

            def replace_temp_before_rename(source, destination, *args, **kwargs):
                if not swapped:
                    source_fd = kwargs["src_dir_fd"]
                    os.unlink(source, dir_fd=source_fd)
                    replacement = os.open(
                        source,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=source_fd,
                    )
                    try:
                        os.write(replacement, b"wrong")
                    finally:
                        os.close(replacement)
                    swapped.append(True)
                return real_rename(source, destination, *args, **kwargs)

            with mock.patch.multiple(verify, ROOT=root, DIST=dist), \
                    mock.patch.object(
                        verify.os, "rename", side_effect=replace_temp_before_rename
                    ):
                with self.assertRaisesRegex(
                    verify.VerifyError, "identity differs"
                ):
                    verify.publish_bytes(output, b"expected")
            self.assertEqual(swapped, [True])

    @unittest.skipUnless(os.name == "nt", "Windows direct-handle publish coverage")
    def test_windows_publish_uses_final_create_new_handle(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dist = root / "dist"
            dist.mkdir()
            output = dist / "release.zip"
            with mock.patch.multiple(verify, ROOT=root, DIST=dist), \
                    mock.patch.object(
                        verify.tempfile,
                        "mkstemp",
                        side_effect=AssertionError("Windows temp path publish is forbidden"),
                    ):
                verify.publish_bytes(output, b"expected")
                verify.publish_bytes(output, b"expected")
                with self.assertRaisesRegex(
                    verify.VerifyError, "different bytes"
                ):
                    verify.publish_bytes(output, b"different")
            self.assertEqual(output.read_bytes(), b"expected")


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
                "native process API|unapproved ctypes attribute",
            ),
        )
        for source, pattern in cases:
            with self.subTest(source=source):
                self.assert_boundary_rejects(source, pattern)

    def test_computed_getattr_and_alias_dataflow_bypasses_are_rejected(self):
        cases = (
            (
                "import os\nprefix = 'sys'\nmember = prefix + 'tem'\n"
                "lookup = getattr\nrun = lookup(os, member)\nrun('command')\n",
                "dangerous callable|process API",
            ),
            (
                "import os\ndef choose():\n    return 'system'\n"
                "run = getattr(os, choose())\n",
                "dynamic getattr",
            ),
            (
                "import ctypes\nkernel = ctypes.WinDLL('kernel32')\n"
                "member = 'Create' + 'ProcessW'\ncreate = getattr(kernel, member)\n",
                "native process API|unapproved native API|exactly load kernel32",
            ),
            (
                "import os\nrun = len\nrun = os.system\nrun('command')\n",
                "ambiguous|dangerous callable|process API",
            ),
            (
                "import os\nrun = os.__dict__['sys' + 'tem']\nrun('command')\n",
                "reflective",
            ),
            (
                "import os\nrun = vars(os)['system']\nrun('command')\n",
                "reflective",
            ),
        )
        for source, pattern in cases:
            with self.subTest(source=source):
                self.assert_boundary_rejects(source, pattern)

    def test_ctypes_exports_are_an_exact_kernel32_allowlist(self):
        allowed = (
            "import ctypes\n"
            "kernel = ctypes.WinDLL('kernel32', use_last_error=True)\n"
            "create_file = kernel.CreateFileW\n"
            "get_info = kernel.GetFileInformationByHandle\n"
            "get_path = kernel.GetFinalPathNameByHandleW\n"
            "close = kernel.CloseHandle\n"
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            processor = root / "processor.py"
            processor.write_text(allowed, encoding="utf-8")
            with mock.patch.multiple(verify, ROOT=root, PROCESSOR=processor):
                verify.validate_processor_boundary()

    def test_release_snapshot_requires_exact_reviewed_processor_bytes(self):
        entries = verify._snapshot_package_sources()
        processor_name = (
            "skills/organize-screenshot-inbox/scripts/screenshot_inbox.py"
        )
        changed = dict(entries)
        changed[processor_name] = entries[processor_name] + b"\n# drift\n"
        with self.assertRaisesRegex(
            verify.VerifyError, "reviewed release boundary"
        ):
            verify.validate_processor_boundary(changed)
        for export in ("CreateProcessW", "WinHttpOpen", "DeleteFileW"):
            source = (
                "import ctypes\n"
                "kernel = ctypes.WinDLL('kernel32', use_last_error=True)\n"
                "call = kernel.%s\n" % export
            )
            with self.subTest(export=export):
                self.assert_boundary_rejects(
                    source, "native process API|unapproved native API|unapproved kernel32 export"
                )

    def test_ctypes_allowlist_rejects_reviewed_boundary_bypasses(self):
        cases = (
            (
                "import ctypes\nctypes.pythonapi.PyRun_SimpleString(b'pass')\n",
                "unapproved ctypes attribute",
            ),
            (
                "import ctypes\nmember = 'python' + 'api'\n"
                "api = getattr(ctypes, member)\napi.PyRun_SimpleString(b'pass')\n",
                "ctypes module|unapproved ctypes reference",
            ),
            (
                "import ctypes\nkernel = len\n"
                "kernel = ctypes.WinDLL('kernel32', use_last_error=True)\n"
                "kernel.CreateProcessW(None)\n",
                "exactly one binding",
            ),
            (
                "import ctypes\n"
                "kernel = [ctypes.WinDLL('kernel32', use_last_error=True)][0]\n"
                "kernel.CreateProcessW(None)\n",
                "direct assignment value",
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
    COMMIT = "a" * 40

    def identity(self, job_id):
        return {
            "job_id": job_id,
            **verify.EXPECTED_MATRIX[job_id],
            "commit_sha": self.COMMIT,
        }

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

    def write_matrix(self, matrix, payload=b"archive"):
        matrix.mkdir()
        for job_id in verify.EXPECTED_MATRIX:
            self.write_bundle(
                matrix / ("release-%s" % job_id),
                payload,
                runtime_report(self.identity(job_id), payload),
            )

    def matrix_patches(self, report, publish):
        return (
            mock.patch.object(verify, "validate_source"),
            mock.patch.object(verify, "_snapshot_package_sources", return_value={}),
            mock.patch.object(verify, "_runtime_evidence_report", return_value=report),
            mock.patch.object(verify, "_validate_archive_bytes"),
            mock.patch.object(verify, "_smoke_archive"),
            mock.patch.object(verify, "_prepare_dist"),
            mock.patch.object(
                verify, "_assert_dist_output", side_effect=lambda path: Path(path)
            ),
            mock.patch.object(verify, "publish_bytes", publish),
            mock.patch.dict(os.environ, {"SAI_COMMIT_SHA": self.COMMIT}),
            mock.patch.object(
                verify, "_current_git_commit", return_value=self.COMMIT
            ),
        )

    def run_compare(self, matrix, report, publish):
        patches = self.matrix_patches(report, publish)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5], patches[6], patches[7], patches[8], patches[9]:
            return verify.compare_matrix(matrix, len(verify.EXPECTED_MATRIX))

    def test_matrix_rejects_missing_or_extra_bundle_files(self):
        payload = b"archive"
        report = runtime_report(archive_payload=payload)
        for mode in ("missing", "extra"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as raw:
                matrix = Path(raw) / "matrix"
                self.write_matrix(matrix, payload)
                bundle = matrix / "release-ubuntu-py39"
                if mode == "missing":
                    (bundle / "runtime-artifact-evidence.json").unlink()
                else:
                    (bundle / "unexpected.txt").write_text("extra", encoding="utf-8")
                publish = mock.Mock()
                with self.assertRaises(verify.VerifyError):
                    self.run_compare(matrix, report, publish)
                publish.assert_not_called()

    def test_matrix_validates_every_bundle_before_any_publish(self):
        payload = b"archive"
        report = runtime_report(archive_payload=payload)
        with tempfile.TemporaryDirectory() as raw:
            matrix = Path(raw) / "matrix"
            self.write_matrix(matrix, payload)
            checksum = (
                matrix / "release-windows-py314" / (verify.ARCHIVE_NAME + ".sha256")
            )
            checksum.write_bytes(b"wrong\n")
            publish = mock.Mock()
            with self.assertRaisesRegex(verify.VerifyError, "checksum"):
                self.run_compare(matrix, report, publish)
            publish.assert_not_called()

    def test_matrix_requires_exact_one_level_job_set_and_provenance(self):
        payload = b"archive"
        report = runtime_report(archive_payload=payload)
        for mutation in ("extra-root", "wrong-provenance"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as raw:
                matrix = Path(raw) / "matrix"
                self.write_matrix(matrix, payload)
                if mutation == "extra-root":
                    (matrix / "copied-job").mkdir()
                else:
                    evidence = matrix / "release-ubuntu-py39" / "runtime-artifact-evidence.json"
                    bad = runtime_report(self.identity("ubuntu-py310"), payload)
                    evidence.write_bytes(verify._runtime_evidence_bytes(bad))
                publish = mock.Mock()
                with self.assertRaisesRegex(verify.VerifyError, "exact expected|artifact directory"):
                    self.run_compare(matrix, report, publish)
                publish.assert_not_called()

    def test_matrix_never_uses_recursive_path_discovery_and_reports_identities(self):
        payload = b"archive"
        report = runtime_report(archive_payload=payload)
        with tempfile.TemporaryDirectory() as raw:
            matrix = Path(raw) / "matrix"
            self.write_matrix(matrix, payload)
            published = {}

            def capture(path, value, mode=0o644):
                published[Path(path).name] = value

            with mock.patch.object(
                Path, "rglob", side_effect=AssertionError("rglob must not be used")
            ):
                self.run_compare(matrix, report, capture)
            final = json.loads(published["cross-platform-reproducibility.json"])
            self.assertEqual(final["commit_sha"], self.COMMIT)
            self.assertEqual(
                [row["job_id"] for row in final["matrix_identities"]],
                sorted(verify.EXPECTED_MATRIX),
            )

    @unittest.skipIf(os.name == "nt", "POSIX symlink regression")
    def test_matrix_rejects_symlink_before_bundle_enumeration(self):
        payload = b"archive"
        report = runtime_report(archive_payload=payload)
        with tempfile.TemporaryDirectory() as raw:
            matrix = Path(raw) / "matrix"
            self.write_matrix(matrix, payload)
            target = matrix / "release-ubuntu-py39"
            moved = matrix / "real-ubuntu-py39"
            target.rename(moved)
            target.symlink_to(moved, target_is_directory=True)
            publish = mock.Mock()
            with self.assertRaisesRegex(verify.VerifyError, "symlink|reparse"):
                self.run_compare(matrix, report, publish)
            publish.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_one_level_enumerator_rejects_windows_junction(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            matrix = root / "matrix"
            matrix.mkdir()
            outside = root / "outside"
            outside.mkdir()
            junction = matrix / "release-ubuntu-py39"
            result = os.spawnv(
                os.P_WAIT,
                os.environ.get("COMSPEC", "cmd.exe"),
                ["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)],
            )
            if result != 0:
                self.skipTest("Windows junction creation is unavailable")
            with self.assertRaisesRegex(verify.VerifyError, "reparse|link"):
                verify._enumerate_plain_directory(matrix)

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
