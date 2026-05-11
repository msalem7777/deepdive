"""
local_server.py
════════════════════════════════════════════════════════════════════════════════
DeepDive — Local bridge server.

ENDPOINTS
─────────
  GET  /status          Health check; returns server state.
  GET  /gemini_key      Returns the LLM API key from env (GEMINI_API_KEY).
  POST /dag             Receive and save a DAG JSON from the web frontend.
  GET  /result          Poll for the latest saved DAG path.
  POST /upload          Receive a dataset file + DAG metadata. Saves the file
                        to datasets/, saves the DAG to dag_outputs/, then
                        launches analyze.py in a background process.
  GET  /upload_status   Poll for the status of the running analysis job.
  GET  /download/<name> Serve a file from results/ (predictions CSV, model).

DATASET PIPELINE
────────────────
  Browser  →  POST /upload  (multipart: file + dag JSON + response_variable)
  Server   →  saves file to datasets/<timestamp>_<filename>
           →  saves DAG  to dag_outputs/<timestamp>.json
           →  runs:  python analyze.py
                       --input   datasets/<file>
                       --response <column_name>
                       --dag     dag_outputs/<timestamp>.json
                       --output  results/<timestamp>/
  Browser  →  polls GET /upload_status until status == "complete" | "error"
           →  receives predictions_url + optional model_url in the response

REQUIREMENTS
────────────
    pip install flask flask-cors
    analyze.py must be in the same directory as local_server.py.

USAGE
─────
    python local_server.py               # default port 7432
    python local_server.py --port 8080   # custom port
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

# ── Flask + CORS ──────────────────────────────────────────────────────────────
try:
    from flask import Flask, jsonify, request, send_file
    from flask_cors import CORS
except ImportError:
    print(
        "\n  ERROR: Flask or flask-cors not installed.\n"
        "  Run:  pip install flask flask-cors\n"
    )
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_PORT = 7432

# DAG JSON files land here
DAG_DIR = Path("dag_outputs")

# Uploaded dataset files land here.
# This folder is intentionally excluded from the git repo via .gitignore
# so user data is never committed.
DATASET_DIR = Path("datasets")

# Analysis script outputs land here (predictions, model, status)
RESULTS_DIR = Path("results")

# Path to the analysis script (placeholder until the real one is ready)
ANALYZE_SCRIPT = Path("analyze.py")

# Max upload size — 1 GB expressed in bytes.
# Flask will reject requests larger than this before they hit our handler.
MAX_UPLOAD_BYTES = 1 * 1024 * 1024 * 1024   # 1 GB

# Allowed browser origins for CORS.
# Add your GitHub Pages URL here once you have it.
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5500",
    "https://msalem7777.github.io",
]

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="  %(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("deepdive-server")

# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────

flask_app = Flask(__name__)

# Set maximum content length — Flask enforces this automatically and returns
# 413 Request Entity Too Large if exceeded.
flask_app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

CORS(
    flask_app,
    origins=ALLOWED_ORIGINS,
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared in-memory state (protected by a lock for thread safety)
# ─────────────────────────────────────────────────────────────────────────────

_lock  = threading.Lock()
_state = {
    # DAG tracking
    "last_dag_path": None,
    "last_dag_time": None,
    "total_dags":    0,

    # Upload / analysis job tracking
    # Only one job runs at a time (sufficient for local single-user use).
    "job": {
        "status":          "idle",   # "idle"|"running"|"complete"|"error"
        "timestamp":       None,     # job timestamp string (used as folder name)
        "dataset_path":    None,     # Path to uploaded file
        "dag_path":        None,     # Path to associated DAG JSON
        "response_var":    None,     # Column name of the response variable
        "results_dir":     None,     # Path to results output folder
        "predictions_url": None,     # Relative URL for predictions CSV download
        "model_url":       None,     # Relative URL for model download (if requested)
        "n_predicted":     0,        # Number of rows predicted
        "error_msg":       None,     # Error message if status == "error"
        "process":         None,     # subprocess.Popen handle (not serialised)
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# DAG validation helper
# ─────────────────────────────────────────────────────────────────────────────

def _validate_dag(graph: dict) -> list[str]:
    """
    Lightweight structural check on a graph dict.
    Returns a list of error strings; empty list = valid.
    """
    errors = []
    if "nodes" not in graph: errors.append("Missing 'nodes'")
    if "edges" not in graph: errors.append("Missing 'edges'")
    if errors: return errors

    node_ids = set()
    for i, n in enumerate(graph["nodes"]):
        if not isinstance(n, dict):
            errors.append(f"Node {i} is not a dict"); continue
        if "id" not in n:
            errors.append(f"Node {i} missing 'id'")
        else:
            node_ids.add(n["id"])
        if not str(n.get("name", "")).strip():
            errors.append(f"Node {i} missing 'name'")

    for j, e in enumerate(graph.get("edges", [])):
        if not (isinstance(e, list) and len(e) == 2):
            errors.append(f"Edge {j} must be [src_id, tgt_id]")
        else:
            if e[0] not in node_ids: errors.append(f"Edge {j} src={e[0]} unknown")
            if e[1] not in node_ids: errors.append(f"Edge {j} tgt={e[1]} unknown")

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Analysis job runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_analysis(
    dataset_path: Path,
    dag_path:     Path,
    response_var: str,
    results_dir:  Path,
    timestamp:    str,
) -> None:
    """
    Launch analyze.py as a subprocess and monitor it.
    Updates _state["job"] throughout.

    This function runs in a daemon thread so it never blocks HTTP responses.

    CONTRACT with analyze.py:
    ─────────────────────────
    The script is called with:
        python analyze.py
            --input    <dataset_path>
            --response <response_var>
            --dag      <dag_path>
            --output   <results_dir>

    It must write to <results_dir>:
        predictions.csv   — rows where response was missing + predicted values
        status.json       — { "status": "complete"|"error",
                               "n_predicted": <int>,
                               "response": "<col>",
                               "message": "<optional human-readable note>" }

    If the user requested the model (future feature), the script also writes:
        model.pt          — serialised PyTorch model (TorchScript format)
    """
    results_dir.mkdir(parents=True, exist_ok=True)

    with _lock:
        _state["job"]["status"]      = "running"
        _state["job"]["results_dir"] = str(results_dir)

    log.info(f"Starting analysis job {timestamp}")
    log.info(f"  Dataset  : {dataset_path}")
    log.info(f"  Response : {response_var}")
    log.info(f"  DAG      : {dag_path}")
    log.info(f"  Output   : {results_dir}")

    try:
        cmd = [
            sys.executable,          # same Python interpreter running this server
            str(ANALYZE_SCRIPT),
            "--input",    str(dataset_path),
            "--response", response_var,
            "--dag",      str(dag_path),
            "--output",   str(results_dir),
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        with _lock:
            _state["job"]["process"] = proc

        # Stream subprocess output to our log
        for line in proc.stdout:
            log.info(f"  [analyze] {line.rstrip()}")

        proc.wait()

        # ── Read status.json written by analyze.py ────────────────────────
        status_file = results_dir / "status.json"
        if status_file.exists():
            with open(status_file) as f:
                job_result = json.load(f)
        else:
            # analyze.py didn't write a status file — treat as error
            job_result = {
                "status":      "error",
                "n_predicted": 0,
                "message":     "analyze.py did not write a status.json file.",
            }

        predictions_path = results_dir / "predictions.csv"
        model_path       = results_dir / "model.pt"

        with _lock:
            _state["job"]["status"]      = job_result.get("status", "error")
            _state["job"]["n_predicted"] = job_result.get("n_predicted", 0)
            _state["job"]["error_msg"]   = job_result.get("message") if job_result.get("status") == "error" else None
            _state["job"]["process"]     = None

            # Build download URLs if files exist
            if predictions_path.exists():
                _state["job"]["predictions_url"] = f"/download/{timestamp}/predictions.csv"
            if model_path.exists():
                _state["job"]["model_url"] = f"/download/{timestamp}/model.pt"

        log.info(f"Analysis job {timestamp} → {job_result.get('status')}")

    except Exception as exc:
        log.error(f"Analysis job {timestamp} crashed: {exc}")
        with _lock:
            _state["job"]["status"]    = "error"
            _state["job"]["error_msg"] = str(exc)
            _state["job"]["process"]   = None


# ─────────────────────────────────────────────────────────────────────────────
# Routes — health + key
# ─────────────────────────────────────────────────────────────────────────────

@flask_app.route("/status", methods=["GET"])
def status():
    """Health check — returns server state and current job status."""
    with _lock:
        job = {k: v for k, v in _state["job"].items() if k != "process"}
        return jsonify({
            "ok":            True,
            "server":        "deepdive-local-server",
            "version":       "2.0.0",
            "total_dags":    _state["total_dags"],
            "last_dag_time": _state["last_dag_time"],
            "job":           job,
        })


@flask_app.route("/gemini_key", methods=["GET"])
def gemini_key():
    """
    Return the LLM API key from the GEMINI_API_KEY environment variable.
    Only reachable from localhost (CORS restricts cross-origin access).
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return jsonify({
            "error": "GEMINI_API_KEY not set.",
            "hint": (
                "Windows PowerShell: $env:GEMINI_API_KEY = 'your-key'\n"
                "Linux/macOS:        export GEMINI_API_KEY=your-key"
            ),
        }), 404
    return jsonify({"key": key})


