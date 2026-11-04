# -*- coding: utf-8 -*-
"""종목 리서치 PPT → 텍스트 덤프(도형·표·차트 데이터·발표자 노트, 슬라이드 순서).

  python -X utf8 thesis-lab/pptx_to_text.py <출력폴더> <pptx 1> [<pptx 2> ...]

마크다운(docs/thesis/<code>.md + private/<code>.md) 변환의 첫 단계. 절차는 종목 리서치/README.md 참고.
"""
import sys, zipfile, re, os
from xml.etree import ElementTree as ET

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'


def para_text(p):
    out = []
    for r in p:
        if r.tag == A + 'r':
            t = r.find(A + 't')
            out.append(t.text or '' if t is not None else '')
        elif r.tag == A + 'br':
            out.append('\n')
        elif r.tag == A + 'fld':
            t = r.find(A + 't')
            out.append(t.text or '' if t is not None else '')
    return ''.join(out)


def body_text(el):
    paras = []
    for p in el.iter(A + 'p'):
        s = para_text(p).strip()
        if s:
            paras.append(s)
    return paras


def dump(path, out):
    z = zipfile.ZipFile(path)
    slides = sorted([n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)],
                    key=lambda s: int(re.findall(r'\d+', s)[0]))
    lines = [f'# {os.path.basename(path)}  ({len(slides)} slides)', '']
    for n in slides:
        num = int(re.findall(r'\d+', n)[0])
        root = ET.fromstring(z.read(n))
        lines.append(f'\n===== SLIDE {num} =====')
        # 도형(텍스트박스) — 문서 순서
        for sp in root.iter(P + 'sp'):
            tb = sp.find(P + 'txBody')
            if tb is None:
                continue
            paras = body_text(tb)
            if paras:
                lines.append('[box] ' + ' | '.join(paras))
        # 표
        for ti, tbl in enumerate(root.iter(A + 'tbl'), 1):
            lines.append(f'[table {ti}]')
            for tr in tbl.iter(A + 'tr'):
                cells = []
                for tc in tr.findall(A + 'tc'):
                    cells.append(' '.join(body_text(tc)))
                lines.append('  | ' + ' | '.join(cells) + ' |')
        # 차트 (데이터 캐시)
        rels_name = n.replace('slides/', 'slides/_rels/') + '.rels'
        if rels_name in z.namelist():
            rels = ET.fromstring(z.read(rels_name))
            for rel in rels:
                tgt = rel.get('Target', '')
                if 'charts/chart' in tgt:
                    cpath = 'ppt/' + tgt.replace('../', '')
                    try:
                        c = ET.fromstring(z.read(cpath))
                        C = '{http://schemas.openxmlformats.org/drawingml/2006/chart}'
                        title = ' '.join(x.text or '' for x in c.iter(A + 't'))[:80]
                        series = []
                        for ser in c.iter(C + 'ser'):
                            nm = ' '.join((v.text or '') for v in ser.iter(C + 'v'))
                            series.append(nm[:300])
                        lines.append(f'[chart] {title} :: ' + ' || '.join(series))
                    except Exception as e:
                        lines.append(f'[chart] (parse fail {e})')
                if 'notesSlides/' in tgt:
                    npath = 'ppt/' + tgt.replace('../', '')
                    try:
                        nroot = ET.fromstring(z.read(npath))
                        notes = []
                        for sp in nroot.iter(P + 'sp'):
                            notes += body_text(sp)
                        notes = [x for x in notes if not re.fullmatch(r'\d+', x)]
                        if notes:
                            lines.append('[notes] ' + ' / '.join(notes))
                    except Exception:
                        pass
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(out, len(lines), 'lines')


if __name__ == '__main__':
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    for p in sys.argv[2:]:
        base = os.path.splitext(os.path.basename(p))[0]
        dump(p, os.path.join(outdir, base + '.txt'))
