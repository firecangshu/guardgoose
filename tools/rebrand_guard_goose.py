"""品牌更名脚本：Watch Goose → Guard Goose。
盾牌本体图（无文字）直接复用，重新合成带文字的完整版 LOGO：
护院鹅（亮黄+深蓝描边）+ GUARD GOOSE（深蓝），风格对齐原版。"""
from PIL import Image, ImageDraw, ImageFont

BADGE_SRC = r"e:\小有可为\waveguard\h5\public\watchgoose-badge.png"
FULL_DST = r"e:\小有可为\waveguard\h5\public\guardgoose.png"
BADGE_DST = r"e:\小有可为\waveguard\h5\public\guardgoose-badge.png"

CN_FONT = r"C:\Windows\Fonts\msyhbd.ttc"   # 微软雅黑 Bold
EN_FONT = r"C:\Windows\Fonts\arialbd.ttf"  # Arial Bold

YELLOW = (255, 210, 40, 255)
NAVY = (27, 42, 84, 255)

badge = Image.open(BADGE_SRC).convert("RGBA")
bw, bh = badge.size

# 画布：盾牌居中，下方留文字区
W = bw + 60
cn_size = 96
en_size = 40
cn_font = ImageFont.truetype(CN_FONT, cn_size, index=0)
en_font = ImageFont.truetype(EN_FONT, en_size)

H = bh + 200
canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
canvas.paste(badge, ((W - bw) // 2, 0), badge)

draw = ImageDraw.Draw(canvas)


def draw_outlined_text(draw, xy, text, font, fill, outline, width=5):
    """描边文字：八方向偏移画描边色，再盖主色。"""
    x, y = xy
    for dx in range(-width, width + 1):
        for dy in range(-width, width + 1):
            if dx * dx + dy * dy <= width * width:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text(xy, text, font=font, fill=fill)


# 护院鹅：亮黄 + 深蓝描边 + 白色投影，居中
cn_text = "护院鹅"
cn_bbox = draw.textbbox((0, 0), cn_text, font=cn_font)
cn_w = cn_bbox[2] - cn_bbox[0]
cn_x = (W - cn_w) // 2
cn_y = bh + 16
draw.text((cn_x + 4, cn_y + 5), cn_text, font=cn_font, fill=(255, 255, 255, 220))  # 白色投影
draw_outlined_text(draw, (cn_x, cn_y), cn_text, cn_font, YELLOW, NAVY, width=5)

# GUARD GOOSE：深蓝，居中
en_text = "GUARD GOOSE"
en_bbox = draw.textbbox((0, 0), en_text, font=en_font)
en_w = en_bbox[2] - en_bbox[0]
en_x = (W - en_w) // 2
en_y = cn_y + cn_size + 18
draw.text((en_x + 2, en_y + 3), en_text, font=en_font, fill=(255, 255, 255, 200))  # 白色投影
draw.text((en_x, en_y), en_text, font=en_font, fill=NAVY)

# 紧贴裁剪
bbox = canvas.getbbox()
canvas = canvas.crop(bbox)
canvas.save(FULL_DST)
print(f"完整版 {canvas.size[0]}x{canvas.size[1]} → {FULL_DST}")

# 盾牌版改名复用
badge.save(BADGE_DST)
print(f"盾牌版 {bw}x{bh} → {BADGE_DST}")
