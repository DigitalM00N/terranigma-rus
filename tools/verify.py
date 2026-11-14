"""Static check of a built ROM: walk every text entry the way the engine does
(following E0/CC/CB jumps, name/str calls and returns) and compare the produced
glyph/control stream with the original ROM.

Usage:  python tools/verify.py <original.sfc> <built.sfc> [script_root]
For an English round-trip build the streams must be identical.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from tscript import CTRL_ARGS, CTRL_ARGS2  # noqa: E402

TABLE_CODES = {0xD2: 0x12C5CD, 0xE4: 0x12C793, 0xE5: 0x12C793, 0xE6: 0x12D2BB}


def to_file(addr, size):
    bank = addr >> 16
    if size > 0x400000 and bank <= 0x3F and (addr & 0xFFFF) >= 0x8000:
        return 0x400000 + bank * 0x10000 + (addr & 0xFFFF)
    return addr & 0x3FFFFF


def stream(rom, start_file, max_steps=20000, engine=1):
    out = []
    stack = []
    p = start_file
    size = len(rom)
    for _ in range(max_steps):
        b = rom[p]
        if b < 0x80:
            out.append(b)
            p += 1
            continue
        if b < 0xC0:
            out.append((b << 8) | rom[p + 1])
            p += 2
            continue
        n = (CTRL_ARGS2 if engine == 2 else CTRL_ARGS).get(b)
        if engine == 2 and b == 0xE0:
            n = 7
        if n is None:
            out.append(("BAD", b))
            return out
        args = rom[p + 1:p + 1 + n]
        nxt = p + 1 + n
        bank = p & 0xFF0000
        if b == 0xE0 and engine != 2:
            p = to_file(args[0] | args[1] << 8 | args[2] << 16, size)
            continue
        if b == 0xCB:
            p = bank | args[0] | args[1] << 8
            continue
        if b == 0xCC:
            stack.append(nxt)
            p = to_file(args[0] | args[1] << 8 | args[2] << 16, size)
            continue
        if b in TABLE_CODES:
            t = TABLE_CODES[b] + 2 * args[0]
            ptr = rom[t] | rom[t + 1] << 8
            if ptr < 0x8000:
                out.append(("RAM", ptr))
                p = nxt
                continue
            stack.append(nxt)
            pass  # dictionary call: its text simply continues the stream
            p = 0x120000 | ptr
            continue
        ends = (0xD3, 0xD4, 0xD7) if engine != 2 else (0xC0, 0xD3, 0xD4, 0xD7)
        if b in ends:
            if engine == 2 and b != 0xD4:
                out.append(b)
                out.append("END")
                return out
            if b != 0xD4:
                out.append(b)
            if stack:
                p = stack.pop()
                continue
            out.append("END")
            return out
        out.append((b, bytes(args)))
        p = nxt
    out.append("STEPS")
    return out


def main():
    orig = open(sys.argv[1], "rb").read()
    new = open(sys.argv[2], "rb").read()
    root = sys.argv[3] if len(sys.argv) > 3 else "script"
    meta = json.load(open(os.path.join(root, "refs.json")))
    bad = 0
    for sid, s in meta["segments"].items():
        a = stream(orig, s["off"])
        b = stream(new, s["off"])
        if a != b:
            bad += 1
            if bad <= 10:
                k = next(i for i in range(min(len(a), len(b))) if a[i] != b[i]) if any(
                    x != y for x, y in zip(a, b)) else min(len(a), len(b))
                print("MISMATCH", sid, "at token", k, a[k:k + 5], b[k:k + 5])
        # also check through the COP references (short segments are re-pointed)
        for ref in s["refs"]:
            if ref["type"] == "cop":
                at = ref["at"]
                tgt = (at & 0xFF0000) | new[at + 2] | new[at + 3] << 8
                c = stream(new, tgt)
                if c != a:
                    bad += 1
                    if bad <= 10:
                        print("MISMATCH via COP at %06X for %s" % (at, sid))
    # bank $92 strings: tables and menus, walked with the engine(s) that may run them
    extra = []
    for tname, t in meta["tables"].items():
        for e in t["entries"]:
            if "off" in e:
                extra.append((tname, e["off"], 0))
    for key, e in list(meta.get("menu", {}).items()) + list(meta.get("extra", {}).items()):
        extra.append((key, e["off"], e["engine"]))
    tabs = {"name": 0x12C5CD, "str2": 0x12C793, "str3": 0x12D2BB}
    for key, off, eng in extra:
        for engine in ((1, 2) if eng == 0 else (eng,)):
            a = stream(orig, off, engine=engine)
            b = stream(new, off, engine=engine)
            if a != b:
                bad += 1
                if bad <= 20:
                    print("MISMATCH bank92", key, "engine", engine)
    # table slots (short strings are re-pointed)
    for tname, t in meta["tables"].items():
        for i, e in enumerate(t["entries"]):
            if "off" not in e:
                continue
            slot = t["table"] + 2 * i
            tgt = 0x120000 | new[slot] | new[slot + 1] << 8
            for engine in (1, 2):
                if stream(new, tgt, engine=engine) != stream(orig, e["off"], engine=engine):
                    bad += 1
                    if bad <= 20:
                        print("MISMATCH via table", tname, i, "engine", engine)
    # description table slots: [attr][string]
    for key, e in meta.get("menu", {}).items():
        if key.startswith("desc:"):
            slot = 0x128EA4 + 2 * int(key[5:])
            tgt = 0x120000 | new[slot] | new[slot + 1] << 8
            if new[tgt - 1] != orig[e["off"] - 1] or any(
                    stream(new, tgt, engine=g) != stream(orig, e["off"], engine=g) for g in (1, 2)):
                bad += 1
                if bad <= 20:
                    print("MISMATCH via description table", key)
    print("segments checked: %d + bank92 %d, mismatches: %d" % (len(meta["segments"]), len(extra), bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
