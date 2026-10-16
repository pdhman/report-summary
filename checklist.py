# -*- coding: utf-8 -*-
"""
checklist.py
마인드 체크리스트를 월별 시트로 관리하고 '준수율 통계' 시트를 자동으로 만든다.

사용법:
    python checklist.py --new 2609        # 2026년 9월 시트 생성 + 통계 갱신
    python checklist.py                   # 통계 시트만 갱신
    옵션: --xlsx 일지.xlsx | --dry-run | --force | --no-backup

시트 규칙:
    체크리스트_YYMM  — 월 하나당 시트 하나. 4행에 날짜, 5행부터 항목별 O/X('일일 준수율' 행 윗줄까지).
                      항목 수는 달마다 달라도 된다. 새 달에 항목 추가: --new 2610 --add-item "진입 직전:문구"
    준수율 통계      — 모든 월 시트를 훑어 항목별·일별 준수율과 그날 손익을 모아 보여준다.

준수율 계산:
    O / (O + X).  빈칸과 '-'(해당 없음)은 분모에서 빠진다.
    (기존 COUNTA 방식은 안 적은 항목까지 분모에서 빠져 준수율이 실제보다 높게 나왔다.)
"""
import argparse
import datetime as dt
import os
import re
import shutil
import sys

import openpyxl
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

from update_trading_journal import (BACKUP_DIR, DEFAULT_XLSX, KEEP_BACKUPS, center_vertical,
                                    prune_backups)

PREFIX = '체크리스트_'
STATS_SHEET = '준수율 통계'
EQUITY_SHEET = '수익곡선'
LEGACY_SHEET = '체크리스트'
N_DATES = 22                      # 월 시트에 준비할 날짜 칸 수 (한 달 거래일)
FIRST_ITEM, LAST_ITEM = 5, 15     # 항목 행 범위
DATE_COL0 = 3                     # 날짜 시작 열(C)

FILL_HEAD = PatternFill('solid', fgColor='FFF7E6')
FILL_GROUP = PatternFill('solid', fgColor='DDEBF7')
FILL_RATE = PatternFill('solid', fgColor='FFFF99')
FILL_TITLE = PatternFill('solid', fgColor='1F3864')
THIN = Side(style='thin', color='BFBFBF')
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def month_sheets(wb):
    """체크리스트_YYMM 시트를 이름순(=시간순)으로."""
    return [wb[n] for n in sorted(wb.sheetnames) if n.startswith(PREFIX)]


def item_range(ws):
    """항목 행 범위(첫 행, 끝 행). 끝은 A열 '일일 준수율' 바로 윗줄 — 달마다 항목 수가 달라도 된다."""
    for r in range(FIRST_ITEM, ws.max_row + 1):
        if str(ws.cell(r, 1).value or '').strip() == '일일 준수율':
            return FIRST_ITEM, r - 1
    return FIRST_ITEM, LAST_ITEM


def date_cols(ws):
    """4행에서 날짜가 들어 있는 열 번호."""
    return [c for c in range(DATE_COL0, ws.max_column + 1)
            if isinstance(ws.cell(4, c).value, dt.datetime)]


def rate_formula(rng):
    """O/(O+X). 빈칸·'-'는 분모에서 제외."""
    return (f'=IFERROR(COUNTIF({rng},"O")/'
            f'(COUNTIF({rng},"O")+COUNTIF({rng},"X")),"")')


def read_template(ws):
    """기존 시트에서 글(제목·항목·낭독·주간리뷰)을 그대로 읽어온다.

    items 는 [(구분, 문구)] — 구분은 그 구분의 첫 항목에만 있고 나머지는 None.
    footer 는 '일일 준수율' 행으로부터의 거리로 적어, 항목 수가 바뀌어도 같이 밀린다.
    """
    first, last = item_range(ws)
    creed_col = next((c for c in range(1, ws.max_column + 1)
                      if isinstance(ws.cell(4, c).value, str)
                      and '낭독' in ws.cell(4, c).value), None)
    return {
        'title': ws['A1'].value,
        'guide': ws['A2'].value,
        'date_label': ws['C3'].value,
        'items': [(ws.cell(r, 1).value, ws.cell(r, 2).value) for r in range(first, last + 1)],
        'creed': [ws.cell(r, creed_col).value for r in range(4, 7)] if creed_col else [],
        'footer': [(r - (last + 1), ws.cell(r, 1).value) for r in range(last + 2, last + 9)
                   if ws.cell(r, 1).value],
    }


