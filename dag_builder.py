"""
DeepDive - Interactive Directed Acyclic Graph Editor
Creates graphs visually and saves them as models for noise propagation.
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import json
import math
import random
import os
import threading

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

try:
    import pandas as pd
    _PANDAS = True
except ImportError:
    _PANDAS = False

try:
    from dag_sampler import DAGSampler
    _SAMPLER = True
except Exception:
    _SAMPLER = False

try:
    from dag_hypothesis_test import HypothesisTester, HypothesisResult
    _HYPOTHESIS = True
except Exception as _e:
    _HYPOTHESIS = False
    _HYPOTHESIS_ERR = str(_e)


# ─── Color Palette ────────────────────────────────────────────────────────────
BG_DARK     = "#0d1117"
BG_PANEL    = "#161b22"
BG_CARD     = "#21262d"
ACCENT      = "#58a6ff"
ACCENT2     = "#3fb950"
ACCENT3     = "#f78166"
ACCENT4     = "#d2a8ff"
TEXT_MAIN   = "#e6edf3"
TEXT_DIM    = "#8b949e"
BORDER      = "#30363d"
NODE_FILL   = "#1f6feb"
NODE_LEAF   = "#238636"
NODE_ROOT   = "#9a3412"
EDGE_COLOR  = "#58a6ff"
SELECTED    = "#f78166"


# Variable types with display properties
VAR_TYPES = {
    "continuous": {"label": "~",  "color": "#58a6ff", "desc": "Continuous (real-valued)"},
    "binary":     {"label": "01", "color": "#3fb950", "desc": "Binary (0 or 1)"},
    "ordinal":    {"label": "1…n","color": "#d2a8ff", "desc": "Ordinal (integer levels)"},
    "categorical":{"label": "A…Z","color": "#ffa657", "desc": "Categorical (one-hot)"},
    "count":      {"label": "#",  "color": "#79c0ff", "desc": "Count (non-negative int)"},
}
VAR_TYPE_NAMES = list(VAR_TYPES.keys())


class Node:
    _id_counter = 0

    def __init__(self, x, y, name=None, var_type="continuous"):
        Node._id_counter += 1
        self.id = Node._id_counter
        self.x = x
        self.y = y
        self.name = name or f"X{self.id}"
        self.var_type = var_type   # "continuous" | "binary" | "ordinal" | "categorical" | "count"
        self.radius = 32
        self.canvas_id = None
        self.text_id = None
        self.selected = False

    def contains(self, x, y):
        return math.hypot(x - self.x, y - self.y) <= self.radius

    def to_dict(self):
        return {"id": self.id, "name": self.name, "x": self.x, "y": self.y,
                "var_type": self.var_type}



# ══════════════════════════════════════════════════════════════════════════════
# Generate Data Dialog
# ══════════════════════════════════════════════════════════════════════════════

class GenerateDialog:
    """
    Modal dialog for configuring and running DAGSampler data generation.
    Options:
      - Number of datasets / rows / seed
      - Edge function types to include
      - Noise distributions to include
      - Post-processing probability
      - Export: graph JSON, SCM configs JSON, datasets (separate CSVs,
                single CSV, single Excel, Parquet)
    """

    def __init__(self, parent, graph: dict):
        self.graph   = graph
        self.result_message = ""

        self.window = tk.Toplevel(parent)
        self.window.title("Generate Synthetic Datasets")
        self.window.configure(bg=BG_DARK)
        self.window.geometry("680x760")
        self.window.minsize(560, 500)
        self.window.resizable(True, True)
        self.window.grab_set()
        self.window.transient(parent)

        self._build()

    def _section(self, parent, title):
        tk.Label(parent, text=title, font=("Courier", 10, "bold"),
                 fg=TEXT_DIM, bg=BG_DARK, anchor="w").pack(fill=tk.X, pady=(12, 2))
        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X)

    def _row(self, parent, label, widget_fn):
        f = tk.Frame(parent, bg=BG_DARK)
        f.pack(fill=tk.X, pady=3)
        tk.Label(f, text=label, font=("Courier", 10), fg=TEXT_MAIN,
                 bg=BG_DARK, width=22, anchor="w").pack(side=tk.LEFT)
        w = widget_fn(f)
        w.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return w

    def _spinbox(self, parent, from_, to, default):
        v = tk.StringVar(value=str(default))
        sb = tk.Spinbox(parent, from_=from_, to=to, textvariable=v,
                        font=("Courier", 10), bg=BG_CARD, fg=TEXT_MAIN,
                        buttonbackground=BG_CARD, relief=tk.FLAT,
                        insertbackground=ACCENT, width=10)
        sb._var = v
        return sb

    def _build(self):
        # ── Scrollable body + fixed footer ───────────────────────────────
        # The progress bar and buttons stay pinned at the bottom; everything
        # else scrolls so the window can be resized freely in both directions.
        self.window.grid_rowconfigure(0, weight=1)
        self.window.grid_rowconfigure(1, weight=0)
        self.window.grid_columnconfigure(0, weight=1)

        # Scrollable area
        scroll_frame = tk.Frame(self.window, bg=BG_DARK)
        scroll_frame.grid(row=0, column=0, sticky="nsew")
        scroll_frame.grid_rowconfigure(0, weight=1)
        scroll_frame.grid_columnconfigure(0, weight=1)

        vscroll = tk.Scrollbar(scroll_frame, orient=tk.VERTICAL,
                               bg=BG_CARD, troughcolor=BG_DARK)
        vscroll.grid(row=0, column=1, sticky="ns")

        sc = tk.Canvas(scroll_frame, bg=BG_DARK, bd=0,
                       highlightthickness=0, yscrollcommand=vscroll.set)
        sc.grid(row=0, column=0, sticky="nsew")
        vscroll.config(command=sc.yview)

        inner = tk.Frame(sc, bg=BG_DARK)
        win_id = sc.create_window((0, 0), window=inner, anchor="nw")

        inner.bind("<Configure>",
                   lambda e: sc.configure(scrollregion=sc.bbox("all")))
        sc.bind("<Configure>",
                lambda e: sc.itemconfig(win_id, width=e.width))
        sc.bind_all("<MouseWheel>",
                    lambda e: sc.yview_scroll(int(-1*(e.delta/120)), "units"))

        p = tk.Frame(inner, bg=BG_DARK)   # padded content wrapper
        p.pack(fill=tk.BOTH, expand=True, padx=20, pady=12)

        # Helper: two-column grid row (label col=0, widget col=1)
        def _glabel(parent, row, text):
            tk.Label(parent, text=text, font=("Courier", 10), fg=TEXT_MAIN,
                     bg=BG_DARK, anchor="w"
                     ).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 16))

        def _gspin(parent, row, from_, to, default):
            v = tk.StringVar(value=str(default))
            sb = tk.Spinbox(parent, from_=from_, to=to, textvariable=v,
                            font=("Courier", 10), bg=BG_CARD, fg=TEXT_MAIN,
                            buttonbackground=BG_CARD, relief=tk.FLAT,
                            insertbackground=ACCENT, width=12)
            sb._var = v
            sb.grid(row=row, column=1, sticky="ew", pady=3)
            return sb

        # ── Sampling parameters ───────────────────────────────────────────
        self._section(p, "SAMPLING PARAMETERS")

        sp = tk.Frame(p, bg=BG_DARK)
        sp.pack(fill=tk.X, pady=2)
        sp.columnconfigure(1, weight=1)

        _glabel(sp, 0, "Number of datasets")
        self.n_datasets_sb = _gspin(sp, 0, 1, 1000, 10)

        _glabel(sp, 1, "Rows per dataset")
        self.n_rows_sb = _gspin(sp, 1, 10, 100000, 500)

        # Seed row (special: entry + checkbox)
        _glabel(sp, 2, "Base random seed")
        seed_cell = tk.Frame(sp, bg=BG_DARK)
        seed_cell.grid(row=2, column=1, sticky="ew", pady=3)
        self.seed_var = tk.StringVar(value="42")
        tk.Entry(seed_cell, textvariable=self.seed_var, font=("Courier", 10),
                 bg=BG_CARD, fg=TEXT_MAIN, insertbackground=ACCENT,
                 relief=tk.FLAT, width=10).pack(side=tk.LEFT)
        self.random_seed_var = tk.BooleanVar(value=False)
        tk.Checkbutton(seed_cell, text="random", variable=self.random_seed_var,
                       font=("Courier", 9), fg=TEXT_DIM, bg=BG_DARK,
                       selectcolor=BG_CARD, activebackground=BG_DARK,
                       activeforeground=ACCENT).pack(side=tk.LEFT, padx=10)

        _glabel(sp, 3, "Noise σ  min")
        self.noise_sigma_lo = _gspin(sp, 3, 0.01, 10.0, 0.1)

        _glabel(sp, 4, "Noise σ  max")
        self.noise_sigma_hi = _gspin(sp, 4, 0.01, 10.0, 2.0)

        _glabel(sp, 5, "Post-process prob")
        self.post_proc_sb = _gspin(sp, 5, 0.0, 1.0, 0.3)

        # ── Edge functional forms ─────────────────────────────────────────
        self._section(p, "EDGE FUNCTIONAL FORMS")
        self.edge_vars = {}
        ef = tk.Frame(p, bg=BG_DARK)
        ef.pack(fill=tk.X, pady=4)
        for c in range(3):
            ef.columnconfigure(c, weight=1)
        for i, (key, label, tip) in enumerate([
            ("nn",     "Neural network",   "Linear proj +\nnonlinear activation"),
            ("tree",   "Decision tree",    "Random threshold\nsplits on parents"),
            ("linear", "Linear weights",   "Weighted sum with\nrandom signed coeffs"),
        ]):
            v = tk.BooleanVar(value=True)
            self.edge_vars[key] = v
            cell = tk.Frame(ef, bg=BG_DARK)
            cell.grid(row=0, column=i, sticky="nw", padx=(0, 12), pady=2)
            tk.Checkbutton(cell, text=label, variable=v,
                           font=("Courier", 10, "bold"), fg=TEXT_MAIN, bg=BG_DARK,
                           selectcolor=BG_CARD, activebackground=BG_DARK,
                           activeforeground=ACCENT).pack(anchor="w")
            tk.Label(cell, text=tip, font=("Courier", 8), fg=TEXT_DIM,
                     bg=BG_DARK, justify=tk.LEFT).pack(anchor="w", padx=(20, 0))

        # ── Noise distributions ───────────────────────────────────────────
        self._section(p, "ROOT NODE NOISE DISTRIBUTIONS")
        self.noise_vars = {}
        nd = tk.Frame(p, bg=BG_DARK)
        nd.pack(fill=tk.X, pady=4)
        for c, key in enumerate(["normal", "uniform", "laplace", "beta", "exponential"]):
            nd.columnconfigure(c, weight=1)
            v = tk.BooleanVar(value=True)
            self.noise_vars[key] = v
            tk.Checkbutton(nd, text=key, variable=v,
                           font=("Courier", 10), fg=TEXT_MAIN, bg=BG_DARK,
                           selectcolor=BG_CARD, activebackground=BG_DARK,
                           activeforeground=ACCENT
                           ).grid(row=0, column=c, sticky="w", padx=2)

        # ── Export options ────────────────────────────────────────────────
        self._section(p, "EXPORT OPTIONS")

        self.export_graph_var    = tk.BooleanVar(value=True)
        self.export_configs_var  = tk.BooleanVar(value=True)
        self.export_sep_csv_var  = tk.BooleanVar(value=True)
        self.export_combined_var = tk.BooleanVar(value=False)
        self.export_excel_var    = tk.BooleanVar(value=False)
        self.export_parquet_var  = tk.BooleanVar(value=False)

        exp_grid = tk.Frame(p, bg=BG_DARK)
        exp_grid.pack(fill=tk.X, pady=2)
        exp_grid.columnconfigure(1, weight=1)   # description column expands

        for row_i, (var, label, tip) in enumerate([
            (self.export_graph_var,    "DAG graph JSON",
             "The graph structure (nodes, edges, topology)"),
            (self.export_configs_var,  "SCM configs JSON",
             "Sampled functional forms and noise params for every dataset"),
            (self.export_sep_csv_var,  "Separate CSVs",
             "One CSV file per dataset  (dataset_0000.csv, ...)"),
            (self.export_combined_var, "Combined CSV",
             "All datasets stacked in one file with a dataset_id column"),
            (self.export_excel_var,    "Excel workbook",
             "One sheet per dataset  (requires: pip install openpyxl)"),
            (self.export_parquet_var,  "Parquet files",
             "Columnar binary format per dataset  (requires: pip install pyarrow)"),
        ]):
            tk.Checkbutton(exp_grid, text=label, variable=var,
                           font=("Courier", 10), fg=TEXT_MAIN, bg=BG_DARK,
                           selectcolor=BG_CARD, activebackground=BG_DARK,
                           activeforeground=ACCENT, anchor="w", width=18
                           ).grid(row=row_i, column=0, sticky="w", pady=2)
            # Description label: wraplength is updated dynamically on resize
            desc = tk.Label(exp_grid, text=tip, font=("Courier", 8),
                            fg=TEXT_DIM, bg=BG_DARK, anchor="w", justify=tk.LEFT)
            desc.grid(row=row_i, column=1, sticky="ew", padx=(4, 0), pady=2)

        # ── Output directory ──────────────────────────────────────────────
        self._section(p, "OUTPUT DIRECTORY")
        dir_row = tk.Frame(p, bg=BG_DARK)
        dir_row.pack(fill=tk.X, pady=4)
        self.outdir_var = tk.StringVar(value=os.path.expanduser("~"))
        tk.Entry(dir_row, textvariable=self.outdir_var, font=("Courier", 9),
                 bg=BG_CARD, fg=TEXT_MAIN, insertbackground=ACCENT,
                 relief=tk.FLAT).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(dir_row, text="Browse", font=("Courier", 9),
                  bg=BG_CARD, fg=TEXT_DIM, relief=tk.FLAT, padx=10, pady=4,
                  cursor="hand2",
                  command=self._browse_dir).pack(side=tk.LEFT, padx=(8, 0))

        # ── Fixed footer: progress + buttons ─────────────────────────────
        footer = tk.Frame(self.window, bg=BG_PANEL)
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        tk.Frame(footer, bg=BORDER, height=1).pack(fill=tk.X)

        prog_frame = tk.Frame(footer, bg=BG_PANEL)
        prog_frame.pack(fill=tk.X, padx=20, pady=(8, 4))

        self.progress_var = tk.StringVar(value="")
        tk.Label(prog_frame, textvariable=self.progress_var,
                 font=("Courier", 9), fg=ACCENT2, bg=BG_PANEL,
                 anchor="w").pack(fill=tk.X)
        self.progress_bar = ttk.Progressbar(prog_frame, mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=(2, 0))

        btn_row = tk.Frame(footer, bg=BG_PANEL)
        btn_row.pack(fill=tk.X, padx=20, pady=(6, 14))
        self.generate_btn = tk.Button(
            btn_row, text="▶  Generate & Export",
            font=("Courier", 11, "bold"),
            bg=NODE_FILL, fg=TEXT_MAIN, relief=tk.FLAT,
            activebackground="#2563eb", padx=18, pady=8,
            cursor="hand2", command=self._run)
        self.generate_btn.pack(side=tk.LEFT)
        tk.Button(btn_row, text="✕ Cancel",
                  font=("Courier", 10), bg=BG_CARD, fg=TEXT_DIM,
                  relief=tk.FLAT, padx=12, pady=8, cursor="hand2",
                  command=self.window.destroy).pack(side=tk.RIGHT)

    def _browse_dir(self):
        d = filedialog.askdirectory(title="Choose output folder",
                                    initialdir=self.outdir_var.get())
        if d:
            self.outdir_var.set(d)

    def _get_int(self, spinbox, default):
        try:
            return int(float(spinbox._var.get()))
        except Exception:
            return default

    def _get_float(self, spinbox, default):
        try:
            return float(spinbox._var.get())
        except Exception:
            return default

    def _run(self):
        # Validate choices
        edge_types  = [k for k, v in self.edge_vars.items()  if v.get()]
        noise_types = [k for k, v in self.noise_vars.items() if v.get()]
        if not edge_types:
            messagebox.showwarning("Nothing selected",
                                   "Select at least one edge functional form.",
                                   parent=self.window)
            return
        if not noise_types:
            messagebox.showwarning("Nothing selected",
                                   "Select at least one noise distribution.",
                                   parent=self.window)
            return

        any_export = any([
            self.export_graph_var.get(), self.export_configs_var.get(),
            self.export_sep_csv_var.get(), self.export_combined_var.get(),
            self.export_excel_var.get(), self.export_parquet_var.get(),
        ])
        if not any_export:
            messagebox.showwarning("Nothing to export",
                                   "Enable at least one export option.",
                                   parent=self.window)
            return

        n_datasets  = self._get_int(self.n_datasets_sb, 10)
        n_rows      = self._get_int(self.n_rows_sb, 500)
        pp_prob     = self._get_float(self.post_proc_sb, 0.3)
        sigma_lo    = self._get_float(self.noise_sigma_lo, 0.1)
        sigma_hi    = self._get_float(self.noise_sigma_hi, 2.0)
        outdir      = self.outdir_var.get()

        if self.random_seed_var.get():
            base_seed = None
        else:
            try:
                base_seed = int(self.seed_var.get())
            except Exception:
                base_seed = 42

        os.makedirs(outdir, exist_ok=True)

        # Disable button, run in thread to keep UI responsive
        self.generate_btn.config(state=tk.DISABLED, text="Generating…")
        self.progress_bar["maximum"] = n_datasets
        self.progress_bar["value"] = 0

        def worker():
            try:
                sampler = DAGSampler.from_dict(
                    self.graph,
                    edge_types=edge_types,
                    noise_types=noise_types,
                    post_process_prob=pp_prob,
                    noise_sigma_range=(sigma_lo, sigma_hi),
                )

                datasets   = []
                all_configs = []
                seeds = (
                    [base_seed + i for i in range(n_datasets)]
                    if base_seed is not None else [None] * n_datasets
                )

                for i, seed in enumerate(seeds):
                    self.progress_var.set(f"Sampling dataset {i+1}/{n_datasets}…")
                    self.window.update_idletasks()
                    ds, cfg = sampler.sample_dataset(
                        n_rows=n_rows, seed=seed, return_config=True)
                    datasets.append(ds)
                    all_configs.append(cfg)
                    self.progress_bar["value"] = i + 1
                    self.window.update_idletasks()

                self.progress_var.set("Writing files…")
                self.window.update_idletasks()
                files_written = []

                # ── DAG graph JSON ─────────────────────────────────────────
                if self.export_graph_var.get():
                    p = os.path.join(outdir, "dag_graph.json")
                    with open(p, "w") as f:
                        json.dump(self.graph, f, indent=2)
                    files_written.append("dag_graph.json")

                # ── SCM configs JSON ───────────────────────────────────────
                if self.export_configs_var.get():
                    serial = []
                    for i, cfg in enumerate(all_configs):
                        entry = {"dataset_id": i}
                        for nid, nc in cfg.items():
                            ec = nc.edge_config
                            ec_dict = None
                            if ec is not None:
                                if ec.kind == "nn":
                                    ec_dict = {
                                        "kind": "nn",
                                        "W": ec.W.tolist(),
                                        "b": ec.b.tolist(),
                                        "activation": ec.activation,
                                    }
                                elif ec.kind == "tree":
                                    ec_dict = {
                                        "kind": "tree",
                                        "thresholds": [list(t) for t in ec.thresholds],
                                    }
                                elif ec.kind == "linear":
                                    ec_dict = {
                                        "kind": "linear",
                                        "weights": ec.weights.tolist(),
                                    }
                            entry[nc.name] = {
                                "noise_dist":  nc.noise_dist,
                                "noise_sigma": nc.noise_sigma,
                                "edge":        ec_dict,
                                "post_process": nc.post_process,
                                "warp_a": nc.warp_a,
                                "warp_b": nc.warp_b,
                                "quantize_k": nc.quantize_k,
                            }
                        serial.append(entry)
                    p = os.path.join(outdir, "scm_configs.json")
                    with open(p, "w") as f:
                        json.dump(serial, f, indent=2)
                    files_written.append("scm_configs.json")

                # ── Separate CSVs ──────────────────────────────────────────
                if self.export_sep_csv_var.get():
                    sub = os.path.join(outdir, "datasets_csv")
                    os.makedirs(sub, exist_ok=True)
                    for i, ds in enumerate(datasets):
                        fname = f"dataset_{i:04d}.csv"
                        if _PANDAS and isinstance(ds, pd.DataFrame):
                            ds.to_csv(os.path.join(sub, fname), index=False)
                        else:
                            import csv
                            cols = list(ds.keys())
                            with open(os.path.join(sub, fname), "w", newline="") as f:
                                w = csv.writer(f)
                                w.writerow(cols)
                                for row in zip(*[ds[c] for c in cols]):
                                    w.writerow(row)
                    files_written.append(f"datasets_csv/  ({n_datasets} files)")

                # ── Combined CSV ───────────────────────────────────────────
                if self.export_combined_var.get():
                    if _PANDAS:
                        frames = []
                        for i, ds in enumerate(datasets):
                            df = ds.copy() if isinstance(ds, pd.DataFrame) else pd.DataFrame(ds)
                            df.insert(0, "dataset_id", i)
                            frames.append(df)
                        combined = pd.concat(frames, ignore_index=True)
                        p = os.path.join(outdir, "all_datasets.csv")
                        combined.to_csv(p, index=False)
                        files_written.append("all_datasets.csv")
                    else:
                        self.progress_var.set("Combined CSV skipped (pandas not available)")

                # ── Excel workbook ─────────────────────────────────────────
                if self.export_excel_var.get():
                    try:
                        import openpyxl
                        if _PANDAS:
                            p = os.path.join(outdir, "all_datasets.xlsx")
                            with pd.ExcelWriter(p, engine="openpyxl") as writer:
                                for i, ds in enumerate(datasets):
                                    df = ds if isinstance(ds, pd.DataFrame) else pd.DataFrame(ds)
                                    df.to_excel(writer, sheet_name=f"dataset_{i:04d}",
                                                index=False)
                            files_written.append("all_datasets.xlsx")
                    except ImportError:
                        self.progress_var.set("Excel skipped — install openpyxl")

                # ── Parquet ────────────────────────────────────────────────
                if self.export_parquet_var.get():
                    try:
                        import pyarrow  # noqa: F401
                        if _PANDAS:
                            sub = os.path.join(outdir, "datasets_parquet")
                            os.makedirs(sub, exist_ok=True)
                            for i, ds in enumerate(datasets):
                                df = ds if isinstance(ds, pd.DataFrame) else pd.DataFrame(ds)
                                df.to_parquet(os.path.join(sub, f"dataset_{i:04d}.parquet"),
                                              index=False)
                            files_written.append(f"datasets_parquet/  ({n_datasets} files)")
                    except ImportError:
                        self.progress_var.set("Parquet skipped — install pyarrow")

                self.progress_var.set(
                    f"Done ✓  {n_datasets} datasets × {n_rows} rows  "
                    f"→  {outdir}")
                self.result_message = (
                    f"Generated {n_datasets} datasets × {n_rows} rows — "
                    f"{len(files_written)} export(s) written")

                self.window.after(0, lambda: messagebox.showinfo(
                    "Generation Complete",
                    f"✓  {n_datasets} datasets  ×  {n_rows} rows\n\n"
                    + "\n".join(f"  • {f}" for f in files_written)
                    + f"\n\nOutput folder:\n{outdir}",
                    parent=self.window))

            except Exception as e:
                import traceback
                self.window.after(0, lambda: messagebox.showerror(
                    "Error", f"Generation failed:\n\n{e}\n\n{traceback.format_exc()}",
                    parent=self.window))
                self.progress_var.set(f"Error: {e}")
            finally:
                self.window.after(0, lambda: self.generate_btn.config(
                    state=tk.NORMAL, text="▶  Generate & Export"))

        threading.Thread(target=worker, daemon=True).start()




# ══════════════════════════════════════════════════════════════════════════════
# Hypothesis Test Dialog
# ══════════════════════════════════════════════════════════════════════════════

class HypothesisDialog:
    """
    Modal dialog for configuring and running the TabPFN hypothesis test,
    then displaying the full results inline without needing a separate window.
    """

    def __init__(self, parent, graph: dict):
        self.graph = graph
        self.result_message = ""
        self._log_lines = []

        self.window = tk.Toplevel(parent)
        self.window.title("Hypothesis Test — DAG vs Real Data")
        self.window.configure(bg=BG_DARK)
        self.window.geometry("780x680")
        self.window.minsize(640, 500)
        self.window.resizable(True, True)
        self.window.grab_set()
        self.window.transient(parent)

        self._build()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        self.window.grid_rowconfigure(0, weight=1)
        self.window.grid_columnconfigure(0, weight=1)

        # Notebook: Config tab | Results tab
        nb = ttk.Notebook(self.window)
        nb.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        self.nb = nb

        self.config_tab  = tk.Frame(nb, bg=BG_DARK)
        self.results_tab = tk.Frame(nb, bg=BG_DARK)
        nb.add(self.config_tab,  text="  ⚙  Configuration  ")
        nb.add(self.results_tab, text="  📊  Results  ")

        self._build_config_tab()
        self._build_results_tab()

        # Footer
        footer = tk.Frame(self.window, bg=BG_PANEL)
        footer.grid(row=1, column=0, sticky="ew")
        tk.Frame(footer, bg=BORDER, height=1).pack(fill=tk.X)

        btn_row = tk.Frame(footer, bg=BG_PANEL)
        btn_row.pack(fill=tk.X, padx=16, pady=10)

        self.run_btn = tk.Button(
            btn_row, text="▶  Run Hypothesis Test",
            font=("Courier", 11, "bold"),
            bg="#6e40c9", fg=TEXT_MAIN, relief=tk.FLAT,
            activebackground="#5a32a3", padx=18, pady=8,
            cursor="hand2", command=self._run)
        self.run_btn.pack(side=tk.LEFT)

        self.save_btn = tk.Button(
            btn_row, text="💾 Save Results",
            font=("Courier", 10),
            bg=BG_CARD, fg=TEXT_DIM, relief=tk.FLAT,
            padx=12, pady=8, cursor="hand2",
            state=tk.DISABLED, command=self._save_results)
        self.save_btn.pack(side=tk.LEFT, padx=8)

        tk.Button(btn_row, text="✕ Close",
                  font=("Courier", 10), bg=BG_CARD, fg=TEXT_DIM,
                  relief=tk.FLAT, padx=12, pady=8, cursor="hand2",
                  command=self.window.destroy).pack(side=tk.RIGHT)

    def _section(self, parent, text):
        tk.Label(parent, text=text, font=("Courier", 10, "bold"),
                 fg=TEXT_DIM, bg=BG_DARK, anchor="w").pack(fill=tk.X, pady=(12, 2))
        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X)

    def _build_config_tab(self):
        # Scrollable
        sc_frame = tk.Frame(self.config_tab, bg=BG_DARK)
        sc_frame.pack(fill=tk.BOTH, expand=True)
        sc_frame.grid_rowconfigure(0, weight=1)
        sc_frame.grid_columnconfigure(0, weight=1)

        vsb = tk.Scrollbar(sc_frame, orient=tk.VERTICAL, bg=BG_CARD, troughcolor=BG_DARK)
        vsb.grid(row=0, column=1, sticky="ns")
        sc = tk.Canvas(sc_frame, bg=BG_DARK, bd=0, highlightthickness=0,
                       yscrollcommand=vsb.set)
        sc.grid(row=0, column=0, sticky="nsew")
        vsb.config(command=sc.yview)
        inner = tk.Frame(sc, bg=BG_DARK)
        wid = sc.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: sc.configure(scrollregion=sc.bbox("all")))
        sc.bind("<Configure>", lambda e: sc.itemconfig(wid, width=e.width))
        sc.bind_all("<MouseWheel>", lambda e: sc.yview_scroll(int(-1*(e.delta/120)), "units"))

        p = tk.Frame(inner, bg=BG_DARK)
        p.pack(fill=tk.BOTH, expand=True, padx=20, pady=12)

        def gspin(parent, row, from_, to, default, inc=1):
            v = tk.StringVar(value=str(default))
            sb = tk.Spinbox(parent, from_=from_, to=to, increment=inc,
                            textvariable=v, font=("Courier", 10),
                            bg=BG_CARD, fg=TEXT_MAIN, buttonbackground=BG_CARD,
                            relief=tk.FLAT, insertbackground=ACCENT, width=12)
            sb._var = v
            sb.grid(row=row, column=1, sticky="ew", pady=3)
            return sb

        def glabel(parent, row, text):
            tk.Label(parent, text=text, font=("Courier", 10), fg=TEXT_MAIN,
                     bg=BG_DARK, anchor="w").grid(
                         row=row, column=0, sticky="w", pady=3, padx=(0, 16))

        # ── DAG source ───────────────────────────────────────────────────
        self._section(p, "DAG MODEL")
        dag_frame = tk.Frame(p, bg=BG_DARK)
        dag_frame.pack(fill=tk.X, pady=4)
        dag_frame.columnconfigure(1, weight=1)

        # Radio: use current canvas graph vs load from file
        self.dag_source_var = tk.StringVar(value="canvas")
        rb_row = tk.Frame(dag_frame, bg=BG_DARK)
        rb_row.grid(row=0, column=0, columnspan=2, sticky="w", pady=3)
        tk.Radiobutton(rb_row, text="Use current graph", variable=self.dag_source_var,
                       value="canvas", font=("Courier", 10), fg=TEXT_MAIN, bg=BG_DARK,
                       selectcolor=BG_CARD, activebackground=BG_DARK,
                       activeforeground=ACCENT,
                       command=self._on_dag_source_changed).pack(side=tk.LEFT, padx=(0, 16))
        tk.Radiobutton(rb_row, text="Load from file", variable=self.dag_source_var,
                       value="file", font=("Courier", 10), fg=TEXT_MAIN, bg=BG_DARK,
                       selectcolor=BG_CARD, activebackground=BG_DARK,
                       activeforeground=ACCENT,
                       command=self._on_dag_source_changed).pack(side=tk.LEFT)

        # File row (shown only when "file" is selected)
        self._dag_file_row = tk.Frame(dag_frame, bg=BG_DARK)
        self._dag_file_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=3)
        self._dag_file_row.columnconfigure(0, weight=1)
        self.dag_path_var = tk.StringVar(value="")
        tk.Entry(self._dag_file_row, textvariable=self.dag_path_var,
                 font=("Courier", 9), bg=BG_CARD, fg=TEXT_MAIN,
                 insertbackground=ACCENT, relief=tk.FLAT).pack(
                     side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(self._dag_file_row, text="Browse", font=("Courier", 9),
                  bg=BG_CARD, fg=TEXT_DIM, relief=tk.FLAT, padx=8, pady=2,
                  cursor="hand2", command=self._browse_dag).pack(side=tk.LEFT, padx=(6, 0))
        self._dag_file_row.grid_remove()   # hidden until "file" is chosen

        # ── Real dataset ──────────────────────────────────────────────────
        self._section(p, "REAL DATASET")
        ds = tk.Frame(p, bg=BG_DARK)
        ds.pack(fill=tk.X, pady=4)
        ds.columnconfigure(1, weight=1)

        glabel(ds, 0, "CSV file")
        file_cell = tk.Frame(ds, bg=BG_DARK)
        file_cell.grid(row=0, column=1, sticky="ew", pady=3)
        self.data_path_var = tk.StringVar(value="")
        tk.Entry(file_cell, textvariable=self.data_path_var, font=("Courier", 9),
                 bg=BG_CARD, fg=TEXT_MAIN, insertbackground=ACCENT,
                 relief=tk.FLAT).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(file_cell, text="Browse", font=("Courier", 9),
                  bg=BG_CARD, fg=TEXT_DIM, relief=tk.FLAT, padx=8, pady=2,
                  cursor="hand2",
                  command=self._browse_data).pack(side=tk.LEFT, padx=(6, 0))

        glabel(ds, 1, "Target column")
        self.target_var = tk.StringVar(value="")
        tk.Entry(ds, textvariable=self.target_var, font=("Courier", 10),
                 bg=BG_CARD, fg=TEXT_MAIN, insertbackground=ACCENT,
                 relief=tk.FLAT, width=20).grid(row=1, column=1, sticky="w", pady=3)

        glabel(ds, 2, "Task")
        self.task_var = tk.StringVar(value="classification")
        task_frame = tk.Frame(ds, bg=BG_DARK)
        task_frame.grid(row=2, column=1, sticky="w", pady=3)
        for val, label in [("classification", "Classification"), ("regression", "Regression")]:
            tk.Radiobutton(task_frame, text=label, variable=self.task_var, value=val,
                           font=("Courier", 10), fg=TEXT_MAIN, bg=BG_DARK,
                           selectcolor=BG_CARD, activebackground=BG_DARK,
                           activeforeground=ACCENT).pack(side=tk.LEFT, padx=(0, 16))

        # ── Synthetic data ────────────────────────────────────────────────
        self._section(p, "SYNTHETIC DATA  (from DAG)")
        sd = tk.Frame(p, bg=BG_DARK)
        sd.pack(fill=tk.X, pady=4)
        sd.columnconfigure(1, weight=1)

        glabel(sd, 0, "Number of datasets")
        self.n_datasets_sb = gspin(sd, 0, 1, 200, 20)

        glabel(sd, 1, "Rows per dataset")
        self.n_rows_sb = gspin(sd, 1, 50, 100000, 500, inc=50)

        glabel(sd, 2, "Base seed")
        self.seed_sb = gspin(sd, 2, 0, 99999, 42)

        # ── Fine-tuning ───────────────────────────────────────────────────
        self._section(p, "TABPFN FINE-TUNING")
        ft = tk.Frame(p, bg=BG_DARK)
        ft.pack(fill=tk.X, pady=4)
        ft.columnconfigure(1, weight=1)

        glabel(ft, 0, "Epochs")
        self.epochs_sb = gspin(ft, 0, 1, 500, 30)

        glabel(ft, 1, "Learning rate")
        self.lr_var = tk.StringVar(value="1e-5")
        tk.Entry(ft, textvariable=self.lr_var, font=("Courier", 10),
                 bg=BG_CARD, fg=TEXT_MAIN, insertbackground=ACCENT,
                 relief=tk.FLAT, width=12).grid(row=1, column=1, sticky="w", pady=3)

        glabel(ft, 2, "Device")
        self.device_var = tk.StringVar(value="auto")
        dev_frame = tk.Frame(ft, bg=BG_DARK)
        dev_frame.grid(row=2, column=1, sticky="w", pady=3)
        for val in ["auto", "cuda", "cpu"]:
            tk.Radiobutton(dev_frame, text=val, variable=self.device_var, value=val,
                           font=("Courier", 10), fg=TEXT_MAIN, bg=BG_DARK,
                           selectcolor=BG_CARD, activebackground=BG_DARK,
                           activeforeground=ACCENT).pack(side=tk.LEFT, padx=(0, 12))

        # ── Evaluation ────────────────────────────────────────────────────
        self._section(p, "EVALUATION")
        ev = tk.Frame(p, bg=BG_DARK)
        ev.pack(fill=tk.X, pady=4)
        ev.columnconfigure(1, weight=1)

        glabel(ev, 0, "CV folds")
        self.cv_folds_sb = gspin(ev, 0, 2, 20, 5)

        glabel(ev, 1, "Significance α")
        self.alpha_var = tk.StringVar(value="0.05")
        tk.Entry(ev, textvariable=self.alpha_var, font=("Courier", 10),
                 bg=BG_CARD, fg=TEXT_MAIN, insertbackground=ACCENT,
                 relief=tk.FLAT, width=8).grid(row=1, column=1, sticky="w", pady=3)

        glabel(ev, 2, "Permutations")
        self.perm_sb = gspin(ev, 2, 100, 10000, 1000, inc=100)

    def _build_results_tab(self):
        self.results_tab.grid_rowconfigure(0, weight=1)
        self.results_tab.grid_columnconfigure(0, weight=1)

        # Log / output text widget
        log_frame = tk.Frame(self.results_tab, bg=BG_DARK)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.log_text = tk.Text(
            log_frame, font=("Courier", 10), bg=BG_PANEL, fg=TEXT_MAIN,
            insertbackground=ACCENT, relief=tk.FLAT, wrap=tk.WORD,
            state=tk.DISABLED, padx=12, pady=10)
        vsb2 = tk.Scrollbar(log_frame, command=self.log_text.yview,
                             bg=BG_CARD, troughcolor=BG_DARK)
        self.log_text.configure(yscrollcommand=vsb2.set)
        vsb2.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Tag styles for coloured output
        self.log_text.tag_configure("header",  foreground=ACCENT,  font=("Courier", 11, "bold"))
        self.log_text.tag_configure("good",    foreground=ACCENT2, font=("Courier", 10, "bold"))
        self.log_text.tag_configure("bad",     foreground=ACCENT3, font=("Courier", 10, "bold"))
        self.log_text.tag_configure("warn",    foreground="#ffa657")
        self.log_text.tag_configure("dim",     foreground=TEXT_DIM)
        self.log_text.tag_configure("normal",  foreground=TEXT_MAIN)
        self.log_text.tag_configure("value",   foreground=ACCENT4, font=("Courier", 10, "bold"))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _browse_dag(self):
        path = filedialog.askopenfilename(
            title="Select DAG model JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.dag_path_var.set(path)

    def _on_dag_source_changed(self):
        if self.dag_source_var.get() == "file":
            self._dag_file_row.grid()
        else:
            self._dag_file_row.grid_remove()

    def _browse_data(self):
        path = filedialog.askopenfilename(
            title="Select real dataset CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.data_path_var.set(path)
            # Auto-detect columns and suggest target
            try:
                import pandas as pd
                cols = pd.read_csv(path, nrows=0).columns.tolist()
                if cols:
                    self.target_var.set(cols[-1])
            except Exception:
                pass

    def _gint(self, sb, default):
        try: return int(float(sb._var.get()))
        except: return default

    def _gfloat(self, var_or_sb, default):
        try:
            v = var_or_sb._var.get() if hasattr(var_or_sb, "_var") else var_or_sb.get()
            return float(v)
        except: return default

    def _log(self, text, tag="normal"):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.window.update_idletasks()

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _validate(self):
        if not self.data_path_var.get():
            messagebox.showwarning("Missing data", "Select a real dataset CSV.", parent=self.window)
            return False
        if not self.target_var.get().strip():
            messagebox.showwarning("Missing target", "Enter the target column name.", parent=self.window)
            return False
        return True

    def _run(self):
        if not self._validate():
            return

        self.run_btn.config(state=tk.DISABLED, text="Running…")
        self.save_btn.config(state=tk.DISABLED)
        self._clear_log()
        self.nb.select(1)   # switch to Results tab
        self._result = None

        def worker():
            try:
                import pandas as pd
                import tempfile, json as _json

                # ── Load real data ─────────────────────────────────────────
                self._log("═" * 56, "header")
                self._log("  DAG Hypothesis Test", "header")
                self._log("═" * 56, "header")

                data_path  = self.data_path_var.get()
                target_col = self.target_var.get().strip()
                task       = self.task_var.get()

                self._log(f"\n  Loading: {data_path}", "dim")
                df = pd.read_csv(data_path)
                if target_col not in df.columns:
                    raise ValueError(f"Column '{target_col}' not found.\n"
                                     f"Available: {list(df.columns)}")

                y = df[target_col].values
                # Drop non-numeric columns but keep NaNs — TabPFN handles them natively
                X_df = df.drop(columns=[target_col]).select_dtypes(include="number")
                X = X_df.to_numpy()   # preserves NaN without forcing float conversion

                nan_count = int(pd.isna(X).sum()) + int(pd.isna(y).sum())
                self._log(f"  Rows: {X.shape[0]}   Features: {X.shape[1]}   Target: {target_col}", "dim")
                if nan_count:
                    self._log(f"  NaN values: {nan_count} — TabPFN will handle these natively", "warn")
                self._log(f"  Task: {task}", "dim")

                # ── Resolve DAG: current canvas graph or file ─────────────
                if self.dag_source_var.get() == "file":
                    dag_file_path = self.dag_path_var.get().strip()
                    if not dag_file_path:
                        raise ValueError("No DAG file selected. Choose a file or switch to 'Use current graph'.")
                    with open(dag_file_path) as _f:
                        dag_graph = _json.load(_f)
                    self._log(f"  DAG: {dag_file_path}", "dim")
                else:
                    dag_graph = self.graph
                    self._log(f"  DAG: current canvas graph ({len(dag_graph['nodes'])} nodes)", "dim")

                # ── Save graph to temp file so HypothesisTester can load it ──
                tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
                _json.dump(dag_graph, tmp)
                tmp.close()

                # ── Configure tester, redirect logging to our text widget ──
                n_datasets  = self._gint(self.n_datasets_sb, 20)
                n_rows      = self._gint(self.n_rows_sb, 500)
                seed        = self._gint(self.seed_sb, 42)
                epochs      = self._gint(self.epochs_sb, 30)
                lr          = self._gfloat(self.lr_var, 1e-5)
                device      = self.device_var.get()
                cv_folds    = self._gint(self.cv_folds_sb, 5)
                alpha       = self._gfloat(self.alpha_var, 0.05)
                n_perm      = self._gint(self.perm_sb, 1000)

                self._log(f"\n  Synth datasets: {n_datasets} × {n_rows} rows", "dim")
                self._log(f"  Fine-tuning: {epochs} epochs  lr={lr}  device={device}", "dim")
                self._log(f"  CV folds: {cv_folds}   α={alpha}   permutations={n_perm}\n", "dim")

                tester = HypothesisTester(
                    task=task, device=device, epochs=epochs,
                    learning_rate=lr, n_cv_folds=cv_folds,
                    alpha=alpha, n_permutations=n_perm, verbose=False,
                )

                # Monkey-patch tester._log to route to our text widget
                def ui_log(msg):
                    tag = "normal"
                    if "baseline" in msg.lower(): tag = "dim"
                    elif "fine-tun" in msg.lower(): tag = "dim"
                    elif "fold" in msg.lower(): tag = "dim"
                    self._log(msg, tag)
                tester._log = ui_log

                # ── Run ────────────────────────────────────────────────────
                result = tester.run(
                    dag_path=tmp.name, X=X, y=y,
                    n_synth_datasets=n_datasets,
                    n_synth_rows=n_rows,
                    synth_seed=seed,
                )
                self._result = result

                # ── Render results ─────────────────────────────────────────
                self._log("\n" + "═" * 56, "header")
                self._log("  RESULTS", "header")
                self._log("═" * 56, "header")
                self._log(f"  Metric          : {result.metric}", "normal")
                self._log(f"  Baseline        : {result.baseline_mean:.4f} ± {result.baseline_std:.4f}", "normal")
                self._log(f"  Fine-tuned      : {result.finetuned_mean:.4f} ± {result.finetuned_std:.4f}", "value")
                sign = "+" if result.delta_mean >= 0 else ""
                self._log(f"  Δ (improvement) : {sign}{result.delta_mean:.4f}", "value")
                self._log(f"  p-value         : {result.p_value:.4f}  (α={result.alpha})", "normal")

                if result.significant and result.delta_mean > 0:
                    self._log("\n  ✓  SIGNIFICANT IMPROVEMENT", "good")
                    self._log("  Fine-tuning on the DAG data improved performance.", "good")
                    self._log("  The DAG is a plausible approximation of the true DGP.", "good")
                elif result.delta_mean > 0:
                    self._log("\n  ~  Improvement detected but NOT significant yet.", "warn")
                    self._log("  Try more synthetic datasets or revise the DAG.", "warn")
                else:
                    self._log("\n  ✗  No improvement detected.", "bad")
                    self._log("  The DAG may not reflect the true DGP.", "bad")
                    self._log("  Consider revising the graph structure.", "bad")

                self._log(f"\n  Elapsed: {result.elapsed_sec:.1f}s", "dim")
                self._log("═" * 56, "header")

                self.result_message = (
                    f"Hypothesis test done — p={result.p_value:.4f}  "
                    f"({'significant ✓' if result.significant else 'not significant'})"
                )
                self.window.after(0, lambda: self.save_btn.config(state=tk.NORMAL))

            except Exception as e:
                import traceback
                self._log(f"\n  ERROR: {e}", "bad")
                self._log(traceback.format_exc(), "dim")

            finally:
                self.window.after(0, lambda: self.run_btn.config(
                    state=tk.NORMAL, text="▶  Run Hypothesis Test"))

        threading.Thread(target=worker, daemon=True).start()

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save_results(self):
        if self._result is None:
            return
        path = filedialog.asksaveasfilename(
            title="Save hypothesis test results",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            initialfile="hypothesis_result.json")
        if path:
            tester = HypothesisTester(task=self._result.task, verbose=False)
            tester.save_result(self._result, path)
            # Also offer plot
            plot_path = path.replace(".json", "_plot.png")
            try:
                tester.plot(self._result, save_path=plot_path)
                self._log(f"\n  Plot saved → {plot_path}", "dim")
            except Exception:
                pass
            self._log(f"  Results saved → {path}", "dim")


class DAGBuilderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DeepDive — Causal Graph Editor")
        self.root.configure(bg=BG_DARK)
        self.root.geometry("1200x780")
        self.root.minsize(900, 600)

        self.nodes: list[Node] = []
        self.edges: list[tuple[Node, Node]] = []  # (from, to)

        self.selected_node: Node | None = None
        self.drag_start: tuple | None = None
        self.edge_source: Node | None = None
        self.mode = "select"   # "select" | "add_node" | "add_edge" | "delete"
        self.temp_edge_line = None

        self._build_ui()
        self._bind_events()
        self._refresh_canvas()

    # ─── UI Construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        # Top toolbar
        toolbar = tk.Frame(self.root, bg=BG_PANEL, height=52, bd=0)
        toolbar.pack(fill=tk.X, side=tk.TOP)
        toolbar.pack_propagate(False)

        tk.Label(toolbar, text="⬡  DeepDive", font=("Courier", 14, "bold"),
                 fg=ACCENT, bg=BG_PANEL, padx=16).pack(side=tk.LEFT, pady=10)

        sep = tk.Frame(toolbar, bg=BORDER, width=1)
        sep.pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=8)

        self.mode_buttons = {}
        modes = [
            ("✦ Select",   "select",    ACCENT),
            ("＋ Add Node", "add_node",  ACCENT2),
            ("→ Add Edge", "add_edge",  ACCENT4),
            ("✕ Delete",   "delete",    ACCENT3),
        ]
        for label, mode, color in modes:
            btn = tk.Button(toolbar, text=label, font=("Courier", 10, "bold"),
                            bg=BG_CARD, fg=TEXT_DIM, relief=tk.FLAT,
                            activebackground=BG_CARD, activeforeground=color,
                            padx=12, pady=6, cursor="hand2",
                            command=lambda m=mode: self._set_mode(m))
            btn.pack(side=tk.LEFT, padx=4, pady=8)
            self.mode_buttons[mode] = (btn, color)

        sep2 = tk.Frame(toolbar, bg=BORDER, width=1)
        sep2.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)

        tk.Button(toolbar, text="⟳ Clear", font=("Courier", 10),
                  bg=BG_CARD, fg=TEXT_DIM, relief=tk.FLAT,
                  activebackground=BG_CARD, activeforeground=ACCENT3,
                  padx=10, pady=6, cursor="hand2",
                  command=self._clear_graph).pack(side=tk.LEFT, padx=4, pady=8)

        tk.Button(toolbar, text="🧪 Hypothesis Test", font=("Courier", 10, "bold"),
                  bg="#6e40c9", fg=TEXT_MAIN, relief=tk.FLAT,
                  activebackground="#5a32a3", activeforeground=TEXT_MAIN,
                  padx=14, pady=6, cursor="hand2",
                  command=self._open_hypothesis_dialog).pack(side=tk.RIGHT, padx=4, pady=8)

        tk.Button(toolbar, text="⚙ Generate Data", font=("Courier", 10, "bold"),
                  bg=NODE_FILL, fg=TEXT_MAIN, relief=tk.FLAT,
                  activebackground="#2563eb", activeforeground=TEXT_MAIN,
                  padx=14, pady=6, cursor="hand2",
                  command=self._open_generate_dialog).pack(side=tk.RIGHT, padx=4, pady=8)

        tk.Button(toolbar, text="💾 Save Graph", font=("Courier", 10),
                  bg=BG_CARD, fg=TEXT_DIM, relief=tk.FLAT,
                  activebackground=BG_CARD, activeforeground=ACCENT2,
                  padx=10, pady=6, cursor="hand2",
                  command=self._export_model).pack(side=tk.RIGHT, padx=4, pady=8)

        tk.Button(toolbar, text="📂 Load", font=("Courier", 10),
                  bg=BG_CARD, fg=TEXT_DIM, relief=tk.FLAT,
                  activebackground=BG_CARD, activeforeground=ACCENT,
                  padx=10, pady=6, cursor="hand2",
                  command=self._load_graph).pack(side=tk.RIGHT, padx=4, pady=8)

        # Main area: canvas + side panel
        main = tk.Frame(self.root, bg=BG_DARK)
        main.pack(fill=tk.BOTH, expand=True)

        # Canvas
        canvas_frame = tk.Frame(main, bg=BORDER, bd=1)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 4), pady=8)

        self.canvas = tk.Canvas(canvas_frame, bg=BG_DARK, bd=0, highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Side panel
        side = tk.Frame(main, bg=BG_PANEL, width=240)
        side.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 8), pady=8)
        side.pack_propagate(False)

        self._build_side_panel(side)

        # Status bar
        self.status_var = tk.StringVar(value="Ready — select a mode above to begin")
        statusbar = tk.Frame(self.root, bg=BG_PANEL, height=28)
        statusbar.pack(fill=tk.X, side=tk.BOTTOM)
        statusbar.pack_propagate(False)
        tk.Label(statusbar, textvariable=self.status_var,
                 font=("Courier", 9), fg=TEXT_DIM, bg=BG_PANEL,
                 anchor="w", padx=12).pack(fill=tk.X)

        self._set_mode("select")

    def _build_side_panel(self, parent):
        tk.Label(parent, text="GRAPH INFO", font=("Courier", 10, "bold"),
                 fg=TEXT_DIM, bg=BG_PANEL, padx=12, pady=10,
                 anchor="w").pack(fill=tk.X)

        sep = tk.Frame(parent, bg=BORDER, height=1)
        sep.pack(fill=tk.X, padx=8)

        info_frame = tk.Frame(parent, bg=BG_PANEL)
        info_frame.pack(fill=tk.X, padx=12, pady=8)

        self.info_nodes = tk.StringVar(value="Nodes: 0")
        self.info_edges = tk.StringVar(value="Edges: 0")
        self.info_roots = tk.StringVar(value="Root nodes: 0")
        self.info_leaves = tk.StringVar(value="Leaf nodes: 0")
        self.info_acyclic = tk.StringVar(value="Acyclic: ✓")

        for var in [self.info_nodes, self.info_edges, self.info_roots,
                    self.info_leaves, self.info_acyclic]:
            tk.Label(info_frame, textvariable=var, font=("Courier", 10),
                     fg=TEXT_MAIN, bg=BG_PANEL, anchor="w").pack(fill=tk.X, pady=2)

        sep2 = tk.Frame(parent, bg=BORDER, height=1)
        sep2.pack(fill=tk.X, padx=8, pady=4)

        tk.Label(parent, text="SELECTED NODE", font=("Courier", 10, "bold"),
                 fg=TEXT_DIM, bg=BG_PANEL, padx=12, anchor="w").pack(fill=tk.X)

        sel_frame = tk.Frame(parent, bg=BG_PANEL)
        sel_frame.pack(fill=tk.X, padx=12, pady=6)

        self.sel_name_var = tk.StringVar(value="—")
        tk.Label(sel_frame, text="Name:", font=("Courier", 9),
                 fg=TEXT_DIM, bg=BG_PANEL, anchor="w").pack(fill=tk.X)

        name_row = tk.Frame(sel_frame, bg=BG_PANEL)
        name_row.pack(fill=tk.X)
        self.sel_name_entry = tk.Entry(name_row, textvariable=self.sel_name_var,
                                       font=("Courier", 11, "bold"),
                                       bg=BG_CARD, fg=TEXT_MAIN,
                                       insertbackground=ACCENT,
                                       relief=tk.FLAT, bd=4)
        self.sel_name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(name_row, text="✓", font=("Courier", 10),
                  bg=NODE_FILL, fg=TEXT_MAIN, relief=tk.FLAT,
                  padx=6, cursor="hand2",
                  command=self._rename_selected).pack(side=tk.LEFT, padx=(4, 0))

        self.sel_role_var = tk.StringVar(value="")
        tk.Label(sel_frame, textvariable=self.sel_role_var,
                 font=("Courier", 9), fg=TEXT_DIM, bg=BG_PANEL,
                 anchor="w").pack(fill=tk.X, pady=(4, 0))

        # Variable type selector
        tk.Label(sel_frame, text="Variable type:", font=("Courier", 9),
                 fg=TEXT_DIM, bg=BG_PANEL, anchor="w").pack(fill=tk.X, pady=(6, 2))
        self.sel_vartype_var = tk.StringVar(value="continuous")
        self._vartype_menu = ttk.Combobox(
            sel_frame, textvariable=self.sel_vartype_var,
            values=VAR_TYPE_NAMES, state="readonly",
            font=("Courier", 9), width=14)
        self._vartype_menu.pack(fill=tk.X)
        self._vartype_menu.bind("<<ComboboxSelected>>", self._on_vartype_changed)

        # Ordinal levels spinner (shown only for ordinal)
        self._ordinal_frame = tk.Frame(sel_frame, bg=BG_PANEL)
        self._ordinal_frame.pack(fill=tk.X, pady=(4, 0))
        tk.Label(self._ordinal_frame, text="Levels (k):", font=("Courier", 9),
                 fg=TEXT_DIM, bg=BG_PANEL).pack(side=tk.LEFT)
        self.sel_levels_var = tk.StringVar(value="5")
        tk.Spinbox(self._ordinal_frame, from_=2, to=20,
                   textvariable=self.sel_levels_var,
                   font=("Courier", 9), bg=BG_CARD, fg=TEXT_MAIN,
                   buttonbackground=BG_CARD, relief=tk.FLAT, width=5,
                   command=self._on_levels_changed).pack(side=tk.LEFT, padx=(6, 0))
        self._ordinal_frame.pack_forget()  # hidden by default

        self.sel_parents_var = tk.StringVar(value="")
        tk.Label(sel_frame, textvariable=self.sel_parents_var,
                 font=("Courier", 9), fg=TEXT_DIM, bg=BG_PANEL,
                 anchor="w", wraplength=200).pack(fill=tk.X, pady=(6, 0))

        sep3 = tk.Frame(parent, bg=BORDER, height=1)
        sep3.pack(fill=tk.X, padx=8, pady=8)

        tk.Label(parent, text="HOW TO USE", font=("Courier", 10, "bold"),
                 fg=TEXT_DIM, bg=BG_PANEL, padx=12, anchor="w").pack(fill=tk.X)

        # Variable type legend
        tk.Label(parent, text="VAR TYPE LEGEND", font=("Courier", 10, "bold"),
                 fg=TEXT_DIM, bg=BG_PANEL, padx=12, anchor="w").pack(fill=tk.X, pady=(4,2))
        legend_frame = tk.Frame(parent, bg=BG_PANEL)
        legend_frame.pack(fill=tk.X, padx=12)
        for vt_name, vt_info in VAR_TYPES.items():
            row = tk.Frame(legend_frame, bg=BG_PANEL)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"  {vt_info['label']}", font=("Courier", 9, "bold"),
                     fg=vt_info["color"], bg=BG_PANEL, width=5,
                     anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=vt_name, font=("Courier", 9),
                     fg=TEXT_DIM, bg=BG_PANEL, anchor="w").pack(side=tk.LEFT)

        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X, padx=8, pady=6)
        tk.Label(parent, text="HOW TO USE", font=("Courier", 10, "bold"),
                 fg=TEXT_DIM, bg=BG_PANEL, padx=12, anchor="w").pack(fill=tk.X)

        help_text = (
            "✦ Select   drag nodes\n"
            "＋ Add Node   click canvas\n"
            "→ Add Edge   click source\n"
            "             then target\n"
            "✕ Delete   click to remove\n\n"
            "Dbl-click node to rename\n"
            "Select node to set type\n"
            "Ring color = var type\n"
            "R/L = root / leaf"
        )
        tk.Label(parent, text=help_text, font=("Courier", 9),
                 fg=TEXT_DIM, bg=BG_PANEL, padx=12, pady=4,
                 justify=tk.LEFT, anchor="nw").pack(fill=tk.X)

    # ─── Event Binding ────────────────────────────────────────────────────────

    def _bind_events(self):
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Configure>", lambda e: self._refresh_canvas())
        self.root.bind("<Escape>", lambda e: self._set_mode("select"))
        self.root.bind("<Delete>", lambda e: self._delete_selected())
        self.root.bind("<BackSpace>", lambda e: self._delete_selected())

    # ─── Mode Management ──────────────────────────────────────────────────────

    def _set_mode(self, mode):
        self.mode = mode
        self.edge_source = None
        if self.temp_edge_line:
            self.canvas.delete(self.temp_edge_line)
            self.temp_edge_line = None

        cursors = {"select": "fleur", "add_node": "plus",
                   "add_edge": "crosshair", "delete": "X_cursor"}
        self.canvas.config(cursor=cursors.get(mode, "arrow"))

        status = {
            "select":   "Select mode — click to select, drag to move nodes",
            "add_node": "Add Node mode — click anywhere on canvas to place a node",
            "add_edge": "Add Edge mode — click a source node, then a target node",
            "delete":   "Delete mode — click a node or edge to remove it",
        }
        self.status_var.set(status.get(mode, ""))

        for m, (btn, color) in self.mode_buttons.items():
            if m == mode:
                btn.config(bg=BG_DARK, fg=color, relief=tk.FLAT)
            else:
                btn.config(bg=BG_CARD, fg=TEXT_DIM, relief=tk.FLAT)

        self._deselect_all()
        self._refresh_canvas()

    # ─── Canvas Interactions ─────────────────────────────────────────────────

    def _on_click(self, event):
        x, y = event.x, event.y
        hit = self._node_at(x, y)

        if self.mode == "select":
            self._deselect_all()
            if hit:
                hit.selected = True
                self.selected_node = hit
                self.drag_start = (x - hit.x, y - hit.y)
                self._update_side_panel(hit)
            else:
                self.selected_node = None
                self._update_side_panel(None)
            self._refresh_canvas()

        elif self.mode == "add_node":
            if not hit:
                node = Node(x, y)
                self.nodes.append(node)
                self._refresh_canvas()
                self._update_info()
                self.status_var.set(f"Added node '{node.name}' — double-click to rename")

        elif self.mode == "add_edge":
            if hit:
                if self.edge_source is None:
                    self.edge_source = hit
                    hit.selected = True
                    self.status_var.set(f"Edge source: '{hit.name}' — now click the target node")
                    self._refresh_canvas()
                else:
                    if hit is not self.edge_source:
                        self._try_add_edge(self.edge_source, hit)
                    self.edge_source = None
                    if self.temp_edge_line:
                        self.canvas.delete(self.temp_edge_line)
                        self.temp_edge_line = None
                    self._deselect_all()
                    self._refresh_canvas()

        elif self.mode == "delete":
            if hit:
                self._delete_node(hit)
            else:
                edge = self._edge_near(x, y)
                if edge:
                    self.edges.remove(edge)
                    self._refresh_canvas()
                    self._update_info()
                    self.status_var.set("Edge deleted")

    def _on_drag(self, event):
        if self.mode == "select" and self.selected_node and self.drag_start:
            dx, dy = self.drag_start
            self.selected_node.x = event.x - dx
            self.selected_node.y = event.y - dy
            self._refresh_canvas()

    def _on_release(self, event):
        self.drag_start = None

    def _on_double_click(self, event):
        hit = self._node_at(event.x, event.y)
        if hit:
            self._rename_node_dialog(hit)

    def _on_motion(self, event):
        if self.mode == "add_edge" and self.edge_source:
            if self.temp_edge_line:
                self.canvas.delete(self.temp_edge_line)
            self.temp_edge_line = self.canvas.create_line(
                self.edge_source.x, self.edge_source.y,
                event.x, event.y,
                fill=ACCENT4, width=2, dash=(6, 4),
                arrow=tk.LAST, arrowshape=(12, 15, 5),
                tags="temp")

    # ─── Graph Logic ──────────────────────────────────────────────────────────

    def _node_at(self, x, y):
        for node in reversed(self.nodes):
            if node.contains(x, y):
                return node
        return None

    def _edge_near(self, x, y, threshold=8):
        for edge in self.edges:
            a, b = edge
            # Point-to-segment distance
            dx, dy = b.x - a.x, b.y - a.y
            if dx == dy == 0:
                continue
            t = max(0, min(1, ((x - a.x)*dx + (y - a.y)*dy) / (dx*dx + dy*dy)))
            px, py = a.x + t*dx, a.y + t*dy
            if math.hypot(x - px, y - py) < threshold:
                return edge
        return None

    def _try_add_edge(self, src, tgt):
        # Prevent duplicate edges
        if (src, tgt) in self.edges:
            self.status_var.set("Edge already exists")
            return
        # Prevent self-loops
        if src is tgt:
            self.status_var.set("Cannot connect a node to itself")
            return
        # Check acyclicity
        self.edges.append((src, tgt))
        if not self._is_acyclic():
            self.edges.pop()
            messagebox.showwarning("Cycle Detected",
                                   f"Adding {src.name} → {tgt.name} would create a cycle.\n"
                                   "This must remain a DAG.")
            self.status_var.set("Edge rejected — would create a cycle")
        else:
            self.status_var.set(f"Edge added: {src.name} → {tgt.name}")
            self._refresh_canvas()
            self._update_info()

    def _is_acyclic(self):
        """Kahn's algorithm for cycle detection."""
        children = {n: [] for n in self.nodes}
        in_deg = {n: 0 for n in self.nodes}
        for a, b in self.edges:
            children[a].append(b)
            in_deg[b] += 1
        queue = [n for n in self.nodes if in_deg[n] == 0]
        visited = 0
        while queue:
            n = queue.pop()
            visited += 1
            for c in children[n]:
                in_deg[c] -= 1
                if in_deg[c] == 0:
                    queue.append(c)
        return visited == len(self.nodes)

    def _get_roots(self):
        """Nodes with no incoming edges."""
        targets = {b for _, b in self.edges}
        return [n for n in self.nodes if n not in targets]

    def _get_leaves(self):
        """Nodes with no outgoing edges."""
        sources = {a for a, _ in self.edges}
        return [n for n in self.nodes if n not in sources]

    def _delete_node(self, node):
        self.edges = [(a, b) for a, b in self.edges if a is not node and b is not node]
        self.nodes.remove(node)
        if self.selected_node is node:
            self.selected_node = None
            self._update_side_panel(None)
        self._refresh_canvas()
        self._update_info()
        self.status_var.set(f"Deleted node '{node.name}'")

    def _delete_selected(self):
        if self.selected_node:
            self._delete_node(self.selected_node)

    def _deselect_all(self):
        for n in self.nodes:
            n.selected = False
        self.selected_node = None

    # ─── Rendering ────────────────────────────────────────────────────────────

    def _refresh_canvas(self):
        self.canvas.delete("all")
        self._draw_grid()
        self._draw_edges()
        self._draw_nodes()

    def _draw_grid(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        step = 40
        for x in range(0, w, step):
            self.canvas.create_line(x, 0, x, h, fill="#1a1f26", width=1)
        for y in range(0, h, step):
            self.canvas.create_line(0, y, w, y, fill="#1a1f26", width=1)

    def _draw_edges(self):
        for a, b in self.edges:
            # Direction vector
            dx, dy = b.x - a.x, b.y - a.y
            dist = math.hypot(dx, dy) or 1
            ux, uy = dx / dist, dy / dist

            x1 = a.x + ux * a.radius
            y1 = a.y + uy * a.radius
            x2 = b.x - ux * b.radius - ux * 6
            y2 = b.y - uy * b.radius - uy * 6

            self.canvas.create_line(
                x1, y1, x2, y2,
                fill=EDGE_COLOR, width=2,
                arrow=tk.LAST, arrowshape=(14, 17, 6),
                smooth=False, tags="edge"
            )

    def _draw_nodes(self):
        roots = set(self._get_roots())
        leaves = set(self._get_leaves())
        both = roots & leaves

        for node in self.nodes:
            # Fill color by graph role
            if node in both:
                fill = "#2d2a4a"
            elif node in roots:
                fill = "#3d1a0a"
            elif node in leaves:
                fill = "#0a2a1a"
            else:
                fill = "#0a1a2f"

            # Ring color by variable type
            vt = VAR_TYPES.get(node.var_type, VAR_TYPES["continuous"])
            ring = vt["color"]

            r = node.radius
            x, y = node.x, node.y

            # Shadow
            self.canvas.create_oval(x-r+4, y-r+4, x+r+4, y+r+4,
                                    fill="#0a0e14", outline="")
            # Main circle
            self.canvas.create_oval(x-r, y-r, x+r, y+r,
                                    fill=fill, outline=ring,
                                    width=3 if node.selected else 2)

            # Selection highlight
            if node.selected:
                self.canvas.create_oval(x-r-5, y-r-5, x+r+5, y+r+5,
                                        outline=SELECTED, width=2, dash=(4, 3))

            # Top-right: graph role badge (R/L/·/?)
            if node in both:
                role = "?"
            elif node in roots:
                role = "R"
            elif node in leaves:
                role = "L"
            else:
                role = "·"
            self.canvas.create_text(x + r - 5, y - r + 7, text=role,
                                    font=("Courier", 7, "bold"), fill=ring)

            # Bottom: variable type label
            type_label = vt["label"]
            self.canvas.create_text(x, y + r - 8, text=type_label,
                                    font=("Courier", 7), fill=ring)

            # Name (centre)
            self.canvas.create_text(x, y - 4,
                                    text=node.name,
                                    font=("Courier", 11, "bold"),
                                    fill=TEXT_MAIN)

    # ─── Side Panel Updates ───────────────────────────────────────────────────

    def _update_info(self):
        roots = self._get_roots()
        leaves = self._get_leaves()
        acyclic = self._is_acyclic()
        self.info_nodes.set(f"Nodes: {len(self.nodes)}")
        self.info_edges.set(f"Edges: {len(self.edges)}")
        self.info_roots.set(f"Root nodes: {len(roots)}  ({', '.join(n.name for n in roots) or '—'})")
        self.info_leaves.set(f"Leaf nodes: {len(leaves)}  ({', '.join(n.name for n in leaves) or '—'})")
        self.info_acyclic.set(f"Acyclic: {'✓ yes' if acyclic else '✗ NO — fix graph!'}")

    def _on_vartype_changed(self, event=None):
        if not self.selected_node:
            return
        vt = self.sel_vartype_var.get()
        self.selected_node.var_type = vt
        # Show ordinal levels spinner only for ordinal/categorical
        if vt in ("ordinal", "categorical"):
            self._ordinal_frame.pack(fill=tk.X, pady=(4, 0))
        else:
            self._ordinal_frame.pack_forget()
        self._refresh_canvas()

    def _on_levels_changed(self):
        if self.selected_node:
            try:
                self.selected_node.levels = int(self.sel_levels_var.get())
            except ValueError:
                pass

    def _update_side_panel(self, node):
        if node is None:
            self.sel_name_var.set("—")
            self.sel_role_var.set("")
            self.sel_vartype_var.set("continuous")
            self.sel_parents_var.set("")
            self._vartype_menu.config(state="disabled")
            self._ordinal_frame.pack_forget()
        else:
            self.sel_name_var.set(node.name)
            self._vartype_menu.config(state="readonly")
            roots = set(self._get_roots())
            leaves = set(self._get_leaves())
            if node in roots and node in leaves:
                role = "standalone (isolated)"
            elif node in roots:
                role = "root (exogenous)"
            elif node in leaves:
                role = "leaf (observable)"
            else:
                role = "intermediate"
            self.sel_role_var.set(f"Role: {role}")

            self.sel_vartype_var.set(node.var_type)
            # Show ordinal frame if applicable
            if node.var_type in ("ordinal", "categorical"):
                levels = getattr(node, "levels", 5)
                self.sel_levels_var.set(str(levels))
                self._ordinal_frame.pack(fill=tk.X, pady=(4, 0))
            else:
                self._ordinal_frame.pack_forget()

            parents = [a.name for a, b in self.edges if b is node]
            children = [b.name for a, b in self.edges if a is node]
            lines = []
            if parents:
                lines.append(f"Parents: {', '.join(parents)}")
            if children:
                lines.append(f"Children: {', '.join(children)}")
            self.sel_parents_var.set("\n".join(lines))
        self._update_info()

    # ─── Node Rename ──────────────────────────────────────────────────────────

    def _rename_node_dialog(self, node):
        new_name = simpledialog.askstring(
            "Rename Node",
            f"Enter new name for node '{node.name}':",
            initialvalue=node.name,
            parent=self.root)
        if new_name and new_name.strip():
            node.name = new_name.strip()
            self._refresh_canvas()
            self._update_side_panel(node)

    def _rename_selected(self):
        if self.selected_node:
            new_name = self.sel_name_var.get().strip()
            if new_name:
                self.selected_node.name = new_name
                self._refresh_canvas()
                self._update_side_panel(self.selected_node)

    # ─── Graph Operations ─────────────────────────────────────────────────────

    def _clear_graph(self):
        if self.nodes:
            if messagebox.askyesno("Clear Graph", "Remove all nodes and edges?"):
                self.nodes.clear()
                self.edges.clear()
                self.selected_node = None
                Node._id_counter = 0
                self._refresh_canvas()
                self._update_info()

    def _build_graph_model(self):
        """Build and return the graph dict used by DAGSampler / export."""
        leaves = {n.id for n in self._get_leaves()}
        roots  = {n.id for n in self._get_roots()}
        parents_map = {n.id: [] for n in self.nodes}
        for a, b in self.edges:
            parents_map[b.id].append(a.id)
        return {
            "nodes":    [n.to_dict() for n in self.nodes],
            "edges":    [[a.id, b.id] for a, b in self.edges],
            "leaf_ids": list(leaves),
            "root_ids": list(roots),
            "parents":  {str(k): v for k, v in parents_map.items()},
            "var_types": {str(n.id): n.var_type for n in self.nodes},
            "levels":    {str(n.id): getattr(n, "levels", 5)
                          for n in self.nodes
                          if n.var_type in ("ordinal", "categorical")},
        }

    def _export_model(self):
        """Save the raw DAG structure as JSON (no data)."""
        if not self.nodes:
            messagebox.showwarning("Empty Graph", "Add some nodes before saving.")
            return
        if not self._is_acyclic():
            messagebox.showerror("Cycle Detected",
                                 "The graph contains a cycle. Fix it first.")
            return
        model = self._build_graph_model()
        path = filedialog.asksaveasfilename(
            title="Save DAG Graph",
            defaultextension=".json",
            filetypes=[("JSON model", "*.json"), ("All files", "*.*")],
            initialfile="dag_model.json")
        if path:
            with open(path, "w") as f:
                json.dump(model, f, indent=2)
            self.status_var.set(f"Graph saved → {path}")

    # ─── Generate Data Dialog ─────────────────────────────────────────────────

    def _open_generate_dialog(self):
        if not self.nodes:
            messagebox.showwarning("Empty Graph", "Build a graph first.")
            return
        if not self._is_acyclic():
            messagebox.showerror("Cycle Detected", "Fix the cycle before generating data.")
            return
        if not _SAMPLER:
            messagebox.showerror("Missing Module",
                                 "dag_sampler.py not found.\n"
                                 "Place it in the same folder as dag_builder.py.")
            return
        if not _NUMPY:
            messagebox.showerror("Missing Dependency",
                                 "numpy is required.\nRun: pip install numpy")
            return

        dlg = GenerateDialog(self.root, self._build_graph_model())
        self.root.wait_window(dlg.window)
        if dlg.result_message:
            self.status_var.set(dlg.result_message)

    def _open_hypothesis_dialog(self):
        if not self.nodes:
            messagebox.showwarning("Empty Graph", "Build a graph first.")
            return
        if not self._is_acyclic():
            messagebox.showerror("Cycle Detected", "Fix the cycle before testing.")
            return
        if not _SAMPLER:
            messagebox.showerror("Missing Module",
                                 "dag_sampler.py not found in the same folder.")
            return
        if not _HYPOTHESIS:
            messagebox.showerror("Missing Module",
                                 f"dag_hypothesis_test.py could not be imported.\n\n"
                                 f"{_HYPOTHESIS_ERR}")
            return
        if not _NUMPY:
            messagebox.showerror("Missing Dependency",
                                 "numpy is required.\nRun: pip install numpy")
            return
        dlg = HypothesisDialog(self.root, self._build_graph_model())
        self.root.wait_window(dlg.window)
        if dlg.result_message:
            self.status_var.set(dlg.result_message)

    def _load_graph(self):
        path = filedialog.askopenfilename(
            title="Load DAG Model",
            filetypes=[("JSON model", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path) as f:
                model = json.load(f)
            self.nodes.clear()
            self.edges.clear()
            id_map = {}
            max_id = 0
            var_types = model.get("var_types", {})
            levels_map = model.get("levels", {})
            for nd in model["nodes"]:
                vt = nd.get("var_type") or var_types.get(str(nd["id"]), "continuous")
                n = Node(nd["x"], nd["y"], nd["name"], var_type=vt)
                n.id = nd["id"]
                if vt in ("ordinal", "categorical"):
                    n.levels = levels_map.get(str(nd["id"]), 5)
                id_map[nd["id"]] = n
                max_id = max(max_id, nd["id"])
                self.nodes.append(n)
            Node._id_counter = max_id
            for a_id, b_id in model["edges"]:
                self.edges.append((id_map[a_id], id_map[b_id]))
            self._refresh_canvas()
            self._update_info()
            self.status_var.set(f"Graph loaded from {path}")
        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to load graph:\n{e}")





def main():
    root = tk.Tk()
    app = DAGBuilderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()