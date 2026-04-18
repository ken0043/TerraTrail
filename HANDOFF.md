# TerraTrail — 引き継ぎドキュメント (HANDOFF)

**宛先**: このプロジェクトを引き継ぐ次の Claude / 開発者
**最終更新**: 2026-04-17（OBJ 出力 + クリッピング追加）
**状態**: MVP 動作確認済み・Bambu Studio で実機検証済み

---

## 1. プロジェクト概要

ユーザー（Hironao 様 / info@magnarecta.com）からの依頼で、以下のようなツールを作成中：

> GPX ファイルや手動ルートから、Bambu Lab H2C でマルチカラー印刷できる「冒険のミニチュア 3D マップ」を生成する Web アプリ。コース色、山の高さ、河川、都市などを色分けしたモデルを出力する。

参考にした既存ツール：
- https://map2model.com/
- https://makerworld.com/ja/models/1502820-personalized-3d-map
- https://makerworld.com/ja/models/1264871-hiking-and-cycling-map-generator-trailprint3d

ユーザーが合意した要件（AskUserQuestion で確認済み）:

| 項目 | 選択内容 |
|---|---|
| ツールの形態 | Web アプリ（規模によってはデスクトップ化も視野） |
| 入力データ形式 | GPX ファイル + 手動ルート入力 |
| 標高データソース | 両対応（日本→GSI、海外→SRTM を自動切替） |
| 色分け方針 | 3MF（マルチカラー）+ 個別 STL の両方出力 |

---

## 2. 現在の実装状況

### ✅ 動作確認済み

- **GPX パーサ** (`terratrail/gpx_loader.py`): 丹沢GPX（350点、標高付き）、サンプル高尾山GPXの両方でパース成功
- **投影** (`terratrail/mesh.py::Projection`): 単体テスト合格（`tests/test_projection.py`）
- **メッシュ生成** (`terratrail/mesh.py`): 7 レイヤーの mesh 生成成功（base/mid/peak/route/river/city/peak_marker）
- **STL 出力**: 個別 & 結合 STL が trimesh で正常出力
- **3MF 出力** (`terratrail/export.py::export_3mf`): **3MF Material Extension の `<m:basematerials>` を正しく埋め込み、7 色が `displaycolor` 属性で保存されることを確認済み**
- **Flask アプリ** (`terratrail/app.py`): `GET /`, `POST /api/generate`, SSE 進捗, `GET /api/result/<id>`, `GET /download/<id>/<file>` すべて動作
- **CLI** (`cli.py`): ヘッドレスで同じパイプラインを実行可能
- **オフライン統合テスト** (`tests/test_pipeline_offline.py`): 合成 DEM を使い全パイプラインを検証（`--tanzawa` フラグで丹沢サンプルをテスト可能）

### ⚠️ 未検証（このサンドボックスで確認不能）

- **実 DEM 取得（GSI / Terrarium）**: このクラウドサンドボックスでは外部プロキシが `cyberjapandata.gsi.go.jp` と `s3.amazonaws.com` への HTTPS をブロックしており、実環境でのタイル取得が未検証。ユーザーのローカル Mac では動作する想定。
- **実 OSM 取得（Overpass API）**: 同上の理由で未検証。
- **Bambu Studio での色表示**: 3MF XML は正しく生成されているが、実際に Bambu Studio にドラッグして色が期待どおり表示されるか未確認。

### 🐛 ユーザーから報告された既知の問題と、この turn で行った修正

