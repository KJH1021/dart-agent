"""XBRL 원본 계정 → 표준 스키마 정규화.

OpenDART의 fnlttSinglAcntAll은 기업마다 계정 명칭이 제각각이라
(예: '매출액' / '수익(매출액)' / '영업수익') account_id(IFRS 표준계정코드)를 1순위,
계정명 정규식을 2순위로 두고 매핑한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from .client import DartClient

# 표준 항목: (표준 id 목록, 계정명 fallback 정규식, 재무제표 구분)
SPEC: dict[str, tuple[list[str], str | None, tuple[str, ...]]] = {
    # 재무상태표
    "자산총계": (["ifrs-full_Assets"], r"^자산총계$", ("BS",)),
    "유동자산": (["ifrs-full_CurrentAssets"], r"^유동자산$", ("BS",)),
    "재고자산": (["ifrs-full_Inventories"], r"^재고자산$", ("BS",)),
    "매출채권": (
        ["ifrs-full_TradeAndOtherCurrentReceivables", "dart_ShortTermTradeReceivable"],
        r"^매출채권",
        ("BS",),
    ),
    "현금성자산": (["ifrs-full_CashAndCashEquivalents"], r"현금및현금성자산", ("BS",)),
    "유형자산": (["ifrs-full_PropertyPlantAndEquipment"], r"^유형자산$", ("BS",)),
    "부채총계": (["ifrs-full_Liabilities"], r"^부채총계$", ("BS",)),
    "유동부채": (["ifrs-full_CurrentLiabilities"], r"^유동부채$", ("BS",)),
    "매입채무": (
        ["ifrs-full_TradeAndOtherCurrentPayables", "dart_ShortTermTradePayables"],
        r"^매입채무",
        ("BS",),
    ),
    "자본총계": (["ifrs-full_Equity"], r"^자본총계$", ("BS",)),
    "지배주주지분": (["ifrs-full_EquityAttributableToOwnersOfParent"], None, ("BS",)),
    # 손익계산서
    "매출액": (["ifrs-full_Revenue"], r"^(매출액|수익\(매출액\)|영업수익)$", ("IS", "CIS")),
    "매출원가": (["ifrs-full_CostOfSales"], r"^매출원가$", ("IS", "CIS")),
    "매출총이익": (["ifrs-full_GrossProfit"], r"^매출총이익$", ("IS", "CIS")),
    "판관비": (
        ["dart_TotalSellingGeneralAdministrativeExpenses"],
        r"^판매비와관리비$",
        ("IS", "CIS"),
    ),
    "영업이익": (
        ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
        r"^영업이익(\(손실\))?$",
        ("IS", "CIS"),
    ),
    "세전이익": (["ifrs-full_ProfitLossBeforeTax"], r"법인세비용차감전", ("IS", "CIS")),
    "당기순이익": (["ifrs-full_ProfitLoss"], r"^당기순이익(\(손실\))?$", ("IS", "CIS")),
    # 현금흐름표
    "영업활동현금흐름": (
        ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
        r"영업활동.*현금흐름",
        ("CF",),
    ),
    "투자활동현금흐름": (
        ["ifrs-full_CashFlowsFromUsedInInvestingActivities"],
        r"투자활동.*현금흐름",
        ("CF",),
    ),
    "CAPEX": (
        [
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "dart_PurchaseOfPropertyPlantAndEquipment",
        ],
        r"유형자산의?\s*(취득|증가)",
        ("CF",),
    ),
    "감가상각비": (
        ["ifrs-full_DepreciationAndAmortisationExpense"],
        r"감가상각비및무형자산상각비|^감가상각비$",
        ("CF", "IS", "CIS"),
    ),
}


def _to_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace(" ", "")
    if s in {"", "-", "－", "N/A"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def normalize(rows: list[dict]) -> dict[str, float]:
    """API 응답 행 목록 → {표준항목: 당기금액}."""
    out: dict[str, float] = {}
    if not rows:
        return out

    by_id: dict[str, list[dict]] = {}
    for r in rows:
        by_id.setdefault((r.get("account_id") or "").strip(), []).append(r)

    for item, (ids, pattern, sj_divs) in SPEC.items():
        picked = None

        for aid in ids:
            for r in by_id.get(aid, []):
                if r.get("sj_div") in sj_divs:
                    picked = r
                    break
            if picked:
                break

        if picked is None and pattern:
            rx = re.compile(pattern)
            for r in rows:
                if r.get("sj_div") not in sj_divs:
                    continue
                name = re.sub(r"\s+", "", r.get("account_nm", ""))
                if rx.search(name):
                    picked = r
                    break

        if picked is not None:
            val = _to_number(picked.get("thstrm_amount"))
            if val is not None:
                out[item] = val

    # 파생: 매출총이익이 공시되지 않은 경우 보완
    if "매출총이익" not in out and {"매출액", "매출원가"} <= out.keys():
        out["매출총이익"] = out["매출액"] - out["매출원가"]
    return out


@dataclass
class FinancialsFetcher:
    client: DartClient
    reprt_code: str = "11011"  # 사업보고서

    def one_year(self, corp_code: str, year: int) -> tuple[dict[str, float], str]:
        """연결 우선, 없으면 별도. (표준항목dict, 사용한 fs_div) 반환."""
        for fs_div in ("CFS", "OFS"):
            rows = self.client.financials_all(corp_code, year, self.reprt_code, fs_div)
            data = normalize(rows)
            if data:
                return data, fs_div
        return {}, ""

    def series(self, corp_code: str, years: list[int]) -> pd.DataFrame:
        """연도 × 표준항목 DataFrame. 단위는 원."""
        records = []
        for y in years:
            data, fs_div = self.one_year(corp_code, y)
            if not data:
                continue
            records.append({"연도": y, "fs_div": fs_div, **data})
        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records).set_index("연도").sort_index()
