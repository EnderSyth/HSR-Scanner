import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "capture_pipeline_analysis",
    Path(__file__).resolve().parents[1] / "tools" / "analyze_capture_pipeline.py",
)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class PipelineAnalysisTest(unittest.TestCase):
    def test_immediate_confirmation_is_not_misattributed_to_next_uid(self):
        rows = [
            dict(event="nav", item_type="relic", uid="2", target_uid="3",
                 grab_start_s="1", grab_end_s="1.001", source="d"),
            dict(event="poll", item_type="relic", uid="2", capture_id="2", source="grab",
                 grab_start_s="1.002", grab_end_s="1.011", signature_end_s="1.012",
                 equals_previous="0", equals_candidate="1"),
            dict(event="poll", item_type="relic", uid="3", capture_id="3", source="grab",
                 grab_start_s="1.03", grab_end_s="1.039", signature_end_s="1.04",
                 equals_previous="0", equals_candidate="0"),
            dict(event="accept", item_type="relic", uid="3", capture_id="3",
                 signature_end_s="1.05", decision="stable"),
        ]
        result = "\n".join(analysis.analyze(rows))
        self.assertIn("post_input_confirmation_matched=1", result)
        self.assertIn("input end to immediate next grab start: n=1, avg=1.000", result)
        self.assertIn("input end to first target grab start: n=1, avg=29.000", result)
        self.assertIn("relic UID 1-200 d input to first change: n=1, avg=29.000", result)
        self.assertIn("relic UID 1-200 first change to acceptance: n=1, avg=10.000", result)

    def test_cache_does_not_inflate_grab_count_and_truncation_is_explicit(self):
        rows = [dict(event="poll", item_type="relic", uid="3", capture_id="3",
                     source="cache", grab_start_s="1", grab_end_s="1.009",
                     signature_end_s="1.01"),
                dict(event="dropped", decision="10")]
        result = "\n".join(analysis.analyze(rows))
        self.assertIn("cached_poll=1", result)
        self.assertIn("WARNING: truncated trace; dropped records=10", result)
        self.assertNotIn("grab + conversion:", result)


if __name__ == "__main__":
    unittest.main()