# ─────────────────────────────────────────────────────────────────────────────
# Routes — DAG
# ─────────────────────────────────────────────────────────────────────────────

@flask_app.route("/dag", methods=["POST", "OPTIONS"])
def receive_dag():
    """
    Receive a DAG JSON from the web frontend, validate, and save it.
    Body: application/json matching the DeepDive graph schema.
    """
    if request.method == "OPTIONS":
        return "", 204

    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    try:
        graph = request.get_json(force=True)
    except Exception as e:
        return jsonify({"error": f"JSON parse error: {e}"}), 400

    errors = _validate_dag(graph)
    if errors:
        return jsonify({"error": "Invalid DAG", "details": errors}), 400

    DAG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dag_path  = DAG_DIR / f"dag_{timestamp}.json"

    with open(dag_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)

    log.info(f"DAG saved → {dag_path}  ({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")

    with _lock:
        _state["last_dag_path"] = str(dag_path)
        _state["last_dag_time"] = datetime.now().isoformat()
        _state["total_dags"]   += 1

    return jsonify({
        "ok":       True,
        "saved_to": str(dag_path),
        "nodes":    len(graph["nodes"]),
        "edges":    len(graph["edges"]),
    })


@flask_app.route("/result", methods=["GET"])
def result():
    """Return the path of the most recently saved DAG."""
    with _lock:
        return jsonify({
            "last_dag_path": _state["last_dag_path"],
            "last_dag_time": _state["last_dag_time"],
            "total_dags":    _state["total_dags"],
        })


