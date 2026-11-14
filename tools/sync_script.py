"""Bring script/rus up to date after a new extraction.

Entries are taken in the order of script/eng; an entry that already exists in
script/rus keeps its (possibly translated) text, new entries get the English text.
Entries that disappeared are saved to script/rus_removed.txt if they were changed.

Usage: python tools/sync_script.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from insert import read_script  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENG = os.path.join(ROOT, "script", "eng")
RUS = os.path.join(ROOT, "script", "rus")


def main():
    os.makedirs(RUS, exist_ok=True)
    old_all = {}
    for fn in os.listdir(RUS):
        if fn.endswith(".txt"):
            for k, v in read_script(os.path.join(RUS, fn)).items():
                old_all[k] = v
    eng_all = {}
    added = 0
    for fn in sorted(os.listdir(ENG)):
        if not fn.endswith(".txt"):
            continue
        eng = read_script(os.path.join(ENG, fn))
        eng_all.update(eng)
        out = []
        for k, v in eng.items():
            if k in old_all:
                body = old_all[k]
            else:
                body = v
                added += 1
            out.append("=== %s ===\n%s\n" % (k, body))
        with open(os.path.join(RUS, fn), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out))
    removed = {k: v for k, v in old_all.items() if k not in eng_all}
    if removed:
        with open(os.path.join(ROOT, "script", "rus_removed.txt"), "a", encoding="utf-8", newline="\n") as f:
            for k, v in removed.items():
                f.write("=== %s ===\n%s\n\n" % (k, v))
    print("added %d new entries, %d entries no longer exist%s" % (
        added, len(removed), " (saved to script/rus_removed.txt)" if removed else ""))


if __name__ == "__main__":
    main()
