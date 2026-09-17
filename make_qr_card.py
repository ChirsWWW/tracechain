# -*- coding: utf-8 -*-
"""生成演示二维码卡片：三种状态的码 + 五个站点地址 + 扫码说明。
用法：python make_qr_card.py
输出：演示二维码卡片.png（工作区根目录）
"""
import io
import os
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

CORE = "https://tracechain-core.onrender.com"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "演示二维码卡片.png")

ITEMS = [
    ("① 完整链路（推荐演示）", "TC260917BWEQ5VF3-2A27",
     "生产 → 流通×3 → 销售核销，6 条存证全绿"),
    ("② 仅发货未售出", "TC260917V26ZMB0J-6AA5",
     "生产 → 流通，销售端尚无记录"),
    ("③ 空白码（仅赋码）", "TC260917DYSKE78N-5B20",
     "尚未在任何端口登记，扫码提示待登记"),
]
SITES = [
    ("核心 / 管理控制台", "https://tracechain-core.onrender.com"),
    ("生产登记 源产通", "https://tracechain-produce.onrender.com"),
    ("流通上报 运链通", "https://tracechain-logistics.onrender.com"),
    ("销售核销 销证通", "https://tracechain-retail.onrender.com"),
    ("消费验真 正源查", "https://tracechain-consume.onrender.com"),
]

F = "C:/Windows/Fonts/msyh.ttc"
FB = "C:/Windows/Fonts/msyhbd.ttc"
M = "C:/Windows/Fonts/consola.ttf"


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def qr(url):
    req = urllib.request.Request(CORE + "/qrurl?u=" + urllib.parse.quote(url, safe=""))
    for _ in range(4):
        try:
            with urllib.request.urlopen(req, timeout=75) as r:
                return Image.open(io.BytesIO(r.read())).convert("RGB")
        except Exception:
            continue
    raise SystemExit("二维码下载失败：" + url)


W = 1180
QR_S = 240
PAD = 46
H = PAD + 96 + (QR_S + 132) * 3 + 60 + 40 + len(SITES) * 34 + PAD
img = Image.new("RGB", (W, H), "#ffffff")
d = ImageDraw.Draw(img)

INK, SUB, ACC = "#1b2733", "#63748a", "#0f6b52"

d.rectangle([0, 0, W, 8], fill=ACC)
d.text((PAD, PAD - 6), "溯链 TraceChain · 演示二维码", font=font(FB, 40), fill=INK)
d.text((PAD, PAD + 54), "用手机扫一扫下方二维码 → 打开公众验真页；"
                        "五个网址相互独立，数据同源共享",
       font=font(F, 19), fill=SUB)

y = PAD + 110
for title, code, note in ITEMS:
    card_h = QR_S + 108
    d.rounded_rectangle([PAD, y, W - PAD, y + card_h], radius=16,
                        fill="#f5f9f7", outline="#d8e5df", width=2)
    im = qr(f"{CORE}/t/{code}")
    im = im.resize((QR_S, QR_S), Image.LANCZOS)
    img.paste(im, (PAD + 26, y + 40))
    tx = PAD + 26 + QR_S + 34
    d.text((tx, y + 42), title, font=font(FB, 26), fill=ACC)
    d.text((tx, y + 84), code, font=font(M, 25), fill=INK)
    d.text((tx, y + 124), note, font=font(F, 19), fill=SUB)
    d.text((tx, y + 158), f"验真页：{CORE}/t/{code}", font=font(F, 15), fill="#94a3b8")
    y += card_h + 26

y += 14
d.line([PAD, y, W - PAD, y], fill="#e2e8f0", width=2)
y += 26
d.text((PAD, y), "五个独立站点入口", font=font(FB, 24), fill=INK)
y += 42
for name, url in SITES:
    d.text((PAD, y), "· " + name, font=font(F, 18), fill=SUB)
    d.text((PAD + 230, y), url, font=font(M, 17), fill=ACC)
    y += 34

y += 10
d.text((PAD, y), "提示：管理控制台需口令 admin888；免费层冷启动约 5 秒，首次打开请稍候。",
       font=font(F, 16), fill="#94a3b8")

img.save(OUT)
print("已生成：", OUT, img.size)
