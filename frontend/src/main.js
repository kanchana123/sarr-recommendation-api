const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8080";

const form = document.getElementById("search-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = document.getElementById("query").value.trim();
  const rerank = document.getElementById("rerank").checked;

  statusEl.textContent = "Searching…";
  resultsEl.innerHTML = "";

  const clientStarted = performance.now();

  try {
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
    const elapsedMs =
      typeof data.took_ms === "number" ? data.took_ms : clientMs;
    const resultLabel = data.total === 1 ? "result" : "results";
    const rerankNote = data.reranked ? " with reranking" : "";

    statusEl.textContent =
      `Found ${data.total} ${resultLabel}${rerankNote}. ` +
      `Request completed in ${formatDuration(elapsedMs)}.`;

    for (const hit of data.results) {
      const article = document.createElement("article");
      article.className = "hit";
      article.innerHTML = `
        <div class="hit-top">
          <h2>${escapeHtml(hit.name)}</h2>
          <span class="score">${hit.score.toFixed(3)}</span>
        </div>
        <p>${escapeHtml(hit.summary || "")}</p>
        <p class="meta">★ ${hit.stars ?? 0} · forks ${hit.forks ?? 0}</p>
      `;
      resultsEl.appendChild(article);
    }
  } catch (error) {
    const clientMs = performance.now() - clientStarted;
    const detail = error instanceof Error ? error.message : "Search failed";
    statusEl.textContent =
      `${detail}. Request failed after ${formatDuration(clientMs)}.`;
  }
});

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
