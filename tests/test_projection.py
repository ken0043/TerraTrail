"""Sanity check for the Projection class."""
import sys
from pathlib import Path
import math

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from terratrail.mesh import Projection


def test_square_bbox_maps_to_square():
    # ~1 km wide bbox at equator
    proj = Projection(west=0.0, south=0.0, east=0.01, north=0.01, size_mm=100.0)
    # equirectangular preserves shape when dlon == dlat (close to equator)
    # y-axis uses km_per_lat = 110.574, x uses 111.32*cos(lat_c)
    # For centered bbox at lat_c=0.005: km_per_lon ≈ 111.32
    # So aspect ≈ 111.32/110.574 ≈ 1.007
    ratio = proj.width_mm / proj.height_mm
    assert abs(ratio - 111.32 / 110.574) < 0.01, f"unexpected ratio: {ratio}"

    # The longer axis should be 100mm (configured size)
    assert max(proj.width_mm, proj.height_mm) == 100.0
    print(f"  width_mm={proj.width_mm:.2f} height_mm={proj.height_mm:.2f} OK")


def test_projection_origin_at_sw():
    proj = Projection(west=138.0, south=35.0, east=138.2, north=35.2, size_mm=150.0)
    x, y = proj.project(138.0, 35.0)
    assert abs(float(x)) < 1e-6 and abs(float(y)) < 1e-6
    x, y = proj.project(138.2, 35.2)
    assert abs(float(x) - proj.width_mm) < 1e-4
    assert abs(float(y) - proj.height_mm) < 1e-4
    print(f"  SW=({x:.4f},{y:.4f}) end=({proj.width_mm:.2f},{proj.height_mm:.2f}) OK")


if __name__ == "__main__":
    test_square_bbox_maps_to_square()
    test_projection_origin_at_sw()
    print("All projection tests passed.")
