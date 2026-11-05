from PyQt6.QtCore import QEvent, QMetaObject, Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QEnterEvent, QStandardItemModel
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..backend.client import pid_upscale_status
from ..backend.resources import ControlMode, UpscalerName
from ..backend.workflow import (
    pid_scale_factor,
    pid_tile_count,
    pid_tile_sizes,
    pid_upscale_extent,
)
from ..localization import translate as _
from ..model.jobs import JobKind
from ..model.model import DocumentModel, TileOverlapMode, UpscaleMethod
from ..model.properties import Bind, Binding, bind, bind_combo, bind_toggle
from ..model.root import root
from . import theme
from .settings_widgets import WarningIcon
from .switch import SwitchWidget
from .theme import SignalBlocker, set_text_clipped
from .widget import (
    ErrorBox,
    GenerateButton,
    QueueButton,
    StrengthWidget,
    StyleSelectWidget,
    WorkspaceSelectWidget,
)


class FactorWidget(QWidget):
    value_changed = pyqtSignal(float)

    def __init__(self, parent: QWidget | None):
        super().__init__(parent)
        self._value = 1.0

        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setMinimum(100)
        self.slider.setMaximum(400)
        self.slider.setTickInterval(50)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setSingleStep(50)
        self.slider.setPageStep(50)
        self.slider.valueChanged.connect(self.change_factor_slider)

        self.input = QDoubleSpinBox(self)
        self.input.setMinimum(1.0)
        self.input.setMaximum(4.0)
        self.input.setSingleStep(0.5)
        self.input.setPrefix(_("Scale") + ": ")
        self.input.setSuffix("x")
        self.input.setDecimals(2)
        self.input.valueChanged.connect(self.change_factor)
        fm = self.input.fontMetrics()
        self.input.setMinimumWidth(fm.horizontalAdvance(self.input.prefix() + "4 x") + 10)

        self.target_label = QLabel(self)
        self.target_label.setStyleSheet(f"color: {theme.grey};")

        value_layout = QHBoxLayout()
        value_layout.addWidget(self.slider)
        value_layout.addWidget(self.input)
        layout = QVBoxLayout(self)
        layout.addLayout(value_layout)
        layout.addWidget(self.target_label, alignment=Qt.AlignmentFlag.AlignRight)

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value: float):
        if value != self._value:
            self._value = value
            with SignalBlocker(self.input), SignalBlocker(self.slider):
                self.slider.setValue(int(value * 100))
                self.input.setValue(value)
            self.update_target_extent()
            self.value_changed.emit(value)

    def change_factor_slider(self, value: float):
        rounded = round(value / 50) * 50
        if rounded != value:
            self.slider.setValue(rounded)
        else:
            self.value = value / 100

    def change_factor(self, value: float):
        self.value = value

    def update_target_extent(self):
        e = root.active_model.document.extent * self.value
        if self.slider.isSliderDown() or self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.target_label.setText(_("Target size") + f": {e.width} x {e.height}")
        else:
            self.target_label.setText("")

    def enterEvent(self, event: QEnterEvent | None):
        self.update_target_extent()
        super().enterEvent(event)

    def leaveEvent(self, a0: QEvent | None):
        self.update_target_extent()
        super().leaveEvent(a0)


