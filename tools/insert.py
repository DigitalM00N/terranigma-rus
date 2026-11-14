"""Build a translated Terranigma ROM from script files.

Usage:
  python tools/insert.py <original.sfc> <script_dir> <out.sfc> [--force-relocate] [--ips out.ips] [--bps out.bps]

What it does
  1. Expands the 4 MB HiROM image to 8 MB ExHiROM (upper-half mirror of every bank is
     copied so $00-$3F:8000-FFFF keep working; banks $40-$7D:0000-7FFF become free space).
  2. Adds text control code E0 = long jump without return (re-uses the CC handler tail).
  3. Inserts the Cyrillic glyphs.
  4. Every script segment whose encoded bytes differ from the original is written into
     the free space; a 4-byte stub "E0 lo hi bank" is placed at its original address.
  5. String tables (D2/E5/E6) are updated in place / via stubs.
  6. Fixes header + checksum, optionally writes IPS/BPS patches.
"""
import json
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.dirname(__file__))
import cyrfont  # noqa: E402
from tscript import CTRL_ARGS, CTRL_ARGS2, CTRL_BY_NAME, CTRL_NAMES2, ENG, RUS, RUS2  # noqa: E402

LINE_LIMIT = 18          # glyphs per dialogue line (English never exceeds ~19)
ORIG_SIZE = 0x400000
NEW_SIZE = 0x800000
E0_TABLE_SLOT = 0x591E4 + (0xE0 - 0xC0) * 2
E0_HANDLER = 0x9AB4      # tail of CC handler: set bank + Y without pushing a return


# ---------------------------------------------------------------- script parsing
HDR = re.compile(r"^=== (\S+) ===$", re.M)


def read_script(path):
    """Return {id: body} from a script file (comment lines start with '#')."""
    txt = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    out = {}
    parts = HDR.split(txt)
    for i in range(1, len(parts), 2):
        body = parts[i + 1]
        if body.startswith("\n"):
            body = body[1:]
        body = "\n".join(l for l in body.split("\n") if not l.startswith("#"))
        body = body.rstrip("\n")
        out[parts[i]] = body
    return out


class EncodeError(Exception):
    pass


def parse_id(s):
    """'BB:AAAA' -> file offset (BB may be a CPU bank $80-$FF; it is folded to the ROM bank)."""
    b, a = s.split(":")
    return ((int(b, 16) & 0x3F) << 16) | int(a, 16)


def raw_bank(s):
    return int(s.split(":")[0], 16)


def tokenize(body, table, two_byte, engine=1):
    """Body text -> list of items: bytes or ('ref', kind, target_off).
    engine 2 = menu text engine (different control-code argument counts)."""
    if engine == 2:
        args_tab = CTRL_ARGS2
        by_name = {v: k for k, v in CTRL_NAMES2.items()}
    else:
        args_tab = CTRL_ARGS
        by_name = CTRL_BY_NAME
    items = []
    i = 0
    quote_open = True
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "[":
            j = body.index("]", i)
            tag = body[i + 1:j]
            i = j + 1
            name, _, arg = tag.partition(":")
            if name == "wait":
                items.append(bytes([0xD5]))
                if i < n and body[i] == "\n":
                    i += 1
                continue
            if name == "g":
                items.append(bytes([int(arg, 16)]))
                continue
            if name == "w":
                v = int(arg, 16)
                items.append(bytes([v >> 8, v & 0xFF]))
                continue
            if name == "jmp":
                items.append(("ref", "jmp", parse_id(arg)))
                continue
            if name == "goto":
                items.append(("ref", "fall", parse_id(arg)))
                continue
            if name == "call":
                rb = raw_bank(arg)
                items.append(("ref", "call", parse_id(arg), rb if rb >= 0x80 else 0xC0 | rb))
                continue
            if name in by_name:
                code = by_name[name]
            else:
                try:
                    code = int(name.lstrip("$"), 16)
                except ValueError:
                    raise EncodeError("unknown tag [%s]" % tag)
            args = bytes(int(a, 16) for a in arg.split()) if arg else b""
            if code not in args_tab or len(args) != args_tab[code]:
                raise EncodeError("tag [%s] needs %s arg bytes" % (tag, args_tab.get(code, "?")))
            items.append(bytes([code]) + args)
            continue
        i += 1
        if ch == "\n":
            items.append(bytes([0xCF]))
            continue
        if ch == "…":
            items.append(bytes([0x7F, 0x7F, 0x7F]))
            continue
        if ch == '"':
            items.append(bytes([0x74 if quote_open else 0x75]))
            quote_open = not quote_open
            continue
        if ch in two_byte:
            v = two_byte[ch]
            items.append(bytes([v >> 8, v & 0xFF]))
            continue
        b = table.get(ch)
        if b is None:
            raise EncodeError("character %r has no glyph" % ch)
        items.append(bytes([b]))
    return items


