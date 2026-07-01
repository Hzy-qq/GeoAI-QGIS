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
