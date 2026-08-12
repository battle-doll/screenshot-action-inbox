# Release-hardening checkpoint

Checkpoint time: 2026-08-13 02:00 KST

## Current state

- Worktree branch: `codex/screenshot-action-inbox`
- The current hardening suite passes locally: 74 tests, with three Windows-only tests skipped on macOS.
- `python -X utf8 scripts/verify.py all` passes and produces a reproducible local candidate.
- The candidate is **not release-approved** and must not be uploaded to the OpenAI plugin portal yet.
- The previously created `v1.0.0` release and tag were withdrawn and deleted before any portal upload.

## Release blockers to resolve next

1. Bind source reads to stable file descriptors/handles. `scripts/verify.py::_read_regular_bytes` currently performs a pathname open after link checks, leaving a check/open race.
2. Validate the exact immutable package snapshot. `validate_source` and `_snapshot_package_sources` currently read separately, so validation is not yet cryptographically or structurally bound to the bytes passed to `_canonical_archive_bytes`.
3. Close computed-name static-boundary bypasses in `validate_processor_boundary`, including dangerous APIs reached through constructed `getattr` names. Keep the Windows-required `ctypes` use narrowly allowlisted.
4. Require exact expected CI job/platform identities in `compare_matrix`, not only a count of mutually identical bundles.
5. Run the three non-skipped Windows-only junction/handle/race tests on a real Windows runner, then rerun the complete Windows, macOS, and Linux matrix.

## Resume sequence

1. Fix blockers 1-4 and add adversarial regression tests.
2. Run `python -X utf8 scripts/verify.py all` twice and `git diff --check`.
3. Commit and push the hardening branch; do not update `main` yet.
4. Require every GitHub Actions matrix job and the aggregate identity job to pass on the same commit.
5. Independently audit the exact commit and compare the downloaded aggregate ZIP/runtime evidence with a fresh local build.
6. Only then update `main`, create a new `v1.0.0` release, reinstall the local plugin from the exact artifact, and upload that ZIP at <https://platform.openai.com/plugins>.
7. Treat portal upload, scanning, review submission, approval, and directory publication as separate states.

## Verification command

```bash
cd "/Users/aether/Documents/ChatGPT/플러그인"
python -X utf8 scripts/verify.py all
```
