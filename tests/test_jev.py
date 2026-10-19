"""Tests for the embedded Jev System-1 primitives."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prompt_studio.jev import EmbeddedEngine, tokenize


class TestChoice(unittest.TestCase):
    def setUp(self):
        self.engine = EmbeddedEngine()

    def test_picks_the_highest_scoring_option_and_normalises(self):
        result = self.engine.choice(("a", "b", "c"), {"a": 0.1, "b": 2.0, "c": 0.1})
        self.assertEqual(result.value, "b")
        self.assertAlmostEqual(sum(result.probabilities.values()), 1.0, places=5)
        self.assertGreater(result.margin, 0.5)
        self.assertEqual(result.backend, "embedded")

    def test_missing_evidence_defaults_to_zero(self):
        result = self.engine.choice(("a", "b"), {})
        self.assertEqual(set(result.probabilities), {"a", "b"})
        self.assertAlmostEqual(result.probabilities["a"], 0.5, places=5)

    def test_duplicate_options_collapse_and_input_is_bounded(self):
        self.assertEqual(len(self.engine.choice(("a", "a", "b"), {}).probabilities), 2)
        with self.assertRaises(ValueError):
            self.engine.choice((), {})
        with self.assertRaises(ValueError):
            self.engine.choice(tuple(f"o{i}" for i in range(17)), {})
        with self.assertRaises(ValueError):
            self.engine.choice(("a",), {"a": float("nan")})
        with self.assertRaises(ValueError):
            self.engine.choice(("a", "b"), {"a": 1.0}, temperature=0)

    def test_is_deterministic(self):
        first = self.engine.choice(("a", "b"), {"a": 0.4, "b": 0.9})
        second = self.engine.choice(("a", "b"), {"a": 0.4, "b": 0.9})
        self.assertEqual(first.probabilities, second.probabilities)


class TestScore(unittest.TestCase):
    def setUp(self):
        self.engine = EmbeddedEngine()

    def test_clamps_out_of_range_readings(self):
        self.assertEqual(self.engine.score("x", 1.7).value, 1.0)
        self.assertEqual(self.engine.score("x", -3).value, 0.0)

    def test_rejects_blank_metric(self):
        with self.assertRaises(ValueError):
            self.engine.score("  ", 0.5)

    def test_returns_bounded_signals(self):
        result = self.engine.score("specificity", 0.5, {"a": 2.0, "b": 0.25})
        self.assertEqual(result.signals, {"a": 1.0, "b": 0.25})


class TestNoul(unittest.TestCase):
    def setUp(self):
        self.engine = EmbeddedEngine()

    def test_reads_present_fields_with_source_sentence(self):
        schema = {"budget": ("budget", "$"), "deadline": ("deadline", "tomorrow")}
        result = self.engine.noul(schema, "Keep the budget under $80. Deadline is tomorrow.")
        self.assertEqual(result.readings["budget"].cues, ("budget", "$"))
        self.assertEqual(result.readings["budget"].probability, round(1 - math.exp(-2.0), 6))
        self.assertIn("$80", result.readings["budget"].value)

    def test_absent_fields_score_zero_and_empty(self):
        result = self.engine.noul({"budget": ("budget",)}, "No money talk here.")
        self.assertEqual(result.readings["budget"].probability, 0.0)
        self.assertEqual(result.readings["budget"].value, "")

    def test_probability_is_monotonic_in_cues(self):
        one = self.engine.noul({"x": ("alpha", "beta")}, "alpha").readings["x"].probability
        two = self.engine.noul({"x": ("alpha", "beta")}, "alpha beta").readings["x"].probability
        self.assertLess(one, two)
        self.assertLess(two, 1.0)


class TestTokenizer(unittest.TestCase):
    def test_lowercases_and_splits_punctuation(self):
        self.assertEqual(tokenize("Build a $80 plan, offline!"), ["build", "a", "80", "plan", "offline"])


if __name__ == "__main__":
    unittest.main()
