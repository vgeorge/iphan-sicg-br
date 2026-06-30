# Decisions

Lightweight ADR log. Append a new entry when a technical choice is made; do not rewrite
history. See `PROGRESS.md` for status and `README.md` for the overview.

## D1 — Fully static, no server runtime
Browser reads only JSON generated at build time; no runtime third-party API calls.
Any JS dependency self-hosted in `vendor/` (no CDN) so the built site works offline.

## D2 — IPHAN SICG GeoServer/WFS is the primary source
**Superseded the earlier shapefile-fallback decision.** The spec's endpoint
`portal.iphan.gov.br/geoserver/ows` is dead (redirects to gov.br), but the GeoServer
moved to a **new host that is fully alive**: `https://geoserver.iphan.gov.br/geoserver/ows`.
Layer `SICG:sitios` serves **32,229 point features as GeoJSON with coordinates**
(CRS EPSG:4674 ≈ WGS84) and clean fields: `co_iphan` (IPHAN code), `identificacao_bem`
(name), `ds_classificacao` (period), `ds_tipo_bem` (type), `sintese_bem` (synthesis incl.
municipality), `dt_cadastro`. This is better than the shapefile (has coords + structured
fields), so GeoServer is primary. UF derived from `co_iphan` prefix; municipality parsed
from `sintese_bem`. Dedupe by `co_iphan`. (`SICG:sitios_pol` polygons exist but are not
used.) Shapefile remains a future fallback if the host goes down.

## D3 — OSM fetched from local Overpass
Build pulls OSM data from the local Overpass instance at
`https://localhost:8080/api/interpreter`, not the public `overpass-api.de`. The endpoint
uses a self-signed TLS cert, so the build skips cert verification (Python `verify=False`
/ `NODE_TLS_REJECT_UNAUTHORIZED=0`). Brazil named-area query (not bbox). Raw saved to
`data/raw/osm_raw.json`.

## D4 — OSM ↔ IPHAN matching
Match by the **`ref:iphan`** tag on the OSM feature first (an earlier draft used `ref:CNSA`, but
real OSM-BR data uses `ref:iphan`, e.g. `SP-3526407-BA-ST-00001`). Normalize both sides
(strip non-alphanumerics, uppercase) before comparing to IPHAN `co_iphan`
(e.g. `GO5203203BAST00017`) — same structure once dashes are removed. Only ~4 of 489 OSM
features carry the tag, so the main matcher is **spatial proximity** (nearest IPHAN point
via a coarse grid index). Outcome per site: mapeado / não mapeado / divergências.

**Threshold tightened to 150 m** (was 500 m). At 500 m a "match" is weak evidence — two
distinct sites can lie within half a kilometer. The real proximity matches cluster tightly
(median 54 m, p75 105 m, p90 212 m), with the nearest orphan at 502 m. 150 m keeps the
confident core; matches that were 150–500 m are now **demoted to triage candidates**
instead of silently accepted. Tunable via `PROXIMITY_M` in `build/fetch.py`.

`ref:iphan` negation values (`no`/`none`/`não`/…) are honored as an explicit "no IPHAN
record" assertion and excluded from matching.

## D5 — Vanilla JS + hash routing (+ Grid.js for the index)
Plain HTML + vanilla JS, no Node build step. Item pages routed via URL fragment
(`site.html#{id}`). **Amended:** the index table uses **Grid.js** (self-hosted in
`vendor/gridjs/`, MIT, ~53 KB) for pagination + column sorting over 32k rows — the
500-row render cap was inadequate for browsing. Sidebar filters stay custom and drive
Grid.js via `updateConfig().forceRender()`. `stats.html`, `site.html`, `osm.html` remain
pure vanilla. No CDN; `vendor/` is committed so the site works offline.

## D6 — No unlabelled mock data
Never ship mock/placeholder data as a silent fallback; if used, label it clearly in
both the UI and the docs.

## D7 — Municipality from IBGE code, not sintese text
`co_iphan` embeds the **7-digit IBGE municipality code** (chars 2..9, e.g.
`RS4319406…` → `4319406` = São Pedro do Sul). Decode it against a build-time fetch of
the IBGE municipality list (`servicodados.ibge.gov.br/api/v1/localidades/municipios`,
cached to `data/raw/ibge_municipios.json`). This resolves ~100% of records (4 misses of
32,229) vs only ~34% from parsing `sintese_bem` (which is null in 44% of records).
Sintese parsing remains the fallback.