# ─────────────────────────────────────────────────────────────────────────────
# Routes — dataset upload
# ─────────────────────────────────────────────────────────────────────────────

@flask_app.route("/upload", methods=["POST", "OPTIONS"])
def upload_dataset():
    """
    Receive a dataset file + DAG metadata from the browser.

    Expected multipart/form-data fields:
        file              — the CSV or XLSX file (binary)
        dag               — DAG JSON string
        response_variable — column name of the response variable (string)

    Workflow:
        1. Save the file  to datasets/<timestamp>_<filename>
        2. Save the DAG   to dag_outputs/<timestamp>.json
        3. Launch analyze.py in a background thread
        4. Return 202 Accepted immediately
    """
    if request.method == "OPTIONS":
        return "", 204

    # ── Validate file field ───────────────────────────────────────────────
    if "file" not in request.files:
        return jsonify({"error": "No file field in request."}), 400

    uploaded_file = request.files["file"]
    if not uploaded_file.filename:
        return jsonify({"error": "Empty filename."}), 400

    # ── Validate response variable ────────────────────────────────────────
    response_var = request.form.get("response_variable", "").strip()
    if not response_var:
        return jsonify({"error": "response_variable field is required."}), 400

    # ── Parse DAG (optional — a DAG may not be built yet) ─────────────────
    dag_json_str = request.form.get("dag", "")
    graph        = None
    dag_path     = None

    if dag_json_str:
        try:
            graph = json.loads(dag_json_str)
        except json.JSONDecodeError:
            return jsonify({"error": "dag field is not valid JSON."}), 400

        errors = _validate_dag(graph)
        if errors:
            return jsonify({"error": "Invalid DAG", "details": errors}), 400

    # ── Generate a shared timestamp for this job ──────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Save dataset file ─────────────────────────────────────────────────
    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    # Sanitise filename — strip path components, replace spaces
    safe_name    = Path(uploaded_file.filename).name.replace(" ", "_")
    dataset_path = DATASET_DIR / f"{timestamp}_{safe_name}"
    uploaded_file.save(str(dataset_path))

    log.info(f"Dataset saved → {dataset_path}  ({dataset_path.stat().st_size:,} bytes)")

    # ── Save DAG alongside the dataset ────────────────────────────────────
    if graph:
        DAG_DIR.mkdir(parents=True, exist_ok=True)
        dag_path = DAG_DIR / f"dag_{timestamp}.json"
        with open(dag_path, "w", encoding="utf-8") as f:
            json.dump(graph, f, indent=2, ensure_ascii=False)
        log.info(f"DAG saved → {dag_path}")

        with _lock:
            _state["last_dag_path"] = str(dag_path)
            _state["last_dag_time"] = datetime.now().isoformat()
            _state["total_dags"]   += 1

    # ── Reset job state and launch analysis in background ─────────────────
    results_dir = RESULTS_DIR / timestamp

    with _lock:
        _state["job"] = {
            "status":          "running",
            "timestamp":       timestamp,
            "dataset_path":    str(dataset_path),
            "dag_path":        str(dag_path) if dag_path else None,
            "response_var":    response_var,
            "results_dir":     str(results_dir),
            "predictions_url": None,
            "model_url":       None,
            "n_predicted":     0,
            "error_msg":       None,
            "process":         None,
        }

    # Check analyze.py exists before launching
    if not ANALYZE_SCRIPT.exists():
        log.warning(f"analyze.py not found at {ANALYZE_SCRIPT.resolve()} — job marked as error.")
        with _lock:
            _state["job"]["status"]    = "error"
            _state["job"]["error_msg"] = (
                f"analyze.py not found. Place it at {ANALYZE_SCRIPT.resolve()}"
            )
        return jsonify({
            "ok":        False,
            "error":     "analyze.py not found on server.",
            "timestamp": timestamp,
        }), 500

    thread = threading.Thread(
        target=_run_analysis,
        args=(dataset_path, dag_path or Path(""), response_var, results_dir, timestamp),
        daemon=True,
    )
    thread.start()

    log.info(f"Analysis job {timestamp} started for response='{response_var}'")

    # Return 202 Accepted — the browser will poll /upload_status
    return jsonify({
        "ok":          True,
        "accepted":    True,
        "timestamp":   timestamp,
        "dataset":     str(dataset_path),
        "response_var": response_var,
        "message":     "Dataset received. Analysis started.",
    }), 202


