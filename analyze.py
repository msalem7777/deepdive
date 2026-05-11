"""
analyze.py
════════════════════════════════════════════════════════════════════════════════
DeepDive — Analysis placeholder script.

This is a PLACEHOLDER that will be replaced with the real TabPFN-based
foundation model pipeline.  It implements the full file contract so that
the server, browser, and results UI work end-to-end before the real model
is ready.

WHAT THIS PLACEHOLDER DOES
───────────────────────────
1. Reads the uploaded dataset (CSV or XLSX).
2. Identifies rows where the response variable is missing (NaN / empty).
3. For numeric responses: fills predictions with the column mean.
   For categorical responses: fills predictions with the column mode.
4. Writes predictions.csv — only the rows that had missing values, with a
   new column "predicted_<response>" containing the estimated values.
5. Writes status.json — the contract file local_server.py reads to know
   whether the job succeeded and how many rows were predicted.

REAL SCRIPT CONTRACT (do not change these outputs when replacing)
────────────────────────────────────────────────────────────────
  --input    <path>   : path to the dataset file (CSV or XLSX)
  --response <name>   : column name of the response variable
  --dag      <path>   : path to the DAG JSON (may be empty string if no DAG)
  --output   <dir>    : directory to write all outputs into

  Outputs written to <dir>:
    predictions.csv     REQUIRED — rows with missing response + predictions
    status.json         REQUIRED — { "status": "complete"|"error",
                                      "n_predicted": <int>,
                                      "response": "<col>",
                                      "message": "<optional note>" }
    model.pt            OPTIONAL — serialised TorchScript model
                                   (only written if user requested it)

REQUIREMENTS (placeholder only)
────────────────────────────────
    pip install pandas openpyxl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime


def _load_dataset(path: str):
    """
    Load a CSV or XLSX file into a pandas DataFrame.
    Returns (df, error_string).  error_string is None on success.
    """
    try:
        import pandas as pd
    except ImportError:
        return None, "pandas is not installed. Run: pip install pandas"

    p = Path(path)
    if not p.exists():
        return None, f"File not found: {path}"

    ext = p.suffix.lower()
    try:
        if ext == ".csv":
            # Try UTF-8 first, fall back to latin-1 for Windows-encoded files
            try:
                df = pd.read_csv(path, encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(path, encoding="latin-1")
        elif ext in (".xlsx", ".xls"):
            try:
                import openpyxl  # noqa: F401
            except ImportError:
                return None, "openpyxl not installed. Run: pip install openpyxl"
            df = pd.read_excel(path)
        else:
            return None, f"Unsupported file type: {ext}"
    except Exception as e:
        return None, f"Could not read file: {e}"

    return df, None


def _write_status(output_dir: Path, status: str, n_predicted: int,
                  response: str, message: str = "") -> None:
    """Write the status.json file that local_server.py reads."""
    status_data = {
        "status":      status,
        "n_predicted": n_predicted,
        "response":    response,
        "message":     message,
        "timestamp":   datetime.now().isoformat(),
    }
    with open(output_dir / "status.json", "w") as f:
        json.dump(status_data, f, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DeepDive analysis placeholder script"
    )
    parser.add_argument("--input",    required=True,  help="Path to dataset file")
    parser.add_argument("--response", required=True,  help="Response column name")
    parser.add_argument("--dag",      default="",     help="Path to DAG JSON (optional)")
    parser.add_argument("--output",   required=True,  help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[analyze] Input    : {args.input}")
    print(f"[analyze] Response : {args.response}")
    print(f"[analyze] DAG      : {args.dag or '(none)'}")
    print(f"[analyze] Output   : {output_dir}")

    # ── Load dataset ──────────────────────────────────────────────────────────
    df, err = _load_dataset(args.input)
    if err:
        print(f"[analyze] ERROR: {err}", file=sys.stderr)
        _write_status(output_dir, "error", 0, args.response, err)
        sys.exit(1)

    print(f"[analyze] Loaded {len(df)} rows × {len(df.columns)} columns")

    # ── Validate response column ──────────────────────────────────────────────
    if args.response not in df.columns:
        msg = (
            f"Response column '{args.response}' not found in dataset. "
            f"Available columns: {list(df.columns)}"
        )
        print(f"[analyze] ERROR: {msg}", file=sys.stderr)
        _write_status(output_dir, "error", 0, args.response, msg)
        sys.exit(1)

    # ── Find rows with missing response ───────────────────────────────────────
    import pandas as pd

    missing_mask = df[args.response].isna()
    n_missing    = missing_mask.sum()

    print(f"[analyze] Rows with missing '{args.response}': {n_missing}")

    if n_missing == 0:
        # No missing values — nothing to predict
        msg = f"No missing values found in '{args.response}'. Nothing to predict."
        print(f"[analyze] {msg}")

        # Write an empty predictions file so the contract is satisfied
        empty_pred = df[missing_mask].copy()
        empty_pred[f"predicted_{args.response}"] = []
        empty_pred.to_csv(output_dir / "predictions.csv", index=False)

        _write_status(output_dir, "complete", 0, args.response, msg)
        sys.exit(0)

    # ── Placeholder prediction logic ──────────────────────────────────────────
    # Real script: replace everything in this block with the TabPFN pipeline.
    #
    # The placeholder uses the simplest possible estimator:
    #   - Numeric columns  → mean imputation
    #   - Other columns    → mode imputation
    # This produces a valid predictions.csv in exactly the right format.

    response_series = df[args.response].dropna()

    if pd.api.types.is_numeric_dtype(df[args.response]):
        fill_value = float(response_series.mean())
        pred_label = f"mean ({fill_value:.4f})"
    else:
        fill_value = str(response_series.mode().iloc[0]) if len(response_series) else "unknown"
        pred_label = f"mode ({fill_value})"

    print(f"[analyze] Placeholder prediction: {pred_label}")

    # Build the predictions DataFrame
    # Contains only the rows that had missing response values
    pred_df = df[missing_mask].copy().reset_index(drop=False)
    pred_df.rename(columns={"index": "original_row_index"}, inplace=True)
    pred_df[f"predicted_{args.response}"] = fill_value

    # ── Write predictions.csv ─────────────────────────────────────────────────
    pred_path = output_dir / "predictions.csv"
    pred_df.to_csv(pred_path, index=False)
    print(f"[analyze] Wrote predictions → {pred_path}  ({n_missing} rows)")

    # ── Write status.json ─────────────────────────────────────────────────────
    _write_status(
        output_dir,
        status      = "complete",
        n_predicted = int(n_missing),
        response    = args.response,
        message     = (
            f"Placeholder analysis complete. "
            f"{n_missing} rows predicted using {pred_label}. "
            f"Replace analyze.py with the real pipeline for actual predictions."
        ),
    )

    print(f"[analyze] Done.")
    sys.exit(0)


if __name__ == "__main__":
    main()
