# Coverage Prediction (QGIS Plugin)

QGIS 3.22+/4.x plugin yang meniru tampilan dan workflow
[propagationpredict.onrender.com](https://propagationpredict.onrender.com).

Plugin ini melakukan analisa coverage RF dengan terrain-aware beam analysis,
optimasi tilt antena, dan ekspor footprint ke KMZ untuk Google Earth.

## Layout dialog

Layout dirancang persis seperti screenshot referensi:

| Pane | Fungsi |
| ---- | ------ |
| Kiri (Basic RF / Tilt Optimizer) | Input parameter site, antena, beamwidth, max distance, DEM source, basemap, dan parameter optimasi tilt. |
| Kanan-atas (Terrain Analysis) | Profil elevasi sepanjang azimuth dengan kurva main / upper / lower beam dan koreksi kelengkungan bumi (k = 4/3). |
| Kanan-bawah (Coverage Map) | `QgsMapCanvas` yang merender sektor antena, footprint coverage, titik-titik prediksi RSRP, beam intersection, dan marker antena. Tombol Export to KMZ untuk membuat file KMZ. |

## Inti perhitungan

* `rf_core.py` — geodesi (`destination_point`, `haversine_distance`),
  sampling DEM Open-Meteo dengan fallback flat-terrain, beam height dengan
  koreksi 4/3 Earth radius, free-space loss + log-distance penalty + knife-edge
  diffraction (ITU-R P.526), dan tilt sweep optimiser.
* `coverage_layers.py` — builder layer QGIS (graduated point cloud, sektor,
  footprint, intersection markers, antenna marker).
* `kmz_exporter.py` — KMZ writer murni `xml` + `zipfile` (tanpa dependency
  eksternal seperti `simplekml`).
* `coverage_prediction_dialog.py` — UI Qt 3-pane dengan `QSplitter`,
  matplotlib `FigureCanvas`, dan `QgsMapCanvas`.
* `coverage_prediction_plugin.py` — entry point QGIS (menu/toolbar action),
  worker thread untuk analisa dan tilt sweep, integrasi dengan QGIS Project.

## Cara instalasi

1. Salin folder `CoveragePredictionPlugin/` ke direktori plugin QGIS:
   * Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   * macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
   * Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
2. Restart QGIS.
3. Buka menu **Plugins → Manage and Install Plugins**, aktifkan
   **Coverage Prediction** pada tab Installed.

## Cara pemakaian

1. Klik ikon Coverage Prediction di toolbar atau pilih dari menu
   **Plugins → Coverage Prediction**.
2. Isi koordinat site, azimuth, tinggi antena, tilt mekanis/elektris,
   beamwidth vertikal/horizontal, frekuensi, daya pancar, gain antena,
   loss feeder, dan tinggi penerima.
3. Atur **Max Distance** (slider) sesuai radius analisa yang diinginkan.
4. Pilih **DEM Source**:
   * `Open-Meteo (Online)` — sampling DEM via API Open-Meteo.
   * `Flat Terrain (Offline)` — gunakan jika koneksi internet tidak tersedia.
5. Klik **Run Analysis**. Profil terrain dan beam, plus footprint coverage,
   akan muncul beberapa detik kemudian.
6. (Opsional) Buka tab **Tilt Optimizer**, atur range mekanis & elektris,
   klik **Find optimal tilt**, lalu klik **Apply selected tilt** untuk
   mengisi nilai tilt terpilih ke tab Basic RF.
7. Klik **Export to KMZ** untuk menyimpan footprint, sektor, intersection,
   dan titik RSRP ke file KMZ yang bisa langsung dibuka di Google Earth.

## Catatan pengembangan

Kode propagasi sengaja dipertahankan QGIS-free (`rf_core.py`,
`kmz_exporter.py`) sehingga bisa dipakai sebagai library dari skrip lain atau
diuji unit-test di luar QGIS.
