# ReEx - Review Examiner

A Chrome extension that reads the reviews on an e-commerce product page and estimates how many look fake and how many look genuine.

```
extension/  Manifest V3 extension: pulls reviews from the page, shows the results in a popup
backend/    FastAPI service: scores each review and the product as a whole
```

## How it works

1. You click **Analyze reviews** in the popup. It injects `extractor.js` into the current tab.
2. On Amazon, ReEx can also check more reviews than the page shows (**Reviews to check** in the popup, default up to 100). `loadReviewsInPage` in `popup.js`:
   - loads the product's reviews page in an invisible frame inside the product page you're on, so no tab opens (Amazon allows this because both pages are on the same Amazon site)
   - presses Amazon's own "Show 10 more reviews" button, waiting 1.5 s between presses
   - reads the reviews and removes the frame (it keeps going and cleans up even if you close the popup)
   
   Notes:
   - This needs you to be **signed in to Amazon**: signed out, Amazon only shows the first 10.
   - Amazon's list ends at about **100 reviews** even when a product has more, and 100 takes about 20 seconds.
   - It stops straight away on a sign-in or CAPTCHA page instead of trying to get past it.
   - Badges only appear on the reviews visible on the product page; the popup lists all of them.
3. The extractor finds the reviews:
   - **Amazon**: `data-hook` selectors (body, title, rating, date, verified badge, author, review and reviewer IDs)
   - **Flipkart**: looks for each "Certified Buyer" label and walks up to the review around it (brittle, because Flipkart's class names change often)
   - **Any other site**: `schema.org/Review` objects embedded as JSON-LD
4. The reviews go to `POST /analyze`, which scores each one from 0 to 1 by combining:
   - **Text model** (optional): TF-IDF + Logistic Regression trained with `train.py`
   - **Rule-based signals**: near-duplicate text, several top ratings posted on the same date, very short high ratings, no verified purchase, a rating far from the product's average, and star ratings that contradict the text (a 5★ review with negative wording, or the reverse)
   - **History signals**, from pages analyzed before (see [What the backend stores](#what-the-backend-stores)): the same long review text seen on a different product, and reviewers whose reviews were flagged on 2 or more other products (Amazon only, using the reviewer's profile ID)
   
   Reviews not in English are marked **not analyzed** and left out of every count, because the model and the sentiment analyser only understand English. Very short reviews are always analyzed, since language detection is unreliable on a few words. Hinglish written in Latin letters is detected as English, so it gets analyzed.
5. Sentiment of each review (title + text) comes from [VADER](https://github.com/cjhutto/vaderSentiment), a word-list-based analyser that needs no training. When the text scores neutral, the star rating decides.
6. The popup shows:
   - a trust score out of 100, how many reviews are suspicious vs. genuine, and why each one was flagged
   - the store's rating next to the average of the analyzed reviews, with and without the suspicious ones (e.g. "4.4★ → 4.0★ without the suspicious ones")
   - how many genuine reviewers were positive, negative or neutral
   - a **"Wrong?"** button on each review, which sends your correction to `POST /feedback` for later retraining
7. Each review on the page also gets a badge: green "looks genuine", red "likely suspicious" or grey "not analyzed". Hover over it to see the reasons.
8. Results are cached in the browser for each page URL. Reopening the popup shows the last result straight away. Clicking **Analyze** again reuses it without calling the backend, as long as the page shows the same reviews (for up to a day).

If no model has been trained yet, the backend scores reviews with the rules alone, and the popup says so.

## What the backend stores

The backend keeps a small SQLite database at `backend/data/reex.sqlite3`, which is excluded from git:

- **`reviews`**: one row per product and review: a SHA-256 hash of the normalized review text (never the text itself), whether it's long enough to count as a copy (12+ words), the reviewer's profile ID if the page shows one, and whether ReEx flagged it. This is what makes the history signals work. It only contains pages you have analyzed yourself.
- **`feedback`**: reviews someone marked as wrongly judged, with the review text, the stars, ReEx's verdict and the user's correction. The text is kept because retraining needs it. It's public review text from the product page.

Delete the file to reset both.

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

1. Download the Fake Reviews Dataset (Salminen et al., 2022) from [OSF](https://osf.io/tyue9/) (no account needed) or [Kaggle](https://www.kaggle.com/datasets/mexwell/fake-reviews-dataset), and save it as `backend/data/fake_reviews_dataset.csv`.
2. Train:

```bash
cd backend
.venv/Scripts/python train.py --data data/fake_reviews_dataset.csv
```

This prints a classification report and saves `backend/models/baseline.joblib`. Restart the server so it loads the model. To use a different CSV, pass `--text-col`, `--label-col` and `--fake-label`.

> **Caveat:** the fakes in this dataset were written by a machine (GPT-2), so the model mostly learns "does this sound like generated text?". It won't catch paid reviews written by humans. To cover those, combine it with other datasets (Ott et al. deceptive opinion spam, YelpChi/YelpNYC), and test on data from a domain you didn't train on.

### Benchmark the models

```bash
cd backend
.venv/Scripts/python benchmark.py --data data/fake_reviews_dataset.csv
```

This compares Logistic Regression (with trigrams and without stop words as well), Naive Bayes, Linear SVM and Random Forest. Each one is evaluated three ways:
- **In-domain:** a normal 80/20 split.
- **Cross-category:** train on 9 product categories, test on the 10th, repeated for each category.
- **External (optional):** test on a second labelled CSV with `--external`, e.g. the Ott corpus.

Exact duplicate texts are removed before splitting, so no review appears in both the training and the test set. Results are saved to `backend/reports/benchmark.md`. The full run takes several minutes; `--skip-cross-category` makes it much faster.

### Extension

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and select the `extension/` folder.
3. Open an Amazon or Flipkart product page, scroll to the reviews, and click the ReEx icon.

The backend URL is set by `API_BASE` in `extension/popup.js`. If you change it, also update `host_permissions` in `manifest.json`.

## Tests

```bash
cd backend
.venv/Scripts/python -m pytest
```

## Known limitations

- Beyond the reviews on the page, only Amazon is supported, up to Amazon's own limit of about 100 reviews, and only while signed in. Flipkart and other sites use just the reviews on the page.
- Site selectors break when stores change their HTML. Flipkart breaks most often.
- Scores are estimates. The UI says "likely suspicious", not "fake", on purpose.

## Next steps

- Fine-tune a transformer (DistilBERT/RoBERTa) to replace the TF-IDF baseline
- Load more reviews on Flipkart; reach past Amazon's 100 by loading each star rating separately
- Add more site adapters (Myntra, Meesho, ...) and a generic DOM-heuristic extractor
- Run a small model in the browser (ONNX Runtime Web / transformers.js), so reviews never leave the browser
