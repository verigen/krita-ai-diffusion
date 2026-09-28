# Prompt Library — Inline `/` Expansion

## Context

The Prompt Library (see `docs/agent/PROMPT_LIBRARY_PLAN.md`, shipped in commits
`5e335b8` / `386b931`) lets users save positive prompts and recall them from a
dedicated workspace page via an **Apply** button that replaces the active
region's prompt. Recalling still means leaving the canvas, switching to the
Prompts workspace, finding the entry, and applying it — heavy for the common
case of "drop my usual quality boilerplate in mid-sentence."

This change adds the inline recall that the original plan explicitly deferred:
while typing in a prompt box, `/` opens a popup of saved prompts; accepting one
**expands the typed `/token` into the entry's full prompt text, inline**. It
turns the library from a place you visit into something you type.

The decisive finding from exploration: the plugin **already has a mature
autocomplete engine** — `PromptAutoComplete` (`ai_diffusion/ui/autocomplete.py`),
a `QCompleter` in popup mode wired into every `TextPromptWidget`. It already
implements trigger modes (`<lora:…>`, `<layer:…>`) plus danbooru-tag completion,
is driven on every keystroke, handles UTF-16↔Python cursor math, and delegates
popup keyboard navigation. **The `/` feature is a new mode inside this engine,
not new machinery.** This keeps behavior (popup styling, Esc-to-dismiss,
Up/Down/Enter/Tab) identical to what users already know.

Confirmed UX decisions (with the user):
- **Inline replace** — replace only the typed `/token`, leave the rest of the box.
- **Fuzzy popup, pick-to-disambiguate** — no naming rules, no data migration;
  names stay free-form and may repeat. Selection is resolved by entry **id**.
- **Rich rows** — name + truncated body preview + category/★ marker.
- **Sort favorites first, then most-used** (`favorite`, then `use_count` /
  `last_used`), reusing fields the model already tracks.

## How it works

While typing, `TextPromptWidget.notify_text_changed` → `PromptAutoComplete.check_completion()`
runs on every edit (`autocomplete.py:211`, called from `widget.py:588`). The
current token is extracted by `_current_text()` (`autocomplete.py:204`), which
walks back to the nearest separator. `/` is **not** a separator in the default
set (`" ()>,|{\n"`), so typing `/portrait` yields the token `"/portrait"`.

We add a `/` branch to `check_completion`, ahead of the tag fall-through:

1. If the token starts with `/` **and** the widget is a positive box, enter
   prompt mode. `query = token[1:]` (may be empty → browse all).
2. Point the completer at a usage-sorted proxy over `root.prompts` (see below),
   `setCompletionPrefix(query)` with the existing `MatchContains` /
   case-insensitive settings so `/port` still finds a name like
   `"cinematic portrait"`. Set the rich delegate. Show the popup via the same
   `complete(rect)` path.
3. `return` before the tag fall-through so `/` never triggers tag search.

On accept, `_insert_completion` (`autocomplete.py:250`) branches on the stored
mode. For prompt mode it:
- Resolves the **selected row** (not the typed string — names can repeat) via
  `self._completer.currentIndex()` → map through the completion/proxy model to
  the source `PromptLibrary` row → read `id` (`Qt.UserRole`) and `positive`
  (`PromptLibrary.positive_role`).
- Replaces the whole `/query` token (set `_completion_prefix = "/" + query`,
  `_completion_suffix = ""`) with `entry.positive`.
- Calls `root.prompts.mark_used(id)` (`prompt_library.py:128`) to feed the
  usage sort.

Gating to positive boxes: `PromptAutoComplete` gets the owning widget in its
ctor (`autocomplete.py:141`); `TextPromptWidget` exposes `is_negative`
(`widget.py:636`). The `/` branch is skipped when `is_negative` is true — the
library is positive-only.

`keyPressEvent` needs **no change**: `is_active` + `action_keys` already forward
Up/Down/Enter/Tab/Esc to the popup for any mode (`widget.py:561`).

## Changes

**`ai_diffusion/ui/autocomplete.py`** (the core; ~all logic lives here):
- In `PromptAutoComplete.__init__`: store `self._is_negative = getattr(widget, "is_negative", False)`
  (or read it lazily in `check_completion`), add `self._mode` state, and build a
  `self._prompt_model` — a small `QSortFilterProxyModel` over `root.prompts`
  whose `lessThan` orders by `(favorite desc, use_count desc, last_used desc,
  name)` using the model's custom roles. (Alternatively extend `PromptFilter`
  in `prompt_library.py` with a `sort_by_usage` flag and reuse it — a proxy is
  fine since no text filtering is needed; `QCompleter` does the contains match.)
