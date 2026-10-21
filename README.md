# ReEx - Review Examiner

A Chrome extension that reads the reviews on an e-commerce product page and estimates how many look fake and how many look genuine.

```
extension/  Manifest V3 extension: pulls reviews from the page, shows the results in a popup
backend/    FastAPI service: scores each review and the product as a whole
```

## How it works

1. You click **Analyze reviews** in the popup. It injects `extractor.js` into the current tab.
2. The extractor finds the reviews:
   - **Amazon**: `data-hook` selectors (body, rating, date, verified badge, author)
   - **Flipkart**: looks for each "Certified Buyer" label and walks up to the review around it (brittle, because Flipkart's class names change often)
   - **Any other site**: `schema.org/Review` objects embedded as JSON-LD
3. The reviews go to `POST /analyze`, which scores each one from 0 to 1 by combining:
   - **Text model** (optional): TF-IDF + Logistic Regression trained with `train.py`
   - **Rule-based signals**: near-duplicate text, several top ratings posted on the same date, very short high ratings, no verified purchase, a rating far from the product's average, and star ratings that contradict the text (a 5★ review with negative wording, or the reverse)
4. Sentiment of each review (title + text) comes from [VADER](https://github.com/cjhutto/vaderSentiment), a word-list-based analyser that needs no training. When the text scores neutral, the star rating decides.
5. The popup shows:
   - a trust score out of 100, how many reviews are suspicious vs. genuine, and why each one was flagged
   - the store's rating next to the average of the analyzed reviews, with and without the suspicious ones (e.g. "4.4★ → 4.0★ without the suspicious ones")
   - how many genuine reviewers were positive, negative or neutral
6. Each review on the page also gets a badge: green "looks genuine" or red "likely suspicious". Hover over it to see the reasons.

If no model has been trained yet, the backend scores reviews with the rules alone, and the popup says so.

## Setup

### Backend (Windows, Git Bash)

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

On macOS or Linux, use `.venv/bin/python` instead. To check it's running, open http://localhost:8000/health, or see the API docs at http://localhost:8000/docs.

### Train the baseline model

1. Download the [Fake Reviews Dataset](https://www.kaggle.com/datasets/mexwell/fake-reviews-dataset) (Salminen et al., 2022) to `backend/data/fake_reviews_dataset.csv`.
2. Train:

```bash
cd backend
.venv/Scripts/python train.py --data data/fake_reviews_dataset.csv
```

This prints a classification report and saves `backend/models/baseline.joblib`. Restart the server so it loads the model. To use a different CSV, pass `--text-col`, `--label-col` and `--fake-label`.

> **Caveat:** the fakes in this dataset were written by a machine (GPT-2), so the model mostly learns "does this sound like generated text?". It won't catch paid reviews written by humans. To cover those, combine it with other datasets (Ott et al. deceptive opinion spam, YelpChi/YelpNYC), and test on data from a domain you didn't train on.

### Extension

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and select the `extension/` folder.
3. Open an Amazon or Flipkart product page, scroll to the reviews, and click the ReEx icon.

The backend URL is set by `API_URL` in `extension/popup.js`. If you change it, also update `host_permissions` in `manifest.json`.

## Tests

```bash
cd backend
.venv/Scripts/python -m pytest
```

## Known limitations

- Only reviews currently on the page are analyzed (usually 5-10). Opening the "See all reviews" page gives more.
- Site selectors break when stores change their HTML. Flipkart breaks most often.
- Scores are estimates. The UI says "likely suspicious", not "fake", on purpose.

## Next steps

- Fine-tune a transformer (DistilBERT/RoBERTa) to replace the TF-IDF baseline
- Follow pagination to collect more reviews
- Add more site adapters (Myntra, Meesho, ...) and a generic DOM-heuristic extractor
- Run a small model in the browser (ONNX Runtime Web / transformers.js), so reviews never leave the browser
