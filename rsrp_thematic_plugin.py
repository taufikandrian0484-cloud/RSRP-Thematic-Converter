import os
import shutil
import tempfile
import zipfile
from xml.sax.saxutils import escape

import pandas as pd
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import (
    QgsFillSymbol,
    QgsGraduatedSymbolRenderer,
    QgsProject,
    QgsRendererRange,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .about_dialog import AboutDialog
from .rsrp_thematic_dialog import RsrpThematicDialog

try:
    import geopandas as gpd
except ImportError:  # pragma: no cover
    gpd = None


class RsrpThematicPlugin:
    FIELD_NAME = "Serving Cell Average RSRP (dBm)"
    POINT_POLYGON_PERIMETER_METERS = 150.0
    STYLE_RANGES = (
        (-140.9, -110.0, "Red", "#ff0000", "e60000ff"),
        (-110.0, -105.0, "Yellow", "#ffff00", "e600ffff"),
        (-105.0, -100.0, "Light Green", "#00ff00", "e600ff00"),
        (-100.0, -95.0, "Dark Green", "#00b050", "e600b050"),
        (-95.0, 0.0, "Blue", "#0000ff", "e6ff0000"),
    )

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.about_action = None
        self.logo_icon_path = os.path.join(
            os.path.dirname(__file__),
            "resources",
            "icons",
            "RSRP_logo.png",
        )
        self.info_icon_path = os.path.join(
            os.path.dirname(__file__),
            "resources",
            "icons",
            "information.png",
        )

    def initGui(self):
        self.action = QAction(QIcon(self.logo_icon_path), "RSRP Thematic Converter", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&RSRP Thematic Converter", self.action)
        self.iface.addToolBarIcon(self.action)

        self.about_action = QAction(
            QIcon(self.info_icon_path),
            "About RSRP Thematic Converter",
            self.iface.mainWindow(),
        )
        self.about_action.triggered.connect(self.show_about)
        self.iface.addPluginToMenu("&RSRP Thematic Converter", self.about_action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu("&RSRP Thematic Converter", self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.about_action is not None:
            self.iface.removePluginMenu("&RSRP Thematic Converter", self.about_action)

    def show_about(self):
        dialog = AboutDialog(self.iface.mainWindow())
        dialog.exec_()

    def run(self):
        if gpd is None:
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Missing dependency",
                "GeoPandas is not available in this QGIS Python environment.",
            )
            return

        dialog = RsrpThematicDialog(self.iface.mainWindow())
        dialog.input_path_edit.textChanged.connect(lambda _: self._refresh_csv_fields(dialog))
        self._refresh_csv_fields(dialog)

        if dialog.exec_():
            self._process(dialog)

    def _refresh_csv_fields(self, dialog):
        path = dialog.selected_input_path()
        dialog.x_field_combo.clear()
        dialog.y_field_combo.clear()

        if not path.lower().endswith(".csv") or not os.path.exists(path):
            return

        try:
            dataframe = pd.read_csv(path, nrows=5)
        except Exception:
            return

        dialog.set_csv_fields([str(column) for column in dataframe.columns])

    def _process(self, dialog):
        input_path = dialog.selected_input_path()
        layer_name = dialog.selected_layer_name()

        try:
            if input_path.lower().endswith(".csv"):
                geodataframe, layer = self._load_csv(
                    input_path,
                    layer_name,
                    dialog.selected_x_field(),
                    dialog.selected_y_field(),
                )
            elif input_path.lower().endswith(".gpkg"):
                geodataframe, layer = self._load_geopackage(input_path, layer_name)
            else:
                raise ValueError("Only CSV and GeoPackage files are supported.")

            export_geodataframe = self._prepare_export_geodataframe(geodataframe)
            if self._geometry_signature(export_geodataframe) != self._geometry_signature(geodataframe):
                layer = self._create_layer_from_geodataframe(export_geodataframe, layer_name)

            self._apply_graduated_style(layer)
            QgsProject.instance().addMapLayer(layer)
            self._export_geodataframe_to_kmz(export_geodataframe, layer_name, dialog.selected_output_path())
            self.iface.messageBar().pushSuccess(
                "RSRP Thematic Converter",
                f'Layer "{layer.name()}" loaded, styled, and exported to KMZ successfully.',
            )
        except Exception as exc:
            QMessageBox.critical(self.iface.mainWindow(), "Conversion failed", str(exc))

    def _load_csv(self, csv_path, layer_name, x_field, y_field):
        if not x_field or not y_field:
            raise ValueError("Please choose X and Y fields for the CSV input.")

        dataframe = pd.read_csv(csv_path)
        self._validate_rsrp_column(dataframe.columns)

        x_values = pd.to_numeric(dataframe[x_field], errors="coerce")
        y_values = pd.to_numeric(dataframe[y_field], errors="coerce")

        geodataframe = gpd.GeoDataFrame(
            dataframe.copy(),
            geometry=gpd.points_from_xy(x_values, y_values),
            crs="EPSG:4326",
        ).dropna(subset=["geometry"])

        if geodataframe.empty:
            raise ValueError("No valid point geometry could be created from the selected CSV fields.")

        layer = self._create_layer_from_geodataframe(geodataframe, layer_name)
        return geodataframe, layer

    def _load_geopackage(self, gpkg_path, layer_name):
        geodataframe = gpd.read_file(gpkg_path)
        self._validate_rsrp_column(geodataframe.columns)

        layer = QgsVectorLayer(gpkg_path, layer_name, "ogr")
        if not layer.isValid():
            raise ValueError("QGIS could not load the GeoPackage layer.")
        return geodataframe, layer

    def _validate_rsrp_column(self, columns):
        if self.FIELD_NAME not in columns:
            raise ValueError(f'The required field "{self.FIELD_NAME}" was not found.')

    def _temp_geojson_path(self, layer_name):
        safe_name = "".join(character if character.isalnum() else "_" for character in layer_name)
        file_handle, path = tempfile.mkstemp(prefix=f"{safe_name}_", suffix=".geojson")
        os.close(file_handle)
        return path

    def _create_layer_from_geodataframe(self, geodataframe, layer_name):
        source_path = self._temp_geojson_path(layer_name)
        geodataframe.to_file(source_path, driver="GeoJSON")
        layer = QgsVectorLayer(source_path, layer_name, "ogr")
        if not layer.isValid():
            raise ValueError("QGIS could not load the generated layer.")
        return layer

    def _export_geodataframe_to_kmz(self, geodataframe, layer_name, output_kmz_path):
        if not output_kmz_path.lower().endswith(".kmz"):
            output_kmz_path = f"{output_kmz_path}.kmz"

        temp_dir = tempfile.mkdtemp(prefix="rsrp_kmz_")
        kml_path = os.path.join(temp_dir, "doc.kml")
        try:
            with open(kml_path, "w", encoding="utf-8") as kml_file:
                kml_file.write(self._build_kml_document(geodataframe, layer_name))

            with zipfile.ZipFile(output_kmz_path, "w", zipfile.ZIP_DEFLATED) as kmz_file:
                kmz_file.write(kml_path, arcname="doc.kml")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _build_kml_document(self, geodataframe, layer_name):
        geodataframe = self._prepare_export_geodataframe(geodataframe)
        if geodataframe.crs is not None:
            geodataframe = geodataframe.to_crs(epsg=4326)

        style_blocks = []
        for index, (_, _, label, _, kml_color) in enumerate(self.STYLE_RANGES, start=1):
            style_blocks.append(self._kml_style_block(f"rsrp_{index}", label, kml_color))

        placemarks = []
        placemark_id = 1
        for _, row in geodataframe.iterrows():
            geometry = row.geometry
            if geometry is None or geometry.is_empty:
                continue

            style_id, label = self._style_for_value(row.get(self.FIELD_NAME))
            geometry_xml = self._geometry_to_kml(geometry, placemark_id)
            if not geometry_xml:
                continue

            name = self._placemark_name(row, placemark_id, label)
            placemarks.append(
                "\n".join(
                    [
                        f'\t<Placemark id="pm_{placemark_id}">',
                        f"\t\t<name>{escape(name)}</name>",
                        f"\t\t<styleUrl>#{style_id}</styleUrl>",
                        geometry_xml,
                        "\t</Placemark>",
                    ]
                )
            )
            placemark_id += 1

        return "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2" xmlns:kml="http://www.opengis.net/kml/2.2" xmlns:atom="http://www.w3.org/2005/Atom">',
                "<Document>",
                f"\t<name>{escape(layer_name)}.kml</name>",
                "\t<open>1</open>",
                *style_blocks,
                *placemarks,
                "</Document>",
                "</kml>",
            ]
        )

    def _kml_style_block(self, style_id, label, kml_color):
        return "\n".join(
            [
                f'\t<Style id="{style_id}">',
                "\t\t<PolyStyle>",
                f"\t\t\t<color>{kml_color}</color>",
                "\t\t\t<fill>1</fill>",
                "\t\t\t<outline>0</outline>",
                "\t\t</PolyStyle>",
                "\t</Style>",
            ]
        )

    def _prepare_export_geodataframe(self, geodataframe):
        if geodataframe.empty:
            return geodataframe

        geometry_types = set(geodataframe.geometry.geom_type.dropna().unique())
        if geometry_types and geometry_types.issubset({"Point", "MultiPoint"}):
            return self._convert_points_to_square_polygons(geodataframe)

        return geodataframe

    def _convert_points_to_square_polygons(self, geodataframe):
        source_crs = geodataframe.crs or "EPSG:4326"
        working = geodataframe.set_crs(source_crs, allow_override=True)

        try:
            metric_crs = working.estimate_utm_crs()
        except Exception:
            metric_crs = None

        if metric_crs is None:
            metric_crs = "EPSG:3857"

        working = working.to_crs(metric_crs)
        half_side_meters = self.POINT_POLYGON_PERIMETER_METERS / 8.0

        def point_to_square(geometry):
            if geometry is None or geometry.is_empty:
                return geometry
            if geometry.geom_type == "Point":
                return geometry.buffer(half_side_meters, cap_style=3)
            if geometry.geom_type == "MultiPoint":
                squares = [point.buffer(half_side_meters, cap_style=3) for point in geometry.geoms]
                if not squares:
                    return geometry
                merged = squares[0]
                for square in squares[1:]:
                    merged = merged.union(square)
                return merged
            return geometry

        working = working.copy()
        working.geometry = working.geometry.apply(point_to_square)
        return working.to_crs(epsg=4326)

    def _geometry_signature(self, geodataframe):
        if geodataframe.empty:
            return tuple()
        return tuple(sorted(set(geodataframe.geometry.geom_type.dropna().unique())))

    def _style_for_value(self, value):
        numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.isna(numeric_value):
            return "rsrp_1", self.STYLE_RANGES[0][2]

        for index, (lower, upper, label, _, _) in enumerate(self.STYLE_RANGES, start=1):
            in_range = lower <= float(numeric_value) < upper
            on_upper_bound = index == len(self.STYLE_RANGES) and float(numeric_value) == upper
            if in_range or on_upper_bound:
                return f"rsrp_{index}", label

        return "rsrp_1", self.STYLE_RANGES[0][2]

    def _placemark_name(self, row, placemark_id, label):
        value = row.get(self.FIELD_NAME)
        if pd.isna(value):
            return f"{placemark_id} - {label}"
        return f"{placemark_id} - {value}"

    def _geometry_to_kml(self, geometry, placemark_id):
        geometry_type = geometry.geom_type

        if geometry_type == "Polygon":
            return self._polygon_to_kml(geometry, placemark_id)

        if geometry_type == "MultiPolygon":
            polygon_parts = []
            for index, polygon in enumerate(geometry.geoms, start=1):
                polygon_parts.append(self._polygon_to_kml(polygon, f"{placemark_id}_{index}"))
            return "\n".join(["\t\t<MultiGeometry>", *polygon_parts, "\t\t</MultiGeometry>"])

        if geometry_type == "LineString":
            coordinates = self._coordinate_sequence_to_kml(list(geometry.coords))
            return "\n".join(
                [
                    f'\t\t<LineString id="ln_{placemark_id}">',
                    "\t\t\t<tessellate>1</tessellate>",
                    f"\t\t\t<coordinates>{coordinates}</coordinates>",
                    "\t\t</LineString>",
                ]
            )

        if geometry_type == "MultiLineString":
            line_parts = []
            for index, line in enumerate(geometry.geoms, start=1):
                coordinates = self._coordinate_sequence_to_kml(list(line.coords))
                line_parts.append(
                    "\n".join(
                        [
                            f'\t\t\t<LineString id="ln_{placemark_id}_{index}">',
                            "\t\t\t\t<tessellate>1</tessellate>",
                            f"\t\t\t\t<coordinates>{coordinates}</coordinates>",
                            "\t\t\t</LineString>",
                        ]
                    )
                )
            return "\n".join(["\t\t<MultiGeometry>", *line_parts, "\t\t</MultiGeometry>"])

        return ""

    def _polygon_to_kml(self, polygon, placemark_id):
        outer_coordinates = self._coordinate_sequence_to_kml(list(polygon.exterior.coords))
        interior_blocks = []
        for index, interior in enumerate(polygon.interiors, start=1):
            inner_coordinates = self._coordinate_sequence_to_kml(list(interior.coords))
            interior_blocks.append(
                "\n".join(
                    [
                        "\t\t\t<innerBoundaryIs>",
                        f'\t\t\t\t<LinearRing id="inner_{placemark_id}_{index}">',
                        f"\t\t\t\t\t<coordinates>{inner_coordinates}</coordinates>",
                        "\t\t\t\t</LinearRing>",
                        "\t\t\t</innerBoundaryIs>",
                    ]
                )
            )

        return "\n".join(
            [
                f'\t\t<Polygon id="pg_{placemark_id}">',
                "\t\t\t<outerBoundaryIs>",
                f'\t\t\t\t<LinearRing id="outer_{placemark_id}">',
                f"\t\t\t\t\t<coordinates>{outer_coordinates}</coordinates>",
                "\t\t\t\t</LinearRing>",
                "\t\t\t</outerBoundaryIs>",
                *interior_blocks,
                "\t\t</Polygon>",
            ]
        )

    def _coordinate_sequence_to_kml(self, coordinates):
        return " ".join(self._point_coordinates_to_kml(point) for point in coordinates)

    def _point_coordinates_to_kml(self, point):
        if len(point) >= 3:
            return f"{point[0]},{point[1]},{point[2]}"
        return f"{point[0]},{point[1]},0"

    def _apply_graduated_style(self, layer):
        symbol = QgsFillSymbol.createSimple({"outline_style": "no"})
        ranges = []
        for lower, upper, label, color_hex, _ in self.STYLE_RANGES:
            range_symbol = symbol.clone()
            range_symbol.setColor(QColor(color_hex))
            ranges.append(QgsRendererRange(lower, upper, range_symbol, label))

        renderer = QgsGraduatedSymbolRenderer(self.FIELD_NAME, ranges)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
