"""Mesh construction from DEM + routes + OSM features.

All meshes are produced in a local Cartesian frame measured in
millimeters.  The DEM lon/lat bbox is projected with an equirectangular
mapping centered on the DEM midpoint (good enough for the few-to-tens-of-km
scale that fits on a 3D printer); the longer axis is then scaled to match
the user's requested `size_mm`.

The terrain is built as **three closed solids** (one per elevation band:
base / mid / peak).  Each solid runs from `z=0` up to the terrain surface
for its cells, with vertical walls on any cell boundary where the
neighbouring cell is in a different band or outside the shape.  This
guarantees that every material is a watertight manifold — a requirement
for slicers like Bambu Studio that reject "open" (non-manifold) meshes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import math
import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon, Point, MultiPolygon
from shapely.vectorized import contains as shapely_contains

from .config import MATERIALS, GenerationOptions
from .elevation import DEM, sample_elevation
from .gpx_loader import Route, resample_route
from .osm import OSMFeatures


# =============================================================================
# Projection
# =============================================================================

@dataclass
class Projection:
    """Equirectangular projection from lon/lat to mm on the final model."""

    west: float
    south: float
    east: float
    north: float
    size_mm: float

    _mm_per_lon: float = 0.0
    _mm_per_lat: float = 0.0
    _lon_span_km: float = 0.0
    _lat_span_km: float = 0.0

    def __post_init__(self):
        lat_c = math.radians((self.north + self.south) / 2.0)
        km_per_lon = 111.320 * math.cos(lat_c)
        km_per_lat = 110.574
        self._lon_span_km = (self.east - self.west) * km_per_lon
        self._lat_span_km = (self.north - self.south) * km_per_lat
        longer_km = max(self._lon_span_km, self._lat_span_km, 1e-6)
        mm_per_km = self.size_mm / longer_km
        self._mm_per_lon = km_per_lon * mm_per_km
        self._mm_per_lat = km_per_lat * mm_per_km

    @property
    def width_mm(self) -> float:
        longer_km = max(self._lon_span_km, self._lat_span_km, 1e-6)
        return self._lon_span_km * self.size_mm / longer_km

    @property
    def height_mm(self) -> float:
        longer_km = max(self._lon_span_km, self._lat_span_km, 1e-6)
        return self._lat_span_km * self.size_mm / longer_km

    @property
    def mm_per_km(self) -> float:
        longer_km = max(self._lon_span_km, self._lat_span_km, 1e-6)
        return self.size_mm / longer_km

    def project(self, lon, lat):
        x = (np.asarray(lon) - self.west) * self._mm_per_lon
        y = (np.asarray(lat) - self.south) * self._mm_per_lat
        return x, y

    def unproject(self, x_mm, y_mm):
        """Inverse of `project` — converts mm back to lon/lat."""
        lon = self.west + np.asarray(x_mm) / self._mm_per_lon
        lat = self.south + np.asarray(y_mm) / self._mm_per_lat
        return lon, lat


# =============================================================================
# Mesh bundle (one Trimesh per material name)
# =============================================================================

@dataclass
class MeshBundle:
    meshes: Dict[str, trimesh.Trimesh] = field(default_factory=dict)
    projection: Optional[Projection] = None

    def add(self, material: str, mesh: trimesh.Trimesh) -> None:
        if mesh is None or len(mesh.faces) == 0:
            return
        if material in self.meshes:
            self.meshes[material] = trimesh.util.concatenate(
                [self.meshes[material], mesh]
            )
        else:
            self.meshes[material] = mesh

    def apply_color(self) -> None:
        for name, m in self.meshes.items():
            if name in MATERIALS:
                rgb, _slot, _desc = MATERIALS[name]
                m.visual.face_colors = np.tile(
                    np.array([rgb[0], rgb[1], rgb[2], 255], dtype=np.uint8),
                    (len(m.faces), 1),
                )

    def combined(self) -> trimesh.Trimesh:
        if not self.meshes:
            return trimesh.Trimesh()
        return trimesh.util.concatenate(list(self.meshes.values()))

    def to_scene(self) -> trimesh.Scene:
        scene = trimesh.Scene()
        for name, mesh in self.meshes.items():
            scene.add_geometry(mesh, node_name=name, geom_name=name)
        return scene


# =============================================================================
# Shape polygon
# =============================================================================

def compute_shape_polygon(opts: GenerationOptions, proj: Projection) -> Polygon:
    """Return the outer shape of the map as a Shapely polygon in mm coords."""
    w, h = proj.width_mm, proj.height_mm
    cx, cy = w / 2.0, h / 2.0
    shape = (opts.shape or "rectangle").lower()

    if shape == "rectangle":
        return Polygon([(0, 0), (w, 0), (w, h), (0, h)])

    if shape == "square":
        s = min(w, h)
        x0, y0 = cx - s / 2, cy - s / 2
        return Polygon([(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)])

    if shape == "circle":
        r = min(w, h) / 2.0
        ang = np.linspace(0, 2 * math.pi, 96, endpoint=False)
        pts = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in ang]
        return Polygon(pts)

    if shape == "rounded":
        s_min = min(w, h)
        radius = s_min * 0.12
        rect = Polygon([(0, 0), (w, 0), (w, h), (0, h)])
        # buffer(-r).buffer(r) rounds the corners.
        rounded = rect.buffer(-radius, join_style=1).buffer(
            radius, join_style=1, resolution=16
        )
        if rounded.is_empty:
            return rect
        return rounded if isinstance(rounded, Polygon) else rounded.geoms[0]

    if shape == "hexagon":
        r = min(w, h) / 2.0 * 1.05  # fatten slightly so hex fits the min axis
        # Pointy-top hexagon so it looks natural on a landscape frame.
        ang = np.linspace(0, 2 * math.pi, 6, endpoint=False) + math.pi / 2
        pts = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in ang]
        return Polygon(pts)

    if shape == "octagon":
        r = min(w, h) / 2.0 * 1.04
        ang = np.linspace(0, 2 * math.pi, 8, endpoint=False) + math.pi / 8
        pts = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in ang]
        return Polygon(pts)

    # Fallback: rectangle.
    return Polygon([(0, 0), (w, 0), (w, h), (0, h)])


# =============================================================================
# Terrain (three closed solids, one per elevation band)
# =============================================================================

def build_terrain(
    dem: DEM,
    opts: GenerationOptions,
    proj: Projection,
    shape_poly: Optional[Polygon] = None,
) -> Tuple[MeshBundle, np.ndarray]:
    """Build terrain meshes based on `opts.model_mode` and return the z grid.

    Two modes:

    * "layered" — one watertight solid per elevation band (base/mid/peak),
      each running from z=0 all the way up to the local terrain surface.
      Produces strong geological-style colour banding along the full
      vertical extent of the model.  Slicing is simple but every layer
      between z=0 and the peak height may contain multiple colours,
      which means the H2C has to change filament on almost every layer.

    * "single"  — one watertight main body (coloured `base`) that extends
      from z=0 to *just below* the surface, plus thin coloured caps that
      occupy the final `surface_color_depth_mm` of the top.  Bambu Studio
      only has to switch filament near the surface, which can cut print
      time on multi-material prints by 50-80%.
    """
    nx = int(opts.grid_resolution)
    ny = max(2, int(round(nx * proj.height_mm / proj.width_mm)))

    lons = np.linspace(dem.west, dem.east, nx)
    lats = np.linspace(dem.north, dem.south, ny)
    LON, LAT = np.meshgrid(lons, lats)

    elev = sample_elevation(dem, LON.ravel(), LAT.ravel()).reshape(ny, nx)
    min_e = float(np.nanmin(elev))
    max_e = float(np.nanmax(elev))
    if max_e - min_e < 1e-6:
        max_e = min_e + 1.0
    rel = (elev - min_e) / (max_e - min_e)

    vertical_range_mm = (max_e - min_e) / 1000.0 * proj.mm_per_km * opts.z_exaggeration
    vertical_range_mm = max(vertical_range_mm, 2.0)

    z_grid = rel * vertical_range_mm + opts.base_thickness_mm  # (ny, nx)

    X, Y = proj.project(LON, LAT)

    # Per-cell elevation band
    cell_rel = (
        rel[:-1, :-1] + rel[:-1, 1:] + rel[1:, :-1] + rel[1:, 1:]
    ) / 4.0
    band = np.zeros(cell_rel.shape, dtype=np.int8)  # 0=base, 1=mid, 2=peak
    band[cell_rel >= opts.band_mid_pct] = 1
    band[cell_rel >= opts.band_peak_pct] = 2

    # Shape mask (cell centers inside the shape)
    if shape_poly is not None and not shape_poly.is_empty:
        cell_cx = (X[:-1, :-1] + X[:-1, 1:] + X[1:, :-1] + X[1:, 1:]) / 4.0
        cell_cy = (Y[:-1, :-1] + Y[:-1, 1:] + Y[1:, :-1] + Y[1:, 1:]) / 4.0
        inside = shapely_contains(shape_poly, cell_cx, cell_cy)
        band[~inside] = -1

    bundle = MeshBundle(projection=proj)
    mode = (opts.model_mode or "layered").lower()

    if mode == "single":
        _build_terrain_single(bundle, band, z_grid, X, Y, opts)
    else:
        _build_terrain_layered(bundle, band, z_grid, X, Y)

    return bundle, z_grid


def _build_terrain_layered(
    bundle: MeshBundle,
    band: np.ndarray,
    z_grid: np.ndarray,
    X: np.ndarray,
    Y: np.ndarray,
) -> None:
    """3 separate full-height closed solids, one per band."""
    for band_val, mat_name in ((0, "base"), (1, "mid"), (2, "peak")):
        target = (band == band_val)
        mesh = _build_closed_solid(target, z_grid, 0.0, X, Y)
        if mesh is not None:
            bundle.add(mat_name, mesh)


def _build_terrain_single(
    bundle: MeshBundle,
    band: np.ndarray,
    z_grid: np.ndarray,
    X: np.ndarray,
    Y: np.ndarray,
    opts: GenerationOptions,
) -> None:
    """Horizontal-strata colouring: each material occupies a specific
    physical z-range instead of following per-cell band assignments.

    This gives the model a clean "geological strata" look — when sliced in
    cross-section the colours form perfectly horizontal bands at the same
    heights everywhere.  It is also much more efficient to print on the
    H2C: layers below the first stratum boundary are all a single colour,
    so the printer only changes filament at the two stratum transitions
    (plus any route/feature decorations).

    Layout:
        z = 0            .. band_mid_z   — `base`
        z = band_mid_z   .. band_peak_z  — `mid`   (only where terrain ≥ band_mid_z)
        z = band_peak_z  .. terrain      — `peak`  (only where terrain ≥ band_peak_z)
    """
    in_shape = band >= 0
    if not in_shape.any():
        return

    # Absolute z-thresholds (mm) for the colour strata.
    z_bottom = float(opts.base_thickness_mm)
    z_max = float(z_grid.max())
    if z_max - z_bottom < 1e-6:
        z_max = z_bottom + 2.0
    z_mid_thresh = z_bottom + opts.band_mid_pct * (z_max - z_bottom)
    z_peak_thresh = z_bottom + opts.band_peak_pct * (z_max - z_bottom)

    # Per-corner z values clamped to each stratum's ceiling.
    z_grid_cap_mid = np.minimum(z_grid, z_mid_thresh)
    z_grid_cap_peak = np.minimum(z_grid, z_peak_thresh)

    # Stratum 1 (base): every cell, from z=0 to z = min(terrain, mid_z).
    base_mesh = _build_closed_solid(in_shape, z_grid_cap_mid, 0.0, X, Y)
    if base_mesh is not None:
        bundle.add("base", base_mesh)

    # Cell mean z (used to decide which cells participate in mid/peak strata).
    cell_top = (
        z_grid[:-1, :-1] + z_grid[:-1, 1:] + z_grid[1:, :-1] + z_grid[1:, 1:]
    ) / 4.0

    # Stratum 2 (mid): cells where terrain pokes above `z_mid_thresh`.
    mid_cells = in_shape & (cell_top > z_mid_thresh + 1e-4)
    if mid_cells.any():
        mid_mesh = _build_closed_solid(mid_cells, z_grid_cap_peak, z_mid_thresh, X, Y)
        if mid_mesh is not None:
            bundle.add("mid", mid_mesh)

    # Stratum 3 (peak): cells where terrain pokes above `z_peak_thresh`.
    peak_cells = in_shape & (cell_top > z_peak_thresh + 1e-4)
    if peak_cells.any():
        peak_mesh = _build_closed_solid(peak_cells, z_grid, z_peak_thresh, X, Y)
        if peak_mesh is not None:
            bundle.add("peak", peak_mesh)


def _build_closed_solid(
    cells_mask: np.ndarray,
    z_top: "np.ndarray | float",
    z_bot: "np.ndarray | float",
    X: np.ndarray,
    Y: np.ndarray,
) -> Optional[trimesh.Trimesh]:
    """Build a watertight trimesh for every cell in `cells_mask`.

    Parameters
    ----------
    cells_mask : (ny-1, nx-1) bool
    z_top      : (ny, nx) float array OR scalar — z at the top of each corner
    z_bot      : (ny, nx) float array OR scalar — z at the bottom of each corner
    X, Y       : (ny, nx) float arrays in mm
    """
    ny_c, nx_c = cells_mask.shape
    target = cells_mask
    if not target.any():
        return None

    # Which grid corners are shared by any in-region cell?
    corner_used = np.zeros((ny_c + 1, nx_c + 1), dtype=bool)
    corner_used[:-1, :-1] |= target
    corner_used[:-1, 1:] |= target
    corner_used[1:, :-1] |= target
    corner_used[1:, 1:] |= target

    # Allocate top + bottom vertices for each used corner (vectorised).
    corner_top = np.full(corner_used.shape, -1, dtype=np.int64)
    corner_bot = np.full(corner_used.shape, -1, dtype=np.int64)
    idx_used = np.argwhere(corner_used)
    nV = idx_used.shape[0]
    if nV == 0:
        return None

    ys = idx_used[:, 0]
    xs = idx_used[:, 1]
    xs_mm = X[ys, xs]
    ys_mm = Y[ys, xs]
    zt = z_top[ys, xs] if hasattr(z_top, "shape") else np.full(nV, float(z_top))
    zb = z_bot[ys, xs] if hasattr(z_bot, "shape") else np.full(nV, float(z_bot))

    vertices = np.empty((nV * 2, 3), dtype=np.float64)
    vertices[0::2, 0] = xs_mm
    vertices[0::2, 1] = ys_mm
    vertices[0::2, 2] = zt
    vertices[1::2, 0] = xs_mm
    vertices[1::2, 1] = ys_mm
    vertices[1::2, 2] = zb

    corner_top[ys, xs] = np.arange(0, 2 * nV, 2)
    corner_bot[ys, xs] = np.arange(1, 2 * nV + 1, 2)

    # Identify "wall" cell edges: edge between this cell and a neighbour
    # whose band differs (or falls outside the grid).  Vectorised with
    # numpy so that a 200×200 grid doesn't bog down in Python loops.
    pad = np.full((ny_c + 2, nx_c + 2), False, dtype=bool)
    pad[1:-1, 1:-1] = target
    north_wall = target & ~pad[0:-2, 1:-1]  # neighbour above (cy-1)
    south_wall = target & ~pad[2:, 1:-1]    # neighbour below (cy+1)
    west_wall = target & ~pad[1:-1, 0:-2]   # neighbour left  (cx-1)
    east_wall = target & ~pad[1:-1, 2:]     # neighbour right (cx+1)

    # Build faces with numpy.  Each in-band cell contributes 4 tris
    # (2 top + 2 bottom) plus 2 tris per wall direction it emits.
    cy_idx, cx_idx = np.nonzero(target)

    tl_t = corner_top[cy_idx,     cx_idx]
    tr_t = corner_top[cy_idx,     cx_idx + 1]
    bl_t = corner_top[cy_idx + 1, cx_idx]
    br_t = corner_top[cy_idx + 1, cx_idx + 1]
    tl_b = corner_bot[cy_idx,     cx_idx]
    tr_b = corner_bot[cy_idx,     cx_idx + 1]
    bl_b = corner_bot[cy_idx + 1, cx_idx]
    br_b = corner_bot[cy_idx + 1, cx_idx + 1]

    top_faces = np.column_stack([tl_t, bl_t, br_t, tl_t, br_t, tr_t]).reshape(-1, 3)
    bot_faces = np.column_stack([tl_b, br_b, bl_b, tl_b, tr_b, br_b]).reshape(-1, 3)

    def _walls(cell_mask, quad):
        """Given a boolean mask over the cell grid and a 4-tuple of corner
        arrays (a_b, a_t, b_b, b_t), emit two triangles per True cell.

        The caller specifies the winding so that normals point outward.
        """
        if not cell_mask.any():
            return np.empty((0, 3), dtype=np.int64)
        sel = cell_mask[cy_idx, cx_idx]
        a_b, a_t, b_b, b_t = (q[sel] for q in quad)
        f1 = np.column_stack([a_b, b_b, b_t])
        f2 = np.column_stack([a_b, b_t, a_t])
        return np.vstack([f1, f2])

    # For each wall direction, pick the two in-plane corners and their
    # top/bottom counterparts in an order that produces outward normals.
    w_north = _walls(north_wall, (tl_b, tl_t, tr_b, tr_t))
    w_south = _walls(south_wall, (br_b, br_t, bl_b, bl_t))
    w_west  = _walls(west_wall,  (bl_b, bl_t, tl_b, tl_t))
    w_east  = _walls(east_wall,  (tr_b, tr_t, br_b, br_t))

    faces = np.vstack([top_faces, bot_faces, w_north, w_south, w_west, w_east])

    mesh = trimesh.Trimesh(
        vertices=vertices,
        faces=faces,
        process=False,
    )
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()

    # Band boundaries occasionally produce "islands" that are connected to
    # the rest of the solid only through a single shared vertex — that's a
    # textbook non-manifold edge.  fill_holes patches the tiny open loops;
    # if it still isn't watertight, fall back to process=True which will
    # rebuild adjacency using default tolerances.
    try:
        if not mesh.is_watertight:
            trimesh.repair.fill_holes(mesh)
        if not mesh.is_watertight:
            mesh = trimesh.Trimesh(
                vertices=mesh.vertices,
                faces=mesh.faces,
                process=True,
            )
            trimesh.repair.fill_holes(mesh)
        mesh.fix_normals()
    except Exception:
        pass
    return mesh


# =============================================================================
# Frame (decorative border + simple text pads)
# =============================================================================

def build_frame(
    opts: GenerationOptions,
    proj: Projection,
    shape_poly: Polygon,
    terrain_top_mm: float,
) -> MeshBundle:
    """Build a raised border around the map shape, using `frame` material."""
    bundle = MeshBundle(projection=proj)
    if opts.frame_style == "none" or shape_poly is None or shape_poly.is_empty:
        return bundle

    w = opts.frame_width_mm
    h = opts.frame_height_mm
    base = opts.base_thickness_mm

    style = opts.frame_style
    if style == "auto":
        # Pick "stepped" when there's enough room, otherwise "simple".
        style = "stepped" if w >= 5.0 else "simple"

    if style == "simple":
        _frame_simple(shape_poly, base, h, w, bundle)
    elif style == "stepped":
        _frame_stepped(shape_poly, base, h, w, bundle)
    else:
        _frame_simple(shape_poly, base, h, w, bundle)

    return bundle


def _frame_ring(outer: Polygon, inset: float):
    """Return the polygon (outer - inset-shrunk-outer) as a ring."""
    inner = outer.buffer(-inset, join_style=1, resolution=16)
    if inner.is_empty:
        return outer
    ring = outer.difference(inner)
    return ring


def _extrude(poly, base_z: float, height: float) -> Optional[trimesh.Trimesh]:
    if poly is None or poly.is_empty:
        return None
    try:
        if isinstance(poly, MultiPolygon):
            meshes = []
            for g in poly.geoms:
                m = trimesh.creation.extrude_polygon(g, height=height)
                m.apply_translation([0, 0, base_z])
                meshes.append(m)
            return trimesh.util.concatenate(meshes) if meshes else None
        m = trimesh.creation.extrude_polygon(poly, height=height)
        m.apply_translation([0, 0, base_z])
        return m
    except Exception:
        return None


def _frame_simple(outer: Polygon, base: float, height: float, width: float, bundle: MeshBundle):
    ring = _frame_ring(outer, width)
    mesh = _extrude(ring, base_z=0.0, height=base + height)
    if mesh is not None:
        bundle.add("frame", mesh)


def _frame_stepped(outer: Polygon, base: float, height: float, width: float, bundle: MeshBundle):
    # Lower, wider shelf + a narrower riser on top (lip style).
    shelf_w = width
    lip_w = max(width * 0.55, 1.5)
    shelf_h = max(base * 0.4, 1.2)
    total_h = base + height

    # Bottom shelf: full outer minus (outer shrunk by shelf_w)
    shelf_ring = _frame_ring(outer, shelf_w)
    shelf_mesh = _extrude(shelf_ring, base_z=0.0, height=shelf_h)
    if shelf_mesh is not None:
        bundle.add("frame", shelf_mesh)

    # Top lip: same outer boundary, narrower lip
    lip_ring = _frame_ring(outer, lip_w)
    lip_mesh = _extrude(lip_ring, base_z=shelf_h, height=total_h - shelf_h)
    if lip_mesh is not None:
        bundle.add("frame", lip_mesh)


# =============================================================================
# Helpers to keep feature geometry inside the shape
# =============================================================================

def clip_coords_to_shape(points_mm: np.ndarray, shape_poly: Polygon):
    """Clip a polyline (2-col) against the shape; return list of np arrays."""
    if len(points_mm) < 2:
        return []
    try:
        ls = LineString(points_mm)
    except Exception:
        return []
    if not ls.is_valid or ls.is_empty:
        return []
    inter = ls.intersection(shape_poly)
    if inter.is_empty:
        return []
    out = []
    geoms = getattr(inter, "geoms", [inter])
    for g in geoms:
        if hasattr(g, "coords"):
            arr = np.asarray(g.coords)
            if len(arr) >= 2:
                out.append(arr)
    return out


# =============================================================================
# Route ribbon
# =============================================================================

def build_route(
    routes: List[Route],
    dem: DEM,
    proj: Projection,
    opts: GenerationOptions,
    terrain_z: np.ndarray,
    shape_poly: Optional[Polygon] = None,
) -> MeshBundle:
    bundle = MeshBundle(projection=proj)
    if not opts.include_route:
        return bundle

    min_e = float(np.nanmin(dem.elev))
    max_e = float(np.nanmax(dem.elev))
    if max_e - min_e < 1e-6:
        max_e = min_e + 1.0
    vertical_range_mm = (max_e - min_e) / 1000.0 * proj.mm_per_km * opts.z_exaggeration
    vertical_range_mm = max(vertical_range_mm, 2.0)

    for route in routes:
        r = resample_route(route, spacing_deg=0.0003)
        lons = r.points[:, 0]
        lats = r.points[:, 1]
        xs, ys = proj.project(lons, lats)
        xy = np.column_stack([xs, ys])

        # Clip to shape polygon in mm so ribbons never cross the frame.
        segments = (
            clip_coords_to_shape(xy, shape_poly)
            if shape_poly is not None
            else [xy]
        )
        for seg in segments:
            if len(seg) < 2:
                continue
            seg_lon, seg_lat = proj.unproject(seg[:, 0], seg[:, 1])
            elev_m = sample_elevation(dem, seg_lon, seg_lat)
            rel = (elev_m - min_e) / (max_e - min_e)
            z_top = rel * vertical_range_mm + opts.base_thickness_mm
            mesh = _ribbon_mesh(
                xs=seg[:, 0], ys=seg[:, 1], z_base=z_top,
                width=opts.route_width_mm,
                height=opts.route_height_mm,
            )
            if mesh is not None:
                bundle.add("route", mesh)

    return bundle


# =============================================================================
# Rivers / lakes
# =============================================================================

def build_rivers(
    features: OSMFeatures,
    dem: DEM,
    proj: Projection,
    opts: GenerationOptions,
    shape_poly: Optional[Polygon] = None,
) -> MeshBundle:
    bundle = MeshBundle(projection=proj)
    if not opts.include_rivers:
        return bundle

    min_e = float(np.nanmin(dem.elev))
    max_e = float(np.nanmax(dem.elev))
    if max_e - min_e < 1e-6:
        max_e = min_e + 1.0
    vertical_range_mm = (max_e - min_e) / 1000.0 * proj.mm_per_km * opts.z_exaggeration
    vertical_range_mm = max(vertical_range_mm, 2.0)

    def _z_for(lons_, lats_):
        elev_m = sample_elevation(dem, np.asarray(lons_), np.asarray(lats_))
        rel = (elev_m - min_e) / (max_e - min_e)
        return rel * vertical_range_mm + opts.base_thickness_mm + 0.05

    for line in features.rivers:
        if len(line) < 2:
            continue
        xs, ys = proj.project(line[:, 0], line[:, 1])
        xy = np.column_stack([xs, ys])
        segments = (
            clip_coords_to_shape(xy, shape_poly)
            if shape_poly is not None
            else [xy]
        )
        for seg in segments:
            if len(seg) < 2:
                continue
            lon_s, lat_s = proj.unproject(seg[:, 0], seg[:, 1])
            z = _z_for(lon_s, lat_s)
            mesh = _ribbon_mesh(
                xs=seg[:, 0], ys=seg[:, 1], z_base=z,
                width=opts.river_width_mm,
                height=opts.river_depth_mm,
            )
            if mesh is not None:
                bundle.add("river", mesh)

    for poly in features.lakes:
        if len(poly) < 3:
            continue
        xs, ys = proj.project(poly[:, 0], poly[:, 1])
        ring = np.column_stack([xs, ys])
        try:
            polygon = Polygon(ring).buffer(0)
            if shape_poly is not None:
                polygon = polygon.intersection(shape_poly)
            if polygon.is_empty or polygon.area < 1e-6:
                continue
            if hasattr(polygon, "geoms"):
                geoms = list(polygon.geoms)
            else:
                geoms = [polygon]
            for geom in geoms:
                if geom.is_empty or not isinstance(geom, Polygon):
                    continue
                xs_c, ys_c = np.array(geom.exterior.coords).T
                lon_c, lat_c = proj.unproject(xs_c, ys_c)
                z_mid = float(np.mean(_z_for(lon_c, lat_c)))
                mesh = _extrude_polygon(geom, base_z=z_mid - 0.1,
                                        height=opts.river_depth_mm + 0.2)
                if mesh is not None:
                    bundle.add("river", mesh)
        except Exception:
            continue

    return bundle


# =============================================================================
# Cities / peaks
# =============================================================================

def build_cities(
    features: OSMFeatures, dem: DEM, proj: Projection,
    opts: GenerationOptions, shape_poly: Optional[Polygon] = None,
) -> MeshBundle:
    bundle = MeshBundle(projection=proj)
    if not opts.include_cities:
        return bundle

    min_e = float(np.nanmin(dem.elev))
    max_e = float(np.nanmax(dem.elev))
    if max_e - min_e < 1e-6:
        max_e = min_e + 1.0
    vertical_range_mm = (max_e - min_e) / 1000.0 * proj.mm_per_km * opts.z_exaggeration
    vertical_range_mm = max(vertical_range_mm, 2.0)

    for lon, lat, pop, name in features.cities:
        r_mm = 1.5 + min(4.0, math.log10(max(pop, 1000)) - 2)
        x, y = proj.project(np.array([lon]), np.array([lat]))
        if shape_poly is not None and not shape_poly.contains(Point(float(x[0]), float(y[0]))):
            continue
        elev_m = float(sample_elevation(dem, np.array([lon]), np.array([lat]))[0])
        rel = (elev_m - min_e) / (max_e - min_e)
        z_base = rel * vertical_range_mm + opts.base_thickness_mm + 0.05
        circle = _circle_polygon(float(x[0]), float(y[0]), r_mm, segments=24)
        # Clip to shape so city pads don't poke out of the frame.
        if shape_poly is not None:
            circle = circle.intersection(shape_poly)
            if circle.is_empty:
                continue
        mesh = _extrude_polygon(circle, base_z=z_base, height=opts.city_thickness_mm)
        if mesh is not None:
            bundle.add("city", mesh)
    return bundle


# =============================================================================
# Buildings (extruded footprint polygons) + sea (recessed blue zones)
# =============================================================================

def build_buildings(
    features: OSMFeatures,
    dem: DEM,
    proj: Projection,
    opts: GenerationOptions,
    shape_poly: Optional[Polygon] = None,
) -> MeshBundle:
    """Extrude each OSM building footprint into a simple block.

    Heights are read from the OSM `height`/`building:levels` tags and
    compressed to the printable range via `opts.building_height_scale`,
    clamped between `building_min_height_mm` and `building_max_height_mm`.
    """
    bundle = MeshBundle(projection=proj)
    if not getattr(opts, "include_buildings", False) or not features.buildings:
        return bundle

    min_e = float(np.nanmin(dem.elev))
    max_e = float(np.nanmax(dem.elev))
    if max_e - min_e < 1e-6:
        max_e = min_e + 1.0
    vertical_range_mm = (max_e - min_e) / 1000.0 * proj.mm_per_km * opts.z_exaggeration
    vertical_range_mm = max(vertical_range_mm, 2.0)

    default_h_m = 8.0  # used when the building has no tagged height
    for poly_coords, height_m in features.buildings:
        if len(poly_coords) < 4:
            continue
        xs, ys = proj.project(poly_coords[:, 0], poly_coords[:, 1])
        try:
            polygon = Polygon(np.column_stack([xs, ys])).buffer(0)
        except Exception:
            continue
        if polygon.is_empty or polygon.area < 0.6:  # drop sub-1mm² shapes
            continue
        if shape_poly is not None:
            polygon = polygon.intersection(shape_poly)
            if polygon.is_empty or polygon.area < 0.5:
                continue
        # Building base at terrain height (mean elev at footprint centroid).
        centroid = polygon.centroid
        if centroid.is_empty:
            continue
        lon_c, lat_c = proj.unproject(
            np.array([centroid.x]), np.array([centroid.y])
        )
        elev_m = float(sample_elevation(dem, lon_c, lat_c)[0])
        rel = (elev_m - min_e) / (max_e - min_e)
        z_base = rel * vertical_range_mm + opts.base_thickness_mm

        h_m = height_m if height_m is not None else default_h_m
        z_h = max(
            min(h_m * opts.building_height_scale, opts.building_max_height_mm),
            opts.building_min_height_mm,
        )

        geoms = [polygon] if isinstance(polygon, Polygon) else list(polygon.geoms)
        for g in geoms:
            if not isinstance(g, Polygon) or g.is_empty or g.area < 0.5:
                continue
            mesh = _extrude_polygon(g, base_z=z_base, height=z_h)
            if mesh is not None:
                bundle.add("building", mesh)

    return bundle


def build_sea(
    features: OSMFeatures,
    dem: DEM,
    proj: Projection,
    opts: GenerationOptions,
    shape_poly: Optional[Polygon] = None,
) -> MeshBundle:
    """Render `natural=water` polygons tagged as sea/ocean/bay as blue
    recessed zones slightly below the terrain base plate."""
    bundle = MeshBundle(projection=proj)
    if not getattr(opts, "include_sea", False):
        return bundle
    sea_polys = features.seas
    if not sea_polys:
        return bundle

    base_z = max(float(opts.base_thickness_mm) - float(opts.sea_depth_mm), 0.5)
    height = float(opts.sea_depth_mm) + 0.2

    for poly_coords in sea_polys:
        if len(poly_coords) < 3:
            continue
        xs, ys = proj.project(poly_coords[:, 0], poly_coords[:, 1])
        try:
            polygon = Polygon(np.column_stack([xs, ys])).buffer(0)
        except Exception:
            continue
        if polygon.is_empty:
            continue
        if shape_poly is not None:
            polygon = polygon.intersection(shape_poly)
            if polygon.is_empty:
                continue
        geoms = [polygon] if isinstance(polygon, Polygon) else list(polygon.geoms)
        for g in geoms:
            if not isinstance(g, Polygon) or g.is_empty or g.area < 1.0:
                continue
            mesh = _extrude_polygon(g, base_z=base_z, height=height)
            if mesh is not None:
                bundle.add("sea", mesh)
    return bundle


def build_peaks(
    features: OSMFeatures, dem: DEM, proj: Projection,
    opts: GenerationOptions, shape_poly: Optional[Polygon] = None,
) -> MeshBundle:
    bundle = MeshBundle(projection=proj)
    if not opts.include_peaks:
        return bundle

    min_e = float(np.nanmin(dem.elev))
    max_e = float(np.nanmax(dem.elev))
    if max_e - min_e < 1e-6:
        max_e = min_e + 1.0
    vertical_range_mm = (max_e - min_e) / 1000.0 * proj.mm_per_km * opts.z_exaggeration
    vertical_range_mm = max(vertical_range_mm, 2.0)

    for lon, lat, ele, name in features.peaks:
        x, y = proj.project(np.array([lon]), np.array([lat]))
        if shape_poly is not None and not shape_poly.contains(Point(float(x[0]), float(y[0]))):
            continue
        elev_m = float(sample_elevation(dem, np.array([lon]), np.array([lat]))[0])
        if ele is not None:
            elev_m = ele
        rel = (elev_m - min_e) / (max_e - min_e)
        z_base = rel * vertical_range_mm + opts.base_thickness_mm
        cone = _cone_mesh(
            cx=float(x[0]), cy=float(y[0]),
            base_z=z_base,
            radius=opts.peak_radius_mm,
            height=opts.peak_height_mm,
            segments=12,
        )
        if cone is not None:
            bundle.add("peak_marker", cone)
    return bundle


# =============================================================================
# Low-level geometry helpers
# =============================================================================

def _ribbon_mesh(xs, ys, z_base, width, height) -> Optional[trimesh.Trimesh]:
    if len(xs) < 2:
        return None
    p = np.column_stack([xs, ys])
    seg = np.diff(p, axis=0)
    seg_len = np.linalg.norm(seg, axis=1, keepdims=True)
    seg_len[seg_len == 0] = 1.0
    seg_n = seg / seg_len
    tang = np.zeros_like(p)
    tang[0] = seg_n[0]
    tang[-1] = seg_n[-1]
    tang[1:-1] = seg_n[:-1] + seg_n[1:]
    tl = np.linalg.norm(tang, axis=1, keepdims=True)
    tl[tl == 0] = 1.0
    tang = tang / tl
    norm = np.column_stack([-tang[:, 1], tang[:, 0]])

    half_w = width / 2.0
    left = p + norm * half_w
    right = p - norm * half_w

    n = len(xs)
    zb = np.asarray(z_base, dtype=np.float64)
    zt = zb + height

    verts = []
    for i in range(n):
        verts.append([left[i, 0], left[i, 1], float(zb[i])])
        verts.append([right[i, 0], right[i, 1], float(zb[i])])
        verts.append([left[i, 0], left[i, 1], float(zt[i])])
        verts.append([right[i, 0], right[i, 1], float(zt[i])])
    V = np.asarray(verts, dtype=np.float64)

    faces = []
    for i in range(n - 1):
        a = 4 * i
        b = 4 * (i + 1)
        faces += [[a + 2, a + 3, b + 3], [a + 2, b + 3, b + 2]]
        faces += [[a, b + 1, b], [a, a + 1, b + 1]]
        faces += [[a, a + 2, b + 2], [a, b + 2, b]]
        faces += [[a + 1, b + 1, b + 3], [a + 1, b + 3, a + 3]]
    faces += [[0, 1, 3], [0, 3, 2]]
    a = 4 * (n - 1)
    faces += [[a, a + 2, a + 3], [a, a + 3, a + 1]]
    return trimesh.Trimesh(
        vertices=V, faces=np.asarray(faces, dtype=np.int64), process=True
    )


def _circle_polygon(cx, cy, r, segments=24) -> Polygon:
    ang = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    xs = cx + r * np.cos(ang)
    ys = cy + r * np.sin(ang)
    return Polygon(np.column_stack([xs, ys]))


def _extrude_polygon(polygon, base_z, height) -> Optional[trimesh.Trimesh]:
    if polygon is None or polygon.is_empty:
        return None
    try:
        mesh = trimesh.creation.extrude_polygon(polygon, height=height)
    except Exception:
        return None
    mesh.apply_translation([0, 0, base_z])
    return mesh


def _cone_mesh(cx, cy, base_z, radius, height, segments=12) -> Optional[trimesh.Trimesh]:
    ang = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    xs = cx + radius * np.cos(ang)
    ys = cy + radius * np.sin(ang)
    verts = [[x, y, base_z] for x, y in zip(xs, ys)]
    apex_idx = len(verts)
    verts.append([cx, cy, base_z + height])
    bot_idx = len(verts)
    verts.append([cx, cy, base_z])
    faces = []
    n = segments
    for i in range(n):
        a = i
        b = (i + 1) % n
        faces.append([a, b, apex_idx])
        faces.append([bot_idx, b, a])
    return trimesh.Trimesh(
        vertices=np.asarray(verts, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int64),
        process=True,
    )
