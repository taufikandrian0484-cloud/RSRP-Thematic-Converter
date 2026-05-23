def classFactory(iface):
    from .coverage_prediction_plugin import CoveragePredictionPlugin

    return CoveragePredictionPlugin(iface)
