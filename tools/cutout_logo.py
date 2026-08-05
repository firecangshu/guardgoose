"""抠图脚本：把护院鹅(Watch Goose) LOGO 的灰色背景去除，输出透明 PNG。
从图片四角做洪水填充（容差内同色即背景），只删与边角连通的区域，
盾牌内部的灰白栅栏不会被误删。输出裁剪到内容紧贴边界。"""
from collections import deque

from PIL import Image

SRC = r"C:\Users\User\AppData\Roaming\Qoder\SharedClientCache\cache\images\d414a4eb\d3967a6b5d287d03418118627f059a1b-ade8cdcc.jpg"
DST = r"e:\小有可为\waveguard\h5\public\watchgoose.png"
TOL = 34  # 单通道容差（RGB 距离和 <= TOL*3 视为背景）

im = Image.open(SRC).convert("RGB")
w, h = im.size
px = im.load()
seed = px[0, 0]
print(f"源图 {w}x{h}，背景基准色 {seed}")


def is_bg(c: tuple) -> bool:
    return abs(c[0] - seed[0]) + abs(c[1] - seed[1]) + abs(c[2] - seed[2]) <= TOL * 3


visited = bytearray(w * h)
bg = bytearray(w * h)
dq = deque()
for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
    if is_bg(px[x, y]) and not visited[y * w + x]:
        visited[y * w + x] = 1
        dq.append((x, y))

while dq:
    x, y = dq.popleft()
    bg[y * w + x] = 1
    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
        if 0 <= nx < w and 0 <= ny < h and not visited[ny * w + nx]:
            if is_bg(px[nx, ny]):
                visited[ny * w + nx] = 1
                dq.append((nx, ny))

rgba = im.convert("RGBA")
apx = rgba.load()
removed = 0
for y in range(h):
    row = y * w
    for x in range(w):
        if bg[row + x]:
            apx[x, y] = (255, 255, 255, 0)
            removed += 1

bbox = rgba.getbbox()
out = rgba.crop(bbox)
out.save(DST)
print(f"去除背景像素 {removed}，输出 {out.size[0]}x{out.size[1]} → {DST}")
