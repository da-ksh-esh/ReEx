"""Rule-based signals that look at a review in the context of the other reviews on the page.

Each signal returns, per review, a weight (how suspicious it is) and a human-readable reason.
"""

import re
from collections import Counter

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .schemas import Review

DUPLICATE_SIMILARITY = 0.8
DUPLICATE_WEIGHT = 0.5
BURST_MIN_COUNT = 3
BURST_WEIGHT = 0.3
SHORT_GENERIC_MAX_WORDS = 5
SHORT_GENERIC_WEIGHT = 0.25
UNVERIFIED_WEIGHT = 0.2
RATING_DEVIATION = 2.5
RATING_DEVIATION_WEIGHT = 0.2

_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _near_duplicates(reviews: list[Review]) -> set[int]:
    texts = [r.text for r in reviews]
    if len(texts) < 2:
        return set()
    try:
        matrix = TfidfVectorizer().fit_transform(texts)
    except ValueError:  # every text was empty / stop words only
        return set()
    sim = cosine_similarity(matrix)
    dupes = set()
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if sim[i, j] >= DUPLICATE_SIMILARITY:
                dupes.update((i, j))
    return dupes


def _burst_dates(reviews: list[Review]) -> set[str]:
    """Exact dates on which several top-rated reviews were posted.

    Only dates with a year are considered, so relative dates like "2 months ago" are ignored.
    """
    dates = [
        r.date.strip()
        for r in reviews
        if r.date and _YEAR.search(r.date) and r.rating is not None and r.rating >= 4.5
    ]
    return {d for d, n in Counter(dates).items() if n >= BURST_MIN_COUNT}


def compute_signals(reviews: list[Review]) -> list[tuple[float, list[str]]]:
    dupes = _near_duplicates(reviews)
    bursts = _burst_dates(reviews)
    ratings = [r.rating for r in reviews if r.rating is not None]
    mean_rating = sum(ratings) / len(ratings) if ratings else None

    out = []
    for i, r in enumerate(reviews):
        weight, reasons = 0.0, []
        if i in dupes:
            weight += DUPLICATE_WEIGHT
            reasons.append("Nearly identical to another review on this page")
        if r.date and r.date.strip() in bursts:
            weight += BURST_WEIGHT
            reasons.append(f"Part of a burst of top ratings posted on {r.date.strip()}")
        if len(r.text.split()) <= SHORT_GENERIC_MAX_WORDS and (r.rating or 0) >= 4:
            weight += SHORT_GENERIC_WEIGHT
            reasons.append("Very short, high-rated review with no detail")
        if r.verified is False:
            weight += UNVERIFIED_WEIGHT
            reasons.append("Not marked as a verified purchase")
        if (
            mean_rating is not None
            and r.rating is not None
            and len(ratings) >= 5
            and abs(r.rating - mean_rating) >= RATING_DEVIATION
        ):
            weight += RATING_DEVIATION_WEIGHT
            reasons.append("Rating is far from the product's average")
        out.append((min(weight, 1.0), reasons))
    return out
