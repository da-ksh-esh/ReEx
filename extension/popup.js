const API_URL = "http://localhost:8000/analyze";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};
const stars = (x) => `${x.toFixed(1)}★`;

$("analyze").addEventListener("click", async () => {
  $("summary").replaceChildren();
  $("list").replaceChildren();
  $("status").textContent = "Reading reviews…";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const [{ result: page }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["extractor.js"] });
    if (!page.reviews.length) {
      $("status").textContent = "No reviews found on this page. Try opening the product's reviews section.";
      return;
    }
    $("status").textContent = `Analyzing ${page.reviews.length} reviews…`;
    const res = await fetch(API_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(page) });
    if (!res.ok) throw new Error(`Backend returned ${res.status}`);
    const data = await res.json();
    render(page, data);
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: annotatePage, args: [data.results] });
  } catch (err) {
    $("status").textContent = /Failed to fetch/.test(err.message)
      ? "Can't reach the backend. Is it running on localhost:8000?"
      : `Error: ${err.message}`;
  }
});

// Runs inside the product page (serialized by executeScript), so it must not use anything from this file.
function annotatePage(results) {
  document.querySelectorAll(".reex-badge").forEach((b) => b.remove());
  let placed = 0;
  for (const r of results) {
    const target = document.querySelector(`[data-reex-index="${r.index}"]`);
    if (!target) continue;
    const suspicious = r.label === "suspicious";
    const badge = document.createElement("div");
    badge.className = "reex-badge";
    badge.textContent = suspicious
      ? `⚠ ReEx: likely suspicious (${Math.round(r.suspicion * 100)}%)`
      : "✓ ReEx: looks genuine";
    badge.title = r.reasons.length ? r.reasons.join("\n") : "No warning signs found";
    Object.assign(badge.style, {
      display: "inline-block",
      margin: "4px 0 6px",
      padding: "2px 10px",
      borderRadius: "10px",
      font: "600 12px/1.6 system-ui, sans-serif",
      color: "#fff",
      background: suspicious ? "#c62828" : "#2e7d32",
      cursor: "help",
    });
    target.prepend(badge);
    placed++;
  }
  return placed;
}

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
  $("status").textContent = `Checked ${data.total} visible reviews (${page.site})` + (data.model_loaded ? "" : " · rules only, no model trained yet");
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
  const sorted = [...data.results].sort((a, b) => b.suspicion - a.suspicion);
  for (const r of sorted) {
    const review = page.reviews[r.index];
    const item = el("div", `review ${r.label}`);
    const label = r.label === "suspicious" ? "Likely suspicious" : "Looks genuine";
    item.append(
      el("div", "tag", `${label} · ${Math.round(r.suspicion * 100)}%` + (review.rating != null ? ` · ${review.rating}★` : "") + ` · ${r.sentiment}`),
      el("div", "snippet", review.text.length > 160 ? review.text.slice(0, 160) + "…" : review.text)
    );
    if (r.reasons.length) {
      const ul = el("ul", "reasons");
      r.reasons.forEach((x) => ul.appendChild(el("li", null, x)));
      item.appendChild(ul);
    }
    $("list").appendChild(item);
  }
}
