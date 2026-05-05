import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class RsrpThematicDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RSRP Thematic Converter")
        self.setMinimumWidth(560)
        self.logo_path = os.path.join(
            os.path.dirname(__file__),
            "resources",
            "icons",
            "RSRP_logo.png",
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        logo_label = QLabel()
        logo_label.setAlignment(Qt.AlignCenter)
        if os.path.exists(self.logo_path):
            pixmap = QPixmap(self.logo_path)
            if not pixmap.isNull():
                logo_label.setPixmap(
                    pixmap.scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
        layout.addWidget(logo_label)

        intro = QLabel(
            'Load a CSV or GeoPackage, style it using '
            '"Serving Cell Average RSRP (dBm)", and export the result as KMZ for Google Earth.'
        )
        intro.setAlignment(Qt.AlignCenter)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)

        self.input_path_edit = QLineEdit()
        self.input_browse_button = QPushButton("Browse...")
        self.input_browse_button.clicked.connect(self._browse_input)
        input_row = QHBoxLayout()
        input_row.addWidget(self.input_path_edit)
        input_row.addWidget(self.input_browse_button)
        form.addRow("Input file", input_row)

        self.layer_name_edit = QLineEdit("rsrp_thematic_layer")
        form.addRow("Layer name", self.layer_name_edit)

        self.x_field_combo = QComboBox()
        self.y_field_combo = QComboBox()
        form.addRow("CSV X field", self.x_field_combo)
        form.addRow("CSV Y field", self.y_field_combo)

        self.output_path_edit = QLineEdit()
        self.output_browse_button = QPushButton("Browse...")
        self.output_browse_button.clicked.connect(self._browse_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_path_edit)
        output_row.addWidget(self.output_browse_button)
        form.addRow("Output KMZ", output_row)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.run_button = QPushButton("Run")
        self.cancel_button = QPushButton("Cancel")
        self.run_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def _browse_input(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Input File",
            "",
            "Spatial data (*.csv *.gpkg)",
        )
        if path:
            self.input_path_edit.setText(path)

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save KMZ",
            "",
            "Google Earth KMZ (*.kmz)",
        )
        if path:
            if not path.lower().endswith(".kmz"):
                path = f"{path}.kmz"
            self.output_path_edit.setText(path)

    def set_csv_fields(self, fields):
        self.x_field_combo.clear()
        self.y_field_combo.clear()
        self.x_field_combo.addItems(fields)
        self.y_field_combo.addItems(fields)

        x_candidates = ("longitude", "lon", "x", "long", "lng")
        y_candidates = ("latitude", "lat", "y")

        self._select_candidate(self.x_field_combo, x_candidates)
        self._select_candidate(self.y_field_combo, y_candidates)

    def _select_candidate(self, combo, candidates):
        lowered = {combo.itemText(i).lower(): i for i in range(combo.count())}
        for candidate in candidates:
            if candidate in lowered:
                combo.setCurrentIndex(lowered[candidate])
                return

    def selected_input_path(self):
        return self.input_path_edit.text().strip()

    def selected_output_path(self):
        return self.output_path_edit.text().strip()

    def selected_layer_name(self):
        return self.layer_name_edit.text().strip() or "rsrp_thematic_layer"

    def selected_x_field(self):
        return self.x_field_combo.currentText().strip()

    def selected_y_field(self):
        return self.y_field_combo.currentText().strip()

    def validate_before_accept(self):
        if not self.selected_input_path():
            QMessageBox.warning(self, "Missing input", "Please choose an input CSV or GeoPackage file.")
            return False

        if not self.selected_output_path():
            QMessageBox.warning(self, "Missing output", "Please choose an output KMZ path.")
            return False

        return True

    def accept(self):
        if self.validate_before_accept():
            super().accept()
