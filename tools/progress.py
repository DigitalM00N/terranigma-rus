"""Translation progress: how many entries in script/rus differ from script/eng,
and which ones still contain Latin letters outside tags.

Usage: python tools/progress.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from insert import read_script  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENG = os.path.join(ROOT, "script", "eng")
RUS = os.path.join(ROOT, "script", "rus")
LATIN = re.compile(r"[A-Za-z]{3,}")


def plain(body):
    return re.sub(r"\[[^\]]*\]", "", body)


def main():
    total_done = total = 0
    for fn in sorted(os.listdir(ENG)):
        if not fn.endswith(".txt") or fn == "name_grid.txt":
            continue
        eng = read_script(os.path.join(ENG, fn))
        rus_path = os.path.join(RUS, fn)
        rus = read_script(rus_path) if os.path.exists(rus_path) else {}
        done = sum(1 for k, v in eng.items() if k in rus and rus[k] != v)
        has_text = [k for k, v in eng.items() if LATIN.search(plain(v))]
        left = [k for k in has_text if k not in rus or LATIN.search(plain(rus[k]))]
        total += len(has_text)
        total_done += len(has_text) - len(left)
        print("%-16s %4d/%-4d entries with text translated%s" % (
            fn, len(has_text) - len(left), len(has_text),
            ("  (next: %s)" % left[0]) if left else ""))
    pct = 100.0 * total_done / total if total else 100.0
    print("total: %d/%d (%.1f%%)" % (total_done, total, pct))


if __name__ == "__main__":
    main()
