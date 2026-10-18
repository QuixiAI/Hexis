"""Clean text before it is read into memory.

Every document passes through clean_document_text before it is stored and
split into sections: invisible characters (zero-width spaces, soft hyphens,
byte-order marks) and odd spaces go, line endings are unified, trailing
spaces are trimmed and runs of blank lines shortened. Prose also has runs of
spaces collapsed; code and data keep their spacing, which carries meaning.
Email gets more (clean_email_text): senders' HTML, entities, preview padding,
tracking links and quoted replies are removed.
"""
from __future__ import annotations

import html
import re

_HTML_LOOKING = re.compile(r"(?i)<(?:!doctype|html|head|body|table|div|p|br|span|style)\b")
_BLOCK_END = re.compile(r"(?i)<\s*(?:br\s*/?|/p|/div|/tr|/li|/h[1-6]|/table|/blockquote)\s*>")
# Zero-width and spacing characters that email templates pad preheaders with.
_INVISIBLE = re.compile("[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufeff]")
_ODD_SPACES = re.compile("[\u00a0\u2000-\u200a\u202f\u205f\u3000]")
_LONG_URL = re.compile(r"https?://([^/\s>)\]]+)[^\s>)\]]{40,}")


def strip_html(value: str) -> str:
    """HTML to plain text, keeping paragraph breaks."""
    cleaned = re.sub(r"(?s)<!--.*?-->", " ", value)
    cleaned = re.sub(r"(?is)<(head|script|style|noscript|xml)\b.*?</\1\s*>", " ", cleaned)
    cleaned = _BLOCK_END.sub("\n", cleaned)
    return re.sub(r"(?s)<[^>]+>", " ", cleaned)


def clean_email_text(value: str) -> str:
    """The readable text of an email, without markup or padding.

    Senders put raw HTML even in the plain-text part, pad previews with
    invisible characters, and wrap every link in a long tracking URL; kept
    as-is that was most of what the reader and the embedder saw. Long links
    keep only their site; quoted reply lines are dropped (the earlier message
    is its own email).
    """
    text = value or ""
    if _HTML_LOOKING.search(text):
        text = strip_html(text)
    text = html.unescape(text)
    text = _INVISIBLE.sub("", text)
    text = _ODD_SPACES.sub(" ", text)
    text = _LONG_URL.sub(lambda m: f"[link: {m.group(1)}]", text)
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.lstrip().startswith(">"):
            continue
        lines.append(re.sub(r"[ \t]+", " ", line).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()



# Spacing is meaningful here (indentation, aligned columns).
_KEEP_SPACING = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".rb",
    ".php", ".swift", ".kt", ".scala", ".r", ".sql", ".sh", ".bash", ".zsh", ".ps1", ".yaml", ".yml",
    ".json", ".xml", ".css", ".scss", ".less", ".csv", ".tsv", ".ipynb", ".tex",
}


def clean_document_text(value: str, file_type: str | None = None) -> str:
    """Whitespace and invisible-character cleanup safe for any document."""
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE.sub("", text)
    text = _ODD_SPACES.sub(" ", text)
    keep_spacing = (file_type or "").lower() in _KEEP_SPACING
    lines = []
    for line in text.split("\n"):
        line = line.rstrip()
        if not keep_spacing:
            # Collapse runs of spaces inside the line, but keep its indentation
            # (Markdown lists and quoted blocks use it).
            indent = len(line) - len(line.lstrip(" \t"))
            line = line[:indent] + re.sub(r"[ \t]{2,}", " ", line[indent:])
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
