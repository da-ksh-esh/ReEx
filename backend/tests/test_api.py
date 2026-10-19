import joblib
from fastapi.testclient import TestClient

from app import main
from app.model import TextModel
from train import build_pipeline

client = TestClient(main.app)


def _review(text, rating=4.0, **kw):
    return {"text": text, "rating": rating, **kw}


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_empty_reviews():
    body = client.post("/analyze", json={"reviews": []}).json()
    assert body["total"] == 0 and body["trust_score"] == 100


def test_duplicates_and_bursts_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "text_model", TextModel(path=tmp_path / "missing.joblib"))
    copy = "Amazing product, works perfectly and arrived quickly, highly recommend to everyone"
    reviews = [
        _review(copy, 5, date="3 March 2024", verified=False),
        _review(copy + "!", 5, date="3 March 2024", verified=False),
        _review("Great", 5, date="3 March 2024"),
        _review("The strap broke after two weeks and support never replied to my emails.", 2, verified=True),
    ]
    body = client.post("/analyze", json={"reviews": reviews}).json()
    labels = [r["label"] for r in body["results"]]
    assert labels[:3] == ["suspicious"] * 3
    assert labels[3] == "genuine"
    assert body["model_loaded"] is False


def test_trained_model_is_used(tmp_path, monkeypatch):
    # Tiny fixture just to check the wiring between train.py and the API, not model quality.
    fake = ["best product ever buy now", "best purchase ever buy it now", "buy now best ever"] * 4
    real = ["battery lasts two days with heavy use", "the zipper jammed on day three", "fits well, sleeves run long"] * 4
    pipeline = build_pipeline().fit(fake + real, ["CG"] * len(fake) + ["OR"] * len(real))
    path = tmp_path / "model.joblib"
    joblib.dump({"pipeline": pipeline, "positive_label": "CG"}, path)
    monkeypatch.setattr(main, "text_model", TextModel(path=path))

    body = client.post("/analyze", json={"reviews": [_review("best product ever buy now"), _review("the zipper jammed on day three")]}).json()
    assert body["model_loaded"] is True
    scores = [r["model_score"] for r in body["results"]]
    assert scores[0] > scores[1]
