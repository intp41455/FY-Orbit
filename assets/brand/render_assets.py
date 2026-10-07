#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FY 品牌基础资产渲染脚本（零成本 / 本机离线 / 无网络依赖）
================================================================
产出：
  favicon/favicon-16.png  favicon-32.png  favicon-48.png
  favicon/apple-touch-icon-180.png  favicon-192.png  favicon-512.png
  favicon/favicon.ico（16/32/48 三档打包）
  og-github-1280x640.png
  og-share-1200x630.png
  readme-banner-1280x320.png
  wechat-thumb-200x200.png
  changelog-template-1080x540.png

事实来源：
  deliverables/product-strategy/brand-visual-system-2026-10-06.md
  deliverables/product-strategy/landing-page-2026-10-06.html  (:root token 基线)

设计约束（已内建）：
  * 色值只取品牌色板；出图后自动跑色板审计（凸组合模型），越界即告警
  * 标题字号自动收敛（二分搜索 + 真实墨迹宽度），保证不溢出安全边距
  * 深色底 + 径向光晕 + 64px 细网格，与落地页 .bg / ::before / ::after 同构
  * FY 字标用几何多边形绘制（与 logo-*.svg 的手写 <path> 同源），不依赖字体
  * 中文字体只从本机 Windows 字体目录查找，绝不下载、不联网
  * 出图后逐块校验墨迹包围盒：越界 / 垂直重叠 / 缺字 一律打印告警

运行：
  C:/Users/intpj/.workbuddy/binaries/python/envs/default/Scripts/python.exe render_assets.py
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# =====================================================================
# 0. 路径与字体
# =====================================================================
HERE = Path(__file__).resolve().parent
FAVICON_DIR = HERE / "favicon"

# 中文字体查找链（按顺序命中即用；全部缺失则大声报错，避免静默出豆腐块）
FONT_CHAIN_BOLD = [
    (r"C:\Windows\Fonts\msyhbd.ttc", 0, "Microsoft YaHei Bold"),
    (r"C:\Windows\Fonts\msyh.ttc", 0, "Microsoft YaHei"),
    (r"C:\Windows\Fonts\simhei.ttf", 0, "SimHei"),
]
# 次级字重链（副标 / 说明文字用，避免通篇粗体）
FONT_CHAIN_REG = [
    (r"C:\Windows\Fonts\msyh.ttc", 0, "Microsoft YaHei"),
    (r"C:\Windows\Fonts\simhei.ttf", 0, "SimHei"),
    (r"C:\Windows\Fonts\msyhbd.ttc", 0, "Microsoft YaHei Bold"),
]

_font_cache: dict = {}


def _resolve(chain: list, label: str) -> tuple:
    for path, idx, name in chain:
        if os.path.isfile(path):
            return path, idx, name
    raise SystemExit(
        f"[FATAL] 未找到任何可用中文字体（{label}）。\n"
        f"        已尝试：{[c[0] for c in chain]}\n"
        f"        请安装「微软雅黑 / 黑体」后重跑；本脚本不会联网下载字体。"
    )


FONT_BOLD_PATH, FONT_BOLD_IDX, FONT_BOLD_NAME = _resolve(FONT_CHAIN_BOLD, "bold")
FONT_REG_PATH, FONT_REG_IDX, FONT_REG_NAME = _resolve(FONT_CHAIN_REG, "regular")


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """按字号取字体（带缓存）。bold=False 走次级字重链。"""
    if bold:
        path, idx = FONT_BOLD_PATH, FONT_BOLD_IDX
    else:
        path, idx = FONT_REG_PATH, FONT_REG_IDX
    key = (path, int(size), idx)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(path, int(size), index=idx)
    return _font_cache[key]


# =====================================================================
# 1. 品牌常量 —— 唯一事实来源
# =====================================================================
def rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


BRAND = {
    "ink": rgb("#07080B"),
    "ink_2": rgb("#0C0E14"),
    "surface": rgb("#12151D"),
    "surface_2": rgb("#171B25"),
    "text": rgb("#EAECF2"),
    "muted": rgb("#8A93A8"),
    "muted_2": rgb("#5F6779"),
    "accent": rgb("#35E0C8"),
    "accent_deep": rgb("#1FA894"),
    "accent_ink": rgb("#04211D"),
    "violet": rgb("#8A7BFF"),
    "amber": rgb("#F5C26B"),
}

# 允许出现在成品图里的色值集合（出图后自动审计，越界即告警）
PALETTE = {v for v in BRAND.values()} | {
    rgb("#000000"),
    rgb("#FFFFFF"),
    rgb("#7FF0DC"),   # 落地页 h1 .accent 渐变中停
    rgb("#5A6272"),   # 浅底副标
}

# ---------------------------------------------------------------------
# 中文可变文本层：中文产品名一旦拍板，只改这里即可全量替换
# ---------------------------------------------------------------------
TEXTS = {
    # 产品名：FY Orbit / 星轨（FY 为几何字标，文本侧为 Orbit · 星轨）
    "name": "FY Orbit · 星轨",
    "positioning": "FY Orbit · 星轨",                  # OG 主标
    "positioning_unified": "FY Orbit · 星轨",          # banner 同款
    "positioning_l1": "本地多智能体",
    "positioning_l2": "搭建与统一调度平台",
    # 新副标（功能描述，非竞品名，合规安全）
    "subtitle": "本地运行 · 无需联网 · 可视化与代码实时同源 · 统一调度外部 Agent",
    "selling_points": [
        "本地运行 · 无需联网",
        "画布与代码实时同源",
        "统一调度外部 Agent",
    ],
    "signature": "全部开源 · 完全免费",
}

