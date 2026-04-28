"""
EV Supply Chain Dashboard Server
=================================
Usage:
  python serve.py                  # refresh simulation data then serve
  python serve.py --no-refresh     # serve existing data (fast)
  python serve.py --port 8080      # custom port

Opens http://localhost:8080/dashboard.html in your browser automatically.
"""
import argparse
import http.server
import json
import os
import sys
import threading
import webbrowser

import numpy as np


# ── Simulation runner ────────────────────────────────────────────────────────

class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):  return int(obj)
        if isinstance(obj, np.floating): return round(float(obj), 4)
        if isinstance(obj, np.ndarray):  return obj.tolist()
        return super().default(obj)


def refresh_data(n_weeks: int = 260, seed: int = 42) -> None:
    """Run all scenarios and write results/simulation_data.json."""
    sys.path.insert(0, os.path.dirname(__file__))
    from model.hybrid_model import EVSupplyChainModel
    from model.shocks import SCENARIOS

    os.makedirs("results", exist_ok=True)
    results = {}
    total = len(SCENARIOS)
    for i, (name, sc) in enumerate(SCENARIOS.items(), 1):
        print(f"  [{i}/{total}] {name} … ", end="", flush=True)
        m = EVSupplyChainModel(scenario=sc, seed=seed, n_weeks=n_weeks)
        m.run()
        df = m.get_results()
        results[name] = df.to_dict(orient="list")
        print("done")

    out = os.path.join(os.path.dirname(__file__), "results", "simulation_data.json")
    with open(out, "w") as f:
        json.dump(results, f, cls=_Encoder)
    print(f"\n  Data saved → {out}  ({os.path.getsize(out)//1024} KB)\n")


# ── HTTP server ───────────────────────────────────────────────────────────────

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass   # suppress per-request noise


def serve(port: int, open_browser: bool = True) -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    url = f"http://localhost:{port}/dashboard.html"
    print(f"  Serving at  {url}")
    print("  Press Ctrl+C to stop.\n")

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    with http.server.HTTPServer(("", port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Server stopped.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="EV Supply Chain Dashboard Server")
    parser.add_argument("--no-refresh", action="store_true",
                        help="Skip simulation run; use existing results/simulation_data.json")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    parser.add_argument("--weeks", type=int, default=260, help="Simulation weeks (default: 260)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    print("=" * 58)
    print("  EV Supply Chain Dashboard")
    print("=" * 58)

    if not args.no_refresh:
        print(f"\n  Running {args.weeks}-week simulation for all scenarios…\n")
        refresh_data(n_weeks=args.weeks, seed=args.seed)
    else:
        json_path = os.path.join(os.path.dirname(__file__), "results", "simulation_data.json")
        if not os.path.exists(json_path):
            print("  ERROR: results/simulation_data.json not found.")
            print("  Run without --no-refresh to generate it first.\n")
            sys.exit(1)
        print("  Using existing simulation data (--no-refresh).\n")

    serve(args.port)


if __name__ == "__main__":
    main()
