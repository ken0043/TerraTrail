"""OpenStreetMap feature extraction via the Overpass API.

We deliberately keep the queries narrow:

* rivers:   waterway=river / waterway=stream (lines only)
* lakes:    natural=water (polygons only)
* cities:   place=city / place=town (points with a radius proxy)
* peaks:    natural=peak (points)

The Overpass API is a shared public resource; users with heavy workloads
should self-host or add caching in front of this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import numpy as np
import requests

from .config import OVERPASS_URL


OVERPASS_TIMEOUT = 60
HTTP_TIMEOUT = 90


@dataclass
class OSMFeatures:
    """Container for extracted geographic features in a bbox (WGS84)."""

    # Each line: Nx2 array (lon, lat) of a polyline
    rivers: List[np.ndarray] = field(default_factory=list)
    # Each polygon: Nx2 array of the outer ring
    lakes: List[np.ndarray] = field(default_factory=list)
    # Sea / large water bodies — same schema as `lakes` but intended for
    # natural=water + water=sea|bay|ocean or `place=sea`.
    seas: List[np.ndarray] = field(default_factory=list)
    # Buildings — outer rings of building footprints + height in metres.
    # Each entry: (polygon_np_array, height_m_or_None)
    buildings: List[Tuple[np.ndarray, Optional[float]]] = field(default_factory=list)
    # (lon, lat, population_estimate, name)
    cities: List[Tuple[float, float, int, str]] = field(default_factory=list)
    # (lon, lat, elevation_m_or_None, name)
    peaks: List[Tuple[float, float, Optional[float], str]] = field(default_factory=list)


def fetch_features(
    bbox: Tuple[float, float, float, float],
    include_rivers: bool = True,
    include_cities: bool = True,
    include_peaks: bool = True,
    include_buildings: bool = False,
    include_sea: bool = False,
) -> OSMFeatures:
    """Fetch all requested feature layers for a lon/lat bbox."""
    west, south, east, north = bbox
    bbox_str = f"{south},{west},{north},{east}"

    parts = [f'[out:json][timeout:{OVERPASS_TIMEOUT}];', "("]
    if include_rivers:
        parts.append(f'way["waterway"~"^(river|stream|canal)$"]({bbox_str});')
        parts.append(f'way["natural"="water"]({bbox_str});')
        parts.append(f'relation["natural"="water"]({bbox_str});')
    if include_sea:
        # Sea / bays / oceans expressed via natural=water + water=sea|bay|ocean
        # or via natural=coastline (lines) or place=sea|ocean.
        parts.append(
            f'way["natural"="water"]["water"~"^(sea|bay|ocean|strait|lagoon)$"]({bbox_str});'
        )
        parts.append(
            f'relation["natural"="water"]["water"~"^(sea|bay|ocean|strait|lagoon)$"]({bbox_str});'
        )
    if include_cities:
        parts.append(f'node["place"~"^(city|town|village)$"]({bbox_str});')
    if include_peaks:
        parts.append(f'node["natural"="peak"]({bbox_str});')
    if include_buildings:
        # Include both standalone buildings and building:parts.  Ignore
        # multipolygon relations (rare and expensive to resolve) — the
        # outer way of such a relation is usually tagged too.
        parts.append(f'way["building"]({bbox_str});')
    parts.append(");out body geom;")
    query = "".join(parts)

    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "TerraTrail/0.1"},
        )
    except requests.RequestException:
        return OSMFeatures()

    if not resp.ok:
        return OSMFeatures()

    data = resp.json()

    features = OSMFeatures()
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "way":
            geom = el.get("geometry")
            if not geom:
                continue
            coords = np.asarray([(p["lon"], p["lat"]) for p in geom], dtype=np.float64)
            if len(coords) < 2:
                continue
            if "building" in tags:
                if len(coords) < 4:
                    continue
                height = _parse_building_height(tags)
                features.buildings.append((coords, height))
            elif "waterway" in tags:
                features.rivers.append(coords)
            elif tags.get("natural") == "water":
                water_kind = tags.get("water", "")
                if water_kind in ("sea", "bay", "ocean", "strait", "lagoon"):
                    features.seas.append(coords)
                else:
                    features.lakes.append(coords)
        elif el["type"] == "node":
            lon = float(el["lon"])
            lat = float(el["lat"])
            if tags.get("place") in ("city", "town", "village"):
                pop_str = tags.get("population", "0")
                try:
                    pop = int(pop_str.replace(",", "").split()[0])
                except (ValueError, IndexError):
                    pop = 0
                name = tags.get("name", tags.get("name:en", ""))
                features.cities.append((lon, lat, pop, name))
            elif tags.get("natural") == "peak":
                ele_str = tags.get("ele")
                try:
                    ele = float(ele_str) if ele_str else None
                except ValueError:
                    ele = None
                name = tags.get("name", tags.get("name:en", ""))
                features.peaks.append((lon, lat, ele, name))
        elif el["type"] == "relation":
            kind = "lake"
            if tags.get("natural") == "water" and tags.get("water", "") in (
                "sea", "bay", "ocean", "strait", "lagoon"
            ):
                kind = "sea"
            for member in el.get("members", []):
                if member.get("role") == "outer" and member.get("geometry"):
                    coords = np.asarray(
                        [(p["lon"], p["lat"]) for p in member["geometry"]],
                        dtype=np.float64,
                    )
                    if len(coords) >= 3:
                        (features.seas if kind == "sea" else features.lakes).append(coords)

    return features


def _parse_building_height(tags: dict) -> Optional[float]:
    """Parse an OSM building's height from its tags, in metres.

    Supports either `height` (metres, optionally with unit suffix) or
    `building:levels` (number of floors, ~3 m per level).  Returns None
    when no usable value is present.
    """
    h = tags.get("height")
    if h:
        try:
            cleaned = (
                h.strip()
                .lower()
                .replace("m", "")
                .replace("meters", "")
                .replace("metre", "")
                .replace(",", ".")
                .strip()
            )
            return float(cleaned)
        except (ValueError, AttributeError):
            pass
    levels = tags.get("building:levels") or tags.get("levels")
    if levels:
        try:
            return float(str(levels).split(";")[0].strip()) * 3.0
        except ValueError:
            pass
    return None
