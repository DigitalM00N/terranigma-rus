"""Render script segments as dialogue-window pages (PNG) using the game font
plus the new Cyrillic glyphs.  Advance = 12 px per glyph, 18 glyphs per line,
4 lines per window (default window).

Usage:  python tools/preview.py <original.sfc> <script_file> <segment_id> [out.png]
        python tools/preview.py <original.sfc> <script_file> --all <out_dir>   (first 40)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import cyrfont  # noqa: E402
from fontview import glyph  # noqa: E402
from insert import read_script  # noqa: E402
from png import write_png  # noqa: E402
from tscript import RUS, RUS2  # noqa: E402

ADV, LINE_H, COLS, ROWS = 12, 16, 18, 4
PAL = [(255, 255, 255), (248, 248, 248), (96, 112, 200), (24, 40, 96), (255, 80, 80)]


def glyph_for(rom, ch):
    if ch in RUS2:
        return glyph(rom, cyrfont.FONT_BASE + (RUS2[ch] & 0x1FF) * 64)
    b = RUS.get(ch)
    if b is None:
        return None
    return glyph(rom, cyrfont.FONT_BASE + b * 64)


def pages_of(body):
    body = re.sub(r"\[(name|str|str2|str3):[^\]]*\]", "######", body)
    body = body.replace("…", "...")
    pages, cur = [], []
    for part in re.split(r"(\[wait\]\n?|\[[^\]]*\])", body):
        if not part:
            continue
        if part.startswith("[wait]"):
            pages.append(cur)
            cur = []
            continue
        if part.startswith("["):
            continue
        cur.append(part)
    pages.append(cur)
    out = []
    for p in pages:
        lines = "".join(p).split("\n")
        for i in range(0, max(1, len(lines)), ROWS):
            chunk = lines[i:i + ROWS]
            if any(l for l in chunk):
                out.append(chunk)
    return out


def render(rom, body, path):
    pages = pages_of(body)
    pad = 8
    W = COLS * ADV + 2 * pad + 4
    PH = ROWS * LINE_H + 2 * pad
    H = max(1, len(pages)) * (PH + 6)
    img = [[0] * W for _ in range(H)]
    for pi, lines in enumerate(pages):
        y0 = pi * (PH + 6)
        for y in range(PH):
            for x in range(W):
                img[y0 + y][x] = 3
        for li, line in enumerate(lines):
            for ci, ch in enumerate(line):
                g = glyph_for(rom, ch)
                gx = pad + ci * ADV
                gy = y0 + pad + li * LINE_H
                if g is None:
                    for yy in range(12):
                        for xx in range(8):
                            if gx + xx < W:
                                img[gy + yy][gx + xx] = 4
                    continue
                for yy in range(16):
                    for xx in range(16):
                        v = g[yy][xx]
                        if v in (1, 2) and gx + xx < W:
                            img[gy + yy][gx + xx] = v
            if len(line) > COLS:  # overflow marker
                for yy in range(LINE_H):
                    img[y0 + pad + li * LINE_H + yy][W - 3] = 4
    S = 2
    big = [[v for v in row for _ in range(S)] for row in img for _ in range(S)]
    write_png(path, W * S, H * S, big, PAL)


def main():
    rom = bytearray(open(sys.argv[1], "rb").read()[:0x400000])
    cyrfont.apply(rom)
    script = read_script(sys.argv[2])
    if sys.argv[3] == "--all":
        out_dir = sys.argv[4]
        os.makedirs(out_dir, exist_ok=True)
        for sid in list(script)[:40]:
            render(rom, script[sid], os.path.join(out_dir, sid.replace(":", "_") + ".png"))
        print("wrote", min(40, len(script)), "previews to", out_dir)
        return
    sid = sys.argv[3]
    out = sys.argv[4] if len(sys.argv) > 4 else sid.replace(":", "_") + ".png"
    render(rom, script[sid], out)
    print("wrote", out)


if __name__ == "__main__":
    main()
