#!/usr/bin/env python3
"""scripts.new_box - 造一只**合规**的新盒子

## 为什么要有这个脚本

接一个新域要手抄 6 个文件（definition / beeline / schemas / runner /
measure / __init__）+ 在 `kanban/trigger.py` 注册 3 处 + 根 README 加一行。

**难的不是抄文件，是每处都要跟别处对得上**：

- 判据的 metric 名必须是**产出 schema 里的字段名**（否则判据永远读不到值）
- beeline 引用的 schema id 必须**是本盒声明的**（不许借别人的）
- runner 必须认本盒 beeline 的**每一个 op**（声明了却跑不了 = 又一族的「声明了却拿不到」）
- 每个声明的指标都要有**采集算法**（声明了却没采集器 = 看板上拿不到那个数）
- 盒子的 README 里写的**条数必须等于实际条数**（写 5 条实际 8 条 → 读者照着验收就漏）
- 根 README 必须**提到这只盒子**

漏一处的后果不是报错，是一只「看起来注册了、其实哪儿都不对」的盒子。

所以脚手架**按守卫的同一套口径生成**：生成完的盒子必须真的通过
`tests/test_box_standard.py` 和 `tests/test_docs_match_code.py` 的检查。
`tests/test_new_box_scaffold.py` 就是这么验的——它**调用真守卫**，
不抄一份检查项（抄一份必然漂）。

## 它不替你写业务

判据是什么、产出长什么样、腿怎么实现——那些是业务知识，住在盒子里，
不进内核。脚手架只保证**盒子长得对**。

## 接一个**新域**也能走通

新域刚开始时，域里只有这一只盒子——**画不出环**是正常状态，
不是「谁接错了」。所以脚手架只拒绝**真的坏了**的接线
（分叉、成环、冒出第二个终点），域还小不算。
接线合法性问的是看板自己那套场图推导（`core/field_map.py`），
脚手架不抄一份拓扑规则。

## 默认生成的是**骨架盒**（skeleton）

骨架盒 = 结构已立、判据已声明、**腿还没实现** → 跑起来一定是红的。

为什么不给「live」的模板：`evaluate_acceptance` 里是
`if not failed: ACCEPTED` —— 一只**零判据**的盒子会被判成「全过」，
那是「看起来成功，其实什么也没验」的谎。而一只**假装 live** 的盒子
（声明了 live 但腿是桩）更坏：看板上它不是红的，读数却毫无意义。

骨架盒反过来：真声明判据，产出**不带**那些量 → 全红 → 看板标「未实现」。
**「交不出东西」本身就是诚实的信号。**

## 用法

```bash
# 只生成盒子文件（不碰 kanban/trigger.py）
.venv/bin/python scripts/new_box.py wms_picking_box --domain WMS \
    --display-name "拣货盒" --description "把订单拣成出库单"

# 生成 + 注册（改 kanban/trigger.py 和根 README）
.venv/bin/python scripts/new_box.py wms_picking_box --domain WMS --apply

# 生成到别处（自测用）
.venv/bin/python scripts/new_box.py wms_picking_box --domain WMS --out /tmp/x
```

**不许覆盖已存在的盒子**：已交付的盒子被脚手架冲掉，比没有脚手架更糟。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

BOXES_DIR = REPO_ROOT / "boxes"
TRIGGER_PATH = REPO_ROOT / "kanban" / "trigger.py"
ROOT_README = REPO_ROOT / "README.md"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

# 场图的判定码里，**哪些算「接线坏了」**（`core/field_map.py` 给的）。
#
# 另一半（`too_small` / `empty`）是**正常状态**：新域的第一只盒子，
# 域里就一只，撑不起环——那是「域才刚开」，不是「谁接错了」。
# 只认人话那句 reason 分不开这两者，所以 core 给机器可读的判定码。
WIRING_DEFECTS = frozenset({
    "no_sink",        # 域内没有汇点
    "multiple_sinks",  # 冒出了第二个终点 → 圆心不唯一
    "cycle",          # 交接边成环
    "not_a_chain",    # 分叉：环上相邻却没有交接边
})


@dataclass
class BoxSpec:
    """一只新盒子的「身份 + 骨架参数」

    **这些名字全是机械推导的**：从 box_id 推出模块名、op 前缀、schema id。
    推导是为了**唯一**：同一只盒子只有一个名字，不靠人记两套。
    """

    box_id: str
    module: str
    prefix: str
    const: str
    display_name: str
    domain: str
    value_stream: str
    role: str
    description: str
    # **它接谁**（投给谁）。空着 = 它成了本域的**第二个终点** →
    # 圆心算不出来 → 看板拓扑直接禁用。所以接线必须在生成时就问清楚，
    # 不能留个空列表让后人想起来再填。
    feeds_into: list[str] = field(default_factory=list)
    # **它插在链上哪一段之后**（上游那只盒子的下游被它接手）。
    # 域里是一条**线性链**：新盒子要么插进链里，要么自己开一条新链，
    # 不能从中间分叉（分叉 → 环序排不出来 → 拓扑画不出来）。
    insert_after: str = ""

    # op / schema / 判据的名字
    op_intake: str = field(init=False)
    op_work: str = field(init=False)
    op_packager: str = field(init=False)
    schema_input: str = field(init=False)
    schema_plan: str = field(init=False)
    schema_observation: str = field(init=False)
    schema_output: str = field(init=False)
    metric_ok: str = field(init=False)
    task_type: str = field(init=False)
    beeline_id: str = field(init=False)
    beeline_version: int = 1

    def __post_init__(self) -> None:
        self.op_intake = f"{self.prefix}_intake"
        self.op_work = f"{self.prefix}_work"
        self.op_packager = f"{self.prefix}_packager"
        self.schema_input = f"schema_{self.module}_input"
        self.schema_plan = f"schema_{self.module}_plan"
        self.schema_observation = f"schema_{self.module}_observation"
        self.schema_output = f"schema_{self.module}_output"
        self.metric_ok = f"{self.prefix}_ok"
        self.task_type = f"run_{self.prefix}"
        self.beeline_id = f"beeline_{self.module}_v{self.beeline_version}"


def parse_box_id(box_id: str) -> tuple[str, str]:
    """box_id → (模块名, op 前缀)

    **为什么这么推**：模块名 = box_id 去掉 `_box` 后缀，op 前缀 = 模块名最后一段。
    例如 `wms_picking_box` → (`wms_picking`, `picking`)。

    推导出来的好处是**唯一**：不存在「盒子叫 A、op 叫 B」这种要人记的对应关系。
    """
    box_id = box_id.strip()
    if not box_id:
        raise ValueError("box_id 不能为空")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", box_id):
        raise ValueError(
            f"box_id {box_id!r} 不合法：只许小写字母、数字、下划线，且以字母开头"
        )
    if not box_id.endswith("_box"):
        raise ValueError(
            f"box_id {box_id!r} 不合法：必须以 `_box` 结尾"
            "（仓里 8 只盒子都守这个约定，模块名由它推导）"
        )
    module = box_id[: -len("_box")]
    if not module:
        raise ValueError(f"box_id {box_id!r} 去掉 `_box` 后是空的")
    prefix = module.split("_")[-1]
    return module, prefix


def build_spec(args: argparse.Namespace) -> BoxSpec:
    module, prefix = parse_box_id(args.box_id)
    insert_after = (args.insert_after or "").strip()
    feeds_into = [b.strip() for b in (args.feeds_into or "").split(",") if b.strip()]
    if args.sink and (feeds_into or insert_after):
        raise ValueError(
            "--sink（它是本域的终点）和接线互斥：终点没有下游"
        )
    if insert_after and feeds_into:
        raise ValueError(
            "--insert-after（它接手谁的活）和 --feeds-into（它投给谁）"
            "不能同时给：插入链上时，下游由它接手的那只盒子决定"
        )
    if insert_after:
        # **它接手那只盒子的下游** —— 链是线性的，新盒子插在中间，
        # 那只盒子原来的下游就变成新盒子的下游。
        feeds_into = _downstream_of(insert_after)
    elif not feeds_into and not args.sink:
        raise ValueError(
            "**新盒子必须说清它站在哪**——不说的接线有两种坏法：\n"
            "  · 在已有链的域里，它成了本域的**第二个终点** → 圆心算不出来"
            "（看板拓扑直接禁用）\n"
            "  · 在新域里，它成了一只**没接线的孤盒子** → 谁都不知道它跟谁有关\n"
            "所以生成时就问清楚：\n"
            "  --insert-after <box_id>   插在它后面（接手它的下游）\n"
            "  --feeds-into <box_id>      它投给谁（自己开一条链时用）\n"
            "  --sink                    它是本域的终点（没有下游）"
        )
    _validate_feeds_into(feeds_into)
    spec = BoxSpec(
        box_id=args.box_id,
        module=module,
        prefix=prefix,
        const=module.upper(),
        display_name=args.display_name or args.box_id.replace("_", " ").title(),
        domain=args.domain,
        value_stream=args.value_stream,
        role=args.role,
        description=args.description,
        feeds_into=feeds_into,
        insert_after=insert_after,
    )
    code, reason = _wiring_problem(spec)
    if code in WIRING_DEFECTS:
        raise ValueError(
            "**这条接线画不出拓扑**（用的是看板自己那套场图推导）：\n"
            f"{reason}\n\n"
            "域里是一条线性链："
            "新盒子要么**插在链上**（--insert-after <上游盒子>），"
            "要么**自己开一条链**（--feeds-into <终点>），"
            "要么**自己是终点**（--sink）。"
        )
    return spec


def _downstream_of(box_id: str) -> list[str]:
    """那只盒子现在投给谁"""
    try:
        from kanban.trigger import BOX_REGISTRY
    except Exception:  # noqa: BLE001 - 仓外生成时不查
        return []
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        raise ValueError(
            f"要插在 {box_id} 后面，但仓里没有这只盒子。"
            f"已注册的：{sorted(BOX_REGISTRY)}"
        )
    return list(entry[0]().placement.feeds_into)


def _validate_feeds_into(feeds_into: list[str]) -> None:
    """接线指向的盒子必须真的存在

    **为什么现在就要查**：一条指向虚空的边不会报错，只会让场图少画一只盒子
    ——「声明了却拿不到」的又一族。
    """
    if not feeds_into:
        return
    try:
        from kanban.trigger import BOX_REGISTRY
    except Exception:  # noqa: BLE001 - 仓外生成时不校验
        return
    unknown = [b for b in feeds_into if b not in BOX_REGISTRY]
    if unknown:
        raise ValueError(
            f"接线指向了不存在的盒子：{unknown}。\n"
            f"已注册的盒子：{sorted(BOX_REGISTRY)}"
        )


def _wiring_problem(spec: BoxSpec) -> tuple[str, str]:
    """这条接线能不能画出拓扑？用**看板自己那套**场图推导来问

    返回 `(判定码, 人话原因)`。

    **为什么不自己写一套拓扑规则**：规则已经在
    `core/field_map.py` 里了（唯一汇点、环要成链、交接不能成环）。
    脚手架再抄一份，那份必然跟真的那份漂——而漂了的后果是
    「看板拓扑悄悄坏掉」。

    **为什么拿「判定码」而不是人话**：场图有两种「画不出来」——
    **接线坏了**（分叉、成环、两个汇点）和**这个域还太小**
    （新域的第一只盒子，域里就一只，撑不起环）。
    前者必须有人来改，后者是**正常状态**：
    接一个新域的第一步本来就该被允许。
    只看人话那句话，脚本分不开这两者——
    所以 core 给的是**机器可读的判定码**，脚手架只拒绝「接线坏了」那几种。

    **这个检查是端到端跑出来的**：脚手架第一版让接线留空，
    一次就把 9 条真守卫跑红了（第二个汇点 → 圆心算不出来）。
    """
    try:
        from core.field_map import PlacementView, derive_field_map
        from kanban.trigger import (
            order_boxes_by_flow,
            placement_views,
            registered_box_ids,
        )
    except Exception:  # noqa: BLE001 - 仓外生成时不校验
        return "ok", ""

    views = dict(placement_views())
    if spec.insert_after and spec.insert_after in views:
        # 上游那只盒子的下游改成新盒子（插入链中）
        views[spec.insert_after] = replace(
            views[spec.insert_after], feeds_into=(spec.box_id,),
        )
    ids = [
        b for b in order_boxes_by_flow(registered_box_ids())
        if views[b].domain == spec.domain
    ]
    placements = [views[b] for b in ids]
    placements.append(PlacementView(
        box_id=spec.box_id,
        domain=spec.domain,
        feeds_into=tuple(spec.feeds_into),
    ))
    m = derive_field_map(spec.domain, placements)
    return m.reason_code, m.reason


# ============================================================
# 模板
# ============================================================
#
# 用 __TOKEN__ 占位而不是 f-string：生成的代码里有大量 { } （dict 字面量），
# f-string 里每处都要双写，可读性会塌掉。

_INIT_TEMPLATE = '''"""boxes.__MODULE__ - __DISPLAY__

