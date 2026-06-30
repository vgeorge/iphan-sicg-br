# Progress

Status record for the Brazilian archaeological sites site. Update after each milestone.
See `DECISIONS.md` for technical choices and `README.md` for the overview.

## Milestones

- [x] 1. `build/fetch.py` runs and produces `data/sites-index.json` + sample `data/site/*.json`
- [x] 2. `index.html` renders the table; client-side filters work
- [x] 3. `site.html` renders the three-panel item view
- [x] 4. `README.md` documents sources, build, known gaps, and OSM import status

## Log

### 2026-06-28 (live SICG per-record links; osm name→link; elemento fixes)
- **Per-record IPHAN link found** (supersedes D13): `sicg.iphan.gov.br/sicg/bem/visualizar/<id_bem>`
  is public + live (separate host from the dead portal). Build now emits `id_bem`; the ficha
  links each record to its SICG page ("Ver no SICG"). Found via the archived `detalhes/1699`
  search page → SICG `pesquisarBem` (POST, not archivable per-query) → `bem/visualizar/<id>`
  (GET, per-record, archived & live).
- **osm.html**: name is now the link to `elemento.html` (↔ cmp button removed); table rebuilt
  robustly (precomputed cell HTML, no hidden column) — fixes the broken table/filters.
- **elemento.html**: OSM-ID link was being HTML-escaped by `row()` → fixed (real anchor);
  haversine candidate SQL fixed (`pow` not `power`; negative-coord `--` comment avoided).

### 2026-06-28 (element-centric comparison page + iD/JOSM + triage in Grid.js)
- **`elemento.html`** (D17): page centered on an OSM element (`#{osm_id}`) showing all
  candidate IPHAN matches ranked by haversine distance (pure SQL — `pow/sin/cos/asin`).
  Resolves the element from `sites` (matched) or `osm_orphans` (orphan); badges the current
  match; each candidate offers a `ref:iphan=` copy. Surfaces near-ties (two IPHAN within
  150 m of one OSM feature). Reached from `osm.html` ("↔ cmp") and the ficha.
- **iD + JOSM deep links** (D18) on the ficha and element pages (centered, with selection).
- **`osm.html` rewritten on Grid.js** (pagination/sort/filter) with a "↔ cmp" link per row.
- **ID glossary** in `sobre.html`: `co_iphan` (UF·IBGE·natureza·tipo·seq) vs the free-form
  `identificacao_bem` mnemonic.
- Verified in Chrome: orphan element (1 candidate) and matched element (10 candidates,
  near-tie Catuaba 34.7 m vs Ramal do Iquiri 71 m).

### 2026-06-28 (DuckDB-WASM rebuild + polygons/protection/context + maps)
- **Full DuckDB-WASM rebuild** (D14): site backed by one `data/sites.parquet` (+ `osm_orphans.parquet`)
  queried in the browser. Retired `sites-index.json`, `data/site/*.json`, `osm-unmatched.json`.
  `db.js` boots the vendored single-threaded (MVP) duckdb-wasm; `index`/`site`/`osm`/`stats` query it.
  DuckDB core vendored; Apache Arrow bundled via one-time esbuild (`build/_bundle/`, gitignored).
  No spatial extension — points as lat/lon, polygons as GeoJSON-text, haversine SQL for radius.
- **Enrichment** (D15): build now fetches `SICG:sitios_pol` (footprints, ~44%), `SICG:Bem_Protecao`
  (legal protection, ~39%), `SICG:ctx_imediato` (context, build-time point-in-polygon). Ficha shows
  a Leaflet map (point + polygon + OSM feature) over OSM tiles, plus proteção + contexto sections.
- **Constraint relaxed** (D16): runtime now accepts OSM raster tiles + the auto-loaded DuckDB parquet
  extension (browser-cached); everything else stays local. Priority is speed.
- Verified in Chrome: index (DuckDB filters/pagination, debounce), ficha (map renders polygon+OSM
  point, divergence, protection), osm triage (295 rows from parquet), stats (enrichment coverage).
  Network panel: only `tile.openstreetmap.org`, `extensions.duckdb.org`, and local assets.

