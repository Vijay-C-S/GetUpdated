"""GU-Markdown -> house article HTML.

The writer model returns the article in this compact markup instead of HTML (about a third fewer
output tokens: no class names, no table/div tags); this turns it into exactly the HTML the site's
CSS expects. Syntax:

  ## Section            -> <h2>        ### Sub -> <h3>
  paragraph lines       -> <p>         (blank line between blocks)
  - item / 1. item      -> <ul>/<ol>
  | Particular | Details |   (first row = header; a |---| row is optional)
                        -> house table, first column in <strong>
  :::tip Title          -> tip-box  (also :::warning, :::alert, :::info)
  text...
  :::
  !> one line           -> <p class="key-point">
  **bold**  *italic*  [Label](https://...)  {red:text} {green:text} {amber:text}
"""
from __future__ import annotations

import html
import re

BOX_ICON = {"tip": "fa-lightbulb", "warning": "fa-triangle-exclamation", "alert": "fa-clock", "info": ""}
LINK_ICON = ' <i class="fas fa-external-link-alt"></i>'


def inline(text: str) -> str:
    links: list[str] = []

    def keep_link(m: re.Match) -> str:
        url = html.unescape(m.group(2)).strip()
        links.append(f'<a href="{url}" class="official-link" target="_blank" rel="noopener">'
                     f"{m.group(1)}{LINK_ICON}</a>")
        return f"\x00{len(links) - 1}\x00"

    t = html.escape(text.strip(), quote=False)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", keep_link, t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"\{(red|green|amber):([^{}]+)\}", r'<span class="hl-\1">\2</span>', t)
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], t)


def _table(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    cells = [r for r in cells if not all(re.fullmatch(r":?-{2,}:?", c) for c in r if c)]
    if not cells:
        return ""
    head, body = cells[0], cells[1:]
    out = ['<div class="table-responsive"><table class="data-table"><thead><tr>'
           + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"]
    for r in body:
        tds = []
        for i, c in enumerate(r):
            v = inline(c)
            tds.append(f"<td><strong>{v}</strong></td>" if i == 0 and not v.startswith("<strong>") else f"<td>{v}</td>")
        out.append("<tr>" + "".join(tds) + "</tr>")
    return "\n".join(out) + "\n</tbody></table></div>"


def _blocks(lines: list[str]) -> list[str]:
    out, i, para = [], 0, []

    def flush() -> None:
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if not s:
            flush()
            i += 1
            continue
        m = re.match(r":::\s*(tip|warning|alert|info)\b\s*(.*)", s, re.I)
        if m:
            flush()
            kind, title = m.group(1).lower(), m.group(2).strip()
            j = i + 1
            while j < len(lines) and lines[j].strip() != ":::":
                j += 1
            icon = f'<i class="fas {BOX_ICON[kind]}"></i> ' if BOX_ICON[kind] else ""
            head = f"<h4>{icon}{inline(title)}</h4>" if title else ""
            out.append(f'<div class="{kind}-box">{head}' + "".join(_blocks(lines[i + 1:j])) + "</div>")
            i = j + 1
            continue
        if s.startswith("#"):
            flush()
            level = min(len(s) - len(s.lstrip("#")), 4)
            out.append(f"<h{level}>{inline(s.lstrip('#'))}</h{level}>")
        elif s.startswith("!>"):
            flush()
            out.append(f'<p class="key-point">{inline(s[2:])}</p>')
        elif s.startswith("|"):
            flush()
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            out.append(_table(lines[i:j]))
            i = j
            continue
        elif re.match(r"([-*•]|\d+[.)])\s+", s):
            flush()
            ordered = bool(re.match(r"\d+[.)]\s", s))
            items, j = [], i
            while j < len(lines) and re.match(r"\s*([-*•]|\d+[.)])\s+", lines[j]):
                items.append(re.sub(r"^\s*([-*•]|\d+[.)])\s+", "", lines[j]))
                j += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>\n" + "\n".join(f"<li>{inline(x)}</li>" for x in items) + f"\n</{tag}>")
            i = j
            continue
        else:
            para.append(s)
        i += 1
    flush()
    return out


def to_html(md: str) -> str:
    return "\n".join(_blocks(md.replace("\r\n", "\n").split("\n"))) + "\n"
