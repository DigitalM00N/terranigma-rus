"""Menu / shop / item-description text in bank $92 (file 0x12xxxx).

Bank $92 holds text run by both text engines:
  engine 1 ($85:915A, dialogue) - shop/inn dialogues, some menu captions
  engine 2 ($85:869B, menus)    - item lists, status screen captions
Strings are found by scanning the text area: every byte right after a
terminator starts a candidate string.  Known tables are skipped.
"""
from tscript import CTRL_ARGS, CTRL_ARGS2, TERM1, TERM2, ParseError

DESC_TABLE = 0x128EA4      # 200 x ptr16 -> [attr byte][string]
DESC_COUNT = 200
REGION = (0x128000, 0x12EE00)
SKIP = [
    (0x128EA4, 0x129035),  # description pointer table
    (0x12C5CD, 0x12C603),  # D2 table
    (0x12C793, 0x12C993),  # E5 table
    (0x12D2BB, 0x12D4BB),  # E6 table
]


def parse_eng(rom, off, args, term, stops=(), limit=0x800, region_end=None):
    toks = []
    p = off
    while True:
        if p != off and p in stops:
            return toks, p, "fall"
        if p - off > limit or p >= (region_end or REGION[1]):
            raise ParseError("runaway")
        b = rom[p]
        if b < 0x80:
            toks.append(("t", b, p))
            p += 1
        elif b < 0xC0:
            toks.append(("w", (b << 8) | rom[p + 1], p))
            p += 2
        else:
            if b not in args:
                raise ParseError("bad code %02X" % b)
            n = args[b]
            toks.append(("c", b, bytes(rom[p + 1:p + 1 + n]), p))
            p += 1 + n
            if b in term:
                return toks, p, "term"


def parse_menu(rom, off, stops=(), region_end=None):
    """Try engine 1 and engine 2. Returns (engine, toks, end, how) or raises."""
    if region_end is None:
        region_end = REGION[1] if REGION[0] <= off < REGION[1] else (off & ~0xFFFF) + 0x10000
    res = {}
    for eng, args, term in ((1, CTRL_ARGS, TERM1), (2, CTRL_ARGS2, TERM2)):
        a = dict(args)
        a.pop(0xE0, None) if eng == 1 else None  # E0 does not exist in original engine 1
        try:
            res[eng] = parse_eng(rom, off, a, term, stops, region_end=region_end)
        except ParseError:
            pass
    if not res:
        raise ParseError("no engine parses %06X" % off)
    if len(res) == 2:
        t1, t2 = res[1], res[2]
        if t1[1] == t2[1] and [x[:3] for x in t1[0]] == [x[:3] for x in t2[0]]:
            return (0,) + t1          # engine-neutral
        # fewer unknown glyphs wins (a wrong arg count turns params into glyphs)
        w1, w2 = weird(t1[0]), weird(t2[0])
        if w1 != w2:
            return (1,) + t1 if w1 < w2 else (2,) + t2
        # prefer engine-specific evidence
        c1 = {t[1] for t in t1[0] if t[0] == "c"}
        c2 = {t[1] for t in t2[0] if t[0] == "c"}
        if c1 & {0xC1, 0xC4, 0xDA, 0xDB, 0xDC, 0xDF, 0xDE}:
            return (1,) + t1
        if c2 & {0xE0, 0xE2}:
            return (2,) + t2
        # engine 1 reads C2 with 4 args, engine 2 with 5: a lone glyph between C2 and
        # the next control code means the 5th arg was misread as text -> engine 2
        tk = t1[0]
        for k, t in enumerate(tk[:-2]):
            if t[0] == "c" and t[1] == 0xC2 and tk[k + 1][0] == "t" and tk[k + 2][0] == "c":
                return (2,) + t2
        raise ParseError("ambiguous engine at %06X" % off)
    eng = next(iter(res))
    return (eng,) + res[eng]


def weird(toks):
    from tscript import ENG
    return sum(1 for t in toks if t[0] == "w" or (t[0] == "t" and t[1] not in ENG))


def has_letters(toks):
    """Real text: at least one run of 2+ letters and more letters than odd glyphs."""
    run = best = letters = 0
    for t in toks:
        if t[0] == "t" and (0x21 <= t[1] <= 0x3A or 0x41 <= t[1] <= 0x5A):
            run += 1
            letters += 1
            best = max(best, run)
        else:
            run = 0
    return best >= 2 and letters > weird(toks)