__DESCRIPTION__

**成熟度：skeleton（骨架）**。判据已声明、腿还没实现——
所以这只盒子现在跑出来一定是红的。看板上标着「未实现」，
且不计入验收率。

**为什么宁可红**：一只**零判据**的盒子会被判成「全过」——
那是「看起来成功，其实什么也没验」的谎。
骨架盒反过来：真声明它将来要验的东西，产出**不带**那些量 → 全红。
"""

from __future__ import annotations

from boxes.__MODULE__.definition import __CONST___BOX, get_definition
from boxes.__MODULE__.runner import RUNNER, __CONST___HANDLERS
from boxes.__MODULE__.beeline import __CONST___BEELINE, get_beeline

__all__ = [
    "__CONST___BOX",
    "get_definition",
    "RUNNER",
    "__CONST___HANDLERS",
    "__CONST___BEELINE",
    "get_beeline",
]
'''

_SCHEMAS_TEMPLATE = '''"""boxes.__MODULE__.schemas - __DISPLAY__ 的业务 schemas

**schema 归消费者所有**：本盒自己声明自己吃什么、吐什么。
别的盒子要投料给我，得按**我的** schema 来，
而不是照它自己的习惯另立一套同形状的定义。

## 四个 schema 对应 beeline 的三跳

| schema | 谁产出 | 装什么 |
|---|---|---|
| `__SCHEMA_INPUT__` | 外部投料 | 这次履约的输入 |
| `__SCHEMA_PLAN__` | `__OP_INTAKE__` | 这一跳打算做什么 |
| `__SCHEMA_OBSERVATION__` | `__OP_WORK__` | 这一跳**真的**观测到的东西 |
| `__SCHEMA_OUTPUT__` | `__OP_PACKAGER__` | 这次履约的产出 |

