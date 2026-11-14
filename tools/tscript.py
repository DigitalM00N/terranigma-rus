"""Terranigma (Europe) text engine model: control codes, encoding table, parser.

Engine: $85:915A.  Byte classes:
  00-7F : single glyph (font at $B6:8000, 64 bytes/glyph, 16x16 2bpp)
  80-BF : two-byte glyph reference (word & 3FFF: low 9 bits glyph, upper -> bank)
  C0-FF : control codes via jump table $85:91E4
"""

# number of parameter bytes after each control code
CTRL_ARGS = {
    0xC0: 0, 0xC1: 0, 0xC2: 4, 0xC3: 2, 0xC4: 1, 0xC5: 1, 0xC6: 1, 0xC7: 1,
    0xC8: 1, 0xC9: 1, 0xCA: 3, 0xCB: 2, 0xCC: 3, 0xCD: 2, 0xCE: 4, 0xCF: 0,
    0xD0: 0, 0xD1: 0, 0xD2: 1, 0xD3: 0, 0xD4: 0, 0xD5: 0, 0xD6: 0, 0xD7: 0,
    0xD8: 2, 0xD9: 1, 0xDA: 0, 0xDB: 0, 0xDC: 0, 0xDD: 4, 0xDE: 2, 0xDF: 0,
    0xE0: 3,  # added by the patch: long jump (no return) lo hi bank
    0xE3: 2, 0xE4: 1, 0xE5: 1, 0xE6: 1,
}

# symbolic names used in the editable script
CTRL_NAMES = {
    0xC0: "clr", 0xC1: "win", 0xC2: "box", 0xC3: "pos", 0xC4: "c4", 0xC5: "c5",
    0xC6: "col", 0xC7: "c7", 0xC8: "c8", 0xC9: "sp", 0xCA: "ca", 0xCB: "jmp",
    0xCC: "call", 0xCD: "num", 0xCE: "sel", 0xD0: "alt", 0xD1: "noalt",
    0xD2: "name", 0xD3: "end", 0xD4: "ret", 0xD5: "wait", 0xD6: "d6", 0xD7: "d7",
    0xD8: "d8", 0xD9: "d9", 0xDA: "da", 0xDB: "db", 0xDC: "dc", 0xDD: "dd",
    0xDE: "de", 0xDF: "df", 0xE0: "goto", 0xE3: "e3", 0xE4: "str", 0xE5: "str2",
    0xE6: "str3",
}
CTRL_BY_NAME = {v: k for k, v in CTRL_NAMES.items()}

# ---- original (English) glyph table: byte -> char -------------------------
ENG = {0x20: " "}
for i in range(26):
    ENG[0x21 + i] = chr(ord("A") + i)
    ENG[0x41 + i] = chr(ord("a") + i)
for i in range(10):
    ENG[0x63 + i] = str(i)
ENG.update({
    0x60: "?", 0x61: "(", 0x62: ")", 0x6D: "!", 0x6E: ",", 0x6F: ":",
    0x70: "→", 0x71: "←", 0x72: "↑", 0x73: "↓", 0x74: "“", 0x75: "”",
    0x76: "'", 0x77: "=", 0x79: "%", 0x7A: "*", 0x7B: "+", 0x7C: "-",
    0x7D: "/", 0x7E: "&", 0x7F: ".",
})

# ---- Russian table --------------------------------------------------------
# Cyrillic letters identical in shape to Latin ones reuse the Latin glyphs.
RUS_REUSE = {
    "А": 0x21, "В": 0x22, "Е": 0x25, "Ё": 0x25, "К": 0x2B, "М": 0x2D, "Н": 0x28,
    "О": 0x2F, "Р": 0x30, "С": 0x23, "Т": 0x34, "Х": 0x38,
    "а": 0x41, "е": 0x45, "ё": 0x45, "о": 0x4F, "р": 0x50, "с": 0x43, "у": 0x59,
    "х": 0x58,
}
# New single-byte glyph slots (empty in the original font).
# slot 0x00 must stay blank: its tiles are the "empty" tile 0 of tilemaps that share the font VRAM
FREE_SLOTS = list(range(0x01, 0x20)) + list(range(0x3B, 0x40)) + list(range(0x5B, 0x60))
RUS_NEW_LOWER = "бвгджзийклмнптфцчшщъыьэюя"
RUS_NEW_UPPER_1B = "БГДЖЗИЛПУФЦЧШЭЮЯ"
RUS_NEW_UPPER_2B = "ЩЪЫЬЙ"  # rare: two-byte glyph references into free font area

