# -*- coding: utf-8 -*-
"""
'시장의 시선' 전용 페이지 생성기 → docs/gaze.html

2026-09-22 분리: 뉴스 브리핑 페이지 최상단과 홈 카드에 시선을 얹어 두었더니
한 화면이 너무 길어져, 시선을 자체 탭으로 떼어냈다(사용자 요청).
데이터·화면 조각은 market_gaze 에, 날짜 바는 site_nav 의 허브와 같은 방식
(달력 + 이전/다음 + 최신으로)을 쓴다.

make_brief.build() 가 함수 안에서 늦게 import 해 호출한다 — 모듈 최상단에서
make_brief 를 읽어오므로 순환 import 를 피하려면 이 순서여야 한다.
"""
import os

import site_nav
import market_gaze
from make_brief import _SHARED_STYLE   # 배경·타이포그래피 공용

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "docs")


def build():
    allg = market_gaze.load_all()
    days = sorted(allg, reverse=True)          # 최신 먼저
    if not days:
        print("[시선] 데이터 없음 — 건너뜀")
        return

    panels = []
    for i, pretty in enumerate(days):
        prev = allg[days[i + 1]] if i + 1 < len(days) else None   # 하루 앞선 날짜
        hide = "" if i == 0 else ' style="display:none"'
        ymd = pretty.replace("-", "")
        panels.append(f'<div class="day" id="day-{ymd}"{hide}>'
                      f'{market_gaze.block_html(allg[pretty], prev)}</div>')

    ymds = [d.replace("-", "") for d in days]
    body = (f'<div class="wrap">{site_nav._datebar(ymds, ymds[0])}'
            f'<div id="view">{"".join(panels)}</div></div>'
            f'\n{site_nav.nav_html("gaze")}')
    full = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>시장의 시선</title></head><body>" + body
            + _SHARED_STYLE + market_gaze.GAZE_CSS
            + site_nav.NAV_CSS + site_nav.DATEBAR_CSS + site_nav.HUB_JS
            + "</body></html>")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "gaze.html"), "w", encoding="utf-8") as fh:
        fh.write(full)
    print(f"[시선] gaze.html 갱신 ({len(days)}건, 최신 {days[0]})")


if __name__ == "__main__":
    build()
