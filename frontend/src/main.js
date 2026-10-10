const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";
// Vite `base: "./"` — a leading slash breaks GitHub Pages (`/repo/gemini.svg`).
const GEMINI_ICON = `${import.meta.env.BASE_URL}gemini.svg`;

const form = document.getElementById("search-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const recsEl = document.getElementById("recommendations");
const rerankInput = document.getElementById("rerank");
const llmInput = document.getElementById("llm");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = document.getElementById("query").value.trim();
  const rerank = rerankInput.checked;
  const useLlm = llmInput.checked;

  statusEl.textContent = "Searching…";
  resultsEl.innerHTML = "";
  recsEl.innerHTML = "";

  const clientStarted = performance.now();

  try {
    // LLM checked → RAG + Gemini. Otherwise classic search; Rerank only affects ranking.
    if (useLlm) {
      await runRag(query, rerank, clientStarted);
      return;
    }

    await runClassicSearch(query, rerank, clientStarted);
  } catch (error) {
    const clientMs = performance.now() - clientStarted;
    const detail = error instanceof Error ? error.message : "Search failed";
    statusEl.textContent = `${detail}. Request failed after ${formatDuration(clientMs)}.`;
  }
});

async function runClassicSearch(query, rerank, clientStarted) {
  const response = await fetch(`${API_BASE}/v1/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, limit: 10, rerank }),
  });
  const clientMs = performance.now() - clientStarted;
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${await response.text()}`);
  }
  const data = await response.json();
  renderRankedList(data, clientMs, rerank);
}

async function runRag(query, rerank, clientStarted) {
  const response = await fetch(`${API_BASE}/v1/rag`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ query, rerank }),
  });
  if (!response.ok) {
    throw new Error(`API ${response.status}: ${await response.text()}`);
  }

  let draft = "";
  const clientMsAtList = { value: null };

  await readSse(response, (event, data) => {
    if (event === "ranked_list") {
      // Fork A: show reranked hits as soon as retrieval finishes.
      clientMsAtList.value = performance.now() - clientStarted;
      renderRankedList(data, clientMsAtList.value, rerank);
      statusEl.textContent += " Generating a grounded top-3…";
      recsEl.innerHTML = `
        <article class="rec rec-draft rec-ai">
          ${geminiBadge("Generating recommendations…", "Streaming from Gemini")}
          <pre class="rec-stream"></pre>
        </article>`;
    } else if (event === "llm_delta") {
      draft += data.text || "";
      const streamEl = recsEl.querySelector(".rec-stream");
      if (streamEl) {
        streamEl.textContent = draft;
      }
    } else if (event === "llm_done") {
      renderRecommendations(data);
      const llmNote =
        typeof data.llm_ms === "number"
          ? ` Grounded top-3 in ${formatDuration(data.llm_ms)}.`
          : "";
      if (data.dropped?.length) {
        statusEl.textContent += ` Dropped ${data.dropped.length} citation(s) not in the retrieved set.`;
      }
      statusEl.textContent += llmNote;
    } else if (event === "llm_error") {
      // Fork A still stands; generation is optional.
      recsEl.innerHTML = "";
      statusEl.textContent += ` ${data.message || "Generation failed."} Ranked results above still apply.`;
    }
  });
}

function renderRankedList(data, clientMs, requestedRerank) {
  const elapsedMs = typeof data.took_ms === "number" ? data.took_ms : clientMs;
  const resultLabel = data.total === 1 ? "result" : "results";
  const rankingNote = data.reranked
    ? "with cross-encoder rerank"
    : "semantic ranking";
  const skippedRerank =
    requestedRerank && !data.reranked
      ? " Rerank was requested but the API did not apply it."
      : "";
  statusEl.textContent =
    `Found ${data.total} ${resultLabel} (${rankingNote}).` +
    skippedRerank +
    ` Fast path in ${formatDuration(elapsedMs)}.`;

  resultsEl.innerHTML = "";
  for (const hit of data.results || []) {
    const article = document.createElement("article");
    article.className = "hit";
    article.innerHTML = `
      <div class="hit-top">
        <h2>${escapeHtml(hit.name)}</h2>
        <span class="score">${Number(hit.score).toFixed(3)}</span>
      </div>
      <p>${escapeHtml(hit.summary || "")}</p>
      <p class="meta">${metaLine(hit)}</p>
      ${healthBadges(hit)}
    `;
    resultsEl.appendChild(article);
  }
}

