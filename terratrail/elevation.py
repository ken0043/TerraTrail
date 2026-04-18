"""DEM (elevation) fetching from GSI (Japan) and AWS Terrarium (global).

Both providers publish raster tiles.  We fetch the tiles that cover the
requested bbox, decode them into a single heightmap, then resample the
heightmap onto a user-chosen regular grid in the projected coordinate
system used for mesh generation.

Elevation decoding:
    - GSI dem_png:   e = (R*2^16 + G*2^8 + B) * u where u = 0.01 m, and
                     R=128,G=0,B=0 indicates "no data".
    - Terrarium:     e = R*256 + G + B/256 - 32768 (meters)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import io
import math
import time

import numpy as np
import requests
from PIL import Image

from .config import (
    GSI_DEM_URL,
    GSI_DEM_Z,
    TERRARIUM_URL,
    TERRARIUM_Z,
    in_japan,
)

TILE_PX = 256
HTTP_TIMEOUT = 20
MAX_TILES = 144  # safety cap (12x12) per request


# -----------------------------------------------------------------------------
# Web Mercator tile math
# -----------------------------------------------------------------------------

def lonlat_to_tile(lon: float, lat: float, z: int) -> Tuple[float, float]:
    """Return fractional tile (x, y) at zoom z."""
    lat_rad = math.radians(lat)
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def tile_to_lonlat(x: float, y: float, z: int) -> Tuple[float, float]:
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_rad)
    return lon, lat


# -----------------------------------------------------------------------------
# Tile fetching + decoding
# -----------------------------------------------------------------------------

def _fetch_png(url: str) -> Optional[Image.Image]:
    try:
        r = requests.get(
            url,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "TerraTrail/0.1 (hobbyist 3D printing)"},
        )
    except requests.RequestException:
        return None
    if r.status_code == 404:
        return None
    if not r.ok:
        return None
    if len(r.content) < 100:  # some servers return empty 200s for missing tiles
        return None
    try:
        img = Image.open(io.BytesIO(r.content))
        img.load()
        return img
    except Exception:
        return None


def decode_gsi(img: Image.Image) -> np.ndarray:
    arr = np.asarray(img.convert("RGB"), dtype=np.int64)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    # "No data" indicator per GSI spec
    nodata = (r == 128) & (g == 0) & (b == 0)
    # Two's-complement encoding: values above 2^23 are negative.
    x = r * 65536 + g * 256 + b
    mask_neg = x >= (1 << 23)
    x = x.astype(np.float64)
    x[mask_neg] = x[mask_neg] - (1 << 24)
    elev = x * 0.01
    elev[nodata] = np.nan
    return elev


def decode_terrarium(img: Image.Image) -> np.ndarray:
    arr = np.asarray(img.convert("RGB"), dtype=np.float64)
    return arr[..., 0] * 256.0 + arr[..., 1] + arr[..., 2] / 256.0 - 32768.0


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

@dataclass
class DEM:
    """A rectangular heightmap aligned to a lon/lat bbox."""

    elev: np.ndarray          # shape (H, W), meters, NaN for missing
    west: float
    south: float
    east: float
    north: float
    source: str               # "GSI" or "Terrarium"

    @property
    def shape(self) -> Tuple[int, int]:
        return self.elev.shape


def fetch_dem(
    bbox: Tuple[float, float, float, float],
    prefer: str = "auto",
    zoom: Optional[int] = None,
) -> DEM:
    """Fetch a DEM covering the given bbox.

    `prefer` is "auto" (GSI for Japan, Terrarium elsewhere), "gsi", or
    "terrarium".  Falls back to the other provider if the preferred one
    returns no data.
    """
    west, south, east, north = bbox
    cx, cy = (west + east) / 2, (south + north) / 2

    if prefer == "auto":
        source = "gsi" if in_japan(cx, cy) else "terrarium"
    else:
        source = prefer

    try:
        return _fetch_with_provider(bbox, source, zoom)
    except RuntimeError:
        # try the other provider as a fallback
        alt = "terrarium" if source == "gsi" else "gsi"
        return _fetch_with_provider(bbox, alt, zoom)


def _fetch_with_provider(
    bbox: Tuple[float, float, float, float],
    source: str,
    zoom: Optional[int],
) -> DEM:
    if source == "gsi":
        url_tmpl = GSI_DEM_URL
        z = zoom or GSI_DEM_Z
        decoder = decode_gsi
        label = "GSI"
    elif source == "terrarium":
        url_tmpl = TERRARIUM_URL
        z = zoom or TERRARIUM_Z
        decoder = decode_terrarium
        label = "Terrarium"
    else:
        raise ValueError(f"unknown DEM source: {source}")

    west, south, east, north = bbox

    x0, y0 = lonlat_to_tile(west, north, z)  # NW corner
    x1, y1 = lonlat_to_tile(east, south, z)  # SE corner
    tx0, tx1 = int(math.floor(x0)), int(math.floor(x1))
    ty0, ty1 = int(math.floor(y0)), int(math.floor(y1))

    n_tiles = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    if n_tiles > MAX_TILES:
        # Drop zoom to avoid hammering the server.
        while n_tiles > MAX_TILES and z > 8:
            z -= 1
            x0, y0 = lonlat_to_tile(west, north, z)
            x1, y1 = lonlat_to_tile(east, south, z)
            tx0, tx1 = int(math.floor(x0)), int(math.floor(x1))
            ty0, ty1 = int(math.floor(y0)), int(math.floor(y1))
            n_tiles = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)

    width_px = (tx1 - tx0 + 1) * TILE_PX
    height_px = (ty1 - ty0 + 1) * TILE_PX
    mosaic = np.full((height_px, width_px), np.nan, dtype=np.float64)

    any_ok = False
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            url = url_tmpl.format(z=z, x=tx, y=ty)
            img = _fetch_png(url)
            if img is None:
                continue
            try:
                arr = decoder(img)
            except Exception:
                continue
            px = (tx - tx0) * TILE_PX
            py = (ty - ty0) * TILE_PX
            mosaic[py:py + TILE_PX, px:px + TILE_PX] = arr
            any_ok = True
            time.sleep(0.02)  # be polite

    if not any_ok:
        raise RuntimeError(f"no tiles returned from {label}")

    # Bbox of the fetched mosaic in lon/lat.
    nw_lon, nw_lat = tile_to_lonlat(tx0, ty0, z)
    se_lon, se_lat = tile_to_lonlat(tx1 + 1, ty1 + 1, z)

    # Crop to the exact requested bbox.
    col0 = int(round((west - nw_lon) / (se_lon - nw_lon) * width_px))
    col1 = int(round((east - nw_lon) / (se_lon - nw_lon) * width_px))
    row0 = int(round((nw_lat - north) / (nw_lat - se_lat) * height_px))
    row1 = int(round((nw_lat - south) / (nw_lat - se_lat) * height_px))
    col0, col1 = max(0, col0), min(width_px, col1)
    row0, row1 = max(0, row0), min(height_px, row1)
    cropped = mosaic[row0:row1, col0:col1]

    # Fill NaNs with nearest-neighbour fill (very simple).
    if np.isnan(cropped).any():
        cropped = _fill_nan(cropped)

    return DEM(
        elev=cropped,
        west=west,
        south=south,
        east=east,
        north=north,
        source=label,
    )


def _fill_nan(arr: np.ndarray) -> np.ndarray:
    """Replace NaNs with the global mean (tiny missing patches only)."""
    if not np.isnan(arr).any():
        return arr
    mean = float(np.nanmean(arr)) if np.any(~np.isnan(arr)) else 0.0
    out = arr.copy()
    out[np.isnan(out)] = mean
    return out


def sample_elevation(dem: DEM, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Bilinear-sample elevation at the given lon/lat points."""
    H, W = dem.elev.shape
    fx = (lons - dem.west) / (dem.east - dem.west) * (W - 1)
    fy = (dem.north - lats) / (dem.north - dem.south) * (H - 1)
    fx = np.clip(fx, 0, W - 1)
    fy = np.clip(fy, 0, H - 1)
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    x1 = np.clip(x0 + 1, 0, W - 1)
    y1 = np.clip(y0 + 1, 0, H - 1)
    wx = fx - x0
    wy = fy - y0
    v = (
        dem.elev[y0, x0] * (1 - wx) * (1 - wy)
        + dem.elev[y0, x1] * wx * (1 - wy)
        + dem.elev[y1, x0] * (1 - wx) * wy
        + dem.elev[y1, x1] * wx * wy
    )
    return v
