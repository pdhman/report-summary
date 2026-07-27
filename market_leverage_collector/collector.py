from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime
from io import StringIO
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "market_leverage.db"

URLS = {
    "credit_balance": (
        "https://freesis.kofia.or.kr/stat/FreeSIS.do"
        "?parentDivId=MSIS10000000000000"
        "&serviceId=STATSCU0100000070"
    ),
    "market_funds": (
        "https://freesis.kofia.or.kr/stat/FreeSIS.do"
        "?parentDivId=MSIS10000000000000"
        "&serviceId=STATSCU0100000060"
    ),
}


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """다중 컬럼을 일반 문자열 컬럼으로 변환합니다."""
    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            "_".join(
                str(value).strip()
                for value in column
                if str(value).strip() not in {"", "nan"}
            )
            for column in df.columns
        ]
    else:
        df.columns = [str(column).strip() for column in df.columns]

    return df


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """공백, 쉼표, 퍼센트 등이 포함된 데이터를 정리합니다."""
    df = flatten_columns(df)
    df = df.dropna(axis=0, how="all")
    df = df.dropna(axis=1, how="all")
    df = df.reset_index(drop=True)

    for column in df.columns:
        text = (
            df[column]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace("−", "-", regex=False)
            .str.strip()
        )

        numeric = pd.to_numeric(text, errors="coerce")

        if numeric.notna().mean() >= 0.6:
            df[column] = numeric

    return df


def count_dates(df: pd.DataFrame) -> int:
    """테이블 내부 날짜 형식의 개수를 계산합니다."""
    text = df.astype(str).to_string()
    patterns = [
        r"\d{4}[./-]\d{1,2}[./-]\d{1,2}",
        r"\d{2}[./-]\d{1,2}[./-]\d{1,2}",
    ]

    return sum(len(re.findall(pattern, text)) for pattern in patterns)


