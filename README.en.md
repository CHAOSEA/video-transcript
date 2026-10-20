# Video Transcript Workbench

**Online CLI Beta v0.3.0**<br>
[简体中文](README.md) · [Online platform verification matrix](docs/online-support-matrix.md) · [Xiaohongshu post assets](docs/xiaohongshu/图文发布素材.md)

Video Transcript Workbench is a command-line tool for turning online video links and local media into traceable transcripts. It prefers available subtitles, then uses local CPU speech recognition when subtitles are unavailable and platform access allows media retrieval. Outputs include timestamped Markdown and `evidence.json`.

> This is a sample-verified Beta. It does not guarantee that every video or network environment will work. Claims are limited to the specific paths marked PASS in the support matrix.

## Workflow

![Workflow: try manual and automatic subtitles first, then local CPU ASR when platform access permits, and save transcript and evidence files](docs/assets/xhs-01-workflow.png)

1. Provide a YouTube, Bilibili, Douyin, or Xiaohongshu video link, or select a local media file.
2. For online links, look for a matching manual subtitle track, then an automatic track.
3. If no usable subtitles are available, retrieve audio or an audio-containing MP4 only when normal platform access permits, then run faster-whisper locally on CPU/int8.
4. Write the timestamped transcript to `transcript.md` and raw segments, source, and run settings to `evidence.json`.

Original subtitle and ASR segment text is preserved. Formatting does not overwrite raw segments. The tool does not call a cloud transcription service, read browser cookies, or bypass login, human-verification, or regional restrictions.

## Output preview

![Illustrative Markdown transcript and evidence JSON layout with fictional content](docs/assets/xhs-02-output-preview.png)

The title, transcript, and JSON shown above are **fictional format examples**. They do not come from a user's video and do not indicate transcription accuracy.

## Online platform status

![Sample-scoped platform status: YouTube subtitle retrieval, Bilibili audio ASR, and one Xiaohongshu MP4 ASR sample passed; Douyin access is blocked](docs/assets/xhs-03-platform-status.png)

| Platform and path | Status | Verification |
|---|---|---|
| YouTube manual subtitles | PASS | Real retrieval; a fallback subtitle format was verified after a specific format failed. |
| YouTube automatic subtitles | PASS | Real automatic captions were retrieved and remain marked as automatic in evidence. |
| YouTube no-subtitle audio fallback | Restricted | The tested environment hit a platform access check; the tool does not bypass it. |
| Bilibili audio fallback → local ASR | PASS | Real audio-only download and local CPU transcription. |
| Bilibili subtitle retrieval | Not tested | No real subtitle success sample is available. |
| Xiaohongshu HTTPS MP4 → local ASR | PASS (one sample) | One public video completed real retrieval and local transcription; this is not a guarantee for other videos. |
| Xiaohongshu subtitle retrieval | Not tested | No real subtitle-track parsing success has been verified. |
| Douyin online transcription | Restricted | The real sample hit a fresh-cookie challenge; no online end-to-end transcription passed, and the tool does not read or obtain credentials. |

See the [online support matrix](docs/online-support-matrix.md) for sample details and user-visible limitations. **There is no CER benchmark against human-verified references, so this project makes no model-ranking or universal accuracy claim.**

## Commands

The package provides three entry points:

- `video-transcript-link` — online links, subtitles first, with local CPU ASR fallback when allowed.
- `video-transcript-online` — retrieve online subtitles only; no media download or ASR.
- `video-transcript` — transcribe local WAV, MP3, M4A, MP4, WebM, Ogg, or Opus files.

## Install on Windows

The validated environment is Windows 10/11 with Python 3.11. Python 3.10+ is declared, but other versions and operating systems have not been verified.

~~~powershell
git clone https://github.com/CHAOSEA/video-transcript-workbench.git
cd video-transcript-workbench
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m pip check
~~~

Node.js is needed by yt-dlp for YouTube JavaScript challenge support. Node 24.22.0 LTS with yt-dlp-ejs 0.8.0 passed isolated checks; EJS requires Node.js 22 or newer. This project does not install Node.js.

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

### Subtitles only

~~~powershell
video-transcript-online "https://www.youtube.com/watch?v=VIDEO_ID" --language en --output-dir "D:\transcripts\subtitle-run-01"
~~~

This command fetches platform metadata and matching subtitles only. It does not download media or run speech recognition. Supported language preferences are `zh`, `en`, and `ja`.

## Use local media

~~~powershell
video-transcript "D:\media\sample.mp4" --model small --language zh --output-dir "D:\transcripts\local-run-01"
~~~

Media is decoded and transcribed locally. For video input, the first audio track is used. The source file is not modified. The selected model must already be available locally; missing files cause a clear error, with no silent download or downgrade.

## Results and privacy

Successful runs create:

- `transcript.md` — original segment text with timestamps.
- `evidence.json` — video identifier or local input SHA256, subtitle/ASR path, raw segments, model/device settings, dependency versions, and run status.

Online evidence omits the original query string, signed subtitle URL, and signed media URL. A required share parameter explicitly present in a Xiaohongshu URL is used only during the request and is not written to evidence or safe errors. Result files may contain video titles and full transcripts; treat output folders as private.

The tool does not use cloud ASR, automatically read browser profiles or cookies, or bypass access checks. Online downloads are limited by duration and byte size. Temporary media is removed when processing ends. Local decoding of long media can use substantial memory.

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

## Xiaohongshu post assets

The project includes three portrait images, an editable image-generation script, and Chinese post copy for later Xiaohongshu Skill or image-text post use: [open the post materials](docs/xiaohongshu/图文发布素材.md).

The transcript preview uses fictional content. Platform claims are limited to tested samples and do not mean all four platforms are fully supported.

## License

Project source is licensed under MIT. Copyright (c) 2026 CHAOS. See [LICENSE](LICENSE) for the complete license. Dependencies, model files, and processed video/audio/subtitles retain their own licenses and platform terms.