# ---------------------------------------------------------------------
# FY 字标几何（原生网格 146 × 100，cap height = 100）
# 与 logo-mark.svg / logo-horizontal.svg 中的手写 <path> 完全同源
# ---------------------------------------------------------------------
FY_F = [(0, 0), (64, 0), (64, 24), (24, 24), (24, 42), (56, 42), (56, 66), (24, 66), (24, 100), (0, 100)]
FY_Y = [(72, 0), (94, 0), (109, 26), (124, 0), (146, 0), (120, 44), (120, 100), (98, 100), (98, 44)]
FY_BOX_W, FY_BOX_H = 146.0, 100.0


# =====================================================================
# 2. 版式预设 —— 尺寸 / 字号 / 边距一处定义，全局复用
#    所有 *_baseline 一律为「基线」y（Pillow anchor 末位 's'）
# =====================================================================
PRESETS = {
    # 图形标母版（favicon 与应用图标源）
    "mark": dict(size=512, ss=4, radius_ratio=0.26, fy_ratio=0.6616),

    # GitHub 仓库社交预览图（仓库 Settings → Social preview）
    "gh_social": dict(
        w=1280, h=640, pad=72,
        tile=96, tile_y=110,
        sig_size=20,
        sig_baseline=110 + 48 + 7,          # 与图形标光学中线对齐
        title_max=92, title_tracking_ratio=0.011,
        title_baseline=325,
        bullet_size=30, bullet_baselines=(417, 471, 525),
        bullet_dot_r=4.0, bullet_text_x=104,
    ),

    # 通用 OG 分享卡（更克制：只有标 + 主标 + 一行副标）
    "og_share": dict(
        w=1200, h=630, pad=80,
        tile=104, tile_y=160,
        title_max=76, title_tracking_ratio=0.011,
        title_baseline=385,
        sub_size=27, sub_baseline=465,
    ),

    # favicon 各档
    "favicon": dict(
        rounded_pad_ratio=0.03,   # 16/32/48：透明底 + 圆角标，四周留 3%
        fullbleed_fy_ratio=0.46,  # 180/192/512：满幅渐变（maskable 安全），FY 占 46%
    ),

    # 第二批：README 横幅（品牌主渐变铺底：青→深青 150deg）
    "readme_banner": dict(
        w=1280, h=320, pad=64,
        mark_size=192, mark_x=64, mark_y=64, mark_radius_ratio=0.22,
        fy_box_w=150, fy_x=312, fy_baseline=150,   # FY 几何字标（深青字）
        en_size=30, en_baseline=200,               # 英文 Find Yourself
        cn_size=28, cn_baseline=252,               # 中文标语（自动收敛字号）
        text_x=312,
    ),

    # 第二批：公众号次图（方形，logo-mark 居中，透明底）
    "wechat_thumb": dict(
        size=200, mark_radius_ratio=0.22,
    ),

    # 第二批：更新日志模板（深色氛围底，与 OG 图同构）
    "changelog": dict(
        w=1080, h=540, pad=80,
        title_max=72, title_tracking_ratio=0.01, title_baseline=128,
        divider_y=172,
        bullet_size=30, bullet_baselines=(252, 312, 372, 432),
        bullet_text_x=112, bullet_dot_r=4.0,
    ),
}


# =====================================================================
# 3. 基础绘制原语
# =====================================================================
def linear_gradient(size: tuple, c0: tuple, c1: tuple, angle_deg: float) -> Image.Image:
    """精确复现 CSS linear-gradient(<angle>deg, c0, c1)。

    CSS 角度 θ 的屏幕方向向量 = (sinθ, -cosθ)。先在 1px 高的渐变条上采样 t，
    再用 AFFINE 变换把「t 对 (x,y) 的线性映射」精确搬到目标尺寸
    （线性函数在双线性重采样下可被精确重建，因此无插值误差）。
    """
    w, h = int(size[0]), int(size[1])
    rad = math.radians(angle_deg)
    dx, dy = math.sin(rad), -math.cos(rad)
    proj = [0.0, dx, dy, dx + dy]           # 四个角点在方向向量上的投影
    pmin, pmax = min(proj), max(proj)
    span = (pmax - pmin) or 1.0

    band = 2048
    strip = Image.new("L", (band, 2))
    strip.putdata([round(255 * (i / (band - 1))) for i in range(band)] * 2)

    k = band - 1
    a = dx / (w * span) * k
    b = dy / (h * span) * k
    c = (-pmin / span) * k
    gray = strip.transform((w, h), Image.Transform.AFFINE, (a, b, c, 0.0, 0.0, 0.0),
                           resample=Image.Resampling.BILINEAR)

    luts = [[round(c0[ch] + (c1[ch] - c0[ch]) * (i / 255.0)) for i in range(256)] for ch in range(3)]
    return Image.merge("RGB", (gray.point(luts[0]), gray.point(luts[1]), gray.point(luts[2])))


