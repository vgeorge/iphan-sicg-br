#!/usr/bin/env python3
"""Build-time data pipeline for the Brazilian archaeological sites site.

Fetches IPHAN (SICG GeoServer/WFS) and OSM (local Overpass) once, caches the raw
responses under data/raw/, then merges/dedupes IPHAN by `co_iphan`, enriches each site
with its polygonal footprint (SICG:sitios_pol), legal protection acts
(SICG:Bem_Protecao) and landscape context (SICG:ctx_imediato, build-time
point-in-polygon), matches OSM features (ref:iphan tag, else ~150 m proximity), and
writes a single runtime-queryable store:

  data/sites.parquet        one row per site (all fields + geometry + OSM match)
  data/osm_orphans.parquet  OSM archaeological_site features with no IPHAN match

The browser loads these Parquet files via self-hosted DuckDB-WASM (no runtime API
calls except OSM raster tiles for the map). See DECISIONS.md D14/D15.
Re-running skips fetch if raw files exist (use --force to re-fetch).

Usage:
  python build/fetch.py [--force]
"""

import argparse
import collections
import json
import math
import re
import sys
import time
import unicodedata
from html import unescape
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data"

# --- Data sources ------------------------------------------------------------
# IPHAN SICG GeoServer (new host; the old portal.iphan.gov.br is dead). See
# DECISIONS.md D2/D15. The server rejects `startIndex` paging (400), but serves a
# whole layer in one request when given a count above the total.
IPHAN_WFS = "https://geoserver.iphan.gov.br/geoserver/ows"
IPHAN_LAYERS = {
    "sitios": "SICG:sitios",            # points, ~32k — primary
    "sitios_pol": "SICG:sitios_pol",    # MultiPolygon footprints, ~15k
    "protecao": "SICG:Bem_Protecao",    # legal protection acts (all bens)
    "contexto": "SICG:ctx_imediato",    # landscape context polygons, ~8
}
IPHAN_MAXFEATURES = 100000

# Local Overpass (self-signed TLS -> verify disabled). See DECISIONS.md D3.
OVERPASS_URL = "https://localhost:8080/api/interpreter"
OVERPASS_QUERY = """
[out:json][timeout:180];
area["name"="Brasil"]["admin_level"="2"]->.br;
(
  node["historic"="archaeological_site"](area.br);
  way["historic"="archaeological_site"](area.br);
  relation["historic"="archaeological_site"](area.br);
);
out center tags;
""".strip()

PROXIMITY_M = 150.0    # OSM<->IPHAN proximity match radius (tight: ~p75 of real matches)
CANDIDATE_M = 1000.0   # orphan considered a near "candidate" within this distance
POLY_SIMPLIFY = 0.00005  # Douglas-Peucker tolerance (deg) ~5 m; bounds parquet size

# Normalized ref:iphan values that *assert* there is no IPHAN record (vs a real code).
REF_NEGATION = {"NO", "NONE", "NAO", "SEM", "INEXISTENTE"}

# IBGE municipalities — build-time lookup to decode municipality from the 7-digit
# IBGE code embedded in co_iphan (chars 2..9). See DECISIONS.md D7.
IBGE_MUNICIPIOS = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"


