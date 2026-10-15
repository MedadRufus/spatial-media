# Madurai Bus Network map

An interactive map of TNSTC Madurai city bus routes, drawn over open
OpenStreetMap bus stops and bus stands.

**Works fully offline.** `index.html` is one self-contained file. Leaflet,
the map styling, the basemap (roads, river, tanks) and every route are
embedded, and it uses system fonts. Copy it to your phone or laptop and open
it in any browser with no signal. Nothing is fetched at runtime. The only
network-dependent items are the optional "Full stop list on MaduraiOne" links.

## What open data exists (October 2026)

| Source | Madurai bus coverage |
| --- | --- |
| OpenStreetMap | ~160 bus stops and ~15 bus stands. **No bus route relations at all.** |
| Mobility Database (GTFS catalog) | Feeds for Chennai, Bengaluru, Delhi, Mumbai and others. **Nothing for Madurai.** |
| OpenCity / data.gov.in | Ward boundaries only. |

No openly licensed dataset of Madurai bus routes exists today, so the map
keeps two kinds of layer apart.

## Layers

| File | Content | Source and licence |
| --- | --- | --- |
| `data/osm_bus_stops.geojson` | Bus stops and platforms | © OpenStreetMap contributors, ODbL |
| `data/osm_bus_stations.geojson` | Bus stands (Periyar, Mattuthavani, Arappalayam, Anna and others) | © OpenStreetMap contributors, ODbL |
| `data/osm_basemap.geojson` | Main roads, the Vaigai river and tanks, used as the basemap | © OpenStreetMap contributors, ODbL |
| `data/maduraione_routes.geojson` | All 966 route variants (926 bus numbers) | Stop lists: [MaduraiOne CityBus](https://maduraione.in). Geometry: OSM, Nominatim and OSRM |

### About the route layer

* The route list (bus number, route name, corridor, Periyar stand platform and
  main stops) comes from MaduraiOne's public MCP endpoint
  (`https://api.maduraione.in/mcp`). MaduraiOne publishes no open licence for
  this data. Check their terms before you reuse or redistribute the route
  layer. The OSM-derived layers are ODbL.
* That endpoint returns only about six main stops per route, not every
  intermediate stop.
* Each main stop is placed by matching its name against OSM (bus stops,
  places, landmarks). Nominatim is the fallback. Stops that cannot be placed
  are marked "not on map" in the route panel. Any stop that would cause an
  implausible detour is dropped.
* The located stops are joined along the road network with the public
  [OSRM](https://project-osrm.org/) router (car profile). **The lines show
  plausible paths between main stops. They are not surveyed bus paths.**
  Routes that OSRM could not join are drawn as dashed straight lines.

## Rebuild

```sh
pip install requests
python3 fetch_data.py      # writes data/*.geojson; caches responses in .cache/
python3 build_map.py       # bundles template.html + data + vendor/leaflet into index.html (offline)
```

`fetch_data.py` rate-limits itself (about 2 requests/s to MaduraiOne, 1/s to
Nominatim and OSRM) and caches every response, so reruns are fast.

## Making the routes open

The lasting fix is to map the routes as
[OSM bus route relations](https://wiki.openstreetmap.org/wiki/Relation:route),
for example with a field survey and the PTNA tools. Once they exist,
`fetch_data.py` can read the route layer directly from OSM.
