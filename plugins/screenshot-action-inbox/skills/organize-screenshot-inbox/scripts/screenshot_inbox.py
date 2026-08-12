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
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
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


class InboxError(ValueError):
    """A user-correctable validation or output error."""


def _reject_constant(value):
    raise InboxError("non-finite JSON number is not allowed: %s" % value)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
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
        if ord(char) < 32 and char not in "\n\t":
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
    return text


def _path_collision_key(value):
    return unicodedata.normalize("NFC", value).casefold()


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
        if any(char in part for char in '<>:"|?*'):
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
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise InboxError("%s appears to contain a secret; redact it first" % label)
    for match in re.finditer(r"(?<![0-9])(?:[0-9][^0-9A-Za-z]{0,3}){12,18}[0-9](?![0-9])", text):
        candidate = match.group(0)
        if any(unicodedata.category(char).startswith("L") for char in candidate):
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
    counts = {category: 0 for category in sorted(CATEGORIES)}
    for item in data["items"]:
        counts[item["category"]] += 1
    lines = [
        "# %s" % _markdown(data["batch_title"]),
        "",
        "> Review draft. No calendar event, message, deletion, or file move was executed.",
        "",
        "- Generated from observations: `%s`" % data["generated_at"],
        "- Sources reviewed: **%d**" % len(data["sources"]),
        "- Extracted items: **%d**" % len(data["items"]),
        "- Needs review: **%d**" % sum(1 for item in data["items"] if item["status"] == "needs_review"),
        "",
    ]
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
            lines.append("- Sources: %s" % ", ".join("`%s`" % _markdown(name) for name in _source_names(item, source_map)))
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
        if not char.isspace() and unicodedata.category(char) != "Cf":
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
                "path": source_map[source_id]["relative_path"],
                "sha256": source_map[source_id]["sha256"],
            }
            for source_id in item["source_ids"]
        ]
        uid_basis = json.dumps(
            {"item_id": item["id"], "sources": identity_sources},
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
        lines.extend(["STATUS:TENTATIVE", "TRANSP:TRANSPARENT", "END:VEVENT"])
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
    normalized_path = unicodedata.normalize("NFC", source["relative_path"])
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


def _existing_artifacts_match(output_dir, artifacts):
    try:
        root_stat = os.lstat(str(output_dir))
    except OSError:
        return False
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode) or _is_reparse(root_stat):
        return False
    actual = {path.name for path in output_dir.iterdir()}
    if actual != set(artifacts):
        return False
    for name, payload in artifacts.items():
        path = output_dir / name
        try:
            path_stat = os.lstat(str(path))
        except OSError:
            return False
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
            or _is_reparse(path_stat)
            or path.read_bytes() != payload
        ):
            return False
    return True


def write_artifacts(output, artifacts):
    destination = Path(output)
    if _lexists(destination):
        if _existing_artifacts_match(destination, artifacts):
            return "UNCHANGED"
        raise InboxError("output already exists with different or incomplete content: %s" % destination)
    parent = destination.parent
    _require_plain_directory(parent, "output parent")
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
        os.replace(str(stage), str(destination))
    except Exception:
        shutil.rmtree(str(stage), ignore_errors=True)
        raise
    return "CREATED"


def _is_reparse(stat_result):
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _lexists(path):
    try:
        os.lstat(str(path))
    except FileNotFoundError:
        return False
    return True


def _require_plain_directory(path, label):
    try:
        result = os.lstat(str(path))
    except OSError as exc:
        raise InboxError("%s must be an existing plain directory: %s" % (label, exc))
    if not stat.S_ISDIR(result.st_mode) or stat.S_ISLNK(result.st_mode) or _is_reparse(result):
        raise InboxError("%s must be an existing non-link, non-reparse directory" % label)


def inventory(root, recursive=False, include_hash=False):
    base = Path(root)
    _require_plain_directory(base, "inventory root")
    records = []
    skipped = []

    def visit(directory, prefix=""):
        try:
            entries = sorted(os.scandir(str(directory)), key=lambda entry: unicodedata.normalize("NFC", entry.name).casefold())
        except OSError as exc:
            raise InboxError("cannot inventory %s: %s" % (directory, exc))
        for entry in entries:
            relative = (prefix + "/" + entry.name).lstrip("/")
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                skipped.append({"relative_path": relative, "reason": "STAT_FAILED: %s" % exc})
                continue
            if entry.is_symlink() or _is_reparse(entry_stat):
                skipped.append({"relative_path": relative, "reason": "LINK_OR_REPARSE_POINT"})
                continue
            if entry.is_dir(follow_symlinks=False):
                if recursive:
                    visit(Path(entry.path), relative)
                continue
            if not entry.is_file(follow_symlinks=False):
                skipped.append({"relative_path": relative, "reason": "NOT_REGULAR_FILE"})
                continue
            if Path(entry.name).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                validate_relative_path(relative, "inventory path")
            except InboxError as exc:
                skipped.append({"relative_path": relative, "reason": "UNSAFE_PATH: %s" % exc})
                continue
            if entry_stat.st_size > MAX_IMAGE_BYTES:
                skipped.append({"relative_path": relative, "reason": "FILE_TOO_LARGE"})
                continue
            canonical = unicodedata.normalize("NFC", relative)
            record = {
                "id": "src-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12],
                "relative_path": relative,
                "bytes": entry_stat.st_size,
            }
            if include_hash:
                hasher = hashlib.sha256()
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                try:
                    descriptor = os.open(entry.path, flags)
                except OSError as exc:
                    raise InboxError("cannot open inventory source for hashing: %s" % exc)
                with os.fdopen(descriptor, "rb") as handle:
                    opened_stat = os.fstat(handle.fileno())
                    before_key = (
                        entry_stat.st_dev,
                        entry_stat.st_ino,
                        entry_stat.st_size,
                        getattr(entry_stat, "st_mtime_ns", int(entry_stat.st_mtime * 1000000000)),
                    )
                    opened_key = (
                        opened_stat.st_dev,
                        opened_stat.st_ino,
                        opened_stat.st_size,
                        getattr(opened_stat, "st_mtime_ns", int(opened_stat.st_mtime * 1000000000)),
                    )
                    if (
                        not stat.S_ISREG(opened_stat.st_mode)
                        or _is_reparse(opened_stat)
                        or before_key != opened_key
                    ):
                        raise InboxError("inventory source changed before hashing")
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(block)
                    after_stat = os.fstat(handle.fileno())
                    after_key = (
                        after_stat.st_dev,
                        after_stat.st_ino,
                        after_stat.st_size,
                        getattr(after_stat, "st_mtime_ns", int(after_stat.st_mtime * 1000000000)),
                    )
                try:
                    path_after_stat = os.lstat(entry.path)
                except OSError as exc:
                    raise InboxError("inventory source changed during hashing: %s" % exc)
                path_after_key = (
                    path_after_stat.st_dev,
                    path_after_stat.st_ino,
                    path_after_stat.st_size,
                    getattr(path_after_stat, "st_mtime_ns", int(path_after_stat.st_mtime * 1000000000)),
                )
                if (
                    opened_key != after_key
                    or after_key != path_after_key
                    or _is_reparse(path_after_stat)
                ):
                    raise InboxError("inventory source changed during hashing")
                record["sha256"] = hasher.hexdigest()
            records.append(record)

    visit(base)
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
        raise InboxError("refusing to overwrite existing output: %s" % destination)
    parent = destination.parent
    _require_plain_directory(parent, "output parent")
    with destination.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(str(destination), 0o600)
    return "CREATED"


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
