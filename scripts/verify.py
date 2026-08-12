#!/usr/bin/env python3
"""Validate, test, reproducibly package, and compare Screenshot Action Inbox."""

from __future__ import print_function

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "screenshot-action-inbox"
MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
SKILL_ROOT = PLUGIN_ROOT / "skills" / "organize-screenshot-inbox"
PROCESSOR = SKILL_ROOT / "scripts" / "screenshot_inbox.py"
VERSION = "1.0.0"
PACKAGE_NAME = "screenshot-action-inbox"
ARCHIVE_NAME = "%s-skills-only-%s.zip" % (PACKAGE_NAME, VERSION)
DIST = ROOT / "dist"

PACKAGE_SOURCES = {
    ".codex-plugin/plugin.json": MANIFEST_PATH,
    "assets/icon.png": PLUGIN_ROOT / "assets" / "icon.png",
    "assets/logo.png": PLUGIN_ROOT / "assets" / "logo.png",
    "LICENSE": ROOT / "LICENSE",
    "NOTICE": ROOT / "NOTICE",
    "PRIVACY.md": ROOT / "PRIVACY.md",
    "SUPPORT.md": ROOT / "SUPPORT.md",
    "TERMS.md": ROOT / "TERMS.md",
    "skills/organize-screenshot-inbox/SKILL.md": SKILL_ROOT / "SKILL.md",
    "skills/organize-screenshot-inbox/agents/openai.yaml": SKILL_ROOT / "agents" / "openai.yaml",
    "skills/organize-screenshot-inbox/references/observation-schema.md": SKILL_ROOT / "references" / "observation-schema.md",
    "skills/organize-screenshot-inbox/references/privacy-and-safety.md": SKILL_ROOT / "references" / "privacy-and-safety.md",
    "skills/organize-screenshot-inbox/scripts/screenshot_inbox.py": PROCESSOR,
}

TEXT_SOURCE_PATHS = [
    ROOT / ".gitattributes",
    ROOT / ".gitignore",
    ROOT / "CHANGELOG.md",
    ROOT / "LICENSE",
    ROOT / "NOTICE",
    ROOT / "PRIVACY.md",
    ROOT / "README.md",
    ROOT / "SECURITY.md",
    ROOT / "SBOM.spdx.json",
    ROOT / "SUBMISSION.md",
    ROOT / "SUPPORT.md",
    ROOT / "TERMS.md",
    ROOT / "THIRD_PARTY_NOTICES.md",
    ROOT / "THREAT_MODEL.md",
    ROOT / "TRADEMARKS.md",
    ROOT / ".agents" / "plugins" / "marketplace.json",
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / "evals" / "cases.json",
    ROOT / "scripts" / "verify.py",
    ROOT / "tests" / "fixtures" / "observations.json",
    ROOT / "tests" / "reviewer-fixtures" / "README.md",
    *sorted((ROOT / "tests" / "reviewer-fixtures").glob("*.svg")),
    ROOT / "tests" / "test_screenshot_inbox.py",
    *[path for path in PACKAGE_SOURCES.values() if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}],
]

FORBIDDEN_SKILLS_ONLY_KEYS = {"apps", "mcpServers", "hooks"}
FORBIDDEN_IMPORTS = {
    "socket", "urllib", "http", "ftplib", "smtplib", "requests", "httpx",
    "aiohttp", "subprocess", "webbrowser"
}
ALLOWED_CATEGORIES = {
    "Productivity", "Creativity", "Developer Tools", "Business & Operations",
    "Data & Analytics", "Communication", "Education & Research", "Security",
    "Finance", "Healthcare", "Travel", "Entertainment", "Other"
}
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$",
    *("COM%d" % number for number in range(1, 10)),
    *("LPT%d" % number for number in range(1, 10)),
}


class VerifyError(AssertionError):
    pass


