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
OBSERVATIONS_PATH = ROOT / "tests" / "fixtures" / "observations.json"
EXPECTED_PROCESSOR_SHA256 = "52bc4b4b37cc384a13222e035f89841b9590e0bc0029fd2b8ee4046135b9b447"
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

EXPECTED_MATRIX = {
    "ubuntu-py39": {
        "runner_label": "ubuntu-latest", "runner_os": "Linux",
        "runner_arch": "X64", "python": "3.9",
    },
    "ubuntu-py310": {
        "runner_label": "ubuntu-latest", "runner_os": "Linux",
        "runner_arch": "X64", "python": "3.10",
    },
    "ubuntu-py311": {
        "runner_label": "ubuntu-latest", "runner_os": "Linux",
        "runner_arch": "X64", "python": "3.11",
    },
    "ubuntu-py312": {
        "runner_label": "ubuntu-latest", "runner_os": "Linux",
        "runner_arch": "X64", "python": "3.12",
    },
    "ubuntu-py313": {
        "runner_label": "ubuntu-latest", "runner_os": "Linux",
        "runner_arch": "X64", "python": "3.13",
    },
    "ubuntu-py314": {
        "runner_label": "ubuntu-latest", "runner_os": "Linux",
        "runner_arch": "X64", "python": "3.14",
    },
    "macos-intel-py39": {
        "runner_label": "macos-15-intel", "runner_os": "macOS",
        "runner_arch": "X64", "python": "3.9",
    },
    "macos-py314": {
        "runner_label": "macos-latest", "runner_os": "macOS",
        "runner_arch": "ARM64", "python": "3.14",
    },
    "windows-py39": {
        "runner_label": "windows-latest", "runner_os": "Windows",
        "runner_arch": "X64", "python": "3.9",
    },
    "windows-py314": {
        "runner_label": "windows-latest", "runner_os": "Windows",
        "runner_arch": "X64", "python": "3.14",
    },
}
PROVENANCE_FIELDS = {
    "job_id", "runner_label", "runner_os", "runner_arch", "python", "commit_sha",
}

_OPEN_SUPPORTS_DIR_FD = os.open in getattr(os, "supports_dir_fd", set())
_RENAME_SUPPORTS_DIR_FD = os.rename in getattr(os, "supports_dir_fd", set())
_UNLINK_SUPPORTS_DIR_FD = os.unlink in getattr(os, "supports_dir_fd", set())
_MKDIR_SUPPORTS_DIR_FD = os.mkdir in getattr(os, "supports_dir_fd", set())
_LISTDIR_SUPPORTS_FD = os.listdir in getattr(os, "supports_fd", set())
_STAT_SUPPORTS_DIR_FD = os.stat in getattr(os, "supports_dir_fd", set())
_STAT_SUPPORTS_NOFOLLOW = os.stat in getattr(os, "supports_follow_symlinks", set())


def _stable_stat_key(result):
    return (
        getattr(result, "st_dev", None),
        getattr(result, "st_ino", None),
        result.st_size,
        getattr(result, "st_mtime_ns", int(result.st_mtime * 1000000000)),
        getattr(result, "st_ctime_ns", int(result.st_ctime * 1000000000)),
    )


class VerifyError(AssertionError):
    pass


def fail(message):
    raise VerifyError(message)


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


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


def _canonicalize_system_root_alias(path):
    """Resolve only a known platform root alias, never a user path component."""
    absolute = os.path.abspath(os.fspath(path))
    if os.name == "nt" or not absolute.startswith(os.path.sep):
        return absolute
    parts = [part for part in absolute.split(os.path.sep) if part]
    if not parts:
        return absolute
    first = os.path.sep + parts[0]
    aliases = {"/etc": "/private/etc", "/tmp": "/private/tmp", "/var": "/private/var"}
    expected = aliases.get(first) if sys.platform == "darwin" else None
    if expected is None or os.path.normpath(os.path.realpath(first)) != expected:
        return absolute
    return os.path.join(expected, *parts[1:])


def _posix_directory_flags():
    directory = getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not directory or not nofollow or not _OPEN_SUPPORTS_DIR_FD:
        fail("descriptor-bound no-follow directory traversal is unavailable")
    return os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0)


def _open_posix_plain_directory(path):
    """Open an absolute directory component-by-component without following links."""
    absolute = _canonicalize_system_root_alias(path)
    if not os.path.isabs(absolute):
        fail("descriptor-bound directory path must be absolute: %s" % path)
    flags = _posix_directory_flags()
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in (part for part in absolute.split(os.path.sep) if part):
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        result = os.fstat(descriptor)
        if not stat.S_ISDIR(result.st_mode) or _is_link_or_reparse(result):
            fail("required path is not a descriptor-bound plain directory: %s" % path)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _windows_file_api():
    if os.name != "nt":
        fail("Windows handle-bound I/O is unavailable on this platform")
    import ctypes
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    return ctypes, ByHandleFileInformation, create_file, get_information, close_handle


def _windows_handle_value(ctypes_module, handle):
    return ctypes_module.cast(handle, ctypes_module.c_void_p).value


