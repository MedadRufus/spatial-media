#!/usr/bin/env python3
"""Build the data layers for the Madurai bus map.

Layers produced in ./data:
  osm_bus_stops.geojson      OpenStreetMap bus stops / platforms   (ODbL)
  osm_bus_stations.geojson   OpenStreetMap bus stands              (ODbL)
  osm_basemap.geojson        OpenStreetMap main roads, river, tanks (ODbL)
  maduraione_routes.geojson  MaduraiOne CityBus route variants (stop lists from
                             MaduraiOne's MCP endpoint, geometry from OSM + OSRM;
                             approximate, see README)

Usage:  python3 fetch_data.py [--cache DIR]
Responses are cached so reruns are cheap and polite to the upstream services.
"""
import argparse
import difflib
import hashlib
import json
import math
import os
import re
import sys
import time

import requests

BBOX = (9.70, 77.85, 10.20, 78.40)  # south, west, north, east
OVERPASS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
SITEMAP = "https://maduraione.in/sitemap.xml"
MCP = "https://api.maduraione.in/mcp"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://router.project-osrm.org/route/v1/driving/"
UA = {"User-Agent": "madurai-bus-map/1.0 (open-data visualisation)"}

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")


class Cache:
    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def get(self, key, fetch, delay=0.0):
        path = os.path.join(self.root, hashlib.sha1(key.encode()).hexdigest() + ".json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        value = fetch()
        with open(path, "w") as f:
            json.dump(value, f)
        time.sleep(delay)
        return value


def retry(fn, tries=6):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # network / upstream hiccups
            if i == tries - 1:
                raise
            print(f"  retry {i + 1}: {e}", file=sys.stderr)
            time.sleep(2 ** i)


def overpass(query):
    def go():
        last = None
        for url in OVERPASS:
            try:
                r = requests.post(url, data={"data": query}, headers=UA, timeout=150)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
        raise last
    return retry(go)


def fetch_osm(cache):
    s, w, n, e = BBOX
    q = f"""[out:json][timeout:180];
(node["highway"="bus_stop"]({s},{w},{n},{e});
 way["highway"="bus_stop"]({s},{w},{n},{e});
 node["public_transport"="platform"]["bus"="yes"]({s},{w},{n},{e});
 nwr["amenity"="bus_station"]({s},{w},{n},{e}););
out center tags;"""
    data = cache.get("osm:" + q, lambda: overpass(q))
    stops, stations, seen = [], [], set()
    for el in data["elements"]:
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None:
            continue
        t = el.get("tags", {})
        props = {
            "name": t.get("name:en") or t.get("name") or "",
            "name_ta": t.get("name:ta", ""),
            "osm": f"{el['type']}/{el['id']}",
        }
        feat = {"type": "Feature", "properties": props,
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}}
        if t.get("amenity") == "bus_station":
            stations.append(feat)
        elif (round(lat, 5), round(lon, 5)) not in seen:
            seen.add((round(lat, 5), round(lon, 5)))
            stops.append(feat)
    return data["osm3s"]["timestamp_osm_base"], stops, stations


def fetch_basemap(cache):
    s, w, n, e = 9.78, 77.92, 10.12, 78.30
    q = f"""[out:json][timeout:180];
(way["highway"~"^(motorway|trunk|primary|secondary)$"]({s},{w},{n},{e});
 way["waterway"="river"]({s},{w},{n},{e});
 way["natural"="water"]["water"~"^(lake|reservoir|pond|basin)$"]({s},{w},{n},{e});
 way["landuse"="reservoir"]({s},{w},{n},{e}););
out geom;"""
    data = cache.get("osm:" + q, lambda: overpass(q))
    feats = []
    for el in data["elements"]:
        t = el.get("tags", {})
        line = [[round(p["lon"], 5), round(p["lat"], 5)] for p in el.get("geometry", [])]
        if len(line) < 2:
            continue
        if "highway" in t:
            kind = "major" if t["highway"] in ("motorway", "trunk", "primary") else "road"
            geom = {"type": "LineString", "coordinates": simplify(line, 0.00008)}
        elif t.get("waterway") == "river":
            kind = "river"
            geom = {"type": "LineString", "coordinates": simplify(line, 0.00008)}
        else:
            if len(line) < 4 or line[0] != line[-1]:
                continue
            kind = "water"
            geom = {"type": "Polygon", "coordinates": [simplify(line, 0.00008)]}
        feats.append({"type": "Feature",
                      "properties": {"k": kind, "n": t.get("name:en") or t.get("name", "")},
                      "geometry": geom})
    return feats


def mcp_call(cache, tool, args):
    """Call a tool on MaduraiOne's public MCP endpoint and return its text (None if not found)."""
    key = "mcp:" + tool + json.dumps(args, sort_keys=True)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}

    def go():
        r = requests.post(MCP, json=body, timeout=30,
                          headers={**UA, "Accept": "application/json, text/event-stream"})
        r.raise_for_status()
        res = r.json()
        if "error" in res:
            return None
        return res["result"]["content"][0]["text"]
    return cache.get(key, lambda: retry(go), delay=0.5)


