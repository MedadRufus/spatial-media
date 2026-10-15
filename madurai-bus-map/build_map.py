#!/usr/bin/env python3
"""Bundle template.html + data/*.geojson into a single self-contained index.html."""
import json
import os

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/"
VENDOR = os.path.join(HERE, "vendor")


def vendored(name):
    """Leaflet is kept in vendor/ so building and viewing need no network."""
    path = os.path.join(VENDOR, name)
    if not os.path.exists(path):
        os.makedirs(VENDOR, exist_ok=True)
        r = requests.get(LEAFLET + name, timeout=60)
        r.raise_for_status()
        with open(path, "w") as f:
            f.write(r.text)
    with open(path) as f:
        return f.read()


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
    css = vendored("leaflet.min.css")
    js = vendored("leaflet.min.js").replace("</script", "<\\/script")
    with open(os.path.join(HERE, "template.html")) as f:
        html = f.read()
    html = (html.replace("/*__LEAFLET_CSS__*/", css)
            .replace("/*__LEAFLET_JS__*/", js)
            .replace("__DATA__", blob))
    out = os.path.join(HERE, "index.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"wrote {out} ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
