"""tests.test_new_box_scaffold - 脚手架生成的盒子必须真的过真守卫

**为什么这条守卫不是「抄一份检查项」**：抄的那份必然漂。
这里**调用真守卫**——`tests/test_box_standard.py` 里的那些方法
本来就是按 `box_id` 参数化的，传新盒子的 id 进去就行；
`tests/test_docs_match_code.py` 的条数解析也直接拿来用。

**为什么值得这么较真**：脚手架的全部价值就是
「新域接入只写业务，不写样板」。它值不值这个钱，
取决于它生成的盒子**是不是真的合规**——不是「看起来像」。

**不许真的往仓里写**：盒子生成在临时目录，靠给 `boxes.__path__`
加一条临时路径让它能被 import（不动真的 `boxes/` 目录，
也不碰 `kanban/trigger.py`）。
"""

from __future__ import annotations

import argparse
import ast
import importlib
import pathlib

import pytest

from kanban.trigger import (
    BOX_REGISTRY,
    METRIC_COMPUTERS,
    RUNNER_REGISTRY,
    list_task_entries,
    trigger_task,
)
from runtime.store import TaskRunStore
from scripts.new_box import (
    apply_registration,
    build_spec,
    parse_box_id,
    write_box,
)

REPO = pathlib.Path(__file__).resolve().parent.parent


def _spec(
    box_id: str = "fixture_probe_box",
    *,
    domain: str = "FIXTURE",
    feeds_into: str = "",
    insert_after: str = "",
    sink: bool = True,
):
    """按脚手架的**真实入口**造一份 spec（不手搓字段）

    默认是**一个新域的第一只盒子**（`--domain FIXTURE --sink`）：
    这正是脚手架要支持的头号场景（接一个新域只写业务）。
    它**不改任何别的盒子** → 守卫跑起来跟「仓里原样」一样干净。
    """
    return build_spec(argparse.Namespace(
        box_id=box_id,
        domain=domain,
        display_name="脚手架样例盒",
        value_stream="Software Delivery",
        role="样例域的履约",
        description="脚手架自测用的一只盒子",
        feeds_into=feeds_into,
        insert_after=insert_after,
        sink=sink,
    ))


@pytest.fixture()
def scaffolded(tmp_path, monkeypatch):
    """在临时目录造一只盒子，并把它注册进**真注册表**"""
    spec = _spec()
    write_box(spec, tmp_path / "boxes")

    # 让 `boxes` 包能找到临时目录里那只盒子（不改真的 boxes/ 目录）
    import boxes

    monkeypatch.setattr(
        boxes, "__path__", list(boxes.__path__) + [str(tmp_path / "boxes")],
    )
    module = importlib.import_module(f"boxes.{spec.module}")
    measure = importlib.import_module(f"boxes.{spec.module}.measure")

    monkeypatch.setitem(
        BOX_REGISTRY, spec.box_id,
        (module.get_definition, {spec.beeline_id: module.get_beeline}),
    )
    monkeypatch.setitem(RUNNER_REGISTRY, spec.box_id, module.RUNNER)
    monkeypatch.setitem(METRIC_COMPUTERS, spec.box_id, measure.COMPUTERS)
    return spec


