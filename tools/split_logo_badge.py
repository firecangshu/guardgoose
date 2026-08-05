"""盾牌版分离（比例切法）：在 68%~82% 高度区间选"不透明像素最少"的行作切线，
把下方文字切掉；若切线下方仍有残留，由紧贴裁剪兜底。"""
from PIL import Image

FULL = r"e:\小有可为\waveguard\h5\public\watchgoose.png"
BADGE = r"e:\小有可为\waveguard\h5\public\watchgoose-badge.png"

im = Image.open(FULL).convert("RGBA")
w, h = im.size
alpha = im.getchannel("A")

row_counts = [sum(1 for x in range(w) if alpha.getpixel((x, y)) > 16) for y in range(h)]

lo, hi = int(h * 0.68), int(h * 0.82)
cut = min(range(lo, hi), key=lambda y: row_counts[y])
print(f"切线行 {cut}/{h}，该行不透明像素 {row_counts[cut]}")

badge = im.crop((0, 0, w, cut))
bbox = badge.getbbox()
if bbox:
    badge = badge.crop(bbox)
badge.save(BADGE)
print(f"盾牌版 {badge.size[0]}x{badge.size[1]} → {BADGE}")
