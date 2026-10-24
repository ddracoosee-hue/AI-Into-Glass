"""Demo cut tests: beat windows, output times, captions. Run: python -m unittest discover -s scripts"""

import unittest

import demo_run as R

# Marks from the second clean rehearsal on Oct 22 (seconds into the take).
MARKS = {
    "start": 0.0,
    "deck": 22.4,
    "b2": 28.8,
    "run-detail": 32.7,
    "b3": 45.9,
    "error-open": 50.1,
    "b4": 53.8,
    "sessions": 61.2,
    "b5": 63.5,
    "focus-on": 68.1,
    "hub-asks": 77.9,
    "outside": 79.6,
    "push-prompt": 90.1,
    "push-restored": 92.7,
    "rm-prompt": 103.8,
    "rm-reply": 107.2,
    "b8": 110.4,
    "todo-done": 117.6,
    "b9": 120.8,
    "summary": 125.0,
}


class Cut(unittest.TestCase):
    def test_every_beat_has_a_window_and_the_cut_fits(self):
        plan = R.plan(MARKS)
        self.assertEqual([b["key"] for b in plan], [b[0] for b in R.BEATS])
        total = plan[-1]["out_end"]
        self.assertLessEqual(total, 120)
        for b in plan:
            self.assertGreaterEqual(
                b["out_end"] - b["out_start"], 2.5, b["key"]
            )  # every caption stays up 2.5 s or more
        for a, b in zip(plan, plan[1:]):
            self.assertEqual(a["out_end"], b["out_start"])

    def test_a_beat_can_skip_the_wait_in_its_middle(self):
        focus = next(b for b in R.plan(MARKS) if b["key"] == "focus")
        self.assertEqual(focus["segments"], [(63.5, 70.6), (76.9, 82.6)])  # the hub session's thinking is cut out
        self.assertAlmostEqual(R.out_time(R.plan(MARKS), "focus", 79.6) - focus["out_start"], 7.1 + 2.7, places=3)

    def test_out_time_refuses_a_moment_outside_its_beat(self):
        with self.assertRaises(ValueError):
            R.out_time(R.plan(MARKS), "deck", 40.0)

    def test_an_empty_window_is_an_error(self):
        with self.assertRaises(ValueError):
            R.plan({**MARKS, "run-detail": 10.0})

    def test_stills_land_inside_their_beats(self):
        plan = R.plan(MARKS)
        for _, beat, mark, offset in R.STILLS:
            R.out_time(plan, beat, MARKS[mark] + offset)

    def test_captions_round_trip_through_srt(self):
        plan = R.plan(MARKS)
        srt = R.to_srt(plan)
        self.assertTrue(
            srt.startswith("1\n00:00:00,000 --> 00:00:06,000\nThree AI agents. Two projects. One glance.\n")
        )
        self.assertEqual(R.from_srt(srt), [b["caption"] for b in plan])
        edited = srt.replace("Two taps to say no.", "Two taps, and it's a no.")
        self.assertIn("Two taps, and it's a no.", R.from_srt(edited))


if __name__ == "__main__":
    unittest.main()
