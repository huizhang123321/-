# -*- coding: utf-8 -*-
"""生成 KeiHub 的图标（深色圆角底 + 蓝色小鱼）。

    python make_icon.py

输出 ``assets/keihub.ico``，包含 16/24/32/48/64/128/256 七种尺寸，
打包脚本（build_*.ps1）会用它当 exe 图标。

内部按 1024px 绘制再降采样，避免小尺寸锯齿。
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "assets" / "keihub.ico"
S = 1024
RADIUS = 232
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

BG_TOP = (24, 34, 54)
BG_BOTTOM = (13, 18, 28)
FISH = (79, 155, 255, 255)
FISH_DARK = (52, 120, 220, 255)
FISH_LIGHT = (126, 188, 255, 255)
INK = (16, 22, 34, 255)


def _background() -> Image.Image:
    bg = Image.new("RGBA", (S, S))
    draw = ImageDraw.Draw(bg)
    for y in range(S):
        t = y / (S - 1)
        color = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (S, y)], fill=color + (255,))

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=RADIUS, fill=255)

    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)
    return out


def build() -> Image.Image:
    img = _background()
    d = ImageDraw.Draw(img)

    # 气泡
    for cx, cy, r, alpha in [(268, 232, 20, 70), (310, 178, 11, 50), (775, 262, 15, 45)]:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(120, 170, 255, alpha))

    # 鱼单独画一层，最后整体放大，保证小尺寸（16/32px）也看得清
    fish = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    f = ImageDraw.Draw(fish)

    cx, cy = 500, 545        # 鱼身中心
    bw, bh = 250, 152        # 半宽 / 半高

    # 尾巴（贴在身体右缘向外张开）
    tail_x = cx + bw - 18
    f.polygon([(tail_x, cy - 92), (tail_x + 150, cy - 150),
               (tail_x + 150, cy + 150), (tail_x, cy + 92)], fill=FISH_DARK)
    # 背鳍 / 腹鳍
    f.polygon([(cx - 40, cy - bh + 18), (cx + 70, cy - bh - 88), (cx + 120, cy - bh + 30)],
              fill=FISH_DARK)
    f.polygon([(cx - 30, cy + bh - 18), (cx + 30, cy + bh + 70), (cx + 90, cy + bh - 10)],
              fill=FISH_DARK)
    # 鱼身 + 高光
    f.ellipse((cx - bw, cy - bh, cx + bw, cy + bh), fill=FISH)
    f.ellipse((cx - bw + 40, cy - 70, cx + 60, cy + 40), fill=FISH_LIGHT)
    # 眼睛
    ex, ey = cx - bw + 92, cy - 18
    f.ellipse((ex - 38, ey - 38, ex + 38, ey + 38), fill=(255, 255, 255, 255))
    f.ellipse((ex - 20, ey - 20, ex + 20, ey + 20), fill=INK)
    # 腮线
    f.arc((cx - bw + 10, cy - 70, cx - bw + 130, cy + 80),
          start=280, end=80, fill=(45, 110, 210, 255), width=9)

    scale = 1.18
    big = fish.resize((int(S * scale), int(S * scale)), Image.LANCZOS)
    off = (big.width - S) // 2
    img.alpha_composite(big.crop((off, off, off + S, off + S)))
    return img


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = build()
    img.save(OUT, format="ICO", sizes=SIZES)
    img.resize((256, 256), Image.LANCZOS).save(OUT.with_name("keihub-256.png"))
    print(OUT)


if __name__ == "__main__":
    main()
