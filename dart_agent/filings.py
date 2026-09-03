"""사업보고서 원문 추적 — 연도별 서술 변화 감지.

숫자는 재무제표 API로 충분하지만, '무엇을 왜 하는지'는 본문에만 있다.
「사업의 내용」·「연구개발활동」·「기타 참고사항」의 문장 단위 변화를 잡아
전략 전환(신규 라인 언급, 고객사 구성 변화, 리스크 문구 추가)을 포착한다.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from lxml import etree

from .client import DartClient

# 관심 섹션 — 필요에 따라 늘리면 된다
DEFAULT_SECTIONS = [
    "사업의 개요",
    "주요 제품 및 서비스",
    "원재료 및 생산설비",
    "매출 및 수주상황",
    "위험관리 및 파생거래",
    "주요계약 및 연구개발활동",
    "기타 참고사항",
]

# "1. 사업의 개요", "II. 사업의 내용", "가. ..." 형태의 제목 줄
HEADING = re.compile(
    r"^\s*(?:[IVX]+\.|\d+\.|[가-힣]\.)\s*(?P<title>[^\n]{2,60}?)\s*$", re.MULTILINE
)


@dataclass
class AnnualReport:
    corp_code: str
    fiscal_year: int
    rcept_no: str
    report_nm: str
    rcept_dt: str
    text: str

    def sections(self) -> dict[str, str]:
        return split_sections(self.text)


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def xml_to_text(raw: bytes) -> str:
    """DART 원문 XML → 평문. 표는 셀 사이를 ' | '로 이어 한 줄로 만든다."""
    text = _decode(raw)
    # 선언부 인코딩이 실제와 다른 경우가 있어 제거 후 파싱
    text = re.sub(r"<\?xml[^>]*\?>", "", text, count=1)
    try:
        root = etree.fromstring(text.encode("utf-8"), etree.XMLParser(recover=True))
        parts = [t.strip() for t in root.itertext() if t and t.strip()]
        flat = "\n".join(parts)
    except Exception:
        flat = re.sub(r"<[^>]+>", "\n", text)

    flat = re.sub(r"&[a-z]+;", " ", flat)
    flat = re.sub(r"[ \t ]+", " ", flat)
    flat = re.sub(r"\n{3,}", "\n\n", flat)
    return flat.strip()


def split_sections(text: str) -> dict[str, str]:
    """제목 줄을 경계로 본문을 자른다. 중복 제목은 뒤에 나온 것이 더 길면 교체."""
    marks = [(m.start(), m.group("title").strip()) for m in HEADING.finditer(text)]
    out: dict[str, str] = {}
    for i, (pos, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[pos:end].strip()
        if len(body) < 40:  # 목차 줄
            continue
        if title not in out or len(body) > len(out[title]):
            out[title] = body
    return out


_NUM = re.compile(r"\d[\d,.]*")

# 사업보고서 본문의 절반은 표다. 표가 한 줄로 뭉개진 것을 문장으로 세면
# 매년 숫자만 바뀌는 노이즈가 diff를 가득 채운다. 숫자 밀도로 걸러낸다.
MAX_SENT_LEN = 400
MAX_NUM_TOKENS = 5
MAX_DIGIT_RATIO = 0.20


def _is_tabular(s: str) -> bool:
    if len(_NUM.findall(s)) > MAX_NUM_TOKENS:
        return True
    digits = sum(c.isdigit() for c in s)
    return digits / max(len(s), 1) > MAX_DIGIT_RATIO


def _sentences(body: str) -> list[str]:
    """줄 경계(=XML 텍스트 노드 경계)를 먼저 지키고 그 안에서 문장을 나눈다.

    줄바꿈을 먼저 공백으로 뭉개면 표 셀과 그 뒤 산문이 한 덩어리가 되어
    멀쩡한 문장까지 표로 오인된다. 셀은 대개 짧아 길이 하한에서 걸러진다.
    """
    out = []
    for line in body.split("\n"):
        line = re.sub(r"[ \t ]+", " ", line).strip()
        if not line:
            continue
        for s in re.split(r"(?<=[.。])\s+|(?<=니다\.)\s*", line):
            s = s.strip()
            if not (15 <= len(s) <= MAX_SENT_LEN):
                continue
            if _is_tabular(s):
                continue
            out.append(s)
    return out


def _key(s: str) -> str:
    """비교용 정규화 키. 공백·구두점 차이만으로 '변경'이 잡히는 것을 막는다.

    XML 태그 경계에서 공백이 붙거나 사라지는 일이 흔해서
    ('각국에수출' vs '각국에 수출') 표시는 원문으로, 비교는 이 키로 한다.
    """
    return re.sub(r"[\s,·․※\-—()（）]", "", s)


def match_section(sections: dict[str, str], wanted: str) -> str | None:
    """제목 표기 흔들림(공백·괄호)을 흡수해 가장 비슷한 섹션을 찾는다."""
    key = re.sub(r"\s+", "", wanted)
    for title, body in sections.items():
        if key in re.sub(r"\s+", "", title):
            return body
    return None


class FilingTracker:
    def __init__(self, client: DartClient):
        self.client = client

    def find_annual_report(self, corp_code: str, fiscal_year: int) -> dict | None:
        """해당 사업연도의 사업보고서 공시 1건. 정정본이 있으면 최종본이 잡힌다."""
        rows = self.client.filings(
            corp_code=corp_code,
            bgn_de=f"{fiscal_year + 1}0101",
            end_de=f"{fiscal_year + 2}0630",
            pblntf_ty="A",
            last_reprt_at="Y",
        )
        cands = [
            r
            for r in rows
            if "사업보고서" in r.get("report_nm", "")
            and f"{fiscal_year}.12" in r.get("report_nm", "").replace(" ", "")
        ]
        if not cands:
            cands = [r for r in rows if r.get("report_nm", "").strip().startswith("사업보고서")]
        if not cands:
            return None
        cands.sort(key=lambda r: r.get("rcept_dt", ""))
        return cands[0]

    def fetch(self, corp_code: str, fiscal_year: int) -> AnnualReport | None:
        meta = self.find_annual_report(corp_code, fiscal_year)
        if not meta:
            return None
        zf = self.client.document(meta["rcept_no"])
        names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not names:
            return None
        # 본문 파일이 가장 크다 (첨부는 별도)
        names.sort(key=lambda n: zf.getinfo(n).file_size, reverse=True)
        text = xml_to_text(zf.read(names[0]))
        return AnnualReport(
            corp_code=corp_code,
            fiscal_year=fiscal_year,
            rcept_no=meta["rcept_no"],
            report_nm=meta.get("report_nm", "").strip(),
            rcept_dt=meta.get("rcept_dt", ""),
            text=text,
        )

    def diff(
        self,
        corp_code: str,
        old_year: int,
        new_year: int,
        sections: list[str] | None = None,
        max_items: int = 12,
    ) -> dict[str, dict[str, list[str]]]:
        """섹션별 {added: [...], removed: [...]}."""
        sections = sections or DEFAULT_SECTIONS
        old = self.fetch(corp_code, old_year)
        new = self.fetch(corp_code, new_year)
        if not old or not new:
            return {}

        old_secs, new_secs = old.sections(), new.sections()
        result: dict[str, dict[str, list[str]]] = {}

        for want in sections:
            a, b = match_section(old_secs, want), match_section(new_secs, want)
            if not a or not b:
                continue
            sa, sb = _sentences(a), _sentences(b)
            if not sa or not sb:
                continue
            # 비교는 정규화 키로, 표시는 원문으로
            sm = difflib.SequenceMatcher(
                None, [_key(s) for s in sa], [_key(s) for s in sb], autojunk=False
            )
            added, removed = [], []
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag in ("replace", "insert"):
                    added += sb[j1:j2]
                if tag in ("replace", "delete"):
                    removed += sa[i1:i2]
            if added or removed:
                result[want] = {
                    "added": added[:max_items],
                    "removed": removed[:max_items],
                    "added_total": len(added),
                    "removed_total": len(removed),
                }
        return result
