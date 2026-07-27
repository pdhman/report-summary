import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import collector


class CollectorTests(unittest.TestCase):
    def test_flatten_and_normalize_dataframe(self):
        columns = pd.MultiIndex.from_tuples(
            [("기준", "날짜"), ("금액", "잔고"), ("비율", "증감")]
        )
        raw = pd.DataFrame(
            [
                ["2026-07-24", "1,200", "2.5%"],
                ["2026-07-25", "1,300", "−1.0%"],
                [None, None, None],
            ],
            columns=columns,
        )

        result = collector.normalize_dataframe(raw)

        self.assertEqual(
            list(result.columns),
            ["기준_날짜", "금액_잔고", "비율_증감"],
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(result["금액_잔고"].tolist(), [1200, 1300])
        self.assertEqual(result["비율_증감"].tolist(), [2.5, -1.0])

    def test_make_unique_columns(self):
        result = collector.make_unique_columns(
            ["날짜", "전체", "전체", "  담보\n융자  ", ""]
        )

        self.assertEqual(
            result,
            ["날짜", "전체", "전체_2", "담보 융자", "열_5"],
        )

    def test_choose_data_table_prefers_dates(self):
        menu = pd.DataFrame({"항목": ["a", "b", "c", "d"]})
        data = pd.DataFrame(
            {
                "날짜": ["2026-07-23", "2026-07-24", "2026-07-25"],
                "잔고": ["1,000", "1,100", "1,200"],
            }
        )

        result = collector.choose_data_table([menu, data])

        self.assertEqual(result["잔고"].tolist(), [1000, 1100, 1200])

    def test_find_date_column(self):
        df = pd.DataFrame(
            {
                "잔고": [100, 110, 120],
                "구분": ["A", "B", "C"],
                "기준일": ["2026-07-23", "2026-07-24", "잘못된 값"],
            }
        )

        self.assertEqual(collector.find_date_column(df), "기준일")

    def test_save_to_database_deduplicates_dates(self):
        first = pd.DataFrame(
            {"날짜": ["2026-07-24", "2026-07-25"], "잔고": [100, 110]}
        )
        second = pd.DataFrame(
            {"날짜": ["2026-07-25", "2026-07-26"], "잔고": [115, 120]}
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"
            with patch.object(collector, "DB_PATH", db_path):
                collector.save_to_database("sample", first)
                collector.save_to_database("sample", second)

            with sqlite3.connect(db_path) as connection:
                saved = pd.read_sql('SELECT * FROM "sample"', connection)
            connection.close()

        self.assertEqual(len(saved), 3)
        self.assertEqual(
            saved.loc[saved["날짜"] == "2026-07-25", "잔고"].iloc[0],
            115,
        )

    def test_print_latest_change(self):
        df = pd.DataFrame(
            {
                "날짜": ["2026-07-24", "2026-07-25"],
                "잔고": [100.0, 110.0],
                "비율": [0.7, 0.8],
            }
        )
        output = StringIO()

        with redirect_stdout(output):
            collector.print_latest_change("테스트", df)

        text = output.getvalue()
        self.assertIn("기준일: 2026-07-25", text)
        self.assertIn("전일 대비 +10", text)
        self.assertIn("(+10.00%)", text)
        self.assertIn("비율: 0.80", text)
        self.assertIn("전일 대비 +0.10", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
