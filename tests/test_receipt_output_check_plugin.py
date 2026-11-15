"""tests.test_receipt_output_check_plugin - 票据盒自己的业务插件

**M5 的目标**：证明设计盒那种「业务侧贡献插件」的模式**能复用到另一只盒子**。

这只盒子的插件守的是：**产出自报的算术标志位，要跟产出里的金额对得上**。
判据读的是**盒子自己算的标志位**——盒子说「算过了」就过了，
没人回头核对它算的那份数跟最终带出去的那份数是不是同一份。

**为什么单元级验算术**：票据盒走 LLM 读照片，产出**每次都不一样**
（同一张票两次抽出来的字段可能不同）→ 端到端的算术断言不可靠。
所以：
- **算术对不对** → 单元级（构造确定的产出 ✓）
- **插件有没有真的被调用** → 端到端（数调用次数 ✓）
"""

from __future__ import annotations

import pytest

from boxes.receipt.plugins import (
    OUTPUT_CHECK_IMPL,
    OUTPUT_CHECK_PLUGIN,
    check_receipt_output,
)
from core.plugins import PLUGIN_TYPES


#: 一张**算得开**的票（构造出来的，确定的）
GOOD_RESULT = {
    "items": [
        {"line_no": 1, "name": "牛奶", "qty": 2, "unit_price": 5.0, "amount": 10.0},
        {"line_no": 2, "name": "面包", "qty": 1, "unit_price": 8.5, "amount": 8.5},
    ],
    "subtotal": 18.5,
    "tax": 0.0,
    "discount": 0.0,
    "included_discount": None,
    "total": 18.5,
    "items_count": 2,
    "line_arithmetic": True,
    "sum_arithmetic": True,
    "total_arithmetic": True,
}


def _view(result: dict) -> dict:
    return {"result": result}


def _params(**overrides) -> dict:
    base = {
        "check_lines": True,
        "check_sum": True,
        "check_total": True,
        "tolerance": 0.01,
    }
    base.update(overrides)
    return base


# ============================================================
# 一、插件的登记与绑定
# ============================================================


class TestThePluginIsBound:
    def test_the_type_is_registered_by_the_business_side(self):
        """插件类型登记在 `boxes/*` 里，不在 core 里"""
        ptype = PLUGIN_TYPES[OUTPUT_CHECK_PLUGIN]
        assert ptype.allowed_hosts == ("box",)
        assert ptype.implementation_key == OUTPUT_CHECK_IMPL
        assert ptype.params_model is not None

    def test_the_receipt_box_mounts_it(self):
        from boxes.receipt import get_definition

        ids = [p.id for p in get_definition().plugins]
        assert OUTPUT_CHECK_PLUGIN in ids


# ============================================================
# 二、算术对不对（单元级，确定的）
# ============================================================


class TestTheArithmetic:
    def test_a_consistent_receipt_passes(self):
        """标志位说「算过了」，重算也对得上 → 通过"""
        assert check_receipt_output(_view(GOOD_RESULT), _params()) is None

    def test_a_subtotal_that_does_not_add_up_is_caught(self):
        """小计被挪了 1 块 → 「Σ行金额 = 小计」不成立"""
        result = dict(GOOD_RESULT, subtotal=19.5)
        out = check_receipt_output(_view(result), _params())
        assert out is not None
        assert any("Σ行金额" in p for p in out["problems"]), out

    def test_a_total_that_does_not_add_up_is_caught(self):
        """实付被挪了 1 块 → 「小计 + 税 − 优惠 = 实付」不成立"""
        result = dict(GOOD_RESULT, total=19.5)
        out = check_receipt_output(_view(result), _params())
        assert out is not None
        assert any("实付" in p for p in out["problems"]), out

    def test_a_discount_already_included_in_the_total_is_not_double_counted(self):
        """**优惠已经含在合计里** → 不能再减一次

        实测六贤记那张：合计 39.70(2.60)、折让 2.60、付款 39.70。
        再减一次就成 37.10，跟实付对不上。
        """
        result = {
            **GOOD_RESULT,
            # 三行加起来正好 39.70（15.90 + 8.80 + 15.00）
            "items": [
                {"line_no": 1, "name": "甲", "qty": 1, "unit_price": 15.9, "amount": 15.9},
                {"line_no": 2, "name": "奶皮鞭子", "qty": 2, "unit_price": 4.4, "amount": 8.8},
                {"line_no": 3, "name": "丙", "qty": 1, "unit_price": 15.0, "amount": 15.0},
            ],
            "subtotal": 39.7,
            "tax": 0.0,
            "discount": 2.6,
            "included_discount": 2.6,
            "total": 39.7,
            "items_count": 3,
        }
        assert check_receipt_output(_view(result), _params()) is None

    def test_a_flag_that_says_failed_is_not_re_checked(self):
        """**说「没过」的，判据会红** —— 插件不重复判据的活

        插件只核对「说『过了』的那些标志位是不是真的」。
        """
        result = dict(GOOD_RESULT, sum_arithmetic=False, subtotal=99.0)
        # 小计一改，总额那笔也跟着对不上 → 把那笔也关掉，
        # 只留「Σ行金额」这一笔不查
        assert check_receipt_output(
            _view(result), _params(check_total=False)
        ) is None

    def test_a_flag_that_does_not_apply_is_not_re_checked(self):
        """「用不上」（n/a）的，这张票本来就没那一栏"""
        result = dict(GOOD_RESULT, line_arithmetic="n/a")
        assert check_receipt_output(_view(result), _params()) is None

    def test_the_checks_can_be_turned_off_one_by_one(self):
        """盒子自己说了算要核哪几笔（参数是业务声明）"""
        result = dict(GOOD_RESULT, subtotal=19.5)
        assert check_receipt_output(
            _view(result), _params(check_sum=False, check_total=False)
        ) is None


