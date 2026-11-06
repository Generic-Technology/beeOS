"""
core.models - beeBox Definition 顶层数据结构

按 [beebox-design.md §3.1 BeeBox definition] 定义：
  definition = 基础元信息 + 数据形状 + Fulfillment 5 维度 + 度量
  其中 task[].beeline_id + version 是机制层（履约程序 Procedure）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: **不许静默吞掉不认识的参数。**
#:
#: pydantic 的默认行为是 `extra="ignore"`：传一个它不认识的参数，
#: 它**照单全收地扔掉**，不报错也不提示。
#:
#: 后果实测过：票据盒和需求集盒都写了 `ResultDef(description=...)`，
#: 而当时 `ResultDef` 根本没有这个字段 → 那两句说明**从来没被存下来**。
#: 代码看着像写了，读的人以为产出有这句说明——实际产出里一个字都没有。
#:
#: 这正是「声明了却拿不到」那族病：**你以为声明了，其实没声明**。
#: 所以改成 `forbid`：不认识的参数当场报错，当场说得清是哪个字段。
#:
#: **为什么这件事值得动 core**：整个架构升级的主题就是
#: 「自相矛盾要在装配期就报错，别等运行时」。
#: 而「不认识的字段被静默吞掉」是同一族病在**模型层**的表现。
_FORBID_EXTRA = ConfigDict(extra="forbid")

# 媒体字段默认接受的 MIME 类型。
#
# 取交集：浏览器能解码出预览 ∩ 视觉接口接受 data URL。
# HEIC / AVIF 不在其中——Chrome 解不了 HEIC（预览直接是破图），
# 接口也拒（实测 400 invalid param: image data url media type
# ["image/heic"] not supported）。两头都不认的东西，放进来只会晚失败。
DEFAULT_MEDIA_ACCEPT: list[str] = [
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
]

# 判据的「不适用」哨兵。
#
# 为什么不能拿 None 顶：null 在这套系统里已经有一个明确的含义了——
# **模型没读到这一格**（小票上的 subtotal 读不出来就是 null，而且这是
# 个真问题，判据该红）。要是再用 null 表示「这张票压根没有这一栏」，
# 两个意思完全相反的状态就挤进同一个值里，谁也分不清。
#
# 实测踩坑（连续两轮，都是同一个病根——把某类票的形状当成所有票的形状）：
#   1. 零售 POS 小票是含税价，没有税额栏 → 「小计+税=实付」判据恒红
#   2. 餐馆结账单只印「品项/数量/金额」，没有单价栏 → 「数量×单价=金额」恒红
# 两次都是判据适用性问题，不是产出有问题。伪装成「通过」是撒谎（盖住症状），
# 硬判成「失败」也是撒谎（把用不上说成做错了）。所以给它第三个状态。
NOT_APPLICABLE = "n/a"


def is_not_applicable(value: Any) -> bool:
    """这个判据值是不是「不适用」"""
    return value == NOT_APPLICABLE

# ============================================================
# 数据形状：Schema 定义（definition 自带）
# ============================================================


class FieldDef(BaseModel):
    """字段定义（递归支持 object / array）"""

    model_config = _FORBID_EXTRA
    name: str
    type: str  # string / number / boolean / integer / object / array / enum / ref:<schema_id>
    required: bool = True
    description: Optional[str] = None
    # object 类型用 properties 嵌套
    properties: Optional[list["FieldDef"]] = None
    # array 类型用 items 描述元素
    items: Optional["FieldDef"] = None
    # enum 类型的合法取值表；不声明表示"暂不约束取值"（只校验类型）
    values: Optional[list[Any]] = None
    # 非空 = 这是个媒体字段，取值是 data URL / http URL。
    # 声明式，不靠字段名猜（photo / image / picture …）——
    # 按名字猜等于把业务约定写进内核，换个盒子就失效。
    # 投料口据此把文本框换成图片上传。
    media: Optional[Literal["image"]] = None
    # 媒体字段接受哪些 MIME 类型。不声明就用 DEFAULT_MEDIA_ACCEPT。
    #
    # 为什么要显式收窄：`image/*` 是个**浏览器**的说法，不是「能解码、
    # 能发出去」的说法。iPhone 拍的 .HEIC 在浏览器里 MIME 是 image/heic，
    # 放行 `image/*` 就一路带到 API 才被 400 拒（invalid param: image data
    # url media type ["image/heic"] not supported），用户等一轮才看到一句
    # 读不懂的 JSON。收窄在投料口拦，才对得起「投进来之前就说清楚」。
    media_accept: Optional[list[str]] = None

    def accepted_media_types(self) -> list[str]:
        """这个媒体字段实际接受的 MIME 类型（没声明就用平台默认集）"""
        return self.media_accept or DEFAULT_MEDIA_ACCEPT

    @model_validator(mode="after")
    def validate_type_structure(self) -> "FieldDef":
        if self.type == "object" and not self.properties:
            raise ValueError(f"object type field '{self.name}' must have properties")
        if self.type == "array" and not self.items:
            raise ValueError(f"array type field '{self.name}' must have items")
        return self


class SchemaDef(BaseModel):
    """业务 schema 定义（definition 内唯一）"""

    model_config = _FORBID_EXTRA
    id: str = Field(description="schema 唯一标识（definition 内唯一）")
    description: Optional[str] = None
    fields: list[FieldDef]


# ============================================================
# Fulfillment 维度 - Intent / Contract / Policy
# ============================================================


class TaskDef(BaseModel):
    """履约意图（Intent）：beeBox 接收什么 task"""

    model_config = _FORBID_EXTRA
    type: str = Field(description="task 类型标识")
    task_schema: str = Field(description="引用 schemas 块内的 schema id")
    beeline_id: str = Field(description="1:1 绑定的 beeline（履约程序 Procedure 实现）")
    beeline_version: int = Field(description="绑定的 beeline 具体 version")
    trigger: Optional[str] = Field(default=None, description="触发条件描述（业务化）")

    # 这只盒子吃不吃**业务自然语言**——「我要记一笔差旅」那种，
    # 而不是「target_base_url=http://…」那种。
    #
    # **为什么必须声明，不能从 schema 形状推**：
    # `kanban.trigger._payload_kind` 按「必填字段里有没有 array/object/ref」
    # 判断投料框该渲染成文本框还是 JSON 编辑器。那答的是**长什么样**，
    # 不是**接不接自然语言**——两件事。
    #
    # 推断错过一次：wms_system_box 的投料三个字段全是可选标量，
    # 形状上就是「一个文本框」，于是它被算成了吃自然语言的盒子，
    # 一句业务表述会被 `/api/structure` 默认路由进系统档案盒——
    # 那只盒子的活是去观测线上系统，不是解析人话。
    #
    # 形状推断还有一个更隐蔽的失败：给一个本来吃文本的盒子加一个
    # 可选的对象字段，推断结果不变；反过来给不吃文本的盒子去掉
    # 一个必填字段，它就突然开始吃文本了。**谁接自然语言是业务属性，
    # 改字段不该改这件事。**
    accepts_free_text: bool = Field(
        default=False,
        description="是否接收业务自然语言输入。默认否——猜错会让无关业务表述被路由进这只盒子",
    )

    # 投料里哪个字段是「本次履约处理的业务对象」的稳定标识。
    #
    # **为什么要有这个**：归档要按「工作单元」（一个需求 / 一次运维）分组变化，
    # 而履约记录里原本**没有任何字段能把一次履约关联到它处理的业务对象**：
    # `intent` 是 task type，`originator` 是触发源，业务标识只活在 artifact
    # payload 里、没被索引。分组只能靠事后从 payload 里刨——那不是索引，是碰运气。
    #
    # **为什么由盒子声明、而不是运行时按字段名猜**：
    # 「这个盒子拿哪个字段当业务对象」是业务约定，不是机制约定。
    # 靠名字猜（找 set_id / requirement_ref / xxx_id）等于把业务约定写进内核，
    # 加一只盒子就得改内核，而盒子带不走自己的约定。
    #
    # 声明了就必须真的给：投料缺这个字段 → 拒绝这次履约，不静默降级成空。
    # 否则会出现「履约跑了，但归档时归不到任何单元」——那条记录等于凭空消失。
    subject_ref_field: Optional[str] = Field(
        default=None,
        description=(
            "task_schema 里承载业务对象标识的字段名。留空表示这只盒子的履约"
            "不针对某个特定业务对象（如小票捕获、档案同步）"
        ),
    )

    # 业务对象标识取自**产出**里的哪个键。
    #
    # **为什么要有这个**：有些盒子**产生**业务对象（需求盒产出需求集），
    # 而不是消费它——它的 id 是这次履约的**结果**，投料里根本没有
    # （自由文本投料连字段名都不存在）。
    #
    # 于是这类盒子的履约记录归不到任何工作单元：工作单元是它**造出来**的，
    # 记录却不属于它。看板上就少了一只盒子——「需求集是需求盒产出的，
    # 可需求盒不在这个需求集的任务里」。这不是显示问题，是**归属丢了**。
    #
    # 之前需求盒的 definition 里就写着「靠产出的 set_id 认领」，
    # 但机制没实现——**声明了却拿不到**，所以那条记录 subject_ref 是空的。
    #
    # 声明了就必须真的产出：产出里没这个键 → 拒绝这次履约，
    # 不静默降级成空（那等于这条记录凭空消失）。
    subject_ref_from_result: Optional[str] = Field(
        default=None,
        description=(
            "产出（result）里哪个键是这只盒子**产生**的业务对象标识。"
            "留空表示业务对象标识不从产出里取"
        ),
    )

    # 标识藏在**对象里面**时，从对象的哪个键取。
    #
    # **为什么要有这个**：`ref:` 类型的字段装的是**整份对象**（上游盒子的产出），
    # 不是 id 字符串。于是「dev 盒子的 subject_ref 应该是哪个需求」，
    # 答案在设计包对象的 `set_id` 里，而不是在设计包本身。
    #
    # 之前只有一个 `subject_ref_field`，它只能指向「直接就是 id」的字段——
    # 于是 dev / testing 这两只「收对象」的盒子要么拿不到 subject_ref（归档里
    # 归不到任何工作单元），要么被迫把整份对象塞进去（subject_ref 变成
    # 一份对象的 repr）。两条都是「声明了却拿不到」。
    #
    # 声明了就必须是**对象**：字段值不是 dict → 拒绝；对象里没这个键 → 拒绝。
    # 不静默降级成空——「算了却看不见」。
    subject_ref_subfield: Optional[str] = Field(
        default=None,
        description=(
            "从 subject_ref_field 指向的对象里取哪个键当业务对象标识。"
            "留空表示该字段本身就是 id 字符串。声明了它，"
            "该字段的值就必须是对象，且对象里必须有这个键"
        ),
    )

    # 本次履约处理的业务对象是**哪一类**。
    #
    # **为什么不能只靠 subject_ref 的字符串判**：ops 盒子的 subject 是
    # 运维事件号（ops_task_id），其余盒子的是需求号（set_id）。两者可能
    # 撞字符串（`OPS-1` 和 `OPS-1`），不区分就分不出归档里这条记录
    # 是「这条需求的一次运维」还是「一个运维事件」。
    #
    # **由盒子声明，不从字符串前缀猜**：前缀猜等于把业务约定写进内核。
    # 声明了 subject_ref_field 就必须声明 subject_kind——否则「它关于谁」
    # 是靠猜的，而猜错时没人会发现。
    subject_kind: Optional[str] = Field(
        default=None,
        description=(
            "业务对象的种类（如 requirement / ops_event）。"
            "声明了 subject_ref_field 就必须声明它——"
            "不声明就等于让运行时去猜"
        ),
    )

    # 上面那条约束也适用于 subject_ref_from_result：
    # 「它关于哪一类业务对象」不能靠猜——产出里那个键的种类由盒子说了算。
    @model_validator(mode="after")
    def _subject_kind_required(self) -> "TaskDef":
        if (self.subject_ref_field or self.subject_ref_from_result) and not self.subject_kind:
            raise ValueError(
                "声明了 subject_ref_field / subject_ref_from_result 就必须声明 "
                "subject_kind——「它关于哪一类业务对象」不能靠猜"
            )
        return self


class AcceptanceRule(BaseModel):
    """履约合同（Contract）：验收标准（单次判据）

    metric 的取值可以是 bool，也可以是 NOT_APPLICABLE（"n/a"）表示
    **这条判据对本次产出用不上**——比如票上根本没印单价，却要求验
    「数量×单价=金额」。n/a 不算失败，但也**不算通过**：看板会把两者
    分开显示，免得「5 条过 + 1 条用不上」被读成「6 条全过」。
    """

    model_config = _FORBID_EXTRA
    metric: str
    op: Literal["gte", "lte", "eq", "in", "match"]
    value: Any


class ExceptionRule(BaseModel):
    """履约合同（Contract）：例外条款

    **为什么有 `reason`**（2026-11-04 补）：
    以前票据盒在这里写了 `reason="没读出任何商品行——照片太糊 / 不是小票 /
    拍的是二维码，别硬编"`，而**这个字段当时并不存在**——pydantic 默认
    **静默丢弃**不认识的参数。于是例外触发时，**没人看得到为什么**。

    例外条款是「这份合同说了什么情况下不交付」，
    而 `reason` 是「为什么是这个情况、该怎么办」——
    后者才是**人能拿去行动的东西**。丢了它，
    例外就只剩一个动作名（escalate / no_deliver / rollback），
    收到的人还得自己去猜。

    同一个根：`extra="forbid"`（见本文件末尾 `_FORBID_EXTRA`）
    让「写了却没存」当场变成报错。
    """

    model_config = _FORBID_EXTRA
    condition: str
    action: Literal["no_deliver", "rollback", "escalate"]
    reason: str = Field(
        default="",
        description="这条例外为什么会发生、收到的人该怎么做（人话）",
    )


class ResultDef(BaseModel):
    """履约合同（Contract）：业务结果定义

    **为什么有 `description`**（2026-11-04 补）：
    以前有两只盒子在这里写了 `description=...`（票据盒写「结构化小票 + 6 项
    机械校验证据」，需求集盒写「结构化业务需求集 + 4 项抽取验证证据」），
    而**这个字段当时并不存在**——pydantic 默认**静默丢弃**不认识的参数。
    于是作者写的那句话去哪了？**没去哪**：它从来没被存下来。

    这是「声明了却拿不到」那族病的典型形态：代码看着像写了，
    读的人以为产出有这句说明，实际上产出里一个字都没有。

    现在这个字段真的存在了，那两句说明也真的被带上了。
    而且**不许再静默吞**：`extra="forbid"` 让不认识的参数当场报错，
    而不是悄悄消失（见本文件末尾 `_FORBID_EXTRA`）。
    """

    model_config = _FORBID_EXTRA
    type: str = Field(description="业务结果类型标识")
    result_schema: str = Field(description="引用 schemas 块内的 schema id")
    description: str = Field(
        default="",
        description="这个产出是什么（人话一句）。给读产出的人看",
    )
    acceptance: list[AcceptanceRule]
    exceptions: list[ExceptionRule]

    # 这只盒子的腿实现到哪一步了。
    #
    # **为什么要有这个**：`evaluate_acceptance` 里是 `if not failed: ACCEPTED`。
    # 所以一只**零条判据**的盒子会被判成「全过」——那就是
    # 「看起来成功，其实什么也没验」的谎。加骨架盒时最容易踩这个坑。
    #
    # 于是骨架盒的做法反过来：**真声明判据**（它将来要验的东西），
    # 产出**不带**这些 metric → 全部判红 → REJECTED。
    # 「交不出东西」本身就是诚实的信号。
    #
    # 但「未实现」和「实现了但不达标」在看板上必须分得开，而且
    # 骨架盒**不该拖低整体验收率**——那跟拿开发期调试记录算验收率
    # 是同一个病。所以成熟度写在盒子上，由看板和聚合读它。
    maturity: Literal["live", "skeleton"] = Field(
        default="live",
        description=(
            "live = 腿已实现，acceptance 的读数有意义；"
            "skeleton = 结构已立、判据已声明、腿还没实现，"
            "读数不代表能力，不计入验收率"
        ),
    )

    # 产出的展示形态。盒子自己声明它的产出长什么样，而不是让看板去猜
    # （以前靠「result 里有没有 nodes 字段」猜，猜错就把一只盒子的产出
    #  渲染成另一只盒子的形状，看起来像渲染成功，其实是彻底错读）。
    # generic = 声明成通用 JSON 视图：没注册渲染器的产出落到这里，
    # 至少如实呈现，而不是掉进某个不相关的专用视图里。
    view: str = "generic"

    # **我这盒子的「投料条数」叫什么**（可空 = 不声明）
    #
    # **为什么要有它**（2026-11-01 实测撞出来的）：
    # runtime 的失败归因里有一条「投料是空的 → EMPTY_INPUT」，
    # 以前它是**写死一个业务字段名**的：`result.get("requirement_count") == 0`。
    # 一个**盒子专属的字段名**躺在 runtime 里 = 业务知识漏进内核：
    # runtime 不该知道哪只盒子管「需求」还是管「单据」。
    #
    # 现在由**盒子自己声明**：合同里写清「我的投料条数是哪个 metric」，
    # runtime 只按名字去读。**知识在盒子里，runtime 只按合同办事。**
    #
    # 不声明 = 不参与这条归因（runtime 会用另一条通用口径：
    # 所有判据的实测值都是空 → 也是「投料没东西」）。
    empty_input_metric: Optional[str] = Field(
        default=None,
        description="投料条数指标的名字（空 = 不声明）",
    )


class QueenAuthorization(BaseModel):
    """履约政策（Policy）：queen 自治授权档位"""

    model_config = _FORBID_EXTRA
    task_routing: Literal["allow", "observe_only", "off"] = "allow"
    exception_handling: Literal["allow", "observe_only", "off"] = "allow"
    continuous_improvement: Literal["allow", "observe_only", "off"] = "observe_only"


class QueenRules(BaseModel):
    """履约政策（Policy）：queen 自治规则（业务化规则，可选）"""

    model_config = _FORBID_EXTRA
    task_routing: Optional[dict[str, Any]] = None
    exception_handling: Optional[dict[str, Any]] = None
    continuous_improvement: Optional[dict[str, Any]] = None


class QueenDef(BaseModel):
    """履约政策（Policy）：beeBox queen 自治配置"""

    model_config = _FORBID_EXTRA
    authorization: QueenAuthorization
    rules: QueenRules


# ============================================================
# 履约度量（Metrics）
# ============================================================


class MetricDef(BaseModel):
    """度量定义（持续统计）"""

    model_config = _FORBID_EXTRA
    name: str
    definition: str
    target: float
    # 单位是声明的一部分，不是渲染时的约定：target 和实测 actual 共用同一个 unit，
    # 看板就不可能把「98.0%」和「98.0 秒」画成一样的东西。
    # pct = 百分数（0~100），s = 秒，count = 次。
    unit: Literal["pct", "s", "count"] = "pct"
    # 方向也是声明，不能从 unit 反推：同样是 pct，达成率越高越好，
    # 失败率越低越好。从单位猜方向会让「失败率 0%」被误判成没达标。
    direction: Literal["higher_is_better", "lower_is_better"] = "higher_is_better"


class MetricsDef(BaseModel):
    model_config = _FORBID_EXTRA
    quality: list[MetricDef] = Field(default_factory=list)
    latency: list[MetricDef] = Field(default_factory=list)
    cost: list[MetricDef] = Field(default_factory=list)


# **`PlacementDef` 已退休（2026-11-06）**
#
# 它当年存在的理由是：「调用方得能知道这只盒子接谁、投给谁——
# 接线是**描述**的一部分，不是看板长什么样」。
# 接线搬到**域声明**（`domains/<域>/`）之后，这个理由没了：
# 接线不再是「盒子自己的字段」，而是**域的事实**。
#
# 剩下的三样（`display_name` / `value_stream` / `role`）是**展示位**，
# 新协议里本来就在顶层，所以旧协议也跟着摊平——
# 留着个只剩三个字段的壳子，只会让「placement」这个名字继续骗人。
#
# **判据**：新协议（`core/scaffold.py` 的 `BoxDeclaration`）里
# 已经没有 `domain` / `feeds_into` / `feedback_into`，
# 旧协议这里也不再有 `placement`。


# ============================================================
# 顶层：BeeBox Definition
# ============================================================


class BeeBoxDefinition(BaseModel):
    """beeBox definition 顶层结构

    按 Fulfillment 5 维度 + 度量组织：
    - 基础元信息（id / version / description）
    - 数据形状（schemas）
    - 履约意图 Intent（task 列表）
    - 履约合同 Contract（result）
    - 履约政策 Policy（queen）
    - 履约度量 Metrics
    - 展示位（display_name / value_stream / role）

    机制层独立：
    - task[].beeline_id + version（不作为顶层独立字段）

    **接线不在这里**：盒子之间怎么连，是**域**的事实（`domains/<域>/`），
    不再是盒子自己的字段。旧协议里的 `placement` 已退休（2026-11-06）。
    """

    model_config = _FORBID_EXTRA
    id: str = Field(description="definition 唯一标识")
    version: int = Field(description="definition 版本（递增序列号）")
    description: str

    schemas: list[SchemaDef] = Field(default_factory=list)
    task: list[TaskDef]
    result: ResultDef
    queen: QueenDef
    metrics: MetricsDef
    # ---- 展示位（原来住在 placement 里，退休后摊平到顶层）----
    display_name: str = Field(default="", description="给人看的名字")
    value_stream: str = Field(default="", description="所属企业价值流")
    role: str = Field(default="", description="业务责任单元的职责")

    def get_schema(self, schema_id: str) -> Optional[SchemaDef]:
        """按 id 查找 schema"""
        for s in self.schemas:
            if s.id == schema_id:
                return s
        return None

    def get_task(self, task_type: str) -> Optional[TaskDef]:
        """按 type 查找 task"""
        for t in self.task:
            if t.type == task_type:
                return t
        return None

# ============================================================
# 域声明（Domain Declaration）
#
# **盒子之间关系的唯一事实源。**
#
# 这部分类型住 `core/models.py`，因为它们是「盒子和域共用的公共类型」——
# 盒子协议（`BeeBoxDefinition` / `BoxDeclaration`）和域协议
# （`FulfillmentScopeDeclaration`）都要用同一批形状类（`SchemaDef` / `FieldDef`），
# 装在一个模块里，依赖是单向的（域 → 盒子，不反向）。
#
# **域声明住在 `domains/<域>/`**——那才是业务事实的地方；
# 这里只定形状，不含任何业务词。
# ============================================================


class EdgeKind(str, Enum):
    """一条边是什么性质

    - `handoff` —— **数据交接**：产出交出去给下游验（**必须有通道**）
    - `archive` —— **归档归属**：这一环的产物进档案（档案盒是去线上读的，
      不收上游产出，所以**没有通道**）
    - `feedback` —— **闭环反馈**：上一轮结论回到起点变成新一轮输入
    """

    handoff = "handoff"
    archive = "archive"
    feedback = "feedback"


class Edge(BaseModel):
    """域里的一条边

    **判重键是「完整通道标识」**（`from_box` / `to_box` / `to_task` / `via` / `kind`），
    **不是 `(from, to, kind)`**——同一个来源可以填下游的**两个不同字段**，
    那是两条合法通道，在 `(from, to, kind)` 上却是同一条边。
    """

    model_config = _FORBID_EXTRA

    from_box: str = Field(description="上游盒子")
    to_box: str = Field(description="下游盒子")
    kind: EdgeKind = Field(description="这条边是什么性质")

    # **目标 task**：下游有多个 task 时，指明接的是哪一个入口。
    # 单 task 的盒子**可以省略**（唯一推断）——但协议里必须有这个位置，
    # 否则多 task 盒子来了，这条边就指不到具体入口。
    to_task: Optional[str] = Field(default=None, description="下游的哪个 task 入口")

    # **通道**：仅 handoff 有意义——下游投料里的哪个 `ref:` 字段。
    # 与 `no_channel` **互斥**（D12：反馈边可能本来就没通道）。
    via: Optional[str] = Field(default=None, description="下游投料里的哪个 ref 字段")
    no_channel: bool = Field(default=False, description="显式声明「这条边没有数据通道」")

    # 跨域入口边：from_box 在本域之外。
    external: bool = Field(default=False, description="from_box 在本域之外")

    @property
    def channel_key(self) -> tuple[str, str, str, str, str]:
        """判重键：完整通道标识"""
        return (
            self.from_box,
            self.to_box,
            self.to_task or "",
            self.via or "",
            self.kind.value,
        )


class RuleKind(str, Enum):
    """域级规则的种类——**全都是「完备性规则」**

    约束的是「**应该被声明、但可能还没被声明**的东西」，
    而不是「已经声明的东西的对不对」——后者是 `decl_checks` 的活。
    """

    #: 除豁免外，每只非汇点成员都要有一条 archive 边指向汇点
    all_archived = "all_archived"
    #: 环上每只非汇点成员都要接到下一环
    all_handoff = "all_handoff"
    #: 每条 handoff 边都要有能走通的通道
    each_handoff_has_channel = "each_handoff_has_channel"


class RuleExemption(BaseModel):
    """一条规则的豁免/禁止

    **要能表达「禁止」，不只「允许不做」**——
    「需求盒不得归档」是业务上的**禁止**，
    写成「允许豁免」就拦不住有人给它补一条归档边（D15）。
    """

    model_config = _FORBID_EXTRA

    box_id: str = Field(description="被豁免/禁止的盒子")
    reason: str = Field(description="**为什么**——结构化，不放注释里")
    mode: Literal["may", "must"] = Field(
        default="may",
        description="may=可以不做；must=不得做",
    )


class Rule(BaseModel):
    """一条域级规则"""

    model_config = _FORBID_EXTRA

    kind: RuleKind = Field(description="规则种类")
    exemptions: list[RuleExemption] = Field(default_factory=list, description="豁免/禁止")


class FulfillmentScopeDeclaration(BaseModel):
    """一个域的声明——**盒子之间关系的唯一事实源**

    **成员是声明的，不是推导的**（见 `docs/domain-declaration.md` §12.1）：
    「档案盒属于 WMS 域」是**业务判断**，靠接线推不出来。
    """

    model_config = _FORBID_EXTRA

    id: str = Field(description="域唯一标识")
    display_name: str = Field(description="给人看的名字")
    boxes: list[str] = Field(default_factory=list, description="本域有哪些盒子（唯一一份）")
    edges: list[Edge] = Field(default_factory=list, description="盒子之间怎么连（业务事实）")
    rules: list[Rule] = Field(default_factory=list, description="域级完备性规则（业务断言）")


@dataclass(frozen=True)
class BoxFacts:
    """一只盒子的事实（域装配器与接线投影共用一份）

    **只抄跟域声明有关的那几项**——这样 `core` 不必认识任何一只盒子，
    也不必认识注册表：注册表在 `kanban/trigger.py`，由它把盒子抄成这个
    视图再交给域装配器。
    """

    box_id: str
    #: 这只盒子有几个 task 入口（`to_task` 要在这里校验）
    task_types: tuple[str, ...] = ()
    #: task 类型 → 投料 schema id
    task_schemas: Mapping[str, str] = field(default_factory=dict)
    #: 产出 schema id
    result_schema: str = ""
    #: 这只盒子声明的全部 schema（id → 定义）
    schemas: Mapping[str, "SchemaDef"] = field(default_factory=dict)

    def task_schema_id(self, task_type: str | None) -> str | None:
        """某个 task 入口的投料 schema id

        `task_type` 为 None 时：**唯一推断**——只有一只 task 时能推出来，
        多只 task 时推不出来（得显式写 `to_task`）。
        """
        if not self.task_schemas:
            return None
        if task_type is not None:
            return self.task_schemas.get(task_type)
        if len(self.task_schemas) == 1:
            return next(iter(self.task_schemas.values()))
        return None

    def task_schema(self, task_type: str | None = None) -> Optional["SchemaDef"]:
        """某个 task 入口的投料形状"""
        sid = self.task_schema_id(task_type)
        return self.schemas.get(sid) if sid else None

    def result_schema_def(self) -> Optional["SchemaDef"]:
        """产出形状"""
        return self.schemas.get(self.result_schema) if self.result_schema else None
