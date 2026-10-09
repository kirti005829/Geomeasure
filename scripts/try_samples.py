from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLE_EXTS = {".kml", ".kmz", ".zip", ".shp"}


def make_client(url: str | None):
    if url:
        import httpx

        return httpx.Client(base_url=url, timeout=60), None
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    tmp = tempfile.TemporaryDirectory()
    settings = Settings(
        database_url=f"sqlite:///{Path(tmp.name) / 'try.db'}", upload_dir=Path(tmp.name) / "uploads"
    )
    client = TestClient(create_app(settings))
    client.__enter__()
    return client, tmp


def fmt_num(value, unit: str) -> str:
    return "" if value is None else f"{value:,.3f} {unit}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("paths", nargs="*", default=[str(ROOT / "samples")])
    parser.add_argument("--url", help="base URL of a running server (default: in-process)")
    parser.add_argument("--details", action="store_true", help="print every feature")
    args = parser.parse_args()

    files: list[Path] = []
    for p in map(Path, args.paths):
        files += sorted(f for f in p.rglob("*") if f.suffix.lower() in SAMPLE_EXTS) if p.is_dir() else [p]

    import logging

    logging.disable(logging.WARNING)  # keep the output readable
    client, _tmp = make_client(args.url)

    for f in files:
        rel = f.relative_to(ROOT) if f.is_relative_to(ROOT) else f
        r = client.post("/api/files/", files={"file": (f.name, f.read_bytes())})
        body = r.json()
        if "error" in body and isinstance(body["error"], dict):  # rejected before storing
            print(f"\n✗ {rel}  HTTP {r.status_code}  {body['error']['code']}: {body['error']['message']}")
            continue
        if body["status"] == "FAILED":
            print(f"\n✗ {rel}  HTTP {r.status_code}  {body['error_code']}: {body['error']}")
            continue

        m = client.get(f"/api/files/{body['id']}/measurements/", params={"limit": 1000}).json()
        s = m["summary"]
        layers = ", ".join(
            f"{layer['name']} ({layer['crs']}, {layer['crs_source']})" for layer in body["layers"]
        )
        print(
            f"\n✓ {rel}  HTTP {r.status_code}  {body['feature_count']} features  [{body['processing_ms']} ms]"
        )
        print(f"    layers : {layers}")
        print(f"    status : {s['by_status']}")
        area = s["total_area"]["hectares"] if s["total_area"] else None
        length = s["total_length"]["km"] if s["total_length"] else None
        print(
            f"    totals : {fmt_num(area, 'ha')}  {fmt_num(length, 'km')}   max Δ geodesic: {s['max_abs_difference_pct']} %"
        )
        for w in body["warnings"]:
            print(f"    warn   : {w}")
        if args.details:
            for it in m["items"]:
                value = (
                    fmt_num(it["area"]["m2"], "m²")
                    if it["area"]
                    else fmt_num(it["length"]["m"], "m")
                    if it["length"]
                    else ""
                )
                crs = (it["method"] or {}).get("projected_crs") or ""
                crs = crs if crs.startswith("EPSG") else ("local LAEA/AEQD" if crs else "")
                print(
                    f"      #{it['index']:<3} {it['status']:<15} {str(it['geometry_type']):<16} "
                    f"{value:>20}  {crs:<16} {it['name'] or ''}"
                )
                for note in it["notes"]:
                    if it["status"] != "NOT_APPLICABLE":
                        print(f"           ↳ {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
