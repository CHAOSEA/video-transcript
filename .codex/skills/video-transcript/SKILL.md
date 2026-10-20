---
name: video-transcript
description: Use the local Video Transcript CLI to transcribe supported video links or local audio/video, verify outputs, or create a separate analysis note from a transcript when requested. Do not use for changing the transcription engine.
---

# Video Transcript

Use the installed Video Transcript CLI entry points as the transcription engine. This skill routes the request and checks the output; it does not perform ASR itself.

## Choose the command

- Online video link with a transcript requested: use `video-transcript-link`. It tries manual subtitles, then automatic subtitles, then local ASR only when platform access and resource limits permit. By default, the CLI lets CTranslate2 try CUDA and uses CPU/int8 when CUDA is unavailable.
- Online video link with subtitles only requested: use `video-transcript-online`. It does not download media or run ASR.
- Local audio or video file: use `video-transcript`. The CLI supports WAV, MP3, M4A, MP4, WebM, Ogg, and Opus.

Read [the online support matrix](../../../docs/online-support-matrix.md) when a user asks whether a platform/path is supported or an access failure needs explaining. Treat its results as sample-scoped, not as a universal guarantee.

## Prepare the run

1. Use the machine and execution environment that can access the media, installed CLI, and local model files. Codex discovers this repository-scoped skill when the repository is open as the active local project.
2. Resolve the executable before running. Prefer the repository virtual environment entry point (`.venv\Scripts\video-transcript-link.exe` on Windows or `.venv/bin/video-transcript-link` on Unix-like systems); otherwise use an already available command on PATH. If neither exists, report the installation requirement. Do not search unrelated private project folders or install dependencies automatically.
3. Preserve the user's requested model and language. Do not select a model on the user's behalf. For a local-media ASR request without a model, show `video-transcript models recommend --intent quality` and ask which candidate to use. For an online transcript request without a model, it is safe to try the subtitle-first link command without `--model`; if subtitles are unavailable, the CLI must return `model_required` before downloading media. Then ask the user to choose a model before retrying. Never prepare/download a model unless the user explicitly authorizes that network action. Device defaults to `auto`; use `--device cuda` only when the user explicitly requires GPU, and `--device cpu` when requested.
4. Use the requested output directory. If none is given, choose a new, non-existing directory in the user's Documents/VideoTranscript folder, outside the Git working tree. The CLI requires a new output directory; never reuse or overwrite a prior run.
5. For online URLs, use the CLI directly. Do not fetch media with a separate downloader, use browser cookies, read account profiles, alter proxy settings, bypass access checks, or retry the same blocked request repeatedly.
6. Keep share tokens and signed URLs out of progress messages, saved logs, and reports. Pass a user-provided URL only to the CLI as required; do not echo its full query string.

## Run and verify

Use the CLI's installed command in the active environment. On Windows PowerShell, the patterns are:

~~~powershell
video-transcript-link 'https://www.youtube.com/watch?v=VIDEO_ID' --language zh --device auto --output-dir 'D:\transcripts\subtitles-first-01' # model optional for subtitle-first attempt
video-transcript-online 'https://www.youtube.com/watch?v=VIDEO_ID' --language zh --output-dir 'D:\transcripts\subtitles-01'
video-transcript 'D:\media\sample.mp4' --language zh --model small --device auto --output-dir 'D:\transcripts\local-01'
~~~

For first-use diagnosis, run `video-transcript doctor` (or `video-transcript doctor --json` for an agent-readable report), then `video-transcript models list` and `video-transcript models recommend --intent quality`. Model inspection and recommendation are read-only. `video-transcript models prepare <name>` downloads model files and requires explicit user authorization; `video-transcript models verify <name>` checks a prepared model. These commands are part of the v0.3.2 Beta CLI and are absent from the historical v0.3.0 release; check the active environment with `python -m pip show video-transcript-workbench` before using version-specific commands.

Run only the command matching the user's request. Do not install dependencies, download large models, or change machine configuration unless the user explicitly asks for setup.

After the command exits:

- Confirm success from its exit status and output evidence. Check that `transcript.md` exists and is non-empty for a successful transcript run; check `evidence.json` and its recorded status and method.
- Treat `success`, `uncertain`, `no_speech_detected`, and `failed` as distinct evidence states. For `uncertain`, preserve and report the quality warnings and say the raw segments need review. For `no_speech_detected`, do not describe the status note as a transcript or as proof that the media contains no speech.
- Do not report success based only on files being present. On failure, report the actual stage/status from evidence or CLI output and the next safe action.
- If an online request stops at an access check, do not describe it as definitely requiring login unless the evidence explicitly says so. If the user already has media saved through a platform-permitted path, offer the local `video-transcript` command as the next step; never retrieve cookies, use another downloader to evade the check, or claim that local import proves online support.
- Report output paths, selected method (manual subtitle, automatic subtitle, or local ASR), and actual model/device/compute type when available. If CUDA auto-selection falls back to CPU, state that the same model was retained. Do not paste the full transcript unless asked.
- Never claim a universal CER score, accuracy ranking, or platform guarantee. The only documented comparison is a 30-utterance FLEURS Mandarin read-speech set; there is no CER benchmark on manually checked natural-video dialogue.

## Optional analysis document

Only create an analysis document when the user asks for analysis, a summary, or another written interpretation. The CLI does not generate this file.

1. Confirm that a successful `transcript.md` exists. If transcription failed, report that first and do not invent an analysis from partial evidence.
2. If the active agent/model would send transcript text to a cloud service, explain that before reading or processing the private transcript and obtain explicit user authorization for that external processing. A request to create an analysis document alone does not authorize uploading private transcript text. Without authorization, do not read or send it; offer a local model or ask for authorization. Never send it to a separate service.
3. Create `analysis.md` next to the transcript, or use the path requested by the user. If the target already exists, choose a new filename or ask where to save it. Never overwrite `transcript.md` or `evidence.json`.
4. Shape the analysis to the user's request. If no format is specified, provide a concise summary, key points with transcript time ranges, and a short section for claims or uncertainties that need verification.
5. Separate what the speaker said from the agent's interpretation. Refer to timestamps for transcript-grounded claims, flag possible ASR errors when wording is unclear, and do not silently correct the source transcript. Do not add outside research unless requested.

## Privacy and access

The CLI keeps ASR local and does not use cloud transcription. An optional analysis document is generated by the active agent/model, not by the local CLI. Online access and downloaded media still depend on platform rules and the user's rights. Do not retrieve credentials or attempt to defeat login, human-verification, permission, or regional checks.

If the user asks the agent to read, summarize, translate, or analyze the transcript, explain that this may send transcript text to the agent's model service when applicable. If they only asked for transcription, return the local output paths without loading the transcript contents.
