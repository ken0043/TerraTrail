"""Configuration constants and material definitions for TerraTrail."""
from dataclasses import dataclass, field
from typing import Tuple

# -----------------------------------------------------------------------------
# Material / color palette (RGB 0-255)
# These map to Bambu H2C AMS slots.  Keep the number small (<=4 recommended)
# so that a single AMS cassette can print the whole model.
# -----------------------------------------------------------------------------
MATERIALS = {
    # name           color RGB        Bambu slot  description
    "base":        ((180, 195,  90), 1, "Low-elevation terrain / land"),
    "mid":         ((130, 150,  80), 2, "Mid-elevation hills"),
    "peak":        ((230, 232, 232), 3, "Snow-capped peaks / high terrain"),
    "route":       ((230,  70,  50), 4, "Hiking / cycling route"),
    "river":       (( 60, 130, 210), 5, "Rivers and lakes"),
    "sea":         (( 40, 110, 180), 5, "Sea / large water bodies"),
    "building":    ((220, 210, 195), 6, "Buildings"),
    "city":        ((120, 120, 120), 6, "Urban area pads / cities (optional)"),
    "peak_marker": ((255, 200,  40), 7, "Mountain peak markers"),
    "frame":       (( 25,  25,  25), 8, "Decorative border frame"),
    "frame_text":  ((240, 240, 240), 9, "Text engraved into the frame"),
}


# Allowed shape names for the outer map outline.
SHAPE_CHOICES = ("rectangle", "square", "circle", "rounded", "hexagon", "octagon")

# Allowed frame styles.
FRAME_CHOICES = ("none", "simple", "stepped", "auto")


@dataclass
class GenerationOptions:
    """User-tunable parameters for a single generation job."""

    # Physical size of the output model (in millimeters).  The longer axis of
    # the bounding box is scaled to this length; the other axis keeps aspect.
    size_mm: float = 150.0

    # How thick the base plate is (mm) beneath the lowest terrain point.
    base_thickness_mm: float = 3.0

    # Vertical exaggeration.  1.0 = physical scale (preserves accurate
    # mountain proportions); users can bump this up for a more dramatic look.
    z_exaggeration: float = 1.0

    # DEM grid resolution (samples per side).  Higher = finer detail but slower.
    grid_resolution: int = 200

    # Route tube parameters (millimeters on the final print).
    route_width_mm: float = 1.2
    route_height_mm: float = 1.0

    # River inset width / depth (mm).
    river_width_mm: float = 0.8
    river_depth_mm: float = 0.4

    # Peak marker radius / height (mm).
    peak_radius_mm: float = 1.2
    peak_height_mm: float = 1.5

    # City pad thickness (mm).
    city_thickness_mm: float = 0.6

    # Feature layers to include.
    include_rivers: bool = True
    include_cities: bool = False  # now mostly redundant with buildings; keep
                                  # available but default to OFF
    # Peak markers (yellow triangle cones) default OFF — they made the
    # surface of the printed map look cluttered with OSM-tagged peaks.
    include_peaks: bool = False
    include_route: bool = True
    include_buildings: bool = True  # extrude OSM `building=*` footprints
    include_sea: bool = True        # render natural=water polygons as sea

    # Building height (mm) rules.  Overpass heights are in metres.
    building_min_height_mm: float = 0.8    # minimum even for low structures
    building_max_height_mm: float = 12.0   # cap so skyscrapers don't tower
    building_height_scale: float = 0.02    # mm per metre (i.e. 1:50)
    # Sea areas are recessed by this many mm below the terrain base plate
    # (creates a visible "water level" contour).
    sea_depth_mm: float = 0.6

    # Terrain construction mode:
    #   "layered" — 3 separate closed solids (base/mid/peak), each going
    #               from z=0 up to the local surface.  Full-height colour
    #               banding.  Good for visual contrast, but causes many
    #               filament changes per layer when printed on the H2C.
    #   "single"  — one watertight main body in `base` colour plus thin
    #               (~cap depth) coloured caps that sit on the top of the
    #               mid/peak cells.  Dramatically fewer filament changes
    #               at the cost of colour being visible only on the top.
    model_mode: str = "layered"
    # Thickness of the coloured surface caps in "single" mode (mm).  Pick
    # a multiple of your first-layer height (0.2mm → 0.4mm cap = 2 layers).
    surface_color_depth_mm: float = 0.6

    # Elevation thresholds (0..1 percentile) that split base/mid/peak
    # materials.
    band_mid_pct: float = 0.35
    band_peak_pct: float = 0.75

    # Optional padding around the route bbox (degrees of lat/lon).
    bbox_pad_deg: float = 0.01

    # Outer shape of the map: one of SHAPE_CHOICES.
    shape: str = "rectangle"

    # Frame / border style: one of FRAME_CHOICES.
    frame_style: str = "none"
    # Width of the frame (in mm, when visible).
    frame_width_mm: float = 6.0
    # Height the frame rises above the base plate (in mm).
    frame_height_mm: float = 5.0
    # If True, engrave route stats (distance, elevation gain) into the frame
    # as small recessed text.  Requires the frame to be enabled.
    frame_text: bool = True
    # Optional title shown on the top edge of the frame (falls back to the
    # GPX track name).
    frame_title: str = ""


# -----------------------------------------------------------------------------
# Data source endpoints
# -----------------------------------------------------------------------------

# GSI (国土地理院) PNG DEM tiles.  Elevation encoded in the RGB channels.
GSI_DEM_URL = "https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png"
GSI_DEM_Z = 14  # ~10m resolution

# AWS Open Data Terrain (Terrarium encoded PNG).  Worldwide coverage.
TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TERRARIUM_Z = 12  # ~30m resolution worldwide

# Overpass API for OpenStreetMap queries.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Rough bounding box for Japan.
JAPAN_BBOX = (122.0, 24.0, 153.0, 46.0)  # (west, south, east, north)


def in_japan(lon: float, lat: float) -> bool:
    """Return True if the point falls inside the rough Japan bounding box."""
    w, s, e, n = JAPAN_BBOX
    return w <= lon <= e and s <= lat <= n