## D8 — Filters revised to match real data cardinality
After profiling, dropped two useless controls: **Tipo** (`ds_tipo_bem` is the constant
"Sítio" for all 32k records) and **Coordenadas** (100% have coords). Kept Busca, UF (27),
Período (`ds_classificacao`, 10 real values incl. ~45% "Sem classificação"), Status OSM.
Same columns removed from the index table.

## D9 — Surface OSM elements with no IPHAN match
The build emits `data/osm-unmatched.json`: OSM `archaeological_site` features in Brazil
that matched **no** IPHAN record (neither by `ref:iphan` nor ~500 m proximity) — 245 of
489. Shown on a dedicated page `osm.html`, flagging any that carry a `ref:iphan` value
which wasn't found in the cadastre (invalid/stale reference). Helps spot sites missing
from IPHAN, mispositioned features, or bad refs.

## D10 — Expose match confidence (distance / near-miss)
Index rows carry `osm_method` + `osm_distance_m`; the table shows the proximity distance
next to the "mapeado" badge and the OSM column is sortable by it. Orphans carry the
**nearest IPHAN distance/id even beyond the 500 m cap** (`nearest_iphan_m`,
`nearest_iphan_id`, searched within ±2 grid cells ≈ a few km). `osm.html` flags
"quase-acerto" (≤1 km) — 31 cases just past the threshold, likely the same site. Surfaces
where the proximity threshold is borderline instead of hiding it.

## D11 — Stats panel computed client-side
`stats.html` aggregates `sites-index.json` + `osm-unmatched.json` in the browser (totals,
by-UF with % mapped, by-period, match method, orphan indicators). No precomputed
stats.json — 32k client-side aggregation is instant and keeps the build simpler.

## D12 — Triage workflow for OSM orphans
`osm.html` is a **triage tool**, not just a list. Each OSM orphan is classified:
`candidato` (no ref, but an IPHAN site within 1 km — suggests `ref:iphan=<co_iphan>`),
`sem_cadastro` (no IPHAN nearby — suggests `ref:iphan=no`), `ref_invalido` (carries a
code not found in IPHAN), `triado_no` (already `ref:iphan=no`). Each row offers a
copy-the-tag button, a link to compare the nearest IPHAN ficha, and an "abrir no editor
iD" link. **`ref:iphan=no` is presented as a suggestion, not established convention** —
taginfo shows it is not yet used in OSM. The site is static and never edits OSM itself.

## D13 — No per-record IPHAN deep links
There is no public, human-readable per-record IPHAN URL: SICG requires login, the legacy
CNSA/SGPA consultation is dead (redirects to gov.br), and the GeoServer only returns raw
JSON. So the ficha links only to the internal `sobre.html` for context. `sobre.html`
covers CNSA vs SICG, homologation, a field glossary, and official source links.

**Superseded (2026-06-28):** the SICG record viewer **is** public and live on a separate
host — `https://sicg.iphan.gov.br/sicg/bem/visualizar/<id_bem>` (no login; verified 200,
returns the full ficha by `id_bem`, e.g. `…/visualizar/18989` = Riacho dos Bois I). The
build now emits `id_bem` and the ficha links each record to its SICG page ("Ver no SICG").
The general `portal.iphan.gov.br` consultation is still dead; `sicg.iphan.gov.br` is the
working surface. (The Wayback Machine archives `bem/visualizar/<id>` too, as a fallback.)

## D14 — DuckDB-WASM rebuild (Approach A: core, no spatial extension)
The whole site is now backed by one Parquet queried in the browser via **DuckDB-WASM**.
`build/fetch.py` emits `data/sites.parquet` (one row/site) + `data/osm_orphans.parquet`;
the old JSON outputs (`sites-index.json`, `data/site/*.json`, `osm-unmatched.json`) are
retired. `index`/`site`/`osm`/`stats` all query the shared `db.js` helper, which boots the
vendored **single-threaded (MVP)** duckdb-wasm build (needs no SharedArrayBuffer/COI
headers, so it runs under plain `python -m http.server`).

