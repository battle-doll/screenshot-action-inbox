#!/usr/bin/env python3
"""Build a deterministic, source-linked action inbox from reviewed observations.

This program intentionally does not open images, read EXIF, use the network,
execute commands, or mutate source screenshots. It accepts bounded JSON that a
host model created after visual inspection and emits review artifacts only.
"""

from __future__ import print_function

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import sys
import tempfile
import unicodedata


SCHEMA_VERSION = "1.0"
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_SOURCES = 100
MAX_ITEMS = 500
MAX_QUESTIONS = 100
MAX_STRING_LENGTH = 4096
MAX_DEPTH = 10
MAX_IMAGE_BYTES = 50 * 1024 * 1024

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif"}
CATEGORIES = {"action", "event", "receipt", "reference", "unknown"}
CONFIDENCE = {"high", "medium", "low"}
PRIORITIES = {"high", "medium", "low", "unknown"}
ITEM_STATUS = {"open", "reference", "needs_review", "complete"}
SOURCE_STATUS = {"reviewed", "unreadable", "unsupported", "redaction_required"}
ARCHIVE_RECOMMENDATIONS = {"keep", "archive", "review"}
ARCHIVE_BUCKETS = {"actions", "events", "receipts", "references", "mixed", "unknown"}
CALENDAR_STATUS = {"draft", "needs_review", "none"}

TOP_LEVEL_FIELDS = {
    "schema_version", "generated_at", "batch_title", "sources", "items", "questions"
}
SOURCE_FIELDS = {
    "id", "relative_path", "capture_date", "sha256", "archive_recommendation",
    "archive_bucket", "status"
}
ITEM_FIELDS = {
    "id", "category", "title", "details", "source_ids", "evidence", "confidence",
    "priority", "owner", "due", "amount", "calendar", "duplicate_group", "status"
}
AMOUNT_FIELDS = {"value", "currency"}
CALENDAR_FIELDS = {"status", "start", "end", "location"}

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
AMOUNT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
RFC3339_SECONDS_RE = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?P<offset>Z|[+-][0-9]{2}:[0-9]{2})$"
)
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)\b(password|passcode|api[_ -]?key|secret|access[_ -]?token|"
        r"recovery[_ -]?phrase|otp|verification[_ -]?code|비밀번호|인증번호)\b"
        r"\s*[:=]\s*\S+"
    ),
)

WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$",
    *("COM%d" % n for n in range(1, 10)),
    *("LPT%d" % n for n in range(1, 10)),
}

# Python 3.9 through 3.14 ship different current Unicode databases. Use the
# standard library's frozen Unicode 3.2 profile for normalization decisions so
# accepted input produces the same paths and collision keys on every supported
# runtime. Characters introduced later remain supported, but are treated as
# opaque instead of acquiring version-dependent mappings.
FROZEN_UCD = unicodedata.ucd_3_2_0


def _stable_normalize(form, value):
    # Some runtimes let newly assigned combining characters influence
    # ucd_3_2_0 normalization despite reporting them as unassigned. Treat each
    # post-3.2 code point as an opaque boundary and normalize only frozen-data
    # runs, which keeps the result stable while preserving newer characters.
    pieces = []
    frozen_run = []
    for char in value:
        if FROZEN_UCD.category(char) == "Cn":
            if frozen_run:
                pieces.append(FROZEN_UCD.normalize(form, "".join(frozen_run)))
                frozen_run = []
            pieces.append(char)
        else:
            frozen_run.append(char)
    if frozen_run:
        pieces.append(FROZEN_UCD.normalize(form, "".join(frozen_run)))
    return "".join(pieces)


def _stable_casefold(value):
    normalized = _stable_normalize("NFC", value)
    folded = "".join(
        char.casefold() if FROZEN_UCD.category(char) != "Cn" else char
        for char in normalized
    )
    return _stable_normalize("NFC", folded)


class InboxError(ValueError):
    """A user-correctable validation or output error."""


