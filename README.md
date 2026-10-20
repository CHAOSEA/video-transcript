# Video Transcript Workbench

**Online CLI Beta candidate** — YouTube, Bilibili, Douyin, and Xiaohongshu links are intended inputs. The tool prefers available subtitles and uses local CPU speech recognition when the subtitle-first workflow can safely continue to audio fallback. Each platform has sample-scoped support status below; extractor presence alone does not guarantee access.

## What it does

Paste a supported video link. The unified `video-transcript-link` command tries a matching manual subtitle first, then an automatic subtitle. When subtitles are unavailable and platform access permits it, the tool downloads an audio-only stream and runs faster-whisper locally on CPU. It writes timestamped Markdown and `evidence.json`. It does not send audio to a cloud transcription service.

The package also provides a local media command and a subtitles-only online command:

- `video-transcript-link` — subtitle first, with local CPU ASR fallback when allowed.
- `video-transcript-online` — retrieve subtitles only; never downloads media or runs ASR.
- `video-transcript` — transcribe a local WAV, MP3, M4A, MP4, WebM, Ogg, or Opus file.

## Online support status

The matrix describes specific real test samples. PASS does not guarantee that another video, language, region, or network will work.

| Capability | Status | What has been verified |
|---|---|---|
| YouTube manual subtitles | PASS | Real subtitle retrieval, including a JSON3-to-VTT format fallback. |
| YouTube automatic subtitles | PASS | Real automatic subtitle retrieval; output remains marked automatic. |
| YouTube no-subtitle audio fallback | BLOCKED in the tested environment | Audio format selection succeeded, then platform access verification stopped transfer before audio bytes arrived. The tool does not bypass this check. |
| Bilibili no-subtitle audio fallback | PASS | Real audio-only download followed by local CPU ASR. |
| Bilibili subtitle retrieval | NOT TESTED | No suitable public subtitle track was exposed in the tested anonymous samples. |
| Douyin subtitle retrieval | BLOCKED | Rechecked 2026-10-19 through the available process-level proxy; yt-dlp again returned a fresh-cookie challenge before metadata or subtitle tracks. No credentials were read or supplied. |
| Douyin audio fallback | BLOCKED | The same access check prevents reaching subtitle selection or audio download; the tool does not bypass it. |
| Xiaohongshu manual/automatic subtitle success | NOT TESTED | The tested sample returned metadata but no subtitle tracks, so no subtitle format was parsed. |
| Xiaohongshu no-subtitle HTTPS MP4 fallback | PASS (one sample) | A page-provided HTTPS `og:video` MP4 was downloaded under the existing host/size/duration limits and transcribed locally with small/CPU/int8; 408 segments were produced. |
| Local CPU transcription | PASS | Clean Windows Python 3.11.9 install and real small-model transcription. |

See [the public support matrix](docs/online-support-matrix.md) for sample-scoped details and current limitations.

No CER benchmark has been completed against human-verified references. Model accuracy rankings are not claimed.

## Requirements

