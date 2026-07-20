"""app.py — LogTab (per-file widget) + LogViewerWindow (tabbed controller)."""

from __future__ import annotations

import os
import re
import sys
import time
import bisect
from typing import Optional, List, Tuple, Dict, Any

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QThread, QSize, QPoint
from PySide6.QtGui import (
    QAction, QKeySequence, QColor, QTextCharFormat, QFont, QDragEnterEvent,
    QDropEvent, QCursor,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPlainTextEdit, QWidget, QVBoxLayout,
    QHBoxLayout, QGridLayout, QLabel, QLineEdit, QCheckBox, QPushButton,
    QMenuBar, QMenu, QStatusBar, QFileDialog, QMessageBox, QInputDialog,
    QProgressBar, QSplitter, QTreeWidget, QTreeWidgetItem, QSlider,
    QDialog, QDialogButtonBox, QSpinBox, QComboBox, QFontComboBox,
    QSizePolicy, QToolBar, QFrame, QProgressDialog, QListView, QTabWidget,
)

from .exceptions import FileChangedError, CompressedSaveError
from .helpers import (
    fmt_size, truncate_for_display, parse_dnd_files, dnd_files_to_open,
    is_compressed, open_maybe_compressed,
    WINDOW_SIZE_LINES, MAX_DISPLAY_LINES, MAX_DISPLAY_LINE_LENGTH,
    FOLLOW_POLL_MS, FILTER_PROGRESS_MS, DEFAULT_ENCODING,
    TAG_HIGHLIGHT, TAG_BOOKMARK, TAG_EDITED, TAG_TRUNCATED,
    TAG_ERROR, TAG_WARN, TAG_INFO, TAG_DEBUG, TAG_TIMESTAMP, TAG_CONTEXT,
    SUPPORTED_ENCODINGS, OPEN_FILETYPES, THEME_DARK, THEME_LIGHT,
)
from .i18n import I18N
from .config import UserConfig
from .indexer import LineIndexer, IndexEntry
from .filter_engine import FilterEngine
from .edit_buffer import EditBuffer
from .workers import IndexerWorker, FilterWorker, SaveWorker
from .widgets import LogPlainTextEdit, SettingsDialog, SearchResultsModel, MiniMap, FormatDialog
from .ui.ui_main_window import Ui_LogViewerWindow

from .log_tab import LogTab

# =====================================================================
# LogViewerWindow — kontroler z QTabWidget
# =====================================================================

