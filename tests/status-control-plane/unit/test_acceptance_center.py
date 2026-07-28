from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


class AcceptanceCenterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[3]
        cls.index = cls.repo / "status/web/index.html"
        cls.page = cls.repo / "status/web/acceptance.html"
        cls.js = cls.repo / "status/web/assets/acceptance/acceptance.js"
        cls.css = cls.repo / "status/web/assets/acceptance/acceptance.css"
        cls.data = cls.repo / "status/data/acceptance/chatgpt_latest.json"

    def test_files_exist(self):
        for path in (self.index, self.page, self.js, self.css, self.data):
            self.assertTrue(path.is_file(), path)

    def test_visible_entry(self):
        text = self.index.read_text(encoding="utf-8")
        self.assertIn("/acceptance.html", text)
        self.assertIn("验收 · FAIL", text)

    def test_payload_contract(self):
        data = json.loads(self.data.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertTrue(data["public_safe"])
        self.assertEqual(data["reviewer"]["display_name"], "ChatGPT")
        self.assertEqual(data["reviewer"]["model"], "GPT-5.6 Pro")
        self.assertEqual(data["subject"]["taskpack_version"], "v0.0.0.1")
        self.assertEqual(
            data["subject"]["taskpack_sha256"],
            "996664cc4cbe3d0f3d189d9d5ff19633f86669a6618461076fa42a4b7af4e5dc",
        )
        self.assertEqual(data["verdict"]["status"], "FAIL")
        self.assertFalse(data["verdict"]["product_complete"])
        self.assertFalse(data["page_runtime"]["agent_dependency"])
        self.assertEqual(data["page_runtime"]["llm_calls"], 0)
        self.assertEqual(data["page_runtime"]["token_consumption"], 0)
        self.assertEqual(len(data["domain_verdicts"]), 6)
        self.assertEqual(len(data["blocking_findings"]), 4)

    def test_no_direct_dynamic_inner_html(self):
        js = self.js.read_text(encoding="utf-8")
        self.assertNotIn(".innerHTML", js)
        self.assertIn("textContent", js)

    def test_no_model_api_domains(self):
        combined = "\n".join(
            p.read_text(encoding="utf-8")
            for p in (self.page, self.js, self.css, self.data)
        ).lower()
        forbidden = (
            "api.openai.com",
            "api.anthropic.com",
            "generativelanguage.googleapis.com",
        )
        for value in forbidden:
            self.assertNotIn(value, combined)

    def test_no_secret_like_values(self):
        text = self.data.read_text(encoding="utf-8")
        patterns = (
            r"gh[pous]_[A-Za-z0-9]{20,}",
            r"github_pat_[A-Za-z0-9_]+",
            r"sk-[A-Za-z0-9_-]{16,}",
            r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}",
        )
        for pattern in patterns:
            self.assertIsNone(re.search(pattern, text), pattern)


if __name__ == "__main__":
    unittest.main()
