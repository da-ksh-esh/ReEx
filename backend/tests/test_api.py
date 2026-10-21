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


class StubModel:
    loaded = True

    def __init__(self, scores):
        self.scores = scores

    def predict_fake_proba(self, texts):
        return self.scores[: len(texts)]


def test_wording_reason_only_when_confident_and_flagged(monkeypatch):
    monkeypatch.setattr(main, "text_model", StubModel([0.9, 0.68, 0.75, 0.6]))
    reviews = [
        _review("The battery easily lasts a full week of heavy daily typing sessions."),
        _review("Keycaps feel slightly sandy but the stabilisers on the spacebar rattle a bit."),
        _review("Bluetooth pairing with my laptop dropped twice during the first evening."),
        _review("Nice", 5, verified=False),
    ]
    results = client.post("/analyze", json={"reviews": reviews}).json()["results"]
    wording = "Wording resembles known fake reviews"

    # confident + flagged -> reason shown
    assert results[0]["label"] == "suspicious" and wording in results[0]["reasons"]
    # not confident, not flagged -> no reason
    assert results[1]["label"] == "genuine" and wording not in results[1]["reasons"]
    # confident but overall genuine -> reason hidden to stay consistent with the label
    assert results[2]["label"] == "genuine" and wording not in results[2]["reasons"]
    # flagged by the rules, model not confident -> only the rule reasons
    assert results[3]["label"] == "suspicious" and wording not in results[3]["reasons"]
    for r in results:
        if r["label"] == "genuine":
            assert wording not in r["reasons"]


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
