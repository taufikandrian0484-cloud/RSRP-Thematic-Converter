"""Builders that turn a :class:`CoverageResult` into QGIS layers.

The same set of layers is reused for the embedded :class:`QgsMapCanvas`, the
QGIS Project (so the user can keep working with them after closing the dialog)
and the KMZ export.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsGraduatedSymbolRenderer,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsRendererRange,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)


COVERAGE_RANGES = (
    (-200.0, -110.0, "-140 to -110 dBm", "#ff0000"),
    (-110.0, -105.0, "-110 to -105 dBm", "#ffff00"),
    (-105.0, -100.0, "-105 to -100 dBm", "#00ff00"),
    (-100.0, -95.0, "-100 to -95 dBm", "#00b050"),
    (-95.0, 0.0, "-95 to -40 dBm", "#0000ff"),
)

INTERSECTION_COLORS = {
    "Main Beam Intersection": "#00b050",
    "Upper Intersection": "#0066ff",
    "Lower Intersection": "#ff3030",
}


@dataclass
class CoverageLayerBundle:
    coverage_points: QgsVectorLayer
    sector_polygon: QgsVectorLayer
    footprint_polygon: QgsVectorLayer
    intersections: QgsVectorLayer
    antenna_marker: QgsVectorLayer

    def all_layers(self) -> List[QgsVectorLayer]:
        return [
            self.footprint_polygon,
            self.sector_polygon,
            self.coverage_points,
            self.intersections,
            self.antenna_marker,
        ]


def _crs() -> QgsCoordinateReferenceSystem:
    return QgsCoordinateReferenceSystem("EPSG:4326")


def build_coverage_points_layer(name: str, points) -> QgsVectorLayer:
    layer = QgsVectorLayer("Point?crs=EPSG:4326", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes(
        [
            QgsField("distance_m", QVariant.Double),
            QgsField("rsrp_dbm", QVariant.Double),
            QgsField("elevation_m", QVariant.Double),
            QgsField("clearance_m", QVariant.Double),
        ]
    )
    layer.updateFields()

    features: List[QgsFeature] = []
    for point in points:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(point.longitude, point.latitude)))
        feature.setAttributes(
            [
                float(point.distance_m),
                float(point.rsrp_dbm),
                float(point.elevation_m),
                float(point.beam_clearance_m),
            ]
        )
        features.append(feature)
    provider.addFeatures(features)
    layer.updateExtents()

    ranges = []
    for lower, upper, label, color_hex in COVERAGE_RANGES:
        symbol = QgsMarkerSymbol.createSimple(
            {
                "name": "circle",
                "color": color_hex,
                "size": "3.0",
                "outline_color": "white",
                "outline_width": "0.2",
            }
        )
        ranges.append(QgsRendererRange(lower, upper, symbol, label))

    renderer = QgsGraduatedSymbolRenderer("rsrp_dbm", ranges)
    layer.setRenderer(renderer)
    return layer


def build_sector_layer(name: str, polygon: List[Tuple[float, float]]) -> QgsVectorLayer:
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("kind", QVariant.String)])
    layer.updateFields()

    if polygon:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(lon, lat) for lat, lon in polygon]]))
        feature.setAttributes(["sector"])
        provider.addFeature(feature)
        layer.updateExtents()

    symbol = QgsFillSymbol.createSimple(
        {
            "color": "255,255,102,80",
            "outline_color": "255,200,40",
            "outline_width": "0.6",
        }
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


def build_footprint_layer(name: str, polygon: List[Tuple[float, float]]) -> QgsVectorLayer:
    layer = QgsVectorLayer("Polygon?crs=EPSG:4326", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("kind", QVariant.String)])
    layer.updateFields()

    if polygon:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(lon, lat) for lat, lon in polygon]]))
        feature.setAttributes(["footprint"])
        provider.addFeature(feature)
        layer.updateExtents()

    symbol = QgsFillSymbol.createSimple(
        {
            "color": "51,153,255,90",
            "outline_color": "51,153,255",
            "outline_width": "0.6",
        }
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


def build_intersection_layer(name: str, intersections) -> QgsVectorLayer:
    layer = QgsVectorLayer("Point?crs=EPSG:4326", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes(
        [
            QgsField("kind", QVariant.String),
            QgsField("distance_m", QVariant.Double),
        ]
    )
    layer.updateFields()

    for intersection in intersections:
        if intersection.distance_m is None:
            continue
        feature = QgsFeature(layer.fields())
        feature.setGeometry(
            QgsGeometry.fromPointXY(QgsPointXY(intersection.longitude, intersection.latitude))
        )
        feature.setAttributes([intersection.label, float(intersection.distance_m)])
        provider.addFeature(feature)
    layer.updateExtents()

    # Use a categorised renderer keyed on the kind attribute. We emulate that
    # without QgsCategorizedSymbolRenderer to keep this file dependency-light.
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "triangle",
            "color": INTERSECTION_COLORS["Main Beam Intersection"],
            "size": "5.0",
            "outline_color": "white",
            "outline_width": "0.4",
        }
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


def build_antenna_marker_layer(name: str, latitude: float, longitude: float) -> QgsVectorLayer:
    layer = QgsVectorLayer("Point?crs=EPSG:4326", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("role", QVariant.String)])
    layer.updateFields()

    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(longitude, latitude)))
    feature.setAttributes(["antenna"])
    provider.addFeature(feature)
    layer.updateExtents()

    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "triangle",
            "color": "0,0,0,255",
            "size": "6.0",
            "outline_color": "white",
            "outline_width": "0.6",
        }
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


def build_layer_bundle(result, layer_prefix: str = "Coverage") -> CoverageLayerBundle:
    coverage_points = build_coverage_points_layer(f"{layer_prefix} - Coverage Points", result.points)
    sector = build_sector_layer(f"{layer_prefix} - Sector", result.sector_polygon)
    footprint = build_footprint_layer(f"{layer_prefix} - Footprint", result.footprint_polygon)
    intersections = build_intersection_layer(
        f"{layer_prefix} - Beam Intersections",
        [result.main_intersection, result.upper_intersection, result.lower_intersection],
    )
    antenna = build_antenna_marker_layer(
        f"{layer_prefix} - Antenna",
        result.profile.latitudes[0] if result.profile.latitudes else 0.0,
        result.profile.longitudes[0] if result.profile.longitudes else 0.0,
    )
    return CoverageLayerBundle(
        coverage_points=coverage_points,
        sector_polygon=sector,
        footprint_polygon=footprint,
        intersections=intersections,
        antenna_marker=antenna,
    )
