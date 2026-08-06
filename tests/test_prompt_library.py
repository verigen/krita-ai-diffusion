from pathlib import Path

from PyQt5.QtCore import QModelIndex, Qt

from ai_diffusion.prompt_library import PromptEntry, PromptFilter, PromptLibrary
from ai_diffusion.util import ensure


class EventHandler:
    def __init__(self, library: PromptLibrary):
        self.begin_insert = []
        self.end_insert = []
        self.begin_remove = []
        self.end_remove = []
        self.data_changed = []
        self.connect(library)

    def on_begin_insert(self, parent, start, end):
        self.begin_insert.append((parent, start, end))

    def on_end_insert(self):
        self.end_insert.append(())

    def on_begin_remove(self, parent, start, end):
        self.begin_remove.append((parent, start, end))

    def on_end_remove(self):
        self.end_remove.append(())

    def on_data_changed(self, start, end):
        self.data_changed.append((start, end))

    def connect(self, library: PromptLibrary):
        library.rowsAboutToBeInserted.connect(self.on_begin_insert)
        library.rowsInserted.connect(self.on_end_insert)
        library.rowsAboutToBeRemoved.connect(self.on_begin_remove)
        library.rowsRemoved.connect(self.on_end_remove)
        library.dataChanged.connect(self.on_data_changed)


def test_create_and_roles(tmp_path: Path):
    library = PromptLibrary(tmp_path / "library.json")
    entry = library.create("Portrait", "(masterpiece:1.2), portrait", category="people")

    assert library.data(library.index(0), Qt.ItemDataRole.DisplayRole) == "Portrait"
    assert library.data(library.index(0), Qt.ItemDataRole.EditRole) == "Portrait"
    assert library.data(library.index(0), Qt.ItemDataRole.UserRole) == entry.id
    assert library.data(library.index(0), PromptLibrary.positive_role) == entry.positive
    assert library.data(library.index(0), PromptLibrary.category_role) == "people"
    assert library.data(library.index(0), PromptLibrary.favorite_role) is False
    assert library.data(library.index(0), PromptLibrary.tags_role) == []


def test_add_remove(tmp_path: Path):
    library = PromptLibrary(tmp_path / "library.json")
    events = EventHandler(library)

    entry1 = library.create("A", "prompt a")
    entry2 = library.create("B", "prompt b")

    assert len(library) == 2
    assert events.begin_insert == [(QModelIndex(), 0, 0), (QModelIndex(), 1, 1)]
    assert len(events.end_insert) == 2

    library.remove(entry1.id)

    assert len(library) == 1
    assert library[0].id == entry2.id
    assert events.begin_remove == [(QModelIndex(), 0, 0)]
    assert len(events.end_remove) == 1


def test_update(tmp_path: Path):
    library = PromptLibrary(tmp_path / "library.json")
    events = EventHandler(library)
    entry = library.create("A", "prompt a")

    library.update(entry.id, name="Renamed", favorite=True)

    assert entry.name == "Renamed"
    assert entry.favorite is True
    assert events.data_changed == [(library.index(0), library.index(0))]


def test_mark_used(tmp_path: Path):
    library = PromptLibrary(tmp_path / "library.json")
    entry = library.create("A", "prompt a")

    assert entry.use_count == 0
    assert entry.last_used is None

    library.mark_used(entry.id)

    assert entry.use_count == 1
    assert entry.last_used is not None

    library2 = PromptLibrary(tmp_path / "library.json")
    assert ensure(library2.find(entry.id)).use_count == 1


def test_serialization_roundtrip(tmp_path: Path):
    library = PromptLibrary(tmp_path / "library.json")
    entry = library.create("A", "prompt a, (weighted:1.1)", category="cat", tags=["one", "two"])
    library.update(entry.id, favorite=True)
    library.mark_used(entry.id)

    library2 = PromptLibrary(tmp_path / "library.json")
    assert len(library2) == 1
    loaded = library2[0]
    assert loaded.id == entry.id
    assert loaded.name == entry.name
    assert loaded.positive == entry.positive
    assert loaded.category == entry.category
    assert loaded.tags == entry.tags
    assert loaded.favorite == entry.favorite
    assert loaded.use_count == entry.use_count
    assert loaded.last_used == entry.last_used
    assert loaded.created == entry.created


def test_from_dict_tolerates_missing_keys():
    entry = PromptEntry.from_dict({"id": "abc", "name": "n", "positive": "p"})
    assert entry.id == "abc"
    assert entry.name == "n"
    assert entry.positive == "p"
    assert entry.category == ""
    assert entry.tags == []
    assert entry.favorite is False
    assert entry.use_count == 0
    assert entry.last_used is None


def test_create_unique_ids(tmp_path: Path):
    library = PromptLibrary(tmp_path / "library.json")
    entry1 = library.create("A", "prompt a")
    entry2 = library.create("B", "prompt b")
    assert entry1.id != entry2.id


def test_search_filter(tmp_path: Path):
    library = PromptLibrary(tmp_path / "library.json")
    entry_a = library.create("Alpha portrait", "a detailed face", category="people", tags=["face"])
    entry_b = library.create("Beta landscape", "a scenic mountain view", category="scenery")
    library.update(entry_b.id, favorite=True)

    filtered = PromptFilter(library)
    assert filtered.rowCount() == 2

    filtered.search_text = "mountain"
    assert filtered.rowCount() == 1
    assert filtered[0].id == entry_b.id

    filtered.search_text = "face"
    assert filtered.rowCount() == 1
    assert filtered[0].id == entry_a.id

    filtered.search_text = ""
    filtered.category = "people"
    assert filtered.rowCount() == 1
    assert filtered[0].id == entry_a.id

    filtered.category = ""
    filtered.favorites_only = True
    assert filtered.rowCount() == 1
    assert filtered[0].id == entry_b.id