## 判据 metric 名就是本文件的字段名

这不是风格约定：`acceptance` 指向 schema 里没有的字段，
等于判了一条**判不出来的判据**。生成期硬校验
（`tests/test_box_standard.py::TestDeclarationCoherence`）。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef


__CONST___INPUT_SCHEMA = SchemaDef(
    id="__SCHEMA_INPUT__",
    description="投料：__DESCRIPTION__",
    fields=[
        FieldDef(
            name="subject_ref",
            type="string",
            required=False,
            description="这次履约针对哪个业务对象（留空则本盒自己判定）",
        ),
    ],
)

__CONST___PLAN_SCHEMA = SchemaDef(
    id="__SCHEMA_PLAN__",
    description="计划：这一跳要做什么",
    fields=[
        FieldDef(
            name="plan",
            type="string",
            required=False,
            description="这一跳打算做什么（人话一句，够定位就行）",
        ),
    ],
)

__CONST___OBSERVATION_SCHEMA = SchemaDef(
    id="__SCHEMA_OBSERVATION__",
    description="观测：这一跳真的看到了什么",
    fields=[
        FieldDef(
            name="observed",
            type="string",
            required=False,
            description="这一跳**真的**观测到的东西（没观测到就留空，别填推测）",
        ),
    ],
)

__CONST___OUTPUT_SCHEMA = SchemaDef(
    id="__SCHEMA_OUTPUT__",
    description="产出：__DESCRIPTION__ 的结果",
    fields=[
        FieldDef(
            name="__METRIC_OK__",
            type="boolean",
            required=False,
            description="判据要读的实测值：这次真的做成了吗",
        ),
    ],
)

