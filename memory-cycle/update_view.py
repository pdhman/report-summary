# -*- coding: utf-8 -*-
"""메모리 사이클 대시보드 — '현재 결론' 경량 갱신.

data/cycle_view_manual.json 만 고친 뒤 이 스크립트를 돌리면, 이미 만들어진 페이지에서
결론 구역(현재 결론·항목별 해설·수급 지표 탭 + 머리말 기준일)만 다시 그려 넣는다.
차트·백테스트는 그대로 둔다. 표준 라이브러리만 써서 입력 데이터가 없는 곳
(클라우드 세션, GitHub Actions)에서도 돈다. 차트까지 새로 그리려면 PC 에서 cycle_model.py.

사용법:
    python memory-cycle/update_view.py          # 검사 → docs/memory.html (+ 로컬 사본) 갱신
    python memory-cycle/update_view.py --check  # 검사와 바뀔 구역만 출력, 파일은 안 씀

종료 코드: 0 = 성공(바뀐 것 없음 포함), 1 = 입력 오류 또는 페이지에 구역 표시 없음.
"""
from __future__ import annotations

import os
import sys

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import cycle_view as cvw  # noqa: E402

ROOT = os.path.dirname(BASE)
TARGETS = [  # (경로, 공개판 여부) — 없는 파일은 건너뜀(클라우드에는 로컬 사본이 없다)
    (os.path.join(ROOT, "docs", "memory.html"), True),
    (os.path.join(BASE, "memory_cycle.html"), False),
]


def main() -> int:
    check_only = "--check" in sys.argv
    cv = cvw.load_cycle_view()
    if cv is None:
        print(f"[오류] {cvw.VIEW_JSON} 없음")
        return 1
    errs = cvw.validate(cv)
    if errs:
        print("[오류] cycle_view_manual.json 검사 실패 — 페이지는 건드리지 않았습니다:")
        for e in errs:
            print("  -", e)
        return 1
    parts = cvw.render_cycle_view(cv)
    print(f"[검사] 통과 — 기준일 {cv['as_of']}, 판정 {len(cv['rows'])}행, "
          f"최근 신호 {len(cv.get('updates', []))}건")

    found = False
    for path, public in TARGETS:
        if not os.path.exists(path):
            continue
        found = True
        with open(path, encoding="utf-8") as f:
            page = f.read()
        new = page
        changed = []
        for key, part in cvw.PANEL_KEYS:
            html = cvw.apply_public(parts[part]) if public else parts[part]
            out = cvw.splice(new, key, html)
            if out is None:
                if html:
                    print(f"[오류] {os.path.basename(path)} 에 '{key}' 구역 표시가 없습니다 — "
                          "PC 에서 전체 빌드(python memory-cycle/cycle_model.py)를 한 번 돌려야 합니다.")
                    return 1
                continue
            if out != new:
                changed.append(key)
            new = out
        out = cvw.splice(new, "asof", cvw.esc(cv["as_of"]))
        if out is not None:
            if out != new:
                changed.append("asof")
            new = out
        name = os.path.relpath(path, ROOT)
        if not changed:
            print(f"[{name}] 바뀐 구역 없음")
            continue
        print(f"[{name}] 바뀐 구역: {', '.join(changed)}" + (" (--check: 쓰지 않음)" if check_only else ""))
        if not check_only:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
    if not found:
        print("[오류] 갱신할 페이지(docs/memory.html)가 없습니다.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
