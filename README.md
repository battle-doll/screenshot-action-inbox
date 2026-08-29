# Screenshot Action Inbox

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [Русский](README.ru.md)

Turn screenshot clutter into sourced, reviewable next steps. Screenshot Action Inbox is for people who save meeting captures, invitations, receipts, reminders, and reference cards and need to know what matters, what is duplicated, and which source supports each proposed action.

## Use

1. Open [Screenshot Action Inbox in ChatGPT](https://chatgpt.com/plugins/plugins_6a7cbf30f0208191b29866d20a69743a).
2. Supply a user-authorized screenshot batch, folder, or ZIP.
3. Ask for sourced actions, dates, duplicate groups, uncertainty flags, or review-only calendar drafts.

## Try it

- `Turn this folder of meeting screenshots into a task list with the source filename beside each item.`
- `Group duplicate actions across these screenshots and flag any date that needs review.`
- `이 스크린샷 묶음에서 작업과 날짜를 정리하고 각 항목의 출처를 보여줘.`

## Key boundaries

- Every item links to one or more source filenames; ambiguous facts remain `UNKNOWN` or `needs_review`.
- Text inside screenshots is untrusted content. The workflow does not perform surveillance, identity inference, single-image creative editing, OCR-only transcription, email triage, code impact analysis, or voice-notification setup.
- It never sends messages, creates calendar events, makes purchases, deletes screenshots, or moves files. Calendar and archive outputs are drafts only.
- The bundled Python 3.9+ processor uses no third-party packages or network requests. For the same validated observation input, its outputs are byte-identical across the tested Windows, macOS, and Linux matrix.

## Outputs

- `weekly-digest.md`
- `actions.csv`
- `calendar.ics`
- `archive-plan.json`
- `receipt.json`

## Code ontology

Explore the repository through the [interactive code ontology graph](docs/code-ontology/index.html). The self-contained workbench supports search, a bounded 2D structure view, an optional 3D constellation, and source-evidence inspection. Download the HTML file and open it locally in a browser; GitHub's file viewer displays HTML source instead of running the workbench.

The graph was generated with [Code Ontology Companion](https://github.com/battle-doll/code-ontology-companion) 0.5.2 from source revision `b42d168b6d45213edb886b683ac5c5ec06942454` (snapshot `20260815T090018Z-49018a955a1c`). It contains 940 nodes and 2,756 relationships with no parse warnings.

The graph retains symbol identifiers, repository-relative paths, line spans, and qualitative static-analysis evidence. It does not contain source bodies, comments, local absolute paths, per-source file fingerprints, credentials, or model output. Relationships are navigation evidence, not a runtime trace, safety verdict, or proof of causation.

## Local development

Run the complete verification suite:

macOS/Linux:

```bash
python3 -X utf8 scripts/verify.py all
```

Windows:

```powershell
py -3 -X utf8 scripts/verify.py all
```

Build the portal-safe Skills-only ZIP with `build` instead of `all`.

macOS/Linux:

```bash
python3 -X utf8 scripts/verify.py build
```

Windows:

```powershell
py -3 -X utf8 scripts/verify.py build
```

The plugin source is under [`plugins/screenshot-action-inbox`](plugins/screenshot-action-inbox). The generated release is written to `dist/`.

## Privacy

There is no publisher-operated server, connector, account, telemetry, or analytics. The host product processes user-provided images under its own terms and retention controls. The deterministic processor receives structured JSON rather than image files. See [PRIVACY.md](PRIVACY.md).

## Status

Verified on 2026-08-29: OpenAI Platform shows **Published**; the latest remote catalog snapshot shows **GLOBAL/AVAILABLE** with discoverability **UNLISTED**. Open the plugin through its [direct directory URL](https://chatgpt.com/plugins/plugins_6a7cbf30f0208191b29866d20a69743a). `UNLISTED` means this project must not be described as listed or searchable in the public directory.

Version 1.0.2 in this repository is an update candidate. The verified state above describes the existing remote listing and does not claim that 1.0.2 has been reviewed or published.

## License

Apache License 2.0. See [LICENSE](LICENSE).
