"""Dump sprites (OAM) from a Mesen savestate and render them to PNG.

Usage: python tools/oamdump.py state.mss out.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import mss  # noqa: E402
from png import write_png  # noqa: E402


def val(st, name):
    k = st.find(name.encode() + b"\x00")
    if k < 0:
        return None
    p = k + len(name) + 1
    n = int.from_bytes(st[p:p + 4], "little")
    return int.from_bytes(st[p + 4:p + 4 + n], "little")


def sprites(st):
    oam = mss.field(st, "ppu.oamRam", 544)
    base = val(st, "ppu.oamBaseAddress")          # word address
    off = val(st, "ppu.oamAddressOffset")         # words
    mode = val(st, "ppu.oamMode")
    sizes = {0: (8, 16), 1: (8, 32), 2: (8, 64), 3: (16, 32), 4: (16, 64), 5: (32, 64)}[mode]
    out = []
    for i in range(128):
        x, y, t, a = oam[i * 4:i * 4 + 4]
        hi = (oam[512 + i // 4] >> ((i % 4) * 2)) & 3
        x |= (hi & 1) << 8
        if x >= 256:
            x -= 512
        size = sizes[(hi >> 1) & 1]
        tile = t | ((a & 1) << 8)
        out.append(dict(i=i, x=x, y=y, tile=tile, pal=(a >> 1) & 7, prio=(a >> 4) & 3,
                        hflip=bool(a & 0x40), vflip=bool(a & 0x80), size=size))
    return out, base, off


def tile_addr(tile, base, off):
    """Byte address in VRAM of an 8x8 4bpp OBJ tile."""
    w = base + (tile & 0xFF) * 16 + (off if tile & 0x100 else 0)  # words
    return (w * 2) & 0xFFFF


def render(st, path):
    vram = mss.field(st, "ppu.vram", 0x10000)
    cg = mss.field(st, "ppu.cgram", 512)
    sp, base, off = sprites(st)
    W, H = 256, 240
    img = [[0] * W for _ in range(H)]
    pal = [(0, 0, 0)]
    colors = {}
    for s in reversed(sp):
        if s["y"] >= 224 and s["y"] < 240:
            continue
        n = s["size"] // 8
        for ty in range(n):
            for tx in range(n):
                t = (s["tile"] & 0x10F) + ((s["tile"] & 0xF0) + ty * 16 & 0xF0) + tx
                t = (s["tile"] & 0x100) | (((s["tile"] >> 4) + ty) & 0xF) << 4 | ((s["tile"] + tx) & 0xF)
                a = tile_addr(t, base, off)
                for py in range(8):
                    b0, b1 = vram[a + py * 2], vram[a + py * 2 + 1]
                    b2, b3 = vram[a + 16 + py * 2], vram[a + 16 + py * 2 + 1]
                    for px in range(8):
                        bit = 7 - px
                        c = ((b0 >> bit) & 1) | ((b1 >> bit) & 1) << 1 | ((b2 >> bit) & 1) << 2 | ((b3 >> bit) & 1) << 3
                        if not c:
                            continue
                        X = tx * 8 + px
                        Y = ty * 8 + py
                        if s["hflip"]:
                            X = n * 8 - 1 - X
                        if s["vflip"]:
                            Y = n * 8 - 1 - Y
                        X += s["x"]
                        Y = (Y + s["y"]) & 0xFF
                        if 0 <= X < W and 0 <= Y < H:
                            ci = 128 + s["pal"] * 16 + c
                            v = cg[ci * 2] | cg[ci * 2 + 1] << 8
                            rgb = ((v & 31) << 3, ((v >> 5) & 31) << 3, ((v >> 10) & 31) << 3)
                            if rgb not in colors:
                                colors[rgb] = len(pal)
                                pal.append(rgb)
                            img[Y][X] = colors[rgb] % 256
    write_png(path, W, H, img, (pal + [(0, 0, 0)] * 256)[:256])
    return sp, base, off


if __name__ == "__main__":
    shot, st = mss.load(sys.argv[1])
    sp, base, off = render(st, sys.argv[2])
    vis = [s for s in sp if not (224 <= s["y"] < 240) and -64 < s["x"] < 256]
    print("base %04X off %04X visible sprites %d" % (base, off, len(vis)))
    for s in vis:
        print("#%3d x=%4d y=%3d tile=%03X pal=%d size=%d%s%s" % (
            s["i"], s["x"], s["y"], s["tile"], s["pal"], s["size"],
            " H" if s["hflip"] else "", " V" if s["vflip"] else ""))
