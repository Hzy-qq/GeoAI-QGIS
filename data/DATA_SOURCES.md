# Runtime data sources

This repository intentionally contains no production GIS datasets.

| Dataset | Runtime source | License | Use |
|---|---|---|---|
| Administrative boundary | OpenStreetMap Nominatim | ODbL 1.0 | Region extent and area |
| University/college POIs | OpenStreetMap Overpass public instances | ODbL 1.0 | POI count and buffers |
| Road network | OpenStreetMap Overpass public instances | ODbL 1.0 | Road clipping and length |

Data is downloaded into `outputs/tasks/<task_id>/raw` and may be reused through
`outputs/data_cache`. OSM is community-maintained data and does not represent an
official university or road inventory.

The road query includes motorway, trunk, primary, secondary, tertiary,
unclassified, residential, living_street and their link classes. Footways,
paths, steps, construction roads and service roads are excluded.
# State 5 bundled adjacency fixture

`fixtures/nanjing_neighbor_cities.gpkg` is a small GADM 4.1-derived academic test fixture
copied from the earlier project stage. It exists to make the Nanjing multi-turn acceptance
test deterministic. It may contain historical administrative units and must not be presented
as current official administrative data.
