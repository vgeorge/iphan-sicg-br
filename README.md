# Sítios Arqueológicos do Brasil — IPHAN × OpenStreetMap

🌐 **Site ao vivo:** https://vgeorge.github.io/iphan-sicg-br/

Site estático que cruza o cadastro de sítios arqueológicos do **IPHAN (SICG/CNSA)** com
o **OpenStreetMap**, mostrando quais sítios já estão mapeados, quais não estão e onde há
divergências. Interface em português do Brasil.

**Consulta local via DuckDB-WASM**: todos os dados externos (IPHAN, OSM, IBGE) são obtidos
no passo de build (`build/fetch.py`), que gera um Parquet lido no navegador pelo DuckDB-WASM.
Em tempo de execução, o site só acessa a rede para os **tiles do mapa** (OSM) e para baixar a
**extensão `parquet` do DuckDB** (cacheada pelo navegador após o primeiro acesso).

## Como rodar

Pré-requisitos: Python 3.11+, `requests` e uma instância **Overpass local** em
`https://localhost:8080/api/interpreter` (TLS autoassinado).

```sh
# O site já vem com os dados (data/*.parquet versionados) — basta servir:
python -m http.server 8099
# abra http://localhost:8099/index.html

# Para regenerar os Parquet (requer Overpass local em localhost:8080):
python build/fetch.py            # use --force para refazer o download
```

O build baixa ~24 MB do IPHAN e consulta o Overpass local, gravando os brutos em
`data/raw/` (reutilizados em execuções seguintes, **não versionado**). Os arquivos
`data/*.parquet` sim são versionados — são o dado que o site consome.

## Fontes de dados

- **IPHAN — primária.** GeoServer WFS `https://geoserver.iphan.gov.br/geoserver/ows`.
  Quatro camadas `SICG`: `sitios` (pontos, ~32k), `sitios_pol` (polígonos/área),
  `Bem_Protecao` (atos de proteção legal) e `ctx_imediato` (contexto paisagístico). Dados
  de uso público (NUP 72020.002963/2022-34) — atribuição no rodapé do site.
- **OSM — referência cruzada (build-time).** Overpass local, consulta por área nomeada
  `Brasil` (`historic=archaeological_site`). Bruto salvo em `data/raw/osm_raw.json`.
- **IBGE — apoio (build-time).** Lista de municípios para decodificar o município a partir
  do código IBGE embutido no `co_iphan`. Cache em `data/raw/ibge_municipios.json`.

### Correspondência OSM ↔ IPHAN

1. Tag **`ref:iphan`** do OSM normalizada contra o `co_iphan` do IPHAN (igualdade exata).
2. Fallback: **proximidade espacial** (ponto OSM mais próximo dentro de **150 m**).

Situação por sítio: **mapeado** · **não mapeado** · **divergências detectadas** (ex.:
nome diferente entre IPHAN e OSM).

## Estrutura

```
build/fetch.py        pipeline de dados (única origem de fetch) -> emite Parquet
db.js                 helper DuckDB-WASM compartilhado (carrega o Parquet)
index.html            tabela (Grid.js) + filtros — consulta DuckDB
site.html             ficha (mapa Leaflet + IPHAN + OSM + situação), hash routing (#{id})
elemento.html         comparativo centrado num elemento OSM → candidatos IPHAN (#{osm_id})
stats.html            painel de estatísticas — agregação via DuckDB
osm.html              triagem de elementos OSM sem cadastro IPHAN — Grid.js
sobre.html            contexto sobre o cadastro IPHAN (CNSA/SICG, campos, fontes)
vendor/gridjs/        Grid.js auto-hospedado (sem CDN) — versionado
vendor/leaflet/       Leaflet auto-hospedado — versionado
vendor/duckdb/        DuckDB-WASM (core) auto-hospedado — versionado
data/                 Parquet versionado; raw/ ignorado
  sites.parquet       uma linha por sítio (campos + área GeoJSON + proteção + contexto + match OSM)
  osm_orphans.parquet feições OSM órfãs (+ triagem + IPHAN mais próximo)
  raw/                cache bruto do build (IPHAN, OSM, IBGE) — NÃO versionado
```

Filtros do índice refletem a **cardinalidade real** dos dados: removidos "Tipo"
(constante "Sítio") e "Coordenadas" (100% têm). Veja DECISIONS.md D8.

`{id}` é o `co_iphan` normalizado (ex.: `AC1200450BAST00080`).

## Números (último build)

- **32.227** sítios IPHAN únicos (2 duplicatas por `co_iphan` descartadas).
- **100%** com coordenadas (SICG é homologado).
- **Município** resolvido para 32.223 (só 4 sem) via código IBGE.
- **14.326** com polígono de área (`SICG:sitios_pol`), **12.539** com ato de proteção
  legal (`SICG:Bem_Protecao`), **125** em área de contexto (`SICG:ctx_imediato`).
- **170** mapeados no OSM (3 por `ref:iphan`, demais por proximidade ≤150 m).
- **295** feições OSM sem correspondência no IPHAN, em triagem (81 candidatos próximos,
  213 sem cadastro aparente, 1 `ref:iphan` inválido) → `osm.html`.
- **`data/sites.parquet`**: ~2,8 MB (zstd).

## Lacunas conhecidas

- **Cobertura OSM baixa**: só ~0,6% dos sítios IPHAN têm correspondência no OSM — esperado,
  já que sítios arqueológicos costumam ter localização sigilosa.
- **`ref:iphan` raro**: apenas ~4 feições OSM trazem a tag; o casamento depende sobretudo
  de proximidade, que pode gerar falsos positivos/negativos perto do limiar de 500 m.
- **Município** vem do código IBGE no `co_iphan` (resolve ~100%); 4 registros com código
  fora da lista IBGE caem no fallback de texto e podem ficar sem município.
- **Tipo/Natureza** são praticamente constantes no SICG ("Sítio" / "Bem Arqueológico",
  com só 3 paleontológicos); por isso não viram filtro.
- **Período** inclui ~45% "Sem classificação" e alguns valores ruidosos (ex.: "Sítio").
- Camada de **polígonos** `SICG:sitios_pol` não é usada (apenas pontos).

## Status de importação OSM

Este projeto é **exploratório/analítico** — não realiza upload para o OSM. Qualquer
importação futura deve seguir as diretrizes da comunidade
([Import guidelines](https://wiki.openstreetmap.org/wiki/Import/Guidelines)) e discussão
prévia, observando a sensibilidade de localização de sítios arqueológicos.

## Licenças

Os `data/*.parquet` combinam dados IPHAN/SICG (uso público, NUP 72020.002963/2022-34)
com campos derivados do OpenStreetMap ([ODbL](https://www.openstreetmap.org/copyright)).
A porção originada do OSM (tags e ids correspondidos) permanece sob ODbL; redistribuição
requer atribuição ao OpenStreetMap. O código deste repositório é MIT.
