import configparser
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


def load_plugin_metadata():
    metadata_path = os.path.join(os.path.dirname(__file__), "metadata.txt")
    parser = configparser.ConfigParser()
    parser.read(metadata_path, encoding="utf-8")
    return parser["general"] if parser.has_section("general") else {}


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.metadata = load_plugin_metadata()
        self.info_icon_path = os.path.join(
            os.path.dirname(__file__),
            "resources",
            "icons",
            "information.png",
        )
        self.setWindowTitle("About RSRP Thematic Converter")
        self.setMinimumWidth(460)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        header_layout = QHBoxLayout()
        icon_label = QLabel()
        if os.path.exists(self.info_icon_path):
            pixmap = QPixmap(self.info_icon_path)
            if not pixmap.isNull():
                icon_label.setPixmap(
                    pixmap.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
        header_layout.addWidget(icon_label, alignment=Qt.AlignTop)

        title = QLabel(self.metadata.get("name", "RSRP Thematic Converter"))
        title.setTextFormat(Qt.RichText)
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        header_layout.addWidget(title, stretch=1, alignment=Qt.AlignVCenter)
        layout.addLayout(header_layout)

        version = QLabel(f'Version: {self.metadata.get("version", "-")}')
        version.setWordWrap(True)
        layout.addWidget(version)

        author = QLabel(f'Author: {self.metadata.get("author", "-")}')
        author.setWordWrap(True)
        layout.addWidget(author)

        email = QLabel(f'Email: {self.metadata.get("email", "-").strip()}')
        email.setWordWrap(True)
        layout.addWidget(email)

        description = QLabel(self.metadata.get("description", ""))
        description.setWordWrap(True)
        layout.addWidget(description)

        about = QLabel(self.metadata.get("about", ""))
        about.setWordWrap(True)
        layout.addWidget(about)

        details = QLabel(
            "This plugin loads CSV or GeoPackage data, applies fixed RSRP thematic classes, "
            "converts point-based input to square polygons for Google Earth-style output, "
            "and exports the result as KMZ. \n\n"
            " If RSRP thematic has been helpful in your work, consider supporting its continued development: \n"
        )
        details.setWordWrap(True)
        layout.addWidget(details)

        donation_title = QLabel("Donation")
        donation_title.setStyleSheet("font-weight: 700;")
        layout.addWidget(donation_title)

        paypal_link = QLabel(
            '<a href="https://www.paypal.me/raisetodevelope">PayPal - https://www.paypal.me/raisetodevelope</a>'
        )
        paypal_link.setOpenExternalLinks(True)
        paypal_link.setWordWrap(True)
        layout.addWidget(paypal_link)

        kofi_link = QLabel(
            '<a href="https://ko-fi.com/taufikandrian_2026">Ko-fi - https://ko-fi.com/taufikandrian_2026</a>'
        )
        kofi_link.setOpenExternalLinks(True)
        kofi_link.setWordWrap(True)
        layout.addWidget(kofi_link)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, alignment=Qt.AlignRight)
