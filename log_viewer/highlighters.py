"""Kolorowanie składni (JSON / XML) dla okna FormatDialog."""

from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from .helpers import THEME_DARK

MAX_HIGHLIGHT_BLOCK_LEN = 10_000
MAX_HIGHLIGHT_DOC_CHARS = 500_000

_RE_JSON_NUMBER = re.compile(r"\b-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\b")
_RE_JSON_LITERAL = re.compile(r"\b(?:true|false|null)\b")
_RE_JSON_STRING_OR_KEY = re.compile(r'("(?:\\.|[^"\\])*")(\s*:)?')

_RE_XML_TAG = re.compile(r"</?[\w:.-]+|\?>|/?>|<\?[\w:.-]+")
_RE_XML_ATTR = re.compile(r"""\b([\w:.-]+)(\s*=\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')""")


class StructuredCodeHighlighter(QSyntaxHighlighter):
    """Lekki i bezpieczny highlighter składni JSON oraz XML dla QPlainTextEdit w FormatDialog."""

    def __init__(self, document: QTextDocument, mode: str = "JSON", theme: dict[str, str] | None = None) -> None:
        super().__init__(document)
        self._mode = mode.upper()
        self._enabled = True
        self._theme = theme if theme is not None else THEME_DARK

        self._fmt_key = QTextCharFormat()
        self._fmt_string = QTextCharFormat()
        self._fmt_number = QTextCharFormat()
        self._fmt_literal = QTextCharFormat()
        self._fmt_comment = QTextCharFormat()

        self._rebuild_formats()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, val: bool) -> None:
        if self._enabled != val:
            self._enabled = val
            self.rehighlight()

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, rehighlight: bool = True) -> None:
        new_mode = mode.upper()
        if self._mode != new_mode:
            self._mode = new_mode
            if rehighlight and self._enabled:
                self.rehighlight()

    def set_theme(self, theme: dict[str, str]) -> None:
        self._theme = theme
        self._rebuild_formats()
        if self._enabled:
            self.rehighlight()

    def _rebuild_formats(self) -> None:
        t = self._theme
        c_key = QColor(t.get("syn_key", "#9cdcfe"))
        c_str = QColor(t.get("syn_string", "#ce9178"))
        c_num = QColor(t.get("syn_number", "#b5cea8"))
        c_lit = QColor(t.get("syn_literal", "#569cd6"))
        c_com = QColor(t.get("syn_comment", "#6a9955"))

        self._fmt_key = QTextCharFormat()
        self._fmt_key.setForeground(c_key)
        self._fmt_key.setFontWeight(QFont.Weight.Bold)

        self._fmt_string = QTextCharFormat()
        self._fmt_string.setForeground(c_str)

        self._fmt_number = QTextCharFormat()
        self._fmt_number.setForeground(c_num)

        self._fmt_literal = QTextCharFormat()
        self._fmt_literal.setForeground(c_lit)
        self._fmt_literal.setFontWeight(QFont.Weight.Bold)

        self._fmt_comment = QTextCharFormat()
        self._fmt_comment.setForeground(c_com)
        self._fmt_comment.setFontItalic(True)

    def highlightBlock(self, text: str) -> None:
        if not self._enabled or not text or len(text) > MAX_HIGHLIGHT_BLOCK_LEN:
            self.setCurrentBlockState(0)
            return

        if self._mode == "JSON":
            self._highlight_json(text)
        elif self._mode == "XML":
            self._highlight_xml(text)

    def _highlight_json(self, text: str) -> None:
        self.setCurrentBlockState(0)
        for m in _RE_JSON_NUMBER.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._fmt_number)

        for m in _RE_JSON_LITERAL.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._fmt_literal)

        for m in _RE_JSON_STRING_OR_KEY.finditer(text):
            str_start, str_end = m.span(1)
            if m.group(2) is not None:
                self.setFormat(str_start, str_end - str_start, self._fmt_key)
            else:
                self.setFormat(str_start, str_end - str_start, self._fmt_string)

    def _highlight_xml(self, text: str) -> None:
        for m in _RE_XML_TAG.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._fmt_literal)

        for m in _RE_XML_ATTR.finditer(text):
            k_start, k_end = m.span(1)
            v_start, v_end = m.span(3)
            self.setFormat(k_start, k_end - k_start, self._fmt_key)
            self.setFormat(v_start, v_end - v_start, self._fmt_string)

        # Obsługa komentarzy XML <!-- ... --> (także wieloliniowych)
        self.setCurrentBlockState(0)
        start_idx = 0
        if self.previousBlockState() != 1:
            start_idx = text.find("<!--")

        while start_idx >= 0:
            end_idx = text.find("-->", start_idx)
            if end_idx == -1:
                self.setCurrentBlockState(1)
                comment_len = len(text) - start_idx
            else:
                comment_len = end_idx - start_idx + 3
            self.setFormat(start_idx, comment_len, self._fmt_comment)
            if end_idx == -1:
                break
            start_idx = text.find("<!--", start_idx + comment_len)