def add_item(tpl, group, text):
    """tpl 의 해당 구분 맨 끝에 항목을 넣는다(구분이 없으면 맨 끝에 새 구분으로)."""
    items = tpl['items']
    if any(str(t).strip() == text for _, t in items):
        return False
    cur, end = None, None
    for i, (g, _) in enumerate(items):
        if g:
            cur = str(g).strip()
        if cur == group:
            end = i
    if end is None:
        items.append((group, text))
    else:
        items.insert(end + 1, (None, text))
    return True


def build_month_sheet(wb, name, tpl, dates=None, values=None):
    """월 시트를 새 배치(날짜 칸 22개)로 만든다. dates/values 를 주면 기존 기록을 옮긴다."""
    if name in wb.sheetnames:
        del wb[name]
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    n = max(N_DATES, len(dates or []))
    last_date_col = DATE_COL0 + n - 1
    rate_col = last_date_col + 1                     # 항목 준수율
    creed_col = rate_col + 2                         # 낭독 문구

    ws['A1'] = tpl['title']
    ws['A1'].font = Font(size=13, bold=True)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=rate_col)
    ws['A2'] = tpl['guide']
    ws['A2'].font = Font(size=9, color='808080')
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=rate_col)
    ws['C3'] = tpl['date_label']
    ws['C3'].font = Font(size=9, color='808080')

    ws.cell(4, 1, '구분')
    ws.cell(4, 2, '체크 항목')
    for c in (1, 2):
        ws.cell(4, c).fill = FILL_GROUP
        ws.cell(4, c).font = Font(bold=True)
        ws.cell(4, c).alignment = Alignment(horizontal='center')
    for i in range(n):                                # 날짜 칸
        cell = ws.cell(4, DATE_COL0 + i)
        if dates and i < len(dates):
            cell.value = dates[i]
        cell.number_format = 'mm-dd'
        cell.fill = FILL_HEAD
        cell.alignment = Alignment(horizontal='center')
        cell.border = BOX
    ws.cell(4, rate_col, '항목 준수율').fill = FILL_RATE
    ws.cell(4, rate_col).font = Font(bold=True)
    ws.cell(4, rate_col).alignment = Alignment(horizontal='center')

    last_item = FIRST_ITEM + len(tpl['items']) - 1
    for k, (_, item) in enumerate(tpl['items']):      # 항목 행
        r = FIRST_ITEM + k
        ws.cell(r, 2, item).alignment = Alignment(wrap_text=True, vertical='center')
        ws.cell(r, 2).border = BOX
        ws.row_dimensions[r].height = 25.5
        for i in range(n):
            cell = ws.cell(r, DATE_COL0 + i)
            if values and i < len(values):
                cell.value = values[i][k]
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = BOX
        rc = ws.cell(r, rate_col, rate_formula(
            f'{L(DATE_COL0)}{r}:{L(last_date_col)}{r}'))
        rc.number_format = '0%'
        rc.fill = FILL_RATE
        rc.alignment = Alignment(horizontal='center')
        rc.border = BOX

    groups = [(FIRST_ITEM + k, g) for k, (g, _) in enumerate(tpl['items']) if g]
    starts = [r for r, _ in groups] + [last_item + 1]
    for (r, label), nxt in zip(groups, starts[1:]):   # 구분 병합
        ws.cell(r, 1, label).font = Font(bold=True)
        ws.cell(r, 1).fill = FILL_GROUP
        ws.cell(r, 1).alignment = Alignment(horizontal='center', vertical='center')
        if nxt - 1 > r:
            ws.merge_cells(start_row=r, start_column=1, end_row=nxt - 1, end_column=1)

    r = last_item + 1                                  # 일일 준수율
    ws.cell(r, 1, '일일 준수율').font = Font(bold=True)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    ws.cell(r, 1).alignment = Alignment(horizontal='center')
    ws.cell(r, 1).fill = FILL_RATE
    for i in range(n):
        col = L(DATE_COL0 + i)
        cell = ws.cell(r, DATE_COL0 + i,
                       rate_formula(f'{col}{FIRST_ITEM}:{col}{last_item}'))
        cell.number_format = '0%'
        cell.fill = FILL_RATE
        cell.alignment = Alignment(horizontal='center')
        cell.border = BOX
    total = ws.cell(r, rate_col, rate_formula(
        f'{L(DATE_COL0)}{FIRST_ITEM}:{L(last_date_col)}{last_item}'))
    total.number_format = '0%'
    total.fill = FILL_RATE
    total.font = Font(bold=True)
    total.alignment = Alignment(horizontal='center')

    for i, text in enumerate(tpl['creed']):            # 낭독 문구
        cell = ws.cell(4 + i, creed_col, text)
        cell.font = Font(bold=(i == 0), size=10 if i else 11)
        if i == 0:
            cell.fill = FILL_GROUP
    for off, text in tpl['footer']:                    # 주간 리뷰 / 핵심 문장
        row = last_item + 1 + off
        ws.cell(row, 1, text)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=rate_col)
        ws.cell(row, 1).font = Font(bold=('주간 리뷰' in text or '핵심 문장' in text))

    ws.column_dimensions['A'].width = 11
    ws.column_dimensions['B'].width = 52
    for i in range(n):
        ws.column_dimensions[L(DATE_COL0 + i)].width = 8
    ws.column_dimensions[L(rate_col)].width = 11
    ws.column_dimensions[L(creed_col)].width = 60
    ws.freeze_panes = ws.cell(5, DATE_COL0)
    return ws


