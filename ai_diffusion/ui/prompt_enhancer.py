from __future__ import annotations

from krita import Krita
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import eventloop
from ..backend.llm_client import LlmBackend, LlmModel, OllamaClient, OpenAiClient, create_llm_client
from ..backend.network import NetworkError
from ..localization import translate as _
from ..model.prompt_enhancer import EnhancerState
from ..model.region import Region, RootRegion
from ..model.root import root
from ..prompt_enhancer import EnhancerPreset
from ..settings import Setting, Settings, settings
from . import theme
from .settings_widgets import (
    ComboBoxSetting,
    SettingsTab,
    SettingsWriteGuard,
    SliderSetting,
    SpinBoxSetting,
    SwitchSetting,
    TextAreaSetting,
    TextSetting,
)
from .theme import SignalBlocker, add_header, green, red, yellow


class PromptEnhanceDialog(QDialog):
    def __init__(self, parent: QWidget | None, region: RootRegion | Region, text: str):
        super().__init__(parent)
        self.setWindowTitle(_("Enhance prompt"))
        self._region = region
        self._original_text = text
        self._enhancer = root.enhancer
        self._presets = root.enhancer_presets

        self._preset_combo = QComboBox(self)
        self._preset_combo.setModel(self._presets)
        default_index = max(0, self._presets.find_index(self._presets.default))
        self._preset_combo.setCurrentIndex(default_index)

        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel(_("Preset") + ":", self))
        preset_layout.addWidget(self._preset_combo, 1)

        self._original_edit = QPlainTextEdit(self)
        self._original_edit.setPlainText(text)
        self._original_edit.setReadOnly(True)

        self._enhanced_edit = QPlainTextEdit(self)

        self._status_label = QLabel(self)

        self._regenerate_button = QPushButton(_("Regenerate"), self)
        self._regenerate_button.clicked.connect(self._start)
        self._cancel_button = QPushButton(_("Cancel"), self)
        self._cancel_button.clicked.connect(self.reject)
        self._apply_button = QPushButton(_("Apply"), self)
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self._status_label, 1)
        button_layout.addWidget(self._regenerate_button)
        button_layout.addWidget(self._cancel_button)
        button_layout.addWidget(self._apply_button)

        layout = QVBoxLayout(self)
        layout.addLayout(preset_layout)
        layout.addWidget(QLabel(_("Original"), self))
        layout.addWidget(self._original_edit, 1)
        layout.addWidget(QLabel(_("Enhanced"), self))
        layout.addWidget(self._enhanced_edit, 1)
        layout.addLayout(button_layout)

        self.setMinimumSize(480, 420)
        self.resize(1280, 1120)

        self._enhancer.result_ready.connect(self._on_result)
        self._enhancer.error_occurred.connect(self._on_error)
        self._enhancer.state_changed.connect(self._on_state)

        self._start()

    def _current_preset(self) -> EnhancerPreset:
        id = self._preset_combo.currentData()
        return self._presets.find(id) or EnhancerPreset(id="", name="")

    def _start(self):
        self._apply_button.setEnabled(False)
        self._enhanced_edit.setEnabled(False)
        self._status_label.setStyleSheet("")
        self._status_label.setText(_("Enhancing…"))
        self._enhanced_edit.setPlainText("")
        self._enhancer.enhance(self._original_text, self._current_preset())

    def _on_result(self, text: str):
        self._enhanced_edit.setEnabled(True)
        self._enhanced_edit.setPlainText(text)
        self._status_label.setText("")
        self._apply_button.setEnabled(True)

    def _on_error(self, message: str):
        self._enhanced_edit.setEnabled(True)
        self._status_label.setText(message)
        self._status_label.setStyleSheet(f"color: {theme.red};")
        self._apply_button.setEnabled(False)

    def _on_state(self, state: EnhancerState):
        busy = state is EnhancerState.running
        self._regenerate_button.setEnabled(not busy)
        self._preset_combo.setEnabled(not busy)

    def _apply(self):
        self._region.positive = self._enhanced_edit.toPlainText()
        self.accept()

    def reject(self):
        self._enhancer.cancel()
        super().reject()

    def done(self, a0):
        self._enhancer.result_ready.disconnect(self._on_result)
        self._enhancer.error_occurred.disconnect(self._on_error)
        self._enhancer.state_changed.disconnect(self._on_state)
        super().done(a0)


