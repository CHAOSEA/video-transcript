# Online support matrix

Updated: 2026-10-20. This is a public, sample-scoped compatibility summary. PASS means that the named type of path worked on at least one tested public video; it is not a guarantee for all videos, regions, or networks.

| Platform/path | Status | Evidence summary |
|---|---|---|
| YouTube manual subtitles | PASS | Real sample; Apollo 11 JSON3 failure fell back to a manual VTT track and produced 335 segments. |
| YouTube automatic subtitles | PASS | A real automatic caption track was retrieved and preserved as automatic in evidence. |
| YouTube audio fallback when subtitles are unavailable | PASS (one full run; repeatability remains limited) | The pinned stable version previously completed the no-subtitle audio-only download and local small/CPU/int8 ASR path on an openly licensed public video through a one-run proxy override. On 2026-10-20, two stable-version media requests for that sample received HTTP 403. The latest official nightly downloaded audio once, but its full local ASR run did not succeed. Accuracy was not evaluated. |
| Bilibili audio fallback when subtitles are unavailable | PASS | Real audio-only stream was downloaded and transcribed locally on CPU. |
| Bilibili manual/automatic subtitle success | NOT TESTED | Tested anonymous public pages did not expose a usable subtitle track; no successful real subtitle sample is claimed. |
| Douyin manual/automatic subtitle retrieval | BLOCKED | On 2026-10-20, yt-dlp stable 2026.8.19 and official nightly 2026.10.10.233141 both returned a fresh-cookie access challenge for a public sample before metadata or tracks. The message does not establish that login is the cause. |
| Douyin audio fallback | BLOCKED | Stopped before subtitle selection and audio transfer because metadata retrieval was blocked. No cookies, signed request generation, or browser-session reuse was attempted. |
| Xiaohongshu manual/automatic subtitle success | NOT TESTED | The tested share page returned metadata but no subtitle tracks; no subtitle parser success is claimed. |
| Xiaohongshu audio fallback when subtitles are unavailable | PASS (one sample) | Page-provided HTTPS `og:video` MP4 returned 206 `video/mp4` for a 1 KiB range probe; bounded 85,787,212-byte MP4 download then passed PyAV audio/video validation and small/CPU/int8 ASR, producing 408 segments. |
| Local media CPU transcription | PASS | Windows Python 3.11.9, cached small model, CPU/int8 real run. |

## Real-sample coverage

The following counts summarize the R0/R1 and release checks. A page check, a successful subtitle path, and an end-to-end ASR run are different measurements; they are not interchangeable.

| Platform | Public sample checks | Verified result |
|---|---|---|
| YouTube | Earlier public videos were used for subtitle, parser, and media-transfer diagnostics; an openly licensed sample was rechecked through stable and nightly versions. | Manual subtitle retrieval succeeded on two videos and automatic subtitle retrieval on one. The pinned stable version has one earlier full no-subtitle ASR success. In this run, stable received HTTP 403 twice during media transfer; nightly downloaded audio, but its end-to-end ASR did not pass. |
| Bilibili | R0 checked six public videos; R1 checked three additional public pages for metadata and subtitle tracks. | One no-subtitle audio sample completed local ASR. No usable subtitle track was found in these checks, so subtitle-success remains unverified. |
| Xiaohongshu | Multiple URL, network, page, and HTTPS-media diagnostics were performed. | One public video completed HTTPS MP4 retrieval, audio decoding, and local ASR. |
| Douyin | A public sample was checked through the anonymous access path using stable yt-dlp 2026.8.19 and official nightly 2026.10.10.233141. | Both versions returned a fresh-cookie access challenge before metadata. No online end-to-end ASR passed. |

The current regression suite count is reported in the README and CI results; it includes synthetic and offline cases and does not represent real-video sample counts. The real-platform checks above are targeted compatibility checks, not a random or representative market sample. ASR has no CER benchmark against human-verified references.

## User-visible limitations

- Subtitle availability depends on each video's subtitle tracks and the requested language.
- YouTube no-subtitle fallback completed once with yt-dlp 2026.8.19 and an explicit proxy. Two later stable-version transfer requests received HTTP 403, while the latest nightly downloaded audio but did not complete local ASR on the sample. Treat the compatibility as request-context dependent, not a guarantee. If the inherited proxy is stale, use `--proxy http://127.0.0.1:PORT` for one command; the option is not persisted and does not accept credentials. The program does not bypass login, human verification, permission, or regional restrictions.
- Bilibili subtitle success has not yet been verified on a real public sample.
- A tested Douyin sample returned the same fresh-cookie challenge before metadata with both yt-dlp stable and official nightly. This wording does not prove that login is the cause. The tool will not obtain or use browser credentials or synthesize platform signatures. Upstream tracking includes [issue #9667](https://github.com/yt-dlp/yt-dlp/issues/9667) and the still-open [PR #17826](https://github.com/yt-dlp/yt-dlp/pull/17826), which discusses request-signature and cookie-state generation; this project does not adopt that unmerged bypass-like logic.
- Xiaohongshu manual/automatic subtitle parsing remains untested on a successful track. The MP4 ASR result is limited to one public sample and is not a universal compatibility or accuracy guarantee.
- The ASR comparison has no human-reference CER results. No model accuracy ranking or universal accuracy claim is made.
- A successful public sample does not guarantee another video's availability or transcription quality.

The evidence files distinguish manual from automatic captions and keep raw source segments for traceability. They do not store original query-bearing URLs or signed subtitle/media URLs.

## Upstream diagnostics checked on 2026-10-20

- The [latest yt-dlp stable release](https://github.com/yt-dlp/yt-dlp/releases/latest) was 2026.08.19. Upstream reports include intermittent YouTube HTTP 403 cases even on recent builds; a 403 is not enough evidence to identify a project-side extractor bug or a login requirement ([issue #17647](https://github.com/yt-dlp/yt-dlp/issues/17647)).
- The latest PyPI nightly available during this check was `2026.10.10.233141.dev0`. It downloaded audio for the test video once, while a later ASR run did not produce a successful transcript. The project keeps the stable dependency pin until the full path is repeatable; yt-dlp's [installation guidance](https://pypi.org/project/yt-dlp/) describes nightly as a development snapshot and recommends trying it when stable breaks.
- The proposed [Douyin 403 fix, PR #17826](https://github.com/yt-dlp/yt-dlp/pull/17826), was still open when checked and describes generating request signature and cookie state. This project does not implement unmerged signature-generation work or bypass platform access checks; it keeps the local-media transcription path available instead.
