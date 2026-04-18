"""Offline smoke test that bypasses network calls.

Verifies that the mesh + export stages work end-to-end by monkey-patching
`fetch_dem` and `fetch_features` with synthetic data.  Run from the repo root:

    python tests/test_pipeline_offline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from terratrail import pipeline
from terratrail.elevation import DEM
from terratrail.osm import OSMFeatures
from terratrail.gpx_loader import load_gpx
from terratrail.config import GenerationOptions


def _synthetic_dem(bbox, prefer="auto", zoom=None):
    w, s, e, n = bbox
    ny, nx = 200, 200
    lons = np.linspace(w, e, nx)
    lats = np.linspace(n, s, ny)
    LON, LAT = np.meshgrid(lons, lats)
    # Dramatic terrain centered on bbox midpoint
    cx, cy = (w + e) / 2, (s + n) / 2
    dx = (LON - cx) / (e - w)
    dy = (LAT - cy) / (n - s)
    ridge = np.sin(np.radians(LON * 900)) * 60
    peak = 800 * np.exp(-(dx * dx + dy * dy) * 12)
    flank = 300 * np.exp(-((LON - cx - 0.008) ** 2 + (LAT - cy + 0.004) ** 2) * 50000)
    elev = 100 + peak + flank + ridge
    return DEM(elev=elev, west=w, south=s, east=e, north=n, source="synthetic")


def _synthetic_features(bbox, include_rivers=True, include_cities=True, include_peaks=True):
    w, s, e, n = bbox
    cx, cy = (w + e) / 2, (s + n) / 2
    features = OSMFeatures()
    if include_rivers:
        # River that stays inside
        lons = np.linspace(w + 0.002, e - 0.002, 20)
        lats = cy + 0.003 * np.sin((lons - w) * 400)
        features.rivers.append(np.column_stack([lons, lats]))
        # Out-of-bounds river extending far east (tests clipping)
        lons_long = np.linspace(cx, e + 0.05, 30)  # goes 0.05 deg past east edge
        lats_long = np.linspace(cy, n - 0.002, 30)
        features.rivers.append(np.column_stack([lons_long, lats_long]))
    if include_cities:
        features.cities.append((cx - 0.006, cy - 0.004, 15000, "Test Town"))
        features.cities.append((cx + 0.004, cy + 0.005, 5000, "Test Village"))
        # A city that is OUTSIDE the bbox — should be dropped by clipping.
        features.cities.append((e + 0.03, cy, 9000, "Outside Town"))
    if include_peaks:
        features.peaks.append((cx, cy, 899.0, "Test Peak"))
        features.peaks.append((e + 0.02, n + 0.02, 1200.0, "Outside Peak"))
    return features


def main():
    pipeline.fetch_dem = _synthetic_dem
    pipeline.fetch_features = _synthetic_features

    import sys as _sys
    gpx = "tanzawa_nabewariyama.gpx" if "--tanzawa" in _sys.argv else "takao.gpx"
    routes = load_gpx(ROOT / "examples" / gpx)
    print(f"Using sample: {gpx}")
    opts = GenerationOptions(
        size_mm=120.0,
        z_exaggeration=2.5,
        grid_resolution=140,
        base_thickness_mm=3.0,
    )
    result = pipeline.run_generation(
        routes=routes,
        options=opts,
        out_root=ROOT / "outputs",
        dem_source="auto",
        progress=lambda s, p: print(f"  [{p:3d}%] {s}"),
    )

    print("\nJob:", result["job_id"])
    print("3MF:", result["threemf"])
    print("ZIP:", result["zip"])
    print("Layers:")
    for L in result["manifest"]["layers"]:
        print(
            f"  {L['material']:<12} tris={L['triangle_count']:>8,}  stl={L['stl_file']}"
        )
    assert result["threemf"].exists()
    assert result["zip"].exists()
    print("\nAll files present. OK.")


if __name__ == "__main__":
    main()
