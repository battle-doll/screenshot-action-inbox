import ast
import copy
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "screenshot-action-inbox"
SCRIPT = (
    PLUGIN_ROOT
    / "skills"
    / "organize-screenshot-inbox"
    / "scripts"
    / "screenshot_inbox.py"
)
FIXTURE = ROOT / "tests" / "fixtures" / "observations.json"

SPEC = importlib.util.spec_from_file_location("screenshot_inbox", SCRIPT)
inbox = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(inbox)


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class ObservationTests(unittest.TestCase):
    def test_valid_fixture_builds_all_artifacts(self):
        data = inbox.validate_observations(load_fixture())
        artifacts = inbox.build_artifacts(data)
        self.assertEqual(
            set(artifacts),
            {"weekly-digest.md", "actions.csv", "calendar.ics", "archive-plan.json", "receipt.json"},
        )
        receipt = json.loads(artifacts["receipt.json"])
        self.assertEqual(receipt["counts"]["sources"], 3)
        self.assertEqual(receipt["counts"]["items"], 4)
        self.assertEqual(receipt["counts"]["calendar_drafts"], 1)
        self.assertEqual(receipt["side_effects"]["network_requests"], 0)
        self.assertEqual(receipt["side_effects"]["source_files_changed"], 0)

    def test_reordered_object_keys_are_deterministic(self):
        original = load_fixture()
        reordered = {key: original[key] for key in reversed(list(original))}
        first = inbox.build_artifacts(inbox.validate_observations(original))
        second = inbox.build_artifacts(inbox.validate_observations(reordered))
        self.assertEqual(first, second)

    def test_unknown_source_is_rejected(self):
        value = load_fixture()
        value["items"][0]["source_ids"] = ["src-missing"]
        with self.assertRaisesRegex(inbox.InboxError, "unknown source"):
            inbox.validate_observations(value)

    def test_unknown_fields_are_rejected(self):
        value = load_fixture()
        value["items"][0]["execute"] = "delete-all"
        with self.assertRaisesRegex(inbox.InboxError, "unsupported fields"):
            inbox.validate_observations(value)

    def test_secret_and_card_patterns_are_rejected(self):
        value = load_fixture()
        value["items"][0]["details"] = "password: hunter2"
        with self.assertRaisesRegex(inbox.InboxError, "secret"):
            inbox.validate_observations(value)
        value = load_fixture()
        value["items"][0]["details"] = "Card 4111 1111 1111 1111"
        with self.assertRaisesRegex(inbox.InboxError, "payment-card"):
            inbox.validate_observations(value)
        value = load_fixture()
        value["items"][0]["details"] = "Card 4111\u00a01111\u20091111\u202f1111"
        with self.assertRaisesRegex(inbox.InboxError, "payment-card"):
            inbox.validate_observations(value)
        value = load_fixture()
        value["sources"][0]["relative_path"] = "Card 4111 1111 1111 1111.png"
        with self.assertRaisesRegex(inbox.InboxError, "payment-card"):
            inbox.validate_observations(value)
        value = load_fixture()
        value["items"][2]["amount"]["value"] = "4111111111111111"
        with self.assertRaisesRegex(inbox.InboxError, "payment-card"):
            inbox.validate_observations(value)

    def test_naive_calendar_time_is_rejected(self):
        value = load_fixture()
        value["items"][1]["calendar"]["start"] = "2026-08-20T18:30:00"
        with self.assertRaisesRegex(inbox.InboxError, "explicit"):
            inbox.validate_observations(value)

    def test_noncanonical_datetime_forms_are_rejected(self):
        bad_values = [
            "2026-08-20 18:30:00+09:00",
            "2026-08-20T18:30+09:00",
            "2026-08-20T18:30:00.123+09:00",
            "2026-W34-4T18:30:00+09:00",
            "2026-08-20T18:30:00+24:00",
            "2026-08-20T18:30:00-00:00",
        ]
        for bad in bad_values:
            value = load_fixture()
            value["items"][1]["calendar"]["start"] = bad
            with self.subTest(value=bad):
                with self.assertRaises(inbox.InboxError):
                    inbox.validate_observations(value)

    def test_calendar_end_must_be_after_start(self):
        value = load_fixture()
        value["items"][1]["calendar"]["end"] = "2026-08-20T17:00:00+09:00"
        with self.assertRaisesRegex(inbox.InboxError, "after start"):
            inbox.validate_observations(value)

    def test_redaction_status_remains_visible_without_secrets(self):
        value = load_fixture()
        value["sources"][2]["status"] = "redaction_required"
        data = inbox.validate_observations(value)
        receipt = json.loads(inbox.build_artifacts(data)["receipt.json"])
        self.assertIn("SOURCE_REVIEW_INCOMPLETE", receipt["warnings"])

    def test_archive_requires_reviewed_hashed_source(self):
        value = load_fixture()
        value["sources"][0]["sha256"] = None
        with self.assertRaisesRegex(inbox.InboxError, "SHA-256"):
            inbox.validate_observations(value)
        value = load_fixture()
        value["sources"][0]["status"] = "unreadable"
        with self.assertRaisesRegex(inbox.InboxError, "fully reviewed"):
            inbox.validate_observations(value)

    def test_uncertainty_invariants_are_enforced(self):
        value = load_fixture()
        value["items"][0]["confidence"] = "low"
        with self.assertRaisesRegex(inbox.InboxError, "low-confidence"):
            inbox.validate_observations(value)
        value = load_fixture()
        value["items"][1]["status"] = "needs_review"
        with self.assertRaisesRegex(inbox.InboxError, "cannot create"):
            inbox.validate_observations(value)
        value = load_fixture()
        value["items"][2]["calendar"] = {
            "status": "draft",
            "start": "2026-08-20",
            "end": None,
            "location": None,
        }
        with self.assertRaisesRegex(inbox.InboxError, "limited"):
            inbox.validate_observations(value)
        value = load_fixture()
        value["items"][1]["due"] = "2026-08-21T18:30:00+09:00"
        with self.assertRaisesRegex(inbox.InboxError, "must match"):
            inbox.validate_observations(value)


