/**
 * gemini_bridge.js
 * ══════════════════════════════════════════════════════════════════════════════
 * DeepDive — LLM bridge module.
 *
 * KEY MODE (Option B — Cloudflare Worker proxy)
 * ──────────────────────────────────────────────
 * The API key is stored as a secret in a Cloudflare Worker environment variable.
 * The browser never sees the key — it sends requests to the Worker URL below,
 * and the Worker forwards them to Groq with the key attached server-side.
 *
 * To update the key: go to the Worker dashboard → Settings → Variables & Secrets.
 * To change the Worker URL: update PROXY_URL below.
 *
 * PROVIDER SWITCHING
 * ──────────────────
 * Change ACTIVE_PROVIDER to "groq", "openrouter", or "ollama".
 * ══════════════════════════════════════════════════════════════════════════════
 */

// ─────────────────────────────────────────────────────────────────────────────
// ██  PROXY CONFIGURATION  ────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Cloudflare Worker URL that proxies requests to Groq.
 * The Worker holds the API key in its environment — it never appears here.
 * Update this if you redeploy the Worker under a different name.
 */
const PROXY_URL = "https://deepdive-llm-proxy.mohamed-salem930.workers.dev";

// ─────────────────────────────────────────────────────────────────────────────
// ██  PROVIDER CONFIGURATION  ─────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

/** Change this one line to switch providers. */
const ACTIVE_PROVIDER = "groq";

