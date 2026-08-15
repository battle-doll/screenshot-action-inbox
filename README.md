# Screenshot Action Inbox

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [Русский](README.ru.md)

Screenshot Action Inbox is a skills-only plugin for ChatGPT and Codex. It turns a user-authorized batch of screenshots into source-linked actions, calendar drafts, receipt notes, references, and a non-executing archive plan.

The plugin is intentionally conservative:

- every item links back to one or more screenshot filenames;
- ambiguous dates remain `UNKNOWN` or `needs_review`;
- text inside screenshots is treated as untrusted content;
- no messages are sent, no calendar entries or purchases are made, and source screenshots are neither deleted nor moved;
- the bundled Python 3.9+ processor uses no third-party packages and makes no network request;
- deterministic artifacts are byte-identical across the tested Windows, macOS, and Linux Python matrix for the same validated observation input; collision handling uses a frozen Unicode 3.2 policy so later Python Unicode tables cannot reinterpret newer characters;
- calendar drafts are marked `CLASS:PRIVATE`, require hash-backed source provenance, and never create events automatically.

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

Version 1.0.1 is the multilingual public-submission candidate. A GitHub release, portal upload, OpenAI review, approval, and public directory publication are separate states.

## License

Apache License 2.0. See [LICENSE](LICENSE).