def _reject_constant(value):
    raise InboxError("non-finite JSON number is not allowed: %s" % value)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if any(FROZEN_UCD.category(char) == "Cc" for char in key):
            raise InboxError("JSON object keys must not contain control characters")
        if key in result:
            raise InboxError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _bounded_walk(value, depth=0):
    if depth > MAX_DEPTH:
        raise InboxError("JSON nesting exceeds %d levels" % MAX_DEPTH)
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise InboxError("a string exceeds %d characters" % MAX_STRING_LENGTH)
        if "\x00" in value:
            raise InboxError("NUL characters are not allowed")
        if any(FROZEN_UCD.category(char) == "Cc" and char not in "\n\t" for char in value):
            raise InboxError("control characters are not allowed")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise InboxError("unpaired Unicode surrogate is not allowed")
    elif isinstance(value, list):
        for item in value:
            _bounded_walk(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InboxError("JSON object keys must be strings")
            _bounded_walk(key, depth + 1)
            _bounded_walk(item, depth + 1)


def load_json_strict(path):
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise InboxError("cannot read input: %s" % exc)
    if size > MAX_INPUT_BYTES:
        raise InboxError("input exceeds %d bytes" % MAX_INPUT_BYTES)
    try:
        raw = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise InboxError("input must be readable UTF-8 JSON: %s" % exc)
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except InboxError:
        raise
    except json.JSONDecodeError as exc:
        raise InboxError("invalid JSON at line %d column %d" % (exc.lineno, exc.colno))
    _bounded_walk(value)
    return value


def _require_object(value, label):
    if not isinstance(value, dict):
        raise InboxError("%s must be an object" % label)
    return value


def _require_list(value, label):
    if not isinstance(value, list):
        raise InboxError("%s must be an array" % label)
    return value


def _require_string(value, label, allow_empty=False, maximum=MAX_STRING_LENGTH):
    if not isinstance(value, str):
        raise InboxError("%s must be a string" % label)
    if not allow_empty and not value.strip():
        raise InboxError("%s must not be empty" % label)
    if len(value) > maximum:
        raise InboxError("%s exceeds %d characters" % (label, maximum))
    for char in value:
        if FROZEN_UCD.category(char) == "Cc" and char not in "\n\t":
            raise InboxError("%s contains a control character" % label)
    return value


def _optional_string(value, label, maximum=MAX_STRING_LENGTH):
    if value is None:
        return None
    return _require_string(value, label, allow_empty=False, maximum=maximum)


def _closed_fields(obj, allowed, label):
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise InboxError("%s has unsupported fields: %s" % (label, ", ".join(unknown)))


def _parse_date(value, label):
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise InboxError("%s must be a real date in YYYY-MM-DD form" % label)
    if parsed.isoformat() != value:
        raise InboxError("%s must use canonical YYYY-MM-DD form" % label)
    return parsed


def _parse_aware_datetime(value, label):
    if not isinstance(value, str):
        raise InboxError("%s must be an RFC 3339 string" % label)
    match = RFC3339_SECONDS_RE.fullmatch(value)
    if not match:
        raise InboxError("%s must use YYYY-MM-DDTHH:MM:SSZ or an explicit +/-HH:MM offset" % label)
    try:
        fields = {name: int(match.group(name)) for name in ("year", "month", "day", "hour", "minute", "second")}
        offset_text = match.group("offset")
        if offset_text == "Z":
            timezone = dt.timezone.utc
        else:
            offset_hour = int(offset_text[1:3])
            offset_minute = int(offset_text[4:6])
            if offset_text == "-00:00":
                raise ValueError("unknown local offset is not accepted")
            if offset_hour > 23 or offset_minute > 59:
                raise ValueError("invalid UTC offset")
            delta = dt.timedelta(hours=offset_hour, minutes=offset_minute)
            if offset_text[0] == "-":
                delta = -delta
            timezone = dt.timezone(delta)
        return dt.datetime(tzinfo=timezone, **fields)
    except (TypeError, ValueError, OverflowError):
        raise InboxError("%s must be a real RFC 3339 timestamp" % label)


def _parse_date_or_datetime(value, label):
    if not isinstance(value, str):
        raise InboxError("%s must be a string" % label)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return "date", _parse_date(value, label)
    return "datetime", _parse_aware_datetime(value, label)


def _valid_id(value, label):
    text = _require_string(value, label, maximum=64)
    if not ID_RE.fullmatch(text):
        raise InboxError("%s must use letters, digits, dot, underscore, or hyphen" % label)
    _assert_no_sensitive_value(text, label)
    return text


def _path_collision_key(value):
    return _stable_casefold(value)


def validate_relative_path(value, label="relative_path"):
    text = _require_string(value, label, maximum=512)
    if text.startswith(("/", "\\")) or text.startswith("//"):
        raise InboxError("%s must be relative" % label)
    if "\\" in text:
        raise InboxError("%s must use forward slashes" % label)
    if PureWindowsPath(text).drive or PureWindowsPath(text).is_absolute():
        raise InboxError("%s must not contain a Windows drive or UNC root" % label)
    posix = PurePosixPath(text)
    parts = text.split("/")
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise InboxError("%s contains an empty, current, or parent segment" % label)
    for part in parts:
        if len(part) > 180:
            raise InboxError("%s contains an overlong segment" % label)
        if any(ord(char) < 32 or ord(char) == 127 for char in part):
            raise InboxError("%s contains a control character" % label)
        if any(char in part for char in '<>:"|?*`'):
            raise InboxError("%s contains a cross-platform unsafe character" % label)
        if part.endswith((".", " ")):
            raise InboxError("%s contains a segment ending in dot or space" % label)
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED:
            raise InboxError("%s contains a Windows reserved name" % label)
    return text


def _luhn_valid(digits):
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        number = int(char)
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def _assert_no_sensitive_value(text, label):
    if text is None:
        return
    normalized = _stable_normalize("NFKC", text)
    for pattern in SECRET_PATTERNS:
        if pattern.search(normalized):
            raise InboxError("%s appears to contain a secret; redact it first" % label)
    for match in re.finditer(
        r"(?<![0-9])(?:[0-9][^0-9A-Za-z]{0,3}){12,18}[0-9](?![0-9])",
        normalized,
    ):
        candidate = match.group(0)
        if any(FROZEN_UCD.category(char).startswith("L") for char in candidate):
            continue
        digits = "".join(char for char in candidate if char.isascii() and char.isdigit())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            raise InboxError("%s appears to contain a full payment-card number" % label)


def _validate_source(raw, index):
    label = "sources[%d]" % index
    source = _require_object(raw, label)
    _closed_fields(source, SOURCE_FIELDS, label)
    source_id = _valid_id(source.get("id"), label + ".id")
    relative_path = validate_relative_path(source.get("relative_path"), label + ".relative_path")
    _assert_no_sensitive_value(relative_path, label + ".relative_path")
    capture_date = source.get("capture_date")
    if capture_date is not None:
        _parse_date(capture_date, label + ".capture_date")
    digest = source.get("sha256")
    if digest is not None:
        digest = _require_string(digest, label + ".sha256", maximum=64).lower()
        if not SHA256_RE.fullmatch(digest):
            raise InboxError("%s.sha256 must contain 64 lowercase hex characters" % label)
    recommendation = source.get("archive_recommendation", "review")
    if recommendation not in ARCHIVE_RECOMMENDATIONS:
        raise InboxError("%s.archive_recommendation is invalid" % label)
    bucket = source.get("archive_bucket", "unknown")
    if bucket not in ARCHIVE_BUCKETS:
        raise InboxError("%s.archive_bucket is invalid" % label)
    status_value = source.get("status", "reviewed")
    if status_value not in SOURCE_STATUS:
        raise InboxError("%s.status is invalid" % label)
    return {
        "id": source_id,
        "relative_path": relative_path,
        "capture_date": capture_date,
        "sha256": digest,
        "archive_recommendation": recommendation,
        "archive_bucket": bucket,
        "status": status_value,
    }


def _validate_amount(raw, label):
    if raw is None:
        return None
    amount = _require_object(raw, label)
    _closed_fields(amount, AMOUNT_FIELDS, label)
    value = _require_string(amount.get("value"), label + ".value", maximum=64)
    currency = _require_string(amount.get("currency"), label + ".currency", maximum=3)
    if not AMOUNT_RE.fullmatch(value):
        raise InboxError("%s.value must be a plain decimal string" % label)
    if not CURRENCY_RE.fullmatch(currency):
        raise InboxError("%s.currency must be a three-letter uppercase code" % label)
    _assert_no_sensitive_value(value, label + ".value")
    return {"value": value, "currency": currency}


def _validate_calendar(raw, label):
    if raw is None:
        return None
    calendar = _require_object(raw, label)
    _closed_fields(calendar, CALENDAR_FIELDS, label)
    status_value = calendar.get("status")
    if status_value not in CALENDAR_STATUS:
        raise InboxError("%s.status is invalid" % label)
    location = _optional_string(calendar.get("location"), label + ".location", maximum=500)
    _assert_no_sensitive_value(location, label + ".location")
    if status_value != "draft":
        if calendar.get("start") is not None or calendar.get("end") is not None:
            raise InboxError("%s may include dates only when status is draft" % label)
        return {"status": status_value, "start": None, "end": None, "location": location}
    start = calendar.get("start")
    if start is None:
        raise InboxError("%s.start is required for a calendar draft" % label)
    start_kind, start_value = _parse_date_or_datetime(start, label + ".start")
    end = calendar.get("end")
    end_kind = None
    end_value = None
    if end is not None:
        end_kind, end_value = _parse_date_or_datetime(end, label + ".end")
        if end_kind != start_kind:
            raise InboxError("%s start and end must both be dates or datetimes" % label)
        if end_value <= start_value:
            raise InboxError("%s.end must be after start" % label)
    elif start_kind == "date":
        end_value = start_value + dt.timedelta(days=1)
        end = end_value.isoformat()
    return {
        "status": status_value,
        "start": start,
        "end": end,
        "location": location,
        "_kind": start_kind,
    }


def _validate_item(raw, index, source_ids):
    label = "items[%d]" % index
    item = _require_object(raw, label)
    _closed_fields(item, ITEM_FIELDS, label)
    item_id = _valid_id(item.get("id"), label + ".id")
    category = item.get("category")
    if category not in CATEGORIES:
        raise InboxError("%s.category is invalid" % label)
    title = _require_string(item.get("title"), label + ".title", maximum=300)
    details = _optional_string(item.get("details"), label + ".details")
    evidence = _require_string(item.get("evidence"), label + ".evidence", maximum=1000)
    refs = _require_list(item.get("source_ids"), label + ".source_ids")
    if not refs:
        raise InboxError("%s.source_ids must not be empty" % label)
    normalized_refs = []
    for ref_index, ref in enumerate(refs):
        source_id = _valid_id(ref, "%s.source_ids[%d]" % (label, ref_index))
        if source_id not in source_ids:
            raise InboxError("%s references unknown source %s" % (label, source_id))
        if source_id not in normalized_refs:
            normalized_refs.append(source_id)
    confidence = item.get("confidence")
    if confidence not in CONFIDENCE:
        raise InboxError("%s.confidence is invalid" % label)
    priority = item.get("priority", "unknown")
    if priority not in PRIORITIES:
        raise InboxError("%s.priority is invalid" % label)
    owner = _optional_string(item.get("owner"), label + ".owner", maximum=200)
    due = item.get("due")
    if due is not None:
        _parse_date_or_datetime(due, label + ".due")
    duplicate_group = item.get("duplicate_group")
    if duplicate_group is not None:
        duplicate_group = _valid_id(duplicate_group, label + ".duplicate_group")
    status_value = item.get("status", "open")
    if status_value not in ITEM_STATUS:
        raise InboxError("%s.status is invalid" % label)
    amount = _validate_amount(item.get("amount"), label + ".amount")
    calendar = _validate_calendar(item.get("calendar"), label + ".calendar")
    for field_name, text in (
        ("title", title), ("details", details), ("evidence", evidence), ("owner", owner)
    ):
        _assert_no_sensitive_value(text, label + "." + field_name)
    return {
        "id": item_id,
        "category": category,
        "title": title,
        "details": details,
        "source_ids": sorted(normalized_refs),
        "evidence": evidence,
        "confidence": confidence,
        "priority": priority,
        "owner": owner,
        "due": due,
        "amount": amount,
        "calendar": calendar,
        "duplicate_group": duplicate_group,
        "status": status_value,
    }


def validate_observations(raw):
    _bounded_walk(raw)
    root = _require_object(raw, "root")
    _closed_fields(root, TOP_LEVEL_FIELDS, "root")
    if root.get("schema_version") != SCHEMA_VERSION:
        raise InboxError("schema_version must be %s" % SCHEMA_VERSION)
    generated_at = _require_string(root.get("generated_at"), "generated_at", maximum=64)
    _parse_aware_datetime(generated_at, "generated_at")
    batch_title = _require_string(root.get("batch_title"), "batch_title", maximum=200)
    _assert_no_sensitive_value(batch_title, "batch_title")
    source_rows = _require_list(root.get("sources"), "sources")
    if not source_rows or len(source_rows) > MAX_SOURCES:
        raise InboxError("sources must contain 1 to %d records" % MAX_SOURCES)
    sources = [_validate_source(row, index) for index, row in enumerate(source_rows)]
    source_ids = [source["id"] for source in sources]
    if len(set(source_ids)) != len(source_ids):
        raise InboxError("source IDs must be unique")
    for source in sources:
        if source["archive_recommendation"] == "archive":
            if source["status"] != "reviewed":
                raise InboxError("archive recommendations require a fully reviewed source")
            if source["sha256"] is None:
                raise InboxError("archive recommendations require a source SHA-256")
    path_keys = [_path_collision_key(source["relative_path"]) for source in sources]
    if len(set(path_keys)) != len(path_keys):
        raise InboxError("source paths collide after Unicode/case normalization")
    item_rows = _require_list(root.get("items"), "items")
    if len(item_rows) > MAX_ITEMS:
        raise InboxError("items exceeds %d records" % MAX_ITEMS)
    items = [_validate_item(row, index, set(source_ids)) for index, row in enumerate(item_rows)]
    source_map = {source["id"]: source for source in sources}
    for item in items:
        if item["confidence"] == "low" and item["status"] != "needs_review":
            raise InboxError("low-confidence items must use needs_review status")
        if item["status"] == "needs_review" and item["calendar"] and item["calendar"]["status"] == "draft":
            raise InboxError("items needing review cannot create a calendar draft")
        if item["calendar"] and item["calendar"]["status"] == "needs_review" and item["status"] != "needs_review":
            raise InboxError("ambiguous calendar items must use needs_review status")
        if item["calendar"] and item["calendar"]["status"] == "draft":
            if item["category"] not in {"action", "event"}:
                raise InboxError("calendar drafts are limited to action or event items")
            if item["due"] is not None and item["due"] != item["calendar"]["start"]:
                raise InboxError("item due and calendar start must match when both are present")
            if any(source_map[source_id]["sha256"] is None for source_id in item["source_ids"]):
                raise InboxError("calendar drafts require SHA-256 for every referenced source")
        if (
            any(source_map[source_id]["status"] != "reviewed" for source_id in item["source_ids"])
            and item["status"] != "needs_review"
        ):
            raise InboxError("items from incomplete sources must use needs_review status")
    item_ids = [item["id"] for item in items]
    if len(set(item_ids)) != len(item_ids):
        raise InboxError("item IDs must be unique")
    raw_questions = root.get("questions", [])
    questions_list = _require_list(raw_questions, "questions")
    if len(questions_list) > MAX_QUESTIONS:
        raise InboxError("questions exceeds %d entries" % MAX_QUESTIONS)
    questions = []
    for index, question in enumerate(questions_list):
        text = _require_string(question, "questions[%d]" % index, maximum=1000)
        _assert_no_sensitive_value(text, "questions[%d]" % index)
        if text not in questions:
            questions.append(text)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "batch_title": batch_title,
        "sources": sorted(sources, key=lambda row: row["id"]),
        "items": sorted(items, key=lambda row: row["id"]),
        "questions": sorted(questions),
    }