# --- Fetch -------------------------------------------------------------------
def fetch_ibge(force=False):
    raw_path = RAW / "ibge_municipios.json"
    if raw_path.exists() and not force:
        print(f"  IBGE: using cached {raw_path.name}")
        data = json.loads(raw_path.read_text())
    else:
        print(f"  IBGE: fetching municipalities from {IBGE_MUNICIPIOS}")
        try:
            data = _get_json(IBGE_MUNICIPIOS, params={}, label="IBGE")
            raw_path.write_text(json.dumps(data, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            print(f"  IBGE: FAILED ({exc}); municipality will fall back to sintese",
                  file=sys.stderr)
            return {}
    return {str(d["id"]): d["nome"] for d in data}


def fetch_layer(key, layer, force=False):
    """Fetch + cache one WFS layer as GeoJSON FeatureCollection."""
    raw_path = RAW / f"{key}.json"
    if raw_path.exists() and not force:
        print(f"  {layer}: using cached {raw_path.name}")
        return json.loads(raw_path.read_text())

    print(f"  {layer}: fetching from {IPHAN_WFS}")
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeName": layer,
        "outputFormat": "application/json",
        "count": IPHAN_MAXFEATURES,
    }
    fc = _get_json(IPHAN_WFS, params=params, label=layer)
    n = len(fc.get("features", []))
    print(f"    fetched {n} / {fc.get('totalFeatures', '?')} features")
    raw_path.write_text(json.dumps(fc, ensure_ascii=False))
    return fc


def fetch_osm(force=False):
    raw_path = RAW / "osm_raw.json"
    if raw_path.exists() and not force:
        print(f"  OSM: using cached {raw_path.name}")
        return json.loads(raw_path.read_text())

    print(f"  OSM: querying local Overpass {OVERPASS_URL}")
    try:
        data = _post_overpass()
    except Exception as exc:  # noqa: BLE001 - record gap, don't abort (spec)
        print(f"  OSM: FAILED after retries ({exc}); recording zero matches",
              file=sys.stderr)
        data = {"elements": [], "_error": str(exc)}
    raw_path.write_text(json.dumps(data, ensure_ascii=False))
    return data


def _post_overpass():
    last = None
    wait = 15
    for attempt in range(1, 4):
        try:
            r = requests.post(OVERPASS_URL, data={"data": OVERPASS_QUERY},
                              verify=False, timeout=200)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"    attempt {attempt} failed: {exc}", file=sys.stderr)
            if attempt < 3:
                time.sleep(wait)
                wait *= 2
    raise last


def _get_json(url, params, label, retries=3):
    wait = 10
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, verify=False, timeout=200)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"    {label} attempt {attempt} failed: {exc}", file=sys.stderr)
            if attempt < retries:
                time.sleep(wait)
                wait *= 2
    raise last


# --- Transform ---------------------------------------------------------------
def slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "sitio"


def norm_ref(value):
    """Normalize an IPHAN/CNSA code for comparison (strip non-alnum, upper)."""
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


def strip_html(text):
    """Collapse HTML to plain text (context fields arrive as markup)."""
    if not text:
        return None
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


_UF = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}


def uf_from_code(code):
    pref = (code or "")[:2].upper()
    return pref if pref in _UF else None


_MUN_RE = re.compile(r"cidade\(s\) de ([^,.]+?)(?:,|\.| é | e |$)", re.IGNORECASE)


def municipality_from_sintese(sintese):
    if not sintese:
        return None
    m = _MUN_RE.search(sintese)
    return m.group(1).strip() if m else None


def municipality_for(code, sintese, ibge_map):
    """IBGE-code lookup first (chars 2..9 of co_iphan), else sintese fallback."""
    ibge_code = (code or "")[2:9]
    name = ibge_map.get(ibge_code)
    if name:
        return name
    return municipality_from_sintese(sintese)


def parse_iphan(fc, ibge_map):
    """-> dict keyed by id, each holding normalized + raw IPHAN fields."""
    sites = {}
    dups = 0
    no_mun = 0
    for feat in fc.get("features", []):
        p = feat.get("properties", {}) or {}
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") if geom else None
        lon = lat = None
        if coords and len(coords) >= 2:
            lon, lat = float(coords[0]), float(coords[1])

        code = p.get("co_iphan")
        name = p.get("identificacao_bem")
        sid = norm_ref(code) or slugify(f"{name}-{uf_from_code(code) or ''}")
        if sid in sites:
            dups += 1
            continue

        municipality = municipality_for(code, p.get("sintese_bem"), ibge_map)
        if not municipality:
            no_mun += 1

        sites[sid] = {
            "id": sid,
            "co_iphan": code,
            "name": name,
            "state": uf_from_code(code),
            "municipality": municipality,
            "site_type": p.get("ds_tipo_bem"),
            "period": p.get("ds_classificacao"),
            "nature": p.get("ds_natureza"),
            "id_bem": p.get("id_bem"),
            "dt_cadastro": p.get("dt_cadastro"),
            "sintese": p.get("sintese_bem"),
            "lat": lat,
            "lon": lon,
            "has_coords": lat is not None and lon is not None,
            "area_geojson": None,
            "protecoes": None,
            "contexto": None,
            "fotos": None,
        }
    print(f"  IPHAN: {len(sites)} unique sites ({dups} duplicates dropped, "
          f"{no_mun} without municipality)")
    return sites


