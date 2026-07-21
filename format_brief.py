# -*- coding: utf-8 -*-
"""
시황 브리핑 서식 자동 복구기.

클립보드에서 붙여넣은 Gemini 원문은 줄바꿈이 통째로 사라지는 등 서식이
깨진 경우가 많다. 이 스크립트는 줄바꿈에 의존하지 않고 구조 마커만으로
(`N. [분류] 제목`, `요약:`, `영향 분석:`, 마크다운 링크, '운용 전략 제언')
본문을 파싱해 briefs/*.md 표준 형식으로 재조립한다.

사용법:  python format_brief.py briefs/2026-07-21.md   (제자리 덮어쓰기)
파싱에 실패하면 원본을 그대로 두고 종료코드 1을 반환한다(안전 장치).
"""
import io
import re
import sys

# 게시물에서 항상 제거할 문구 (make_brief._clean 과 동일 규칙 + 서식용)
STRIP = ["박동현 님, ", "박동현 님,", "박동현 님 ", "박동현 님",
         "펀드매니저용 ", "펀드매니저용"]

ADVICE_MARK = "라파엔투자자문"          # 마지막 '운용 전략 제언' 섹션 시작 표식
ITEM_RE = re.compile(r"(?=\d{1,2}\.\s*\[[^\]\n]{1,14}\])")   # 'N. [분류]' 경계
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _clean_inline(s):
    """세그먼트 공통 정리: 불릿/굵게 표식·LaTeX 찌꺼기·중복 공백 제거."""
    s = re.sub(r"\$([^$]+)\$", r"\1", s)          # $Reaction\ Function$ → Reaction Function
    s = s.replace("\\ ", " ").replace("\\'", "'")
    s = re.sub(r"[*_]{1,3}", "", s)               # 남아있는 굵게/기울임 표식
    s = re.sub(r"^[\s>•·\-]+", "", s)             # 앞머리 불릿 기호
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_item(chunk):
    """'N. [분류] 제목 요약: ... 영향 분석: ... (출처)' 한 항목 파싱."""
    m = re.match(r"\s*(\d{1,2})\.\s*(\[[^\]]+\])\s*(.*)\Z", chunk, re.S)
    if not m:
        return None
    num, cat, body = m.group(1), m.group(2), m.group(3)

    sm = re.search(r"요\s*약\s*[:：]", body)
    am = re.search(r"영향\s*분석\s*[:：]", body)
    if not sm or not am or am.start() < sm.start():
        return None
    title = _clean_inline(body[:sm.start()])
    summary = _clean_inline(body[sm.end():am.start()])
    rest = body[am.end():]

    # 출처 링크: 마크다운 링크가 살아 있으면 그대로, 죽었으면 캡션 텍스트만이라도 살린다
    links = LINK_RE.findall(rest)
    if links:
        analysis = _clean_inline(rest[:LINK_RE.search(rest).start()])
        link_lines = [f"* [{_clean_inline(t)}]({u})" for t, u in links]
    else:
        analysis, caption = rest, ""
        last = max(analysis.rfind("다."), analysis.rfind("다.\""))
        if last != -1 and len(analysis) - last > 6:      # '다.' 뒤에 꼬리가 남으면 출처 캡션
            caption = analysis[last + 2:]
            analysis = analysis[:last + 2]
        analysis = _clean_inline(analysis)
        caption = _clean_inline(caption)
        link_lines = [f"* {caption}"] if len(caption) >= 5 else []

    lines = [f"**{num}. {cat} {title}**", "",
             f"* **요약:** {summary}",
             f"* **영향 분석:** {analysis}"] + link_lines
    return "\n".join(lines)


def _parse_advice(text):
    """'운용 전략 제언' 섹션 → 헤딩 + 문단들."""
    body = re.sub(r"^.*?제언\s*", "", text, count=1, flags=re.S)
    if "\n\n" in body.strip():
        paras = [_clean_inline(p) for p in re.split(r"\n\s*\n", body) if p.strip()]
    else:
        body = _clean_inline(body)
        # 줄바꿈이 소실된 경우: '…습니다.' 뒤에 '그러나/오늘'로 시작하는 지점에서 문단 분리
        paras = re.split(r"(?<=니다\.)\s*(?=그러나|오늘|다만|따라서|결론)", body)
        paras = [p.strip() for p in paras if p.strip()]
    out = ["---", "", "### **라파엔투자자문 장 개시 전 운용 전략 제언**", ""]
    out += [p + "\n" for p in paras]
    return "\n".join(out).rstrip() + "\n"


def format_text(raw):
    """원문(서식 불문) → 표준 md. 실패 시 None."""
    text = raw
    for p in STRIP:
        text = text.replace(p, "")

    # 헤더 날짜
    dm = re.search(r"\[(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\]", text)
    header_date = f"[{dm.group(1)}.{int(dm.group(2)):02d}.{int(dm.group(3)):02d}]" if dm else ""

    # 제언 섹션 분리
    advice = ""
    ai = text.find(ADVICE_MARK)
    if ai == -1:
        am = re.search(r"운용 전략 제언", text)
        ai = am.start() if am else -1
    if ai != -1:
        advice = _parse_advice(text[ai:])
        text = text[:ai]

    # 항목 분리
    chunks = [c for c in ITEM_RE.split(text) if c.strip()]
    if chunks and not re.match(r"\s*\d{1,2}\.\s*\[", chunks[0]):
        chunks = chunks[1:]                      # 첫 덩어리는 헤더 잔여물
    items = [_parse_item(c) for c in chunks]
    items = [i for i in items if i]
    if len(items) < 3:                           # 구조를 못 읽었으면 실패 처리
        return None

    title_line = f"### **{header_date} 글로벌 매크로 데일리 브리핑**".replace("  ", " ").strip()
    parts = [title_line, ""] + [i + "\n" for i in items]
    md = "\n".join(parts).rstrip() + "\n"
    if advice:
        md += "\n" + advice
    return md


def main():
    if len(sys.argv) < 2:
        print("사용법: python format_brief.py <briefs/YYYY-MM-DD.md>")
        return 1
    path = sys.argv[1]
    raw = io.open(path, encoding="utf-8-sig").read()
    md = format_text(raw)
    if md is None:
        print("[서식복구] 구조 인식 실패 — 원본을 그대로 둡니다 (수동 확인 필요)")
        return 1
    io.open(path, "w", encoding="utf-8", newline="\n").write(md)
    n_items = md.count("* **요약:**")
    print(f"[서식복구] 완료: 항목 {n_items}개 + 제언 {'있음' if ADVICE_MARK in md else '없음'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