class EnhancerPresetSettings:
    name = Setting(_("Name"), "")
    system_prompt = Setting(
        _("System Prompt"), "", _("Instructions sent to the language model along with the prompt")
    )
    model = Setting(
        _("Model Override"), "", _("Use a specific model for this preset instead of the default")
    )
    temperature = Setting(
        _("Temperature"), 0.7, _("Controls the randomness of the rewritten prompt")
    )


class EnhancerPresetEditor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._presets = root.enhancer_presets
        self._write_guard = SettingsWriteGuard()

        self._combo = QComboBox(self)
        self._combo.setModel(self._presets)
        self._combo.currentIndexChanged.connect(self._change_preset)

        self._new_button = QToolButton(self)
        self._new_button.setIcon(Krita.instance().icon("list-add"))
        self._new_button.setToolTip(_("Create a new preset"))
        self._new_button.clicked.connect(self._create_preset)

        self._duplicate_button = QToolButton(self)
        self._duplicate_button.setIcon(Krita.instance().icon("duplicate"))
        self._duplicate_button.setToolTip(_("Duplicate the current preset"))
        self._duplicate_button.clicked.connect(self._duplicate_preset)

        self._delete_button = QToolButton(self)
        self._delete_button.setIcon(Krita.instance().icon("deletelayer"))
        self._delete_button.setToolTip(_("Delete the current preset"))
        self._delete_button.clicked.connect(self._delete_preset)

        self._default_button = QToolButton(self)
        self._default_button.setCheckable(True)
        self._default_button.setIcon(_favorite_icon())
        self._default_button.setToolTip(_("Use this preset by default"))
        self._default_button.toggled.connect(self._toggle_default)

        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.addWidget(self._combo, 1)
        control_layout.addWidget(self._new_button)
        control_layout.addWidget(self._duplicate_button)
        control_layout.addWidget(self._delete_button)
        control_layout.addWidget(self._default_button)

        self._name = TextSetting(EnhancerPresetSettings.name, self)
        self._name.value_changed.connect(self._write_name)

        self._system_prompt = TextAreaSetting(
            EnhancerPresetSettings.system_prompt, self, line_count=8
        )
        self._system_prompt.value_changed.connect(self._schedule_write_system_prompt)

        self._model = ComboBoxSetting(EnhancerPresetSettings.model, parent=self, editable=True)
        self._model.value_changed.connect(self._write_model)

        self._temperature = SliderSetting(
            EnhancerPresetSettings.temperature, self, 0.0, 2.0, "{:.1f}"
        )
        self._temperature_check = self._temperature.add_checkbox(_("Override"))
        self._temperature_check.toggled.connect(self._write_temperature)
        self._temperature.value_changed.connect(self._write_temperature)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        add_header(layout, Setting(_("Presets"), "", _("Manage prompt enhancer presets")))
        layout.addLayout(control_layout)
        layout.addWidget(self._name)
        layout.addWidget(self._system_prompt)
        layout.addWidget(self._model)
        layout.addWidget(self._temperature)
        self.setLayout(layout)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._commit_system_prompt)

        self._presets.dataChanged.connect(self._refresh_default_button)
        self._presets.rowsInserted.connect(self._refresh_default_button)
        self._presets.rowsRemoved.connect(self._refresh_default_button)

        self.read()

    def set_available_models(self, models: list[LlmModel]):
        current = self._model.value
        items = [(_("(default)"), "")] + [(m.name, m.id) for m in models]
        self._model.set_items(items)
        self._model.value = current

    def _current_preset(self) -> EnhancerPreset | None:
        id = self._combo.currentData()
        return self._presets.find(id)

    def _select(self, id: str):
        index = self._presets.find_index(id)
        if index >= 0:
            self._combo.setCurrentIndex(index)

    def read(self):
        self._select(self._presets.default)
        self._read_preset(self._current_preset())

    def _change_preset(self, index: int):
        self._read_preset(self._current_preset())

    def _read_preset(self, preset: EnhancerPreset | None):
        with self._write_guard:
            self._name.value = preset.name if preset else ""
            self._system_prompt.value = preset.system_prompt if preset else ""
            self._model.value = preset.model if preset else ""
            temperature = preset.temperature if preset is not None else None
            self._temperature.value = temperature if temperature is not None else 0.7
            self._temperature_check.setChecked(temperature is not None)
            self._temperature.enabled = temperature is not None
        self._refresh_default_button()

    def _create_preset(self):
        preset = self._presets.create(_("New preset"))
        self._select(preset.id)

    def _duplicate_preset(self):
        preset = self._current_preset()
        if preset is not None:
            new_preset = self._presets.duplicate(preset.id)
            if new_preset is not None:
                self._select(new_preset.id)

    def _delete_preset(self):
        preset = self._current_preset()
        if preset is None or len(self._presets) <= 1:
            return
        q = QMessageBox.question(
            self,
            _("Delete preset"),
            _("Are you sure you want to delete '{name}'?").format(name=preset.name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.StandardButton.No,
        )
        if q == QMessageBox.StandardButton.Yes:
            self._presets.remove(preset.id)

    def _toggle_default(self, checked: bool):
        preset = self._current_preset()
        if checked and preset is not None:
            self._presets.set_default(preset.id)

    def _refresh_default_button(self, *args):
        preset = self._current_preset()
        with SignalBlocker(self._default_button):
            self._default_button.setChecked(
                preset is not None and preset.id == self._presets.default
            )
        self._delete_button.setEnabled(len(self._presets) > 1)

    def _write_name(self):
        if self._write_guard:
            return
        preset = self._current_preset()
        if preset is not None:
            self._presets.update(preset.id, name=self._name.value)

    def _schedule_write_system_prompt(self):
        if not self._write_guard:
            self._save_timer.start()

    def _commit_system_prompt(self):
        preset = self._current_preset()
        if preset is not None:
            self._presets.update(preset.id, system_prompt=self._system_prompt.value)

    def _write_model(self):
        if self._write_guard:
            return
        preset = self._current_preset()
        if preset is not None:
            self._presets.update(preset.id, model=self._model.value)

    def _write_temperature(self):
        if self._write_guard:
            return
        preset = self._current_preset()
        if preset is not None:
            value = self._temperature.value if self._temperature_check.isChecked() else None
            self._presets.update(preset.id, temperature=value)


def _favorite_icon():
    return QIcon(str(theme.icon_path / "star.png"))


class PromptEnhancerSettings(SettingsTab):
    def __init__(self):
        super().__init__(_("Prompt Enhancer"))

        S = Settings
        self.add("enhancer_enabled", SwitchSetting(S._enhancer_enabled, parent=self))
        self.add("enhancer_backend", ComboBoxSetting(S._enhancer_backend, parent=self))
        self._widgets["enhancer_backend"].value_changed.connect(self._update_backend_widgets)

        add_header(self._layout, S._enhancer_url)
        url_layout = QHBoxLayout()
        self._url_edit = QLineEdit(self)
        self._url_edit.textChanged.connect(self.write)
        url_layout.addWidget(self._url_edit, 1)
        self._test_button = QPushButton(_("Test connection"), self)
        self._test_button.clicked.connect(self._test_connection)
        url_layout.addWidget(self._test_button)
        self._layout.addLayout(url_layout)

        self._connection_status = QLabel(self)
        self._connection_status.setWordWrap(True)
        self._layout.addWidget(self._connection_status)

        add_header(self._layout, S._enhancer_api_key)
        key_layout = QHBoxLayout()
        self._key_edit = QLineEdit(self)
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.textChanged.connect(self.write)
        key_layout.addWidget(self._key_edit, 1)
        self._key_reveal = QToolButton(self)
        self._key_reveal.setCheckable(True)
        self._key_reveal.setText("👁")
        self._key_reveal.setToolTip(_("Show API key"))
        self._key_reveal.toggled.connect(self._toggle_key_reveal)
        key_layout.addWidget(self._key_reveal)
        self._layout.addLayout(key_layout)

        add_header(self._layout, S._enhancer_model)
        model_layout = QHBoxLayout()
        self._model_combo = QComboBox(self)
        self._model_combo.setEditable(True)
        self._model_combo.editTextChanged.connect(self.write)
        model_layout.addWidget(self._model_combo, 1)
        self._refresh_button = QPushButton(_("Refresh"), self)
        self._refresh_button.clicked.connect(self._refresh_models)
        model_layout.addWidget(self._refresh_button)
        self._layout.addLayout(model_layout)

        self.add(
            "enhancer_ollama_keep_alive", TextSetting(S._enhancer_ollama_keep_alive, parent=self)
        )
        self.add("enhancer_timeout", SpinBoxSetting(S._enhancer_timeout, self, 5, 600, suffix=" s"))

        self.add(
            "enhancer_free_comfy_vram", SwitchSetting(S._enhancer_free_comfy_vram, parent=self)
        )
        self._widgets["enhancer_free_comfy_vram"].value_changed.connect(
            self._update_free_vram_widgets
        )
        self.add(
            "enhancer_free_comfy_vram_timeout",
            SpinBoxSetting(S._enhancer_free_comfy_vram_timeout, self, 1, 120, suffix=" s"),
        )

        self._layout.addSpacing(12)
        self._preset_editor = EnhancerPresetEditor(self)
        self._layout.addWidget(self._preset_editor)
        self._layout.addStretch()

        self._pending_task = None
        self._update_backend_widgets()
        self._update_free_vram_widgets()

    def _update_backend_widgets(self, *args):
        backend: LlmBackend = self._widgets["enhancer_backend"].value
        is_ollama = backend is LlmBackend.ollama
        default_url = OllamaClient.default_url if is_ollama else OpenAiClient.default_url
        self._url_edit.setPlaceholderText(default_url)
        self._key_edit.setToolTip(
            _("Usually not required for Ollama")
            if is_ollama
            else _("Required for most OpenAI-compatible endpoints")
        )
        self._widgets["enhancer_ollama_keep_alive"].visible = is_ollama

    def _update_free_vram_widgets(self, *args):
        enabled = self._widgets["enhancer_free_comfy_vram"].value
        self._widgets["enhancer_free_comfy_vram_timeout"].visible = enabled

    def _build_client_from_widgets(self):
        backend = self._widgets["enhancer_backend"].value
        url = self._url_edit.text()
        key = self._key_edit.text()
        return create_llm_client(backend, url, key)

    def _test_connection(self):
        self._run_connection_check()

    def _refresh_models(self):
        self._run_connection_check()

    def _run_connection_check(self):
        if self._pending_task is not None and not self._pending_task.done():
            return
        self._test_button.setEnabled(False)
        self._refresh_button.setEnabled(False)
        self._connection_status.setText(_("Connecting…"))
        self._connection_status.setStyleSheet(f"color: {yellow}; font-weight:bold")
        self._pending_task = eventloop.run(self._check_connection())

    async def _check_connection(self):
        try:
            client = self._build_client_from_widgets()
            models = await client.check_connection()
            self._connection_status.setText(
                _("Connected") + f" — {len(models)} " + _("models available")
            )
            self._connection_status.setStyleSheet(f"color: {green}; font-weight:bold")
            self._populate_models(models)
        except NetworkError as e:
            self._connection_status.setText(_("Error") + f": {e.message}")
            self._connection_status.setStyleSheet(f"color: {red};")
        except Exception as e:
            self._connection_status.setText(_("Error") + f": {e}")
            self._connection_status.setStyleSheet(f"color: {red};")
        finally:
            self._test_button.setEnabled(True)
            self._refresh_button.setEnabled(True)

    def _populate_models(self, models: list[LlmModel]):
        current = self._model_combo.currentText()
        with SignalBlocker(self._model_combo):
            self._model_combo.clear()
            for m in models:
                self._model_combo.addItem(m.name, m.id)
            self._model_combo.setCurrentText(current)
        self._preset_editor.set_available_models(models)

    def _toggle_key_reveal(self, checked: bool):
        self._key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _read(self):
        self._url_edit.setText(settings.enhancer_url)
        self._key_edit.setText(settings.enhancer_api_key)
        with SignalBlocker(self._model_combo):
            if (
                settings.enhancer_model
                and self._model_combo.findText(settings.enhancer_model) == -1
            ):
                self._model_combo.addItem(settings.enhancer_model)
            self._model_combo.setCurrentText(settings.enhancer_model)
        self._update_backend_widgets()
        self._preset_editor.read()

    def _write(self):
        settings.enhancer_url = self._url_edit.text()
        settings.enhancer_api_key = self._key_edit.text()
        settings.enhancer_model = self._model_combo.currentText()
