// Injected into the current tab by popup.js. The value of the last expression is returned to the popup.
// Each review found in the DOM gets a data-reex-index attribute matching its position in `reviews`,
// so the popup can later put a badge on it.
(() => {
  // Remove badges from a previous run first, so their text never ends up inside extracted reviews.
  document.querySelectorAll(".reex-badge").forEach((b) => b.remove());
  const clean = (s) => (s || "").replace(/\s+/g, " ").replace(/\s*Read more\s*$/i, "").trim();
  // innerText keeps <br> line breaks as whitespace; textContent would glue the words together.
  const text = (el) => clean(el && (el.innerText ?? el.textContent));
  const num = (s) => {
    const m = (s || "").match(/([1-5](?:\.\d)?)/);
    return m ? parseFloat(m[1]) : null;
  };
  const count = (s) => {
    const digits = (s || "").replace(/[^\d]/g, "");
    return digits ? parseInt(digits, 10) : null;
  };

  function amazonPageRating() {
    const average = num(text(document.querySelector('[data-hook="rating-out-of-text"], [data-hook="average-star-rating"]')));
    return average == null ? null : { average, count: count(text(document.querySelector('[data-hook="total-review-count"]'))) };
  }

  function amazon() {
    return [...document.querySelectorAll('[data-hook="review"]')].map((n) => {
      const q = (s) => n.querySelector(s);
      return {
        _el: n,
        // Newer layout (2026) first, then the classic review-body / review-title hooks.
        text: text(q('[data-hook="reviewRichContentContainer"], [data-hook="review-body"]')),
        title: text(q('[data-hook="reviewTitle"], [data-hook="review-title"] > span:last-child, [data-hook="review-title"]')),
        rating: num(text(q('[data-hook="review-star-rating"], [data-hook="cmps-review-star-rating"], i[class*="a-star-"]'))),
        date: text(q('[data-hook="review-date"]')).replace(/^Reviewed in .*? on /, ""),
        verified: !!q('[data-hook="avp-badge"], [data-hook="avp-badge-linkless"]'),
        author: text(q(".a-profile-name")),
        // Profile links look like /gp/profile/amzn1.account.AF56...; reviews imported from other countries have none.
        reviewer_id: (q('a.a-profile, a[href*="/gp/profile/"]')?.getAttribute("href") || "").match(/amzn1\.account\.[A-Z0-9]+/)?.[0] || null,
      };
    });
  }

  // Flipkart's class names are obfuscated and change often, so this anchors on the
  // "Certified Buyer" label and walks up to the enclosing review block. Brittle by nature.
  function flipkart() {
    const anchors = [...document.querySelectorAll("p, span, div")].filter(
      (el) => el.children.length === 0 && /Certified Buyer/i.test(el.textContent)
    );
    const blocks = new Set();
    for (const a of anchors) {
      let el = a;
      for (let i = 0; i < 8 && el; i++, el = el.parentElement) {
        const leaves = [...el.querySelectorAll("*")].filter((c) => c.children.length === 0);
        if (leaves.some((c) => /^[1-5]$/.test(text(c))) && el.textContent.length > 30) {
          blocks.add(el);
          break;
        }
      }
    }
    return [...blocks].map((b) => {
      const leaves = [...b.querySelectorAll("*")].filter((c) => c.children.length === 0).map(text).filter(Boolean);
      const body = leaves.reduce((a, s) => (s.length > a.length ? s : a), "");
      return {
        _el: b,
        text: body,
        rating: num(leaves.find((s) => /^[1-5]$/.test(s))),
        date: leaves.find((s) => /(ago|\b(19|20)\d{2})$/i.test(s)) || null,
        verified: true,
        author: leaves[leaves.findIndex((s) => /Certified Buyer/i.test(s)) - 1] || null,
      };
    });
  }

  // Generic fallback: schema.org Review / AggregateRating objects embedded as JSON-LD.
  function jsonLd() {
    const out = [];
    let pageRating = null;
    const walk = (o) => {
      if (!o || typeof o !== "object") return;
      if (Array.isArray(o)) return o.forEach(walk);
      const type = [].concat(o["@type"] || []);
      if (!pageRating && o.aggregateRating?.ratingValue != null) {
        const average = num(String(o.aggregateRating.ratingValue));
        if (average != null) {
          pageRating = { average, count: count(String(o.aggregateRating.ratingCount ?? o.aggregateRating.reviewCount ?? "")) };
        }
      }
      if (type.includes("Review") && o.reviewBody) {
        out.push({
          text: clean(o.reviewBody),
          title: o.name || null,
          rating: num(String(o.reviewRating?.ratingValue ?? "")),
          date: o.datePublished || null,
          author: typeof o.author === "string" ? o.author : o.author?.name || null,
        });
      }
      Object.values(o).forEach(walk);
    };
    document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
      try { walk(JSON.parse(s.textContent)); } catch (_) {}
    });
    return { reviews: out, pageRating };
  }

  const host = location.hostname;
  let site = "generic";
  let reviews = [];
  let pageRating = null;
  if (/amazon\./.test(host)) { site = "amazon"; reviews = amazon(); pageRating = amazonPageRating(); }
  else if (/flipkart\.com$/.test(host)) { site = "flipkart"; reviews = flipkart(); }
  const ld = jsonLd();
  if (!reviews.length) reviews = ld.reviews;
  pageRating = pageRating || ld.pageRating;

  // Dedupe by page element, not by text: identical text in two different reviews is a duplicate
  // signal the backend needs to see. JSON-LD reviews have no element, so those dedupe by text.
  const seen = new Set();
  reviews = reviews
    .filter((r) => {
      const key = r._el || r.text;
      return r.text && !seen.has(key) && seen.add(key);
    })
    .slice(0, 200);

  document.querySelectorAll("[data-reex-index]").forEach((el) => el.removeAttribute("data-reex-index"));
  reviews.forEach((r, i) => {
    if (r._el) r._el.setAttribute("data-reex-index", String(i));
    delete r._el; // DOM nodes can't be sent back to the popup
  });
  return { site, url: location.href, page_rating: pageRating, reviews };
})();
