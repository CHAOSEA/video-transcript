---
name: video-transcript
description: Use the local Video Transcript CLI to transcribe supported video links or local audio/video, verify outputs, or create a separate analysis note from a transcript when requested. Do not use for changing the transcription engine.
---

# Video Transcript

Use the installed Video Transcript CLI entry points as the transcription engine. This skill routes the request and checks the output; it does not perform ASR itself.

## Choose the command

- Online video link with a transcript requested: use `video-transcript-link`. It tries manual subtitles, then automatic subtitles, then local CPU ASR only when platform access and resource limits permit.
- Online video link with subtitles only requested: use `video-transcript-online`. It does not download media or run ASR.
- Local audio or video file: use `video-transcript`. The CLI supports WAV, MP3, M4A, MP4, WebM, Ogg, and Opus.

Read [the online support matrix](../../../docs/online-support-matrix.md) when a user asks whether a platform/path is supported or an access failure needs explaining. Treat its results as sample-scoped, not as a universal guarantee.

## Prepare the run

1. Use the machine and execution environment that can access the media, installed CLI, and local model files. Codex discovers this repository-scoped skill when the repository is open as the active local project.
2. Resolve the executable before running. Prefer the repository virtual environment entry point (`.venv\Scripts\video-transcript-link.exe` on Windows or `.venv/bin/video-transcript-link` on Unix-like systems); otherwise use an already available command on PATH. If neither exists, report the installation requirement. Do not search unrelated private project folders or install dependencies automatically.
3. Preserve the user's requested model and language. If no model is specified, use `small` as the tested convenience default; do not claim it is the most accurate. If the model is missing, report that and ask before downloading weights or changing environments.
4. Use the requested output directory. If none is given, choose a new, non-existing directory in the user's Documents/VideoTranscriptWorkbench folder, outside the Git working tree. The CLI requires a new output directory; never reuse or overwrite a prior run.
5. For online URLs, use the CLI directly. Do not fetch media with a separate downloader, use browser cookies, read account profiles, alter proxy settings, bypass access checks, or retry the same blocked request repeatedly.
6. Keep share tokens and signed URLs out of progress messages, saved logs, and reports. Pass a user-provided URL only to the CLI as required; do not echo its full query string.

## Run and verify

Use the CLI's installed command in the active environment. On Windows PowerShell, the patterns are:

~~~powershell
video-transcript-link 'https://www.youtube.com/watch?v=VIDEO_ID' --language zh --model small --output-dir 'D:\transcripts\run-01'
video-transcript-online 'https://www.youtube.com/watch?v=VIDEO_ID' --language zh --output-dir 'D:\transcripts\subtitles-01'
video-transcript 'D:\media\sample.mp4' --language zh --model small --output-dir 'D:\transcripts\local-01'
~~~

Run only the command matching the user's request. Do not install dependencies, download large models, or change machine configuration unless the user explicitly asks for setup.

After the command exits:

- Confirm success from its exit status and output evidence. Check that `transcript.md` exists and is non-empty for a successful transcript run; check `evidence.json` and its recorded status and method.
- Do not report success based only on files being present. On failure, report the actual stage/status from evidence or CLI output and the next safe action.
- Report output paths, selected method (manual subtitle, automatic subtitle, or local ASR), and model/device when available. Do not paste the full transcript unless asked.
- Never claim a CER score, accuracy ranking, or universal platform compatibility; the project has no human-reference CER benchmark.

## Optional analysis document

Only create an analysis document when the user asks for analysis, a summary, or another written interpretation. The CLI does not generate this file.

1. Confirm that a successful `transcript.md` exists. If transcription failed, report that first and do not invent an analysis from partial evidence.
2. Tell the user that analysis requires the agent to read the transcript and that the text may be processed by the active model service. When the user requested analysis, continue with that requested task; do not send the transcript to a separate service.
3. Create `analysis.md` next to the transcript, or use the path requested by the user. If the target already exists, choose a new filename or ask where to save it. Never overwrite `transcript.md` or `evidence.json`.
4. Shape the analysis to the user's request. If no format is specified, provide a concise summary, key points with transcript time ranges, and a short section for claims or uncertainties that need verification.
5. Separate what the speaker said from the agent's interpretation. Refer to timestamps for transcript-grounded claims, flag possible ASR errors when wording is unclear, and do not silently correct the source transcript. Do not add outside research unless requested.

## Privacy and access

The CLI keeps ASR local and does not use cloud transcription. An optional analysis document is generated by the active agent/model, not by the local CLI. Online access and downloaded media still depend on platform rules and the user's rights. Do not retrieve credentials or attempt to defeat login, human-verification, permission, or regional checks.

If the user asks the agent to read, summarize, translate, or analyze the transcript, explain that this may send transcript text to the agent's model service when applicable. If they only asked for transcription, return the local output paths without loading the transcript contents.