# ============================================================
# 三、规则只有一份
# ============================================================


class TestTheRulesLiveInOnePlace:
    def test_the_runner_and_the_plugin_use_the_same_arithmetic(self):
        """**runner 算的标志位 == 插件重算的结论**（同一份规则）

        各写各的必然漂：一边改了公式，另一边还在按旧的核对，
        而读者看到的是「核验过了」。
        """
        from boxes.receipt.arithmetic import sum_matches, total_matches

        assert sum_matches(GOOD_RESULT["items"], GOOD_RESULT["subtotal"])
        assert total_matches(
            GOOD_RESULT["subtotal"],
            GOOD_RESULT["tax"],
            GOOD_RESULT["discount"],
            GOOD_RESULT["included_discount"],
            GOOD_RESULT["total"],
        )

    def test_the_arithmetic_module_lives_in_the_box(self):
        """算术规则住在 `boxes/receipt/` 里——core 不该知道小票怎么算"""
        import boxes.receipt.arithmetic as m

        assert m.__name__ == "boxes.receipt.arithmetic"
        assert hasattr(m, "discount_multiplier")
        assert hasattr(m, "line_amount_ok")


# ============================================================
# 四、插件有没有真的被调用（端到端）
# ============================================================


class TestThePluginIsReallyCalled:
    def test_the_receipt_run_calls_the_verify_plugin(self, monkeypatch, stub_llm):
        """**到达约定扩展点时确实执行**——不许「声明了却一次都没跑过」"""
        import os
        import tempfile

        from kanban import trigger as trig
        from kanban.trigger import _EXAMPLE_PAYLOADS, trigger_task
        from runtime.store import TaskRunStore

        box_id = "receipt_capture_box"
        ctx = trig.PLUGIN_CONTEXT_REGISTRY[box_id]
        impls = dict(ctx.implementations)
        calls: list[int] = []

        real = impls["receipt.output_check"]

        def _counting(view, params):
            calls.append(1)
            return real(view, params)

        impls["receipt.output_check"] = _counting
        monkeypatch.setitem(
            trig.PLUGIN_CONTEXT_REGISTRY,
            box_id,
            type(ctx)(
                types=dict(ctx.types),
                extension_points=dict(ctx.extension_points),
                implementations=impls,
            ),
        )

        store = TaskRunStore(
            os.path.join(tempfile.mkdtemp(prefix="m5_"), "runs.jsonl")
        )
        trigger_task(
            box_id,
            "capture_receipt",
            _EXAMPLE_PAYLOADS[(box_id, "capture_receipt")],
            store,
        )
        assert calls, "核验钩子一次都没被调用——它挂在那儿是摆设"


# ============================================================
# 三、第二轮 CR 补的两条（2026-11-15）
# ============================================================


class TestSelfReportMustBeVerifiable:
    """**自报「过了」，就得真的验过**

    一行单价都没有时，这条判据**根本没法验**——
    说它「过了」就是**假装验过了**。
    「假装验过」比「算错」更难查：读者看到的是「全过」。
    """

    def test_a_self_reported_pass_that_cannot_be_verified_is_caught(self):
        result = {
            "items": [{"qty": 1, "unit_price": None, "amount": 1}],
            "line_arithmetic": True,
        }
        out = check_receipt_output(_view(result), _params())
        assert out is not None, "自报「过了」但根本没法验——被放行了"
        assert any("没法验" in p for p in out["problems"]), out

    def test_no_rows_at_all_with_a_self_reported_pass_is_caught(self):
        result = {"items": [], "line_arithmetic": True}
        out = check_receipt_output(_view(result), _params())
        assert out is not None, "一张没有行的票说「每行金额都对得上」——被放行了"


class TestTheToleranceAppliesEverywhere:
    """**容差要管到每一笔账**——不然参数就是在骗人

    以前容差只喂给 Σ行金额与实付，行金额走的是写死的 0.01：
    配 `tolerance=0.05` 而行金额差 0.04 时，行金额仍被拒——
    「参数说 0.05、实际按 0.01 办」= 参数是摆设。
    """

    def test_the_tolerance_reaches_the_line_amount_check(self):
        result = {
            "items": [{"qty": 1, "unit_price": 10.0, "amount": 10.04}],
            "line_arithmetic": True,
        }
        assert check_receipt_output(_view(result), _params(tolerance=0.05)) is None
        assert check_receipt_output(_view(result), _params(tolerance=0.01)) is not None

    def test_the_tolerance_reaches_the_subtotal_check(self):
        result = dict(
            GOOD_RESULT,
            subtotal=18.54,
        )
        assert check_receipt_output(_view(result), _params(tolerance=0.05)) is None
        assert check_receipt_output(_view(result), _params(tolerance=0.01)) is not None

    def test_the_tolerance_reaches_the_total_check(self):
        result = dict(GOOD_RESULT, total=18.54)
        assert check_receipt_output(_view(result), _params(tolerance=0.05)) is None
        assert check_receipt_output(_view(result), _params(tolerance=0.01)) is not None
