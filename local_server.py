"""
local_server.py
════════════════════════════════════════════════════════════════════════════════
DeepDive — Lightweight local HTTP server that acts as the bridge between the
GitHub Pages web frontend and your local Python environment (dag_sampler.py, etc.).

HOW IT WORKS
────────────
1. You run this script once:  python local_server.py
2. The web frontend (hosted on GitHub Pages) POSTs DAG JSON to:
       http://localhost:<PORT>/dag
3. This server validates the payload, saves it to disk, and (optionally)
   immediately triggers dag_sampler.py to generate synthetic data.
4. A /status endpoint lets the frontend poll whether the server is alive.
5. A /result endpoint lets the frontend poll for the latest saved DAG path.

CROSS-ORIGIN (CORS)
───────────────────
GitHub Pages runs on a different origin than localhost, so every response
includes the appropriate Access-Control-Allow-* headers.  The ALLOWED_ORIGINS
list below controls which origins are trusted.  Add your GitHub Pages URL once
you have it (e.g. "https://yourname.github.io").

GEMINI KEY
──────────
The Gemini API key is read from the environment variable GEMINI_API_KEY.
Set it before launching:
    Windows PowerShell:  $env:GEMINI_API_KEY = "your-key-here"
    Windows CMD:         set GEMINI_API_KEY=your-key-here
    Linux / macOS:       export GEMINI_API_KEY=your-key-here
The /gemini_key endpoint returns the key to the web frontend over localhost
only — it is NEVER forwarded anywhere other than the Gemini API.

REQUIREMENTS
────────────
    pip install flask flask-cors
    (dag_sampler.py must be in the same directory or on PYTHONPATH)

USAGE
─────
    python local_server.py                      # default port 7432
    python local_server.py --port 8080          # custom port
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# ── Flask + CORS ───────────────────────────────────────────────────────────────
try:
    from flask import Flask, jsonify, request, abort
    from flask_cors import CORS
except ImportError:
    print(
        "\n  ERROR: Flask or flask-cors not installed.\n"
        "  Run:  pip install flask flask-cors\n"
    )
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION  (edit these if needed)
# ─────────────────────────────────────────────────────────────────────────────

# Default port.  7432 is arbitrary but unlikely to conflict with common services
# (Flask=5000, React=3000, Vite=5173, macOS AirPlay=5000, etc.)
DEFAULT_PORT = 7432

# Directory where received DAG JSON files are saved.
# Created automatically if it does not exist.
OUTPUT_DIR = Path("dag_outputs")

# Which browser origins are allowed to talk to this server.
# Add your GitHub Pages URL here once you know it.
# Example: "https://yourname.github.io"
ALLOWED_ORIGINS = [
    "http://localhost:3000",        # local dev (e.g. live-server)
    "http://localhost:5173",        # local dev (Vite)
    "http://127.0.0.1:5500",        # VS Code Live Server
    "https://msalem7777.github.io",   # ← REPLACE with your actual GitHub Pages URL
    # Add more origins as needed:
    # "https://custom-domain.com",
]

# Set to True to automatically run dag_sampler after receiving a DAG.
AUTO_SAMPLE = False

# Number of rows to generate when AUTO_SAMPLE is True.
AUTO_SAMPLE_ROWS = 500

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="  %(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("deepdive-server")

# ─────────────────────────────────────────────────────────────────────────────
# Flask application
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)

# Apply CORS to every route.  flask-cors handles the preflight OPTIONS requests
# automatically, which is necessary for POST from a cross-origin page.
CORS(
    app,
    origins=ALLOWED_ORIGINS,
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With"],
)

# In-memory state shared across requests (thread-safe via a simple lock).
_state_lock = threading.Lock()
_state = {
    "last_dag_path": None,      # Path of the most recently saved DAG file
    "last_dag_time": None,      # ISO timestamp of last save
    "total_received": 0,        # How many DAGs have been received this session
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: validate that a dict looks like a DAG graph
# ─────────────────────────────────────────────────────────────────────────────

def _validate_dag(graph: dict) -> list[str]:
    """
    Return a list of validation error strings.
    Empty list means the graph is acceptable.

    We do a lightweight structural check rather than a full cycle test here —
    the web frontend already enforces acyclicity, and dag_sampler.py does a
    topological sort that will catch any remaining issues.
    """
    errors = []

    # Must have nodes and edges keys
    if "nodes" not in graph:
        errors.append("Missing 'nodes' key")
    if "edges" not in graph:
        errors.append("Missing 'edges' key")

    if errors:
        # Can't do further checks without these keys
        return errors

    nodes = graph["nodes"]
    edges = graph["edges"]

    # nodes must be a non-empty list
    if not isinstance(nodes, list) or len(nodes) == 0:
        errors.append("'nodes' must be a non-empty list")
        return errors

    # Every node must have an id and a name
    node_ids = set()
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            errors.append(f"Node {i} is not a dict")
            continue
        if "id" not in n:
            errors.append(f"Node {i} missing 'id'")
        else:
            node_ids.add(n["id"])
        if "name" not in n or not str(n["name"]).strip():
            errors.append(f"Node {i} missing or empty 'name'")

    # edges must be a list of [src_id, tgt_id] pairs
    if not isinstance(edges, list):
        errors.append("'edges' must be a list")
    else:
        for j, e in enumerate(edges):
            if not (isinstance(e, list) and len(e) == 2):
                errors.append(f"Edge {j} must be [src_id, tgt_id]")
            else:
                src, tgt = e
                if src not in node_ids:
                    errors.append(f"Edge {j} src={src} is not a known node id")
                if tgt not in node_ids:
                    errors.append(f"Edge {j} tgt={tgt} is not a known node id")

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Helper: run dag_sampler in a background thread
# ─────────────────────────────────────────────────────────────────────────────

def _auto_sample_background(dag_path: Path, n_rows: int) -> None:
    """
    Attempt to import and run DAGSampler on the saved DAG file.
    Runs in a daemon thread so it does not block the HTTP response.
    """
    try:
        log.info(f"AUTO_SAMPLE: generating {n_rows} rows from {dag_path.name} …")
        sys.path.insert(0, str(dag_path.parent))  # ensure dag_sampler is findable
        from dag_sampler import DAGSampler          # type: ignore

        sampler = DAGSampler.from_file(dag_path)
        df = sampler.sample_dataset(n_rows=n_rows, seed=42)

        # Save alongside the DAG file
        csv_path = dag_path.with_suffix(".csv")
        try:
            df.to_csv(csv_path, index=False)
            log.info(f"AUTO_SAMPLE: saved → {csv_path}")
        except AttributeError:
            # pandas not available; df is a plain dict
            import csv
            cols = list(df.keys())
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(cols)
                for row in zip(*[df[c] for c in cols]):
                    w.writerow(row)
            log.info(f"AUTO_SAMPLE: saved (csv module) → {csv_path}")

    except Exception as exc:
        log.error(f"AUTO_SAMPLE failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/status", methods=["GET"])
def status():
    """
    Simple health-check endpoint.
    The web frontend pings this on load to confirm the local server is running.
    Returns 200 + a JSON object with server metadata.
    """
    with _state_lock:
        return jsonify({
            "ok": True,
            "server": "deepdive-local-server",
            "version": "1.0.0",
            "total_received": _state["total_received"],
            "last_dag_time": _state["last_dag_time"],
        })


@app.route("/gemini_key", methods=["GET"])
def gemini_key():
    """
    Returns the Gemini API key from the GEMINI_API_KEY environment variable.

    SECURITY: This endpoint is only useful when called from localhost because
    the CORS policy restricts it to the ALLOWED_ORIGINS list above.  The key
    is never logged and never forwarded — the browser calls Gemini directly.

    If the key is not set, returns a 404 so the frontend can show a helpful
    error message rather than silently failing.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        # Return 404 with a descriptive message
        return jsonify({
            "error": "GEMINI_API_KEY environment variable is not set.",
            "hint": (
                "Windows PowerShell: $env:GEMINI_API_KEY = 'your-key-here'\n"
                "Windows CMD:        set GEMINI_API_KEY=your-key-here\n"
                "Linux/macOS:        export GEMINI_API_KEY=your-key-here"
            ),
        }), 404

    # Only return the key — nothing else
    return jsonify({"key": key})


