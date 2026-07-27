"""回写模板 report_flow_state.py 的守卫。

这份模板要被拷进 9 个仓、跑在各自的 CI 里。它写坏一次，
status 就会拿到一条**看起来正常的错记录** —— 那比不写更危险。

所以模板本身也必须有测试，而且要和 status 侧的采信规则严丝合缝：
状态词表一致、键名规则一致、时间戳带偏移。
两边任何一边偷偷放宽，这里都要红。
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "docs", "templates"))
import collect_github as G  # noqa: E402
import report_flow_state as R  # noqa: E402


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _read(self, project_dir="proj"):
        p = os.path.join(self.d, project_dir, "docs", "governance", "flow_state.json") \
            if project_dir != "." else \
            os.path.join(self.d, "docs", "governance", "flow_state.json")
        return json.load(open(p, encoding="utf-8"))

    def test_writes_a_record_with_offset_timestamp(self):
        R.report(self.d, "proj", "BL-X.stage", "healthy", n=7, note="ok")
        rec = self._read()["steps"]["BL-X.stage"]
        self.assertEqual(rec["state"], "healthy")
        self.assertEqual(rec["n"], 7)
        self.assertIn("+", rec["at"], "时间戳必须带时区偏移，否则 status 侧会算错新鲜度")

    def test_merges_instead_of_clobbering(self):
        """写第二步不能把第一步冲掉 —— 冲掉了那一步就变成「没上报」。"""
        R.report(self.d, "proj", "BL-X.a", "healthy")
        R.report(self.d, "proj", "BL-X.b", "blocked")
        steps = self._read()["steps"]
        self.assertEqual(set(steps), {"BL-X.a", "BL-X.b"})

    def test_whole_repo_form(self):
        R.report(self.d, ".", "BL-X.a", "healthy")
        self.assertIn("BL-X.a", self._read(".")["steps"])

    def test_unreadable_file_is_preserved_not_silently_dropped(self):
        p = os.path.join(self.d, "proj", "docs", "governance")
        os.makedirs(p, exist_ok=True)
        open(os.path.join(p, "flow_state.json"), "w").write("{ broken")
        R.report(self.d, "proj", "BL-X.a", "healthy")
        self.assertTrue(os.path.exists(os.path.join(p, "flow_state.json.unreadable")),
                        "读不动的旧文件被静默删了 —— 出问题时查不到证据")

    def test_note_truncated_to_contract_limit(self):
        R.report(self.d, "proj", "BL-X.a", "healthy", note="x" * 900)
        self.assertLessEqual(len(self._read()["steps"]["BL-X.a"]["note"]), 120)

    def test_bad_state_refused(self):
        with self.assertRaises(SystemExit):
            R.report(self.d, "proj", "BL-X.a", "probably_fine")

    def test_bad_key_refused(self):
        with self.assertRaises(SystemExit):
            R.report(self.d, "proj", "../../etc/passwd", "healthy")


class ContractParityTest(unittest.TestCase):
    """★ 写的一侧和读的一侧必须是同一套规矩。

    两边各自演化是这类通道最典型的烂法：写端加了个新状态词，
    读端不认，那条记录被静默丢掉 —— 而写端以为自己上报成功了。
    """

    def test_state_vocabulary_matches_status_side(self):
        self.assertEqual(set(R.STATES), set(G._LIVE_STATES),
                         "模板与 status 采信的状态词表不一致，会出现「上报了但没人认」")

    def test_key_rule_matches_status_side(self):
        for ok in ("BL-X.stage", "a.b:c-d", "A" * 80):
            self.assertTrue(R.KEY_RE.match(ok))
            self.assertTrue(G._LIVE_KEY.match(ok))
        for bad in ("../x", "a b", "A" * 81, ""):
            self.assertFalse(bool(R.KEY_RE.match(bad)))
            self.assertFalse(bool(G._LIVE_KEY.match(bad)))

    def test_written_record_is_accepted_end_to_end(self):
        """端到端：模板写出来的东西，status 侧必须原样收下。"""
        d = tempfile.mkdtemp()
        R.report(d, "proj", "BL-X.a", "degraded", n=3, note="打折")
        raw = open(os.path.join(d, "proj", "docs", "governance", "flow_state.json"),
                   encoding="utf-8").read()
        got, why = G._parse_flow_state(raw, "proj")
        self.assertIsNone(why)
        self.assertEqual(got["BL-X.a"]["state"], "degraded")
        self.assertEqual(got["BL-X.a"]["n"], 3)
        self.assertIsNotNone(got["BL-X.a"]["at"])

    def test_fresh_record_is_counted_stale_one_is_not(self):
        """再往前一步：收下之后，新鲜的算实测、过期的降级成不确定。"""
        import collect as C
        d = tempfile.mkdtemp()
        R.report(d, "proj", "BL-X.a", "healthy",
                 now=datetime.now(timezone(timedelta(hours=8))))
        raw = open(os.path.join(d, "proj", "docs", "governance", "flow_state.json"),
                   encoding="utf-8").read()
        got, _ = G._parse_flow_state(raw, "proj")
        C._LIVE.clear()
        C._LIVE["BL-X.a"] = {"state": got["BL-X.a"]["state"], "note": "", "n": None,
                             "at": got["BL-X.a"]["at"]}
        self.assertEqual(C._pr_repo_state({"key": "BL-X.a"})[0], "healthy")
        C._LIVE["BL-X.a"]["at"] = got["BL-X.a"]["at"] - timedelta(hours=300)
        self.assertEqual(C._pr_repo_state({"key": "BL-X.a"})[0], "unknown",
                         "过期记录仍被当成实测通过 —— 假绿")


if __name__ == "__main__":
    unittest.main()
