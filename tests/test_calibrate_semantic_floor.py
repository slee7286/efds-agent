"""Tests for the SEMANTIC_FLOOR calibration script.

The live sweep needs a bearer token and a populated corpus, so the network path
is exercised by hand. What is tested here is the decision logic: which floors
were asked for, how a sweep is reduced to numbers, and which floor gets
recommended. A wrong recommendation is the failure that matters, because it
would be applied as a configuration value.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load():
    # The script is loaded by path because scripts/ is not an importable package.
    spec = importlib.util.spec_from_file_location("calibrate_semantic_floor", SCRIPTS / "calibrate_semantic_floor.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calibrate = _load()


class TestParseFloors:
    def test_sorts_and_deduplicates(self):
        assert calibrate.parse_floors(["0.40", "0.20", "0.40", "0.30"]) == [0.20, 0.30, 0.40]

    def test_rejects_out_of_range(self):
        with pytest.raises(SystemExit):
            calibrate.parse_floors(["1.5"])

    def test_rejects_non_numeric(self):
        with pytest.raises(SystemExit):
            calibrate.parse_floors(["high"])

    def test_requires_at_least_one(self):
        with pytest.raises(SystemExit):
            calibrate.parse_floors([])


class TestSummarise:
    def test_reduces_a_sweep(self):
        result = calibrate.summarise(0.30, [3, 10, 0, 7], [True, True, False, True], result_limit=10)
        assert result["floor"] == 0.30
        assert result["questions"] == 4
        assert result["mean_rows"] == 5.0
        assert result["max_rows"] == 10
        assert result["saturated"] == 1  # one question filled the whole window
        assert result["empty"] == 1
        assert result["hits"] == 3
        assert result["hit_rate"] == 0.75

    def test_empty_sweep_does_not_divide_by_zero(self):
        result = calibrate.summarise(0.30, [], [], result_limit=10)
        assert result["questions"] == 0
        assert result["hit_rate"] == 0.0


class TestRecommend:
    @staticmethod
    def _summary(floor, hit_rate, saturated=0):
        return {"floor": floor, "questions": 4, "hit_rate": hit_rate, "saturated": saturated}

    def test_prefers_the_highest_floor_that_keeps_the_best_hit_rate(self):
        # 0.20 saturates the window, so it is the weakest filter that still finds
        # everything; 0.40 is the strongest floor that loses nothing.
        summaries = [
            self._summary(0.20, 1.0, saturated=4),
            self._summary(0.30, 1.0),
            self._summary(0.40, 1.0),
            self._summary(0.50, 0.75),
        ]
        assert calibrate.recommend(summaries)["floor"] == 0.40

    def test_returns_none_when_no_floor_finds_evidence(self):
        summaries = [self._summary(0.20, 0.0), self._summary(0.30, 0.0)]
        assert calibrate.recommend(summaries) is None

    def test_ignores_floors_with_no_questions(self):
        summaries = [{"floor": 0.9, "questions": 0, "hit_rate": 0.0, "saturated": 0}, self._summary(0.20, 0.5)]
        assert calibrate.recommend(summaries)["floor"] == 0.20


class TestRowMatchesCase:
    def test_matches_on_expected_source_type(self):
        case = {"expected_source_types": ["knowledge_process"]}
        assert calibrate.row_matches_case({"source_type": "knowledge_process"}, case)
        assert not calibrate.row_matches_case({"source_type": "icu_article"}, case)

    def test_falls_back_to_expected_claims(self):
        case = {"expected_source_types": [], "expected_claims": ["speaker"]}
        assert calibrate.row_matches_case({"content": "Contact the speaker coordinator."}, case)
        assert not calibrate.row_matches_case({"content": "Nothing relevant."}, case)

    def test_a_case_with_no_expectations_matches_nothing(self):
        assert not calibrate.row_matches_case({"source_type": "anything"}, {})


class TestLoadQuestions:
    def test_reads_case_objects(self, tmp_path):
        path = tmp_path / "cases.json"
        path.write_text('[{"question": "How do I book a room?"}]', encoding="utf-8")
        assert calibrate.load_questions(path) == [{"question": "How do I book a room?"}]

    def test_reads_bare_strings(self, tmp_path):
        path = tmp_path / "cases.json"
        path.write_text('["one", "two"]', encoding="utf-8")
        assert [item["question"] for item in calibrate.load_questions(path)] == ["one", "two"]

    def test_rejects_an_empty_file(self, tmp_path):
        path = tmp_path / "cases.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(SystemExit):
            calibrate.load_questions(path)

    def test_reports_a_missing_file(self, tmp_path):
        with pytest.raises(SystemExit):
            calibrate.load_questions(tmp_path / "absent.json")
