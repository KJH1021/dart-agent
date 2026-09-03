"""DART 기업분석 에이전트.

수집(client/corpcode) → 정규화(financials) → 계산(metrics/compare) → 서술추적(filings)
→ 리포트(report) 의 4계층 구조.
"""

from .client import DartClient, DartError, NoData
from .compare import Analyzer, CompanyProfile
from .corpcode import CorpIndex
from .filings import FilingTracker

__all__ = [
    "DartClient",
    "DartError",
    "NoData",
    "CorpIndex",
    "Analyzer",
    "CompanyProfile",
    "FilingTracker",
]
__version__ = "0.1.0"