def items_size(items):
    return sum(len(x) if isinstance(x, bytes) else 4 for x in items)


def plain_lines(body):
    plain = re.sub(r"\[wait\]\n?", "\n", body)
    # clearing / opening a window also starts a new line
    plain = re.sub(r"\[(clr|win|da|db|df|box:[^\]]*)\]", "\n", plain)
    plain = re.sub(r"\[name:00\]", "#####", plain)  # hero name: up to 5 letters
    plain = re.sub(r"\[(name|str|str2|str3):[^\]]*\]", "######", plain)
    plain = re.sub(r"\[[^\]]*\]", "", plain)
    return plain.split("\n")


def line_warnings(body, limit=None):
    """Lines longer than the window (or than the longest English line of the same
    entry, for the few wide special windows)."""
    limit = limit or LINE_LIMIT
    return [l for l in plain_lines(body) if len(l) > limit]




# ---------------------------------------------------------------- ROM helpers
def expand_exhirom(rom):
    """8 MB ExHiROM: lower 4 MB unchanged ($80-$BF/$C0-$FF), upper 4 MB:
    $00:8000-FFFF must mirror the original bank 0 (vectors + COP/NMI entry code);
    $01-$3F:8000-FFFF is free text space.  These banks map WRAM at $0000-$1FFF,
    which the text engines need (they access their variables through DB)."""
    out = bytearray(bytes([0xFF]) * NEW_SIZE)
    out[:ORIG_SIZE] = rom
    out[0x408000:0x410000] = rom[0x8000:0x10000]
    return out


TEXT_BANKS = (0x01, 0x3F)       # banks used for relocated text, addresses $8000-$FFFF
CODE_ADDR = 0x3FF000            # $3F:F000 - small routines added by the patch


class Allocator:
    """Free space: banks $01-$3F, addresses $8000-$FFFF (file 0x4n8000-0x4nFFFF)."""

    def __init__(self):
        self.bank = TEXT_BANKS[0]
        self.pos = 0x8000

    def alloc(self, size):
        if size > 0x8000:
            raise ValueError("block too large")
        limit = (CODE_ADDR & 0xFFFF) if self.bank == TEXT_BANKS[1] else 0x10000
        if self.pos + size > limit:
            self.bank += 1
            self.pos = 0x8000
            if self.bank > TEXT_BANKS[1]:
                raise ValueError("out of expansion space")
        addr = (self.bank << 16) | self.pos
        self.pos += size
        return addr

    def used(self):
        return (self.bank - TEXT_BANKS[0]) * 0x8000 + self.pos - 0x8000


def snes_to_file(addr):
    bank = addr >> 16
    if bank <= 0x3F and (addr & 0xFFFF) >= 0x8000:
        return 0x400000 + bank * 0x10000 + (addr & 0xFFFF)
    return addr & 0x3FFFFF


def asm_launcher(ptr_var, bank_var, engine):
    """Text launcher that sets the text bank once (the original ones at $C7:CD0E and
    $C7:CD38 re-set bank $92 on every frame, which breaks relocated text)."""
    def lo(v):
        return bytes([v & 0xFF, (v >> 8) & 0xFF])
    # STY ptr ; SEP #$20 ; LDA #$92 ; STA bank
    head = bytes([0x8C]) + lo(ptr_var) + bytes([0xE2, 0x20, 0xA9, 0x92, 0x8D]) + lo(bank_var)
    # loop: PHB ; SEP #$20 ; LDA bank ; PHA ; PLB ; REP #$20 ; LDY ptr ; JSL engine ; PLB
    loop = (bytes([0x8B, 0xE2, 0x20, 0xAD]) + lo(bank_var) + bytes([0x48, 0xAB, 0xC2, 0x20, 0xAC])
            + lo(ptr_var) + bytes([0x22]) + engine.to_bytes(3, "little") + bytes([0xAB]))
    # SEP #$20 ; w: JSL $808150 ; DEC ; BPL w ; REP #$20
    wait = bytes([0xE2, 0x20, 0x22, 0x50, 0x81, 0x80, 0x3A, 0x10, 0xF9, 0xC2, 0x20])
    # BCC done ; wait ; BRA loop ; done: STZ bank ; RTL
    bcc = bytes([0x90, len(wait) + 2])
    body_before_bra = loop + bcc + wait
    bra = bytes([0x80, (-(len(body_before_bra) + 2)) & 0xFF])
    return head + body_before_bra + bra + bytes([0x9C]) + lo(bank_var) + bytes([0x6B])


