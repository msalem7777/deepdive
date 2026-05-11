/**
 * dag_editor.js
 * ══════════════════════════════════════════════════════════════════════════════
 * DeepDive — Self-contained DAG canvas editor.
 *
 * RESPONSIBILITIES
 * ────────────────
 * • Render nodes and directed edges on an HTML5 <canvas>.
 * • Support mouse interactions: add node, drag node, add edge, delete.
 * • Enforce acyclicity: reject any edge that would form a cycle (Kahn's algo).
 * • Require every node to have a non-empty name before the graph can be submitted.
 * • Undo / redo with a deep-copy snapshot stack.
 * • Auto-layout (Sugiyama-style layered layout).
 * • Export the graph as the JSON schema expected by dag_sampler.py.
 * • Support injecting a graph from outside (used by the Gemini bridge).
 *
 * USAGE
 * ─────
 *   const editor = new DAGEditor("my-canvas-id", {
 *     onGraphChange: (graph) => console.log(graph),
 *   });
 *   editor.setMode("add_node");
 *   editor.undo();
 *   editor.layout();
 *   const json = editor.exportGraph();
 *   editor.importGraph(someGraphDict);
 *
 * COORDINATE SYSTEM
 * ─────────────────
 * All node positions are stored in "world" coordinates (pixels relative to the
 * canvas origin at top-left).  The canvas is not scrollable yet — nodes are
 * clamped to the visible area on placement.
 * ══════════════════════════════════════════════════════════════════════════════
 */

// ─────────────────────────────────────────────────────────────────────────────
// Constants & theme
// ─────────────────────────────────────────────────────────────────────────────

/** Visual radius of every node circle, in pixels. */
const NODE_R = 36;

/** How many pixels away from an edge midpoint counts as a "click" on that edge. */
const EDGE_HIT_DIST = 10;

/** Maximum number of undo snapshots kept in memory. */
const MAX_UNDO = 60;

/** Duration (ms) of the "pulse" animation on newly placed nodes. */
const PULSE_DURATION = 700;

/** Variable type choices with display metadata. */
const VAR_TYPES = {
  continuous:  { label: "~",    color: "#f59e0b", desc: "Continuous (real-valued)" },
  binary:      { label: "01",   color: "#34d399", desc: "Binary (0 or 1)" },
  ordinal:     { label: "1…n",  color: "#a78bfa", desc: "Ordinal (integer levels)" },
  categorical: { label: "A…Z",  color: "#fb923c", desc: "Categorical (one-hot)" },
  count:       { label: "#",    color: "#38bdf8", desc: "Count (non-negative int)" },
};

// Dark theme palette — mirrors local_server.py's comments about the tkinter scheme
// but uses amber as the primary accent for the web layer.
const THEME = {
  bg:          "#0f1117",   // canvas background
  grid:        "#1a1e2a",   // subtle grid lines
  nodeFill:    "#1c2033",   // default node interior
  nodeRoot:    "#1f150a",   // root node tint
  nodeLeaf:    "#0a1f12",   // leaf node tint
  edgeColor:   "#f59e0b",   // edge / arrow color (amber)
  edgeShadow:  "#92400e",   // edge glow shadow
  textMain:    "#f1f5f9",   // primary text
  textDim:     "#64748b",   // secondary / muted text
  selected:    "#f59e0b",   // selection ring
  danger:      "#ef4444",   // delete / error accent
  pulseColor:  "#fcd34d",   // new-node pulse ring
};


// ─────────────────────────────────────────────────────────────────────────────
// Utility helpers
// ─────────────────────────────────────────────────────────────────────────────

/** Deep-clone any JSON-serialisable value. */
function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

/** Euclidean distance between two points. */
function dist(ax, ay, bx, by) {
  return Math.hypot(bx - ax, by - ay);
}

/**
 * Point-to-segment distance.
 * Used to detect whether a click lands "on" an edge line.
 */
function pointToSegmentDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return dist(px, py, ax, ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lenSq));
  return dist(px, py, ax + t * dx, ay + t * dy);
}

/**
 * Kahn's algorithm: returns true if the directed graph is acyclic.
 *
 * @param {number[]} nodeIds   - array of all node ids
 * @param {Array}    edges     - array of [srcId, tgtId] pairs
 */
function isDAG(nodeIds, edges) {
  const inDeg = {};
  const children = {};
  for (const id of nodeIds) { inDeg[id] = 0; children[id] = []; }
  for (const [src, tgt] of edges) {
    children[src].push(tgt);
    inDeg[tgt]++;
  }
  const queue = nodeIds.filter(id => inDeg[id] === 0);
  let visited = 0;
  while (queue.length) {
    const n = queue.shift();
    visited++;
    for (const c of children[n]) {
      if (--inDeg[c] === 0) queue.push(c);
    }
  }
  return visited === nodeIds.length;
}

/**
 * Build a topological order for the given node ids and edges.
 * Returns null if the graph has a cycle.
 */