class UpscaleWidget(QWidget):
    _model: DocumentModel
    _model_bindings: list[QMetaObject.Connection | Binding]

    def __init__(self):
        super().__init__()
        self._model = root.active_model
        self._model_bindings = []
        root.connection.state_changed.connect(self.update_models)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 4, 0)
        self.setLayout(layout)

        self.workspace_select = WorkspaceSelectWidget(self)
        self.model_select = QComboBox(self)
        self.model_select.currentIndexChanged.connect(self._change_upscaler)
        model_layout = QHBoxLayout()
        model_layout.addWidget(self.workspace_select)
        model_layout.addWidget(self.model_select)
        layout.addLayout(model_layout)

        self.factor_widget = FactorWidget(self)
        self.factor_widget.value_changed.connect(self._update_factor)
        layout.addWidget(self.factor_widget)

        self.pid_summary = QLabel(self)
        self.pid_summary.setStyleSheet(f"color: {theme.grey};")
        self.pid_summary.setWordWrap(True)
        layout.addWidget(self.pid_summary)

        self.pid_tile_row = QWidget(self)
        self.pid_tile_combo = QComboBox(self)
        for size in pid_tile_sizes:
            self.pid_tile_combo.addItem(f"{size} px", size)
        self.pid_tile_combo.currentIndexChanged.connect(self._change_pid_tile_size)
        pid_tile_layout = QHBoxLayout(self.pid_tile_row)
        pid_tile_layout.setContentsMargins(0, 0, 0, 0)
        pid_tile_layout.addWidget(QLabel(_("PiD Tile Size"), self), 1)
        pid_tile_layout.addWidget(self.pid_tile_combo, 1)
        self.pid_tile_row.setToolTip(
            _(
                "Size of the tiles PiD renders, in output pixels. Larger tiles mean fewer passes and less seam blending, but need more VRAM."
            )
        )
        layout.addWidget(self.pid_tile_row)

        self.refinement_checkbox = QGroupBox(_("Refine upscaled image"), self)
        self.refinement_checkbox.setCheckable(True)

        self.style_select = StyleSelectWidget(self)
        self.strength_slider = StrengthWidget(range=(0.2, 0.5), prefix=False)
        strength_layout = QHBoxLayout()
        strength_layout.addWidget(QLabel(_("Strength"), self), 1)
        strength_layout.addWidget(self.strength_slider.widget(), 3)

        self.unblur_slider = StrengthWidget(range=(0.0, 1.0), prefix=False)
        self.unblur_row = QWidget(self)
        unblur_layout = QHBoxLayout(self.unblur_row)
        unblur_layout.setContentsMargins(0, 0, 0, 0)
        unblur_layout.addWidget(QLabel(_("Image guidance"), self), 1)
        unblur_layout.addWidget(self.unblur_slider.widget(), 3)
        root.connection.models_changed.connect(self._update_style)

        self.overlap_custom_combo = QComboBox(self)
        self.overlap_custom_combo.addItem(_("Automatic"), TileOverlapMode.auto)
        self.overlap_custom_combo.addItem(_("Custom"), TileOverlapMode.custom)
        self.overlap_input = QSpinBox(self)
        self.overlap_input.setMinimum(0)
        self.overlap_input.setMaximum(128)
        self.overlap_input.setSingleStep(8)
        self.overlap_input.setSuffix(" px")
        self.overlap_input.setEnabled(False)
        self.overlap_row = QWidget(self)
        overlap_layout = QHBoxLayout(self.overlap_row)
        overlap_layout.setContentsMargins(0, 0, 0, 0)
        overlap_layout.addWidget(QLabel(_("Tile Overlap"), self), 2)
        overlap_layout.addWidget(self.overlap_custom_combo)
        overlap_layout.addWidget(self.overlap_input)

        self.refine_scale_row = QWidget(self)
        self.refine_scale_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.refine_scale_slider.setRange(10, 20)
        self.refine_scale_slider.setSingleStep(1)
        self.refine_scale_slider.setPageStep(1)
        self.refine_scale_slider.valueChanged.connect(self._change_refine_scale)
        self.refine_scale_value = QLabel("1.5x", self)
        self.refine_scale_value.setMinimumWidth(30)
        refine_scale_layout = QHBoxLayout(self.refine_scale_row)
        refine_scale_layout.setContentsMargins(0, 0, 0, 0)
        refine_scale_label = QLabel(_("Resolution"), self)
        refine_scale_layout.addWidget(refine_scale_label, 1)
        refine_scale_layout.addWidget(self.refine_scale_slider, 3)
        refine_scale_layout.addWidget(self.refine_scale_value)
        self.refine_scale_row.setToolTip(
            _(
                "Resolution of the refine pass relative to the canvas, before PiD upscale. Higher values add more detail but are slower. Limited by the maximum pixel count setting."
            )
        )

        self.use_prompt_switch = SwitchWidget(self)
        self.use_prompt_switch.toggled.connect(self._update_prompt)
        self.use_prompt_value = QLabel(_("Off"), self)
        self.prompt_warning = WarningIcon(self)
        self.prompt_label = QLabel(self)
        self.prompt_label.setMinimumWidth(40)
        prompt_layout = QHBoxLayout()
        prompt_layout.addWidget(QLabel(_("Use Prompt"), self))
        prompt_layout.addWidget(self.prompt_label, 1)
        prompt_layout.addWidget(self.prompt_warning)
        prompt_layout.addWidget(self.use_prompt_value)
        prompt_layout.addWidget(self.use_prompt_switch)

        group_layout = QVBoxLayout(self.refinement_checkbox)
        group_layout.addWidget(self.style_select)
        group_layout.addLayout(strength_layout)
        group_layout.addWidget(self.refine_scale_row)
        group_layout.addWidget(self.unblur_row)
        group_layout.addWidget(self.overlap_row)
        group_layout.addLayout(prompt_layout)
        self.refinement_checkbox.setLayout(group_layout)
        self.refinement_checkbox.toggled.connect(self._update_method)
        layout.addWidget(self.refinement_checkbox)

        self.upscale_button = GenerateButton(JobKind.upscaling, self)
        self.upscale_button.operation = _("Upscale")
        self.upscale_button.clicked.connect(self.upscale)

        self.queue_button = QueueButton(supports_batch=False, parent=self)
        self.queue_button.setFixedHeight(self.upscale_button.height() - 2)

        actions_layout = QHBoxLayout()
        actions_layout.addWidget(self.upscale_button)
        actions_layout.addWidget(self.queue_button)
        layout.addLayout(actions_layout)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        layout.addWidget(self.progress_bar)

        self.error_box = ErrorBox(self)
        layout.addWidget(self.error_box)

        layout.addStretch()

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
                model.upscale.upscaler_changed.connect(self._select_upscaler),
                model.upscale.method_changed.connect(self._select_upscaler),
                model.upscale.method_changed.connect(self._update_method),
                model.upscale.refine_scale_changed.connect(self._update_refine_scale),
                model.upscale.pid_tile_size_changed.connect(self._update_pid_tile_size),
                model.upscale.use_diffusion_changed.connect(self._update_summary),
                model.upscale.factor_changed.connect(self._update_summary),
                model.upscale.target_extent_changed.connect(self._update_summary),
                bind(model.upscale, "factor", self.factor_widget, "value"),
                bind_toggle(model.upscale, "use_diffusion", self.refinement_checkbox),
                bind(model, "style", self.style_select, "value"),
                bind(model.upscale, "strength", self.strength_slider, "value"),
                bind(model.upscale, "unblur_strength", self.unblur_slider, "value"),
                bind_combo(model.upscale, "tile_overlap_mode", self.overlap_custom_combo),
                bind(model.upscale, "tile_overlap", self.overlap_input, "value"),
                bind_toggle(model.upscale, "use_prompt", self.use_prompt_switch),
                bind(model.upscale, "can_generate", self.upscale_button, "enabled", Bind.one_way),
                bind(model, "error", self.error_box, "error", Bind.one_way),
                model.upscale.tile_overlap_mode_changed.connect(self._update_overlap),
                model.upscale.use_prompt_changed.connect(self._update_prompt),
                model.regions.modified.connect(self._update_prompt),
                model.regions.added.connect(self._update_prompt),
                model.regions.removed.connect(self._update_prompt),
                model.progress_changed.connect(self.update_progress),
                model.style_changed.connect(self._update_style),
                model.style_changed.connect(self._update_pid_item),
            ]
            self.upscale_button.model = model
            self.queue_button.model = model
            self._update_prompt()
            self._update_style()
            self._update_overlap()
            self._update_pid_item()
            self._select_upscaler()
            self._update_refine_scale()
            self._update_pid_tile_size()
            self._update_method()
            self.update_progress()

    def update_models(self):
        if client := root.connection.client_if_connected:
            with SignalBlocker(self.model_select):
                self.model_select.clear()
                for file in sorted(client.models.upscalers, key=_upscaler_order):
                    if file == UpscalerName.default.value:
                        name = f"Default ({file.removesuffix('.pth')})"
                        self.model_select.addItem(name, file)
                    elif file == UpscalerName.fast_4x.value:
                        name = f"Fast ({file.removesuffix('.safetensors')})"
                        self.model_select.addItem(name, file)
                    elif file == UpscalerName.quality.value:
                        name = f"Quality ({file.removesuffix('.pth')})"
                        self.model_select.addItem(name, file)
                    elif file == UpscalerName.sharp.value:
                        name = f"Sharp ({file.removesuffix('.pth')})"
                        self.model_select.addItem(name, file)
                    elif file in [UpscalerName.fast_2x.value, UpscalerName.fast_3x.value]:
                        pass
                    else:
                        self.model_select.addItem(file, file)
                self.model_select.insertSeparator(self.model_select.count())
                self.model_select.addItem(_("PiD (latent upscale)"), _pid_item)
            self._update_pid_item()
            self._select_upscaler()
            self._update_method()

    def _select_upscaler(self, *_args):
        upscale = self.model.upscale
        data = _pid_item if upscale.method is UpscaleMethod.pid else upscale.upscaler
        selected = self.model_select.findData(data)
        with SignalBlocker(self.model_select):
            self.model_select.setCurrentIndex(max(selected, 0))

    def _change_upscaler(self, index: int):
        data = self.model_select.itemData(index)
        if data == _pid_item:
            self.model.upscale.method = UpscaleMethod.pid
        elif data:
            self.model.upscale.method = UpscaleMethod.model
            self.model.upscale.upscaler = data

    def _update_pid_item(self, *_args):
        index = self.model_select.findData(_pid_item)
        if index < 0:
            return
        client = root.connection.client_if_connected
        available, reason = pid_upscale_status(client.models if client else None, self.model.arch)
        item_model = self.model_select.model()
        if isinstance(item_model, QStandardItemModel) and (item := item_model.item(index)):
            item.setEnabled(available or self.model.upscale.method is UpscaleMethod.pid)
        tooltip = _(
            "Upscale the latent with a PiD (Pixel Diffusion) model and decode directly at high resolution. The style's model is used for the optional refine pass, its VAE family selects the PiD model."
        )
        if not available:
            tooltip = _("Unavailable") + f": {reason}"
        self.model_select.setItemData(index, tooltip, Qt.ItemDataRole.ToolTipRole)
        self._update_summary()

    def _update_method(self, *_args):
        is_pid = self.model.upscale.method is UpscaleMethod.pid
        self.unblur_row.setVisible(not is_pid)
        self.overlap_row.setVisible(not is_pid)
        self.refine_scale_row.setVisible(is_pid)
        self.pid_summary.setVisible(is_pid)
        self.pid_tile_row.setVisible(is_pid)
        if is_pid:
            self.refinement_checkbox.setTitle(_("Refine before upscale"))
            # PiD needs the style (VAE family) even if refinement is disabled
            self.style_select.setEnabled(True)
        else:
            self.refinement_checkbox.setTitle(_("Refine upscaled image"))
            self.style_select.setEnabled(self.refinement_checkbox.isChecked())
        self._update_summary()

    def _update_refine_scale(self, *_args):
        value = round(self.model.upscale.refine_scale * 10)
        if self.refine_scale_slider.value() != value:
            with SignalBlocker(self.refine_scale_slider):
                self.refine_scale_slider.setValue(value)
        self.refine_scale_value.setText(f"{value / 10:.1f}x")
        self._update_summary()

    def _change_refine_scale(self, value: int):
        self.model.upscale.refine_scale = value / 10
        self._update_refine_scale()

    def _update_pid_tile_size(self, *_args):
        index = self.pid_tile_combo.findData(self.model.upscale.pid_tile_size)
        if index < 0:  # persisted value not in list: fall back to default
            index = self.pid_tile_combo.findData(2048)
        if self.pid_tile_combo.currentIndex() != index:
            with SignalBlocker(self.pid_tile_combo):
                self.pid_tile_combo.setCurrentIndex(index)
        self._update_summary()

    def _change_pid_tile_size(self, index: int):
        if size := self.pid_tile_combo.itemData(index):
            self.model.upscale.pid_tile_size = size
        self._update_summary()

    def _update_summary(self, *_args):
        upscale = self.model.upscale
        if upscale.method is not UpscaleMethod.pid:
            return
        client = root.connection.client_if_connected
        available, reason = pid_upscale_status(client.models if client else None, self.model.arch)
        if not available:
            self.pid_summary.setText(_("PiD upscale unavailable") + f": {reason}")
            return
        try:
            canvas = self.model.document.extent
            max_pixels = client.performance_settings.max_pixel_count if client else 6
            e = pid_upscale_extent(
                canvas,
                upscale.factor,
                upscale.use_diffusion,
                upscale.refine_scale,
                self.model.arch,
                max_pixels,
            )
            pid_out = e.initial * pid_scale_factor
            tiles = pid_tile_count(e.initial, upscale.pid_tile_size)
            tiles_text = _("1 tile") if tiles == 1 else f"{tiles} " + _("tiles")
            step = _("Refine at") if upscale.use_diffusion else _("Encode")
            self.pid_summary.setText(
                f"{step} {e.initial.width}x{e.initial.height} → PiD {pid_out.width}x{pid_out.height}"
                f" ({tiles_text}) → {e.target.width}x{e.target.height}"
            )
        except Exception as e:
            self.pid_summary.setText(_("PiD upscale unavailable") + f": {e}")

    def update_progress(self):
        self.progress_bar.setValue(int(self.model.progress * 100))

    def upscale(self):
        self.model.upscale_image()

    def _update_overlap(self):
        self.overlap_input.setEnabled(
            self.model.upscale.tile_overlap_mode is TileOverlapMode.custom
        )

    def _update_style(self):
        arch = self.model.arch
        if arch.is_edit:
            tooltip = _("Not supported for edit models")
            self.strength_slider.setEnabled(False)
            self.strength_slider.setToolTip(tooltip)
            self.unblur_slider.setEnabled(False)
            self.unblur_slider.setToolTip(tooltip)
        else:
            self.strength_slider.setEnabled(True)
            self.strength_slider.setToolTip("")
            has_unblur = False
            if client := root.connection.client_if_connected:
                models = client.models.for_arch(self.model.arch)
                has_unblur = models.find_control(ControlMode.blur) is not None
            self.unblur_slider.setEnabled(has_unblur)
            if not has_unblur:
                tooltip = _("The tile/unblur control model is not installed.")
            else:
                tooltip = _(
                    "When enabled, the low resolution image is used as guidance for refining the upscaled image.\nThis produces results which are closer to the original while enhancing local details."
                )
            self.unblur_slider.setToolTip(tooltip)

    def _update_prompt(self):
        self.use_prompt_value.setText(_("On") if self.model.upscale.use_prompt else _("Off"))
        text = self.model.regions.positive
        if len(self.model.regions) > 0:
            text = f"<b>{len(self.model.regions)} " + _("Regions") + f"</b> | {text}"
        padding = 8
        if self.model.upscale.use_prompt and len(self.model.regions) == 0:
            padding += self.prompt_warning.icon_size
            self.prompt_warning.show_message(
                _(
                    "Text prompt regions have not been set up.\nIt is not recommended to use a single text description for tiled upscale,\nunless it can be generally applied to all parts of the image."
                )
            )
        else:
            self.prompt_warning.hide()
        set_text_clipped(self.prompt_label, text, padding=padding)

    def _update_factor(self):
        if self.factor_widget.value == 1 and self.model.upscale.use_diffusion:
            self.upscale_button.operation = _("Refine")
        else:
            self.upscale_button.operation = _("Upscale")


_pid_item = "__pid_latent_upscale__"


def _upscaler_order(filename: str):
    return {
        UpscalerName.default.value: 0,
        UpscalerName.fast_4x.value: 1,
        UpscalerName.quality.value: 2,
        UpscalerName.sharp.value: 3,
    }.get(filename, 99)
