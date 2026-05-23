"""Plugin entry point for Coverage Prediction.

This class registers the QGIS menu/toolbar action, creates the dialog on
demand, and ties the ``rf_core`` analysis pipeline to the dialog UI as well as
to the QGIS Project. All long-running work (terrain sampling, tilt sweep) is
performed in a :class:`QThread` so the QGIS main window never freezes.
"""

from __future__ import annotations

import os
from typing import List, Optional

from qgis.PyQt.QtCore import QObject, Qt, QThread, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from qgis.core import QgsProject, QgsRectangle, QgsVectorLayer

from .about_dialog import AboutDialog
from .coverage_layers import CoverageLayerBundle, build_layer_bundle
from .coverage_prediction_dialog import CoveragePredictionDialog
from .kmz_exporter import export_coverage_to_kmz
from .rf_core import (
    CoverageResult,
    SiteParameters,
    TerrainSampler,
    analyse_coverage,
    optimise_tilt,
)


PLUGIN_MENU = "&Coverage Prediction"


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


class _CoverageWorker(QObject):
    """Runs ``analyse_coverage`` on a worker thread."""

    finished = pyqtSignal(object, object)  # result, error
    progress = pyqtSignal(str)

    def __init__(self, params: SiteParameters) -> None:
        super().__init__()
        self.params = params

    def run(self) -> None:
        try:
            self.progress.emit("Sampling terrain elevation...")
            profile = TerrainSampler().sample(self.params)
            self.progress.emit("Computing beam profile and link budget...")
            result = analyse_coverage(self.params, profile=profile)
            self.finished.emit(result, None)
        except Exception as exc:  # pragma: no cover - QGIS UI safeguard
            self.finished.emit(None, exc)


class _TiltWorker(QObject):
    finished = pyqtSignal(object, object)  # candidates, error
    progress = pyqtSignal(str)

    def __init__(self, params: SiteParameters, bounds: dict) -> None:
        super().__init__()
        self.params = params
        self.bounds = bounds

    def run(self) -> None:
        try:
            self.progress.emit("Sampling terrain for tilt sweep...")
            profile = TerrainSampler().sample(self.params)
            self.progress.emit("Sweeping mechanical and electrical tilt...")
            mech_range = _frange(self.bounds["mech_min"], self.bounds["mech_max"], self.bounds["step"])
            elec_range = _frange(self.bounds["elec_min"], self.bounds["elec_max"], self.bounds["step"])
            candidates = optimise_tilt(
                self.params,
                mechanical_range=mech_range,
                electrical_range=elec_range,
                rsrp_threshold_dbm=self.bounds["threshold"],
                profile=profile,
            )
            self.finished.emit(candidates, None)
        except Exception as exc:  # pragma: no cover
            self.finished.emit(None, exc)


