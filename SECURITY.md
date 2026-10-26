# Local data and trusted extensions

The web app and its model gateway bind to loopback. Browser API writes require a
per-process app token and same-origin requests. There is no public listener,
tunnel or automatic telemetry. Any model runtime has its own security settings.

Local settings, transcripts, project notes and logs are unencrypted. Keep the
state folder private and outside public repositories or shared sync folders.
Use example paths in screenshots. The Git privacy check is a guard against common
mistakes, not proof that every possible secret or media artifact is safe.

The process controller checks the launching user, PID start time, executable identity,
session and process group. It never stops a service merely because it owns a port.
Stop only the work you started. WSL shares the host GPU and is not extra VRAM.

Plugins are trusted local programs with your account permissions. Capability
labels and source approval are consent controls, not an operating-system sandbox.
Review dependencies too. Modified code requires approval again.

Update checks are advisory. They do not download weights, run a new installer,
overwrite customizations or restart your backend. The optional runtime installer
is an explicit separate operation using upstream sources; inspect it first.

Before publishing, run `python3 tests/check_public_files.py --staged`. Runtimes,
local configs, models, logs and databases must never be committed. Commit with a
GitHub noreply address. Report sensitive issues privately to the maintainer rather
than including credentials or personal paths in a public issue.
