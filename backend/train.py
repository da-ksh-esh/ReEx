"""Train the baseline fake-review text classifier (TF-IDF + Logistic Regression).

Defaults match the "Fake Reviews Dataset" (Salminen et al., 2022) on Kaggle, whose columns are
`category, rating, label, text_` with label `CG` (computer-generated / fake) or `OR` (original).
Any CSV works if you point the column / label flags at it.

    python train.py --data data/fake_reviews_dataset.csv
"""

import argparse
from pathlib import Path

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from app.model import DEFAULT_MODEL_PATH


def build_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, max_features=200_000)),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
        ]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", required=True, type=Path, help="CSV file with review text and labels")
    parser.add_argument("--text-col", default="text_")
    parser.add_argument("--label-col", default="label")
    parser.add_argument("--fake-label", default="CG", help="label value that means fake")
    parser.add_argument("--out", type=Path, default=DEFAULT_MODEL_PATH)
    args = parser.parse_args()

    df = pd.read_csv(args.data).dropna(subset=[args.text_col, args.label_col])
    texts = df[args.text_col].astype(str)
    labels = df[args.label_col].astype(str)
    if args.fake_label not in set(labels):
        raise SystemExit(f"Fake label {args.fake_label!r} not found in column {args.label_col!r}")

    x_train, x_test, y_train, y_test = train_test_split(
        texts, labels, test_size=0.2, random_state=42, stratify=labels
    )
    pipeline = build_pipeline().fit(x_train, y_train)
    print(classification_report(y_test, pipeline.predict(x_test), digits=3))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipeline, "positive_label": args.fake_label, "trained_on": str(args.data)}, args.out)
    print(f"Saved model to {args.out}")


if __name__ == "__main__":
    main()
