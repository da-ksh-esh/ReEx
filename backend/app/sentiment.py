"""Lexicon-based sentiment (VADER), used for the star-vs-text mismatch signal and the sentiment summary."""

from typing import Literal, Optional

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

Sentiment = Literal["positive", "negative", "neutral"]

# VADER's own convention for calling a compound score positive / negative.
NEUTRAL_BAND = 0.05

_analyzer = SentimentIntensityAnalyzer()


def compound_score(text: str) -> float:
    return _analyzer.polarity_scores(text)["compound"]


def sentiment_label(compound: float, rating: Optional[float]) -> Sentiment:
    if compound >= NEUTRAL_BAND:
        return "positive"
    if compound <= -NEUTRAL_BAND:
        return "negative"
    # VADER scores plain complaints ("stopped working after a week") as neutral, so fall back to the stars.
    if rating is not None:
        if rating >= 4:
            return "positive"
        if rating <= 2:
            return "negative"
    return "neutral"
