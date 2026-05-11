# DeepDive — Setup & Deployment Guide

## What you have

| File | Role |
|---|---|
| `web/index.html` | The single-page web app (GitHub Pages) |
| `web/dag_editor.js` | Canvas DAG editor — no dependencies |
| `web/gemini_bridge.js` | Gemini API integration |
| `local_server.py` | DeepDive local Python bridge server |

---

## Quickstart (local development)

### 1. Install Python dependencies

```powershell
pip install flask flask-cors
```

### 2. Get a free Groq API key

Go to **https://console.groq.com**, sign up with your Google account, then:
- Click **API Keys → Create API key**
- Copy the key (shown only once)

No credit card required. The free tier supports 30 requests/minute on Llama 3, which is more than enough.

### 3. Set your API key

```powershell
# Windows PowerShell
$env:GEMINI_API_KEY = "your-groq-key-here"

# Windows CMD
set GEMINI_API_KEY=your-groq-key-here

# Linux / macOS
export GEMINI_API_KEY=your-groq-key-here
```

> The env var is still named `GEMINI_API_KEY` — the local server uses that name regardless of which provider is active. To switch providers, change `ACTIVE_PROVIDER` at the top of `gemini_bridge.js`.

### 3. Start the local bridge server

```powershell
python local_server.py
# Listening on http://localhost:7432
```

### 4. Open the web app

Open `web/index.html` directly in your browser, or use VS Code Live Server
(right-click → Open with Live Server).

---

## Deploying to GitHub Pages

### One-time setup

1. Create a GitHub repository (can be private or public).
2. Put the contents of the `web/` folder in a `docs/` folder at the repo root,
   OR use the `web/` folder directly and configure Pages to serve from it.
3. In repository Settings → Pages → Source, choose:
   - Branch: `main`  (or `gh-pages`)
   - Folder: `/docs`  (or `/web`)
4. GitHub Pages will assign you a URL like `https://msalem7777.github.io/deepdive`.

### Update the CORS allowed origins

Open `local_server.py` and add your Pages URL to `ALLOWED_ORIGINS`:

```python
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:5500",
    "https://msalem7777.github.io",   # ← your Pages URL
]
```

Restart `local_server.py` after changing this.

---

## Usage

### Drawing a DAG manually

| Action | How |
|---|---|
| Add node | Click **＋ Node** then click the canvas |
| Name a node | A dialog pops up immediately; you MUST name every node |
| Add emoji | Type one in the dialog, or use **✨ Suggest** in the sidebar after placing |
| Add edge | Click **→ Edge**, click source node, click target node |
| Delete | Click **✕ Delete**, click a node or edge |
| Move nodes | Click **✦ Select**, drag a node |
| Auto-layout | Click **⊞ Layout** (or press `L`) |
| Undo / Redo | `Ctrl+Z` / `Ctrl+Y` |

### Using Gemini to build a DAG from text

1. Click **✨ Ask Gemini** to open the LLM panel.
2. Describe your causal model in plain English.
3. Click **Generate DAG** — Gemini returns a graph with emojis pre-assigned.
4. Edit the result manually as needed.

> Gemini features require `local_server.py` to be running (it serves the API key).

### Sending the DAG to your local Python environment

Click **▶ Send to Server**.  The graph is POSTed to `local_server.py`, which
saves it to `dag_outputs/dag_YYYYMMDD_HHMMSS.json`.  You can then load it with:

```python
from dag_sampler import DAGSampler
sampler = DAGSampler.from_file("dag_outputs/dag_20240510_143201.json")
df = sampler.sample_dataset(n_rows=500, seed=42)
```

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| `S` | Select mode |
| `N` | Add node mode |
| `E` | Add edge mode |
| `D` | Delete mode |
| `L` | Auto-layout |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `Escape` | Back to Select mode |
| `Delete` | Delete selected node |

---

## Troubleshooting

**"Local server unreachable" (red dot in status bar)**
→ Make sure `python local_server.py` is running in a terminal.
→ Check that port 7432 is not blocked by a firewall.

**"Gemini API key not set"**
→ Set `GEMINI_API_KEY` in the environment BEFORE running `local_server.py`.
→ Restarting the server picks up the new env variable.

**DAG rejected by server: "Missing 'name' key"**
→ All nodes must be named. Unnamed nodes are highlighted in red on the canvas.

**GitHub Pages shows a blank page**
→ Ensure `index.html`, `dag_editor.js`, and `gemini_bridge.js` are all in the
  same folder on the `gh-pages` branch.

---

## Architecture summary

```
GitHub Pages (static)           Your machine
──────────────────────          ────────────────────
index.html                      local_server.py (DeepDive bridge)
dag_editor.js          POST /dag ────────────────►  dag_outputs/dag_*.json
gemini_bridge.js  ──────────────────────────────►  (optionally: dag_sampler.py)

                   GET /gemini_key ────────────►  $GEMINI_API_KEY (env var)
                   POST to Gemini API (direct)
```

The local server ONLY listens on `127.0.0.1` — it never accepts connections
from the internet.  Your Gemini key never leaves your machine except in the
direct browser → Gemini API call.