@app.route("/dag", methods=["POST", "OPTIONS"])
def receive_dag():
    """
    Main endpoint: receive a DAG JSON payload from the web frontend.

    Expected request body (application/json):
    {
        "nodes":     [ { "id": 1, "name": "Rain", "emoji": "🌧️", ... }, ... ],
        "edges":     [ [1, 2], [1, 3], ... ],
        "leaf_ids":  [3, 4],
        "root_ids":  [1],
        "parents":   { "2": [1], "3": [1] },
        "var_types": { "1": "continuous", ... },
        "levels":    {}
    }

    On success: saves the graph as a timestamped JSON file and returns 200.
    On failure: returns 400 with a list of validation errors.
    """
    # Flask + flask-cors handle OPTIONS (preflight) automatically.
    # This branch is here as a safety net only.
    if request.method == "OPTIONS":
        return "", 204

    # ── Parse JSON body ────────────────────────────────────────────────────
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    try:
        graph = request.get_json(force=True)
    except Exception as exc:
        return jsonify({"error": f"JSON parse error: {exc}"}), 400

    if not isinstance(graph, dict):
        return jsonify({"error": "Payload must be a JSON object (dict)"}), 400

    # ── Validate ──────────────────────────────────────────────────────────
    errors = _validate_dag(graph)
    if errors:
        log.warning(f"Rejected DAG — {len(errors)} validation error(s): {errors}")
        return jsonify({"error": "Invalid DAG", "details": errors}), 400

    # ── Save to disk ──────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Timestamped filename so multiple submissions don't overwrite each other
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = OUTPUT_DIR / f"dag_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)

    log.info(
        f"Saved DAG → {filename}  "
        f"({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)"
    )

    # ── Update shared state ────────────────────────────────────────────────
    with _state_lock:
        _state["last_dag_path"] = str(filename)
        _state["last_dag_time"] = datetime.now().isoformat()
        _state["total_received"] += 1

    # ── Optionally trigger sampler ─────────────────────────────────────────
    if AUTO_SAMPLE:
        t = threading.Thread(
            target=_auto_sample_background,
            args=(filename, AUTO_SAMPLE_ROWS),
            daemon=True,  # dies when main process exits
        )
        t.start()

    return jsonify({
        "ok": True,
        "saved_to": str(filename),
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
    })