def radial_alpha(size: tuple, cx: float, cy: float, rx: float, ry: float,
                 alpha: float, fade: float = 1.0, gamma: float = 1.0,
                 resolution: int = 8) -> Image.Image:
    """径向渐变的 alpha 掩膜。

    对应 CSS：radial-gradient(rx ry at cx cy, color 0%, transparent <fade*100>%)
      cx/cy 为 0..1 归一化圆心；rx/ry 为像素半径；alpha 为 0..1 峰值。
    低分辨率计算 + BICUBIC 放大（径向渐变足够平滑，肉眼无差）。
    """
    W, H = int(size[0]), int(size[1])
    sw, sh = max(2, W // resolution), max(2, H // resolution)
    m = Image.new("L", (sw, sh))
    px = m.load()
    for j in range(sh):
        v = (j + 0.5) / sh
        dy = (v - cy) / ry
        for i in range(sw):
            u = (i + 0.5) / sw
            d = math.hypot((u - cx) / rx, dy)
            t = 1.0 - d / fade
            px[i, j] = 0 if t <= 0.0 else (255 if t >= 1.0 else round(255 * alpha * (t ** gamma)))
    return m.resize((W, H), Image.Resampling.BICUBIC)


def dark_canvas(w: int, h: int, grid: bool = True) -> Image.Image:
    """深色底 + 双径向光晕 + 64px 细网格。与落地页 .bg / .bg::before / .bg::after 同构。"""
    canvas = Image.new("RGB", (w, h), BRAND["ink"])

    glows = [
        # 落地页：radial-gradient(900px 520px at 50% -8%, rgba(53,224,200,.14), transparent 62%)
        dict(cx=0.50, cy=-0.08, rx=900.0, ry=520.0, color=BRAND["accent"], alpha=0.14, fade=0.62),
        # 落地页：radial-gradient(760px 480px at 88% 8%, rgba(138,123,255,.10), transparent 60%)
        dict(cx=0.88, cy=0.08, rx=760.0, ry=480.0, color=BRAND["violet"], alpha=0.10, fade=0.60),
    ]
    for g in glows:
        a = radial_alpha((w, h), g["cx"], g["cy"], g["rx"], g["ry"], g["alpha"], g["fade"])
        layer = Image.new("RGBA", (w, h), g["color"] + (0,))
        layer.putalpha(a)
        canvas = Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB")

    if grid:
        cell, ga = 64, int(round(255 * 0.028))     # rgba(255,255,255,.028)
        gridmask = Image.new("L", (w, h), 0)
        gd = ImageDraw.Draw(gridmask)
        for x in range(0, w + 1, cell):
            gd.line([(x, 0), (x, h)], fill=ga, width=1)
        for y in range(0, h + 1, cell):
            gd.line([(0, y), (w, y)], fill=ga, width=1)

        # 落地页 mask-image: radial-gradient(circle at 50% 22%, #000 0%, transparent 72%)
        cxp, cyp = 0.5, 0.22
        px_, py_ = cxp * w, cyp * h
        R = max(math.hypot(px_ - x, py_ - y) for x in (0, w) for y in (0, h))
        fade = radial_alpha((w, h), cxp, cyp, R, R, 1.0, 0.72)
        gridmask = Image.composite(gridmask, Image.new("L", (w, h), 0), fade)

        layer = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        layer.putalpha(gridmask)
        canvas = Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB")

    return canvas


# ------------------------- 文本原语 -------------------------
# Pillow 的默认 anchor 是 "la"（y = 文本框顶），与设计稿直觉不符；
# 本脚本统一用 "?s"（y = 基线），与 SVG / CSS 语义一致。
def tracked_advance(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont,
                    tracking: float = 0.0) -> float:
    """考虑 letter-spacing 后的总推进宽度（末字不带尾随字距）。"""
    if not text:
        return 0.0
    return sum(draw.textlength(ch, font=f) for ch in text) + tracking * (len(text) - 1)


def ink_bbox(draw: ImageDraw.ImageDraw, xy: tuple, text: str, f: ImageFont.FreeTypeFont,
             tracking: float = 0.0, anchor: str = "ls") -> tuple:
    """已绘制文本的真实墨迹包围盒（考虑 tracking 与 anchor）。"""
    if not text:
        return (xy[0], xy[1], xy[0], xy[1])
    x, y = xy
    adv = tracked_advance(draw, text, f, tracking)
    if anchor[0] == "m":
        x -= adv / 2.0
    elif anchor[0] == "r":
        x -= adv

    lo = hi = top = bot = None
    cx = x
    for ch in text:
        bb = draw.textbbox((cx, y), ch, font=f, anchor="ls")
        lo = bb[0] if lo is None else lo
        hi = bb[2] if hi is None else max(hi, bb[2])
        top = bb[1] if top is None else min(top, bb[1])
        bot = bb[3] if bot is None else max(bot, bb[3])
        cx += draw.textlength(ch, font=f) + tracking
    return (lo, top, hi, bot)


def ink_width(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont,
              tracking: float = 0.0) -> float:
    bb = ink_bbox(draw, (0.0, 0.0), text, f, tracking)
    return bb[2] - bb[0]


def fit_font_size(draw: ImageDraw.ImageDraw, text: str, max_width: float, max_size: int,
                  tracking_ratio: float = 0.0, bold: bool = True, min_size: int = 8) -> int:
    """自动收敛字号：二分搜索「真实墨迹宽度」不超出 max_width 的最大字号（规格 4.3 要点 1）。"""
    lo, hi, best = min_size, int(max_size), min_size
    while lo <= hi:
        mid = (lo + hi) // 2
        f = font(mid, bold=bold)
        if ink_width(draw, text, f, -mid * tracking_ratio) <= max_width:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def draw_tracked(draw: ImageDraw.ImageDraw, xy: tuple, text: str, f: ImageFont.FreeTypeFont,
                 fill: tuple, tracking: float = 0.0, anchor: str = "ls") -> None:
    """Pillow 无原生 letter-spacing，逐字符累加 x 偏移模拟（规格 4.3 要点 2）。

    anchor 末位 's' 表示 y 为基线；'l'/'m'/'r' 为左/中/右对齐。
    """
    if tracking == 0.0 and anchor[0] == "l":
        draw.text(xy, text, font=f, fill=fill, anchor=anchor)
        return
    x, y = xy
    if anchor[0] == "m":
        x -= tracked_advance(draw, text, f, tracking) / 2.0
    elif anchor[0] == "r":
        x -= tracked_advance(draw, text, f, tracking)
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill, anchor="ls")
        x += draw.textlength(ch, font=f) + tracking


