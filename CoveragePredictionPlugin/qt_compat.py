"""Compatibility shim for PyQt5 (QGIS 3.x) and PyQt6 (QGIS 4.x).

QGIS 4 ships with PyQt6, where every Qt enum was moved into its scoped
namespace (``Qt.Orientation.Horizontal`` instead of ``Qt.Horizontal``,
``QFrame.Shape.StyledPanel`` instead of ``QFrame.StyledPanel`` etc.). This
module exposes a small set of stable constants that resolve to the right
attribute on both PyQt5 and PyQt6 so the rest of the plugin can stay
version-agnostic.

It also resolves the matplotlib Qt backend, which was renamed from
``backend_qt5agg`` to ``backend_qtagg`` (still aliased on most recent
matplotlib versions, but not guaranteed in stripped-down Conda builds).
"""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAbstractItemView, QFrame, QSizePolicy


def _resolve(owner, scoped: str, fallback: str):
    """Return ``owner.<scoped>`` if it exists, otherwise ``owner.<fallback>``.

    PyQt6 exposes scoped enums (``Qt.Orientation``); PyQt5 exposes both the
    scoped *and* the flat names; very old builds expose only the flat name.
    """

    parts = scoped.split(".")
    target = owner
    try:
        for part in parts:
            target = getattr(target, part)
        return target
    except AttributeError:
        return getattr(owner, fallback)


# ---------------------------------------------------------------------------
# Qt enums
# ---------------------------------------------------------------------------

# Orientation
HORIZONTAL = _resolve(Qt, "Orientation.Horizontal", "Horizontal")
VERTICAL = _resolve(Qt, "Orientation.Vertical", "Vertical")

# Alignment
ALIGN_LEFT = _resolve(Qt, "AlignmentFlag.AlignLeft", "AlignLeft")
ALIGN_RIGHT = _resolve(Qt, "AlignmentFlag.AlignRight", "AlignRight")
ALIGN_CENTER = _resolve(Qt, "AlignmentFlag.AlignCenter", "AlignCenter")
ALIGN_TOP = _resolve(Qt, "AlignmentFlag.AlignTop", "AlignTop")
ALIGN_VCENTER = _resolve(Qt, "AlignmentFlag.AlignVCenter", "AlignVCenter")

# Aspect ratio / image transformation
KEEP_ASPECT_RATIO = _resolve(Qt, "AspectRatioMode.KeepAspectRatio", "KeepAspectRatio")
SMOOTH_TRANSFORMATION = _resolve(Qt, "TransformationMode.SmoothTransformation", "SmoothTransformation")

# Global colours
WHITE = _resolve(Qt, "GlobalColor.white", "white")


# ---------------------------------------------------------------------------
# Widget enums
# ---------------------------------------------------------------------------

QFRAME_STYLED_PANEL = _resolve(QFrame, "Shape.StyledPanel", "StyledPanel")

NO_EDIT_TRIGGERS = _resolve(QAbstractItemView, "EditTrigger.NoEditTriggers", "NoEditTriggers")
SELECT_ROWS = _resolve(QAbstractItemView, "SelectionBehavior.SelectRows", "SelectRows")
SINGLE_SELECTION = _resolve(QAbstractItemView, "SelectionMode.SingleSelection", "SingleSelection")

SIZE_POLICY_EXPANDING = _resolve(QSizePolicy, "Policy.Expanding", "Expanding")


# ---------------------------------------------------------------------------
# Matplotlib Qt backend
# ---------------------------------------------------------------------------

try:  # matplotlib >= 3.5
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas  # noqa: F401
except ImportError:  # pragma: no cover - PyQt5/older matplotlib
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas  # noqa: F401
