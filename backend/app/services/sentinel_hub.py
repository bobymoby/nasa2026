"""Sentinel Hub client (Copernicus Data Space Ecosystem or commercial sentinel-hub.com).

Authenticates with OAuth client credentials. Credentials only work on the
deployment they were created on:
    cdse -> https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings
    sh   -> https://apps.sentinel-hub.com/dashboard/#/account/settings
"""

import threading
import time
from datetime import date, timedelta

import httpx

from app.timing import emit, timed

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

WGS84 = "http://www.opengis.net/def/crs/EPSG/0/4326"

# cap on simultaneous requests across all threads (plots x years); above this
# Copernicus starts answering 429 Too Many Requests
MAX_CONCURRENT_REQUESTS = 10

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

# NDVI as a coloured RGBA image for display; clouds, shadows and snow are transparent.
# Keep the stops in sync with NDVI_LEGEND in the frontend's SentinelPanel.
EVAL_NDVI_COLOR = """//VERSION=3
function setup() {
  return { input: ["B04","B08","SCL","dataMask"], output: { bands: 4 } };
}
const stops = [-0.2, 0, 0.2, 0.4, 0.6, 0.8];
const colors = [
  [0.65, 0.0, 0.15], [0.84, 0.19, 0.15], [0.99, 0.68, 0.38],
  [0.85, 0.94, 0.55], [0.40, 0.74, 0.39], [0.0, 0.41, 0.22]
];
function evaluatePixel(s) {
  if (s.dataMask == 0 || [3,8,9,10,11].includes(s.SCL)) return [0, 0, 0, 0];
  const ndvi = (s.B08 - s.B04) / (s.B08 + s.B04);
  return [...colorBlend(ndvi, stops, colors), 1];
}"""

EVALSCRIPTS = {"truecolor": EVAL_TRUECOLOR, "ndvi": EVAL_NDVI, "ndvi-color": EVAL_NDVI_COLOR}


class SentinelHubError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------- client


class SentinelHub:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        deployment: str = "cdse",
    ):
        if deployment not in DEPLOYMENTS:
            raise ValueError(f"deployment must be one of {list(DEPLOYMENTS)}")
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = DEPLOYMENTS[deployment]["token_url"]
        self.base = DEPLOYMENTS[deployment]["base_url"]
        self.deployment = deployment
        self.http = httpx.Client(timeout=120)
        self._token: str | None = None
        self._expires = 0.0
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)

    def close(self) -> None:
        self.http.close()

    # OAuth2 client-credentials flow, token cached until shortly before expiry
    def token(self) -> str:
        with self._lock:
            if self._token is None or time.time() > self._expires - 60:
                start = time.perf_counter()
                r = self.http.post(
                    self.token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    timeout=30,
                )
                if r.status_code != 200:
                    raise SentinelHubError(
                        f"Token request failed ({r.status_code}): {r.text}",
                        r.status_code,
                    )
                j = r.json()
                self._token = j["access_token"]
                self._expires = time.time() + j.get("expires_in", 600)
                emit("sh token refresh", (time.perf_counter() - start) * 1000)
            return self._token

    def _post(self, path: str, payload: dict, accept: str | None = None) -> httpx.Response:
        for attempt in range(4):
            headers = {"Authorization": f"Bearer {self.token()}"}
            if accept:
                headers["Accept"] = accept
            with self._slots, timed(f"sh POST {path}") as info:
                r = self.http.post(self.base + path, json=payload, headers=headers)
                info.update(status=r.status_code, kb=len(r.content) // 1024,
                            attempt=attempt + 1 if attempt else None)
            if r.status_code == 429:  # rate limited -> back off
                emit("sh rate limited, sleeping", 2**attempt * 1000)
                time.sleep(2**attempt)
                continue
            if r.status_code == 401 and attempt == 0:  # token expired -> refresh once
                self._token = None
                continue
            if not r.is_success:
                raise SentinelHubError(
                    f"{path} failed ({r.status_code}): {r.text}", r.status_code
                )
            return r
        raise SentinelHubError(f"{path}: rate-limited, giving up", 429)

    # -------- Catalog API (STAC search)
    def search(
        self, bbox, t_from, t_to, collection="sentinel-2-l2a", max_cloud=None, limit=100
    ) -> list[dict]:
        payload = {
            "bbox": bbox,
            "datetime": f"{t_from}T00:00:00Z/{t_to}T23:59:59Z",
            "collections": [collection],
            "limit": min(limit, 100),
        }
        if max_cloud is not None:
            payload["filter"] = f"eo:cloud_cover < {max_cloud}"
            payload["filter-lang"] = "cql2-text"
        features: list[dict] = []
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
    ) -> bytes:
        payload = {
            "input": {
                "bounds": {"bbox": bbox, "properties": {"crs": WGS84}},
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
        res_deg: float | tuple[float, float] = 0.0001,
        collection="sentinel-2-l2a",
        max_cloud=100,
        geometry: dict | None = None,
        end_exclusive: bool = False,
    ) -> dict:
        """With `geometry` (GeoJSON polygon, lon/lat) only pixels inside it count."""
        resx, resy = res_deg if isinstance(res_deg, tuple) else (res_deg, res_deg)
        end_time = (
            f"{date.fromisoformat(t_to) + timedelta(days=1)}T00:00:00Z"
            if end_exclusive
            else f"{t_to}T23:59:59Z"
        )
        bounds = {"geometry": geometry} if geometry else {"bbox": bbox}
        payload = {
            "input": {
                "bounds": {**bounds, "properties": {"crs": WGS84}},
                "data": [
                    {"type": collection, "dataFilter": {"maxCloudCoverage": max_cloud}}
                ],
            },
            "aggregation": {
                "timeRange": {
                    "from": f"{t_from}T00:00:00Z",
                    "to": end_time,
                },
                "aggregationInterval": {"of": interval},
                "evalscript": evalscript,
                "resx": resx,  # degrees; ~10 m pixels for field work
                "resy": resy,
            },
            "calculations": {"default": {}},
        }
        return self._post("/api/v1/statistics", payload).json()


def summarize_ndvi_stats(raw: dict) -> list[dict]:
    """Flatten a Statistical API response into one row per interval with data."""
    rows = []
    for item in raw.get("data", []):
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
    return rows