# =====================================================================
# 4. 图形标绘制
# =====================================================================
def draw_mark(size: int, *, radius_ratio: float, fy_ratio: float,
              inset_ratio: float = 0.0, ss: int = 4) -> Image.Image:
    """渲染 FY 图形标（青→深青渐变圆角方 + 深青 FY 几何字）。

    size         输出边长
    radius_ratio 圆角 = 图形标边长 × ratio（满幅时传 0）
    fy_ratio     FY 字宽占「图形标」边长的比例（与 SVG 一致为 0.6616）
    inset_ratio  图形标四周内缩比例（0 = 满幅；0.03 = 四周留 3%）
    """
    S = size * ss
    T = int(round(S * (1.0 - 2.0 * inset_ratio)))
    off = (S - T) // 2
    rr = int(round(T * radius_ratio))

    # 渐变只在图形标自身的 bbox 内铺（对应 CSS 渐变作用于元素盒，而非画布）
    grad = linear_gradient((T, T), BRAND["accent"], BRAND["accent_deep"], 150.0)

    mask = Image.new("L", (T, T), 0)
    md = ImageDraw.Draw(mask)
    if rr <= 0:
        md.rectangle([0, 0, T - 1, T - 1], fill=255)
    else:
        md.rounded_rectangle([0, 0, T - 1, T - 1], radius=rr, fill=255)

    tile = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    tile.paste(grad, (0, 0), mask)

    # FY 字标：几何多边形，与 logo-*.svg 的手写 <path> 同源
    s = (T * fy_ratio) / FY_BOX_W
    tx = (T - FY_BOX_W * s) / 2.0
    ty = (T - FY_BOX_H * s) / 2.0
    fymask = Image.new("L", (T, T), 0)
    fd = ImageDraw.Draw(fymask)
    for poly in (FY_F, FY_Y):
        fd.polygon([(tx + px * s, ty + py * s) for (px, py) in poly], fill=255)
    tile.paste(Image.new("RGBA", (T, T), BRAND["accent_ink"] + (255,)), (0, 0), fymask)

    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    canvas.alpha_composite(tile, (off, off))
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def paste_mark(canvas: Image.Image, size: int, xy: tuple, radius_ratio: float = 0.26,
               fy_ratio: float = 0.6616) -> None:
    """把一个图形标贴到已有画布（用于 OG 图左上 / 居中角标）。"""
    m = draw_mark(size, radius_ratio=radius_ratio, fy_ratio=fy_ratio)
    canvas.alpha_composite(m, (int(xy[0]), int(xy[1])))


def paint_fy(draw: ImageDraw.ImageDraw, box_w: float, x0: float, y0: float, fill: tuple) -> None:
    """把几何 FY 字标（与 SVG 同源）直接画到画布上。

    box_w  字标框宽度（字标框高 = box_w × 100/146，cap height = 100 格）
    x0,y0  字标框左上角
    fill   单色填充（如深青字 / 强调青）
    """
    s = box_w / FY_BOX_W
    for poly in (FY_F, FY_Y):
        draw.polygon([(x0 + px * s, y0 + py * s) for (px, py) in poly], fill=fill)


