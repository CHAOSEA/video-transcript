# 视频转录 | Video Transcript

**Video Transcript · 在线 CLI Beta v0.3.1 · 中文说明**<br>
[English](README.en.md) · [在线平台实测矩阵](docs/online-support-matrix.md)

Video Transcript 把已支持平台的视频链接或本地音视频，变成带时间轴的逐字稿。没有可用字幕时，只要平台允许获取媒体，工具就会在本机识别语音；在 Codex 中还可以按需基于逐字稿生成单独的分析稿。

命令行会生成 `transcript.md` 和 `evidence.json`，后者记录原始分段、来源和运行参数。分析稿由 Codex 项目 Skill 按你的要求另存为 `analysis.md`，不覆盖逐字稿；单独使用命令行时不会生成分析稿。

> Beta 已经过多轮真实平台样本检查和自动化回归。公开支持范围按平台与处理路径披露，不代表所有视频都能处理成功。

## 项目是什么？怎么使用？

核心产品是独立运行的 Python 命令行工具（CLI）；本仓库同时提供 Codex 项目级 Skill，帮助 Agent 调用 CLI 并检查结果。字幕获取、媒体处理和 ASR 始终由 CLI 执行。

### 推荐方式：Codex + 项目 Skill

1. 在 Codex 中打开本仓库根目录。
2. 按下方安装说明安装 CLI，并准备好本地 Whisper 模型。
3. 直接提出转录需求，或显式调用较短的 Skill 名 `$video-transcript`，例如：“用 `$video-transcript` 转录这个链接，中文，输出到 D:\transcripts\run-01。”

Skill 位于 [.codex/skills/video-transcript/SKILL.md](.codex/skills/video-transcript/SKILL.md)。它会选择在线链接、纯字幕或本地媒体入口，并检查 CLI 输出。若要求分析稿，Codex 会读取逐字稿并另存 `analysis.md`；逐字稿可能会交给当前 Codex 模型处理。分析稿和原稿分开保存。

### 也可以独立使用

不需要 Codex、WorkBuddy 或云端 AI 服务。安装并激活项目环境后，在 PowerShell 运行：

~~~powershell
video-transcript-link "https://www.youtube.com/watch?v=VIDEO_ID" --language zh --model small --output-dir "D:\transcripts\run-01"
~~~

本地文件使用 video-transcript；只获取在线字幕时使用 video-transcript-online。

### WorkBuddy 和其他 Agent

其他能在本机访问终端和文件的 Agent 也可以调用 CLI，但当前仓库没有专用的 WorkBuddy Skill 或插件。若 Agent 在云端或另一台机器运行，它无法直接使用本机媒体、Python 环境或模型缓存。

CLI 的 ASR 在本机运行。若要求云端 Agent 阅读或总结生成的逐字稿，文本可能会进入该 Agent 的模型处理流程；仅需转录时，可直接运行 CLI 并留在本机查看输出。
## 处理流程

![处理流程：字幕优先；没有可用字幕且平台允许时本机 CPU 识别；Codex 可按需另存分析文稿](docs/assets/xhs-01-workflow.png)

流程简述：

1. 输入 YouTube、Bilibili、抖音或小红书视频链接，或选择本地媒体文件。
2. 在线链接先查找匹配语言的人工字幕，再尝试自动字幕。
3. 没有可用字幕时，只有在平台正常允许访问的情况下，才获取音轨或带音轨 MP4，并在本机运行 faster-whisper CPU/int8。
4. CLI 输出时间轴逐字稿 `transcript.md` 和原始分段、来源及运行参数 `evidence.json`。
5. 用户提出分析需求时，Codex 可基于逐字稿另存 `analysis.md`，不覆盖转录文件。

字幕内容与 ASR 原始识别文本都会保留；格式化不会覆盖原始分段。工具不调用云端转录服务，不读取浏览器 Cookie，也不绕过登录、真人验证或地区限制。

## 输出效果示意

![输出效果示意：transcript.md 与 evidence.json 为 CLI 输出，analysis.md 是 Codex 按需生成的独立文件；示例内容为虚构文本](docs/assets/xhs-02-output-preview.png)