function topoSort(nodeIds, edges) {
  const inDeg = {};
  const children = {};
  for (const id of nodeIds) { inDeg[id] = 0; children[id] = []; }
  for (const [src, tgt] of edges) {
    children[src].push(tgt);
    inDeg[tgt]++;
  }
  const queue = nodeIds.filter(id => inDeg[id] === 0);
  const order = [];
  while (queue.length) {
    const n = queue.shift();
    order.push(n);
    for (const c of children[n]) {
      if (--inDeg[c] === 0) queue.push(c);
    }
  }
  return order.length === nodeIds.length ? order : null;
}


// ─────────────────────────────────────────────────────────────────────────────
// DAGEditor class
// ─────────────────────────────────────────────────────────────────────────────

class DAGEditor {
  /**
   * @param {string} canvasId   - id of the <canvas> element
   * @param {object} opts
   *   opts.onGraphChange  - called with the current graph dict whenever it changes
   *   opts.onStatus       - called with a status string for the status bar
   *   opts.onNodeSelect   - called with the selected node object (or null)
   */
  constructor(canvasId, opts = {}) {
    this._canvas  = document.getElementById(canvasId);
    this._ctx     = this._canvas.getContext("2d");
    this._opts    = opts;

    // ── Graph state ──────────────────────────────────────────────────────
    // Every node: { id, name, emoji, x, y, varType, selected, pulseStart }
    // pulseStart is the performance.now() timestamp when the node was placed;
    // used to drive the "new node" pulse ring animation.
    this._nodes = [];

    // Every edge: [srcId, tgtId]
    this._edges = [];

    // Auto-incrementing id counter (never reused, even after deletes).
    this._nextId = 1;

    // ── Interaction state ────────────────────────────────────────────────
    this._mode         = "select";  // "select" | "add_node" | "add_edge" | "delete"
    this._selectedNode = null;      // reference to the currently selected node object
    this._edgeSource   = null;      // node that was clicked first in add_edge mode
    this._dragging     = false;
    this._dragOffsetX  = 0;
    this._dragOffsetY  = 0;

    // Temporary mouse position used to draw the "rubber-band" edge preview.
    this._mouseX = 0;
    this._mouseY = 0;

    // ── Undo / redo stacks ───────────────────────────────────────────────
    // Each entry is a deep-clone snapshot of { nodes, edges, nextId }.
    this._undoStack = [];
    this._redoStack = [];

    // ── Animation ────────────────────────────────────────────────────────
    this._rafId = null;  // requestAnimationFrame handle

    // ── Bootstrap ────────────────────────────────────────────────────────
    this._bindEvents();
    this._startRenderLoop();
    this._setStatus("Select a mode from the toolbar to begin.");
  }


  // ═══════════════════════════════════════════════════════════════════════════
  // Public API
  // ═══════════════════════════════════════════════════════════════════════════

  /** Switch the editing mode. */
  setMode(mode) {
    this._mode       = mode;
    this._edgeSource = null;   // reset any in-progress edge
    this._deselectAll();

    const msgs = {
      select:   "Click a node to select it. Drag to move.",
      add_node: "Click anywhere on the canvas to place a new node.",
      add_edge: "Click a source node, then a target node.",
      delete:   "Click a node or edge to delete it.",
    };
    this._setStatus(msgs[mode] || "");

    // Update canvas cursor style for better affordance
    const cursors = {
      select:   "default",
      add_node: "crosshair",
      add_edge: "cell",
      delete:   "not-allowed",
    };
    this._canvas.style.cursor = cursors[mode] || "default";
  }

  /** Undo the last graph mutation. */
  undo() {
    if (this._undoStack.length === 0) return;
    // Push current state onto redo stack before reverting
    this._redoStack.push(this._snapshot());
    const prev = this._undoStack.pop();
    this._restoreSnapshot(prev);
    this._setStatus("Undo.");
    this._notifyChange();
  }

  /** Redo a previously undone mutation. */
  redo() {
    if (this._redoStack.length === 0) return;
    this._undoStack.push(this._snapshot());
    const next = this._redoStack.pop();
    this._restoreSnapshot(next);
    this._setStatus("Redo.");
    this._notifyChange();
  }

