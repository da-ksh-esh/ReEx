"""Compare text classifiers for fake-review detection, measuring how well each generalizes.

Three evaluations, each meant to answer a different question:
  in-domain      stratified 80/20 split: how well does it fit this dataset?
  cross-category train on 9 product categories, test on the 10th, for each category:
                 does it still work on products it never saw? (no extra data needed)
  external       optional; train on the whole dataset, test on another labelled CSV
                 (e.g. the Ott deceptive opinion spam corpus): does it work on other kinds of fakes?

Exact duplicate texts are removed before any split, so the same review can never sit in both the
training and the test set.

    python benchmark.py --data data/fake_reviews_dataset.csv
    python benchmark.py --data data/fake_reviews_dataset.csv --external data/ott.csv \\
        --ext-text-col text --ext-label-col deceptive --ext-fake-label deceptive
"""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

from app.store import normalize
from train import build_pipeline


def _tfidf(**kw) -> TfidfVectorizer:
    return TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, max_features=200_000, **kw)


# name -> factory. "LR (deployed)" is exactly what train.py ships.
MODELS = {
    "LR (deployed)": build_pipeline,
    "LR + trigrams": lambda: Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 3), min_df=2, sublinear_tf=True, max_features=300_000)),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
        ]
    ),
    # If accuracy drops a lot without stop words, the model relies on writing style
    # (how GPT-2 uses "the", "and", ...) more than on what the review says.
    "LR, no stop words": lambda: Pipeline(
        [("tfidf", _tfidf(stop_words="english")), ("clf", LogisticRegression(max_iter=1000, class_weight="balanced"))]
    ),
    "Naive Bayes": lambda: Pipeline([("tfidf", _tfidf()), ("clf", MultinomialNB())]),
    "Linear SVM": lambda: Pipeline([("tfidf", _tfidf()), ("clf", LinearSVC(class_weight="balanced"))]),
    # Trees are slow on huge sparse inputs, so Random Forest gets a smaller vocabulary.
    "Random Forest": lambda: Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=5, sublinear_tf=True, max_features=20_000)),
            ("clf", RandomForestClassifier(n_estimators=150, n_jobs=-1, random_state=42, class_weight="balanced")),
        ]
    ),
}


def _scores(pipeline, texts, y_true, fake_label) -> dict:
    pred = pipeline.predict(texts)
    if hasattr(pipeline, "predict_proba"):
        classes = list(pipeline.classes_)
        score = pipeline.predict_proba(texts)[:, classes.index(fake_label)]
    else:
        score = pipeline.decision_function(texts)
        if pipeline.classes_[1] != fake_label:
            score = -score
    y_bin = (np.asarray(y_true) == fake_label).astype(int)
    return {
        "accuracy": accuracy_score(y_true, pred),
        "f1": f1_score(y_true, pred, average="macro"),
        "auc": roc_auc_score(y_bin, score) if len(set(y_bin)) == 2 else float("nan"),
    }


def load(path: Path, text_col: str, label_col: str, extra_cols=()) -> pd.DataFrame:
    df = pd.read_csv(path).dropna(subset=[text_col, label_col])
    df = df.rename(columns={text_col: "text", label_col: "label"})[["text", "label", *extra_cols]]
    df["text"] = df["text"].astype(str)
    df["label"] = df["label"].astype(str)
    before = len(df)
    df = df[~df["text"].map(normalize).duplicated()].reset_index(drop=True)
    print(f"{path.name}: {len(df)} reviews ({before - len(df)} exact duplicates removed)")
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--text-col", default="text_")
    parser.add_argument("--label-col", default="label")
    parser.add_argument("--category-col", default="category")
    parser.add_argument("--fake-label", default="CG")
    parser.add_argument("--external", type=Path, help="second labelled CSV to test on")
    parser.add_argument("--ext-text-col", default="text")
    parser.add_argument("--ext-label-col", default="label")
    parser.add_argument("--ext-fake-label", default="fake", help="label value meaning fake in the external CSV")
    parser.add_argument("--models", nargs="*", choices=list(MODELS), default=list(MODELS))
    parser.add_argument("--skip-cross-category", action="store_true", help="faster: skip the 10-fold category test")
    parser.add_argument("--out", type=Path, default=Path("reports/benchmark.md"))
    args = parser.parse_args()

    df = load(args.data, args.text_col, args.label_col, extra_cols=[args.category_col])
    ext = None
    if args.external:
        ext = load(args.external, args.ext_text_col, args.ext_label_col)
        # Map the external labels onto the training labels so one model scores both.
        other = next(lbl for lbl in df["label"].unique() if lbl != args.fake_label)
        ext["label"] = np.where(ext["label"] == args.ext_fake_label, args.fake_label, other)

    train_df, test_df = train_test_split(df, test_size=0.2, random_state=42, stratify=df["label"])
    categories = sorted(df[args.category_col].unique())

    rows = []
    for name in args.models:
        start = time.time()
        row = {"model": name}

        model = MODELS[name]().fit(train_df["text"], train_df["label"])
        for k, v in _scores(model, test_df["text"], test_df["label"], args.fake_label).items():
            row[f"in_{k}"] = v

        if not args.skip_cross_category:
            accs = []
            for cat in categories:
                held_out = df[args.category_col] == cat
                m = MODELS[name]().fit(df.loc[~held_out, "text"], df.loc[~held_out, "label"])
                accs.append(_scores(m, df.loc[held_out, "text"], df.loc[held_out, "label"], args.fake_label)["accuracy"])
            row["cross_mean"] = float(np.mean(accs))
            row["cross_worst"] = float(np.min(accs))
            row["cross_worst_cat"] = categories[int(np.argmin(accs))]

        if ext is not None:
            full = MODELS[name]().fit(df["text"], df["label"])
            for k, v in _scores(full, ext["text"], ext["label"], args.fake_label).items():
                row[f"ext_{k}"] = v

        row["seconds"] = time.time() - start
        rows.append(row)
        print(f"  {name}: done in {row['seconds']:.0f}s")

    report = render_report(rows, args, len(df), ext)
    print("\n" + report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"Saved to {args.out}")


def render_report(rows, args, n_reviews, ext) -> str:
    pct = lambda x: f"{100 * x:.1f}%"
    header = ["Model", "In-domain acc", "F1", "AUC"]
    if not args.skip_cross_category:
        header += ["Cross-category mean", "Worst category"]
    if ext is not None:
        header += ["External acc", "External AUC"]
    lines = [
        "# Fake-review classifier benchmark",
        "",
        f"Training data: `{args.data.name}`, {n_reviews} reviews after removing exact duplicates.",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for r in rows:
        cells = [r["model"], pct(r["in_accuracy"]), f"{r['in_f1']:.3f}", f"{r['in_auc']:.3f}"]
        if not args.skip_cross_category:
            cells += [pct(r["cross_mean"]), f"{pct(r['cross_worst'])} ({r['cross_worst_cat']})"]
        if ext is not None:
            cells += [pct(r["ext_accuracy"]), f"{r['ext_auc']:.3f}"]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "- **In-domain**: stratified 80/20 split of the training data.",
        "- **Cross-category**: trained on 9 product categories, tested on the 10th, repeated for each category.",
    ]
    if ext is not None:
        lines.append(f"- **External**: trained on all training data, tested on `{args.external.name}` ({len(ext)} reviews).")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
