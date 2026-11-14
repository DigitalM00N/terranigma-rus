"""Graphic texts on the boot / title screens (sprites inside compressed graphics).

  presents   : "PRESENTS" under the Nintendo logo. 4 sprites 16x16 = 64x16 image,
               tiles 0x108-0x10F / 0x118-0x11F of block $AC:CF9A (tile N at (N-0x100)*32).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import lz  # noqa: E402

PRESENTS_BLOCK = 0x2CCF9A

# 4x7 capitals, '#' = ink
MINI = {
    "П": ["####", "#..#", "#..#", "#..#", "#..#", "#..#", "#..#"],
    "Р": ["###.", "#..#", "#..#", "###.", "#...", "#...", "#..."],
    "Е": ["####", "#...", "#...", "###.", "#...", "#...", "####"],
    "Д": [".###.", ".#.#.", ".#.#.", ".#.#.", ".#.#.", "#####", "#...#"],
    "С": [".###", "#...", "#...", "#...", "#...", "#...", ".###"],
    "Т": ["####", ".#..", ".#..", ".#..", ".#..", ".#..", ".#.."],
    "А": [".##.", "#..#", "#..#", "####", "#..#", "#..#", "#..#"],
    "В": ["###.", "#..#", "#..#", "###.", "#..#", "#..#", "###."],
    "Л": [".###", ".#.#", ".#.#", ".#.#", ".#.#", "#..#", "#..#"],
    "Я": [".###", "#..#", "#..#", ".###", ".#.#", "#..#", "#..#"],
    "Н": ["#..#", "#..#", "#..#", "####", "#..#", "#..#", "#..#"],
    "Ж": ["#.#.#", "#.#.#", ".###.", "..#..", ".###.", "#.#.#", "#.#.#"],
    "М": ["#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"],
    "И": ["#..#", "#..#", "#.##", "##.#", "#..#", "#..#", "#..#"],
    "К": ["#..#", "#.#.", "##..", "#...", "##..", "#.#.", "#..#"],
    "О": [".##.", "#..#", "#..#", "#..#", "#..#", "#..#", ".##."],
    " ": ["..", "..", "..", "..", "..", "..", ".."],
}
INK, SHADOW = 9, 14   # colours used by the original lettering


def draw_text(text, width, height, top=0):
    img = [[0] * width for _ in range(height)]
    glyphs = [MINI[c] for c in text]
    total = sum(len(g[0]) for g in glyphs) + (len(glyphs) - 1)
    if total > width - 1:
        raise ValueError("text '%s' is %d px wide, only %d fit" % (text, total, width - 1))
    x = (width - 1 - total) // 2
    for g in glyphs:
        for y, row in enumerate(g):
            for dx, ch in enumerate(row):
                if ch == "#":
                    img[top + y][x + dx] = INK
        x += len(g[0]) + 1
    # drop shadow right / down / down-right (like the original lettering)
    for y in range(height):
        for x in range(width):
            if img[y][x] == INK:
                for ox, oy in ((1, 0), (0, 1), (1, 1)):
                    X, Y = x + ox, y + oy
                    if X < width and Y < height and img[Y][X] == 0:
                        img[Y][X] = SHADOW
    return img


def tile4(img, x0, y0):
    out = bytearray(32)
    for y in range(8):
        for x in range(8):
            c = img[y0 + y][x0 + x]
            bit = 7 - x
            out[y * 2] |= (c & 1) << bit
            out[y * 2 + 1] |= ((c >> 1) & 1) << bit
            out[16 + y * 2] |= ((c >> 2) & 1) << bit
            out[16 + y * 2 + 1] |= ((c >> 3) & 1) << bit
    return bytes(out)


TITLE_FONT_BLOCK = 0x380000     # $F8:0000, OBJ tiles 0x00-0xFF of the title screen
TITLE_FONT_LOAD_LO = 0x06F1D8   # operand of LDA #$0000 (-> $66)
TITLE_FONT_LOAD_BANK = 0x06F1DD  # operand of LDA #$00F8 (-> $68)
PUSH_TABLE = 0x32BCC1          # 15 records x 7 bytes: [x][0x80-x][0][0][tile][attr][..]
PUSH_COUNT = 15
# blank tiles in the font rows that no title sprite covers (the 32x32 orb at 0xC0 owns
# columns 0-3 of rows C-F), used for new letters
NEW_TILES = list(range(0xF4, 0xF8)) + list(range(0xE4, 0xE8)) + list(range(0xD4, 0xD8))
BLANK_TILE = 0xD0
# letters of the 8x8 title font: tile numbers
TITLE_TILES = {}
for _i, _c in enumerate("ABCDEFGH"):
    TITLE_TILES[_c] = 0xC8 + _i
for _i, _c in enumerate("IJKLMNOP"):
    TITLE_TILES[_c] = 0xD8 + _i
for _i, _c in enumerate("QRSTUVWX"):
    TITLE_TILES[_c] = 0xE8 + _i
TITLE_TILES["Y"], TITLE_TILES["Z"] = 0xF8, 0xF9
LOOKALIKE = {"А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
             "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y"}
# 7x7 masks for Cyrillic letters without a Latin twin (style of the title font)
TITLE_NEW = {
    "Ж": ["##.#.##", ".#.#.#.", "..###..", "..###..", ".#.#.#.", "#..#..#", "##.#.##"],
    "Л": ["..####.", "..#..#.", "..#..#.", ".##..#.", ".#...#.", "##...#.", "#...###"],
    "П": ["#######", ".##..#.", ".##..#.", ".##..#.", ".##..#.", ".##..#.", "###.###"],
    "Я": [".######", "##..##.", "##..##.", ".#####.", "..#.##.", ".#..##.", "###.###"],
    "Г": ["#######", ".##...#", ".##....", ".##....", ".##....", ".##....", "####..."],
    "Д": ["..####.", "..#.##.", "..#.##.", ".##.##.", ".#..##.", "#######", "#.....#"],
    "Ч": ["###.###", ".#...#.", ".#...#.", "..####.", ".....#.", ".....#.", "....###"],
    "Б": ["#######", ".##...#", ".##....", ".#####.", ".##..##", ".##..##", "######."],
}


def _mask_from_tile(data, tile, mirror=False):
    a = tile * 32
    m = []
    for y in range(8):
        b0, b1, b2, b3 = data[a + 2 * y], data[a + 2 * y + 1], data[a + 16 + 2 * y], data[a + 16 + 2 * y + 1]
        row = ""
        for x in range(8):
            c = ((b0 >> (7 - x)) & 1) | ((b1 >> (7 - x)) & 1) << 1 | ((b2 >> (7 - x)) & 1) << 2 | ((b3 >> (7 - x)) & 1) << 3
            row += "#" if c >= 14 else "."
        m.append(row[::-1] if mirror else row)
    return m


def _title_glyph(mask):
    """Colour like the title font: rows 0-2 colour 15, rows 3-6 colour 14, shadow 1."""
    img = [[0] * 8 for _ in range(8)]
    for y, row in enumerate(mask[:8]):
        for x, ch in enumerate(row[:8]):
            if ch == "#":
                img[y][x] = 15 if y < 3 else 14
    for y in range(8):
        for x in range(8):
            if img[y][x] in (14, 15):
                for ox, oy in ((1, 0), (0, 1)):
                    X, Y = x + ox, y + oy
                    if X < 8 and Y < 8 and img[Y][X] == 0:
                        img[Y][X] = 1
    return img


def patch_push_start(rom, orig, text, alloc=None):
    """Replace "PUSH START BUTTON" (title screen) with `text` (A-Z / Cyrillic capitals)."""
    data, used = lz.decompress(orig, TITLE_FONT_BLOCK)
    data = bytearray(data)
    letters = [c for c in text if c != " "]
    if len(letters) > PUSH_COUNT:
        raise ValueError("'%s': at most %d letters" % (text, PUSH_COUNT))
    if len(text) > 17:
        raise ValueError("'%s': at most 17 cells including spaces" % text)
    new_tiles = {}
    free = iter(NEW_TILES)
    cells = []
    for c in text:
        if c == " ":
            cells.append(None)
            continue
        lat = LOOKALIKE.get(c, c)
        if lat in TITLE_TILES:
            cells.append(TITLE_TILES[lat])
            continue
        if c not in new_tiles:
            if c == "И":
                mask = _mask_from_tile(data, TITLE_TILES["N"], mirror=True)
            elif c in TITLE_NEW:
                mask = TITLE_NEW[c]
            else:
                raise ValueError("no title-font glyph for '%s'" % c)
            t = next(free)
            data[t * 32:t * 32 + 32] = tile4(_title_glyph(mask), 0, 0)
            new_tiles[c] = t
        cells.append(new_tiles[c])
    # layout: original text spans x 0x00..0x80 (17 cells); centre the new text
    start = (17 - len(text)) * 4
    recs = []
    for i, t in enumerate(cells):
        if t is not None:
            recs.append((start + 8 * i, t))
    recs.reverse()                                  # table lists the last letter first
    while len(recs) < PUSH_COUNT:
        recs.append((start, BLANK_TILE))
    for k, (x, t) in enumerate(recs):
        a = PUSH_TABLE + 7 * k
        rom[a] = x
        rom[a + 1] = (0x80 - x) & 0xFF
        rom[a + 4] = t
    if new_tiles:
        comp = orig[TITLE_FONT_BLOCK:TITLE_FONT_BLOCK + 3] + lz.compress(bytes(data))[3:]
        if len(comp) <= used:
            rom[TITLE_FONT_BLOCK:TITLE_FONT_BLOCK + len(comp)] = comp
            back, _ = lz.decompress(bytes(rom), TITLE_FONT_BLOCK)
        else:
            if alloc is None:
                raise ValueError("title font graphics grew to %d bytes (room: %d)" % (len(comp), used))
            from insert import snes_to_file
            addr = alloc.alloc(len(comp))
            f = snes_to_file(addr)
            rom[f:f + len(comp)] = comp
            # $86:F1D7  LDA #$0000 / STA $66 / LDA #$00F8 / STA $68  -> new address
            rom[TITLE_FONT_LOAD_LO:TITLE_FONT_LOAD_LO + 2] = (addr & 0xFFFF).to_bytes(2, "little")
            rom[TITLE_FONT_LOAD_BANK] = addr >> 16
            back, _ = lz.decompress(bytes(rom), f)
        assert back == bytes(data)
        return len(comp), used
    return 0, used


CREDITS_START = 0x07ED24        # ending staff roll, ASCII: "/<style>TEXT\" lines, "=" page end,
CREDITS_END = 0x07F358          # "/\x80" section end; whole block ends after the last "=/\x80="
CREDITS_PTR_OPERAND = 0x07CF0F  # LDA #$ED24 / STA $04C4
CREDITS_DB_OPERAND = 0x07EC02   # LDA #$87 / PHA / PLB in the parser ($C7:EC01)
CREDITS_CHARS = set("0123456789:;ABCDEFGHIJKLMNOPQRSTUVWXYZ ")


def patch_credits(rom, orig, title, name, alloc):
    """Insert a page "title / name" into the staff roll before the COPYRIGHT page.
    The block is moved to the expansion area (parser bank + start address patched)."""
    for s in (title, name):
        bad = sorted(set(s) - CREDITS_CHARS)
        if bad:
            raise ValueError("credits text '%s': only A-Z, 0-9, ':' and spaces (bad: %s)" % (s, "".join(bad)))
        if len(s) > 30:
            raise ValueError("credits text '%s' is longer than 30 characters" % s)
    block = bytes(orig[CREDITS_START:CREDITS_END])
    cp = block.find(b"/\x00COPYRIGHT")
    if cp < 0:
        raise ValueError("COPYRIGHT page not found in the staff roll")
    page = b"/\x00" + title.encode("ascii") + b"\\/\x01" + name.encode("ascii") + b"="
    new = block[:cp] + page + block[cp:]
    from insert import snes_to_file
    addr = alloc.alloc(len(new))
    f = snes_to_file(addr)
    rom[f:f + len(new)] = new
    rom[CREDITS_PTR_OPERAND:CREDITS_PTR_OPERAND + 2] = (addr & 0xFFFF).to_bytes(2, "little")
    rom[CREDITS_DB_OPERAND] = addr >> 16
    return addr, len(new)


def patch_presents(rom, orig, text):
    data, used = lz.decompress(orig, PRESENTS_BLOCK)
    data = bytearray(data)
    img = draw_text(text, 64, 16)
    for i in range(4):                       # sprite i: tiles 0x108+2i (+1, +16, +17)
        for sub, (dx, dy) in enumerate(((0, 0), (8, 0), (0, 8), (8, 8))):
            t = 0x108 + 2 * i + (1 if dx else 0) + (16 if dy else 0)
            off = (t - 0x100) * 32
            data[off:off + 32] = tile4(img, i * 16 + dx, dy)
    comp = orig[PRESENTS_BLOCK:PRESENTS_BLOCK + 3] + lz.compress(bytes(data))[3:]
    if len(comp) > used:
        raise ValueError("PRESENTS graphics grew to %d bytes (room: %d)" % (len(comp), used))
    rom[PRESENTS_BLOCK:PRESENTS_BLOCK + len(comp)] = comp
    back, _ = lz.decompress(bytes(rom), PRESENTS_BLOCK)
    assert back == bytes(data)
    return len(comp), used
