# TerraTrail

**冒険の軌跡を Bambu Lab H2C 用マルチカラー 3D マップに変換するツール**

GPX ファイル（Strava / Garmin / ヤマレコ 等）または地図上で描画した手動ルートから、地形（DEM）・河川・都市・山頂を色分けした 3D モデル（3MF / STL）を生成します。出力された `.3mf` は Bambu Studio で読み込むと各パーツが色分けされた状態で表示され、AMS スロットに直接マッピングしてマルチカラー印刷できます。

## 主な機能

- **入力**
  - GPX ファイル（トラック + ルート両対応）
  - 地図クリックによる手動ルート入力
- **標高データ自動切替**
  - 日本国内 → **国土地理院 DEM PNG**（約 10m メッシュ）
  - それ以外 → **AWS Terrain Tiles（Terrarium）**（世界 ~30m）
- **地物抽出（OpenStreetMap / Overpass API）**
  - 河川・水路 (`waterway=river|stream|canal`)
  - 湖沼 (`natural=water`)
  - 都市・町・村 (`place=city|town|village`、人口で半径スケール)
  - 山頂 (`natural=peak`、標高タグ反映)
- **色分け 7 レイヤー**
  - `base`（低地）/ `mid`（中腹）/ `peak`（山頂）
  - `route`（ルート）/ `river`（河川・湖）
  - `city`（都市）/ `peak_marker`（山頂マーカー）
- **出力レイヤーは bbox の外にはみ出さない** — OSM が返す川や道は通常 bbox 境界を越えて続いていますが、Shapely で DEM の矩形にクリッピングしてから 3D 化します。
- **Bambu 向け出力**
  - **`terratrail.obj` + `terratrail.mtl`** — 色付き OBJ（推奨）。MTL の `Kd` で 7 色を指定しており、Bambu Studio にドラッグするだけで自動的に色分け認識されます。
  - **`terratrail_<layer>.stl`** — レイヤーごとの個別 STL。手動で AMS 割当したい場合に使用。
  - **`terratrail_all.stl`** — 単色プレビュー用の結合 STL。
  - **`terratrail.3mf`** — 予備。`<m:basematerials>` 埋め込みの 3MF。
  - **`terratrail_<job>.zip`** — 上記 + `manifest.json` をまとめた ZIP。

## インストール

Python 3.10+ を推奨。

```bash
pip install -r requirements.txt
```

## 使い方

### A. Web UI（推奨）

```bash
python run.py
# → http://127.0.0.1:5000/
```

左ペインで GPX をアップロード（または右の地図をクリックして手動ルートを描画）し、パラメータを調整して「モデル生成」を押すと、進捗バーが動き終わるとダウンロードリンクが表示されます。生成された `terratrail.3mf` を Bambu Studio で開いてください。

### B. コマンドライン

```bash
# GPX ファイルから
python cli.py --gpx examples/takao.gpx --size 150 --z 2.5 --grid 200 --out ./outputs

# 手動座標（lon,lat を複数）
python cli.py --coords 138.70,35.35 138.78,35.37 138.80,35.42 --size 120

# 機能の有効/無効
python cli.py --gpx my.gpx --no-rivers --no-cities
```

### 主要オプション

| オプション | 既定値 | 説明 |
|---|---|---|
| `--size` | 150 mm | モデル長辺（他軸はアスペクト維持） |
| `--z` | 2.0 | 垂直倍率（景観を強調） |
| `--grid` | 200 | DEM 格子点数（高いほど細かいが遅い） |
| `--dem` | auto | `auto` / `gsi` / `terrarium` |
| `--route-width` | 1.2 mm | ルート帯の幅 |
| `--route-height` | 1.0 mm | ルート帯の高さ |

`terratrail/config.py` の `GenerationOptions` にて更に多くのパラメータ（河川の深さ、都市パッドの厚み、バンド閾値 など）を調整できます。

## 出力を Bambu Studio で使う

**推奨：色付き OBJ 経由**