@app.route("/result", methods=["GET"])
def result():
    """
    Lets the frontend poll for the path of the most recently saved DAG.
    Useful for UX feedback ("your DAG was saved to dag_outputs/dag_20240510_143201.json").
    """
    with _state_lock:
        return jsonify({
            "last_dag_path": _state["last_dag_path"],
            "last_dag_time": _state["last_dag_time"],
            "total_received": _state["total_received"],
        })


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DeepDive local bridge server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=str,
        default=str(OUTPUT_DIR),
        help=f"Directory to save received DAG JSON files (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--auto-sample",
        action="store_true",
        default=AUTO_SAMPLE,
        help="Automatically run dag_sampler after each received DAG",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=AUTO_SAMPLE_ROWS,
        help=f"Rows to generate when --auto-sample is set (default: {AUTO_SAMPLE_ROWS})",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Apply CLI overrides to module-level config variables
    global OUTPUT_DIR, AUTO_SAMPLE, AUTO_SAMPLE_ROWS
    OUTPUT_DIR = Path(args.output_dir)
    AUTO_SAMPLE = args.auto_sample
    AUTO_SAMPLE_ROWS = args.sample_rows

    # Print a clear startup banner so users know the server is running
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║         DeepDive — Local Bridge Server  v1.0.0      ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print()
    print(f"  Listening on  :  http://localhost:{args.port}")
    print(f"  Saving DAGs to:  {OUTPUT_DIR.resolve()}")
    print(f"  Auto-sample   :  {'ON (' + str(AUTO_SAMPLE_ROWS) + ' rows)' if AUTO_SAMPLE else 'OFF'}")
    print()

    # Warn if Gemini key is not set
    if not os.environ.get("GEMINI_API_KEY"):
        print("  ⚠  GEMINI_API_KEY not set — LLM features will be unavailable.")
        print("     Set it with:  $env:GEMINI_API_KEY = 'your-key-here'  (PowerShell)")
        print()

    print("  Allowed origins:")
    for origin in ALLOWED_ORIGINS:
        print(f"    • {origin}")
    print()
    print("  Press Ctrl+C to stop.\n")

    # Start Flask.  use_reloader=False is important — the reloader spawns a
    # second process which breaks the threading.Lock and confuses the state dict.
    app.run(
        host="127.0.0.1",   # only accept connections from localhost
        port=args.port,
        debug=False,        # keep False in production; set True for dev tracing
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
