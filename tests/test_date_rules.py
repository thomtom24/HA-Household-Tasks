"""Standalone date-only scheduling tests: python -m unittest discover -s tests."""
import importlib.util
from datetime import date
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "custom_components" / "household_tasks" / "date_rules.py"
spec = importlib.util.spec_from_file_location("household_tasks_date_rules", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DateOnlyTests(unittest.TestCase):
    def test_week_later_regardless_of_completion_time(self):
        self.assertEqual(module.completion_due_date(date(2026, 11, 10), 7), date(2026, 11, 17))

    def test_month_boundary(self):
        self.assertEqual(module.completion_due_date(date(2026, 11, 29), 7), date(2026, 12, 6))

    def test_calendar_previous_day(self):
        self.assertEqual(module.offset_due_date(date(2026, 11, 20), 1), date(2026, 11, 19))

    def test_calendar_across_dst(self):
        self.assertEqual(module.offset_due_date(date(2027, 3, 29), 2), date(2027, 3, 27))

    def test_legacy_offsets(self):
        self.assertEqual(module.legacy_calendar_days({"calendar_offset_hours": 24}), 1)
        self.assertEqual(module.legacy_calendar_days({"calendar_offset_hours": 48}), 2)
        self.assertEqual(module.legacy_calendar_days({"calendar_offset_hours": 0}), 0)
        self.assertEqual(module.legacy_calendar_days({"calendar_offset_days": 3, "calendar_offset_hours": 24}), 3)


if __name__ == "__main__":
    unittest.main()
