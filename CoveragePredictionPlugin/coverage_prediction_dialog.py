"""Main dialog for the Coverage Prediction plugin.

The layout mirrors the screenshot of propagationpredict.onrender.com:

    +-----------------------------------------------------------+
    | RF Parameters         | Terrain Analysis        [Reset]   |
    | (Basic RF / Tilt Opt) | (matplotlib elevation profile)    |
    |                       +-----------------------------------+
    |                       | Coverage Map        [Export KMZ]  |
    |                       | (QgsMapCanvas)                    |
    +-----------------------------------------------------------+
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Matplotlib is bundled with QGIS, so we can rely on it.
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsRectangle
from qgis.gui import QgsMapCanvas


COVERAGE_LEGEND = (
    ("\u25cf", "#ff0000", "-140 to -110 dBm"),
    ("\u25cf", "#ffff00", "-110 to -105 dBm"),
    ("\u25cf", "#00ff00", "-105 to -100 dBm"),
    ("\u25cf", "#00b050", "-100 to -95 dBm"),
    ("\u25cf", "#0000ff", "-95 to -40 dBm"),
    ("\u25cf", "#000000", "Antenna"),
    ("\u25cf", "#00b050", "Impact (Main Beam)"),
    ("\u25cf", "#7fff7f", "Beam End"),
    ("\u25cf", "#0066ff", "Upper Intersection"),
    ("\u25cf", "#ff3030", "Lower Intersection"),
    ("\u25a0", "#3399ff", "Coverage Footprint"),
    ("\u25a0", "#ffff66", "Sector"),
)


class CoveragePredictionDialog(QDialog):
    """Reusable, signal-driven dialog for the Coverage Prediction plugin."""

    run_requested = pyqtSignal()
    reset_requested = pyqtSignal()
    export_kmz_requested = pyqtSignal(str)
    optimise_tilt_requested = pyqtSignal()
    apply_tilt_candidate_requested = pyqtSignal(float, float)
    reset_view_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Coverage Prediction")
        self.setMinimumSize(1280, 800)

        self._icon_dir = os.path.join(os.path.dirname(__file__), "resources", "icons")

        self._build_ui()
        self._connect_internal_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 1100])
        root.addWidget(splitter, stretch=1)

        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.status_bar.showMessage("Ready")
        root.addWidget(self.status_bar)

    # -- left panel ----------------------------------------------------

    def _build_left_panel(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title = QLabel("RF Parameters")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #1f6f8b;")
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_basic_tab(), "Basic RF")
        self.tabs.addTab(self._build_tilt_optimizer_tab(), "Tilt Optimizer")
        layout.addWidget(self.tabs, stretch=1)

        button_row = QHBoxLayout()
        self.reset_button = QPushButton("Reset")
        self.run_button = QPushButton("Run Analysis")
        self.run_button.setStyleSheet(
            "QPushButton { background-color: #1f6f8b; color: white; padding: 8px 18px; font-weight: 600; }"
            "QPushButton:disabled { background-color: #aac8d3; color: #f0f0f0; }"
        )
        button_row.addWidget(self.reset_button)
        button_row.addWidget(self.run_button)
        layout.addLayout(button_row)
        return wrapper

    def _build_basic_tab(self) -> QWidget:
        widget = QWidget()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(10)

        # ---- Site location -----------------------------------------
        site_box = QGroupBox("Site Location")
        site_form = QFormLayout(site_box)
        site_form.setLabelAlignment(Qt.AlignLeft)

        self.latitude_spin = self._make_double_spin(-90.0, 90.0, 5, -6.88908, suffix="")
        self.longitude_spin = self._make_double_spin(-180.0, 180.0, 5, 107.61848, suffix="")
        self.azimuth_spin = self._make_double_spin(0.0, 359.9, 1, 90.0, suffix="\u00b0")

        site_form.addRow("Latitude:", self.latitude_spin)
        site_form.addRow("Longitude:", self.longitude_spin)
        site_form.addRow("Azimuth:", self.azimuth_spin)
        outer.addWidget(site_box)

        # ---- RF Parameters -----------------------------------------
        rf_box = QGroupBox("RF Parameters")
        rf_form = QFormLayout(rf_box)
        rf_form.setLabelAlignment(Qt.AlignLeft)

        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["Metric (m, km)", "Imperial (ft, mi)"])
        rf_form.addRow("Unit System:", self.unit_combo)

        self.antenna_height_spin = self._make_double_spin(1.0, 500.0, 2, 40.0, suffix=" m")
        self.mechanical_tilt_spin = self._make_double_spin(-10.0, 30.0, 2, 4.0, suffix="\u00b0")
        self.electrical_tilt_spin = self._make_double_spin(-10.0, 30.0, 2, 2.0, suffix="\u00b0")
        self.total_tilt_label = QLabel("6.0\u00b0")
        self.total_tilt_label.setStyleSheet(
            "background-color: #d6ecf3; color: #1f6f8b; font-weight: 700; padding: 4px 10px;"
            "border-radius: 3px; min-width: 80px;"
        )
        self.total_tilt_label.setAlignment(Qt.AlignCenter)
        self.vertical_beamwidth_spin = self._make_double_spin(0.5, 90.0, 1, 6.0, suffix="\u00b0")
        self.horizontal_beamwidth_spin = self._make_double_spin(1.0, 360.0, 2, 65.0, suffix="\u00b0")
        self.frequency_spin = self._make_double_spin(50.0, 100_000.0, 1, 2_100.0, suffix=" MHz")
        self.tx_power_spin = self._make_double_spin(0.0, 80.0, 1, 46.0, suffix=" dBm")
        self.antenna_gain_spin = self._make_double_spin(0.0, 40.0, 1, 17.0, suffix=" dBi")
        self.cable_loss_spin = self._make_double_spin(0.0, 20.0, 1, 2.0, suffix=" dB")
        self.receiver_height_spin = self._make_double_spin(0.0, 50.0, 1, 1.5, suffix=" m")

        rf_form.addRow("Antenna Height:", self.antenna_height_spin)
        rf_form.addRow("Mechanical Tilt:", self.mechanical_tilt_spin)
        rf_form.addRow("Electrical Tilt:", self.electrical_tilt_spin)
        rf_form.addRow("Total Tilt:", self.total_tilt_label)
        rf_form.addRow("Vertical Beamwidth:", self.vertical_beamwidth_spin)
        rf_form.addRow("Horizontal Beamwidth:", self.horizontal_beamwidth_spin)
        rf_form.addRow("Frequency:", self.frequency_spin)
        rf_form.addRow("Tx Power:", self.tx_power_spin)
        rf_form.addRow("Antenna Gain:", self.antenna_gain_spin)
        rf_form.addRow("Cable / Feeder Loss:", self.cable_loss_spin)
        rf_form.addRow("Receiver Height:", self.receiver_height_spin)
        outer.addWidget(rf_box)

        # ---- Analysis range ----------------------------------------
        range_box = QGroupBox("Analysis Range")
        range_layout = QVBoxLayout(range_box)
        range_form = QFormLayout()
        self.max_distance_spin = self._make_double_spin(50.0, 50_000.0, 2, 5_000.0, suffix=" m")
        range_form.addRow("Max Distance:", self.max_distance_spin)
        range_layout.addLayout(range_form)

        slider_row = QHBoxLayout()
        self.distance_slider = QSlider(Qt.Horizontal)
        self.distance_slider.setRange(50, 50_000)
        self.distance_slider.setValue(5_000)
        self.distance_slider_label = QLabel("5000 m")
        self.distance_slider_label.setMinimumWidth(70)
        slider_row.addWidget(self.distance_slider, stretch=1)
        slider_row.addWidget(self.distance_slider_label)
        range_layout.addLayout(slider_row)

        sample_form = QFormLayout()
        self.sample_count_spin = QSpinBox()
        self.sample_count_spin.setRange(16, 2_048)
        self.sample_count_spin.setValue(256)
        self.sample_count_spin.setSuffix(" samples")
        sample_form.addRow("Profile resolution:", self.sample_count_spin)
        range_layout.addLayout(sample_form)
        outer.addWidget(range_box)

        # ---- Data source -------------------------------------------
        data_box = QGroupBox("Data Source")
        data_form = QFormLayout(data_box)
        self.dem_combo = QComboBox()
        self.dem_combo.addItems(["Open-Meteo (Online)", "Flat Terrain (Offline)"])
        data_form.addRow("DEM Source:", self.dem_combo)

        self.dem_status_label = QLabel("\u25cf Online: Ready")
        self.dem_status_label.setStyleSheet("color: #2c8c2c; font-weight: 600;")
        data_form.addRow("", self.dem_status_label)

        basemap_row = QHBoxLayout()
        self.basemap_combo = QComboBox()
        self.basemap_combo.addItems(
            [
                "None (Default OSM Maps)",
                "OpenStreetMap (XYZ)",
                "Esri World Imagery (XYZ)",
            ]
        )
        self.basemap_refresh_button = QPushButton()
        self.basemap_refresh_button.setIcon(self._icon("info"))
        self.basemap_refresh_button.setToolTip("Refresh basemap connection")
        self.basemap_refresh_button.setFixedWidth(34)
        basemap_row.addWidget(self.basemap_combo, stretch=1)
        basemap_row.addWidget(self.basemap_refresh_button)
        data_form.addRow("Basemap:", basemap_row)
        outer.addWidget(data_box)

        outer.addStretch(1)
        return widget

    def _build_tilt_optimizer_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        intro = QLabel(
            "Sweep mechanical and electrical tilt to find the combination that maximises predicted "
            "coverage above a chosen RSRP threshold for the current site location, antenna height, "
            "beamwidth, and analysis range."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        bounds_box = QGroupBox("Search Range")
        bounds_form = QFormLayout(bounds_box)
        self.mech_min_spin = self._make_double_spin(-10.0, 30.0, 1, 0.0, suffix="\u00b0")
        self.mech_max_spin = self._make_double_spin(-10.0, 30.0, 1, 10.0, suffix="\u00b0")
        self.elec_min_spin = self._make_double_spin(-10.0, 30.0, 1, 0.0, suffix="\u00b0")
        self.elec_max_spin = self._make_double_spin(-10.0, 30.0, 1, 10.0, suffix="\u00b0")
        self.tilt_step_spin = self._make_double_spin(0.1, 5.0, 1, 1.0, suffix="\u00b0")
        self.threshold_spin = self._make_double_spin(-160.0, -40.0, 1, -110.0, suffix=" dBm")

        bounds_form.addRow("Mechanical min:", self.mech_min_spin)
        bounds_form.addRow("Mechanical max:", self.mech_max_spin)
        bounds_form.addRow("Electrical min:", self.elec_min_spin)
        bounds_form.addRow("Electrical max:", self.elec_max_spin)
        bounds_form.addRow("Step:", self.tilt_step_spin)
        bounds_form.addRow("RSRP threshold:", self.threshold_spin)
        layout.addWidget(bounds_box)

        actions_row = QHBoxLayout()
        self.optimise_button = QPushButton("Find optimal tilt")
        self.apply_tilt_button = QPushButton("Apply selected tilt")
        self.apply_tilt_button.setEnabled(False)
        actions_row.addWidget(self.optimise_button)
        actions_row.addWidget(self.apply_tilt_button)
        layout.addLayout(actions_row)

        self.tilt_table = QTableWidget(0, 4)
        self.tilt_table.setHorizontalHeaderLabels(
            ["Mechanical (\u00b0)", "Electrical (\u00b0)", "Coverage (%)", "Main impact (m)"]
        )
        self.tilt_table.horizontalHeader().setStretchLastSection(True)
        self.tilt_table.verticalHeader().setVisible(False)
        self.tilt_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tilt_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.tilt_table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.tilt_table, stretch=1)
        return widget

    # -- right panel ---------------------------------------------------

    def _build_right_panel(self) -> QWidget:
        wrapper = QSplitter(Qt.Vertical)
        wrapper.setChildrenCollapsible(False)
        wrapper.addWidget(self._build_terrain_panel())
        wrapper.addWidget(self._build_coverage_panel())
        wrapper.setStretchFactor(0, 1)
        wrapper.setStretchFactor(1, 1)
        wrapper.setSizes([400, 500])
        return wrapper

    def _build_terrain_panel(self) -> QWidget:
        widget = QFrame()
        widget.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Terrain Analysis")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #1f6f8b;")
        header.addWidget(title)
        header.addStretch(1)
        self.reset_view_button = QPushButton("Reset View")
        self.reset_view_button.setIcon(self._icon("info"))
        header.addWidget(self.reset_view_button)
        layout.addLayout(header)

        self.figure = Figure(figsize=(5, 3.2), tight_layout=True)
        self.figure_canvas = FigureCanvas(self.figure)
        self.figure_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.figure_canvas, stretch=1)

        self.axes = self.figure.add_subplot(111)
        self._reset_terrain_chart()

        return widget

    def _build_coverage_panel(self) -> QWidget:
        widget = QFrame()
        widget.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Coverage Map")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #1f6f8b;")
        header.addWidget(title)
        header.addStretch(1)

        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setFixedWidth(34)
        self.zoom_out_button = QPushButton("-")
        self.zoom_out_button.setFixedWidth(34)
        self.refresh_canvas_button = QPushButton()
        self.refresh_canvas_button.setIcon(self._icon("info"))
        self.refresh_canvas_button.setToolTip("Refresh coverage map")
        self.refresh_canvas_button.setFixedWidth(34)
        self.export_kmz_button = QPushButton("Export to KMZ")
        self.export_kmz_button.setIcon(self._icon("info"))
        self.export_kmz_button.setEnabled(False)

        header.addWidget(self.zoom_in_button)
        header.addWidget(self.zoom_out_button)
        header.addWidget(self.refresh_canvas_button)
        header.addWidget(self.export_kmz_button)
        layout.addLayout(header)

        canvas_row = QHBoxLayout()
        self.map_canvas = QgsMapCanvas()
        self.map_canvas.setCanvasColor(Qt.white)
        self.map_canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        self.map_canvas.setExtent(QgsRectangle(95.0, -11.0, 141.0, 6.0))
        self.map_canvas.refresh()
        canvas_row.addWidget(self.map_canvas, stretch=1)
        canvas_row.addWidget(self._build_legend_panel())
        layout.addLayout(canvas_row, stretch=1)

        intersection_row = QHBoxLayout()
        self.main_intersection_label = QLabel("Main Beam Intersection: \u2014")
        self.coverage_footprint_label = QLabel("Coverage Footprint: \u2014")
        intersection_row.addWidget(self.main_intersection_label)
        intersection_row.addWidget(self.coverage_footprint_label)
        intersection_row.addStretch(1)
        layout.addLayout(intersection_row)

        return widget

    def _build_legend_panel(self) -> QWidget:
        legend = QFrame()
        legend.setFrameShape(QFrame.StyledPanel)
        legend.setStyleSheet("QFrame { background-color: #ffffff; border: 1px solid #cfd8dc; }")
        layout = QVBoxLayout(legend)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)

        title = QLabel("Coverage Prediction")
        title.setStyleSheet("font-weight: 700; color: #1f6f8b;")
        layout.addWidget(title)

        for marker, color, label in COVERAGE_LEGEND:
            row = QHBoxLayout()
            row.setSpacing(6)
            swatch = QLabel(marker)
            swatch.setStyleSheet(f"color: {color}; font-size: 16px;")
            swatch.setFixedWidth(16)
            row.addWidget(swatch)
            row.addWidget(QLabel(label))
            row.addStretch(1)
            layout.addLayout(row)

        layout.addStretch(1)
        legend.setFixedWidth(220)
        return legend

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_double_spin(
        self,
        minimum: float,
        maximum: float,
        decimals: int,
        value: float,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        spin.setMinimumWidth(140)
        return spin

    def _icon(self, name: str) -> QIcon:
        for extension in (".svg", ".png"):
            candidate = os.path.join(self._icon_dir, f"{name}{extension}")
            if os.path.exists(candidate):
                return QIcon(candidate)
        return QIcon()

    def _connect_internal_signals(self) -> None:
        for spin in (self.mechanical_tilt_spin, self.electrical_tilt_spin):
            spin.valueChanged.connect(self._update_total_tilt_label)
        self._update_total_tilt_label()

        self.distance_slider.valueChanged.connect(self._sync_distance_from_slider)
        self.max_distance_spin.valueChanged.connect(self._sync_distance_from_spin)
        self.dem_combo.currentTextChanged.connect(self._update_dem_status)

        self.run_button.clicked.connect(self.run_requested.emit)
        self.reset_button.clicked.connect(self._handle_reset)
        self.reset_view_button.clicked.connect(self.reset_view_requested.emit)
        self.optimise_button.clicked.connect(self.optimise_tilt_requested.emit)
        self.apply_tilt_button.clicked.connect(self._emit_apply_tilt)
        self.tilt_table.itemSelectionChanged.connect(self._update_apply_tilt_state)

        self.zoom_in_button.clicked.connect(self._zoom_in)
        self.zoom_out_button.clicked.connect(self._zoom_out)
        self.refresh_canvas_button.clicked.connect(self.map_canvas.refresh)
        self.export_kmz_button.clicked.connect(self._handle_export_kmz)

    def _update_total_tilt_label(self) -> None:
        total = self.mechanical_tilt_spin.value() + self.electrical_tilt_spin.value()
        self.total_tilt_label.setText(f"{total:.1f}\u00b0")

    def _sync_distance_from_slider(self, value: int) -> None:
        if abs(self.max_distance_spin.value() - value) > 0.001:
            blocker = self.max_distance_spin.blockSignals(True)
            self.max_distance_spin.setValue(float(value))
            self.max_distance_spin.blockSignals(blocker)
        self.distance_slider_label.setText(f"{value} m")

    def _sync_distance_from_spin(self, value: float) -> None:
        slider_value = max(self.distance_slider.minimum(), min(self.distance_slider.maximum(), int(value)))
        if self.distance_slider.value() != slider_value:
            blocker = self.distance_slider.blockSignals(True)
            self.distance_slider.setValue(slider_value)
            self.distance_slider.blockSignals(blocker)
        self.distance_slider_label.setText(f"{int(value)} m")

    def _update_dem_status(self, text: str) -> None:
        if "online" in text.lower():
            self.dem_status_label.setText("\u25cf Online: Ready")
            self.dem_status_label.setStyleSheet("color: #2c8c2c; font-weight: 600;")
        else:
            self.dem_status_label.setText("\u25cf Offline: Flat terrain")
            self.dem_status_label.setStyleSheet("color: #c0392b; font-weight: 600;")

    def _handle_reset(self) -> None:
        self.latitude_spin.setValue(-6.88908)
        self.longitude_spin.setValue(107.61848)
        self.azimuth_spin.setValue(90.0)
        self.antenna_height_spin.setValue(40.0)
        self.mechanical_tilt_spin.setValue(4.0)
        self.electrical_tilt_spin.setValue(2.0)
        self.vertical_beamwidth_spin.setValue(6.0)
        self.horizontal_beamwidth_spin.setValue(65.0)
        self.frequency_spin.setValue(2_100.0)
        self.tx_power_spin.setValue(46.0)
        self.antenna_gain_spin.setValue(17.0)
        self.cable_loss_spin.setValue(2.0)
        self.receiver_height_spin.setValue(1.5)
        self.max_distance_spin.setValue(5_000.0)
        self.sample_count_spin.setValue(256)
        self.dem_combo.setCurrentIndex(0)
        self.basemap_combo.setCurrentIndex(0)
        self.tilt_table.setRowCount(0)
        self.apply_tilt_button.setEnabled(False)
        self.export_kmz_button.setEnabled(False)
        self._reset_terrain_chart()
        self.main_intersection_label.setText("Main Beam Intersection: \u2014")
        self.coverage_footprint_label.setText("Coverage Footprint: \u2014")
        self.status_bar.showMessage("Ready")
        self.reset_requested.emit()

    def _zoom_in(self) -> None:
        self.map_canvas.zoomIn()

    def _zoom_out(self) -> None:
        self.map_canvas.zoomOut()

    def _handle_export_kmz(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export coverage to KMZ",
            "coverage.kmz",
            "Google Earth KMZ (*.kmz)",
        )
        if not path:
            return
        if not path.lower().endswith(".kmz"):
            path = f"{path}.kmz"
        self.export_kmz_requested.emit(path)

    def _emit_apply_tilt(self) -> None:
        row = self.tilt_table.currentRow()
        if row < 0:
            return
        try:
            mechanical = float(self.tilt_table.item(row, 0).text())
            electrical = float(self.tilt_table.item(row, 1).text())
        except (AttributeError, ValueError):
            return
        self.apply_tilt_candidate_requested.emit(mechanical, electrical)

    def _update_apply_tilt_state(self) -> None:
        self.apply_tilt_button.setEnabled(self.tilt_table.currentRow() >= 0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_site_parameters(self):
        # Local import to avoid a hard dependency cycle in headless tests.
        from .rf_core import SiteParameters

        return SiteParameters(
            latitude=self.latitude_spin.value(),
            longitude=self.longitude_spin.value(),
            azimuth_deg=self.azimuth_spin.value(),
            antenna_height_m=self.antenna_height_spin.value(),
            mechanical_tilt_deg=self.mechanical_tilt_spin.value(),
            electrical_tilt_deg=self.electrical_tilt_spin.value(),
            vertical_beamwidth_deg=self.vertical_beamwidth_spin.value(),
            horizontal_beamwidth_deg=self.horizontal_beamwidth_spin.value(),
            max_distance_m=self.max_distance_spin.value(),
            frequency_mhz=self.frequency_spin.value(),
            tx_power_dbm=self.tx_power_spin.value(),
            antenna_gain_dbi=self.antenna_gain_spin.value(),
            cable_loss_db=self.cable_loss_spin.value(),
            receiver_height_m=self.receiver_height_spin.value(),
            sample_count=self.sample_count_spin.value(),
            dem_source=self.dem_combo.currentText(),
        )

    def apply_tilt_values(self, mechanical: float, electrical: float) -> None:
        self.mechanical_tilt_spin.setValue(mechanical)
        self.electrical_tilt_spin.setValue(electrical)

    def get_tilt_search_bounds(self):
        return {
            "mech_min": self.mech_min_spin.value(),
            "mech_max": self.mech_max_spin.value(),
            "elec_min": self.elec_min_spin.value(),
            "elec_max": self.elec_max_spin.value(),
            "step": max(0.1, self.tilt_step_spin.value()),
            "threshold": self.threshold_spin.value(),
        }

    def populate_tilt_candidates(self, candidates) -> None:
        self.tilt_table.setRowCount(0)
        for candidate in candidates[:25]:
            row = self.tilt_table.rowCount()
            self.tilt_table.insertRow(row)
            self.tilt_table.setItem(row, 0, QTableWidgetItem(f"{candidate.mechanical_tilt_deg:.1f}"))
            self.tilt_table.setItem(row, 1, QTableWidgetItem(f"{candidate.electrical_tilt_deg:.1f}"))
            self.tilt_table.setItem(row, 2, QTableWidgetItem(f"{candidate.coverage_score * 100:.1f}"))
            distance = candidate.main_intersection_m
            self.tilt_table.setItem(
                row,
                3,
                QTableWidgetItem(f"{distance:.0f}" if distance is not None else "—"),
            )
        if self.tilt_table.rowCount() > 0:
            self.tilt_table.selectRow(0)

    def update_terrain_chart(
        self,
        result,
        unit_label: str = "m",
        on_complete: Optional[Callable[[], None]] = None,
    ) -> None:
        self.axes.clear()
        if result is None or not result.profile.distances_m:
            self._reset_terrain_chart()
            return

        distances = result.profile.distances_m
        elevations = result.profile.elevations_m

        self.axes.fill_between(distances, elevations, min(elevations) - 5.0, color="#c8b18d", alpha=0.55, label="Terrain")
        self.axes.plot(distances, elevations, color="#7a5a32", linewidth=1.5)
        self.axes.plot(distances, result.main_beam.heights_m, color="#1f9d3a", linewidth=2.0, label="Main Beam")
        self.axes.plot(distances, result.upper_beam.heights_m, color="#1f4ed8", linewidth=1.5, linestyle="--", label="Upper Beam")
        self.axes.plot(distances, result.lower_beam.heights_m, color="#d62728", linewidth=1.5, linestyle="--", label="Lower Beam")

        for intersection, color in (
            (result.main_intersection, "#1f9d3a"),
            (result.upper_intersection, "#1f4ed8"),
            (result.lower_intersection, "#d62728"),
        ):
            if intersection.distance_m is not None:
                self.axes.scatter(
                    [intersection.distance_m],
                    [intersection.elevation_m or 0.0],
                    color=color,
                    s=42,
                    zorder=6,
                    edgecolor="white",
                    linewidths=1.0,
                )

        self.axes.set_xlabel(f"Distance ({unit_label})")
        self.axes.set_ylabel(f"Elevation ({unit_label})")
        self.axes.grid(True, color="#dfe6ec", linestyle="-", linewidth=0.6)
        self.axes.legend(loc="upper right", fontsize=8, framealpha=0.85)
        self.figure_canvas.draw_idle()
        if on_complete is not None:
            on_complete()

    def _reset_terrain_chart(self) -> None:
        self.axes.clear()
        self.axes.set_xlabel("Distance (m)")
        self.axes.set_ylabel("Elevation (m)")
        self.axes.grid(True, color="#dfe6ec", linestyle="-", linewidth=0.6)
        self.axes.set_xlim(0.0, 1.0)
        self.axes.set_ylim(0.0, 1.0)
        self.figure_canvas.draw_idle()

    def set_intersection_summary(self, main_distance: Optional[float], footprint_label: Optional[str]) -> None:
        if main_distance is None:
            self.main_intersection_label.setText("Main Beam Intersection: \u2014")
        else:
            self.main_intersection_label.setText(f"Main Beam Intersection: {main_distance:.1f} m")

        if footprint_label is None:
            self.coverage_footprint_label.setText("Coverage Footprint: \u2014")
        else:
            self.coverage_footprint_label.setText(f"Coverage Footprint: {footprint_label}")

    def set_status(self, message: str, level: str = "info") -> None:
        colors = {
            "info": "#1f6f8b",
            "success": "#2c8c2c",
            "warning": "#c08e2c",
            "error": "#c0392b",
        }
        color = colors.get(level, "#1f6f8b")
        self.status_bar.setStyleSheet(f"QStatusBar {{ color: {color}; font-weight: 600; }}")
        self.status_bar.showMessage(message)

    def set_export_enabled(self, enabled: bool) -> None:
        self.export_kmz_button.setEnabled(enabled)

    def set_run_enabled(self, enabled: bool) -> None:
        self.run_button.setEnabled(enabled)
        self.optimise_button.setEnabled(enabled)
