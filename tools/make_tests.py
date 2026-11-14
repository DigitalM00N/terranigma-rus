"""Build the two test ROMs for emulator checks.

  test_eng.sfc : English text, but every string is moved to the new area
                 (tests the relocation mechanism; must play exactly like the original)
  test_cyr.sfc : every letter replaced by Cyrillic (tests font and encoding)

Usage: python tools/make_tests.py "Terranigma (Europe).sfc"
"""
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
import insert  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOW = "абвгдежзийклмнопрстуфхцчшщъыьэюя"
UP = "АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"


def cyrillize(body):
    out = []
    k = 0
    for part in re.split(r"(\[[^\]]*\])", body):
        if part.startswith("["):
            out.append(part)
            continue
        s = []
        for ch in part:
            if "a" <= ch <= "z":
                s.append(LOW[k % len(LOW)])
                k += 1
            elif "A" <= ch <= "Z":
                s.append(UP[k % len(UP)])
                k += 1
            else:
                s.append(ch)
        out.append("".join(s))
    return "".join(out)


def main():
    rom = sys.argv[1]
    eng = os.path.join(ROOT, "script", "eng")
    build = os.path.join(ROOT, "build")
    os.makedirs(build, exist_ok=True)

    insert.build(rom, eng, os.path.join(build, "test_eng.sfc"), english=True, force=True)

    tmp = os.path.join(ROOT, "work", "cyr_test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for fn in os.listdir(eng):
        txt = open(os.path.join(eng, fn), encoding="utf-8").read()
        parts = re.split(r"(^=== \S+ ===$)", txt, flags=re.M)
        res = [parts[0]]
        for i in range(1, len(parts), 2):
            res.append(parts[i])
            res.append(cyrillize(parts[i + 1]))
        open(os.path.join(tmp, fn), "w", encoding="utf-8", newline="\n").write("".join(res))
    # Russian name-entry grid and a Cyrillic default hero name
    shutil.copy(os.path.join(ROOT, "script", "rus", "name_grid.txt"), os.path.join(tmp, "name_grid.txt"))
    t = open(os.path.join(tmp, "tables.txt"), encoding="utf-8").read()
    t = re.sub(r"(=== hero:default ===\n)[^\n]*", "\\1Арк", t)
    open(os.path.join(tmp, "tables.txt"), "w", encoding="utf-8", newline="\n").write(t)
    insert.build(rom, tmp, os.path.join(build, "test_cyr.sfc"))
    for f in ("test_eng", "test_cyr"):
        w = os.path.join(build, f + "_warnings.txt")
        if os.path.exists(w):
            os.remove(w)


if __name__ == "__main__":
    main()
