#!/usr/bin/env python3
"""Bundle template.html + data/*.geojson into a single self-contained index.html."""
import json
import os

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LEAFLET_CSS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"


def load(name):
    with open(os.path.join(HERE, "data", name)) as f:
        return json.load(f)


def main():
    data = {
        "routes": load("maduraione_routes.geojson"),
        "stops": load("osm_bus_stops.geojson"),
        "stations": load("osm_bus_stations.geojson"),
        "basemap": load("osm_basemap.geojson"),
    }
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    css = requests.get(LEAFLET_CSS, timeout=60).text
    with open(os.path.join(HERE, "template.html")) as f:
        html = f.read()
    html = html.replace("/*__LEAFLET_CSS__*/", css).replace("__DATA__", blob)
    out = os.path.join(HERE, "index.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
