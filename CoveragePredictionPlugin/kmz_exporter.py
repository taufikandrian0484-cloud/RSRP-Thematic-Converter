"""KMZ exporter for Coverage Prediction results.

The resulting KMZ embeds:

* A graduated coverage point cloud (one Placemark per sample with a colour
  matching the predicted RSRP bin).
* The horizontal sector polygon.
* The coverage footprint polygon.
* The main / upper / lower beam intersection markers.
* The antenna location marker.

The XML is built without any external dependencies (no simplekml, no
geopandas) so the plugin works in stock QGIS installations.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from typing import List, Tuple
from xml.sax.saxutils import escape


COVERAGE_BINS = (
    (-200.0, -110.0, "RSRP -140 to -110 dBm", "ff0000ff"),
    (-110.0, -105.0, "RSRP -110 to -105 dBm", "ff00ffff"),
    (-105.0, -100.0, "RSRP -105 to -100 dBm", "ff00ff00"),
    (-100.0, -95.0, "RSRP -100 to -95 dBm", "ff50b000"),
    (-95.0, 0.0, "RSRP -95 to -40 dBm", "ffff0000"),
)

INTERSECTION_STYLE = {
    "Main Beam Intersection": ("intersection_main", "ff50b000"),
    "Upper Intersection": ("intersection_upper", "ffff6600"),
    "Lower Intersection": ("intersection_lower", "ff3030ff"),
}


def export_coverage_to_kmz(result, params, output_path: str, layer_name: str = "Coverage Prediction") -> str:
    """Write ``result`` to ``output_path`` and return the final file path."""

    if not output_path.lower().endswith(".kmz"):
        output_path = f"{output_path}.kmz"

    temp_dir = tempfile.mkdtemp(prefix="coverage_kmz_")
    kml_path = os.path.join(temp_dir, "doc.kml")
    try:
        with open(kml_path, "w", encoding="utf-8") as kml_file:
            kml_file.write(_build_kml(result, params, layer_name))
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as kmz:
            kmz.write(kml_path, arcname="doc.kml")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return output_path


def _build_kml(result, params, layer_name: str) -> str:
    parts: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "<Document>",
        f"\t<name>{escape(layer_name)}</name>",
        "\t<open>1</open>",
        _build_description(result, params),
    ]

    parts.extend(_build_styles())
    parts.append(_build_sector_placemark(result.sector_polygon))
    parts.append(_build_footprint_placemark(result.footprint_polygon))
    parts.extend(_build_coverage_placemarks(result.points))
    parts.extend(_build_intersection_placemarks(result))
    parts.append(_build_antenna_placemark(params))

    parts.append("</Document>")
    parts.append("</kml>")
    return "\n".join(parts)


def _build_description(result, params) -> str:
    def _format_distance(value):
        return f"{value:.1f} m" if value is not None else "-"

    description = (
        f"Latitude: {params.latitude:.5f}\n"
        f"Longitude: {params.longitude:.5f}\n"
        f"Azimuth: {params.azimuth_deg:.1f} deg\n"
        f"Antenna height: {params.antenna_height_m:.1f} m\n"
        f"Total tilt: {params.total_tilt_deg:.2f} deg\n"
        f"Vertical beamwidth: {params.vertical_beamwidth_deg:.1f} deg\n"
        f"Horizontal beamwidth: {params.horizontal_beamwidth_deg:.1f} deg\n"
        f"Max distance: {params.max_distance_m:.0f} m\n"
        f"Frequency: {params.frequency_mhz:.0f} MHz\n"
        f"Tx power: {params.tx_power_dbm:.1f} dBm\n"
        f"DEM source: {result.profile.source}\n"
        f"Main beam impact: {_format_distance(result.main_intersection.distance_m)}\n"
        f"Upper intersection: {_format_distance(result.upper_intersection.distance_m)}\n"
        f"Lower intersection: {_format_distance(result.lower_intersection.distance_m)}\n"
    )
    return f"\t<description><![CDATA[<pre>{escape(description)}</pre>]]></description>"


def _build_styles() -> List[str]:
    blocks: List[str] = []
    for index, (_, _, label, kml_color) in enumerate(COVERAGE_BINS, start=1):
        blocks.append(
            "\n".join(
                [
                    f'\t<Style id="coverage_{index}">',
                    "\t\t<IconStyle>",
                    "\t\t\t<scale>0.6</scale>",
                    f"\t\t\t<color>{kml_color}</color>",
                    "\t\t\t<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon>",
                    "\t\t</IconStyle>",
                    "\t\t<LabelStyle><scale>0</scale></LabelStyle>",
                    "\t</Style>",
                ]
            )
        )

    blocks.append(
        "\n".join(
            [
                '\t<Style id="sector_style">',
                "\t\t<LineStyle><color>ff28c8ff</color><width>1.4</width></LineStyle>",
                "\t\t<PolyStyle><color>50ffff66</color><fill>1</fill><outline>1</outline></PolyStyle>",
                "\t</Style>",
            ]
        )
    )

    blocks.append(
        "\n".join(
            [
                '\t<Style id="footprint_style">',
                "\t\t<LineStyle><color>ffff9933</color><width>1.4</width></LineStyle>",
                "\t\t<PolyStyle><color>5affb340</color><fill>1</fill><outline>1</outline></PolyStyle>",
                "\t</Style>",
            ]
        )
    )

    for style_id, color in INTERSECTION_STYLE.values():
        blocks.append(
            "\n".join(
                [
                    f'\t<Style id="{style_id}">',
                    "\t\t<IconStyle>",
                    "\t\t\t<scale>0.9</scale>",
                    f"\t\t\t<color>{color}</color>",
                    "\t\t\t<Icon><href>http://maps.google.com/mapfiles/kml/shapes/target.png</href></Icon>",
                    "\t\t</IconStyle>",
                    "\t</Style>",
                ]
            )
        )

    blocks.append(
        "\n".join(
            [
                '\t<Style id="antenna_style">',
                "\t\t<IconStyle>",
                "\t\t\t<scale>1.0</scale>",
                "\t\t\t<color>ff000000</color>",
                "\t\t\t<Icon><href>http://maps.google.com/mapfiles/kml/shapes/triangle.png</href></Icon>",
                "\t\t</IconStyle>",
                "\t</Style>",
            ]
        )
    )

    return blocks


def _bin_index_for(rsrp: float) -> int:
    for index, (lower, upper, _, _) in enumerate(COVERAGE_BINS, start=1):
        if lower <= rsrp < upper:
            return index
    return len(COVERAGE_BINS)


def _build_coverage_placemarks(points) -> List[str]:
    placemarks: List[str] = []
    for index, point in enumerate(points, start=1):
        bin_index = _bin_index_for(point.rsrp_dbm)
        placemarks.append(
            "\n".join(
                [
                    "\t<Placemark>",
                    f"\t\t<name>{index}: {point.rsrp_dbm:.1f} dBm</name>",
                    f"\t\t<styleUrl>#coverage_{bin_index}</styleUrl>",
                    "\t\t<Point>",
                    f"\t\t\t<coordinates>{point.longitude:.6f},{point.latitude:.6f},0</coordinates>",
                    "\t\t</Point>",
                    "\t</Placemark>",
                ]
            )
        )
    return placemarks


def _polygon_xml(polygon: List[Tuple[float, float]]) -> str:
    if not polygon:
        return ""
    coords = " ".join(f"{lon:.6f},{lat:.6f},0" for lat, lon in polygon)
    return "\n".join(
        [
            "\t\t<Polygon>",
            "\t\t\t<outerBoundaryIs>",
            "\t\t\t\t<LinearRing>",
            f"\t\t\t\t\t<coordinates>{coords}</coordinates>",
            "\t\t\t\t</LinearRing>",
            "\t\t\t</outerBoundaryIs>",
            "\t\t</Polygon>",
        ]
    )


def _build_sector_placemark(polygon: List[Tuple[float, float]]) -> str:
    geometry = _polygon_xml(polygon)
    if not geometry:
        return ""
    return "\n".join(
        [
            "\t<Placemark>",
            "\t\t<name>Antenna Sector</name>",
            "\t\t<styleUrl>#sector_style</styleUrl>",
            geometry,
            "\t</Placemark>",
        ]
    )


def _build_footprint_placemark(polygon: List[Tuple[float, float]]) -> str:
    geometry = _polygon_xml(polygon)
    if not geometry:
        return ""
    return "\n".join(
        [
            "\t<Placemark>",
            "\t\t<name>Coverage Footprint</name>",
            "\t\t<styleUrl>#footprint_style</styleUrl>",
            geometry,
            "\t</Placemark>",
        ]
    )


def _build_intersection_placemarks(result) -> List[str]:
    placemarks: List[str] = []
    for intersection in (result.main_intersection, result.upper_intersection, result.lower_intersection):
        if intersection.distance_m is None:
            continue
        style_id, _ = INTERSECTION_STYLE.get(intersection.label, ("intersection_main", "ff50b000"))
        placemarks.append(
            "\n".join(
                [
                    "\t<Placemark>",
                    f"\t\t<name>{escape(intersection.label)}: {intersection.distance_m:.1f} m</name>",
                    f"\t\t<styleUrl>#{style_id}</styleUrl>",
                    "\t\t<Point>",
                    f"\t\t\t<coordinates>{intersection.longitude:.6f},{intersection.latitude:.6f},0</coordinates>",
                    "\t\t</Point>",
                    "\t</Placemark>",
                ]
            )
        )
    return placemarks


def _build_antenna_placemark(params) -> str:
    return "\n".join(
        [
            "\t<Placemark>",
            "\t\t<name>Antenna</name>",
            "\t\t<styleUrl>#antenna_style</styleUrl>",
            "\t\t<Point>",
            f"\t\t\t<coordinates>{params.longitude:.6f},{params.latitude:.6f},{params.antenna_height_m:.1f}</coordinates>",
            "\t\t</Point>",
            "\t</Placemark>",
        ]
    )