def in_skip(p, extra):
    for a, b in SKIP + extra:
        if a <= p < b:
            return True
    return False


def ptr_table_len(rom, p, limit=300):
    """Length of a pointer table at p: consecutive words pointing into the text region,
    where the smallest target is the byte right after the table (or after its attr byte)."""
    words = []
    while len(words) < limit:
        w = rom[p + 2 * len(words)] | (rom[p + 2 * len(words) + 1] << 8)
        if w < 0x8000 or not (REGION[0] <= (0x120000 | w) < REGION[1]):
            break
        words.append(0x120000 | w)
    for k in range(len(words), 1, -1):
        m = min(words[:k])
        if m in (p + 2 * k, p + 2 * k + 1):
            return k
    return 0


def find_ptr_tables(rom):
    """Pointer tables placed right after a terminator inside the text area."""
    tables = []
    q = REGION[0]
    while q < REGION[1]:
        if rom[q] in (0xD3, 0xD4, 0xD7, 0xC0):
            n = ptr_table_len(rom, q + 1)
            if n >= 2:
                tables.append((q + 1, n))
                q += 1 + 2 * n
                continue
        q += 1
    return tables


def find_menu_strings(rom, table_ranges):
    """Returns (desc entries, region strings).
    desc: list of (index, off) ; region: sorted list of offsets."""
    desc = []
    starts = set()
    for i in range(DESC_COUNT):
        p = rom[DESC_TABLE + 2 * i] | (rom[DESC_TABLE + 2 * i + 1] << 8)
        off = 0x120000 | p
        desc.append((i, off))
        starts.add(off)
    desc_ranges = []
    for _, off in desc:
        try:
            _, toks, end, _ = parse_menu(rom, off)
        except ParseError:
            continue
        desc_ranges.append((off - 1, end))  # include attr byte
    region = []
    cand = [REGION[0]]
    for q in range(REGION[0], REGION[1]):
        if rom[q] in (0xD3, 0xD4, 0xD7, 0xC0):
            cand.append(q + 1)
    ptabs = find_ptr_tables(rom)
    for tp, n in ptabs:
        desc_ranges.append((tp, tp + 2 * n))
        cand.append(tp + 2 * n)
        if n > 256:  # tile maps etc.: not text pointers
            continue
        for k in range(n):
            cand.append(0x120000 | rom[tp + 2 * k] | (rom[tp + 2 * k + 1] << 8))
    for c in cand:
        if c in starts or in_skip(c, table_ranges + desc_ranges):
            continue
        try:
            eng, toks, end, how = parse_menu(rom, c)
        except ParseError:
            continue
        if has_letters(toks) and not any(in_skip(t[-1], table_ranges + desc_ranges) for t in toks):
            region.append(c)
    return desc, sorted(set(region))


def strict_text(toks):
    """No unknown glyphs, a word of 3+ letters, and a space (or 4+ letters)."""
    if weird(toks):
        return False
    run = best = sp = let = 0
    for t in toks:
        if t[0] == "t" and (0x21 <= t[1] <= 0x3A or 0x41 <= t[1] <= 0x5A):
            run += 1
            let += 1
            best = max(best, run)
        else:
            run = 0
        if t[0] == "t" and t[1] == 0x20:
            sp += 1
    return best >= 3 and (sp >= 1 or let >= 4)


def find_gap_strings(rom, covered, banks, ends, terms=(0xD3, 0xD4, 0xD7)):
    """Text-engine strings in `banks` that no known reference reaches.
    A candidate must start exactly where known text ends (`ends`: set of end offsets
    of known entries) and look like real text; accepted strings extend the chain."""
    found = []
    ends = set(ends)
    bank_set = set(banks)
    changed = True
    while changed:
        changed = False
        for c in sorted(ends):
            if (c >> 16) not in bank_set or covered[c] or rom[c - 1] not in terms:
                continue
            hi = (c & ~0xFFFF) + 0x10000
            try:
                eng, toks, end, how = parse_menu(rom, c, region_end=hi)
            except ParseError:
                continue
            if any(covered[x] for x in range(c, end)) or not strict_text(toks):
                continue
            found.append((c, end, eng))
            covered[c:end] = b"" * (end - c)
            ends.add(end)
            changed = True
    return found
