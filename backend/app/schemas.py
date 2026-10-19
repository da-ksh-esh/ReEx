from typing import Literal, Optional

from pydantic import BaseModel, Field


class Review(BaseModel):
    text: str
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    title: Optional[str] = None
    date: Optional[str] = None
    verified: Optional[bool] = None
    author: Optional[str] = None


class AnalyzeRequest(BaseModel):
    url: Optional[str] = None
    site: Optional[str] = None
    reviews: list[Review] = Field(max_length=500)


class ReviewResult(BaseModel):
    index: int
    suspicion: float  # 0..1, higher = more likely fake
    label: Literal["suspicious", "genuine"]
    model_score: Optional[float] = None
    reasons: list[str]


class AnalyzeResponse(BaseModel):
    total: int
    suspicious: int
    genuine: int
    trust_score: int  # 0..100, higher = more trustworthy product reviews
    model_loaded: bool
    results: list[ReviewResult]