VARIANT = re.compile(
    r"### Variant \d+: (?P<name>.+?)\n(?P<body>.*?)(?=\n### Variant|\Z)", re.S)


def parse_bus(num, text):
    out = []
    for m in VARIANT.finditer(text or ""):
        body = m.group("body")
        field = lambda label: (re.search(r"\*\*" + label + r"\*\*: ([^\n]+)", body) or [None, ""])[1].strip()
        stops = re.sub(r"\*\(.*?\)\*", "", field("Key Route Milestones"))
        out.append({
            "bus": num,
            "route_name": m.group("name").strip(),
            "spoke": field("Transit Spoke Corridor"),
            "pbs_platform": field("Periyar Bus Stand Departure"),
            "total_stops": int(field("Total Stops") or 0) or None,
            "stops": [s.strip() for s in stops.split("➔") if s.strip()],
            "canonical_url": field("View Interactive Map"),
        })
    return out


def bus_numbers(cache):
    xml = cache.get("sitemap", lambda: retry(lambda: requests.get(SITEMAP, headers=UA, timeout=60).text))
    return re.findall(r"<loc>https://maduraione\.in/route/([^<]+)</loc>", xml)


# --- Stop locations, from OpenStreetMap only -------------------------------

STOPWORDS = re.compile(r"\b(BUS STOP|BUS STAND|BUS STATION|STOP|JN|JUNCTION|ROAD|RD|"
                       r"PAERUNDHU NILAIYAM|NILAIYAM)\b")
ALIASES = {"PBS": "PERIYAR BUS STAND", "MATTUTHAVANI": "MGR BUS STAND",
           "MATTUTHAVANI BUS STAND": "MGR BUS STAND"}


def norm(name):
    n = name.upper().replace("&", " AND ")
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    n = STOPWORDS.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def gazetteer(cache):
    """Named OSM features around Madurai: name -> (lat, lon)."""
    s, w, n, e = BBOX
    q = f"""[out:json][timeout:180];
(node["place"]["name"]({s},{w},{n},{e});
 nwr["highway"="bus_stop"]["name"]({s},{w},{n},{e});
 nwr["amenity"="bus_station"]["name"]({s},{w},{n},{e});
 nwr["railway"~"^(station|halt)$"]["name"]({s},{w},{n},{e});
 nwr["aeroway"="aerodrome"]["name"]({s},{w},{n},{e});
 nwr["amenity"~"^(college|university|hospital|school|cinema|place_of_worship|marketplace|townhall|police|post_office)$"]["name"]({s},{w},{n},{e});
 nwr["tourism"]["name"]({s},{w},{n},{e});
 nwr["landuse"="residential"]["name"]({s},{w},{n},{e}););
out center tags;"""
    data = cache.get("osm:" + q, lambda: overpass(q))
    # lower rank wins: transit features and places beat landmarks
    rank = lambda t: (0 if t.get("highway") == "bus_stop" or t.get("amenity") == "bus_station"
                      else 1 if "place" in t else 2)
    gaz = {}
    for el in data["elements"]:
        t = el.get("tags", {})
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None:
            continue
        for key in ("name", "name:en", "alt_name", "old_name", "official_name"):
            for nm in t.get(key, "").split(";"):
                k = norm(nm)
                if k and (k not in gaz or rank(t) < gaz[k][2]):
                    gaz[k] = (lat, lon, rank(t))
    return {k: (v[0], v[1]) for k, v in gaz.items()}


def nominatim(cache, name):
    s, w, n, e = BBOX
    params = {"q": name.title() + ", Madurai", "format": "jsonv2", "limit": 1,
              "viewbox": f"{w},{n},{e},{s}", "bounded": 1, "countrycodes": "in"}

    def go():
        r = requests.get(NOMINATIM, params=params, headers=UA, timeout=30)
        r.raise_for_status()
        res = r.json()
        return [float(res[0]["lat"]), float(res[0]["lon"])] if res else None
    return cache.get("nom:" + json.dumps(params, sort_keys=True), lambda: retry(go), delay=1.1)


def locate_stops(cache, names):
    gaz = gazetteer(cache)
    keys = list(gaz)
    coords, how = {}, {"osm": 0, "fuzzy": 0, "nominatim": 0, "none": 0}
    for name in names:
        k = norm(ALIASES.get(name, name))
        hit = gaz.get(k)
        if hit:
            how["osm"] += 1
        else:
            close = difflib.get_close_matches(k, keys, n=1, cutoff=0.9) if len(k) >= 6 else []
            if close:
                hit = gaz[close[0]]
                how["fuzzy"] += 1
            else:
                hit = nominatim(cache, ALIASES.get(name, name))
                how["nominatim" if hit else "none"] += 1
        coords[name] = tuple(hit) if hit else None
    print(f"  stop locations: {how}")
    return coords


