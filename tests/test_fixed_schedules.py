"""Standalone recurrence regression tests: python -m unittest discover -s tests."""
import importlib.util
from datetime import datetime
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "household_tasks_scheduling",
    Path(__file__).resolve().parents[1] / "custom_components" / "household_tasks" / "scheduling.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fixed_schedule = module.fixed_schedule


class FixedScheduleTests(unittest.TestCase):
    def test_monthly_first_future_occurrence(self):
        now = datetime(2026, 11, 13, 12)
        due, _ = fixed_schedule(now, {"trigger_type": "monthly", "month_day": 20}, now)
        self.assertEqual(due, datetime(2026, 11, 20, 0))

    def test_monthly_day_31_clamped(self):
        now = datetime(2027, 2, 28, 12)
        due, _ = fixed_schedule(now, {"trigger_type": "monthly", "month_day": 31}, None)
        self.assertEqual(due, datetime(2027, 2, 28, 0))

    def test_quarterly(self):
        now = datetime(2026, 11, 13)
        due, _ = fixed_schedule(now, {"trigger_type": "monthly", "schedule_interval": 3, "month_day": 1}, now)
        self.assertEqual(due, datetime(2027, 1, 1, 0))

    def test_yearly_leap_day_clamped(self):
        now = datetime(2027, 3, 1)
        due, _ = fixed_schedule(now, {"trigger_type": "yearly", "year_month": 2, "month_day": 29}, None)
        self.assertEqual(due, datetime(2027, 2, 28, 0))

    def test_yearly_next_leap_year(self):
        now = datetime(2028, 2, 29, 12)
        due, _ = fixed_schedule(now, {"trigger_type": "yearly", "year_month": 2, "month_day": 29}, None)
        self.assertEqual(due, datetime(2028, 2, 29, 0))

    def test_completed_occurrence_advances(self):
        now = datetime(2026, 11, 13, 12)
        config = {"trigger_type": "monthly", "month_day": 13}
        _, token = fixed_schedule(now, config, None)
        due, _ = fixed_schedule(now, config, None, token)
        self.assertEqual(due, datetime(2026, 12, 13, 0))

    def test_late_completion_does_not_shift(self):
        now = datetime(2026, 11, 20, 12)
        config = {"trigger_type": "monthly", "month_day": 13}
        _, token = fixed_schedule(now, config, None)
        due, _ = fixed_schedule(now, config, None, token)
        self.assertEqual(due, datetime(2026, 12, 13, 0))

    def test_complete_next_month_early(self):
        now = datetime(2026, 11, 14, 12)
        config = {"trigger_type": "monthly", "month_day": 20}
        due, token = fixed_schedule(now, config, None)
        self.assertEqual(due, datetime(2026, 11, 20))
        next_due, _ = fixed_schedule(now, config, None, token)
        self.assertEqual(next_due, datetime(2026, 12, 20))

    def test_complete_next_year_early(self):
        now = datetime(2026, 11, 14, 12)
        config = {"trigger_type": "yearly", "year_month": 12, "month_day": 1}
        due, token = fixed_schedule(now, config, None)
        next_due, _ = fixed_schedule(now, config, None, token)
        self.assertEqual(next_due, datetime(2027, 12, 1))

    def test_initial_schedule_skips_past(self):
        now = datetime(2026, 11, 13, 12)
        due, _ = fixed_schedule(now, {"trigger_type": "yearly", "year_month": 1, "month_day": 1}, now)
        self.assertEqual(due, datetime(2027, 1, 1, 0))


if __name__ == "__main__":
    unittest.main()