def parse_osm(data):
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None and "center" in el:
            lat = el["center"].get("lat")
            lon = el["center"].get("lon")
        out.append({
            "osm_id": f"{el['type']}/{el['id']}",
            "type": el["type"],
            "lat": lat,
            "lon": lon,
            "tags": tags,
            "ref_iphan_norm": norm_ref(tags.get("ref:iphan")),
        })
    print(f"  OSM: {len(out)} archaeological_site elements")
    return out


# --- Enrich (polygons / protection / context) --------------------------------
def enrich_polygons(sites, pol_fc):
    """Attach the site footprint (SICG:sitios_pol) by id_bem, as simplified GeoJSON."""
    from shapely.geometry import shape
    from shapely import force_2d
    by_idbem = {s["id_bem"]: s for s in sites.values() if s.get("id_bem") is not None}
    n = 0
    for feat in pol_fc.get("features", []):
        s = by_idbem.get((feat.get("properties") or {}).get("id_bem"))
        if not s:
            continue
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            g = shape(geom)
            if not g.is_valid:
                g = g.buffer(0)
            g = force_2d(g)  # SICG polygons carry a Z; GeoJSON/Leaflet want [lon, lat]
            g = g.simplify(POLY_SIMPLIFY, preserve_topology=True)
            if g.is_empty:
                continue
            s["area_geojson"] = json.dumps(g.__geo_interface__, ensure_ascii=False)
            n += 1
        except Exception as exc:  # noqa: BLE001 - skip bad geometry, keep going
            print(f"    polygon skip: {exc}", file=sys.stderr)
    print(f"  polygons: footprints attached to {n} sites")


def enrich_protection(sites, prot_fc):
    """Aggregate legal protection acts (SICG:Bem_Protecao) per id_bem, archaeological only."""
    by_idbem = {s["id_bem"]: s for s in sites.values() if s.get("id_bem") is not None}
    agg = collections.defaultdict(list)
    for feat in prot_fc.get("features", []):
        p = feat.get("properties") or {}
        idb = p.get("id_bem")
        if idb not in by_idbem:           # filter to our archaeological set
            continue
        agg[idb].append({
            "tipo": p.get("ds_tipo_protecao"),
            "condicao": p.get("ds_condicao_protecao"),
        })
    for idb, lst in agg.items():
        by_idbem[idb]["protecoes"] = lst
    print(f"  protection: acts attached to {len(agg)} sites")


def enrich_context(sites, ctx_fc):
    """Attach landscape context (SICG:ctx_imediato) via build-time point-in-polygon."""
    from shapely.geometry import shape, Point
    from shapely.strtree import STRtree
    polys = []
    for feat in ctx_fc.get("features", []):
        p = feat.get("properties") or {}
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            g = shape(geom)
            if not g.is_valid:
                g = g.buffer(0)
            if g.is_empty:
                continue
        except Exception as exc:  # noqa: BLE001
            print(f"    context geom skip: {exc}", file=sys.stderr)
            continue
        polys.append((g, {
            "objeto_analise": strip_html(p.get("objeto_analise")),
            "aspecto_geografico": strip_html(p.get("aspecto_geografico")),
            "morfologia_paisagem": strip_html(p.get("morfologia_paisagem")),
            "sintese_historica": strip_html(p.get("sintese_historica")),
        }))
    if not polys:
        print("  context: no polygons available")
        return
    tree = STRtree([g for g, _ in polys])
    n = 0
    for s in sites.values():
        if not s["has_coords"]:
            continue
        pt = Point(s["lon"], s["lat"])
        for i in tree.query(pt):
            g, ctx = polys[i]
            if g.covers(pt):
                s["contexto"] = ctx
                n += 1
                break
    print(f"  context: attached to {n} sites (across {len(polys)} landscape areas)")


# --- Photos (optional, scraped from the SICG record viewer) ------------------
# The SICG HTML exposes image filenames via MultimidiaServlet?nomeArquivo=<hash>.thumb.jpg.
# Runtime fetch of the HTML is blocked by CORS, so we scrape at build time and store the
# filenames; the ficha then hotlinks the (public, CORS-exempt) <img>.
SICG_VISUALIZAR = "https://sicg.iphan.gov.br/sicg/bem/visualizar/{}"
SICG_RAW = RAW / "sicg"
_THUMB_RE = re.compile(r"nomeArquivo=([0-9a-f]+\.\d+\.thumb\.jpg)")