def patch_launchers(rom):
    code = CODE_ADDR
    for orig_entry, ptr_var, bank_var, engine in ((0x07CD0E, 0x0DC0, 0x0DC2, 0x85915A),
                                                 (0x07CD38, 0x099D, 0x099F, 0x85869B)):
        blob = asm_launcher(ptr_var, bank_var, engine)
        f = snes_to_file(code)
        rom[f:f + len(blob)] = blob
        rom[orig_entry:orig_entry + 4] = bytes([0x5C]) + code.to_bytes(3, "little")  # JML
        code += len(blob)


def cpu_addr(off):
    """CPU address for an original ROM location (file offset < 4 MB).
    Banks $80-$BF are used for $8000-$FFFF (they map WRAM at $0000-$1FFF, which the
    text engines rely on through DB); banks $C0-$FF only for addresses below $8000."""
    if (off & 0xFFFF) >= 0x8000:
        return 0x800000 | off
    return 0xC00000 | off


def stub(addr):
    return bytes([0xE0, addr & 0xFF, (addr >> 8) & 0xFF, addr >> 16])


def fix_header(rom):
    for h in (0x00FFC0, 0x40FFC0):
        rom[h + 0x15] = 0x35       # ExHiROM + FastROM
        rom[h + 0x17] = 0x0D       # 8 MB
        rom[h + 0x1C:h + 0x20] = b"\xFF\xFF\x00\x00"
    s = sum(rom) & 0xFFFF
    for h in (0x00FFC0, 0x40FFC0):
        rom[h + 0x1C:h + 0x1E] = ((s ^ 0xFFFF) & 0xFFFF).to_bytes(2, "little")
        rom[h + 0x1E:h + 0x20] = s.to_bytes(2, "little")


# ---------------------------------------------------------------- patches
def write_ips(src, dst, path):
    """IPS with RLE records. Note: IPS cannot shrink files; target is larger here."""
    out = bytearray(b"PATCH")
    i, n = 0, len(dst)

    def same(k):
        return k < len(src) and src[k] == dst[k]

    while i < n:
        if same(i):
            i += 1
            continue
        if i == 0x454F46:  # offset "EOF" is not representable
            i -= 1
        # RLE run?
        r = i
        while r < n and r - i < 0xFFFF and dst[r] == dst[i]:
            r += 1
        if r - i >= 16:
            out += i.to_bytes(3, "big") + b"\x00\x00" + (r - i).to_bytes(2, "big") + dst[i:i + 1]
            i = r
            continue
        j = i
        while j < n and j - i < 0xFFFF and not (same(j) and same(j + 1)):
            # stop before a long RLE run
            if j + 16 <= n and dst[j:j + 16] == dst[j:j + 1] * 16 and j > i:
                break
            j += 1
        out += i.to_bytes(3, "big") + (j - i).to_bytes(2, "big") + dst[i:j]
        i = j
    out += b"EOF"
    open(path, "wb").write(out)


def write_bps(src, dst, path):
    """BPS patch: SourceRead for unchanged bytes, SourceCopy for the mirrored bank halves,
    TargetCopy (RLE) for fill runs, TargetRead for everything else."""
    def num(v):
        o = bytearray()
        while True:
            x = v & 0x7F
            v >>= 7
            if v == 0:
                o.append(0x80 | x)
                return o
            o.append(x)
            v -= 1

    def signed(v):
        return num((abs(v) << 1) | (v < 0))

    out = bytearray(b"BPS1") + num(len(src)) + num(len(dst)) + num(0)
    n, ls = len(dst), len(src)
    i = 0
    src_rel = 0
    tgt_rel = 0
    lit_start = None

    def flush(upto):
        nonlocal lit_start
        if lit_start is not None and upto > lit_start:
            out.extend(num(((upto - lit_start - 1) << 2) | 1))
            out.extend(dst[lit_start:upto])
        lit_start = None

    def mirror_src(k):
        if k >= 0x400000 and (k & 0xFFFF) >= 0x8000:
            return k - 0x400000
        return None

    while i < n:
        # 1) SourceRead
        j = i
        while j < n and j < ls and src[j] == dst[j]:
            j += 1
        if j - i >= 8 or (j == n and j > i):
            flush(i)
            out += num(((j - i - 1) << 2) | 0)
            i = j
            continue
        # 2) SourceCopy from the mirrored upper half
        m = mirror_src(i)
        if m is not None:
            k = i
            while k < n and mirror_src(k) is not None and dst[k] == src[k - 0x400000]:
                k += 1
                if (k & 0xFFFF) == 0:
                    break
            if k - i >= 8:
                flush(i)
                out += num(((k - i - 1) << 2) | 2) + signed(m - src_rel)
                src_rel = m + (k - i)
                i = k
                continue
        # 3) RLE via TargetCopy
        r = i
        while r < n and dst[r] == dst[i]:
            r += 1
        if r - i >= 16 and i > 0:
            flush(i)
            # literal first byte, then copy from the previous output byte
            out += num((0 << 2) | 1) + dst[i:i + 1]
            out += num(((r - i - 2) << 2) | 3) + signed(i - tgt_rel)
            tgt_rel = i + (r - i - 1)
            i = r
            continue
        if lit_start is None:
            lit_start = i
        i += 1
    flush(n)
    out += zlib.crc32(src).to_bytes(4, "little")
    out += zlib.crc32(dst).to_bytes(4, "little")
    out += zlib.crc32(out).to_bytes(4, "little")
    open(path, "wb").write(out)


