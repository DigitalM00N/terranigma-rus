"""Extract Terranigma (Europe) dialogue + name tables into editable UTF-8 script files.

Usage:  python tools/extract.py "Terranigma (Europe).sfc" script/eng
Writes:
  <out>/dialog_XX.txt   - dialogue segments grouped by ROM bank (XX = file bank)
  <out>/tables.txt      - D2 / E5 / E6 string tables from bank $92
  <out>/../refs.json    - machine data needed by the inserter (do not edit)
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from tscript import CTRL_ARGS, CTRL_NAMES, CTRL_NAMES2, ENG, ParseError  # noqa: E402
import menus  # noqa: E402

NAME_GRID_TABLE = 0x078D5C
HERO_NAME_IMM = (0x078C8F, 0x078C94, 0x078C99)  # 3 letters, then D1 (0x078C9E) and D4 (0x078CA3)
TERM = (0xD3, 0xD4, 0xCB, 0xD7)  # D7 = end variant
ROOT_COPS = (0x1B, 0x1D)  # COP #$1B/#$1D <ptr16> : show text in caller's bank

# string tables in bank $92 (file 0x12xxxx): name, table offset, entry count
TABLES = [("name", 0x12C5CD, 25), ("str2", 0x12C793, 256), ("str3", 0x12D2BB, 256)]


def lid(off):
    return "%02X:%04X" % (off >> 16, off & 0xFFFF)


def parse_raw(rom, off, stops, limit=0x4000):
    """Parse until a terminator or until reaching an offset in `stops` (other entry)."""
    toks = []
    p = off
    bank_end = (off & ~0xFFFF) + 0x10000
    while True:
        if p != off and p in stops:
            return toks, p, "fall"
        if p >= bank_end or p - off > limit:
            raise ParseError("runaway at %06X" % off)
        b = rom[p]
        if b < 0x80:
            toks.append(("t", b, p))
            p += 1
        elif b < 0xC0:
            toks.append(("w", (b << 8) | rom[p + 1], p))
            p += 2
        else:
            if b not in CTRL_ARGS or b == 0xE0:
                raise ParseError("bad code %02X at %06X" % (b, p))
            n = CTRL_ARGS[b]
            toks.append(("c", b, bytes(rom[p + 1:p + 1 + n]), p))
            p += 1 + n
            if b in TERM:
                return toks, p, "term"


def looks_like_text(toks):
    letters = sum(1 for t in toks if t[0] == "t" and (0x21 <= t[1] <= 0x5A))
    glyphs = sum(1 for t in toks if t[0] == "t")
    ctrl_ok = any(t[0] == "c" and t[1] in (0xCF, 0xD5, 0xD3, 0xD4, 0xC1, 0xDA) for t in toks)
    spaces = sum(1 for t in toks if t[0] == "t" and t[1] == 0x20)
    if glyphs == 0:
        return any(t[0] == "c" and t[1] in (0xD2, 0xE4, 0xE5, 0xE6, 0xCC) for t in toks)
    if letters >= glyphs * 0.6 and ctrl_ok and (spaces > 0 or glyphs < 14):
        return True
    # lines like "........ Hm." : count punctuation as text, but allow no unknown glyphs
    punct = sum(1 for t in toks if t[0] == "t" and t[1] in ENG and not (0x21 <= t[1] <= 0x5A)
                and t[1] != 0x20)
    unknown = sum(1 for t in toks if t[0] == "w" or (t[0] == "t" and t[1] not in ENG))
    return (ctrl_ok and unknown == 0 and letters >= 2
            and letters + punct >= glyphs * 0.6 and (spaces > 0 or glyphs < 14))


def find_roots(rom):
    """COP #$1B/#$1D <ptr16> calls whose target parses as plausible text."""
    roots = defaultdict(list)
    for i in range(len(rom) - 4):
        if rom[i] == 0x02 and rom[i + 1] in ROOT_COPS:
            off = (i & 0xFF0000) | rom[i + 2] | (rom[i + 3] << 8)
            try:
                toks, _, _ = parse_raw(rom, off, set())
            except ParseError:
                continue
            if looks_like_text(toks):
                roots[off].append({"type": "cop", "at": i})
    return roots


