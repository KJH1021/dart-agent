"""리포트 생성 — Markdown / HTML."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .compare import CompanyProfile

CSS = """
:root{--bg:#fbfaf9;--fg:#1c1a17;--mut:#6b645c;--line:#e3ded7;--acc:#8a5a2b;--card:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#141310;--fg:#eeeae4;--mut:#a49c92;--line:#2e2a25;--acc:#d99a5b;--card:#1c1a17}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Noto Sans KR",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:48px 24px 80px}
h1{font-size:28px;margin:0 0 4px;letter-spacing:-.02em}
h2{font-size:19px;margin:44px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:15px;margin:26px 0 8px;color:var(--acc)}
.sub{color:var(--mut);font-size:13px;margin-bottom:8px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:9px 12px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:var(--card);font-weight:600}
thead th{background:var(--card);color:var(--mut);font-weight:600;font-size:12px;text-transform:none}
tbody tr:last-child td{border-bottom:none}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--acc);border-radius:8px;padding:12px 16px;margin:12px 0;font-size:13px;color:var(--mut)}
.add{color:#2f7d4f}.rem{color:#a3402f;text-decoration:line-through;opacity:.75}
@media(prefers-color-scheme:dark){.add{color:#6cc38d}.rem{color:#e0806e}}
ul.diff{margin:6px 0 14px;padding-left:20px;font-size:13px}
ul.diff li{margin:4px 0}
footer{margin-top:60px;color:var(--mut);font-size:12px;border-top:1px solid var(--line);padding-top:16px}
"""


def _table(df: pd.DataFrame, index_name: str = "") -> str:
    if df is None or df.empty:
        return '<p class="sub">데이터 없음</p>'
    d = df.copy()
    d.index.name = index_name or d.index.name or ""
    html = d.to_html(border=0, na_rep="–", float_format=lambda v: f"{v:,.2f}".rstrip("0").rstrip("."))
    return f'<div class="scroll">{html}</div>'


def build_html(
    profiles: dict[str, CompanyProfile],
    matrix: pd.DataFrame,
    trends: dict[str, pd.DataFrame],
    diffs: dict[str, dict],
    base_year: int,
    title: str = "반도체 밸류체인 경쟁사 비교",
) -> str:
    parts = [
        "<!doctype html>",
        '<html lang="ko"><head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{title}</title><style>{CSS}</style>",
        "</head><body>",
        '<div class="wrap">',
        f"<h1>{title}</h1>",
        f'<p class="sub">기준 사업연도 {base_year} · 출처 금융감독원 전자공시(DART) · 생성 {datetime.now():%Y-%m-%d %H:%M}</p>',
    ]

    parts.append(f"<h2>1. 기준연도 비교표 ({base_year})</h2>")
    parts.append(_table(matrix, "기업"))

    notes = [f"<b>{n}</b> — {'; '.join(p.notes)}" for n, p in profiles.items() if p.notes]
    if notes:
        parts.append('<div class="note">' + "<br>".join(notes) + "</div>")

    parts.append("<h2>2. 지표별 추이</h2>")
    for metric, df in trends.items():
        parts.append(f"<h3>{metric}</h3>")
        parts.append(_table(df, "연도"))

    parts.append("<h2>3. 기업별 상세</h2>")
    for name, p in profiles.items():
        ov = p.overview
        parts.append(f"<h3>{name}")
        if p.corp.stock_code:
            parts.append(f" <span class='sub'>({p.corp.stock_code})</span>")
        parts.append("</h3>")
        meta = " · ".join(
            filter(
                None,
                [
                    ov.get("ceo_nm") and f"대표 {ov['ceo_nm']}",
                    ov.get("induty_code") and f"업종코드 {ov['induty_code']}",
                    ov.get("est_dt") and f"설립 {ov['est_dt'][:4]}",
                    ov.get("acc_mt") and f"결산 {ov['acc_mt']}월",
                ],
            )
        )
        if meta:
            parts.append(f'<p class="sub">{meta}</p>')
        parts.append(_table(p.metrics, "연도"))

    if diffs:
        parts.append("<h2>4. 사업보고서 서술 변화</h2>")
        for name, sections in diffs.items():
            parts.append(f"<h3>{name}</h3>")
            if not sections:
                parts.append('<p class="sub">비교 가능한 섹션을 찾지 못했습니다.</p>')
                continue
            for sec, d in sections.items():
                parts.append(
                    f"<p class='sub'><b>{sec}</b> — 추가 {d['added_total']}문장 / 삭제 {d['removed_total']}문장</p>"
                )
                parts.append('<ul class="diff">')
                for s in d["added"]:
                    parts.append(f'<li class="add">+ {s}</li>')
                for s in d["removed"]:
                    parts.append(f'<li class="rem">− {s}</li>')
                parts.append("</ul>")

    parts.append(
        "<footer>본 리포트는 DART 공시 원문에서 기계적으로 추출·계산한 결과입니다. "
        "연결/별도 구분과 정정공시 여부를 확인한 뒤 활용하세요. 투자 판단의 근거로 쓰기 위한 자료가 아닙니다.</footer>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)


def save(html: str, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