def _fetch_sicg_page(id_bem):
    """Fetch + cache one SICG record HTML. Returns text or None on failure."""
    path = SICG_RAW / f"{id_bem}.html"
    if path.exists():
        return path.read_text(encoding="utf-8", errors="ignore")
    url = SICG_VISUALIZAR.format(id_bem)
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=60,
                             headers={"User-Agent": "sitios-arqueologicos-br/1.0 (build)"})
            r.raise_for_status()
            path.write_text(r.text, encoding="utf-8")
            return r.text
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1 + attempt)
    print(f"    SICG {id_bem}: failed ({last})", file=sys.stderr)
    return None


def enrich_photos(sites, limit=None):
    """Scrape SICG record pages for photo filenames. Resumable via data/raw/sicg/ cache."""
    SICG_RAW.mkdir(parents=True, exist_ok=True)
    targets = [s for s in sites.values() if s.get("id_bem") is not None]
    if limit:
        targets = targets[:limit]
    n_photos = n_pages = 0
    for i, s in enumerate(targets, 1):
        html = _fetch_sicg_page(s["id_bem"])
        if html is None:
            continue
        n_pages += 1
        thumbs = sorted(set(_THUMB_RE.findall(html)))
        if thumbs:
            s["fotos"] = thumbs
            n_photos += 1
        if i % 500 == 0:
            print(f"    SICG: {i}/{len(targets)} pages, {n_photos} with photos")
    print(f"  photos: {n_photos} sites with photos (of {n_pages} pages fetched)")


# --- Match -------------------------------------------------------------------
def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_grid(sites):
    """Coarse spatial index of IPHAN sites by ~0.01 deg (~1 km) cell."""
    grid = {}
    for s in sites.values():
        if not s["has_coords"]:
            continue
        key = (round(s["lat"] / 0.01), round(s["lon"] / 0.01))
        grid.setdefault(key, []).append(s)
    return grid


def nearest_iphan(lat, lon, grid, span=2):
    """Nearest IPHAN site to a point, searching ±span grid cells. No distance cap.
    Returns (site, distance_m) or (None, None)."""
    if lat is None:
        return None, None
    best, bestd = None, None
    ck = (round(lat / 0.01), round(lon / 0.01))
    for di in range(-span, span + 1):
        for dj in range(-span, span + 1):
            for cand in grid.get((ck[0] + di, ck[1] + dj), []):
                d = haversine_m(lat, lon, cand["lat"], cand["lon"])
                if bestd is None or d < bestd:
                    best, bestd = cand, d
    return best, (round(bestd, 1) if bestd is not None else None)