def crawl(rom, roots):
    """Follow jmp/call references. Returns entries {off: refs}."""
    entries = defaultdict(list)
    for o, r in roots.items():
        entries[o].extend(r)
    queue = list(entries)
    done = set()
    while queue:
        o = queue.pop()
        if o in done:
            continue
        done.add(o)
        try:
            toks, _, _ = parse_raw(rom, o, set())
        except ParseError:
            continue
        for t in toks:
            if t[0] != "c":
                continue
            if t[1] == 0xCB:
                tgt = (o & 0xFF0000) | t[2][0] | (t[2][1] << 8)
                entries[tgt].append({"type": "jmp", "at": t[3]})
                queue.append(tgt)
            elif t[1] == 0xCC:
                tgt = ((t[2][2] & 0x3F) << 16) | t[2][0] | (t[2][1] << 8)
                entries[tgt].append({"type": "call", "at": t[3]})
                queue.append(tgt)
    return entries


# dictionary words (str/str2/str3 tables) are expanded inline into the script text
DICT = {}


def build_dict(rom):
    for name, toff, count in TABLES:
        if name == "name":
            continue
        for i in range(count):
            p = rom[toff + 2 * i] | (rom[toff + 2 * i + 1] << 8)
            if p < 0x8000:
                continue
            toks, _, _ = parse_raw(rom, 0x120000 | p, set())
            body = "".join(tok_text(t, set(), 0x120000) for t in toks)
            if body.endswith("[ret]") and "[" not in body[:-5]:
                DICT[(name, i)] = body[:-5]


def tok_text(t, entry_set, bank, eng=1):
    if t[0] == "t":
        ch = ENG.get(t[1])
        if ch is None:
            return "[g:%02X]" % t[1]
        if ch in "[]{}":
            return "[g:%02X]" % t[1]
        return ch
    if t[0] == "w":
        return "[w:%04X]" % t[1]
    code, args = t[1], t[2]
    if code == 0xCF:
        return "\n"
    if code == 0xD5:
        return "[wait]\n"
    if code == 0xCB:
        tgt = bank | args[0] | (args[1] << 8)
        return "[jmp:%s]" % lid(tgt)
    if code in (0xE4, 0xE5, 0xE6) and args:
        key = ("str3" if code == 0xE6 else "str2", args[0])
        if key in DICT:
            return DICT[key]
    if code == 0xCC:
        # keep the original bank byte ($C0-$FF or $80-$BF mirror) for exact round trips
        return "[call:%02X:%02X%02X]" % (args[2], args[1], args[0])
    if eng == 2:
        name = CTRL_NAMES2.get(code, "$%02X" % code)
    else:
        name = CTRL_NAMES.get(code, "%02X" % code)
    if args:
        return "[%s:%s]" % (name, " ".join("%02X" % a for a in args))
    return "[%s]" % name


def render(toks, entry_set, bank, eng=1):
    return "".join(tok_text(t, entry_set, bank, eng) for t in toks)


