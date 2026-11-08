"""Render a desk page to safe HTML for the read-only page view.

* Raw HTML in the page is *not* passed through (markdown-it ``html=False``), and
  HTML comments (doubt/finding templates) are removed before rendering.
* Headings get GitHub-style ``id``s (same slugs the link resolver uses), so
  ``#anchor`` links work.
* Links are rewritten from the resolved links stored in the index:
    - an ``ok`` link to a page   -> ``#/page/<rel>`` (+ ``?h=<anchor>``), handled by the frontend;
    - external ``http(s)``/``mailto`` -> kept, opened in a new tab;
    - anything else (broken, private, asset, folder, unknown) -> no ``href``, just a
      styled ``<span>``-like anchor with a ``title`` explaining why. So nothing
      in the page view can point at a private area or a raw file path.
* Local images are not loaded (shown as their alt text); remote images are kept as links.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

from markdown_it import MarkdownIt

from .links import slugify
from .parser import WIKILINK_RE

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_EXTERNAL_OK = re.compile(r"^(https?:|mailto:)", re.I)
WIKI_SCHEME = "deskbrain-wiki:"


def _md() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable(["table", "strikethrough"])
    return md


_MD = _md()


def _wikilinks_to_md(text: str) -> str:
    """Turn ``[[target|alias]]`` (outside code fences) into ``[alias](deskbrain-wiki:N)``.

    N is the index into the page's wikilinks in order, matching the stored link order.
    """
    out, in_fence, n = [], False, 0
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence:
            def sub(m: re.Match) -> str:
                nonlocal n
                label = (m.group(3) or m.group(1)).strip().replace("[", "").replace("]", "")
                s = f"[{label}]({WIKI_SCHEME}{n})"
                n += 1
                return s
            line = WIKILINK_RE.sub(sub, line)
        out.append(line)
    return "\n".join(out)


def page_href(rel: str, anchor: str | None = None) -> str:
    h = "#/page/" + quote(rel, safe="/")
    if anchor:
        h += "?h=" + quote(slugify(unquote(anchor)).strip("-"), safe="")
    return h


def render_page(text: str, rel: str, links: list[dict]) -> str:
    """HTML for one page. ``links`` are the page's resolved links (``Index.links_from``)."""
    text = _wikilinks_to_md(_COMMENT.sub("", text))
    wiki = [l for l in links if l["kind"] == "wiki"]
    by_raw: dict[str, dict] = {}
    for l in links:
        if l["kind"] != "wiki":
            by_raw.setdefault(l["raw"], l)

    tokens = _MD.parse(text)
    seen_slugs: dict[str, int] = {}
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            inline = tokens[i + 1]
            base = slugify(inline.content)
            k = seen_slugs.get(base, 0)
            seen_slugs[base] = k + 1
            tok.attrSet("id", (base if k == 0 else f"{base}-{k}").strip("-") or f"h{i}")
        if tok.type != "inline" or not tok.children:
            continue
        for child in tok.children:
            if child.type == "link_open":
                _rewrite_link(child, rel, by_raw, wiki)
            elif child.type == "image":
                src = child.attrGet("src") or ""
                if not _EXTERNAL_OK.match(src):
                    # Local image: do not load files from the desk; show alt text.
                    child.type = "text"
                    child.content = f"[image: {child.content or src}]"
                    child.children = None
    html = _MD.renderer.render(tokens, _MD.options, {})
    # Wide tables scroll inside their own box on phones (raw HTML is escaped, so these tags are ours).
    return html.replace("<table>", '<div class="table-scroll"><table>').replace("</table>", "</table></div>")


def _rewrite_link(tok, rel: str, by_raw: dict[str, dict], wiki: list[dict]) -> None:
    href = tok.attrGet("href") or ""
    link = None
    if href.startswith(WIKI_SCHEME):
        try:
            link = wiki[int(href[len(WIKI_SCHEME):])]
        except (ValueError, IndexError):
            link = None
    else:
        link = by_raw.get(href) or by_raw.get(unquote(href))
    tok.attrs.pop("href", None)
    if link is None:
        if _EXTERNAL_OK.match(href):
            _external(tok, href)
        else:
            tok.attrSet("class", "link-unknown")
            tok.attrSet("title", "unresolved link")
        return
    status = link["status"]
    if status == "external" and _EXTERNAL_OK.match(href):
        _external(tok, href)
    elif status == "ok" and link["target"] and link["target"].lower().endswith(".md"):
        tok.attrSet("href", page_href(link["target"], link["anchor"]))
        tok.attrSet("class", "link-page")
    elif status == "excluded":
        tok.attrSet("class", "link-private")
        tok.attrSet("title", "private area (not shown)")
    elif status in ("broken", "bad-anchor", "outside"):
        tok.attrSet("class", "link-broken")
        tok.attrSet("title", f"{status} link")
    else:  # asset, folder, external with odd scheme
        tok.attrSet("class", "link-asset")
        tok.attrSet("title", f"{status}: {link['target'] or ''}")


def _external(tok, href: str) -> None:
    tok.attrSet("href", href)
    tok.attrSet("class", "link-external")
    tok.attrSet("target", "_blank")
    tok.attrSet("rel", "noopener noreferrer")