def _canonical_json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _hash_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _markdown(value):
    if value is None or value == "":
        return "UNKNOWN"
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped = text.replace("\\", "\\\\")
    for char in "`*_{}[]<>()#+-.!|":
        escaped = escaped.replace(char, "\\" + char)
    return escaped


def _source_names(item, source_map):
    return [source_map[source_id]["relative_path"] for source_id in item["source_ids"]]


def build_digest(data):
    source_map = {row["id"]: row for row in data["sources"]}
    reviewed_sources = [source for source in data["sources"] if source["status"] == "reviewed"]
    incomplete_sources = [source for source in data["sources"] if source["status"] != "reviewed"]
    needs_review_count = (
        sum(1 for item in data["items"] if item["status"] == "needs_review")
        + len(incomplete_sources)
    )
    counts = {category: 0 for category in sorted(CATEGORIES)}
    for item in data["items"]:
        counts[item["category"]] += 1
    lines = [
        "# %s" % _markdown(data["batch_title"]),
        "",
        "> Review draft. No calendar event, message, deletion, or file move was executed.",
        "",
        "- Generated from observations: `%s`" % data["generated_at"],
        "- Sources in batch: **%d**" % len(data["sources"]),
        "- Sources reviewed: **%d**" % len(reviewed_sources),
        "- Extracted items: **%d**" % len(data["items"]),
        "- Needs review: **%d**" % needs_review_count,
        "",
    ]
    lines.extend(["## Incomplete sources", ""])
    if incomplete_sources:
        lines.extend([
            "| Source | Status |",
            "| --- | --- |",
        ])
        for source in incomplete_sources:
            lines.append("| %s | %s |" % (
                _markdown(source["relative_path"]),
                _markdown(source["status"]),
            ))
    else:
        lines.append("None.")
    lines.append("")
    section_order = (
        ("action", "Actions"),
        ("event", "Events"),
        ("receipt", "Receipts"),
        ("reference", "References"),
        ("unknown", "Unknown or unreadable"),
    )
    for category, heading in section_order:
        rows = [item for item in data["items"] if item["category"] == category]
        lines.extend(["## %s" % heading, ""])
        if not rows:
            lines.extend(["None.", ""])
            continue
        lines.extend([
            "| Priority | Item | Due | Confidence | Source | Status |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for item in rows:
            lines.append(
                "| %s | %s | %s | %s | %s | %s |" % (
                    _markdown(item["priority"]),
                    _markdown(item["title"]),
                    _markdown(item["due"]),
                    _markdown(item["confidence"]),
                    _markdown(", ".join(_source_names(item, source_map))),
                    _markdown(item["status"]),
                )
            )
        lines.append("")
        for item in rows:
            lines.append("### %s — %s" % (item["id"], _markdown(item["title"])))
            lines.append("")
            lines.append("- Evidence: %s" % _markdown(item["evidence"]))
            lines.append("- Details: %s" % _markdown(item["details"]))
            lines.append("- Owner: %s" % _markdown(item["owner"]))
            if item["amount"]:
                lines.append("- Amount: `%s %s`" % (item["amount"]["value"], item["amount"]["currency"]))
            if item["duplicate_group"]:
                lines.append("- Duplicate group: `%s`" % item["duplicate_group"])
            if item["calendar"]:
                lines.append("- Calendar: `%s`" % item["calendar"]["status"])
            lines.append("- Sources: %s" % ", ".join(_markdown(name) for name in _source_names(item, source_map)))
            lines.append("")
    lines.extend(["## Questions", ""])
    if data["questions"]:
        for question in data["questions"]:
            lines.append("- %s" % _markdown(question))
    else:
        lines.append("None.")
    lines.extend([
        "",
        "## Archive plan",
        "",
        "The accompanying `archive-plan.json` is a dry-run proposal only. It contains no executable commands and has not changed any screenshot.",
        "",
    ])
    return ("\n".join(lines)).encode("utf-8")


def _spreadsheet_safe(value):
    if value is None:
        return ""
    text = str(value).replace("\x00", "")
    index = 0
    while index < len(text):
        char = text[index]
        category = FROZEN_UCD.category(char)
        if char not in " \t\n\r\v\f" and category not in {"Zs", "Zl", "Zp", "Cf"}:
            break
        index += 1
    meaningful = text[index:]
    if meaningful.startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def build_csv(data):
    source_map = {row["id"]: row for row in data["sources"]}
    output = io.StringIO(newline="")
    fieldnames = [
        "item_id", "category", "title", "details", "owner", "due", "priority",
        "confidence", "amount_value", "currency", "source_ids_json", "source_files_json", "evidence",
        "duplicate_group", "status", "calendar_status"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for item in data["items"]:
        amount = item["amount"] or {}
        row = {
            "item_id": item["id"],
            "category": item["category"],
            "title": item["title"],
            "details": item["details"] or "",
            "owner": item["owner"] or "",
            "due": item["due"] or "",
            "priority": item["priority"],
            "confidence": item["confidence"],
            "amount_value": amount.get("value", ""),
            "currency": amount.get("currency", ""),
            "source_ids_json": json.dumps(item["source_ids"], ensure_ascii=False, separators=(",", ":")),
            "source_files_json": json.dumps(_source_names(item, source_map), ensure_ascii=False, separators=(",", ":")),
            "evidence": item["evidence"],
            "duplicate_group": item["duplicate_group"] or "",
            "status": item["status"],
            "calendar_status": (item["calendar"] or {}).get("status", "none"),
        }
        safe = {}
        for key, value in row.items():
            safe[key] = value if key == "amount_value" else _spreadsheet_safe(value)
        writer.writerow(safe)
    return output.getvalue().encode("utf-8")


def _ics_escape(value):
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold_ics_line(line):
    pieces = []
    current = ""
    limit = 75
    for char in line:
        encoded = (current + char).encode("utf-8")
        if current and len(encoded) > limit:
            pieces.append(current)
            current = char
            limit = 74
        else:
            current += char
    pieces.append(current)
    return "\r\n ".join(pieces)


def _utc_stamp(value):
    parsed = _parse_aware_datetime(value, "generated_at")
    utc = parsed.astimezone(dt.timezone.utc)
    return "%04d%02d%02dT%02d%02d%02dZ" % (
        utc.year, utc.month, utc.day, utc.hour, utc.minute, utc.second
    )


def _calendar_value(value, kind):
    if kind == "date":
        return value.replace("-", "")
    parsed = _parse_aware_datetime(value, "calendar datetime")
    utc = parsed.astimezone(dt.timezone.utc)
    return "%04d%02d%02dT%02d%02d%02dZ" % (
        utc.year, utc.month, utc.day, utc.hour, utc.minute, utc.second
    )


def build_ics(data):
    source_map = {row["id"]: row for row in data["sources"]}
    stamp = _utc_stamp(data["generated_at"])
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//battle-doll//Screenshot Action Inbox 1.0//EN",
        "CALSCALE:GREGORIAN",
    ]
    draft_items = [
        item for item in data["items"]
        if item["calendar"] and item["calendar"]["status"] == "draft"
    ]
    for item in draft_items:
        calendar = item["calendar"]
        if not calendar or calendar["status"] != "draft":
            continue
        identity_sources = [
            {
                "id": source_map[source_id]["id"],
                "sha256": source_map[source_id]["sha256"],
            }
            for source_id in item["source_ids"]
        ]
        uid_basis = json.dumps(
            {
                "item_id": item["id"],
                "sources": identity_sources,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        uid = hashlib.sha256(uid_basis.encode("utf-8")).hexdigest()[:32] + "@screenshot-action-inbox.local"
        description_parts = [
            item["details"] or "No additional details.",
            "Visible basis: " + item["evidence"],
            "Sources: " + ", ".join(_source_names(item, source_map)),
            "Confidence: " + item["confidence"],
            "Draft only; not imported.",
        ]
        lines.extend(["BEGIN:VEVENT", "UID:" + uid, "DTSTAMP:" + stamp])
        kind = calendar["_kind"]
        if kind == "date":
            lines.append("DTSTART;VALUE=DATE:" + _calendar_value(calendar["start"], kind))
            if calendar["end"]:
                lines.append("DTEND;VALUE=DATE:" + _calendar_value(calendar["end"], kind))
        else:
            lines.append("DTSTART:" + _calendar_value(calendar["start"], kind))
            if calendar["end"]:
                lines.append("DTEND:" + _calendar_value(calendar["end"], kind))
        lines.append("SUMMARY:" + _ics_escape(item["title"]))
        lines.append("DESCRIPTION:" + _ics_escape("\n".join(description_parts)))
        if calendar["location"]:
            lines.append("LOCATION:" + _ics_escape(calendar["location"]))
        lines.extend([
            "CLASS:PRIVATE",
            "STATUS:TENTATIVE",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ])
    if not draft_items:
        lines.extend([
            "BEGIN:X-SAI-NO-EVENTS",
            "X-SAI-STATUS:NO-EXPORTABLE-CALENDAR-DRAFTS",
            "END:X-SAI-NO-EVENTS",
        ])
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold_ics_line(line) for line in lines) + "\r\n").encode("utf-8")


def _archive_destination(source):
    if source["archive_recommendation"] != "archive":
        return None
    period = source["capture_date"][:7] if source["capture_date"] else "undated"
    normalized_path = _stable_normalize("NFC", source["relative_path"])
    return "%s/%s/%s" % (period, source["archive_bucket"], normalized_path)


def build_archive_plan(data):
    rows = []
    destination_keys = {}
    for source in data["sources"]:
        destination = _archive_destination(source)
        status_value = {
            "archive": "PROPOSED",
            "keep": "KEEP",
            "review": "REVIEW",
        }[source["archive_recommendation"]]
        row = {
            "source_id": source["id"],
            "source_relative_path": source["relative_path"],
            "source_sha256": source["sha256"],
            "recommendation": source["archive_recommendation"],
            "proposed_destination": destination,
            "status": status_value,
            "conflict": False,
            "requires_explicit_approval": True,
            "executed": False,
        }
        rows.append(row)
        if destination:
            destination_keys.setdefault(_path_collision_key(destination), []).append(row)
    for collisions in destination_keys.values():
        if len(collisions) > 1:
            for row in collisions:
                row["conflict"] = True
                row["status"] = "CONFLICT"
                row["proposed_destination"] = None
    return {
        "schema_version": SCHEMA_VERSION,
        "plan_type": "PLAN_ONLY",
        "dry_run": True,
        "executed": False,
        "generated_at": data["generated_at"],
        "notice": "No source file was moved, renamed, overwritten, or deleted.",
        "entries": rows,
    }


def build_artifacts(data):
    canonical_input = _canonical_json_bytes(data)
    archive_plan = build_archive_plan(data)
    artifacts = {
        "weekly-digest.md": build_digest(data),
        "actions.csv": build_csv(data),
        "calendar.ics": build_ics(data),
        "archive-plan.json": _canonical_json_bytes(archive_plan),
    }
    warning_codes = []
    if data["questions"]:
        warning_codes.append("UNRESOLVED_QUESTIONS")
    if any(item["status"] == "needs_review" for item in data["items"]):
        warning_codes.append("ITEMS_NEED_REVIEW")
    if any(source["status"] != "reviewed" for source in data["sources"]):
        warning_codes.append("SOURCE_REVIEW_INCOMPLETE")
    if any(row["conflict"] for row in archive_plan["entries"]):
        warning_codes.append("ARCHIVE_DESTINATION_CONFLICT")
    if not any(
        item["calendar"] and item["calendar"]["status"] == "draft"
        for item in data["items"]
    ):
        warning_codes.append("NO_EXPORTABLE_CALENDAR_DRAFTS")
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "processor": "screenshot-action-inbox/1.0.0",
        "canonical_input_sha256": _hash_bytes(canonical_input),
        "generated_at": data["generated_at"],
        "counts": {
            "sources": len(data["sources"]),
            "items": len(data["items"]),
            "calendar_drafts": sum(
                1 for item in data["items"]
                if item["calendar"] and item["calendar"]["status"] == "draft"
            ),
        },
        "warnings": warning_codes,
        "side_effects": {
            "network_requests": 0,
            "source_image_files_opened_by_processor": 0,
            "calendar_events_created": 0,
            "messages_sent": 0,
            "source_files_changed": 0,
            "archive_plan_executed": False,
        },
        "outputs": {
            name: {"sha256": _hash_bytes(payload), "bytes": len(payload)}
            for name, payload in sorted(artifacts.items())
        },
    }
    artifacts["receipt.json"] = _canonical_json_bytes(receipt)
    return artifacts


def _is_reparse(stat_result):
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _source_stat_key(stat_result):
    return (
        getattr(stat_result, "st_dev", None),
        getattr(stat_result, "st_ino", None),
        stat_result.st_size,
        getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1000000000)),
        getattr(stat_result, "st_ctime_ns", int(stat_result.st_ctime * 1000000000)),
    )


