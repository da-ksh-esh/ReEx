from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .model import TextModel
from .schemas import AnalyzeRequest, AnalyzeResponse, ReviewResult
from .signals import compute_signals

SUSPICIOUS_THRESHOLD = 0.5
MODEL_WEIGHT = 0.6  # share of the final score from the text model when it's available
MODEL_CONFIDENT = 0.7  # model score needed before its wording reason is shown to the user

app = FastAPI(title="ReEx review analyzer")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

text_model = TextModel()


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": text_model.loaded}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    reviews = [r for r in req.reviews if r.text.strip()]
    signals = compute_signals(reviews)
    model_scores = text_model.predict_fake_proba([r.text for r in reviews])

    results = []
    for i, (signal_weight, reasons) in enumerate(signals):
        model_score = model_scores[i] if model_scores else None
        if model_score is None:
            suspicion = signal_weight
        else:
            suspicion = MODEL_WEIGHT * model_score + (1 - MODEL_WEIGHT) * signal_weight
        label = "suspicious" if suspicion >= SUSPICIOUS_THRESHOLD else "genuine"
        # Only blame the wording when the model is confident and the review is actually flagged,
        # so a "genuine" review never shows a reason saying it looks fake.
        if model_score is not None and model_score >= MODEL_CONFIDENT and label == "suspicious":
            reasons = ["Wording resembles known fake reviews", *reasons]
        results.append(
            ReviewResult(
                index=i,
                suspicion=round(suspicion, 3),
                label=label,
                model_score=None if model_score is None else round(model_score, 3),
                reasons=reasons,
            )
        )

    suspicious = sum(r.label == "suspicious" for r in results)
    mean_suspicion = sum(r.suspicion for r in results) / len(results) if results else 0.0
    return AnalyzeResponse(
        total=len(results),
        suspicious=suspicious,
        genuine=len(results) - suspicious,
        trust_score=round(100 * (1 - mean_suspicion)),
        model_loaded=text_model.loaded,
        results=results,
    )