- Windows 10/11 and Python 3.11 are the validated setup. Python 3.10+ is declared, but other versions and operating systems have not been verified.
- Node.js is needed by yt-dlp for full YouTube JavaScript challenge support. Node 24.22.0 LTS and yt-dlp-ejs 0.8.0 passed the isolated R3 check, including a real public YouTube metadata request where yt-dlp invoked its Node challenge solver; yt-dlp-ejs requires Node 22 or newer. Node.js 25.9.0 was used in the initial platform validation, but that release line is now end-of-life. See [Node.js release status](https://nodejs.org/en/about/previous-releases) and the [yt-dlp EJS runtime requirements](https://github.com/yt-dlp/yt-dlp/wiki/EJS).
- Install the Whisper model separately. This project does not download model weights automatically. The tested model was `Systran/faster-whisper-small`; model files remain separate from the Python package and have their own license.

## Install on Windows

From the project directory, create an isolated environment and install the package:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m pip check
```

The installer fetches Python dependencies, including `yt-dlp[default]` and `yt-dlp-ejs`. It does not install Node.js or Whisper weights.

If the small model is not already in the Hugging Face cache, you can explicitly download it with the Hugging Face Hub CLI or Python API. For example:

```powershell
hf download Systran/faster-whisper-small
```

This command downloads model files to the Hugging Face cache. To keep model storage at a chosen location, pass an existing local model directory to `--model`. Model downloads are an explicit user action; the transcription commands run with local model loading and do not silently fetch a missing model or switch to another model.

## Use an online video link

### Subtitle-first workflow with local ASR fallback

```powershell
video-transcript-link "https://www.youtube.com/watch?v=VIDEO_ID" `
  --language zh `
  --model small `
  --output-dir "D:\transcripts\video-run-01"
```

The command accepts HTTPS video URLs from YouTube, Bilibili, Douyin, and Xiaohongshu video notes. It tries manual subtitles before automatic subtitles. If no usable subtitle is available, it can download an audio-only stream and run the selected local model. For Xiaohongshu only, it can fall back to an MP4 that contains both video and audio when no audio-only format is available. Both paths remain subject to platform access, network availability, duration/size limits, and local resources. Share links are resolved to a canonical video URL before a download is attempted. Pure image/text Xiaohongshu notes are not supported.

The four extractors are included in the pinned yt-dlp version. Real compatibility varies by sample and access environment; consult the support matrix for actual verification status. The program does not use account cookies or bypass platform checks.

### Subtitles only

```powershell
video-transcript-online "https://www.youtube.com/watch?v=VIDEO_ID" `
  --language en `
  --output-dir "D:\transcripts\subtitle-run-01"
```

This command fetches matching subtitles and metadata only for the supported platform URLs. It does not download media or run ASR.

Supported language preferences are `zh`, `en`, and `ja`.

## Use local media

```powershell
video-transcript "D:\media\sample.mp4" `
  --model small `
  --language zh `
  --output-dir "D:\transcripts\local-run-01"
```

The media is decoded and transcribed locally. For video inputs, the first audio track is used. The source file is not modified.

## Outputs and evidence

Successful runs create:

- `transcript.md` — original segment text with a timestamp for each segment.
- `evidence.json` — source video ID or local input SHA256, selected subtitle/ASR path, raw segments, model/device/compute settings where applicable, package versions, and run status.

Raw ASR or subtitle segment text is preserved in evidence. Markdown formatting does not rewrite the stored raw text. Online evidence stores a canonical video URL without the original query string; signed media and subtitle URLs are not stored. Titles and transcript text may appear in the result files, so treat output folders as private.

When a run fails, the tool writes classified evidence and does not create a success transcript. It will not silently downgrade the selected model.

## CPU use and limits

ASR uses `device=cpu` and `compute_type=int8`. The model is selected explicitly and is not automatically downgraded. Larger models require more memory and take longer; this project does not yet provide evidence-based Fast/Balanced/High-accuracy presets.

Online downloads are audio-only and bounded by configurable duration and byte limits. Temporary media is stored in a private temporary directory and removed after success or failure. WebM/Opus is handled according to its actual container and codec, never renamed to pretend it is M4A.

The default download limit is 30 minutes and 512 MiB. The CLI allows a maximum of 4 hours and 2 GiB. Long media can use substantial memory during local decoding; start with shorter clips on machines with limited memory.

## Failure states

- `no_available_subtitles`, `no_matching_language`, or an empty/unsupported subtitle track may lead to the configured local ASR fallback.
- `subtitle_parse_failed` and `incomplete_subtitles` stop by default. `--fallback-on-suspect-subtitles` explicitly allows ASR for these cases.
- `network_error`, `proxy_unavailable`, `access_or_auth_restricted`, and unsafe redirect errors stop the workflow. They are not treated as “no subtitles” and do not trigger downloads.
- Missing local model files, duration/size limits, unsupported media, or decoder failures are recorded as failures; the tool does not claim a successful transcript.

## Network and proxy troubleshooting

The program does not configure a proxy, change system networking, or read browser profiles or cookies. It uses the network environment available to Python and yt-dlp.

For Xiaohongshu URLs, an explicitly supplied `xsec_token` share parameter may be sent to the platform in memory when needed. Its value and other query parameters are omitted from evidence and errors.

- Confirm that the machine can reach the platform over HTTPS.
- If your organization requires a proxy, use its documented Python/command-line proxy configuration and confirm the Node.js runtime can also access required resources.
- In PowerShell, `Get-ChildItem Env:HTTP_PROXY,Env:HTTPS_PROXY` can show whether proxy variables are present. **Do not paste proxy values into a bug report**; they can contain credentials.
- Retry only after resolving a network issue. If the platform requests login, human verification, permissions, or regional access, this tool will stop and report the restriction.

A proxy or network failure is not the same as “no subtitles.” The evidence records separate statuses. Do not upload signed URLs, cookies, private transcripts, or full logs when asking for help.

## Content access and platform limits

Use the tool only for media you are authorized to access and process, and follow the applicable platform terms and copyright rules. This project does not read login credentials, browser cookies, or profile data; it does not bypass paywalls, login checks, human verification, or regional restrictions.

Platforms may require an access check before metadata, subtitles, or audio can be retrieved. The tested YouTube no-subtitle audio fallback and Douyin metadata request were blocked by platform checks in the tested environment. One Xiaohongshu video completed HTTPS MP4 download and local ASR, but this does not establish subtitle support or guarantee access to other videos. Bilibili's real subtitle success path remains unverified.

## Tests

The project uses Python's built-in `unittest` runner:

```powershell
python -m unittest discover -s tests -v
```

The current local acceptance run passes 75 tests on Windows Python 3.11.9. Automated tests include synthetic media and subtitle fixtures; they do not replace real-platform compatibility checks.

## License

This project is licensed under the MIT License. Copyright (c) 2026 CHAOS. See [LICENSE](LICENSE) and the [dependency and rights record](DEPENDENCIES.md). The project license applies to this project's source code; third-party dependencies, Whisper model files, and video/audio/subtitle content retain their own licenses and terms.
