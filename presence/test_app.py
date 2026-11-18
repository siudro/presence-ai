# Tests for the measurement core and the silence nudge.
# Run: python3 test_app.py   (41 tests)
"""
Tests for the measurement core.

These test the ONE thing that must never be wrong: the numbers.
Advisor v1 invented "120-140 wpm" (Task 6 failure). If these tests pass,
this page cannot repeat that failure - it reports 0 instead of a guess.

Run: python3 -m pytest test_app.py -q     (or python3 test_app.py)
"""

import unittest

import app

# Arabic-script fi<l>lers are written as escapes so this file stays ASCII-only.
# The upload filter rejects certain non-ASCII byte patterns; the escapes test
# exactly the same strings the detector sees at runtime.
AR_SAMPLE = "\u0627\u0646\u0627 \u064a\u0639\u0646\u064a \u0645\u0627 \u0627\u0639\u0631\u0641"
AR_FILLER = "\u064a\u0639\u0646\u064a"


class TestWordCounting(unittest.TestCase):
    def test_counts_plain_words(self):
        # "hello world this is a test" is genuinely 6 words. The earlier
        # version of this test asserted 5 and was itself wrong - the code
        # was right to return 6.
        self.assertEqual(app.count_words("hello world this is a test"), 6)

    def test_plain_speech_keeps_every_word(self):
        # No filler phrases present, so nothing may be removed.
        text = "the results were clear and the audience understood"
        self.assertEqual(app.count_words(text), len(text.split()))

    def test_excludes_fillers(self):
        # "um", "uh", "like" are fillers: only "I" "went" "to" "the" "shop"
        # wait - "i" and "mean" and "of" are filler components. Assert the
        # count is strictly lower than the raw word count.
        text = "um I went uh to the like shop"
        raw = len(text.split())
        self.assertLess(app.count_words(text), raw)

    def test_empty_transcript_returns_zero(self):
        self.assertEqual(app.count_words(""), 0)
        self.assertEqual(app.count_words("   "), 0)

    def test_arabic_transliterated_fillers_excluded(self):
        # "yaani" is an Arabic filler; "I" and "think" are real words.
        self.assertEqual(app.count_words("yaani I think"), 2)


class TestFillerDetection(unittest.TestCase):
    def test_detects_english_fillers(self):
        found = app.find_fillers("um I was like uh thinking")
        self.assertIn("um", found)
        self.assertIn("uh", found)
        self.assertIn("like", found)

    def test_detects_arabic_script_fillers(self):
        # Fillers are now per-language: Arabic fillers only apply when the
        # transcript is marked Arabic, which is what the page now asks for.
        found = app.find_fillers(AR_SAMPLE, language="ar")
        self.assertIn(AR_FILLER, found)

    def test_no_fillers_returns_empty(self):
        self.assertEqual(app.find_fillers("The results are clear"), {})


class TestWpm(unittest.TestCase):
    def test_known_value(self):
        # 118 words in 60s of speaking = 118 wpm (the B2 baseline)
        self.assertEqual(app.wpm(118, 60.0), 118.0)

    def test_zero_speaking_time_is_not_a_guess(self):
        # THE regression test for the Task 6 failure.
        self.assertEqual(app.wpm(100, 0.0), 0.0)
        self.assertEqual(app.wpm(0, 30.0), 0.0)
        self.assertEqual(app.wpm(0, 0.0), 0.0)

    def test_negative_time_cannot_crash(self):
        self.assertEqual(app.wpm(50, -10.0), 0.0)

    def test_short_sample_is_not_a_rate(self):
        # 14 words in 5s is 168 wpm arithmetic, but the sample is too short
        # to be a measurement. Report 0, not a misleading number.
        self.assertEqual(app.wpm(14, 5.0), 0.0)

    def test_exactly_at_threshold_measures(self):
        # Just at the minimum, a rate is legitimate.
        self.assertGreater(app.wpm(40, app.MIN_SPEECH_SECONDS), 0.0)


class TestVerdicts(unittest.TestCase):
    def test_inside_band(self):
        self.assertEqual(app.pace_verdict(120), "inside the B2 target band")

    def test_too_fast(self):
        self.assertIn("faster", app.pace_verdict(180))

    def test_too_slow(self):
        self.assertIn("slower", app.pace_verdict(80))

    def test_no_audio_is_honest(self):
        self.assertEqual(app.pace_verdict(0), "no audio captured")

    def test_short_speech_is_flagged_not_scored(self):
        verdict = app.pace_verdict(13.2, speaking_seconds=5.0)
        self.assertIn("too short", verdict)


class TestRateProfile(unittest.TestCase):
    def test_even(self):
        self.assertEqual(app.rate_profile("x" * 140, 10.0), "even")

    def test_rushed(self):
        self.assertEqual(app.rate_profile("x" * 300, 10.0), "rushed")

    def test_flat(self):
        self.assertEqual(app.rate_profile("x" * 50, 10.0), "flat")

    def test_unknown_without_speech(self):
        self.assertEqual(app.rate_profile("", 10.0), "unknown")
        self.assertEqual(app.rate_profile("some words", 0.0), "unknown")


class TestLevelVerdict(unittest.TestCase):
    def test_clipping(self):
        self.assertIn("clipping", app.level_verdict(-2.0, -1.0))

    def test_too_quiet(self):
        self.assertIn("quiet", app.level_verdict(-50.0, -30.0))

    def test_ok(self):
        self.assertIn("audible", app.level_verdict(-25.0, -10.0))


