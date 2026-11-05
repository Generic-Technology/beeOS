"""tests.test_domain_declaration - 域声明的守卫

**域声明是盒子之间关系的唯一事实源**（住在 `domains/<域>/`）。
这份守卫盯的是那件最容易悄悄坏掉的事：
**声明写在文件里，却没人真的用它**。

一份没人装配的域声明，跟没有声明一模一样——
看板照常渲染（读的是别的来源），图看起来完全正常，
只是有一只盒子接错了对象，而**没有任何东西会报错**。

所以这里守四件事：

| 守卫 | 抓什么 |
|---|---|
| 装配总闸 | 域声明本身不合法（域级装配器当场拒）|
| 真的在装配 | 注册表里的声明就是模块里那个，域装配器确实跑了 |
| 全局归属 | 每只盒子要么在域里，要么**显式**独立；不能两处都算 |
| 迁移期双写 | 盒子上的旧接线字段跟域声明**不得矛盾** |

## 为什么「旧字段」还要留

`placement.domain` / `feeds_into` / `feedback_into` 正在退休，
但盒子还没迁完新协议。**两份事实并存 = 必然漂**，
而漂了不报错——所以这里不把它们删掉，而是**钉住它们跟域声明不矛盾**：

- 旧 `feeds_into` 的每一条，域声明里都得有对应的边
- 旧 `feedback_into` 的每一条，域声明里都得是 `feedback` 边
- 旧 `placement.domain` 说它在哪个域，域声明就得把它算进那个域

**不是「两边一样」**——域声明比旧字段**多**：
真实的数据依赖图比 `feeds_into` 多 3 条交接
（需求→测试、设计→测试、开发→运维），那 3 条以前藏在脚本里。
要求「完全一样」等于逼着人把真实依赖藏起来。
"""

from __future__ import annotations

import pytest

from core.domain import assemble_domain
from core.models import EdgeKind, RuleKind
from domains import DOMAIN_REGISTRY, STANDALONE_BOXES
from kanban.trigger import BOX_REGISTRY, box_facts, domain_assemblies


def _legacy_placement(box_id: str):
    """盒子自己的旧接线声明（迁移期还在）"""
    return BOX_REGISTRY[box_id][0]().placement


# ============================================================
# 1. 装配总闸
# ============================================================

