const API_BASE = "http://localhost:8000";
const CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000; // reuse results for the same reviews for a day
const CACHE_MAX_ENTRIES = 30;
const SETTINGS_KEY = "reexSettings"; // deliberately not "reex:…", which is the cache prefix
const DEFAULT_MAX_REVIEWS = 100;
const LOAD_PAUSE_MS = 1500; // wait between "show more" presses, to load pages at a human pace

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

// ---- settings ----

async function loadSettings() {
  try {
    return { maxReviews: DEFAULT_MAX_REVIEWS, ...(await chrome.storage.local.get(SETTINGS_KEY))[SETTINGS_KEY] };
  } catch (_) {
    return { maxReviews: DEFAULT_MAX_REVIEWS };
  }
}

$("max-reviews").addEventListener("change", (e) => {
  chrome.storage.local.set({ [SETTINGS_KEY]: { maxReviews: Number(e.target.value) } });
});

// ---- loading more reviews (Amazon) ----

const LOAD_FAILURES = {
  "signed-out": "Sign in to Amazon to load more than the reviews on this page.",
  blocked: "Amazon asked for a check (sign-in or CAPTCHA), so ReEx stopped loading more reviews.",
  "not-amazon-product": null, // not an Amazon product page: just use what's on the page, no message
};

// Two reviews are the same if they share Amazon's review id, or the same author and opening text.
const idKey = (r) => r.review_id || null;
const textKey = (r) => `${(r.author || "").toLowerCase()}|${r.text.toLowerCase().replace(/\s+/g, " ").slice(0, 120)}`;

// Page reviews keep their positions (badges on the page use them); new ones go after.
function mergeReviews(pageReviews, loaded) {
  const ids = new Set(pageReviews.map(idKey).filter(Boolean));
  const texts = new Set(pageReviews.map(textKey));
  const extra = [];
  for (const r of loaded) {
    if ((idKey(r) && ids.has(idKey(r))) || texts.has(textKey(r))) continue;
    if (idKey(r)) ids.add(idKey(r));
    texts.add(textKey(r));
    extra.push(r);
  }
  return extra;
}

// Runs inside the Amazon product page (serialized by executeScript), so it must be self-contained.
//
// Amazon's reviews page lists 10 reviews and a "Show 10 more reviews" button, and only shows more to
// signed-in users. Rather than opening a tab, this loads that page in an invisible frame inside the
// product page (allowed: both are on the same Amazon site), presses the page's own button with a
// pause between presses, reads the reviews with the extractor, and removes the frame. It keeps
// running and cleans up even if the popup is closed.
async function loadReviewsInPage(maxReviews, pauseMs) {
  const SHOW_MORE_TIMEOUT_MS = 10000;
  const LOAD_TIMEOUT_MS = 20000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const report = (loaded) =>
    chrome.runtime.sendMessage({ type: "reex:progress", loaded, limit: maxReviews }).catch(() => {}); // popup may be closed

  const asin = location.pathname.match(/\/(?:dp|gp\/product|product-reviews|portal\/customer-reviews)\/([A-Z0-9]{10})(?:[/?]|$)/)?.[1];
  if (!asin || typeof window.__reexExtract !== "function") return { ok: false, reason: "not-amazon-product" };

  document.getElementById("reex-reviews-frame")?.remove(); // left over from an interrupted run
  const frame = document.createElement("iframe");
  frame.id = "reex-reviews-frame";
  frame.setAttribute("aria-hidden", "true");
  frame.tabIndex = -1;
  // Off-screen but laid out at a normal size, so Amazon renders the reviews (and innerText works).
  Object.assign(frame.style, { position: "fixed", left: "-10000px", top: "0", width: "1000px", height: "800px", border: "0", opacity: "0", pointerEvents: "none" });
  frame.src = `/portal/customer-reviews/${asin}?ie=UTF8&reviewerType=all_reviews`;

  try {
    await new Promise((resolve, reject) => {
      frame.addEventListener("load", resolve, { once: true });
      setTimeout(() => reject(new Error("The reviews page took too long to load")), LOAD_TIMEOUT_MS);
      document.body.appendChild(frame);
    });
    const doc = frame.contentDocument;
    if (!doc) return { ok: false, reason: "error", message: "Amazon didn't allow loading its reviews page" };
    const blocked = () =>
      !!doc.querySelector('form[action*="validateCaptcha"], input#captchacharacters') ||
      /\/(ap\/signin|ax\/claim)/.test(frame.contentWindow.location.pathname);
    if (/\/(ap\/signin|ax\/claim)/.test(frame.contentWindow.location.pathname)) return { ok: false, reason: "signed-out" };

    const count = () => doc.querySelectorAll('[data-hook="review"]').length;
    const button = () => doc.querySelector('[data-hook="show-more-button"]');
    report(count());
    while (!blocked() && button() && count() < maxReviews) {
      await sleep(pauseMs);
      const before = count();
      button().click();
      const start = Date.now();
      while (Date.now() - start < SHOW_MORE_TIMEOUT_MS && count() <= before) await sleep(250);
      report(count());
      if (count() <= before) break; // the button did nothing; don't keep pressing it
    }
    if (blocked()) return { ok: false, reason: "blocked" };

    const page = window.__reexExtract(doc, { tag: false });
    return { ok: true, reviews: page.reviews.slice(0, maxReviews), complete: !button() };
  } catch (err) {
    return { ok: false, reason: "error", message: String(err?.message || err) };
  } finally {
    frame.remove();
  }
}

