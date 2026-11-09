"""validate_cuin(deadline=...): several short attempts inside a total time budget. No bench, no network."""
import sys
import types
import unittest
from unittest import mock

frappe = types.ModuleType("frappe")
frappe.whitelist = lambda *a, **k: (lambda f: f)
frappe.log_error = lambda *a, **k: None
sys.modules.setdefault("frappe", frappe)

import requests  # noqa: E402

from vcl_kra_validation import api  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class DeadlineTests(unittest.TestCase):
    def run_with(self, post, deadline, cuin="0123456789"):
        clock = FakeClock()
        with mock.patch.object(api.time, "monotonic", clock.monotonic), mock.patch.object(api.time, "sleep", clock.sleep), \
                mock.patch.object(api.requests, "post", post), mock.patch.object(api.requests, "get", post):
            out = api.validate_cuin(cuin, deadline=deadline)
        return out, clock.t - 1000.0

    def test_dropped_requests_give_up_inside_the_budget(self):
        calls = []

        def post(*a, **k):
            calls.append(k["timeout"])
            clock_advance = k["timeout"][1]
            raise requests.exceptions.ReadTimeout("dropped")

        # each timed-out attempt costs its read timeout: advance the fake clock inside the call
        clock = FakeClock()

        def post_costly(*a, **k):
            calls.append(k["timeout"])
            clock.t += k["timeout"][1]
            raise requests.exceptions.ReadTimeout("dropped")

        with mock.patch.object(api.time, "monotonic", clock.monotonic), mock.patch.object(api.time, "sleep", clock.sleep), \
                mock.patch.object(api.requests, "post", post_costly):
            out = api.validate_cuin("0123456789", deadline=15)
        self.assertFalse(out["valid"])
        self.assertTrue(out["timed_out"])
        self.assertLessEqual(clock.t - 1000.0, 15.5)
        self.assertGreaterEqual(len(calls), 2, "more than one short attempt, not one long wait")
        self.assertTrue(all(c[1] <= 5 for c in calls))

    def test_a_later_attempt_can_still_answer(self):
        clock = FakeClock()
        n = {"i": 0}
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"success": False, "errorDto": {"errorMsg": "not found"}}
        ok.text = "{}"

        def post(*a, **k):
            n["i"] += 1
            if n["i"] < 3:
                clock.t += k["timeout"][1]
                raise requests.exceptions.ReadTimeout("dropped")
            return ok

        with mock.patch.object(api.time, "monotonic", clock.monotonic), mock.patch.object(api.time, "sleep", clock.sleep), \
                mock.patch.object(api.requests, "post", post):
            out = api.validate_cuin("0123456789", deadline=15)
        self.assertNotIn("timed_out", out)
        self.assertEqual(n["i"], 3)

    def test_connection_errors_also_retry_inside_the_budget(self):
        clock = FakeClock()

        def post(*a, **k):
            clock.t += 1
            raise requests.exceptions.ConnectionError("reset")

        with mock.patch.object(api.time, "monotonic", clock.monotonic), mock.patch.object(api.time, "sleep", clock.sleep), \
                mock.patch.object(api.requests, "post", post):
            out = api.validate_cuin("0123456789", deadline=15)
        self.assertTrue(out["timed_out"])
        self.assertLessEqual(clock.t - 1000.0, 16)

    def test_no_deadline_keeps_the_original_shape(self):
        seen = []

        def post(*a, **k):
            seen.append(k["timeout"])
            raise requests.exceptions.ReadTimeout("slow")

        with mock.patch.object(api.requests, "post", post):
            out = api.validate_cuin("0123456789")
        self.assertEqual(seen, [(10, 20)] * 3)
        self.assertNotIn("timed_out", out)

    def test_bad_deadline_is_ignored(self):
        with mock.patch.object(api.requests, "post", side_effect=requests.exceptions.ReadTimeout("x")):
            out = api.validate_cuin("0123456789", deadline="soon")
        self.assertFalse(out["valid"])


if __name__ == "__main__":
    unittest.main()
