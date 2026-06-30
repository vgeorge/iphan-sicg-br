// Shared DuckDB-WASM helper. Loads the vendored single-threaded (MVP) build, which
// needs no SharedArrayBuffer/COI headers, so it works under plain `python -m http.server`.
// The two parquet files are fetched once and registered as virtual files, then exposed
// as views `sites` and `osm_orphans`. No runtime third-party calls (data is local).
//
// Usage (from a module script):
//   import { initDB, rows, scalar, q } from './db.js';
//   await initDB();
//   const r = await rows(`SELECT * FROM sites WHERE uf = ${q('BA')} LIMIT 50`);

import { AsyncDuckDB, ConsoleLogger } from './vendor/duckdb/duckdb.mjs';

const WORKER = new URL('vendor/duckdb/duckdb-browser-mvp.worker.js', import.meta.url);
const WASM = new URL('vendor/duckdb/duckdb-mvp.wasm', import.meta.url);

const FILES = {
  'sites.parquet': new URL('data/sites.parquet', import.meta.url),
  'osm_orphans.parquet': new URL('data/osm_orphans.parquet', import.meta.url),
};

let _initP = null;
let _conn = null;

async function build() {
  const worker = new Worker(WORKER);
  const db = new AsyncDuckDB(new ConsoleLogger(), worker);
  await db.instantiate(WASM.href);
  for (const [name, url] of Object.entries(FILES)) {
    const buf = new Uint8Array(await (await fetch(url)).arrayBuffer());
    await db.registerFileBuffer(name, buf);
  }
  _conn = await db.connect();
  await _conn.query("CREATE OR REPLACE VIEW sites AS SELECT * FROM read_parquet('sites.parquet')");
  await _conn.query("CREATE OR REPLACE VIEW osm_orphans AS SELECT * FROM read_parquet('osm_orphans.parquet')");
}

export function initDB() {
  if (!_initP) _initP = build();
  return _initP;
}

/** SQL-quote a value for safe interpolation (single quotes doubled). */
export function q(v) {
  return "'" + String(v == null ? '' : v).replace(/'/g, "''") + "'";
}

// DuckDB COUNT()/int64 columns arrive as JS BigInt; coerce to Number (counts are small).
const num = (v) => (typeof v === 'bigint' ? Number(v) : v);

/** Run SQL; returns an Apache Arrow Table. */
export async function arrow(sql) {
  await initDB();
  return _conn.query(sql);
}

/** Run SQL; returns an array of plain JS row objects. */
export async function rows(sql) {
  const table = await arrow(sql);
  const fields = table.schema.fields.map(f => f.name);
  return table.toArray().map(r => {
    const o = {};
    for (const f of fields) o[f] = num(r[f]);
    return o;
  });
}

/** Run SQL that returns a single scalar (first column of first row), or null. */
export async function scalar(sql) {
  const table = await arrow(sql);
  const r = table.toArray()[0];
  if (!r) return null;
  return num(r[table.schema.fields[0].name]);
}
