"""Command-line generation helper.

Usage:
    python cli.py --gpx my_hike.gpx --size 150 --z 2 --out ./outputs
    python cli.py --coords 138.70,35.35 138.78,35.37 138.80,35.42 --size 120

Primarily intended for quick smoke tests and headless usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from terratrail.config import GenerationOptions
from terratrail.gpx_loader import load_gpx, route_from_coords
from terratrail.pipeline import run_generation


def main():
    p = argparse.ArgumentParser(description="Generate a TerraTrail 3D miniature map")
    p.add_argument("--gpx", type=Path, help="GPX file input")
    p.add_argument(
        "--coords",
        nargs="+",
        help='Manual coordinates, each "lon,lat"',
    )
    p.add_argument("--out", type=Path, default=Path("./outputs"), help="Output root")
    p.add_argument("--dem", choices=["auto", "gsi", "terrarium"], default="auto")
    p.add_argument("--size", type=float, default=150.0, help="Longer axis in mm")
    p.add_argument("--z", type=float, default=2.0, help="Vertical exaggeration")
    p.add_argument("--grid", type=int, default=200, help="DEM grid resolution")
    p.add_argument("--route-width", type=float, default=1.2)
    p.add_argument("--route-height", type=float, default=1.0)
    p.add_argument("--no-rivers", action="store_true")
    p.add_argument("--no-cities", action="store_true")
    p.add_argument("--no-peaks", action="store_true")
    p.add_argument("--no-route", action="store_true")
    args = p.parse_args()

    routes = []
    if args.gpx:
        routes.extend(load_gpx(args.gpx))
    if args.coords:
        pts = []
        for s in args.coords:
            lon, lat = s.split(",")
            pts.append([float(lon), float(lat)])
        routes.append(route_from_coords(pts, name="cli"))
    if not routes:
        p.error("Provide --gpx or --coords")

    opts = GenerationOptions(
        size_mm=args.size,
        z_exaggeration=args.z,
        grid_resolution=args.grid,
        route_width_mm=args.route_width,
        route_height_mm=args.route_height,
        include_rivers=not args.no_rivers,
        include_cities=not args.no_cities,
        include_peaks=not args.no_peaks,
        include_route=not args.no_route,
    )

    def progress(step, pct):
        print(f"  [{pct:3d}%] {step}")

    result = run_generation(
        routes=routes,
        options=opts,
        out_root=args.out,
        dem_source=args.dem,
        progress=progress,
    )
    print("\n=== DONE ===")
    print(f"Job: {result['job_id']}")
    print(f"Output: {result['out_dir']}")
    print(f"3MF:    {result['threemf']}")
    print(f"ZIP:    {result['zip']}")
    print(f"DEM source: {result['dem_source']}")
    print("Layers:")
    for layer in result["manifest"]["layers"]:
        print(f"  - {layer['material']:<12} {layer['triangle_count']:>8,} tris  ({layer['stl_file']})")


if __name__ == "__main__":
    main()
