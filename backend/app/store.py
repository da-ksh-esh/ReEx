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


class Store:
    def __init__(self, path: Path = DEFAULT_DB_PATH):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # A connection per call keeps this safe under FastAPI's thread pool.
        return sqlite3.connect(self.path)

    def copied_elsewhere(self, product: str, hashes: Iterable[str]) -> set[str]:
        """Hashes of long reviews that were also seen on a different product."""
        hashes = list(set(hashes))
        if not hashes:
            return set()
        marks = ",".join("?" * len(hashes))
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT DISTINCT text_hash FROM reviews WHERE long_text = 1 AND product != ? AND text_hash IN ({marks})",
                [product, *hashes],
            )
            return {h for (h,) in rows}

    def flagged_products(self, product: str, reviewers: Iterable[str]) -> dict[str, int]:
        """For each reviewer, how many other products they had a review flagged suspicious on."""
        reviewers = list(set(reviewers))
        if not reviewers:
            return {}
        marks = ",".join("?" * len(reviewers))
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT reviewer, COUNT(DISTINCT product) FROM reviews "
                f"WHERE suspicious = 1 AND product != ? AND reviewer IN ({marks}) GROUP BY reviewer",
                [product, *reviewers],
            )
            return dict(rows.fetchall())

    def record(self, product: str, rows: Iterable[tuple[str, bool, Optional[str], bool]]) -> None:
        """rows: (text_hash, long_text, reviewer, suspicious). Re-analyzing a page updates its rows."""
        now = time.time()
        with closing(self._connect()) as db, db:
            db.executemany(
                "INSERT OR REPLACE INTO reviews (product, text_hash, long_text, reviewer, suspicious, seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(product, h, int(long_), reviewer, int(susp), now) for h, long_, reviewer, susp in rows],
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
