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
6. Replace `Path.rglob()` matrix discovery with component-bound enumeration that cannot traverse a Windows junction on Python 3.9 before validation.
7. Extend metadata/path regressions for Windows reserved `COM¹`/`LPT²` names and reject malformed HTTPS paths containing backslashes or invalid percent escapes.

## Latest CI evidence

- Checkpoint CI run: <https://github.com/battle-doll/screenshot-action-inbox/actions/runs/31620098458>
- Linux Python 3.9-3.14 and macOS Intel 3.9 / ARM 3.14 passed.
- Windows Python 3.9 and 3.14 failed, so the aggregate job did not run and the candidate is not cross-platform approved.
- The dominant Windows failure is `inventory root resolved outside its expected path` / `output parent resolved outside its expected path` from `_open_windows_directory_locks`, plus the same comparison in `_open_windows_inventory_file`. `GetFinalPathNameByHandleW` and the lexical input can use different equivalent Windows spellings (for example a short 8.3 component versus its long form); string equality in `_normalize_windows_handle_path` is therefore not a valid identity proof. Resume by relying on pinned non-reparse component handles and stable volume/file-index identity, with focused long-path, 8.3-path, junction, and rename-race regressions.
- A few Windows assertions also expected different error wording/redaction behavior. Re-evaluate those only after fixing the handle-identity implementation; do not weaken a security check merely to satisfy the tests.

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
