# Video Transcript

**Video Transcript · v0.3.2 Beta Release Candidate**<br>
[简体中文](README.md) · [Online platform verification matrix](docs/online-support-matrix.md)

> This README is prepared for the v0.3.2 Beta candidate. The installation commands below target the v0.3.2 Release wheel and checksum file; they become downloadable only after that Release and its assets are published. The already published [v0.3.0 Pre-release](https://github.com/CHAOSEA/video-transcript/releases/tag/v0.3.0) is a historical version and does not include the Doctor, model-guidance, or CUDA support described here.

Turn supported video links or local media into timestamped transcripts. When usable subtitles are unavailable, the tool can recognize speech locally if the platform permits media retrieval. This gives some videos without captions a local ASR path, subject to platform access.

The CLI writes `transcript.md` and `evidence.json` with raw segments, source, and run settings. If you request analysis in Codex, the project Skill can help save a separate `analysis.md`. If this requires sending a private transcript to a cloud model, the agent must explain that and obtain explicit authorization first; requesting analysis alone does not authorize uploading the text. The analysis is optional and never overwrites the transcript; the CLI does not generate it.

> The Beta has gone through multiple real-platform sample checks and automated regression runs. Public claims are scoped by platform and processing path; they do not guarantee success for every video.

## What is this, and how do I use it?

The core product is a standalone Python command-line tool (CLI). This repository also includes a Codex project Skill that helps an agent invoke the CLI and check the outputs. Subtitle retrieval, media processing, and ASR are always performed by the CLI.

### Recommended: Codex + the project Skill

1. Open the repository root in Codex.
2. Install the CLI using the instructions below. A subtitle-only path needs no model; choose and prepare a local Whisper model only if ASR is needed.
3. Ask for a transcript directly, or invoke the shorter Skill name `$video-transcript`. For example: “Use `$video-transcript` to transcribe this link in Chinese and save it under D:\transcripts\run-01.”

The Skill is at [.codex/skills/video-transcript/SKILL.md](.codex/skills/video-transcript/SKILL.md). It selects the online-link, subtitles-only, or local-media entry point and checks the CLI output. It can help create a separate `analysis.md` when requested. If this requires sending private transcript text to a cloud model, the agent must explain that and obtain explicit authorization first; requesting analysis alone is not authorization to upload the text. The analysis file stays separate from the source transcript.

### Use the CLI on its own

Codex, WorkBuddy, and cloud AI services are optional. After installing the project and activating .venv, run this in PowerShell:

~~~powershell
video-transcript-link "https://www.youtube.com/watch?v=VIDEO_ID" --language zh --model small --output-dir "D:\transcripts\run-01"
~~~

Use video-transcript for local media and video-transcript-online for subtitles only.

### Using with Codex, WorkBuddy, and Other AI Agents

Video Transcript Workbench provides a standard command-line interface and is not tied to a particular AI agent. Codex, WorkBuddy, and other agents authorized to use a terminal and files can run the same CLI commands; no dedicated Skill is needed to invoke an installed CLI. This repository includes a Codex project Skill to help Codex select commands and verify results, but it is neither the transcription engine nor a prerequisite for CLI use. The project does not currently provide a native WorkBuddy Skill or connector.

The execution environment must have access to the installed CLI, Python dependencies, prepared local model, and media to process. A cloud agent or another computer does not automatically have access to files, terminals, or model caches on your computer; an authorized remote execution environment is required to use those resources. Use `video-transcript-link` for online video links, `video-transcript` for local audio/video, and `video-transcript-online` for subtitle retrieval only. Model preparation is explicit; ASR loads the local model and runs on the computer executing the CLI.

#### Generic Agent workflow (Windows PowerShell)

The diagnostic and model-guidance commands below are for the v0.3.2 CLI; the published v0.3.0 wheel does not include them. Agents can run `python -m pip show video-transcript-workbench` in the Python environment that runs the CLI, then use commands supported by that installed version.

```powershell
# Check that the commands are available, then inspect local models and devices
Get-Command video-transcript, video-transcript-link, video-transcript-online
video-transcript doctor --json | ConvertFrom-Json

# Inspect model state; Doctor, list, and recommend do not download
video-transcript models list
video-transcript models recommend --intent quality --device auto
# Run this only after the user chooses a model and explicitly decides to download it:
# video-transcript models prepare small

# Local media: choose this or the online-link command
video-transcript "D:\media\sample.mp4" --model small --device auto --language zh --output-dir "D:\transcripts\run-01"

# Online link: choose this or the local-media command
# A subtitle-first attempt may omit the model; without subtitles the CLI returns model_required before media download
# video-transcript-link "https://www.youtube.com/watch?v=VIDEO_ID" --language zh --output-dir "D:\transcripts\run-01"
```

After the command exits, check its exit code and `status` in `evidence.json` before reading `transcript.md` and Evidence within the user's authorization. `success` means the pipeline produced usable text; it does not mean accuracy has been validated. `uncertain` means a heuristic quality signal was found. Check `quality.review_scope`: when it is `segments`, prioritize the indices in `affected_segment_indices`; other segments are not automatically judged incorrect. When it is `run`, review the result as a whole. This status is not an accuracy or confidence score. `no_speech_detected` is a detection result, not proof that the media contains no speech. `failed` means processing did not complete. Do not infer transcript accuracy from a successful exit code or the presence of output files alone.

ASR inference runs on the computer executing the CLI. If an agent is to read a private transcript and process it with a cloud model, it must explain that the text will be sent to the cloud and obtain the user's explicit authorization first. A request to create an analysis document does not by itself authorize uploading the text. Without authorization, do not read or send it; offer a local model or ask for consent. For local-only handling, run the CLI and review its outputs on the local computer.

## Workflow

![Workflow: subtitles first; local ASR on CPU or NVIDIA CUDA when subtitles are unavailable and platform access permits; Codex can optionally write a separate analysis document](docs/assets/xhs-01-workflow.png)

1. Provide a YouTube, Bilibili, Douyin, or Xiaohongshu video link, or select a local media file.
2. For online links, look for a matching manual subtitle track, then an automatic track.
3. If no usable subtitles are available, retrieve audio or an audio-containing MP4 only when normal platform access permits, then run faster-whisper locally; `auto` selects CUDA when CTranslate2 can use it and otherwise uses CPU/int8.
4. The CLI writes the timestamped transcript to `transcript.md` and raw segments, source, and run settings to `evidence.json`.
5. When the user asks for analysis, Codex can use the transcript to create a separate `analysis.md` without changing the transcript.

Original subtitle and ASR segment text is preserved. Formatting does not overwrite raw segments. The tool does not call a cloud transcription service, read browser cookies, or bypass login, human-verification, or regional restrictions.

## Output preview

![Illustrative outputs: transcript.md and evidence.json from the CLI, plus an optional separate analysis.md from Codex; all content is fictional](docs/assets/xhs-02-output-preview.png)

The transcript, JSON, and analysis text shown above are **fictional format examples**. `analysis.md` is not produced by the CLI; Codex creates it only when requested. The examples do not indicate transcription accuracy.

## Online platform status

![Platform verification scope: multiple public-video checks, successful paths, and access limits; automated regression tests are counted separately](docs/assets/xhs-03-platform-status.png)

| Platform and path | Status | Verification |
|---|---|---|
| YouTube manual subtitles | PASS | Real retrieval; a fallback subtitle format was verified after a specific format failed. |
| YouTube automatic subtitles | PASS | Real automatic captions were retrieved and remain marked as automatic in evidence. |
| YouTube no subtitles → local ASR | PASS (sample-scoped; media requests can fail intermittently) | Stable and nightly builds have both completed real no-subtitle audio downloads and CPU ASR. After an audio-only HTTP 403, the program now makes one bounded attempt to use a platform-provided HTTPS progressive MP4 if available. The MP4 → decode → ASR path was exercised with an injected first-stream 403 on nightly. Stable yt-dlp did not expose a muxed format for the same sample, so this fallback is conditional. The sample had no useful dialogue and is not an accuracy result. |
| Bilibili audio fallback → local ASR | PASS | Real audio-only download and local CPU transcription. |
| Bilibili subtitle retrieval | Not tested | No real subtitle success sample is available. |
| Xiaohongshu HTTPS MP4 → local ASR | PASS (one sample) | One public video completed real retrieval and local transcription; this is not a guarantee for other videos. |
| Xiaohongshu subtitle retrieval | Not tested | No real subtitle-track parsing success has been verified. |
| Douyin online transcription | Restricted | Both yt-dlp stable and official nightly returned a fresh-cookie access challenge before metadata for a public sample. No media download or credential workaround was attempted. |

### Sample coverage

- YouTube: earlier public samples verified manual subtitles on two videos and automatic subtitles on one. On 2026-10-20, stable and nightly both completed no-subtitle audio → CPU ASR; two other stable media requests received HTTP 403. The new HTTPS progressive MP4 fallback completed a real download, decode, and ASR run on nightly after the primary 403 was injected locally. The stable extractor did not expose a muxed MP4 for that same video. That animation sample contained no useful dialogue and produced repetitive ASR output, so these runs prove workflow execution, not recognition quality.
- Bilibili: R0 checked six public videos, and R1 checked three additional public pages for metadata and subtitle tracks. One no-subtitle audio sample completed local ASR; no real subtitle-success sample is available.
- Xiaohongshu: multiple URL, network, and HTTPS-media diagnostics were run; one public video completed the MP4-to-local-ASR path.
- Douyin: one public sample was checked without credentials using yt-dlp 2026.8.19 and official nightly 2026.10.10.233141. Both were blocked by a fresh-cookie challenge before metadata; this does not prove that login is required, and online transcription did not complete.
- The v0.3.2 RC passed 162 automated regression tests; the v0.3.0 acceptance historically passed 86. Tests include offline and synthetic cases and do not represent real-video samples.

See the [online support matrix](docs/online-support-matrix.md) for sample coverage and user-visible limitations. Public-video checks, successful paths, and automated regression tests are reported separately. In an initial 30-utterance FLEURS Mandarin read-speech sample, weighted CER was 16.78% for small and 7.89% for large-v3. This describes that read-speech set and the local CUDA/float16 setup only; it does not predict results on interviews, multi-speaker discussions, or all Chinese videos. See the [accuracy evaluation guide](docs/accuracy-evaluation.md) for methodology, limitations, and sources.

## Commands

The package provides three entry points:

- `video-transcript-link` — online links, subtitles first, with local CPU or CUDA ASR fallback when allowed.
- `video-transcript-online` — retrieve online subtitles only; no media download or ASR.
- `video-transcript` — transcribe local WAV, MP3, M4A, MP4, WebM, Ogg, or Opus files.
- v0.3.2 also provides `video-transcript doctor [--json]` and `video-transcript models recommend` for environment diagnosis and model guidance.

## Install on Windows (no Git required)

### v0.3.2 Beta (after GitHub Release publication)

Git, GitHub CLI, Codex, and WorkBuddy are not required. The versioned URLs below become active after the v0.3.2 Release is published with its wheel and `SHA256SUMS.txt` asset. Python 3.11 is the validated installation environment. The project itself is not published on PyPI.

~~~powershell
$installRoot = Join-Path $env:USERPROFILE "video-transcript-v0.3.2"
New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
Set-Location $installRoot

$wheelName = "video_transcript_workbench-0.3.2-py3-none-any.whl"
$release = "https://github.com/CHAOSEA/video-transcript/releases/download/v0.3.2"
Invoke-WebRequest "$release/$wheelName" -OutFile $wheelName
Invoke-WebRequest "$release/SHA256SUMS.txt" -OutFile "SHA256SUMS.txt"
$sumLine = Get-Content .\SHA256SUMS.txt | Where-Object { $_ -match [regex]::Escape($wheelName) } | Select-Object -First 1
if (-not $sumLine) { throw "Release checksum for $wheelName was not found." }
$expected = ($sumLine.Trim() -split "\s+")[0]
$actual = (Get-FileHash -Algorithm SHA256 $wheelName).Hash
if ($actual -ne $expected) { throw "Wheel SHA256 does not match the published checksum." }

py -3.11 -m venv .venv
$venvRoot = (Resolve-Path .\.venv).Path
$python = Join-Path $venvRoot "Scripts\python.exe"
& $python -m pip install ".\$wheelName"
& $python -m pip check
$venvScripts = Join-Path $venvRoot "Scripts"
$env:PATH = "$venvScripts;$env:PATH"
$cli = Join-Path $venvScripts "video-transcript.exe"
$linkCli = Join-Path $venvScripts "video-transcript-link.exe"
$onlineCli = Join-Path $venvScripts "video-transcript-online.exe"
& $cli --help
& $linkCli --help
& $onlineCli --help
& $cli doctor
~~~

After installation, activate `.venv`, or add `.venv\Scripts` to the current PowerShell process's PATH as shown above, then run `video-transcript doctor`. v0.3.0 is retained as a historical Pre-release and remains listed on [GitHub Releases](https://github.com/CHAOSEA/video-transcript/releases); it does not include v0.3.2's onboarding, model-management, and CUDA features.

### Prepare a model, then transcribe

Subtitle retrieval does not need a Whisper model. For local ASR, inspect available candidates and cache state first. Model preparation downloads files; run it only after choosing a model and deciding to download it:

~~~powershell
$venvScripts = (Resolve-Path .\.venv\Scripts).Path
$env:PATH = "$venvScripts;$env:PATH"
$cli = Join-Path $venvScripts "video-transcript.exe"
& $cli doctor
& $cli models list
& $cli models recommend --intent quality --device auto
# Run these only after selecting a model and deciding to download it:
& $cli models prepare small
& $cli models verify small

# Local media: auto tries CUDA and may fall back to CPU/int8 with the same model
& $cli "D:\media\sample.wav" --model small --device auto --language zh --output-dir "D:\transcripts\run-01"

# Online link: omit --model for subtitle retrieval; choose a model and retry if ASR is needed
& (Join-Path $venvScripts "video-transcript-link.exe") "https://www.youtube.com/watch?v=VIDEO_ID" --language zh --output-dir "D:\transcripts\run-02"
~~~

Model weights stay in the local cache, are not included in the project wheel, and retain their own licenses and terms. The tool does not download a model automatically, silently switch models, or choose a larger model because a GPU is detected. `--device auto` selects only the compute device; if CUDA initialization or inference fails, the same model is retried on CPU/int8. Use `--device cuda` to require CUDA; explicit CUDA failures do not fall back.

Some online JavaScript challenge paths need Node.js. Use a supported Node 24 LTS release; Node 25 has reached end of life. This project does not install Node.js. A missing or unsupported runtime only affects some online extraction paths, not local transcription.

### v0.3.2 Beta features and model guidance

v0.3.2 provides offline `video-transcript doctor`, `video-transcript doctor --json`, `video-transcript models recommend --intent {resources,speed,quality,cached}`, and `--device auto|cpu|cuda`. These commands are not in the historical v0.3.0 wheel.

`doctor --json` is machine-readable for Codex, WorkBuddy, or another agent authorized to use the terminal. It reports capabilities separately and omits model absolute paths, tokens, and environment-variable values. It probes CTranslate2 CUDA availability but does not load a model; CPU int8 readiness is sufficient for a local-ASR `ready` status without an NVIDIA GPU. Node 25 is reported as end-of-life with a recommendation to use Node 24 LTS; Node/EJS status affects only some online JavaScript challenge paths.

The model guide provides candidates and evidence. In a FLEURS set of 30 Mandarin read-speech utterances, large-v3 had 7.89% CER versus 16.78% for small, and took about 3.26 times longer in the same CUDA/float16 comparison. This only describes that dataset and does not guarantee better results on every video. In one Q0 CPU sample, base was faster and had lower process peak RSS; that is not a general speed or memory guarantee. `recommend` does not download, set a global default, or change the chosen model when a GPU is detected.

Windows CUDA inference requires a compatible NVIDIA driver, CUDA 12 cuBLAS, and cuDNN 9. Follow the [official faster-whisper GPU instructions](https://github.com/SYSTRAN/faster-whisper#gpu); do not download DLLs from untrusted sites. `models device` and Doctor show CTranslate2 probe results, not a tested model load or inference. At runtime `--device auto` tries CUDA and falls back to the same model on CPU/int8 if needed.

## Use an online link (v0.3.2)

### Subtitle-first with local ASR fallback

~~~powershell
video-transcript-link "https://www.youtube.com/watch?v=VIDEO_ID" --language zh --model small --device auto --output-dir "D:\transcripts\video-run-01"
~~~

In v0.3.2, omit `--model` to try subtitles first; a successful subtitle path does not need a Whisper model. If usable subtitles are unavailable, the command returns `model_required` before downloading media. Choose a model, prepare it if needed, then retry with explicit `--model`. The historical v0.3.0 requires `--model`.

The command recognizes YouTube, Bilibili, Douyin, and Xiaohongshu video-note URLs. It prefers manual subtitles, then automatic subtitles. Xiaohongshu support is for video notes only, not pure image/text notes.

URL recognition does not mean every link can be processed. Access permissions, subtitle tracks, region, network, media format, and local resources all affect results. Check the sample-scoped support matrix above.

If an online request is blocked but you already have media saved through a platform-permitted path, continue with the local `video-transcript` command below. The online command does not read browser sessions or bypass access checks.

If your system proxy environment variables point to an unavailable port, pass `--proxy` to override the route for this command only. This does not change Windows proxy settings or environment variables:

~~~powershell
video-transcript-link "https://www.youtube.com/watch?v=VIDEO_ID" --language zh --model small --output-dir "D:\transcripts\video-run-01" --proxy "http://127.0.0.1:1080"
~~~

Replace the example port with your local HTTP(S) proxy port. The proxy option is not persisted and does not accept embedded usernames or passwords. When omitted, the command follows the process proxy environment as usual.

### Subtitles only

~~~powershell
video-transcript-online "https://www.youtube.com/watch?v=VIDEO_ID" --language en --output-dir "D:\transcripts\subtitle-run-01"
~~~

This command fetches platform metadata and matching subtitles only. It does not download media or run speech recognition. Supported language preferences are `zh`, `en`, and `ja`.
If your inherited proxy is unavailable, add `--proxy "http://127.0.0.1:1080"` to use a proxy for this command only.

## Use local media (v0.3.2)

~~~powershell
video-transcript "D:\media\sample.mp4" --model small --device auto --language zh --output-dir "D:\transcripts\local-run-01"
~~~

Media is decoded and transcribed locally. `--device auto` (the default) queries CTranslate2 for CUDA support, tries NVIDIA GPU inference when available, and retries the same model on CPU/int8 if CUDA initialization or inference fails. Use `--device cpu` to force CPU or `--device cuda` to require CUDA and fail clearly if unavailable. Device selection never changes the chosen model. For video input, the first audio track is used and the source file is not modified. Local input is limited to 30 minutes by default; use `--max-duration-seconds` to change the limit, up to four hours. The selected model must already be available locally; missing files cause a clear error, with no silent download or downgrade.

Add `--vad-filter` to enable faster-whisper's built-in Silero VAD. It can reduce repetitive hallucinations on pure silence, but may miss quiet speech or speech masked by noise, so it remains opt-in. Evidence distinguishes detected speech, no speech detected, and uncertain results. Suspicious raw segments are kept for review rather than silently removed.

Run `video-transcript models device` to check the current CTranslate2 CUDA capability. On Windows, faster-whisper currently requires a compatible NVIDIA driver plus CUDA 12 cuBLAS and cuDNN 9; see the [upstream GPU instructions](https://github.com/SYSTRAN/faster-whisper#gpu). A visible GPU alone does not prove that inference can load.

In one same-audio run on the development RTX 3060, a 209.5-second sample took 49.1 seconds with `small` CPU/int8 and 9.3 seconds with `small` CUDA/float16; `large-v3` CUDA/float16 took 27.6 seconds. This is a single-machine, single-sample timing observation. There is no human reference, so it does not compare accuracy or predict speed on other GPUs.

The model is still explicitly selected. In the initial FLEURS read-speech comparison, large-v3 had a lower CER but took about 3.3 times longer. That is not enough to choose a universal default for interviews, mixed audio, or CPU users. The project does not offer size-based presets labeled “balanced” or “high accuracy.” Review proper names, numbers, and negations in important transcripts.

## Results and privacy

Successful runs create:

- `transcript.md` — original segment text with timestamps.
- `evidence.json` — video identifier or local input SHA256, subtitle/ASR path, raw segments, model/device settings, dependency versions, and run status.

Online evidence omits the original query string, signed subtitle URL, and signed media URL. A required share parameter explicitly present in a Xiaohongshu URL is used only during the request and is not written to evidence or safe errors. Result files may contain video titles and full transcripts; treat output folders as private.

The tool does not use cloud ASR, automatically read browser profiles or cookies, or bypass access checks. Online downloads are limited by duration and byte size. Temporary media is removed when processing ends. Longer local audio can still use substantial memory.

## Known limitations

- Platform access checks, network failures, and proxy failures are reported as separate failures, not as “no subtitles.”
- The explicitly selected model is never silently downgraded; missing model files produce an error.
- The FLEURS CER comparison uses dataset-provided Mandarin read-speech references that the maintainer did not individually audit. There is no CER benchmark on natural video dialogue with manually checked references, so it cannot establish a universal default. large-v3 scored better on the FLEURS sample only; this does not guarantee better results on every video.
- A successful sample does not guarantee another video, language, region, or network will work.
- Use only media you are authorized to access and process, and follow platform terms and content rights.

See the [accuracy evaluation guide](docs/accuracy-evaluation.md) for the initial FLEURS results, methodology, and limitations. Keep private audio, references, transcripts, and evidence files outside the repository.

## Development and tests

The v0.3.2 RC passed **162 automated tests** on Windows Python 3.11; the v0.3.0 release acceptance historically passed 86. Tests include synthetic media and subtitle fixtures; they do not replace real platform compatibility checks.

~~~powershell
python -m unittest discover -s tests -v
~~~

See [dependency and rights record](DEPENDENCIES.md) for the Node.js 24 LTS / yt-dlp-ejs check, clean dependency installation, and package validation.

## License

Project source is licensed under MIT. Copyright (c) 2026 CHAOS. See [LICENSE](LICENSE) for the complete license. Dependencies, model files, and processed video/audio/subtitles retain their own licenses and platform terms.