def _frange(start: float, stop: float, step: float) -> List[float]:
    if step <= 0:
        return [start]
    values: List[float] = []
    value = start
    # Tolerance protects against float drift when stop falls on a step.
    while value <= stop + 1e-9:
        values.append(round(value, 4))
        value += step
    return values


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class CoveragePredictionPlugin:
    """Main plugin class registered by ``classFactory``."""

    def __init__(self, iface) -> None:
        self.iface = iface
        self._action: Optional[QAction] = None
        self._about_action: Optional[QAction] = None
        self._dialog: Optional[CoveragePredictionDialog] = None
        self._latest_result: Optional[CoverageResult] = None
        self._latest_params: Optional[SiteParameters] = None
        self._latest_bundle: Optional[CoverageLayerBundle] = None
        self._coverage_thread: Optional[QThread] = None
        self._coverage_worker: Optional[_CoverageWorker] = None
        self._tilt_thread: Optional[QThread] = None
        self._tilt_worker: Optional[_TiltWorker] = None

        self._icon_dir = os.path.join(os.path.dirname(__file__), "resources", "icons")

    # ------------------------------------------------------------------
    # QGIS lifecycle
    # ------------------------------------------------------------------

    def initGui(self) -> None:
        self._action = QAction(
            self._load_icon("coverage_logo"),
            "Coverage Prediction",
            self.iface.mainWindow(),
        )
        self._action.triggered.connect(self.run)
        self.iface.addPluginToMenu(PLUGIN_MENU, self._action)
        self.iface.addToolBarIcon(self._action)

        self._about_action = QAction(
            self._load_icon("info"),
            "About Coverage Prediction",
            self.iface.mainWindow(),
        )
        self._about_action.triggered.connect(self.show_about)
        self.iface.addPluginToMenu(PLUGIN_MENU, self._about_action)

    def unload(self) -> None:
        if self._action is not None:
            self.iface.removePluginMenu(PLUGIN_MENU, self._action)
            self.iface.removeToolBarIcon(self._action)
            self._action = None
        if self._about_action is not None:
            self.iface.removePluginMenu(PLUGIN_MENU, self._about_action)
            self._about_action = None
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
            self._dialog = None
        self._stop_thread("_coverage_thread", "_coverage_worker")
        self._stop_thread("_tilt_thread", "_tilt_worker")

    def _load_icon(self, name: str) -> QIcon:
        for extension in (".svg", ".png"):
            candidate = os.path.join(self._icon_dir, f"{name}{extension}")
            if os.path.exists(candidate):
                return QIcon(candidate)
        return QIcon()

    # ------------------------------------------------------------------
    # Dialog wiring
    # ------------------------------------------------------------------

    def run(self) -> None:
        if self._dialog is None:
            self._dialog = CoveragePredictionDialog(self.iface.mainWindow())
            self._wire_dialog(self._dialog)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def show_about(self) -> None:
        AboutDialog(self.iface.mainWindow()).exec_()

    def _wire_dialog(self, dialog: CoveragePredictionDialog) -> None:
        dialog.run_requested.connect(self._on_run_clicked)
        dialog.reset_requested.connect(self._on_reset_clicked)
        dialog.export_kmz_requested.connect(self._on_export_clicked)
        dialog.optimise_tilt_requested.connect(self._on_optimise_clicked)
        dialog.apply_tilt_candidate_requested.connect(self._on_apply_tilt_candidate)
        dialog.reset_view_requested.connect(self._on_reset_view_clicked)

    # ------------------------------------------------------------------
    # Run analysis
    # ------------------------------------------------------------------

    def _on_run_clicked(self) -> None:
        if self._dialog is None:
            return
        params = self._dialog.get_site_parameters()
        if params.max_distance_m <= 0.0:
            QMessageBox.warning(
                self._dialog,
                "Invalid analysis range",
                "Max distance must be greater than zero.",
            )
            return

        self._dialog.set_run_enabled(False)
        self._dialog.set_export_enabled(False)
        self._dialog.set_status("Running coverage analysis...", level="info")

        self._coverage_thread = QThread(self._dialog)
        self._coverage_worker = _CoverageWorker(params)
        self._coverage_worker.moveToThread(self._coverage_thread)
        self._coverage_thread.started.connect(self._coverage_worker.run)
        self._coverage_worker.progress.connect(lambda message: self._dialog.set_status(message, level="info"))
        self._coverage_worker.finished.connect(lambda result, error: self._on_coverage_finished(params, result, error))
        self._coverage_thread.start()

    def _on_coverage_finished(
        self,
        params: SiteParameters,
        result: Optional[CoverageResult],
        error: Optional[Exception],
    ) -> None:
        self._stop_thread("_coverage_thread", "_coverage_worker")
        if self._dialog is None:
            return

        self._dialog.set_run_enabled(True)
        if error is not None or result is None:
            message = str(error) if error is not None else "Unknown analysis error."
            self._dialog.set_status(f"Analysis failed: {message}", level="error")
            QMessageBox.critical(self._dialog, "Coverage Prediction", message)
            return

        self._latest_result = result
        self._latest_params = params

        self._dialog.update_terrain_chart(result, unit_label="m")
        self._refresh_layers(params, result)
        self._update_intersection_summary(result)
        self._dialog.set_export_enabled(True)
        self._dialog.set_status(
            f"Analysis complete. DEM source: {result.profile.source}.",
            level="success",
        )

    def _refresh_layers(self, params: SiteParameters, result: CoverageResult) -> None:
        if self._dialog is None:
            return

        # Remove previous QGIS project layers built by the plugin so we don't
        # accumulate clutter on repeated runs.
        if self._latest_bundle is not None:
            project = QgsProject.instance()
            for layer in self._latest_bundle.all_layers():
                if layer is not None and project.mapLayer(layer.id()) is not None:
                    project.removeMapLayer(layer.id())

        bundle = build_layer_bundle(result, layer_prefix="Coverage Prediction")
        self._latest_bundle = bundle

        project = QgsProject.instance()
        project.addMapLayers(bundle.all_layers(), addToLegend=True)

        # Render the same layers in the embedded canvas so the dialog matches
        # the screenshot exactly.
        self._dialog.map_canvas.setLayers(bundle.all_layers())
        extent = self._compute_canvas_extent(params, result, bundle)
        self._dialog.map_canvas.setExtent(extent)
        self._dialog.map_canvas.refresh()

    def _compute_canvas_extent(
        self,
        params: SiteParameters,
        result: CoverageResult,
        bundle: CoverageLayerBundle,
    ) -> QgsRectangle:
        extent = QgsRectangle()
        extent.setMinimal()
        for layer in bundle.all_layers():
            if isinstance(layer, QgsVectorLayer) and layer.featureCount() > 0:
                if extent.isEmpty():
                    extent = QgsRectangle(layer.extent())
                else:
                    extent.combineExtentWith(layer.extent())

        if extent.isEmpty() or extent.width() == 0.0 or extent.height() == 0.0:
            # Fallback to a small box around the antenna.
            buffer_deg = max(params.max_distance_m, 500.0) / 111_000.0
            extent = QgsRectangle(
                params.longitude - buffer_deg,
                params.latitude - buffer_deg,
                params.longitude + buffer_deg,
                params.latitude + buffer_deg,
            )
        else:
            extent.scale(1.15)
        return extent

    def _update_intersection_summary(self, result: CoverageResult) -> None:
        if self._dialog is None:
            return
        main_distance = result.main_intersection.distance_m
        if result.lower_intersection.distance_m is not None and result.upper_intersection.distance_m is not None:
            footprint_label = (
                f"{result.upper_intersection.distance_m:.0f} - "
                f"{result.lower_intersection.distance_m:.0f} m"
            )
        elif main_distance is not None:
            footprint_label = f"~{main_distance:.0f} m"
        else:
            footprint_label = None
        self._dialog.set_intersection_summary(main_distance, footprint_label)

    # ------------------------------------------------------------------
    # Reset / view actions
    # ------------------------------------------------------------------

    def _on_reset_clicked(self) -> None:
        if self._dialog is None:
            return
        if self._latest_bundle is not None:
            project = QgsProject.instance()
            for layer in self._latest_bundle.all_layers():
                if layer is not None and project.mapLayer(layer.id()) is not None:
                    project.removeMapLayer(layer.id())
            self._latest_bundle = None
        self._dialog.map_canvas.setLayers([])
        self._dialog.map_canvas.refresh()
        self._latest_result = None
        self._latest_params = None
        self._dialog.set_status("Inputs reset", level="info")

    def _on_reset_view_clicked(self) -> None:
        if self._dialog is None or self._latest_params is None or self._latest_result is None:
            return
        if self._latest_bundle is None:
            return
        extent = self._compute_canvas_extent(self._latest_params, self._latest_result, self._latest_bundle)
        self._dialog.map_canvas.setExtent(extent)
        self._dialog.map_canvas.refresh()
        # Re-fit terrain chart axes too.
        self._dialog.update_terrain_chart(self._latest_result, unit_label="m")

    # ------------------------------------------------------------------
    # Tilt optimiser
    # ------------------------------------------------------------------

    def _on_optimise_clicked(self) -> None:
        if self._dialog is None:
            return
        params = self._dialog.get_site_parameters()
        bounds = self._dialog.get_tilt_search_bounds()
        if bounds["mech_max"] < bounds["mech_min"] or bounds["elec_max"] < bounds["elec_min"]:
            QMessageBox.warning(
                self._dialog,
                "Invalid tilt range",
                "Tilt maxima must be greater than or equal to the minima.",
            )
            return

        self._dialog.set_run_enabled(False)
        self._dialog.set_status("Searching optimal tilt combinations...", level="info")

        self._tilt_thread = QThread(self._dialog)
        self._tilt_worker = _TiltWorker(params, bounds)
        self._tilt_worker.moveToThread(self._tilt_thread)
        self._tilt_thread.started.connect(self._tilt_worker.run)
        self._tilt_worker.progress.connect(lambda message: self._dialog.set_status(message, level="info"))
        self._tilt_worker.finished.connect(self._on_tilt_finished)
        self._tilt_thread.start()

    def _on_tilt_finished(self, candidates, error) -> None:
        self._stop_thread("_tilt_thread", "_tilt_worker")
        if self._dialog is None:
            return
        self._dialog.set_run_enabled(True)
        if error is not None:
            self._dialog.set_status(f"Tilt sweep failed: {error}", level="error")
            QMessageBox.critical(self._dialog, "Coverage Prediction", str(error))
            return
        self._dialog.populate_tilt_candidates(candidates)
        if candidates:
            best = candidates[0]
            self._dialog.set_status(
                f"Best tilt: mechanical {best.mechanical_tilt_deg:.1f}\u00b0, "
                f"electrical {best.electrical_tilt_deg:.1f}\u00b0 "
                f"(coverage {best.coverage_score * 100:.1f}%).",
                level="success",
            )
        else:
            self._dialog.set_status("Tilt sweep returned no candidates.", level="warning")

    def _on_apply_tilt_candidate(self, mechanical: float, electrical: float) -> None:
        if self._dialog is None:
            return
        self._dialog.apply_tilt_values(mechanical, electrical)
        self._dialog.set_status(
            f"Applied tilt: mechanical {mechanical:.1f}\u00b0, electrical {electrical:.1f}\u00b0."
            " Press Run Analysis to recompute coverage.",
            level="info",
        )

    # ------------------------------------------------------------------
    # KMZ export
    # ------------------------------------------------------------------

    def _on_export_clicked(self, output_path: str) -> None:
        if self._dialog is None or self._latest_result is None or self._latest_params is None:
            return
        try:
            written = export_coverage_to_kmz(
                self._latest_result,
                self._latest_params,
                output_path,
            )
        except Exception as exc:
            self._dialog.set_status(f"KMZ export failed: {exc}", level="error")
            QMessageBox.critical(self._dialog, "Coverage Prediction", str(exc))
            return
        self._dialog.set_status(f"KMZ exported to {written}", level="success")
        self.iface.messageBar().pushSuccess("Coverage Prediction", f"KMZ saved to {written}")

    # ------------------------------------------------------------------
    # Thread helpers
    # ------------------------------------------------------------------

    def _stop_thread(self, thread_attr: str, worker_attr: str) -> None:
        thread = getattr(self, thread_attr, None)
        worker = getattr(self, worker_attr, None)
        if thread is not None:
            try:
                thread.quit()
                thread.wait(1500)
            except RuntimeError:
                pass
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()
        setattr(self, thread_attr, None)
        setattr(self, worker_attr, None)
