# Runtime data sources

This repository contains a reproducible educational OSM snapshot and normalized
Nanjing runtime layers. They are not official or production GIS datasets.

| Dataset | Preferred runtime source | Network fallback | License | Use |
|---|---|---|---|---|
| Administrative boundary | Nanjing normalized pack, then Jiangsu PBF | OSM Nominatim | ODbL 1.0 | Region extent and area |
| 11 POI types | Nanjing normalized pack, then Jiangsu PBF | OSM Overpass | ODbL 1.0 | Count, density, buffers and distance |
| Main road network | Nanjing normalized pack, then Jiangsu PBF | OSM Shortbread/Overpass | ODbL 1.0 | Road clipping, density and length |
| Water polygons | Nanjing normalized pack, then Jiangsu PBF | OSM Shortbread/Overpass | ODbL 1.0 | Exclusion and distance constraints |

Source files:

- `osm/jiangsu-latest.osm.pbf`: Jiangsu OSM/Geofabrik snapshot, file modification
  time 2026-07-01; the runtime records this timestamp in output provenance.
- `osm/nanjing/*.gpkg`: normalized boundary, university, school, hospital, clinic,
  pharmacy, subway station, park, police, fire station, supermarket, charging
  station, main-road and water layers derived from that PBF.

The runtime order is persistent cache -> normalized Nanjing pack -> Jiangsu PBF ->
allowlisted network fallback. Network data and successful geographic tiles are
cached under `outputs/data_cache`; writes are atomic so an interrupted response is
not treated as a valid cache entry. OSM is community-maintained data and does not
represent an official facility, road or water inventory.

All exposed road workflows use only motorway, trunk, primary, secondary and their
link classes. Tertiary, unclassified, residential, living_street, footway, path,
steps, construction and service roads are excluded. Results are engineering
approximations rather than surveying measurements.

After replacing the PBF, rebuild the normalized pack with:

```powershell
python scripts\build_osm_offline_pack.py
```

`OSM_LOCAL_PBF_PATH`, `BOUNDARY_SOURCE_MODE`, `POI_SOURCE_MODE`,
`ROAD_SOURCE_MODE` and `WATER_SOURCE_MODE` control explicit overrides. Keep their
default `auto` values for local-first behavior.

## Bundled adjacency fixture

`fixtures/nanjing_neighbor_cities.gpkg` is a small GADM 4.1-derived academic test
fixture copied from an earlier project stage. It makes the Nanjing adjacency test
deterministic, may contain historical units, and must not be presented as current
official administrative data.