async function loadMoreReviews(tabId, maxReviews) {
  const onProgress = (msg) => {
    if (msg?.type === "reex:progress") $("status").textContent = `Loading more reviews from Amazon… ${msg.loaded} of up to ${msg.limit}`;
  };
  chrome.runtime.onMessage.addListener(onProgress);
  try {
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func: loadReviewsInPage, args: [maxReviews, LOAD_PAUSE_MS] });
    return result;
  } catch (err) {
    return { ok: false, reason: "error", message: String(err?.message || err) };
  } finally {
    chrome.runtime.onMessage.removeListener(onProgress);
  }
}

// ---- actions ----

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

// Show the saved setting and the last result for this page as soon as the popup opens.
(async () => {
  try {
    $("max-reviews").value = String((await loadSettings()).maxReviews);
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
    const { maxReviews } = await loadSettings();
    const onPage = page.reviews.length;

    // The cache key includes the setting, so switching "how many reviews" re-runs the analysis.
    const sig = `${maxReviews}/${signature(page.reviews)}`;
    const cached = await readCache(page.url);
    let data;
    let fromCache = false;
    // A result where loading more reviews failed is never reused: the cause (signed out, worker not
    // running) may be fixed by now, so the next click should try again.
    const reusable = cached && cached.signature === sig && Date.now() - cached.savedAt < CACHE_MAX_AGE_MS && !cached.page.load?.note;
    if (reusable) {
      data = cached.data;
      page.reviews = cached.page.reviews;
      page.load = cached.page.load;
      fromCache = true;
    } else {
      page.load = { onPage, extra: 0, complete: true, note: null };
      if (maxReviews > onPage && page.site === "amazon") {
        $("status").textContent = "Loading more reviews from Amazon…";
        const loaded = await loadMoreReviews(tab.id, maxReviews);
        if (loaded?.ok) {
          const extra = mergeReviews(page.reviews, loaded.reviews).slice(0, Math.max(0, maxReviews - onPage));
          page.reviews = [...page.reviews, ...extra];
          page.load = { onPage, extra: extra.length, complete: loaded.complete, note: null };
        } else {
          page.load.note = LOAD_FAILURES[loaded?.reason] ?? (loaded?.reason ? `Couldn't load more reviews (${loaded.message || loaded.reason}).` : null);
        }
      }
      if (!page.reviews.length) {
        $("status").textContent = "No reviews found on this page. Try opening the product's reviews section.";
        return;
      }
      $("status").textContent = `Analyzing ${page.reviews.length} reviews…`;
      const res = await fetch(`${API_BASE}/analyze`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(page) });
      if (!res.ok) throw new Error(`Backend returned ${res.status}`);
      data = await res.json();
      if (!page.load.note) await writeCache(page.url, { signature: sig, savedAt: Date.now(), page, data });
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

function sourceNote(load) {
  if (!load || !load.extra) return "on this page";
  // Amazon's list stops after about 100 reviews even when a product has more.
  const more = load.complete ? "as many as Amazon lists" : "stopped at your limit";
  return `${load.onPage} on this page + ${load.extra} from the reviews pages, ${more}`;
}

function render(page, data) {
  const skippedNote = data.skipped ? ` · ${data.skipped} not in English, skipped` : "";
  $("status").textContent =
    `Checked ${data.total} reviews (${sourceNote(page.load)})${skippedNote}` + (data.model_loaded ? "" : " · rules only, no model trained yet");
  $("load-note").textContent = page.load?.note || "";
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
