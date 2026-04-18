"""GPX file loading + manual route ingestion.

Produces a uniform `Route` object that the rest of the pipeline consumes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple, Optional
import io

import gpxpy
import gpxpy.gpx
import numpy as np


@dataclass
class Route:
    """A single polyline in WGS84 (longitude, latitude, optional elevation)."""

    # Nx2 or Nx3 numpy array of (lon, lat) or (lon, lat, elev).
    points: np.ndarray
    name: str = ""

    @property
    def has_elevation(self) -> bool:
        return self.points.shape[1] >= 3

    @property
    def lonlat(self) -> np.ndarray:
        return self.points[:, :2]

    def bbox(self, pad: float = 0.0) -> Tuple[float, float, float, float]:
        """Return (west, south, east, north) padded by `pad` degrees."""
        lons = self.points[:, 0]
        lats = self.points[:, 1]
        return (
            float(lons.min() - pad),
            float(lats.min() - pad),
            float(lons.max() + pad),
            float(lats.max() + pad),
        )


def load_gpx(file_or_bytes) -> List[Route]:
    """Parse a GPX file (path, file-like, or bytes) into a list of `Route`s."""
    import os
    from pathlib import Path as _Path

    if isinstance(file_or_bytes, (bytes, bytearray)):
        stream = io.BytesIO(file_or_bytes)
        gpx = gpxpy.parse(stream)
    elif isinstance(file_or_bytes, (str, os.PathLike)):
        with open(file_or_bytes, "r", encoding="utf-8") as f:
            gpx = gpxpy.parse(f)
    else:
        gpx = gpxpy.parse(file_or_bytes)

    routes: List[Route] = []

    # Tracks (recorded paths)
    for track in gpx.tracks:
        for i, seg in enumerate(track.segments):
            pts = _segment_points(seg.points)
            if len(pts) < 2:
                continue
            name = track.name or f"Track {i + 1}"
            routes.append(Route(points=pts, name=name))

    # Routes (planned paths)
    for r_idx, rte in enumerate(gpx.routes):
        pts = _segment_points(rte.points)
        if len(pts) < 2:
            continue
        routes.append(Route(points=pts, name=rte.name or f"Route {r_idx + 1}"))

    return routes


def _segment_points(pts: Sequence) -> np.ndarray:
    if not pts:
        return np.zeros((0, 2))
    data = []
    for p in pts:
        if p.elevation is not None:
            data.append((p.longitude, p.latitude, p.elevation))
        else:
            data.append((p.longitude, p.latitude))
    # If mixed elevation/no-elevation, drop elevation col for simplicity.
    if all(len(row) == 3 for row in data):
        return np.asarray(data, dtype=np.float64)
    return np.asarray([(row[0], row[1]) for row in data], dtype=np.float64)


def route_from_coords(coords: Sequence[Sequence[float]], name: str = "manual") -> Route:
    """Build a Route from a list of [lon, lat] (or [lon, lat, elev]) pairs."""
    arr = np.asarray(coords, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError("coords must be a list of [lon, lat] pairs")
    return Route(points=arr, name=name)


def resample_route(route: Route, spacing_deg: float = 0.0005) -> Route:
    """Linear-interpolate `route` to roughly uniform point spacing.

    `spacing_deg` is measured in WGS84 degrees (~55 m at the equator).  This
    gives smoother tube geometry without costing too much memory.
    """
    pts = route.points[:, :2]
    if len(pts) < 2:
        return route
    seg = np.diff(pts, axis=0)
    seg_len = np.linalg.norm(seg, axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = float(cum[-1])
    if total == 0:
        return route
    n = max(2, int(np.ceil(total / spacing_deg)) + 1)
    target = np.linspace(0, total, n)
    lon = np.interp(target, cum, pts[:, 0])
    lat = np.interp(target, cum, pts[:, 1])
    if route.has_elevation:
        ele = np.interp(target, cum, route.points[:, 2])
        out = np.column_stack([lon, lat, ele])
    else:
        out = np.column_stack([lon, lat])
    return Route(points=out, name=route.name)


def merge_bboxes(routes: List[Route], pad: float = 0.0) -> Tuple[float, float, float, float]:
    """Return the combined bbox (w, s, e, n) of several routes."""
    if not routes:
        raise ValueError("need at least one route")
    lons = np.concatenate([r.points[:, 0] for r in routes])
    lats = np.concatenate([r.points[:, 1] for r in routes])
    return (
        float(lons.min() - pad),
        float(lats.min() - pad),
        float(lons.max() + pad),
        float(lats.max() + pad),
    )