def fix_formulas(ws):
    """준수율 수식을 O/(O+X) 방식으로 교체."""
    cols = [c for c in range(DATE_COL0, ws.max_column + 1)
            if isinstance(ws.cell(4, c).value, dt.datetime)]
    if not cols:
        return 0
    first, last = L(cols[0]), L(cols[-1])
    i0, i1 = item_range(ws)
    rate_col = next((c for c in range(cols[-1] + 1, ws.max_column + 2)
                     if str(ws.cell(4, c).value or '').startswith('항목 준수율')), None)
    n = 0
    for r in range(i0, i1 + 1):
        if rate_col:
            ws.cell(r, rate_col, rate_formula(f'{first}{r}:{last}{r}'))
            n += 1
    for c in cols:
        ws.cell(i1 + 1, c,
                rate_formula(f'{L(c)}{i0}:{L(c)}{i1}'))
        n += 1
    if rate_col:
        ws.cell(i1 + 1, rate_col,
                rate_formula(f'{first}{i0}:{last}{i1}'))
    return n


def collect(wb):
    """월 시트에서 (항목별 월간 집계, 일별 집계)를 뽑는다.

    항목은 행 번호가 아니라 '문구'로 대조한다. 중간에 항목을 고치거나 추가해도
    이전 달 통계가 다른 항목에 붙지 않는다.
    """
    items, months, per_item, per_day = [], [], {}, []
    for ws in month_sheets(wb):
        label = ws.title[len(PREFIX):]
        months.append(label)
        i0, i1 = item_range(ws)
        for c in date_cols(ws):
            marks = [ws.cell(r, c).value for r in range(i0, i1 + 1)]
            o = sum(1 for m in marks if m == 'O')
            x = sum(1 for m in marks if m == 'X')
            if o + x:
                per_day.append({'date': ws.cell(4, c).value.date(),
                                'rate': o / (o + x), 'o': o, 'x': x})
        for r in range(i0, i1 + 1):
            item = ws.cell(r, 2).value
            if item is None:
                continue
            item = str(item).strip()
            if item not in items:
                items.append(item)
            marks = [ws.cell(r, c).value for c in date_cols(ws)]
            o = sum(1 for m in marks if m == 'O')
            x = sum(1 for m in marks if m == 'X')
            prev = per_item.get((item, label), (0, 0))
            per_item[(item, label)] = (prev[0] + o, prev[1] + x)
    per_day.sort(key=lambda d: d['date'])
    return items, months, per_item, per_day