class LogViewerWindow(QMainWindow):
    """Główna aplikacja PySide6 — kontroler zarządzający zakładkami.

    Per-tab cleanup pattern (w LogTab.close / open_file / cmd_save_edits):
        self._indexer_worker.deleteLater
        self._filter_worker.deleteLater
        self._save_worker.deleteLater
        self._indexer_thread.deleteLater
        self._filter_thread.deleteLater
        self._save_thread.deleteLater

    Ostrzeżenie o czasie zapisu dla dużych plików (est_seconds / save_warning /
    "zapis potrwa") znajduje się w LogTab.cmd_save_edits.
    """

    def __init__(self, config: Optional[UserConfig] = None,
                 initial_file: Optional[str] = None):
        super().__init__()
        self.ui = Ui_LogViewerWindow()
        self.ui.setupUi(self)

        self.config = config if config is not None else UserConfig()
        self.lang = self.config.get("language", "pl")
        self.encoding: str = self.config.get("encoding", DEFAULT_ENCODING)
        self.window_size_lines: int = int(self.config.get("window_size_lines", WINDOW_SIZE_LINES))
        self.max_display_lines: int = int(self.config.get("max_display_lines", MAX_DISPLAY_LINES))
        self.max_display_line_length: int = int(self.config.get("max_display_line_length", MAX_DISPLAY_LINE_LENGTH))
        self.index_interval_bytes: int = int(self.config.get("index_interval_bytes", 1024 * 1024))
        self.font_family: Optional[str] = self.config.get("font_family", None)
        self.font_size: int = int(self.config.get("font_size", 10))

        # Aktywny motyw (dark/light) — wykrywany z systemu
        self.theme: dict = THEME_DARK
        self._follow_action: Optional[QAction] = None
        self._enc_action_group: Optional[QtGui.QActionGroup] = None
        self._is_restoring_toolbar: bool = False

        # Build UI
        self.tabs = self.ui.tabs
        self.tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._build_toolbar()
        self._rebuild_menubar()

        self._apply_language()
        self._apply_theme()

        self.statusBar().showMessage(self.t("st_ready"))

        if initial_file:
            QTimer.singleShot(100, lambda: self.open_file_in_tab(initial_file))

    # ------------------------------------------------------------------ delegation
    def __getattr__(self, name: str):
        """Deleguje tab-specific atrybuty/metody do aktywnej zakładki.

        Wywoływane tylko gdy normalne wyszukiwanie atrybutu zawiedzie.
        Dzięki temu window.text, window.position_slider, window._search_results,
        window._load_window(), window._on_index_done() itp. działają tak samo
        jak przed refaktorem (delegując do aktywnej zakładki).
        """
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        tabs = self.__dict__.get("tabs")
        if tabs is None:
            raise AttributeError(name)
        tab = tabs.currentWidget()
        if tab is None:
            raise AttributeError(name)
        return getattr(tab, name)

    def _delegate_to_tab(self, method_name: str, *args, **kwargs):
        """Woła metodę na aktywnej zakładce. No-op jeśli brak zakładki."""
        tab = self.tabs.currentWidget()
        if tab is None:
            return None
        method = getattr(tab, method_name, None)
        if method is None:
            return None
        return method(*args, **kwargs)

    # ------------------------------------------------------------------ i18n
    def t(self, key: str) -> str:
        return I18N[self.lang].get(key, key)

    def _fmt(self, msg_key: str, **kw) -> str:
        return self.t(msg_key).format(**kw)

    def set_language(self, lang: str) -> None:
        if lang not in I18N:
            return
        self.lang = lang
        self.config.set("language", lang)
        self._apply_language()
        self._rebuild_menubar()
        # Retranslate przycisków paska narzędzi
        if hasattr(self, "btn_find_next"):
            self.btn_find_next.setText(self.t("btn_find_next"))
        if hasattr(self, "btn_find_prev"):
            self.btn_find_prev.setText(self.t("btn_find_prev"))
        if hasattr(self, "btn_clear_search"):
            self.btn_clear_search.setText(self.t("btn_clear_search"))
        if hasattr(self, "btn_apply_filter"):
            self.btn_apply_filter.setText(self.t("btn_apply_filter"))
        if hasattr(self, "btn_clear_filter"):
            self.btn_clear_filter.setText(self.t("btn_clear_filter"))

        # Zaktualizuj labelki i opcje paska narzędzi
        if hasattr(self, "lbl_search"):
            self.lbl_search.setText(self.t("lbl_search"))
        if hasattr(self, "lbl_filter"):
            self.lbl_filter.setText(self.t("lbl_filter"))
        if hasattr(self, "lbl_filter_context"):
            self.lbl_filter_context.setText(self.t("lbl_filter_context"))
        if hasattr(self, "search_regex_cb"):
            self.search_regex_cb.setText(self.t("cb_regex"))
        if hasattr(self, "search_case_cb"):
            self.search_case_cb.setText(self.t("cb_case"))
        if hasattr(self, "search_negate_cb"):
            self.search_negate_cb.setText(self.t("cb_negate"))
        if hasattr(self, "filter_regex_cb"):
            self.filter_regex_cb.setText(self.t("cb_regex"))
        if hasattr(self, "filter_case_cb"):
            self.filter_case_cb.setText(self.t("cb_case"))
        if hasattr(self, "filter_negate_cb"):
            self.filter_negate_cb.setText(self.t("cb_negate"))

        # Zaktualizuj każdy tab (labelki paneli bocznych, etc.)
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, LogTab):
                tab._lbl_bookmarks.setText(self.t("lbl_bookmarks"))
                tab._lbl_edits.setText(self.t("lbl_edits"))
                tab.bm_tree.setHeaderLabels([self.t("col_line")])
                tab.ed_tree.setHeaderLabels([self.t("col_line")])
                tab.btn_del_bookmarks.setText(self.t("btn_delete_sel"))
                tab.btn_del_edits.setText(self.t("btn_delete_sel"))
                tab._search_results_label.setText(self.t("lbl_search_results_empty"))
                tab._refresh_status()
        tab = self.tabs.currentWidget()
        if isinstance(tab, LogTab):
            tab._refresh_status()
        else:
            self.statusBar().showMessage(self.t("st_ready"))

    def _apply_language(self) -> None:
        self.setWindowTitle(self.t("app_title"))

    # ------------------------------------------------------------------ UI
    def _detect_system_theme(self) -> dict:
        """Wykrywa motyw systemowy (dark/light) i zwraca odpowiedni THEME."""
        try:
            from PySide6.QtGui import QGuiApplication
            hints = QGuiApplication.styleHints()
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return THEME_DARK
            elif scheme == Qt.ColorScheme.Light:
                return THEME_LIGHT
        except Exception:
            pass
        try:
            from PySide6.QtGui import QPalette
            pal = QtWidgets.QApplication.palette()
            bg = pal.color(QPalette.Window)
            text_color = pal.color(QPalette.WindowText)
            if text_color.lightness() > bg.lightness():
                return THEME_DARK
            return THEME_LIGHT
        except Exception:
            pass
        return THEME_DARK

    def _apply_theme(self) -> None:
        """Aplikuje motyw (dark lub light) zgodny z systemem operacyjnym."""
        self.theme = self._detect_system_theme()
        t = self.theme
        qss = f"""
            QMainWindow, QWidget {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
            }}
            QPlainTextEdit, QTextEdit {{
                background-color: {t["bg_main"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                font-family: "Menlo", "DejaVu Sans Mono", "Consolas", "Courier New", monospace;
            }}
            QToolBar {{
                background-color: {t["bg_panel"]};
                border: none;
                border-bottom: 1px solid {t["border"]};
                spacing: 2px;
                padding: 2px;
            }}
            QMenuBar {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
                border-bottom: 1px solid {t["border"]};
            }}
            QMenuBar::item:selected {{
                background-color: {t["accent"]};
            }}
            QMenu {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
            }}

            QMenu::item:selected {{
                background-color: {t["accent"]};
            }}
            QMenu::item:disabled {{
                color: #555555;
            }}
            QPushButton:disabled {{
                color: #555555;
                background-color: {t["bg_panel"]};
                border: 1px solid #333333;
            }}
            QLineEdit:disabled {{
                color: #555555;
                background-color: {t["bg_panel"]};
                border: 1px solid #333333;
            }}
            QCheckBox:disabled {{
                color: #555555;
            }}
            QLabel:disabled {{
                color: #555555;
            }}
            QSpinBox:disabled {{
                color: #555555;
                background-color: {t["bg_panel"]};
                border: 1px solid #333333;
            }}
            QStatusBar {{
                background-color: {t["bg_statusbar"]};
                color: {t["fg_dim"]};
                border-top: 1px solid {t["border"]};
            }}
            QLabel {{
                color: {t["fg_main"]};
                background: transparent;
            }}
            QLineEdit {{
                background-color: {t["bg_input"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                padding: 2px 4px;
                border-radius: 2px;
            }}
            QLineEdit:focus {{
                border: 1px solid {t["accent"]};
            }}
            QCheckBox {{
                color: {t["fg_main"]};
                spacing: 4px;
            }}
            QPushButton {{
                background-color: {t["bg_input"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                padding: 3px 10px;
                border-radius: 2px;
                font-weight: normal;
            }}
            QPushButton:hover {{
                background-color: {t["bg_hover"]};
                border: 1px solid {t["border_light"]};
            }}
            QPushButton:pressed {{
                background-color: {t["bg_selected"]};
                color: {t["fg_bright"]};
            }}
            QSlider {{
                background: transparent;
            }}
            QSlider::groove:vertical {{
                background: {t["bg_input"]};
                width: 6px;
                border-radius: 3px;
            }}
            QSlider::handle:vertical {{
                background: {t["accent"]};
                height: 16px;
                width: 12px;
                margin: -5px 0;
                border-radius: 3px;
            }}
            QTreeWidget {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                alternate-background-color: {t["bg_alt"]};
            }}
            QTreeWidget::item:selected {{
                background-color: {t["bg_selected"]};
            }}
            QListView {{
                background-color: {t["bg_main"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                alternate-background-color: {t["bg_alt"]};
            }}
            QListView::item:selected {{
                background-color: {t["bg_selected"]};
            }}
            QSplitter::handle {{
                background-color: {t["border"]};
            }}
            QSplitter::handle:horizontal {{
                width: 2px;
            }}
            QSplitter::handle:vertical {{
                height: 2px;
            }}
            QTabWidget::pane {{
                border: 1px solid {t["border"]};
                top: -1px;
            }}
            QTabWidget::tab-bar {{
                alignment: left;
            }}
            QTabBar::tab {{
                background-color: {t["bg_panel"]};
                color: {t["fg_dim"]};
                border: 1px solid {t["border"]};
                padding: 4px 12px;
                border-bottom: none;
            }}
            QTabBar::tab:selected {{
                background-color: {t["bg_main"]};
                color: {t["fg_main"]};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {t["bg_hover"]};
            }}
            QTabBar::close-button {{
                image: none;
                subcontrol-position: right;
                border: 1px solid {t["border"]};
                border-radius: 2px;
                padding: 2px;
                margin: 2px;
            }}
            QTabBar::close-button:hover {{
                background-color: {t["accent"]};
            }}
            QHeaderView::section {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
                border: none;
                border-bottom: 1px solid {t["border"]};
                padding: 2px 4px;
            }}
            QScrollBar:vertical {{
                background: {t["bg_main"]};
                width: 10px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {t["border_light"]};
                min-height: 20px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {t["fg_dim"]};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar:horizontal {{
                background: {t["bg_main"]};
                height: 10px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {t["border_light"]};
                min-width: 20px;
                border-radius: 5px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {t["fg_dim"]};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
            QProgressBar {{
                background-color: {t["bg_input"]};
                border: 1px solid {t["border"]};
                border-radius: 2px;
                text-align: center;
                color: {t["fg_main"]};
            }}
            QProgressBar::chunk {{
                background-color: {t["accent"]};
                border-radius: 1px;
            }}
            QProgressDialog {{
                background-color: {t["bg_panel"]};
            }}
            QDialog {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
            }}
            QSpinBox, QComboBox, QFontComboBox {{
                background-color: {t["bg_input"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                padding: 2px;
                border-radius: 2px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {t["bg_input"]};
                color: {t["fg_main"]};
                selection-background-color: {t["accent"]};
            }}
        """
        self.setStyleSheet(qss)
        # Zaktualizuj każdy tab
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, LogTab):
                tab._apply_theme()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.lbl_search = QLabel(self.t("lbl_search"))
        toolbar.addWidget(self.lbl_search)
        self.search_entry = QLineEdit()
        self.search_entry.setMaximumWidth(250)
        self.search_entry.returnPressed.connect(self.cmd_find_next)
        toolbar.addWidget(self.search_entry)

        self.search_regex_cb = QCheckBox(self.t("cb_regex"))
        self.search_case_cb = QCheckBox(self.t("cb_case"))
        self.search_negate_cb = QCheckBox(self.t("cb_negate"))
        toolbar.addWidget(self.search_regex_cb)
        toolbar.addWidget(self.search_case_cb)
        toolbar.addWidget(self.search_negate_cb)

        # Kolejność: Następny po lewej, Poprzedni po prawej (wg preferencji
        # użytkownika) — Enter w polu wyszukiwania nadal = Następny.
        self.btn_find_next = QPushButton(self.t("btn_find_next"))
        self.btn_find_next.clicked.connect(self.cmd_find_next)
        toolbar.addWidget(self.btn_find_next)
        self.btn_find_prev = QPushButton(self.t("btn_find_prev"))
        self.btn_find_prev.clicked.connect(self.cmd_find_prev)
        toolbar.addWidget(self.btn_find_prev)
        self.btn_clear_search = QPushButton(self.t("btn_clear_search"))
        self.btn_clear_search.clicked.connect(self.cmd_clear_search)
        toolbar.addWidget(self.btn_clear_search)

        toolbar.addSeparator()

        self.lbl_filter = QLabel(self.t("lbl_filter"))
        toolbar.addWidget(self.lbl_filter)
        self.filter_entry = QLineEdit()
        self.filter_entry.setMaximumWidth(250)
        self.filter_entry.returnPressed.connect(self.cmd_apply_filter)
        toolbar.addWidget(self.filter_entry)

        self.filter_regex_cb = QCheckBox(self.t("cb_regex"))
        self.filter_case_cb = QCheckBox(self.t("cb_case"))
        self.filter_negate_cb = QCheckBox(self.t("cb_negate"))
        toolbar.addWidget(self.filter_regex_cb)
        toolbar.addWidget(self.filter_case_cb)
        toolbar.addWidget(self.filter_negate_cb)

        # Separator — oddziela opcje filtru (regex/case/negacja) od kontekstu.
        toolbar.addSeparator()

        # Ile linii kontekstu po każdym trafieniu filtru (dla stack trace).
        self.lbl_filter_context = QLabel(self.t("lbl_filter_context"))
        toolbar.addWidget(self.lbl_filter_context)
        self.filter_context_spin = QSpinBox()
        self.filter_context_spin.setRange(0, 50)
        self.filter_context_spin.setValue(0)
        self.filter_context_spin.setFixedWidth(56)
        self.filter_context_spin.setToolTip(self.t("tt_filter_context"))
        toolbar.addWidget(self.filter_context_spin)

        self.btn_apply_filter = QPushButton(self.t("btn_apply_filter"))
        self.btn_apply_filter.clicked.connect(self.cmd_apply_filter)
        toolbar.addWidget(self.btn_apply_filter)
        self.btn_clear_filter = QPushButton(self.t("btn_clear_filter"))
        self.btn_clear_filter.clicked.connect(self.cmd_clear_filter)
        toolbar.addWidget(self.btn_clear_filter)

        self.search_entry.textChanged.connect(self._save_toolbar_to_tab)
        self.search_regex_cb.stateChanged.connect(self._save_toolbar_to_tab)
        self.search_case_cb.stateChanged.connect(self._save_toolbar_to_tab)
        self.search_negate_cb.stateChanged.connect(self._save_toolbar_to_tab)

        self.filter_entry.textChanged.connect(self._save_toolbar_to_tab)
        self.filter_regex_cb.stateChanged.connect(self._save_toolbar_to_tab)
        self.filter_case_cb.stateChanged.connect(self._save_toolbar_to_tab)
        self.filter_negate_cb.stateChanged.connect(self._save_toolbar_to_tab)
        self.filter_context_spin.valueChanged.connect(self._save_toolbar_to_tab)


    def _rebuild_menubar(self) -> None:
        menubar = self.menuBar()
        menubar.clear()

        file_menu = menubar.addMenu(self.t("menu_file"))
        file_menu.addAction(self._mkaction(self.t("mi_open"), QKeySequence.StandardKey.Open, self.cmd_open))
        self._action_save = self._mkaction(self.t("mi_save"), QKeySequence.StandardKey.Save, self.cmd_save_edits)
        file_menu.addAction(self._action_save)
        self._action_save_as = self._mkaction(self.t("mi_save_as"), QKeySequence.StandardKey.SaveAs, self.cmd_save_as)
        file_menu.addAction(self._action_save_as)
        file_menu.addSeparator()
        self._action_export = self._mkaction(self.t("mi_export"), "Ctrl+E", self.cmd_export)
        file_menu.addAction(self._action_export)
        file_menu.addSeparator()
        file_menu.addAction(self._mkaction(self.t("mi_exit"), QKeySequence.StandardKey.Quit, self.close))

        edit_menu = menubar.addMenu(self.t("menu_edit"))
        self._action_find = self._mkaction(self.t("mi_find"), QKeySequence.StandardKey.Find, self.cmd_find_dialog)
        edit_menu.addAction(self._action_find)
        self._action_find_next = self._mkaction(self.t("mi_find_next"), QKeySequence.StandardKey.FindNext, self.cmd_find_next)
        edit_menu.addAction(self._action_find_next)
        self._action_find_prev = self._mkaction(self.t("mi_find_prev"), QKeySequence.StandardKey.FindPrevious, self.cmd_find_prev)
        edit_menu.addAction(self._action_find_prev)
        self._action_clear_search = self._mkaction(self.t("btn_clear_search"), "Ctrl+Shift+C", self.cmd_clear_search)
        edit_menu.addAction(self._action_clear_search)
        edit_menu.addSeparator()
        self._action_filter = self._mkaction(self.t("mi_filter"), "Ctrl+L", self.cmd_filter_dialog)
        edit_menu.addAction(self._action_filter)
        self._action_clear_filter = self._mkaction(self.t("mi_clear_filter"), "", self.cmd_clear_filter)
        edit_menu.addAction(self._action_clear_filter)
        edit_menu.addSeparator()
        self._action_edit_line = self._mkaction(self.t("mi_edit_line"), "Ctrl+D", self.cmd_edit_line)
        edit_menu.addAction(self._action_edit_line)
        self._action_format_selection = self._mkaction(self.t("mi_format_selection"), "Ctrl+K", self.cmd_format_selection)
        edit_menu.addAction(self._action_format_selection)
        edit_menu.addSeparator()
        self._action_save_edits = self._mkaction(self.t("mi_save_edits"), "", self.cmd_save_edits)
        edit_menu.addAction(self._action_save_edits)
        self._action_clear_edits = self._mkaction(self.t("mi_clear_edits"), "", self.cmd_clear_edits)
        edit_menu.addAction(self._action_clear_edits)

        view_menu = menubar.addMenu(self.t("menu_view"))
        self._follow_action = self._mkaction(self.t("mi_follow"), "Ctrl+T", self.cmd_toggle_follow)
        self._follow_action.setCheckable(True)
        view_menu.addAction(self._follow_action)
        view_menu.addSeparator()

        enc_menu = view_menu.addMenu(self.t("mi_encoding"))
        self._enc_action_group = QtGui.QActionGroup(self)
        self._enc_action_group.setExclusive(True)
        for enc_code, enc_label in SUPPORTED_ENCODINGS:
            act = enc_menu.addAction(enc_label)
            act.setCheckable(True)
            act.setChecked(enc_code == self.encoding)
            act.triggered.connect(lambda checked, c=enc_code: self.cmd_set_encoding(c))
            self._enc_action_group.addAction(act)
        view_menu.addSeparator()

        lang_menu = view_menu.addMenu(self.t("mi_lang"))

        self._lang_action_group = QtGui.QActionGroup(self)
        self._lang_action_group.setExclusive(True)

        act_pl = self._mkaction(self.t("mi_lang_pl"), "", lambda: self.set_language("pl"))
        act_pl.setCheckable(True)
        act_pl.setChecked(self.lang == "pl")
        self._lang_action_group.addAction(act_pl)
        lang_menu.addAction(act_pl)

        act_en = self._mkaction(self.t("mi_lang_en"), "", lambda: self.set_language("en"))
        act_en.setCheckable(True)
        act_en.setChecked(self.lang == "en")
        self._lang_action_group.addAction(act_en)
        lang_menu.addAction(act_en)

        view_menu.addSeparator()
        self._action_next_tab = self._mkaction(
            self.t("mi_next_tab"),
            [QKeySequence.StandardKey.NextChild, "Ctrl+Tab", "Ctrl+]"],
            self.cmd_next_tab
        )
        view_menu.addAction(self._action_next_tab)

        self._action_prev_tab = self._mkaction(
            self.t("mi_prev_tab"),
            [QKeySequence.StandardKey.PreviousChild, "Ctrl+Shift+Tab", "Ctrl+["],
            self.cmd_prev_tab
        )
        view_menu.addAction(self._action_prev_tab)

        self._action_close_tab = self._mkaction(
            self.t("mi_close_tab"),
            QKeySequence.StandardKey.Close,
            self.cmd_close_tab
        )
        view_menu.addAction(self._action_close_tab)

        view_menu.addSeparator()
        view_menu.addAction(self._mkaction(self.t("mi_settings"), QKeySequence.StandardKey.Preferences, self.cmd_settings))

        bm_menu = menubar.addMenu(self.t("menu_bookmarks"))
        self._action_toggle_bookmark = self._mkaction(self.t("mi_toggle_bookmark"), "Ctrl+B", self.cmd_toggle_bookmark)
        bm_menu.addAction(self._action_toggle_bookmark)
        self._action_next_bookmark = self._mkaction(self.t("mi_next_bookmark"), "F4", self.cmd_next_bookmark)
        bm_menu.addAction(self._action_next_bookmark)
        self._action_prev_bookmark = self._mkaction(self.t("mi_prev_bookmark"), "Shift+F4", self.cmd_prev_bookmark)
        bm_menu.addAction(self._action_prev_bookmark)
        bm_menu.addSeparator()
        self._action_clear_bookmarks = self._mkaction(self.t("mi_clear_bookmarks"), "", self.cmd_clear_bookmarks)
        bm_menu.addAction(self._action_clear_bookmarks)

        goto_menu = menubar.addMenu(self.t("menu_goto"))
        self._action_goto = self._mkaction(self.t("mi_goto"), "Ctrl+G", self.cmd_goto)
        goto_menu.addAction(self._action_goto)
        self._action_goto_start = self._mkaction(self.t("mi_goto_start"), QKeySequence.StandardKey.MoveToStartOfDocument, self.cmd_goto_start)
        goto_menu.addAction(self._action_goto_start)
        self._action_goto_end = self._mkaction(self.t("mi_goto_end"), QKeySequence.StandardKey.MoveToEndOfDocument, self.cmd_goto_end)
        goto_menu.addAction(self._action_goto_end)

        help_menu = menubar.addMenu(self.t("menu_help"))
        help_menu.addAction(self._mkaction(self.t("mi_about"), "", self.cmd_about))

        self._context_actions = [
            self._action_save, self._action_save_as, self._action_export,
            self._action_find, self._action_find_next, self._action_find_prev, self._action_clear_search,
            self._action_filter, self._action_clear_filter,
            self._action_edit_line, self._action_format_selection, self._action_save_edits, self._action_clear_edits,
            self._follow_action,
            self._action_toggle_bookmark, self._action_next_bookmark, self._action_prev_bookmark, self._action_clear_bookmarks,
            self._action_goto, self._action_goto_end
        ]
        self._update_ui_state()


    def _mkaction(self, label: str, shortcut, handler) -> QAction:
        act = QAction(label, self)
        if shortcut:
            if isinstance(shortcut, list):
                shortcuts = []
                for s in shortcut:
                    if isinstance(s, str):
                        shortcuts.append(QKeySequence(s))
                    else:
                        shortcuts.append(s)
                act.setShortcuts(shortcuts)
            else:
                if isinstance(shortcut, str):
                    act.setShortcut(QKeySequence(shortcut))
                else:
                    act.setShortcut(shortcut)
        act.triggered.connect(handler)
        return act

    # --------------------------------------------------------- tab mgmt ---
    def _new_tab(self, title: str = "") -> LogTab:
        """Tworzy nową pustą zakładkę i ustawia ją jako aktywną."""
        tab = LogTab(self)
        # Połącz sygnały tab z oknem
        tab.status_changed.connect(self._on_tab_status_changed)
        tab.title_changed.connect(lambda title_str, t=tab: self._on_tab_title_changed(t, title_str))
        self.tabs.addTab(tab, title or self.t("st_ready"))
        self.tabs.setCurrentWidget(tab)
        # Aplikuj motyw do nowej zakładki
        tab._apply_theme()
        tab._apply_font_to_text()
        return tab

    def open_file_in_tab(self, path: str) -> Optional[LogTab]:
        """Otwiera plik w nowej zakładce."""
        if not os.path.isfile(path):
            QMessageBox.critical(self, self.t("app_title"), self.t("msg_no_file"))
            return None
        tab = self._new_tab(title=os.path.basename(path))
        tab.open_file(path)
        self._update_ui_state()
        return tab

    def _on_tab_status_changed(self, msg: str) -> None:
        """Aktualizuje status bar — tylko dla aktywnej zakładki."""
        sender = self.sender()
        if sender is None or sender is self.tabs.currentWidget():
            self.statusBar().showMessage(msg)

    def _on_tab_title_changed(self, tab: LogTab, title: str) -> None:
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setTabText(index, title)

    def _on_tab_changed(self, index: int) -> None:
        """Aktualizuje status bar, slider, minimap i follow action po zmianie zakładki."""
        self._update_ui_state()
        if index < 0:
            self.statusBar().showMessage(self.t("st_ready"))
            if self._follow_action is not None:
                self._follow_action.setChecked(False)
            self._is_restoring_toolbar = True
            try:
                self.search_entry.clear()
                self.search_regex_cb.setChecked(False)
                self.search_case_cb.setChecked(False)
                self.search_negate_cb.setChecked(False)
                self.filter_entry.clear()
                self.filter_regex_cb.setChecked(False)
                self.filter_case_cb.setChecked(False)
                self.filter_negate_cb.setChecked(False)
                self.filter_context_spin.setValue(0)
            finally:
                self._is_restoring_toolbar = False
            return
        tab = self.tabs.widget(index)
        if isinstance(tab, LogTab):
            self._load_toolbar_from_tab(tab)
            tab._refresh_status()
            tab._update_position_slider()
            tab._update_minimap_viewport()
            if self._follow_action is not None:
                self._follow_action.setChecked(tab.follow_active)
            # Auto-focus na text widget — bez tego szare podświetlenie
            # bieżącej linii nie jest widoczne po przełączeniu zakładki
            # (kursor jest tam, ale Qt nie renderuje ExtraSelections dla
            # nieważnego widżetu). setFocus + _update_current_line_highlight
            # wymusza przerysowanie.
            tab.text.setFocus()
            tab._update_current_line_highlight()


    def _save_toolbar_to_tab(self, *args, **kwargs) -> None:
        if self._is_restoring_toolbar: return
        tab = self.tabs.currentWidget()
        if not isinstance(tab, LogTab): return

        tab.tb_search_text = self.search_entry.text()
        tab.tb_search_regex = self.search_regex_cb.isChecked()
        tab.tb_search_case = self.search_case_cb.isChecked()
        tab.tb_search_negate = self.search_negate_cb.isChecked()

        tab.tb_filter_text = self.filter_entry.text()
        tab.tb_filter_regex = self.filter_regex_cb.isChecked()
        tab.tb_filter_case = self.filter_case_cb.isChecked()
        tab.tb_filter_negate = self.filter_negate_cb.isChecked()
        tab.tb_filter_context = self.filter_context_spin.value()

    def _load_toolbar_from_tab(self, tab: LogTab) -> None:
        self._is_restoring_toolbar = True
        try:
            self.search_entry.setText(tab.tb_search_text)
            self.search_regex_cb.setChecked(tab.tb_search_regex)
            self.search_case_cb.setChecked(tab.tb_search_case)
            self.search_negate_cb.setChecked(tab.tb_search_negate)

            self.filter_entry.setText(tab.tb_filter_text)
            self.filter_regex_cb.setChecked(tab.tb_filter_regex)
            self.filter_case_cb.setChecked(tab.tb_filter_case)
            self.filter_negate_cb.setChecked(tab.tb_filter_negate)
            self.filter_context_spin.setValue(tab.tb_filter_context)
        finally:
            self._is_restoring_toolbar = False

    def _update_ui_state(self) -> None:
        has_tabs = self.tabs.count() > 0
        if hasattr(self, "_context_actions"):
            for action in self._context_actions:
                action.setEnabled(has_tabs)

        toolbar_widgets = [
            getattr(self, "search_entry", None),
            getattr(self, "search_regex_cb", None),
            getattr(self, "search_case_cb", None),
            getattr(self, "search_negate_cb", None),
            getattr(self, "btn_find_next", None),
            getattr(self, "btn_find_prev", None),
            getattr(self, "btn_clear_search", None),

            getattr(self, "filter_entry", None),
            getattr(self, "filter_regex_cb", None),
            getattr(self, "filter_case_cb", None),
            getattr(self, "filter_negate_cb", None),
            getattr(self, "filter_context_spin", None),
            getattr(self, "btn_apply_filter", None),
            getattr(self, "btn_clear_filter", None),
        ]
        for w in toolbar_widgets:
            if w is not None:
                w.setEnabled(has_tabs)

    def _on_tab_close_requested(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if not isinstance(tab, LogTab):
            return
        if len(tab.edit_buffer) > 0:
            choice = QMessageBox.question(
                self, self.t("app_title"),
                self.t("msg_clear_edits").format(n=len(tab.edit_buffer)),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if choice != QMessageBox.Yes:
                return
        tab.close()
        self.tabs.removeTab(index)
        tab.deleteLater()
        self._update_ui_state()

    # ----------------------------------------------------------- file ops ---
    def cmd_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.t("mi_open"), "", OPEN_FILETYPES)
        if path:
            self.open_file_in_tab(path)

    # ---------------------------------------------- delegated cmd_* methods ---
    # Te metody są definiowane na oknie aby toolbar/menubar mogły się do nich
    # podpiąć przy budowie UI (zanim istnieje jakakolwiek zakładka). Delegują
    # wywołanie do aktywnej zakładki (tabs.currentWidget()).

    def cmd_find_dialog(self):
        return self._delegate_to_tab("cmd_find_dialog")

    def cmd_find_next(self):
        return self._delegate_to_tab("cmd_find_next")

    def cmd_find_prev(self):
        return self._delegate_to_tab("cmd_find_prev")

    def cmd_clear_search(self):
        return self._delegate_to_tab("cmd_clear_search")

    def cmd_filter_dialog(self):
        return self._delegate_to_tab("cmd_filter_dialog")

    def cmd_apply_filter(self):
        return self._delegate_to_tab("cmd_apply_filter")

    def cmd_clear_filter(self, silent: bool = False):
        return self._delegate_to_tab("cmd_clear_filter", silent)

    def cmd_goto(self):
        return self._delegate_to_tab("cmd_goto")

    def cmd_goto_start(self):
        return self._delegate_to_tab("cmd_goto_start")

    def cmd_goto_end(self):
        return self._delegate_to_tab("cmd_goto_end")

    def cmd_edit_line(self):
        return self._delegate_to_tab("cmd_edit_line")

    def cmd_format_selection(self):
        return self._delegate_to_tab("cmd_format_selection")

    def cmd_save_edits(self):
        return self._delegate_to_tab("cmd_save_edits")

    def cmd_save_as(self):
        return self._delegate_to_tab("cmd_save_as")

    def cmd_export(self):
        return self._delegate_to_tab("cmd_export")

    def cmd_clear_edits(self):
        return self._delegate_to_tab("cmd_clear_edits")

    def cmd_toggle_bookmark(self):
        return self._delegate_to_tab("cmd_toggle_bookmark")

    def cmd_next_bookmark(self):
        return self._delegate_to_tab("cmd_next_bookmark")

    def cmd_prev_bookmark(self):
        return self._delegate_to_tab("cmd_prev_bookmark")

    def cmd_clear_bookmarks(self):
        return self._delegate_to_tab("cmd_clear_bookmarks")

    def cmd_toggle_follow(self):
        return self._delegate_to_tab("cmd_toggle_follow")

    def cmd_set_encoding(self, encoding: str):
        return self._delegate_to_tab("cmd_set_encoding", encoding)

    def cmd_next_tab(self):
        if self.tabs.count() > 1:
            idx = (self.tabs.currentIndex() + 1) % self.tabs.count()
            self.tabs.setCurrentIndex(idx)

    def cmd_prev_tab(self):
        if self.tabs.count() > 1:
            idx = (self.tabs.currentIndex() - 1) % self.tabs.count()
            self.tabs.setCurrentIndex(idx)

    def cmd_close_tab(self):
        idx = self.tabs.currentIndex()
        if idx >= 0:
            self._on_tab_close_requested(idx)

    # --------------------------------------------------------- settings ---
    def cmd_settings(self) -> None:
        dialog = SettingsDialog(self, self)
        if dialog.exec() == QDialog.Accepted:
            family, size, ws, md, ml, ii = dialog.get_values()
            font_changed = (family != self.font_family) or (size != self.font_size)
            self.font_family = family
            self.font_size = size
            self.window_size_lines = ws
            self.max_display_lines = md
            self.max_display_line_length = ml
            self.index_interval_bytes = ii
            self.config.set("font_family", family)
            self.config.set("font_size", size)
            self.config.set("window_size_lines", ws)
            self.config.set("max_display_lines", md)
            self.config.set("max_display_line_length", ml)
            self.config.set("index_interval_bytes", ii)
            # Aplikuj zmiany do wszystkich zakładek
            for i in range(self.tabs.count()):
                t = self.tabs.widget(i)
                if not isinstance(t, LogTab):
                    continue
                if font_changed:
                    t._apply_font_to_text()
                if t.file_path and t.indexer:
                    if t.indexer.index_interval_bytes != ii:
                        try:
                            cursor = t.text.textCursor()
                            saved_line = t.line_map[cursor.blockNumber()] if t.line_map else 0
                        except Exception:
                            saved_line = 0
                        try:
                            t.indexer.close()
                        except Exception:
                            pass
                        t._start_reindex(saved_line)
                    else:
                        try:
                            cursor = t.text.textCursor()
                            saved_line = t.line_map[cursor.blockNumber()] if t.line_map else 0
                        except Exception:
                            saved_line = 0
                        t._load_window(at_line=saved_line)
            QMessageBox.information(self, self.t("app_title"), self.t("msg_settings_applied"))

    # ----------------------------------------------------------- DnD ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
        if paths:
            self._on_files_dropped(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    @Slot(list)
    def _on_files_dropped(self, paths: List[str]) -> None:
        """DnD otwiera każdy plik w nowej zakładce."""
        existing = [p for p in paths if os.path.isfile(p)]
        if not existing:
            QMessageBox.information(self, self.t("app_title"), self.t("msg_dnd_no_files"))
            return
        if len(existing) == 1:
            self.open_file_in_tab(existing[0])
            self.statusBar().showMessage(self.t("msg_dnd_opened").format(path=existing[0]))
        else:
            choice, ok = QInputDialog.getInt(
                self, self.t("app_title"),
                self.t("msg_dnd_multiple").format(n=len(existing)),
                1, 1, len(existing), 1,
            )
            if ok:
                self.open_file_in_tab(existing[choice - 1])

    # ----------------------------------------------------------- misc ----
    def cmd_about(self) -> None:
        QMessageBox.about(self, self.t("app_title"), self.t("msg_about"))

    def closeEvent(self, event):
        """Zamknij wszystkie zakładki (z pytaniem o niezapisane edycje)."""
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if not isinstance(tab, LogTab):
                continue
            if len(tab.edit_buffer) > 0:
                if not QMessageBox.question(
                    self, self.t("app_title"),
                    self.t("msg_clear_edits").format(n=len(tab.edit_buffer)) + "\n\n" + self.t("mi_exit") + "?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                ) == QMessageBox.Yes:
                    event.ignore()
                    return
            tab.close()
        event.accept()
