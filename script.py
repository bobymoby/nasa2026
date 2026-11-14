#!/usr/bin/env python3
"""
Query Sentinel Hub (Copernicus Data Space Ecosystem or commercial sentinel-hub.com)
with OAuth client credentials.

Credentials (env vars, or a .env file next to this script):
    SH_CLIENT_ID=...
    SH_CLIENT_SECRET=...
    SH_DEPLOYMENT=cdse          # "cdse" (dataspace.copernicus.eu) or "sh" (services.sentinel-hub.com)

Credentials only work on the deployment they were created on:
    cdse -> https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings
    sh   -> https://apps.sentinel-hub.com/dashboard/#/account/settings

Examples:
    python sh_query.py search --bbox 23.2 42.6 23.4 42.75 --from 2025-06-01 --to 2025-06-30 --max-cloud 20
    python sh_query.py image  --bbox 23.2 42.6 23.4 42.75 --from 2025-06-01 --to 2025-06-30 --type ndvi -o ndvi.tif
    python sh_query.py image  --bbox 23.2 42.6 23.4 42.75 --from 2025-06-01 --to 2025-06-30 --type truecolor -o rgb.png
    python sh_query.py stats  --bbox 23.2 42.6 23.4 42.75 --from 2025-03-01 --to 2025-10-31 --interval P10D -o ndvi.csv

Requires: pip install requests
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

DEPLOYMENTS = {
    "cdse": {
        "token_url": "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
        "base_url": "https://sh.dataspace.copernicus.eu",
    },
    "sh": {
        "token_url": "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token",
        "base_url": "https://services.sentinel-hub.com",
    },
}

# ---------------------------------------------------------------- evalscripts

EVAL_TRUECOLOR = """//VERSION=3
function setup() {
  return { input: ["B02","B03","B04","dataMask"], output: { bands: 4 } };
}
function evaluatePixel(s) {
  const g = 2.5;
  return [g*s.B04, g*s.B03, g*s.B02, s.dataMask];
}"""

EVAL_NDVI = """//VERSION=3
function setup() {
  return { input: ["B04","B08","SCL","dataMask"],
           output: { bands: 1, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) {
  // mask clouds/shadows/snow using the L2A scene classification
  if (s.dataMask == 0 || [3,8,9,10,11].includes(s.SCL)) return [NaN];
  return [(s.B08 - s.B04) / (s.B08 + s.B04)];
}"""

EVAL_NDVI_STATS = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B04","B08","SCL","dataMask"] }],
    output: [
      { id: "ndvi", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}
function evaluatePixel(s) {
  const cloudy = [3,8,9,10,11].includes(s.SCL);
  const ndvi = (s.B08 - s.B04) / (s.B08 + s.B04);
  return { ndvi: [ndvi], dataMask: [s.dataMask && !cloudy ? 1 : 0] };
}"""

# ---------------------------------------------------------------- client


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class SentinelHub:
    def __init__(self, client_id: str, client_secret: str, deployment: str = "cdse"):
        if deployment not in DEPLOYMENTS:
            raise ValueError(f"deployment must be one of {list(DEPLOYMENTS)}")
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = DEPLOYMENTS[deployment]["token_url"]
        self.base = DEPLOYMENTS[deployment]["base_url"]
        self.session = requests.Session()
        self._token = None
        self._expires = 0.0

    # OAuth2 client-credentials flow, token cached until shortly before expiry
    def token(self) -> str:
        if self._token is None or time.time() > self._expires - 60:
            r = requests.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=30,
            )
            if r.status_code != 200:
                raise RuntimeError(f"Token request failed ({r.status_code}): {r.text}")
            j = r.json()
            self._token = j["access_token"]
            self._expires = time.time() + j.get("expires_in", 600)
        return self._token

    def _post(self, path: str, payload: dict, accept: str | None = None):
        for attempt in range(4):
            headers = {"Authorization": f"Bearer {self.token()}"}
            if accept:
                headers["Accept"] = accept
            r = self.session.post(
                self.base + path, json=payload, headers=headers, timeout=120
            )
            if r.status_code == 429:  # rate limited -> back off
                time.sleep(2**attempt)
                continue
            if r.status_code == 401 and attempt == 0:  # token expired -> refresh once
                self._token = None
                continue
            if not r.ok:
                raise RuntimeError(f"{path} failed ({r.status_code}): {r.text}")
            return r
        raise RuntimeError(f"{path}: rate-limited, giving up")

    # -------- Catalog API (STAC search)
    def search(
        self, bbox, t_from, t_to, collection="sentinel-2-l2a", max_cloud=None, limit=100
    ):
        payload = {
            "bbox": bbox,
            "datetime": f"{t_from}T00:00:00Z/{t_to}T23:59:59Z",
            "collections": [collection],
            "limit": min(limit, 100),
        }
        if max_cloud is not None:
            payload["filter"] = f"eo:cloud_cover < {max_cloud}"
            payload["filter-lang"] = "cql2-text"
        features = []
        while True:
            j = self._post(
                "/api/v1/catalog/1.0.0/search", payload, accept="application/geo+json"
            ).json()
            features += j.get("features", [])
            nxt = j.get("context", {}).get("next")
            if nxt is None or len(features) >= limit:
                return features[:limit]
            payload["next"] = nxt

    # -------- Process API (imagery)
    def image(
        self,
        bbox,
        t_from,
        t_to,
        evalscript,
        fmt="image/tiff",
        width=512,
        height=512,
        collection="sentinel-2-l2a",
        max_cloud=100,
    ):
        payload = {
            "input": {
                "bounds": {
                    "bbox": bbox,
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
                },
                "data": [
                    {
                        "type": collection,
                        "dataFilter": {
                            "timeRange": {
                                "from": f"{t_from}T00:00:00Z",
                                "to": f"{t_to}T23:59:59Z",
                            },
                            "maxCloudCoverage": max_cloud,
                            "mosaickingOrder": "leastCC",
                        },
                    }
                ],
            },
            "output": {
                "width": width,
                "height": height,
                "responses": [{"identifier": "default", "format": {"type": fmt}}],
            },
            "evalscript": evalscript,
        }
        return self._post("/api/v1/process", payload, accept=fmt).content

    # -------- Statistical API (time series)
    def stats(
        self,
        bbox,
        t_from,
        t_to,
        evalscript,
        interval="P10D",
        res_deg=0.0001,
        collection="sentinel-2-l2a",
        max_cloud=100,
    ):
        payload = {
            "input": {
                "bounds": {
                    "bbox": bbox,
                    "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
                },
                "data": [
                    {"type": collection, "dataFilter": {"maxCloudCoverage": max_cloud}}
                ],
            },
            "aggregation": {
                "timeRange": {"from": f"{t_from}T00:00:00Z", "to": f"{t_to}T23:59:59Z"},
                "aggregationInterval": {"of": interval},
                "evalscript": evalscript,
                "resx": res_deg,  # ~10 m at mid-latitudes
                "resy": res_deg,
            },
            "calculations": {"default": {}},
        }
        return self._post("/api/v1/statistics", payload).json()


# ---------------------------------------------------------------- CLI


def main():
    load_dotenv(Path(__file__).with_name(".env"))

    p = argparse.ArgumentParser(
        description="Query Sentinel Hub with client credentials"
    )
    p.add_argument(
        "--deployment",
        default=os.getenv("SH_DEPLOYMENT", "cdse"),
        choices=list(DEPLOYMENTS),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument(
            "--bbox",
            nargs=4,
            type=float,
            required=True,
            metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        )
        sp.add_argument("--from", dest="t_from", required=True, help="YYYY-MM-DD")
        sp.add_argument("--to", dest="t_to", required=True, help="YYYY-MM-DD")
        sp.add_argument("--collection", default="sentinel-2-l2a")
        sp.add_argument("--max-cloud", type=float, default=None)

    s = sub.add_parser("search", help="list available scenes (Catalog API)")
    common(s)
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("-o", "--out", help="save raw STAC features as JSON")

    i = sub.add_parser("image", help="download an image (Process API)")
    common(i)
    i.add_argument("--type", choices=["truecolor", "ndvi"], default="truecolor")
    i.add_argument("--evalscript", help="path to a custom evalscript file")
    i.add_argument("--width", type=int, default=512)
    i.add_argument("--height", type=int, default=512)
    i.add_argument("-o", "--out", required=True, help=".tif / .png / .jpg")

    st = sub.add_parser("stats", help="NDVI time series (Statistical API)")
    common(st)
    st.add_argument(
        "--interval", default="P10D", help="ISO-8601 duration, e.g. P1D, P10D, P1M"
    )
    st.add_argument("--evalscript", help="path to a custom evalscript file")
    st.add_argument("-o", "--out", help="save as CSV (default: print)")

    a = p.parse_args()

    cid, secret = os.getenv("SH_CLIENT_ID"), os.getenv("SH_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("Set SH_CLIENT_ID and SH_CLIENT_SECRET (env vars or .env file).")
    sh = SentinelHub(cid, secret, a.deployment)

    if a.cmd == "search":
        feats = sh.search(a.bbox, a.t_from, a.t_to, a.collection, a.max_cloud, a.limit)
        for f in feats:
            pr = f["properties"]
            cc = pr.get("eo:cloud_cover")
            print(
                f"{pr['datetime']}  cloud={cc if cc is not None else '-':>6}  {f['id']}"
            )
        print(f"{len(feats)} scenes", file=sys.stderr)
        if a.out:
            Path(a.out).write_text(json.dumps(feats, indent=2))

    elif a.cmd == "image":
        ext = Path(a.out).suffix.lower()
        fmt = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(
            ext, "image/tiff"
        )
        if a.evalscript:
            script = Path(a.evalscript).read_text()
        else:
            script = EVAL_NDVI if a.type == "ndvi" else EVAL_TRUECOLOR
        if a.type == "ndvi" and fmt != "image/tiff":
            print(
                "Note: NDVI is FLOAT32; use .tif to keep real values.", file=sys.stderr
            )
        data = sh.image(
            a.bbox,
            a.t_from,
            a.t_to,
            script,
            fmt,
            a.width,
            a.height,
            a.collection,
            a.max_cloud if a.max_cloud is not None else 100,
        )
        Path(a.out).write_bytes(data)
        print(f"Saved {a.out} ({len(data) / 1024:.0f} KB)")

    elif a.cmd == "stats":
        script = Path(a.evalscript).read_text() if a.evalscript else EVAL_NDVI_STATS
        j = sh.stats(
            a.bbox,
            a.t_from,
            a.t_to,
            script,
            a.interval,
            collection=a.collection,
            max_cloud=a.max_cloud if a.max_cloud is not None else 100,
        )
        rows = []
        for item in j.get("data", []):
            if "error" in item:
                continue
            b = item["outputs"]["ndvi"]["bands"]["B0"]["stats"]
            if b.get("sampleCount", 0) == b.get("noDataCount", 0):
                continue  # fully cloudy / no data in this interval
            rows.append(
                {
                    "from": item["interval"]["from"][:10],
                    "to": item["interval"]["to"][:10],
                    "mean": b.get("mean"),
                    "stDev": b.get("stDev"),
                    "min": b.get("min"),
                    "max": b.get("max"),
                    "valid_px": b["sampleCount"] - b["noDataCount"],
                }
            )
        if a.out:
            with open(a.out, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["from"])
                w.writeheader()
                w.writerows(rows)
            print(f"Saved {len(rows)} intervals to {a.out}")
        else:
            for r in rows:
                print(
                    f"{r['from']} → {r['to']}  NDVI mean={r['mean']:.3f}  sd={r['stDev']:.3f}  px={r['valid_px']}"
                )


if __name__ == "__main__":
    main()
