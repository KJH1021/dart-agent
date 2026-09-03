"""명령행 인터페이스.

  python -m dart_agent.cli compare --group 소재 --years 2021-2024
  python -m dart_agent.cli compare 동진쎄미켐 솔브레인 한솔케미칼 --years 2022-2024 --diff
  python -m dart_agent.cli search 케미칼
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .client import DartClient, DartError
from .compare import Analyzer
from .corpcode import CorpIndex
from .filings import FilingTracker
from .report import build_html, save

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "companies.yaml"


def parse_years(spec: str) -> list[int]:
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(y) for y in spec.split(",")]


def load_config() -> dict:
    if CONFIG.exists():
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    return {}


def cmd_search(args, client: DartClient) -> int:
    idx = CorpIndex(client)
    hits = idx.search(args.keyword, listed_only=not args.all)
    if not hits:
        print(f"'{args.keyword}' 검색 결과 없음")
        return 1
    for c in hits:
        print(f"{c.corp_code}  {c.stock_code or '      '}  {c.corp_name}")
    return 0


def cmd_compare(args, client: DartClient) -> int:
    cfg = load_config()
    years = parse_years(args.years)

    names = list(args.companies)
    if args.group:
        group = (cfg.get("groups") or {}).get(args.group)
        if not group:
            print(f"'{args.group}' 그룹이 config/companies.yaml에 없습니다.")
            return 1
        names += group
    if not names:
        print("비교할 기업을 지정하세요 (이름 나열 또는 --group).")
        return 1

    print(f"[1/4] 기업 매칭 및 재무 수집 — {len(names)}개사, {years[0]}~{years[-1]}")
    analyzer = Analyzer(client)
    profiles, matrix = analyzer.compare(names, years)
    if not profiles:
        print("수집된 기업이 없습니다.")
        return 1

    print("[2/4] 지표 추이 정리")
    trend_metrics = cfg.get("trend_metrics") or ["매출액(억원)", "영업이익률(%)"]
    trends = {}
    for m in trend_metrics:
        df = analyzer.trend(profiles, m)
        if not df.empty:
            trends[m] = df

    diffs = {}
    if args.diff and len(years) >= 2:
        print("[3/4] 사업보고서 서술 변화 추적 (원문 다운로드 — 시간이 걸립니다)")
        tracker = FilingTracker(client)
        sections = cfg.get("diff_sections")
        for name, p in profiles.items():
            try:
                diffs[name] = tracker.diff(p.corp.corp_code, years[-2], years[-1], sections)
                print(f"  · {name} — {len(diffs[name])}개 섹션 변화")
            except DartError as e:
                print(f"  ! {name} 원문 조회 실패: {e}")
    else:
        print("[3/4] 서술 변화 추적 생략 (--diff 로 활성화)")

    print("[4/4] 리포트 생성")
    html = build_html(
        profiles, matrix, trends, diffs, years[-1],
        title=args.title or (f"{args.group} 그룹 비교" if args.group else "기업 비교 리포트"),
    )
    out = save(html, args.out)
    print(f"\n완료 → {out}   (API 호출 {client.call_count}회)")

    if not matrix.empty:
        print("\n" + matrix.to_string())
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(prog="dart-agent", description="DART 기업분석 에이전트")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="기업명 검색 (corp_code 확인용)")
    s.add_argument("keyword")
    s.add_argument("--all", action="store_true", help="비상장 포함")

    c = sub.add_parser("compare", help="경쟁사 비교 리포트 생성")
    c.add_argument("companies", nargs="*", help="기업명 또는 종목코드")
    c.add_argument("--group", help="config/companies.yaml의 그룹명")
    c.add_argument("--years", default="2021-2024", help="예: 2021-2024 또는 2022,2024")
    c.add_argument("--diff", action="store_true", help="사업보고서 서술 변화 포함")
    c.add_argument("--out", default=str(ROOT / "out" / "report.html"))
    c.add_argument("--title")
    c.add_argument("--no-cache", action="store_true")

    args = ap.parse_args(argv)

    try:
        client = DartClient(use_cache=not getattr(args, "no_cache", False))
    except ValueError as e:
        print(f"설정 오류: {e}")
        return 2

    try:
        if args.cmd == "search":
            return cmd_search(args, client)
        return cmd_compare(args, client)
    except DartError as e:
        print(f"DART 오류: {e}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
