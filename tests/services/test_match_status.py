import unittest

from app.services.match_status import (
    PREDICTABLE_FUTURE_MATCH_STATUSES,
    is_predictable_future_match,
)


class MatchStatusTests(unittest.TestCase):
    def test_scheduled_and_ns_are_equally_predictable(self) -> None:
        self.assertTrue(is_predictable_future_match("SCHEDULED"))
        self.assertTrue(is_predictable_future_match("NS"))
        self.assertEqual(
            is_predictable_future_match("SCHEDULED"),
            is_predictable_future_match("NS"),
        )

    def test_predictable_future_statuses_constant_matches_helper(self) -> None:
        self.assertEqual(PREDICTABLE_FUTURE_MATCH_STATUSES, ("SCHEDULED", "NS"))
        for status in PREDICTABLE_FUTURE_MATCH_STATUSES:
            self.assertTrue(is_predictable_future_match(status))
        self.assertFalse(is_predictable_future_match("FINISHED"))
