# AiBrainWorX — Codex / Gemini handoff

Updated October 25, 2026 (Pacific). Owner: AiBrainWorX.

## Current scope

This is the separate Linux/WSL alpha, version 0.1.0-alpha.1. The Windows
application remains its own repository. The owner also requested a focused
Windows 0.4.0-alpha.3 context/output, update-check and customization update;
that source is published separately.

No Gemini API or Google Drive automation is configured. The owner can give this
file to Gemini, then bring reviewed proposals back. Do not message other assistants
automatically or treat their notes as permission to change unrelated products.

## Implemented

- Reused original MIT profiles, memory, model catalog, skills and bounded bridges.
  Reuse hashes are in docs/reused-source.json; they describe the starting sources.
- Native POSIX process ownership checks: user, PID start time, executable inode,
  process group and session. Node can change its process title without losing
  ownership. An executable replacement or reused PID is refused.
- Loopback browser workspace, profiles, chat, memory, model library, Update Center
  and reviewed user plugins. No framework/style-library replacement was chosen.
- Strata and compatible text adapters count the final rendered prompt, reserve
  256 context tokens, cap passes at 4096, allow two extra text continuations and
  allocate at most 12288 output tokens. Tools/JSON/Responses are not blindly joined.
- Built-in supported chat keeps a stable visible ID and full local transcript,
  with bounded unverified working summaries. Explicit saved memory remains separate.
  Other backend transports retain history without claiming exact compaction.
- Read-only component checks cover launcher, Strata app/matching engine, Harness,
  recorded model sources, bundled skills, upstream references and user plugins.
  Missing provenance is Needs review, not Up to date. No automatic updater runs.
- API-v1 plugins live in local state with separate data and source approval.
  They are trusted local programs, not a sandbox. No voice plugin is installed.
- Customization migration defaults to preview, uses a separate candidate folder,
  preserves backups and flags conflicting vendor edits for manual review.

## Runtime acceptance

Ubuntu 24.04 WSL2, Python 3.12. Official native Node 24, Harness 0.2.1-rc.2,
Strata 0.2.6 and checksum-verified NVIDIA CUDA13 engine 0.1.50 were installed
separately. Native Strata API started in lazy/unloaded mode and stopped cleanly.
Native Harness reached authenticated-ready state and stopped by verified ownership.
Existing model files were reused; no weights were downloaded or copied.

Use native Linux storage for per-user state and the runtime cache. Windows mounts
may not enforce Harness credential permissions and can make Node imports slow.
The check is preserved. State defaults to the Linux XDG state directory; runtimes
to the XDG cache directory. Actual machine paths are private and not in Git.

The shared gateway was also tested against the already-running Windows Qwen:
normal stop, forced cutoff with exactly two extra passes and one DONE, and native
Responses/tool-schema counting. These are acceptance probes, not speed benchmarks.

## Remaining acceptance and design work

- Native Linux Qwen generation, throughput, long-context handoff quality, live
  Unreal/Blender/Android and fresh-machine installation need separate acceptance.
  WSL shares the GPU with Windows; do not load another large model while the
  Windows model occupies it. No Linux speed improvement is claimed.
- Exact Ollama/LM Studio counting adapters, voice/audio hooks, Linux video playback
  and desktop packaging are not complete. Catalog entries are not installed models.
- Style-library selection was deferred by the owner until functionality is done.
- Keep update installation explicit; do not overwrite user state or vendor patches.
- Run Setup.sh and the privacy guard before publishing. Use the repository-local
  AiBrainWorX GitHub noreply identity. Never commit settings, logs, weights,
  databases, tokens, personal paths or private session notes.