  /**
   * Auto-layout: assign node positions using a Sugiyama-inspired layered layout.
   *
   * Algorithm:
   *   1. Compute layer assignments via longest-path ranking.
   *   2. Within each layer, sort nodes to minimise crossings (barycentric heuristic).
   *   3. Assign (x, y) coordinates from the layer / position indices.
   */
  layout() {
    if (this._nodes.length === 0) return;
    this._saveUndo();

    const ids    = this._nodes.map(n => n.id);
    const order  = topoSort(ids, this._edges);
    if (!order) { this._setStatus("Cannot layout — graph has a cycle."); return; }

    // Longest-path layer assignment
    const layer = {};
    for (const id of ids) layer[id] = 0;
    for (const id of order) {
      const outEdges = this._edges.filter(([s]) => s === id);
      for (const [, tgt] of outEdges) {
        layer[tgt] = Math.max(layer[tgt], layer[id] + 1);
      }
    }

    // Group nodes into layers
    const byLayer = {};
    for (const id of ids) {
      const l = layer[id];
      if (!byLayer[l]) byLayer[l] = [];
      byLayer[l].push(id);
    }

    // Barycentric ordering within each layer
    const layerNums = Object.keys(byLayer).map(Number).sort((a, b) => a - b);
    // First layer: use topological index as initial order
    const topoIdx = {};
    order.forEach((id, i) => { topoIdx[id] = i; });
    for (const l of layerNums) {
      byLayer[l].sort((a, b) => {
        // Use average position of parents in the layer above as the barycenter
        const parentsOf = (id) => this._edges.filter(([, t]) => t === id).map(([s]) => s);
        const bary = (id) => {
          const parents = parentsOf(id).filter(p => layer[p] === l - 1);
          if (parents.length === 0) return topoIdx[id];
          return parents.reduce((sum, p) => {
            const pos = byLayer[l - 1]?.indexOf(p) ?? 0;
            return sum + pos;
          }, 0) / parents.length;
        };
        return bary(a) - bary(b);
      });
    }

    // Assign pixel coordinates
    const cw     = this._canvas.width;
    const ch     = this._canvas.height;
    const nL     = layerNums.length;
    const hStep  = nL > 1 ? (ch - 120) / (nL - 1) : 0;

    for (const l of layerNums) {
      const nodesInLayer = byLayer[l];
      const nN = nodesInLayer.length;
      const wStep = nN > 1 ? (cw - 120) / (nN - 1) : 0;
      nodesInLayer.forEach((id, i) => {
        const node = this._nodeById(id);
        node.x = nN > 1 ? 60 + i * wStep : cw / 2;
        node.y = nL > 1 ? 60 + l * hStep : ch / 2;
      });
    }

    this._setStatus("Auto-layout applied.");
    this._notifyChange();
  }

  /** Clear the entire graph (after confirmation). */
  clear(skipConfirm = false) {
    if (!skipConfirm && this._nodes.length > 0) {
      if (!window.confirm("Clear the entire graph? This cannot be undone.")) return;
    }
    this._saveUndo();
    this._nodes  = [];
    this._edges  = [];
    this._nextId = 1;
    this._selectedNode = null;
    this._edgeSource   = null;
    this._setStatus("Graph cleared.");
    this._notifyChange();
  }

  /**
   * Import a graph from the dict schema used by dag_sampler.py.
   * Called by the Gemini bridge when the LLM returns a graph.
   *
   * @param {object} graph  - { nodes, edges, ... }
   * @param {boolean} animate - if true, pulse-animate every imported node
   */
  importGraph(graph, animate = true) {
    this._saveUndo();
    this._nodes = [];
    this._edges = [];

    let maxId = 0;
    for (const n of (graph.nodes || [])) {
      const varType = n.var_type
        || (graph.var_types && graph.var_types[String(n.id)])
        || "continuous";
      this._nodes.push({
        id:         n.id,
        name:       n.name || `X${n.id}`,
        emoji:      n.emoji || "",           // Gemini-assigned emoji
        x:          n.x ?? 100 + Math.random() * 400,
        y:          n.y ?? 100 + Math.random() * 300,
        varType,
        selected:   false,
        pulseStart: animate ? performance.now() : null,
      });
      maxId = Math.max(maxId, n.id);
    }
    this._nextId = maxId + 1;
    this._edges  = (graph.edges || []).map(e => [e[0], e[1]]);

    // Auto-layout imported graphs so they look tidy
    // (We call it directly without saving another undo snapshot)
    this._layoutInternal();

    this._setStatus(`Imported ${this._nodes.length} nodes, ${this._edges.length} edges.`);
    this._notifyChange();
  }

  /**
   * Export the current graph as the dict schema expected by dag_sampler.py.
   * Returns null and sets a status message if any node has an empty name.
   */
  exportGraph() {
    // ── Validation: all nodes must be named ─────────────────────────────
    const unnamed = this._nodes.filter(n => !n.name.trim());
    if (unnamed.length > 0) {
      this._setStatus(
        `⚠ ${unnamed.length} node(s) have no name. Name every node before exporting.`
      );
      // Visually select the first unnamed node to draw attention to it
      this._deselectAll();
      unnamed[0].selected = true;
      this._selectedNode = unnamed[0];
      if (this._opts.onNodeSelect) this._opts.onNodeSelect(unnamed[0]);
      return null;
    }

    const ids    = this._nodes.map(n => n.id);
    const edgeSet = new Set(this._edges.map(([s, t]) => `${s}→${t}`));

    // Compute roots (no incoming edges) and leaves (no outgoing edges)
    const hasParent = new Set(this._edges.map(([, t]) => t));
    const hasChild  = new Set(this._edges.map(([s]) => s));
    const rootIds   = ids.filter(id => !hasParent.has(id));
    const leafIds   = ids.filter(id => !hasChild.has(id));

    // Build parents map: { "nodeId": [parentId, ...] }
    const parents = {};
    for (const id of ids) parents[String(id)] = [];
    for (const [src, tgt] of this._edges) {
      parents[String(tgt)].push(src);
    }

    return {
      nodes: this._nodes.map(n => ({
        id:       n.id,
        name:     n.name.trim(),
        emoji:    n.emoji || "",
        x:        Math.round(n.x),
        y:        Math.round(n.y),
        var_type: n.varType,
      })),
      edges:     this._edges.map(([s, t]) => [s, t]),
      root_ids:  rootIds,
      leaf_ids:  leafIds,
      parents,
      var_types: Object.fromEntries(this._nodes.map(n => [String(n.id), n.varType])),
      levels:    {},   // filled in if ordinal/categorical nodes exist
    };
  }