class TestEveryDomainAssembles:
    """域声明不合法 → 装配期就拒（不许「看板照常渲染，只是接错了」）"""

    def test_every_domain_declares_at_least_one_box(self):
        for domain_id, declaration in DOMAIN_REGISTRY.items():
            assert declaration.boxes, (
                f"域 {domain_id!r} 一只盒子都没有——"
                f"它要么从注册表里删掉，要么把成员写上"
            )

    def test_every_domain_assembles_without_problems(self):
        """装配器一次报齐所有问题——这里要的是**一条都没有**"""
        facts = box_facts()
        for domain_id, declaration in DOMAIN_REGISTRY.items():
            assembled = assemble_domain(declaration, facts)
            assert assembled.ok, (
                f"域 {domain_id!r} 装配不过：\n"
                + "\n".join(f"  · {p}" for p in assembled.problems)
            )

    def test_the_domain_id_matches_the_module_it_lives_in(self):
        """域 id 与目录名对得上——看板按 id 筛域名，前端按目录找文件"""
        for domain_id in DOMAIN_REGISTRY:
            assert domain_id, "域 id 不能是空串"

    def test_the_rules_are_not_vacuous(self):
        """每条规则都得**真的在数东西**

        一条 `total=0` 的规则读数是「0/0 通过」——
        它永远是绿的，而它什么也没守住。
        这跟「一只零判据的盒子被判成全过」是同一个病：
        **看起来成功，其实什么也没验**。
        """
        for domain_id, declaration in DOMAIN_REGISTRY.items():
            assembled = assemble_domain(declaration, box_facts())
            for reading in assembled.rules_reading:
                assert reading.total > 0, (
                    f"域 {domain_id!r} 的规则 {reading.kind!r} 读数是 0/0——"
                    f"它什么也没守住，还永远绿"
                )

    def test_every_domain_declares_at_least_one_rule(self):
        """一个域一条规则都不写 = 它声明了连线，没声明任何业务断言

        域级规则正是这套协议存在的理由（域声明不只是「谁连着谁」，
        还有「这个域额外要求什么」）。
        规则清空不会报任何错——只有看板上的「域级规则」那块空了，
        而**没人知道刚才那条业务断言被谁删了**。

        最少该有的是 `each_handoff_has_channel`：
        每条交接边都得有能走通的通道，**任何域都成立**。
        """
        for domain_id, declaration in DOMAIN_REGISTRY.items():
            assert declaration.rules, (
                f"域 {domain_id!r} 一条域级规则都没写——"
                f"域声明只声明了连线，没声明任何业务断言"
            )
            kinds = {rule.kind for rule in declaration.rules}
            assert RuleKind.each_handoff_has_channel in kinds, (
                f"域 {domain_id!r} 没有「每条交接边都要有通道」这条规则——"
                f"它对任何域都成立"
            )

    def test_every_exemption_says_why(self):
        """豁免必须写得出理由——「为什么」是结构化事实，不是注释"""
        for domain_id, declaration in DOMAIN_REGISTRY.items():
            for rule in declaration.rules:
                for ex in rule.exemptions:
                    assert ex.reason.strip(), (
                        f"域 {domain_id!r} 的规则 {rule.kind.value!r} "
                        f"对 {ex.box_id!r} 的豁免没写理由"
                    )
                    assert ex.mode in ("may", "must"), (
                        f"域 {domain_id!r} 的豁免模式 {ex.mode!r} 不认识"
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
        import domains.learning.domain as learning
        import domains.wms.domain as wms

        assert DOMAIN_REGISTRY[wms.DOMAIN.id] is wms.DOMAIN
        assert DOMAIN_REGISTRY[learning.DOMAIN.id] is learning.DOMAIN

    def test_the_board_actually_assembles_every_domain(self):
        """看板那套装配要把每个域都装一遍——少装一个，那个域就是死的"""
        assemblies = domain_assemblies()
        assert set(assemblies) == set(DOMAIN_REGISTRY), (
            f"看板装配的域 {sorted(assemblies)} "
            f"跟注册表 {sorted(DOMAIN_REGISTRY)} 对不上"
        )
        for domain_id, assembled in assemblies.items():
            assert assembled.ok, (
                f"域 {domain_id!r} 在看板那套装配里不过：\n"
                + "\n".join(f"  · {p}" for p in assembled.problems)
            )


# ============================================================
# 3. 全局归属
# ============================================================

class TestEveryBoxBelongsSomewhere:
    """每只盒子要么在域里，要么**显式**独立——不能「没人管」"""

    def test_no_box_is_in_a_domain_and_also_declared_standalone(self):
        members = {b for d in DOMAIN_REGISTRY.values() for b in d.boxes}
        both = members & set(STANDALONE_BOXES)
        assert not both, (
            f"这些盒子既在域里、又显式声明独立：{sorted(both)}——"
            f"两边都算，看板上它归哪儿？"
        )

    def test_every_registered_box_is_in_a_domain_or_standalone(self):
        """忘了归域 → 盒子不在任何域里，却没人知道"""
        members = {b for d in DOMAIN_REGISTRY.values() for b in d.boxes}
        registered = set(BOX_REGISTRY)
        missing = sorted(registered - members - set(STANDALONE_BOXES))
        assert not missing, (
            f"这些盒子既不在任何域里、也没显式声明独立：{missing}——"
            f"要么把它们加进域声明，要么在 STANDALONE_BOXES 里点名"
        )

    def test_a_standalone_box_really_is_standalone(self):
        """显式独立的那只，不能在域声明里偷偷有边"""
        members = {b for d in DOMAIN_REGISTRY.values() for b in d.boxes}
        for box_id in STANDALONE_BOXES:
            assert box_id in BOX_REGISTRY, (
                f"STANDALONE_BOXES 里点了名 {box_id!r}，注册表里却没有它"
            )
            assert box_id not in members, (
                f"{box_id!r} 显式声明独立，却又出现在域成员里"
            )


# ============================================================
# 4. 迁移期双写：旧字段跟域声明不得矛盾
# ============================================================

class TestTheLegacyFieldsDoNotContradictTheDeclaration:
    """旧接线字段正在退休，但**还在**——两份事实并存时必须钉住不矛盾"""

    def _domains_of(self, box_id: str) -> list[str]:
        return [d.id for d in DOMAIN_REGISTRY.values() if box_id in d.boxes]

    def test_a_legacy_edge_exists_in_the_domain_declaration(self):
        """旧 `feeds_into` 的每一条，域声明里都得有对应的**交接或归档**边

        旧字段是**有损**的投影（它把「数据交接」和「归档归属」混在一个列表里），
        所以这里不比「完全一样」，只比**不矛盾**：
        旧字段说有的边，域声明里一条都不能没有。

        **为什么不能是 `feedback`**：旧协议把反馈边单独放在 `feedback_into`，
        所以旧 `feeds_into` 里的每一条都是「我把产出交给它」或「我的产出进它」。
        域声明把它说成 `feedback` = 两边对**这条边是什么性质**的判断不一致。
        """
        for box_id in BOX_REGISTRY:
            placement = _legacy_placement(box_id)
            domains = self._domains_of(box_id)
            if not domains:
                continue
            declared_pairs = {
                (e.from_box, e.to_box)
                for did in domains
                for e in DOMAIN_REGISTRY[did].edges
                if e.kind != EdgeKind.feedback
            }
            for target in placement.feeds_into:
                assert (box_id, target) in declared_pairs, (
                    f"{box_id!r} 的旧 feeds_into 说它投给 {target!r}，"
                    f"域声明里却没有这条交接/归档边——两边矛盾了，"
                    f"看板读的是域声明，所以这条关系会**在图上消失**"
                )

    def test_a_legacy_feedback_edge_is_a_feedback_edge_in_the_declaration(self):
        """旧 `feedback_into` 的每一条，域声明里都得是 `feedback` 边

        反馈边混进交接边 = 主干成环 = 环序排不出来（看板会静默按原顺序收尾）。
        """
        for box_id in BOX_REGISTRY:
            placement = _legacy_placement(box_id)
            domains = self._domains_of(box_id)
            if not domains:
                continue
            feedback_pairs = {
                (e.from_box, e.to_box)
                for did in domains
                for e in DOMAIN_REGISTRY[did].edges
                if e.kind == EdgeKind.feedback
            }
            for target in placement.feedback_into:
                assert (box_id, target) in feedback_pairs, (
                    f"{box_id!r} 的旧 feedback_into 说它反馈给 {target!r}，"
                    f"域声明里却没有这条 feedback 边——"
                    f"两边对「这条边是什么性质」的判断不一致"
                )

    def test_the_legacy_domain_field_matches_the_declaration(self):
        """旧 `placement.domain` 说它在哪个域，域声明就得把它算进那个域"""
        for box_id in BOX_REGISTRY:
            placement = _legacy_placement(box_id)
            if not placement.domain:
                continue
            assert box_id in DOMAIN_REGISTRY[placement.domain].boxes, (
                f"{box_id!r} 的旧 placement.domain 说它属于 "
                f"{placement.domain!r}，域声明的成员清单里却没有它"
            )


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
        for declaration in DOMAIN_REGISTRY.values():
            for edge in declaration.edges:
                if edge.kind != EdgeKind.archive:
                    continue
                assert not edge.via, (
                    f"域 {declaration.id!r} 的归档边 "
                    f"{edge.from_box!r} → {edge.to_box!r} 带了通道 "
                    f"{edge.via!r}——归档不是数据交接"
                )

    def test_a_handoff_edge_always_has_a_channel(self):
        """交接边**必须有通道**——没有通道的交接是声明了却走不通"""
        for declaration in DOMAIN_REGISTRY.values():
            for edge in declaration.edges:
                if edge.kind != EdgeKind.handoff:
                    continue
                assert edge.via, (
                    f"域 {declaration.id!r} 的交接边 "
                    f"{edge.from_box!r} → {edge.to_box!r} 没有通道——"
                    f"交接没有通道 = 下游拿不到这份产出"
                )

    def test_the_kind_matches_the_channel(self):
        """有通道的边是交接，没通道的边不是——两者不许含糊"""
        for declaration in DOMAIN_REGISTRY.values():
            for edge in declaration.edges:
                if edge.via:
                    assert edge.kind == EdgeKind.handoff, (
                        f"域 {declaration.id!r} 的边 "
                        f"{edge.from_box!r} → {edge.to_box!r} 带了通道 "
                        f"{edge.via!r}，kind 却是 {edge.kind.value!r}"
                    )
                else:
                    assert edge.kind != EdgeKind.handoff, (
                        f"域 {declaration.id!r} 的交接边 "
                        f"{edge.from_box!r} → {edge.to_box!r} 没有通道"
                    )