def apply_bps(src, patch):
    """Reference BPS applier (used to self-test generated patches)."""
    p = 4

    def num():
        nonlocal p
        data, shift = 0, 1
        while True:
            x = patch[p]
            p += 1
            data += (x & 0x7F) * shift
            if x & 0x80:
                return data
            shift <<= 7
            data += shift

    assert patch[:4] == b"BPS1"
    ssize, tsize, msize = num(), num(), num()
    p += msize
    out = bytearray(tsize)
    o = 0
    srel = trel = 0
    end = len(patch) - 12
    while p < end:
        d = num()
        cmd, ln = d & 3, (d >> 2) + 1
        if cmd == 0:
            out[o:o + ln] = src[o:o + ln]
            o += ln
        elif cmd == 1:
            out[o:o + ln] = patch[p:p + ln]
            p += ln
            o += ln
        else:
            v = num()
            v = -(v >> 1) if v & 1 else v >> 1
            if cmd == 2:
                srel += v
                out[o:o + ln] = src[srel:srel + ln]
                srel += ln
                o += ln
            else:
                trel += v
                for _ in range(ln):
                    out[o] = out[trel]
                    o += 1
                    trel += 1
    assert zlib.crc32(out) == int.from_bytes(patch[-8:-4], "little"), "BPS self-test failed"
    return out


def apply_ips(src, patch):
    out = bytearray(src)
    p = 5
    while patch[p:p + 3] != b"EOF":
        off = int.from_bytes(patch[p:p + 3], "big")
        size = int.from_bytes(patch[p + 3:p + 5], "big")
        p += 5
        if size == 0:
            rl = int.from_bytes(patch[p:p + 2], "big")
            data = patch[p + 2:p + 3] * rl
            p += 3
        else:
            data = patch[p:p + size]
            p += size
        if off + len(data) > len(out):
            out.extend(b"\x00" * (off + len(data) - len(out)))
        out[off:off + len(data)] = data
    return out


# ---------------------------------------------------------------- main build
def find_refs(script_dir):
    for cand in (os.path.join(os.path.dirname(os.path.abspath(script_dir)), "refs.json"),
                 os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "script", "refs.json")):
        if os.path.exists(cand):
            return cand
    raise SystemExit("refs.json not found - run tools/extract.py first")


NAME_GFX_PTR = 0x02F505            # 24-bit pointer to the compressed name-screen tiles
NAME_GFX_FONT = 0x368000


def patch_name_gfx(rom, orig, codes, alloc):
    """Redraw the default name inside the compressed graphics block.
    5 positions; each letter = 4 tiles (4bpp, low planes = font tile, high planes 0):
    top at 0x1000 + k*0x40 (+0x20 right), bottom at 0x1200 + k*0x40."""
    import lz
    ptr = rom[NAME_GFX_PTR] | rom[NAME_GFX_PTR + 1] << 8 | rom[NAME_GFX_PTR + 2] << 16
    src = ptr & 0x3FFFFF
    data, used = lz.decompress(orig, src)
    data = bytearray(data)
    for k, code in enumerate(codes[:5]):
        g = rom[NAME_GFX_FONT + code * 64:NAME_GFX_FONT + code * 64 + 64]
        for t, base in ((0, 0x1000), (1, 0x1020), (2, 0x1200), (3, 0x1220)):
            a = base + k * 0x40
            data[a:a + 16] = g[t * 16:t * 16 + 16]
            data[a + 16:a + 32] = bytes(16)
    comp = lz.compress(bytes(data))
    comp = orig[src:src + 3] + comp[3:]          # keep the original 3-byte header
    if len(comp) <= used:
        rom[src:src + len(comp)] = comp
    else:
        a = alloc.alloc(len(comp))
        f = snes_to_file(a)
        rom[f:f + len(comp)] = comp
        rom[NAME_GFX_PTR:NAME_GFX_PTR + 3] = a.to_bytes(3, "little")
    newptr = rom[NAME_GFX_PTR] | rom[NAME_GFX_PTR + 1] << 8 | rom[NAME_GFX_PTR + 2] << 16
    back, _ = lz.decompress(bytes(rom), snes_to_file(newptr))
    assert back == bytes(data), "name graphics re-compression check failed"