def choose_data_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """날짜 데이터와 행이 많은 테이블을 선택합니다."""
    candidates = []

    for table in tables:
        if table.empty or len(table) < 3:
            continue

        date_score = count_dates(table)
        size_score = min(len(table), 100)
        column_score = len(table.columns)

        score = date_score * 100 + size_score + column_score
        candidates.append((score, table))

    if not candidates:
        raise RuntimeError(
            "데이터 테이블을 찾지 못했습니다. "
            "금융투자협회 화면 구조 또는 조회 방식 확인이 필요합니다."
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return normalize_dataframe(candidates[0][1])


def make_unique_columns(columns: list[str]) -> list[str]:
    """중복 컬럼 이름에 순번을 붙여 저장 가능한 이름으로 만듭니다."""
    counts: dict[str, int] = {}
    result = []

    for index, column in enumerate(columns, start=1):
        cleaned = re.sub(r"\s+", " ", column).strip() or f"열_{index}"
        counts[cleaned] = counts.get(cleaned, 0) + 1
        suffix = f"_{counts[cleaned]}" if counts[cleaned] > 1 else ""
        result.append(f"{cleaned}{suffix}")

    return result


def extract_accessible_grids(frame) -> list[pd.DataFrame]:
    """FreeSIS의 가상 스크롤 접근성 그리드를 DataFrame으로 변환합니다."""
    extracted = []
    grids = frame.locator('[role="grid"]')

    for grid_index in range(grids.count()):
        grid = grids.nth(grid_index)

        if grid.locator('[role="gridcell"]').count() == 0:
            continue

        columns = grid.evaluate(
            """grid => {
                const row = Array.from(grid.querySelectorAll('[role="row"]'))
                    .find(item => item.querySelector('[role="gridcell"]'));
                if (!row) return [];

                const headers = Array.from(
                    grid.querySelectorAll('[role="columnheader"]')
                );

                return Array.from(
                    row.querySelectorAll('[role="gridcell"]')
                ).map(cell => {
                    const cellRect = cell.getBoundingClientRect();
                    const center = cellRect.left + cellRect.width / 2;
                    const labels = headers
                        .map(header => ({
                            text: header.innerText.trim().replace(/\\s+/g, ' '),
                            rect: header.getBoundingClientRect(),
                        }))
                        .filter(item =>
                            item.text &&
                            item.rect.left <= center + 1 &&
                            item.rect.right >= center - 1
                        )
                        .sort((a, b) => a.rect.top - b.rect.top)
                        .map(item => item.text);

                    return {
                        colIndex: Number(cell.getAttribute('aria-colindex')),
                        name: labels
                            .filter(
                                (value, index) => labels.indexOf(value) === index
                            )
                            .join('_'),
                    };
                });
            }"""
        )

        if not columns:
            continue

        dimensions = grid.evaluate(
            """grid => {
                const candidates = Array.from(grid.querySelectorAll('*'))
                    .filter(
                        element =>
                            element.scrollHeight > element.clientHeight + 100
                    )
                    .sort(
                        (a, b) =>
                            (b.scrollHeight - b.clientHeight) -
                            (a.scrollHeight - a.clientHeight)
                    );

                if (!candidates.length) {
                    return {height: 0, viewport: 0};
                }

                return {
                    height: candidates[0].scrollHeight,
                    viewport: candidates[0].clientHeight,
                };
            }"""
        )

        height = dimensions["height"]
        viewport = dimensions["viewport"]
        step = max(viewport // 2, 200)
        targets = list(range(0, height + 1, step)) if height else [0]

        if targets[-1] != height:
            targets.append(height)

        rows_by_index = {}

        for target in targets:
            grid.evaluate(
                """(grid, target) => {
                    const candidates = Array.from(grid.querySelectorAll('*'))
                        .filter(
                            element =>
                                element.scrollHeight >
                                element.clientHeight + 100
                        )
                        .sort(
                            (a, b) =>
                                (b.scrollHeight - b.clientHeight) -
                                (a.scrollHeight - a.clientHeight)
                        );

                    if (!candidates.length) return;

                    candidates[0].scrollTop = target;
                    candidates[0].dispatchEvent(
                        new Event('scroll', {bubbles: true})
                    );
                }""",
                target,
            )
            frame.wait_for_timeout(250)

            visible_rows = grid.evaluate(
                """grid => Array.from(
                    grid.querySelectorAll('[role="row"]')
                )
                    .filter(row => row.querySelector('[role="gridcell"]'))
                    .map(row => ({
                        rowIndex: Number(row.getAttribute('aria-rowindex')),
                        cells: Array.from(
                            row.querySelectorAll('[role="gridcell"]')
                        ).map(cell => ({
                            colIndex: Number(
                                cell.getAttribute('aria-colindex')
                            ),
                            text: cell.innerText.trim(),
                        })),
                    }))"""
            )

            for row in visible_rows:
                cell_map = {
                    cell["colIndex"]: cell["text"]
                    for cell in row["cells"]
                }
                values = [
                    cell_map.get(column["colIndex"], "")
                    for column in columns
                ]

                if any(values):
                    rows_by_index[row["rowIndex"]] = values

        if rows_by_index:
            column_names = make_unique_columns(
                [column["name"] for column in columns]
            )
            rows = [
                rows_by_index[index]
                for index in sorted(rows_by_index)
            ]
            extracted.append(pd.DataFrame(rows, columns=column_names))

    return extracted


def collect_rendered_table(url: str) -> pd.DataFrame:
    """Playwright로 HTML 테이블과 FreeSIS 접근성 그리드를 수집합니다."""
    extracted_tables: list[pd.DataFrame] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        page = browser.new_page(
            locale="ko-KR",
            viewport={"width": 1600, "height": 1200},
        )

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=90_000,
        )

        page.wait_for_timeout(12_000)

        for frame in page.frames:
            try:
                extracted_tables.extend(extract_accessible_grids(frame))
            except Exception:
                pass

            try:
                html = frame.content()
                frame_tables = pd.read_html(StringIO(html))
                extracted_tables.extend(frame_tables)
            except (ImportError, ValueError, TypeError):
                continue

        browser.close()

    return choose_data_table(extracted_tables)

def find_date_column(df: pd.DataFrame) -> str | None:
    """날짜로 변환 가능한 비율이 높은 컬럼을 찾습니다."""
    for column in df.columns:
        values = df[column]

        # 숫자 값은 나노초 타임스탬프로 해석될 수 있으므로 제외합니다.
        if pd.api.types.is_numeric_dtype(values):
            continue

        text = values.astype("string").str.strip()
        date_like = text.str.match(
            r"^\d{2,4}[./-]\d{1,2}[./-]\d{1,2}$",
            na=False,
        )

        if date_like.mean() < 0.5:
            continue

        parsed = pd.to_datetime(
            text.where(date_like),
            errors="coerce",
            format="mixed",
        )

        if parsed.notna().mean() >= 0.5:
            return column

    return None


def save_to_database(table_name: str, new_df: pd.DataFrame) -> None:
    """기존 데이터와 합친 뒤 SQLite에 저장합니다."""
    new_df = new_df.copy()
    new_df["수집시각"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    date_column = find_date_column(new_df)

    with closing(sqlite3.connect(DB_PATH)) as connection:
        try:
            old_df = pd.read_sql(
                f'SELECT * FROM "{table_name}"',
                connection,
            )
        except Exception:
            old_df = pd.DataFrame()

        combined = pd.concat(
            [old_df, new_df],
            ignore_index=True,
        )

        if date_column and date_column in combined.columns:
            combined = combined.drop_duplicates(
                subset=[date_column],
                keep="last",
            )

        combined.to_sql(
            table_name,
            connection,
            if_exists="replace",
            index=False,
        )



def format_number(value: float, signed: bool = False) -> str:
    """정수는 정수로, 소수는 두 자리까지 표시합니다."""
    decimals = 0 if float(value).is_integer() else 2
    sign = "+" if signed else ""
    return format(value, f"{sign},.{decimals}f")


def print_latest_change(name: str, df: pd.DataFrame) -> None:
    """최근 2개 거래일의 숫자 데이터 변화를 출력합니다."""
    date_column = find_date_column(df)

    if date_column is None:
        print(f"[{name}] 날짜 컬럼을 찾지 못했습니다.")
        print(df.tail())
        return

    working = df.copy()
    working["_date"] = pd.to_datetime(
        working[date_column],
        errors="coerce",
    )

    working = (
        working.dropna(subset=["_date"])
        .sort_values("_date")
        .reset_index(drop=True)
    )

    if len(working) < 2:
        print(f"[{name}] 비교 가능한 데이터가 부족합니다.")
        return

    previous = working.iloc[-2]
    latest = working.iloc[-1]

    print(f"\n[{name}]")
    print(f"기준일: {latest['_date'].date()}")

    numeric_columns = working.select_dtypes(include="number").columns

    for column in numeric_columns:
        if column == "_date":
            continue

        old_value = previous[column]
        new_value = latest[column]

        if pd.isna(old_value) or pd.isna(new_value):
            continue

        change = new_value - old_value
        change_rate = change / old_value * 100 if old_value != 0 else float("nan")

        print(
            f"{column}: {format_number(new_value)} "
            f"/ 전일 대비 {format_number(change, signed=True)} "
            f"({change_rate:+.2f}%)"
        )


def main() -> None:
    for name, url in URLS.items():
        print(f"\n{name} 수집 중...")

        try:
            df = collect_rendered_table(url)

            csv_path = DATA_DIR / f"{name}.csv"
            df.to_csv(
                csv_path,
                index=False,
                encoding="utf-8-sig",
            )

            save_to_database(name, df)
            print_latest_change(name, df)

            print(f"저장 완료: {csv_path}")

        except Exception as error:
            print(f"{name} 수집 실패: {error}")


if __name__ == "__main__":
    main()
