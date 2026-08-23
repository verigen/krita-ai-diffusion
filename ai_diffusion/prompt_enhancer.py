from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QAbstractListModel, QModelIndex, Qt, QUuid, pyqtSignal

from .localization import translate as _
from .util import client_logger as log
from .util import encode_json, read_json_with_comments, user_data_dir


@dataclass
class EnhancerPreset:
    id: str
    name: str
    system_prompt: str = ""
    model: str = ""  # "" -> use settings.enhancer_model
    temperature: float | None = None  # None -> server default
    created: float = 0.0

    @staticmethod
    def from_dict(data: dict[str, Any]):
        return EnhancerPreset(
            id=data["id"],
            name=data.get("name", ""),
            system_prompt=data.get("system_prompt", ""),
            model=data.get("model", ""),
            temperature=data.get("temperature", None),
            created=data.get("created", 0.0),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "system_prompt": self.system_prompt,
            "model": self.model,
            "temperature": self.temperature,
            "created": self.created,
        }


def builtin_presets() -> list[EnhancerPreset]:
    now = time.time()
    return [
        EnhancerPreset(
            id=QUuid.createUuid().toString(),
            name=_("Detailed"),
            system_prompt=_(
                "Rewrite the given prompt into a detailed, richly descriptive prompt for an image"
                " generation model. Expand on subject, setting, lighting, mood and style using"
                " comma-separated descriptive phrases. Reply only with the rewritten prompt, no"
                " explanations."
            ),
            created=now,
        ),
        EnhancerPreset(
            id=QUuid.createUuid().toString(),
            name=_("Concise"),
            system_prompt=_(
                "Rewrite the given prompt to be short and concise, keeping only the essential"
                " subject and style tags and removing redundant or filler words. Reply only with"
                " the rewritten prompt, no explanations."
            ),
            created=now,
        ),
        EnhancerPreset(
            id=QUuid.createUuid().toString(),
            name=_("Danbooru Tags"),
            system_prompt=_(
                "Rewrite the given prompt as a comma-separated list of Danbooru-style tags"
                " describing the subject, pose, clothing, setting and art style. Reply only with"
                " the rewritten prompt, no explanations."
            ),
            created=now,
        ),
    ]


class EnhancerPresets(QAbstractListModel):
    system_prompt_role = Qt.ItemDataRole.UserRole + 1
    model_role = Qt.ItemDataRole.UserRole + 2
    temperature_role = Qt.ItemDataRole.UserRole + 3
    is_default_role = Qt.ItemDataRole.UserRole + 4

    default_changed = pyqtSignal(str)

    def __init__(self, database: Path | None = None, parent=None):
        super().__init__(parent)
        self._database = database or user_data_dir / "enhancer" / "presets.json"
        self._presets: list[EnhancerPreset] = []
        self._default: str = ""
        self.load()

    def rowCount(self, parent: QModelIndex | None = None):
        return len(self._presets)

    def data(self, index: QModelIndex, role: int = 0):
        if 0 <= index.row() < len(self._presets):
            item = self._presets[index.row()]
            match role:
                case Qt.ItemDataRole.DisplayRole | Qt.ItemDataRole.EditRole:
                    return item.name
                case Qt.ItemDataRole.UserRole:
                    return item.id
                case EnhancerPresets.system_prompt_role:
                    return item.system_prompt
                case EnhancerPresets.model_role:
                    return item.model
                case EnhancerPresets.temperature_role:
                    return item.temperature
                case EnhancerPresets.is_default_role:
                    return item.id == self._default
                case _:
                    return None

    def create(
        self,
        name: str,
        system_prompt: str = "",
        model: str = "",
        temperature: float | None = None,
    ):
        preset = EnhancerPreset(
            id=QUuid.createUuid().toString(),
            name=name,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
            created=time.time(),
        )
        self.add(preset)
        return preset

    def add(self, preset: EnhancerPreset):
        end = len(self._presets)
        self.beginInsertRows(QModelIndex(), end, end)
        self._presets.append(preset)
        self.endInsertRows()
        if not self._default:
            self._set_default_silent(preset.id)
        self.save()
        return preset

    def duplicate(self, id: str):
        preset = self.find(id)
        if preset is None:
            return None
        copy = EnhancerPreset(
            id=QUuid.createUuid().toString(),
            name=f"{preset.name} ({_('copy')})",
            system_prompt=preset.system_prompt,
            model=preset.model,
            temperature=preset.temperature,
            created=time.time(),
        )
        return self.add(copy)

    def remove(self, id: str):
        index = self.find_index(id)
        if index == -1:
            return
        was_default = self._default == id
        self.beginRemoveRows(QModelIndex(), index, index)
        del self._presets[index]
        self.endRemoveRows()
        if was_default:
            self._set_default_silent(self._presets[0].id if self._presets else "")
        if not self._presets:
            for preset in builtin_presets():
                self.add(preset)
        else:
            self.save()

    def update(self, id: str, **changes: Any):
        index = self.find_index(id)
        if index == -1:
            return
        preset = self._presets[index]
        for key, value in changes.items():
            setattr(preset, key, value)
        self.dataChanged.emit(self.index(index), self.index(index))
        self.save()

    def find(self, id: str):
        return next((p for p in self._presets if p.id == id), None)

    def find_index(self, id: str):
        return next((i for i, p in enumerate(self._presets) if p.id == id), -1)

    @property
    def default(self) -> str:
        return self._default

    def set_default(self, id: str):
        if id != self._default and self.find(id) is not None:
            self._set_default_silent(id)
            self.save()

    def _set_default_silent(self, id: str):
        old_index = self.find_index(self._default)
        self._default = id
        if old_index != -1:
            self.dataChanged.emit(self.index(old_index), self.index(old_index))
        new_index = self.find_index(id)
        if new_index != -1:
            self.dataChanged.emit(self.index(new_index), self.index(new_index))
        self.default_changed.emit(id)

    def load(self):
        if not self._database or not self._database.exists():
            self._seed_builtins()
            return
        try:
            data = read_json_with_comments(self._database)
            if isinstance(data, list):
                presets = [EnhancerPreset.from_dict(p) for p in data]
                default = presets[0].id if presets else ""
            else:
                presets = [EnhancerPreset.from_dict(p) for p in data.get("presets", [])]
                default = data.get("default", "")
                ids = {p.id for p in presets}
                if default not in ids and presets:
                    default = presets[0].id
            if presets:
                end = len(self._presets)
                self.beginInsertRows(QModelIndex(), end, end + len(presets) - 1)
                self._presets.extend(presets)
                self.endInsertRows()
                self._default = default
            log.info(f"Loaded {len(self)} prompt enhancer presets from {self._database}")
        except Exception as e:
            log.error(f"Failed to read {self._database}: {e}")

    def save(self):
        if self._database:
            data = {
                "version": 1,
                "default": self._default,
                "presets": [p.to_dict() for p in self._presets],
            }
            self._database.parent.mkdir(parents=True, exist_ok=True)
            self._database.write_text(json.dumps(data, indent=2, default=encode_json))

    def _seed_builtins(self):
        presets = builtin_presets()
        end = len(self._presets)
        self.beginInsertRows(QModelIndex(), end, end + len(presets) - 1)
        self._presets.extend(presets)
        self.endInsertRows()
        self._default = presets[0].id if presets else ""
        self.save()

    def __iter__(self):
        return iter(self._presets)

    def __len__(self):
        return len(self._presets)

    def __getitem__(self, index: int):
        return self._presets[index]
