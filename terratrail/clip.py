"""Clip geographic features (lines, polygons, points) to a bbox in lon/lat.

Overpass returns the full geometry of any way that has *any* node inside
the query bbox — so rivers, roads, etc. often extend far past the edge of
the requested area.  We clip them to the exact DEM bounding box before
turning them into 3D meshes so that nothing sticks out of the printed
base plate.

We also apply a small safety clip to route lines (in case a GPX has
stray points outside the merged bbox).
"""
from __future__ import annotations

from typing import List, Tuple, Iterable

import numpy as np
from shapely.geometry import (
    LineString,
    MultiLineString,
    Polygon,
    MultiPolygon,
    box,
)
from shapely.ops import unary_union


BBox = Tuple[float, float, float, float]  # (west, south, east, north)


def _bbox_poly(bbox: BBox) -> Polygon:
    w, s, e, n = bbox
    return box(w, s, e, n)


def clip_lines(
    lines: Iterable[np.ndarray],
    bbox: BBox,
    min_points: int = 2,
) -> List[np.ndarray]:
    """Clip each polyline to `bbox` and return the pieces that remain.

    Input polylines may be 2D (lon, lat) or 3D (lon, lat, z).  Z is
    preserved along with XY clipping — Shapely interpolates Z at the
    clip intersections as a linear blend of the surrounding vertices.
    """
    region = _bbox_poly(bbox)
    out: List[np.ndarray] = []
    for line in lines:
        arr = np.asarray(line, dtype=np.float64)
        if arr.shape[0] < 2:
            continue
        try:
            ls = LineString(arr)
        except Exception:
            continue
        if not ls.is_valid or ls.is_empty:
            continue
        clipped = ls.intersection(region)
        if clipped.is_empty:
            continue
        for part in _iter_lines(clipped):
            coords = np.asarray(part.coords, dtype=np.float64)
            if len(coords) >= min_points:
                out.append(coords)
    return out


def clip_polygons(
    polygons: Iterable[np.ndarray],
    bbox: BBox,
) -> List[np.ndarray]:
    """Clip each polygon outer ring to `bbox`."""
    region = _bbox_poly(bbox)
    out: List[np.ndarray] = []
    for ring in polygons:
        arr = np.asarray(ring, dtype=np.float64)
        if arr.shape[0] < 3:
            continue
        try:
            poly = Polygon(arr).buffer(0)  # fix self-intersections
        except Exception:
            continue
        if poly.is_empty:
            continue
        clipped = poly.intersection(region)
        if clipped.is_empty:
            continue
        for part in _iter_polygons(clipped):
            coords = np.asarray(part.exterior.coords, dtype=np.float64)
            if len(coords) >= 3:
                out.append(coords)
    return out


def point_in_bbox(lon: float, lat: float, bbox: BBox) -> bool:
    w, s, e, n = bbox
    return w <= lon <= e and s <= lat <= n


def _iter_lines(geom):
    if isinstance(geom, LineString):
        if not geom.is_empty:
            yield geom
    elif isinstance(geom, MultiLineString):
        for g in geom.geoms:
            if not g.is_empty:
                yield g
    else:
        # Could be GeometryCollection — yield any LineString children.
        for g in getattr(geom, "geoms", []):
            if isinstance(g, (LineString, MultiLineString)):
                yield from _iter_lines(g)


def _iter_polygons(geom):
    if isinstance(geom, Polygon):
        if not geom.is_empty:
            yield geom
    elif isinstance(geom, MultiPolygon):
        for g in geom.geoms:
            if not g.is_empty:
                yield g
    else:
        for g in getattr(geom, "geoms", []):
            if isinstance(g, (Polygon, MultiPolygon)):
                yield from _iter_polygons(g)