const PROVIDERS = {

  groq: {
    label:     "Groq · Llama 3.3",
    // Requests go to the Cloudflare Worker, which forwards to Groq with the key.
    // The browser never sees or sends the API key.
    baseUrl:   PROXY_URL,
    model:     "llama-3.3-70b-versatile",
    keyHeader: "",     // no key header — the Worker handles auth
    keyPrefix: "",
    needsKey:  false,  // key lives in Cloudflare, not here

    buildBody(messages, systemPrompt) {
      return {
        model: this.model,
        messages: [
          { role: "system", content: systemPrompt },
          ...messages,
        ],
        temperature: 0.4,
        max_tokens:  4096,
        stream:      false,
      };
    },

    extractText(data) {
      return data?.choices?.[0]?.message?.content ?? "";
    },
  },

  openrouter: {
    label:     "OpenRouter · Mistral 7B",
    baseUrl:   "https://openrouter.ai/api/v1/chat/completions",
    model:     "mistralai/mistral-7b-instruct:free",
    keyHeader: "Authorization",
    keyPrefix: "Bearer ",
    needsKey:  true,

    buildBody(messages, systemPrompt) {
      return {
        model: this.model,
        messages: [
          { role: "system", content: systemPrompt },
          ...messages,
        ],
        temperature: 0.4,
        max_tokens:  4096,
      };
    },

    extractText(data) {
      return data?.choices?.[0]?.message?.content ?? "";
    },
  },

  ollama: {
    label:     "Ollama · Local",
    baseUrl:   "http://localhost:11434/v1/chat/completions",
    model:     "llama3",
    keyHeader: "",
    keyPrefix: "",
    needsKey:  false,

    buildBody(messages, systemPrompt) {
      return {
        model: this.model,
        messages: [
          { role: "system", content: systemPrompt },
          ...messages,
        ],
        stream: false,
      };
    },

    extractText(data) {
      return data?.choices?.[0]?.message?.content ?? "";
    },
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Local bridge server URL (only used when HARDCODED_API_KEY is empty)
// ─────────────────────────────────────────────────────────────────────────────
const LOCAL_SERVER_URL = "http://localhost:7432";

// ─────────────────────────────────────────────────────────────────────────────
// System prompts
// ─────────────────────────────────────────────────────────────────────────────

/**
 * ONBOARDING SYSTEM PROMPT
 * ─────────────────────────
 * Critical design goals:
 *   1. Ask about directionality explicitly — "what causes what?"
 *   2. Ask about moderators/interactions — "does X change the effect of Y on Z?"
 *   3. Produce a fully connected graph with real edges, never an unconnected node set.
 *   4. Spread nodes across the canvas with varied x/y coordinates (not all at 0,0).
 *   5. The JSON must be emitted in a single line with the DEEPDIVE_DAG_JSON: prefix.
 */
const ONBOARDING_SYSTEM_PROMPT = `
You are DeepDive's onboarding assistant. Help the user build a causal DAG
(Directed Acyclic Graph) through a warm, focused conversation.

CONVERSATION FLOW — follow this exactly, one question at a time:
────────────────────────────────────────────────────────────────
Step 1. Ask what phenomenon or system the user wants to model.
Step 2. Ask what the main outcome variable is (the thing they most want to explain or predict).
Step 3. Ask what variables they think CAUSE or INFLUENCE that outcome.
Step 4. Ask explicitly: "For each cause you mentioned, what direction does the effect go?
        Does A cause B, or does B cause A? Are there any feedback loops we should simplify?"
Step 5. Ask: "Are there any moderators or interactions — situations where the effect of
        one variable on another CHANGES depending on a third variable?"
Step 6. Ask about variable types if not obvious (binary, count, continuous, categorical).
Step 7. When you have enough information (after steps 1-6, or if the user says they're done),
        say: "I have enough to build your DAG! Here it is:"
        Then on THE VERY NEXT LINE, emit ONLY the JSON — nothing before or after it on that line:
        DEEPDIVE_DAG_JSON:<json>

CRITICAL JSON RULES — violations will break the app:
─────────────────────────────────────────────────────
1. The JSON must be valid and complete on a SINGLE LINE immediately after DEEPDIVE_DAG_JSON:
2. Every node MUST appear in at least one edge (no isolated nodes).
3. edges array MUST be non-empty — a DAG with no edges is useless.
4. The graph MUST be acyclic (no cycles, no self-loops).
5. root_ids = node ids with NO incoming edges. leaf_ids = node ids with NO outgoing edges.
6. parents map MUST be consistent with edges: if [A,B] is in edges, then B's parents include A.
7. SPREAD THE NODES across the canvas using varied x and y coordinates:
   - Canvas is roughly 800px wide × 500px tall
   - Place root nodes near the top (y: 60-120), leaves near the bottom (y: 380-460)
   - Spread nodes horizontally so they don't overlap (x values from 80 to 720)
   - No two nodes should have the same x AND y values
   - Example spread for 5 nodes: x values like 100, 250, 400, 550, 650

NODE SCHEMA (each node in the nodes array):
────────────────────────────────────────────
{
  "id": <integer, 1-indexed, unique>,
  "name": <string, max 14 chars, no spaces, snake_case or CamelCase>,
  "emoji": <single emoji character that represents the concept>,
  "x": <integer, 80 to 720>,
  "y": <integer, 60 to 460>,
  "var_type": <"continuous" | "binary" | "ordinal" | "categorical" | "count">
}

FULL JSON SCHEMA:
─────────────────
{
  "nodes": [...],
  "edges": [[src_id, tgt_id], ...],
  "root_ids": [...],
  "leaf_ids": [...],
  "parents": {"<node_id_string>": [<parent_id>, ...], ...},
  "var_types": {"<node_id_string>": "<var_type>", ...},
  "levels": {}
}

EXAMPLE of a valid 3-node DAG (Rain → WetGround → Slip):
DEEPDIVE_DAG_JSON:{"nodes":[{"id":1,"name":"Rain","emoji":"🌧️","x":400,"y":80,"var_type":"binary"},{"id":2,"name":"WetGround","emoji":"💧","x":400,"y":270,"var_type":"continuous"},{"id":3,"name":"Slip","emoji":"🩹","x":400,"y":440,"var_type":"binary"}],"edges":[[1,2],[2,3]],"root_ids":[1],"leaf_ids":[3],"parents":{"1":[],"2":[1],"3":[2]},"var_types":{"1":"binary","2":"continuous","3":"binary"},"levels":{}}

Be warm, encouraging, and concise. Never produce the JSON until you have asked at
least steps 1-4. Never produce an empty edges array.
`.trim();

/**
 * CHAT SYSTEM PROMPT — used after onboarding is complete.
 */
const CHAT_SYSTEM_PROMPT = `
You are DeepDive's causal modelling assistant. The user has built (or is building)
a causal DAG. Help them refine it, understand it, or answer questions about
causal reasoning, DAG structure, confounders, mediators, and moderators.

If the user asks you to rebuild or modify the DAG, emit the updated graph on its
own line using this format (no other text on that line):
DEEPDIVE_DAG_JSON:{"nodes":[...],"edges":[...],"root_ids":[...],"leaf_ids":[...],"parents":{...},"var_types":{...},"levels":{}}

When emitting a DAG:
- ALWAYS include edges (never emit a DAG with an empty edges array)
- Spread nodes across the canvas (x: 80-720, y: 60-460, roots at top, leaves at bottom)
- Make parents map consistent with edges

Keep replies concise and conversational.
`.trim();

/**
 * SINGLE-SHOT TEXT-TO-DAG — used by the quick-generate action.
 */
const TEXT_TO_DAG_SYSTEM_PROMPT = `
Convert the user's description into a causal DAG. Return ONLY the following line and nothing else:
DEEPDIVE_DAG_JSON:{"nodes":[...],"edges":[...],"root_ids":[...],"leaf_ids":[...],"parents":{...},"var_types":{...},"levels":{}}

Node schema: {"id":<int>,"name":<string max 14 chars no spaces>,"emoji":<single emoji>,"x":<80-720>,"y":<60-460>,"var_type":<"continuous"|"binary"|"ordinal"|"categorical"|"count">}

Rules:
- MUST include edges — never return an empty edges array.
- Roots (no parents) go near top (y: 60-120). Leaves (no children) near bottom (y: 380-460).
- Spread nodes horizontally so they don't overlap.
- parents map must be consistent with edges.
- 4-12 nodes. Acyclic. Memorable emoji for every node.
- Return ONLY the DEEPDIVE_DAG_JSON line. No explanation, no markdown.
`.trim();


// ─────────────────────────────────────────────────────────────────────────────
// Module state
// ─────────────────────────────────────────────────────────────────────────────

let _apiKey    = null;
let _keyStatus = "unknown"; // "ok" | "missing" | "error" | "unknown"

/** Resolved provider config. */
const _provider = PROVIDERS[ACTIVE_PROVIDER];


// ─────────────────────────────────────────────────────────────────────────────
// Key management
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Initialise the LLM connection.
 *
 * When using the Cloudflare Worker proxy (needsKey = false), this resolves
 * immediately — no network call needed since the key lives in the Worker.
 *
 * When using a provider that needs a key directly (e.g. Ollama switched to
 * a keyed provider), it falls back to fetching from local_server.py.
 *
 * @param {function} onStatus  callback(message, type)
 */
async function fetchApiKey(onStatus) {
  // Proxy mode — key is held by the Cloudflare Worker, not by us
  if (!_provider.needsKey) {
    _apiKey    = "proxy";   // sentinel value — not a real key
    _keyStatus = "ok";
    onStatus(`Provider: ${_provider.label} ✓`, "ok");
    return;
  }

  // Direct mode — fetch key from local bridge server
  onStatus("Connecting to DeepDive local server…", "info");
  try {
    const res = await fetch(`${LOCAL_SERVER_URL}/gemini_key`, {
      signal: AbortSignal.timeout(3000),
    });
    if (res.ok) {
      const data = await res.json();
      _apiKey    = data.key;
      _keyStatus = "ok";
      onStatus(`Connected ✓  Provider: ${_provider.label}`, "ok");
    } else {
      const err  = await res.json().catch(() => ({}));
      _keyStatus = "missing";
      onStatus(
        `API key not set. ${err.hint || "Set the key env var and restart local_server.py."}`,
        "warn"
      );
    }
  } catch {
    _keyStatus = "error";
    onStatus("LLM unavailable — no API key configured.", "error");
  }
}

/** Returns true if we have a working key and can make LLM calls. */
function llmReady() {
  return _keyStatus === "ok" && !!_apiKey;
}


// ─────────────────────────────────────────────────────────────────────────────
// Core LLM call
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Send a list of messages to the active provider and return the reply string.
 *
 * @param {Array}  messages      [{role, content}, ...]
 * @param {string} systemPrompt
 * @returns {Promise<string|null>}
 */
async function _callLLM(messages, systemPrompt) {
  if (!llmReady()) return null;

  // In proxy mode the Worker handles the Authorization header — we send only Content-Type
  const headers = { "Content-Type": "application/json" };
  if (_provider.needsKey && _provider.keyHeader && _apiKey !== "proxy") {
    headers[_provider.keyHeader] = `${_provider.keyPrefix}${_apiKey}`;
  }

  try {
    const res = await fetch(_provider.baseUrl, {
      method:  "POST",
      headers,
      body:    JSON.stringify(_provider.buildBody(messages, systemPrompt)),
      signal:  AbortSignal.timeout(45_000),   // 45s — large DAG JSON can be slow
    });

    if (!res.ok) {
      const errText = await res.text();
      console.error(`LLM API ${res.status}:`, errText);
      return null;
    }

    return _provider.extractText(await res.json()) || null;

  } catch (e) {
    console.error("LLM fetch error:", e);
    return null;
  }
}


// ─────────────────────────────────────────────────────────────────────────────
// Layout helper — spread nodes across the canvas regardless of LLM coordinates
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Assign good canvas coordinates to every node in the graph.
 * Called whenever the LLM returns all-zero or all-same coordinates.
 *
 * Uses a simple layered layout:
 *   - Compute each node's "layer" = longest path from any root
 *   - Spread layers top (roots) to bottom (leaves)
 *   - Within each layer, spread nodes evenly across the horizontal axis
 *
 * Canvas assumptions: ~820px wide × 520px tall (matches the CSS flex layout).
 *
 * @param {object} graph  - the parsed graph object (mutated in place)
 */
function _spreadNodes(graph) {
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  if (nodes.length === 0) return;

  const CANVAS_W = 820;
  const CANVAS_H = 520;
  const PAD_X    = 80;    // horizontal padding from edge
  const PAD_Y    = 70;    // vertical padding from edge

  // Build children map for topological ordering
  const children = {};
  const inDeg    = {};
  for (const n of nodes) { children[n.id] = []; inDeg[n.id] = 0; }
  for (const [src, tgt] of edges) {
    children[src].push(tgt);
    inDeg[tgt]++;
  }

  // Kahn's topological sort to get layer depths (longest-path ranking)
  const layer = {};
  for (const n of nodes) layer[n.id] = 0;
  const queue = nodes.filter(n => inDeg[n.id] === 0).map(n => n.id);
  const visited = [];
  const inDegCopy = { ...inDeg };
  while (queue.length) {
    const id = queue.shift();
    visited.push(id);
    for (const child of (children[id] || [])) {
      layer[child] = Math.max(layer[child], layer[id] + 1);
      if (--inDegCopy[child] === 0) queue.push(child);
    }
  }
  // If there's a cycle (shouldn't happen but guard anyway), assign remaining nodes to layer 0
  for (const n of nodes) { if (layer[n.id] === undefined) layer[n.id] = 0; }

  // Group nodes by layer
  const byLayer = {};
  for (const n of nodes) {
    const l = layer[n.id];
    if (!byLayer[l]) byLayer[l] = [];
    byLayer[l].push(n.id);
  }
  const layerNums = Object.keys(byLayer).map(Number).sort((a, b) => a - b);
  const numLayers = layerNums.length;

  // Assign pixel coordinates
  for (const l of layerNums) {
    const nodesInLayer = byLayer[l];
    const numInLayer   = nodesInLayer.length;
    const y = numLayers === 1
      ? CANVAS_H / 2
      : PAD_Y + (l / (numLayers - 1)) * (CANVAS_H - 2 * PAD_Y);

    nodesInLayer.forEach((id, i) => {
      const x = numInLayer === 1
        ? CANVAS_W / 2
        : PAD_X + (i / (numInLayer - 1)) * (CANVAS_W - 2 * PAD_X);
      const node = nodes.find(n => n.id === id);
      if (node) {
        node.x = Math.round(x);
        node.y = Math.round(y);
      }
    });
  }
}

/**
 * Returns true if all nodes have x=0 and y=0 (LLM ignored coordinates).
 */
function _allZeroCoords(graph) {
  return (graph.nodes || []).every(n => (n.x === 0 || !n.x) && (n.y === 0 || !n.y));
}


// ─────────────────────────────────────────────────────────────────────────────
// DAG JSON extraction
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Scan an LLM reply for the DEEPDIVE_DAG_JSON: marker, parse and repair the graph.
 *
 * Repairs applied automatically:
 *   - Strips markdown code fences the LLM adds despite being told not to
 *   - Recomputes root_ids, leaf_ids, and parents map from the edges array
 *   - Fills in missing var_types
 *   - Spreads node coordinates if the LLM returned all-zero positions
 *
 * Returns { graph: null, cleanText } if no valid graph is found.
 * NOTE: unlike the previous version, we NO LONGER reject graphs with empty
 * edges here — rejection + retry is handled by the caller (onboardingTurn).
 */
function extractDAGFromReply(replyText) {
  const MARKER = "DEEPDIVE_DAG_JSON:";

  // Strip markdown fences if the LLM wrapped the whole reply
  let text = replyText.trim();
  const fenceMatch = text.match(/```(?:json)?\s*([\s\S]*?)```/s);
  if (fenceMatch && fenceMatch[1].includes(MARKER)) {
    text = fenceMatch[1].trim();
  }

  const idx = text.indexOf(MARKER);
  if (idx === -1) return { graph: null, cleanText: replyText };

  // JSON runs from after the marker to the first newline
  const afterMarker = text.slice(idx + MARKER.length).trim();
  const jsonStr     = afterMarker.split("\n")[0].trim();
  const cleanText   = text.slice(0, idx).trim();

  let graph;
  try {
    graph = JSON.parse(jsonStr);
  } catch (e) {
    console.error("DAG JSON parse failed:", jsonStr, e);
    return { graph: null, cleanText: replyText };
  }

  if (!graph.nodes || graph.nodes.length === 0) {
    return { graph: null, cleanText: replyText };
  }

  // ── Repair: ensure edges array exists ──────────────────────────────────────
  if (!graph.edges) graph.edges = [];

  // ── Repair: recompute root_ids, leaf_ids, parents from edges ───────────────
  // (LLM frequently gets these wrong even when edges are correct)
  const allIds    = graph.nodes.map(n => n.id);
  const hasParent = new Set(graph.edges.map(([, t]) => t));
  const hasChild  = new Set(graph.edges.map(([s]) => s));
  graph.root_ids  = allIds.filter(id => !hasParent.has(id));
  graph.leaf_ids  = allIds.filter(id => !hasChild.has(id));

  graph.parents = {};
  for (const id of allIds) graph.parents[String(id)] = [];
  for (const [src, tgt] of graph.edges) {
    const k = String(tgt);
    if (!graph.parents[k]) graph.parents[k] = [];
    if (!graph.parents[k].includes(src)) graph.parents[k].push(src);
  }

  // ── Repair: fill missing var_types ─────────────────────────────────────────
  if (!graph.var_types) graph.var_types = {};
  for (const node of graph.nodes) {
    if (!graph.var_types[String(node.id)]) {
      graph.var_types[String(node.id)] = node.var_type || "continuous";
    }
  }

  // ── Repair: spread coordinates if LLM ignored the layout instructions ──────
  // This is the primary fix for "all nodes in top-left corner"
  if (_allZeroCoords(graph)) {
    _spreadNodes(graph);
  }

  if (!graph.levels) graph.levels = {};

  return { graph, cleanText };
}


// ─────────────────────────────────────────────────────────────────────────────
// Onboarding conversation
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Send one turn of the guided onboarding conversation.
 * Mutates history in place.
 *
 * @param {Array}  history
 * @param {string} userMessage
 * @returns {Promise<{ reply: string, graph: object|null }>}
 */
async function onboardingTurn(history, userMessage) {
  history.push({ role: "user", content: userMessage });

  const raw = await _callLLM(history, ONBOARDING_SYSTEM_PROMPT);

  if (!raw) {
    const fallback = "Sorry, I couldn't reach the LLM. Check your connection and try again.";
    history.push({ role: "assistant", content: fallback });
    return { reply: fallback, graph: null };
  }

  const { graph, cleanText } = extractDAGFromReply(raw);

  // ── Auto-retry if the LLM produced nodes but forgot edges ──────────────────
  // This is the primary fix for "DAG has no edges".
  // We inject a correction into the history and call the LLM one more time.
  if (graph && graph.edges.length === 0 && graph.nodes.length > 0) {
    console.warn("LLM returned no edges — sending correction and retrying.");

    // Add the bad response to history so the LLM has context
    history.push({ role: "assistant", content: cleanText || raw });

    const correction =
      "Your DAG JSON had no edges — every node was disconnected. " +
      "Please regenerate the DEEPDIVE_DAG_JSON with edges connecting the nodes " +
      "based on the causal relationships we discussed. " +
      "The edges array must not be empty.";

    history.push({ role: "user", content: correction });

    const raw2 = await _callLLM(history, ONBOARDING_SYSTEM_PROMPT);
    if (raw2) {
      const retry = extractDAGFromReply(raw2);
      // Apply layout if still all-zero after retry
      if (retry.graph && _allZeroCoords(retry.graph)) _spreadNodes(retry.graph);
      history.push({ role: "assistant", content: retry.cleanText || raw2 });
      return { reply: retry.cleanText || raw2, graph: retry.graph };
    }
  }

  // Normal path — graph is fine (or null, meaning no JSON in this turn yet)
  history.push({ role: "assistant", content: cleanText || raw });
  return { reply: cleanText || raw, graph };
}


// ─────────────────────────────────────────────────────────────────────────────
// General chat
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Send one turn of the general post-onboarding chat.
 *
 * @param {Array}  history
 * @param {string} userMessage
 * @returns {Promise<{ reply: string, graph: object|null }>}
 */
async function chatTurn(history, userMessage) {
  history.push({ role: "user", content: userMessage });

  const raw = await _callLLM(history, CHAT_SYSTEM_PROMPT);

  if (!raw) {
    const fallback = "LLM unavailable. Check your connection.";
    history.push({ role: "assistant", content: fallback });
    return { reply: fallback, graph: null };
  }

  const { graph, cleanText } = extractDAGFromReply(raw);

  // Auto-retry if the LLM produced a DAG with no edges
  if (graph && graph.edges.length === 0 && graph.nodes.length > 0) {
    console.warn("Chat LLM returned no edges — retrying with correction.");
    history.push({ role: "assistant", content: cleanText || raw });
    history.push({
      role: "user",
      content:
        "Your DAG had no edges. Please regenerate it with edges connecting the nodes. " +
        "The edges array must not be empty.",
    });
    const raw2 = await _callLLM(history, CHAT_SYSTEM_PROMPT);
    if (raw2) {
      const retry = extractDAGFromReply(raw2);
      if (retry.graph && _allZeroCoords(retry.graph)) _spreadNodes(retry.graph);
      history.push({ role: "assistant", content: retry.cleanText || raw2 });
      return { reply: retry.cleanText || raw2, graph: retry.graph };
    }
  }

  history.push({ role: "assistant", content: cleanText || raw });
  return { reply: cleanText || raw, graph };
}


// ─────────────────────────────────────────────────────────────────────────────
// Single-shot text → DAG
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Convert a plain-English description to a DAG in one LLM call.
 *
 * @param {string}   text
 * @param {function} onProgress  callback(message, type)
 * @returns {Promise<object|null>}
 */
async function textToDAG(text, onProgress) {
  if (!llmReady()) { onProgress("LLM not ready.", "error"); return null; }
  if (!text.trim()) { onProgress("Enter a description first.", "warn"); return null; }

  onProgress("Generating DAG…", "info");

  const raw = await _callLLM(
    [{ role: "user", content: text }],
    TEXT_TO_DAG_SYSTEM_PROMPT
  );

  if (!raw) { onProgress("No response from LLM. Try again.", "error"); return null; }

  const { graph } = extractDAGFromReply(raw);
  if (!graph) {
    onProgress("Could not parse a valid DAG. Try rephrasing.", "warn");
    return null;
  }

  onProgress(`DAG generated: ${graph.nodes?.length} nodes, ${graph.edges?.length} edges ✓`, "ok");
  return graph;
}


// ─────────────────────────────────────────────────────────────────────────────
// Automatic emoji assignment
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Ask the LLM for a single emoji representing a node concept.
 * Called automatically when a node is named. Silent fallback if LLM unavailable.
 *
 * @param {string} nodeName
 * @returns {Promise<string>}
 */
async function autoEmoji(nodeName) {
  if (!llmReady()) return "◈";

  const raw = await _callLLM(
    [{
      role: "user",
      content:
        `Pick exactly one emoji that visually represents "${nodeName}" in a causal model. ` +
        `Return ONLY the single emoji character — nothing else.`,
    }],
    "You are an emoji selector. Return exactly one emoji character and nothing else."
  );

  if (!raw) return "◈";

  // Extract the first proper emoji, guard against stray text
  const match = raw.trim().match(/\p{Emoji_Presentation}|\p{Emoji}\uFE0F/u);
  return match ? match[0] : (raw.trim().slice(0, 2) || "◈");
}


// ─────────────────────────────────────────────────────────────────────────────
// Exports
// ─────────────────────────────────────────────────────────────────────────────

window.DeepDiveLLM = {
  fetchApiKey,
  llmReady,
  onboardingTurn,
  chatTurn,
  textToDAG,
  autoEmoji,
  extractDAGFromReply,
  providerLabel:    _provider.label,
  LOCAL_SERVER_URL,
};
