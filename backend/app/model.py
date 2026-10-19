"""Loads the text classifier produced by train.py, if one exists."""

from pathlib import Path
from typing import Optional

import joblib

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "baseline.joblib"


class TextModel:
    def __init__(self, path: Path = DEFAULT_MODEL_PATH):
        self.pipeline = None
        self.positive_index: Optional[int] = None
        if path.exists():
            bundle = joblib.load(path)
            self.pipeline = bundle["pipeline"]
            classes = list(self.pipeline.classes_)
            self.positive_index = classes.index(bundle["positive_label"])

    @property
    def loaded(self) -> bool:
        return self.pipeline is not None

    def predict_fake_proba(self, texts: list[str]) -> Optional[list[float]]:
        if not self.loaded or not texts:
            return None
        probs = self.pipeline.predict_proba(texts)
        return [float(p[self.positive_index]) for p in probs]