图中逐字稿、JSON 和分析稿均为**虚构格式示例**。`analysis.md` 不是 CLI 自动输出；Codex 只有在用户提出分析需求时才会生成。示例不代表识别准确率。

## 在线平台实测状态

![平台实测范围：展示多轮公开视频检查、各平台成功路径和访问限制；104 项自动化回归测试另行统计](docs/assets/xhs-03-platform-status.png)

| 平台与路径 | 实测状态 | 说明 |
|---|---|---|
| YouTube 人工字幕 | PASS | 已真实获取字幕；特定字幕格式失败时验证过备用字幕格式。 |
| YouTube 自动字幕 | PASS | 已真实获取自动字幕，证据中保留其自动生成属性。 |
| YouTube 无字幕音频 → 本地 ASR | PASS（单样本，复测有波动） | 一条无字幕视频此前完成音轨下载和 CPU/int8 转录；2026-10-20 复测同一样本时在元信息阶段收到 HTTP 403，ASR 未启动。 |
| Bilibili 无字幕音轨 → 本地 ASR | PASS | 已完成真实音轨下载和 CPU 转录。 |
| Bilibili 字幕获取 | 未验证 | 尚无真实字幕成功样本。 |
| 小红书 HTTPS MP4 → 本地 ASR | PASS（单样本） | 一条公开视频完成真实下载与本地转录；不代表其他视频都能访问。 |
| 小红书字幕获取 | 未验证 | 尚无成功解析真实字幕轨道的样本。 |
| 抖音在线转录 | 受限 | 当前提取器在取得视频信息前遇到访问限制，尚未完成在线端到端转录；工具不会读取或获取凭据。 |

### 实测覆盖范围

- YouTube：5 条不同公开视频用于字幕、解析或媒体传输诊断；2 条人工字幕和 1 条自动字幕路径成功；一条无字幕视频此前用 yt-dlp 2026.8.19 完成本地 ASR，但同一样本在 2026-10-20 复测时于元信息阶段收到 HTTP 403。另一次不同视频的媒体传输诊断也在平台访问检查处停止；复测不计为新视频样本。
- Bilibili：R0 检查 6 个公开视频，R1 又检查 3 个公开页面的元信息和字幕轨；1 条无字幕音轨完成本机 ASR，尚无真实字幕成功样本。
- 小红书：经过多轮链接、网络和 HTTPS 媒体诊断，1 条公开视频完成 MP4 到本机 ASR 的端到端流程。
- 抖音：使用 yt-dlp 2026.8.19、未提供凭据的公开样本复核仍在取得视频信息前受限；这还不能证明原因一定是登录要求。
- 当前 104 项自动化回归测试另行统计，包含离线和合成案例，不等于 104 条真实视频。v0.3.0 发布验收时的 86 项测试是历史记录。

详细样本覆盖、状态定义和用户可见限制见[在线平台实测矩阵](docs/online-support-matrix.md)。公开视频检查数量、成功路径数量和自动化回归数量分别披露。**目前没有人工校订参考稿 CER 基准，因此不声称模型准确率排名或普遍准确率。**

## 命令

安装后提供三个入口：

- `video-transcript-link`：在线链接，字幕优先；符合条件时回退到本地 CPU ASR。
- `video-transcript-online`：只获取在线字幕，不下载媒体、不运行 ASR。
- `video-transcript`：转录本地 WAV、MP3、M4A、MP4、WebM、Ogg 或 Opus 文件。

## Windows 安装

已验证环境为 Windows 10/11、Python 3.11。项目声明 Python 3.10+，其他版本和操作系统尚未完成验证。

~~~powershell
git clone https://github.com/CHAOSEA/video-transcript.git
cd video-transcript
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\Activate.ps1
~~~

在线 YouTube JavaScript challenge 支持需要 Node.js。Node 24.22.0 LTS 与 yt-dlp-ejs 0.8.0 已完成隔离环境检查；EJS 要求 Node.js 22 或更新版本。Node.js 不由本项目安装。

