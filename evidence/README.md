# Evidence — device and build checks

Screenshots, logs and notes collected while validating the app on the reference
device (POCO F2 Pro, `8ffbcdbf`, Android 13 / HyperOS). Interrupted builds,
failed installs and negative results are recorded too, not just the successes.

| Date | Change | Folder |
| --- | --- | --- |
| 2026-11-17 | `add-client-identity-and-stamping` | [`2026-11-17-identity-stamping/`](2026-11-17-identity-stamping/README.md) |
| 2026-11-17 | `harden-stamp-cost-and-idle-link` | [`2026-11-17-stamp-cap-and-link-refresh/`](2026-11-17-stamp-cap-and-link-refresh/README.md) |
| 2026-11-17 | Launcher icon and loading screen | [`2026-11-17-icon-and-splash/`](2026-11-17-icon-and-splash/README.md) |

## Conventions

- One folder per change and date, with a `README.md` that lists every file and
  what it proves.
- Screenshots are `adb exec-out screencap -p` output, unedited except for
  cropping and downscaling to keep them readable.
- Command transcripts are copied verbatim, including the `adb` invocation.
- A pending check stays listed as pending; do not delete a failed run after
  fixing it, add the successful one next to it.
