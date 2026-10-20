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
    d.text((88, 71), "VIDEO TRANSCRIPT WORKBENCH", font=font(18, True), fill=INK)
    d.text((72, 155), "OPEN-SOURCE CLI  ·  V0.3.0", font=font(18, True), fill=TEAL)
    paragraph(d, (72, 202), title, font(54, True), INK, 910, leading=7, max_lines=2)
    paragraph(d, (74, 350), subtitle, font(23), MUTED, 900, leading=8, max_lines=2)
    d.line((72, 1380, 1008, 1380), fill=LINE, width=2)
    d.text((72, 1392), "视频转录工作台  ·  Beta · 以实测样本为准", font=font(16), fill=MUTED)
    d.text((950, 1390), page, font=font(16, True), fill=TEAL)
    return im, d


def card_workflow():
    im, d = base("视频链接 →\n可追溯的逐字稿", "字幕优先；平台允许且字幕不可用时，再由本机 CPU 执行语音识别。", "01 / 03")
    steps = [
        ("01", "输入视频链接", "YouTube · Bilibili · 小红书 · 抖音"),
        ("02", "优先查找字幕", "人工字幕优先于自动字幕，并记录来源类型。"),
        ("03", "本地 ASR 回退", "没有可用字幕且访问允许时，下载媒体并运行 faster-whisper。"),
        ("04", "得到两类文件", "transcript.md 阅读时间轴；evidence.json 保留原始分段与运行信息。"),
    ]
    y = 462
    for i, (num, title, desc) in enumerate(steps):
        rounded(d, (72, y, 1008, y + 148), 24, fill=WHITE, outline=LINE, width=2)
        d.ellipse((98, y + 35, 176, y + 113), fill=MINT if i != 2 else PALE_ORANGE)
        d.text((112, y + 55), num, font=font(26, True), fill=TEAL if i != 2 else "#9B5B17")
        d.text((204, y + 28), title, font=font(29, True), fill=INK)
        paragraph(d, (206, y + 76), desc, font(20), MUTED, 745, leading=4, max_lines=2)
        if i < len(steps) - 1:
            d.line((137, y + 148, 137, y + 177), fill=TEAL, width=4)
        y += 174
    rounded(d, (72, 1175, 1008, 1312), 24, fill=DARK)
    d.text((105, 1202), "音频留在本机识别", font=font(27, True), fill=WHITE)
    d.text((105, 1250), "不调用云端转录服务；平台访问受限时明确停止。", font=font(20), fill="#D8E9E4")
    return im


def card_output():
    im, d = base("输出不是一段纯文本", "Markdown 方便阅读；Evidence 保留可以回查的原始分段和运行信息。", "02 / 03")
    # Transcript mock
    rounded(d, (72, 432, 1008, 912), 28, fill=WHITE, outline=LINE, width=2)
    rounded(d, (98, 458, 328, 504), 20, fill=MINT)
    d.text((118, 468), "transcript.md", font=font(19, True, mono=True), fill=TEAL)
    rounded(d, (732, 458, 974, 504), 20, fill=PALE_ORANGE)
    d.text((752, 468), "格式示意 · 虚构文本", font=font(16, True), fill="#94581A")
    d.text((106, 543), "示例视频逐字稿", font=font(27, True), fill=INK)
    d.line((106, 592, 974, 592), fill=LINE, width=2)
    d.text((108, 625), "00:00:01.200 — 00:00:03.600", font=font(19, True, mono=True), fill=TEAL)
    paragraph(d, (108, 665), "今天我们聊聊语音识别的工程设计。", font(24), INK, 830, leading=7)
    d.text((108, 734), "00:00:04.000 — 00:00:06.200", font=font(19, True, mono=True), fill=TEAL)
    paragraph(d, (108, 774), "每个片段都有时间范围，方便回听核对。", font(24), INK, 830, leading=7)
    d.text((108, 844), "……", font=font(24), fill=MUTED)
    # Evidence mock
    rounded(d, (72, 942, 1008, 1270), 28, fill=DARK)
    rounded(d, (98, 968, 306, 1014), 20, fill="#204A4B")
    d.text((117, 977), "evidence.json", font=font(19, True, mono=True), fill="#BCE5D9")
    code = [
        '{ "method": "local_asr",',
        '  "model": "small", "device": "cpu",',
        '  "compute_type": "int8",',
        '  "segments": [{"start": 1.2, "end": 3.6, ...}] }',
    ]
    for i, line in enumerate(code):
        d.text((108, 1042 + i * 45), line, font=font(20, mono=True), fill="#E6F2EF")
    d.text((74, 1300), "内容为演示示例，不来自真实用户视频。", font=font(17, True), fill=MUTED)
    return im


def card_status():
    im, d = base("平台结果按样本披露", "提取器能识别平台，不等于每条视频都能访问或转录。", "03 / 03")
    rows = [
        ("YouTube", "人工 / 自动字幕：实测样本通过", "PASS", TEAL, MINT),
        ("Bilibili", "音轨下载 → 本地 ASR：样本通过；字幕未验证", "样本通过", TEAL, MINT),
        ("小红书", "HTTPS MP4 → 本地 ASR：1 个样本通过", "单样本", TEAL, MINT),
        ("抖音 Douyin", "fresh-cookie challenge 阻断；在线端到端未通过", "受限", "#9B5B17", PALE_ORANGE),
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
    d.text((104, 1178), "准确率还没有 CER 基准", font=font(26, True), fill=WHITE)
    paragraph(d, (106, 1226), "目前没有人工校订参考稿；这些流程实测不代表识别准确率，也不保证其他链接成功。", font(18), "#D8E9E4", 850, leading=4, max_lines=3)
    return im


for filename, image in [
    ("xhs-01-workflow.png", card_workflow()),
    ("xhs-02-output-preview.png", card_output()),
    ("xhs-03-platform-status.png", card_status()),
]:
    image.save(OUT / filename, optimize=True)
    print(f"wrote {filename} {image.width}x{image.height}")