  /**
   * Update a single node's properties.
   * Called by the sidebar form when the user edits name / varType / emoji.
   */
  updateNode(id, props) {
    const node = this._nodeById(id);
    if (!node) return;
    this._saveUndo();
    Object.assign(node, props);
    this._notifyChange();
  }

  /** Return a reference to the currently selected node (or null). */
  get selectedNode() { return this._selectedNode; }

  /** Return a read-only copy of the node list (useful for the sidebar). */
  get nodes() { return this._nodes; }

  /** Return a read-only copy of the edge list. */
  get edges() { return this._edges; }


  // ═══════════════════════════════════════════════════════════════════════════
  // Private — Event Binding
  // ═══════════════════════════════════════════════════════════════════════════

  _bindEvents() {
    // ── Canvas mouse events ───────────────────────────────────────────────
    this._canvas.addEventListener("mousedown",  e => this._onMouseDown(e));
    this._canvas.addEventListener("mousemove",  e => this._onMouseMove(e));
    this._canvas.addEventListener("mouseup",    e => this._onMouseUp(e));
    this._canvas.addEventListener("dblclick",   e => this._onDblClick(e));

    // ── Keyboard shortcuts (on the document so they work without focus) ──
    document.addEventListener("keydown", e => this._onKeyDown(e));

    // ── Resize: update canvas logical size to match CSS size ─────────────
    // The canvas element has a CSS size (set in style.css) but the internal
    // pixel buffer must be set explicitly.  A ResizeObserver is more robust
    // than listening to window.resize.
    const ro = new ResizeObserver(() => this._resizeCanvas());
    ro.observe(this._canvas);
    this._resizeCanvas();  // run once immediately
  }

  _resizeCanvas() {
    const rect = this._canvas.getBoundingClientRect();
    // Only update if the size actually changed (avoids unnecessary redraws)
    if (this._canvas.width !== Math.round(rect.width) ||
        this._canvas.height !== Math.round(rect.height)) {
      this._canvas.width  = Math.round(rect.width);
      this._canvas.height = Math.round(rect.height);
    }
  }

  /** Convert a MouseEvent's client coordinates to canvas-local coordinates. */
  _canvasXY(e) {
    const rect = this._canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }


  // ═══════════════════════════════════════════════════════════════════════════
  // Private — Mouse Handlers
  // ═══════════════════════════════════════════════════════════════════════════

  _onMouseDown(e) {
    if (e.button !== 0) return;   // only primary (left) button
    const { x, y } = this._canvasXY(e);
    const hit = this._nodeAt(x, y);

    if (this._mode === "select") {
      this._deselectAll();
      if (hit) {
        hit.selected      = true;
        this._selectedNode = hit;
        this._dragging     = true;
        this._dragOffsetX  = x - hit.x;
        this._dragOffsetY  = y - hit.y;
        if (this._opts.onNodeSelect) this._opts.onNodeSelect(hit);
        this._setStatus(`Selected: ${hit.name || "(unnamed)"}`);
      } else {
        this._selectedNode = null;
        if (this._opts.onNodeSelect) this._opts.onNodeSelect(null);
      }

    } else if (this._mode === "add_node") {
      if (!hit) this._addNode(x, y);

    } else if (this._mode === "add_edge") {
      if (hit) {
        if (!this._edgeSource) {
          // First click — record source
          this._edgeSource = hit;
          hit.selected = true;
          this._setStatus(`Edge source: "${hit.name || "(unnamed)"}". Now click the target.`);
        } else {
          // Second click — attempt to add the edge
          if (hit !== this._edgeSource) {
            this._addEdge(this._edgeSource.id, hit.id);
          }
          this._edgeSource = null;
          this._deselectAll();
        }
      }

    } else if (this._mode === "delete") {
      if (hit) {
        this._deleteNode(hit.id);
      } else {
        const edge = this._edgeAt(x, y);
        if (edge) this._deleteEdge(edge);
      }
    }
  }