1. `terratrail.obj` と `terratrail.mtl` を **同じフォルダ** に置いたまま、`.obj` を Bambu Studio の画面にドラッグ＆ドロップ。
2. 自動で 7 色が認識され、各色ごとに AMS スロットがアサインされます。必要ならスロット番号を調整してください（既定候補は `manifest.json` の `suggested_ams_slot`）。
3. スライス → H2C に送信。`peak_marker` など小さなパーツはサポートの有無・造形方向に注意。

**代替：個別 STL で手動割当**

`terratrail_<layer>.stl` を複数インポートし、各ボディに任意のフィラメントを割り当てる古典的な方法も使えます。

## データソース & 利用規約

- **国土地理院 DEM PNG**: [利用規約](https://maps.gsi.go.jp/development/ichiran.html)。非商用・個人利用の範囲でご利用ください。大量アクセスはしないでください。
- **AWS Terrain Tiles**: [レジストリ](https://registry.opendata.aws/terrain-tiles/)（CC BY 4.0 相当）。
- **OpenStreetMap / Overpass API**: [© OpenStreetMap contributors](https://www.openstreetmap.org/copyright)（ODbL）。ツールは軽い利用を想定しており、本番用途では[自前 Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API/Installation) の利用を検討してください。
- **OSM タイル（地図プレビュー）**: [使用ポリシー](https://operations.osmfoundation.org/policies/tiles/)。

## 仕組み

```
 GPX / 手動ルート
      │
      ▼
  bbox 計算  ──▶  DEM 取得（GSI or Terrarium）
                       │
                       ▼
                OSM 地物取得（河川/都市/山頂）
                       │
                       ▼
 projection（緯度経度→mm）  ──▶  地形メッシュ（3 バンド）
                                   + ルート帯
                                   + 河川帯 / 湖
                                   + 都市パッド
                                   + 山頂コーン
                       │
                       ▼
         per-material trimesh コレクション
                       │
           ┌───────────┴───────────┐
           ▼                       ▼
      個別 STL × N           3MF（<m:basematerials> 付き）
```

## ディレクトリ構成

```
TerraTrail/
├── run.py                    # Flask エントリポイント
├── cli.py                    # コマンドライン
├── requirements.txt
├── terratrail/
│   ├── config.py             # 色・既定値・エンドポイント
│   ├── gpx_loader.py         # GPX/手動入力パーサ
│   ├── elevation.py          # GSI / Terrarium DEM タイル取得
│   ├── osm.py                # Overpass API 地物取得
│   ├── mesh.py               # 3D メッシュ構築（地形・ルート・地物）
│   ├── export.py             # STL / 3MF 書き出し + 色情報注入
│   ├── pipeline.py           # 上記を束ねるオーケストレータ
│   └── app.py                # Flask Web UI + REST API
├── templates/index.html      # Leaflet UI
├── static/{js,css}/          # フロントエンドアセット
├── examples/takao.gpx        # 動作確認用サンプル（高尾山周回）
├── tests/
│   ├── test_projection.py        # 投影の単体テスト
│   └── test_pipeline_offline.py  # ネット非依存の統合テスト
└── outputs/                  # 生成結果（git 管理外）
```

## 既知の制限と今後の拡張案

- **DEM 解像度**: GSI dem_png 標準タイル（~10m）で数 km 四方までを想定。30km 以上の広域は `--grid` を上げるか zoom を自動で下げます。
- **Overpass 依存**: パブリック Overpass は重量アクセスを禁止しています。自前 Overpass があれば `config.py` の `OVERPASS_URL` を差し替えてください。
- **3D プレビュー**: 現状は生成後にダウンロードのみ。将来的に Three.js でブラウザ内プレビューを追加予定。
- **ラベル**: 山頂・都市名のテキストを埋め込むオプションは未実装（テキスト 3D 化は別途対応）。
- **マルチトラック**: 複数トラックはひとつのルート色にまとめられます。セグメント別に色を変えるには `config.MATERIALS` に追加スロットを定義し、`mesh.build_route` を拡張してください。

## ライセンス

MIT（ソースコード）。生成される 3D モデルは、元データ（GSI / AWS / OSM）の利用規約に従います。
