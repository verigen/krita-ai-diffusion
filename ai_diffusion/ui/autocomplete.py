import csv
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, cast

from PyQt5.QtCore import QAbstractProxyModel, QRect, QSize, QStringListModel, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPalette, QPen, QPixmap, QTextCursor
from PyQt5.QtWidgets import QApplication, QCompleter, QPlainTextEdit, QStyle, QStyledItemDelegate

from ..files import FileFilter
from ..model.root import root
from ..prompt_library import PromptEntry, PromptFilter, PromptLibrary
from ..settings import settings
from ..text import char16_index_to_str_index
from ..util import ensure, plugin_dir, user_data_dir
from . import theme


class TagType(Enum):
    general = 0
    artist = 1
    copyright = 3
    character = 4
    meta = 5


@dataclass
class TagItem:
    tag: str
    type_: TagType
    count: int
    meta: str


class TagListModel(QStringListModel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tags = []

    def setTags(self, tags):
        self.tags = tags
        super().setStringList([tag.tag for tag in tags])


def cursor_position(text: str, cursor: QTextCursor):
    return char16_index_to_str_index(text, cursor.position())


class TagCompleterDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        tag_item = self._tag_item(index)

        painter = ensure(painter)
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            text_color = option.palette.highlightedText().color()
        else:
            painter.fillRect(option.rect, self._background_color(tag_item.type_))
            text_color = option.palette.text().color()

        # Set up fonts
        normal_font = QFont(option.font)
        small_font = QFont(option.font)
        small_font.setPointSize(normal_font.pointSize() - 2)
        small_font.setItalic(True)

        # Calculate rectangles
        rect = option.rect
        meta_width = QFontMetrics(normal_font).width(tag_item.meta) + 10
        meta_rect = QRect(rect.right() - meta_width, rect.top(), meta_width, rect.height())

        # Draw the tag
        painter.setFont(normal_font)
        painter.setPen(QPen(text_color))
        painter.drawText(
            rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tag_item.tag
        )

        # Draw the meta info
        painter.setFont(small_font)
        painter.setPen(QPen(text_color.lighter(150)))  # Slightly lighter color for meta info
        painter.drawText(
            meta_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tag_item.meta
        )

        painter.restore()

    def sizeHint(self, option, index):
        tag_item = self._tag_item(index)

        normal_font = QFont(option.font)
        small_font = QFont(option.font)
        small_font.setPointSize(normal_font.pointSize() - 2)

        tag_width = QFontMetrics(normal_font).width(tag_item.tag)
        meta_width = QFontMetrics(small_font).width(tag_item.meta)

        total_width = tag_width + meta_width + 10  # Add some padding
        size = super().sizeHint(option, index)

        return QSize(total_width, size.height())

    def _tag_item(self, index) -> TagItem:
        model = index.model()
        while isinstance(model, QAbstractProxyModel):
            model = model.sourceModel()
        assert isinstance(model, TagListModel)
        return model.tags[index.model().mapToSource(index).row()]

    def _background_color(self, tag_type):
        tag_colors = {
            TagType.general: QColor(Qt.GlobalColor.blue),
            TagType.artist: QColor(Qt.GlobalColor.red),
            TagType.copyright: QColor(Qt.GlobalColor.yellow),
            TagType.character: QColor(Qt.GlobalColor.green),
            TagType.meta: QColor(Qt.GlobalColor.cyan),
        }

        tag_color = tag_colors.get(tag_type, QColor(Qt.GlobalColor.white))

        # Get the default background color for dropdown items
        app = cast(QApplication, QApplication.instance())
        base_color = app.palette().color(QPalette.Base)

        # Blend the colors
        return self._blend_colors(base_color, tag_color, 0.2)

    # how is there not a native qt way to do this
    def _blend_colors(self, from_, to, factor):
        r = from_.red() * (1 - factor) + to.red() * factor
        g = from_.green() * (1 - factor) + to.green() * factor
        b = from_.blue() * (1 - factor) + to.blue() * factor
        return QColor(int(r), int(g), int(b))


class PromptCompleterDelegate(QStyledItemDelegate):
    _star_pixmap = QPixmap(str(theme.icon_path / "star.png")).scaled(
        16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )

    def paint(self, painter, option, index):
        entry = self._prompt_entry(index)

        painter = ensure(painter)
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

    def _prompt_entry(self, index) -> PromptEntry:
        model = index.model()
        while isinstance(model, QAbstractProxyModel):
            model = model.sourceModel()
        assert isinstance(model, PromptLibrary)
        row_index = index
        while isinstance(row_index.model(), QAbstractProxyModel):
            row_index = row_index.model().mapToSource(row_index)
        return model[row_index.row()]


# Ensure there's only one of these globally. It gets pretty big if we have one per widget.
_tag_model = TagListModel([])
_tag_files = None


class PromptAutoComplete:
    def __init__(self, widget: QPlainTextEdit):
        self._widget = widget
        self._completer = QCompleter()
        self._completer.activated.connect(self._insert_completion)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setWidget(widget)
        self._popup = ensure(self._completer.popup())
        self._item_delegate = ensure(self._popup.itemDelegate())
        self._completion_prefix = ""
        self._completion_suffix = ""

        self._lora_model = FileFilter(root.files.loras)
        self._lora_model.available_only = True

        self._prompt_filter = PromptFilter(root.prompts)
        self._prompt_filter.sort_by_usage = True
        self._prompt_delegate = PromptCompleterDelegate()

        self._reload_tag_model()
        settings.changed.connect(self._reload_tag_model)

    def _reload_tag_model(self):
        global _tag_files

        tag_files = settings.tag_files

        if _tag_files == tag_files:
            return

        all_tags = []
        plugin_tags_path = plugin_dir / "tags"
        user_tags_path = user_data_dir / "tags"
        for tag_name in tag_files:
            plugin_tag_path = plugin_tags_path / f"{tag_name}.csv"
            user_tag_path = user_tags_path / f"{tag_name}.csv"
            tag_path = user_tag_path if user_tag_path.is_file() else plugin_tag_path
            if not tag_path.is_file():
                # formerly active file that was deleted
                continue

            with tag_path.open("r", encoding="utf-8") as f:
                csv_reader = csv.reader(f)
                for tag, type_str, count, _aliases in csv_reader:
                    if type_str.isdigit():  # skip header rows if they exist
                        tag = tag.replace("_", " ")
                        try:
                            tag_type = TagType(int(type_str))
                        except Exception:  # default to general category if category unrecognised
                            tag_type = TagType(0)
                        count = int(count)
                        count_str = str(count)
                        if count > 1_000_000:
                            count_str = f"{count / 1_000_000:.0f}m"
                        elif count > 1_000:
                            count_str = f"{count / 1_000:.0f}k"
                        meta = f"{tag_name} {count_str}"
                        all_tags.append(TagItem(tag, tag_type, count, meta))

        sorted_tags = sorted(all_tags, key=lambda x: x.count, reverse=True)
        seen = set()
        unique_tags = [a for a in sorted_tags if a.tag not in seen and seen.add(a.tag) is None]

        _tag_model.setTags(unique_tags)
        _tag_files = tag_files

    def _current_text(self, separators=" ()>,|{\n") -> str:
        text = self._widget.toPlainText()
        start = pos = cursor_position(text, self._widget.textCursor())
        while pos > 0 and (text[pos - 1] not in separators or pos > 1 and text[pos - 2] == "\\"):
            pos -= 1
        return text[pos:start]

    def check_completion(self):
        prefix = self._current_text()
        name = prefix.removeprefix("<lora:")
        lora_mode = len(prefix) > len(name)
        layer_mode = False
        if not lora_mode:
            name = prefix.removeprefix("<layer:")
            layer_mode = len(prefix) > len(name)
        prompt_mode = False
        if (
            not lora_mode
            and not layer_mode
            and prefix.startswith("/")
            and not self._widget.is_negative
        ):
            name = prefix.removeprefix("/")
            prompt_mode = True

        if lora_mode:
            self._completer.setModel(self._lora_model)
            self._completion_prefix = name
            self._completion_suffix = ">"
            self._popup.setItemDelegate(self._item_delegate)
        elif layer_mode:
            layers = root.active_model.document.layers
            layer_model = QStringListModel([layer.name for layer in layers.images])
            self._completer.setModel(layer_model)
            self._completion_prefix = name
            self._completion_suffix = ">"
            self._popup.setItemDelegate(self._item_delegate)
        elif prompt_mode:
            self._completer.setModel(self._prompt_filter)
            self._completion_prefix = name
            self._completion_suffix = ""
            self._popup.setItemDelegate(self._prompt_delegate)
        else:
            # fall through to tag search
            self._completion_prefix = prefix = self._current_text(separators="()>,|{\n").lstrip()
            name = prefix.replace("\\(", "(").replace("\\)", ")")
            if not name.startswith("<") and len(name.rstrip()) > 2:
                self._completer.setModel(_tag_model)
                self._popup.setItemDelegate(TagCompleterDelegate())
                self._completion_suffix = ""
            else:
                self._popup.hide()
                return
        self._completer.setCompletionPrefix(name)
        rect = self._widget.cursorRect()
        self._popup.setCurrentIndex(ensure(self._completer.completionModel()).index(0, 0))
        scrollbar = ensure(self._popup.verticalScrollBar())
        rect.setWidth(self._popup.sizeHintForColumn(0) + scrollbar.sizeHint().width())
        self._completer.complete(rect)

    def _insert_completion(self, completion):
        triggers = ""
        prefix = self._current_text()
        if prefix.startswith("<lora:"):
            if file := root.files.loras.find(f"{completion}.safetensors"):
                triggers = " " + file.meta("lora_triggers", "")
        elif prefix.startswith("<layer:"):
            pass
        elif prefix.startswith("/") and not self._widget.is_negative:
            entry = self._current_prompt_entry()
            if entry is None:
                return
            completion = entry.positive
            root.prompts.mark_used(entry.id)
        else:  # tag completion
            # escape () in tags so they won't be interpreted as prompt weights
            completion = completion.replace("(", "\\(").replace(")", "\\)")
        text = self._widget.toPlainText()
        initial_cursor = self._widget.textCursor()
        pos = cursor_position(text, initial_cursor)
        start_pos = pos - len(self._completion_prefix)  # pos in python string
        start_cursor_pos = initial_cursor.position() - len(self._completion_prefix)  # pos in utf-16
        fill = completion + self._completion_suffix + triggers
        text = text[:start_pos] + fill + text[pos:]
        self._widget.setPlainText(text)
        cursor = self._widget.textCursor()
        cursor.setPosition(start_cursor_pos + len(fill))
        self._widget.setTextCursor(cursor)

    def _current_prompt_entry(self) -> PromptEntry | None:
        index = self._completer.currentIndex()
        if not index.isValid():
            return None
        source_index = ensure(self._completer.completionModel()).mapToSource(index)
        if not source_index.isValid():
            return None
        return self._prompt_filter[source_index.row()]

    @property
    def is_active(self):
        return self._popup.isVisible()

    action_keys: ClassVar[list[Qt.Key]] = [
        Qt.Key.Key_Enter,
        Qt.Key.Key_Return,
        Qt.Key.Key_Up,
        Qt.Key.Key_Down,
        Qt.Key.Key_Tab,
        Qt.Key.Key_Backtab,
    ]
