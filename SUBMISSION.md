# Public Plugin Submission

## Listing

- Plugin: Screenshot Action Inbox
- Package: `screenshot-action-inbox`
- Version: 1.0.0
- Developer: `battle-doll`
- Category: Productivity
- Submission type: Skills only
- Authentication: None
- Availability target: Global, subject to publisher verification and policy confirmation
- Listing language: English
- Tested content and filenames: English and Korean; processor supports UTF-8 generally

Short description:

> Turn screenshots into actions

Long description:

> Turn batches of user-provided screenshots into a reviewable action inbox. Extract candidate tasks, dates, events, receipts, and references; tie every item to its source screenshot; group duplicates; mark uncertainty; and draft calendar-ready entries. The plugin never creates calendar events, sends messages, deletes screenshots, or moves files. Its dependency-free build and validation commands consume structured observations and write deterministic local reports for review; optional authorized inventory reads image bytes only when SHA-256 hashing is explicitly requested.

Starter prompts:

1. Turn these screenshots into a sourced action inbox and flag anything uncertain.
2. Extract tasks and dates, group duplicates, and draft calendar entries for review.
3. Prioritize this screenshot batch and show the source for every proposed action.

## Data-use declaration

| Area | Version 1.0.0 behavior |
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

Upload `dist/screenshot-action-inbox-skills-only-1.0.0.zip` only after the aggregate CI job passes for the exact commit being submitted.

## Review cases

The exact five positive and three negative cases are in [`evals/cases.json`](evals/cases.json). All fixtures must be synthetic and sanitized. Expected outputs require source filenames, confidence, ambiguity markers, and explicit draft status. Calendar cases additionally require SHA-256-backed source provenance, `CLASS:PRIVATE`, and a stable source-bound UID; incomplete-source cases must remain visible in the digest and review counts.

## Submission sequence

1. Confirm the `battle-doll` publisher identity is verified in the selected OpenAI organization.
2. Confirm organization Apps Management write permission.
3. Create a **Skills only** draft at <https://platform.openai.com/plugins>.
4. Fill listing, support, privacy, terms, localization, and availability fields.
5. Upload the exact validated ZIP and wait for every bundled skill scan to finish.
6. Enter the five positive and three negative review cases.
7. Enter the release notes and truthfully complete all policy/IP/data attestations.
8. Select **Submit for review**.
9. Wait for OpenAI review and approval.
10. After approval, separately select **Publish**.
11. Confirm publication using the exact listing name or the portal directory URL.

Draft creation, ZIP upload, completed skill scans, review submission, approval, publication, and enhanced directory placement are distinct states.

## Release notes

> Initial Skills-only submission. Screenshot Action Inbox turns user-provided screenshot batches into source-linked actions, dates, events, receipt notes, and references. It flags ambiguity, generates review-only calendar and archive drafts, and includes no MCP server, app connector, authentication, telemetry, or external write actions. Its dependency-free processor is tested on Windows, macOS, and Linux.
