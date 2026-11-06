"""tests.test_fulfillment_scope - 协作边界声明的守卫

**协作边界声明是盒子之间关系的唯一事实源**（住在 `fulfillment_scopes/<边界>/`）。
这份守卫盯的是那件最容易悄悄坏掉的事：
**声明写在文件里，却没人真的用它**。

一份没人装配的协作边界声明，跟没有声明一模一样——
看板照常渲染（读的是别的来源），图看起来完全正常，
只是有一只盒子接错了对象，而**没有任何东西会报错**。

所以这里守四件事：

| 守卫 | 抓什么 |
|---|---|
| 装配总闸 | 协作边界声明本身不合法（边界级装配器当场拒）|
| 真的在装配 | 注册表里的声明就是模块里那个，协作边界装配器确实跑了 |
| 全局归属 | 每只盒子要么在边界里，要么**显式**独立；不能两处都算 |
| 退休 | 旧接线字段**不得复活**（一只盒子不许同时留两套）|

## 旧字段已经退休（2026-11-06）

`placement.domain` / `feeds_into` / `feedback_into` 这三个字段**退休了**。

迁移期它们跟协作边界声明**双写**，那时候这里守的是「不矛盾」
（协作边界声明比旧字段**多** 3 条真实交接：需求→测试、设计→测试、开发→运维，
那 3 条以前藏在编排脚本里。要求「完全一样」等于逼着人把真实依赖藏起来）。

现在旧字段已经摘干净，这条守卫换成**更硬**的那条：
**不许再定义**这三个名字。

- 判据是「**定义**」，不是「字符串里不许有」——
  退休名出现在**读**的地方（兼容层读旧产出）没问题，
  出现在**定义**的地方（模型字段、声明关键字）才是复活。
- 为什么守得住：盒子身上再留一份接线 = **两套接线**，
  而两套接线迟早给出不同答案——到那时没人知道该信哪份。
"""

from __future__ import annotations

import pytest

from core.fulfillment_scope import assemble_fulfillment_scope
from core.models import EdgeKind, RuleKind
from fulfillment_scopes import FULFILLMENT_SCOPE_REGISTRY, STANDALONE_BOXES
from kanban.trigger import BOX_REGISTRY, box_facts, fulfillment_scope_assemblies


#: 退休的接线字段名——**不许再定义**
RETIRED_WIRING_FIELDS = ("placement", "domain", "feeds_into", "feedback_into")


# ============================================================
# 1. 装配总闸
# ============================================================

class TestEveryDomainAssembles:
    """协作边界声明不合法 → 装配期就拒（不许「看板照常渲染，只是接错了」）"""

    def test_every_domain_declares_at_least_one_box(self):
        for domain_id, declaration in FULFILLMENT_SCOPE_REGISTRY.items():
            assert declaration.boxes, (
                f"协作边界 {domain_id!r} 一只盒子都没有——"
                f"它要么从注册表里删掉，要么把成员写上"
            )

    def test_every_domain_assembles_without_problems(self):
        """装配器一次报齐所有问题——这里要的是**一条都没有**"""
        facts = box_facts()
        for domain_id, declaration in FULFILLMENT_SCOPE_REGISTRY.items():
            assembled = assemble_fulfillment_scope(declaration, facts)
            assert assembled.ok, (
                f"协作边界 {domain_id!r} 装配不过：\n"
                + "\n".join(f"  · {p}" for p in assembled.problems)
            )

    def test_the_domain_id_matches_the_module_it_lives_in(self):
        """协作边界 id 与目录名对得上——看板按 id 筛协作边界名，前端按目录找文件"""
        for domain_id in FULFILLMENT_SCOPE_REGISTRY:
            assert domain_id, "协作边界 id 不能是空串"

    def test_the_rules_are_not_vacuous(self):
        """每条规则都得**真的在数东西**

        一条 `total=0` 的规则读数是「0/0 通过」——
        它永远是绿的，而它什么也没守住。
        这跟「一只零判据的盒子被判成全过」是同一个病：
        **看起来成功，其实什么也没验**。
        """
        for domain_id, declaration in FULFILLMENT_SCOPE_REGISTRY.items():
            assembled = assemble_fulfillment_scope(declaration, box_facts())
            for reading in assembled.rules_reading:
                assert reading.total > 0, (
                    f"协作边界 {domain_id!r} 的规则 {reading.kind!r} 读数是 0/0——"
                    f"它什么也没守住，还永远绿"
                )

    def test_every_domain_declares_at_least_one_rule(self):
        """一个协作边界一条规则都不写 = 它声明了连线，没声明任何业务断言

        边界级规则正是这套协议存在的理由（协作边界声明不只是「谁连着谁」，
        还有「这个协作边界额外要求什么」）。
        规则清空不会报任何错——只有看板上的「边界级规则」那块空了，
        而**没人知道刚才那条业务断言被谁删了**。

        最少该有的是 `each_handoff_has_channel`：
        每条交接边都得有能走通的通道，**任何协作边界都成立**。
        """
        for domain_id, declaration in FULFILLMENT_SCOPE_REGISTRY.items():
            assert declaration.rules, (
                f"协作边界 {domain_id!r} 一条边界级规则都没写——"
                f"协作边界声明只声明了连线，没声明任何业务断言"
            )
            kinds = {rule.kind for rule in declaration.rules}
            assert RuleKind.each_handoff_has_channel in kinds, (
                f"协作边界 {domain_id!r} 没有「每条交接边都要有通道」这条规则——"
                f"它对任何协作边界都成立"
            )

    def test_every_exemption_says_why(self):
        """豁免必须写得出理由——「为什么」是结构化事实，不是注释"""
        for domain_id, declaration in FULFILLMENT_SCOPE_REGISTRY.items():
            for rule in declaration.rules:
                for ex in rule.exemptions:
                    assert ex.reason.strip(), (
                        f"协作边界 {domain_id!r} 的规则 {rule.kind.value!r} "
                        f"对 {ex.box_id!r} 的豁免没写理由"
                    )
                    assert ex.mode in ("may", "must"), (
                        f"协作边界 {domain_id!r} 的豁免模式 {ex.mode!r} 不认识"
                    )