function metaLine(hit) {
  const meta = hit.metadata || {};
  const parts = [`★ ${formatCount(hit.stars ?? 0)}`, `forks ${formatCount(hit.forks ?? 0)}`];
  if (typeof meta.downloads_30d === "number") {
    parts.push(`${formatCount(meta.downloads_30d)} downloads / 30 days`);
  }
  if (meta.latest_release) {
    parts.push(`released ${formatDate(meta.latest_release)}`);
  }
  return parts.map(escapeHtml).join(" · ");
}

// Only packages the health collector has checked carry these fields.
function healthBadges(hit) {
  const meta = hit.metadata || {};
  if (!meta.health_checked_at) {
    return "";
  }
  const badges = [];
  if (meta.health_status === "gone") {
    badges.push(badge("Repo gone", "bad", "The linked GitHub repository no longer exists."));
  } else if (typeof meta.health_score === "number") {
    const level = meta.health_score >= 0.7 ? "good" : meta.health_score >= 0.4 ? "fair" : "bad";
    badges.push(
      badge(
        `Health ${meta.health_score.toFixed(2)}`,
        level,
        "0–1 from push and release recency, release cadence, open issues per star and recent commits.",
      ),
    );
  }
  if (meta.archived) {
    badges.push(badge("Archived", "bad", "The GitHub repository is archived (read-only)."));
  }
  if (hit.last_commit) {
    badges.push(badge(`Last commit ${timeAgo(hit.last_commit)}`, "neutral"));
  }
  if (typeof meta.commits_90d === "number" && meta.commits_90d > 0) {
    const plus = meta.commits_90d >= 5 ? "5+" : String(meta.commits_90d);
    badges.push(badge(`${plus} commits in 90 days`, "neutral"));
  }
  badges.push(badge(`Checked ${timeAgo(meta.health_checked_at)}`, "muted"));
  return `<div class="badges">${badges.join("")}</div>`;
}

function badge(label, level, title = "") {
  const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
  return `<span class="badge badge-${level}"${titleAttr}>${escapeHtml(label)}</span>`;
}

function formatCount(value) {
  const n = Number(value) || 0;
  if (n >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, "")}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1e4) return `${Math.round(n / 1e3)}k`;
  return n.toLocaleString("en-US");
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleDateString("en-US", { year: "numeric", month: "short" });
}

function timeAgo(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  const days = Math.floor((Date.now() - date.getTime()) / 86_400_000);
  if (days < 1) return "today";
  if (days === 1) return "yesterday";
  if (days < 60) return `${days} days ago`;
  if (days < 730) return `${Math.round(days / 30)} months ago`;
  return `${Math.floor(days / 365)} years ago`;
}

function geminiBadge(title, subtitle = "Generated with Gemini") {
  return `
    <div class="ai-header">
      <img src="${GEMINI_ICON}" class="gemini-icon" width="28" height="28" alt="" aria-hidden="true" />
      <div class="ai-header-text">
        <h2 class="rec-heading">${escapeHtml(title)}</h2>
        <p class="ai-label">${escapeHtml(subtitle)}</p>
      </div>
    </div>
  `;
}

function renderRecommendations(data) {
  const recs = data.recommendations || [];
  if (!recs.length) {
    recsEl.innerHTML =
      "<p class=\"meta\">No grounded recommendations passed citation checks.</p>";
    return;
  }
  recsEl.innerHTML = geminiBadge("Grounded top-3");
  recs.forEach((rec, index) => {
    const article = document.createElement("article");
    article.className = "rec rec-ai";
    const warn = rec.snippet_in_description
      ? ""
      : `<p class="meta">Cited snippet was not found verbatim in the stored description.</p>`;
    article.innerHTML = `
      <div class="hit-top">
        <h3>${index + 1}. ${escapeHtml(rec.package)}</h3>
        <span class="ai-chip">
          <img src="${GEMINI_ICON}" class="gemini-icon gemini-icon-sm" width="16" height="16" alt="" aria-hidden="true" />
          Gemini
        </span>
      </div>
      <p>${escapeHtml(rec.reason)}</p>
      <blockquote>${escapeHtml(rec.cited_snippet)}</blockquote>
      ${warn}
    `;
    recsEl.appendChild(article);
  });
}

async function readSse(response, onEvent) {
  // Parse text/event-stream frames split by blank lines (event + data lines).
  if (!response.body) {
    throw new Error("No response body for SSE");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const parsed = parseSseBlock(chunk);
      if (parsed) {
        onEvent(parsed.event, parsed.data);
      }
    }
  }
}

function parseSseBlock(block) {
  let eventName = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (!dataLines.length) {
    return null;
  }
  return { event: eventName, data: JSON.parse(dataLines.join("\n")) };
}

function formatDuration(ms) {
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  return `${(ms / 1000).toFixed(2)} s`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