### 2026-06-28 (triage workflow + IPHAN context + tighter matching)
- **Triage workflow** in `osm.html` (D12): each OSM orphan classified (candidato /
  sem_cadastro / ref_invalido / triado_no) with a suggested tag, copy button, nearest-IPHAN
  compare link, and "abrir no editor iD". `ref:iphan=no` framed as suggestion, not convention.
- **Tightened proximity threshold to 150 m** (D4, was 500 m): matches 197→170; the 27 weak
  150–500 m matches now surface as triage candidates instead of silent accepts.
- **IPHAN context**: added `sobre.html` (CNSA vs SICG, homologação, field glossary, sources).
  Removed unhelpful per-record IPHAN links from the ficha (no public human-readable per-record
  URL exists — SICG needs login, legacy CNSA dead, GeoServer is raw JSON) (D13).
- Verified in Chrome: triage categories + suggestions + editor links, ficha links, sobre page.

### 2026-06-28 (improvements: Grid.js, stats, match confidence)
- **Index now uses Grid.js** (self-hosted `vendor/gridjs/`): pagination (50/pg, 645 pgs)
  + column sorting over 32k rows, driven by the custom sidebar filters (DECISIONS.md D5).
- **Match confidence exposed** (D10): index shows proximity distance next to the badge
  and sorts by it; orphans now carry the nearest IPHAN distance/id even beyond 500 m —
  31 "quase-acerto" (≤1 km) surfaced in `osm.html`.
- **`stats.html`** (D11): client-side panel — totals, by-UF (% mapped), by-period, match
  method, orphan indicators.
- Verified all pages in Chrome (Grid.js pagination, filter→grid, stats cards, osm column).

### 2026-06-28 (data-quality + filters revision)
- Profiled real data: **Tipo** (`ds_tipo_bem`) is constant "Sítio"; **Coordenadas** are
  100% present → both removed as filters/columns (DECISIONS.md D8).
- **Município** was 66% null (parsed from `sintese_bem`). Now decoded from the IBGE code
  embedded in `co_iphan` via a build-time IBGE fetch → only 4 of 32,227 unresolved (D7).
- Added **`osm.html`** + `data/osm-unmatched.json`: 245 OSM `archaeological_site` features
  with no IPHAN match (1 carries an invalid `ref:iphan`) (D9). Linked from the index.
- Verified all three pages in Chrome after the changes.

### 2026-06-28 (build session)
- **All 4 milestones complete.** Full pipeline + static site built and verified.
- IPHAN GeoServer found alive at the **new host** `geoserver.iphan.gov.br` (layer
  `SICG:sitios`, 32,229 GeoJSON points with coords) → made primary, superseding the
  shapefile fallback (see DECISIONS.md D2). Server rejects `startIndex` paging; whole
  layer fetched in one request via a large `count`.
- OSM matching uses **`ref:iphan`** (not `ref:CNSA`) + ~500 m proximity (DECISIONS.md D4).
- Build output: 32,227 unique sites (2 dup `co_iphan` dropped), 100% with coords,
  **197 mapped** in OSM. `data/` gitignored (~150 MB, regenerable).
- Verified in Chrome: index table + filters render (32.227 count), item page shows all
  three panels and a real divergence (`Catuaba` vs OSM `Geoglifo`).
- Commits: pipeline → UI → docs. Known gaps captured in README.

### 2026-06-28 (bootstrap)
- Repo bootstrapped; reviewed the original build spec.
- Established docs: created `PROGRESS.md` and `DECISIONS.md`.
- Confirmed a **local Overpass** instance at `https://localhost:8080/api/interpreter`
  (self-signed TLS cert → skip verification) — used instead of the public endpoint.
- Found the **IPHAN GeoServer URL stale** (`portal.iphan.gov.br/geoserver/ows`
  302-redirects to `gov.br/iphan`); decided on the CNSA shapefile as primary source.
- No pipeline code written yet — next session starts at milestone 1.