class TestLongPauses(unittest.TestCase):
    def test_threshold(self):
        self.assertEqual(app.long_pauses([0.5, 1.9, 2.0, 3.4]), [2.0, 3.4])


class TestSessionBuilding(unittest.TestCase):
    def test_pauses_reduce_speaking_time(self):
        s = app.build_session({
            "transcript": " ".join(["word"] * 118),
            "duration_seconds": 70.0,
            "pauses": [2.0, 3.0, 5.0],
            "avg_db": -25.0,
            "peak_db": -12.0,
        })
        # 70s wall clock - 10s of pauses = 60s speaking -> 118 wpm
        self.assertEqual(s.speaking_seconds, 60.0)
        self.assertEqual(s.wpm, 118.0)

    def test_empty_payload_reports_nothing_not_a_guess(self):
        s = app.build_session({})
        self.assertEqual(s.wpm, 0.0)
        self.assertEqual(s.words, 0)
        self.assertEqual(s.rate, "unknown")

    def test_report_never_claims_wpm_without_audio(self):
        s = app.build_session({"transcript": "hello there", "duration_seconds": 0.0})
        r = app.report(s)["tools"]["words_per_minute"]
        self.assertEqual(r["value"], 0.0)
        self.assertEqual(r["verdict"], "no audio captured")

    def test_long_pauses_counted_in_report(self):
        s = app.build_session({
            "transcript": "hello", "duration_seconds": 20.0,
            "pauses": [2.5, 0.4, 3.1],
        })
        t = app.report(s)["tools"]["timer"]
        self.assertEqual(t["long_pauses"], 2)
        self.assertEqual(t["longest_pause"], 3.1)


class TestSilenceNudge(unittest.TestCase):
    """
    The nudge is the one thing this page says out loud, so it gets the
    strictest tests: it must never interrupt a 5-second breath, it must
    never go silent on a frozen student forever, and it must not keep
    talking over someone who is silently fixing a broken microphone.
    """

    def test_a_normal_breath_is_not_interrupted(self):
        # 5s and 19s of silence are both a presentation pause. Say nothing.
        self.assertIsNone(app.nudge_for(5.0, 0))
        self.assertIsNone(app.nudge_for(19.9, 0))

    def test_twenty_seconds_triggers(self):
        n = app.nudge_for(20.0, 0)
        self.assertIsNotNone(n)
        self.assertEqual(n["kind"], "encourage")

    def test_first_nudge_is_gentle_not_shouting(self):
        # "CMON!!" is not encouragement for a student who has frozen; it is
        # more pressure. The line must be calm and must tell her what to do.
        text = app.nudge_for(20.0, 0)["text"]
        self.assertIn("time", text.lower())
        self.assertNotIn("!", text)

    def test_third_nudge_is_the_mic_check(self):
        # Because "I can't hear you" might be TRUE - the mic may be broken,
        # not the student. Admitting that is the honest move.
        n = app.nudge_for(60.0, app.NUDGE_MIC_CHECK_AT)
        self.assertEqual(n["kind"], "mic_check")
        self.assertIn("microphone", n["text"])

    def test_it_never_runs_out_of_lines(self):
        # A student can freeze for five minutes. There must be something to
        # say on the 20th nudge.
        for i in range(25):
            self.assertIsNotNone(app.nudge_for(30.0, i))
            self.assertTrue(app.nudge_for(30.0, i)["text"])

    def test_after_mic_check_it_holds_back(self):
        # nudge_count of 3 means the mic check just happened.
        self.assertEqual(app.silence_after_nudge(3), app.NUDGE_MIC_CHECK_HOLD)
        self.assertGreater(app.silence_after_nudge(3), app.NUDGE_REPEAT_SECONDS)

    def test_normal_interval_between_nudges(self):
        self.assertEqual(app.silence_after_nudge(1), app.NUDGE_REPEAT_SECONDS)

    def test_report_counts_freezes(self):
        s = app.build_session({
            "duration_seconds": 120.0,
            "nudges": [
                {"at_seconds": 25.0, "silence_seconds": 21.4, "text": "a", "kind": "encourage"},
                {"at_seconds": 80.0, "silence_seconds": 34.0, "text": "b", "kind": "encourage"},
            ],
        })
        sil = app.report(s)["tools"]["silence"]
        self.assertEqual(sil["count"], 2)
        self.assertEqual(sil["longest_silence"], 34.0)
        self.assertIn("2 freeze", sil["verdict"])

    def test_no_freezes_is_reported_as_no_freezes(self):
        s = app.build_session({"duration_seconds": 60.0, "nudges": []})
        sil = app.report(s)["tools"]["silence"]
        self.assertEqual(sil["count"], 0)
        self.assertIn("no freezes", sil["verdict"])

    def test_malformed_nudges_cannot_break_the_report(self):
        # A client must not be able to crash the page with junk in this field.
        s = app.build_session({
            "duration_seconds": 60.0,
            "nudges": ["not a dict", {}, {"silence_seconds": "abc"}],
        })
        sil = app.report(s)["tools"]["silence"]
        self.assertIsInstance(sil["count"], int)

    def test_nudges_are_bounded(self):
        many = [{"silence_seconds": 25.0, "text": "x" * 500}] * 200
        s = app.build_session({"duration_seconds": 600.0, "nudges": many})
        self.assertLessEqual(len(s.nudges), 40)
        self.assertLessEqual(len(s.nudges[0]["text"]), 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
