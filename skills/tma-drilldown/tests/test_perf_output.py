"""Tests for perf stat output parsing."""

import pytest
from perfmon_tools.core.perf_output import (
    parse_perf_stat_text,
    parse_perf_stat_json,
    parse_perf_stat_interval,
    parse_auto,
    _normalize_event_values,
    PERF_TO_PERFMON,
)


SAMPLE_TEXT_OUTPUT = """\
 Performance counter stats for 'sleep 1':

     1,234,567,890      cycles                    (66.52%)
       456,789,012      instructions              #    0.37  insn per cycle
        12,345,678      cache-misses
       <not counted>    branch-misses

       1.001234567 seconds time elapsed
"""

SAMPLE_JSON_OUTPUT = """\
{"counter-value": "1234567890.000000", "unit": "", "event": "cycles", "pcnt-running": 66.52}
{"counter-value": "456789012.000000", "unit": "", "event": "instructions", "pcnt-running": 100.00}
{"counter-value": "<not counted>", "unit": "", "event": "branch-misses", "pcnt-running": 0.00}
"""

SAMPLE_INTERVAL_OUTPUT = """\
1.000123456;cycles;5000000;;100.00
1.000123456;instructions;2500000;;100.00
2.000234567;cycles;5100000;;100.00
2.000234567;instructions;2600000;;100.00
"""


class TestParseText:
    def test_basic_values(self):
        result = parse_perf_stat_text(SAMPLE_TEXT_OUTPUT)
        assert result.event_values["cycles"] == 1234567890.0
        assert result.event_values["instructions"] == 456789012.0
        assert result.event_values["cache-misses"] == 12345678.0

    def test_duration(self):
        result = parse_perf_stat_text(SAMPLE_TEXT_OUTPUT)
        assert result.duration_seconds == pytest.approx(1.001234567)

    def test_multiplexing_detection(self):
        result = parse_perf_stat_text(SAMPLE_TEXT_OUTPUT)
        # cycles at 66.52% should be flagged
        mux_events = [m.event for m in result.multiplexing_issues]
        assert "cycles" in mux_events
        assert "branch-misses" in mux_events  # <not counted>

    def test_not_counted(self):
        result = parse_perf_stat_text(SAMPLE_TEXT_OUTPUT)
        assert "branch-misses" not in result.event_values

    def test_raw_text_preserved(self):
        result = parse_perf_stat_text(SAMPLE_TEXT_OUTPUT)
        assert result.raw_text == SAMPLE_TEXT_OUTPUT


class TestParseJson:
    def test_basic_values(self):
        result = parse_perf_stat_json(SAMPLE_JSON_OUTPUT)
        assert result.event_values["cycles"] == 1234567890.0
        assert result.event_values["instructions"] == 456789012.0

    def test_not_counted(self):
        result = parse_perf_stat_json(SAMPLE_JSON_OUTPUT)
        assert "branch-misses" not in result.event_values
        mux_events = [m.event for m in result.multiplexing_issues]
        assert "branch-misses" in mux_events

    def test_multiplexing_detection(self):
        result = parse_perf_stat_json(SAMPLE_JSON_OUTPUT)
        mux_events = [m.event for m in result.multiplexing_issues]
        assert "cycles" in mux_events  # 66.52% < 90%


class TestParseInterval:
    def test_two_intervals(self):
        intervals = parse_perf_stat_interval(SAMPLE_INTERVAL_OUTPUT)
        assert len(intervals) == 2
        assert intervals[0]["cycles"] == 5000000.0
        assert intervals[0]["instructions"] == 2500000.0
        assert intervals[1]["cycles"] == 5100000.0


class TestNormalize:
    def test_perf_to_perfmon_mapping(self):
        values = {"topdown-fe-bound": 25.0, "topdown-be-bound": 40.0, "slots": 1000000}
        normalized = _normalize_event_values(values)
        assert normalized["PERF_METRICS.FRONTEND_BOUND"] == 25.0
        assert normalized["PERF_METRICS.BACKEND_BOUND"] == 40.0
        assert normalized["TOPDOWN.SLOTS"] == 1000000

    def test_cpu_wrapper_strip(self):
        values = {"cpu/INST_RETIRED.ANY/": 500}
        normalized = _normalize_event_values(values)
        assert normalized["INST_RETIRED.ANY"] == 500

    def test_cpu_core_wrapper_strip(self):
        values = {"cpu_core/INST_RETIRED.ANY/": 500}
        normalized = _normalize_event_values(values)
        assert normalized["INST_RETIRED.ANY"] == 500

    def test_original_preserved(self):
        values = {"topdown-fe-bound": 25.0}
        normalized = _normalize_event_values(values)
        assert "topdown-fe-bound" in normalized


class TestParseAuto:
    def test_detects_json(self):
        result = parse_auto(SAMPLE_JSON_OUTPUT)
        assert result.event_values["cycles"] == 1234567890.0

    def test_detects_text(self):
        result = parse_auto(SAMPLE_TEXT_OUTPUT)
        assert result.event_values["cycles"] == 1234567890.0

    def test_normalizes_names(self):
        topdown_json = '{"counter-value": "25.0", "unit": "", "event": "topdown-fe-bound", "pcnt-running": 100.00}\n'
        result = parse_auto(topdown_json)
        assert "PERF_METRICS.FRONTEND_BOUND" in result.event_values
