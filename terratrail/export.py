"""Export a MeshBundle as individual STLs and/or a combined 3MF.

3MF support comes via trimesh's built-in exporter which writes one
`<object>` element per mesh.  We then post-process the 3MF to add a
`<m:basematerials>` resource (3MF Material Extension) so that slicers
like Bambu Studio automatically display each body with the correct
color and let the user map each material to an AMS slot.

The STL export is provided for users who prefer Bambu Studio's "Import
as part" flow where they manually assign filaments per body.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List
import io
import re
import shutil
import tempfile
import zipfile
import json

import numpy as np
import trimesh

from .config import MATERIALS
from .mesh import MeshBundle


def export_individual_stls(bundle: MeshBundle, out_dir: Path) -> List[Path]:
    """Write one STL per material.  Returns the list of paths written."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for name, mesh in bundle.meshes.items():
        if mesh is None or len(mesh.faces) == 0:
            continue
        path = out_dir / f"terratrail_{name}.stl"
        mesh.export(path.as_posix(), file_type="stl")
        written.append(path)
    return written


def export_combined_stl(bundle: MeshBundle, out_path: Path) -> Path:
    """Write a single STL that concatenates every material (no color info)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined = bundle.combined()
    combined.export(out_path.as_posix(), file_type="stl")
    return out_path


def export_colored_obj(bundle: MeshBundle, out_path: Path) -> Path:
    """Write a multi-material OBJ + MTL pair.

    Bambu Studio (and most other slicers / DCC tools) picks up `Kd` colors
    from the referenced MTL file and surfaces one colored part per
    material group.  This is the recommended output for the Bambu H2C
    multi-color workflow because it requires no 3MF-specific extensions.

    Output structure:
        <stem>.obj   — geometry with `usemtl <name>` sections per material
        <stem>.mtl   — one `newmtl` block per material with Kd / Ka
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stem = out_path.stem
    obj_path = out_path.with_suffix(".obj")
    mtl_path = out_path.with_suffix(".mtl")

    obj_lines: list[str] = [
        f"# TerraTrail colored OBJ",
        f"# materials: {', '.join(bundle.meshes.keys())}",
        f"mtllib {mtl_path.name}",
        "",
    ]
    mtl_lines: list[str] = ["# TerraTrail MTL — one material per print color", ""]

    vertex_offset = 0  # OBJ vertex indices are 1-based and global
    for material_name, mesh in bundle.meshes.items():
        if mesh is None or len(mesh.faces) == 0:
            continue
        rgb, _slot, desc = MATERIALS.get(material_name, ((128, 128, 128), 0, material_name))
        r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0

        mtl_lines.extend(
            [
                f"newmtl {material_name}",
                f"  # {desc}",
                f"Ka {r * 0.2:.4f} {g * 0.2:.4f} {b * 0.2:.4f}",
                f"Kd {r:.4f} {g:.4f} {b:.4f}",
                f"Ks 0.0000 0.0000 0.0000",
                f"Ns 10.0",
                f"d 1.0",
                f"illum 2",
                "",
            ]
        )

        obj_lines.append(f"o {material_name}")
        obj_lines.append(f"g {material_name}")
        obj_lines.append(f"usemtl {material_name}")
        for vx, vy, vz in mesh.vertices:
            obj_lines.append(f"v {vx:.6f} {vy:.6f} {vz:.6f}")
        for fa, fb, fc in mesh.faces:
            obj_lines.append(
                f"f {fa + 1 + vertex_offset} {fb + 1 + vertex_offset} {fc + 1 + vertex_offset}"
            )
        obj_lines.append("")
        vertex_offset += len(mesh.vertices)

    obj_path.write_text("\n".join(obj_lines), encoding="utf-8")
    mtl_path.write_text("\n".join(mtl_lines), encoding="utf-8")
    return obj_path