def _windows_handle_information(api, handle):
    ctypes_module, information_type, unused_create, get_information, unused_close = api
    information = information_type()
    if not get_information(handle, ctypes_module.byref(information)):
        raise ctypes_module.WinError(ctypes_module.get_last_error())
    identity = (
        int(information.dwVolumeSerialNumber),
        (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
    )
    return information, identity


def _open_windows_regular_file(path):
    """Pin every directory component, then return a CRT fd for the leaf handle."""
    import msvcrt

    api = _windows_file_api()
    ctypes_module, information_type, create_file, get_information, close_handle = api
    absolute = os.path.abspath(os.fspath(path))
    drive, tail = os.path.splitdrive(absolute)
    if not drive:
        fail("Windows handle-bound file path must be absolute: %s" % path)
    current = drive + os.path.sep
    parent_paths = [current]
    parts = [part for part in tail.split(os.path.sep) if part]
    if not parts:
        fail("required path does not name a file: %s" % path)
    for component in parts[:-1]:
        current = os.path.join(current, component)
        parent_paths.append(current)

    file_read_attributes = 0x00000080
    generic_read = 0x80000000
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse_point = 0x00200000
    sequential_scan = 0x08000000
    invalid_handle = ctypes_module.c_void_p(-1).value
    directory_handles = []
    file_handle = None
    transferred = False
    try:
        for component_path in parent_paths:
            handle = create_file(
                component_path, file_read_attributes, share_read_write, None,
                open_existing, backup_semantics | open_reparse_point, None,
            )
            if _windows_handle_value(ctypes_module, handle) == invalid_handle:
                raise ctypes_module.WinError(ctypes_module.get_last_error())
            directory_handles.append(handle)
            information, unused_identity = _windows_handle_information(api, handle)
            if information.dwFileAttributes & 0x400 or not information.dwFileAttributes & 0x10:
                fail("path contains a link, reparse point, or non-directory component: %s" % component_path)

        file_handle = create_file(
            absolute, generic_read, share_read_write, None, open_existing,
            open_reparse_point | sequential_scan, None,
        )
        if _windows_handle_value(ctypes_module, file_handle) == invalid_handle:
            raise ctypes_module.WinError(ctypes_module.get_last_error())
        information, identity = _windows_handle_information(api, file_handle)
        if information.dwFileAttributes & 0x400 or information.dwFileAttributes & 0x10:
            fail("required path is not a regular non-reparse file: %s" % path)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        descriptor = msvcrt.open_osfhandle(
            _windows_handle_value(ctypes_module, file_handle), flags
        )
        transferred = True
        return descriptor, (api, directory_handles), identity
    finally:
        if file_handle is not None and not transferred:
            close_handle(file_handle)
        if not transferred:
            for handle in reversed(directory_handles):
                close_handle(handle)


def _close_windows_directory_handles(locks):
    if locks is None:
        return
    api, handles = locks
    close_handle = api[4]
    for handle in reversed(handles):
        close_handle(handle)


def _create_windows_regular_file(path):
    """Create a final Windows file through one non-reparse, non-replaceable handle."""
    import msvcrt

    locks = _open_windows_plain_directory_chain(Path(path).parent)
    api = locks[0]
    ctypes_module, unused_information_type, create_file, unused_get, close_handle = api
    generic_write = 0x40000000
    share_read = 0x00000001
    create_new = 1
    file_attribute_normal = 0x00000080
    open_reparse_point = 0x00200000
    sequential_scan = 0x08000000
    invalid_handle = ctypes_module.c_void_p(-1).value
    handle = None
    transferred = False
    try:
        handle = create_file(
            os.path.abspath(os.fspath(path)),
            generic_write,
            share_read,
            None,
            create_new,
            file_attribute_normal | open_reparse_point | sequential_scan,
            None,
        )
        if _windows_handle_value(ctypes_module, handle) == invalid_handle:
            handle = None
            raise ctypes_module.WinError(ctypes_module.get_last_error())
        information, identity = _windows_handle_information(api, handle)
        if information.dwFileAttributes & 0x400 or information.dwFileAttributes & 0x10:
            fail("new output is not a regular non-reparse file: %s" % path)
        descriptor = msvcrt.open_osfhandle(
            _windows_handle_value(ctypes_module, handle),
            os.O_WRONLY | getattr(os, "O_BINARY", 0),
        )
        transferred = True
        return descriptor, locks, identity
    finally:
        if handle is not None and not transferred:
            close_handle(handle)
        if not transferred:
            _close_windows_directory_handles(locks)


def _open_windows_plain_directory_chain(path):
    api = _windows_file_api()
    ctypes_module, information_type, create_file, get_information, close_handle = api
    absolute = os.path.abspath(os.fspath(path))
    drive, tail = os.path.splitdrive(absolute)
    if not drive:
        fail("Windows handle-bound directory path must be absolute: %s" % path)
    current = drive + os.path.sep
    component_paths = [current]
    for component in (part for part in tail.split(os.path.sep) if part):
        current = os.path.join(current, component)
        component_paths.append(current)
    file_read_attributes = 0x00000080
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    backup_semantics = 0x02000000
    open_reparse_point = 0x00200000
    invalid_handle = ctypes_module.c_void_p(-1).value
    handles = []
    try:
        for component_path in component_paths:
            handle = create_file(
                component_path, file_read_attributes, share_read_write, None,
                open_existing, backup_semantics | open_reparse_point, None,
            )
            if _windows_handle_value(ctypes_module, handle) == invalid_handle:
                raise ctypes_module.WinError(ctypes_module.get_last_error())
            handles.append(handle)
            information = information_type()
            if not get_information(handle, ctypes_module.byref(information)):
                raise ctypes_module.WinError(ctypes_module.get_last_error())
            if information.dwFileAttributes & 0x400 or not information.dwFileAttributes & 0x10:
                fail("path contains a link, reparse point, or non-directory component: %s" % component_path)
        return api, handles
    except BaseException:
        for handle in reversed(handles):
            close_handle(handle)
        raise


def _enumerate_plain_directory(path, boundary=None):
    candidate = Path(os.path.abspath(os.fspath(path)))
    if boundary is not None:
        candidate = _lexical_child(candidate, boundary)
    descriptor = -1
    windows_locks = None
    try:
        if os.name == "nt":
            windows_locks = _open_windows_plain_directory_chain(candidate)
            names = os.listdir(str(candidate))
            results = {
                name: os.lstat(str(candidate / name))
                for name in names
            }
        else:
            if not (
                _LISTDIR_SUPPORTS_FD
                and _STAT_SUPPORTS_DIR_FD
                and _STAT_SUPPORTS_NOFOLLOW
            ):
                fail("descriptor-bound directory enumeration is unavailable")
            descriptor = _open_posix_plain_directory(candidate)
            names = os.listdir(descriptor)
            results = {
                name: os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                for name in names
            }
        normalized = set()
        for name, result in results.items():
            if (
                not isinstance(name, str)
                or not name
                or name in {".", ".."}
                or "/" in name
                or "\\" in name
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
            ):
                fail("directory contains an unsafe entry name: %r" % name)
            key = unicodedata.normalize("NFC", name).casefold()
            if key in normalized:
                fail("directory entries collide after normalization")
            normalized.add(key)
            if _is_link_or_reparse(result):
                fail("directory contains a symlink or reparse point: %s" % (candidate / name))
        return results
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _close_windows_directory_handles(windows_locks)


def _read_regular_bytes(path, maximum, boundary=None):
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        fail("maximum file size must be a nonnegative integer")
    candidate = Path(os.path.abspath(os.fspath(path)))
    if boundary is not None:
        candidate = _lexical_child(candidate, boundary)
    descriptor = -1
    parent_descriptor = -1
    windows_locks = None
    try:
        if os.name == "nt":
            descriptor, windows_locks, unused_identity = _open_windows_regular_file(candidate)
        else:
            parent_descriptor = _open_posix_plain_directory(candidate.parent)
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(candidate.name, flags, dir_fd=parent_descriptor)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _is_link_or_reparse(opened):
                fail("required path is not a descriptor-bound regular file: %s" % candidate)
            if opened.st_size > maximum:
                fail("file exceeds %d bytes: %s" % (maximum, candidate))
            payload = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
        if len(payload) > maximum:
            fail("file exceeds %d bytes: %s" % (maximum, candidate))
        if _stable_stat_key(opened) != _stable_stat_key(after):
            fail("file changed while being read: %s" % candidate)
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        _close_windows_directory_handles(windows_locks)


def _prepare_dist():
    candidate = _lexical_child(DIST, ROOT, label="DIST")
    repository = Path(os.path.abspath(os.fspath(ROOT)))
    if candidate.parent != repository:
        fail("DIST parent must be the repository root: %s" % candidate.parent)
    if os.name == "nt":
        locks = _open_windows_plain_directory_chain(repository)
        try:
            try:
                result = os.lstat(str(candidate))
            except FileNotFoundError:
                os.mkdir(str(candidate), 0o755)
                result = os.lstat(str(candidate))
            if _is_link_or_reparse(result) or not stat.S_ISDIR(result.st_mode):
                fail("DIST must be a plain directory: %s" % candidate)
            return candidate
        finally:
            _close_windows_directory_handles(locks)
    if not (_MKDIR_SUPPORTS_DIR_FD and _STAT_SUPPORTS_DIR_FD and _STAT_SUPPORTS_NOFOLLOW):
        fail("descriptor-bound DIST creation is unavailable")
    parent_descriptor = _open_posix_plain_directory(repository)
    try:
        try:
            result = os.stat(
                candidate.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            os.mkdir(candidate.name, 0o755, dir_fd=parent_descriptor)
            result = os.stat(
                candidate.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
        if _is_link_or_reparse(result) or not stat.S_ISDIR(result.st_mode):
            fail("DIST must be a plain directory: %s" % candidate)
        return candidate
    finally:
        os.close(parent_descriptor)


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


def _package_snapshot_payload(source_entries, archive_name, maximum):
    if not isinstance(source_entries, dict) or set(source_entries) != set(PACKAGE_SOURCES):
        fail("source snapshot does not match the exact package allowlist")
    payload = source_entries.get(archive_name)
    if not isinstance(payload, bytes):
        fail("package snapshot entry is not immutable bytes: %s" % archive_name)
    if len(payload) > maximum:
        fail("package snapshot entry exceeds %d bytes: %s" % (maximum, archive_name))
    return payload


def _package_name_for_path(path):
    candidate = os.path.normcase(os.path.abspath(os.fspath(path)))
    for name, source_path in PACKAGE_SOURCES.items():
        if os.path.normcase(os.path.abspath(os.fspath(source_path))) == candidate:
            return name
    return None


def _source_payload(path, maximum, source_entries=None):
    archive_name = _package_name_for_path(path)
    if source_entries is not None and archive_name is not None:
        return _package_snapshot_payload(source_entries, archive_name, maximum)
    return _read_regular_bytes(path, maximum, boundary=ROOT)


def _png_dimensions(path, source_entries=None):
    payload = _source_payload(path, 5 * 1024 * 1024, source_entries)
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


def validate_text_files(source_entries=None):
    unique = []
    seen = set()
    for path in TEXT_SOURCE_PATHS:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    for path in unique:
        payload = _source_payload(path, 10 * 1024 * 1024, source_entries)
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


def _branding_asset_source(field, relative, source_entries=None):
    expected, archive_name = EXPECTED_BRANDING_ASSETS[field]
    if relative != expected:
        fail("%s must exactly match the packaged asset %s" % (field, expected))
    asset = PACKAGE_SOURCES.get(archive_name)
    expected_source = PLUGIN_ROOT.joinpath(*archive_name.split("/"))
    if asset is None or Path(asset) != expected_source:
        fail("%s is not bound to the package allowlist" % field)
    if source_entries is not None:
        _package_snapshot_payload(source_entries, archive_name, 5 * 1024 * 1024)
        return Path(asset)
    return _assert_regular_file(asset, boundary=ROOT)


def _is_valid_https_url(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        return False
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if (
        "\\" in value
        or re.search(r"%(?![0-9A-Fa-f]{2})", value)
        or re.search(r"%5[cC]", value)
    ):
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


def validate_manifest(source_entries=None):
    if source_entries is None:
        manifest = _load_json_file(MANIFEST_PATH, "manifest")
    else:
        manifest = _load_json_bytes(
            _package_snapshot_payload(
                source_entries, ".codex-plugin/plugin.json", 2 * 1024 * 1024
            ),
            "manifest",
        )
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
        asset = _branding_asset_source(field, relative, source_entries)
        asset_payload = _source_payload(asset, 5 * 1024 * 1024, source_entries)
        if len(asset_payload) > 5 * 1024 * 1024:
            fail("%s exceeds 5 MiB" % field)
        width, height = _png_dimensions(asset, source_entries)
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


def validate_skill(source_entries=None):
    skill_path = SKILL_ROOT / "SKILL.md"
    text = _source_payload(skill_path, 1024 * 1024, source_entries).decode("utf-8")
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
    agent_text = _source_payload(agent_path, 1024 * 1024, source_entries).decode("utf-8")
    _validate_agents_metadata(agent_text)


def _static_string(node, strings):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return strings.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left, strings)
        right = _static_string(node.right, strings)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        pieces = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                pieces.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                piece = _static_string(value.value, strings)
                if piece is None or value.format_spec is not None or value.conversion != -1:
                    return None
                pieces.append(piece)
            else:
                return None
        return "".join(pieces)
    return None


def _qualified_call_name(node, bindings, strings=None):
    if strings is None:
        strings = {}
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _qualified_call_name(node.value, bindings, strings)
        if base:
            return "%s.%s" % (base, node.attr)
    if isinstance(node, ast.Call):
        callee = _qualified_call_name(node.func, bindings, strings)
        if callee in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            base = _qualified_call_name(node.args[0], bindings, strings)
            member = _static_string(node.args[1], strings)
            if base and member is not None:
                return "%s.%s" % (base, member)
        if callee in {
            "ctypes.WinDLL", "ctypes.CDLL", "ctypes.PyDLL", "ctypes.OleDLL"
        } and node.args:
            library = _static_string(node.args[0], strings)
            if library is not None:
                return "%s[%s]" % (callee, library)
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


def _dangerous_reference(reference):
    if not reference:
        return False
    final = reference.rsplit(".", 1)[-1]
    if reference in {"eval", "exec", "compile", "__import__", "builtins.__import__"}:
        return True
    if final in {"__import__", "import_module"}:
        return True
    return bool(
        reference.startswith("os.")
        and (
            final in DANGEROUS_OS_CALLS
            or final.startswith("exec")
            or final.startswith("spawn")
        )
    )


def _ctypes_native_export(reference):
    if not reference:
        return None
    match = re.fullmatch(
        r"ctypes\.(WinDLL|CDLL|PyDLL|OleDLL)\[([^]]+)\]\."
        r"([A-Za-z_][A-Za-z0-9_]*)(?:\.(argtypes|restype))?",
        reference,
    )
    if match:
        return match.groups()
    return None


def _validate_ctypes_reference(reference):
    if not reference or (reference != "ctypes" and not reference.startswith("ctypes.")):
        return
    if reference in {
        "ctypes", "ctypes.Structure", "ctypes.WinDLL", "ctypes.POINTER",
        "ctypes.WinDLL[kernel32]", "ctypes.wintypes",
    }:
        return
    allowed_wintypes = {
        "BOOL", "DWORD", "FILETIME", "HANDLE", "LPCWSTR", "LPVOID", "LPWSTR",
    }
    if reference.startswith("ctypes.wintypes."):
        member = reference[len("ctypes.wintypes."):]
        if member in allowed_wintypes:
            return
        fail("processor must not resolve unapproved ctypes reference %s" % reference)
    native = _ctypes_native_export(reference)
    if native is None:
        fail("processor must not resolve unapproved ctypes reference %s" % reference)
    loader, library, export, declaration_attribute = native
    allowed = {
        "CreateFileW", "GetFileInformationByHandle",
        "GetFinalPathNameByHandleW", "CloseHandle",
    }
    if loader != "WinDLL" or library.casefold() != "kernel32" or export not in allowed:
        fail("processor must not resolve native process API or unapproved native API %s" % reference)
    if declaration_attribute not in {None, "argtypes", "restype"}:
        fail("processor must not derive attributes from a kernel32 export")


def _validate_ctypes_boundary(tree):
    """Allow only the exact Win32 declarations required by the processor."""
    parents = {}
    store_counts = {}
    ctypes_names = set()
    imported_names = set()
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
        if isinstance(parent, ast.Name) and isinstance(parent.ctx, ast.Store):
            store_counts[parent.id] = store_counts.get(parent.id, 0) + 1
        elif isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            store_counts[parent.name] = store_counts.get(parent.name, 0) + 1
        elif isinstance(parent, ast.arg):
            store_counts[parent.arg] = store_counts.get(parent.arg, 0) + 1
        elif isinstance(parent, ast.ExceptHandler) and parent.name:
            store_counts[parent.name] = store_counts.get(parent.name, 0) + 1
        elif isinstance(parent, ast.Import):
            for alias in parent.names:
                local = alias.asname or alias.name.split(".")[0]
                imported_names.add(local)
                if alias.name == "ctypes":
                    ctypes_names.add(local)
                elif alias.name.startswith("ctypes."):
                    fail("processor must import only the top-level ctypes module")
        elif isinstance(parent, ast.ImportFrom) and parent.module == "ctypes":
            for alias in parent.names:
                imported_names.add(alias.asname or alias.name)
                if alias.name != "wintypes":
                    fail("processor must import only ctypes.wintypes directly")

    direct_allowed = {"Structure", "WinDLL", "POINTER"}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in ctypes_names
        ):
            if node.attr not in direct_allowed:
                fail("processor must not access unapproved ctypes attribute %s" % node.attr)
            if node.attr == "WinDLL":
                parent = parents.get(node)
                if not isinstance(parent, ast.Call) or parent.func is not node:
                    fail("processor must call ctypes.WinDLL directly")

    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Name)
            or not isinstance(node.ctx, ast.Load)
            or node.id not in ctypes_names
        ):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            continue
        grandparent = parents.get(parent)
        enclosing = grandparent
        while enclosing is not None and not isinstance(
            enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            enclosing = parents.get(enclosing)
        if (
            isinstance(parent, ast.Tuple)
            and parent.elts
            and parent.elts[0] is node
            and isinstance(grandparent, ast.Return)
            and enclosing is not None
            and enclosing.name == "_windows_file_information_api"
        ):
            continue
        fail("processor must not embed or dynamically derive the ctypes module")

    loader_names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        is_direct_windll = (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id in ctypes_names
            and function.attr == "WinDLL"
        )
        if not is_direct_windll:
            continue
        if (
            len(node.args) != 1
            or _static_string(node.args[0], {}) != "kernel32"
            or len(node.keywords) != 1
            or node.keywords[0].arg != "use_last_error"
            or not isinstance(node.keywords[0].value, ast.Constant)
            or node.keywords[0].value.value is not True
        ):
            fail("processor ctypes.WinDLL declaration must exactly load kernel32")
        parent = parents.get(node)
        if (
            not isinstance(parent, (ast.Assign, ast.AnnAssign))
            or parent.value is not node
        ):
            fail("processor ctypes.WinDLL must be the direct assignment value")
        if isinstance(parent, ast.Assign):
            if len(parent.targets) != 1 or not isinstance(parent.targets[0], ast.Name):
                fail("processor ctypes.WinDLL must bind one plain name")
            target = parent.targets[0].id
        else:
            if not isinstance(parent.target, ast.Name):
                fail("processor ctypes.WinDLL must bind one plain name")
            target = parent.target.id
        if target in imported_names or store_counts.get(target) != 1:
            fail("processor ctypes.WinDLL target must have exactly one binding")
        loader_names.add(target)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    continue
                target = node.targets[0].id
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target = node.target.id
                value = node.value
            else:
                continue
            if isinstance(value, ast.Name) and value.id in loader_names:
                if target in imported_names or store_counts.get(target) != 1:
                    fail("processor ctypes.WinDLL alias must have exactly one binding")
                if target not in loader_names:
                    loader_names.add(target)
                    changed = True

    allowed_exports = {
        "CreateFileW", "GetFileInformationByHandle",
        "GetFinalPathNameByHandleW", "CloseHandle",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        if node.id not in loader_names:
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Attribute) and parent.value is node:
            if parent.attr not in allowed_exports:
                fail("processor must not resolve unapproved kernel32 export %s" % parent.attr)
            if isinstance(parents.get(parent), ast.Attribute):
                fail("processor must not derive attributes from a kernel32 export")
            continue
        if (
            isinstance(parent, (ast.Assign, ast.AnnAssign))
            and parent.value is node
        ):
            if isinstance(parent, ast.Assign):
                if len(parent.targets) != 1 or not isinstance(parent.targets[0], ast.Name):
                    fail("processor ctypes.WinDLL alias must bind one plain name")
                target = parent.targets[0].id
            else:
                if not isinstance(parent.target, ast.Name):
                    fail("processor ctypes.WinDLL alias must bind one plain name")
                target = parent.target.id
            if target not in loader_names:
                fail("processor must not embed or dynamically derive a ctypes.WinDLL handle")
            continue
        fail("processor must not embed or dynamically derive a ctypes.WinDLL handle")


def validate_processor_boundary(source_entries=None):
    source_payload = _source_payload(
        PROCESSOR, 2 * 1024 * 1024, source_entries
    )
    if (
        source_entries is not None
        and sha256_bytes(source_payload) != EXPECTED_PROCESSOR_SHA256
    ):
        fail("processor bytes do not match the reviewed release boundary")
    source = source_payload.decode("utf-8")
    tree = ast.parse(source, filename=str(PROCESSOR))
    imports = set()
    bindings = {}
    strings = {}
    ambiguous_bindings = set()
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
                value = _qualified_call_name(node.value, bindings, strings)
                string_value = _static_string(node.value, strings)
                targets = [
                    name
                    for target in node.targets
                    for name in _assignment_targets(target)
                ]
            elif isinstance(node, ast.AnnAssign):
                value = _qualified_call_name(node.value, bindings, strings)
                string_value = _static_string(node.value, strings)
                targets = _assignment_targets(node.target)
            else:
                continue
            if string_value is not None:
                for target in targets:
                    if target not in strings:
                        strings[target] = string_value
                        changed = True
                    elif strings[target] is not None and strings[target] != string_value:
                        strings[target] = None
                        changed = True
            else:
                for target in targets:
                    if target in strings and strings[target] is not None:
                        strings[target] = None
                        changed = True
            if value:
                for target in targets:
                    if target not in bindings:
                        bindings[target] = value
                        changed = True
                    elif bindings[target] != value:
                        ambiguous_bindings.add(target)
    forbidden = sorted(imports & FORBIDDEN_IMPORTS)
    if forbidden:
        fail("processor imports forbidden network/process modules: %s" % ", ".join(forbidden))
    unexpected = sorted(imports - PROCESSOR_ALLOWED_IMPORTS)
    if unexpected:
        fail("processor imports modules outside the stdlib allowlist: %s" % ", ".join(unexpected))
    _validate_ctypes_boundary(tree)
    for reference in bindings.values():
        if _dangerous_reference(reference):
            fail("processor must not resolve dangerous callable or process API %s" % reference)
        _validate_ctypes_reference(reference)
    for node in ast.walk(tree):
        reference = _qualified_call_name(node, bindings, strings)
        if isinstance(node, ast.Attribute) and node.attr in {
            "__dict__", "__getattribute__", "__getattr__",
        }:
            fail("processor must not use reflective attribute lookup")
        if isinstance(node, (ast.Attribute, ast.Call)) and reference:
            if _dangerous_reference(reference):
                fail("processor must not resolve dangerous callable or process API %s" % reference)
            _validate_ctypes_reference(reference)
        if isinstance(node, ast.Call):
            callee = _qualified_call_name(node.func, bindings, strings)
            if isinstance(node.func, ast.Name) and node.func.id in ambiguous_bindings:
                fail("processor must not call an ambiguously rebound callable alias")
            if callee in {"globals", "locals", "vars", "builtins.globals", "builtins.locals", "builtins.vars"}:
                fail("processor must not use reflective namespace lookup")
            if callee in {"getattr", "builtins.getattr"}:
                if len(node.args) < 2 or _static_string(node.args[1], strings) is None:
                    fail("processor must not use dynamic getattr member names")
            if callee in {
                "ctypes.WinDLL", "ctypes.CDLL", "ctypes.PyDLL", "ctypes.OleDLL"
            }:
                library = _static_string(node.args[0], strings) if node.args else None
                if callee != "ctypes.WinDLL" or library is None or library.casefold() != "kernel32":
                    fail("processor must not load unapproved native libraries")
            if _dangerous_reference(callee):
                fail("processor must not call process API or dangerous callable %s" % callee)
        if isinstance(node, ast.Subscript):
            base = _qualified_call_name(node.value, bindings, strings)
            if base and (
                base.startswith("ctypes.WinDLL[")
                or base.startswith("ctypes.CDLL[")
                or base.startswith("ctypes.PyDLL[")
                or base.startswith("ctypes.OleDLL[")
                or base.startswith("ctypes.windll.")
                or base.startswith("ctypes.cdll.")
                or base.startswith("ctypes.pydll.")
                or base.startswith("ctypes.oledll.")
            ):
                fail("processor must not resolve native APIs by subscription")


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


def validate_source(source_entries=None):
    if source_entries is None:
        source_entries = _snapshot_package_sources()
    if not isinstance(source_entries, dict) or set(source_entries) != set(PACKAGE_SOURCES):
        fail("source snapshot does not match the exact package allowlist")
    for archive_name in PACKAGE_SOURCES:
        validate_archive_name(archive_name)
        _package_snapshot_payload(source_entries, archive_name, 100 * 1024 * 1024)
    validate_text_files(source_entries)
    manifest = validate_manifest(source_entries)
    validate_skill(source_entries)
    validate_processor_boundary(source_entries)
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
    return source_entries


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
    if not isinstance(entries, dict) or set(entries) != set(PACKAGE_SOURCES):
        fail("source snapshot does not match the exact package allowlist")
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


def _validate_archive_bytes(
    payload, source_entries=None, smoke=True, observation_payload=None
):
    if not payload or len(payload) > 100 * 1024 * 1024:
        fail("archive is empty or exceeds 100 MiB")
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
        if observation_payload is None:
            fail("smoke validation requires an immutable observation snapshot")
        _smoke_archive(payload, observation_payload)
    return payload


def validate_archive(path, smoke=True):
    archive_path = Path(path)
    payload = _read_regular_bytes(archive_path, 100 * 1024 * 1024)
    source_entries = _snapshot_package_sources()
    observation_payload = None
    if smoke:
        observation_payload = _read_regular_bytes(
            OBSERVATIONS_PATH, 2 * 1024 * 1024, boundary=ROOT
        )
    _validate_archive_bytes(
        payload,
        source_entries=source_entries,
        smoke=smoke,
        observation_payload=observation_payload,
    )
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


def _smoke_archive(archive_payload, observation_payload):
    with tempfile.TemporaryDirectory(prefix="sai-extracted-smoke-") as raw:
        root = Path(raw) / "패키지 경로 with spaces"
        root.mkdir()
        _safe_extract(archive_payload, root)
        observation = Path(raw) / "observations.json"
        observation.write_bytes(observation_payload)
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
    if os.name == "nt":
        _publish_bytes_windows(path, payload, mode)
    else:
        _publish_bytes_posix(path, payload, mode)


def _publish_bytes_posix(path, payload, mode):
    if not (_OPEN_SUPPORTS_DIR_FD and _RENAME_SUPPORTS_DIR_FD and _UNLINK_SUPPORTS_DIR_FD):
        fail("descriptor-bound publish is unavailable")
    parent_descriptor = _open_posix_plain_directory(path.parent)
    descriptor = -1
    temporary_name = None
    try:
        try:
            existing = os.stat(
                path.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            not stat.S_ISREG(existing.st_mode) or _is_link_or_reparse(existing)
        ):
            fail("output path must be missing or a regular non-link file: %s" % path)
        flags = (
            os.O_RDWR | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        for unused_attempt in range(128):
            candidate = ".%s.tmp-%s" % (path.name, os.urandom(12).hex())
            try:
                descriptor = os.open(
                    candidate, flags, 0o600, dir_fd=parent_descriptor
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if descriptor < 0 or temporary_name is None:
            fail("could not allocate a unique descriptor-bound output")
        with os.fdopen(descriptor, "w+b") as handle:
            descriptor = -1
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _is_link_or_reparse(opened):
                fail("temporary output is not a regular file")
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
            try:
                destination = os.stat(
                    path.name, dir_fd=parent_descriptor, follow_symlinks=False
                )
            except FileNotFoundError:
                destination = None
            if destination is not None and (
                not stat.S_ISREG(destination.st_mode) or _is_link_or_reparse(destination)
            ):
                fail("output path changed to a non-regular file before publish")
            os.rename(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            temporary_name = None
            published = os.stat(
                path.name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            if (
                not stat.S_ISREG(published.st_mode)
                or _is_link_or_reparse(published)
                or (published.st_dev, published.st_ino)
                != (opened.st_dev, opened.st_ino)
            ):
                fail("published output identity differs from the written descriptor")
            handle.seek(0)
            if handle.read(len(payload) + 1) != payload:
                fail("published descriptor bytes differ from the requested payload")
            check_descriptor = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_descriptor,
            )
            try:
                result = os.fstat(check_descriptor)
                if (
                    not stat.S_ISREG(result.st_mode)
                    or _is_link_or_reparse(result)
                    or (result.st_dev, result.st_ino)
                    != (opened.st_dev, opened.st_ino)
                    or os.read(check_descriptor, len(payload) + 1) != payload
                ):
                    fail("published output does not match the written descriptor")
            finally:
                os.close(check_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)


def _publish_bytes_windows(path, payload, mode):
    descriptor = -1
    locks = None
    try:
        try:
            existing_payload = _read_regular_bytes(
                path, len(payload), boundary=DIST
            )
        except FileNotFoundError:
            existing_payload = None
        if existing_payload is not None:
            if existing_payload == payload:
                return
            fail("refusing to replace an existing Windows output with different bytes")
        descriptor, locks, identity = _create_windows_regular_file(path)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _is_link_or_reparse(opened):
                fail("temporary output is not a regular file")
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            check_descriptor = -1
            check_locks = None
            try:
                check_descriptor, check_locks, check_identity = (
                    _open_windows_regular_file(path)
                )
                with os.fdopen(check_descriptor, "rb") as check:
                    check_descriptor = -1
                    if check_identity != identity or check.read(len(payload) + 1) != payload:
                        fail("published Windows output differs from its written handle")
            finally:
                if check_descriptor >= 0:
                    os.close(check_descriptor)
                _close_windows_directory_handles(check_locks)
        if _read_regular_bytes(path, len(payload), boundary=DIST) != payload:
            fail("published Windows output differs from the requested payload")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _close_windows_directory_handles(locks)


def build_release(source_entries=None, observation_payload=None):
    if source_entries is None:
        source_entries = _snapshot_package_sources()
    validate_source(source_entries)
    if observation_payload is None:
        observation_payload = _read_regular_bytes(
            OBSERVATIONS_PATH, 2 * 1024 * 1024, boundary=ROOT
        )
    _prepare_dist()
    output = DIST / ARCHIVE_NAME
    checksum_path = output.with_suffix(".zip.sha256")
    _assert_dist_output(output)
    _assert_dist_output(checksum_path)
    first_bytes = _canonical_archive_bytes(source_entries)
    second_bytes = _canonical_archive_bytes(source_entries)
    if first_bytes != second_bytes:
        fail("independent builds are not byte-identical")
    _validate_archive_bytes(
        first_bytes,
        source_entries=source_entries,
        smoke=True,
        observation_payload=observation_payload,
    )
    digest = sha256_bytes(first_bytes)
    publish_bytes(output, first_bytes)
    checksum = ("%s  %s\n" % (digest, output.name)).encode("ascii")
    publish_bytes(checksum_path, checksum)
    published = _read_regular_bytes(output, 100 * 1024 * 1024, boundary=DIST)
    if published != first_bytes:
        fail("published archive differs from the validated release bytes")
    _validate_archive_bytes(
        published, source_entries=source_entries, smoke=False
    )
    print("release=%s" % output)
    print("sha256=%s" % digest)
    print("reproducible_builds=2")
    return output


def _validate_matrix_identity(provenance, expected_commit=None):
    if not isinstance(provenance, dict) or set(provenance) != PROVENANCE_FIELDS:
        fail("runtime provenance fields are incomplete or unsupported")
    if any(not isinstance(value, str) or not value for value in provenance.values()):
        fail("runtime provenance values must be nonempty strings")
    expected = EXPECTED_MATRIX.get(provenance["job_id"])
    if expected is None:
        fail("runtime provenance has an unexpected matrix job ID")
    actual = {field: provenance[field] for field in expected}
    if actual != expected:
        fail("runtime provenance does not match the expected job platform")
    if not re.fullmatch(r"[0-9a-f]{40}", provenance["commit_sha"]):
        fail("runtime provenance commit SHA is invalid")
    if expected_commit is not None and provenance["commit_sha"] != expected_commit:
        fail("runtime provenance commit SHA does not match the aggregate checkout")
    return provenance


def _current_git_commit():
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    value = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", value):
        fail("could not resolve the exact aggregate checkout commit")
    return value


def _matrix_provenance_from_environment():
    names = {
        "job_id": "SAI_MATRIX_ID",
        "runner_label": "SAI_MATRIX_OS_LABEL",
        "runner_os": "SAI_RUNNER_OS",
        "runner_arch": "SAI_RUNNER_ARCH",
        "python": "SAI_MATRIX_PYTHON",
        "commit_sha": "SAI_COMMIT_SHA",
    }
    present = {field: os.environ.get(name) for field, name in names.items()}
    if not any(value is not None for value in present.values()):
        return None
    if any(value is None for value in present.values()):
        fail("matrix runtime provenance environment is incomplete")
    runtime_python = "%d.%d" % (sys.version_info[0], sys.version_info[1])
    if present["python"] != runtime_python:
        fail("matrix Python provenance does not match the running interpreter")
    if present["commit_sha"] != _current_git_commit():
        fail("matrix commit provenance does not match the checked-out commit")
    return _validate_matrix_identity(present, expected_commit=present["commit_sha"])


def _validate_runtime_evidence_report(report, require_provenance=False):
    if not isinstance(report, dict) or set(report) != {
        "schema_version", "fixture", "source_archive_sha256",
        "observation_sha256", "artifacts", "provenance",
    }:
        fail("runtime evidence has incomplete or unsupported top-level fields")
    if report["schema_version"] != 1 or report["fixture"] != "tests/fixtures/observations.json":
        fail("runtime evidence metadata is unexpected")
    for field in ("source_archive_sha256", "observation_sha256"):
        if not isinstance(report[field], str) or not re.fullmatch(r"[0-9a-f]{64}", report[field]):
            fail("runtime evidence %s is invalid" % field)
    provenance = report["provenance"]
    if provenance is None:
        if require_provenance:
            fail("matrix runtime evidence is missing provenance")
    else:
        _validate_matrix_identity(provenance)
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


def _runtime_evidence_report(source_entries, observation_payload, provenance=None):
    namespace = {}
    source = _package_snapshot_payload(
        source_entries,
        "skills/organize-screenshot-inbox/scripts/screenshot_inbox.py",
        2 * 1024 * 1024,
    ).decode("utf-8")
    exec(compile(source, str(PROCESSOR), "exec"), namespace)
    observations = _load_json_bytes(
        observation_payload, "runtime observation fixture"
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
        "source_archive_sha256": sha256_bytes(
            _canonical_archive_bytes(source_entries)
        ),
        "observation_sha256": sha256_bytes(observation_payload),
        "provenance": provenance,
        "artifacts": {
            name: {"sha256": sha256_bytes(payload), "bytes": len(payload)}
            for name, payload in sorted(artifacts.items())
        },
    }
    return _validate_runtime_evidence_report(report)


def _runtime_evidence_bytes(report):
    _validate_runtime_evidence_report(report)
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _load_runtime_evidence(payload, require_provenance=False):
    report = _load_json_bytes(payload, "runtime evidence")
    return _validate_runtime_evidence_report(
        report, require_provenance=require_provenance
    )


def runtime_evidence(
    source_entries=None, observation_payload=None, provenance=None
):
    if source_entries is None:
        source_entries = _snapshot_package_sources()
    validate_source(source_entries)
    if observation_payload is None:
        observation_payload = _read_regular_bytes(
            OBSERVATIONS_PATH, 2 * 1024 * 1024, boundary=ROOT
        )
    if provenance is None:
        provenance = _matrix_provenance_from_environment()
    report = _runtime_evidence_report(
        source_entries, observation_payload, provenance=provenance
    )
    _prepare_dist()
    output = DIST / "runtime-artifact-evidence.json"
    _assert_dist_output(output)
    publish_bytes(output, _runtime_evidence_bytes(report))
    print(json.dumps(report, sort_keys=True))
    return report


def verify_all():
    source_entries = _snapshot_package_sources()
    observation_payload = _read_regular_bytes(
        OBSERVATIONS_PATH, 2 * 1024 * 1024, boundary=ROOT
    )
    validate_source(source_entries)
    run_tests()
    build_release(source_entries, observation_payload)
    runtime_evidence(source_entries, observation_payload)
    print("all_verification=PASS")


def compare_matrix(root, expect_count):
    if expect_count != len(EXPECTED_MATRIX):
        fail("matrix comparison must require the exact supported job count")
    expected_commit = os.environ.get("SAI_COMMIT_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        fail("aggregate checkout commit SHA provenance is missing or invalid")
    if expected_commit != _current_git_commit():
        fail("aggregate commit provenance does not match the checked-out commit")
    source_entries = _snapshot_package_sources()
    validate_source(source_entries)
    observation_payload = _read_regular_bytes(
        OBSERVATIONS_PATH, 2 * 1024 * 1024, boundary=ROOT
    )
    matrix_root = Path(os.path.abspath(os.fspath(root)))
    root_entries = _enumerate_plain_directory(matrix_root)
    expected_directories = {
        "release-%s" % job_id for job_id in EXPECTED_MATRIX
    }
    if set(root_entries) != expected_directories:
        fail("matrix root does not contain the exact expected release job directories")
    for name, result in root_entries.items():
        if not stat.S_ISDIR(result.st_mode):
            fail("matrix artifact entry is not a plain directory: %s" % name)
    expected_bundle_names = {
        ARCHIVE_NAME,
        ARCHIVE_NAME + ".sha256",
        "runtime-artifact-evidence.json",
    }
    candidates = []
    for job_id in sorted(EXPECTED_MATRIX):
        parent = matrix_root / ("release-%s" % job_id)
        bundle_entries = _enumerate_plain_directory(parent, boundary=matrix_root)
        if set(bundle_entries) != expected_bundle_names:
            fail("matrix artifact directory has missing or extra files: %s" % parent)
        for name, result in bundle_entries.items():
            if not stat.S_ISREG(result.st_mode):
                fail("matrix bundle entry is not a regular file: %s" % (parent / name))
        candidates.append((job_id, parent / ARCHIVE_NAME))
    archive_payloads = []
    identities = []
    expected_report = _runtime_evidence_report(
        source_entries, observation_payload, provenance=None
    )
    expected_core = dict(expected_report)
    expected_core.pop("provenance")
    for job_id, candidate in candidates:
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
        evidence_report = _load_runtime_evidence(
            evidence_payload, require_provenance=True
        )
        provenance = evidence_report["provenance"]
        _validate_matrix_identity(provenance, expected_commit=expected_commit)
        if provenance["job_id"] != job_id:
            fail("matrix runtime provenance does not match its artifact directory")
        evidence_core = dict(evidence_report)
        evidence_core.pop("provenance")
        if evidence_core != expected_core:
            fail("matrix runtime evidence does not match the current exact artifacts")
        if evidence_report["source_archive_sha256"] != digest:
            fail("matrix runtime evidence is not bound to its exact source archive")
        archive_payloads.append(payload)
        identities.append(provenance)
    if any(payload != archive_payloads[0] for payload in archive_payloads[1:]):
        fail("matrix archives are not byte-identical")
    payload = archive_payloads[0]
    _smoke_archive(payload, observation_payload)
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
        "archives_compared": len(archive_payloads),
        "runtime_evidence_compared": len(identities),
        "runtime_artifacts": expected_report["artifacts"],
        "matrix_identities": identities,
        "commit_sha": expected_commit,
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
            verify_all()
    except (VerifyError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