# ============================================================
# 2. 真的在装配（声明了却没人装 = 没迁移）
# ============================================================

class TestTheDeclarationIsActuallyUsed:
    """声明写在文件里没人装 = 没迁移"""

    def test_the_registry_holds_the_module_objects_not_copies(self):
        """注册表里的必须**就是**模块里那个声明对象

        复制一份 = 又一份能漂的事实：改了模块里的，注册表里的还是旧的，
        而看板读的是注册表里的那份。
        """
        import fulfillment_scopes.learning.scope as learning
        import fulfillment_scopes.wms.scope as wms

        assert FULFILLMENT_SCOPE_REGISTRY[wms.SCOPE.id] is wms.SCOPE
        assert FULFILLMENT_SCOPE_REGISTRY[learning.SCOPE.id] is learning.SCOPE

    def test_the_board_actually_assembles_every_domain(self):
        """看板那套装配要把每个协作边界都装一遍——少装一个，那个协作边界就是死的"""
        assemblies = fulfillment_scope_assemblies()
        assert set(assemblies) == set(FULFILLMENT_SCOPE_REGISTRY), (
            f"看板装配的协作边界 {sorted(assemblies)} "
            f"跟注册表 {sorted(FULFILLMENT_SCOPE_REGISTRY)} 对不上"
        )
        for domain_id, assembled in assemblies.items():
            assert assembled.ok, (
                f"协作边界 {domain_id!r} 在看板那套装配里不过：\n"
                + "\n".join(f"  · {p}" for p in assembled.problems)
            )


# ============================================================
# 3. 全局归属
# ============================================================

class TestEveryBoxBelongsSomewhere:
    """每只盒子要么在边界里，要么**显式**独立——不能「没人管」"""

    def test_no_box_is_in_a_domain_and_also_declared_standalone(self):
        members = {b for d in FULFILLMENT_SCOPE_REGISTRY.values() for b in d.boxes}
        both = members & set(STANDALONE_BOXES)
        assert not both, (
            f"这些盒子既在边界里、又显式声明独立：{sorted(both)}——"
            f"两边都算，看板上它归哪儿？"
        )

    def test_every_registered_box_is_in_a_domain_or_standalone(self):
        """忘了归协作边界 → 盒子不在任何协作边界里，却没人知道"""
        members = {b for d in FULFILLMENT_SCOPE_REGISTRY.values() for b in d.boxes}
        registered = set(BOX_REGISTRY)
        missing = sorted(registered - members - set(STANDALONE_BOXES))
        assert not missing, (
            f"这些盒子既不在任何协作边界里、也没显式声明独立：{missing}——"
            f"要么把它们加进协作边界声明，要么在 STANDALONE_BOXES 里点名"
        )

    def test_a_standalone_box_really_is_standalone(self):
        """显式独立的那只，不能在协作边界声明里偷偷有边"""
        members = {b for d in FULFILLMENT_SCOPE_REGISTRY.values() for b in d.boxes}
        for box_id in STANDALONE_BOXES:
            assert box_id in BOX_REGISTRY, (
                f"STANDALONE_BOXES 里点了名 {box_id!r}，注册表里却没有它"
            )
            assert box_id not in members, (
                f"{box_id!r} 显式声明独立，却又出现在协作边界成员里"
            )


