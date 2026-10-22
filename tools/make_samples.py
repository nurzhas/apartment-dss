"""Save a real search as an offline demo set (data/samples/*.json), so the app can be shown
without network access. Run: .venv\\Scripts\\python -m tools.make_samples"""
import json
import sys
import time
from pathlib import Path

from krisha import Geocoder, KrishaClient, SearchQuery, collect

ROOT = Path(__file__).resolve().parent.parent
QUERIES = {
    "sale": SearchQuery(mode="sale", rooms=[2, 3], price_from=25_000_000, price_to=45_000_000, pages=3),
    "rent": SearchQuery(mode="rent", rooms=[1, 2], price_from=150_000, price_to=350_000, pages=2),
}


def main(modes):
    sys.stdout.reconfigure(encoding="utf-8")
    client = KrishaClient(ROOT / "data" / "cache.sqlite")
    geocoder = Geocoder(ROOT / "data" / "cache.sqlite")
    out = ROOT / "data" / "samples"
    out.mkdir(parents=True, exist_ok=True)
    for mode in modes:
        res = collect(client, QUERIES[mode], lambda p: print(f"  [{mode}] {p['message']}", flush=True), geocoder=geocoder)
        d = {"id": f"sample-{mode}", "mode": mode, "created": time.time(), "title": "Демо-набор", **res}
        for i, x in enumerate(d["listings"], 1):
            x["code"] = f"A{i}"
        (out / f"{mode}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print(mode, len(d["listings"]), "listings, coordinates:", d["geo"], d["problems"])
    client.close()


if __name__ == "__main__":
    main(sys.argv[1:] or ["sale", "rent"])
