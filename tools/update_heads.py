"""Edit the <head> of many site pages at once (pages don't share a layout file).

Dry run by default: prints what would change. Add --write to save.
Only the <head>...</head> part of each page is touched; line endings are kept as they are.

Examples
  python tools/update_heads.py replace '<old tag>' '<new tag>'
  python tools/update_heads.py add '<meta name="x" content="y">'          # before </head>, skipped where present
  python tools/update_heads.py remove '<link rel="stylesheet" href="old.css">'
  python tools/update_heads.py add '<script ...></script>' --only blogs --write

By default it covers every page that has a <head>: the root *.html pages, blogs/*.html and
automation/templates/post_template.html (so future posts get the change too).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HEAD_RE = re.compile(r"<head\b[^>]*>.*?</head>", re.S | re.I)
SKIP = {"google3bd63faaf7fcb837.html"}  # Search Console verification file


def pages(only: str) -> list[Path]:
    groups = {
        "root": [p for p in sorted(REPO.glob("*.html")) if p.name not in SKIP],
        "blogs": sorted((REPO / "blogs").glob("*.html")),
        "template": [REPO / "automation" / "templates" / "post_template.html"],
    }
    if only == "all":
        return groups["root"] + groups["blogs"] + groups["template"]
    return groups[only]


def edit_head(head: str, action: str, a: str, b: str | None) -> str:
    if action == "replace":
        return head.replace(a, b or "")
    if action == "remove":
        # also drop the line the snippet sat on when it is alone on that line
        return re.sub(r"[ \t]*" + re.escape(a) + r"[ \t]*\r?\n?", "", head)
    if action == "add":
        if a in head:
            return head
        m = re.search(r"([ \t]*)</head>", head, re.I)
        indent = "    "
        eol = "\r\n" if "\r\n" in head else "\n"
        return head[:m.start()] + indent + a + eol + head[m.start():]
    raise ValueError(action)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["replace", "add", "remove"])
    ap.add_argument("snippet")
    ap.add_argument("replacement", nargs="?", help="replace only: the new text")
    ap.add_argument("--only", choices=["all", "root", "blogs", "template"], default="all")
    ap.add_argument("--write", action="store_true", help="save changes (default: dry run)")
    args = ap.parse_args(argv)
    if args.action == "replace" and args.replacement is None:
        ap.error("replace needs a replacement (use '' to delete)")

    changed, no_head = [], []
    for path in pages(args.only):
        text = path.read_bytes().decode("utf-8")
        m = HEAD_RE.search(text)
        if not m:
            no_head.append(path)
            continue
        new_head = edit_head(m.group(0), args.action, args.snippet, args.replacement)
        if new_head == m.group(0):
            continue
        changed.append(path)
        if args.write:
            path.write_bytes((text[:m.start()] + new_head + text[m.end():]).encode("utf-8"))

    for p in changed:
        print(("updated  " if args.write else "would update  ") + str(p.relative_to(REPO)))
    for p in no_head:
        print("no <head> " + str(p.relative_to(REPO)), file=sys.stderr)
    print(f"{len(changed)} file(s) {'updated' if args.write else 'would change (dry run; add --write)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