**Approach A** = DuckDB core only, **no spatial extension.** The `spatial` extension
auto-loads from `extensions.duckdb.org` (a network call), so we avoid it: points are
`lat`/`lon` columns, polygons are GeoJSON-text, and radius queries use haversine in SQL.
Maps are rendered by Leaflet from the GeoJSON text. Geometry display and proximity queries
need no extension.

**Vendoring:** duckdb-wasm core (`duckdb-mvp.wasm` + worker) is **vendored** locally. The
**Apache Arrow** dependency is bundled into `vendor/duckdb/duckdb.mjs` at vendoring time via
a one-time esbuild step (`build/_bundle/`, gitignored); the bundled output IS committed. No
app build step at runtime.

**Bootstrap gotchas (1.32.0):** the MVP worker is a *classic* worker (no `{type:'module'}`);
do **not** call `db.open()` (connect opens implicitly); queries go through a connection
(`conn.query`), not `db.query`; `db.open()` with no config throws because the worker reads
`data.path`. See `db.js`.

## D15 — Enrich with polygons, legal protection, landscape context
The build now fetches four IPHAN WFS layers and joins them (all key on `id_bem` except
context, which is spatial):
- **`SICG:sitios`** (points, ~32k) — primary, as before.
- **`SICG:sitios_pol`** (MultiPolygon, ~14.8k) — site footprint. Attached by `id_bem`,
  simplified (Douglas-Peucker ~5 m) and stored as GeoJSON-text `area_geojson`. ~44% of sites.
- **`SICG:Bem_Protecao`** (Point, ~18.4k) — legal protection acts (`ds_tipo_protecao`,
  `ds_condicao_protecao`). Covers *all* bens → filtered to our archaeological `id_bem` set;
  multiple acts per bem aggregated into JSON array `protecoes`. ~39% of sites.
- **`SICG:ctx_imediato`** (Polygon, 8) — landscape/cultural context (macro areas), keyed by
  `id_contexto_imediato`. Attached by **build-time point-in-polygon** (shapely STRtree) →
  `contexto` JSON (HTML stripped). Only 125 sites fall in these 8 areas.

The ficha (`site.html`) now renders a **Leaflet map** (point + polygon + OSM feature) plus
proteção and contexto sections.

## D16 — Runtime network calls relaxed (OSM tiles + DuckDB parquet extension)
The original "no runtime third-party calls" constraint is **relaxed**. Priority shifted to
speed, and two runtime network calls are now accepted:
- **OSM raster tiles** (`tile.openstreetmap.org`) — the map base layer (Leaflet).
- **DuckDB `parquet` extension** (`extensions.duckdb.org`) — auto-loaded once on first
  `read_parquet`, then browser-cached. The duckdb-wasm core itself and Leaflet are **vendored
  locally** (no CDN). IPHAN/OSM/IBGE data is still fetched only at build time and read from
  local Parquet; no cadastre data crosses the network at runtime.

## D17 — Element-centric comparison page (`elemento.html`)
`osm.html` triages OSM orphans and `site.html` is IPHAN-centric (one IPHAN site → its
single matched OSM). The missing view is **OSM-element-centric**: given an OSM
`archaeological_site`, show *all* candidate IPHAN matches ranked by distance, so a mapper
can pick the right `ref:iphan` (or confirm none). `elemento.html#{osm_id}` resolves the
element from `sites` (matched) or `osm_orphans` (orphan), then runs a **haversine candidate
query in pure SQL** (DuckDB has `sin/cos/asin/sqrt/pow/radians` built-in; the `spatial`
extension is not needed). Each candidate shows distance + label (provável ≤150 m /
possível ≤1 km / distante), name→ficha, `co_iphan`, a `ref:iphan=` copy suggestion, and the
current match is badged. Reached from `osm.html` ("↔ cmp") and the ficha ("↔ Possíveis
matches IPHAN deste elemento"). Surfacing near-ties (e.g. two IPHAN sites within 150 m of
one OSM feature) is the point.

## D18 — iD + JOSM deep links
Ficha (`site.html`) and both element pages offer editor deep links centered on the site:
**iD** (`openstreetmap.org/edit?editor=id&{type}={id}#map=18/lat/lon`) and **JOSM remote
control** (`127.0.0.1:8111/load_and_zoom?…&select={type}{id}`). JOSM needs remote control
enabled. The ficha uses these for the matched element (or just the area when unmapped);
`elemento.html`/`osm.html` target the OSM element. The site never edits OSM itself.