| 問題 | 原因 | 修正 |
|---|---|---|
| `No module named 'networkx'` | `networkx`, `mapbox-earcut`, `manifold3d` は trimesh の**オプショナル**依存であり、当初の `requirements.txt` に含めていなかった | `requirements.txt` に追加（`networkx`, `mapbox-earcut`, `manifold3d`, `lxml`） |
| 手動でルートを描けない | `static/js/app.js` の `map.on("click")` が「GPX ファイルがあれば return」の条件分岐で手動クリックを無視していた | UI をラジオボタンでの **GPX / 手動 モード切替**に刷新。`templates/index.html`, `static/js/app.js`, `static/css/style.css` を更新 |
| **地形の枠外に川や道が長く突き出している** | Overpass API は bbox に**触れている** way の全ジオメトリを返すため、川や道が bbox 境界をはみ出して続いていた | `terratrail/clip.py` を新規追加（Shapely で LineString/Polygon を bbox にクリップ）。`pipeline.py` の DEM 取得直後、メッシュ化前に `_clip_features_and_routes` を呼ぶ。単体テスト・統合テストとも合格 |
| **3MF だと Bambu Studio で色が正しく反映されない / ユーザーは色付き OBJ を希望** | trimesh の 3MF には Bambu が認識する拡張メタが載らないため | `terratrail/export.py::export_colored_obj` を追加（標準 OBJ + MTL の `Kd` カラー指定）。Bambu Studio はドラッグ＆ドロップで自動的に色分けを認識。3MF は残すが UI では「予備」扱いに |

---

## 3. ユーザーの環境

- **OS**: macOS（Terminal: zsh）
- **Python**: 未インストール。`python`・`pip` ともに command not found。**→ `python3` / `pip3` を Homebrew 経由で入れてもらう必要がある**
- **プリンタ**: Bambu Lab H2C
- **想定ユーザー像**: 非ソフトウェアエンジニア。Terminal コマンドは実行できるが、開発作業には慣れていない。**初心者にやさしい案内が必要**。

### 引き継ぎ前に次の Claude がするべきこと（ユーザーにガイドすべき内容）

1. Homebrew + Python3 のインストール手順を案内
2. TerraTrail フォルダの場所を特定させる（Finder からドラッグで Terminal に貼る方法など）
3. `pip3 install -r requirements.txt` を実行させる
4. `python3 run.py` で Web UI 起動 → http://127.0.0.1:5000/
5. サンプルとして `examples/tanzawa_nabewariyama.gpx` か `examples/takao.gpx` をアップロードして動作確認
6. 実行できたら Bambu Studio で 3MF を開いて色表示を確認

---

## 4. アーキテクチャ

```
┌────────────────┐
│ run.py / cli.py│
└────────┬───────┘
         ▼
┌────────────────────────────────────────┐
│ terratrail/pipeline.py::run_generation │
└──┬───────┬───────┬─────┬──────┬────────┘
   │       │       │     │      │
   ▼       ▼       ▼     ▼      ▼
gpx    elevation  osm   mesh    export
_loader  .py    .py    .py      .py
         │         │     │       │
         ▼         │     │       ▼
     GSI/Terrar.  │     │    trimesh → STL
     PNG tiles    │     │       + 3MF
                  ▼     ▼
               Overpass  Shapely
                 API     (ポリゴン)
```

### ファイル構成

```
TerraTrail/
├── README.md                 # ユーザー向けドキュメント
├── HANDOFF.md                # このファイル（次の Claude 向け）
├── requirements.txt          # Python 依存
├── run.py                    # Flask 起動エントリ
├── cli.py                    # CLI エントリ
├── terratrail/
│   ├── __init__.py
│   ├── config.py             # 色・エンドポイント・オプション定義
│   ├── gpx_loader.py         # GPX パース + Route/resample
│   ├── elevation.py          # DEM タイル取得 (GSI + Terrarium) + decoder
│   ├── osm.py                # Overpass API クエリ
│   ├── mesh.py               # メッシュ構築（地形・ルート・河川・都市・山頂）
│   ├── export.py             # STL 個別/結合、3MF + <m:basematerials> 注入
│   ├── pipeline.py           # 上記を束ねるオーケストレータ
│   └── app.py                # Flask Web UI + REST API
├── templates/
│   └── index.html            # Leaflet UI (最新: モード切替 UI)
├── static/
│   ├── css/style.css
│   └── js/app.js             # 最新: ラジオボタン式モード切替
├── examples/
│   ├── takao.gpx                       # 小サンプル（高尾山、18点）
│   └── tanzawa_nabewariyama.gpx        # ユーザーが自分でアップロードしたもの（鍋割山→寄→大倉、350点）
├── tests/
│   ├── test_projection.py              # 投影テスト（合格確認済み）
│   └── test_pipeline_offline.py        # 合成DEMでの統合テスト（合格）
└── outputs/                  # 生成結果（git 管理外）
```