def draw_mark_inverted(size: int, *, radius_ratio: float = 0.26, fy_ratio: float = 0.6616,
                       ss: int = 4) -> Image.Image:
    """反相图形标：深青圆角方 + 强调青 FY。用于「青渐变铺底」上仍清晰可辨。"""
    S = size * ss
    rr = int(round(S * radius_ratio))
    bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bg)
    if rr <= 0:
        bd.rectangle([0, 0, S - 1, S - 1], fill=BRAND["ink"] + (255,))
    else:
        bd.rounded_rectangle([0, 0, S - 1, S - 1], radius=rr, fill=BRAND["ink"] + (255,))
    s = (S * fy_ratio) / FY_BOX_W
    tx = (S - FY_BOX_W * s) / 2.0
    ty = (S - FY_BOX_H * s) / 2.0
    fymask = Image.new("L", (S, S), 0)
    fd = ImageDraw.Draw(fymask)
    for poly in (FY_F, FY_Y):
        fd.polygon([(tx + px * s, ty + py * s) for (px, py) in poly], fill=255)
    bg.paste(Image.new("RGBA", (S, S), BRAND["accent"] + (255,)), (0, 0), fymask)
    return bg.resize((size, size), Image.Resampling.LANCZOS)


# =====================================================================
# 5. OG / 社交图渲染（返回 (图像, 版式校验块列表)）
# =====================================================================
def render_gh_social() -> tuple:
    p = PRESETS["gh_social"]
    w, h, pad = p["w"], p["h"], p["pad"]
    blocks = []

    canvas = dark_canvas(w, h).convert("RGBA")
    paste_mark(canvas, p["tile"], (pad, p["tile_y"]))
    d = ImageDraw.Draw(canvas)
    blocks.append(("图形标", (pad, p["tile_y"], pad + p["tile"], p["tile_y"] + p["tile"])))

    # 右上：一行小字签名
    fs = font(p["sig_size"], bold=False)
    draw_tracked(d, (w - pad, p["sig_baseline"]), TEXTS["signature"], fs, BRAND["muted"], anchor="rs")
    blocks.append(("签名", ink_bbox(d, (w - pad, p["sig_baseline"]), TEXTS["signature"], fs, anchor="rs")))

    # 主标：自动收敛字号
    title_size = fit_font_size(d, TEXTS["positioning"], w - 2 * pad, p["title_max"],
                               tracking_ratio=p["title_tracking_ratio"], bold=True)
    ft = font(title_size, bold=True)
    trk = -title_size * p["title_tracking_ratio"]
    draw_tracked(d, (pad, p["title_baseline"]), TEXTS["positioning"], ft, BRAND["text"], tracking=trk)
    blocks.append((f"主标({title_size}px)",
                   ink_bbox(d, (pad, p["title_baseline"]), TEXTS["positioning"], ft, trk)))

    # 三行卖点（青点 + muted 文字）
    fb = font(p["bullet_size"], bold=False)
    for i, line in enumerate(TEXTS["selling_points"]):
        by = p["bullet_baselines"][i]
        cy = by - int(p["bullet_size"] * 0.36)
        d.ellipse([pad + 4, cy - p["bullet_dot_r"], pad + 4 + 2 * p["bullet_dot_r"], cy + p["bullet_dot_r"]],
                  fill=BRAND["accent"])
        draw_tracked(d, (p["bullet_text_x"], by), line, fb, BRAND["muted"])
        bb = ink_bbox(d, (p["bullet_text_x"], by), line, fb)
        blocks.append((f"卖点{i + 1}", (min(bb[0], pad + 4), bb[1], bb[2], bb[3])))

    return canvas.convert("RGB"), blocks