class PathTests(unittest.TestCase):
    def test_korean_relative_path_is_accepted(self):
        self.assertEqual(inbox.validate_relative_path("회의/일정 이미지.png"), "회의/일정 이미지.png")

    def test_cross_platform_unsafe_paths_are_rejected(self):
        bad = [
            "/tmp/x.png", "../x.png", "a/../../x.png", "a\\b.png", "C:/x.png",
            "C:\\x.png", "C:x.png", "//server/share/x.png", "safe.txt:$DATA",
            "CON.png", "folder/NUL.json", "folder/trailing. ", "a//b.png",
            "folder/`code`.png",
        ]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(inbox.InboxError):
                    inbox.validate_relative_path(value)

    def test_case_and_unicode_collisions_are_rejected(self):
        value = load_fixture()
        duplicate = copy.deepcopy(value["sources"][0])
        duplicate["id"] = "src-004"
        duplicate["relative_path"] = value["sources"][0]["relative_path"].upper()
        value["sources"].append(duplicate)
        with self.assertRaisesRegex(inbox.InboxError, "collide"):
            inbox.validate_observations(value)


class CsvAndIcsTests(unittest.TestCase):
    def test_csv_neutralizes_formulas_but_keeps_numeric_amount(self):
        value = load_fixture()
        value["items"][0]["title"] = "\ufeff\u200b \t=HYPERLINK(\"https://invalid.example\")"
        data = inbox.validate_observations(value)
        rows = list(csv.DictReader(io.StringIO(inbox.build_csv(data).decode("utf-8"))))
        self.assertTrue(rows[0]["title"].startswith("'"))
        receipt_row = next(row for row in rows if row["item_id"] == "item-003")
        self.assertEqual(receipt_row["amount_value"], "12900")

    def test_markdown_escapes_html_and_table_delimiters(self):
        value = load_fixture()
        value["batch_title"] = "# [remote](https://invalid.example)"
        value["items"][0]["title"] = "<script>alert(1)</script> | ![pixel](https://invalid.example)"
        data = inbox.validate_observations(value)
        digest = inbox.build_digest(data).decode("utf-8")
        self.assertIn(r"&lt;script&gt;alert\(1\)&lt;/script&gt; \| \!\[pixel\]", digest)
        self.assertNotIn("<script>", digest)
        self.assertNotIn("](https://invalid.example)", digest)

    def test_csv_source_provenance_is_unambiguous_json(self):
        value = load_fixture()
        value["sources"][0]["relative_path"] = "a.png; b.png"
        value["sources"][1]["relative_path"] = "c.png"
        value["items"][0]["source_ids"] = ["src-001", "src-002"]
        data = inbox.validate_observations(value)
        rows = list(csv.DictReader(io.StringIO(inbox.build_csv(data).decode("utf-8"))))
        row = next(item for item in rows if item["item_id"] == "item-001")
        self.assertEqual(json.loads(row["source_ids_json"]), ["src-001", "src-002"])
        self.assertEqual(json.loads(row["source_files_json"]), ["a.png; b.png", "c.png"])

    def test_ics_uses_crlf_and_utc(self):
        data = inbox.validate_observations(load_fixture())
        payload = inbox.build_ics(data)
        self.assertTrue(payload.endswith(b"\r\n"))
        self.assertNotIn(b"\n", payload.replace(b"\r\n", b""))
        text = payload.decode("utf-8")
        self.assertIn("DTSTART:20260820T093000Z", text)
        self.assertIn("DTEND:20260820T110000Z", text)
        self.assertIn("DTSTAMP:20260813T000000Z", text)
        self.assertNotIn("METHOD:", text)
        self.assertNotIn("ATTENDEE:", text)
        self.assertNotIn("ORGANIZER:", text)
        self.assertNotIn("VALARM", text)

    def test_ics_property_injection_is_escaped(self):
        value = load_fixture()
        value["items"][1]["title"] = "Meeting\nATTENDEE:mailto:attacker@example.test"
        data = inbox.validate_observations(value)
        text = inbox.build_ics(data).decode("utf-8")
        self.assertIn("SUMMARY:Meeting\\nATTENDEE:mailto:attacker@example.test", text)
        self.assertNotIn("\r\nATTENDEE:", text)

    def test_ics_lines_fold_at_75_octets_without_breaking_utf8(self):
        value = load_fixture()
        value["items"][1]["title"] = "긴 일정 제목 " * 30
        data = inbox.validate_observations(value)
        payload = inbox.build_ics(data)
        payload.decode("utf-8")
        for line in payload.split(b"\r\n"):
            self.assertLessEqual(len(line), 75)

    def test_all_day_event_gets_exclusive_default_end(self):
        value = load_fixture()
        event = value["items"][1]
        event["calendar"] = {
            "status": "draft",
            "start": "2026-08-20",
            "end": None,
            "location": "Community Hall",
        }
        event["due"] = "2026-08-20"
        data = inbox.validate_observations(value)
        text = inbox.build_ics(data).decode("utf-8")
        self.assertIn("DTSTART;VALUE=DATE:20260820", text)
        self.assertIn("DTEND;VALUE=DATE:20260821", text)

    def test_ics_uid_uses_source_identity_but_ignores_mutable_event_text(self):
        first = load_fixture()
        first_data = inbox.validate_observations(first)
        first_uid = next(line for line in inbox.build_ics(first_data).decode("utf-8").split("\r\n") if line.startswith("UID:"))
        edited = load_fixture()
        edited["items"][1]["title"] = "Edited title"
        edited_data = inbox.validate_observations(edited)
        edited_uid = next(line for line in inbox.build_ics(edited_data).decode("utf-8").split("\r\n") if line.startswith("UID:"))
        self.assertEqual(first_uid, edited_uid)
        unrelated = load_fixture()
        unrelated["sources"][0]["relative_path"] = "different/source.png"
        unrelated_data = inbox.validate_observations(unrelated)
        unrelated_uid = next(line for line in inbox.build_ics(unrelated_data).decode("utf-8").split("\r\n") if line.startswith("UID:"))
        self.assertNotEqual(first_uid, unrelated_uid)

    def test_calendar_without_drafts_uses_non_event_extension_component(self):
        value = load_fixture()
        value["items"][1]["calendar"] = {"status": "needs_review", "location": None}
        value["items"][1]["status"] = "needs_review"
        data = inbox.validate_observations(value)
        artifacts = inbox.build_artifacts(data)
        text = artifacts["calendar.ics"].decode("utf-8")
        self.assertNotIn("BEGIN:VEVENT", text)
        self.assertIn("BEGIN:X-SAI-NO-EVENTS", text)
        receipt = json.loads(artifacts["receipt.json"])
        self.assertIn("NO_EXPORTABLE_CALENDAR_DRAFTS", receipt["warnings"])