def daily_pnl(wb):
    """수익곡선 시트에서 날짜별 손익을 가져온다(없으면 빈 dict)."""
    if EQUITY_SHEET not in wb.sheetnames:
        return {}
    ws = wb[EQUITY_SHEET]
    out = {}
    for r in range(5, ws.max_row + 1):
        d, v = ws.cell(r, 4).value, ws.cell(r, 6).value
        if isinstance(d, dt.datetime) and isinstance(v, (int, float)):
            out[d.date()] = v
    return out


def write_stats(wb):
    items, months, per_item, per_day = collect(wb)
    if not months:
        return None
    pnl = daily_pnl(wb)
    if STATS_SHEET in wb.sheetnames:
        del wb[STATS_SHEET]
    ws = wb.create_sheet(STATS_SHEET)
    ws.sheet_view.showGridLines = False

    ws['A1'] = '규칙 준수율 통계'
    ws['A1'].font = Font(size=14, bold=True)
    ws['A2'] = (f"월 시트 {len(months)}개({', '.join(months)}) · 준수율 = O/(O+X) · "
                f'일간 손익은 {EQUITY_SHEET} 시트 기준')
    ws['A2'].font = Font(size=9, color='808080')

    ws.cell(4, 1, '체크 항목').font = Font(bold=True, color='FFFFFF')
    ws.cell(4, 1).fill = FILL_TITLE
    for j, m in enumerate(months):                      # 항목 × 월 표
        c = ws.cell(4, 2 + j, f'{m[:2]}-{m[2:]}')
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = FILL_TITLE
        c.alignment = Alignment(horizontal='center')
    ws.cell(4, 2 + len(months), '전체').font = Font(bold=True, color='FFFFFF')
    ws.cell(4, 2 + len(months)).fill = FILL_TITLE
    for k, item in enumerate(items):
        r = 5 + k
        ws.cell(r, 1, item).alignment = Alignment(wrap_text=True)
        to, tx = 0, 0
        for j, m in enumerate(months):
            o, x = per_item.get((item, m), (0, 0))
            to, tx = to + o, tx + x
            cell = ws.cell(r, 2 + j, o / (o + x) if o + x else None)
            cell.number_format = '0%'
            cell.alignment = Alignment(horizontal='center')
        cell = ws.cell(r, 2 + len(months), to / (to + tx) if to + tx else None)
        cell.number_format = '0%'
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')
        cell.fill = FILL_RATE
    item_last = 4 + len(items)

    col0 = 2 + len(months) + 2                          # 일별 표 시작 열
    for j, h in enumerate(['날짜', '준수율', 'O', 'X', '일간 손익']):
        c = ws.cell(4, col0 + j, h)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = FILL_TITLE
        c.alignment = Alignment(horizontal='center')
    for i, d in enumerate(per_day):
        r = 5 + i
        ws.cell(r, col0, d['date']).number_format = 'yyyy-mm-dd'
        ws.cell(r, col0 + 1, d['rate']).number_format = '0%'
        ws.cell(r, col0 + 2, d['o'])
        ws.cell(r, col0 + 3, d['x'])
        v = pnl.get(d['date'])
        if v is not None:
            ws.cell(r, col0 + 4, round(v, 2)).number_format = '#,##0.00;[Red]-#,##0.00'
    day_last = 4 + len(per_day)

    ws.column_dimensions['A'].width = 46
    for j in range(len(months) + 1):
        ws.column_dimensions[L(2 + j)].width = 9
    for j, w in enumerate((12, 9, 5, 5, 12)):
        ws.column_dimensions[L(col0 + j)].width = w

    if len(per_day) > 1:                                # 일별 준수율 + 손익
        cats = Reference(ws, min_col=col0, min_row=5, max_row=day_last)
        bar = BarChart()
        bar.type = 'col'
        bar.title = '일별 준수율과 손익'
        bar.add_data(Reference(ws, min_col=col0 + 4, min_row=4, max_row=day_last),
                     titles_from_data=True)
        bar.set_categories(cats)
        bar.series[0].graphicalProperties.solidFill = 'BDD7EE'
        bar.series[0].invertIfNegative = False
        bar.y_axis.numFmt = '#,##0'

        line = LineChart()
        line.add_data(Reference(ws, min_col=col0 + 1, min_row=4, max_row=day_last),
                      titles_from_data=True)
        line.y_axis.axId = 200                          # 준수율은 보조축(오른쪽)
        line.y_axis.numFmt = '0%'
        line.y_axis.crosses = 'max'                     # 없으면 왼쪽에 겹쳐 그려진다
        line.y_axis.scaling.min = 0
        line.y_axis.scaling.max = 1
        line.series[0].graphicalProperties.line.solidFill = 'C00000'
        line.series[0].graphicalProperties.line.width = 22000
        line.series[0].smooth = False

        bar.y_axis.crosses = 'autoZero'
        bar.x_axis.delete = False
        bar.y_axis.delete = False
        line.y_axis.delete = False
        bar.x_axis.axPos = 'b'
        bar.x_axis.number_format = 'mm-dd'
        bar.x_axis.tickLblPos = 'low'
        bar.x_axis.tickLblSkip = max(1, len(per_day) // 12)
        bar.height, bar.width = 9, 24
        bar.legend.position = 'b'      # 축 제목 대신 아래쪽 범례로 구분
        bar.legend.overlay = False     # 겹치면 x축 날짜를 가린다
        bar += line
        ws.add_chart(bar, f'A{max(item_last, day_last) + 3}')
    return len(months), len(per_day)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--xlsx', default=DEFAULT_XLSX)
    ap.add_argument('--new', metavar='YYMM', help="새 월 시트 생성 (예: 2609)")
    ap.add_argument('--add-item', action='append', default=[], metavar='구분:문구',
                    help="--new/--edit 시트에 항목 추가 (해당 구분 맨 끝, 여러 번 가능)")
    ap.add_argument('--edit', metavar='YYMM', help="기존 월 시트에 --add-item 추가 (이미 적은 O/X 보존)")
    ap.add_argument('--backup-dir', default=BACKUP_DIR)
    ap.add_argument('--keep-backups', type=int, default=KEEP_BACKUPS)
    ap.add_argument('--no-backup', action='store_true')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    lock = os.path.join(os.path.dirname(os.path.abspath(args.xlsx)),
                        '~$' + os.path.basename(args.xlsx))
    if os.path.exists(lock) and not (args.dry_run or args.force):
        sys.exit(f'엑셀에서 열려 있는 것 같습니다({os.path.basename(lock)}). 닫고 다시 실행하세요.')

    wb = openpyxl.load_workbook(args.xlsx)

    if LEGACY_SHEET in wb.sheetnames:                   # 옛 '체크리스트' → 월 시트로 개명
        ws = wb[LEGACY_SHEET]
        cols = date_cols(ws)
        if not cols:
            sys.exit(f"'{LEGACY_SHEET}' 시트에서 날짜(4행)를 찾지 못했습니다.")
        d = ws.cell(4, cols[0]).value
        ws.title = f'{PREFIX}{d:%y%m}'
        print(f"'{LEGACY_SHEET}' → '{ws.title}' 로 이름 변경 "
              f"({ws.cell(4, cols[0]).value:%m-%d}~{ws.cell(4, cols[-1]).value:%m-%d}, {len(cols)}일)")

    for ws in month_sheets(wb):                         # 준수율 수식 교체
        n = fix_formulas(ws)
        print(f'  {ws.title}: 준수율 수식 {n}개를 O/(O+X) 방식으로 교체')

    if args.edit:                                       # 기록이 있는 달에 항목 추가(체크 값 보존)
        name = PREFIX + args.edit
        if name not in wb.sheetnames:
            sys.exit(f"'{name}' 시트가 없습니다.")
        ws = wb[name]
        tpl = read_template(ws)
        i0, i1 = item_range(ws)
        cols = date_cols(ws)
        dates = [ws.cell(4, c).value for c in cols]
        marks = {str(t).strip(): [ws.cell(r, c).value for c in cols]
                 for r, (_, t) in zip(range(i0, i1 + 1), tpl['items'])}
        extra = [(r - (i1 + 1), c, ws.cell(r, c).value)          # 일일 준수율 아래 메모(예: '*9.8 컨디션 난조')
                 for r in range(i1 + 2, i1 + 4) for c in cols
                 if ws.cell(r, c).value is not None]
        added = [s for s in args.add_item if add_item(tpl, *[x.strip() for x in s.partition(':')[::2]])]
        if not added:
            sys.exit('추가할 항목이 없습니다(이미 있거나 --add-item 이 비었음).')
        pos = wb.sheetnames.index(name)
        values = [[marks.get(str(t).strip(), [None] * len(cols))[i] for _, t in tpl['items']]
                  for i in range(len(cols))]
        ws = build_month_sheet(wb, name, tpl, dates=dates, values=values)
        wb.move_sheet(ws, offset=pos - wb.sheetnames.index(name))
        _, n1 = item_range(ws)
        for off, c, v in extra:
            ws.cell(n1 + 1 + off, c, v)
        print(f"'{name}' 항목 추가: {', '.join(added)} (날짜 {len(cols)}일 기록 보존, 항목 {len(tpl['items'])}개)")

    if args.new:
        if not re.fullmatch(r'\d{4}', args.new):
            sys.exit('--new 는 YYMM 형식입니다 (예: 2609).')
        name = PREFIX + args.new
        src = [ws for ws in month_sheets(wb) if ws.title < name]
        if not src:
            sys.exit('기준으로 삼을 이전 월 시트가 없습니다.')
        tpl = read_template(src[-1])                    # 직전 달(항목 수정이 반영된 최신본)
        for spec in args.add_item:
            group, _, text = spec.partition(':')
            if not text:
                sys.exit(f'--add-item 은 "구분:문구" 형식입니다: {spec}')
            if add_item(tpl, group.strip(), text.strip()):
                print(f'  항목 추가: [{group.strip()}] {text.strip()}')
        build_month_sheet(wb, name, tpl)
        print(f"'{name}' 시트 생성 ({src[-1].title} 기준, 날짜 칸 {N_DATES}개, "
              f"항목 {len(tpl['items'])}개)")

    got = write_stats(wb)
    if got:
        print(f"'{STATS_SHEET}' 시트: 월 {got[0]}개 / 기록된 날 {got[1]}일 + 차트")

    if args.dry_run:
        print('--dry-run: 저장하지 않았습니다.')
        return
    if not args.no_backup:
        os.makedirs(args.backup_dir, exist_ok=True)
        bak = os.path.join(args.backup_dir,
                           f'{os.path.basename(args.xlsx)}.{dt.datetime.now():%Y%m%d_%H%M%S}.bak')
        shutil.copy2(args.xlsx, bak)
        print(f'백업: {bak}')
        prune_backups(args.backup_dir, os.path.basename(args.xlsx), args.keep_backups)
    for ws in month_sheets(wb) + ([wb[STATS_SHEET]] if STATS_SHEET in wb.sheetnames else []):
        center_vertical(ws)                     # 세로 가운데 맞춤으로 통일
    wb.save(args.xlsx)
    print(f'저장: {args.xlsx}')


if __name__ == '__main__':
    main()