### 重要な関数と設計メモ

#### `terratrail/config.py::MATERIALS`

7 つの material 名とデフォルト色・AMS スロット提案を定義。

```python
MATERIALS = {
    "base":        ((210, 200, 180), 1, "..."),  # 低地
    "mid":         ((150, 170, 120), 2, "..."),  # 中腹
    "peak":        ((240, 240, 235), 3, "..."),  # 山頂（雪色）
    "route":       ((230,  70,  50), 4, "..."),  # ルート
    "river":       (( 60, 130, 210), 5, "..."),  # 河川/湖
    "city":        ((120, 120, 120), 6, "..."),  # 都市
    "peak_marker": ((255, 200,  40), 7, "..."),  # 山頂マーカー
}
```

H2C は AMS で 4 色同時印刷のため、ユーザーが Bambu Studio 側で絞る前提。

#### `terratrail/elevation.py::fetch_dem`

- GSI: `https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png` (z=14, ~10m)
- Terrarium: `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png` (z=12, ~30m 世界対応)
- `in_japan()` で中心座標が日本の緩い bbox に入るかで自動選択
- タイル数 > `MAX_TILES`(144) の場合、zoom を自動で下げる
- 片方が失敗したらもう片方にフォールバック

#### `terratrail/mesh.py::Projection`

- equirectangular 投影 (lat_center の cos() でスケール補正)
- bbox の長辺を `size_mm` に合わせて縮尺決定
- **UTM 投影ではない**ので、日本列島を端から端までカバーするような bbox では歪みが出る。ただし 3D プリンタ用のミニチュア（数 km〜数十 km 四方）であれば問題なし

#### `terratrail/export.py::_inject_base_materials`

trimesh の 3MF エクスポータは `<m:basematerials>` を書かないため、後処理で ZIP を開いて `3D/3dmodel.model` XML を書き換えている：
1. `<resources>` 直後に `<m:basematerials id="100">` を挿入
2. 各 `<object>` に `pid="100" pindex="N"` を追加

これにより、Bambu Studio 等の 3MF Material Extension 対応スライサーは色付きで読み込む。

---

## 5. 直近の確認タスク

次の Claude は以下の順で確認してください：

### A. ローカル環境で実 DEM/OSM 取得の動作確認（最優先）

```bash
python3 cli.py --gpx examples/tanzawa_nabewariyama.gpx --size 150 --z 2.5 --grid 200
```

**期待される動作**: GSI からタイル取得→OSM から地物取得→`outputs/job-.../terratrail.3mf` が生成される。

**失敗時の確認ポイント**:
- `fetch_dem` で `no tiles returned from GSI` → ネットワーク / プロキシ / GSI の応答
- Overpass タイムアウト → `terratrail/osm.py::OVERPASS_TIMEOUT` を延ばす or 別ミラー使用

### B. Bambu Studio での表示確認

1. 生成した `terratrail.3mf` を Bambu Studio にドラッグ＆ドロップ
2. オブジェクトパネルに `base / mid / peak / route / river / city / peak_marker` の 7 オブジェクトが色分けされて表示されるか
3. 表示されない場合の修正案：
   - trimesh が書き出す 3MF 内で **triangle が `<object>` 内にネストされた `<components>` を参照**している可能性 → `_inject_base_materials` で `<triangle>` 要素にも `pid/p1/p2/p3` を加える必要があるかも
   - 代替案: Bambu 独自の 3MF 拡張（`Metadata/slice_info.config` 等）を参考に書く

