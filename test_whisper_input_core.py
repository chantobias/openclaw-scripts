#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import unittest

from whisper_input_core import (
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    VK_RETURN,
    build_key_events,
    concat_audio,
    format_duration,
    language_code,
    level_ratio,
    rms_db,
)


class CoreTests(unittest.TestCase):
    def test_concat_empty(self):
        audio = concat_audio([])
        self.assertEqual(audio.size, 0)

    def test_concat_and_clip(self):
        audio = concat_audio([[1.5, 0.25], [-2.0, 0.5]])
        self.assertEqual(list(audio.round(2)), [1.0, 0.25, -1.0, 0.5])

    def test_rms_silence(self):
        self.assertLessEqual(rms_db([]), -100)
        self.assertLessEqual(rms_db([0.0, 0.0, 0.0]), -100)

    def test_rms_signal(self):
        self.assertGreater(rms_db([0.5, -0.5, 0.5]), rms_db([0.05, -0.05]))

    def test_level_ratio(self):
        self.assertEqual(level_ratio(-80), 0.0)
        self.assertEqual(level_ratio(0), 1.0)
        self.assertGreater(level_ratio(-20), 0.0)
        self.assertLess(level_ratio(-20), 1.0)

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "00:00")
        self.assertEqual(format_duration(75), "01:15")
        self.assertEqual(format_duration(-3), "00:00")

    def test_language_code(self):
        self.assertIsNone(language_code("自動偵測"))
        self.assertEqual(language_code("中文"), "zh")
        self.assertEqual(language_code("英文"), "en")
        self.assertIsNone(language_code("火星文"))

    def test_unicode_key_events(self):
        events = build_key_events("你好")
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0], (0, ord("你"), KEYEVENTF_UNICODE))
        self.assertEqual(events[1], (0, ord("你"), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        self.assertEqual(events[2], (0, ord("好"), KEYEVENTF_UNICODE))

    def test_newline_becomes_enter(self):
        events = build_key_events("a\r\nb")
        self.assertIn((VK_RETURN, 0, 0), events)
        self.assertIn((VK_RETURN, 0, KEYEVENTF_KEYUP), events)
        self.assertEqual(events[0][1], ord("a"))
        self.assertEqual(events[-2][1], ord("b"))


if __name__ == "__main__":
    unittest.main()
