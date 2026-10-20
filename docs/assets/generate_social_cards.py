from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H = 1080, 1440
BG = "#F3F5F1"
INK = "#142A32"
MUTED = "#52666B"
TEAL = "#167C72"
MINT = "#DDF1EB"
ORANGE = "#F1A35B"
PALE_ORANGE = "#FFF0DE"
WHITE = "#FFFFFF"
LINE = "#DCE5E1"
DARK = "#12363A"
FONT_REG = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")
FONT_MONO = Path(r"C:\Windows\Fonts\consola.ttf")


def font(size, bold=False, mono=False):
    path = FONT_MONO if mono else (FONT_BOLD if bold else FONT_REG)
    if not path.exists():
        path = Path(r"C:\Windows\Fonts\arial.ttf")
    return ImageFont.truetype(str(path), size)


def rounded(draw, box, radius=28, fill=WHITE, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def wrap(draw, text, fnt, max_width):
    lines, line = [], ""
    for char in text:
        test = line + char
        if line and draw.textbbox((0, 0), test, font=fnt)[2] > max_width:
            lines.append(line)
            line = char
        else:
            line = test
    if line:
        lines.append(line)
    return lines


def paragraph(draw, xy, text, fnt, fill, width, leading=10, max_lines=None):
    x, y = xy
    lines = wrap(draw, text, fnt, width)
    if max_lines:
        lines = lines[:max_lines]
    line_h = fnt.getbbox("国Ag")[3] + leading
    for i, line in enumerate(lines):
        draw.text((x, y + i * line_h), line, font=fnt, fill=fill)
    return y + len(lines) * line_h


def base(title, subtitle, page):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # quiet editorial accents
    d.ellipse((890, -160, 1210, 160), fill=MINT)
    rounded(d, (66, 58, 450, 112), 26, fill=WHITE, outline=LINE, width=2)
    d.text((88, 71), "VIDEO TRANSCRIPT", font=font(18, True), fill=INK)
    d.text((72, 155), "OPEN-SOURCE CLI  ·  V0.3.1", font=font(18, True), fill=TEAL)
    paragraph(d, (72, 202), title, font(54, True), INK, 910, leading=7, max_lines=2)
    paragraph(d, (74, 350), subtitle, font(23), MUTED, 900, leading=8, max_lines=2)
    d.line((72, 1380, 1008, 1380), fill=LINE, width=2)
    d.text((72, 1392), "视频转录  ·  Beta · 实测范围见支持矩阵", font=font(16), fill=MUTED)
    d.text((950, 1390), page, font=font(16, True), fill=TEAL)
    return im, d


def card_workflow():
    im, d = base("视频链接 →\n逐字稿与可选分析", "没有可用字幕且平台允许时，本机 CPU 识别；Codex 可按需另存分析稿。", "01 / 03")
    steps = [
        ("01", "输入链接或本地媒体", "YouTube · Bilibili · 小红书 · 抖音"),
        ("02", "先查找可用字幕", "记录字幕语言及人工 / 自动来源。"),
        ("03", "无字幕时本地识别", "平台允许获取媒体时，再运行 faster-whisper CPU ASR。"),
        ("04", "保存逐字稿与证据", "transcript.md + evidence.json；原始分段和参数可回查。"),
        ("05", "按需生成分析稿", "用户提出后，Codex 另存 analysis.md，不改写转录原文。"),
    ]
    y = 432
    row_h = 130
    row_step = 144
    for i, (num, title, desc) in enumerate(steps):
        accent = i in (2, 4)
        rounded(d, (72, y, 1008, y + row_h), 24, fill=WHITE, outline=LINE, width=2)
        d.ellipse((98, y + 26, 176, y + 104), fill=PALE_ORANGE if accent else MINT)
        d.text((112, y + 46), num, font=font(26, True), fill="#9B5B17" if accent else TEAL)
        d.text((204, y + 18), title, font=font(27, True), fill=INK)
        paragraph(d, (206, y + 62), desc, font(19), MUTED, 745, leading=3, max_lines=2)
        if i < len(steps) - 1:
            d.line((137, y + row_h, 137, y + row_step), fill=TEAL, width=4)
        y += row_step
    rounded(d, (72, 1170, 1008, 1312), 24, fill=DARK)
    d.text((105, 1195), "ASR 在本机运行", font=font(27, True), fill=WHITE)
    d.text((105, 1242), "分析稿是 Codex 的可选后处理，会使用当前 Agent 模型。", font=font(19), fill="#D8E9E4")
    return im


def card_output():
    im, d = base("三类文件，各自分开", "CLI 保存原稿和证据；Codex 可按需另存分析稿，不覆盖原始转录。", "02 / 03")
    # Transcript mock
    rounded(d, (72, 432, 1008, 794), 28, fill=WHITE, outline=LINE, width=2)
    rounded(d, (98, 452, 328, 498), 20, fill=MINT)
    d.text((118, 468), "transcript.md", font=font(19, True, mono=True), fill=TEAL)
    rounded(d, (732, 452, 974, 498), 20, fill=PALE_ORANGE)
    d.text((752, 462), "虚构格式示例", font=font(16, True), fill="#94581A")
    d.text((106, 520), "示例视频逐字稿", font=font(25, True), fill=INK)
    d.line((106, 560, 974, 560), fill=LINE, width=2)
    d.text((108, 582), "00:00:01.200 — 00:00:03.600", font=font(18, True, mono=True), fill=TEAL)
    paragraph(d, (108, 620), "今天我们聊聊语音识别的工程设计。", font(22), INK, 830, leading=5)
    d.text((108, 680), "00:00:04.000 — 00:00:06.200", font=font(18, True, mono=True), fill=TEAL)
    paragraph(d, (108, 718), "每个片段都有时间范围，方便回听核对。", font(22), INK, 830, leading=5)
    d.text((108, 762), "……", font=font(22), fill=MUTED)
    # Evidence mock
    rounded(d, (72, 812, 1008, 1027), 28, fill=DARK)
    rounded(d, (98, 831, 306, 877), 20, fill="#204A4B")
    d.text((117, 840), "evidence.json", font=font(19, True, mono=True), fill="#BCE5D9")
    code = [
        '{ "method": "local_asr",',
        '  "model": "small", "device": "cpu",',
        '  "compute_type": "int8",',
        '  "segments": [{"start": 1.2, "end": 3.6, ...}] }',
    ]
    for i, line in enumerate(code):
        d.text((108, 891 + i * 35), line, font=font(17, mono=True), fill="#E6F2EF")
    rounded(d, (72, 1045, 1008, 1285), 28, fill=PALE_ORANGE, outline=LINE, width=2)
    rounded(d, (98, 1064, 306, 1110), 20, fill=WHITE)
    d.text((117, 1073), "analysis.md", font=font(19, True, mono=True), fill="#94581A")
    d.text((106, 1130), "Codex 按需生成分析文稿", font=font(25, True), fill=INK)
    paragraph(d, (108, 1175), "基于逐字稿整理摘要、观点和时间戳依据。", font(20), MUTED, 830, leading=4, max_lines=2)
    d.text((108, 1240), "单独保存，不覆盖原始转录。", font=font(18, True), fill="#94581A")
    d.text((74, 1302), "三份文件为虚构示例；analysis.md 不是 CLI 默认输出。", font=font(16, True), fill=MUTED)
    return im


def card_status():
    im, d = base("多轮平台实测，按路径披露", "公开样本检查与自动化回归分别统计，成功结果仍按具体路径说明。", "03 / 03")
    rows = [
        ("YouTube", "一条无字幕样本曾完成 ASR；同样本复测收到 403。", "含无字幕 ASR", TEAL, MINT),
        ("Bilibili", "R0 检查 6 个视频，R1 再查 3 个页面；1 条无字幕音轨 ASR 成功，字幕未验证。", "ASR 通过", TEAL, MINT),
        ("小红书", "多轮链接、网络和 HTTPS 媒体诊断；1 条公开视频完成 MP4 → ASR。", "端到端通过", TEAL, MINT),
        ("抖音 Douyin", "新版提取器仍在元信息前受访问限制；原因不等同于已证实要求登录。", "受限", "#9B5B17", PALE_ORANGE),
    ]
    y = 430
    for platform, detail, badge, color, pale in rows:
        rounded(d, (72, y, 1008, y + 160), 25, fill=WHITE, outline=LINE, width=2)
        d.text((105, y + 25), platform, font=font(28, True), fill=INK)
        badge_w = 158 if len(badge) > 3 else 124
        rounded(d, (1008 - badge_w - 28, y + 23, 980, y + 68), 21, fill=pale)
        d.text((1008 - badge_w - 10, y + 33), badge, font=font(17, True), fill=color)
        paragraph(d, (106, y + 88), detail, font(20), MUTED, 850, leading=5, max_lines=2)
        y += 177
    rounded(d, (72, 1150, 1008, 1312), 25, fill=DARK)
    d.text((104, 1178), "104 项自动化回归测试", font=font(26, True), fill=WHITE)
    paragraph(d, (106, 1226), "测试含合成案例，不等于 104 条真实视频。ASR 也尚无人工校订 CER 基准。", font(18), "#D8E9E4", 850, leading=4, max_lines=3)
    return im


for filename, image in [
    ("xhs-01-workflow.png", card_workflow()),
    ("xhs-02-output-preview.png", card_output()),
    ("xhs-03-platform-status.png", card_status()),
]:
    image.save(OUT / filename, optimize=True)
    print(f"wrote {filename} {image.width}x{image.height}")
