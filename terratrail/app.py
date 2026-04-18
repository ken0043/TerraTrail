"""Flask web application for TerraTrail.

Provides:
    GET  /             main UI (Leaflet map + option panel)
    POST /api/generate generate a 3D miniature map from GPX + options
    GET  /download/<job_id>/<filename>   fetch one of the generated files
    GET  /preview/<job_id>               3MF preview page
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Optional
import io
import json
import os
import queue
import threading
import time
import uuid

from flask import (
    Flask,
    jsonify,
    request,
    send_from_directory,
    render_template,
    Response,
    stream_with_context,
    abort,
)

from .config import GenerationOptions
from .gpx_loader import load_gpx, route_from_coords
from .pipeline import run_generation


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUTPUT_ROOT = REPO / "outputs"
OUTPUT_ROOT.mkdir(exist_ok=True)


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(REPO / "static"),
        template_folder=str(REPO / "templates"),
    )

    # Progress for each job goes into this dict of queues.  Each streaming
    # client picks up the events for its job_id.
    app.config["PROGRESS"] = {}

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/healthz")
    def health():
        return {"ok": True}

    @app.post("/api/generate")
    def api_generate():
        # options JSON field + (optional) GPX file field + (optional) coords field
        opts_json = request.form.get("options") or "{}"
        try:
            opts_dict = json.loads(opts_json)
        except json.JSONDecodeError:
            return jsonify({"error": "invalid options json"}), 400

        options = GenerationOptions()
        for k, v in opts_dict.items():
            if hasattr(options, k) and v is not None:
                cur = getattr(options, k)
                try:
                    if isinstance(cur, bool):
                        setattr(options, k, bool(v))
                    elif isinstance(cur, int):
                        setattr(options, k, int(v))
                    elif isinstance(cur, float):
                        setattr(options, k, float(v))
                    elif isinstance(cur, str):
                        setattr(options, k, str(v))
                    else:
                        setattr(options, k, v)
                except (TypeError, ValueError):
                    pass

        dem_source = request.form.get("dem_source", "auto")

        # Optional user-specified bbox (west, south, east, north).
        custom_bbox = None
        bbox_str = request.form.get("custom_bbox")
        if bbox_str:
            try:
                parts = json.loads(bbox_str)
                if isinstance(parts, list) and len(parts) == 4:
                    custom_bbox = tuple(float(v) for v in parts)
            except (ValueError, json.JSONDecodeError):
                custom_bbox = None

        routes = []
        if "gpx" in request.files:
            fh = request.files["gpx"]
            data = fh.read()
            if data:
                routes.extend(load_gpx(data))

        coords_json = request.form.get("coords")
        if coords_json:
            try:
                coords = json.loads(coords_json)
                if coords and len(coords) >= 2:
                    routes.append(route_from_coords(coords, name="manual"))
            except json.JSONDecodeError:
                pass

        if not routes:
            return jsonify({"error": "provide at least one GPX file or a set of coordinates"}), 400

        job_id = f"job-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        progress_q: "queue.Queue[tuple]" = queue.Queue()
        app.config["PROGRESS"][job_id] = progress_q

        def progress(step: str, pct: int):
            progress_q.put((step, pct))

        result_box = {}

        def _worker():
            try:
                result = run_generation(
                    routes=routes,
                    options=options,
                    out_root=OUTPUT_ROOT,
                    job_id=job_id,
                    dem_source=dem_source,
                    progress=progress,
                    custom_bbox=custom_bbox,
                )
                result_box["result"] = result
                progress_q.put(("done", 100))
            except Exception as e:
                result_box["error"] = str(e)
                progress_q.put(("error:" + str(e), -1))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        app.config.setdefault("RESULTS", {})[job_id] = result_box

        return jsonify({"job_id": job_id})

    @app.get("/api/progress/<job_id>")
    def api_progress(job_id):
        progress_q = app.config["PROGRESS"].get(job_id)
        if not progress_q:
            return jsonify({"error": "unknown job"}), 404

        @stream_with_context
        def event_stream():
            last_pct = -1
            while True:
                try:
                    step, pct = progress_q.get(timeout=30)
                except queue.Empty:
                    yield f"data: {json.dumps({'step': 'heartbeat', 'pct': last_pct})}\n\n"
                    continue
                last_pct = pct
                payload = {"step": step, "pct": pct}
                yield f"data: {json.dumps(payload)}\n\n"
                if step == "done" or pct == 100:
                    break
                if step.startswith("error"):
                    break

        return Response(event_stream(), mimetype="text/event-stream")

    @app.get("/api/result/<job_id>")
    def api_result(job_id):
        box = app.config.get("RESULTS", {}).get(job_id)
        if not box:
            return jsonify({"error": "unknown job"}), 404
        if "error" in box:
            return jsonify({"error": box["error"]}), 500
        if "result" not in box:
            return jsonify({"status": "running"})
        r = box["result"]
        return jsonify(
            {
                "status": "done",
                "job_id": r["job_id"],
                "zip": f"/download/{r['job_id']}/{r['zip'].name}",
                "obj": f"/download/{r['job_id']}/{r['obj'].name}",
                "mtl": f"/download/{r['job_id']}/{r['mtl'].name}",
                "threemf": f"/download/{r['job_id']}/{r['threemf'].name}",
                "combined_stl": f"/download/{r['job_id']}/{r['combined_stl'].name}",
                "stl_files": [f"/download/{r['job_id']}/{p.name}" for p in r["stl_files"]],
                "manifest": r["manifest"],
                "bbox": r["bbox"],
                "dem_source": r["dem_source"],
            }
        )

    @app.get("/download/<job_id>/<path:filename>")
    def download(job_id, filename):
        safe = Path(job_id).name
        out_dir = OUTPUT_ROOT / safe
        if not out_dir.is_dir():
            abort(404)
        return send_from_directory(out_dir.as_posix(), filename, as_attachment=True)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)
