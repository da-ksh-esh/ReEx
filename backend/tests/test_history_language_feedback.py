from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app import main
from app.language import is_english
from app.model import TextModel
from app.store import product_key

client = TestClient(main.app)

LONG_COPY = "This keyboard changed my life, the keys are smooth, the lights are bright and the build feels premium overall"


@pytest.fixture(autouse=True)
def no_model(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "text_model", TextModel(path=tmp_path / "missing.joblib"))


def _analyze(url, reviews):
    return client.post("/analyze", json={"url": url, "reviews": reviews}).json()


@pytest.mark.parametrize(
    "url, key",
    [
        ("https://www.amazon.in/Cosmic-Byte-CB-GK-23/dp/B09G8T1D2N/ref=sr_1_2?crid=x", "amazon:B09G8T1D2N"),
        ("https://www.amazon.in/dp/B09G8T1D2N", "amazon:B09G8T1D2N"),
        ("https://www.amazon.com/product-reviews/B09G8T1D2N/ref=cm", "amazon:B09G8T1D2N"),
        ("https://www.flipkart.com/some-phone/p/itm6ac6485515ae4?pid=MOBG6VF5Q82T3XRS&lid=x", "flipkart:MOBG6VF5Q82T3XRS"),
        ("https://www.flipkart.com/some-phone/p/itm6ac6485515ae4", "flipkart:itm6ac6485515ae4"),
        ("https://shop.example.com/products/mug/", "shop.example.com/products/mug"),
        (None, None),
    ],
)
def test_product_key(url, key):
    assert product_key(url) == key


@pytest.mark.parametrize(
    "text, english",
    [
        ("This product didn't complete even 4 weeks it stopped working!", True),
        ("Nice", True),  # too short to judge, kept
        ("Super product", True),
        ("sehr gut und schnell geliefert", False),
        ("muy buen teclado, lo recomiendo", False),
        ("यह कीबोर्ड बहुत अच्छा है", False),
        ("बढ़िया", False),  # non-Latin script is skipped even when short
    ],
)
def test_is_english(text, english):
    assert is_english(text) is english


def test_non_english_reviews_are_skipped_and_excluded_from_counts():
    body = _analyze(None, [{"text": "sehr gut und schnell geliefert", "rating": 5}, {"text": "Stopped working after two weeks of light use.", "rating": 1}])
    assert body["total"] == 1 and body["skipped"] == 1
    assert [r["label"] for r in body["results"]] == ["skipped", "genuine"]
    assert [r["index"] for r in body["results"]] == [0, 1]
    assert body["rating"]["rated"] == 1


def test_copy_on_another_product_is_flagged():
    _analyze("https://www.amazon.in/dp/AAAAAAAAAA", [{"text": LONG_COPY, "rating": 5}])
    body = _analyze("https://www.amazon.in/dp/BBBBBBBBBB", [{"text": LONG_COPY.upper() + "!!", "rating": 5}])
    assert "The same text appears in reviews of another product" in body["results"][0]["reasons"]


def test_reanalyzing_the_same_product_is_not_a_copy():
    url = "https://www.amazon.in/dp/AAAAAAAAAA"
    _analyze(url, [{"text": LONG_COPY, "rating": 5}])
    body = _analyze(url + "?ref=again", [{"text": LONG_COPY, "rating": 5}])
    assert not any("another product" in x for x in body["results"][0]["reasons"])


def test_short_reviews_are_not_treated_as_copies():
    _analyze("https://www.amazon.in/dp/AAAAAAAAAA", [{"text": "Good product, value for money", "rating": 5}])
    body = _analyze("https://www.amazon.in/dp/BBBBBBBBBB", [{"text": "Good product, value for money", "rating": 5}])
    assert not any("another product" in x for x in body["results"][0]["reasons"])


def test_reviewer_flagged_on_two_other_products():
    spammy = {"reviewer_id": "amzn1.account.SPAM", "rating": 5, "verified": False}
    # duplicates on the same page get the reviewer flagged on each product
    for asin in ["AAAAAAAAAA", "BBBBBBBBBB"]:
        dup = f"Best purchase ever made for {asin}, everyone in my family loves it and so will you, buy it now"
        _analyze(f"https://www.amazon.in/dp/{asin}", [{**spammy, "text": dup}, {**spammy, "text": dup + "!"}])

    body = _analyze(
        "https://www.amazon.in/dp/CCCCCCCCCC",
        [{**spammy, "verified": True, "text": "Solid keyboard with crisp switches, though the cable is a little short."}],
    )
    assert "This reviewer was flagged on 2 other products" in body["results"][0]["reasons"]


def test_store_keeps_only_hashes_of_review_text(temp_store):
    _analyze("https://www.amazon.in/dp/AAAAAAAAAA", [{"text": LONG_COPY, "rating": 5}])
    with closing(temp_store._connect()) as db:
        rows = db.execute("SELECT * FROM reviews").fetchall()
    assert len(rows) == 1 and LONG_COPY not in str(rows)


def test_feedback_is_stored(temp_store):
    res = client.post(
        "/feedback",
        json={"url": "https://www.amazon.in/dp/AAAAAAAAAA", "text": "Good one", "rating": 5, "predicted_label": "genuine", "user_label": "fake", "model_score": 0.59},
    )
    assert res.status_code == 200
    with closing(temp_store._connect()) as db:
        row = db.execute("SELECT product, text, predicted_label, user_label FROM feedback WHERE id = ?", (res.json()["id"],)).fetchone()
    assert row == ("amazon:AAAAAAAAAA", "Good one", "genuine", "fake")


def test_feedback_rejects_unknown_labels():
    res = client.post("/feedback", json={"text": "x", "predicted_label": "genuine", "user_label": "maybe"})
    assert res.status_code == 422
