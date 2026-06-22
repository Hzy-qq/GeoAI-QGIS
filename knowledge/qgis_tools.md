# QGIS Tool Knowledge

This document describes the QGIS tools currently available to the GeoAI agent.

## buffer

Purpose: create buffer polygons around input features.

QGIS algorithm: `native:buffer`

Required params:
- `INPUT`: input vector layer, currently `data/processed/places.gpkg`
- `DISTANCE`: buffer distance in meters
- `OUTPUT`: output buffer file path

Optional params:
- `SEGMENTS`: number of segments for rounded buffer edges
- `DISSOLVE`: whether to dissolve buffer features

Typical output:
- `outputs/places_buffer_Nm.gpkg`

## clip

Purpose: clip input features by an overlay polygon layer.

QGIS algorithm: `native:clip`

Required params:
- `INPUT`: input vector layer, currently `data/processed/roads.gpkg`
- `OVERLAY`: overlay polygon layer, usually the places buffer
- `OUTPUT`: clipped output file path

Typical output:
- `outputs/roads_clip_Nm.gpkg`

## sum_line_lengths

Purpose: calculate total line length inside polygons.

QGIS algorithm: `native:sumlinelengths`

Required params:
- `POLYGONS`: polygon layer, usually the places buffer
- `LINES`: line layer, usually clipped roads
- `OUTPUT`: output polygon layer with length fields

Optional params:
- `LEN_FIELD`: length field name, use `road_length`
- `COUNT_FIELD`: count field name, use `road_count`

Typical output:
- `outputs/buffer_with_road_length_Nm.gpkg`