  _onMouseMove(e) {
    const { x, y } = this._canvasXY(e);
    this._mouseX = x;
    this._mouseY = y;

    if (this._mode === "select" && this._dragging && this._selectedNode) {
      // Clamp node position within canvas bounds
      const r  = NODE_R;
      const cw = this._canvas.width;
      const ch = this._canvas.height;
      this._selectedNode.x = Math.max(r, Math.min(cw - r, x - this._dragOffsetX));
      this._selectedNode.y = Math.max(r, Math.min(ch - r, y - this._dragOffsetY));
    }
  }

  _onMouseUp(e) {
    if (this._dragging) {
      this._dragging = false;
      this._notifyChange();   // fire after drag ends, not on every pixel move
    }
  }

  _onDblClick(e) {
    const { x, y } = this._canvasXY(e);
    const hit = this._nodeAt(x, y);
    if (hit && this._mode === "select") {
      this._promptRename(hit);
    }
  }


  // ═══════════════════════════════════════════════════════════════════════════
  // Private — Keyboard Handler
  // ═══════════════════════════════════════════════════════════════════════════

  _onKeyDown(e) {
    // Don't steal keystrokes when the user is typing in an input/textarea
    const tag = document.activeElement?.tagName?.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;

    const ctrl = e.ctrlKey || e.metaKey;   // metaKey = Cmd on Mac

    if (ctrl && e.key === "z") { e.preventDefault(); this.undo(); return; }
    if (ctrl && e.key === "y") { e.preventDefault(); this.redo(); return; }
    if (ctrl && e.key === "Z") { e.preventDefault(); this.redo(); return; } // Ctrl+Shift+Z

    if (e.key === "Escape") {
      this._edgeSource = null;
      this._deselectAll();
      this.setMode("select");
    }

    if (e.key === "Delete" || e.key === "Backspace") {
      if (this._selectedNode) this._deleteNode(this._selectedNode.id);
    }

    // Mode shortcuts
    if (!ctrl) {
      if (e.key === "s" || e.key === "S") this.setMode("select");
      if (e.key === "n" || e.key === "N") this.setMode("add_node");
      if (e.key === "e" || e.key === "E") this.setMode("add_edge");
      if (e.key === "d" || e.key === "D") this.setMode("delete");
      if (e.key === "l" || e.key === "L") this.layout();
    }
  }


  // ═══════════════════════════════════════════════════════════════════════════
  // Private — Graph Mutations
  // ═══════════════════════════════════════════════════════════════════════════

  _addNode(x, y) {
    this._saveUndo();
    const id   = this._nextId++;
    const node = {
      id,
      name:       "",            // Empty by default — user MUST name it
      emoji:      "",
      x,
      y,
      varType:    "continuous",
      selected:   false,
      pulseStart: performance.now(),   // trigger the placement pulse animation
    };
    this._nodes.push(node);

    // Immediately prompt for a name so the "must be named" rule is surfaced early
    this._promptRename(node);
    this._notifyChange();
    this._setStatus(`Node added. Give it a name!`);
  }

  _addEdge(srcId, tgtId) {
    // Guard: no duplicate edges
    if (this._edges.some(([s, t]) => s === srcId && t === tgtId)) {
      this._setStatus("⚠ Edge already exists.");
      return;
    }
    // Guard: no self-loop
    if (srcId === tgtId) {
      this._setStatus("⚠ Cannot connect a node to itself.");
      return;
    }
    // Guard: acyclicity check
    const testEdges = [...this._edges, [srcId, tgtId]];
    if (!isDAG(this._nodes.map(n => n.id), testEdges)) {
      this._setStatus("⚠ Adding this edge would create a cycle — rejected.");
      return;
    }

    this._saveUndo();
    this._edges.push([srcId, tgtId]);
    const src = this._nodeById(srcId);
    const tgt = this._nodeById(tgtId);
    this._setStatus(`Edge added: "${src?.name || srcId}" → "${tgt?.name || tgtId}"`);
    this._notifyChange();
  }

  _deleteNode(id) {
    this._saveUndo();
    this._nodes  = this._nodes.filter(n => n.id !== id);
    this._edges  = this._edges.filter(([s, t]) => s !== id && t !== id);
    if (this._selectedNode?.id === id) {
      this._selectedNode = null;
      if (this._opts.onNodeSelect) this._opts.onNodeSelect(null);
    }
    if (this._edgeSource?.id === id) this._edgeSource = null;
    this._setStatus("Node deleted.");
    this._notifyChange();
  }

  _deleteEdge([srcId, tgtId]) {
    this._saveUndo();
    this._edges = this._edges.filter(([s, t]) => !(s === srcId && t === tgtId));
    this._setStatus("Edge deleted.");
    this._notifyChange();
  }


  // ═══════════════════════════════════════════════════════════════════════════
  // Private — Hit Testing
  // ═══════════════════════════════════════════════════════════════════════════

  /** Return the topmost node that contains the point (x, y), or null. */
  _nodeAt(x, y) {
    // Iterate in reverse so the most recently placed node (drawn on top) is hit first
    for (let i = this._nodes.length - 1; i >= 0; i--) {
      const n = this._nodes[i];
      if (dist(x, y, n.x, n.y) <= NODE_R) return n;
    }
    return null;
  }