NAME_GRID_TABLE = 0x078D5C          # 60 input codes, 6 rows x 10
NAME_GRID_MAP = 0x207045            # BG tilemap of the grid (32 words per row)
NAME_GRID_COLS = (2, 4, 6, 8, 10, 14, 16, 18, 20, 22)


def apply_name_grid(rom, path, table):
    rows = [l.split() for l in open(path, encoding="utf-8-sig").read().splitlines()
            if l.strip() and not l.startswith("#")]
    if len(rows) != 6 or any(len(r) != 10 for r in rows):
        raise SystemExit("ERROR name_grid.txt: need 6 rows of 10 cells")
    codes = []
    for r in rows:
        for cell in r:
            if cell == "[bs]":
                codes.append(0x78)
            elif cell == "[ed]":
                codes.append(0x40)
            elif cell == "_":
                codes.append(0x20)
            elif cell.startswith("[g:"):
                codes.append(int(cell[3:5], 16))
            elif len(cell) == 1 and table.get(cell) is not None:
                codes.append(table[cell])
            else:
                raise SystemExit("ERROR name_grid.txt: cell %r is not a one-byte letter" % cell)
    if 0x78 not in codes or 0x40 not in codes:
        raise SystemExit("ERROR name_grid.txt: [bs] and [ed] are required")
    if any(c == 0x00 or c >= 0x80 for c in codes):
        raise SystemExit("ERROR name_grid.txt: unsupported character")
    rom[NAME_GRID_TABLE:NAME_GRID_TABLE + 60] = bytes(codes)
    for i, code in enumerate(codes):
        r, c = divmod(i, 10)
        top = NAME_GRID_MAP + r * 0xC0 + NAME_GRID_COLS[c] * 2
        for row_off, half in ((0, 0), (0x40, 2)):
            for k in range(2):
                a = top + row_off + 2 * k
                attr = (rom[a] | rom[a + 1] << 8) & 0xFC00
                tile = 0x80 if code == 0x20 else code * 4 + half + k
                v = attr | tile
                rom[a:a + 2] = v.to_bytes(2, "little")


