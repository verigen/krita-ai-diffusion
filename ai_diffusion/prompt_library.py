from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QAbstractListModel, QModelIndex, QSortFilterProxyModel, Qt, QUuid

from .util import client_logger as log
from .util import encode_json, read_json_with_comments, user_data_dir


@dataclass
class PromptEntry:
    id: str
    name: str
    positive: str
    category: str = ""
    tags: list[str] = field(default_factory=list)
    favorite: bool = False
    use_count: int = 0
    last_used: float | None = None
    created: float = 0.0

    @staticmethod
    def from_dict(data: dict[str, Any]):
        return PromptEntry(
            id=data["id"],
            name=data.get("name", ""),
            positive=data.get("positive", ""),
            category=data.get("category", ""),
            tags=list(data.get("tags", [])),
            favorite=data.get("favorite", False),
            use_count=data.get("use_count", 0),
            last_used=data.get("last_used", None),
            created=data.get("created", 0.0),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "positive": self.positive,
            "category": self.category,
            "tags": self.tags,
            "favorite": self.favorite,
            "use_count": self.use_count,
            "last_used": self.last_used,
            "created": self.created,
        }


class PromptLibrary(QAbstractListModel):
    positive_role = Qt.ItemDataRole.UserRole + 1
    category_role = Qt.ItemDataRole.UserRole + 2
    favorite_role = Qt.ItemDataRole.UserRole + 3
    tags_role = Qt.ItemDataRole.UserRole + 4

    def __init__(self, database: Path | None = None, parent=None):
        super().__init__(parent)
        self._database = database or user_data_dir / "prompts" / "library.json"
        self._entries: list[PromptEntry] = []
        self.load()

    def rowCount(self, parent: QModelIndex | None = None):
        return len(self._entries)

    def data(self, index: QModelIndex, role: int = 0):
        if 0 <= index.row() < len(self._entries):
            item = self._entries[index.row()]
            match role:
                case Qt.ItemDataRole.DisplayRole | Qt.ItemDataRole.EditRole:
                    return item.name
                case Qt.ItemDataRole.UserRole:
                    return item.id
                case PromptLibrary.positive_role:
                    return item.positive
                case PromptLibrary.category_role:
                    return item.category
                case PromptLibrary.favorite_role:
                    return item.favorite
                case PromptLibrary.tags_role:
                    return item.tags
                case _:
                    return None

    def create(self, name: str, positive: str, category: str = "", tags: list[str] | None = None):
        entry = PromptEntry(
            id=QUuid.createUuid().toString(),
            name=name,
            positive=positive,
            category=category,
            tags=list(tags) if tags else [],
            created=time.time(),
        )
        self.add(entry)
        return entry

    def add(self, entry: PromptEntry):
        end = len(self._entries)
        self.beginInsertRows(QModelIndex(), end, end)
        self._entries.append(entry)
        self.endInsertRows()
        self.save()
        return entry

    def remove(self, id: str):
        index = self.find_index(id)
        if index == -1:
            return
        self.beginRemoveRows(QModelIndex(), index, index)
        del self._entries[index]
        self.endRemoveRows()
        self.save()

    def update(self, id: str, **changes: Any):
        index = self.find_index(id)
        if index == -1:
            return
        entry = self._entries[index]
        for key, value in changes.items():
            setattr(entry, key, value)
        self.dataChanged.emit(self.index(index), self.index(index))
        self.save()

    def mark_used(self, id: str):
        index = self.find_index(id)
        if index == -1:
            return
        entry = self._entries[index]
        entry.use_count += 1
        entry.last_used = time.time()
        self.dataChanged.emit(self.index(index), self.index(index))
        self.save()

    def find(self, id: str):
        return next((e for e in self._entries if e.id == id), None)

    def find_index(self, id: str):
        return next((i for i, e in enumerate(self._entries) if e.id == id), -1)

    def load(self):
        if not self._database or not self._database.exists():
            return
        try:
            data = read_json_with_comments(self._database)
            entries = [PromptEntry.from_dict(e) for e in data]
            if entries:
                end = len(self._entries)
                self.beginInsertRows(QModelIndex(), end, end + len(entries) - 1)
                self._entries.extend(entries)
                self.endInsertRows()
            log.info(f"Loaded {len(self)} prompts from {self._database}")
        except Exception as e:
            log.error(f"Failed to read {self._database}: {e}")

    def save(self):
        if self._database:
            db = [e.to_dict() for e in self._entries]
            self._database.parent.mkdir(parents=True, exist_ok=True)
            self._database.write_text(json.dumps(db, indent=2, default=encode_json))

    def __iter__(self):
        return iter(self._entries)

    def __len__(self):
        return len(self._entries)

    def __getitem__(self, index: int):
        return self._entries[index]


class PromptFilter(QSortFilterProxyModel):
    def __init__(self, source: PromptLibrary, parent=None):
        super().__init__(parent)
        self._search_text = ""
        self._category = ""
        self._favorites_only = False
        self.setSourceModel(source)
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.sort(0)

    @property
    def search_text(self):
        return self._search_text

    @search_text.setter
    def search_text(self, value: str):
        self._search_text = value
        self.invalidateFilter()

    @property
    def category(self):
        return self._category

    @category.setter
    def category(self, value: str):
        self._category = value
        self.invalidateFilter()

    @property
    def favorites_only(self):
        return self._favorites_only

    @favorites_only.setter
    def favorites_only(self, value: bool):
        self._favorites_only = value
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex):
        source: PromptLibrary = self.sourceModel()  # type: ignore
        entry = source[source_row]
        if self._favorites_only and not entry.favorite:
            return False
        if self._category and entry.category != self._category:
            return False
        if self._search_text:
            needle = self._search_text.lower()
            haystack = (entry.name, entry.positive, *entry.tags)
            if not any(needle in h.lower() for h in haystack):
                return False
        return True

    def __getitem__(self, index: int) -> PromptEntry:
        source: PromptLibrary = self.sourceModel()  # type: ignore
        source_index = self.mapToSource(self.index(index, 0))
        return source[source_index.row()]
