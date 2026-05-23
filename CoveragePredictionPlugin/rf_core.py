"""RF math core for the Coverage Prediction QGIS plugin.

This module is intentionally free of any QGIS-specific imports so that it can be
unit tested as plain Python and reused by every UI surface (dialog, processing
algorithm, scripting console, etc.). It provides:

* Geodesy helpers (destination point from bearing/distance, haversine)
* Terrain elevation sampling against the Open-Meteo Elevation API with a
  graceful offline fallback (flat terrain) so that the plugin still works in
  air-gapped environments.
* Beam geometry calculations including earth-curvature correction.
* A simple but realistic RSRP / received-power estimator that combines free
  space loss, log-distance correction and knife-edge diffraction loss.
* Beam-to-terrain intersection finder (where main / upper / lower beam first
  touch the ground).
* A tilt-optimisation routine that maximises the predicted coverage area for a
  given antenna installation.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request


EARTH_RADIUS_M = 6_371_000.0
# k=4/3 effective Earth radius accounts for standard atmospheric refraction.
EFFECTIVE_EARTH_RADIUS_M = EARTH_RADIUS_M * 4.0 / 3.0
SPEED_OF_LIGHT = 299_792_458.0


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass
class SiteParameters:
    """User-facing RF parameters captured from the dialog."""

    latitude: float
    longitude: float
    azimuth_deg: float = 90.0
    antenna_height_m: float = 40.0
    mechanical_tilt_deg: float = 4.0
    electrical_tilt_deg: float = 2.0
    vertical_beamwidth_deg: float = 6.0
    horizontal_beamwidth_deg: float = 65.0
    max_distance_m: float = 5000.0
    frequency_mhz: float = 2_100.0
    tx_power_dbm: float = 46.0
    antenna_gain_dbi: float = 17.0
    cable_loss_db: float = 2.0
    receiver_height_m: float = 1.5
    sample_count: int = 256
    dem_source: str = "Open-Meteo (Online)"

    @property
    def total_tilt_deg(self) -> float:
        return self.mechanical_tilt_deg + self.electrical_tilt_deg


@dataclass
class TerrainProfile:
    """Sampled elevation profile along the antenna bearing."""

    distances_m: List[float] = field(default_factory=list)
    elevations_m: List[float] = field(default_factory=list)
    latitudes: List[float] = field(default_factory=list)
    longitudes: List[float] = field(default_factory=list)
    source: str = "fallback"

    def __len__(self) -> int:
        return len(self.distances_m)

    @property
    def base_elevation_m(self) -> float:
        return self.elevations_m[0] if self.elevations_m else 0.0


@dataclass
class BeamProfile:
    """Beam height (above MSL) at each sampled distance."""

    label: str
    tilt_deg: float
    heights_m: List[float]


@dataclass
class CoveragePoint:
    distance_m: float
    latitude: float
    longitude: float
    rsrp_dbm: float
    elevation_m: float
    beam_clearance_m: float


@dataclass
class BeamIntersection:
    label: str
    distance_m: Optional[float]
    latitude: Optional[float]
    longitude: Optional[float]
    elevation_m: Optional[float]


@dataclass
class CoverageResult:
    profile: TerrainProfile
    main_beam: BeamProfile
    upper_beam: BeamProfile
    lower_beam: BeamProfile
    points: List[CoveragePoint]
    main_intersection: BeamIntersection
    upper_intersection: BeamIntersection
    lower_intersection: BeamIntersection
    sector_polygon: List[Tuple[float, float]]
    footprint_polygon: List[Tuple[float, float]]


# ---------------------------------------------------------------------------
# Geodesy helpers
# ---------------------------------------------------------------------------


def destination_point(latitude: float, longitude: float, bearing_deg: float, distance_m: float) -> Tuple[float, float]:
    """Return the (lat, lon) reached travelling ``distance_m`` along ``bearing_deg``.

    Uses the spherical earth model which is accurate enough for the kind of
    distances handled by this plugin (a few kilometres at most).
    """

    angular_distance = distance_m / EARTH_RADIUS_M
    bearing_rad = math.radians(bearing_deg)
    lat_rad = math.radians(latitude)
    lon_rad = math.radians(longitude)

    sin_lat2 = math.sin(lat_rad) * math.cos(angular_distance) + math.cos(lat_rad) * math.sin(angular_distance) * math.cos(bearing_rad)
    lat2 = math.asin(max(-1.0, min(1.0, sin_lat2)))
    lon2 = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(angular_distance) * math.cos(lat_rad),
        math.cos(angular_distance) - math.sin(lat_rad) * math.sin(lat2),
    )

    return math.degrees(lat2), ((math.degrees(lon2) + 540.0) % 360.0) - 180.0


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    sin_phi = math.sin(delta_phi / 2.0)
    sin_lambda = math.sin(delta_lambda / 2.0)
    a = sin_phi * sin_phi + math.cos(phi1) * math.cos(phi2) * sin_lambda * sin_lambda
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Terrain sampling
# ---------------------------------------------------------------------------


class TerrainSampler:
    """Samples elevations from Open-Meteo, falling back to a flat profile.

    The Open-Meteo API has a relatively low limit on the number of points that
    can be requested in a single call, so we batch the queries.
    """

    OPEN_METEO_URL = "https://api.open-meteo.com/v1/elevation"
    BATCH_SIZE = 100
    DEFAULT_TIMEOUT = 8.0

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def sample(self, params: SiteParameters) -> TerrainProfile:
        sample_count = max(8, int(params.sample_count))
        distances = [params.max_distance_m * step / (sample_count - 1) for step in range(sample_count)]

        latitudes: List[float] = []
        longitudes: List[float] = []
        for distance in distances:
            lat, lon = destination_point(
                params.latitude,
                params.longitude,
                params.azimuth_deg,
                distance,
            )
            latitudes.append(lat)
            longitudes.append(lon)

        elevations: List[float] = []
        source = "fallback"

        if params.dem_source.lower().startswith("open-meteo"):
            try:
                elevations = self._fetch_open_meteo(latitudes, longitudes)
                source = "open-meteo"
            except Exception:
                elevations = []

        if not elevations:
            # Flat-earth fallback, useful when offline or the DEM service is
            # unreachable.
            elevations = [0.0 for _ in distances]
            source = "fallback (flat terrain)"

        return TerrainProfile(
            distances_m=distances,
            elevations_m=elevations,
            latitudes=latitudes,
            longitudes=longitudes,
            source=source,
        )

    def _fetch_open_meteo(self, latitudes: Sequence[float], longitudes: Sequence[float]) -> List[float]:
        elevations: List[float] = []
        for start in range(0, len(latitudes), self.BATCH_SIZE):
            chunk_lat = latitudes[start : start + self.BATCH_SIZE]
            chunk_lon = longitudes[start : start + self.BATCH_SIZE]
            params = urllib_parse.urlencode(
                {
                    "latitude": ",".join(f"{value:.6f}" for value in chunk_lat),
                    "longitude": ",".join(f"{value:.6f}" for value in chunk_lon),
                }
            )
            url = f"{self.OPEN_METEO_URL}?{params}"
            try:
                with urllib_request.urlopen(url, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (urllib_error.URLError, TimeoutError, ValueError):
                raise

            chunk = payload.get("elevation") or []
            if len(chunk) != len(chunk_lat):
                raise ValueError("Open-Meteo returned an unexpected elevation payload")
            elevations.extend(float(value) for value in chunk)

        return elevations


# ---------------------------------------------------------------------------
# Beam geometry & RF link budget
# ---------------------------------------------------------------------------


def beam_height(antenna_msl_m: float, tilt_deg: float, distance_m: float) -> float:
    """Beam height above MSL at ``distance_m`` accounting for earth curvature.

    Positive ``tilt_deg`` means the beam points downwards (the standard cellular
    convention). Earth-curvature correction uses the 4/3 effective radius which
    is the textbook value for tropospheric refraction.
    """

    drop = distance_m * math.tan(math.radians(tilt_deg))
    earth_drop = (distance_m * distance_m) / (2.0 * EFFECTIVE_EARTH_RADIUS_M)
    return antenna_msl_m - drop - earth_drop


def build_beam_profile(
    label: str,
    antenna_msl_m: float,
    tilt_deg: float,
    distances: Sequence[float],
) -> BeamProfile:
    heights = [beam_height(antenna_msl_m, tilt_deg, distance) for distance in distances]
    return BeamProfile(label=label, tilt_deg=tilt_deg, heights_m=heights)


def first_intersection(
    label: str,
    profile: TerrainProfile,
    beam: BeamProfile,
    receiver_height_m: float = 0.0,
) -> BeamIntersection:
    """Return the first place where the beam descends below terrain+rx height."""

    for index in range(1, len(profile.distances_m)):
        terrain_target = profile.elevations_m[index] + receiver_height_m
        beam_height_now = beam.heights_m[index]
        beam_height_prev = beam.heights_m[index - 1]
        terrain_prev = profile.elevations_m[index - 1] + receiver_height_m

        if beam_height_now <= terrain_target and beam_height_prev > terrain_prev:
            # Linear interpolation between the two flanking samples.
            d0 = profile.distances_m[index - 1]
            d1 = profile.distances_m[index]
            beam_delta = (beam_height_now - terrain_target) - (beam_height_prev - terrain_prev)
            if beam_delta == 0.0:
                ratio = 0.0
            else:
                ratio = (beam_height_prev - terrain_prev) / -beam_delta
            ratio = max(0.0, min(1.0, ratio))
            distance = d0 + (d1 - d0) * ratio
            lat0 = profile.latitudes[index - 1]
            lat1 = profile.latitudes[index]
            lon0 = profile.longitudes[index - 1]
            lon1 = profile.longitudes[index]
            elevation = profile.elevations_m[index - 1] + (profile.elevations_m[index] - profile.elevations_m[index - 1]) * ratio
            return BeamIntersection(
                label=label,
                distance_m=distance,
                latitude=lat0 + (lat1 - lat0) * ratio,
                longitude=lon0 + (lon1 - lon0) * ratio,
                elevation_m=elevation,
            )

    return BeamIntersection(label=label, distance_m=None, latitude=None, longitude=None, elevation_m=None)


def free_space_loss_db(distance_m: float, frequency_mhz: float) -> float:
    if distance_m <= 0.0 or frequency_mhz <= 0.0:
        return 0.0
    return 20.0 * math.log10(distance_m) + 20.0 * math.log10(frequency_mhz) - 27.55


def vertical_pattern_loss_db(angle_deg: float, beamwidth_deg: float, depth_db: float = 18.0) -> float:
    if beamwidth_deg <= 0.0:
        return depth_db
    ratio = (2.0 * angle_deg) / beamwidth_deg
    return min(depth_db, 12.0 * ratio * ratio)


def knife_edge_loss_db(clearance_m: float, distance_m: float, frequency_mhz: float) -> float:
    """Single knife-edge diffraction loss following ITU-R P.526.

    ``clearance_m`` is the beam height minus terrain height (positive = clear).
    """

    if distance_m <= 0.0 or frequency_mhz <= 0.0:
        return 0.0

    wavelength = SPEED_OF_LIGHT / (frequency_mhz * 1.0e6)
    if wavelength <= 0.0:
        return 0.0

    fresnel = math.sqrt(wavelength * distance_m / 2.0)
    if fresnel <= 0.0:
        return 0.0

    nu = -clearance_m / fresnel
    if nu < -0.78:
        return 0.0
    return 6.9 + 20.0 * math.log10(math.sqrt((nu - 0.1) ** 2 + 1.0) + nu - 0.1)


def estimate_rsrp_dbm(
    params: SiteParameters,
    distance_m: float,
    beam_clearance_m: float,
    elevation_angle_deg: float,
) -> float:
    eirp_dbm = params.tx_power_dbm + params.antenna_gain_dbi - params.cable_loss_db
    fspl = free_space_loss_db(distance_m, params.frequency_mhz)
    pattern = vertical_pattern_loss_db(elevation_angle_deg, params.vertical_beamwidth_deg)
    diffraction = knife_edge_loss_db(beam_clearance_m, max(distance_m, 1.0), params.frequency_mhz)
    # Subtract a small log-distance penalty (n=3.0) above 1 km to mimic urban
    # propagation without depending on a calibrated model.
    extra_path_loss = 0.0
    if distance_m > 1_000.0:
        extra_path_loss = 10.0 * (3.0 - 2.0) * math.log10(distance_m / 1_000.0)

    return eirp_dbm - fspl - pattern - diffraction - extra_path_loss


# ---------------------------------------------------------------------------
# High-level analysis pipeline
# ---------------------------------------------------------------------------


def analyse_coverage(params: SiteParameters, profile: Optional[TerrainProfile] = None) -> CoverageResult:
    if profile is None:
        profile = TerrainSampler().sample(params)

    if not profile.distances_m:
        raise ValueError("Terrain profile is empty; cannot run coverage analysis.")

    antenna_msl_m = profile.base_elevation_m + params.antenna_height_m
    half_vbw = params.vertical_beamwidth_deg / 2.0
    main_tilt = params.total_tilt_deg
    upper_tilt = main_tilt - half_vbw
    lower_tilt = main_tilt + half_vbw

    main_beam = build_beam_profile("Main Beam", antenna_msl_m, main_tilt, profile.distances_m)
    upper_beam = build_beam_profile("Upper Beam", antenna_msl_m, upper_tilt, profile.distances_m)
    lower_beam = build_beam_profile("Lower Beam", antenna_msl_m, lower_tilt, profile.distances_m)

    main_intersection = first_intersection("Main Beam Intersection", profile, main_beam, params.receiver_height_m)
    upper_intersection = first_intersection("Upper Intersection", profile, upper_beam, params.receiver_height_m)
    lower_intersection = first_intersection("Lower Intersection", profile, lower_beam, params.receiver_height_m)

    points: List[CoveragePoint] = []
    for index, distance in enumerate(profile.distances_m):
        if distance <= 0.0:
            continue
        terrain_msl = profile.elevations_m[index]
        beam_target = main_beam.heights_m[index]
        clearance = beam_target - (terrain_msl + params.receiver_height_m)
        elevation_angle = math.degrees(math.atan2(antenna_msl_m - (terrain_msl + params.receiver_height_m), distance))
        # angle relative to main beam
        offset_from_main = abs(elevation_angle - (-main_tilt))
        rsrp = estimate_rsrp_dbm(params, distance, clearance, offset_from_main)
        points.append(
            CoveragePoint(
                distance_m=distance,
                latitude=profile.latitudes[index],
                longitude=profile.longitudes[index],
                rsrp_dbm=rsrp,
                elevation_m=terrain_msl,
                beam_clearance_m=clearance,
            )
        )

    sector_polygon = build_sector_polygon(
        params.latitude,
        params.longitude,
        params.azimuth_deg,
        params.horizontal_beamwidth_deg,
        params.max_distance_m,
    )

    footprint_polygon = build_footprint_polygon(
        params.latitude,
        params.longitude,
        params.azimuth_deg,
        params.horizontal_beamwidth_deg,
        upper_intersection,
        lower_intersection,
        main_intersection,
        params.max_distance_m,
    )

    return CoverageResult(
        profile=profile,
        main_beam=main_beam,
        upper_beam=upper_beam,
        lower_beam=lower_beam,
        points=points,
        main_intersection=main_intersection,
        upper_intersection=upper_intersection,
        lower_intersection=lower_intersection,
        sector_polygon=sector_polygon,
        footprint_polygon=footprint_polygon,
    )


def build_sector_polygon(
    latitude: float,
    longitude: float,
    azimuth_deg: float,
    horizontal_beamwidth_deg: float,
    distance_m: float,
    arc_steps: int = 32,
) -> List[Tuple[float, float]]:
    """Pie-slice polygon around the antenna for the horizontal sector."""

    half = horizontal_beamwidth_deg / 2.0
    start = azimuth_deg - half
    stop = azimuth_deg + half

    polygon: List[Tuple[float, float]] = [(latitude, longitude)]
    for step in range(arc_steps + 1):
        bearing = start + (stop - start) * step / arc_steps
        polygon.append(destination_point(latitude, longitude, bearing, distance_m))
    polygon.append((latitude, longitude))
    return polygon


def build_footprint_polygon(
    latitude: float,
    longitude: float,
    azimuth_deg: float,
    horizontal_beamwidth_deg: float,
    upper_intersection: BeamIntersection,
    lower_intersection: BeamIntersection,
    main_intersection: BeamIntersection,
    fallback_distance_m: float,
    arc_steps: int = 24,
) -> List[Tuple[float, float]]:
    """Best-effort coverage footprint between the upper and lower beam ranges."""

    inner = upper_intersection.distance_m if upper_intersection.distance_m is not None else 0.0
    outer = lower_intersection.distance_m
    if outer is None:
        outer = main_intersection.distance_m if main_intersection.distance_m is not None else fallback_distance_m

    if outer is None or outer <= inner:
        outer = fallback_distance_m

    half = horizontal_beamwidth_deg / 2.0
    start = azimuth_deg - half
    stop = azimuth_deg + half

    polygon: List[Tuple[float, float]] = []

    # Outer arc
    for step in range(arc_steps + 1):
        bearing = start + (stop - start) * step / arc_steps
        polygon.append(destination_point(latitude, longitude, bearing, outer))

    # Inner arc, reversed, only if there is a meaningful inner radius
    if inner > 0.0:
        for step in range(arc_steps, -1, -1):
            bearing = start + (stop - start) * step / arc_steps
            polygon.append(destination_point(latitude, longitude, bearing, inner))
    else:
        polygon.append((latitude, longitude))

    polygon.append(polygon[0])
    return polygon


# ---------------------------------------------------------------------------
# Tilt optimiser
# ---------------------------------------------------------------------------


@dataclass
class TiltCandidate:
    mechanical_tilt_deg: float
    electrical_tilt_deg: float
    main_intersection_m: Optional[float]
    coverage_score: float


def optimise_tilt(
    base_params: SiteParameters,
    mechanical_range: Iterable[float] = tuple(i * 0.5 for i in range(0, 21)),  # 0..10 deg
    electrical_range: Iterable[float] = tuple(i * 0.5 for i in range(0, 21)),
    rsrp_threshold_dbm: float = -110.0,
    profile: Optional[TerrainProfile] = None,
) -> List[TiltCandidate]:
    """Sweep mechanical/electrical tilt and return candidates ranked by coverage.

    The terrain profile is sampled once and reused so the sweep stays cheap
    even when the user runs it interactively.
    """

    if profile is None:
        profile = TerrainSampler().sample(base_params)

    candidates: List[TiltCandidate] = []
    for mechanical in mechanical_range:
        for electrical in electrical_range:
            trial = SiteParameters(**{**base_params.__dict__})
            trial.mechanical_tilt_deg = float(mechanical)
            trial.electrical_tilt_deg = float(electrical)
            result = analyse_coverage(trial, profile=profile)

            covered = sum(1 for point in result.points if point.rsrp_dbm >= rsrp_threshold_dbm)
            score = covered / max(1, len(result.points))
            candidates.append(
                TiltCandidate(
                    mechanical_tilt_deg=trial.mechanical_tilt_deg,
                    electrical_tilt_deg=trial.electrical_tilt_deg,
                    main_intersection_m=result.main_intersection.distance_m,
                    coverage_score=score,
                )
            )

    candidates.sort(key=lambda candidate: candidate.coverage_score, reverse=True)
    return candidates
