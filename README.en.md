# Video Transcript

**Video Transcript · Online CLI Beta v0.3.1**<br>
[简体中文](README.md) · [Online platform verification matrix](docs/online-support-matrix.md)

Turn supported video links or local media into timestamped transcripts. When usable subtitles are unavailable, the tool can recognize speech locally if the platform permits media retrieval. This gives some videos without captions a local ASR path, subject to platform access.

The CLI writes `transcript.md` and `evidence.json` with raw segments, source, and run settings. If you request analysis in Codex, the project Skill can have the agent save a separate `analysis.md`. This is optional and never overwrites the transcript; the CLI does not generate it.

> The Beta has gone through multiple real-platform sample checks and automated regression runs. Public claims are scoped by platform and processing path; they do not guarantee success for every video.

## What is this, and how do I use it?

The core product is a standalone Python command-line tool (CLI). This repository also includes a Codex project Skill that helps an agent invoke the CLI and check the outputs. Subtitle retrieval, media processing, and ASR are always performed by the CLI.

### Recommended: Codex + the project Skill

1. Open the repository root in Codex.
2. Install the CLI using the instructions below and make a local Whisper model available.
3. Ask for a transcript directly, or invoke the shorter Skill name `$video-transcript`. For example: “Use `$video-transcript` to transcribe this link in Chinese and save it under D:\transcripts\run-01.”

The Skill is at [.codex/skills/video-transcript/SKILL.md](.codex/skills/video-transcript/SKILL.md). It selects the online-link, subtitles-only, or local-media entry point and checks the CLI output. If you request analysis, Codex reads the transcript and writes a separate `analysis.md`; the transcript may be processed by the active Codex model. The analysis file stays separate from the source transcript.

### Use the CLI on its own

Codex, WorkBuddy, and cloud AI services are optional. After installing the project and activating .venv, run this in PowerShell:

~~~powershell
video-transcript-link "https://www.youtube.com/watch?v=VIDEO_ID" --language zh --model small --output-dir "D:\transcripts\run-01"
~~~

Use video-transcript for local media and video-transcript-online for subtitles only.

### WorkBuddy and other agents

Other agents can call the CLI if they can access a local terminal and files, but this repository does not include a dedicated WorkBuddy Skill or plugin. A cloud agent or a different machine cannot directly use media, Python environments, or model caches on this computer.

ASR runs locally. If you ask a cloud agent to read or summarize the transcript, the text may be processed by that agent's model service. For transcription only, you can run the CLI directly and keep the output local.
## Workflow

![Workflow: subtitles first; local CPU ASR when subtitles are unavailable and platform access permits; Codex can optionally write a separate analysis document](docs/assets/xhs-01-workflow.png)

1. Provide a YouTube, Bilibili, Douyin, or Xiaohongshu video link, or select a local media file.
2. For online links, look for a matching manual subtitle track, then an automatic track.
3. If no usable subtitles are available, retrieve audio or an audio-containing MP4 only when normal platform access permits, then run faster-whisper locally on CPU/int8.
4. The CLI writes the timestamped transcript to `transcript.md` and raw segments, source, and run settings to `evidence.json`.
5. When the user asks for analysis, Codex can use the transcript to create a separate `analysis.md` without changing the transcript.

Original subtitle and ASR segment text is preserved. Formatting does not overwrite raw segments. The tool does not call a cloud transcription service, read browser cookies, or bypass login, human-verification, or regional restrictions.

## Output preview

![Illustrative outputs: transcript.md and evidence.json from the CLI, plus an optional separate analysis.md from Codex; all content is fictional](docs/assets/xhs-02-output-preview.png)

The transcript, JSON, and analysis text shown above are **fictional format examples**. `analysis.md` is not produced by the CLI; Codex creates it only when requested. The examples do not indicate transcription accuracy.

## Online platform status

![Platform verification scope: multiple public-video checks, successful paths, and access limits; 98 automated regression tests are counted separately](docs/assets/xhs-03-platform-status.png)

| Platform and path | Status | Verification |
|---|---|---|
| YouTube manual subtitles | PASS | Real retrieval; a fallback subtitle format was verified after a specific format failed. |
| YouTube automatic subtitles | PASS | Real automatic captions were retrieved and remain marked as automatic in evidence. |
| YouTube no-subtitle audio → local ASR | PASS (one sample; recheck was blocked) | One video without available subtitles previously completed audio download and CPU/int8 transcription. A 2026-10-20 recheck of the same sample received HTTP 403 before metadata, so ASR did not start. |
| Bilibili audio fallback → local ASR | PASS | Real audio-only download and local CPU transcription. |
| Bilibili subtitle retrieval | Not tested | No real subtitle success sample is available. |
| Xiaohongshu HTTPS MP4 → local ASR | PASS (one sample) | One public video completed real retrieval and local transcription; this is not a guarantee for other videos. |
| Xiaohongshu subtitle retrieval | Not tested | No real subtitle-track parsing success has been verified. |
| Douyin online transcription | Restricted | The current extractor remains access-restricted before metadata. Its “fresh cookies” message does not establish that login is the cause; the tool does not obtain or use credentials. |

### Sample coverage