# ─────────────────────────────────────────────────────────────────────────────
# Routes — job status polling
# ─────────────────────────────────────────────────────────────────────────────

@flask_app.route("/upload_status", methods=["GET"])
def upload_status():
    """
    Poll for the status of the current analysis job.

    Returns:
        status          — "idle" | "running" | "complete" | "error"
        n_predicted     — number of rows predicted (available when complete)
        predictions_url — relative URL for downloading predictions.csv
        model_url       — relative URL for downloading model.pt (if present)
        error_msg       — error description (if status == "error")
    """
    with _lock:
        job = {k: v for k, v in _state["job"].items() if k != "process"}
    return jsonify(job)


# ─────────────────────────────────────────────────────────────────────────────
# Routes — file downloads
# ─────────────────────────────────────────────────────────────────────────────

@flask_app.route("/download/<timestamp>/<filename>", methods=["GET"])
def download_file(timestamp: str, filename: str):
    """
    Serve a file from results/<timestamp>/<filename>.

    Allowed filenames: predictions.csv, model.pt
    The timestamp path component prevents directory traversal attacks.
    """
    # Whitelist allowed filenames
    ALLOWED_FILES = {"predictions.csv", "model.pt"}
    if filename not in ALLOWED_FILES:
        return jsonify({"error": f"File '{filename}' is not available for download."}), 403

    # Sanitise timestamp — only allow alphanumeric and underscores
    safe_ts = "".join(c for c in timestamp if c.isalnum() or c == "_")
    file_path = RESULTS_DIR / safe_ts / filename

    if not file_path.exists():
        return jsonify({"error": f"File not found: {file_path}"}), 404

    log.info(f"Serving download: {file_path}")
    return send_file(
        str(file_path.resolve()),
        as_attachment=True,
        download_name=filename,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeepDive local bridge server v2")
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT)
    parser.add_argument("--output-dir", type=str, default=str(DAG_DIR))
    parser.add_argument("--dataset-dir", type=str, default=str(DATASET_DIR))
    parser.add_argument("--results-dir", type=str, default=str(RESULTS_DIR))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    global DAG_DIR, DATASET_DIR, RESULTS_DIR
    DAG_DIR     = Path(args.output_dir)
    DATASET_DIR = Path(args.dataset_dir)
    RESULTS_DIR = Path(args.results_dir)

    # Create directories (datasets/ is gitignored — create it locally)
    for d in [DAG_DIR, DATASET_DIR, RESULTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║       DeepDive — Local Bridge Server  v2.0.0        ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print()
    print(f"  Listening on   :  http://localhost:{args.port}")
    print(f"  DAG outputs    :  {DAG_DIR.resolve()}")
    print(f"  Datasets       :  {DATASET_DIR.resolve()}  (gitignored)")
    print(f"  Results        :  {RESULTS_DIR.resolve()}")
    print(f"  Analysis script:  {ANALYZE_SCRIPT.resolve()}")
    print(f"  Max upload     :  {MAX_UPLOAD_BYTES // (1024**3)} GB")
    print()

    if not os.environ.get("GEMINI_API_KEY"):
        print("  ⚠  GEMINI_API_KEY not set — LLM key endpoint will return 404.")
        print()

    if not ANALYZE_SCRIPT.exists():
        print(f"  ⚠  analyze.py not found — uploads will fail until it is placed here.")
        print()

    print("  Allowed origins:")
    for o in ALLOWED_ORIGINS:
        print(f"    • {o}")
    print()
    print("  Press Ctrl+C to stop.\n")

    flask_app.run(
        host="127.0.0.1",
        port=args.port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
