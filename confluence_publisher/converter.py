from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import mistletoe
from mistletoe import Document
from mistletoe.base_renderer import BaseRenderer

logger = logging.getLogger(__name__)


class ConversionError(Exception):
    pass


@dataclass
class ConversionResult:
    body: str                           # without banner, used for content hashing
    full_body: str                      # banner + body, what gets published
    images: list[str] = field(default_factory=list)         # repo-root-relative paths of local images
    mermaid_blocks: list[str] = field(default_factory=list) # source of each mermaid diagram in order


class ConfluenceRenderer(BaseRenderer):
    def __init__(
        self,
        source_path: str = "<unknown>",
        page_id_map: dict[str, str] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.source_path = source_path
        self._page_id_map = page_id_map or {}
        self._source_dir = str(PurePosixPath(source_path).parent)
        self.images: list[str] = []
        self.mermaid_blocks: list[str] = []

    def render(self, token):
        node_type = type(token).__name__
        if node_type not in self.render_map:
            raise ConversionError(
                f"Unsupported Markdown element '{node_type}' in '{self.source_path}'. "
                f"Remove or convert to a supported syntax node."
            )
        return self.render_map[node_type](token)

    # --- Block tokens ---

    def render_document(self, token):
        return self.render_inner(token)

    def render_heading(self, token):
        inner = self.render_inner(token)
        return f"<h{token.level}>{inner}</h{token.level}>"

    def render_paragraph(self, token):
        return f"<p>{self.render_inner(token)}</p>"

    def render_quote(self, token):
        return f"<blockquote>{self.render_inner(token)}</blockquote>"

    def render_thematic_break(self, token):
        return "<hr/>"

    def render_block_code(self, token):
        # Handles both CodeFence (``` blocks) and BlockCode (indented blocks).
        code = token.children[0].content if token.children else ""
        language = getattr(token, "language", "") or ""
        if language == "mermaid":
            idx = len(self.mermaid_blocks)
            self.mermaid_blocks.append(code)
            return f'<ac:image><ri:attachment ri:filename="mermaid-{idx}.png"/></ac:image>'
        lang_param = (
            f'<ac:parameter ac:name="language">{_escape(language)}</ac:parameter>'
            if language
            else ""
        )
        return (
            f'<ac:structured-macro ac:name="code">'
            f"{lang_param}"
            f"<ac:plain-text-body><![CDATA[{_escape_cdata(code)}]]></ac:plain-text-body>"
            f"</ac:structured-macro>"
        )

    def render_list(self, token):
        tag = "ol" if token.start is not None else "ul"
        return f"<{tag}>{self.render_inner(token)}</{tag}>"

    def render_list_item(self, token):
        return f"<li>{self.render_inner(token)}</li>"

    def render_table(self, token):
        header_row = f"<tr>{self._render_row(token.header, header=True)}</tr>"
        body_rows = "".join(
            f"<tr>{self._render_row(row, header=False)}</tr>"
            for row in token.children
        )
        return f"<table><tbody>{header_row}{body_rows}</tbody></table>"

    def _render_row(self, row, header: bool) -> str:
        tag = "th" if header else "td"
        return "".join(
            f"<{tag}><p>{self.render_inner(cell)}</p></{tag}>"
            for cell in row.children
        )

    def render_table_row(self, token):
        return ""

    def render_table_cell(self, token):
        return self.render_inner(token)

    def render_strikethrough(self, token):
        raise ConversionError(
            f"Strikethrough (~~text~~) is not supported ('{self.source_path}'). "
            f"Remove or rewrite as plain text."
        )

    # --- Inline tokens ---

    def render_raw_text(self, token):
        return _escape(token.content)

    def render_strong(self, token):
        return f"<strong>{self.render_inner(token)}</strong>"

    def render_emphasis(self, token):
        return f"<em>{self.render_inner(token)}</em>"

    def render_inline_code(self, token):
        code = token.children[0].content
        return f"<code>{_escape(code)}</code>"

    def render_link(self, token):
        target = token.target
        if (
            not _is_external(target)
            and not target.startswith("#")
            and target.rstrip("?#").endswith(".md")
        ):
            # Strip any fragment/query from the path for lookup
            md_path = target.split("?")[0].split("#")[0]
            resolved = _resolve_path(self._source_dir, md_path)
            page_id = self._page_id_map.get(resolved)
            if page_id:
                inner = self.render_inner(token)
                return (
                    f"<ac:link>"
                    f'<ri:page ri:content-id="{page_id}"/>'
                    f"<ac:plain-text-link-body>"
                    f"<![CDATA[{_escape_cdata(inner)}]]>"
                    f"</ac:plain-text-link-body>"
                    f"</ac:link>"
                )
            logger.warning(
                "'%s': internal link to '%s' not in manifest — kept as plain link",
                self.source_path,
                target,
            )
        return f'<a href="{_escape_attr(target)}">{self.render_inner(token)}</a>'

    def render_image(self, token):
        src = token.src
        alt = token.children[0].content if token.children else ""
        alt_attr = f' ac:alt="{_escape_attr(alt)}"' if alt else ""
        if _is_external(src):
            return f'<ac:image{alt_attr}><ri:url ri:value="{_escape_attr(src)}"/></ac:image>'
        resolved = _resolve_path(self._source_dir, src)
        self.images.append(resolved)
        filename = PurePosixPath(src).name
        return f'<ac:image{alt_attr}><ri:attachment ri:filename="{_escape_attr(filename)}"/></ac:image>'

    def render_line_break(self, token):
        return " " if token.soft else "<br/>"

    def render_escape_sequence(self, token):
        return _escape(token.children[0].content)

    def render_auto_link(self, token):
        target = _escape_attr(token.children[0].content)
        return f'<a href="{target}">{target}</a>'

    def render_html_span(self, token):
        raise ConversionError(
            f"Inline HTML is not supported ('{self.source_path}'). "
            f"Remove or convert to Markdown."
        )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _escape_cdata(text: str) -> str:
    """Split ]]> so it cannot prematurely close a CDATA section."""
    return text.replace("]]>", "]]]]><![CDATA[>")


def _is_external(url: str) -> bool:
    return url.startswith(("http://", "https://", "mailto:", "ftp://", "//"))


def _resolve_path(source_dir: str, rel_path: str) -> str:
    """Normalise a path relative to source_dir into a repo-root-relative forward-slash path."""
    raw = (source_dir + "/" + rel_path) if source_dir and source_dir != "." else rel_path
    parts: list[str] = []
    for part in raw.replace("\\", "/").split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def build_banner(source_path: str, commit_sha: str) -> str:
    return (
        f'<ac:structured-macro ac:name="info">'
        f"<ac:rich-text-body>"
        f"<p>This page is auto-generated from GitHub. "
        f"Manual edits will be overwritten on next publish.<br/>"
        f"Source: <code>{_escape(source_path)}</code> "
        f"@ <code>{_escape(commit_sha)}</code></p>"
        f"</ac:rich-text-body>"
        f"</ac:structured-macro>"
    )


def content_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def convert(
    text: str,
    source_path: str,
    commit_sha: str,
    page_id_map: dict[str, str] | None = None,
) -> ConversionResult:
    """Convert Markdown to Confluence Storage Format.

    Returns a ConversionResult with:
    - body: converted content without banner (used for hashing)
    - full_body: banner + body (what gets published)
    - images: repo-root-relative paths of local images referenced in the doc
    """
    with ConfluenceRenderer(source_path=source_path, page_id_map=page_id_map) as renderer:
        doc = Document(text)
        body = renderer.render(doc)
        images = list(renderer.images)
        mermaid_blocks = list(renderer.mermaid_blocks)
    banner = build_banner(source_path, commit_sha)
    return ConversionResult(
        body=body,
        full_body=banner + body,
        images=images,
        mermaid_blocks=mermaid_blocks,
    )