def match_osm(sites, osm_list):
    by_ref = {s["co_iphan"] and norm_ref(s["co_iphan"]): s
              for s in sites.values() if s["co_iphan"]}
    grid = build_grid(sites)
    matched_ref = matched_prox = 0
    unmatched = []  # OSM elements with no IPHAN correspondence (orphans)

    for o in osm_list:
        target = None
        method = dist = None
        negated = o["ref_iphan_norm"] in REF_NEGATION  # OSM asserts "no IPHAN record"
        # 1) ref:iphan exact (normalized) — skip if it's a negation value
        if not negated and o["ref_iphan_norm"] and o["ref_iphan_norm"] in by_ref:
            target = by_ref[o["ref_iphan_norm"]]
            matched_ref += 1
            method, dist = "ref:iphan", None
        # 2) nearest IPHAN within PROXIMITY_M — but honor an explicit ref:iphan=no
        elif not negated:
            near, neard = nearest_iphan(o["lat"], o["lon"], grid)
            if near is not None and neard <= PROXIMITY_M:
                target, method, dist = near, "proximity", neard
                matched_prox += 1

        if target is None:
            # Orphan: present in OSM, no matching IPHAN record. Classify for triage and
            # record the nearest IPHAN (even beyond the match radius) to support review.
            tags = o["tags"]
            near, neard = nearest_iphan(o["lat"], o["lon"], grid)
            has_code = bool(o["ref_iphan_norm"]) and not negated
            if negated:
                triage = "triado_no"          # already marked ref:iphan=no
            elif has_code:
                triage = "ref_invalido"        # claims a code not found in IPHAN
            elif neard is not None and neard <= CANDIDATE_M:
                triage = "candidato"           # no ref, but an IPHAN site is nearby
            else:
                triage = "sem_cadastro"        # no ref, no IPHAN nearby
            unmatched.append({
                "osm_id": o["osm_id"],
                "osm_type": o["type"],
                "name": tags.get("name"),
                "lat": o["lat"],
                "lon": o["lon"],
                "ref_iphan": tags.get("ref:iphan"),
                "ref_unmatched": has_code,
                "negated": negated,
                "triage": triage,
                "nearest_iphan_id": near["id"] if near else None,
                "nearest_iphan_co": near["co_iphan"] if near else None,
                "nearest_iphan_name": near["name"] if near else None,
                "nearest_iphan_idbem": near["id_bem"] if near else None,
                "nearest_iphan_m": neard,
                "tags_json": json.dumps(tags, ensure_ascii=False),
            })
            continue
        # keep the closest OSM match per site
        existing = target.get("_osm")
        if existing and existing.get("distance_m") is not None and \
           (dist is None or existing["distance_m"] <= (dist or 1e9)):
            if method != "ref:iphan":
                continue
        target["_osm"] = {
            "osm_id": o["osm_id"],
            "osm_type": o["type"],
            "osm_lat": o["lat"],
            "osm_lon": o["lon"],
            "tags": o["tags"],
            "match_method": method,
            "distance_m": dist,
        }

    unmatched.sort(key=lambda u: (u["name"] or "~", u["osm_id"]))
    tri = collections.Counter(u["triage"] for u in unmatched)
    print(f"  matched: {matched_ref} by ref:iphan, {matched_prox} by proximity; "
          f"{len(unmatched)} OSM orphans (no IPHAN match)")
    print(f"  triage: {dict(tri)}")
    return sites, unmatched


def detect_divergences(site, osm):
    """Light divergence checks between IPHAN record and matched OSM tags."""
    divs = []
    tags = osm["tags"]
    osm_name = tags.get("name")
    if site["name"] and osm_name and \
       norm_ref(site["name"])[:12] != norm_ref(osm_name)[:12]:
        divs.append(f"nome: IPHAN '{site['name']}' vs OSM '{osm_name}'")
    return divs


# --- Output ------------------------------------------------------------------
import pyarrow as pa
import pyarrow.parquet as pq

SITES_SCHEMA = pa.schema([
    ("id", pa.string()), ("co_iphan", pa.string()), ("id_bem", pa.int64()), ("name", pa.string()),
    ("uf", pa.string()), ("municipio", pa.string()),
    ("lat", pa.float64()), ("lon", pa.float64()),
    ("tipo", pa.string()), ("periodo", pa.string()), ("nature", pa.string()),
    ("dt_cadastro", pa.string()), ("sintese", pa.string()),
    ("area_geojson", pa.string()), ("protecoes", pa.string()), ("contexto", pa.string()),
    ("osm_mapped", pa.bool_()), ("osm_method", pa.string()), ("osm_distance_m", pa.float64()),
    ("osm_id", pa.string()), ("osm_type", pa.string()), ("osm_tags", pa.string()),
    ("osm_lat", pa.float64()), ("osm_lon", pa.float64()),
    ("status", pa.string()), ("divergences", pa.string()),
    ("fotos", pa.string()),
])

ORPHANS_SCHEMA = pa.schema([
    ("osm_id", pa.string()), ("osm_type", pa.string()), ("name", pa.string()),
    ("lat", pa.float64()), ("lon", pa.float64()),
    ("ref_iphan", pa.string()), ("ref_unmatched", pa.bool_()), ("negated", pa.bool_()),
    ("triage", pa.string()),
    ("nearest_iphan_id", pa.string()), ("nearest_iphan_co", pa.string()),
    ("nearest_iphan_name", pa.string()), ("nearest_iphan_idbem", pa.int64()),
    ("nearest_iphan_m", pa.float64()),
    ("tags_json", pa.string()),
])