def _source_identity(stat_result):
    return (getattr(stat_result, "st_dev", None), getattr(stat_result, "st_ino", None))


def _source_metadata_key(stat_result):
    return (
        stat_result.st_size,
        getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1000000000)),
        getattr(stat_result, "st_ctime_ns", int(stat_result.st_ctime * 1000000000)),
    )


def _error_marker(exc):
    code = getattr(exc, "winerror", None)
    if code is None:
        code = getattr(exc, "errno", None)
    return "OS_ERROR" if code is None else "OS_ERROR_%s" % code


_OPEN_SUPPORTS_DIR_FD = os.open in getattr(os, "supports_dir_fd", set())
_SCANDIR_SUPPORTS_FD = os.scandir in getattr(os, "supports_fd", set())
_STAT_SUPPORTS_DIR_FD = os.stat in getattr(os, "supports_dir_fd", set())
_STAT_SUPPORTS_NOFOLLOW = os.stat in getattr(os, "supports_follow_symlinks", set())
_MKDIR_SUPPORTS_DIR_FD = os.mkdir in getattr(os, "supports_dir_fd", set())
_UNLINK_SUPPORTS_DIR_FD = os.unlink in getattr(os, "supports_dir_fd", set())
_RMDIR_SUPPORTS_DIR_FD = os.rmdir in getattr(os, "supports_dir_fd", set())
_RENAME_SUPPORTS_DIR_FD = os.rename in getattr(os, "supports_dir_fd", set())
_LISTDIR_SUPPORTS_FD = os.listdir in getattr(os, "supports_fd", set())


def _is_windows_platform():
    return os.name == "nt"


def _descriptor_directory_open_available():
    return bool(
        not _is_windows_platform()
        and getattr(os, "O_DIRECTORY", 0)
        and getattr(os, "O_NOFOLLOW", 0)
        and _OPEN_SUPPORTS_DIR_FD
    )


def _secure_inventory_fds_available():
    return bool(
        _descriptor_directory_open_available()
        and _SCANDIR_SUPPORTS_FD
        and _STAT_SUPPORTS_DIR_FD
        and _STAT_SUPPORTS_NOFOLLOW
    )


