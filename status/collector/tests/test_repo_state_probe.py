"""项目自报回流通道（flow_state.json）的守卫。

这条通道是 owner 定的 85% 自动核查覆盖率**唯一可能达标的路**
（三个系统在主机上一个程序都没有，主机侧核查永远探不到它们），
也是「双向」里各条线回流给本站的那一半。

正因为它把「项目自己写的东西」升格成了实测，每一条把关都必须有负控：
放松任何一条，这里都必须变红。
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import collect  # noqa: E402
import collect_github as cg  # noqa: E402


def _iso(hours_ago):
    return (datetime.now(collect.CN) - timedelta(hours=hours_ago)).isoformat()


class ParseTest(unittest.TestCase):
    def test_bad_json_does_not_raise(self):
        got, why = cg._parse_flow_state("{not json", "X")
        self.assertEqual(got, {})
        self.assertIn("解析失败", why)

    def test_missing_steps_section(self):
        got, why = cg._parse_flow_state(json.dumps({"schema": "x"}), "X")
        self.assertEqual(got, {})
        self.assertIn("steps", why)

    def test_unknown_state_word_is_dropped_not_guessed(self):
        """认不出的状态词必须**丢掉并留下原因**，不能猜成通过。"""
        raw = json.dumps({"steps": {"a.b": {"state": "probably_fine", "at": _iso(1)}}})
        got, why = cg._parse_flow_state(raw, "X")
        self.assertEqual(got, {})
        self.assertIsNotNone(why, "一条都没收下时必须给出原因，不能静默返回空")

    def test_illegal_key_dropped(self):
        raw = json.dumps({"steps": {"../../etc/passwd": {"state": "healthy", "at": _iso(1)}}})
        got, _ = cg._parse_flow_state(raw, "X")
        self.assertEqual(got, {})

    def test_note_is_truncated(self):
        raw = json.dumps({"steps": {"a.b": {"state": "healthy", "at": _iso(1),
                                            "note": "x" * 5000}}})
        got, _ = cg._parse_flow_state(raw, "X")
        self.assertLessEqual(len(got["a.b"]["note"]), 120)

    def test_offset_timestamp_is_not_shifted(self):
        """带 +08:00 的时间戳不能被当成 UTC —— 那会让刚跑完的步骤看起来旧 8 小时。"""
        raw = json.dumps({"steps": {"a.b": {"state": "healthy",
                                            "at": "2026-07-27T08:00:00+08:00"}}})
        got, _ = cg._parse_flow_state(raw, "X")
        self.assertEqual(got["a.b"]["at"].utcoffset(), timedelta(hours=8))


class ProbeTest(unittest.TestCase):
    def setUp(self):
        collect._LIVE.clear()

    def test_missing_record_is_unknown_not_healthy(self):
        st, ev = collect._pr_repo_state({"key": "a.b"})
        self.assertEqual(st, "unknown")

    def test_illegal_key_refused(self):
        st, ev = collect._pr_repo_state({"key": "a b; rm -rf /"})
        self.assertEqual(st, "unknown")
        self.assertIn("不合法", ev)

    def test_fresh_record_counts(self):
        collect._LIVE["a.b"] = {"state": "healthy", "note": "", "n": 12,
                                "at": datetime.now(collect.CN) - timedelta(hours=2)}
        st, ev = collect._pr_repo_state({"key": "a.b", "max_age_h": 26})
        self.assertEqual(st, "healthy")
        self.assertIn("12 条", ev)

    def test_stale_record_is_unknown_not_healthy_and_not_blocked(self):
        """★ 这条是整个通道的命门。

        过期记录既不能算通过（假绿），也不能算断了（假红）——
        只能是「不知道」。三个月前跑通过，不等于今天跑通了。
        """
        collect._LIVE["a.b"] = {"state": "healthy", "note": "", "n": None,
                                "at": datetime.now(collect.CN) - timedelta(hours=200)}
        st, ev = collect._pr_repo_state({"key": "a.b", "max_age_h": 26})
        self.assertEqual(st, "unknown", "过期的自报被当成了实测通过 —— 这就是假绿")
        self.assertIn("新鲜度", ev)

    def test_record_without_timestamp_is_unknown(self):
        collect._LIVE["a.b"] = {"state": "healthy", "note": "", "n": None, "at": None}
        st, _ = collect._pr_repo_state({"key": "a.b"})
        self.assertEqual(st, "unknown")

    def test_reported_bad_state_is_kept(self):
        """项目自己说断了，就是断了 —— 回流不是只用来报喜的。"""
        collect._LIVE["a.b"] = {"state": "blocked_by_input", "note": "缺现场数据",
                                "n": None, "at": datetime.now(collect.CN)}
        st, ev = collect._pr_repo_state({"key": "a.b"})
        self.assertEqual(st, "blocked_by_input")
        self.assertIn("缺现场数据", ev)


class IsolationTest(unittest.TestCase):
    """A 项目的自报绝不能算到 B 项目头上。

    ★ 这个测试第一版是**装饰性的**：它自己调 `_LIVE.clear()`，
      所以把 flow_state() 里那句 clear 删掉，测试照样全绿。
      负控跑出来才发现。现在改成走真正的 flow_state()，
      两个项目排在一起，只有 A 有自报 —— B 拿到 A 的绿就必须红。
    """

    def _run(self, projects):
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "private"), exist_ok=True)
        os.makedirs(os.path.join(d, "data"), exist_ok=True)
        with open(os.path.join(d, "private", "flow_docs.json"), "w") as f:
            json.dump({"projects": projects, "unregistered": [], "at": 0},
                      f, ensure_ascii=False)
        app, data = collect.APP_DIR, collect.DATA_DIR
        collect.APP_DIR, collect.DATA_DIR = d, os.path.join(d, "data")
        try:
            return collect.flow_state()
        finally:
            collect.APP_DIR, collect.DATA_DIR = app, data

    @staticmethod
    def _proj(name, live):
        cells = {"run": {"state": "healthy", "probe": "repo_state",
                         "args": {"key": "x.run"}, "evidence": "e"}}
        return {"project": name, "repo": "R", "stages": ["run"], "live": live,
                "baselines": [{"id": "B", "name": name, "priority": "P0",
                               "cells": cells}], "defects": []}

    def test_second_project_does_not_inherit_first_projects_report(self):
        out = self._run([
            self._proj("A", {"x.run": {"state": "healthy", "at": _iso(1), "n": 5}}),
            self._proj("B", {}),          # B 什么都没吐
        ])
        by = {p["project"]: p for p in out["projects"]}
        a = by["A"]["baselines"][0]["cells"]["run"]
        b = by["B"]["baselines"][0]["cells"]["run"]
        self.assertEqual(a.get("measured"), "healthy", "A 自己吐了，应当被采信")
        self.assertNotEqual(b.get("measured"), "healthy",
                            "B 一条都没吐，却拿到了 A 的绿 —— 自报表在项目之间串了")
        self.assertIn(b.get("measured"), (None, "unknown"))


class RegistryTest(unittest.TestCase):
    def test_probe_is_registered(self):
        self.assertIn("repo_state", collect.PROBES)

    def test_not_treated_as_weak(self):
        """带时间戳的自报不是弱证据 —— 弱证据是「日志还在写」那种。"""
        self.assertNotIn("repo_state", collect.WEAK_PROBES)


if __name__ == "__main__":
    unittest.main()
