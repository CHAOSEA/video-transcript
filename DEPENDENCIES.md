# Dependency, provenance, and license record

Updated: 2026-10-20

## Project source

This release contains the new implementation of local media transcription, public-video URL validation, subtitle retrieval, subtitle-first fallback workflow, and evidence generation. No v9, V10.4, or legacy modular source files are included.

The project's source code is licensed under **MIT**, with copyright line `Copyright (c) 2026 CHAOS`. The root `LICENSE` contains the full SPDX MIT text; `pyproject.toml` declares `license = "MIT"` and includes `LICENSE` in license files. This project license does not change third-party dependency, model, or processed-media rights.

## Direct runtime dependencies

| Component | Version / constraint | License | Source and notes |
|---|---:|---|---|
| Python | Validated: 3.11.9; declared: >=3.10 | Python Software Foundation License | [Official license](https://docs.python.org/3/license.html) |
| faster-whisper | 1.2.1 | MIT | [Upstream repository](https://github.com/SYSTRAN/faster-whisper) |
| PyAV (`av`) | 17.0.0 | BSD-3-Clause | [Upstream license](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt); wheels may bundle FFmpeg components. |
| yt-dlp | 2026.8.19, with `default` extra | Unlicense | [Upstream license](https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE); this project depends on the Python package and does not bundle the standalone executable. |
| yt-dlp-ejs | 0.8.0; installed from `yt-dlp[default]` in the isolated R3 check | Unlicense, with included MIT/ISC components | See the [upstream repository](https://github.com/yt-dlp/ejs). |
| Node.js | Node 24.22.0 LTS verified; EJS requires Node >=22 | Node.js license and bundled notices | System runtime only; not installed or packaged by this project. The R3 check used a checksum-verified temporary Node distribution; the initial validation used Node 25.9.0, which is now EOL. See [Node.js releases](https://nodejs.org/en/about/previous-releases). |

The package declares faster-whisper, PyAV, and yt-dlp in `pyproject.toml`. Their transitive dependencies are resolved by pip; exact versions vary by platform and installation date. A release uploaded to a package index should be accompanied by a lock or reproducible environment record and a generated software bill of materials.

## Validated transitive packages

The clean Windows Python 3.11.9 environment used for the R0 validation included:

| Component | Validated version | License |
|---|---:|---|
| CTranslate2 | 4.8.3 | MIT |
| huggingface-hub | 2.2.0 | Apache-2.0 |
| tokenizers | 0.23.3 | Apache-2.0 |
| ONNX Runtime | 1.31.0 | MIT |

These are environment observations, not direct constraints in `pyproject.toml`. Re-check the resolved environment for each release target.

## Model files

The real local ASR check used `Systran/faster-whisper-small`, revision `536b0662742c02347bc0e980a01041f333bce120`, whose model card identifies MIT licensing. The repository describes it as a CTranslate2-format conversion of OpenAI Whisper Small. Model weights are not included in this release, are not installed with the Python package, and are not downloaded by the transcription commands. Users must obtain model files separately and comply with the model's license and terms.

- [Model card](https://huggingface.co/Systran/faster-whisper-small)
- [faster-whisper upstream](https://github.com/SYSTRAN/faster-whisper)

## Platform and packaging notes

- yt-dlp uses its own platform extractors and media transfer implementation. The project pins yt-dlp 2026.8.19. The no-subtitle audio-to-ASR path completed once with this version through a valid one-run proxy override, but repeated transfers on the same sample have also received HTTP 403. The latest official nightly downloaded audio once but did not complete the full local ASR run; the project does not pin a development build based on that partial result. Re-check the network adapter whenever yt-dlp is upgraded.
- yt-dlp 2026.8.19 includes `Douyin` and `XiaoHongShu` extractors in its upstream support list. That list does not guarantee that any given video, subtitle, CDN, or region is accessible; an anonymous Douyin check remained blocked before metadata, while one Xiaohongshu sample completed the HTTPS MP4 fallback and local ASR. See `docs/online-support-matrix.md`.
- Online CLIs accept a one-command `--proxy` override for HTTP(S) proxies. It overrides inherited proxy settings only for that run, rejects embedded credentials, and is not written to evidence. The normal environment-variable behavior remains when the option is omitted.
- Xiaohongshu download selection prefers audio-only formats, then a page-provided, allowlisted HTTPS `og:video` MP4. PyAV validates that the downloaded MP4 contains both audio and video. The page metadata path was exercised with one real sample; it is not a platform-wide availability guarantee.
- The project does not package or enable remote EJS components, read login credentials, or bypass platform checks.
- For Xiaohongshu, an `xsec_token` explicitly present in the supplied URL may be used only in-memory for the extractor request. Evidence and safe errors keep it out; no browser or account credentials are loaded.
- Python wheels for native packages such as PyAV may include separately licensed binary components. This project builds its own wheel and sdist but does not repackage upstream dependency wheels.
- Runtime dependencies, model files, and third-party video/audio/subtitle content retain their own licenses and terms; the project's MIT grant does not grant rights to those materials.