def render_og_share() -> tuple:
    p = PRESETS["og_share"]
    w, h, pad = p["w"], p["h"], p["pad"]
    blocks = []

    canvas = dark_canvas(w, h).convert("RGBA")
    paste_mark(canvas, p["tile"], ((w - p["tile"]) // 2, p["tile_y"]))
    d = ImageDraw.Draw(canvas)
    blocks.append(("图形标", ((w - p["tile"]) // 2, p["tile_y"],
                              (w + p["tile"]) // 2, p["tile_y"] + p["tile"])))

    # 主标：居中，自动收敛字号
    title_size = fit_font_size(d, TEXTS["positioning"], w - 2 * pad, p["title_max"],
                               tracking_ratio=p["title_tracking_ratio"], bold=True)
    ft = font(title_size, bold=True)
    trk = -title_size * p["title_tracking_ratio"]
    draw_tracked(d, (w / 2, p["title_baseline"]), TEXTS["positioning"], ft, BRAND["text"],
                 tracking=trk, anchor="ms")
    blocks.append((f"主标({title_size}px)",
                   ink_bbox(d, (w / 2, p["title_baseline"]), TEXTS["positioning"], ft, trk, anchor="ms")))

    # 副标：居中
    fs = font(p["sub_size"], bold=False)
    draw_tracked(d, (w / 2, p["sub_baseline"]), TEXTS["subtitle"], fs, BRAND["muted"], anchor="ms")
    blocks.append(("副标", ink_bbox(d, (w / 2, p["sub_baseline"]), TEXTS["subtitle"], fs, anchor="ms")))

    return canvas.convert("RGB"), blocks


# =====================================================================
# 5.5 第二批：3 个不依赖安装包的资产
# =====================================================================
def render_readme_banner() -> tuple:
    """README 横幅：品牌主渐变铺底 + 左侧反相图形标 + 右侧 FY 几何字标/Orbit · 星轨/新副标。"""
    p = PRESETS["readme_banner"]
    w, h, pad = p["w"], p["h"], p["pad"]
    blocks = []

    # 背景 = 品牌主渐变 linear-gradient(150deg,#35E0C8,#1FA894)
    canvas = linear_gradient((w, h), BRAND["accent"], BRAND["accent_deep"], 150.0).convert("RGBA")

    # 左侧图形标（反相：深青方 + 强调青 FY，在青渐变上仍清晰）
    mark = draw_mark_inverted(p["mark_size"], radius_ratio=p["mark_radius_ratio"], fy_ratio=0.6616)
    canvas.alpha_composite(mark, (p["mark_x"], p["mark_y"]))
    blocks.append(("图形标", (p["mark_x"], p["mark_y"],
                              p["mark_x"] + p["mark_size"], p["mark_y"] + p["mark_size"])))

    d = ImageDraw.Draw(canvas)
    ink = BRAND["accent_ink"]   # 深青字 #04211D：在强调青上对比 10.2:1 (AAA)

    # 右侧 FY 几何字标（深青字）
    box_h = p["fy_box_w"] * FY_BOX_H / FY_BOX_W
    paint_fy(d, p["fy_box_w"], p["fy_x"], p["fy_baseline"] - box_h, ink)
    blocks.append(("FY字标", (p["fy_x"], p["fy_baseline"] - box_h,
                              p["fy_x"] + p["fy_box_w"], p["fy_baseline"])))

    # 字标名（文本侧）：Orbit · 星轨（与 FY 几何字标合成 FY Orbit · 星轨）
    fe = font(p["en_size"], bold=False)
    draw_tracked(d, (p["fy_x"], p["en_baseline"]), "Orbit · 星轨", fe, ink)
    blocks.append(("字标名", ink_bbox(d, (p["fy_x"], p["en_baseline"]), "Orbit · 星轨", fe)))

    # 副标（自动收敛字号防溢出）
    cn_size = fit_font_size(d, TEXTS["subtitle"], w - p["text_x"] - pad,
                            p["cn_size"], tracking_ratio=0.0, bold=False)
    fc = font(cn_size, bold=False)
    draw_tracked(d, (p["fy_x"], p["cn_baseline"]), TEXTS["subtitle"], fc, ink)
    blocks.append(("副标", ink_bbox(d, (p["fy_x"], p["cn_baseline"]), TEXTS["subtitle"], fc)))

    return canvas.convert("RGB"), blocks


def render_wechat_thumb() -> tuple:
    """公众号次图：200×200 方形，logo-mark 居中，透明底（作文章缩略图）。"""
    p = PRESETS["wechat_thumb"]
    size = p["size"]
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mark = draw_mark(size, radius_ratio=p["mark_radius_ratio"], fy_ratio=0.6616, ss=4)
    canvas.alpha_composite(mark, (0, 0))
    blocks = [("图形标", (0, 0, size, size))]
    return canvas, blocks


def render_changelog() -> tuple:
    """更新日志模板：深色氛围底，标题「更新日志 vX.X」+ 4 行占位变更条目（与 OG 同构）。"""
    p = PRESETS["changelog"]
    w, h, pad = p["w"], p["h"], p["pad"]
    blocks = []

    canvas = dark_canvas(w, h).convert("RGBA")
    d = ImageDraw.Draw(canvas)

    # 标题：更新日志（白）+ vX.X（强调青），整体居中
    label, ver = "更新日志", "vX.X"
    title_size = fit_font_size(d, label + " " + ver, w - 2 * pad, p["title_max"],
                               tracking_ratio=p["title_tracking_ratio"], bold=True)
    ft = font(title_size, bold=True)
    trk = -title_size * p["title_tracking_ratio"]
    wlabel = ink_width(d, label, ft, trk)
    wver = ink_width(d, ver, ft, trk)
    gap = int(title_size * 0.18)
    total = wlabel + gap + wver
    x0 = (w - total) / 2.0
    draw_tracked(d, (x0, p["title_baseline"]), label, ft, BRAND["text"], tracking=trk)
    draw_tracked(d, (x0 + wlabel + gap, p["title_baseline"]), ver, ft, BRAND["accent"], tracking=trk)
    blocks.append(("标题", ink_bbox(d, (x0, p["title_baseline"]), label + ver, ft, trk)))

    # 标题下方分割线（muted 线色）
    d.line([(pad, p["divider_y"]), (w - pad, p["divider_y"])],
           fill=BRAND["muted_2"], width=2)
    blocks.append(("分割线", (pad, p["divider_y"] - 1, w - pad, p["divider_y"] + 1)))

    # 4 行占位变更条目（青点 + muted 文字）
    lines = [
        "· 新增：____（占位示例，发布前替换为真实条目）",
        "· 优化：____（占位示例，发布前替换为真实条目）",
        "· 修复：____（占位示例，发布前替换为真实条目）",
        "· 已知问题：____（占位示例，发布前替换为真实条目）",
    ]
    fb = font(p["bullet_size"], bold=False)
    for i, line in enumerate(lines):
        by = p["bullet_baselines"][i]
        cy = by - int(p["bullet_size"] * 0.36)
        d.ellipse([pad + 4, cy - p["bullet_dot_r"], pad + 4 + 2 * p["bullet_dot_r"], cy + p["bullet_dot_r"]],
                  fill=BRAND["accent"])
        draw_tracked(d, (p["bullet_text_x"], by), line, fb, BRAND["muted"])
        bb = ink_bbox(d, (p["bullet_text_x"], by), line, fb)
        blocks.append((f"条目{i + 1}", (min(bb[0], pad + 4), bb[1], bb[2], bb[3])))

    return canvas.convert("RGB"), blocks


# =====================================================================
# 6. favicon 渲染
# =====================================================================
def render_favicons() -> list:
    FAVICON_DIR.mkdir(parents=True, exist_ok=True)
    p = PRESETS["favicon"]
    m = PRESETS["mark"]
    out = []

    # 16 / 32 / 48：透明底 + 圆角标（浏览器标签页）
    rounded = draw_mark(m["size"], radius_ratio=m["radius_ratio"], fy_ratio=m["fy_ratio"],
                        inset_ratio=p["rounded_pad_ratio"], ss=m["ss"])
    for s in (16, 32, 48):
        img = rounded.resize((s, s), Image.Resampling.LANCZOS)
        fp = FAVICON_DIR / f"favicon-{s}.png"
        img.save(fp, "PNG", optimize=True)
        out.append(fp)

    # 180 / 192 / 512：满幅渐变（iOS 自行切圆角 / Android maskable 安全区）
    full = draw_mark(m["size"], radius_ratio=0.0, fy_ratio=p["fullbleed_fy_ratio"],
                     inset_ratio=0.0, ss=m["ss"])
    for s, name in ((180, "apple-touch-icon-180.png"), (192, "favicon-192.png"), (512, "favicon-512.png")):
        fp = FAVICON_DIR / name
        full.resize((s, s), Image.Resampling.LANCZOS).convert("RGB").save(fp, "PNG", optimize=True)
        out.append(fp)

    # 多尺寸 ico（16/32/48 三档打包）
    ico_path = FAVICON_DIR / "favicon.ico"
    rounded.resize((256, 256), Image.Resampling.LANCZOS).save(
        ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    out.append(ico_path)

    return out


# =====================================================================
# 7. 校验：色板审计 / 版式越界 / 缺字
# =====================================================================
def scan_colors(img: Image.Image, tol: int = 4) -> set:
    """统计图中占比 >0.05% 的主色（量化后），用于色板合规审计。"""
    q = img.convert("RGB").quantize(colors=48, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    found = set()
    for cnt, idx in q.getcolors():
        if cnt < img.width * img.height * 0.0005:
            continue
        r, g, b = pal[idx * 3: idx * 3 + 3]
        found.add((round(r / tol) * tol, round(g / tol) * tol, round(b / tol) * tol))
    return found


def audit_palette(img: Image.Image, tol: float = 22.0):
    """色板合规审计（凸组合模型）。

    成品图全部由「品牌色 + alpha 叠加」构成，因此任意像素必然落在品牌色板的
    凸包内 —— 必能表示为两个色板色之间的线性插值。凡无法在 tol 内用任何两个
    色板色解释的颜色，即为越界（自造色）。
    """
    pal = sorted(PALETTE)
    found = scan_colors(img)
    bad = []
    for c in found:
        if any(max(abs(c[i] - p[i]) for i in range(3)) <= tol for p in pal):
            continue
        ok = False
        for i, p in enumerate(pal):
            for q in pal[i + 1:]:
                for k in range(33):
                    t = k / 32.0
                    if max(abs(c[j] - (p[j] + (q[j] - p[j]) * t)) for j in range(3)) <= tol:
                        ok = True
                        break
                if ok:
                    break
            if ok:
                break
        if not ok:
            bad.append(c)
    return len(found), bad


def audit_layout(name: str, blocks: list, w: int, h: int, pad: int) -> list:
    """版式校验：横向越界 / 画布越界 / 同一列内的垂直重叠。

    只有「横向也相交」的两块才比较纵向 —— 同一行左右并排（如 图形标 与 右上签名）
    本来就该纵向重叠，不算问题。
    """
    warn = []
    for label, bb in blocks:
        if bb[0] < pad - 1 or bb[2] > w - pad + 1:
            warn.append(f"{label} 横向越界 bbox={_r(bb)}（安全区 x∈[{pad},{w - pad}]）")
        if bb[1] < 0 or bb[3] > h:
            warn.append(f"{label} 超出画布 bbox={_r(bb)}")
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            a, b = blocks[i], blocks[j]
            hx = min(a[1][2], b[1][2]) - max(a[1][0], b[1][0])      # 横向交叠
            vy = min(a[1][3], b[1][3]) - max(a[1][1], b[1][1])      # 纵向交叠
            if hx > 1 and vy > 1:
                warn.append(f"{a[0]} 与 {b[0]} 重叠（横向 {hx:.0f}px × 纵向 {vy:.0f}px）")
    if warn:
        print(f"  ⚠ {name} 版式告警：")
        for x in warn:
            print(f"      - {x}")
    else:
        print(f"  {name} 版式：全部块落在安全区内，且无相互重叠 ✅")
    return warn


def _r(bb):
    return tuple(round(v, 1) for v in bb)


def audit_glyphs() -> None:
    """缺字检查：把每个字形的墨迹盒与 .notdef / U+FFFD 的墨迹盒比对，相同即判定缺字。

    比「墨迹过小」更可靠 —— 「一」「二」「十」等笔画少的字不会再被误判。
    """
    vals = [TEXTS["positioning"], TEXTS["positioning_unified"], TEXTS["subtitle"], TEXTS["signature"],
            *TEXTS["selling_points"], TEXTS["positioning_l1"], TEXTS["positioning_l2"]]
    chars = sorted({ch for t in vals for ch in t if ord(ch) > 0x2000})
    f = font(100, bold=True)
    d = ImageDraw.Draw(Image.new("L", (8, 8)))
    notdef = {
        d.textbbox((0, 0), "\uE000", font=f, anchor="ls"),
        d.textbbox((0, 0), "\uFFFD", font=f, anchor="ls"),
    }
    bad = [ch for ch in chars if d.textbbox((0, 0), ch, font=f, anchor="ls") in notdef]
    print(f"缺字检查：共 {len(chars)} 个非 ASCII 字形，"
          f"{'全部正常渲染 ✅' if not bad else f'⚠ 缺字 {bad}'}")
    print(f"          字符集样本：{''.join(chars[:28])}{' …' if len(chars) > 28 else ''}")


# =====================================================================
# 8. 主流程
# =====================================================================
def main() -> int:
    print("=" * 78)
    print("FY 品牌基础资产渲染")
    print("=" * 78)
    print(f"Python      : {sys.version.split()[0]}  (Pillow {Image.__version__})")
    print(f"输出目录    : {HERE}")
    print(f"粗体字体    : {FONT_BOLD_NAME}  <- {FONT_BOLD_PATH}")
    print(f"常规字体    : {FONT_REG_NAME}  <- {FONT_REG_PATH}")
    print(f"变量文本层  : {TEXTS['positioning']}  /  {TEXTS['subtitle']}")
    print("-" * 78)
    audit_glyphs()
    print("-" * 78)

    made = []
    made += render_favicons()

    img, blocks = render_gh_social()
    fp = HERE / "og-github-1280x640.png"
    img.save(fp, "PNG", optimize=True)
    made.append(fp)
    p = PRESETS["gh_social"]
    gh_warn = audit_layout("og-github-1280x640", blocks, p["w"], p["h"], p["pad"])

    img, blocks = render_og_share()
    fp = HERE / "og-share-1200x630.png"
    img.save(fp, "PNG", optimize=True)
    made.append(fp)
    p = PRESETS["og_share"]
    og_warn = audit_layout("og-share-1200x630", blocks, p["w"], p["h"], p["pad"])

    # ---- 第二批：3 个不依赖安装包的资产 ----
    img, blocks = render_readme_banner()
    fp = HERE / "readme-banner-1280x320.png"
    img.save(fp, "PNG", optimize=True)
    made.append(fp)
    p = PRESETS["readme_banner"]
    rb_warn = audit_layout("readme-banner-1280x320", blocks, p["w"], p["h"], p["pad"])

    img, blocks = render_wechat_thumb()
    fp = HERE / "wechat-thumb-200x200.png"
    img.save(fp, "PNG", optimize=True)
    made.append(fp)
    p = PRESETS["wechat_thumb"]
    wt_warn = audit_layout("wechat-thumb-200x200", blocks, p["size"], p["size"], 0)

    img, blocks = render_changelog()
    fp = HERE / "changelog-template-1080x540.png"
    img.save(fp, "PNG", optimize=True)
    made.append(fp)
    p = PRESETS["changelog"]
    cl_warn = audit_layout("changelog-template-1080x540", blocks, p["w"], p["h"], p["pad"])

    print("-" * 78)
    print(f"{'文件':<28}{'实测尺寸':>14}{'字节数':>12}   格式")
    print("-" * 78)
    for f in made:
        if f.suffix.lower() == ".ico":
            with Image.open(f) as im:
                sizes = sorted(getattr(im, "info", {}).get("sizes", set()))
                print(f"{f.name:<28}{str(sizes):>14}{os.path.getsize(f):>12,}   ICO")
        else:
            with Image.open(f) as im:
                print(f"{f.name:<28}{f'{im.width}x{im.height}':>14}{os.path.getsize(f):>12,}   {im.mode}")

    print("-" * 78)
    for name in ("og-github-1280x640.png", "og-share-1200x630.png",
                 "readme-banner-1280x320.png", "wechat-thumb-200x200.png",
                 "changelog-template-1080x540.png"):
        with Image.open(HERE / name) as im:
            n, bad = audit_palette(im)
        verdict = "全部落在品牌色板（含 alpha 叠加与渐变插值）✅" if not bad else f"⚠ 越界 {bad[:6]}"
        print(f"色板审计 {name}: 主色 {n} 个，{verdict}")

    print("-" * 78)
    total_warn = len(gh_warn) + len(og_warn) + len(rb_warn) + len(wt_warn) + len(cl_warn)
    print(f"完成：共 {len(made)} 个文件 → {HERE}")
    print(f"版式告警合计：{total_warn} 条 " + ("✅" if total_warn == 0 else "⚠ 请按上方明细调整 PRESETS"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