def main():
    rom_path, out = sys.argv[1], sys.argv[2]
    rom = open(rom_path, "rb").read()
    os.makedirs(out, exist_ok=True)

    build_dict(rom)
    roots = find_roots(rom)
    entries = crawl(rom, roots)
    # drop entries that do not parse
    good = {}
    for o, refs in entries.items():
        try:
            parse_raw(rom, o, set())
            good[o] = refs
        except ParseError:
            pass
    stops = set(good)

    segs = {}
    for o in sorted(good):
        toks, end, how = parse_raw(rom, o, stops)
        segs[o] = {"end": end, "fall": end if how == "fall" else None, "toks": toks}

    by_bank = defaultdict(list)
    for o in sorted(segs):
        by_bank[o >> 16].append(o)

    meta = {"segments": {}, "tables": {}}
    for bank, offs in sorted(by_bank.items()):
        lines = []
        for o in offs:
            s = segs[o]
            body = render(s["toks"], stops, o & 0xFF0000)
            if s["fall"] is not None:
                body += "[goto:%s]" % lid(s["fall"])
            lines.append("=== %s ===" % lid(o))
            lines.append(body.rstrip("\n") if not body.endswith("[wait]\n") else body[:-1])
            lines.append("")
            meta["segments"][lid(o)] = {
                "off": o, "len": s["end"] - o,
                "refs": good[o],
            }
        with open(os.path.join(out, "dialog_%02X.txt" % bank), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))

    # string tables
    tl = []
    for name, toff, count in TABLES:
        meta["tables"][name] = {"table": toff, "count": count, "entries": []}
        ptrs = [rom[toff + 2 * i] | (rom[toff + 2 * i + 1] << 8) for i in range(count)]
        for i, p in enumerate(ptrs):
            if p < 0x8000:  # RAM (player names etc.)
                meta["tables"][name]["entries"].append({"ram": p})
                continue
            off = 0x120000 | p
            toks, end, _ = parse_raw(rom, off, set())
            meta["tables"][name]["entries"].append({"off": off, "len": end - off})
            if name != "name":
                continue  # dictionary: expanded into the script, not translated separately
            tl.append("=== %s:%03d ===" % (name, i))
            tl.append(render(toks, set(), 0x120000))
            tl.append("")
    # default hero name: immediates in the name-entry code ($C7:8C8E: LDA #c / STA $0610.. x3)
    hero = "".join(ENG.get(rom[a], "[g:%02X]" % rom[a]) for a in HERO_NAME_IMM)
    tl.append("=== hero:default ===")
    tl.append(hero)
    tl.append("")
    # name-entry letter grid: 6 rows x 10 cells (input codes at $87:8D5C)
    gl = ["# Сетка букв экрана ввода имени: 6 строк по 10 клеток, через пробел.",
          "# Клетки: буква, _ (пробел), [bs] (стереть), [ed] (готово). Только однобайтовые буквы",
          "# (нельзя: Ё Й Щ Ъ Ы Ь заглавные). [bs] и [ed] должны быть хотя бы по одному разу."]
    for r in range(6):
        cells = []
        for c in range(10):
            b = rom[NAME_GRID_TABLE + r * 10 + c]
            cells.append({0x78: "[bs]", 0x40: "[ed]", 0x20: "_"}.get(b, ENG.get(b, "[g:%02X]" % b)))
        gl.append(" ".join(cells))
    with open(os.path.join(out, "name_grid.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(gl) + "\n")
    with open(os.path.join(out, "tables.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(tl))

    # ---------------- menu / shop / item description strings (bank $92)
    table_ranges = [(e["off"], e["off"] + e["len"]) for t in meta["tables"].values()
                    for e in t["entries"] if "off" in e]
    desc, region = menus.find_menu_strings(rom, table_ranges)
    starts = set(region) | {o for _, o in desc}
    # add jump/call targets inside bank $92 that land inside known strings; drop strings
    # whose jumps lead somewhere unknown (those are probably data, not text)
    changed = True
    while changed:
        changed = False
        for o in sorted(starts):
            try:
                _, toks, end, _ = menus.parse_menu(rom, o, starts)
            except ParseError:
                starts.discard(o)
                changed = True
                break
            for t in toks:
                if t[0] == "c" and t[1] in (0xCB, 0xCC):
                    tgt = (0x120000 | t[2][0] | t[2][1] << 8) if t[1] == 0xCB else                         (((t[2][2] & 0x3F) << 16) | t[2][0] | t[2][1] << 8)
                    if tgt in starts:
                        continue
                    ok = menus.REGION[0] <= tgt < menus.REGION[1]
                    if ok:
                        try:
                            menus.parse_menu(rom, tgt)
                        except ParseError:
                            ok = False
                    if ok:
                        starts.add(tgt)
                    else:
                        starts.discard(o)
                    changed = True
                    break
            if changed:
                break
    # default hero name (copied to RAM $0610 when a new game starts), right after the D2 table
    starts.add(0x12C5FF)
    desc_ids = {o: i for i, o in desc}
    ml = []
    meta["menu"] = {}
    for o in sorted(starts):
        eng, toks, end, how = menus.parse_menu(rom, o, starts)
        body = render(toks, starts, 0x120000, eng)
        if how == "fall":
            body += "[goto:%s]" % lid(end)
        key = ("desc:%03d" % desc_ids[o]) if o in desc_ids else lid(o)
        ml.append("=== %s ===" % key)
        ml.append(body.rstrip("\n") if not body.endswith("[wait]\n") else body[:-1])
        ml.append("")
        meta["menu"][key] = {"off": o, "len": end - o, "engine": eng}
    with open(os.path.join(out, "menu_92.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(ml))
    print("menu strings: %d (%d item descriptions)" % (len(starts), len(desc)))

    # ---------------- strings no known reference reaches (gap scan)
    covered = bytearray(len(rom))
    for d_ in list(meta["segments"].values()) + list(meta["menu"].values()):
        covered[d_["off"]:d_["off"] + d_["len"]] = b"" * d_["len"]
    for a_, b_ in table_ranges:
        covered[a_:b_] = b"" * (b_ - a_)
    for tp, n in menus.find_ptr_tables(rom):
        covered[tp:tp + 2 * n] = b"" * (2 * n)
    ends = {d_["off"] + d_["len"] for d_ in list(meta["segments"].values()) + list(meta["menu"].values())}
    ends |= {b_ for a_, b_ in table_ranges}
    gaps = menus.find_gap_strings(rom, covered, list(range(0x08, 0x1A)), ends)
    el = []
    meta["extra"] = {}
    for c, end, eng in sorted(gaps):
        _, toks, end2, how = menus.parse_menu(rom, c)
        body = render(toks, set(), c & 0xFF0000, eng)
        key = lid(c)
        el.append("=== %s ===" % key)
        el.append(body.rstrip("\n") if not body.endswith("[wait]\n") else body[:-1])
        el.append("")
        meta["extra"][key] = {"off": c, "len": end - c, "engine": eng}
    with open(os.path.join(out, "extra.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(el))
    print("extra strings (not reached by known references): %d" % len(gaps))

    # ---------------- location name banners: "FF <name> D4" inside map data (translated in place)
    import re as _re
    locs = {}
    for m in _re.finditer(b"\xff([\x20-\x7f]{3,24})\xd4", rom):
        raw = m.group(1)
        if not (0x21 <= raw[0] <= 0x3A):
            continue
        txt = "".join(ENG.get(b, "\u00a4") for b in raw)
        if "\u00a4" in txt or sum(c.isalpha() for c in txt) < 3:
            continue
        locs.setdefault(txt, []).append(m.start() + 1)
    meta["locations"] = []
    ll = ["# Названия локаций (надписи при входе). Пишутся на место английских:",
          "# перевод не длиннее английского (Щ Ъ Ы Ь Й заглавные считаются за 2).", ""]
    for i, (txt, offs) in enumerate(locs.items()):
        meta["locations"].append({"text": txt, "offs": offs, "len": len(txt)})
        ll.append("=== loc:%03d ===" % i)
        ll.append(txt)
        ll.append("")
    with open(os.path.join(out, "locations.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(ll))
    print("location names: %d unique (%d places in ROM)" % (len(locs), sum(len(v) for v in locs.values())))

    with open(os.path.join(os.path.dirname(os.path.abspath(out)), "refs.json"), "w") as f:
        json.dump(meta, f, indent=0)

    nseg = len(segs)
    nbytes = sum(s["end"] - o for o, s in segs.items())
    print("segments: %d (%d bytes) in %d banks; tables: %d strings"
          % (nseg, nbytes, len(by_bank), sum(len(t["entries"]) for t in meta["tables"].values())))


if __name__ == "__main__":
    main()