def fail(message):
    raise VerifyError(message)


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _assert_regular_file(path):
    if not path.is_file() or path.is_symlink():
        fail("required path is not a regular non-symlink file: %s" % path)
    result = path.lstat()
    attributes = getattr(result, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if attributes & reparse:
        fail("required path is a Windows reparse point: %s" % path)


def _png_dimensions(path):
    payload = path.read_bytes()
    if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[12:16] != b"IHDR":
        fail("branding asset is not a valid PNG header: %s" % path)
    return struct.unpack(">II", payload[16:24])


def validate_archive_name(name):
    if not name or name.startswith(("/", "\\")) or "\\" in name:
        fail("archive path must be relative POSIX form: %r" % name)
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute() or PureWindowsPath(name).drive:
        fail("archive path is absolute or drive-qualified: %s" % name)
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        fail("archive path has an unsafe segment: %s" % name)
    for part in parts:
        if any(ord(char) < 32 or ord(char) == 127 for char in part):
            fail("archive path has a control character: %s" % name)
        if any(char in part for char in '<>:"|?*') or part.endswith((".", " ")):
            fail("archive path is not cross-platform safe: %s" % name)
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED:
            fail("archive path has a Windows reserved component: %s" % name)


def validate_text_files():
    unique = []
    seen = set()
    for path in TEXT_SOURCE_PATHS:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    for path in unique:
        _assert_regular_file(path)
        payload = path.read_bytes()
        if payload.startswith(b"\xef\xbb\xbf"):
            fail("UTF-8 BOM is not allowed: %s" % path)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            fail("text file is not UTF-8: %s (%s)" % (path, exc))
        if "\r" in text:
            fail("text file must use LF line endings: %s" % path)
        if payload and not payload.endswith(b"\n"):
            fail("text file must end with LF: %s" % path)
        if ("[" + "TODO:") in text or ("TODO" + "]") in text:
            fail("submission placeholder remains: %s" % path)


def validate_manifest():
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail("manifest is invalid JSON: %s" % exc)
    if manifest.get("name") != PACKAGE_NAME:
        fail("manifest name mismatch")
    if manifest.get("version") != VERSION or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", VERSION):
        fail("manifest version must match the release semantic version")
    if not manifest.get("description") or len(manifest["description"]) > 1024:
        fail("manifest description is missing or too long")
    if manifest.get("skills") != "./skills/":
        fail("skills-only manifest must reference ./skills/")
    forbidden = sorted(set(manifest) & FORBIDDEN_SKILLS_ONLY_KEYS)
    if forbidden:
        fail("skills-only manifest contains forbidden keys: %s" % ", ".join(forbidden))
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        fail("manifest interface is required")
    if "screenshots" in interface:
        fail("skills-only manifest must not include screenshots")
    display = interface.get("displayName", "")
    subtitle = interface.get("shortDescription", "")
    long_description = interface.get("longDescription", "")
    developer = interface.get("developerName", "")
    if not display or len(display) > 30 or "\n" in display:
        fail("displayName must be one line and at most 30 characters")
    if not subtitle or len(subtitle) > 30 or "\n" in subtitle:
        fail("shortDescription must be one line and at most 30 characters")
    if not long_description or len(long_description) > 4000:
        fail("longDescription is missing or too long")
    if not developer or len(developer) > 80 or "\n" in developer:
        fail("developerName is missing or too long")
    if manifest.get("author", {}).get("name") != developer:
        fail("author.name and interface.developerName must match")
    if interface.get("category") not in ALLOWED_CATEGORIES:
        fail("unsupported plugin category")
    capabilities = interface.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities or len(capabilities) > 20:
        fail("capabilities must contain 1 to 20 entries")
    if any(not isinstance(item, str) or not item or len(item) > 120 or "\n" in item for item in capabilities):
        fail("capability metadata is invalid")
    prompts = interface.get("defaultPrompt")
    if not isinstance(prompts, list) or not 1 <= len(prompts) <= 3:
        fail("starter prompts must contain 1 to 3 entries")
    normalized_prompts = set()
    for prompt in prompts:
        if not isinstance(prompt, str) or not prompt or len(prompt) > 128 or "\n" in prompt or "@" in prompt:
            fail("starter prompt violates final-submission limits")
        key = " ".join(unicodedata.normalize("NFC", prompt).split()).casefold()
        if key in normalized_prompts:
            fail("starter prompts must be unique")
        normalized_prompts.add(key)
    for field in ("websiteURL", "privacyPolicyURL", "termsOfServiceURL"):
        value = interface.get(field)
        if not isinstance(value, str) or not value.startswith("https://") or len(value) > 1024:
            fail("%s must be a valid HTTPS URL" % field)
    for field in ("composerIcon", "logo"):
        relative = interface.get(field)
        if not isinstance(relative, str) or not relative.startswith("./assets/"):
            fail("%s must point inside ./assets/" % field)
        asset = PLUGIN_ROOT / relative[2:]
        _assert_regular_file(asset)
        if asset.stat().st_size > 5 * 1024 * 1024:
            fail("%s exceeds 5 MiB" % field)
        width, height = _png_dimensions(asset)
        if width != height or not 48 <= width <= 4096:
            fail("%s must be square and 48 to 4096 pixels" % field)
    if manifest.get("license") != "Apache-2.0":
        fail("license must be Apache-2.0")
    return manifest


def validate_skill():
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n(.+)\Z", text, flags=re.DOTALL)
    if not match:
        fail("SKILL.md must contain YAML frontmatter and a nonempty body")
    fields = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            fail("SKILL.md frontmatter contains an invalid line")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"\'')
    if set(fields) != {"name", "description"}:
        fail("SKILL.md frontmatter must contain only name and description")
    skill_name = fields["name"]
    if skill_name != "organize-screenshot-inbox" or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_name):
        fail("unexpected skill name")
    if not fields["description"] or len(fields["description"]) > 1024:
        fail("skill description is missing or too long")
    if len(PACKAGE_NAME + ":" + skill_name) > 64:
        fail("qualified skill name exceeds 64 characters")
    if not match.group(2).strip():
        fail("SKILL.md body is empty")
    agent_text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    for marker in ("interface:", "display_name:", "short_description:", "default_prompt:"):
        if marker not in agent_text:
            fail("agents/openai.yaml is missing %s" % marker)
    if "$organize-screenshot-inbox" not in agent_text:
        fail("agents/openai.yaml default prompt must name the skill")


