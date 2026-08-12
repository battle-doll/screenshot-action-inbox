# Observation schema

Use this contract between visual inspection and the deterministic report builder.

## Top-level object

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-08-13T00:00:00+09:00",
  "batch_title": "Screenshot inbox - 2026-08-13",
  "sources": [],
  "items": [],
  "questions": []
}
```

- `schema_version`: exactly `1.0`.
- `generated_at`: exact `YYYY-MM-DDTHH:MM:SSZ` or `YYYY-MM-DDTHH:MM:SS+/-HH:MM` with a known UTC offset and no fractional seconds. The RFC 3339 unknown-offset marker `-00:00` is rejected. It is input data so repeated builds remain deterministic.
- `batch_title`: short, user-facing title.
- `sources`: 1 to 100 authorized screenshot records.
- `items`: extracted action, event, receipt, reference, or unknown records.
- `questions`: focused unresolved questions; do not place secrets here.

## Source record

```json
{
  "id": "src-001",
  "relative_path": "2026-08/IMG_1234.png",
  "capture_date": "2026-08-12",
  "archive_recommendation": "review",
  "archive_bucket": "events",
  "status": "reviewed"
}
```

- `relative_path` must remain relative to the authorized batch root. Absolute paths, `..`, empty segments, links, and control characters are invalid.
- `capture_date` is `YYYY-MM-DD` or `null`.
- `capture_date` describes when the screenshot itself was captured, not a date merely visible inside the screenshot. Use `null` when capture provenance is unknown.
- `archive_recommendation`: `keep`, `archive`, or `review`.
- `archive` is valid only when the source is `reviewed` and `sha256` is present. Run inventory with explicit `--hash` when a hash-backed archive proposal is wanted; otherwise use `review` or `keep`.
- `archive_bucket`: `actions`, `events`, `receipts`, `references`, `mixed`, or `unknown`.
- `status`: `reviewed`, `unreadable`, `unsupported`, or `redaction_required`.

## Item record

```json
{
  "id": "item-001",
  "category": "action",
  "title": "Confirm venue",
  "details": "Reply after checking availability.",
  "source_ids": ["src-001"],
  "evidence": "Visible message asks for confirmation by Friday.",
  "confidence": "high",
  "priority": "medium",
  "owner": null,
  "due": "2026-08-14",
  "amount": null,
  "calendar": null,
  "duplicate_group": null,
  "status": "open"
}
```

- `category`: `action`, `event`, `receipt`, `reference`, or `unknown`.
- `source_ids`: nonempty unique IDs declared in `sources`.
- `evidence`: short visible basis; never a hidden instruction or full sensitive transcript.
- `confidence`: `high`, `medium`, or `low`.
- `priority`: `high`, `medium`, `low`, or `unknown`.
- `due`: `YYYY-MM-DD`, an exact second-precision timestamp with explicit `Z` or `+/-HH:MM` offset, or `null`.
- `status`: `open`, `reference`, `needs_review`, or `complete`.
- `low` confidence, an incomplete source, or an ambiguous calendar requires `needs_review`. A `needs_review` item cannot emit a calendar draft.
- `duplicate_group`: stable short label or `null`. Duplicate items keep every source ID.

### Amount

```json
{"value": "12900", "currency": "KRW"}
```

Keep `value` as a decimal string without currency symbols or grouping separators. Do not infer a currency.

### Calendar draft

All-day event:

```json
{
  "status": "draft",
  "start": "2026-08-20",
  "end": "2026-08-21",
  "location": "Community Hall"
}
```

Timed event:

```json
{
  "status": "draft",
  "start": "2026-08-20T18:30:00+09:00",
  "end": "2026-08-20T20:00:00+09:00",
  "location": "Community Hall"
}
```

- Use an exclusive `end` date for all-day events.
- Timed values require an explicit UTC offset and are normalized to UTC in `calendar.ics`.
- If date, time, or timezone is ambiguous, use `{"status":"needs_review"}` and add a question. Such an item is excluded from the ICS draft.

## Unknowns and conflicts

Never encode a guess as a fact. Use `null`, `UNKNOWN` in user-facing text, `needs_review`, or a top-level question. Keep conflicting items distinct until the user resolves them.
