#!/usr/bin/env python3
"""Validate, test, reproducibly package, and compare Screenshot Action Inbox."""

from __future__ import print_function

import argparse
import ast
import hashlib
import io
import ipaddress
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
from urllib.parse import urlsplit
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
    ROOT / "tests" / "test_verify.py",
    *[path for path in PACKAGE_SOURCES.values() if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}],
]

FORBIDDEN_SKILLS_ONLY_KEYS = {"apps", "mcpServers", "hooks"}
FORBIDDEN_IMPORTS = {
    "socket", "urllib", "http", "ftplib", "smtplib", "requests", "httpx",
    "aiohttp", "subprocess", "webbrowser"
}
PROCESSOR_ALLOWED_IMPORTS = {
    "__future__", "argparse", "csv", "ctypes", "datetime", "hashlib", "io",
    "json", "msvcrt", "os", "pathlib", "re", "shutil", "stat", "sys",
    "tempfile", "unicodedata",
}
DANGEROUS_OS_CALLS = {
    "fork", "forkpty", "kill", "killpg", "popen", "posix_spawn",
    "posix_spawnp", "startfile", "system",
}
EXPECTED_BRANDING_ASSETS = {
    "composerIcon": ("./assets/icon.png", "assets/icon.png"),
    "logo": ("./assets/logo.png", "assets/logo.png"),
}
EXPECTED_RUNTIME_ARTIFACTS = {
    "weekly-digest.md", "actions.csv", "calendar.ics", "archive-plan.json",
    "receipt.json",
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
    *("COM%s" % number for number in ("¹", "²", "³")),
    *("LPT%s" % number for number in ("¹", "²", "³")),
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


def _is_link_or_reparse(result):
    attributes = getattr(result, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(result.st_mode) or bool(attributes & reparse)


def _lexical_child(path, boundary, label="path"):
    candidate_text = os.path.abspath(os.fspath(path))
    boundary_text = os.path.abspath(os.fspath(boundary))
    try:
        common = os.path.commonpath([candidate_text, boundary_text])
    except ValueError:
        fail("%s is outside the allowed boundary: %s" % (label, path))
    if os.path.normcase(common) != os.path.normcase(boundary_text):
        fail("%s is outside the allowed boundary: %s" % (label, path))
    return Path(candidate_text)


def _assert_no_link_chain(path, boundary, allow_missing_leaf=False):
    candidate = _lexical_child(path, boundary)
    base = Path(os.path.abspath(os.fspath(boundary)))
    relative = candidate.relative_to(base)
    chain = [base]
    current = base
    for part in relative.parts:
        current = current / part
        chain.append(current)
    for index, component in enumerate(chain):
        is_leaf = index == len(chain) - 1
        try:
            result = component.lstat()
        except FileNotFoundError:
            if allow_missing_leaf and is_leaf:
                return candidate
            fail("required path component is missing: %s" % component)
        if _is_link_or_reparse(result):
            fail("path contains a symlink or reparse point: %s" % component)
        if not is_leaf and not stat.S_ISDIR(result.st_mode):
            fail("path component is not a directory: %s" % component)
    return candidate


def _assert_regular_file(path, boundary=None):
    candidate = Path(path)
    if boundary is not None:
        candidate = _assert_no_link_chain(candidate, boundary)
    try:
        result = candidate.lstat()
    except FileNotFoundError:
        fail("required path is missing: %s" % candidate)
    if _is_link_or_reparse(result) or not stat.S_ISREG(result.st_mode):
        fail("required path is not a regular non-link file: %s" % candidate)
    return candidate


def _assert_plain_directory(path, boundary=None):
    candidate = Path(path)
    if boundary is not None:
        candidate = _assert_no_link_chain(candidate, boundary)
    try:
        result = candidate.lstat()
    except FileNotFoundError:
        fail("required directory is missing: %s" % candidate)
    if _is_link_or_reparse(result) or not stat.S_ISDIR(result.st_mode):
        fail("required path is not a plain directory: %s" % candidate)
    return candidate


def _read_regular_bytes(path, maximum, boundary=None):
    candidate = _assert_regular_file(path, boundary=boundary)
    with candidate.open("rb") as handle:
        payload = handle.read(maximum + 1)
    if len(payload) > maximum:
        fail("file exceeds %d bytes: %s" % (maximum, candidate))
    if boundary is not None:
        _assert_regular_file(candidate, boundary=boundary)
    return payload


def _prepare_dist():
    candidate = _lexical_child(DIST, ROOT, label="DIST")
    _assert_plain_directory(ROOT)
    try:
        result = candidate.lstat()
    except FileNotFoundError:
        parent = _assert_plain_directory(candidate.parent, boundary=ROOT)
        if parent != Path(os.path.abspath(os.fspath(ROOT))):
            fail("DIST parent must be the repository root: %s" % parent)
        try:
            os.mkdir(str(candidate), 0o755)
        except FileExistsError:
            pass
    else:
        if _is_link_or_reparse(result) or not stat.S_ISDIR(result.st_mode):
            fail("DIST must be a plain directory: %s" % candidate)
    return _assert_plain_directory(candidate, boundary=ROOT)


def _assert_dist_output(path):
    dist = _prepare_dist()
    candidate = _lexical_child(path, dist, label="output path")
    if candidate == dist:
        fail("output path must name a file inside DIST")
    _assert_plain_directory(candidate.parent, boundary=dist)
    try:
        result = candidate.lstat()
    except FileNotFoundError:
        return candidate
    if _is_link_or_reparse(result) or not stat.S_ISREG(result.st_mode):
        fail("output path must be missing or a regular non-link file: %s" % candidate)
    return candidate


def _strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("JSON contains a duplicate key: %s" % key)
        result[key] = value
    return result


def _reject_json_constant(value):
    fail("JSON contains a non-finite number: %s" % value)


def _load_json_bytes(payload, label):
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail("%s is not UTF-8: %s" % (label, exc))
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except VerifyError:
        raise
    except ValueError as exc:
        fail("%s is invalid JSON: %s" % (label, exc))


def _load_json_file(path, label, maximum=2 * 1024 * 1024):
    payload = _read_regular_bytes(path, maximum, boundary=ROOT)
    return _load_json_bytes(payload, label)


def _png_dimensions(path):
    payload = _read_regular_bytes(path, 5 * 1024 * 1024, boundary=ROOT)
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
        payload = _read_regular_bytes(path, 10 * 1024 * 1024, boundary=ROOT)
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


def _branding_asset_source(field, relative):
    expected, archive_name = EXPECTED_BRANDING_ASSETS[field]
    if relative != expected:
        fail("%s must exactly match the packaged asset %s" % (field, expected))
    asset = PACKAGE_SOURCES.get(archive_name)
    expected_source = PLUGIN_ROOT.joinpath(*archive_name.split("/"))
    if asset is None or Path(asset) != expected_source:
        fail("%s is not bound to the package allowlist" % field)
    return _assert_regular_file(asset, boundary=ROOT)


def _is_valid_https_url(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        return False
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if "\\" in value or re.search(r"%(?![0-9A-Fa-f]{2})", value):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        parsed.port
    except (UnicodeError, ValueError):
        return False
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_host) > 253 or ascii_host.startswith(".") or ascii_host.endswith("."):
        return False
    return all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in ascii_host.split(".")
    )


def validate_manifest():
    manifest = _load_json_file(MANIFEST_PATH, "manifest")
    if not isinstance(manifest, dict):
        fail("manifest must be a JSON object")
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
    author = manifest.get("author")
    if not isinstance(author, dict) or author.get("name") != developer:
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
        if not _is_valid_https_url(value):
            fail("%s must be a valid HTTPS URL" % field)
    for field in ("composerIcon", "logo"):
        relative = interface.get(field)
        if not isinstance(relative, str):
            fail("%s must be a string" % field)
        asset = _branding_asset_source(field, relative)
        if asset.stat().st_size > 5 * 1024 * 1024:
            fail("%s exceeds 5 MiB" % field)
        width, height = _png_dimensions(asset)
        if width != height or not 48 <= width <= 4096:
            fail("%s must be square and 48 to 4096 pixels" % field)
    if manifest.get("license") != "Apache-2.0":
        fail("license must be Apache-2.0")
    return manifest


def _parse_agents_yaml(text):
    document = {}
    section = None
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        if "\t" in line or line.rstrip() != line:
            fail("agents/openai.yaml has invalid whitespace at line %d" % line_number)
        top = re.fullmatch(r"([a-z][a-z0-9_]*):", line)
        if top:
            section = top.group(1)
            if section in document:
                fail("agents/openai.yaml repeats section %s" % section)
            document[section] = {}
            continue
        nested = re.fullmatch(r"  ([a-z][a-z0-9_]*): (.+)", line)
        if not nested or section is None:
            fail("agents/openai.yaml has unsupported syntax at line %d" % line_number)
        key, raw_value = nested.groups()
        if key in document[section]:
            fail("agents/openai.yaml repeats key %s.%s" % (section, key))
        if raw_value in {"true", "false"}:
            value = raw_value == "true"
        elif raw_value.startswith('"') and raw_value.endswith('"'):
            try:
                value = json.loads(raw_value)
            except ValueError:
                fail("agents/openai.yaml has an invalid quoted value at line %d" % line_number)
            if not isinstance(value, str):
                fail("agents/openai.yaml quoted values must be strings")
        else:
            fail("agents/openai.yaml values must be quoted strings or booleans")
        document[section][key] = value
    return document


def _validate_agents_metadata(text):
    document = _parse_agents_yaml(text)
    if set(document) != {"interface", "policy"}:
        fail("agents/openai.yaml must contain exactly interface and policy")
    interface = document["interface"]
    if set(interface) != {"display_name", "short_description", "default_prompt"}:
        fail("agents/openai.yaml interface keys are incomplete or unsupported")
    limits = {"display_name": 80, "short_description": 120, "default_prompt": 512}
    for field, maximum in limits.items():
        value = interface[field]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\n" in value:
            fail("agents/openai.yaml %s is invalid" % field)
    if "$organize-screenshot-inbox" not in interface["default_prompt"]:
        fail("agents/openai.yaml default prompt must name the skill")
    if document["policy"] != {"allow_implicit_invocation": True}:
        fail("agents/openai.yaml policy must explicitly allow implicit invocation")
    return document


def validate_skill():
    skill_path = SKILL_ROOT / "SKILL.md"
    text = _read_regular_bytes(skill_path, 1024 * 1024, boundary=ROOT).decode("utf-8")
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
    agent_path = SKILL_ROOT / "agents" / "openai.yaml"
    agent_text = _read_regular_bytes(agent_path, 1024 * 1024, boundary=ROOT).decode("utf-8")
    _validate_agents_metadata(agent_text)


def _qualified_call_name(node, bindings):
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _qualified_call_name(node.value, bindings)
        if base:
            return "%s.%s" % (base, node.attr)
    return None


def _assignment_targets(node):
    if isinstance(node, (ast.Tuple, ast.List)):
        targets = []
        for element in node.elts:
            targets.extend(_assignment_targets(element))
        return targets
    if isinstance(node, ast.Name):
        return [node.id]
    return []


def validate_processor_boundary():
    source = _read_regular_bytes(PROCESSOR, 2 * 1024 * 1024, boundary=ROOT).decode("utf-8")
    tree = ast.parse(source, filename=str(PROCESSOR))
    imports = set()
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                imports.add(root)
                bindings[alias.asname or root] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level:
                fail("processor must not use relative imports")
            root = node.module.split(".")[0]
            imports.add(root)
            for alias in node.names:
                if alias.name == "*":
                    fail("processor must not use wildcard imports")
                bindings[alias.asname or alias.name] = "%s.%s" % (node.module, alias.name)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = _qualified_call_name(node.value, bindings)
                targets = [
                    name
                    for target in node.targets
                    for name in _assignment_targets(target)
                ]
            elif isinstance(node, ast.AnnAssign):
                value = _qualified_call_name(node.value, bindings)
                targets = _assignment_targets(node.target)
            else:
                continue
            if value:
                for target in targets:
                    if target not in bindings:
                        bindings[target] = value
                        changed = True
    forbidden = sorted(imports & FORBIDDEN_IMPORTS)
    if forbidden:
        fail("processor imports forbidden network/process modules: %s" % ", ".join(forbidden))
    unexpected = sorted(imports - PROCESSOR_ALLOWED_IMPORTS)
    if unexpected:
        fail("processor imports modules outside the stdlib allowlist: %s" % ", ".join(unexpected))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        qualified = _qualified_call_name(node.func, bindings)
        final = qualified.rsplit(".", 1)[-1] if qualified else ""
        if qualified in {"eval", "exec", "compile", "__import__", "builtins.__import__"}:
            fail("processor must not call %s" % qualified)
        if final in {"__import__", "import_module"}:
            fail("processor must not perform dynamic imports")
        if (
            qualified
            and qualified.startswith("os.")
            and (
                final in DANGEROUS_OS_CALLS
                or final.startswith("exec")
                or final.startswith("spawn")
            )
        ):
            fail("processor must not call process API %s" % qualified)
        if isinstance(node.func, ast.Attribute) and (
            node.func.attr in DANGEROUS_OS_CALLS
            or node.func.attr.startswith("exec")
            or node.func.attr.startswith("spawn")
        ):
            fail("processor must not call process-like method %s" % node.func.attr)
        if qualified and (
            final in {"CreateProcessA", "CreateProcessW", "ShellExecuteA", "ShellExecuteW", "WinExec"}
            or ".CreateProcess" in qualified
            or ".ShellExecute" in qualified
        ):
            fail("processor must not call native process API %s" % qualified)
        if qualified in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            member = node.args[1]
            if isinstance(member, ast.Constant) and isinstance(member.value, str):
                if (
                    member.value in DANGEROUS_OS_CALLS
                    or member.value in {"__import__", "import_module"}
                    or member.value.startswith("exec")
                    or member.value.startswith("spawn")
                ):
                    fail("processor must not resolve dangerous callable %s" % member.value)


def _validate_evaluations(cases):
    if not isinstance(cases, dict) or set(cases) != {
        "plugin", "version", "fixture_policy", "positive", "negative"
    }:
        fail("evaluation file has incomplete or unsupported top-level fields")
    if cases["plugin"] != PACKAGE_NAME or cases["version"] != VERSION:
        fail("evaluation metadata does not match the release")
    if not isinstance(cases["fixture_policy"], str) or not cases["fixture_policy"].strip():
        fail("evaluation fixture policy is required")
    if not isinstance(cases["positive"], list) or len(cases["positive"]) != 5:
        fail("review evaluations must contain exactly five positive cases")
    if not isinstance(cases["negative"], list) or len(cases["negative"]) != 3:
        fail("review evaluations must contain exactly three negative cases")
    seen_ids = set()
    seen_prompts = set()
    for group in ("positive", "negative"):
        for case in cases[group]:
            if not isinstance(case, dict) or set(case) != {"id", "prompt", "fixture", "expected"}:
                fail("evaluation cases require exactly id, prompt, fixture, and expected")
            case_id = case["id"]
            if (
                not isinstance(case_id, str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", case_id)
                or not case_id.startswith(group + "-")
                or case_id in seen_ids
            ):
                fail("evaluation IDs must be valid, group-prefixed, and unique")
            seen_ids.add(case_id)
            for field in ("prompt", "fixture"):
                value = case[field]
                if not isinstance(value, str) or not value.strip() or len(value) > 1024 or "\n" in value:
                    fail("evaluation %s is missing or invalid: %s" % (field, case_id))
            prompt_key = " ".join(unicodedata.normalize("NFC", case["prompt"]).split()).casefold()
            if prompt_key in seen_prompts:
                fail("evaluation prompts must be unique")
            seen_prompts.add(prompt_key)
            expected = case["expected"]
            if not isinstance(expected, list) or not 1 <= len(expected) <= 20:
                fail("evaluation expected outcomes are missing: %s" % case_id)
            normalized_expected = set()
            for item in expected:
                if not isinstance(item, str) or not item.strip() or len(item) > 512 or "\n" in item:
                    fail("evaluation expected outcome is invalid: %s" % case_id)
                key = " ".join(unicodedata.normalize("NFC", item).split()).casefold()
                if key in normalized_expected:
                    fail("evaluation expected outcomes must be unique: %s" % case_id)
                normalized_expected.add(key)
    return cases


def validate_source():
    for archive_name, path in PACKAGE_SOURCES.items():
        validate_archive_name(archive_name)
        _assert_regular_file(path, boundary=ROOT)
    validate_text_files()
    manifest = validate_manifest()
    validate_skill()
    validate_processor_boundary()
    changelog = _read_regular_bytes(
        ROOT / "CHANGELOG.md", 2 * 1024 * 1024, boundary=ROOT
    ).decode("utf-8")
    if "## %s - 2026-08-13" % VERSION not in changelog:
        fail("changelog does not contain the current version and date")
    if manifest["homepage"] != "https://github.com/battle-doll/screenshot-action-inbox#readme":
        fail("homepage metadata is unexpected")
    cases = _load_json_file(ROOT / "evals" / "cases.json", "evaluation cases")
    _validate_evaluations(cases)
    sbom = _load_json_file(ROOT / "SBOM.spdx.json", "SBOM")
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


def _snapshot_package_sources():
    entries = {}
    total = 0
    for name in sorted(PACKAGE_SOURCES):
        validate_archive_name(name)
        payload = _read_regular_bytes(
            PACKAGE_SOURCES[name], 100 * 1024 * 1024, boundary=ROOT
        )
        entries[name] = payload
        total += len(payload)
    if total > 512 * 1024 * 1024:
        fail("package source size exceeds 512 MiB")
    return entries


def _canonical_archive_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_STORED,
        strict_timestamps=True,
    ) as archive:
        archive.comment = b""
        for name in sorted(entries):
            validate_archive_name(name)
            payload = entries[name]
            if not isinstance(payload, bytes):
                fail("archive entry payload must be bytes: %s" % name)
            archive.writestr(_zip_info(name), payload)
    return output.getvalue()


def build_archive(output):
    destination = _assert_dist_output(output)
    payload = _canonical_archive_bytes(_snapshot_package_sources())
    publish_bytes(destination, payload)
    return destination


def _validate_archive_bytes(payload, source_entries=None, smoke=True):
    if not payload or len(payload) > 100 * 1024 * 1024:
        fail("archive is empty or exceeds 100 MiB")
    if source_entries is None:
        source_entries = _snapshot_package_sources()
    if not isinstance(source_entries, dict) or set(source_entries) != set(PACKAGE_SOURCES):
        fail("source snapshot does not match the exact package allowlist")
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        if archive.comment:
            fail("archive global comment is not canonical")
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if names != sorted(PACKAGE_SOURCES):
            fail("archive entries do not match the exact sorted allowlist")
        if len(names) > 5000:
            fail("archive has too many entries")
        normalized = set()
        member_entries = {}
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
            member_entries[info.filename] = archive.read(info)
        if total > 512 * 1024 * 1024:
            fail("archive uncompressed size exceeds 512 MiB")
        if archive.testzip() is not None:
            fail("archive CRC validation failed")
        for name, source_payload in source_entries.items():
            if member_entries[name] != source_payload:
                fail("archive content differs from source: %s" % name)
    if payload != _canonical_archive_bytes(member_entries):
        fail("archive byte stream is not the canonical ZIP encoding")
    if smoke:
        _smoke_archive(payload)
    return payload


def validate_archive(path, smoke=True):
    archive_path = Path(path)
    payload = _read_regular_bytes(archive_path, 100 * 1024 * 1024)
    _validate_archive_bytes(payload, smoke=smoke)
    print("archive_validation=PASS path=%s" % archive_path)
    return payload


def _safe_extract(archive_payload, destination):
    with zipfile.ZipFile(io.BytesIO(archive_payload), "r") as archive:
        for info in archive.infolist():
            validate_archive_name(info.filename)
            target = destination.joinpath(*info.filename.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(archive.read(info.filename))


def _smoke_archive(archive_payload):
    with tempfile.TemporaryDirectory(prefix="sai-extracted-smoke-") as raw:
        root = Path(raw) / "패키지 경로 with spaces"
        root.mkdir()
        _safe_extract(archive_payload, root)
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
    path = _assert_dist_output(path)
    if not isinstance(payload, bytes):
        fail("published payload must be bytes")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s.tmp-" % path.name,
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    parent_descriptor = -1
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(str(temporary), mode)
        _assert_plain_directory(path.parent, boundary=DIST)
        _assert_dist_output(path)
        if (
            os.name != "nt"
            and os.open in getattr(os, "supports_dir_fd", set())
            and os.rename in getattr(os, "supports_dir_fd", set())
        ):
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
            if not getattr(os, "O_DIRECTORY", 0):
                fail("directory-bound publish is unavailable")
            parent_descriptor = os.open(str(path.parent), flags)
            pinned = os.fstat(parent_descriptor)
            current = path.parent.stat()
            if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
                fail("output parent changed before publish")
            os.rename(
                temporary.name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        else:
            _assert_plain_directory(path.parent, boundary=DIST)
            _assert_dist_output(path)
            os.replace(str(temporary), str(path))
        _assert_regular_file(path, boundary=DIST)
    finally:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def build_release():
    validate_source()
    _prepare_dist()
    output = DIST / ARCHIVE_NAME
    checksum_path = output.with_suffix(".zip.sha256")
    _assert_dist_output(output)
    _assert_dist_output(checksum_path)
    with tempfile.TemporaryDirectory(prefix="sai-double-build-", dir=str(DIST)) as raw:
        temp = Path(raw)
        first = build_archive(temp / "first.zip")
        second = build_archive(temp / "second.zip")
        first_bytes = _read_regular_bytes(first, 100 * 1024 * 1024, boundary=DIST)
        second_bytes = _read_regular_bytes(second, 100 * 1024 * 1024, boundary=DIST)
        if first_bytes != second_bytes:
            fail("independent builds are not byte-identical")
        _validate_archive_bytes(first_bytes, smoke=True)
    digest = sha256_bytes(first_bytes)
    publish_bytes(output, first_bytes)
    checksum = ("%s  %s\n" % (digest, output.name)).encode("ascii")
    publish_bytes(checksum_path, checksum)
    published = _read_regular_bytes(output, 100 * 1024 * 1024, boundary=DIST)
    if published != first_bytes:
        fail("published archive differs from the validated release bytes")
    _validate_archive_bytes(published, smoke=False)
    print("release=%s" % output)
    print("sha256=%s" % digest)
    print("reproducible_builds=2")
    return output


def _validate_runtime_evidence_report(report):
    if not isinstance(report, dict) or set(report) != {
        "schema_version", "fixture", "artifacts"
    }:
        fail("runtime evidence has incomplete or unsupported top-level fields")
    if report["schema_version"] != 1 or report["fixture"] != "tests/fixtures/observations.json":
        fail("runtime evidence metadata is unexpected")
    artifacts = report["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != EXPECTED_RUNTIME_ARTIFACTS:
        fail("runtime evidence must contain exactly the five expected artifacts")
    for name, evidence in artifacts.items():
        if not isinstance(evidence, dict) or set(evidence) != {"sha256", "bytes"}:
            fail("runtime evidence fields are invalid: %s" % name)
        digest = evidence["sha256"]
        size = evidence["bytes"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            fail("runtime evidence SHA-256 is invalid: %s" % name)
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= 100 * 1024 * 1024:
            fail("runtime evidence byte count is invalid: %s" % name)
    return report


def _runtime_evidence_report():
    namespace = {}
    source = _read_regular_bytes(PROCESSOR, 2 * 1024 * 1024, boundary=ROOT).decode("utf-8")
    exec(compile(source, str(PROCESSOR), "exec"), namespace)
    observations = _load_json_file(
        ROOT / "tests" / "fixtures" / "observations.json",
        "runtime observation fixture",
    )
    data = namespace["validate_observations"](observations)
    artifacts = namespace["build_artifacts"](data)
    if not isinstance(artifacts, dict) or set(artifacts) != EXPECTED_RUNTIME_ARTIFACTS:
        fail("processor did not build exactly the five expected runtime artifacts")
    if any(not isinstance(payload, bytes) for payload in artifacts.values()):
        fail("processor runtime artifacts must be bytes")
    report = {
        "schema_version": 1,
        "fixture": "tests/fixtures/observations.json",
        "artifacts": {
            name: {"sha256": sha256_bytes(payload), "bytes": len(payload)}
            for name, payload in sorted(artifacts.items())
        },
    }
    return _validate_runtime_evidence_report(report)


def _runtime_evidence_bytes(report):
    _validate_runtime_evidence_report(report)
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_runtime_evidence(payload):
    report = _load_json_bytes(payload, "runtime evidence")
    return _validate_runtime_evidence_report(report)


def runtime_evidence():
    validate_source()
    report = _runtime_evidence_report()
    _prepare_dist()
    output = DIST / "runtime-artifact-evidence.json"
    _assert_dist_output(output)
    publish_bytes(output, _runtime_evidence_bytes(report))
    print(json.dumps(report, sort_keys=True))
    return report


def compare_matrix(root, expect_count):
    if isinstance(expect_count, bool) or not isinstance(expect_count, int) or expect_count < 1:
        fail("expected matrix archive count must be a positive integer")
    validate_source()
    matrix_root = _assert_plain_directory(Path(root))
    candidates = sorted(matrix_root.rglob(ARCHIVE_NAME))
    if len(candidates) != expect_count:
        fail("expected %d matrix archives, found %d" % (expect_count, len(candidates)))
    expected_bundle_names = {
        ARCHIVE_NAME,
        ARCHIVE_NAME + ".sha256",
        "runtime-artifact-evidence.json",
    }
    candidate_parents = {candidate.parent for candidate in candidates}
    if len(candidate_parents) != expect_count:
        fail("each matrix archive must be in its own artifact directory")
    for parent in candidate_parents:
        _assert_plain_directory(parent, boundary=matrix_root)
        actual_names = {entry.name for entry in parent.iterdir()}
        if actual_names != expected_bundle_names:
            fail("matrix artifact directory has missing or extra files: %s" % parent)
    evidence_paths = sorted(matrix_root.rglob("runtime-artifact-evidence.json"))
    if len(evidence_paths) != expect_count:
        fail("expected %d runtime evidence files, found %d" % (expect_count, len(evidence_paths)))
    if {path.parent for path in evidence_paths} != candidate_parents:
        fail("each matrix archive must have exactly one paired runtime evidence file")
    source_entries = _snapshot_package_sources()
    archive_payloads = []
    evidence_payloads = []
    expected_report = _runtime_evidence_report()
    expected_evidence_payload = _runtime_evidence_bytes(expected_report)
    for candidate in candidates:
        payload = _read_regular_bytes(
            candidate, 100 * 1024 * 1024, boundary=matrix_root
        )
        _validate_archive_bytes(payload, source_entries=source_entries, smoke=False)
        digest = sha256_bytes(payload)
        checksum_path = candidate.parent / (ARCHIVE_NAME + ".sha256")
        checksum_payload = _read_regular_bytes(
            checksum_path, 4096, boundary=matrix_root
        )
        expected_checksum = ("%s  %s\n" % (digest, ARCHIVE_NAME)).encode("ascii")
        if checksum_payload != expected_checksum:
            fail("matrix archive checksum is missing or does not match: %s" % candidate)
        evidence_path = candidate.parent / "runtime-artifact-evidence.json"
        evidence_payload = _read_regular_bytes(
            evidence_path, 2 * 1024 * 1024, boundary=matrix_root
        )
        evidence_report = _load_runtime_evidence(evidence_payload)
        if evidence_report != expected_report or evidence_payload != expected_evidence_payload:
            fail("matrix runtime evidence does not match the current exact artifacts")
        archive_payloads.append(payload)
        evidence_payloads.append(evidence_payload)
    if any(payload != archive_payloads[0] for payload in archive_payloads[1:]):
        fail("matrix archives are not byte-identical")
    if any(payload != evidence_payloads[0] for payload in evidence_payloads[1:]):
        fail("matrix runtime artifact evidence is not byte-identical")
    payload = archive_payloads[0]
    _smoke_archive(payload)
    digest = sha256_bytes(payload)
    _prepare_dist()
    output = DIST / ARCHIVE_NAME
    checksum_output = output.with_suffix(".zip.sha256")
    report_output = DIST / "cross-platform-reproducibility.json"
    _assert_dist_output(output)
    _assert_dist_output(checksum_output)
    _assert_dist_output(report_output)
    publish_bytes(output, payload)
    publish_bytes(checksum_output, ("%s  %s\n" % (digest, output.name)).encode("ascii"))
    report = {
        "status": "PASS",
        "archives_compared": len(candidates),
        "runtime_evidence_compared": len(evidence_paths),
        "runtime_artifacts": expected_report["artifacts"],
        "sha256": digest,
        "archive": output.name,
    }
    publish_bytes(
        report_output,
        (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
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
