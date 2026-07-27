#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端渲染的**不变量**守卫。

这些是**配置级**断言,不是行为级断言 —— 它们检查源码里的那一行写法,
不能证明页面真的画出来了(那需要浏览器)。之所以仍然值得钉,是因为
这里每一条的失败模式都是「改一行配置就整站静默失效、而且失效得**看不出来**」:
页面结构、KPI、表格全都正常,只有图是空的,不报错、不留痕。
"""
import os
import re
import unittest

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "web", "index.html")


class ChartMustPaintSynchronously(unittest.TestCase):
    """★ 实测(2026-07-27):开着动画时全站每一张 Chart 都是**纯空白**。

    canvas 有实例、尺寸正常、数据正确,但 173,280 个像素里 alpha 全为 0。
    根因:Chart.js 的动画只在 requestAnimationFrame 回调里推进,而实测该渲染环境
    600ms 内**一帧 rAF 都没触发**,于是永远停在「柱高为 0」的第一帧。
    同一 canvas 换成 animation:false 立刻画出 54,557 个像素。

    这是本项目第四次栽在「rAF 不触发」上(隐藏页数值滚动冻结、后台标签页星图位图
    停在 300×150、切页首帧、这次)。**凡是画到屏幕上的东西都不能只依赖 rAF。**
    """

    def setUp(self):
        self.src = open(WEB, encoding="utf-8").read()

    def test_chart_animation_is_disabled_globally(self):
        self.assertRegex(
            self.src, r"C\.defaults\.animation\s*=\s*false",
            "Chart 全局动画必须关闭 —— 开着动画时图表只在 rAF 里画,"
            "拿不到帧就是整站空白且不报错")

    def test_no_animated_defaults_object(self):
        """给 defaults.animation 赋一个 {duration:...} 对象 = 把动画打开,同样会整站空白。"""
        bad = re.search(r"C\.defaults\.animation\s*=\s*\{", self.src)
        self.assertIsNone(
            bad, "不得给 C.defaults.animation 赋对象(那就是开动画);"
                 "要调时长请改成 per-chart 配置,并先确认目标环境 rAF 可用")

    def test_no_per_chart_animation_duration(self):
        """单图再开动画也会踩同一个坑,一并挡住。"""
        hits = re.findall(r"animation\s*:\s*\{[^}]*duration", self.src)
        self.assertEqual(hits, [], "单图不得开启动画时长:%r" % (hits,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
