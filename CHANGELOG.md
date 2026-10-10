# Changelog

Changes are listed in Keep a Changelog style.

## [0.1.0] - Unreleased

### Added

- Local audio, video and caption inputs, and individual owned-video links.
- CSV time ranges, resumable transcripts, local CPU speech and an optional GPU flag.
- Timestamped text, segment and word data, and one library summary.
- Topic trends, question and deferral hints, and clear versus ambiguous filler counts.
- Claude and Codex prompt files with a deterministic citation checker.
- A synthetic offline demo, local doctor checks and standard-library tests.
- Optional site-caption reference copies and filler-preservation hint.
- Nullable word times and retained word probabilities.
- GPU library launcher, smoke-check script and separate GPU extra.

### Reliability fixes

- Retain and label no-speech and short transcripts while excluding their rates and trends.
- Distinguish unsupported files, damaged media, missing tools and absent audio streams.
- Add a Python setup guard, no-admin setup routes and clearer recognition limits.
- Ignore generic sign-offs in deferral hints and explain required report heading levels.
- Disable ONNX Runtime initialization telemetry before import, as well as Hugging Face telemetry.
- Show sample sizes on trend labels and require 30 total matches; explain steady.
- Warn when cached media skips CUDA validation; fail the Python guard without ensurepip.
- Mark unpunctuated hint excerpts as potentially cut and clarify fail-fast exit codes.
