# Public Plugin Submission

## Current publication state

Verified on 2026-08-29: OpenAI Platform shows **Published**; the latest remote catalog snapshot shows **GLOBAL/AVAILABLE** with discoverability **UNLISTED**.

- Directory URL: <https://chatgpt.com/plugins/plugins_6a7cbf30f0208191b29866d20a69743a>
- `UNLISTED` does not mean listed or searchable in the public directory.
- Version 1.0.2 in this repository is an update candidate. The verified state above describes the existing remote listing and does not claim that 1.0.2 has been uploaded, reviewed, approved, or published.

## Listing

- Plugin: Screenshot Action Inbox
- Package: `screenshot-action-inbox`
- Version: 1.0.2
- Developer: `battle-doll`
- Category: Productivity
- Submission type: Skills only
- Authentication: None
- Current availability: Global and available, with discoverability unlisted, as verified on 2026-08-29
- Listing language: English
- Documentation languages: English, Korean, Japanese, Simplified Chinese, and Russian
- Tested content and filenames: English and Korean; processor supports UTF-8 generally

Short description:

> Turn screenshots into actions

Long description:

> Organize a user-authorized batch, folder, or ZIP of screenshots into a reviewable action inbox. Extract candidate tasks, dates, events, receipts, and references; preserve source filenames; group duplicates; surface uncertainty; and prepare calendar-ready drafts. Use it when screenshot clutter needs sourced next steps, not for single-image creative edits, OCR-only transcription, email triage, surveillance, identity inference, code architecture analysis, or voice notifications. The plugin never creates calendar events, sends messages, deletes screenshots, or moves files. Its dependency-free processor writes deterministic local review artifacts and makes no network request; optional authorized inventory reads image bytes only when SHA-256 hashing is explicitly requested.

Starter prompts:

1. Turn these screenshots into sourced tasks, dates, and review-only calendar drafts.
2. I have a folder of screenshots—group duplicate actions and flag anything unclear.
3. Review this screenshot ZIP and show which source supports every proposed action.

## Data-use declaration

| Area | Version 1.0.2 behavior |
| --- | --- |
| Accounts/authentication | None |
| Publisher server | None |
| Network requests by bundled code | None |
| Telemetry/analytics/advertising | None |
| Image processing | Host product visually inspects user-provided or user-authorized images |
| Bundled processor input | Bounded, user-created UTF-8 observation JSON |
| Build/validate image access | None; these commands consume structured observation JSON and do not open screenshots or read EXIF |
| Optional inventory | Lists relative filenames and sizes in an authorized folder; hashes bytes only with explicit `--hash` |
| Writes | New local review artifacts in an explicit output directory |
| External writes | None |
| File moves/deletes | None; archive plan is data-only and unexecuted |
| Calendar/messages/payments | Drafts only; never created, sent, or executed |
| Retention by developer | None because no data is sent to the developer |

The host product may process attachments and conversation content under its own privacy policy and workspace controls. The listing must not claim that screenshots remain only on the physical device.

## Release evidence

Run:

```bash
python -X utf8 scripts/verify.py all
```

Release gates:

- source and final-submission metadata validation;
- stdlib-only processor boundary;
- unit, security, path, CSV, ICS, deterministic, and extracted-package tests;
- two byte-identical local ZIP builds;
- Windows, macOS, and Linux CI;
- one identical ZIP SHA-256 across the ten-job CI matrix;
- identical hashes and byte lengths for all five runtime artifacts across the ten-job CI matrix;
- exact Skills-only profile with no MCP, app, hook, or screenshot configuration.

Upload `dist/screenshot-action-inbox-skills-only-1.0.2.zip` only after the aggregate CI job passes for the exact commit being submitted. Never rebuild, replace, or relabel the published 1.0.1 artifacts.

## Review cases

The exact five positive and three negative reviewer cases remain in [`evals/cases.json`](evals/cases.json). The same file now also contains a bilingual discovery golden set with exactly 10 direct, 20 indirect, and 20 negative selection cases. It covers natural screenshot-batch requests and explicit non-selection boundaries for creative image editing, OCR-only transcription, email triage, surveillance and identity inference, code architecture and impact analysis, voice notifications, calendar or message execution, and file moves or deletion.

All fixtures are synthetic and sanitized. Reviewer-case outputs require source filenames, confidence, ambiguity markers, and explicit draft status. Calendar cases additionally require SHA-256-backed source provenance, `CLASS:PRIVATE`, and a stable source-bound UID; incomplete-source cases remain visible in the digest and review counts.

## Submission sequence

1. Confirm the `battle-doll` publisher identity is verified in the selected OpenAI organization.
2. Confirm organization Apps Management write permission.
3. Open the existing plugin and create a **Skills only** update draft at <https://platform.openai.com/plugins>.
4. Fill listing, support, privacy, terms, localization, and availability fields.
5. Upload the exact validated ZIP and wait for every bundled skill scan to finish.
6. Enter the five positive and three negative review cases.
7. Enter the release notes and truthfully complete all policy/IP/data attestations.
8. Select **Submit for review**.
9. Wait for OpenAI review and approval.
10. After approval, separately select **Publish**.
11. Confirm the updated version through the exact listing name and direct directory URL without claiming listed or searchable discoverability.

Draft creation, ZIP upload, completed skill scans, review submission, approval, publication, and enhanced directory placement are distinct states.

## Release notes

> Discovery metadata patch. Screenshot Action Inbox 1.0.2 clarifies when to select the plugin for authorized screenshot batches and when not to select it for creative edits, OCR-only transcription, email triage, surveillance or identity inference, code analysis, voice notifications, or external writes. It adds equivalent first-screen use guidance in English, Korean, Japanese, Simplified Chinese, and Russian plus a bilingual 10 direct, 20 indirect, and 20 negative discovery golden set. Runtime behavior is unchanged: every proposed item remains source-linked, ambiguity remains reviewable, calendar and archive outputs remain drafts, and the dependency-free processor makes no network request.