class TestTheScaffoldedBoxPassesTheRealGuards:
    """**调用真守卫**，不抄一份检查项"""

    def test_it_passes_the_declaration_guards(self, scaffolded):
        from kanban.trigger import box_manifest
        from tests.test_box_standard import TestDeclarationCoherence

        guard = TestDeclarationCoherence()
        guard.test_task_schemas_exist_and_beelines_are_pinned(scaffolded.box_id)
        guard.test_every_acceptance_metric_has_a_value_in_result(scaffolded.box_id)
        guard.test_beeline_only_names_schemas_the_box_declares(scaffolded.box_id)

        # manifest 里也得指向同一个产出 schema
        # （标准里那条守卫是写死在需求盒上的，所以这里对**新盒子**自己查一遍）
        definition = BOX_REGISTRY[scaffolded.box_id][0]()
        assert box_manifest(scaffolded.box_id)["produces"]["schema"] \
            == definition.result.result_schema

    def test_it_passes_the_exception_guards(self, scaffolded):
        from tests.test_box_standard import TestExceptionConditionsAreReachable

        guard = TestExceptionConditionsAreReachable()
        guard.test_condition_names_a_field_the_result_can_carry(scaffolded.box_id)
        guard.test_every_condition_part_can_actually_be_read(scaffolded.box_id)
        guard.test_every_condition_can_actually_fire(scaffolded.box_id)
        guard.test_condition_value_type_matches_the_field_type(scaffolded.box_id)

    def test_it_passes_the_skeleton_guards(self, scaffolded):
        from tests.test_box_standard import TestSkeletonIsHonest

        guard = TestSkeletonIsHonest()
        guard.test_every_box_declares_a_maturity_it_actually_has(scaffolded.box_id)
        guard.test_a_skeleton_box_declares_criteria_and_no_metrics(scaffolded.box_id)

    def test_its_example_payload_is_really_submittable(self, scaffolded):
        from tests.test_box_standard import TestExamplePayloadsAreSubmittable

        TestExamplePayloadsAreSubmittable().test_the_example_payload_satisfies_the_task_schema(
            scaffolded.box_id,
        )

    def test_the_kanban_still_accepts_it(self, scaffolded):
        """注册进去之后，看板层不许因此变红

        （看板不许自己持有「每只盒子是什么」——它从注册表读。
        新盒子一注册，看板的分域、渲染器回退都得照样成立。）
        """
        from tests.test_box_standard import (
            TestArtifactView,
            TestRegistryCoverage,
        )

        TestArtifactView().test_declared_views_all_have_a_renderer_or_fall_back()
        TestRegistryCoverage().test_every_registered_box_has_a_runner()

    def test_its_readme_counts_match_its_definition(self, scaffolded, tmp_path):
        """盒子 README 里写的条数必须等于实际的条数

        **复用文档守卫自己的解析器**（`COUNT_PHRASE` / `_COUNT_KIND`）——
        抄一份解析规则，必然跟真的那份漂。
        """
        from tests.test_docs_match_code import COUNT_PHRASE, _COUNT_KIND

        definition = BOX_REGISTRY[scaffolded.box_id][0]()
        acc_n = len(definition.result.acceptance)
        exc_n = len(definition.result.exceptions or [])
        allowed = {0: {acc_n}, 1: {exc_n}}

        readme = (tmp_path / "boxes" / scaffolded.module / "README.md").read_text(
            encoding="utf-8",
        )
        bad = []
        for lineno, line in enumerate(readme.splitlines(), 1):
            for number, word in COUNT_PHRASE.findall(line):
                kind = _COUNT_KIND.get(word.lower().strip())
                if kind is None:
                    continue
                if int(number) not in allowed[kind]:
                    bad.append(f"第 {lineno} 行：「{number} {word}」")
        assert not bad, (
            f"盒子 README 的条数对不上（实际 {acc_n} 条判据 / {exc_n} 条例外）：{bad}"
        )


class TestTheScaffoldedBoxIsHonestlyRed:
    def test_it_really_fails_its_criteria(self, scaffolded, tmp_path):
        """骨架盒必须**真的红**

        「未实现」这三个字的唯一凭据是跑一遍红。
        只看 `maturity` 字段的话，那只是一行声明。
        """
        entry = list_task_entries(scaffolded.box_id)[0]
        result = trigger_task(
            scaffolded.box_id,
            entry["task_type"],
            entry["sample_payload"],
            TaskRunStore(tmp_path / "runs.jsonl"),
        )
        assert result["acceptance_status"] == "rejected", (
            f"骨架盒跑成了 {result['acceptance_status']}——"
            "「交不出东西」不是「用不上」，更不是通过"
        )
        outcomes = {d["metric"]: d["outcome"] for d in result["acceptance_detail"]}
        assert outcomes and all(v == "failed" for v in outcomes.values()), (
            f"骨架盒不该有判据通过或被判不适用：{outcomes}"
        )