RUS = dict(RUS_REUSE)
_slots = iter(FREE_SLOTS)
for ch in RUS_NEW_LOWER + RUS_NEW_UPPER_1B:
    RUS[ch] = next(_slots)
# two-byte glyphs: word 0x8080+n -> glyph 0x80+n in bank $B6 (file 0x36A000+n*64)
RUS2 = {ch: 0x8080 + i for i, ch in enumerate(RUS_NEW_UPPER_2B)}

# characters common to both tables (punctuation, digits, Latin)
for b, ch in ENG.items():
    RUS.setdefault(ch, b)
RUS["…"] = None  # expanded to "..." by the encoder
RUS["—"] = 0x7C
RUS["–"] = 0x7C
RUS["«"] = 0x74
RUS["»"] = 0x75
RUS['"'] = None  # handled contextually (open/close)

# glyph slot -> char (for rendering the new font and for decoding a patched ROM)
NEW_GLYPHS = {RUS[ch]: ch for ch in RUS_NEW_LOWER + RUS_NEW_UPPER_1B}
NEW_GLYPHS2 = {v: k for k, v in RUS2.items()}


def ctrl_len(b):
    return 1 + CTRL_ARGS.get(b, 0)


class ParseError(Exception):
    pass


def parse_block(rom, off, limit=0x4000):
    """Parse one text block starting at file offset `off`.
    Returns list of tokens and the end offset (exclusive).
    Tokens: ('t', byte) glyph, ('w', word) 2-byte glyph, ('c', code, args)."""
    toks = []
    p = off
    end_bank = (off & ~0xFFFF) + 0x10000
    while True:
        if p >= end_bank or p - off > limit:
            raise ParseError("runaway at %06X" % off)
        b = rom[p]
        if b < 0x80:
            toks.append(("t", b))
            p += 1
        elif b < 0xC0:
            toks.append(("w", (b << 8) | rom[p + 1]))
            p += 2
        else:
            if b not in CTRL_ARGS:
                raise ParseError("bad code %02X at %06X" % (b, p))
            n = CTRL_ARGS[b]
            toks.append(("c", b, bytes(rom[p + 1:p + 1 + n])))
            p += 1 + n
            if b in (0xD3, 0xD4, 0xCB, 0xD7, 0xE0):
                return toks, p


# ---- second text engine ($85:869B, menus) --------------------------------
# Same glyph encoding and font; different control code table ($85:8763).
CTRL_ARGS2 = {
    0xC0: 0, 0xC1: 0, 0xC2: 5, 0xC3: 2, 0xC5: 1, 0xC6: 1, 0xC7: 1, 0xC8: 1,
    0xC9: 1, 0xCA: 3, 0xCB: 2, 0xCC: 3, 0xCD: 2, 0xCE: 4, 0xCF: 0, 0xD0: 0,
    0xD1: 0, 0xD2: 1, 0xD3: 0, 0xD4: 0, 0xD5: 0, 0xD6: 0, 0xD7: 0, 0xD8: 2,
    0xD9: 1, 0xDD: 0, 0xDE: 2, 0xE0: 7, 0xE1: 3, 0xE2: 1, 0xE3: 2, 0xE4: 1,
    0xE5: 1, 0xE6: 1,
}
# readable names valid in engine 2 (all other codes are written as [$XX:..])
CTRL_NAMES2 = {0xD3: "end", 0xD4: "ret", 0xD7: "d7", 0xC0: "end0", 0xD2: "name",
               0xE4: "str", 0xE5: "str2", 0xE6: "str3", 0xCB: "jmp", 0xCC: "call", 0xD5: "wait"}
TERM1 = (0xD3, 0xD4, 0xCB, 0xD7)
TERM2 = (0xC0, 0xD3, 0xD4, 0xCB, 0xD7, 0xD9)
