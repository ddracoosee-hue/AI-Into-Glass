"""Tests for the demo cut. Run: python -m unittest discover -s scripts"""

import ctypes
import unittest

import demo


class Cut(unittest.TestCase):
    def test_nominal_take_cuts_to_the_sixty_second_beat_sheet(self):
        cut = demo.plan_cut(demo.EXAMPLE_MARKS)
        self.assertEqual(
            [b["out"] for b in cut], [(0, 8), (8, 15), (15, 20), (20, 25), (25, 35), (35, 45), (45, 55), (55, 60)]
        )
        self.assertEqual([b["caption"] for b in cut][4], "Claude never stopped.")
        # away → continue is one unbroken stretch of the take; the story jumps three times after it
        self.assertEqual(demo.segments(cut), [(4.0, 39.0), (47.0, 57.0), (69.0, 79.0), (95.5, 100.5)])

    def test_each_driver_action_lands_inside_its_beat(self):
        marks = demo.EXAMPLE_MARKS
        beats = {b["beat"]: b["src"] for b in demo.plan_cut(marks)}
        within = lambda t, window: window[0] <= t <= window[1]
        self.assertTrue(within(marks["push"] + demo.PROMPT_HOLD, beats["detail"]))  # swipe
        self.assertTrue(within(marks["swipe"] + demo.DETAIL_HOLD, beats["approve"]))  # tap
        self.assertTrue(within(marks["env"] + demo.DIALOG_HOLD, beats["risky"]))  # dialog still up
        self.assertTrue(within(marks["rm"] + demo.DENY_AFTER, beats["deny"]))  # double-tap

    def test_a_quick_start_shortens_the_lead_in_instead_of_reusing_footage(self):
        cut = demo.plan_cut({**demo.EXAMPLE_MARKS, "push": 5.0})
        self.assertEqual(cut[0]["src"], (0.0, 5.0))
        self.assertEqual(cut[0]["out"], (0, 5.0))
        self.assertEqual(cut[-1]["out"][1], 57.0)

    def test_srt(self):
        text = demo.srt(demo.plan_cut(demo.EXAMPLE_MARKS))
        self.assertTrue(text.startswith("1\n00:00:00,000 --> 00:00:08,000\nI'm away from the desk.\n"))
        self.assertIn("8\n00:00:55,000 --> 00:01:00,000\nLogged, open-source, built on Even Hub.\n", text)

    def test_ffmpeg_graph(self):
        argv = demo.cut_command("ffmpeg", demo.plan_cut(demo.EXAMPLE_MARKS))
        graph = argv[argv.index("-filter_complex") + 1]
        self.assertEqual(graph.count("trim="), 4)
        self.assertIn("[s0][s1][s2][s3]concat=n=4:v=1:a=0[cat]", graph)
        self.assertIn("[cat]scale=1920:-2:flags=lanczos,subtitles=captions.srt:force_style='", graph)
        self.assertEqual(argv[-1], "approval-gate-demo.mp4")


class Keys(unittest.TestCase):
    def test_sendinput_struct_has_the_size_windows_expects(self):
        # SendInput silently does nothing when cbSize is wrong; INPUT is 40 bytes on Win64, 28 on Win32.
        self.assertEqual(ctypes.sizeof(demo.INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


if __name__ == "__main__":
    unittest.main()
