"""SQLite store for what ReEx has seen on earlier pages, plus user feedback.

- `reviews`: one row per (product, review). Only a SHA-256 hash of the normalized review text is
  kept, never the text, so copies across products can be spotted without storing what people wrote.
- `feedback`: reviews a user marked as wrongly judged. The text is kept here because it is
  needed for retraining; it is public review text from the product page.
"""

import hashlib
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs, urlparse

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "reex.sqlite3"

# Short reviews ("Good product, value for money") repeat naturally across products, so only
# longer ones count as copies.
MIN_WORDS_FOR_COPY = 12

_AMAZON_ASIN = re.compile(r"/(?:dp|gp/product|product-reviews)/([A-Z0-9]{10})(?:[/?]|$)")
_FLIPKART_ITEM = re.compile(r"/p/(itm[0-9a-z]+)", re.IGNORECASE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    product    TEXT NOT NULL,
    text_hash  TEXT NOT NULL,
    long_text  INTEGER NOT NULL,
    reviewer   TEXT,
    suspicious INTEGER NOT NULL,
    seen_at    REAL NOT NULL,
    review_id  TEXT,
    PRIMARY KEY (product, text_hash)
);
CREATE INDEX IF NOT EXISTS reviews_by_hash ON reviews (text_hash);
CREATE INDEX IF NOT EXISTS reviews_by_reviewer ON reviews (reviewer);
CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product         TEXT,
    url             TEXT,
    text            TEXT NOT NULL,
    rating          REAL,
    predicted_label TEXT NOT NULL,
    user_label      TEXT NOT NULL,
    model_score     REAL,
    created_at      REAL NOT NULL
);
"""


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def is_long(text: str) -> bool:
    return len(normalize(text).split()) >= MIN_WORDS_FOR_COPY


def product_key(url: Optional[str]) -> Optional[str]:
    """A stable id for the product a page is about, so the same product under different URLs matches."""
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "amazon." in host and (m := _AMAZON_ASIN.search(parsed.path)):
        return f"amazon:{m.group(1)}"
    if host.endswith("flipkart.com"):
        if pid := parse_qs(parsed.query).get("pid"):
            return f"flipkart:{pid[0]}"
        if m := _FLIPKART_ITEM.search(parsed.path):
            return f"flipkart:{m.group(1).lower()}"
    if not host:
        return None
    return f"{host.removeprefix('www.')}{parsed.path.rstrip('/')}"


def is_same_review(a_id: Optional[str], a_reviewer: Optional[str], b_id: Optional[str], b_reviewer: Optional[str]) -> bool:
    """Whether two sightings of the same text are one review shown twice, not a copy.

    Amazon shows each review on every variant of a product (colour, size...), and each variant has its
    own product id. Matching review ids settle it; for rows stored before review ids were recorded, the
    same reviewer is the best available hint.
    """
    if a_id and b_id:
        return a_id == b_id
    if a_reviewer and b_reviewer:
        return a_reviewer == b_reviewer
    return False


class Store:
    def __init__(self, path: Path = DEFAULT_DB_PATH):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(_SCHEMA)
            # Databases created before review ids were stored lack the column.
            columns = {row[1] for row in db.execute("PRAGMA table_info(reviews)")}
            if "review_id" not in columns:
                db.execute("ALTER TABLE reviews ADD COLUMN review_id TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS reviews_by_review_id ON reviews (review_id)")

    def _connect(self) -> sqlite3.Connection:
        # A connection per call keeps this safe under FastAPI's thread pool.
        return sqlite3.connect(self.path)

    def copied_elsewhere(self, product: str, items: list[tuple[str, Optional[str], Optional[str]]]) -> set[int]:
        """items: (text_hash, review_id, reviewer) of long reviews on this product.

        Returns the positions in `items` whose text was also seen on another product as a *different* review.
        """
        if not items:
            return set()
        hashes = sorted({h for h, _, _ in items})
        marks = ",".join("?" * len(hashes))
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT text_hash, review_id, reviewer FROM reviews WHERE long_text = 1 AND product != ? AND text_hash IN ({marks})",
                [product, *hashes],
            ).fetchall()
        seen: dict[str, list[tuple[Optional[str], Optional[str]]]] = {}
        for h, rid, reviewer in rows:
            seen.setdefault(h, []).append((rid, reviewer))
        return {
            j
            for j, (h, rid, reviewer) in enumerate(items)
            if any(not is_same_review(rid, reviewer, other_id, other_reviewer) for other_id, other_reviewer in seen.get(h, []))
        }

    def flagged_reviews(self, product: str, reviewers: Iterable[str], exclude_review_ids: Iterable[str]) -> dict[str, int]:
        """For each reviewer, how many of their reviews on other products were flagged suspicious.

        Counts distinct reviews rather than products, so one review shown on several variants counts
        once, and leaves out the reviews being analyzed right now (`exclude_review_ids`).
        """
        reviewers = sorted(set(reviewers))
        if not reviewers:
            return {}
        exclude = sorted(set(exclude_review_ids)) or [""]
        r_marks = ",".join("?" * len(reviewers))
        x_marks = ",".join("?" * len(exclude))
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT reviewer, COUNT(DISTINCT COALESCE(review_id, text_hash)) FROM reviews "
                f"WHERE suspicious = 1 AND product != ? AND reviewer IN ({r_marks}) "
                f"AND (review_id IS NULL OR review_id NOT IN ({x_marks})) GROUP BY reviewer",
                [product, *reviewers, *exclude],
            )
            return dict(rows.fetchall())

    def record(self, product: str, rows: Iterable[tuple[str, bool, Optional[str], bool, Optional[str]]]) -> None:
        """rows: (text_hash, long_text, reviewer, suspicious, review_id). Re-analyzing a page updates its rows."""
        now = time.time()
        with closing(self._connect()) as db, db:
            db.executemany(
                "INSERT OR REPLACE INTO reviews (product, text_hash, long_text, reviewer, suspicious, seen_at, review_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(product, h, int(long_), reviewer, int(susp), now, rid) for h, long_, reviewer, susp, rid in rows],
            )

    def add_feedback(
        self,
        url: Optional[str],
        text: str,
        rating: Optional[float],
        predicted_label: str,
        user_label: str,
        model_score: Optional[float],
    ) -> int:
        with closing(self._connect()) as db, db:
            cur = db.execute(
                "INSERT INTO feedback (product, url, text, rating, predicted_label, user_label, model_score, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (product_key(url), url, text, rating, predicted_label, user_label, model_score, time.time()),
            )
            return cur.lastrowid