def site_row(s):
    osm = s.get("_osm")
    mapped = osm is not None
    divergences = detect_divergences(s, osm) if mapped else []
    status = "nao_mapeado"
    if mapped:
        status = "divergencias" if divergences else "mapeado"
    return {
        "id": s["id"],
        "co_iphan": s["co_iphan"],
        "id_bem": s["id_bem"],
        "name": s["name"],
        "uf": s["state"],
        "municipio": s["municipality"],
        "lat": s["lat"],
        "lon": s["lon"],
        "tipo": s["site_type"],
        "periodo": s["period"],
        "nature": s["nature"],
        "dt_cadastro": s["dt_cadastro"],
        "sintese": s["sintese"],
        "area_geojson": s.get("area_geojson"),
        "protecoes": json.dumps(s["protecoes"], ensure_ascii=False) if s.get("protecoes") else None,
        "contexto": json.dumps(s["contexto"], ensure_ascii=False) if s.get("contexto") else None,
        "osm_mapped": mapped,
        "osm_method": osm["match_method"] if mapped else None,
        "osm_distance_m": osm["distance_m"] if mapped else None,
        "osm_id": osm["osm_id"] if mapped else None,
        "osm_type": osm["osm_type"] if mapped else None,
        "osm_tags": json.dumps(osm["tags"], ensure_ascii=False) if mapped else None,
        "osm_lat": osm["osm_lat"] if mapped else None,
        "osm_lon": osm["osm_lon"] if mapped else None,
        "status": status,
        "divergences": json.dumps(divergences, ensure_ascii=False) if divergences else None,
        "fotos": json.dumps(s.get("fotos"), ensure_ascii=False) if s.get("fotos") else None,
    }


def write_parquet(sites, unmatched_osm):
    rows = [site_row(s) for s in
            sorted(sites.values(), key=lambda x: (x["state"] or "ZZ", x["name"] or ""))]
    table = pa.Table.from_pylist(rows, schema=SITES_SCHEMA)
    pq.write_table(table, OUT / "sites.parquet", compression="zstd")
    otable = pa.Table.from_pylist(unmatched_osm, schema=ORPHANS_SCHEMA)
    pq.write_table(otable, OUT / "osm_orphans.parquet", compression="zstd")

    size = (OUT / "sites.parquet").stat().st_size
    osize = (OUT / "osm_orphans.parquet").stat().st_size
    mapped_n = sum(1 for r in rows if r["osm_mapped"])
    poly_n = sum(1 for r in rows if r["area_geojson"])
    print(f"  wrote sites.parquet ({len(rows)} rows, {size/1e6:.1f} MB; "
          f"{mapped_n} mapped, {poly_n} with footprint)")
    print(f"  wrote osm_orphans.parquet ({len(unmatched_osm)} rows, {osize/1e3:.0f} KB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-fetch raw data")
    ap.add_argument("--images", action="store_true",
                    help="scrape SICG record pages for photos (slow; resumable via data/raw/sicg)")
    ap.add_argument("--images-limit", type=int, default=None,
                    help="cap the number of SICG pages fetched (for testing)")
    args = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    print("[1/5] Fetching IPHAN layers + IBGE")
    sitios_fc = fetch_layer("iphan_raw", IPHAN_LAYERS["sitios"], force=args.force)
    pol_fc = fetch_layer("sitios_pol", IPHAN_LAYERS["sitios_pol"], force=args.force)
    prot_fc = fetch_layer("protecao", IPHAN_LAYERS["protecao"], force=args.force)
    ctx_fc = fetch_layer("contexto", IPHAN_LAYERS["contexto"], force=args.force)
    ibge_map = fetch_ibge(force=args.force)

    print("[2/5] Fetching OSM")
    osm_data = fetch_osm(force=args.force)

    print("[3/5] Transform + enrich")
    sites = parse_iphan(sitios_fc, ibge_map)
    enrich_polygons(sites, pol_fc)
    enrich_protection(sites, prot_fc)
    enrich_context(sites, ctx_fc)

    print("[4/5] Match OSM")
    osm_list = parse_osm(osm_data)
    _, unmatched_osm = match_osm(sites, osm_list)

    if args.images:
        print("[*] Scraping SICG photos")
        enrich_photos(sites, limit=args.images_limit)

    print("[5/5] Writing Parquet")
    write_parquet(sites, unmatched_osm)
    print("Done.")


if __name__ == "__main__":
    main()
