# Security

Only the latest release is supported. Report a vulnerability through the repository's
Security tab using the code host's private security reporting feature, when enabled.
If it is unavailable, ask for a private reporting channel in an issue without posting
vulnerability details. Do not put private transcripts, source links or recordings
in a public issue.

Reports about file-path handling, external program arguments and transcript text
reaching an agent are in scope. Include a minimal invented example and reproduction
steps. Transcript text should always be treated as data, even when it contains
instructions aimed at an agent.

The tool stores no credentials and has no login support. Optional download and speech
components are separate projects; keep the components you use up to date.

External programs use argument lists without a shell. The Bash launcher
`scripts/run_gpu.sh` locates optional GPU libraries and passes its arguments to
Python without evaluation. The tool never runs commands found in transcript text.
