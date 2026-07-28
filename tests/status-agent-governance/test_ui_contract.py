from pathlib import Path
import unittest
class UIContractTest(unittest.TestCase):
    def test_chinese_local_fail_closed_ui(self):
        root=Path(__file__).parents[2]/'status/web'
        html=(root/'agent-governance.html').read_text(encoding='utf-8')
        js=(root/'agent-governance.js').read_text(encoding='utf-8')
        css=(root/'agent-governance.css').read_text(encoding='utf-8')
        self.assertIn('lang="zh-CN"',html)
        self.assertIn('证据不足',js)
        self.assertIn('escapeHtml',js)
        self.assertIn('focus-visible',css)
        self.assertIn('min-height: 44px',css)
        combined=html+js+css
        for external in ('cdn.jsdelivr.net','unpkg.com','fonts.googleapis.com'):
            self.assertNotIn(external,combined)
if __name__ == '__main__': unittest.main()