def km(a, b):
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return 6371 * math.hypot(dlat, dlon)


def drop_outliers(pts):
    """Remove stops whose match sends the route on an implausible detour."""
    pts = [p for j, p in enumerate(pts) if j == 0 or p != pts[j - 1]]
    changed = True
    while changed and len(pts) > 2:
        changed = False
        for j in range(1, len(pts) - 1):
            a, p, b = pts[j - 1], pts[j], pts[j + 1]
            if km(a, p) + km(p, b) > 2.5 * km(a, b) + 6:
                del pts[j]
                changed = True
                break
    return pts


def osrm(cache, pts):
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in pts)
    url = OSRM + coords + "?overview=full&geometries=geojson"

    def go():
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 400:
            return r.json()
        r.raise_for_status()
        return r.json()
    return cache.get("osrm:" + url, lambda: retry(go), delay=1.0)


def simplify(coords, tol=0.00004):
    """Douglas-Peucker in degrees (~4 m), keeps files small."""
    if len(coords) < 3:
        return coords
    keep = [False] * len(coords)
    keep[0] = keep[-1] = True
    stack = [(0, len(coords) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = coords[a]
        bx, by = coords[b]
        dx, dy = bx - ax, by - ay
        norm2 = (dx * dx + dy * dy) or 1e-18
        best, idx = 0.0, None
        for i in range(a + 1, b):
            px, py = coords[i]
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / norm2))
            d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best:
                best, idx = d, i
        if idx is not None and best > tol * tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [c for c, k in zip(coords, keep) if k]


def fetch_routes(cache):
    slugs = bus_numbers(cache)
    numbers = sorted({s.split("-")[0].upper() for s in slugs})
    print(f"{len(slugs)} route pages, {len(numbers)} bus numbers")
    variants, missing = [], []
    for i, num in enumerate(numbers):
        found = parse_bus(num, mcp_call(cache, "get_bus_details", {"bus_number": num}))
        if not found:
            missing.append(num)
        variants += found
        if i % 100 == 0:
            print(f"  buses {i}/{len(numbers)}")
    if missing:
        print(f"  not found: {missing}")

    names = sorted({s for v in variants for s in v["stops"]})
    print(f"{len(variants)} variants, {len(names)} milestone stops")
    coords = locate_stops(cache, names)

    feats, skipped = [], []
    for i, v in enumerate(variants):
        pts = drop_outliers([coords[s] for s in v["stops"] if coords.get(s)])
        props = {
            "bus": v["bus"],
            "name": v["route_name"],
            "spoke": v["spoke"],
            "platform": v["pbs_platform"],
            "total_stops": v["total_stops"],
            "via": v["stops"],
            "located": [bool(coords.get(s)) for s in v["stops"]],
            "url": v["canonical_url"],
        }
        if len(pts) < 2:
            skipped.append(v["bus"] + " " + v["route_name"])
            continue
        res = osrm(cache, pts)
        if res.get("code") == "Ok":
            line = res["routes"][0]["geometry"]["coordinates"]
            props["km"] = round(res["routes"][0]["distance"] / 1000, 1)
            props["snapped"] = True
        else:
            line = [[lon, lat] for lat, lon in pts]
            props["snapped"] = False
        line = [[round(x, 5), round(y, 5)] for x, y in simplify(line)]
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "LineString", "coordinates": line}})
        if i % 100 == 0:
            print(f"  routed {i}/{len(variants)}")
    print(f"  skipped (fewer than 2 located stops): {len(skipped)}")
    return feats, skipped


def write(name, feats, meta):
    path = os.path.join(OUT, name)
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "metadata": meta, "features": feats},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {path} ({len(feats)} features, {os.path.getsize(path) // 1024} KB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=os.path.join(HERE, ".cache"))
    args = ap.parse_args()
    cache = Cache(args.cache)
    os.makedirs(OUT, exist_ok=True)

    ts, stops, stations = fetch_osm(cache)
    osm_meta = {"source": "OpenStreetMap contributors", "license": "ODbL 1.0",
                "osm_timestamp": ts}
    write("osm_bus_stops.geojson", stops, osm_meta)
    write("osm_bus_stations.geojson", stations, osm_meta)
    write("osm_basemap.geojson", fetch_basemap(cache), osm_meta)

    routes, skipped = fetch_routes(cache)
    write("maduraione_routes.geojson", routes, {
        "source": "Data & Routing by MaduraiOne CityBus (https://maduraione.in)",
        "license": "None published; route lists retrieved from MaduraiOne's public MCP endpoint",
        "geometry": "Milestone stops located by name in OpenStreetMap / Nominatim and joined "
                    "along roads with OSRM (OpenStreetMap data, ODbL); approximate",
        "retrieved": time.strftime("%Y-%m-%d"),
        "skipped_no_coordinates": skipped,
    })


if __name__ == "__main__":
    main()
