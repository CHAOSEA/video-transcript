# Codex project guidance

- For a user request to transcribe an online video link or local audio/video with this project, use the `$video-transcript-workbench` skill at `.codex/skills/video-transcript-workbench/SKILL.md`.
- The Python CLI is the transcription engine. The skill only routes the request, runs the CLI in the available local environment, and verifies its outputs.
- Read `docs/online-support-matrix.md` before making platform-support claims. Do not generalize sample-level PASS results to every video.
- Keep transcript outputs outside the Git working tree unless the user explicitly chooses another path. Never commit media, transcripts, evidence, credentials, or signed URLs.
