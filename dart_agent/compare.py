"""다중 기업 비교 엔진 — 반도체 밸류체인 경쟁사 비교의 본체."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .client import DartClient, NoData
from .corpcode import Corp, CorpIndex
from .financials import FinancialsFetcher
from .metrics import add_headcount, compute


def _num(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    if s in {"", "-", "－"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class CompanyProfile:
    corp: Corp
    overview: dict
    financials: pd.DataFrame
    metrics: pd.DataFrame
    employees: dict[int, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.corp.corp_name

    @property
    def industry(self) -> str:
        return self.overview.get("induty_code", "")


class Analyzer:
    def __init__(self, client: DartClient, index: CorpIndex | None = None):
        self.client = client
        self.index = index or CorpIndex(client)
        self.fin = FinancialsFetcher(client)

    # ---------- 단일 기업 ----------

    def headcount(self, corp_code: str, years: list[int]) -> dict[int, int]:
        out: dict[int, int] = {}
        for y in years:
            rows = self.client.employees(corp_code, y)
            total = sum(v for r in rows if (v := _num(r.get("sm"))) is not None)
            if total:
                out[y] = int(total)
        return out

    def profile(self, query: str, years: list[int]) -> CompanyProfile:
        corp = self.index.resolve(query)
        notes: list[str] = []

        try:
            overview = self.client.company(corp.corp_code)
        except NoData:
            overview = {}
            notes.append("기업개황 조회 결과 없음")

        fin = self.fin.series(corp.corp_code, years)
        if fin.empty:
            notes.append(f"{years[0]}~{years[-1]}년 사업보고서 재무제표 없음")
            return CompanyProfile(corp, overview, fin, pd.DataFrame(), {}, notes)

        missing = [y for y in years if y not in fin.index]
        if missing:
            notes.append(f"미제출/미조회 연도: {', '.join(map(str, missing))}")
        if (fin["fs_div"] == "OFS").any():
            ofs = fin.index[fin["fs_div"] == "OFS"].tolist()
            notes.append(f"연결 미작성으로 별도재무제표 사용: {ofs}")

        emp = self.headcount(corp.corp_code, years)
        met = add_headcount(compute(fin), emp)
        return CompanyProfile(corp, overview, fin, met, emp, notes)

    # ---------- 다중 기업 ----------

    def compare(
        self, queries: list[str], years: list[int]
    ) -> tuple[dict[str, CompanyProfile], pd.DataFrame]:
        profiles: dict[str, CompanyProfile] = {}
        for q in queries:
            try:
                p = self.profile(q, years)
            except KeyError as e:
                print(f"  ! {e}")
                continue
            profiles[p.name] = p
            print(f"  · {p.name} ({p.corp.stock_code or '비상장'}) — {len(p.financials)}개 연도")
        return profiles, self.matrix(profiles, years[-1])

    def matrix(self, profiles: dict[str, CompanyProfile], year: int) -> pd.DataFrame:
        """특정 연도 기준 기업 × 지표 비교표."""
        keep = [
            "매출액(억원)",
            "영업이익(억원)",
            "영업이익률(%)",
            "순이익률(%)",
            "ROE(%)",
            "부채비율(%)",
            "유동비율(%)",
            "재고회전일수(일)",
            "CAPEX/매출(%)",
            "영업CF/매출(%)",
            "직원수(명)",
            "1인당매출(백만원)",
        ]
        rows = {}
        for name, p in profiles.items():
            if p.metrics.empty or year not in p.metrics.index:
                continue
            row = p.metrics.loc[year]
            rows[name] = {c: row.get(c) for c in keep}
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).T[keep]

    def trend(self, profiles: dict[str, CompanyProfile], metric: str) -> pd.DataFrame:
        """지표 하나를 연도 × 기업 형태로 펼친다."""
        data = {
            name: p.metrics[metric]
            for name, p in profiles.items()
            if not p.metrics.empty and metric in p.metrics.columns
        }
        return pd.DataFrame(data).sort_index()