def validate_processor_boundary():
    tree = ast.parse(PROCESSOR.read_text(encoding="utf-8"), filename=str(PROCESSOR))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec", "compile"}:
            fail("processor must not call %s" % node.func.id)
    forbidden = sorted(imports & FORBIDDEN_IMPORTS)
    if forbidden:
        fail("processor imports forbidden network/process modules: %s" % ", ".join(forbidden))


def validate_source():
    for archive_name, path in PACKAGE_SOURCES.items():
        validate_archive_name(archive_name)
        _assert_regular_file(path)
    validate_text_files()
    manifest = validate_manifest()
    validate_skill()
    validate_processor_boundary()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if "## %s - 2026-08-13" % VERSION not in changelog:
        fail("changelog does not contain the current version and date")
    if manifest["homepage"] != "https://github.com/battle-doll/screenshot-action-inbox#readme":
        fail("homepage metadata is unexpected")
    cases = json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))
    if cases.get("plugin") != PACKAGE_NAME or cases.get("version") != VERSION:
        fail("evaluation metadata does not match the release")
    if len(cases.get("positive", [])) != 5 or len(cases.get("negative", [])) != 3:
        fail("review evaluations must contain exactly five positive and three negative cases")
    eval_ids = [case.get("id") for group in ("positive", "negative") for case in cases.get(group, [])]
    if any(not item for item in eval_ids) or len(set(eval_ids)) != len(eval_ids):
        fail("evaluation IDs must be present and unique")
    sbom = json.loads((ROOT / "SBOM.spdx.json").read_text(encoding="utf-8"))
    packages = sbom.get("packages")
    if (
        sbom.get("spdxVersion") != "SPDX-2.3"
        or sbom.get("dataLicense") != "CC0-1.0"
        or sbom.get("name") != "%s-%s" % (PACKAGE_NAME, VERSION)
        or not isinstance(packages, list)
        or len(packages) != 1
        or packages[0].get("name") != PACKAGE_NAME
        or packages[0].get("versionInfo") != VERSION
        or packages[0].get("licenseDeclared") != "Apache-2.0"
    ):
        fail("SBOM metadata does not match the dependency-free release")
    print("source_validation=PASS")


