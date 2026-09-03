"""표준 재무 항목 → 재무비율.

반도체 소재·장비·소자사 비교를 염두에 두고 골랐다:
장치산업이라 CAPEX/매출, 감가상각 부담, 재고회전이 사이클 국면을 그대로 드러낸다.
"""

from __future__ import annotations

import pandas as pd

PCT = "%"


def _safe(num, den, scale: float = 100.0):
    """0 나눗셈과 결측을 None으로 흡수."""
    if num is None or den in (None, 0):
        return None
    try:
        if pd.isna(num) or pd.isna(den) or den == 0:
            return None
    except TypeError:
        return None
    return num / den * scale


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """financials.series() 결과 → 비율 DataFrame (연도 인덱스 유지)."""
    if df.empty:
        return df

    g = lambda col: df[col] if col in df.columns else pd.Series(index=df.index, dtype=float)

    매출 = g("매출액")
    영업이익 = g("영업이익")
    순이익 = g("당기순이익")
    자산 = g("자산총계")
    부채 = g("부채총계")
    자본 = g("자본총계")
    유동자산 = g("유동자산")
    유동부채 = g("유동부채")
    재고 = g("재고자산")
    매출채권 = g("매출채권")
    매출원가 = g("매출원가")
    capex = g("CAPEX").abs()
    감가상각 = g("감가상각비").abs()
    영업CF = g("영업활동현금흐름")

    out = pd.DataFrame(index=df.index)

    # 수익성
    out["매출총이익률(%)"] = g("매출총이익") / 매출 * 100
    out["영업이익률(%)"] = 영업이익 / 매출 * 100
    out["순이익률(%)"] = 순이익 / 매출 * 100
    out["ROE(%)"] = 순이익 / 자본 * 100
    out["ROA(%)"] = 순이익 / 자산 * 100

    # 안정성
    out["부채비율(%)"] = 부채 / 자본 * 100
    out["유동비율(%)"] = 유동자산 / 유동부채 * 100
    out["자기자본비율(%)"] = 자본 / 자산 * 100

    # 활동성 — 장치산업 사이클 지표
    out["재고자산회전율(회)"] = 매출원가 / 재고
    out["재고회전일수(일)"] = 365 / (매출원가 / 재고)
    out["매출채권회전일수(일)"] = 매출채권 / 매출 * 365
    out["총자산회전율(회)"] = 매출 / 자산

    # 투자 강도
    out["CAPEX/매출(%)"] = capex / 매출 * 100
    out["감가상각/매출(%)"] = 감가상각 / 매출 * 100
    out["영업CF/매출(%)"] = 영업CF / 매출 * 100
    out["잉여현금흐름(억원)"] = (영업CF - capex) / 1e8

    # 규모 (억원 단위로 읽기 쉽게)
    out["매출액(억원)"] = 매출 / 1e8
    out["영업이익(억원)"] = 영업이익 / 1e8
    out["자산총계(억원)"] = 자산 / 1e8

    return out.round(2)


def yoy(df: pd.DataFrame, cols: list[str] | None = None) -> pd.DataFrame:
    """전년 대비 증감률(%)."""
    cols = cols or [c for c in df.columns if df[c].dtype.kind in "fi"]
    return (df[cols].pct_change() * 100).round(1)


def add_headcount(metrics: pd.DataFrame, emp_by_year: dict[int, int]) -> pd.DataFrame:
    """직원 수를 붙이고 1인당 매출을 계산한다. 인력 효율은 소재/장비사 비교에서 특히 유효."""
    if not emp_by_year:
        return metrics
    metrics = metrics.copy()
    metrics["직원수(명)"] = pd.Series(emp_by_year)
    if "매출액(억원)" in metrics.columns:
        metrics["1인당매출(백만원)"] = (
            metrics["매출액(억원)"] * 100 / metrics["직원수(명)"]
        ).round(1)
    return metrics
