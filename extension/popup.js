const API_BASE = "http://localhost:8000";
const CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000; // reuse results for the same reviews for a day
const CACHE_MAX_ENTRIES = 30;

const $ = (id) => document.getElementById(id);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};
const stars = (x) => `${x.toFixed(1)}★`;
const LABELS = { suspicious: "Likely suspicious", genuine: "Looks genuine", skipped: "Not analyzed" };

// ---- cache (chrome.storage.local), keyed by page URL ----

const cacheKey = (url) => `reex:${url.split("#")[0]}`;

// Cheap fingerprint of the extracted reviews: if the page shows the same reviews, the cached verdict still applies.
function signature(reviews) {
  let h = 5381;
  for (const ch of reviews.map((r) => `${r.rating}|${r.text}`).join("\n")) h = ((h * 33) ^ ch.charCodeAt(0)) >>> 0;
  return `${reviews.length}:${h.toString(36)}`;
}

async function readCache(url) {
  const key = cacheKey(url);
  return (await chrome.storage.local.get(key))[key] || null;
}

async function writeCache(url, entry) {
  await chrome.storage.local.set({ [cacheKey(url)]: entry });
  const all = await chrome.storage.local.get(null);
  const keys = Object.keys(all).filter((k) => k.startsWith("reex:"));
  if (keys.length > CACHE_MAX_ENTRIES) {
    keys.sort((a, b) => all[a].savedAt - all[b].savedAt);
    await chrome.storage.local.remove(keys.slice(0, keys.length - CACHE_MAX_ENTRIES));
  }
}

function ago(ms) {
  const min = Math.round((Date.now() - ms) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  return `${Math.round(min / 60)} h ago`;
}

// ---- actions ----

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

// Show the last result for this page as soon as the popup opens, without re-running anything.
(async () => {
  try {
    const tab = await activeTab();
    const cached = tab?.url && (await readCache(tab.url));
    if (cached) {
      render(cached.page, cached.data);
      $("status").textContent += ` · from ${ago(cached.savedAt)}, click Analyze to refresh`;
    }
  } catch (_) {
    // no cache available, nothing to show yet
  }
})();

$("analyze").addEventListener("click", async () => {
  $("summary").replaceChildren();
  $("list").replaceChildren();
  $("status").textContent = "Reading reviews…";
  try {
    const tab = await activeTab();
    const [{ result: page }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["extractor.js"] });
    if (!page.reviews.length) {
      $("status").textContent = "No reviews found on this page. Try opening the product's reviews section.";
      return;
    }

    const sig = signature(page.reviews);
    const cached = await readCache(page.url);
    let data;
    let fromCache = false;
    if (cached && cached.signature === sig && Date.now() - cached.savedAt < CACHE_MAX_AGE_MS) {
      data = cached.data;
      fromCache = true;
    } else {
      $("status").textContent = `Analyzing ${page.reviews.length} reviews…`;
      const res = await fetch(`${API_BASE}/analyze`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(page) });
      if (!res.ok) throw new Error(`Backend returned ${res.status}`);
      data = await res.json();
      await writeCache(page.url, { signature: sig, savedAt: Date.now(), page, data });
    }

    render(page, data);
    if (fromCache) $("status").textContent += ` · same reviews as ${ago(cached.savedAt)}, reused`;
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: annotatePage, args: [data.results] });
  } catch (err) {
    $("status").textContent = /Failed to fetch/.test(err.message)
      ? "Can't reach the backend. Is it running on localhost:8000?"
      : `Error: ${err.message}`;
  }
});