def _secure_output_fds_available():
    return bool(
        _descriptor_directory_open_available()
        and _MKDIR_SUPPORTS_DIR_FD
        and _UNLINK_SUPPORTS_DIR_FD
        and _RMDIR_SUPPORTS_DIR_FD
        and _RENAME_SUPPORTS_DIR_FD
        and _LISTDIR_SUPPORTS_FD
    )


def _plain_directory_flags():
    return (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _canonicalize_root_level_alias(path):
    """Resolve only a platform root alias such as macOS /var -> /private/var.

    User-controlled symlinks below that first component remain visible to the
    component-by-component validator and are rejected.
    """
    absolute = os.path.abspath(os.fspath(path))
    if _is_windows_platform() or not absolute.startswith(os.path.sep):
        return absolute
    parts = [part for part in absolute.split(os.path.sep) if part]
    if not parts:
        return absolute
    first = os.path.sep + parts[0]
    macos_aliases = {
        "/etc": "/private/etc",
        "/tmp": "/private/tmp",
        "/var": "/private/var",
    }
    expected = macos_aliases.get(first) if sys.platform == "darwin" else None
    if expected is None or os.path.normpath(os.path.realpath(first)) != expected:
        return absolute
    return os.path.join(expected, *parts[1:])


def _open_plain_directory_fd(path, label, canonicalize_system_prefix=False):
    """Open every directory component without following links.

    A descriptor is returned only on platforms with openat/O_NOFOLLOW support.
    The caller owns it. Other platforms use the component-chain validator below.
    """
    if not _descriptor_directory_open_available():
        _require_plain_directory_chain(path, label, canonicalize_system_prefix)
        return None
    absolute = os.path.abspath(os.fspath(path))
    if canonicalize_system_prefix:
        absolute = _canonicalize_root_level_alias(absolute)
    descriptor = None
    try:
        descriptor = os.open(os.path.sep, _plain_directory_flags())
        for component in (part for part in absolute.split(os.path.sep) if part):
            next_descriptor = os.open(component, _plain_directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        result = os.fstat(descriptor)
        if not stat.S_ISDIR(result.st_mode) or _is_reparse(result):
            raise InboxError(
                "%s must contain only existing non-link, non-reparse directories" % label
            )
        return descriptor
    except InboxError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise InboxError(
            "%s must contain only existing non-link, non-reparse directories (%s)"
            % (label, _error_marker(exc))
        )


def _require_plain_directory_chain(path, label, canonicalize_system_prefix=False):
    absolute = os.path.abspath(os.fspath(path))
    if canonicalize_system_prefix:
        absolute = _canonicalize_root_level_alias(absolute)
    drive, tail = os.path.splitdrive(absolute)
    if drive:
        current = drive + os.path.sep
    else:
        current = os.path.sep
    components = [part for part in tail.split(os.path.sep) if part]
    paths = [current]
    for component in components:
        current = os.path.join(current, component)
        paths.append(current)
    for component_path in paths:
        try:
            result = os.lstat(component_path)
        except OSError as exc:
            raise InboxError(
                "%s must contain only existing non-link, non-reparse directories (%s)"
                % (label, _error_marker(exc))
            )
        if (
            not stat.S_ISDIR(result.st_mode)
            or stat.S_ISLNK(result.st_mode)
            or _is_reparse(result)
        ):
            raise InboxError(
                "%s must contain only existing non-link, non-reparse directories" % label
            )


def _lexists(path):
    try:
        os.lstat(str(path))
    except FileNotFoundError:
        return False
    return True


def _require_plain_directory(path, label):
    descriptor = _open_plain_directory_fd(path, label, canonicalize_system_prefix=True)
    if descriptor is not None:
        os.close(descriptor)


def _require_directory_fd_still_at_path(directory_fd, path, label):
    expected_identity = _source_identity(os.fstat(directory_fd))
    recheck_fd = _open_plain_directory_fd(
        path, label, canonicalize_system_prefix=True
    )
    if recheck_fd is None:
        raise InboxError("%s cannot be identity-bound on this platform" % label)
    try:
        if _source_identity(os.fstat(recheck_fd)) != expected_identity:
            raise InboxError("%s changed during the operation" % label)
    finally:
        os.close(recheck_fd)


def _valid_artifact_names(artifacts):
    for name in artifacts:
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or os.path.basename(name) != name
            or "/" in name
            or "\\" in name
        ):
            raise InboxError("artifact names must be plain filenames")


def _read_plain_file_at(directory_fd, name):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as handle:
        opened_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened_stat.st_mode) or _is_reparse(opened_stat):
            raise InboxError("output contains a non-regular artifact")
        payload = handle.read()
        after_stat = os.fstat(handle.fileno())
    try:
        path_after_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise InboxError("output artifact changed while reading (%s)" % _error_marker(exc))
    if (
        _source_stat_key(opened_stat) != _source_stat_key(after_stat)
        or _source_stat_key(after_stat) != _source_stat_key(path_after_stat)
        or not stat.S_ISREG(path_after_stat.st_mode)
        or stat.S_ISLNK(path_after_stat.st_mode)
        or _is_reparse(path_after_stat)
    ):
        raise InboxError("output artifact changed while reading")
    return payload


def _existing_artifacts_match_at(parent_fd, directory_name, artifacts):
    try:
        output_fd = os.open(directory_name, _plain_directory_flags(), dir_fd=parent_fd)
    except OSError:
        return False
    try:
        opened_identity = _source_identity(os.fstat(output_fd))
        if set(os.listdir(output_fd)) != set(artifacts):
            return False
        for name, payload in artifacts.items():
            try:
                if _read_plain_file_at(output_fd, name) != payload:
                    return False
            except (InboxError, OSError):
                return False
        try:
            path_after = os.stat(
                directory_name, dir_fd=parent_fd, follow_symlinks=False
            )
        except OSError:
            return False
        if (
            not stat.S_ISDIR(path_after.st_mode)
            or stat.S_ISLNK(path_after.st_mode)
            or _is_reparse(path_after)
            or _source_identity(path_after) != opened_identity
        ):
            return False
        return True
    finally:
        os.close(output_fd)


def _existing_artifacts_match_unlocked(output_dir, artifacts):
    try:
        descriptor = _open_plain_directory_fd(output_dir, "existing output")
    except InboxError:
        return False
    if descriptor is not None:
        try:
            if set(os.listdir(descriptor)) != set(artifacts):
                return False
            for name, payload in artifacts.items():
                try:
                    if _read_plain_file_at(descriptor, name) != payload:
                        return False
                except (InboxError, OSError):
                    return False
            return True
        finally:
            os.close(descriptor)
    output_dir = Path(output_dir)
    try:
        actual = {path.name for path in output_dir.iterdir()}
    except OSError:
        return False
    if actual != set(artifacts):
        return False
    for name, payload in artifacts.items():
        path = output_dir / name
        try:
            path_stat = os.lstat(str(path))
            if (
                not stat.S_ISREG(path_stat.st_mode)
                or stat.S_ISLNK(path_stat.st_mode)
                or _is_reparse(path_stat)
                or path.read_bytes() != payload
            ):
                return False
        except OSError:
            return False
    return True


def _existing_artifacts_match(output_dir, artifacts):
    if not _is_windows_platform():
        return _existing_artifacts_match_unlocked(output_dir, artifacts)
    locks = None
    try:
        locks = _open_windows_directory_locks(output_dir, "existing output")
        return _existing_artifacts_match_unlocked(output_dir, artifacts)
    except (InboxError, OSError):
        return False
    finally:
        _close_windows_directory_locks(locks)


