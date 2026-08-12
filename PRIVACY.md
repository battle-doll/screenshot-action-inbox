# Privacy Policy

Effective date: August 13, 2026

Screenshot Action Inbox is a skills-only plugin published by `battle-doll`. It has no publisher-operated server, user account, connector, telemetry, advertising, or analytics.

## Data the developer receives

The plugin does not send screenshot contents, filenames, extracted observations, reports, or usage data to the developer. The developer therefore does not collect or retain personal data through the plugin.

The ChatGPT or Codex product in which the plugin runs may process user-provided files and conversation content under that product's applicable privacy policy, workspace settings, and retention controls. This policy does not change or override the host product's data handling.

## Local processing

The bundled build and validation commands read only a user-created structured observation JSON file. They do not open screenshot images, read EXIF metadata, access the network, inspect environment secrets, or discover unrelated files. The build command writes reports only to the output location selected in the user's environment.

The optional inventory command lists relative filenames and file sizes within an explicitly authorized folder. It skips links and reparse points. It does not open image contents unless the user explicitly selects the `--hash` option, which reads bytes solely to calculate a local SHA-256 value. Neither mode sends data to the developer.

## Sensitive data

Users should redact passwords, authentication codes, API keys, full payment-card or bank-account numbers, government identifiers, and unnecessary medical or minors' information before providing screenshots. The workflow instructs the host model not to reproduce such values, and the processor rejects several common secret and payment-card patterns. No automated detection is complete.

## Sharing, sale, and retention

The developer does not sell, share, or retain plugin data because the plugin sends no data to the developer. Generated files remain under the user's control and follow the storage and retention rules of the user's device or workspace.

## User choices

Users choose which screenshots to provide, may omit or redact any image, may inspect every generated artifact, and may delete generated files using their normal device or workspace controls. Archive and calendar outputs are drafts and are never applied automatically. `CLASS:PRIVATE` is a calendar classification hint, not encryption or access control: `calendar.ics` remains a plaintext file and can contain event text and source filenames, so users should protect it as potentially sensitive.

## Contact

For privacy questions, open a private-safe issue without attaching personal data at <https://github.com/battle-doll/screenshot-action-inbox/issues>.