  /** Return the first edge whose midpoint is within EDGE_HIT_DIST of (x, y). */
  _edgeAt(x, y) {
    for (const [srcId, tgtId] of this._edges) {
      const src = this._nodeById(srcId);
      const tgt = this._nodeById(tgtId);
      if (!src || !tgt) continue;

      // The visible edge starts/ends at the node circumference, not centre —
      // but for hit-testing we use the full centre-to-centre segment (close enough).
      if (pointToSegmentDist(x, y, src.x, src.y, tgt.x, tgt.y) < EDGE_HIT_DIST) {
        return [srcId, tgtId];
      }
    }
    return null;
  }

  /** Return the node with the given id, or undefined. */
  _nodeById(id) { return this._nodes.find(n => n.id === id); }


  // ═══════════════════════════════════════════════════════════════════════════
  // Private — Undo/Redo Helpers
  // ═══════════════════════════════════════════════════════════════════════════

  /** Capture the current graph state as a deep-cloned snapshot object. */
  _snapshot() {
    return deepClone({ nodes: this._nodes, edges: this._edges, nextId: this._nextId });
  }

  /** Push the current state onto the undo stack before a mutation. */
  _saveUndo() {
    this._undoStack.push(this._snapshot());
    if (this._undoStack.length > MAX_UNDO) this._undoStack.shift();   // cap memory
    this._redoStack = [];   // any new mutation clears the redo stack
  }

  /** Restore graph state from a snapshot. */
  _restoreSnapshot(snap) {
    // Re-attach pulseStart timestamps that were lost through JSON cloning
    // (performance.now() values can't survive serialisation, so set to null).
    this._nodes  = snap.nodes.map(n => ({ ...n, pulseStart: null }));
    this._edges  = snap.edges;
    this._nextId = snap.nextId;
    this._selectedNode = null;
    this._edgeSource   = null;
    if (this._opts.onNodeSelect) this._opts.onNodeSelect(null);
  }


  // ═══════════════════════════════════════════════════════════════════════════
  // Private — Layout (internal, no undo save)
  // ═══════════════════════════════════════════════════════════════════════════

  /** Same as layout() but does NOT push an undo snapshot. */
  _layoutInternal() {
    if (this._nodes.length === 0) return;
    const ids   = this._nodes.map(n => n.id);
    const order = topoSort(ids, this._edges);
    if (!order) return;

    const layer = {};
    for (const id of ids) layer[id] = 0;
    for (const id of order) {
      for (const [s, t] of this._edges) {
        if (s === id) layer[t] = Math.max(layer[t], layer[id] + 1);
      }
    }

    const byLayer = {};
    for (const id of ids) {
      const l = layer[id];
      if (!byLayer[l]) byLayer[l] = [];
      byLayer[l].push(id);
    }

    const layerNums = Object.keys(byLayer).map(Number).sort((a, b) => a - b);
    const cw = this._canvas.width  || 800;
    const ch = this._canvas.height || 500;
    const nL = layerNums.length;
    const hStep = nL > 1 ? (ch - 120) / (nL - 1) : 0;

    for (const l of layerNums) {
      const nodesInLayer = byLayer[l];
      const nN = nodesInLayer.length;
      const wStep = nN > 1 ? (cw - 120) / (nN - 1) : 0;
      nodesInLayer.forEach((id, i) => {
        const node = this._nodeById(id);
        node.x = nN > 1 ? 60 + i * wStep : cw / 2;
        node.y = nL > 1 ? 60 + l * hStep : ch / 2;
      });
    }
  }


  // ═══════════════════════════════════════════════════════════════════════════
  // Private — Rename Prompt
  // ═══════════════════════════════════════════════════════════════════════════

  /**
   * Show a lightweight inline prompt for renaming a node.
   * We use a <dialog> element rather than window.prompt() so it doesn't block
   * the render loop and looks consistent with the site theme.
   * The dialog is created once and reused.
   */
  _promptRename(node) {
    // Find the rename dialog (defined in index.html).
    // The dialog only asks for a name — emoji is assigned automatically by the LLM.
    let dlg = document.getElementById("rename-dialog");
    if (!dlg) return;

    const input = dlg.querySelector("#rename-input");
    if (input) {
      input.value = node.name;
      // Focus and select all so the user can type immediately
      setTimeout(() => { input.focus(); input.select(); }, 50);
    }

    dlg.returnValue = "";
    dlg.showModal();

    const onClose = () => {
      if (dlg.returnValue === "ok") {
        const newName = input?.value?.trim() || "";
        this._saveUndo();
        node.name = newName;

        if (newName) {
          this._setStatus(`Node named "${newName}" — picking emoji…`);

          // ── Automatic emoji assignment ──────────────────────────────────
          // Call the LLM bridge asynchronously so the dialog closes instantly.
          // DeepDiveLLM is defined in gemini_bridge.js and loaded before this
          // file, so it is safe to reference here.  If it isn't available
          // (e.g. script load order issue) we fall back to a generic symbol.
          if (window.DeepDiveLLM?.autoEmoji) {
            window.DeepDiveLLM.autoEmoji(newName).then(emoji => {
              node.emoji = emoji;
              this._setStatus(`Node "${newName}" → ${emoji}`);
              this._notifyChange();
              if (this._opts.onNodeSelect && this._selectedNode?.id === node.id) {
                this._opts.onNodeSelect(node);
              }
            });
          } else {
            // LLM bridge not loaded — use a plain dot as a neutral placeholder
            node.emoji = "◈";
          }

        } else {
          this._setStatus("⚠ Node has no name yet.");
        }

        this._notifyChange();
        if (this._opts.onNodeSelect && this._selectedNode?.id === node.id) {
          this._opts.onNodeSelect(node);
        }
      }
      dlg.removeEventListener("close", onClose);
    };
    dlg.addEventListener("close", onClose);
  }