class TestTheScaffoldItself:
    def test_it_refuses_to_overwrite_a_delivered_box(self, tmp_path):
        """**不许冲掉已交付的盒子**——那比没有脚手架更糟"""
        spec = _spec()
        write_box(spec, tmp_path / "boxes")
        with pytest.raises(SystemExit, match="已经存在"):
            write_box(spec, tmp_path / "boxes")

    def test_box_id_must_follow_the_convention(self):
        """box_id 不合约定就当场说清楚，不生成半只盒子"""
        for bad in ["Wms_Box", "wms-probe-box", "wmsprobe", "", "1wms_box"]:
            with pytest.raises(ValueError):
                parse_box_id(bad)

    def test_the_names_are_derived_not_hand_written(self):
        """模块名 / op 前缀 / schema id 全部**推导**出来

        推导 = 唯一：不存在「盒子叫 A、op 叫 B」这种要人记的对应关系。
        """
        module, prefix = parse_box_id("wms_picking_box")
        assert module == "wms_picking"
        assert prefix == "picking"

        spec = _spec("wms_picking_box")
        assert spec.schema_input == "schema_wms_picking_input"
        assert spec.schema_output == "schema_wms_picking_output"
        assert spec.beeline_id == "beeline_wms_picking_v1"
        assert spec.op_intake == "picking_intake"
        assert spec.op_work == "picking_work"
        assert spec.op_packager == "picking_packager"
        assert spec.metric_ok == "picking_ok"

    def test_the_wiring_must_be_spelled_out(self):
        """**不许留空接线**

        空着的 `feeds_into` 让新盒子变成本域的**第二个终点** →
        圆心算不出来 → 看板拓扑直接禁用。
        这个坑是端到端跑出来的：脚手架第一版留了空列表，
        一次就把 9 条真守卫跑红了。
        """
        args = argparse.Namespace(
            box_id="wms_probe_box", domain="WMS", display_name="x",
            value_stream="Software Delivery", role="r", description="d",
            feeds_into="", insert_after="", sink=False,
        )
        with pytest.raises(ValueError, match="必须说清它站在哪"):
            build_spec(args)

    def test_the_wiring_must_point_at_a_real_box(self):
        """接线指向不存在的盒子 → 当场拒绝

        一条指向虚空的边不会报错，只会让场图少画一只盒子。
        """
        args = argparse.Namespace(
            box_id="wms_probe_box", domain="WMS", display_name="x",
            value_stream="Software Delivery", role="r", description="d",
            feeds_into="nosuch_box", insert_after="", sink=False,
        )
        with pytest.raises(ValueError, match="不存在的盒子"):
            build_spec(args)

    def test_a_sink_and_a_feeds_into_are_mutually_exclusive(self):
        args = argparse.Namespace(
            box_id="wms_probe_box", domain="WMS", display_name="x",
            value_stream="Software Delivery", role="r", description="d",
            feeds_into="wms_ops_box", insert_after="", sink=True,
        )
        with pytest.raises(ValueError, match="互斥"):
            build_spec(args)

    def test_a_branch_wiring_is_refused(self):
        """从链中间**分叉**出去 → 拒绝

        拓扑排序对分叉的域照样排得出顺序，但那个顺序里相邻的两只
        根本没有交接边——而看板画出来的是环，读者会以为相邻就是相连。
        **画出来的是一张撒谎的图**，所以脚手架不产这种盒子。
        """
        args = argparse.Namespace(
            box_id="wms_probe_box", domain="WMS", display_name="x",
            value_stream="Software Delivery", role="r", description="d",
            feeds_into="wms_ops_box", insert_after="", sink=False,
        )
        with pytest.raises(ValueError, match="画不出拓扑"):
            build_spec(args)

    def test_the_generated_box_imports_no_other_box(self, tmp_path):
        """盒子之间不许互相 import（`tests/test_layering.py` 管着这条）"""
        spec = _spec()
        write_box(spec, tmp_path / "boxes")
        box_dir = tmp_path / "boxes" / spec.module

        offenders = []
        for path in sorted(box_dir.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module \
                        and node.module.startswith("boxes."):
                    target = node.module.split(".")[1]
                    if target != spec.module:
                        offenders.append(f"{path.name} → {node.module}")
        assert not offenders, f"生成的盒子 import 了别家盒子：{offenders}"

    def test_the_generated_box_never_writes_into_core(self, tmp_path):
        """生成的盒子只许**从** core 导入，不许**往** core 里写

        业务知识住在盒子里；`core/` 是内核。
        """
        spec = _spec()
        write_box(spec, tmp_path / "boxes")
        box_dir = tmp_path / "boxes" / spec.module

        # 只许导入 core 的**模型**（数据形状），不许别的
        allowed_core = {"core.models", "core.beeline_models", "core.metrics"}
        imported: set[str] = set()
        writes = []
        for path in sorted(box_dir.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("core."):
                        imported.add(node.module)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id == "open":
                    writes.append(f"{path.name}: open(...)")
        assert imported <= allowed_core, (
            f"生成的盒子 import 了 core 里不该进盒子的东西：{sorted(imported - allowed_core)}"
        )
        assert not writes, f"生成的盒子里有写文件的动作：{writes}"


class TestANewDomainIsAFirstClassCase:
    """**接一个新域**是脚手架的头号场景，不能被「域还太小」堵死

    第一版脚手架只问「画得出环吗」——新域刚开始时域里就一只盒子，
    画不出环是**正常状态**（域才刚开），不是「谁接错了」。
    于是脚手架把自己支持的头号场景堵死了：
    域还小（`too_small`）和接线坏了（分叉 / 成环 / 两个汇点）
    **不是一回事**，混在一起就等于「新域永远接不进来」。
    """

    def test_the_first_box_of_a_new_domain_is_allowed(self):
        spec = _spec("qa_probe_box", domain="QA", sink=True)
        assert spec.domain == "QA"
        assert spec.feeds_into == []

    def test_a_new_domain_box_is_not_a_wms_box(self):
        """新域的盒子不许占 `wms_` 前缀

        「wms_* = WMS 域」是读者判断归属的依据；
        新域的盒子抢这个前缀 = 让那条规则失效
        （`tests/test_box_naming.py` 管着这条）。
        """
        spec = _spec("qa_probe_box", domain="QA", sink=True)
        assert not spec.module.startswith("wms_")

    def test_a_broken_wiring_is_still_refused(self):
        """域还小 ≠ 什么都行：真的坏了的接线照样当场拒绝"""
        args = argparse.Namespace(
            box_id="qa_probe_box", domain="WMS", display_name="x",
            value_stream="Software Delivery", role="r", description="d",
            feeds_into="wms_ops_box", insert_after="", sink=False,
        )
        with pytest.raises(ValueError, match="画不出拓扑"):
            build_spec(args)


class TestRewiringTheUpstream:
    """插在链上 → 上游那只盒子的下游要改成新盒子

    **但指向档案盒（汇点）的那条边要留着**：
    整份列表换掉 = 把归档边也删了 → 档案盒收不到这只盒子的产出。
    """

    def test_it_swaps_the_chain_edge_and_keeps_the_archive_edge(self, tmp_path):
        from scripts.new_box import _rewire_upstream

        spec = _spec("qa_probe_box", domain="WMS", insert_after="wms_testing_box", sink=False)
        upstream = tmp_path / "definition.py"
        upstream.write_text(
            "PLACEMENT = PlacementDef(feeds_into=['wms_ops_box', 'wms_system_box'])\n",
            encoding="utf-8",
        )

        note = _rewire_upstream(spec, upstream)

        text = upstream.read_text(encoding="utf-8")
        assert "feeds_into=['qa_probe_box', 'wms_system_box']" in text, (
            f"改写结果不对：{text}"
        )
        assert "wms_ops_box" not in text, "链条那一段应该交给新盒子接手"
        assert "wms_system_box" in text, "指向档案盒（汇点）的边不能被删掉"
        assert note  # 要有话说清楚改了什么

    def test_it_says_so_when_the_file_has_no_wiring(self, tmp_path):
        """文件里找不到 `feeds_into=[...]` → 说清楚要手工改成什么"""
        from scripts.new_box import _rewire_upstream

        spec = _spec("qa_probe_box", domain="WMS", insert_after="wms_testing_box", sink=False)
        upstream = tmp_path / "definition.py"
        upstream.write_text("# 没有接线\n", encoding="utf-8")

        note = _rewire_upstream(spec, upstream)

        assert "feeds_into" in note
        assert "qa_probe_box" in note, "要说清楚该改成什么，不能只说「改一下」"


class TestTheRegistrationInsertion:
    """注册那段插入**不许把 trigger.py 插坏**——插坏了整个看板起不来"""

    def _copies(self, tmp_path):
        """从 **git HEAD** 取一份干净的副本

        用工作区那份不行：万一工作区已经注册过这只盒子，
        「已经注册过」那条报错会先触发，测的就不是插入本身了。

        **三个文件都要副本**：trigger / README / pyproject。
        少拷一个 → 插入那段就会去改**真的**仓文件
        （真发生过：守卫把 `boxes.<新盒子>` 写进了真的 pyproject.toml，
        仓里多一个不存在的包 → `test_the_declared_packages_all_exist` 当场红，
        而红的不是它自己）。
        """
        import subprocess

        def head(path: str) -> str:
            out = subprocess.run(
                ["git", "show", f"HEAD:{path}"],
                cwd=str(REPO), capture_output=True, text=True, check=True,
            )
            return out.stdout

        trigger_copy = tmp_path / "trigger.py"
        trigger_copy.write_text(head("kanban/trigger.py"), encoding="utf-8")
        readme_copy = tmp_path / "README.md"
        readme_copy.write_text(head("README.md"), encoding="utf-8")
        pyproject_copy = tmp_path / "pyproject.toml"
        pyproject_copy.write_text(head("pyproject.toml"), encoding="utf-8")
        return trigger_copy, readme_copy, pyproject_copy

    def test_the_inserted_file_still_parses(self, tmp_path):
        spec = _spec()
        trigger_copy, readme_copy, pyproject_copy = self._copies(tmp_path)
        apply_registration(
            spec,
            trigger_path=trigger_copy,
            readme_path=readme_copy,
            pyproject_path=pyproject_copy,
        )

        # **插错位置 = 语法错误 = 注册表挂掉 = 整个看板起不来**
        ast.parse(trigger_copy.read_text(encoding="utf-8"))

    def test_it_never_writes_into_the_real_repo(self, tmp_path):
        """跑这段插入**不许碰真的仓文件**

        这三个都是真仓文件。守卫要在副本上跑——
        否则每跑一次测试，仓里就多一行 `boxes.<新盒子>`。
        """
        spec = _spec()
        trigger_copy, readme_copy, pyproject_copy = self._copies(tmp_path)
        before = {
            p: p.read_text(encoding="utf-8")
            for p in (
                REPO / "kanban" / "trigger.py",
                REPO / "README.md",
                REPO / "pyproject.toml",
            )
        }
        apply_registration(
            spec,
            trigger_path=trigger_copy,
            readme_path=readme_copy,
            pyproject_path=pyproject_copy,
        )
        for path, text in before.items():
            assert path.read_text(encoding="utf-8") == text, (
                f"{path.name} 被这段插入改了——它必须是副本上的实验"
            )

    def test_the_packaging_list_gets_the_new_package(self, tmp_path):
        """打包清单也得加上它——漏一个 → 装出来的 wheel 里没有那只盒子"""
        spec = _spec()
        trigger_copy, readme_copy, pyproject_copy = self._copies(tmp_path)
        apply_registration(
            spec,
            trigger_path=trigger_copy,
            readme_path=readme_copy,
            pyproject_path=pyproject_copy,
        )
        assert f'"boxes.{spec.module}"' in pyproject_copy.read_text(encoding="utf-8")

    def test_all_three_registries_get_the_box(self, tmp_path):
        """BOX_REGISTRY / METRIC_COMPUTERS / RUNNER_REGISTRY 三张表都要有它

        少登记一处 → 盒子有一半是「声明了却拿不到」。
        """
        spec = _spec()
        trigger_copy, readme_copy, pyproject_copy = self._copies(tmp_path)
        apply_registration(
            spec,
            trigger_path=trigger_copy,
            readme_path=readme_copy,
            pyproject_path=pyproject_copy,
        )

        tree = ast.parse(trigger_copy.read_text(encoding="utf-8"))
        tables = {}
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in ("BOX_REGISTRY", "METRIC_COMPUTERS", "RUNNER_REGISTRY"):
                    value = node.value
                    if isinstance(value, ast.Dict):
                        tables[node.target.id] = {
                            k.value for k in value.keys
                            if isinstance(k, ast.Constant)
                        }
        assert set(tables) == {"BOX_REGISTRY", "METRIC_COMPUTERS", "RUNNER_REGISTRY"}, (
            f"没找到三张注册表：{sorted(tables)}"
        )
        for name, keys in tables.items():
            assert spec.box_id in keys, f"{name} 里没有 {spec.box_id}"

    def test_it_refuses_to_register_twice(self, tmp_path):
        spec = _spec()
        trigger_copy, readme_copy, pyproject_copy = self._copies(tmp_path)
        apply_registration(
            spec,
            trigger_path=trigger_copy,
            readme_path=readme_copy,
            pyproject_path=pyproject_copy,
        )
        with pytest.raises(SystemExit, match="已经注册过"):
            apply_registration(
                spec,
                trigger_path=trigger_copy,
                readme_path=readme_copy,
                pyproject_path=pyproject_copy,
            )