def _write_artifacts_at(parent_fd, destination_name, artifacts):
    try:
        os.stat(destination_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise InboxError("cannot inspect output destination (%s)" % _error_marker(exc))
    else:
        if _existing_artifacts_match_at(parent_fd, destination_name, artifacts):
            return "UNCHANGED"
        raise InboxError("output already exists with different or incomplete content")

    stage_name = None
    stage_fd = None
    staged_files = {}
    stage_identity = None
    renamed = False
    stage_seed = hashlib.sha256()
    for name, payload in sorted(artifacts.items()):
        stage_seed.update(name.encode("utf-8"))
        stage_seed.update(b"\0")
        stage_seed.update(hashlib.sha256(payload).digest())
    stage_prefix = ".sai-stage-" + stage_seed.hexdigest()[:16]
    for unused in range(128):
        candidate = "%s-%02x" % (stage_prefix, unused)
        try:
            os.mkdir(candidate, 0o700, dir_fd=parent_fd)
            stage_name = candidate
            break
        except FileExistsError:
            continue
    if stage_name is None:
        raise InboxError("cannot allocate a unique output staging directory")
    try:
        stage_fd = os.open(stage_name, _plain_directory_flags(), dir_fd=parent_fd)
        stage_identity = _source_identity(os.fstat(stage_fd))
        for name, payload in sorted(artifacts.items()):
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(name, flags, 0o600, dir_fd=stage_fd)
            with os.fdopen(descriptor, "wb") as handle:
                opened_stat = os.fstat(handle.fileno())
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                after_stat = os.fstat(handle.fileno())
            path_after_stat = os.stat(name, dir_fd=stage_fd, follow_symlinks=False)
            if (
                _source_identity(opened_stat) != _source_identity(after_stat)
                or _source_identity(after_stat) != _source_identity(path_after_stat)
                or not stat.S_ISREG(path_after_stat.st_mode)
                or stat.S_ISLNK(path_after_stat.st_mode)
                or _is_reparse(path_after_stat)
            ):
                raise InboxError("staged output changed while writing")
            staged_files[name] = _source_identity(after_stat)
        os.fsync(stage_fd)
        stage_path_stat = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(stage_path_stat.st_mode)
            or stat.S_ISLNK(stage_path_stat.st_mode)
            or _is_reparse(stage_path_stat)
            or _source_identity(stage_path_stat) != stage_identity
        ):
            raise InboxError("output staging directory changed while writing")
        os.rename(
            stage_name,
            destination_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        renamed = True
        destination_stat = os.stat(
            destination_name, dir_fd=parent_fd, follow_symlinks=False
        )
        if (
            not stat.S_ISDIR(destination_stat.st_mode)
            or stat.S_ISLNK(destination_stat.st_mode)
            or _is_reparse(destination_stat)
            or _source_identity(destination_stat) != stage_identity
        ):
            raise InboxError("output destination changed during publication")
        os.fsync(parent_fd)
    finally:
        if stage_fd is not None:
            if not renamed:
                for name, expected_identity in staged_files.items():
                    try:
                        current_stat = os.stat(
                            name, dir_fd=stage_fd, follow_symlinks=False
                        )
                        if _source_identity(current_stat) == expected_identity:
                            os.unlink(name, dir_fd=stage_fd)
                    except OSError:
                        pass
            os.close(stage_fd)
        if not renamed:
            try:
                current_stage = os.stat(
                    stage_name, dir_fd=parent_fd, follow_symlinks=False
                )
                if _source_identity(current_stage) == stage_identity:
                    os.rmdir(stage_name, dir_fd=parent_fd)
            except OSError:
                pass
    return "CREATED"


def write_artifacts(output, artifacts):
    _valid_artifact_names(artifacts)
    destination = Path(output)
    parent = destination.parent
    if _secure_output_fds_available():
        parent_fd = _open_plain_directory_fd(
            parent, "output parent", canonicalize_system_prefix=True
        )
        try:
            result = _write_artifacts_at(parent_fd, destination.name, artifacts)
            _require_directory_fd_still_at_path(parent_fd, parent, "output parent")
            return result
        finally:
            os.close(parent_fd)

    windows_parent_locks = None
    try:
        if _is_windows_platform():
            windows_parent_locks = _open_windows_directory_locks(parent, "output parent")
        _require_plain_directory(parent, "output parent")
        if _lexists(destination):
            if _existing_artifacts_match(destination, artifacts):
                return "UNCHANGED"
            raise InboxError("output already exists with different or incomplete content")
        stage = Path(tempfile.mkdtemp(prefix=".sai-stage-", dir=str(parent)))
        try:
            os.chmod(str(stage), 0o700)
            for name, payload in sorted(artifacts.items()):
                target = stage / name
                with target.open("xb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(str(target), 0o600)
            _require_plain_directory(parent, "output parent")
            os.replace(str(stage), str(destination))
        except Exception:
            shutil.rmtree(str(stage), ignore_errors=True)
            raise
        return "CREATED"
    finally:
        _close_windows_directory_locks(windows_parent_locks)


def _inventory_path_display(relative):
    try:
        _assert_no_sensitive_value(relative, "inventory path")
        relative.encode("utf-8")
        return relative
    except (InboxError, UnicodeError):
        digest = hashlib.sha256(relative.encode("utf-8", "surrogatepass")).hexdigest()[:12]
        return "[REDACTED-%s]" % digest


def _append_inventory_skip(skipped, relative, reason):
    skipped.append({"relative_path": _inventory_path_display(relative), "reason": reason})


def _inventory_failure(action, relative=None, exc=None):
    location = "[ROOT]" if not relative else _inventory_path_display(relative)
    suffix = "" if exc is None else " (%s)" % _error_marker(exc)
    return InboxError("%s: %s%s" % (action, location, suffix))


def _normalize_windows_handle_path(value):
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.normpath(value))


def _windows_file_information_api():
    if not _is_windows_platform():
        raise InboxError("Windows handle binding is unavailable on this platform")
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
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_final_path.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    return (
        ctypes,
        wintypes,
        ByHandleFileInformation,
        create_file,
        get_information,
        get_final_path,
        close_handle,
    )


def _windows_handle_value(ctypes_module, handle):
    return ctypes_module.cast(handle, ctypes_module.c_void_p).value


def _windows_handle_information(api, handle):
    ctypes_module = api[0]
    information = api[2]()
    if not api[4](handle, ctypes_module.byref(information)):
        raise ctypes_module.WinError(ctypes_module.get_last_error())
    identity = (
        int(information.dwVolumeSerialNumber),
        (int(information.nFileIndexHigh) << 32) | int(information.nFileIndexLow),
    )
    return information, identity


def _windows_final_handle_path(api, handle):
    ctypes_module = api[0]
    size = 512
    while True:
        buffer = ctypes_module.create_unicode_buffer(size)
        length = api[5](handle, buffer, size, 0)
        if not length:
            raise ctypes_module.WinError(ctypes_module.get_last_error())
        if length < size:
            return buffer.value
        size = length + 1


def _open_windows_directory_locks(path, label):
    """Pin each Windows directory component against rename/replacement."""
    api = _windows_file_information_api()
    ctypes_module = api[0]
    absolute = os.path.abspath(os.fspath(path))
    drive, tail = os.path.splitdrive(absolute)
    if not drive:
        raise InboxError("%s must be an absolute directory" % label)
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
        final_identity = None
        for component_path in component_paths:
            handle = api[3](
                component_path,
                file_read_attributes,
                share_read_write,
                None,
                open_existing,
                backup_semantics | open_reparse_point,
                None,
            )
            if _windows_handle_value(ctypes_module, handle) == invalid_handle:
                raise ctypes_module.WinError(ctypes_module.get_last_error())
            handles.append(handle)
            information, final_identity = _windows_handle_information(api, handle)
            if information.dwFileAttributes & 0x400:
                raise InboxError(
                    "%s must contain only existing non-link, non-reparse directories" % label
                )
            if not information.dwFileAttributes & 0x10:
                raise InboxError(
                    "%s must contain only existing non-link, non-reparse directories" % label
                )
            final_path = _windows_final_handle_path(api, handle)
            if _normalize_windows_handle_path(final_path) != _normalize_windows_handle_path(
                component_path
            ):
                raise InboxError("%s resolved outside its expected path" % label)
        return (api, handles, final_identity)
    except (InboxError, OSError):
        for handle in reversed(handles):
            api[6](handle)
        raise


def _close_windows_directory_locks(locks):
    if locks is None:
        return
    api, handles, unused_identity = locks
    for handle in reversed(handles):
        api[6](handle)


def _open_windows_inventory_file(path):
    """Open a Windows path itself, returning an fd and stable handle identity."""
    if not _is_windows_platform():
        raise InboxError("Windows handle binding is unavailable on this platform")
    import msvcrt
    api = _windows_file_information_api()
    ctypes_module = api[0]

    generic_read = 0x80000000
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    open_reparse_point = 0x00200000
    sequential_scan = 0x08000000
    handle = api[3](
        os.path.abspath(os.fspath(path)),
        generic_read,
        share_read_write,
        None,
        open_existing,
        open_reparse_point | sequential_scan,
        None,
    )
    invalid_handle = ctypes_module.c_void_p(-1).value
    if _windows_handle_value(ctypes_module, handle) == invalid_handle:
        raise ctypes_module.WinError(ctypes_module.get_last_error())
    transferred = False
    try:
        information, identity = _windows_handle_information(api, handle)
        if information.dwFileAttributes & 0x400:
            raise InboxError("inventory source is a link or reparse point")
        final_path = _windows_final_handle_path(api, handle)
        expected = _normalize_windows_handle_path(os.path.abspath(os.fspath(path)))
        if _normalize_windows_handle_path(final_path) != expected:
            raise InboxError("inventory source handle resolved outside its expected path")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        descriptor = msvcrt.open_osfhandle(_windows_handle_value(ctypes_module, handle), flags)
        transferred = True
        return descriptor, identity
    finally:
        if not transferred:
            api[6](handle)


def _hash_inventory_descriptor(descriptor, close_descriptor=True):
    hasher = hashlib.sha256()
    handle = os.fdopen(descriptor, "rb", closefd=close_descriptor)
    try:
        opened_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened_stat.st_mode) or _is_reparse(opened_stat):
            raise InboxError("inventory source is not a plain regular file")
        opened_key = _source_stat_key(opened_stat)
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
        after_stat = os.fstat(handle.fileno())
        after_key = _source_stat_key(after_stat)
    finally:
        handle.close()
    if opened_key != after_key:
        raise InboxError("inventory source changed while hashing")
    return hasher.hexdigest(), opened_stat, opened_key


