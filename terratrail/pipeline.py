"""High-level orchestration: route -> DEM -> features -> meshes -> files."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import math
import time
import uuid

import numpy as np

from .config import GenerationOptions
from .gpx_loader import Route, merge_bboxes
from .elevation import fetch_dem, DEM
from .osm import fetch_features, OSMFeatures
from .mesh import (
    Projection,
    MeshBundle,
    build_terrain,
    build_route,
    build_rivers,
    build_cities,
    build_peaks,
    build_frame,
    build_buildings,
    build_sea,
    compute_shape_polygon,
)
from .clip import clip_lines, clip_polygons, point_in_bbox
from .export import (
    export_individual_stls,
    export_3mf,
    export_combined_stl,
    export_colored_obj,
    build_manifest,
    zip_outputs,
)


def _clip_features_and_routes(features: OSMFeatures, routes, bbox):
    """Bbox-level clip (cheap, filters wildly-out-of-range geometry)."""
    features.rivers = clip_lines(features.rivers, bbox)
    features.lakes = clip_polygons(features.lakes, bbox)
    if hasattr(features, "seas"):
        features.seas = clip_polygons(features.seas, bbox)
    if hasattr(features, "buildings"):
        # Buildings are stored as (polygon, height) tuples — clip each one
        # individually so the height metadata is preserved.
        new_buildings = []
        for poly, h in features.buildings:
            clipped = clip_polygons([poly], bbox)
            for cp in clipped:
                new_buildings.append((cp, h))
        features.buildings = new_buildings
    features.cities = [c for c in features.cities if point_in_bbox(c[0], c[1], bbox)]
    features.peaks = [p for p in features.peaks if point_in_bbox(p[0], p[1], bbox)]

    clipped_routes = []
    for r in routes:
        pts2d = r.points[:, :2]
        segs = clip_lines([pts2d], bbox)
        for seg in segs:
            clipped_routes.append(type(r)(points=seg, name=r.name))
    return clipped_routes


def compute_auto_bbox(
    routes: List[Route],
    options: GenerationOptions,
) -> Tuple[float, float, float, float]:
    """Compute a lon/lat bbox centered on the route with enough margin so the
    ROUTE ALWAYS FITS inside the shape (after the frame is subtracted).

    Logic:
      1.  Start from the tight route bbox.
      2.  Convert the route's half-dimensions to kilometres at its own
          latitude (equirectangular).
      3.  Expand according to the shape's "inscription factor" — for shapes
          like circle / hexagon we need the bbox to contain the whole
          route's *diagonal* inside the inscribed area.
      4.  Add a frame-width allowance (converted back to lon/lat) so that
          shrinking by frame_width_mm still leaves the route comfortably
          inside.
      5.  Add a small aesthetic padding.
    """
    base = merge_bboxes(routes, pad=0.0)
    cx = (base[0] + base[2]) / 2.0
    cy = (base[1] + base[3]) / 2.0

    lat_rad = math.radians(cy)
    km_per_lon = 111.320 * math.cos(lat_rad)
    km_per_lat = 110.574

    rx_km = (base[2] - base[0]) * km_per_lon / 2.0
    ry_km = (base[3] - base[1]) * km_per_lat / 2.0

    # How much space the shape "wastes" around a bbox-inscribed rectangle.
    # 1.0 = shape already fits the bbox (rectangle/square/rounded).
    # Higher = we need to grow the bbox so the inscribed shape still holds
    # the route.  For circles & regular n-gons, the inscribed shape is a
    # disc of radius = half(smaller side).  A route with diagonal d needs
    # disc radius >= d/2 → half-side ≥ d/2.
    shape = (options.shape or "rectangle").lower()
    if shape in ("circle", "hexagon", "octagon"):
        route_radius_km = math.hypot(rx_km, ry_km)
        req_half = route_radius_km * 1.02  # 2% buffer
        rx_km = ry_km = req_half
    elif shape == "rounded":
        rx_km *= 1.05
        ry_km *= 1.05
    elif shape == "square":
        half = max(rx_km, ry_km)
        rx_km = ry_km = half

    # Frame margin in km — approximation: the ratio model_mm/terrain_km is
    # determined by the longer terrain axis, so frame_km ≈
    # frame_mm * longer_axis_km / size_mm.
    longer_km = max(rx_km, ry_km) * 2.0
    if options.frame_style == "none":
        frame_km = 0.0
    else:
        frame_km = options.frame_width_mm * longer_km / max(options.size_mm, 1.0)

    # Aesthetic padding (never less than 0.5 km).
    aesthetic_km = max(longer_km * 0.05, 0.5)

    tx_km = rx_km + frame_km + aesthetic_km
    ty_km = ry_km + frame_km + aesthetic_km

    # Preserve the square property after we added frame/aesthetic so circles
    # stay fully round and hexagons fully regular.
    if shape in ("square", "circle", "hexagon", "octagon"):
        s = max(tx_km, ty_km)
        tx_km = ty_km = s

    dlon = tx_km / km_per_lon
    dlat = ty_km / km_per_lat
    return (cx - dlon, cy - dlat, cx + dlon, cy + dlat)


def _route_stats(routes: List[Route]) -> Dict[str, float]:
    """Compute total distance (km) and elevation gain (m) across routes."""
    total_km = 0.0
    total_gain_m = 0.0
    min_ele = math.inf
    max_ele = -math.inf
    name = ""
    for r in routes:
        if not name:
            name = r.name or ""
        pts = r.points
        lons = pts[:, 0]
        lats = pts[:, 1]
        # Haversine per segment
        phi1 = np.radians(lats[:-1])
        phi2 = np.radians(lats[1:])
        dphi = phi2 - phi1
        dlam = np.radians(lons[1:] - lons[:-1])
        a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
        seg_km = 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        total_km += float(seg_km.sum())
        if r.has_elevation:
            ele = pts[:, 2]
            d = np.diff(ele)
            total_gain_m += float(d[d > 0].sum())
            min_ele = min(min_ele, float(ele.min()))
            max_ele = max(max_ele, float(ele.max()))
    return {
        "distance_km": round(total_km, 2),
        "elevation_gain_m": round(total_gain_m, 1),
        "elevation_min_m": None if min_ele == math.inf else round(min_ele, 0),
        "elevation_max_m": None if max_ele == -math.inf else round(max_ele, 0),
        "name": name,
    }


def run_generation(
    routes: List[Route],
    options: GenerationOptions,
    out_root: Path,
    job_id: Optional[str] = None,
    dem_source: str = "auto",
    progress=None,
    custom_bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Dict:
    def _tell(step: str, pct: int) -> None:
        if progress:
            try:
                progress(step, pct)
            except Exception:
                pass

    job_id = job_id or f"job-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    out_dir = Path(out_root) / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if not routes:
        raise ValueError("At least one route is required (GPX or manual coordinates).")

    _tell("Computing bounding box", 2)
    if custom_bbox is not None:
        bbox = tuple(custom_bbox)
    else:
        bbox = compute_auto_bbox(routes, options)
    stats = _route_stats(routes)

    _tell("Fetching terrain (DEM)", 10)
    dem = fetch_dem(bbox, prefer=dem_source)

    _tell("Fetching geographic features (OSM)", 30)
    features = fetch_features(
        bbox,
        include_rivers=options.include_rivers,
        include_cities=options.include_cities,
        include_peaks=options.include_peaks,
        include_buildings=getattr(options, "include_buildings", False),
        include_sea=getattr(options, "include_sea", False),
    )

    _tell("Clipping features to bbox", 38)
    routes = _clip_features_and_routes(features, routes, bbox)

    _tell("Building projection + shape", 42)
    proj = Projection(
        west=dem.west, south=dem.south, east=dem.east, north=dem.north,
        size_mm=options.size_mm,
    )
    shape_poly = compute_shape_polygon(options, proj)
    # Terrain polygon: we include a bit BEYOND the inner frame edge so that
    # the staircase of rectangular grid cells is hidden behind the frame
    # (which is a smooth extruded ring) rather than showing through as
    # visible jaggies next to the curved frame edges.
    if options.frame_style != "none":
        inner_shape = shape_poly.buffer(
            -options.frame_width_mm,
            join_style=1,
            resolution=32,
        )
        if inner_shape.is_empty or inner_shape.area < 1e-3:
            inner_shape = shape_poly
        # Terrain extends ~1/3 of the way into the frame area so the staircase
        # of boundary cells ends up hidden under the frame.
        terrain_shape = shape_poly.buffer(
            -options.frame_width_mm * 0.65,
            join_style=1,
            resolution=32,
        )
        if terrain_shape.is_empty or terrain_shape.area < 1e-3:
            terrain_shape = inner_shape
    else:
        inner_shape = shape_poly
        terrain_shape = shape_poly
    # Flatten any MultiPolygon to its biggest component.
    for var_name in ("inner_shape", "terrain_shape"):
        v = locals()[var_name]
        if hasattr(v, "geoms"):
            geoms = list(v.geoms)
            if geoms:
                locals()[var_name] = max(geoms, key=lambda g: g.area)

    _tell("Building terrain solid (per band)", 52)
    terrain_bundle, terrain_z = build_terrain(dem, options, proj, shape_poly=terrain_shape)

    _tell("Building route ribbon", 66)
    route_bundle = build_route(routes, dem, proj, options, terrain_z, shape_poly=inner_shape)

    _tell("Building rivers / lakes", 72)
    rivers_bundle = build_rivers(features, dem, proj, options, shape_poly=inner_shape)

    _tell("Building buildings", 76)
    buildings_bundle = build_buildings(features, dem, proj, options, shape_poly=inner_shape)

    _tell("Building sea areas", 80)
    sea_bundle = build_sea(features, dem, proj, options, shape_poly=inner_shape)

    _tell("Building city pads", 82)
    cities_bundle = build_cities(features, dem, proj, options, shape_poly=inner_shape)

    _tell("Building peak markers", 86)
    peaks_bundle = build_peaks(features, dem, proj, options, shape_poly=inner_shape)

    _tell("Building decorative frame", 89)
    terrain_top_mm = float(terrain_z.max()) if terrain_z.size else options.base_thickness_mm
    frame_bundle = build_frame(options, proj, shape_poly, terrain_top_mm)

    merged = MeshBundle(projection=proj)
    for b in (terrain_bundle, sea_bundle, rivers_bundle, buildings_bundle,
              route_bundle, cities_bundle, peaks_bundle, frame_bundle):
        for name, mesh in b.meshes.items():
            merged.add(name, mesh)
    merged.apply_color()

    _tell("Exporting STL files", 91)
    stl_files = export_individual_stls(merged, out_dir)
    combined_stl = export_combined_stl(merged, out_dir / "terratrail_all.stl")

    _tell("Exporting colored OBJ", 94)
    obj_path = export_colored_obj(merged, out_dir / "terratrail.obj")
    mtl_path = obj_path.with_suffix(".mtl")

    _tell("Exporting 3MF (optional)", 96)
    threemf = export_3mf(merged, out_dir / "terratrail.3mf")

    # Watertight / manifold summary — useful for debugging slicer errors.
    manifold_summary = {}
    for name, mesh in merged.meshes.items():
        try:
            manifold_summary[name] = {
                "is_watertight": bool(mesh.is_watertight),
                "is_winding_consistent": bool(mesh.is_winding_consistent),
                "euler_number": int(mesh.euler_number),
            }
        except Exception:
            manifold_summary[name] = {"is_watertight": None}

    manifest = build_manifest(merged, asdict(options))
    manifest["bbox"] = list(bbox)
    manifest["dem_source"] = dem.source
    manifest["projection"] = {
        "width_mm": round(proj.width_mm, 2),
        "height_mm": round(proj.height_mm, 2),
        "mm_per_km": round(proj.mm_per_km, 4),
    }
    manifest["feature_counts"] = {
        "rivers": len(features.rivers),
        "lakes": len(features.lakes),
        "seas": len(getattr(features, "seas", [])),
        "buildings": len(getattr(features, "buildings", [])),
        "cities": len(features.cities),
        "peaks": len(features.peaks),
    }
    manifest["route_stats"] = stats
    manifest["manifold"] = manifold_summary
    manifest["shape"] = options.shape
    manifest["frame_style"] = options.frame_style

    _tell("Packaging outputs", 98)
    zip_path = zip_outputs(
        [obj_path, mtl_path, *stl_files, combined_stl, threemf],
        out_dir / f"terratrail_{job_id}.zip",
        manifest,
    )

    _tell("Done", 100)
    return {
        "job_id": job_id,
        "out_dir": out_dir,
        "zip": zip_path,
        "obj": obj_path,
        "mtl": mtl_path,
        "threemf": threemf,
        "stl_files": [p for p in stl_files],
        "combined_stl": combined_stl,
        "manifest": manifest,
        "bbox": bbox,
        "dem_source": dem.source,
    }