- Add the `/` branch in `check_completion()` per "How it works", set
  `self._mode = "prompt"` (and reset to a default mode on the other branches).
- Add the prompt-mode branch in `_insert_completion()` that resolves the
  selected entry by id, inserts `entry.positive`, and calls `mark_used`.
  Reuse the existing UTF-16 index math (`cursor_position`,
  `char16_index_to_str_index`) exactly as the other branches do.
- Add a `PromptCompleterDelegate(QStyledItemDelegate)` modeled on the existing
  `TagCompleterDelegate` (`autocomplete.py:47`): bold name, grey truncated
  one-line body preview (flatten newlines, as `ui/prompt_library.py:91` does),
  right-aligned category text and/or ★ for favorites. Set it on the popup in
  the `/` branch; restore the default/tag delegate in the other branches
  (the code already swaps `setItemDelegate` per mode).

**`ai_diffusion/text.py`** (optional, cosmetic): add a `pattern_prompt_ref`
(e.g. `/[^\s]+` at a token boundary) near the other patterns (`text.py:48-52`).

**`ai_diffusion/ui/widget.py`** (optional, cosmetic): add a branch in
`PromptHighlighter.highlightBlock` (`widget.py:444`) to tint `/token` before
expansion, matching how `<lora:…>` is highlighted, so users see it is a live
trigger. `/` is currently not a token char anywhere, so this is additive.

**`ai_diffusion/settings.py`** (optional): a `prompt_expansion` bool
(default `True`) following the existing settings pattern, letting users disable
`/` if it ever collides with their prompt style. `check_completion` short-circuits
the `/` branch when off.

No changes to the data model, `root.py`, the Prompts workspace page, or the
save flow — all already in place.

## UX notes / improvements folded in

- **Browse with bare `/`**: empty query lists all prompts (favorites/most-used
  first) — discoverability without remembering a name.
- **Disambiguation by id**, never by typed text, so duplicate names are safe.
- **Rich rows + usage sort** so the right prompt is usually the top hit.
- **Positive-only** gating keeps the negative box behaving as before.
- **Undo**: the existing insert path uses `setPlainText`, which resets the undo
  stack. Because an expanded body can be large, prefer inserting via a
  `QTextCursor` (`beginEditBlock`/`insertText`/`endEditBlock`) in the prompt
  branch so a single Ctrl+Z removes the expansion and restores `/query`. This is
  a deliberate, localized improvement over the existing branches (flag it in
  review); if consistency is preferred, fall back to the `setPlainText` path the
  other modes use.
- **No collision in practice**: `/` triggers only at a token start (after
  whitespace/newline/start), so fractions like `16/9` or inline URLs do not
  fire it.

## Verification

**Unit tests** (`tests/test_prompt_library.py`, extend the existing file;
headless via `conftest.py`, no Qt event loop needed):
- `test_usage_sort_order` — the completer proxy orders favorites first, then by
  `use_count`/`last_used`, then name, over a seeded `PromptLibrary`.
- `test_duplicate_name_resolves_by_id` — two entries share a name; given a
  selected source row, the resolver returns the correct `id`/`positive`.
- (Logic that needs no live popup — token parsing for `/query`, the
  `_completion_prefix = "/" + query` replacement math — can be unit-tested by
  factoring it into a small pure helper and asserting the resulting string.)

Run: `pytest tests/test_prompt_library.py` (config in `tests/pytest.ini`).

**Manual E2E** (in Krita, positive prompt box of the active region):
1. Type `/` → popup lists saved prompts, favorites/most-used at top, each row
   showing name + preview + category/★.
2. Type `/por` → list narrows to names containing "por" (incl. multi-word names
   like "cinematic portrait"); Up/Down + Enter (or click) accepts.
3. Accept → `/por` is replaced **in place** by the full saved body; surrounding
   text is untouched; cursor lands after the inserted text.
4. Ctrl+Z once → expansion is undone back to `/por` (if the cursor-insert
   improvement is taken).
5. Create two entries with the same name → each is independently selectable and
   expands to its own body (id-resolved).
6. Reopen the Prompts page / inspect `library.json` → `use_count`/`last_used`
   bumped for the expanded entry.
7. Type `/` in the **negative** prompt box → no prompt popup (lora/tag
   completion still works there as before).
8. Confirm `<lora:…>`, `<layer:…>`, and danbooru-tag completion are unchanged.
9. Esc dismisses the popup; typing a space after `/word` dismisses prompt mode.