def inventory(root, recursive=False, include_hash=False):
    base = Path(root)
    records = []
    skipped = []

    def validate_inventory_path(relative):
        try:
            validate_relative_path(relative, "inventory path")
        except InboxError as exc:
            _append_inventory_skip(skipped, relative, "UNSAFE_PATH: %s" % exc)
            return False
        try:
            _assert_no_sensitive_value(relative, "inventory path")
        except InboxError:
            _append_inventory_skip(skipped, relative, "SENSITIVE_FILENAME")
            return False
        return True

    def initial_record(relative, entry_stat):
        if not validate_inventory_path(relative):
            return None
        if entry_stat.st_size > MAX_IMAGE_BYTES:
            _append_inventory_skip(skipped, relative, "FILE_TOO_LARGE")
            return None
        canonical = _stable_normalize("NFC", relative)
        return {
            "id": "src-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12],
            "relative_path": relative,
            "bytes": entry_stat.st_size,
        }

    def visit_secure(directory_fd, prefix=""):
        try:
            entries = sorted(
                os.scandir(directory_fd),
                key=lambda entry: _path_collision_key(entry.name),
            )
        except OSError as exc:
            raise _inventory_failure("cannot inventory directory", prefix, exc)
        for entry in entries:
            relative = (prefix + "/" + entry.name).lstrip("/")
            try:
                entry_stat = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                _append_inventory_skip(
                    skipped, relative, "STAT_FAILED_%s" % _error_marker(exc)
                )
                continue
            if stat.S_ISLNK(entry_stat.st_mode) or _is_reparse(entry_stat):
                _append_inventory_skip(skipped, relative, "LINK_OR_REPARSE_POINT")
                continue
            if stat.S_ISDIR(entry_stat.st_mode):
                if recursive:
                    if not validate_inventory_path(relative):
                        continue
                    try:
                        child_fd = os.open(entry.name, _plain_directory_flags(), dir_fd=directory_fd)
                    except OSError as exc:
                        raise _inventory_failure(
                            "inventory directory changed before traversal", relative, exc
                        )
                    try:
                        opened_stat = os.fstat(child_fd)
                        if (
                            not stat.S_ISDIR(opened_stat.st_mode)
                            or _is_reparse(opened_stat)
                            or _source_identity(opened_stat) != _source_identity(entry_stat)
                        ):
                            raise _inventory_failure(
                                "inventory directory changed before traversal", relative
                            )
                        visit_secure(child_fd, relative)
                        try:
                            path_after = os.stat(
                                entry.name, dir_fd=directory_fd, follow_symlinks=False
                            )
                        except OSError as exc:
                            raise _inventory_failure(
                                "inventory directory changed during traversal", relative, exc
                            )
                        if (
                            not stat.S_ISDIR(path_after.st_mode)
                            or stat.S_ISLNK(path_after.st_mode)
                            or _is_reparse(path_after)
                            or _source_identity(path_after) != _source_identity(opened_stat)
                        ):
                            raise _inventory_failure(
                                "inventory directory changed during traversal", relative
                            )
                    finally:
                        os.close(child_fd)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                _append_inventory_skip(skipped, relative, "NOT_REGULAR_FILE")
                continue
            if Path(entry.name).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            record = initial_record(relative, entry_stat)
            if record is None:
                continue
            if include_hash:
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                try:
                    descriptor = os.open(entry.name, flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise _inventory_failure(
                        "cannot open inventory source for hashing", relative, exc
                    )
                try:
                    digest, unused_opened_stat, opened_key = _hash_inventory_descriptor(descriptor)
                except (InboxError, OSError) as exc:
                    raise _inventory_failure("cannot hash inventory source", relative, exc)
                if opened_key != _source_stat_key(entry_stat):
                    raise _inventory_failure("inventory source changed before hashing", relative)
                try:
                    path_after_stat = os.stat(
                        entry.name, dir_fd=directory_fd, follow_symlinks=False
                    )
                except OSError as exc:
                    raise _inventory_failure(
                        "inventory source changed during hashing", relative, exc
                    )
                if (
                    opened_key != _source_stat_key(path_after_stat)
                    or not stat.S_ISREG(path_after_stat.st_mode)
                    or stat.S_ISLNK(path_after_stat.st_mode)
                    or _is_reparse(path_after_stat)
                ):
                    raise _inventory_failure("inventory source changed during hashing", relative)
                record["sha256"] = digest
            else:
                try:
                    path_after_stat = os.stat(
                        entry.name, dir_fd=directory_fd, follow_symlinks=False
                    )
                except OSError as exc:
                    raise _inventory_failure("inventory source changed during scan", relative, exc)
                if (
                    _source_stat_key(entry_stat) != _source_stat_key(path_after_stat)
                    or not stat.S_ISREG(path_after_stat.st_mode)
                    or stat.S_ISLNK(path_after_stat.st_mode)
                    or _is_reparse(path_after_stat)
                ):
                    raise _inventory_failure("inventory source changed during scan", relative)
            records.append(record)

    def visit_fallback(directory, prefix=""):
        try:
            entries = sorted(
                os.scandir(str(directory)),
                key=lambda entry: _path_collision_key(entry.name),
            )
        except OSError as exc:
            raise _inventory_failure("cannot inventory directory", prefix, exc)
        for entry in entries:
            relative = (prefix + "/" + entry.name).lstrip("/")
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                _append_inventory_skip(
                    skipped, relative, "STAT_FAILED_%s" % _error_marker(exc)
                )
                continue
            if stat.S_ISLNK(entry_stat.st_mode) or _is_reparse(entry_stat):
                _append_inventory_skip(skipped, relative, "LINK_OR_REPARSE_POINT")
                continue
            if stat.S_ISDIR(entry_stat.st_mode):
                if recursive:
                    if not _is_windows_platform():
                        raise InboxError(
                            "recursive inventory is unavailable without secure traversal support"
                        )
                    if not validate_inventory_path(relative):
                        continue
                    child_locks = None
                    try:
                        child_locks = _open_windows_directory_locks(
                            entry.path, "inventory directory"
                        )
                        try:
                            locked_stat = os.lstat(entry.path)
                        except OSError as exc:
                            raise _inventory_failure(
                                "inventory directory changed before traversal", relative, exc
                            )
                        if (
                            not stat.S_ISDIR(locked_stat.st_mode)
                            or stat.S_ISLNK(locked_stat.st_mode)
                            or _is_reparse(locked_stat)
                        ):
                            raise _inventory_failure(
                                "inventory directory changed before traversal", relative
                            )
                        visit_fallback(Path(entry.path), relative)
                    except (InboxError, OSError) as exc:
                        if isinstance(exc, InboxError):
                            raise
                        raise _inventory_failure(
                            "inventory directory changed before traversal", relative, exc
                        )
                    finally:
                        _close_windows_directory_locks(child_locks)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                _append_inventory_skip(skipped, relative, "NOT_REGULAR_FILE")
                continue
            if Path(entry.name).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            record = initial_record(relative, entry_stat)
            if record is None:
                continue
            if include_hash:
                windows_hash = _is_windows_platform()
                descriptor = None
                after_descriptor = None
                try:
                    if windows_hash:
                        descriptor, opened_identity = _open_windows_inventory_file(entry.path)
                    else:
                        flags = (
                            os.O_RDONLY
                            | getattr(os, "O_BINARY", 0)
                            | getattr(os, "O_NOFOLLOW", 0)
                        )
                        descriptor = os.open(entry.path, flags)
                        opened_identity = None
                except (InboxError, OSError) as exc:
                    raise _inventory_failure(
                        "cannot securely open inventory source for hashing", relative, exc
                    )
                try:
                    digest, opened_stat, opened_key = _hash_inventory_descriptor(
                        descriptor, close_descriptor=not windows_hash
                    )
                    if not windows_hash:
                        descriptor = None
                except (InboxError, OSError) as exc:
                    if descriptor is not None:
                        os.close(descriptor)
                    raise _inventory_failure("cannot hash inventory source", relative, exc)
                entry_compare = (
                    _source_metadata_key(entry_stat)
                    if windows_hash else _source_stat_key(entry_stat)
                )
                opened_compare = (
                    _source_metadata_key(opened_stat)
                    if windows_hash else opened_key
                )
                if opened_compare != entry_compare:
                    if descriptor is not None:
                        os.close(descriptor)
                    raise _inventory_failure("inventory source changed before hashing", relative)
                try:
                    path_after_stat = os.lstat(entry.path)
                except OSError as exc:
                    if descriptor is not None:
                        os.close(descriptor)
                    raise _inventory_failure(
                        "inventory source changed during hashing", relative, exc
                    )
                path_after_compare = (
                    _source_metadata_key(path_after_stat)
                    if windows_hash else _source_stat_key(path_after_stat)
                )
                if (
                    opened_compare != path_after_compare
                    or not stat.S_ISREG(path_after_stat.st_mode)
                    or stat.S_ISLNK(path_after_stat.st_mode)
                    or _is_reparse(path_after_stat)
                ):
                    if descriptor is not None:
                        os.close(descriptor)
                    raise _inventory_failure("inventory source changed during hashing", relative)
                if windows_hash:
                    try:
                        after_descriptor, after_identity = _open_windows_inventory_file(entry.path)
                    except (InboxError, OSError) as exc:
                        os.close(descriptor)
                        raise _inventory_failure(
                            "inventory source changed during hashing", relative, exc
                        )
                    os.close(after_descriptor)
                    after_descriptor = None
                    os.close(descriptor)
                    descriptor = None
                    if opened_identity != after_identity:
                        raise _inventory_failure(
                            "inventory source identity changed during hashing", relative
                        )
                record["sha256"] = digest
            records.append(record)

    if _secure_inventory_fds_available():
        root_fd = _open_plain_directory_fd(
            base, "inventory root", canonicalize_system_prefix=True
        )
        root_identity = _source_identity(os.fstat(root_fd))
        try:
            visit_secure(root_fd)
            recheck_fd = _open_plain_directory_fd(
                base, "inventory root", canonicalize_system_prefix=True
            )
            try:
                if _source_identity(os.fstat(recheck_fd)) != root_identity:
                    raise _inventory_failure("inventory root changed during traversal")
            finally:
                os.close(recheck_fd)
        finally:
            os.close(root_fd)
    else:
        if _is_windows_platform():
            root_locks = None
            try:
                root_locks = _open_windows_directory_locks(base, "inventory root")
                visit_fallback(base)
            except InboxError:
                raise
            except OSError as exc:
                raise _inventory_failure("cannot securely lock inventory root", exc=exc)
            finally:
                _close_windows_directory_locks(root_locks)
        else:
            _require_plain_directory(base, "inventory root")
            if recursive:
                raise InboxError(
                    "recursive inventory is unavailable without descriptor-bound traversal support"
                )
            visit_fallback(base)
            _require_plain_directory(base, "inventory root")
    path_keys = [_path_collision_key(row["relative_path"]) for row in records]
    if len(set(path_keys)) != len(path_keys):
        raise InboxError("inventory contains paths that collide after Unicode/case normalization")
    return {
        "inventory_version": SCHEMA_VERSION,
        "image_contents_opened": bool(include_hash),
        "exif_read": False,
        "network_requests": 0,
        "sources": sorted(records, key=lambda row: row["id"]),
        "skipped": sorted(skipped, key=lambda row: _path_collision_key(row["relative_path"])),
    }


def write_single_file(path, payload):
    destination = Path(path)
    parent = destination.parent
    if _secure_output_fds_available():
        parent_fd = _open_plain_directory_fd(
            parent, "output parent", canonicalize_system_prefix=True
        )
        try:
            try:
                destination_stat = os.stat(
                    destination.name, dir_fd=parent_fd, follow_symlinks=False
                )
            except FileNotFoundError:
                destination_stat = None
            except OSError as exc:
                raise InboxError(
                    "cannot inspect output destination (%s)" % _error_marker(exc)
                )
            if destination_stat is not None:
                if (
                    stat.S_ISREG(destination_stat.st_mode)
                    and not stat.S_ISLNK(destination_stat.st_mode)
                    and not _is_reparse(destination_stat)
                ):
                    try:
                        if _read_plain_file_at(parent_fd, destination.name) == payload:
                            _require_directory_fd_still_at_path(
                                parent_fd, parent, "output parent"
                            )
                            return "UNCHANGED"
                    except (InboxError, OSError):
                        pass
                raise InboxError("refusing to overwrite existing output")
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(destination.name, flags, 0o600, dir_fd=parent_fd)
            owns_destination = True
            opened_stat = None
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    opened_stat = os.fstat(handle.fileno())
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                    after_stat = os.fstat(handle.fileno())
                try:
                    path_after_stat = os.stat(
                        destination.name, dir_fd=parent_fd, follow_symlinks=False
                    )
                except OSError:
                    owns_destination = False
                    raise InboxError("output destination changed while writing")
                if (
                    _source_identity(opened_stat) != _source_identity(after_stat)
                    or _source_identity(after_stat) != _source_identity(path_after_stat)
                    or not stat.S_ISREG(path_after_stat.st_mode)
                    or stat.S_ISLNK(path_after_stat.st_mode)
                    or _is_reparse(path_after_stat)
                ):
                    owns_destination = False
                    raise InboxError("output destination changed while writing")
                os.fsync(parent_fd)
            except Exception:
                if owns_destination and opened_stat is not None:
                    try:
                        current_stat = os.stat(
                            destination.name, dir_fd=parent_fd, follow_symlinks=False
                        )
                        if _source_identity(current_stat) == _source_identity(opened_stat):
                            os.unlink(destination.name, dir_fd=parent_fd)
                    except OSError:
                        pass
                raise
            _require_directory_fd_still_at_path(parent_fd, parent, "output parent")
            return "CREATED"
        finally:
            os.close(parent_fd)

    windows_parent_locks = None
    try:
        if _is_windows_platform():
            windows_parent_locks = _open_windows_directory_locks(parent, "output parent")
        _require_plain_directory(parent, "output parent")
        if _lexists(destination):
            try:
                destination_stat = os.lstat(str(destination))
            except OSError:
                destination_stat = None
            if (
                destination_stat is not None
                and stat.S_ISREG(destination_stat.st_mode)
                and not stat.S_ISLNK(destination_stat.st_mode)
                and not _is_reparse(destination_stat)
                and destination.read_bytes() == payload
            ):
                return "UNCHANGED"
            raise InboxError("refusing to overwrite existing output")
        _require_plain_directory(parent, "output parent")
        with destination.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(str(destination), 0o600)
        return "CREATED"
    finally:
        _close_windows_directory_locks(windows_parent_locks)


def command_inventory(args):
    result = inventory(args.root, recursive=args.recursive, include_hash=args.hash)
    status_value = write_single_file(args.out, _canonical_json_bytes(result))
    print("%s: %s (%d screenshots, %d skipped)" % (
        status_value, args.out, len(result["sources"]), len(result["skipped"])
    ))


def command_validate(args):
    data = validate_observations(load_json_strict(args.input))
    print(json.dumps({
        "status": "VALID",
        "sources": len(data["sources"]),
        "items": len(data["items"]),
        "calendar_drafts": sum(
            1 for item in data["items"]
            if item["calendar"] and item["calendar"]["status"] == "draft"
        ),
    }, sort_keys=True))


def command_build(args):
    data = validate_observations(load_json_strict(args.input))
    artifacts = build_artifacts(data)
    status_value = write_artifacts(args.out, artifacts)
    print("%s: %s (%d sources, %d items)" % (
        status_value, args.out, len(data["sources"]), len(data["items"])
    ))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Create a source-linked action inbox without opening or changing screenshots."
    )
    subparsers = parser.add_subparsers(dest="command")
    try:
        subparsers.required = True
    except AttributeError:
        pass
    inventory_parser = subparsers.add_parser("inventory", help="List authorized screenshot filenames")
    inventory_parser.add_argument("root", help="Authorized screenshot directory")
    inventory_parser.add_argument("--out", required=True, help="New JSON inventory path")
    inventory_parser.add_argument("--recursive", action="store_true", help="Include nested directories")
    inventory_parser.add_argument(
        "--hash", action="store_true",
        help="Explicitly read image bytes to include SHA-256; image metadata is still ignored"
    )
    inventory_parser.set_defaults(func=command_inventory)
    validate_parser = subparsers.add_parser("validate", help="Validate observations without reports")
    validate_parser.add_argument("input", help="Observation JSON path")
    validate_parser.set_defaults(func=command_validate)
    build_parser_obj = subparsers.add_parser("build", help="Build deterministic review artifacts")
    build_parser_obj.add_argument("input", help="Observation JSON path")
    build_parser_obj.add_argument("--out", required=True, help="New output directory")
    build_parser_obj.set_defaults(func=command_build)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except InboxError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("ERROR: filesystem operation failed: %s" % exc, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