  // ═══════════════════════════════════════════════════════════════════════════
  // Private — Notification helpers
  // ═══════════════════════════════════════════════════════════════════════════

  _setStatus(msg) {
    if (this._opts.onStatus) this._opts.onStatus(msg);
  }

  _notifyChange() {
    if (this._opts.onGraphChange) {
      const graph = this.exportGraph();   // may return null if nodes are unnamed
      this._opts.onGraphChange(graph);
    }
  }

  _deselectAll() {
    for (const n of this._nodes) n.selected = false;
    this._selectedNode = null;
    if (this._opts.onNodeSelect) this._opts.onNodeSelect(null);
  }


  // ═══════════════════════════════════════════════════════════════════════════
  // Private — Render Loop
  // ═══════════════════════════════════════════════════════════════════════════

  _startRenderLoop() {
    const tick = () => {
      this._render();
      this._rafId = requestAnimationFrame(tick);
    };
    this._rafId = requestAnimationFrame(tick);
  }

  _render() {
    const ctx = this._ctx;
    const cw  = this._canvas.width;
    const ch  = this._canvas.height;
    const now = performance.now();

    // ── Background ────────────────────────────────────────────────────────
    ctx.clearRect(0, 0, cw, ch);
    ctx.fillStyle = THEME.bg;
    ctx.fillRect(0, 0, cw, ch);

    // ── Grid paper ────────────────────────────────────────────────────────
    this._drawGrid(ctx, cw, ch);

    // ── Edges ─────────────────────────────────────────────────────────────
    for (const [srcId, tgtId] of this._edges) {
      const src = this._nodeById(srcId);
      const tgt = this._nodeById(tgtId);
      if (src && tgt) this._drawEdge(ctx, src, tgt);
    }

    // ── Rubber-band edge (preview while adding an edge) ───────────────────
    if (this._mode === "add_edge" && this._edgeSource) {
      this._drawRubberBandEdge(ctx, this._edgeSource, this._mouseX, this._mouseY);
    }

    // ── Nodes ─────────────────────────────────────────────────────────────
    const hasParent = new Set(this._edges.map(([, t]) => t));
    const hasChild  = new Set(this._edges.map(([s]) => s));

    for (const node of this._nodes) {
      const isRoot = !hasParent.has(node.id);
      const isLeaf = !hasChild.has(node.id);
      this._drawNode(ctx, node, isRoot, isLeaf, now);
    }
  }

  // ── Grid ─────────────────────────────────────────────────────────────────

  _drawGrid(ctx, cw, ch) {
    const step = 32;
    ctx.strokeStyle = THEME.grid;
    ctx.lineWidth   = 0.5;
    ctx.beginPath();
    for (let x = 0; x <= cw; x += step) { ctx.moveTo(x, 0); ctx.lineTo(x, ch); }
    for (let y = 0; y <= ch; y += step) { ctx.moveTo(0, y); ctx.lineTo(cw, y); }
    ctx.stroke();
  }

  // ── Edge ─────────────────────────────────────────────────────────────────

  _drawEdge(ctx, src, tgt) {
    const angle  = Math.atan2(tgt.y - src.y, tgt.x - src.x);
    const startX = src.x + Math.cos(angle) * NODE_R;
    const startY = src.y + Math.sin(angle) * NODE_R;
    const endX   = tgt.x - Math.cos(angle) * (NODE_R + 6);   // +6 for arrowhead room
    const endY   = tgt.y - Math.sin(angle) * (NODE_R + 6);

    ctx.save();

    // Glow shadow
    ctx.shadowColor = THEME.edgeShadow;
    ctx.shadowBlur  = 6;

    ctx.strokeStyle = THEME.edgeColor;
    ctx.lineWidth   = 2;
    ctx.beginPath();
    ctx.moveTo(startX, startY);
    ctx.lineTo(endX, endY);
    ctx.stroke();

    // Arrowhead
    const aLen   = 12, aWidth = 5;
    const ax1 = endX - aLen * Math.cos(angle - 0.35);
    const ay1 = endY - aLen * Math.sin(angle - 0.35);
    const ax2 = endX - aLen * Math.cos(angle + 0.35);
    const ay2 = endY - aLen * Math.sin(angle + 0.35);

    ctx.fillStyle = THEME.edgeColor;
    ctx.beginPath();
    ctx.moveTo(endX, endY);
    ctx.lineTo(ax1, ay1);
    ctx.lineTo(ax2, ay2);
    ctx.closePath();
    ctx.fill();

    ctx.restore();
  }

