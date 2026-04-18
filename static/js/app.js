/*
 * TerraTrail frontend (ES module).
 *
 *  - Leaflet map for input (GPX file or click-to-draw manual route).
 *  - Tabbed right panel: "Map" for input, "3D Preview" for the generated
 *    model (full-size, not a small corner widget).
 *  - Three.js + OBJLoader/MTLLoader for the in-browser preview.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { OBJLoader } from "three/addons/loaders/OBJLoader.js";
import { MTLLoader } from "three/addons/loaders/MTLLoader.js";

// =====================================================================
// Tab switching
// =====================================================================

const tabButtons = document.querySelectorAll(".tabs .tab");
const tabPanels = document.querySelectorAll(".tab-panel");

function switchTab(name) {
  tabButtons.forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  tabPanels.forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
  // Leaflet and Three.js both need a size-recalculation nudge after the
  // panel becomes visible.
  if (name === "map" && window._leafletMap) window._leafletMap.invalidateSize();
  if (name === "preview") resizePreview();
}

tabButtons.forEach((b) => {
  b.addEventListener("click", () => {
    if (!b.disabled) switchTab(b.dataset.tab);
  });
});

// =====================================================================
// Leaflet map (input)
// =====================================================================

const map = L.map("map").setView([35.68, 139.73], 10);
window._leafletMap = map;
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "© OpenStreetMap contributors",
}).addTo(map);

const legend = L.control({ position: "bottomright" });
legend.onAdd = function () {
  const div = L.DomUtil.create("div", "legend");
  div.innerHTML = `
    <b>出力レイヤー</b><br>
    <span class="swatch" style="background:#b4c35a"></span>低地<br>
    <span class="swatch" style="background:#829650"></span>中腹<br>
    <span class="swatch" style="background:#e6e8e8"></span>山頂<br>
    <span class="swatch" style="background:#e64632"></span>ルート<br>
    <span class="swatch" style="background:#3c82d2"></span>河川/湖<br>
    <span class="swatch" style="background:#191919"></span>額縁
  `;
  return div;
};
legend.addTo(map);

// =====================================================================
// Input mode (GPX / manual)
// =====================================================================

function currentMode() {
  const el = document.querySelector('input[name="input-mode"]:checked');
  return el ? el.value : "gpx";
}

function setMode(mode) {
  document.getElementById("gpx-mode").classList.toggle("hidden", mode !== "gpx");
  document.getElementById("manual-mode").classList.toggle("hidden", mode !== "manual");
  if (mode === "manual") switchTab("map");
}

document.querySelectorAll('input[name="input-mode"]').forEach((r) => {
  r.addEventListener("change", () => setMode(currentMode()));
});
setMode(currentMode());

// =====================================================================
// Model mode (layered / single)
// =====================================================================

function currentModelMode() {
  const el = document.querySelector('input[name="model-mode"]:checked');
  return el ? el.value : "layered";
}

function syncModelModeUI() {
  const row = document.getElementById("cap-depth-row");
  row.classList.toggle("hidden", currentModelMode() !== "single");
}

document.querySelectorAll('input[name="model-mode"]').forEach((r) => {
  r.addEventListener("change", syncModelModeUI);
});
syncModelModeUI();

// =====================================================================
// Manual drawing
// =====================================================================

let drawnCoords = [];
let drawnLine = null;
const drawnMarkers = [];

function updateDrawStatus() {
  const el = document.getElementById("draw-status");
  if (el) el.textContent = `点数: ${drawnCoords.length}`;
}

function redrawLine() {
  if (drawnLine) map.removeLayer(drawnLine);
  if (drawnCoords.length >= 2) {
    drawnLine = L.polyline(
      drawnCoords.map(([x, y]) => [y, x]),
      { color: "#ff6b52", weight: 4 }
    ).addTo(map);
  } else drawnLine = null;
}

map.on("click", (e) => {
  if (currentMode() !== "manual") return;
  const pt = [e.latlng.lng, e.latlng.lat];
  drawnCoords.push(pt);
  const marker = L.circleMarker(e.latlng, {
    radius: 5, color: "#ff6b52", fillColor: "#ff8b74",
    fillOpacity: 1, weight: 2,
  }).addTo(map);
  drawnMarkers.push(marker);
  redrawLine();
  updateDrawStatus();
});

function clearManual() {
  drawnCoords = [];
  drawnMarkers.forEach((m) => map.removeLayer(m));
  drawnMarkers.length = 0;
  if (drawnLine) { map.removeLayer(drawnLine); drawnLine = null; }
  updateDrawStatus();
}

// =====================================================================
// GPX preview on map
// =====================================================================

const gpxInput = document.getElementById("gpx-file");
let gpxPreviewLayer = null;

function clearGpxPreview() {
  if (gpxPreviewLayer) { map.removeLayer(gpxPreviewLayer); gpxPreviewLayer = null; }
}

gpxInput.addEventListener("change", async () => {
  clearGpxPreview();
  const file = gpxInput.files[0];
  if (!file) return;
  const text = await file.text();
  const xml = new DOMParser().parseFromString(text, "text/xml");
  const pts = [];
  xml.querySelectorAll("trkpt, rtept").forEach((el) => {
    const lat = parseFloat(el.getAttribute("lat"));
    const lon = parseFloat(el.getAttribute("lon"));
    if (!isNaN(lat) && !isNaN(lon)) pts.push([lat, lon]);
  });
  if (pts.length > 1) {
    gpxPreviewLayer = L.polyline(pts, { color: "#ff6b52", weight: 4 }).addTo(map);
    switchTab("map");
    setTimeout(() => map.fitBounds(gpxPreviewLayer.getBounds(), { padding: [30, 30] }), 100);
  } else {
    alert("GPX 内にトラック/ルート点が見つかりませんでした。");
  }
});

// =====================================================================
// Clear button
// =====================================================================

document.getElementById("clear-route").addEventListener("click", () => {
  clearManual();
  clearGpxPreview();
  gpxInput.value = "";
});

// =====================================================================
// Manual bbox (map-area) selection
// =====================================================================

let bboxMode = "auto";
let bboxDrawState = null;  // {corners: [[lng,lat], [lng,lat]]}
let bboxRect = null;

function setBboxMode(mode) {
  bboxMode = mode;
  document.getElementById("manual-bbox-ctrl").classList.toggle("hidden", mode !== "manual");
  if (mode === "auto") {
    clearBboxRect();
  }
}

document.querySelectorAll('input[name="bbox-mode"]').forEach((r) => {
  r.addEventListener("change", () => setBboxMode(r.value));
});

function clearBboxRect() {
  if (bboxRect) { map.removeLayer(bboxRect); bboxRect = null; }
  bboxDrawState = null;
  const s = document.getElementById("bbox-status");
  if (s) { s.style.display = "none"; s.textContent = ""; }
}

document.getElementById("draw-bbox").addEventListener("click", () => {
  clearBboxRect();
  switchTab("map");
  alert("地図で範囲の角を 2 点クリックしてください（左上と右下）。");
  bboxDrawState = { corners: [] };
});

map.on("click", (e) => {
  if (bboxMode !== "manual" || !bboxDrawState) return;
  bboxDrawState.corners.push([e.latlng.lng, e.latlng.lat]);
  if (bboxDrawState.corners.length === 2) {
    const [[lon1, lat1], [lon2, lat2]] = bboxDrawState.corners;
    const w = Math.min(lon1, lon2), e2 = Math.max(lon1, lon2);
    const s = Math.min(lat1, lat2), n = Math.max(lat1, lat2);
    bboxDrawState = { bbox: [w, s, e2, n] };
    if (bboxRect) map.removeLayer(bboxRect);
    bboxRect = L.rectangle([[s, w], [n, e2]], {
      color: "#4fd0e0", weight: 2, fillOpacity: 0.08, dashArray: "5,5",
    }).addTo(map);
    const status = document.getElementById("bbox-status");
    status.style.display = "block";
    status.textContent = `範囲設定: ${(e2 - w).toFixed(4)}° × ${(n - s).toFixed(4)}°`;
  }
});

// =====================================================================
// Generate
// =====================================================================

const genBtn = document.getElementById("generate");
const progressEl = document.getElementById("progress");
const resultEl = document.getElementById("result");

function getShape() {
  const el = document.querySelector('input[name="shape"]:checked');
  return el ? el.value : "rectangle";
}

function readOptions() {
  return {
    size_mm: parseFloat(document.getElementById("opt-size").value),
    base_thickness_mm: parseFloat(document.getElementById("opt-base").value),
    z_exaggeration: parseFloat(document.getElementById("opt-z").value),
    grid_resolution: parseInt(document.getElementById("opt-grid").value, 10),
    route_width_mm: parseFloat(document.getElementById("opt-route-w").value),
    route_height_mm: parseFloat(document.getElementById("opt-route-h").value),
    include_rivers: document.getElementById("opt-rivers").checked,
    include_cities: document.getElementById("opt-cities").checked,
    include_peaks: document.getElementById("opt-peaks").checked,
    include_route: document.getElementById("opt-route").checked,
    include_buildings: document.getElementById("opt-buildings").checked,
    include_sea: document.getElementById("opt-sea").checked,
    shape: getShape(),
    frame_style: document.getElementById("opt-frame").value,
    frame_width_mm: parseFloat(document.getElementById("opt-frame-w").value),
    model_mode: currentModelMode(),
    surface_color_depth_mm: parseFloat(document.getElementById("opt-cap").value),
  };
}

async function startJob() {
  const fd = new FormData();
  fd.append("options", JSON.stringify(readOptions()));
  fd.append("dem_source", document.getElementById("opt-dem").value);

  const mode = currentMode();
  if (mode === "gpx") {
    const file = gpxInput.files[0];
    if (!file) { alert("GPX ファイルを選択してください。"); return; }
    fd.append("gpx", file);
  } else {
    if (drawnCoords.length < 2) {
      alert("マップタブを開き、地図上を 2 点以上クリックしてルートを描画してください。");
      switchTab("map");
      return;
    }
    fd.append("coords", JSON.stringify(drawnCoords));
  }

  if (bboxMode === "manual" && bboxDrawState && bboxDrawState.bbox) {
    fd.append("custom_bbox", JSON.stringify(bboxDrawState.bbox));
  }

  genBtn.disabled = true;
  progressEl.classList.remove("hidden");
  resultEl.classList.add("hidden");
  updateProgress("送信中…", 1);

  let resp;
  try {
    resp = await fetch("/api/generate", { method: "POST", body: fd });
  } catch (e) { alert("サーバへ到達できません: " + e); genBtn.disabled = false; return; }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    alert("エラー: " + (err.error || resp.statusText));
    genBtn.disabled = false;
    return;
  }
  const { job_id } = await resp.json();
  streamProgress(job_id);
}

function updateProgress(step, pct) {
  document.querySelector("#progress .step").textContent = step;
  if (pct >= 0) document.querySelector("#progress .fill").style.width = pct + "%";
}

function streamProgress(jobId) {
  const es = new EventSource(`/api/progress/${jobId}`);
  es.onmessage = (e) => {
    try {
      const { step, pct } = JSON.parse(e.data);
      updateProgress(step, pct);
      if (step === "done" || pct === 100) { es.close(); fetchResult(jobId); }
      if (typeof step === "string" && step.startsWith("error")) {
        es.close();
        alert("生成エラー: " + step);
        genBtn.disabled = false;
      }
    } catch {}
  };
  es.onerror = () => es.close();
}

async function fetchResult(jobId) {
  for (let i = 0; i < 30; i++) {
    const r = await fetch(`/api/result/${jobId}`);
    const j = await r.json();
    if (j.status === "done") {
      showResult(j);
      genBtn.disabled = false;
      enablePreviewTab(j);
      return;
    }
    if (j.error) { alert("生成エラー: " + j.error); genBtn.disabled = false; return; }
    await new Promise((res) => setTimeout(res, 500));
  }
  genBtn.disabled = false;
}

function showResult(j) {
  const m = j.manifest;
  const s = m.route_stats || {};
  const mf = m.manifold || {};
  const allWt = Object.values(mf).every((v) => v.is_watertight);

  const stats = `
    <div class="stats">
      <div><span>距離</span><span>${s.distance_km ?? "—"} km</span></div>
      <div><span>累積標高</span><span>${s.elevation_gain_m ?? "—"} m</span></div>
      <div><span>最高点</span><span>${s.elevation_max_m ?? "—"} m</span></div>
      <div><span>最低点</span><span>${s.elevation_min_m ?? "—"} m</span></div>
      <div><span>サイズ</span><span>${m.projection.width_mm}×${m.projection.height_mm} mm</span></div>
      <div><span>形状/額縁</span><span>${m.shape} / ${m.frame_style}</span></div>
      <div><span>モデル構造</span><span>${m.options.model_mode}</span></div>
      <div><span>Watertight</span><span>${allWt ? "✅" : "⚠"}</span></div>
    </div>`;

  const layers = m.layers
    .map((l) => `<li><span class="swatch" style="background:rgb(${l.color_rgb.join(",")})"></span>
       ${l.material} — ${l.triangle_count.toLocaleString()} tris</li>`)
    .join("");

  resultEl.innerHTML = `
    <h3>生成完了</h3>
    ${stats}
    <div class="download-row">
      <a class="recommended" href="${j.obj}">🖨️ OBJ</a>
      <a href="${j.mtl}">MTL</a>
      <a href="${j.zip}">📦 ZIP</a>
      <a href="${j.combined_stl}">結合 STL</a>
      <a href="${j.threemf}">3MF</a>
    </div>
    <p class="hint" style="margin-top:0.6rem;">
      <code>.obj</code> と <code>.mtl</code> を同じフォルダに置き、OBJ を Bambu Studio にドラッグ。
    </p>
    <details style="margin-top:0.5rem;">
      <summary style="cursor:pointer; color:var(--accent); font-size:0.78rem;">レイヤー詳細</summary>
      <ul>${layers}</ul>
    </details>
  `;
  resultEl.classList.remove("hidden");

  // Populate HUD on preview tab
  const hud = document.getElementById("preview-hud");
  document.getElementById("pv-stat-dist").textContent = `📏 ${s.distance_km ?? "—"} km`;
  document.getElementById("pv-stat-gain").textContent = `⛰ ${s.elevation_gain_m ?? "—"} m`;
  document.getElementById("pv-stat-size").textContent = `🧱 ${m.projection.width_mm}×${m.projection.height_mm} mm`;
  hud.classList.remove("hidden");
}

genBtn.addEventListener("click", startJob);
updateDrawStatus();

// =====================================================================
// 3D Preview (Three.js) — full-size in its own tab
// =====================================================================

const previewCanvas = document.getElementById("preview-canvas");
const previewPlaceholder = document.getElementById("preview-placeholder");

let pvRenderer = null;
let pvScene = null;
let pvCamera = null;
let pvControls = null;
let pvAnim = null;

function disposePreview() {
  if (pvAnim) { cancelAnimationFrame(pvAnim); pvAnim = null; }
  if (pvRenderer) { pvRenderer.dispose(); pvRenderer = null; }
  if (pvControls) { pvControls.dispose(); pvControls = null; }
  pvScene = null;
  pvCamera = null;
}

function resizePreview() {
  if (!pvRenderer || !pvCamera) return;
  const host = document.getElementById("preview-host");
  const w = host.clientWidth;
  const h = host.clientHeight;
  if (w && h) {
    pvCamera.aspect = w / h;
    pvCamera.updateProjectionMatrix();
    pvRenderer.setSize(w, h, false);
  }
}

function enablePreviewTab(result) {
  const btn = document.querySelector('.tabs .tab[data-tab="preview"]');
  btn.disabled = false;
  document.getElementById("preview-badge").classList.remove("hidden");
  switchTab("preview");
  launchPreview(result);
}

async function launchPreview(result) {
  disposePreview();

  const host = document.getElementById("preview-host");
  const w = host.clientWidth || 800;
  const h = host.clientHeight || 600;

  pvRenderer = new THREE.WebGLRenderer({
    canvas: previewCanvas,
    antialias: true,
    alpha: true,
  });
  pvRenderer.setPixelRatio(window.devicePixelRatio);
  pvRenderer.setSize(w, h, false);

  pvScene = new THREE.Scene();
  pvScene.background = null;

  pvCamera = new THREE.PerspectiveCamera(40, w / h, 1, 4000);

  // Lighting: ambient + key + soft fill
  pvScene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const key = new THREE.DirectionalLight(0xffffff, 1.0);
  key.position.set(200, 400, 200);
  pvScene.add(key);
  const fill = new THREE.DirectionalLight(0x88aaff, 0.35);
  fill.position.set(-200, 200, -150);
  pvScene.add(fill);

  pvControls = new OrbitControls(pvCamera, pvRenderer.domElement);
  pvControls.enableDamping = true;
  pvControls.dampingFactor = 0.08;

  previewPlaceholder.textContent = "モデルを読み込み中…";
  previewPlaceholder.classList.remove("hidden");

  try {
    const mtlLoader = new MTLLoader();
    const materials = await new Promise((resolve, reject) => {
      mtlLoader.load(result.mtl, resolve, undefined, reject);
    });
    materials.preload();

    const objLoader = new OBJLoader();
    objLoader.setMaterials(materials);
    const obj = await new Promise((resolve, reject) => {
      objLoader.load(result.obj, resolve, undefined, reject);
    });

    // The OBJ is authored in printer coords (z up).  Rotate -90° around X
    // so that the model's "up" matches Three.js's +Y.
    obj.rotation.x = -Math.PI / 2;
    pvScene.add(obj);

    // Frame the camera on the bbox (after rotation)
    const bbox = new THREE.Box3().setFromObject(obj);
    const center = bbox.getCenter(new THREE.Vector3());
    obj.position.sub(center);

    bbox.setFromObject(obj);
    const size = bbox.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z);
    const dist = maxDim * 1.7;
    pvCamera.position.set(dist * 0.8, dist * 0.7, dist * 0.8);
    pvControls.target.set(0, 0, 0);
    pvCamera.near = Math.max(dist / 200, 0.1);
    pvCamera.far = dist * 20;
    pvCamera.updateProjectionMatrix();

    previewPlaceholder.classList.add("hidden");
  } catch (e) {
    console.error("preview load failed", e);
    previewPlaceholder.textContent = "プレビューの読み込みに失敗しました。";
    previewPlaceholder.classList.remove("hidden");
    return;
  }

  const loop = () => {
    pvAnim = requestAnimationFrame(loop);
    if (pvControls) pvControls.update();
    if (pvRenderer) pvRenderer.render(pvScene, pvCamera);
  };
  loop();

  // Resize handling
  window.addEventListener("resize", resizePreview);
  resizePreview();
}