async function sendFeedback(page, review, result, button) {
  button.disabled = true;
  button.textContent = "Sending…";
  try {
    const res = await fetch(`${API_BASE}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: page.url,
        text: review.text,
        rating: review.rating,
        predicted_label: result.label,
        user_label: result.label === "suspicious" ? "genuine" : "fake",
        model_score: result.model_score,
      }),
    });
    if (!res.ok) throw new Error(res.status);
    button.textContent = "Thanks, saved for retraining";
  } catch (_) {
    button.disabled = false;
    button.textContent = "Couldn't send, try again";
  }
}

// Runs inside the product page (serialized by executeScript), so it must not use anything from this file.
function annotatePage(results) {
  const STYLES = {
    suspicious: { bg: "#c62828", text: (r) => `⚠ ReEx: likely suspicious (${Math.round(r.suspicion * 100)}%)` },
    genuine: { bg: "#2e7d32", text: () => "✓ ReEx: looks genuine" },
    skipped: { bg: "#757575", text: () => "ReEx: not analyzed (not in English)" },
  };
  document.querySelectorAll(".reex-badge").forEach((b) => b.remove());
  let placed = 0;
  for (const r of results) {
    const target = document.querySelector(`[data-reex-index="${r.index}"]`);
    if (!target) continue;
    const style = STYLES[r.label] || STYLES.genuine;
    const badge = document.createElement("div");
    badge.className = "reex-badge";
    badge.textContent = style.text(r);
    badge.title = r.reasons.length ? r.reasons.join("\n") : "No warning signs found";
    Object.assign(badge.style, {
      display: "inline-block",
      margin: "4px 0 6px",
      padding: "2px 10px",
      borderRadius: "10px",
      font: "600 12px/1.6 system-ui, sans-serif",
      color: "#fff",
      background: style.bg,
      cursor: "help",
    });
    target.prepend(badge);
    placed++;
  }
  return placed;
}

// ---- rendering ----

function ratingLines(rating) {
  const lines = [];
  if (rating.page_average != null) {
    const count = rating.page_count != null ? ` (${rating.page_count.toLocaleString()} ratings)` : "";
    lines.push(`Store rating: ${stars(rating.page_average)}${count}`);
  }
  if (rating.analyzed_average != null) {
    if (rating.adjusted_average == null) {
      lines.push(`Analyzed reviews: ${stars(rating.analyzed_average)}, but every rated review looks suspicious`);
    } else if (rating.adjusted_average === rating.analyzed_average) {
      lines.push(`Analyzed reviews: ${stars(rating.analyzed_average)}, unchanged without the suspicious ones`);
    } else {
      lines.push(`Analyzed reviews: ${stars(rating.analyzed_average)} → ${stars(rating.adjusted_average)} without the suspicious ones`);
    }
  }
  return lines;
}

function render(page, data) {
  const skippedNote = data.skipped ? ` · ${data.skipped} not in English, skipped` : "";
  $("status").textContent =
    `Checked ${data.total} visible reviews (${page.site})${skippedNote}` + (data.model_loaded ? "" : " · rules only, no model trained yet");
  const pct = data.total ? (100 * data.suspicious) / data.total : 0;
  const bar = el("div", "bar");
  bar.appendChild(el("div")).style.width = `${pct}%`;
  const s = data.sentiment;
  $("summary").replaceChildren(
    el("div", "score", `Trust score: ${data.trust_score}/100`),
    el("div", null, `${data.suspicious} likely suspicious · ${data.genuine} look genuine`),
    bar,
    ...ratingLines(data.rating).map((line) => el("div", "rating", line)),
    el("div", "rating", `Genuine reviewers say: ${s.positive} positive · ${s.negative} negative · ${s.neutral} neutral`)
  );

  // Most suspicious first; skipped reviews last.
  const order = (r) => (r.label === "skipped" ? -1 : r.suspicion);
  const sorted = [...data.results].sort((a, b) => order(b) - order(a));
  $("list").replaceChildren();
  for (const r of sorted) {
    const review = page.reviews[r.index];
    const item = el("div", `review ${r.label}`);
    const meta = r.label === "skipped" ? "" : ` · ${Math.round(r.suspicion * 100)}%`;
    item.append(
      el("div", "tag", `${LABELS[r.label]}${meta}` + (review.rating != null ? ` · ${review.rating}★` : "") + (r.label === "skipped" ? "" : ` · ${r.sentiment}`)),
      el("div", "snippet", review.text.length > 160 ? review.text.slice(0, 160) + "…" : review.text)
    );
    if (r.reasons.length) {
      const ul = el("ul", "reasons");
      r.reasons.forEach((x) => ul.appendChild(el("li", null, x)));
      item.appendChild(ul);
    }
    if (r.label !== "skipped") {
      const wrong = el("button", "feedback", r.label === "suspicious" ? "Wrong? This looks genuine to me" : "Wrong? This looks fake to me");
      wrong.addEventListener("click", () => sendFeedback(page, review, r, wrong));
      item.appendChild(wrong);
    }
    $("list").appendChild(item);
  }
}