  _drawRubberBandEdge(ctx, srcNode, mx, my) {
    const angle  = Math.atan2(my - srcNode.y, mx - srcNode.x);
    const startX = srcNode.x + Math.cos(angle) * NODE_R;
    const startY = srcNode.y + Math.sin(angle) * NODE_R;

    ctx.save();
    ctx.setLineDash([6, 4]);
    ctx.strokeStyle = THEME.edgeColor + "aa";   // translucent
    ctx.lineWidth   = 2;
    ctx.beginPath();
    ctx.moveTo(startX, startY);
    ctx.lineTo(mx, my);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
  }

  // ── Node ──────────────────────────────────────────────────────────────────

  _drawNode(ctx, node, isRoot, isLeaf, now) {
    const { x, y, id, name, emoji, varType, selected, pulseStart } = node;
    const vt   = VAR_TYPES[varType] || VAR_TYPES.continuous;
    const ring  = vt.color;
    const r     = NODE_R;

    ctx.save();

    // ── Pulse animation on new nodes ──────────────────────────────────────
    if (pulseStart !== null) {
      const elapsed = now - pulseStart;
      if (elapsed < PULSE_DURATION) {
        const progress = elapsed / PULSE_DURATION;          // 0 → 1
        const alpha    = 1 - progress;                      // fades out
        const pulseR   = r + progress * 28;                 // expands outward
        ctx.globalAlpha = alpha * 0.7;
        ctx.strokeStyle = THEME.pulseColor;
        ctx.lineWidth   = 2.5;
        ctx.beginPath();
        ctx.arc(x, y, pulseR, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
      } else {
        // Animation finished — clear pulseStart so we stop animating
        node.pulseStart = null;
      }
    }

    // ── Selection ring ────────────────────────────────────────────────────
    if (selected) {
      ctx.shadowColor = THEME.selected;
      ctx.shadowBlur  = 14;
      ctx.strokeStyle = THEME.selected;
      ctx.lineWidth   = 2;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.arc(x, y, r + 7, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.shadowBlur = 0;
    }

    // ── Drop shadow ───────────────────────────────────────────────────────
    ctx.shadowColor = "rgba(0,0,0,0.5)";
    ctx.shadowBlur  = 12;
    ctx.shadowOffsetY = 4;

    // ── Node fill ─────────────────────────────────────────────────────────
    let fillColor = THEME.nodeFill;
    if (isRoot && isLeaf) fillColor = "#1e1a2a";   // isolated
    else if (isRoot)      fillColor = THEME.nodeRoot;
    else if (isLeaf)      fillColor = THEME.nodeLeaf;

    ctx.fillStyle = fillColor;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;

    // ── Outer ring (var type colour) ──────────────────────────────────────
    ctx.strokeStyle = ring;
    ctx.lineWidth   = selected ? 2.5 : 2;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.stroke();

    // ── Role badge (top-right corner of circle) ───────────────────────────
    const role = isRoot && isLeaf ? "?"
               : isRoot           ? "R"
               : isLeaf           ? "L"
               :                    "·";
    ctx.font      = "bold 9px 'Courier New', monospace";
    ctx.fillStyle = ring;
    ctx.textAlign = "center";
    ctx.fillText(role, x + r * 0.65, y - r * 0.65);

    // ── Var-type label (bottom of circle) ────────────────────────────────
    ctx.font      = "8px 'Courier New', monospace";
    ctx.fillStyle = ring + "cc";   // slightly translucent
    ctx.textAlign = "center";
    ctx.fillText(vt.label, x, y + r - 7);

    // ── Emoji (large, floated above the node) ────────────────────────────
    if (emoji) {
      ctx.font      = `${r * 0.8}px serif`;    // scale emoji with node size
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = "rgba(255,255,255,0.9)";
      // Draw emoji above the top edge of the circle, centred
      ctx.fillText(emoji, x, y - r - 14);
      ctx.textBaseline = "alphabetic";
    }

    // ── Node name ─────────────────────────────────────────────────────────
    ctx.font = `bold ${name.length > 8 ? 9 : 11}px 'Courier New', monospace`;
    ctx.fillStyle  = name ? THEME.textMain : THEME.danger + "99";
    ctx.textAlign  = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(name || "name?", x, y - (emoji ? 4 : 0));
    ctx.textBaseline = "alphabetic";

    ctx.restore();
  }
}

// Export for use in index.html (no module bundler — plain global)
window.DAGEditor = DAGEditor;
window.VAR_TYPES = VAR_TYPES;
