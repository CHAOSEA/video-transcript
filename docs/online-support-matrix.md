# Online support matrix

Updated: 2026-10-20. This is a public, sample-scoped compatibility summary. PASS means that the named type of path worked on at least one tested public video; it is not a guarantee for all videos, regions, or networks.

| Platform/path | Status | Evidence summary |
|---|---|---|
| YouTube manual subtitles | PASS | Real sample; Apollo 11 JSON3 failure fell back to a manual VTT track and produced 335 segments. |
| YouTube automatic subtitles | PASS | A real automatic caption track was retrieved and preserved as automatic in evidence. |
| YouTube audio fallback when subtitles are unavailable | BLOCKED in the tested environment | Big Buck Bunny audio format selection succeeded, but access verification stopped transfer before any media bytes arrived. |
| Bilibili audio fallback when subtitles are unavailable | PASS | Real audio-only stream was downloaded and transcribed locally on CPU. |
| Bilibili manual/automatic subtitle success | NOT TESTED | Tested anonymous public pages did not expose a usable subtitle track; no successful real subtitle sample is claimed. |
| Douyin manual/automatic subtitle retrieval | BLOCKED | Rechecked 2026-10-19 through the available process-level proxy. The real public sample again reached the Douyin extractor and returned a fresh-cookie challenge before metadata or tracks; no credentials were read or supplied. |
| Douyin audio fallback | BLOCKED | Stopped before subtitle selection and audio transfer because the same platform access check applies. |
| Xiaohongshu manual/automatic subtitle success | NOT TESTED | The tested share page returned metadata but no subtitle tracks; no subtitle parser success is claimed. |
| Xiaohongshu audio fallback when subtitles are unavailable | PASS (one sample) | Page-provided HTTPS `og:video` MP4 returned 206 `video/mp4` for a 1 KiB range probe; bounded 85,787,212-byte MP4 download then passed PyAV audio/video validation and small/CPU/int8 ASR, producing 408 segments. |
| Local media CPU transcription | PASS | Windows Python 3.11.9, cached small model, CPU/int8 real run. |

## Real-sample coverage

The following counts summarize the R0/R1 and release checks. A page check, a successful subtitle path, and an end-to-end ASR run are different measurements; they are not interchangeable.

| Platform | Public sample checks | Verified result |
|---|---|---|
| YouTube | Five distinct public videos were used for subtitle, parser, or media-transfer diagnostics. | Manual subtitle retrieval succeeded on two videos and automatic subtitle retrieval on one. Two media-transfer diagnostics stopped at a platform access check; only one was the no-subtitle ASR fallback sample. |
| Bilibili | R0 checked six public videos; R1 checked three additional public pages for metadata and subtitle tracks. | One no-subtitle audio sample completed local ASR. No usable subtitle track was found in these checks, so subtitle-success remains unverified. |
| Xiaohongshu | Multiple URL, network, page, and HTTPS-media diagnostics were performed. | One public video completed HTTPS MP4 retrieval, audio decoding, and local ASR. |
| Douyin | A public sample was checked and rechecked through the available anonymous access path. | A fresh-cookie challenge stopped metadata or media retrieval; no online end-to-end ASR passed. |

The release regression suite contains 86 automated tests, including synthetic and offline cases. Those tests are reported separately and do not represent 86 real videos. The real-platform checks above are targeted compatibility checks, not a random or representative market sample. ASR has no CER benchmark against human-verified references.

## User-visible limitations

- Subtitle availability depends on each video's subtitle tracks and the requested language.
- The tested YouTube audio fallback was stopped by a platform access check. The program does not use cookies or bypass login, human verification, permission, or regional restrictions.
- Bilibili subtitle success has not yet been verified on a real public sample.
- The tested Douyin sample again required fresh cookies when rechecked on 2026-10-19, which this tool will not obtain or use. One Xiaohongshu sample succeeded through a page-provided HTTPS MP4; other videos may expose only HTTP media, no media, or require platform access checks.
- Xiaohongshu manual/automatic subtitle parsing remains untested on a successful track. The MP4 ASR result is limited to one public sample and is not a universal compatibility or accuracy guarantee.
- The ASR comparison has no human-reference CER results. No model accuracy ranking or universal accuracy claim is made.
- A successful public sample does not guarantee another video's availability or transcription quality.

The evidence files distinguish manual from automatic captions and keep raw source segments for traceability. They do not store original query-bearing URLs or signed subtitle/media URLs.