如果 PowerShell 不允许运行激活脚本，可直接调用 .\.venv\Scripts\video-transcript-link.exe 等入口。

Whisper 模型权重需要单独取得，本项目不会静默下载或自动切换模型。已验收的模型为 `Systran/faster-whisper-small`。如果本机尚无该模型，可在明确选择后使用 Hugging Face Hub 工具下载，例如：

~~~powershell
hf download Systran/faster-whisper-small
~~~

模型文件不包含在 Python 安装包中，需遵循其自身许可证和使用条款。

## 在线链接用法

### 字幕优先，必要时本地 ASR

~~~powershell
video-transcript-link "https://www.youtube.com/watch?v=VIDEO_ID" --language zh --model small --output-dir "D:\transcripts\video-run-01"
~~~

支持 YouTube、Bilibili、抖音和小红书视频笔记 URL。字幕语言偏好为 `zh`、`en`、`ja`。小红书仅支持视频笔记，不支持纯图文笔记。

链接可识别不等于每个链接都能处理。访问权限、字幕轨道、地区、网络、媒体格式和本地资源都会影响结果；请先查看上面的样本级实测矩阵。

如果平台拒绝在线请求，但你已经通过平台允许的方式保存了本地视频或音频，可以直接用下面的 `video-transcript` 本地入口继续转录。在线入口不会读取浏览器登录态，也不会绕过访问检查。

### 只获取字幕

~~~powershell
video-transcript-online "https://www.youtube.com/watch?v=VIDEO_ID" --language en --output-dir "D:\transcripts\subtitle-run-01"
~~~

该命令只获取平台元信息和匹配字幕，不下载媒体，也不运行语音识别。

## 本地媒体用法

~~~powershell
video-transcript "D:\media\sample.mp4" --model small --language zh --output-dir "D:\transcripts\local-run-01"
~~~

媒体在本机解码和转录。视频输入使用第一条音轨，源文件不会被修改。为避免误处理超长文件，本地输入默认限制为 30 分钟；可用 `--max-duration-seconds` 调整，最高 4 小时。模型必须是本地已有的模型路径或已缓存模型；缺失时会报错，不会静默下载或降级。

## 结果与隐私

成功运行会生成：

- `transcript.md`：逐段时间轴与原始文字。
- `evidence.json`：视频标识或本地输入 SHA256、字幕/ASR 来源、原始分段、模型与设备参数、依赖版本及运行状态。

在线证据不会保存原始查询参数、签名字幕地址或签名媒体地址。小红书分享链接中显式提供的必要分享参数只在请求期间使用，不写入证据或安全错误。结果文件本身可能包含视频标题和完整逐字稿，请妥善保管输出目录。

工具不使用云端 ASR，不自动读取浏览器资料或 Cookie，不绕过访问验证。线上下载有时长和字节数限制，媒体使用临时目录并在流程结束后清理。较长的本地音频仍会占用较多内存。

## 已知限制

- 平台访问检查、网络或代理故障会作为独立失败状态报告，不会被伪装成“没有字幕”。
- 用户指定的模型不会自动降级；模型文件缺失会明确报错。
- ASR 尚无人工参考稿 CER 对照，较大模型不等于已证明更准确。
- 成功样本不保证其他视频、语言、地区或网络环境可以成功。
- 仅处理自己有权访问和处理的媒体，并遵循平台条款和内容权利要求。

## 开发与测试

维护者的 v0.3.0 发布验收在 Windows Python 3.11 环境通过了 **86 项自动化测试**。测试包含合成媒体与字幕样例，不替代真实平台兼容性验证。

~~~powershell
python -m unittest discover -s tests -v
~~~

Node.js 24 LTS / yt-dlp-ejs、完整依赖安装和发行包检查记录见[依赖与权利记录](DEPENDENCIES.md)。

## 许可证

项目源码采用 MIT 许可证，版权行：`Copyright (c) 2026 CHAOS`。完整许可见 [LICENSE](LICENSE)。依赖、模型文件、以及处理的视频、音频和字幕仍各自遵循其许可证和平台条款。
