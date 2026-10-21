import sys

import pandas as pd

import benchmark


def test_benchmark_runs_end_to_end(tmp_path, monkeypatch):
    fake = ["best product ever buy it now", "best purchase ever buy now friends", "amazing buy now best ever"]
    real = ["the zipper jammed on day three", "battery lasts two days of heavy use", "sleeves run long but fit well"]
    rows = []
    for cat in ["Books_5", "Toys_5"]:
        for i in range(6):
            rows.append({"category": cat, "label": "CG", "text_": f"{fake[i % 3]} {cat} {i}"})
            rows.append({"category": cat, "label": "OR", "text_": f"{real[i % 3]} {cat} {i}"})
    rows.append(dict(rows[0]))  # exact duplicate, must be dropped
    data = tmp_path / "data.csv"
    pd.DataFrame(rows).to_csv(data, index=False)
    ext = tmp_path / "ext.csv"
    pd.DataFrame({"text": fake + real, "label": ["fake"] * 3 + ["real"] * 3}).to_csv(ext, index=False)
    out = tmp_path / "report.md"

    monkeypatch.setattr(
        sys,
        "argv",
        ["benchmark.py", "--data", str(data), "--external", str(ext), "--models", "LR (deployed)", "Naive Bayes", "--out", str(out)],
    )
    benchmark.main()

    report = out.read_text(encoding="utf-8")
    assert "24 reviews after removing exact duplicates" in report
    assert "| LR (deployed) |" in report and "| Naive Bayes |" in report
    assert "Cross-category mean" in report and "External acc" in report