ALL_SCHEMAS = [
    __CONST___INPUT_SCHEMA,
    __CONST___PLAN_SCHEMA,
    __CONST___OBSERVATION_SCHEMA,
    __CONST___OUTPUT_SCHEMA,
]
'''

_BEELINE_TEMPLATE = '''"""boxes.__MODULE__.beeline - __DISPLAY__ 的作业路线

op 链（v__BEELINE_VERSION__）：
  1. `__OP_INTAKE__`      取投料 → 这一跳要做什么
  2. `__OP_WORK__`        真干活 → 真的观测到的东西
  3. `__OP_PACKAGER__`    装配产出

**beeline 跟盒子是 1:1 且不可分割的**：它引用的 schema id 全部来自本盒的
`schemas.py`，每个 op 的 handler 都在本盒的 `runner.py` 里。
所以它住在盒子里面，而不是仓库顶层一个单独的目录——
顶层目录会让人以为存在一种「可复用的跨盒作业路线」，而实际上一条都没有。
"""

from __future__ import annotations

from core.beeline_models import (
    Bee,
    Beeline,
    Idempotency,
    NextRef,
    Operation,
)


# 同一任务重复执行必须产出同一份结果
IDEMPOTENT_BY_TASK = Idempotency(mode="required", key="task_id")


