"""典型案例材料包回归测试。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.models import AnalysisConfig
from app.reporting.case_package import build_case_package


def test_case_package_exports_reproducible_materials(tmp_path: Path) -> None:
    """一份带标签的工业 CSV 应生成四类可复用案例材料。"""

    row_count = 150
    values = np.ones(row_count)
    values[75:95] += 5
    source = tmp_path / "valve1" / "0.csv"
    source.parent.mkdir()
    pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=row_count, freq="s"),
            "Pressure": values,
            "anomaly": [0] * 75 + [1] * 20 + [0] * 55,
            "changepoint": np.zeros(row_count),
        }
    ).to_csv(source, sep=";", index=False)

    package = build_case_package(
        source,
        config=AnalysisConfig(
            detector="mad",
            threshold=2.5,
            rolling_window=21,
            min_event_length=2,
            merge_gap=1,
            use_healthy_baseline=False,
        ),
        output_dir=tmp_path / "outputs",
    )

    for path in (
        package.markdown_path,
        package.events_csv_path,
        package.chart_html_path,
        package.summary_json_path,
    ):
        assert path.is_file()
        assert path.stat().st_size > 0
    assert "典型案例分析" in package.markdown_path.read_text(encoding="utf-8")
    assert "plotly" in package.chart_html_path.read_text(encoding="utf-8").lower()