def export_3mf(bundle: MeshBundle, out_path: Path) -> Path:
    """Write a 3MF containing every layer as a separate object, with colors.

    Bambu Studio will load each object as an individual part, letting the
    user (or an auto-assignment rule) map them to AMS slots.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bundle.apply_color()
    scene = bundle.to_scene()
    scene.export(out_path.as_posix(), file_type="3mf")
    _inject_base_materials(out_path, bundle)
    return out_path


def _inject_base_materials(path: Path, bundle: MeshBundle) -> None:
    """Patch an exported 3MF so each object references a named color.

    Implements the 3MF Material Extension: adds a single `<m:basematerials>`
    resource listing one `<m:base>` per material, then rewrites every
    `<object>` to reference it via `pid`/`pindex` attributes.
    """
    ordered_materials = [m for m in bundle.meshes.keys()]
    if not ordered_materials:
        return
    mat_to_index = {name: i for i, name in enumerate(ordered_materials)}

    base_entries = []
    for name in ordered_materials:
        rgb, _slot, _desc = MATERIALS.get(name, ((128, 128, 128), 0, name))
        hex_color = "#{:02X}{:02X}{:02X}FF".format(rgb[0], rgb[1], rgb[2])
        base_entries.append(
            f'<m:base name="{name}" displaycolor="{hex_color}"/>'
        )

    basematerials_id = 100
    basematerials_xml = (
        f'<m:basematerials id="{basematerials_id}">'
        + "".join(base_entries)
        + "</m:basematerials>"
    )

    with zipfile.ZipFile(path, "r") as zin:
        entries = {n: zin.read(n) for n in zin.namelist()}

    model_key = next((k for k in entries if k.endswith("3dmodel.model")), None)
    if model_key is None:
        return
    model = entries[model_key].decode("utf-8")

    # Insert the basematerials resource just inside <resources>
    model = model.replace(
        "<resources>",
        f"<resources>{basematerials_xml}",
        1,
    )

    # Rewrite each <object ... name="X" ...> to reference our base materials.
    def _patch_object(match: re.Match) -> str:
        head = match.group(0)
        m = re.search(r'name="([^"]+)"', head)
        if not m:
            return head
        name = m.group(1)
        idx = mat_to_index.get(name)
        if idx is None:
            return head
        # remove existing pid/pindex if any
        head = re.sub(r'\s+pid="[^"]*"', "", head)
        head = re.sub(r'\s+pindex="[^"]*"', "", head)
        # insert new attrs before the closing '>'
        insertion = f' pid="{basematerials_id}" pindex="{idx}"'
        if head.endswith("/>"):
            return head[:-2] + insertion + "/>"
        return head[:-1] + insertion + ">"

    model = re.sub(r"<object\b[^>]*>", _patch_object, model)
    entries[model_key] = model.encode("utf-8")

    # rewrite zip
    tmp = path.with_suffix(".3mf.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)
    shutil.move(tmp, path)


def zip_outputs(files: List[Path], zip_path: Path, manifest: Dict) -> Path:
    """Bundle the generated files plus a manifest JSON into a single zip."""
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=f.name)
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return zip_path


def build_manifest(bundle: MeshBundle, options_dict: Dict) -> Dict:
    """Human-readable metadata + suggested AMS slot mapping."""
    layers = []
    for name, mesh in bundle.meshes.items():
        if mesh is None or len(mesh.faces) == 0:
            continue
        rgb, slot, desc = MATERIALS.get(name, ((128, 128, 128), 0, name))
        layers.append(
            {
                "material": name,
                "description": desc,
                "color_rgb": list(rgb),
                "suggested_ams_slot": slot,
                "triangle_count": int(len(mesh.faces)),
                "vertex_count": int(len(mesh.vertices)),
                "stl_file": f"terratrail_{name}.stl",
            }
        )
    return {
        "tool": "TerraTrail",
        "version": "0.1.0",
        "options": options_dict,
        "layers": layers,
        "notes": [
            "Open the 3MF directly in Bambu Studio for colored preview.",
            "If using STL files, import each as a separate part and assign AMS slots.",
            "Suggested filament mapping is in 'suggested_ams_slot' per layer.",
        ],
    }