__CONST___BEELINE = Beeline(
    id="__BEELINE_ID__",
    version=__BEELINE_VERSION__,
    description=(
        "__DISPLAY__ 履约作业路线：__OP_INTAKE__ → __OP_WORK__ → __OP_PACKAGER__"
    ),

    operations=[
        Operation(
            op_id="__OP_INTAKE__",
            type="__OP_INTAKE__",
            input_from="external",
            input="__SCHEMA_INPUT__",
            output="__SCHEMA_PLAN__",
            next=[NextRef(op_id="__OP_WORK__")],
            bee=Bee(type="__OP_INTAKE___handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="__OP_WORK__",
            type="__OP_WORK__",
            input_from="__OP_INTAKE__",
            input="__SCHEMA_PLAN__",
            output="__SCHEMA_OBSERVATION__",
            next=[NextRef(op_id="__OP_PACKAGER__")],
            bee=Bee(type="__OP_WORK___handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="__OP_PACKAGER__",
            type="__OP_PACKAGER__",
            input_from="__OP_WORK__",
            input="__SCHEMA_OBSERVATION__",
            output="__SCHEMA_OUTPUT__",
            next=[],
            bee=Bee(type="__OP_PACKAGER___handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),
    ],
)


def get_beeline() -> Beeline:
    return __CONST___BEELINE
'''

_DEFINITION_TEMPLATE = '''"""boxes.__MODULE__.definition - __DISPLAY__ 的 Definition 数据

__DESCRIPTION__

按 Fulfillment 5 维度 + 度量组织：
- 基础元信息 + placement（归属 / 接谁 / 投给谁 / 属于哪个域）
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + acceptance + exceptions）
- 履约政策 Policy（queen）
- 履约度量 Metrics

**成熟度：skeleton**。腿还没实现 → 产出不带判据要读的量 → 全红。
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    MetricsDef,
    PlacementDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    TaskDef,
)
from boxes.__MODULE__.schemas import ALL_SCHEMAS

__CONST___BOX = BeeBoxDefinition(
    id="__BOX_ID__",
    version=1,
    description=(
        "__DESCRIPTION__\\n\\n"
        "**成熟度：skeleton**。判据已声明、腿还没实现——"
        "跑起来一定是红的，这是诚实的信号，不是故障。"
    ),

    schemas=ALL_SCHEMAS,

    placement=PlacementDef(
        display_name="__DISPLAY__",
        value_stream="__VALUE_STREAM__",
        role="__ROLE__",
        domain="__DOMAIN__",
        # **接线在生成时就填好了**：空着的 feeds_into 会让这只盒子
        # 变成本域的第二个终点 → 圆心算不出来 → 看板拓扑禁用。
        feeds_into=__FEEDS_INTO__,
    ),

    task=[
        TaskDef(
            type="__TASK_TYPE__",
            task_schema="__SCHEMA_INPUT__",
            beeline_id="__BEELINE_ID__",
            beeline_version=__BEELINE_VERSION__,
            # **不声明 subject_ref_field**：等你知道这只盒子处理的是哪一类
            # 业务对象再补。声明了就必须真的投进来（runtime 会拒）。
            trigger="__DESCRIPTION__",
        ),
    ],

    result=ResultDef(
        type="__MODULE___result",
        result_schema="__SCHEMA_OUTPUT__",
        maturity="skeleton",
        acceptance=[
            # **判据读的是产出里的字段名**——字段不在产出 schema 里，
            # 这条判据就永远读不到值（生成期硬校验）。
            AcceptanceRule(metric="__METRIC_OK__", op="eq", value=True),
        ],
        exceptions=[
            # **例外 = 真会发生的失败**。写不出来就先别写：
            # 一条永远不会触发的规则看起来在守着某件事，实际什么都没守。
            ExceptionRule(condition="__METRIC_OK__ == false", action="escalate"),
        ],
        view="generic",
    ),

    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            # 持续改进这一位现在**没有运行时**：queen 接到执行路径上之前，
            # 放开等于放开门上的锁，而锁后面没有房间。
            continuous_improvement="observe_only",
        ),
        rules=QueenRules(
            task_routing={},
            exception_handling={},
            continuous_improvement={},
        ),
    ),

    # **骨架盒不声明指标**：没有产出，声明了也拿不到。
    # 转成 live（腿实现了）时，再在这里声明指标，
    # 并在 `measure.py` 给每个指标一个采集算法。
    metrics=MetricsDef(),
)


def get_definition() -> BeeBoxDefinition:
    return __CONST___BOX
'''

_RUNNER_TEMPLATE = '''"""boxes.__MODULE__.runner - __DISPLAY__ 的 operation 实现

op 链：
  1. `__OP_INTAKE__`      取投料 → 这一跳要做什么
  2. `__OP_WORK__`        真干活 → 真的观测到的东西
  3. `__OP_PACKAGER__`    装配产出

**这三跳现在都是骨架**：它们跑得通，但**不产出判据要读的量**
（`__METRIC_OK__` 不在产出里）→ 判据全红 → REJECTED → 看板标「未实现」。

**为什么不给一个「假装成功」的模板**：一只填了 `__METRIC_OK__=true` 的
桩盒，看板上不是红的，读数却毫无意义——那是比红更坏的谎。
宁可红，不要假装绿。

**失败路径必须显式**：真实现时，每一次失败都要进产出并带原因，
不要静默跳过、不要填默认值。兜底会把链路 bug 伪装成业务 bug。
"""

from __future__ import annotations

from typing import Any

from runtime.runner import HandlerRunner


def __OP_INTAKE___handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：把投料变成「这一跳要做什么」

    骨架实现：只把投料原样带过去。**真实现时**在这里做真正的准备动作
    （解析、校验、查基线……）。
    """
    subject = input_data.get("subject_ref")
    return {
        "plan": f"处理 {subject}" if subject else "处理这次投料",
    }


def __OP_WORK___handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：真干活 → 真的观测到的东西

    骨架实现：**不观测任何东西**。`observed` 留空——留空是诚实的，
    填一个推测值不是。
    """
    return {
        "observed": "",
    }


def __OP_PACKAGER___handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：装配产出

    **刻意不产出 `__METRIC_OK__`** —— 这只盒子还没实现。
    少一个字段 → 判据读不到值 → 判红 → 「未实现」。
    """
    return {
        "note": "骨架盒：这一腿还没实现，产出里刻意不带判据要读的量。",
    }


__CONST___HANDLERS: dict[str, Any] = {
    "__OP_INTAKE__": __OP_INTAKE___handler,
    "__OP_WORK__": __OP_WORK___handler,
    "__OP_PACKAGER__": __OP_PACKAGER___handler,
}

RUNNER = HandlerRunner(__CONST___HANDLERS, owner="__BOX_ID__")
'''

_MEASURE_TEMPLATE = '''"""boxes.__MODULE__.measure - __DISPLAY__ 的实测指标采集

`definition.metrics` 里写的是 target（目标）。目标是承诺，不是事实。
这一层负责从履约历史里把 actual 算出来，让看板显示的每个数字都有出处。

**骨架盒不声明指标**，所以这里没有采集算法。

**转成 live 时**：在 `definition.metrics` 里声明指标，
在这里给**每一个**指标一个采集算法。声明了却没采集器 =
看板上拿不到那个数（`tests/test_metrics.py` 会红）。
"""

from __future__ import annotations

from core.metrics import Computer


# 骨架盒没有指标 → 没有采集算法。键是指标名，值是算法。
COMPUTERS: dict[str, Computer] = {}
'''

_BOX_README_TEMPLATE = '''# __DISPLAY__

__DESCRIPTION__

| | |
|---|---|
| box_id | `__BOX_ID__` |
| 域 | __DOMAIN__ |
| 价值流 | __VALUE_STREAM__ |
| 成熟度 | **skeleton**（腿还没实现，跑起来一定是红的） |
| 判据 | 1 条（`__METRIC_OK__`） |
| 例外 | 1 条（`__METRIC_OK__ == false` → escalate） |
| 接线 | __FEEDS_INTO_TEXT__ |

## 它是什么

`__TASK_TYPE__` 吃 `__SCHEMA_INPUT__`，走 `__BEELINE_ID__`：

1. `__OP_INTAKE__` — 取投料 → 这一跳要做什么
2. `__OP_WORK__` — 真干活 → 真的观测到的东西
3. `__OP_PACKAGER__` — 装配产出 → `__SCHEMA_OUTPUT__`

## 怎么把它变成真的

骨架盒的腿在 `runner.py` 里，三个 handler 现在只把投料带过去。
按顺序做这几件事：

1. **写产出**：让 `__OP_PACKAGER___handler` 真的产出 `__METRIC_OK__`，
   并且**填真实观测到的值**——不是常量 true。
   判据读的就是这个字段，填错就是判据在撒谎。
2. **写腿**：`__OP_WORK___handler` 里做真正的业务动作。
   失败要**显式带原因**进产出，不要静默跳过、不要填默认值。
3. **加指标**：在 `definition.py` 的 `metrics` 里声明指标，
   在 `measure.py` 里给每一个指标一个采集算法。
   声明了却没采集器 = 看板上拿不到那个数。
4. **改成 live**：`result.maturity` 从 `skeleton` 改成 `live`。
   **先做完 1~3 再改这一处**——顺序反了，看板上会出现一只
   「标着 live、读数却毫无意义」的盒子。
5. **看文档**：`README.md`（根目录）的盒子清单要提到这只盒子，
   本文件里写的条数要等于实际的条数——
   两条都有守卫盯着（`tests/test_docs_match_code.py`）。

## 规矩（跟本仓其它盒子一样）

- **判据只判能机械判的**：读产出里的字段，不读「我觉得对不对」。
- **schema 归消费者所有**：本盒声明自己吃什么、吐什么。
- **不许 import 别的盒子**：`tests/test_layering.py` 管着这条。
- **业务知识住在盒子里**，不进 `core/`。
'''


def render(spec: BoxSpec) -> dict[str, str]:
    """生成这只盒子的所有文件内容（文件名 → 正文）"""
    tokens = {
        "__BOX_ID__": spec.box_id,
        "__MODULE__": spec.module,
        "__PREFIX__": spec.prefix,
        "__CONST__": spec.const,
        "__DISPLAY__": spec.display_name,
        "__DOMAIN__": spec.domain,
        "__VALUE_STREAM__": spec.value_stream,
        "__ROLE__": spec.role,
        "__DESCRIPTION__": spec.description,
        "__OP_INTAKE__": spec.op_intake,
        "__OP_WORK__": spec.op_work,
        "__OP_PACKAGER__": spec.op_packager,
        "__SCHEMA_INPUT__": spec.schema_input,
        "__SCHEMA_PLAN__": spec.schema_plan,
        "__SCHEMA_OBSERVATION__": spec.schema_observation,
        "__SCHEMA_OUTPUT__": spec.schema_output,
        "__METRIC_OK__": spec.metric_ok,
        "__TASK_TYPE__": spec.task_type,
        "__BEELINE_ID__": spec.beeline_id,
        "__BEELINE_VERSION__": str(spec.beeline_version),
        "__FEEDS_INTO__": repr(list(spec.feeds_into)),
        "__FEEDS_INTO_TEXT__": (
            "、".join(f"`{b}`" for b in spec.feeds_into)
            if spec.feeds_into
            else "**本域的终点**（没有下游）"
        ),
    }
    templates = {
        "__init__.py": _INIT_TEMPLATE,
        "schemas.py": _SCHEMAS_TEMPLATE,
        "beeline.py": _BEELINE_TEMPLATE,
        "definition.py": _DEFINITION_TEMPLATE,
        "runner.py": _RUNNER_TEMPLATE,
        "measure.py": _MEASURE_TEMPLATE,
        "README.md": _BOX_README_TEMPLATE,
    }
    out: dict[str, str] = {}
    for filename, template in templates.items():
        text = template
        for token, value in tokens.items():
            text = text.replace(token, value)
        out[filename] = text
    return out


def write_box(spec: BoxSpec, out_dir: Path, *, force: bool = False) -> list[Path]:
    """把盒子写到磁盘。**已存在就拒绝**（不许冲掉已交付的盒子）"""
    target = out_dir / spec.module
    if target.exists() and not force:
        raise SystemExit(
            f"{target} 已经存在——脚手架不覆盖已交付的盒子。"
            "要重来就先把那个目录挪走。"
        )
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, content in render(spec).items():
        path = target / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


# ============================================================
# 注册
# ============================================================


def registration_snippet(spec: BoxSpec) -> str:
    """贴进 `kanban/trigger.py` 的注册代码（打印给人看，也能被 --apply 写进去）

    **为什么是「三处」**：BOX_REGISTRY（这只盒子是什么）、
    METRIC_COMPUTERS（它的指标怎么算）、RUNNER_REGISTRY（它的腿在哪）。
    少登记一处，盒子就有一半是「声明了却拿不到」。
    """
    return "\n".join([
        f"from boxes.{spec.module}.beeline import get_beeline as get_{spec.module}_beeline",
        f"from boxes.{spec.module}.measure import COMPUTERS as {spec.const}_COMPUTERS",
        f"from boxes.{spec.module}.runner import RUNNER as {spec.const}_RUNNER",
        f"from boxes.{spec.module} import get_definition as get_{spec.module}_definition",
        "",
        f'    "{spec.box_id}": (',
        f"        get_{spec.module}_definition,",
        f'        {{"beeline_{spec.module}_v{spec.beeline_version}": get_{spec.module}_beeline}},',
        "    ),",
    ])


def _insert_before_dict_close(text: str, dict_name: str, block: str) -> str:
    """在 `dict_name = {` 这个字面量的收尾 `}` 之前插一段

    **为什么要按名字找而不是按行号**：行号会漂（别人加一行 import 就错位），
    而插错位置的代价是**语法错误**——注册表挂掉，整个看板起不来。
    """
    start = text.find(f"{dict_name}")
    if start < 0:
        raise SystemExit(f"kanban/trigger.py 里找不到 {dict_name}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"{dict_name} 后面找不到 '{{'")
    # 逐字符找配对的收尾括号（跳过字符串里的括号——本仓的字面量里没有，
    # 但用行扫描更保守）
    lines = text[brace:].splitlines(keepends=True)
    depth = 0
    offset = brace
    for line in lines:
        depth += line.count("{") - line.count("}")
        offset += len(line)
        if depth <= 0:
            insert_at = offset - len(line)
            break
    else:
        raise SystemExit(f"{dict_name} 的字面量没闭合")
    return text[:insert_at] + block + text[insert_at:]


def _insert_after_last_import(text: str, block: str) -> str:
    """在最后一条 `from boxes.` import 之后插入"""
    lines = text.splitlines(keepends=True)
    last = -1
    for index, line in enumerate(lines):
        if line.startswith("from boxes."):
            last = index
    if last < 0:
        raise SystemExit("kanban/trigger.py 里找不到 `from boxes.` 的 import")
    return "".join(lines[: last + 1]) + block + "".join(lines[last + 1:])


def apply_registration(
    spec: BoxSpec,
    *,
    trigger_path: Path,
    readme_path: Path,
    pyproject_path: Path,
) -> list[str]:
    """把注册写进 `kanban/trigger.py`，并把盒子加进根 README 的清单

    **为什么根 README 也要改**：`tests/test_docs_match_code.py` 盯着
    「README 覆盖全部注册盒子」——新盒子注册了却没进 README，
    那条守卫立刻红。文档滞后是必然的，靠人记就是必然漏。

    **为什么打包清单也要改**：`tests/test_box_naming.py` 盯着
    「每个盒子目录都得在 pyproject 的 packages 里」——
    漏一个 → 装出来的 wheel 里没有那只盒子。

    ## 为什么这三个路径是**必填**的，不给默认值

    这三个文件都是**真的仓文件**。给默认值 = 调用方忘了传路径时，
    它会安静地改真的仓——而调用方往往只是想验一下「这段插入对不对」。
    真发生过：守卫在副本上跑插入，却把 `boxes.<新盒子>` 写进了
    **真的** `pyproject.toml` → 仓里多一个不存在的包 →
    `test_the_declared_packages_all_exist` 当场红，而红的不是它自己。

    所以：**要改哪几个文件，调用方必须自己说**。
    守卫在副本上跑，就传副本。
    """
    notes: list[str] = []
    trigger_text = trigger_path.read_text(encoding="utf-8")
    if f'"{spec.box_id}"' in trigger_text:
        raise SystemExit(f"{spec.box_id} 已经注册过了——不重复注册")

    imports = "\n".join([
        f"from boxes.{spec.module}.beeline import get_beeline as get_{spec.module}_beeline",
        f"from boxes.{spec.module}.measure import COMPUTERS as {spec.const}_COMPUTERS",
        f"from boxes.{spec.module}.runner import RUNNER as {spec.const}_RUNNER",
        f"from boxes.{spec.module} import get_definition as get_{spec.module}_definition",
        "",
    ])
    trigger_text = _insert_after_last_import(trigger_text, imports)

    registry_entry = (
        f'    "{spec.box_id}": (\n'
        f"        get_{spec.module}_definition,\n"
        f'        {{"beeline_{spec.module}_v{spec.beeline_version}": get_{spec.module}_beeline}},\n'
        f"    ),\n"
    )
    trigger_text = _insert_before_dict_close(trigger_text, "BOX_REGISTRY", registry_entry)
    trigger_text = _insert_before_dict_close(
        trigger_text, "METRIC_COMPUTERS",
        f'    "{spec.box_id}": {spec.const}_COMPUTERS,\n',
    )
    trigger_text = _insert_before_dict_close(
        trigger_text, "RUNNER_REGISTRY",
        f'    "{spec.box_id}": {spec.const}_RUNNER,\n',
    )
    trigger_path.write_text(trigger_text, encoding="utf-8")
    notes.append(f"{trigger_path.name}：注册了 {spec.box_id}（3 张表 + import）")

    readme_text = readme_path.read_text(encoding="utf-8")
    row = (
        f"  | [`{spec.module}/`](boxes/{spec.module}/README.md) | "
        f"`{spec.box_id}` | {spec.domain} | 1 | 1 | skeleton |\n"
    )
    # **接在表格最后一行后面**（不是第一行后面）：
    # 插在第一行后面 = 新盒子夹在 WMS 那几行中间，读者会以为它是 WMS 域的。
    rows = list(re.finditer(
        r"(?m)^  \| \[`[^`]+/`\]\(boxes/[^)]+\).*\n", readme_text,
    ))
    if rows:
        anchor = rows[-1]
        readme_text = readme_text[: anchor.end()] + row + readme_text[anchor.end():]
        readme_path.write_text(readme_text, encoding="utf-8")
        notes.append(f"{readme_path.name}：盒子清单加了一行")
    else:
        notes.append(
            f"{readme_path.name}：**没找到盒子清单表格**，请手工加一行：\n" + row.rstrip()
        )

    # **打包清单**：漏一个 → 装出来的 wheel 里没有那只盒子
    py_text = pyproject_path.read_text(encoding="utf-8")
    package_line = f'    "boxes.{spec.module}",\n'
    if package_line not in py_text:
        anchor = re.search(r'(?m)^(\s*)"boxes\.[a-z0-9_]+",\n', py_text)
        if anchor:
            py_text = py_text[: anchor.end()] + package_line + py_text[anchor.end():]
            pyproject_path.write_text(py_text, encoding="utf-8")
            notes.append(f"{pyproject_path.name}：packages 加了 boxes.{spec.module}")
        else:
            notes.append(
                f"{pyproject_path.name}：**没找到 packages 清单**，请手工加一行：\n"
                + package_line.rstrip()
            )

    # **插在链上** → 上游那只盒子的下游改成新盒子
    if spec.insert_after:
        upstream_path = _definition_path_of(spec.insert_after)
        if upstream_path is None:
            notes.append(
                f"**没找到 {spec.insert_after} 的 definition.py** —— "
                f"请手工把它的 feeds_into 改成 [{spec.box_id!r}]"
            )
        else:
            notes.append(_rewire_upstream(spec, upstream_path))
            notes.append(
                f"⚠️  你改了 {spec.insert_after} 的下游。**接线是产品事实**，"
                "仓里有守卫把它整条钉死"
                "（tests/test_wms_requirement_box.py::TestFeedInto）——"
                "那几条钉子要**手工**同步更新，否则会红。"
                "这不是脚手架能替你做的决定：改接线等于改产品结构。"
            )
    return notes


def _definition_path_of(box_id: str) -> Path | None:
    """那只盒子的 `definition.py` 在哪 —— **从注册表里问出来，不靠目录名猜**

    **为什么不能猜**：目录名**不一定**等于 box_id 去掉 `_box`：
    `receipt_capture_box` 住在 `boxes/receipt/`，
    `wms_testing_box` 住在 `boxes/wms_testing/`。
    猜错 = 改错文件（或者报「文件不存在」让人自己找）。
    """
    import sys

    try:
        from kanban.trigger import BOX_REGISTRY
    except Exception:  # noqa: BLE001 - 仓外生成时不查
        return None
    entry = BOX_REGISTRY.get(box_id)
    if entry is None:
        return None
    module = sys.modules.get(getattr(entry[0], "__module__", "") or "")
    path = getattr(module, "__file__", None)
    return Path(path) if path else None


def _rewire_upstream(spec: BoxSpec, path: Path) -> str:
    """把上游盒子的下游改成新盒子（链是线性的，新盒子插在中间）

    **只换链条那一段，归档边留着**：
    一只 WMS 过程盒的下游有两个——链条上的下一只（`wms_ops_box`）
    和本域的汇点/档案盒（`wms_system_box`）。
    整个列表换掉 = 把归档边也删了 → 档案盒收不到这只盒子的产出。

    **哪些是归档边**：域的汇点（center）就是。
    从注册表现推（`domain_field_map`），不写死「wms_system_box」这种名字。
    """
    upstream = spec.insert_after
    if not path.exists():
        return (
            f"**没找到 {upstream} 的 definition.py** —— "
            f"请手工把它的 feeds_into 改成 [{spec.box_id!r}]"
        )
    try:
        from kanban.trigger import BOX_REGISTRY, domain_field_map
        center = domain_field_map(spec.domain).center
        old_targets = list(BOX_REGISTRY[upstream][0]().placement.feeds_into)
    except Exception:  # noqa: BLE001 - 仓外生成时不查
        center = None
        old_targets = list(spec.feeds_into)

    # 链条上的目标换成新盒子；指向汇点（归档）的边留着
    kept = [t for t in old_targets if t == center]
    new_targets = [spec.box_id] + kept

    text = path.read_text(encoding="utf-8")
    match = re.search(r"feeds_into=\[([^\]]*)\]", text)
    if not match:
        return (
            f"**{path.name} 里没找到 feeds_into=[...]** —— "
            f"请手工改成 feeds_into={new_targets!r}"
        )
    replacement = f"feeds_into={new_targets!r}"
    text = text[: match.start()] + replacement + text[match.end():]
    path.write_text(text, encoding="utf-8")
    return f"{path.name}：下游改成 {new_targets}（新盒子接手了链条那一段）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="造一只合规的新盒子（骨架：判据已声明，腿还没实现）",
    )
    parser.add_argument("box_id", help="盒子 id，必须以 _box 结尾，如 wms_picking_box")
    parser.add_argument("--domain", required=True, help="它属于哪个域，如 WMS")
    parser.add_argument("--display-name", default="", help="看板上显示的名字")
    parser.add_argument(
        "--value-stream", default="Software Delivery",
        help="价值流（看板按它配色；填一个看板没登记的名字会红）",
    )
    parser.add_argument("--role", default="", help="它在价值流里的角色")
    parser.add_argument(
        "--description", default="", help="这只盒子干什么（业务话）",
    )
    parser.add_argument(
        "--insert-after", default="",
        help="插在它后面（接手它的下游）——域里是一条线性链",
    )
    parser.add_argument(
        "--feeds-into", default="",
        help="它投给谁（逗号分隔的 box_id）——自己开一条链时用",
    )
    parser.add_argument(
        "--sink", action="store_true",
        help="它是本域的终点（没有下游）——与 --feeds-into 互斥；"
             "新域的第一只盒子用它",
    )
    parser.add_argument(
        "--out", default=None,
        help="生成到哪个目录（默认 = 仓里的 boxes/）",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="顺手把它注册进 kanban/trigger.py 和根 README",
    )
    args = parser.parse_args(argv)

    args.description = args.description or args.display_name or args.box_id
    if not args.role:
        args.role = f"{args.domain} 域的履约"

    try:
        spec = build_spec(args)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else BOXES_DIR
    written = write_box(spec, out_dir)
    print(f"✓ 生成了 {len(written)} 个文件 → {out_dir / spec.module}")
    for path in written:
        print(f"    {path.name}")

    if args.apply:
        if out_dir.resolve() != BOXES_DIR.resolve():
            print(
                "✗ --apply 只能配默认的 boxes/ 目录（注册进仓里的 trigger.py "
                "时，盒子必须在仓里）", file=sys.stderr,
            )
            return 2
        for note in apply_registration(
            spec,
            trigger_path=TRIGGER_PATH,
            readme_path=ROOT_README,
            pyproject_path=PYPROJECT_PATH,
        ):
            print(f"✓ {note}")
    else:
        print("\n下一步：把它注册进去（下面这段贴进 kanban/trigger.py 的三张表）：")
        print(registration_snippet(spec))
        print(
            "\n（或者重跑一次加 --apply，让脚手架替你贴）\n"
            "别忘了根 README 的盒子清单也要加一行——有守卫盯着。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
