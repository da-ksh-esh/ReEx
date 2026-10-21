from typing import Literal, Optional

from pydantic import BaseModel, Field


class Review(BaseModel):
    text: str
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    title: Optional[str] = None
    date: Optional[str] = None
    verified: Optional[bool] = None
    author: Optional[str] = None
    reviewer_id: Optional[str] = None  # stable store account id when the page exposes one


class PageRating(BaseModel):
    """The store's own overall rating for the product, as shown on the page."""

    average: float = Field(ge=0, le=5)
    count: Optional[int] = Field(default=None, ge=0)


class AnalyzeRequest(BaseModel):
    url: Optional[str] = None
    site: Optional[str] = None
    page_rating: Optional[PageRating] = None
    reviews: list[Review] = Field(max_length=500)


class ReviewResult(BaseModel):
    index: int
    suspicion: float  # 0..1, higher = more likely fake; 0 for skipped reviews
    label: Literal["suspicious", "genuine", "skipped"]  # skipped = not English, so not analyzed
    sentiment: Literal["positive", "negative", "neutral"]
    model_score: Optional[float] = None
    reasons: list[str]


class RatingSummary(BaseModel):
    page_average: Optional[float] = None  # the store's rating, echoed from the request
    page_count: Optional[int] = None
    analyzed_average: Optional[float] = None  # mean stars of the reviews we analyzed
    adjusted_average: Optional[float] = None  # same, with suspicious reviews left out
    rated: int  # how many analyzed reviews had a star rating


class SentimentSummary(BaseModel):
    """Sentiment of the reviews labelled genuine: what real buyers seem to say."""

    positive: int
    negative: int
    neutral: int


class AnalyzeResponse(BaseModel):
    total: int  # reviews analyzed (skipped ones not included)
    suspicious: int
    genuine: int
    skipped: int
    trust_score: int  # 0..100, higher = more trustworthy product reviews
    model_loaded: bool
    rating: RatingSummary
    sentiment: SentimentSummary
    results: list[ReviewResult]


class FeedbackRequest(BaseModel):
    """A user saying ReEx judged a review wrongly."""

    url: Optional[str] = None
    text: str = Field(min_length=1, max_length=20_000)
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    predicted_label: Literal["suspicious", "genuine"]
    user_label: Literal["fake", "genuine"]
    model_score: Optional[float] = None


class FeedbackResponse(BaseModel):
    id: int
