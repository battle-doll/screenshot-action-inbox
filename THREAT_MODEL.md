# Threat Model

## Assets and trust boundaries

Protected assets are screenshot contents, third-party personal data, credentials, source provenance, generated artifacts, local files, and the user's understanding of what was or was not completed.

Trust boundaries are:

1. user-selected screenshots to the ChatGPT or Codex host;
2. untrusted pixels, visible text, filenames, QR codes, and metadata to model observations;
3. probabilistic observations to strict JSON;
4. untrusted JSON to the deterministic processor;
5. CSV, ICS, and archive drafts to downstream applications;
6. a data-only plan to any later separately authorized action.

## Primary threats and controls

| Threat | Control |
| --- | --- |
| Screenshot prompt injection | Treat visible text, links, QR codes, filenames, and metadata as quoted untrusted content; never follow them as instructions. |
| Cross-image disclosure | Preserve per-source provenance and correlate sources only for the user's stated goal. |
| Secret or sensitive-data leakage | Minimize evidence; reject common secrets and full payment-card patterns after compatibility normalization; redact sensitive skipped filenames and require redaction when necessary. |
| Hallucinated dates or claims | Require visible evidence, confidence, explicit offset for timed events, and `UNKNOWN`/`needs_review` for ambiguity. |
| Path traversal, link escape, and path-swap races | Validate every path component; use descriptor-bound POSIX traversal and pinned Windows handles; reject links/reparse points; recheck file identity; never execute archive paths. |
| CSV formula injection | Prefix dangerous user-controlled text cells; validate numeric amount fields separately. |
| ICS property injection or disclosure | Escape values, reject controls, emit a private allowlisted VEVENT subset, use CRLF and octet-aware folding, and omit attendees, organizers, alarms, URLs, and METHOD. |
| Archive damage | Emit `PLAN_ONLY`, `dry_run: true`, `executed: false`; include no executor or shell command. |
| Hostile JSON | Reject duplicate keys, unknown fields, non-finite values, excessive size/depth/counts, wrong types, invalid IDs, and missing provenance. |
| Partial or nondeterministic output | Use canonical serialization, source-hash-derived calendar IDs, explicit time input, a frozen Unicode policy, and no ambient randomness in final artifacts. The release verifier uses descriptor- or handle-bound no-reparse writes and fails closed when a Windows target already exists with different bytes. |
| Privilege drift | Ship Skills only; adding MCP, hooks, apps, network, or external writes requires a new security review and release. |

## Residual risk

Image understanding can misread or omit content. Screenshots may be forged, cropped, or stale. Secret-pattern detection cannot recognize every sensitive value. Host-product attachment handling and downstream calendar/spreadsheet behavior are outside the plugin's bundled processor. Human review remains mandatory.
