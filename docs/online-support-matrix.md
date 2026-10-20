# Online support matrix

Updated: 2026-10-20. This is a public, sample-scoped compatibility summary. PASS means that the named type of path worked on at least one tested public video; it is not a guarantee for all videos, regions, or networks.

| Platform/path | Status | Evidence summary |
|---|---|---|
| YouTube manual subtitles | PASS | Real sample; Apollo 11 JSON3 failure fell back to a manual VTT track and produced 335 segments. |
| YouTube automatic subtitles | PASS | A real automatic caption track was retrieved and preserved as automatic in evidence. |
| YouTube audio fallback when subtitles are unavailable | PASS (one sample; recheck was blocked) | A public video with no available subtitles previously completed audio-only download and local small/CPU/int8 ASR using yt-dlp 2026.8.19. A 2026-10-20 recheck of the same sample received HTTP 403 before metadata, so ASR did not start. A different media-transfer diagnostic also remains blocked. |
| Bilibili audio fallback when subtitles are unavailable | PASS | Real audio-only stream was downloaded and transcribed locally on CPU. |
| Bilibili manual/automatic subtitle success | NOT TESTED | Tested anonymous public pages did not expose a usable subtitle track; no successful real subtitle sample is claimed. |
| Douyin manual/automatic subtitle retrieval | BLOCKED | Rechecked 2026-10-20 with yt-dlp 2026.8.19 through the available process-level proxy and without credentials. The extractor returned an access/auth-restricted error before metadata or tracks. Its “fresh cookies” wording does not establish that login is the cause. |
| Douyin audio fallback | BLOCKED | Stopped before subtitle selection and audio transfer because metadata retrieval was blocked. No cookies, signed request generation, or browser-session reuse was attempted. |
| Xiaohongshu manual/automatic subtitle success | NOT TESTED | The tested share page returned metadata but no subtitle tracks; no subtitle parser success is claimed. |
| Xiaohongshu audio fallback when subtitles are unavailable | PASS (one sample) | Page-provided HTTPS `og:video` MP4 returned 206 `video/mp4` for a 1 KiB range probe; bounded 85,787,212-byte MP4 download then passed PyAV audio/video validation and small/CPU/int8 ASR, producing 408 segments. |
| Local media CPU transcription | PASS | Windows Python 3.11.9, cached small model, CPU/int8 real run. |

## Real-sample coverage

The following counts summarize the R0/R1 and release checks. A page check, a successful subtitle path, and an end-to-end ASR run are different measurements; they are not interchangeable.

| Platform | Public sample checks | Verified result |
|---|---|---|
| YouTube | Five distinct public videos were used for subtitle, parser, or media-transfer diagnostics; one previously successful no-subtitle sample was rechecked. | Manual subtitle retrieval succeeded on two videos and automatic subtitle retrieval on one. One no-subtitle video completed audio download and local ASR with yt-dlp 2026.8.19, but a 2026-10-20 recheck of that same sample received HTTP 403 before metadata. A different media-transfer diagnostic also stopped at an access check. The recheck is not a new unique sample. |
| Bilibili | R0 checked six public videos; R1 checked three additional public pages for metadata and subtitle tracks. | One no-subtitle audio sample completed local ASR. No usable subtitle track was found in these checks, so subtitle-success remains unverified. |
| Xiaohongshu | Multiple URL, network, page, and HTTPS-media diagnostics were performed. | One public video completed HTTPS MP4 retrieval, audio decoding, and local ASR. |
| Douyin | A public sample was checked and rechecked through the available anonymous access path using yt-dlp 2026.8.19. | An access/auth-restricted error stopped metadata retrieval; the extractor's fresh-cookie wording does not establish that login is the cause. No online end-to-end ASR passed. |

The current regression suite contains 104 automated tests, including synthetic and offline cases; the v0.3.0 release acceptance historically ran 86. These tests are reported separately and do not represent real-video sample counts. The real-platform checks above are targeted compatibility checks, not a random or representative market sample. ASR has no CER benchmark against human-verified references.

## User-visible limitations

- Subtitle availability depends on each video's subtitle tracks and the requested language.
- YouTube no-subtitle fallback completed on one public sample with yt-dlp 2026.8.19, but a later recheck of the same sample received HTTP 403 before metadata; another media-transfer diagnostic is also blocked by an access check. This demonstrates that success is not repeatable in every request context. The program does not use cookies or bypass login, human verification, permission, or regional restrictions.
- Bilibili subtitle success has not yet been verified on a real public sample.
- The tested Douyin sample remained access-restricted before metadata when rechecked on 2026-10-20 with yt-dlp 2026.8.19. The extractor's fresh-cookie wording does not prove that login is the cause, and this tool will not obtain or use credentials. One Xiaohongshu sample succeeded through a page-provided HTTPS MP4; other videos may expose only HTTP media, no media, or require platform access checks.
- Xiaohongshu manual/automatic subtitle parsing remains untested on a successful track. The MP4 ASR result is limited to one public sample and is not a universal compatibility or accuracy guarantee.
- The ASR comparison has no human-reference CER results. No model accuracy ranking or universal accuracy claim is made.
- A successful public sample does not guarantee another video's availability or transcription quality.

The evidence files distinguish manual from automatic captions and keep raw source segments for traceability. They do not store original query-bearing URLs or signed subtitle/media URLs.

## Upstream diagnostics checked on 2026-10-20

- The [latest yt-dlp stable release](https://github.com/yt-dlp/yt-dlp/releases/latest) was 2026.08.19. Upstream reports include intermittent YouTube HTTP 403 cases even on recent builds; a 403 is not enough evidence to identify a project-side extractor bug or a login requirement ([issue #17647](https://github.com/yt-dlp/yt-dlp/issues/17647)).
- The proposed [Douyin 403 fix, PR #17826](https://github.com/yt-dlp/yt-dlp/pull/17826), was still open when checked and describes generating request signature and cookie state. This project does not implement unmerged signature-generation work or bypass platform access checks; it keeps the local-media transcription path available instead.