def _zip_info(name):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.internal_attr = 0
    info.extra = b""
    info.comment = b""
    return info


def build_archive(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(output), "w", compression=zipfile.ZIP_STORED, strict_timestamps=True) as archive:
        archive.comment = b""
        for name in sorted(PACKAGE_SOURCES):
            archive.writestr(_zip_info(name), PACKAGE_SOURCES[name].read_bytes())
    return output


def validate_archive(path, smoke=True):
    archive_path = Path(path)
    if not archive_path.is_file() or archive_path.stat().st_size > 100 * 1024 * 1024:
        fail("archive is missing or exceeds 100 MiB")
    with zipfile.ZipFile(str(archive_path), "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if names != sorted(PACKAGE_SOURCES):
            fail("archive entries do not match the exact sorted allowlist")
        if len(names) > 5000:
            fail("archive has too many entries")
        normalized = set()
        total = 0
        for info in infos:
            validate_archive_name(info.filename)
            key = unicodedata.normalize("NFC", info.filename).casefold()
            if key in normalized:
                fail("archive entries collide after normalization")
            normalized.add(key)
            if info.is_dir() or info.file_size > 100 * 1024 * 1024:
                fail("archive contains a directory or oversized entry")
            if info.date_time != (1980, 1, 1, 0, 0, 0):
                fail("archive entry timestamp is not fixed")
            if info.compress_type != zipfile.ZIP_STORED or info.extra or info.comment:
                fail("archive metadata is not deterministic")
            mode = (info.external_attr >> 16) & 0xFFFF
            if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o644:
                fail("archive entry is not a mode-0644 regular file")
            total += info.file_size
        if total > 512 * 1024 * 1024:
            fail("archive uncompressed size exceeds 512 MiB")
        if archive.testzip() is not None:
            fail("archive CRC validation failed")
        for name, source in PACKAGE_SOURCES.items():
            if archive.read(name) != source.read_bytes():
                fail("archive content differs from source: %s" % name)
    if smoke:
        _smoke_archive(archive_path)
    print("archive_validation=PASS path=%s" % archive_path)


def _safe_extract(archive_path, destination):
    with zipfile.ZipFile(str(archive_path), "r") as archive:
        for info in archive.infolist():
            validate_archive_name(info.filename)
            target = destination.joinpath(*info.filename.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(archive.read(info.filename))


def _smoke_archive(archive_path):
    with tempfile.TemporaryDirectory(prefix="sai-extracted-smoke-") as raw:
        root = Path(raw) / "패키지 경로 with spaces"
        root.mkdir()
        _safe_extract(archive_path, root)
        observation = Path(raw) / "observations.json"
        observation.write_bytes((ROOT / "tests" / "fixtures" / "observations.json").read_bytes())
        output = Path(raw) / "검증 결과"
        script = root / "skills" / "organize-screenshot-inbox" / "scripts" / "screenshot_inbox.py"
        result = subprocess.run(
            [sys.executable, "-S", "-X", "utf8", str(script), "build", str(observation), "--out", str(output)],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env={**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONPATH": ""},
        )
        if result.returncode:
            fail("extracted package smoke test failed: %s" % result.stderr.strip())
        if set(path.name for path in output.iterdir()) != {
            "weekly-digest.md", "actions.csv", "calendar.ics", "archive-plan.json", "receipt.json"
        }:
            fail("extracted package smoke output is incomplete")


def run_tests():
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode:
        fail("unit tests failed")
    print("unit_tests=PASS")


def publish_bytes(path, payload, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".%s.tmp-" % path.name, dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(str(temporary), mode)
        os.replace(str(temporary), str(path))
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def build_release():
    validate_source()
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / ARCHIVE_NAME
    with tempfile.TemporaryDirectory(prefix="sai-double-build-", dir=str(DIST)) as raw:
        temp = Path(raw)
        first = build_archive(temp / "first.zip")
        second = build_archive(temp / "second.zip")
        first_bytes = first.read_bytes()
        if first_bytes != second.read_bytes():
            fail("independent builds are not byte-identical")
        validate_archive(first, smoke=True)
    digest = sha256_bytes(first_bytes)
    publish_bytes(output, first_bytes)
    checksum = ("%s  %s\n" % (digest, output.name)).encode("ascii")
    publish_bytes(output.with_suffix(".zip.sha256"), checksum)
    validate_archive(output, smoke=False)
    print("release=%s" % output)
    print("sha256=%s" % digest)
    print("reproducible_builds=2")
    return output


def runtime_evidence():
    validate_source()
    namespace = {}
    exec(compile(PROCESSOR.read_text(encoding="utf-8"), str(PROCESSOR), "exec"), namespace)
    observations = json.loads((ROOT / "tests" / "fixtures" / "observations.json").read_text(encoding="utf-8"))
    data = namespace["validate_observations"](observations)
    artifacts = namespace["build_artifacts"](data)
    report = {
        "schema_version": 1,
        "fixture": "tests/fixtures/observations.json",
        "artifacts": {
            name: {"sha256": sha256_bytes(payload), "bytes": len(payload)}
            for name, payload in sorted(artifacts.items())
        },
    }
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / "runtime-artifact-evidence.json"
    publish_bytes(output, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps(report, sort_keys=True))
    return report


def compare_matrix(root, expect_count):
    candidates = sorted(Path(root).rglob(ARCHIVE_NAME))
    if len(candidates) != expect_count:
        fail("expected %d matrix archives, found %d" % (expect_count, len(candidates)))
    digests = {sha256_file(path) for path in candidates}
    if len(digests) != 1:
        fail("matrix archives are not byte-identical: %s" % sorted(digests))
    evidence_paths = sorted(Path(root).rglob("runtime-artifact-evidence.json"))
    if len(evidence_paths) != expect_count:
        fail("expected %d runtime evidence files, found %d" % (expect_count, len(evidence_paths)))
    evidence_payloads = {path.read_bytes() for path in evidence_paths}
    if len(evidence_payloads) != 1:
        fail("matrix runtime artifact hashes are not byte-identical")
    payload = candidates[0].read_bytes()
    output = DIST / ARCHIVE_NAME
    publish_bytes(output, payload)
    digest = next(iter(digests))
    publish_bytes(output.with_suffix(".zip.sha256"), ("%s  %s\n" % (digest, output.name)).encode("ascii"))
    validate_archive(output, smoke=True)
    report = {
        "status": "PASS",
        "archives_compared": len(candidates),
        "runtime_evidence_compared": len(evidence_paths),
        "runtime_artifacts": json.loads(next(iter(evidence_payloads)).decode("utf-8"))["artifacts"],
        "sha256": digest,
        "archive": output.name,
    }
    publish_bytes(DIST / "cross-platform-reproducibility.json", (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps(report, sort_keys=True))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    try:
        sub.required = True
    except AttributeError:
        pass
    sub.add_parser("validate")
    sub.add_parser("test")
    sub.add_parser("build")
    sub.add_parser("all")
    sub.add_parser("runtime-evidence")
    artifact = sub.add_parser("artifact")
    artifact.add_argument("path")
    compare = sub.add_parser("compare-matrix")
    compare.add_argument("root")
    compare.add_argument("--expect-count", type=int, required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            validate_source()
        elif args.command == "test":
            run_tests()
        elif args.command == "build":
            build_release()
        elif args.command == "artifact":
            validate_archive(Path(args.path), smoke=True)
        elif args.command == "runtime-evidence":
            runtime_evidence()
        elif args.command == "compare-matrix":
            compare_matrix(args.root, args.expect_count)
        elif args.command == "all":
            validate_source()
            run_tests()
            build_release()
            print("all_verification=PASS")
    except (VerifyError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