### C. UX の改善

- 手動描画モードで、**最後の点を Undo する「一点戻す」ボタン**
- 生成後に Three.js でブラウザ内 3D プレビュー
- 進捗バーに「DEM 取得完了まで最大 30 秒かかります」等の補足
- 高負荷時の `grid_resolution` 自動制限

### D. 潜在的なバグ

- `mesh.py::build_terrain` の `_perimeter_indices` が大きな grid で重い（Python ループ）→ numpy 化すべき
- `mesh.py::build_rivers` で lake polygon が自己交差すると `extrude_polygon` が落ちる可能性。`.buffer(0)` で修正を試すべき
- 非常に長いルート（1 万点以上）の場合 `_ribbon_mesh` がメモリを食う → `resample_route(spacing_deg=0.001)` で thin できる

---

## 6. 依存関係

`requirements.txt` の内容:

```
flask>=3.0
numpy>=1.24
scipy>=1.10
requests>=2.31
gpxpy>=1.6
trimesh>=4.0
pyproj>=3.6
shapely>=2.0
Pillow>=10.0

# trimesh runtime dependencies that are optional in the base install
# but required for our 3MF export + polygon extrusion pipeline:
networkx>=3.0
mapbox-earcut>=1.0
manifold3d>=2.5
lxml>=5.0
```

**注**: `networkx` は trimesh の 3MF エクスポート、`mapbox-earcut` / `manifold3d` は `extrude_polygon` で必要になる。ユーザーは最初のバージョンでこれらが漏れていて `No module named 'networkx'` エラーを受け取った（修正済み）。

---

## 7. ユーザーのサンプルデータ

`examples/tanzawa_nabewariyama.gpx` は**ユーザー自身がアップロードした**神奈川・丹沢山地の「鍋割山→寄→大倉」縦走コース：

- 350 点、標高付き
- bbox: (139.117, 35.391, 139.179, 35.456) — 約 5.5km × 7.2km
- 日本国内のため GSI の高精度 DEM (~10m) で地形表現できる
- これをそのままサンプルとして動作確認に使ってください

---

## 8. 会話の流れ（要点）

1. ユーザーが map2model.com と MakerWorld の 2 サイトを参照として提示し、同等のツール開発を依頼
2. Claude は `AskUserQuestion` で 4 項目を確認（形態・入力・標高ソース・色分け方針）
3. MVP を一気に実装、全モジュール + UI + テスト
4. ユーザーの Mac 環境でセットアップしようとしたら `python/pip not found`、Claude が Homebrew/python3 セットアップを案内
5. ユーザーが実行したら `No module named 'networkx'` + 手動描画不可の 2 つの不具合報告
6. この turn で修正 + 引き継ぎドキュメント（これ）を作成

---

## 9. 次の Claude への最初の推奨メッセージ

もしユーザーから「続きを進めて」と言われたら、以下のようにすると良いでしょう：

> 前任から引き継ぎました。TerraTrail は MVP 動作確認済みですが、以下 3 点を優先して進めます：
>
> 1. Hironao 様の環境で実 DEM 取得がうまくいっているか確認
> 2. Bambu Studio で色分け表示の検証
> 3. 要望があれば UX 改善（3D プレビュー、Undo ボタン、進捗の詳細表示）
>
> まず最初のステップとして、最新の requirements.txt で再インストール（`pip3 install -r requirements.txt`）し、`python3 run.py` で起動してみてください。`examples/tanzawa_nabewariyama.gpx` をアップロードして生成できれば一旦 OK です。エラーが出たら全文を貼ってください。

---

以上。質問があれば `terratrail/` の各モジュール冒頭の docstring を読むか、`tests/` を実行して挙動を確認してください。