- YouTube: five distinct public videos were used for subtitle, parser, or media-transfer diagnostics. Manual subtitle retrieval succeeded on two videos and automatic subtitle retrieval on one. One no-subtitle video previously completed audio download and local ASR with yt-dlp 2026.8.19, but a 2026-10-20 recheck of the same video received HTTP 403 before metadata. A different transfer diagnostic also stopped at a platform access check; the recheck is not a new unique sample.
- Bilibili: R0 checked six public videos, and R1 checked three additional public pages for metadata and subtitle tracks. One no-subtitle audio sample completed local ASR; no real subtitle-success sample is available.
- Xiaohongshu: multiple URL, network, and HTTPS-media diagnostics were run; one public video completed the MP4-to-local-ASR path.
- Douyin: a public sample was rechecked without credentials using yt-dlp 2026.8.19 and remained access-restricted before metadata; online transcription did not complete.
- The current 98 automated regression tests are counted separately. They include offline and synthetic cases and do not represent 98 real videos. The 86-test v0.3.0 acceptance is historical.

See the [online support matrix](docs/online-support-matrix.md) for sample coverage and user-visible limitations. Public-video checks, successful paths, and automated regression tests are reported separately. **There is no CER benchmark against human-verified references, so this project makes no model-ranking or universal accuracy claim.**

## Commands

The package provides three entry points:

- `video-transcript-link` — online links, subtitles first, with local CPU ASR fallback when allowed.
- `video-transcript-online` — retrieve online subtitles only; no media download or ASR.
- `video-transcript` — transcribe local WAV, MP3, M4A, MP4, WebM, Ogg, or Opus files.

## Install on Windows

The validated environment is Windows 10/11 with Python 3.11. Python 3.10+ is declared, but other versions and operating systems have not been verified.

~~~powershell
git clone https://github.com/CHAOSEA/video-transcript.git
cd video-transcript
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\Activate.ps1
~~~

Node.js is needed by yt-dlp for YouTube JavaScript challenge support. Node 24.22.0 LTS with yt-dlp-ejs 0.8.0 passed isolated checks; EJS requires Node.js 22 or newer. This project does not install Node.js.

If PowerShell blocks the activation script, call .\.venv\Scripts\video-transcript-link.exe and the corresponding entry points directly.

Whisper weights must be obtained separately. This project does not silently download model files or switch models. The tested model was `Systran/faster-whisper-small`. If it is not cached locally, you can explicitly download it with a Hugging Face Hub tool, for example:

~~~powershell
hf download Systran/faster-whisper-small
~~~

Model files are not included in the Python package and have their own license and terms.

## Use an online link

### Subtitle-first with local ASR fallback

~~~powershell
video-transcript-link "https://www.youtube.com/watch?v=VIDEO_ID" --language zh --model small --output-dir "D:\transcripts\video-run-01"
~~~

The command recognizes YouTube, Bilibili, Douyin, and Xiaohongshu video-note URLs. It prefers manual subtitles, then automatic subtitles. Xiaohongshu support is for video notes only, not pure image/text notes.

URL recognition does not mean every link can be processed. Access permissions, subtitle tracks, region, network, media format, and local resources all affect results. Check the sample-scoped support matrix above.

If an online request is blocked but you already have media saved through a platform-permitted path, continue with the local `video-transcript` command below. The online command does not read browser sessions or bypass access checks.

### Subtitles only

~~~powershell
video-transcript-online "https://www.youtube.com/watch?v=VIDEO_ID" --language en --output-dir "D:\transcripts\subtitle-run-01"
~~~

This command fetches platform metadata and matching subtitles only. It does not download media or run speech recognition. Supported language preferences are `zh`, `en`, and `ja`.

## Use local media

~~~powershell
video-transcript "D:\media\sample.mp4" --model small --language zh --output-dir "D:\transcripts\local-run-01"
~~~

Media is decoded and transcribed locally. For video input, the first audio track is used. The source file is not modified. To avoid accidental processing of very long media, local input is limited to 30 minutes by default. Use `--max-duration-seconds` to change the limit, up to four hours. The selected model must already be available locally; missing files cause a clear error, with no silent download or downgrade.

## Results and privacy

Successful runs create:

- `transcript.md` — original segment text with timestamps.
- `evidence.json` — video identifier or local input SHA256, subtitle/ASR path, raw segments, model/device settings, dependency versions, and run status.

Online evidence omits the original query string, signed subtitle URL, and signed media URL. A required share parameter explicitly present in a Xiaohongshu URL is used only during the request and is not written to evidence or safe errors. Result files may contain video titles and full transcripts; treat output folders as private.

The tool does not use cloud ASR, automatically read browser profiles or cookies, or bypass access checks. Online downloads are limited by duration and byte size. Temporary media is removed when processing ends. Longer local audio can still use substantial memory.

## Known limitations

- Platform access checks, network failures, and proxy failures are reported as separate failures, not as “no subtitles.”
- The explicitly selected model is never silently downgraded; missing model files produce an error.
- There is no CER benchmark against human-verified references. A larger model has not been proven more accurate by this project.
- A successful sample does not guarantee another video, language, region, or network will work.
- Use only media you are authorized to access and process, and follow platform terms and content rights.

## Development and tests

The v0.3.0 release acceptance passed **86 automated tests** on Windows Python 3.11. Tests include synthetic media and subtitle fixtures; they do not replace real platform compatibility checks.

~~~powershell
python -m unittest discover -s tests -v
~~~

See [dependency and rights record](DEPENDENCIES.md) for the Node.js 24 LTS / yt-dlp-ejs check, clean dependency installation, and package validation.

## License

Project source is licensed under MIT. Copyright (c) 2026 CHAOS. See [LICENSE](LICENSE) for the complete license. Dependencies, model files, and processed video/audio/subtitles retain their own licenses and platform terms.