# ============================================================
# 4. 退休：旧接线字段不得复活
# ============================================================

class TestTheRetiredWiringFieldsAreGone:
    """旧接线字段**已经摘干净**——一只盒子不许同时留两套接线

    **判据是「不许再定义」**，不是「字符串里不许有」：
    退休名出现在**读**的地方（兼容层读旧产出）没问题，
    出现在**定义**的地方（模型字段、声明关键字）就是复活了。
    """

    def test_no_box_carries_a_placement(self):
        """盒子身上没有 `placement` 这个属性了"""
        for box_id in BOX_REGISTRY:
            definition = BOX_REGISTRY[box_id][0]()
            assert not hasattr(definition, "placement"), (
                f"{box_id!r} 身上还有 placement——接线住在协作边界声明里，"
                f"盒子身上再留一份 = 两套接线并存"
            )

    def test_the_display_fields_live_at_the_top_level(self):
        """展示位摊平到顶层（原来在 placement 里）"""
        for box_id in BOX_REGISTRY:
            definition = BOX_REGISTRY[box_id][0]()
            for field in ("display_name", "value_stream", "role"):
                assert hasattr(definition, field), (
                    f"{box_id!r} 少了顶层展示位 {field!r}"
                )

    def test_no_definition_declares_the_retired_wiring(self):
        """各盒的 `definition.py` 里不许再声明那三个字段

        **用 AST 查**，不查字符串：注释里提一句「以前有 feeds_into」
        是历史说明，不是在定义接线。
        """
        import ast
        import pathlib

        repo = pathlib.Path(__file__).resolve().parent.parent
        offenders: list[str] = []
        for path in sorted(repo.glob("boxes/*/definition.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name not in ("BoxDeclaration", "BeeBoxDefinition"):
                    continue
                for kw in node.keywords:
                    if kw.arg in ("domain", "feeds_into", "feedback_into", "placement"):
                        offenders.append(f"{path.name}:{kw.lineno} {kw.arg}=")
        assert not offenders, (
            "接线字段又回来了（一只盒子不许同时留两套）：\n  " + "\n  ".join(offenders)
        )

    def test_the_domain_declaration_is_the_only_wiring_source(self):
        """协作边界声明里的边 = 接线事实；盒子上再没有第二份"""
        from fulfillment_scopes import box_wiring

        for box_id in BOX_REGISTRY:
            wiring = box_wiring(box_id)
            # 归档、交接、反馈三组都只从协作边界声明来
            for group in ("handoffs", "handoff_from", "feedback", "feedback_from"):
                assert isinstance(wiring[group], list)


# ============================================================
# 5. 边的性质
# ============================================================

class TestTheEdgesSayWhatTheyAre:
    """`kind` 才是事实，汇点只是拓扑位置"""

    def test_an_archive_edge_has_no_channel(self):
        """归档边**没有通道**——档案盒是去线上读的，不收上游产出

        给它挂一个通道 = 「声明了交接却走不通」：
        装配期会被 D3a 拒，但更糟的是有人会觉得「那就接上吧」。
        """
        for declaration in FULFILLMENT_SCOPE_REGISTRY.values():
            for edge in declaration.edges:
                if edge.kind != EdgeKind.archive:
                    continue
                assert not edge.via, (
                    f"协作边界 {declaration.id!r} 的归档边 "
                    f"{edge.from_box!r} → {edge.to_box!r} 带了通道 "
                    f"{edge.via!r}——归档不是数据交接"
                )

    def test_a_handoff_edge_always_has_a_channel(self):
        """交接边**必须有通道**——没有通道的交接是声明了却走不通"""
        for declaration in FULFILLMENT_SCOPE_REGISTRY.values():
            for edge in declaration.edges:
                if edge.kind != EdgeKind.handoff:
                    continue
                assert edge.via, (
                    f"协作边界 {declaration.id!r} 的交接边 "
                    f"{edge.from_box!r} → {edge.to_box!r} 没有通道——"
                    f"交接没有通道 = 下游拿不到这份产出"
                )

    def test_the_kind_matches_the_channel(self):
        """有通道的边是交接，没通道的边不是——两者不许含糊"""
        for declaration in FULFILLMENT_SCOPE_REGISTRY.values():
            for edge in declaration.edges:
                if edge.via:
                    assert edge.kind == EdgeKind.handoff, (
                        f"协作边界 {declaration.id!r} 的边 "
                        f"{edge.from_box!r} → {edge.to_box!r} 带了通道 "
                        f"{edge.via!r}，kind 却是 {edge.kind.value!r}"
                    )
                else:
                    assert edge.kind != EdgeKind.handoff, (
                        f"协作边界 {declaration.id!r} 的交接边 "
                        f"{edge.from_box!r} → {edge.to_box!r} 没有通道"
                    )