def build(orig_path, script_dir, out_path, force=False, ips=None, bps=None, english=False):
    # force: False | True (everything) | "dialog" (only dialogue segments) | "b92" (only tables/menu/extra)
    force_dialog = force in (True, "dialog")
    force_b92 = force in (True, "b92")
    orig = open(orig_path, "rb").read()
    assert len(orig) == ORIG_SIZE, "expected the 4 MB Terranigma (Europe) ROM"
    meta = json.load(open(find_refs(script_dir)))
    table = ENG if english else RUS
    table = {v: k for k, v in ENG.items()} if english else table
    two = {} if english else RUS2

    rom = expand_exhirom(bytearray(orig))
    rom[E0_TABLE_SLOT:E0_TABLE_SLOT + 2] = E0_HANDLER.to_bytes(2, "little")
    patch_launchers(rom)
    font_rom = bytearray(rom[:ORIG_SIZE])
    cyrfont.apply(font_rom)
    rom[:ORIG_SIZE] = font_rom

    segs = meta["segments"]
    script = {}
    for fn in sorted(os.listdir(script_dir)):
        if fn.startswith("dialog_") and fn.endswith(".txt"):
            for k, v in read_script(os.path.join(script_dir, fn)).items():
                script[k] = (fn, v)

    errors, warnings = [], []
    encoded = {}
    eng_dialog = {}
    eng_dir_d = os.path.join(os.path.dirname(find_refs(script_dir)), "eng")
    if os.path.isdir(eng_dir_d):
        for fn in os.listdir(eng_dir_d):
            if fn.startswith("dialog_"):
                eng_dialog.update(read_script(os.path.join(eng_dir_d, fn)))
    for sid, (fn, body) in script.items():
        if sid not in segs:
            errors.append("%s: unknown segment %s" % (fn, sid))
            continue
        try:
            items = tokenize(body, table, two)
        except (EncodeError, ValueError) as e:
            errors.append("%s %s: %s" % (fn, sid, e))
            continue
        lim = LINE_LIMIT
        if sid in eng_dialog:
            lim = max(lim, max(len(l) for l in plain_lines(eng_dialog[sid])))
        for w in line_warnings(body, lim):
            warnings.append("%s %s: long line (%d > %d): %s" % (fn, sid, len(w), lim, w))
        encoded[sid] = items

    missing = [s for s in segs if s not in script]
    if missing:
        errors.append("segments missing from script: %d (e.g. %s)" % (len(missing), missing[0]))
    if errors:
        for e in errors[:50]:
            print("ERROR", e)
        sys.exit(1)

    def plain_bytes(sid):
        """Bytes as they would be written in place (refs encoded the original way)."""
        o = segs[sid]["off"]
        out = bytearray()
        for it in encoded[sid]:
            if isinstance(it, bytes):
                out += it
            else:
                kind, tgt = it[1], it[2]
                if kind == "fall":
                    continue  # original simply runs into the next segment
                if kind == "jmp" and (tgt >> 16) == (o >> 16):
                    out += bytes([0xCB, tgt & 0xFF, (tgt >> 8) & 0xFF])
                elif kind == "call":
                    out += bytes([0xCC, tgt & 0xFF, (tgt >> 8) & 0xFF, it[3]])
                else:
                    return None
        return bytes(out)

    # which segments change?
    changed = []
    for sid, s in segs.items():
        o, ln = s["off"], s["len"]
        pb = plain_bytes(sid)
        if not force_dialog and pb is not None and pb == orig[o:o + ln]:
            continue
        changed.append(sid)

    alloc = Allocator()
    new_addr = {}
    for sid in sorted(changed, key=lambda x: segs[x]["off"]):
        new_addr[sid] = alloc.alloc(items_size(encoded[sid]))

    def target_addr(tgt):
        sid = "%02X:%04X" % (tgt >> 16, tgt & 0xFFFF)
        if sid in new_addr:
            return new_addr[sid]
        return cpu_addr(tgt)

    # emit relocated bodies
    for sid in changed:
        out = bytearray()
        for it in encoded[sid]:
            if isinstance(it, bytes):
                out += it
            else:
                kind, tgt = it[1], it[2]
                a = target_addr(tgt)
                if kind in ("jmp", "fall"):
                    out += stub(a)
                else:
                    out += bytes([0xCC, a & 0xFF, (a >> 8) & 0xFF, a >> 16])
        f = snes_to_file(new_addr[sid])
        rom[f:f + len(out)] = out

    # stubs at original locations
    short = []
    dead = {}  # bank -> list of (start, end) free byte ranges inside relocated segments
    for sid in changed:
        o, ln = segs[sid]["off"], segs[sid]["len"]
        if ln >= 4:
            rom[o:o + 4] = stub(new_addr[sid])
            if ln > 4:
                dead.setdefault(o >> 16, []).append([o + 4, o + ln])
        else:
            short.append(sid)
    for sid in short:
        o = segs[sid]["off"]
        bank = o >> 16
        spot = None
        for rng in dead.get(bank, []):
            if rng[1] - rng[0] >= 4:
                spot = rng[0]
                rng[0] += 4
                break
        if spot is None:
            print("ERROR: no room for stub of short segment", sid)
            sys.exit(1)
        rom[spot:spot + 4] = stub(new_addr[sid])
        for ref in segs[sid]["refs"]:
            at = ref["at"]
            if ref["type"] in ("cop", "jmp"):
                rom[at + 2 - (ref["type"] == "jmp"):at + 4 - (ref["type"] == "jmp")] = \
                    (spot & 0xFFFF).to_bytes(2, "little")
            elif ref["type"] == "call":
                rom[at + 1:at + 4] = bytes([spot & 0xFF, (spot >> 8) & 0xFF, 0xC0 | (spot >> 16)])

    # ---------------- bank $92: name/str tables + menu/shop/description strings
    # These strings may be run by either text engine, so relocated ones get the
    # engine-neutral stub "CC lo hi bank D4" (call + return, 5 bytes).
    eng_dir = os.path.join(os.path.dirname(find_refs(script_dir)), "eng")
    # ---------------- name-entry letter grid
    grid_file = os.path.join(script_dir, "name_grid.txt")
    if os.path.exists(grid_file):
        apply_name_grid(rom, grid_file, table)

    b92 = []  # dicts: key, off, len, items, ptr (table slot or None), eng
    tables_file = os.path.join(script_dir, "tables.txt")
    if os.path.exists(tables_file):
        tscript = read_script(tables_file)
        if "hero:default" in tscript:
            items = tokenize(tscript["hero:default"], table, two)
            name = b"".join(x for x in items if isinstance(x, bytes))
            if not 1 <= len(name) <= 3 or len(name) != len(items) or any(c >= 0x80 for c in name):
                print("ERROR tables.txt hero:default: 1-3 letters, no Щ Ъ Ы Ь or tags")
                sys.exit(1)
            imm = (0x078C8F, 0x078C94, 0x078C99, 0x078C9E, 0x078CA3)
            vals = list(name) + [0xD1, 0xD4]
            vals += [0xD4] * (5 - len(vals))
            for a, v in zip(imm, vals):
                rom[a] = v
            # the same name is also drawn on the name-entry screen: LDA #$00xx ; JSR $8D98 (x5)
            shown = list(name) + [0x20] * (5 - len(name))
            for a, v in zip((0x078CAC, 0x078CB7, 0x078CC2, 0x078CCD, 0x078CD8), shown):
                rom[a] = v
            # ...and it is pre-drawn in the compressed name-screen graphics ($C8:777E)
            if bytes(name) != bytes([orig[0x078C8F], orig[0x078C94], orig[0x078C99]]):
                patch_name_gfx(rom, orig, shown, alloc)
        for tname, t in meta["tables"].items():
            for i, e in enumerate(t["entries"]):
                key = "%s:%03d" % (tname, i)
                if "ram" in e or key not in tscript:
                    continue
                b92.append({"key": key, "off": e["off"], "len": e["len"], "body": tscript[key],
                            "ptr": t["table"] + 2 * i, "eng": 0, "file": "tables.txt"})
    menu_file = os.path.join(script_dir, "menu_92.txt")
    if os.path.exists(menu_file) and "menu" in meta:
        mscript = read_script(menu_file)
        for key, e in meta["menu"].items():
            if key in mscript:
                ptr = None
                if key.startswith("desc:"):  # item description table: [attr][string]
                    ptr = 0x128EA4 + 2 * int(key[5:])
                b92.append({"key": key, "off": e["off"], "len": e["len"], "body": mscript[key],
                            "ptr": ptr, "eng": e["engine"], "file": "menu_92.txt",
                            "attr": key.startswith("desc:")})
    extra_file = os.path.join(script_dir, "extra.txt")
    if os.path.exists(extra_file) and "extra" in meta:
        xscript = read_script(extra_file)
        for key, e in meta["extra"].items():
            if key in xscript:
                b92.append({"key": key, "off": e["off"], "len": e["len"], "body": xscript[key],
                            "ptr": None, "eng": e["engine"], "file": "extra.txt"})
    eng_bodies = {}
    for fn in ("tables.txt", "menu_92.txt", "extra.txt"):
        fp = os.path.join(eng_dir, fn)
        if os.path.exists(fp):
            eng_bodies.update(read_script(fp))

    def plain92(items):
        out = bytearray()
        for it in items:
            if isinstance(it, bytes):
                out += it
            elif it[1] == "fall":
                continue
            elif it[1] == "jmp":
                out += bytes([0xCB, it[2] & 0xFF, (it[2] >> 8) & 0xFF])
            else:
                out += bytes([0xCC, it[2] & 0xFF, (it[2] >> 8) & 0xFF, it[3]])
        return bytes(out)

    def size92(items):
        return sum(len(x) if isinstance(x, bytes) else 5 for x in items)

    def call_stub(addr, ret=True):
        return bytes([0xCC, addr & 0xFF, (addr >> 8) & 0xFF, addr >> 16]) + (bytes([0xD4]) if ret else b"")

    errs92 = []
    for e in b92:
        try:
            e["items"] = tokenize(e["body"], table, two, 2 if e["eng"] == 2 else 1)
        except (EncodeError, ValueError) as ex:
            errs92.append("%s %s: %s" % (e["file"], e["key"], ex))
            continue
        # line length: never longer than the longest English line of the same entry
        if e["key"] in eng_bodies:
            lim = max(len(x) for x in re.sub(r"\[[^\]]*\]", "", eng_bodies[e["key"]]).split("\n"))
            # text windows: 18 glyphs (small [db] window: 14); fixed-width fields
            # ([sp:] item names, engine-2 boxes) keep the English width
            src = eng_bodies[e["key"]]
            if "[sp:" not in src and "[$C2:" not in src:
                lim = max(lim, 14 if "[db]" in src else LINE_LIMIT)
            for line in re.sub(r"\[[^\]]*\]", "", e["body"]).split("\n"):
                if len(line) > lim:
                    warnings.append("%s %s: line longer than original (%d > %d): %s"
                                    % (e["file"], e["key"], len(line), lim, line))
    if errs92:
        for x in errs92[:50]:
            print("ERROR", x)
        sys.exit(1)

    tchanged = 0
    moved92 = []
    for e in b92:
        o, ln = e["off"], e["len"]
        pb = plain92(e["items"])
        has_fall = any(not isinstance(x, bytes) and x[1] == "fall" for x in e["items"])
        if pb == orig[o:o + ln] and (not force_b92 or (ln < 5 and e["ptr"] is None)):
            continue
        tchanged += 1
        if not force_b92 and len(pb) <= ln and (not has_fall or len(pb) == ln):
            rom[o:o + len(pb)] = pb
            continue
        e["new"] = alloc.alloc(size92(e["items"]))
        moved92.append(e)
    new92 = {e["off"]: e["new"] for e in moved92}
    for e in moved92:
        out = bytearray()
        for it in e["items"]:
            if isinstance(it, bytes):
                out += it
            else:
                tgt = new92.get(it[2], cpu_addr(it[2]))
                out += call_stub(tgt, ret=it[1] != "call") if it[1] != "call" else call_stub(tgt, False)
        f = snes_to_file(e["new"])
        rom[f:f + len(out)] = out
    short92 = []
    for e in moved92:
        o, ln = e["off"], e["len"]
        if ln >= 5:
            rom[o:o + 5] = call_stub(e["new"])
            if ln > 5:
                dead.setdefault(o >> 16, []).append([o + 5, o + ln])
        else:
            short92.append(e)
    for e in short92:
        if e["ptr"] is None:
            print("ERROR %s %s: original is only %d bytes and is not reached through a table;"
                  " the translation must fit into %d bytes" % (e["file"], e["key"], e["len"], e["len"]))
            sys.exit(1)
        need = 6 if e.get("attr") else 5
        spot = None
        for rng in dead.get(e["off"] >> 16, []):
            if rng[1] - rng[0] >= need:
                spot = rng[0] + need - 5
                rng[0] += need
                break
        if spot is None:
            print("ERROR: no room for stub of short string", e["key"])
            sys.exit(1)
        if e.get("attr"):
            rom[spot - 1] = orig[e["off"] - 1]
        rom[spot:spot + 5] = call_stub(e["new"])
        rom[e["ptr"]:e["ptr"] + 2] = (spot & 0xFFFF).to_bytes(2, "little")

    # ---------------- location name banners ("FF <name> D4" in map data), in place
    loc_file = os.path.join(script_dir, "locations.txt")
    if os.path.exists(loc_file) and "locations" in meta:
        lscript = read_script(loc_file)
        for i, e in enumerate(meta["locations"]):
            key = "loc:%03d" % i
            if key not in lscript:
                continue
            body = lscript[key].strip()
            try:
                items = tokenize(body, table, two)
            except (EncodeError, ValueError) as ex:
                print("ERROR locations.txt %s: %s" % (key, ex))
                sys.exit(1)
            data = b"".join(x for x in items if isinstance(x, bytes))
            if len(data) > e["len"]:
                print("ERROR locations.txt %s: '%s' takes %d bytes, only %d fit (%s)"
                      % (key, body, len(data), e["len"], e["text"]))
                sys.exit(1)
            for o in e["offs"]:
                rom[o:o + len(data)] = data
                rom[o + len(data)] = 0xD4

    # ---------------- graphic texts on boot / title screens (screens.txt)
    scr_file = os.path.join(script_dir, "screens.txt")
    if os.path.exists(scr_file):
        import screens
        sscript = {k: v.strip() for k, v in read_script(scr_file).items()}
        try:
            if sscript.get("presents") and sscript["presents"] != "PRESENTS":
                screens.patch_presents(rom, orig, sscript["presents"])
            if sscript.get("push_start") and sscript["push_start"] != "PUSH START BUTTON":
                screens.patch_push_start(rom, orig, sscript["push_start"], alloc)
            if sscript.get("credit_title") and sscript.get("credit_name"):
                screens.patch_credits(rom, orig, sscript["credit_title"], sscript["credit_name"], alloc)
        except ValueError as ex:
            print("ERROR screens.txt:", ex)
            sys.exit(1)

    # $00:8000-FFFF mirror of bank 0 (refresh after patching)
    rom[0x408000:0x410000] = rom[0x8000:0x10000]
    fix_header(rom)
    open(out_path, "wb").write(rom)
    print("segments: %d total, %d relocated (%d short); table/menu/extra strings changed: %d (%d relocated)"
          % (len(segs), len(changed), len(short), tchanged, len(moved92)))
    print("expansion used: %d bytes of %d" % (alloc.used(), 0x3F * 0x8000 - 0x1000))
    if warnings:
        wpath = os.path.splitext(out_path)[0] + "_warnings.txt"
        open(wpath, "w", encoding="utf-8").write("\n".join(warnings))
        print("warnings: %d long lines -> %s" % (len(warnings), wpath))
    if ips:
        write_ips(orig, rom, ips)
        assert apply_ips(orig, open(ips, "rb").read()) == rom, "IPS self-test failed"
        print("wrote", ips, "(self-test OK)")
    if bps:
        write_bps(orig, rom, bps)
        assert apply_bps(orig, open(bps, "rb").read()) == rom
        print("wrote", bps, "(self-test OK)")
    return rom


if __name__ == "__main__":
    a = sys.argv[1:]
    opts = {"force": "--force-relocate" in a, "english": "--english" in a}
    for flag in ("--ips", "--bps"):
        if flag in a:
            opts[flag[2:]] = a[a.index(flag) + 1]
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or a[i - 1] not in ("--ips", "--bps"))]
    build(pos[0], pos[1], pos[2], **opts)