class FilesystemTests(unittest.TestCase):
    def test_atomic_output_and_idempotent_rerun(self):
        data = inbox.validate_observations(load_fixture())
        artifacts = inbox.build_artifacts(data)
        with tempfile.TemporaryDirectory(prefix="sai-test-") as raw:
            output = Path(raw) / "result"
            self.assertEqual(inbox.write_artifacts(output, artifacts), "CREATED")
            self.assertEqual(inbox.write_artifacts(output, artifacts), "UNCHANGED")
            (output / "weekly-digest.md").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(inbox.InboxError, "different"):
                inbox.write_artifacts(output, artifacts)

    def test_inventory_ignores_non_images_and_does_not_hash_by_default(self):
        with tempfile.TemporaryDirectory(prefix="sai-inventory-") as raw:
            root = Path(raw)
            (root / "스크린샷.png").write_bytes(b"not-an-image-but-never-opened")
            (root / "notes.txt").write_text("ignore", encoding="utf-8")
            result = inbox.inventory(root)
            self.assertEqual(len(result["sources"]), 1)
            self.assertNotIn("sha256", result["sources"][0])
            self.assertFalse(result["image_contents_opened"])
            self.assertFalse(result["exif_read"])

    def test_inventory_hashes_only_when_explicit(self):
        with tempfile.TemporaryDirectory(prefix="sai-inventory-hash-") as raw:
            root = Path(raw)
            payload = b"synthetic screenshot bytes"
            (root / "screen.png").write_bytes(payload)
            result = inbox.inventory(root, include_hash=True)
            self.assertTrue(result["image_contents_opened"])
            self.assertEqual(result["sources"][0]["sha256"], inbox._hash_bytes(payload))
            self.assertFalse(result["exif_read"])

    def test_inventory_redacts_sensitive_filenames(self):
        with tempfile.TemporaryDirectory(prefix="sai-sensitive-name-") as raw:
            root = Path(raw)
            (root / "Card 4111 1111 1111 1111.png").write_bytes(b"synthetic")
            result = inbox.inventory(root)
            self.assertEqual(result["sources"], [])
            self.assertEqual(result["skipped"][0]["reason"], "SENSITIVE_FILENAME")
            self.assertNotIn("4111", result["skipped"][0]["relative_path"])

    def test_inventory_skips_symlink_when_supported(self):
        with tempfile.TemporaryDirectory(prefix="sai-inventory-") as raw:
            root = Path(raw)
            target = root / "real.png"
            target.write_bytes(b"x")
            link = root / "linked.png"
            try:
                link.symlink_to(target.name)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            result = inbox.inventory(root)
            self.assertEqual(len(result["sources"]), 1)
            self.assertEqual(result["skipped"][0]["reason"], "LINK_OR_REPARSE_POINT")

    @unittest.skipUnless(os.name == "nt", "Windows junction coverage")
    def test_windows_junction_root_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="sai-junction-") as raw:
            root = Path(raw)
            target = root / "target"
            junction = root / "junction"
            target.mkdir()
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            try:
                with self.assertRaisesRegex(inbox.InboxError, "reparse"):
                    inbox.inventory(junction)
            finally:
                os.rmdir(str(junction))

    def test_dangling_output_links_are_never_replaced(self):
        data = inbox.validate_observations(load_fixture())
        artifacts = inbox.build_artifacts(data)
        with tempfile.TemporaryDirectory(prefix="sai-output-link-") as raw:
            root = Path(raw)
            dangling_dir = root / "result"
            dangling_file = root / "inventory.json"
            try:
                dangling_dir.symlink_to("missing-dir", target_is_directory=True)
                dangling_file.symlink_to("missing-file")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(inbox.InboxError, "already exists"):
                inbox.write_artifacts(dangling_dir, artifacts)
            with self.assertRaisesRegex(inbox.InboxError, "overwrite"):
                inbox.write_single_file(dangling_file, b"data")
            self.assertTrue(dangling_dir.is_symlink())
            self.assertTrue(dangling_file.is_symlink())

    def test_cli_runs_under_unicode_space_path(self):
        with tempfile.TemporaryDirectory(prefix="스크린샷 테스트 ") as raw:
            base = Path(raw)
            source = base / "observations.json"
            source.write_text(json.dumps(load_fixture(), ensure_ascii=False), encoding="utf-8")
            output = base / "결과 폴더"
            result = subprocess.run(
                [sys.executable, "-S", "-X", "utf8", str(SCRIPT), "build", str(source), "--out", str(output)],
                cwd=base,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env={**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONPATH": ""},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "receipt.json").is_file())


class ParserAndDependencyTests(unittest.TestCase):
    def test_duplicate_json_key_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="sai-json-") as raw:
            path = Path(raw) / "duplicate.json"
            path.write_text('{"schema_version":"1.0","schema_version":"1.0"}', encoding="utf-8")
            with self.assertRaisesRegex(inbox.InboxError, "duplicate JSON key"):
                inbox.load_json_strict(path)

    def test_unpaired_unicode_surrogate_is_rejected(self):
        value = load_fixture()
        value["items"][0]["title"] = "bad\ud800value"
        with self.assertRaisesRegex(inbox.InboxError, "surrogate"):
            inbox.validate_observations(value)

    def test_crlf_json_parses_identically(self):
        original = FIXTURE.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="sai-json-") as raw:
            lf = Path(raw) / "lf.json"
            crlf = Path(raw) / "crlf.json"
            lf.write_bytes(original.replace("\r\n", "\n").encode("utf-8"))
            crlf.write_bytes(original.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
            self.assertEqual(inbox.load_json_strict(lf), inbox.load_json_strict(crlf))

    def test_shipped_processor_has_no_network_or_process_imports(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertTrue(roots.isdisjoint({"socket", "urllib", "http", "requests", "httpx", "subprocess"}))


if __name__ == "__main__":
    unittest.main()
