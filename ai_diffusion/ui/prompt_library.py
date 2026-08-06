from __future__ import annotations

from PyQt5.QtCore import QMetaObject, QModelIndex, QRect, QSize, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QIcon, QPen, QPixmap
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QMessageBox,
    QPlainTextEdit,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..localization import translate as _
from ..model.model import DocumentModel, Workspace
from ..model.properties import Bind, Binding, bind
from ..model.root import root
from ..prompt_library import PromptEntry, PromptFilter, PromptLibrary
from ..util import ensure
from . import theme
from .theme import SignalBlocker
from .widget import WorkspaceSelectWidget


def _create_tool_button(parent: QWidget, icon: QIcon, tooltip: str, handler):
    button = QToolButton(parent)
    button.setIcon(icon)
    button.setToolTip(tooltip)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setAutoRaise(True)
    button.clicked.connect(handler)
    return button


def _favorite_icon():
    return QIcon(str(theme.icon_path / "star.png"))


class PromptItemDelegate(QStyledItemDelegate):
    _star_pixmap = QPixmap(str(theme.icon_path / "star.png")).scaled(
        16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )

    def paint(self, painter, option, index):
        painter = ensure(painter)
        library: PromptLibrary = index.model().sourceModel()  # type: ignore
        entry = library[index.model().mapToSource(index).row()]  # type: ignore

        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            text_color = option.palette.highlightedText().color()
        else:
            text_color = option.palette.text().color()

        name_font = QFont(option.font)
        name_font.setBold(True)
        preview_font = QFont(option.font)
        preview_font.setPointSize(max(6, option.font.pointSize() - 1))

        margin = 4
        star_width = 16 if entry.favorite else 0
        rect = option.rect.adjusted(margin, margin, -margin - star_width, -margin)
        name_height = QFontMetrics(name_font).height()
        name_rect = QRect(rect.left(), rect.top(), rect.width(), name_height)
        preview_rect = QRect(
            rect.left(), name_rect.bottom() + 2, rect.width(), QFontMetrics(preview_font).height()
        )

        painter.setFont(name_font)
        painter.setPen(QPen(text_color))
        elided_name = QFontMetrics(name_font).elidedText(
            entry.name, Qt.TextElideMode.ElideRight, name_rect.width()
        )
        painter.drawText(
            name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_name
        )

        if entry.favorite:
            star_rect = QRect(option.rect.right() - margin - 16, option.rect.top() + margin, 16, 16)
            painter.drawPixmap(star_rect, self._star_pixmap)

        preview_text = entry.positive.replace("\n", " ")
        painter.setFont(preview_font)
        painter.setPen(QPen(QColor(theme.grey)))
        elided_preview = QFontMetrics(preview_font).elidedText(
            preview_text, Qt.TextElideMode.ElideRight, preview_rect.width()
        )
        painter.drawText(
            preview_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_preview
        )
        painter.restore()

    def sizeHint(self, option, index):
        name_font = QFont(option.font)
        name_font.setBold(True)
        preview_font = QFont(option.font)
        preview_font.setPointSize(max(6, option.font.pointSize() - 1))
        size = super().sizeHint(option, index)
        height = QFontMetrics(name_font).height() + QFontMetrics(preview_font).height() + 10
        return QSize(size.width(), height)


class PromptSaveDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        name: str = "",
        positive: str = "",
        category: str = "",
        tags: list[str] | None = None,
        categories: list[str] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText(_("Name"))
        self.name_edit.setText(name)

        self.category_edit = QComboBox(self)
        self.category_edit.setEditable(True)
        self.category_edit.addItems(categories or [])
        self.category_edit.setCurrentText(category)

        self.tags_edit = QLineEdit(self)
        self.tags_edit.setPlaceholderText(_("Tags, separated by commas"))
        self.tags_edit.setText(", ".join(tags or []))

        self.positive_edit = QPlainTextEdit(self)
        self.positive_edit.setPlainText(positive)

        form = QFormLayout()
        form.addRow(_("Name"), self.name_edit)
        form.addRow(_("Category"), self.category_edit)
        form.addRow(_("Tags"), self.tags_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.positive_edit, 1)
        layout.addWidget(buttons)

        self.resize(420, 360)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def values(self):
        positive = self.positive_edit.toPlainText()
        name = self.name_edit.text().strip() or positive.strip()[:40] or _("Untitled")
        category = self.category_edit.currentText().strip()
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        return name, positive, category, tags


class PromptLibraryWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._model: DocumentModel = root.active_model
        self._model_bindings: list[QMetaObject.Connection | Binding] = []
        self._library = root.prompts
        self._filter = PromptFilter(self._library)

        layout = QVBoxLayout(self)

        self.workspace_select = WorkspaceSelectWidget(self)
        top_layout = QHBoxLayout()
        top_layout.addWidget(self.workspace_select)
        top_layout.addStretch()
        layout.addLayout(top_layout)

        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText(_("Search prompts…"))
        self._search_edit.textChanged.connect(self._set_search_text)

        self._favorites_button = QToolButton(self)
        self._favorites_button.setIcon(_favorite_icon())
        self._favorites_button.setCheckable(True)
        self._favorites_button.setAutoRaise(True)
        self._favorites_button.setToolTip(_("Show favorites only"))
        self._favorites_button.toggled.connect(self._set_favorites_only)

        self._category_combo = QComboBox(self)
        self._category_combo.setToolTip(_("Filter by category"))
        self._category_combo.currentTextChanged.connect(self._set_category)

        search_layout = QHBoxLayout()
        search_layout.addWidget(self._search_edit, 1)
        search_layout.addWidget(self._favorites_button)
        search_layout.addWidget(self._category_combo)
        layout.addLayout(search_layout)

        self._list = QListView(self)
        self._list.setModel(self._filter)
        self._list.setItemDelegate(PromptItemDelegate(self._list))
        selection_model = ensure(self._list.selectionModel())
        selection_model.currentChanged.connect(self._update_selection)
        self._list.doubleClicked.connect(self._apply_selected)
        layout.addWidget(self._list, 1)

        self._preview = QPlainTextEdit(self)
        self._preview.setReadOnly(True)
        self._preview.setMaximumHeight(80)
        layout.addWidget(self._preview)

        self._apply_button = _create_tool_button(
            self, theme.icon("apply"), _("Apply to active prompt"), self._apply_selected
        )
        self._new_button = _create_tool_button(
            self, theme.icon("region-add"), _("New prompt"), self._start_new
        )
        self._edit_button = _create_tool_button(
            self, theme.icon("edit"), _("Edit"), self._start_edit
        )
        self._favorite_button = _create_tool_button(
            self, _favorite_icon(), _("Toggle favorite"), self._toggle_favorite
        )
        self._delete_button = _create_tool_button(
            self, theme.icon("discard"), _("Delete"), self._delete_selected
        )

        actions_layout = QHBoxLayout()
        actions_layout.addWidget(self._apply_button)
        actions_layout.addWidget(self._new_button)
        actions_layout.addWidget(self._edit_button)
        actions_layout.addWidget(self._favorite_button)
        actions_layout.addStretch()
        actions_layout.addWidget(self._delete_button)
        layout.addLayout(actions_layout)

        self._update_category_list()
        self._update_selection(QModelIndex(), QModelIndex())

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, model: DocumentModel):
        if self._model != model:
            Binding.disconnect_all(self._model_bindings)
            self._model = model
            self._model_bindings = [
                bind(model, "workspace", self.workspace_select, "value", Bind.one_way),
            ]

    def _current_entry(self) -> PromptEntry | None:
        index = self._list.currentIndex()
        if not index.isValid():
            return None
        return self._filter[index.row()]

    def _update_selection(self, current: QModelIndex, previous: QModelIndex):
        entry = self._current_entry()
        self._preview.setPlainText(entry.positive if entry else "")
        has_selection = entry is not None
        self._apply_button.setEnabled(has_selection)
        self._edit_button.setEnabled(has_selection)
        self._favorite_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)

    def _set_search_text(self, text: str):
        self._filter.search_text = text

    def _set_favorites_only(self, checked: bool):
        self._filter.favorites_only = checked

    def _set_category(self, text: str):
        self._filter.category = "" if text == _("All") else text

    def _update_category_list(self):
        categories = sorted({e.category for e in self._library if e.category})
        with SignalBlocker(self._category_combo):
            self._category_combo.clear()
            self._category_combo.addItem(_("All"))
            self._category_combo.addItems(categories)

    def _apply_selected(self, *_args):
        entry = self._current_entry()
        if entry is None:
            return
        model = root.model_for_active_document()
        if model is None:
            return
        model.workspace = Workspace.generation
        model.active_regions.active_or_root.positive = entry.positive
        self._library.mark_used(entry.id)

    def _start_new(self):
        self._open_save_dialog(_("New prompt"))

    def _start_edit(self):
        entry = self._current_entry()
        if entry is not None:
            self._open_save_dialog(_("Edit prompt"), entry)

    def _open_save_dialog(self, title: str, entry: PromptEntry | None = None):
        categories = sorted({e.category for e in self._library if e.category})
        dialog = PromptSaveDialog(
            self,
            title,
            name=entry.name if entry else "",
            positive=entry.positive if entry else "",
            category=entry.category if entry else "",
            tags=entry.tags if entry else None,
            categories=categories,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, positive, category, tags = dialog.values()
            if entry is None:
                self._library.create(name, positive, category, tags)
            else:
                self._library.update(
                    entry.id, name=name, positive=positive, category=category, tags=tags
                )
            self._update_category_list()

    def _toggle_favorite(self):
        entry = self._current_entry()
        if entry is not None:
            self._library.update(entry.id, favorite=not entry.favorite)

    def _delete_selected(self):
        entry = self._current_entry()
        if entry is None:
            return
        q = QMessageBox.question(
            self,
            _("Delete prompt"),
            _("Are you sure you want to delete '{name}' from the prompt library?").format(
                name=entry.name
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.StandardButton.No,
        )
        if q == QMessageBox.StandardButton.Yes:
            self._library.remove(entry.id)
            self._update_category_list()
