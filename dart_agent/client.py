"""OpenDART API 저수준 클라이언트.

담당 범위: 인증, 에러코드 해석, 호출 간격 제어, 응답 캐싱.
비즈니스 로직(비율 계산, 비교)은 여기 두지 않는다.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

BASE = "https://opendart.fss.or.kr/api"

# OpenDART 공통 status 코드
STATUS = {
    "000": "정상",
    "010": "등록되지 않은 인증키",
    "011": "사용할 수 없는 인증키 (일시적 사용중지)",
    "012": "접근할 수 없는 IP",
    "013": "조회된 데이터 없음",
    "014": "파일이 존재하지 않음",
    "020": "요청 제한 초과 (일 20,000건)",
    "021": "조회 가능한 회사 개수 초과",
    "100": "필드의 부적절한 값",
    "101": "부적절한 접근",
    "800": "시스템 점검 중",
    "900": "정의되지 않은 오류",
    "901": "사용자 계정의 개인정보 보유기간 만료",
}

# 보고서 코드
REPRT = {
    "사업보고서": "11011",
    "3분기": "11014",
    "반기": "11012",
    "1분기": "11013",
}


class DartError(RuntimeError):
    def __init__(self, status: str, message: str = ""):
        self.status = status
        self.message = message or STATUS.get(status, "알 수 없는 오류")
        super().__init__(f"[{status}] {self.message}")


class NoData(DartError):
    """status 013 — 조회 결과 없음. 호출 실패가 아니라 '해당 연도 미제출' 등 정상 상황."""


@dataclass
class DartClient:
    api_key: str | None = None
    cache_dir: Path = field(default_factory=lambda: Path.home() / "dart-agent" / "cache")
    min_interval: float = 0.12  # 초당 ~8회. 일 한도 20,000건을 태우지 않기 위한 최소 간격
    timeout: int = 30
    use_cache: bool = True

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("DART_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError(
                "DART_API_KEY가 없습니다. https://opendart.fss.or.kr 에서 40자리 키를 발급받아 "
                ".env에 DART_API_KEY=... 로 넣어주세요."
            )
        if len(self.api_key) != 40:
            raise ValueError(f"인증키는 40자리여야 합니다 (현재 {len(self.api_key)}자리).")
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_call = 0.0
        self._session = requests.Session()
        self.call_count = 0

    # ---------- 내부 ----------

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_call = time.monotonic()

    def _cache_path(self, endpoint: str, params: dict[str, Any], ext: str) -> Path:
        key = json.dumps({"e": endpoint, **params}, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(key.encode()).hexdigest()[:20]
        return self.cache_dir / f"{endpoint.split('.')[0]}_{digest}.{ext}"

    # ---------- JSON 엔드포인트 ----------

    def get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        """JSON 엔드포인트 호출. status 013은 NoData로 올린다."""
        params = {k: v for k, v in params.items() if v is not None}
        cache = self._cache_path(endpoint, params, "json")
        if self.use_cache and cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))

        self._throttle()
        resp = self._session.get(
            f"{BASE}/{endpoint}",
            params={"crtfc_key": self.api_key, **params},
            timeout=self.timeout,
        )
        self.call_count += 1
        resp.raise_for_status()
        data = resp.json()

        status = str(data.get("status", "900"))
        if status == "013":
            raise NoData(status, data.get("message", ""))
        if status != "000":
            raise DartError(status, data.get("message", ""))

        if self.use_cache:
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def rows(self, endpoint: str, **params: Any) -> list[dict[str, Any]]:
        """`list` 키를 리스트로 반환. 데이터 없으면 빈 리스트."""
        try:
            return self.get(endpoint, **params).get("list", [])
        except NoData:
            return []

    # ---------- 바이너리(zip) 엔드포인트 ----------

    def get_zip(self, endpoint: str, **params: Any) -> zipfile.ZipFile:
        params = {k: v for k, v in params.items() if v is not None}
        cache = self._cache_path(endpoint, params, "zip")
        if self.use_cache and cache.exists() and cache.stat().st_size > 0:
            return zipfile.ZipFile(cache)

        self._throttle()
        resp = self._session.get(
            f"{BASE}/{endpoint}",
            params={"crtfc_key": self.api_key, **params},
            timeout=max(self.timeout, 90),
        )
        self.call_count += 1
        resp.raise_for_status()

        # 오류일 때는 zip이 아니라 XML/JSON 본문이 온다
        head = resp.content[:2]
        if head != b"PK":
            body = resp.content[:500].decode("utf-8", "replace")
            status = "900"
            for code in STATUS:
                if f"<status>{code}</status>" in body or f'"status":"{code}"' in body:
                    status = code
                    break
            raise DartError(status, body.strip()[:200])

        if self.use_cache:
            cache.write_bytes(resp.content)
        return zipfile.ZipFile(io.BytesIO(resp.content))

    # ---------- 도메인 엔드포인트 ----------

    def company(self, corp_code: str) -> dict[str, Any]:
        """기업개황: 정식명칭, 대표자, 업종, 설립일, 결산월, 상장일 등."""
        return self.get("company.json", corp_code=corp_code)

    def filings(
        self,
        corp_code: str | None = None,
        bgn_de: str | None = None,
        end_de: str | None = None,
        pblntf_ty: str | None = None,
        last_reprt_at: str = "Y",
        page_count: int = 100,
    ) -> list[dict[str, Any]]:
        """공시목록. pblntf_ty: A=정기공시, B=주요사항보고, C=발행공시, D=지분공시 ...
        last_reprt_at=Y면 정정된 최종 보고서만."""
        return self.rows(
            "list.json",
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_ty=pblntf_ty,
            last_reprt_at=last_reprt_at,
            page_count=page_count,
        )

    def financials_all(
        self, corp_code: str, year: int, reprt_code: str = "11011", fs_div: str = "CFS"
    ) -> list[dict[str, Any]]:
        """단일회사 전체 재무제표 (XBRL 표준계정 전량). fs_div: CFS=연결, OFS=별도."""
        return self.rows(
            "fnlttSinglAcntAll.json",
            corp_code=corp_code,
            bsns_year=str(year),
            reprt_code=reprt_code,
            fs_div=fs_div,
        )

    def employees(self, corp_code: str, year: int, reprt_code: str = "11011") -> list[dict[str, Any]]:
        """직원 현황: 사업부문별 인원, 평균 근속연수, 연간급여총액."""
        return self.rows(
            "empSttus.json", corp_code=corp_code, bsns_year=str(year), reprt_code=reprt_code
        )

    def dividends(self, corp_code: str, year: int, reprt_code: str = "11011") -> list[dict[str, Any]]:
        """배당에 관한 사항."""
        return self.rows(
            "alotMatter.json", corp_code=corp_code, bsns_year=str(year), reprt_code=reprt_code
        )

    def major_shareholders(
        self, corp_code: str, year: int, reprt_code: str = "11011"
    ) -> list[dict[str, Any]]:
        """최대주주 현황."""
        return self.rows(
            "hyslrSttus.json", corp_code=corp_code, bsns_year=str(year), reprt_code=reprt_code
        )

    def document(self, rcept_no: str) -> zipfile.ZipFile:
        """공시서류 원본 파일(XML) zip."""
        return self.get_zip("document.xml", rcept_no=rcept_no)
