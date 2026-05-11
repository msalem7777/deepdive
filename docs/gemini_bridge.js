/**
 * gemini_bridge.js
 * ══════════════════════════════════════════════════════════════════════════════
 * DeepDive — LLM bridge module.
 *
 * KEY MODE: Cloudflare Worker proxy.
 * The API key lives in the Worker's environment — never in this file.
 * Update PROXY_URL if you redeploy the Worker.
 *
 * PROVIDER SWITCHING
 * ──────────────────
 * Change ACTIVE_PROVIDER to "groq", "openrouter", or "ollama".
 * ══════════════════════════════════════════════════════════════════════════════
 */

// ─────────────────────────────────────────────────────────────────────────────
// ██  PROXY + PROVIDER CONFIGURATION  ─────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Cloudflare Worker URL — holds the Groq API key server-side.
 * The browser sends requests here; the Worker forwards them to Groq with auth.
 */
const PROXY_URL = "https://deepdive-llm-proxy.mohamed-salem930.workers.dev";

/** Change this one line to switch LLM providers. */
const ACTIVE_PROVIDER = "groq";

const PROVIDERS = {

  groq: {
    label:     "Groq · Llama 3.3",
    baseUrl:   PROXY_URL,          // proxied — key is in the Worker
    model:     "llama-3.3-70b-versatile",
    keyHeader: "",                 // Worker adds auth header
    keyPrefix: "",
    needsKey:  false,              // key is server-side

    buildBody(messages, systemPrompt, tools) {
      const body = {
        model: this.model,
        messages: [
          { role: "system", content: systemPrompt },
          ...messages,
        ],
        temperature: 0.4,
        max_tokens:  4096,
        stream:      false,
      };
      // Attach Groq web-search tool definition if requested
      if (tools) body.tools = tools;
      return body;
    },

    extractText(data) {
      // Handle both standard text replies and tool-use replies
      const msg = data?.choices?.[0]?.message;
      if (!msg) return "";
      // If the model used a tool, we get tool_calls — but for web search
      // Groq returns the final answer in content after tool resolution.
      return msg.content ?? "";
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
// Local bridge server (only used if PROXY_URL is empty and needsKey=true)
// ─────────────────────────────────────────────────────────────────────────────
const LOCAL_SERVER_URL = "http://localhost:7432";

// ─────────────────────────────────────────────────────────────────────────────
// Groq web-search tool definition
// ─────────────────────────────────────────────────────────────────────────────
/**
 * Groq's compound-beta model supports a built-in web_search tool.
 * We use llama-3.3-70b-versatile with tools enabled for literature searches.
 * When tools are passed, Groq may call web_search and return enriched results.
 */
const WEB_SEARCH_TOOL = {
  type: "function",
  function: {
    name:        "web_search",
    description: "Search the web for recent research, papers, and evidence.",
    parameters: {
      type: "object",
      properties: {
        query: {
          type:        "string",
          description: "The search query to run.",
        },
      },
      required: ["query"],
    },
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// ██  SYSTEM PROMPTS  ─────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
// All prompts are written in plain English — no jargon like "DAG", "node",
// "acyclic", or "root". The output JSON still uses those field names internally
// because the code depends on them, but the user never sees that language.

/**
 * ONBOARDING PROMPT
 * ──────────────────
 * Used during the guided setup conversation.
 * Adjusted dynamically to include dataset headers when a file has been uploaded.
 *
 * @param {string[]} headers  — column names from the uploaded dataset (may be empty)
 */
function buildOnboardingPrompt(headers) {
  const hasDataset = headers && headers.length > 0;
  const headerList = hasDataset
    ? `\n\nThe user has uploaded a dataset with these columns:\n${headers.map(h => `  • ${h}`).join("\n")}\nUse these as the variables in the process map wherever they fit.`
    : "";

  return `
You are DeepDive's friendly setup assistant. Your job is to help the user build
a visual map of causes and effects — a diagram that shows how different factors
in their situation connect to and influence each other.

Do NOT use technical terms like "DAG", "node", "edge", "acyclic", "root", or
"leaf". Instead use plain words:
  - "variable" or "factor" instead of node
  - "connection" or "arrow" instead of edge
  - "starting factor" instead of root node
  - "outcome" instead of leaf node
  - "cause-and-effect map" or "process map" instead of DAG${headerList}

CONVERSATION FLOW — ask ONE question at a time, be warm and concise:
──────────────────────────────────────────────────────────────────────
${hasDataset
  ? `Step 1. Tell the user you've seen their dataset and list the columns. Ask which column is the outcome they most care about predicting or explaining.
Step 2. Ask what other factors in the dataset they think influence that outcome.
Step 3. Ask about the direction of each influence — does factor A affect factor B, or is it the other way around?
Step 4. Ask if any factor changes HOW another factor affects the outcome (e.g. "does age change how exercise affects health?").
Step 5. Ask if there are important factors NOT in the dataset that should still appear in the map.`
  : `Step 1. Ask what process, situation, or question the user wants to map out.
Step 2. Ask what the main outcome or result they care about is.
Step 3. Ask what factors cause or influence that outcome.
Step 4. Ask about direction — does factor A affect factor B, or vice versa? Any two-way relationships to simplify?
Step 5. Ask if any factor changes HOW another factor affects the outcome.
Step 6. Ask about the nature of each factor — is it something measured as a number, a yes/no, categories, or a count?`}

When you have enough information (after the steps above, or if the user says
they're ready), say: "I have enough to build your map! Here it is:"
Then on THE VERY NEXT LINE emit ONLY this — nothing else on that line:
DEEPDIVE_DAG_JSON:<json>

CRITICAL RULES FOR THE JSON — breaking these will break the app:
────────────────────────────────────────────────────────────────
1. JSON on a SINGLE LINE immediately after DEEPDIVE_DAG_JSON:
2. edges array MUST be non-empty — every factor must connect to at least one other.
3. No cycles (A→B→A is not allowed). No self-loops (A→A).
4. root_ids = ids of factors with no incoming connections.
5. leaf_ids  = ids of factors with no outgoing connections (the outcomes).
6. parents map: if edge [A,B] exists, then B's parents include A.
7. SPREAD FACTORS across the canvas:
   - Canvas: ~820px wide × 520px tall
   - Starting factors (root_ids) near top: y between 60–120
   - Outcome factors (leaf_ids) near bottom: y between 380–460
   - Middle factors spread vertically in between
   - Spread horizontally: x between 80–740, no two factors at the same position

FACTOR (NODE) SCHEMA:
{ "id":<int 1-indexed>, "name":<string max 14 chars no spaces>,
  "emoji":<single fun emoji>, "x":<80-740>, "y":<60-460>,
  "var_type":<"continuous"|"binary"|"ordinal"|"categorical"|"count"> }

FULL JSON SCHEMA:
{ "nodes":[...], "edges":[[src_id,tgt_id],...],
  "root_ids":[...], "leaf_ids":[...],
  "parents":{"<id>":[parent_ids],...},
  "var_types":{"<id>":"var_type",...}, "levels":{} }

EXAMPLE — Rain causes wet ground, which causes slipping:
DEEPDIVE_DAG_JSON:{"nodes":[{"id":1,"name":"Rain","emoji":"🌧️","x":400,"y":80,"var_type":"binary"},{"id":2,"name":"WetGround","emoji":"💧","x":250,"y":270,"var_type":"continuous"},{"id":3,"name":"Slip","emoji":"🩹","x":400,"y":440,"var_type":"binary"}],"edges":[[1,2],[2,3]],"root_ids":[1],"leaf_ids":[3],"parents":{"1":[],"2":[1],"3":[2]},"var_types":{"1":"binary","2":"continuous","3":"binary"},"levels":{}}

Be warm, encouraging, and non-technical. Never emit the JSON until you have
collected enough information through conversation. Never leave edges empty.
`.trim();
}

/**
 * CHAT PROMPT — used after the map is built.
 * @param {string[]} headers  — dataset column names (may be empty)
 */
function buildChatPrompt(headers) {
  const headerCtx = headers?.length
    ? `\n\nThe user's dataset columns are: ${headers.join(", ")}.`
    : "";

  return `
You are DeepDive's cause-and-effect mapping assistant. The user has built (or
is building) a visual map of how factors connect and influence each other.${headerCtx}

Speak in plain English — avoid jargon like "DAG", "node", "acyclic", "root node".
Use "factor" or "variable" instead of node, "connection" instead of edge,
"outcome" instead of leaf, "starting factor" instead of root.

You can help the user:
- Refine their map by adding or removing connections
- Understand what the map means
- Answer questions about cause and effect, confounders, and feedback
- Search the research literature for evidence about their factors (use web search)

If the user asks you to change or rebuild the map, emit the updated version:
DEEPDIVE_DAG_JSON:{"nodes":[...],"edges":[...],"root_ids":[...],"leaf_ids":[...],"parents":{...},"var_types":{...},"levels":{}}

Rules when emitting a map:
- edges must be non-empty
- Spread factors across the canvas (x: 80-740, y: 60-460, outcomes near bottom)
- parents map must match edges

Keep replies concise and friendly.
`.trim();
}

/**
 * LITERATURE SEARCH PROMPT
 * Instructs the LLM to use web search to find evidence for each variable's
 * relationship to the chosen outcome, then classify them.
 *
 * @param {string[]} headers      — all dataset columns
 * @param {string}   responseVar  — the chosen outcome variable
 */
function buildLiteraturePrompt(headers, responseVar) {
  const otherVars = headers.filter(h => h !== responseVar);
  return `
The user has a dataset with these columns: ${headers.join(", ")}.
Their outcome of interest is: "${responseVar}".
The other variables are: ${otherVars.join(", ")}.

Use your web search capability to find research evidence for whether each of
the other variables is associated with or causes "${responseVar}".

For each variable, search for recent research, meta-analyses, or established
findings. Then produce a plain-English summary structured as follows:

**Variables found in your dataset with research support:**
List each variable in the dataset (other than ${responseVar}) that has solid
research evidence linking it to ${responseVar}. For each, write 1-2 sentences
describing what the evidence says.

**Variables in your dataset with limited or mixed evidence:**
List any dataset variables where the research is unclear or mixed.

**Important factors NOT in your dataset:**
Based on the research you find, list factors that are well-established as
influences on ${responseVar} but are missing from the dataset. Suggest adding
them to the cause-and-effect map.

Be concise, plain, and non-technical. Avoid jargon. Do not produce any JSON.
This is purely a research summary for the user to read.
`.trim();
}


// ─────────────────────────────────────────────────────────────────────────────
// Module state
// ─────────────────────────────────────────────────────────────────────────────

let _apiKey    = null;
let _keyStatus = "unknown";

/**
 * Dataset state — persists for the whole session.
 * Set by parseAndStoreDataset(), read by prompts and literature search.
 */
let _datasetHeaders  = [];    // string[] — column names
let _datasetFilename = "";    // display name shown in chat

const _provider = PROVIDERS[ACTIVE_PROVIDER];


// ─────────────────────────────────────────────────────────────────────────────
// Key management
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Initialise the LLM connection.
 * Proxy mode resolves immediately — no network call needed.
 * Direct mode falls back to local_server.py.
 *
 * @param {function} onStatus  callback(message, type)
 */
async function fetchApiKey(onStatus) {
  if (!_provider.needsKey) {
    _apiKey    = "proxy";
    _keyStatus = "ok";
    onStatus(`Provider: ${_provider.label} ✓`, "ok");
    return;
  }

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
      _keyStatus = "missing";
      onStatus("API key not configured on server.", "warn");
    }
  } catch {
    _keyStatus = "error";
    onStatus("LLM unavailable — no API key configured.", "error");
  }
}

function llmReady() {
  return _keyStatus === "ok" && !!_apiKey;
}


// ─────────────────────────────────────────────────────────────────────────────
// Dataset parsing
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Parse a File object (CSV or XLSX) and extract the header row.
 * Stores headers in module state for use by prompts.
 *
 * CSV: parsed natively in the browser using string splitting — no library needed.
 * XLSX: parsed using SheetJS (loaded via CDN in index.html).
 *
 * @param {File}     file       — the uploaded File object
 * @param {function} onStatus   — callback(message, type)
 * @returns {Promise<string[]>} — array of column header strings
 */
async function parseAndStoreDataset(file, onStatus) {
  onStatus("Reading your file…", "info");

  let headers = [];

  try {
    const ext = file.name.split(".").pop().toLowerCase();

    if (ext === "csv") {
      // ── CSV: read as text, split on first line ────────────────────────────
      const text  = await file.text();
      const lines = text.split(/\r?\n/).filter(l => l.trim());
      if (lines.length === 0) throw new Error("File appears to be empty.");

      // Detect delimiter: comma, semicolon, or tab
      const firstLine = lines[0];
      const delim = firstLine.includes("\t") ? "\t"
                  : firstLine.includes(";")  ? ";"
                  : ",";

      // Parse headers — handle quoted fields
      headers = _parseCSVRow(firstLine, delim);

    } else if (ext === "xlsx" || ext === "xls") {
      // ── XLSX: use SheetJS (window.XLSX must be loaded) ────────────────────
      if (!window.XLSX) {
        throw new Error("SheetJS not loaded. Check your internet connection.");
      }
      const arrayBuffer = await file.arrayBuffer();
      const workbook    = window.XLSX.read(arrayBuffer, { type: "array" });
      const firstSheet  = workbook.Sheets[workbook.SheetNames[0]];
      const rows        = window.XLSX.utils.sheet_to_json(firstSheet, { header: 1 });
      if (!rows || rows.length === 0) throw new Error("Spreadsheet appears to be empty.");
      headers = rows[0].map(h => String(h).trim()).filter(Boolean);

    } else {
      throw new Error(`Unsupported file type ".${ext}". Please upload a CSV or XLSX file.`);
    }

    if (headers.length === 0) throw new Error("Could not find any column headers.");

    // Store in module state — persists for the whole session
    _datasetHeaders  = headers;
    _datasetFilename = file.name;

    onStatus(`Loaded "${file.name}" — ${headers.length} columns found.`, "ok");
    return headers;

  } catch (e) {
    onStatus(`Could not read file: ${e.message}`, "error");
    return [];
  }
}

/**
 * Parse a single CSV row, respecting quoted fields.
 * e.g.  `"Name","Age","City, State"` → ["Name", "Age", "City, State"]
 */
function _parseCSVRow(row, delim = ",") {
  const result = [];
  let current  = "";
  let inQuotes = false;

  for (let i = 0; i < row.length; i++) {
    const ch = row[i];
    if (ch === '"') {
      inQuotes = !inQuotes;
    } else if (ch === delim && !inQuotes) {
      result.push(current.trim());
      current = "";
    } else {
      current += ch;
    }
  }
  result.push(current.trim());
  return result.filter(Boolean);
}

/** Return the currently stored dataset headers (empty array if none uploaded). */
function getDatasetHeaders() { return _datasetHeaders; }

/** Return the uploaded filename for display. */
function getDatasetFilename() { return _datasetFilename; }


// ─────────────────────────────────────────────────────────────────────────────
// Core LLM call
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Send messages to the active provider.
 *
 * @param {Array}   messages      [{role, content}, ...]
 * @param {string}  systemPrompt
 * @param {Array}   tools         optional Groq tool definitions
 * @returns {Promise<string|null>}
 */
async function _callLLM(messages, systemPrompt, tools = null) {
  if (!llmReady()) return null;

  const headers = { "Content-Type": "application/json" };
  if (_provider.needsKey && _provider.keyHeader && _apiKey !== "proxy") {
    headers[_provider.keyHeader] = `${_provider.keyPrefix}${_apiKey}`;
  }

  try {
    const res = await fetch(_provider.baseUrl, {
      method:  "POST",
      headers,
      body:    JSON.stringify(_provider.buildBody(messages, systemPrompt, tools)),
      signal:  AbortSignal.timeout(60_000),   // 60s — web search replies take longer
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
// Layout helper
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Spread nodes across the canvas using a layered layout.
 * Called whenever the LLM returns all-zero coordinates.
 * Mutates graph.nodes in place.
 */
function _spreadNodes(graph) {
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  if (nodes.length === 0) return;

  const CW = 820, CH = 520, PX = 80, PY = 70;

  // Build in-degree and children maps
  const inDeg    = {}, children = {};
  for (const n of nodes) { inDeg[n.id] = 0; children[n.id] = []; }
  for (const [s, t] of edges) { children[s].push(t); inDeg[t]++; }

  // Longest-path layer assignment via topological sort
  const layer = {};
  for (const n of nodes) layer[n.id] = 0;
  const queue = nodes.filter(n => inDeg[n.id] === 0).map(n => n.id);
  const inDegCopy = { ...inDeg };
  while (queue.length) {
    const id = queue.shift();
    for (const c of children[id]) {
      layer[c] = Math.max(layer[c], layer[id] + 1);
      if (--inDegCopy[c] === 0) queue.push(c);
    }
  }

  // Group by layer
  const byLayer = {};
  for (const n of nodes) {
    const l = layer[n.id] ?? 0;
    if (!byLayer[l]) byLayer[l] = [];
    byLayer[l].push(n.id);
  }
  const layerNums = Object.keys(byLayer).map(Number).sort((a, b) => a - b);
  const nL = layerNums.length;

  // Assign pixel positions
  for (const l of layerNums) {
    const ids = byLayer[l];
    const nN  = ids.length;
    const y   = nL === 1 ? CH / 2 : PY + (l / (nL - 1)) * (CH - 2 * PY);
    ids.forEach((id, i) => {
      const x    = nN === 1 ? CW / 2 : PX + (i / (nN - 1)) * (CW - 2 * PX);
      const node = nodes.find(n => n.id === id);
      if (node) { node.x = Math.round(x); node.y = Math.round(y); }
    });
  }
}

function _allZeroCoords(graph) {
  return (graph.nodes || []).every(n => (!n.x || n.x === 0) && (!n.y || n.y === 0));
}


// ─────────────────────────────────────────────────────────────────────────────
// DAG JSON extraction
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Find the DEEPDIVE_DAG_JSON: marker in an LLM reply and return the parsed graph.
 * Repairs root_ids, leaf_ids, parents, var_types automatically.
 * Spreads coordinates if all-zero.
 * Does NOT reject edgeless graphs here — callers handle that with a retry.
 */
function extractDAGFromReply(replyText) {
  const MARKER = "DEEPDIVE_DAG_JSON:";

  let text = replyText.trim();
  // Strip markdown fences the LLM sometimes wraps around the JSON
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/s);
  if (fence && fence[1].includes(MARKER)) text = fence[1].trim();

  const idx = text.indexOf(MARKER);
  if (idx === -1) return { graph: null, cleanText: replyText };

  const jsonStr   = text.slice(idx + MARKER.length).trim().split("\n")[0].trim();
  const cleanText = text.slice(0, idx).trim();

  let graph;
  try { graph = JSON.parse(jsonStr); }
  catch (e) {
    console.error("DAG JSON parse failed:", jsonStr, e);
    return { graph: null, cleanText: replyText };
  }

  if (!graph.nodes?.length) return { graph: null, cleanText: replyText };
  if (!graph.edges) graph.edges = [];

  // Repair topology fields
  const allIds    = graph.nodes.map(n => n.id);
  const hasParent = new Set(graph.edges.map(([, t]) => t));
  const hasChild  = new Set(graph.edges.map(([s]) => s));
  graph.root_ids  = allIds.filter(id => !hasParent.has(id));
  graph.leaf_ids  = allIds.filter(id => !hasChild.has(id));
  graph.parents   = {};
  for (const id of allIds) graph.parents[String(id)] = [];
  for (const [s, t] of graph.edges) {
    const k = String(t);
    if (!graph.parents[k]) graph.parents[k] = [];
    if (!graph.parents[k].includes(s)) graph.parents[k].push(s);
  }

  if (!graph.var_types) graph.var_types = {};
  for (const n of graph.nodes) {
    if (!graph.var_types[String(n.id)])
      graph.var_types[String(n.id)] = n.var_type || "continuous";
  }

  if (_allZeroCoords(graph)) _spreadNodes(graph);
  if (!graph.levels) graph.levels = {};

  return { graph, cleanText };
}


// ─────────────────────────────────────────────────────────────────────────────
// Onboarding conversation
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Send one turn of the guided onboarding conversation.
 * Auto-retries once if the LLM returns a map with no connections.
 *
 * @param {Array}  history
 * @param {string} userMessage
 * @returns {Promise<{ reply: string, graph: object|null }>}
 */
async function onboardingTurn(history, userMessage) {
  history.push({ role: "user", content: userMessage });

  const systemPrompt = buildOnboardingPrompt(_datasetHeaders);
  const raw = await _callLLM(history, systemPrompt);

  if (!raw) {
    const fb = "Sorry, I couldn't reach the AI. Check your connection and try again.";
    history.push({ role: "assistant", content: fb });
    return { reply: fb, graph: null };
  }

  const { graph, cleanText } = extractDAGFromReply(raw);

  // Auto-retry if connections are missing
  if (graph && graph.edges.length === 0) {
    console.warn("No connections in map — retrying with correction.");
    history.push({ role: "assistant", content: cleanText || raw });
    history.push({
      role: "user",
      content:
        "The map you generated had no connections between factors. " +
        "Please regenerate it with arrows showing which factors influence which others. " +
        "The connections list must not be empty.",
    });
    const raw2 = await _callLLM(history, systemPrompt);
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

  const systemPrompt = buildChatPrompt(_datasetHeaders);
  const raw = await _callLLM(history, systemPrompt);

  if (!raw) {
    const fb = "AI unavailable. Check your connection.";
    history.push({ role: "assistant", content: fb });
    return { reply: fb, graph: null };
  }

  const { graph, cleanText } = extractDAGFromReply(raw);

  // Retry if connections missing
  if (graph && graph.edges.length === 0) {
    history.push({ role: "assistant", content: cleanText || raw });
    history.push({
      role: "user",
      content: "The map had no connections. Please regenerate with arrows connecting the factors.",
    });
    const raw2 = await _callLLM(history, systemPrompt);
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
// Literature / evidence search
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Use Groq's web search tool to find research evidence for each dataset variable
 * relative to the chosen outcome, then return a structured plain-English summary.
 *
 * This is a single-shot call (not part of the chat history) so it doesn't
 * pollute the conversation context.
 *
 * @param {string}   responseVar  — the chosen outcome column name
 * @param {function} onProgress   — callback(message, type)
 * @returns {Promise<string|null>}  the summary text, or null on failure
 */
async function searchLiterature(responseVar, onProgress) {
  if (!llmReady()) {
    onProgress("AI not available for literature search.", "error");
    return null;
  }
  if (!_datasetHeaders.length) {
    onProgress("No dataset uploaded — please upload a file first.", "warn");
    return null;
  }

  onProgress("Searching research literature… this may take 20–30 seconds.", "info");

  const systemPrompt = buildLiteraturePrompt(_datasetHeaders, responseVar);
  const messages = [{
    role:    "user",
    content: `Please search the research literature for evidence about what influences "${responseVar}" and summarise what you find in relation to the variables in my dataset.`,
  }];

  // Pass the web_search tool so Groq can perform live searches
  const raw = await _callLLM(messages, systemPrompt, [WEB_SEARCH_TOOL]);

  if (!raw) {
    onProgress("Literature search failed. Try again.", "error");
    return null;
  }

  onProgress("Literature search complete ✓", "ok");
  return raw;
}


// ─────────────────────────────────────────────────────────────────────────────
// Auto emoji
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Ask the LLM for a single emoji for a variable name.
 * Called automatically when a node is named. Silent fallback if unavailable.
 *
 * @param {string} nodeName
 * @returns {Promise<string>}
 */
async function autoEmoji(nodeName) {
  if (!llmReady()) return "◈";

  const raw = await _callLLM(
    [{ role: "user", content:
        `Pick exactly one emoji that represents the concept "${nodeName}" ` +
        `as a variable in a data analysis. Return ONLY the emoji, nothing else.` }],
    "You are an emoji selector. Return exactly one emoji character and nothing else."
  );

  if (!raw) return "◈";
  const match = raw.trim().match(/\p{Emoji_Presentation}|\p{Emoji}\uFE0F/u);
  return match ? match[0] : (raw.trim().slice(0, 2) || "◈");
}


// ─────────────────────────────────────────────────────────────────────────────
// Dataset upload to local server
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Upload a dataset file to the local DeepDive server.
 *
 * Sends a multipart/form-data POST to /upload containing:
 *   file              — the raw file binary
 *   response_variable — the column name of the response variable
 *   dag               — the current DAG JSON (stringified), if available
 *
 * The server saves the file, launches analyze.py, and returns 202 Accepted.
 * The browser then polls /upload_status until the job completes.
 *
 * @param {File}     file          — the File object from the file input / drop
 * @param {string}   responseVar   — column name chosen by the user
 * @param {object}   dagGraph      — current graph dict (may be null)
 * @param {function} onProgress    — callback(message, type)
 * @returns {Promise<{ok: boolean, timestamp: string|null}>}
 */
async function uploadDataset(file, responseVar, dagGraph, onProgress) {
  if (!file) {
    onProgress("No file to upload.", "warn");
    return { ok: false, timestamp: null };
  }
  if (!responseVar) {
    onProgress("Please identify the response variable before uploading.", "warn");
    return { ok: false, timestamp: null };
  }

  onProgress(`Uploading "${file.name}" to local server…`, "info");

  const formData = new FormData();
  formData.append("file",              file);
  formData.append("response_variable", responseVar);
  if (dagGraph) {
    formData.append("dag", JSON.stringify(dagGraph));
  }

  try {
    const res = await fetch(`${LOCAL_SERVER_URL}/upload`, {
      method: "POST",
      body:   formData,
      // Note: do NOT set Content-Type header — the browser sets it automatically
      // with the correct multipart boundary when using FormData.
      signal: AbortSignal.timeout(60_000),   // 60s for large files
    });

    const data = await res.json();

    if (res.ok) {
      onProgress(
        `File received by server ✓  Analysis started for "${responseVar}".`,
        "ok"
      );
      return { ok: true, timestamp: data.timestamp };
    } else {
      const msg = data.error || "Server rejected the upload.";
      onProgress(`Upload failed: ${msg}`, "error");
      return { ok: false, timestamp: null };
    }

  } catch (e) {
    onProgress(
      "Could not reach local server. Is local_server.py running?",
      "error"
    );
    return { ok: false, timestamp: null };
  }
}


/**
 * Poll /upload_status until the analysis job finishes or errors.
 *
 * Calls onUpdate with the latest status object every 3 seconds.
 * Resolves when status is "complete" or "error".
 *
 * @param {function} onUpdate  callback(statusObject) — called each poll cycle
 * @returns {Promise<object>}  the final status object
 */
async function pollAnalysisStatus(onUpdate) {
  return new Promise((resolve) => {
    const interval = setInterval(async () => {
      try {
        const res  = await fetch(`${LOCAL_SERVER_URL}/upload_status`, {
          signal: AbortSignal.timeout(5000),
        });
        const data = await res.json();
        onUpdate(data);

        if (data.status === "complete" || data.status === "error") {
          clearInterval(interval);
          resolve(data);
        }
      } catch {
        // Server temporarily unreachable — keep polling
      }
    }, 3000);   // poll every 3 seconds
  });
}

window.DeepDiveLLM = {
  // Connection
  fetchApiKey,
  llmReady,

  // Dataset — parsing (browser-side)
  parseAndStoreDataset,
  getDatasetHeaders,
  getDatasetFilename,

  // Dataset — server upload + polling
  uploadDataset,
  pollAnalysisStatus,

  // Conversation
  onboardingTurn,
  chatTurn,

  // Features
  searchLiterature,
  autoEmoji,
  extractDAGFromReply,

  // Metadata
  providerLabel:    _provider.label,
  LOCAL_SERVER_URL,
};
