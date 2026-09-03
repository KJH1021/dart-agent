"""기업명 ↔ corp_code(8자리) ↔ 종목코드(6자리) 매핑.

corpCode.xml은 10만 건 규모라 매 호출마다 받지 않고 로컬에 캐시한다(기본 7일).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .client import DartClient

CACHE_TTL = 7 * 24 * 3600


@dataclass(frozen=True)
class Corp:
    corp_code: str
    corp_name: str
    stock_code: str  # 비상장이면 빈 문자열
    modify_date: str

    @property
    def listed(self) -> bool:
        return bool(self.stock_code.strip())


def _normalize(name: str) -> str:
    """'(주)삼성전자' / '삼성전자(주)' / '삼성 전자' 를 같은 키로."""
    name = re.sub(r"\(주\)|주식회사|㈜", "", name)
    return re.sub(r"\s+", "", name).lower()


class CorpIndex:
    def __init__(self, client: DartClient, cache_file: Path | None = None):
        self.client = client
        self.cache_file = cache_file or (client.cache_dir / "corpcode.json")
        self._by_code: dict[str, Corp] = {}
        self._by_name: dict[str, list[Corp]] = {}
        self._by_stock: dict[str, Corp] = {}
        self._load()

    # ---------- 적재 ----------

    def _load(self) -> None:
        raw = self._read_cache()
        if raw is None:
            raw = self._download()
            self.cache_file.write_text(
                json.dumps({"ts": time.time(), "corps": raw}, ensure_ascii=False),
                encoding="utf-8",
            )
        for item in raw:
            corp = Corp(**item)
            self._by_code[corp.corp_code] = corp
            self._by_name.setdefault(_normalize(corp.corp_name), []).append(corp)
            if corp.listed:
                self._by_stock[corp.stock_code] = corp

    def _read_cache(self) -> list[dict] | None:
        if not self.cache_file.exists():
            return None
        try:
            blob = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - blob.get("ts", 0) > CACHE_TTL:
            return None
        return blob["corps"]

    def _download(self) -> list[dict]:
        zf = self.client.get_zip("corpCode.xml")
        name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        root = etree.fromstring(zf.read(name))
        out = []
        for el in root.iter("list"):
            out.append(
                {
                    "corp_code": (el.findtext("corp_code") or "").strip(),
                    "corp_name": (el.findtext("corp_name") or "").strip(),
                    "stock_code": (el.findtext("stock_code") or "").strip(),
                    "modify_date": (el.findtext("modify_date") or "").strip(),
                }
            )
        return out

    # ---------- 조회 ----------

    def __len__(self) -> int:
        return len(self._by_code)

    def by_code(self, corp_code: str) -> Corp | None:
        return self._by_code.get(corp_code)

    def by_stock(self, stock_code: str) -> Corp | None:
        return self._by_stock.get(stock_code.zfill(6))

    def resolve(self, query: str, listed_only: bool = True) -> Corp:
        """기업명·종목코드·corp_code 아무거나 받아 Corp 하나로 확정."""
        q = query.strip()

        if re.fullmatch(r"\d{8}", q) and q in self._by_code:
            return self._by_code[q]
        if re.fullmatch(r"\d{6}", q) and (c := self.by_stock(q)):
            return c

        cands = list(self._by_name.get(_normalize(q), []))
        if listed_only and any(c.listed for c in cands):
            cands = [c for c in cands if c.listed]

        if not cands:
            near = self.search(q, listed_only=listed_only, limit=5)
            hint = ", ".join(c.corp_name for c in near)
            raise KeyError(f"'{query}'를 찾지 못했습니다." + (f" 혹시 이것들? {hint}" if hint else ""))
        if len(cands) > 1:
            # 동명이인 상장사는 사실상 없지만, 최신 갱신본을 택한다
            cands.sort(key=lambda c: c.modify_date, reverse=True)
        return cands[0]

    def search(self, keyword: str, listed_only: bool = True, limit: int = 20) -> list[Corp]:
        key = _normalize(keyword)
        hits = [
            c
            for norm, group in self._by_name.items()
            if key in norm
            for c in group
            if c.listed or not listed_only
        ]
        hits.sort(key=lambda c: (len(c.corp_name), c.corp_name))
        return hits[:limit]
