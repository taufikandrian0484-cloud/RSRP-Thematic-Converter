"""About dialog for the Coverage Prediction plugin."""

from __future__ import annotations

import configparser
import os

from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .qt_compat import (
    ALIGN_RIGHT,
    ALIGN_TOP,
    ALIGN_VCENTER,
    KEEP_ASPECT_RATIO,
    SMOOTH_TRANSFORMATION,
)


def load_metadata() -> dict:
    metadata_path = os.path.join(os.path.dirname(__file__), "metadata.txt")
    parser = configparser.ConfigParser()
    parser.read(metadata_path, encoding="utf-8")
    return dict(parser["general"]) if parser.has_section("general") else {}


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.metadata = load_metadata()
        self.setWindowTitle("About Coverage Prediction")
        self.setMinimumWidth(480)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        icon_label = QLabel()
        icon_path = os.path.join(os.path.dirname(__file__), "resources", "icons", "coverage_logo.svg")
        if os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            if not pixmap.isNull():
                icon_label.setPixmap(pixmap.scaled(48, 48, KEEP_ASPECT_RATIO, SMOOTH_TRANSFORMATION))
        header.addWidget(icon_label, alignment=ALIGN_TOP)

        title = QLabel(self.metadata.get("name", "Coverage Prediction"))
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        header.addWidget(title, stretch=1, alignment=ALIGN_VCENTER)
        layout.addLayout(header)

        layout.addWidget(QLabel(f'Version: {self.metadata.get("version", "-")}'))
        layout.addWidget(QLabel(f'Author: {self.metadata.get("author", "-")}'))
        layout.addWidget(QLabel(f'Email: {self.metadata.get("email", "-").strip()}'))

        description = QLabel(self.metadata.get("about", ""))
        description.setWordWrap(True)
        layout.addWidget(description)

        notes = QLabel(
            "Inspired by propagationpredict.onrender.com. Coverage Prediction samples a digital "
            "elevation model along the antenna bearing, evaluates the upper, main and lower beam "
            "profiles with earth-curvature correction, and renders the predicted coverage as both "
            "QGIS layers and a Google Earth-ready KMZ."
        )
        notes.setWordWrap(True)
        layout.addWidget(notes)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, alignment=ALIGN_RIGHT)
