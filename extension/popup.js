const API_URL = "http://localhost:8000/analyze";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};

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
    render(page, await res.json());
  } catch (err) {
    $("status").textContent = /Failed to fetch/.test(err.message)
      ? "Can't reach the backend. Is it running on localhost:8000?"
      : `Error: ${err.message}`;
  }
});

function render(page, data) {
  $("status").textContent = `Checked ${data.total} visible reviews (${page.site})` + (data.model_loaded ? "" : " · rules only, no model trained yet");
  const pct = data.total ? (100 * data.suspicious) / data.total : 0;
  const bar = el("div", "bar");
  bar.appendChild(el("div")).style.width = `${pct}%`;
  $("summary").replaceChildren(
    el("div", "score", `Trust score: ${data.trust_score}/100`),
    el("div", null, `${data.suspicious} likely suspicious · ${data.genuine} look genuine`),
    bar
  );
  const sorted = [...data.results].sort((a, b) => b.suspicion - a.suspicion);
  for (const r of sorted) {
    const review = page.reviews[r.index];
    const item = el("div", `review ${r.label}`);
    const label = r.label === "suspicious" ? "Likely suspicious" : "Looks genuine";
    item.append(
      el("div", "tag", `${label} · ${Math.round(r.suspicion * 100)}%` + (review.rating != null ? ` · ${review.rating}★` : "")),
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
