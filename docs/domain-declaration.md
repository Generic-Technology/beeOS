# 域声明（Domain Declaration）：盒子之间关系的唯一事实源

> **这是设计稿，尚未实现。** 本文定的是**目标形态**，代码还没改。
> 实现以本文为准；实现未完成前，本文与代码不一致是**预期的**。
>
> **状态**：v0.1 草稿 · 待实现
> **日期**：2026-11-04
> **讨论背景**：BeeBox 标准化讨论（SOLID 分层 / 脚手架化 / 场图归属）
> **相关**：[architecture-upgrade.md](./architecture-upgrade.md)（盒子脚手架化定稿）·
> [beebox-design.md](./beebox-design.md)（主设计稿）· [beeline.md](./beeline.md)
>
> **路径写法约定**：本文里 `core/<domain>.py` 这种**尖括号包住名字**的写法，
> 表示**计划中、尚未创建**的文件；不带尖括号的都是**现在真实存在**的路径。

---

## 0. 一句话

把「盒子之间怎么连」这份事实，从**每只盒子各写一遍**搬到**每个域写一遍**。

盒子不再声明自己接谁、交谁、属于哪个域；域声明成为盒子之间关系的**唯一事实源**；
场图（接线视图）退化为**派生层**——算法住 `core/`，事实归域声明。

---

## 1. 现状（2026-11-04 实测，基于 commit `fdc04d0`）

### 1.1 接线声明现状

WMS 域（`placement.feeds_into`，生产者的交接声明）：

```
wms_requirement → [wms_design]
wms_design      → [wms_development, wms_system]
wms_development → [wms_testing,    wms_system]
wms_testing     → [wms_ops,        wms_system]
wms_ops         → [wms_analysis,   wms_system]
wms_analysis    → [wms_system]
wms_system      → []
```

Learning 域：

```
learning_math    → [learning_growth]
learning_chinese → [learning_growth]
learning_english → [learning_growth]
learning_growth  → []
```

`receipt` → `[]`（未归域）

### 1.2 三个事实

| # | 事实 | 证据 |
|---|---|---|
| F1 | **一份能走通的边分两半说**：生产者说「谁交给谁」（`feeds_into`），消费者说「接什么形状」（`B.task_schema` 里的 `ref:` 通道）。**交界处没有任何东西对账。** | `runtime/handoff.py` 只认 `ref:` 通道；`core/field_map.py` 只认 `feeds_into` |
| F2 | **11 条声明的边里只有 5 条走得通。** 域内 5 条交接边**全部有**真通道；5 条投档案盒的边**全部没有**；1 条闭环反馈边**也没有**。 | 档案盒投料 `schema_system_probe_input` = `target_base_url` / `timeout_seconds` / `readonly_endpoints`，`ref:` 通道数 = 0；它是**去线上读**的盒子，不收上游产出。实测见附录 B |
| F3 | **悬空接线被静默丢弃**：`derive_field_map` 里 `edges = [... if t in idset]`，指向不存在盒子的 `feeds_into` 直接消失，无告警。 | 实测：`a → ghost_box` 得到 `reason_code=star`、`reason=''`，那条边人间蒸发 |

### 1.3 域级意图被复制了 5 份，而它的**例外**没有家

「五个生命周期盒的产物都沉淀进系统档案」是一条**域级意图**，现在它被写成
**5 份盒子级声明**（design / development / testing / ops / analysis 各写一遍 `feeds_into`）。

**而需求盒不在其中。**

> 这看起来像「复制漏了 1 份」——**不是**。需求盒的 `definition.py` 里写着理由：
>
> > 「需求盒产出的是系统档案的**输入**（读基线：这份系统该是什么样），
> > 不是往档案里加东西。声明它「投给」系统盒，等于说档案是需求盒的下游，
> > 而真实关系正好相反。」
>
> **它是主动不写那条边的。**

**但这条「主动不写」没有任何机制守着。** 它住在**散文**里（盒子文件头的注释），
不是声明。后果：

> 如果有人**善意地**给需求盒补上 `feeds_into=[..., "wms_system_box"]`，
> 域图**照样正常渲染**（汇点还是唯一，环序不变），
> 只是 requirement 成了 `feeds_center` 的一员——
> 而「五个生命周期盒都往系统盒汇」这句话，在数据上不再成立了。
> **没有任何东西会报错。**

需求盒自己的注释已经点破了这件事：

> 「它让 requirement 也成了 feeds_center 的一员，于是
> 『五个生命周期盒都往系统盒汇』这句话在数据上不再成立，
> 而拓扑图是照着这句话画的。」

**这就是「域级意图复制成 N 份」的真正代价**：不是漏写了一份，
而是**这条规则的边界靠「没人补那条边」维持着**。
加一只新的生命周期盒时，它会不会被归档、域图还是不是原来那张图，
**全靠人记得**。

> 域声明要解决的就是这件事：**让规则的例外有名有姓**（§5.2）。

### 1.4 域在仓里没有「家」

- `placement.domain` 只是个**字符串标签**，用来筛盒子、画场图
- `boxes/` 下只有盒子，**没有任何域级目录**
- 域级业务规则（「本域的汇点必须是系统档案盒」）**无处可写**
- `core/` 里 `domain` 只是一个字段名

### 1.5 `domain` 一词在仓里指两个东西

| 出处 | 含义 |
|---|---|
| `placement.domain = "WMS"` | **组织分组标签**（筛选 / 场图用） |
| `boxes/wms_design/domain.py` | **DDD 领域本体**（WMS 有 SKU / 货主 / 库位 / 库存 / 批次 / 波次 / 盘点这套概念） |

同一个词「WMS」，一个是「你属于 WMS 这个组」，一个是「WMS 这个领域长什么样」。
`core/models.py` 把两者揉进一句：「domain 答『属于哪个业务领域』」。

---

## 2. 核心判断

### 2.1 「盒子属于哪个域」和「盒子交给谁」是同一件事的两半

它们都是**盒子在域里的位置**。拆开放，就是两份事实。

所以：**一起搬进域声明。盒子身上不再留任何域信息。**

推论：`placement.domain` / `placement.feeds_into` / `placement.feedback_into`
**三个字段全部退休**。

### 2.2 「一只盒子属于多个域」这把钥匙

成员归属若留在盒子上，为了支持多域，`domain` 得变成 `list[str]`——
那还是「盒子身上的域信息」，割裂没消除，只是换了形态。

**只有把成员搬进域声明，多域才天然成立**：盒子出现在 N 个域的 `members` 里，
就属于这 N 个域。

> 这条需求反过来否定了「成员留在盒子上」。

### 2.3 边是业务内容，算法不是

「谁交给谁」是**关于这个领域的断言**，不是关于这只盒子的断言。

盒子能说「我是 WMS 需求捕获盒，我产出需求集包」——那是**它自己的**业务；
但「它交给设计盒」是 **WMS 这个领域**的业务。

所以：

| | 住哪 | 为什么 |
|---|---|---|
| **边**（`Edge`）| **`domains/<域>/`** | 含业务断言（「开发盒」「系统档案盒」是业务词）|
| **接线算法**（`derive_wiring`）| **`core/`** | 零业务词；12 个域共用一份，放业务侧就是 12 份拷贝，必然漂 |

**这正是「域」这个实体存在的意义**——它让「领域怎么流转」这个断言有了归属。

### 2.4 场图（接线视图）是派生层，不是事实源

`BeeBoxMap` 的 12 个字段**全部**从各盒子的 `placement` 派生，
**没有一个字段只属于它自己**。

所以它**不需要被管理**（没有需要协调的变更），也**不需要脚手架**（没有声明可装配）。

它只是**照片**。照片不拥有事实。

> 改完之后，`WiringView` 里的**边本身也不在里面**——边在域声明里。
> 它输出的是**拓扑结构**：汇点是谁、环序怎么排、形状是环还是放射。

### 2.5 runtime 不执行域声明

runtime 执行的是**一次履约**：一只盒子自己的 beeline + handler 表。

- 域描述的是**盒子之间**的关系，那是**两次履约之间**的事
- runtime 不认识注册表（见 `runtime/` 的 import 清单：只依赖 `core.beeline_models` /
  `core.conditions` / `core.models`）
- 域的形状（ring / star）对 runtime 无意义——一次履约只跑一只盒子

**依赖方向允许 ≠ 必须使用。** `core/field_map.py` 现在就在 core 里，runtime 一样不碰它。

---

## 3. 协议

### 3.1 域声明（core，形状）

```python
# core/<domain>.py（新建）
class EdgeKind(str, Enum):
    handoff = "handoff"   # 数据交接：产出交出去给下游验
    archive = "archive"   # 归档归属：这一环的产物进档案（不走通道）
    feedback = "feedback" # 闭环反馈：上一轮结论回到起点变成新一轮输入

class Edge(BaseModel):
    from_box: str
    to_box: str
    kind: EdgeKind
    via: Optional[str] = None      # 仅 handoff 有意义：下游投料里的哪个 ref 字段
    external: bool = False          # from_box 在本域之外（跨域入口边）

class RuleKind(str, Enum):
    # **下面三条都是「完备性规则」**：它们约束的是
    # 「**应该被声明、但可能还没被声明**的东西」。
    # 计数期望（成员数 == N）**不做**——它数的是规则自己，永远绿（§5.3）。
    all_archived = "all_archived"                 # 每只成员都有 archive 边指向汇点（除豁免）
    all_handoff = "all_handoff"                   # 环上每只非汇点成员都接到下一环
    each_handoff_has_channel = "each_handoff_has_channel"  # 每条 handoff 边都有通道

class Rule(BaseModel):
    kind: RuleKind
    # **豁免**：这些成员不参与这条规则。
    # 「为什么豁免」写在声明处的注释里——它现在住在盒子文件头的散文里，
    # 域声明给了它一个正经的家（§5.3）。
    except_: list[str] = []

class DomainDeclaration(BaseModel):
    id: str                          # "wms"
    display_name: str
    members: list[str]               # 本域有哪些盒子（唯一一份成员表）
    edges: list[Edge]                # 盒子之间怎么连（**业务事实**）
    rules: list[Rule] = []           # 域级完备性规则（**业务断言**，判据是机制）

class AssembledDomain(BaseModel):
    """装好的域——与 ``AssembledBox`` 对称"""
    declaration: DomainDeclaration   # 装好的域声明
    wiring: WiringView               # 拓扑投影（派生，见 core/<wiring>.py）
    rules_reading: list[Any] = []    # 域级规则读数（第二阶段才有）
    report: AssemblyReport           # 装配期结论：装好了没、有什么问题
```

**core 只定形状，不含业务词。** 具体值（`"wms_system"`、`"via=design_package_ref"`）
在 `domains/<域>/` 侧填。

**边是业务内容，算法不是** —— 这是本方案最容易含糊的一处：

| | 是什么 | 含业务断言吗 | 住哪 |
|---|---|---|---|
| **边**（`Edge`）| 「WMS 域里设计盒交给开发盒」| **含** ——「开发盒」「系统档案盒」是业务词 | **`domains/<域>/`** |
| **接线算法**（`derive_wiring`）| 「有 N 个汇点时画不出图」「环上相邻必须有交接边」| 不含 —— 零业务词 | **`core/`** |

为什么边是业务内容：**「谁交给谁」是关于这个领域的断言，不是关于这只盒子的断言。**
盒子能说「我是 WMS 需求捕获盒，我产出需求集包」——那是**它自己的**业务；
但「它交给设计盒」是 **WMS 这个领域**的业务：需求捕获与设计之间那条交接，是领域定的。

> 这正是「域」这个实体存在的意义——它让「领域怎么流转」这个断言有了归属。

而接线算法必须留 core，理由与 `core/models.py` 的 `SchemaDef` 形状 +
`runtime/contract.py` 的校验算法完全同构：

```
boxes/wms_design/schemas.py   ← schema_business_design_package（业务事实）
core/models.py                ← SchemaDef（形状）
runtime/contract.py           ← 校验算法（机制）

domains/<域>/<domain>.py      ← Edge(...) / DomainDeclaration（业务事实）
core/<domain>.py                ← DomainDeclaration 的形状 + assemble_domain（机制）
core/<wiring>.py              ← 接线算法（机制）
```

**若接线算法住 `domains/`**，12 个域就要 12 份拷贝——必然漂。
现在它在 `core/field_map.py` 已经是这个位置，改名只是让它更准。

### 3.2 域声明（业务侧，一份样例）

```python
# domains/<wms>/<domain>.py（新建）
DOMAIN = DomainDeclaration(
    id="wms",
    display_name="WMS 仓储",
    members=[
        "wms_requirement", "wms_design", "wms_development",
        "wms_testing", "wms_ops", "wms_analysis", "wms_system",
    ],
    edges=[
        Edge(from_box="wms_requirement", to_box="wms_design",
             kind=EdgeKind.handoff, via="requirement_set_ref"),
        Edge(from_box="wms_design", to_box="wms_development",
             kind=EdgeKind.handoff, via="design_package_ref"),
        # 归档归属：不是交接，是「这一环的产物进档案」——不需要 via
        Edge(from_box="wms_design", to_box="wms_system", kind=EdgeKind.archive),
        # 闭环边：单独一种，进拓扑排序会成环
        Edge(from_box="wms_analysis", to_box="wms_requirement", kind=EdgeKind.feedback),
    ],
)
```

### 3.3 三条边界规则

**① `via` 只说「哪个字段」，不说「什么形状」。**

形状是**下游自己声明的**（`B/schemas.py` 里的 `requirement_set_ref: ref:schema_requirement_set_input`）。
域说「通过哪个字段」，schema 说「那个字段是什么形状」——**各说一半，不重复**。

若域声明里也写上 shape，就与 `B/schemas.py` 变成同一事实两份。

**② 域声明里不列成员以外的东西。**

成员 = `members`（唯一一份）；域级规则 = 另加 `rules`（若有）；
**接线 = `edges`**。三者都不许在别处再写一遍。

**③ 一条边出现在域 D 里，当且仅当两端都是 D 的成员**（`external=True` 的入口边除外）。

一只盒子属于 N 个域，就带着它的边在 N 个域里各出现一次。
**这不是重复，是两个视角各自正确。**

---

## 4. 派生层：接线投影怎么改

**`core/field_map.py` 改名 `core/<wiring>.py`，继续存在，但输入变了**：

| | 现在 | 之后 |
|---|---|---|
| 输入 | `Sequence[PlacementView]`（手工抄的视图）| `DomainDeclaration` 的 `members` + `edges` |
| 事实源 | 各盒子的 `placement` | **域声明**（`domains/<域>/`）|
| 位置 | `core/field_map.py` | `core/<wiring>.py`（仍是 core 的派生层）|
| 输出 | `BeeBoxMap` | `WiringView`（`AssembledDomain` 的一个字段）|

**它从「认 placement」改成「认域声明」——仍然不拥有事实。**

**为什么叫 `wiring` 而不是 `field_map`**：它派生的是**接线**，不是「场」。
「场图」是那个**渲染出来的东西**（画布上那张图），不是这个**算法**。

**`WiringView` 里没有边本身**——边在域声明里。它输出的是**拓扑结构**：
汇点是谁、环序怎么排、形状是环还是放射、以及**边按 `kind` 分类后的分布**。

命名对照（避免「Map」既指事实又指视图）：

| 现在 | 之后 |
|---|---|
| `core/field_map.py` | **`core/<wiring>.py`** |
| `derive_field_map()` | `derive_wiring()` |
| `field_map_of_scope()` | `wiring_of_scope()` |
| `BeeBoxMap` | `WiringView` |
| `PlacementView` | `BoxFacts`（成员归属搬走后，它抄的就是「这只盒子的事实」）|
| 前端 label `... · BeeBox Map` | `... · 场图` |
| 域声明（新） | **域**（Domain）——唯一的事实源 |

**改名牵连 5 个文件 + 1 个测试文件**：
`kanban/trigger.py`(12 处) / `kanban/web.py`(7) / `scripts/new_box.py`(8) /
`tests/test_learning_boxes.py`(3) / `tests/test_docs_match_code.py`(1) /
`tests/test_field_map.py`(33 → 改名 `tests/<wiring>.py`)

> 趁现在改最便宜——以后每次动这三个 import 点都是还债。

---

## 5. 装配期校验（域级）

与 `core/scaffold.py` 的 C1–C12 同构，域级校验同样在装配期报错。

**判定机制共用一份**：这些校验应加成 `core/<decl_checks>.py` 里的
`check_domain_*` 系列（那儿已经是「声明自洽判定的唯一真源，装配器与体检台共用一份」），
**不在 `core/<domain>.py` 里另写一套判定**。

| # | 校验 | 不这么做会怎样 |
|---|---|---|
| D1 | 每个 `members` 里的盒子都在注册表里存在 | 悬空成员 → 域的成员表里有个不存在的盒子 |
| D2 | 每条边的两端都在 `members` 里（或声明为 `external`）| 边指向域外的盒子却没说明 → 拓扑漏边 |
| D3a | `handoff` 边必须有 `via`，且 `via` 必须是下游 `task_schema` 里的 `ref:` 字段 | 声明了交接却没有通道 → **搬的时候没做 `kind` 分类，就会被这条全拦下** |
| D3b | `via` 指向的 `ref:` schema 能在**下游自己的** schemas 里解析 | 通道指向一个不存在的形状 → 交接时才发现 |
| D3c | **上游的产出能通过 `via` 指向的那份形状的校验** | 通道名对、形状对不上 → 交接时才发现，错误离现场隔着两层 |
| D4 | `feedback` 边不进拓扑排序 | 闭环边混进主干 → 排序遇环 → **静默按原顺序收尾** |
| D5 | 汇点唯一 | 多个汇点 → 圆心算不出来 |
| D6 | 主干无环 | 环上顺序变成任意的，图照常渲染，结构是错的 |
| D7 | 每条边两端的 `kind` 合法（`archive` 不得有 `via`）| 归档归属被当成数据交接 |
| D8 | 同一对盒子不得有两条同 `kind` 的边 | 重复声明，含义不明 |
| D9 | 每只已注册盒子要么属于至少一个域，要么显式声明 `standalone` | 忘了归域 → 盒子不在任何域里，却没人知道 |
| D10 | 每条边的两端都属于本域（`external=True` 除外）| 跨域边混进本域 → 拓扑算错 |
| D11 | **每条 `handoff` 边都必须有通道**（D3a/b/c 三级全过）| 声明了交接却走不通 → **现在 11 条声明边里有 6 条没有通道**（5 条投档案盒 + 1 条闭环反馈），搬的时候不做 `kind` 分类就会被这条全拦下 |
| D12 | 每条 `feedback` 边**要么有通道，要么显式声明 `no_channel`** | 反馈边到底通不通，现在看不出来 |

**D11 是这套校验里最有价值的一条**——它把「声明了交接但没有通道」从**静默**变成**装配期就拒**。

**D1–D12 与 C1–C12 的分工**：C* 验「一只盒子自不自洽」（core 不认识注册表，只查自己）；
D* 验「一个域的结构对不对」（需要成员信息，由调用方从注册表收集后传入）。

### 5.1 D3 为什么必须拆成三级（实测逼出来的）

原以为 D3 一条就够：「`via` 是下游投料里的 `ref:` 字段」。**实测证明不够**，而且差得很远。

实测（`requirement → design` 这条边）：

```
下游 design 声明的通道：requirement_set_ref: ref:schema_requirement_set_input
上游 requirement 的产出：schema_requirement_set_package
                         ↑ id 根本不一样
```

而且**形状不是相等，是包含**（实测，2026-11-05）：

| | 上游产出 `schema_requirement_set_package` | 下游声明 `schema_requirement_set_input` |
|---|---|---|
| 字段总数 | **14** | **5** |
| 必填 | **12** | **2**（`set_id` / `requirements`）|
| 可选 | 2（`context` / `document`）| 3（`business_goal` / `context` / `source_sentences`）|

**下游的 5 个字段，上游全都有**；下游只把其中 3 个从必填放宽成可选，
另外 9 个上游字段（`nodes` / `edges` / `requirement_count` / 4 条判据 / …）**下游根本不要**。

**下游声明的是「它自己要的那一小块」，不是上游的全部。** 这是「形状由消费者声明」的真正含义：
消费者**不是复制上游的 schema**，而是声明「我接你的哪一部分、宽松到什么程度」。

**所以 D3c 必须做「实跑校验」，不能做「id 相等」也不能做「形状相等」**：

> 判据 = **用 `runtime.contract.validate_payload` 把上游产出喂给下游声明的那份形状**，
> 零错误才算通道存在。

这跟仓里已经踩过的坑是同一条纪律（`core/conditions.py` 的 docstring）：

> 「它是协议，不是执行……两处各写一份 = 两份事实 = 漂。」

**所以 D3c 必须复用 `runtime.contract.validate_payload` 那一份，不能另写一套形状比较。**
而它现在住在 `runtime/`，`core` 不该反向依赖 `runtime`（`runtime → core` 是既有的干净方向，
`core/scaffold.py` 里那句 `from runtime.runner import HandlerRunner` 是**文档字符串里的示例代码**，不是真 import）。

**→ 倾向：把 `validate_payload` 下沉到 `core/<contract>.py`，`runtime/contract.py` 从 core import。**

依据同 `core/conditions.py`：**它是协议，不是执行**。
现在它住在 `runtime/` 只因为 runtime 是第一个用它的人；域装配期也要用它，且必须是同一份。
（影响面：`runtime/contract.py` + `runtime/handoff.py` + `kanban/trigger.py` +
`boxes/wms_requirement/runner.py` + 约 12 处测试引用——**下沉要连带把测试的 import 一起改**。）

### 5.2 域能机械测的量（WMS 实测清单，2026-11-05）

待定项「域级规则第一版要不要有」的前置问题是「**域能测什么**」。
下表是**实测出来的**答案——每一行都是能机械判定、且**不需要 core 认识任何业务词**的量：

| # | 量 | WMS 实测值 | 怎么测 | 能不能失败 |
|---|---|---|---|---|
| M1 | 域成员数 | **7** | `members` 长度 | ❌ 自证（见下）|
| M2 | 汇点数 | **1**（`wms_system_box`）| 出度 0 且入度 > 0 | ✅ 能（多汇点 / 无汇点）|
| M3 | 接线形状 | **ring** | 域内有交接边 → 环；无交接边 → 放射 | ✅ 能 |
| M4 | 环序 | requirement→design→development→testing→ops→analysis | 交接边拓扑序 | ✅ 能（成环）|
| M5 | 环上相邻必有交接边 | **成立** | 逐对查边 | ✅ 能 |
| M6 | 域内交接边数 | **5** | `kind=handoff` | ❌ 自证 |
| M7 | **交接边有通道数** | **5 / 5** | D3a/b/c 实跑 | ✅ 能 |
| M8 | 投汇点边数（`archive`）| **5** | `kind=archive` | ❌ 自证 |
| M9 | **归档覆盖** | **5 / 6**（6 只非汇点成员里，5 只归档了；剩下 1 只是**有意豁免**）| 有 `archive` 边指向汇点的成员 | ✅ **能**（少声明一条就抓出来）|
| M10 | 闭环反馈边数 | **1** | `kind=feedback` | ❌ 自证 |
| M11 | 反馈边有通道数 | **0 / 1** | 同 M7 | ✅ 能 |
| M12 | 悬空边（指向不存在的盒子）| **0** | 边端点 vs 注册表 | ✅ 能（现在**静默丢弃**）|
| M13 | 跨域边（端点不在本域）| **0** | 边端点 vs `members` | ✅ 能 |

> **M7 / M11 是这批量里最硬的两个**——它们是**唯一**能发现「声明了交接但走不通」的量，
> 而今天没有任何东西在看它们。

### 5.3 域级规则第一版：**完备性规则**，不是计数期望

**关键区分：一条规则能不能失败，取决于它约束的是「已经声明的东西」还是「应该被声明却没被声明的东西」。**

| 规则形态 | 例 | 能不能失败 |
|---|---|---|
| **计数期望** | 「本域成员数 == 7」| ❌ **永远绿**——成员表就是那 7 个，规则数的是自己 |
| **完备性规则** | 「除豁免外，每只成员都要有一条 `archive` 边指向汇点」| ✅ **能**——少声明一条就抓出来 |

> 这跟「部署计数不能自证」是同一条：**自证的检查等于没有检查。**
> 所以域级规则第一版**不做计数期望**（M1 / M6 / M8 / M10 那四行不写进规则）。

**第一版只做三种完备性规则，全部只引用域声明 + 注册表：**

| kind | 判据 | WMS 应读数 | 现在会抓出谁 |
|---|---|---|---|
| `all_archived` | 除豁免外，每只**非汇点**成员都有 `archive` 边指向汇点 | 6 只非汇点成员 − **豁免 1** = 该归档 **5** 只，实际 **5** 只 → **5/5 通过** | **新增的盒子忘了归档** |
| `all_handoff` | 环序上每只非汇点成员都有 `handoff` 边接到下一环 | **5 / 5** | **新增的盒子忘了接进链** |
| `each_handoff_has_channel` | 每条 `handoff` 边都有通道（D3a/b/c）| **5 / 5** | **声明了交接却走不通** |

**为什么规则里必须带「豁免」**：需求盒就是豁免——它**有意**不归档（§1.3）。
而豁免的理由现在住在**需求盒文件头的注释里**（散文）。
域声明给了它一个正经的家：

```python
# domains/<wms>/<domain>.py
rules=[
    # 本域的过程产物都沉淀进系统档案。
    # 需求盒是**有意的例外**：它产出的是系统档案的**输入**（读基线：
    # 这份系统该是什么样），不是往档案里加东西——
    # 声明它「投给」档案盒，等于说档案是需求盒的下游，而真实关系正好相反。
    Rule(kind="all_archived", except_=["wms_requirement"]),
]
```

**「为什么需求盒不归档」这句话，从此住在域级声明里，而不是某只盒子的注释里。**

**规则的表达原则**（与 §3.3 同源）：

> **域级规则只能引用「域声明里已有的东西」+「注册表」+「盒子的 schema」。**
> 想引用「某只盒子是什么角色」（例如「汇点必须是系统档案盒」），就得先让盒子声明角色——
> 那是**另一件事**（盒子的业务分类），第一版不碰。
> **core 判不了的，就别写进规则。**

### 5.4 域级规则的可读数（`rules_reading`）

`AssembledDomain.rules_reading` 现在有位置了——它是**规则的读数**，不是规则的定义：

```
all_archived              5/5   (6 只非汇点成员，豁免 1: wms_requirement)
all_handoff               5/5
each_handoff_has_channel  5/5
```

**读数要能在装配期就出来**（不是等第一次履约才知道），
所以装配期就得能**验通道** → 又回到 §5.1 的 D3c → 又回到「`validate_payload` 下沉 core」。

### 5.5 实测推翻了一条假设：**规则不能只按「环」写**

Learning 域（4 只盒子：math / chinese / english → growth）是**放射**（star），
实测它的三条边（2026-11-05）：

| 边 | 通道 | 判定 |
|---|---|---|
| `learning_math` → `learning_growth` | `subject_result: ref:schema_learning_subject_result` | ✅ 有通道 |
| `learning_chinese` → `learning_growth` | 同上 | ✅ |
| `learning_english` → `learning_growth` | 同上 | ✅ |

**三条边全是真交接（handoff），而且共用同一个通道字段。**
三科的产出是三个不同的 schema（`schema_learning_math_output` / `_chinese_` / `_english_`），
但**字段完全相同**（10 个，名字和类型都一样），
growth 声明的接料形状 `schema_learning_subject_result` 是那 10 个字段的**子集**（9 个）。

**于是 §5.3 那三条规则，Learning 域里有两条不适用**：

| 规则 | ring 域（WMS） | star 域（Learning） |
|---|---|---|
| `all_archived` | 适用 | **不适用**——源盒的边是 handoff，不是 archive |
| `all_handoff` | 适用（环序上接下一环）| **不适用**——**没有环序** |
| `each_handoff_has_channel` | 适用 | **适用**（3/3 通过）|

> **「汇点」这个词盖了两种东西。**
>
> | 域 | 汇点 | 汇点收不收上游产出 |
> |---|---|---|
> | WMS | `wms_system_box`（档案盒）| **不收**——它是去线上读的，所以那 5 条是 `archive` 边、**没有通道** |
> | Learning | `learning_growth_box`（成长记录）| **收**——所以那 3 条是 `handoff` 边、**有通道** |
>
> **「汇点」只是拓扑位置（域内没有下游的那只），不是角色。**
> 决定一条边是 `handoff` 还是 `archive` 的**不是它指向不指向汇点，而是「数据真的流过去吗」**——
> 这正是 `kind` 存在的理由：**kind 才是事实，汇点只是拓扑。**
>
> **所以规则不能挂在「汇点/非汇点」上，要挂在 `kind` 上。**

### 5.6 D3c 验的是「声明层对声明层」，不是「实跑过一次」

装配期**没有上游的产出**（产出要等真履约）。所以 D3c 实际验的是：

> **上游声明的产出形状，能不能满足下游声明的接料形状。**

**这一层是够的**，而且是 sound 的：
artifact 必然符合上游自己声明的 `result_schema`（那是上游 acceptance 的责任），
而 D3c 验的正是「符合上游声明的 artifact，能不能满足下游的接料声明」。
所以「合成样例能过」≈「任何合规产出都能过」。

**但 D3c 验不出「上游真的产出了那个形状」**——那是**上游自己的 acceptance** 管的，
不归域声明。**域声明管的是「声明之间对不对得上」，不是「现场跑没跑通」。**

**因此 D3c 的实现就是「拿两份声明做结构兼容判定」**，
而**最不漂的做法是复用 `runtime.contract.validate_payload` 那一份**——
另写一套结构比较 = 第二份事实 = 漂（同 `core/conditions.py` 的教训）。
这仍然是待定⑥（`validate_payload` 下沉 `core/<contract>.py`）。

---



### 6.1 跨域边：两端不共享任何域

例：「Learning 域的盒子要用票据盒（未归域）的产出」——这条边**不属于 WMS 也不属于 Learning**。

> **实测修正（2026-11-05）**：**现在没有任何盒子用票据盒的产出。**
> `receipt_capture_box` 没有任何一条 `feeds_into` / `feedback_into` 指向它，
> 也没有任何盒子的投料里有 `ref:schema_receipt_parsed` 通道——
> **它的 20 个字段的产出完全悬空。**
> 所以这只是个**假想的例子**，不是现状的描述。
> 真正的现状是：**它是个孤儿盒子**（产出没人接），见 §6.2。

**解法**：域可以声明**外部边**，挂在**接收方**的域里：

```python
Edge(from_box="receipt_capture", to_box="wms_requirement",
     kind=EdgeKind.handoff, via="receipt_ref", external=True)
```

这比现在 `scope_field_map` 的「跨域整体无图，给一句原因」更细：
**不是「跨域整体无图」，而是「跨域的那一条边挂在接收方域里」。**

### 6.2 「未归域」的盒子（`receipt` 现在就是）

它就是「不出现在任何域的 `members` 里」。但要区分**「有意的独立」**和**「忘了加」**。

> **实测（2026-11-05）**：`receipt_capture_box` 不但未归域，
> 而且**是个孤儿**——没人投给它，它那份 20 字段的产出**也没有任何下游能接**。
> 所以「有意的独立」这个解释在这里**站不住**：一只盒子产出完整的东西却没人接，
> 要么是它该进某个域，要么是它该有下游，要么是它**根本不该存在**。
> **这三条是三种不同的决定，域声明不能替它做。**

**解法**：盒子自己声明 `standalone: bool`（这是「我不参与任何域」的**业务属性**，
不是域信息，不违反「盒子不留域信息」）。

### 6.3 成员清单要手工维护

域声明的 `members` 是一份需要维护的清单——**加盒子忘了加进域，它就不在域里**。

这不是「第二份事实」（盒子已经不声明 domain 了），但**必须有守卫兜底**：D1 + D9。

**脚手架知道要加哪个域** → `scripts/new_box.py` 可以直接改域声明，不必人去记。

---

## 7. 迁移顺序

> **前置**：等 6 只 WMS 盒子迁到新协议（`BoxDeclaration`）完成。
> 迁移期两套协议并存是计划内的——控制手段同 `architecture-upgrade.md`：
> 一只盒子不许混着写 / 走了新路径的必须真的在装配。

| 步 | 做什么 | 守什么 |
|---|---|---|
| 1 | `core/<domain>.py`：`DomainDeclaration` / `Edge` / `assemble_domain` | 新协议只有形状，零业务词 |
| 2 | 建 `domains/<wms>/` `domains/<learning>/`（域声明 + 成员 + 边） | 边逐条对着现有 `feeds_into` 搬，**不新增不删除**——但**要按 `kind` 分类**（见下）|
| 3 | 盒子上摘掉 `domain` / `feeds_into` / `feedback_into` | 一只盒子不许同时留两套 |
| 4 | `core/field_map.py` 改成吃域声明 | 派生层仍然不拥有事实 |
| 5 | 看板改读新来源 | 画布上的虚线边界只是它的一张照片 |
| 6 | 补 D1–D12 守卫 + 「域声明必须真的在装配」守卫 | 声明了却没人装 = 没迁移 |

**第 2 步的「按 `kind` 分类」是这次搬迁真正的活。** WMS 域现在的 11 条声明边，
**不是 11 条同一种边**：

| 现在（`feeds_into` / `feedback_into` 一锅烩）| 条数 | 搬成 | 有通道吗 |
|---|---|---|---|
| requirement→design、design→development、development→testing、testing→ops、ops→analysis | **5** | `kind=handoff`（`via` 写各自的 ref 字段名）| **全部有** |
| design→system、development→system、testing→system、ops→system、analysis→system | **5** | `kind=archive`（**不带 `via`**）| **全部没有**——档案盒是去线上读的，不收上游产出 |
| analysis→requirement | **1** | `kind=feedback` | **没有**——这条边是「开新一轮需求」，不是数据交接 |

> **如果第 2 步只是照抄 `feeds_into`，那 6 条没有通道的边会被搬成 `handoff`，
> 然后被 D3c 全部拒掉**——迁移就卡在那里。
> 正确做法是**先分类**：`archive` 和 `feedback` 本来就**不该**有通道，
> D3c 只查 `handoff`。

**第 3 步会抓出来的东西**：那 5 条 `archive` 边搬完之后，
「需求盒没有 archive 边」这件事就会变成**一条显式的豁免**
（§5.3），而不是**一句藏在需求盒注释里的话**。

---

## 8. 收益（这一趟换到了什么）

| 现在 | 之后 |
|---|---|
| 闭环边靠每只盒子自己记得写进 `feedback_into` | 域声明一次性处理，**混了装配期就拒**（D4）|
| 「产物进档案」被复制成 5 份盒子声明，**需求的例外只活在散文里** | 5 条 `archive` 边 + **一条显式豁免** |
| `feeds_into` 混着「数据交接」和「归档归属」两种语义 | `kind` 强制区分三种边（D7）|
| 声明了交接但没有通道 → 静默失效 | D3c/D11 装配期就拒 |
| 悬空 `feeds_into` → 静默丢弃 | D1/D2 装配期就拒 |
| 「域」只是个字符串标签，域级规则无处可写 | 域声明 + `rules` + **豁免机制** |

---

## 9. 代价（诚实清单）

| 代价 | 大小 | 备注 |
|---|---|---|
| 盒子不再自包含「我属于哪个域」 | 小 | 它仍然知道自己跟谁交接——**那才是它该知道的** |
| 成员清单要手工维护 | **中** | D1 + D9 兜底；脚手架可自动改 |
| 加盒子要改域声明 | 中 | 但改的是**一处**（成员 + 边同处），不是两处 |
| 盒子拿不到「域」的上下文（除非查域） | 小 | 看板/工具都能查 |
| `core` 多一个协议文件 | 小 | 与 `scaffold.py` 同构 |
| **一只盒子属于多个域** | **不是代价，是能力** | 现状根本做不到 |

---

## 10. 不做什么

- **不把场图做成持久化实体**——它是派生层，没有需要协调的变更；持久化它 = 第二份接线事实
- **不让 runtime 执行域声明**——runtime 只跑单盒，域是两次履约之间的事
- **不在域声明里写「什么形状」**——形状归下游 schema（各说一半，不重复）
- **不手写「域接线表」**——域声明**就是**唯一真源，再写一份就是 `BOX_META` 当年的病
- **不在 `domains/` 里放场图**——场图是 `core` 的派生产物；`domains/` 装的是**声明**
- **不扫描目录发现域**——域是显式声明的（与盒子注册表同一条纪律）
- **不把 `domain` 一词继续复用**——分组标签、DDD 领域本体、DDD 构件类型是三个东西
- **不做计数期望型域级规则**——「成员数 == 7」永远绿，**自证的检查等于没有检查**（§5.3）
- **不写 core 判不了的规则**——「汇点必须是系统档案盒」需要盒子先声明角色，
  那是另一件事；第一版只做引用完整性（§5.3）
- **不为 `archive` / `feedback` 边硬造通道**——档案盒是去线上读的，不收上游产出；
  给它加一个假的 `ref:` 通道，是**造一根不存在的线**（§7）

---

## 11. 命名对照

| 现在的名字 | 之后 | 是什么 |
|---|---|---|
| `placement.domain` | **退休** | 成员归属 → 域声明的 `members` |
| `placement.feeds_into` | **退休** | 交接边 → 域声明的 `edges`（`kind=handoff`）|
| `placement.feedback_into` | **退休** | 闭环边 → 域声明的 `edges`（`kind=feedback`）|
| （新）| 域声明 `DomainDeclaration` | **唯一事实源**（住在 `domains/<域>/`）|
| （新）| 装配产物 `AssembledDomain` | 装好的域（与 `AssembledBox` 对称）|
| `BeeBoxMap` | `WiringView` | 拓扑投影（派生）|
| `core/field_map.py` | **`core/<wiring>.py`** | 接线算法（机制，零业务词）|
| `derive_field_map()` | `derive_wiring()` | 同上 |
| `field_map_of_scope()` | `wiring_of_scope()` | 同上 |
| `PlacementView` | `BoxFacts` | 一只盒子的事实（域装配器与接线投影共用一份）|
| `boxes/wms_design/domain.py` | 视情况 → `domains/<wms>/<vocabulary>.py` | DDD 领域本体（**升格与否取决于是否被多只盒子需要**）|
| `domain_kind` | 保留 | DDD 构件类型（`entity`/`domain_service`/…）|
| 前端 `· BeeBox Map` | `· 场图` | 画出来的那张照片 |

---

## 12. 目录与命名（已定，2026-11-05）

| 决定 | 内容 |
|---|---|
| **目录名** | **`domains/`** —— 装的是「域声明」，不是「场图」。叫 `maps/` 会让「Map」同时指事实和视图 |
| **接线算法文件名** | **`core/<wiring>.py`** —— 它派生的是接线，不是「场」；「场图」是渲染出来的那张图 |
| **域的装配产物名** | **`AssembledDomain`** —— 与 `AssembledBox` 对称 |
| **边住哪** | **`domains/<域>/`**（业务事实）；**算法住 `core/`**（机制）|
| **core 里两个装配器的文件名** | 倾向 **`core/<box_assembler>.py`** + **`core/<domain_assembler>.py`** —— 见下方说明 |

### 12.1 关于 `core/scaffold.py` 改名（2026-11-05 讨论）

**问题**：「脚手架」这个词在仓里被两个东西占了——

| | 是什么 | 住哪 |
|---|---|---|
| **脚手架** | **生成代码**的工具 | `scripts/new_box.py` |
| **装配器** | 把声明装成产物，**装配期报错** | `core/scaffold.py` |

而 `architecture-upgrade.md` 的标题「盒子脚手架化」指的是**整个改造**
（把「复制模板」变成「按协议填组件」），不是某一个文件。

**三个选项**：

| | 命名 | 成本 | 判断 |
|---|---|---|---|
| A | `core/<box_scaffold>.py` + `core/<domain_scaffold>.py` | 40 文件 | 与 `architecture-upgrade.md` 用词一致；跟 `scripts/new_box.py` 仍撞 |
| B | `core/<box_assembler>.py` + `core/<domain_assembler>.py` | 40 文件 | ✅ **彻底分开**：脚手架=生成、装配器=装配 |
| C | 不改（`core/scaffold.py` + `core/<domain>.py`）| **0** | 靠类名区分（`assemble` vs `assemble_domain`），已经够 |

**倾向 B**：消掉的是贯穿全仓的术语歧义，而不只是文件名好看。
`assembler` 也更准——`assemble()` 干的事就是装配，而 `scaffold` 在编程里
通常指「项目脚手架/生成器」，正好是 `scripts/new_box.py` 的事。

**但 `core/models.py` 不跟着改**——它现在装的是**新旧两套盒子协议**
（`BeeBoxDefinition` + `PlacementDef` 即将退休）。等 12 只盒子迁完，
它瘦成「盒子和域共用的公共类型」，**那时 `models.py` 这个名字恰好合适**。
现在改是替未来还债。

**执行时机**：**等 12 只盒子迁完再一次性改**。改名的 40 个文件里，
36 个是 `boxes/<盒>/{__init__,beeline,definition}.py`——正在被迁移改着，
现在动会撞车。

**参照**：`core/field_map.py` → `core/<wiring>.py` 只牵 5 个文件 + 1 个测试文件，
可以在同一批里顺手改掉。

---

## 13. 待定（需要人拍板）

1. ~~**域级规则（`rules`）第一版要不要有？**~~ → **2026-11-05 已定：要，但只做「完备性规则」**
   （`all_archived` / `all_handoff` / `each_handoff_has_channel`），
   **不做计数期望**（计数期望永远绿 = 没有检查）。
   规则的形状与豁免机制见 §5.3。**已定。**
2. **域需不需要生成器（`scripts/<new_domain>.py`）？** 倾向**第一版不需要**——
   域声明几乎全是业务事实（成员名单 + 边 + 规则），手写更直接；
   而盒子有大量机制样板（7 个文件 + 协议结构 + handler 骨架），才值得生成。
   **域需要的是装配器（机制），不一定是生成器。**
3. **`standalone` 标记放哪？** 盒子自己的字段（`BoxDeclaration`），还是独立清单？
4. **WMS 词表升格不升格？** 现在只被设计盒自己用 → 留在盒子里；
   一旦开发盒/测试盒也要读，就升格到 `domains/<wms>/`。
5. **旧协议的 `PlacementDef` 怎么退休？** 11 只盒子还在用旧的
   `BeeBoxDefinition.placement: PlacementDef`，新协议已把字段摊平。
   三个字段退休时，旧协议的那份要不要一起标退休？
6. **（新增）`validate_payload` 下沉到 `core/<contract>.py`？** D3c 要复用它，
   而 `core` 不该反向依赖 `runtime`（§5.1、§5.6）。倾向**下沉**，
   依据同 `core/conditions.py`：「它是协议，不是执行」。
   **代价**：`runtime/contract.py` + `runtime/handoff.py` + `kanban/trigger.py` +
   `boxes/wms_requirement/runner.py` + 约 12 处测试引用要跟着改 import。
   **不做会怎样**：D3c 只能在 `runtime` 层做 → 域装配器就拿不到通道判定 →
   装配期报错这件事就落空了。
7. **（新增，2026-11-05 实测）规则按形状分派怎么写？** Learning 域是**放射**，
   `all_archived` / `all_handoff` 在它身上**都不适用**（§5.5）。
   三个选项：
   - (a) 规则自己声明适用范围（`applies_to: ring | star`）——**判据自己管自己**，最笨但最直白
   - (b) 按 `kind` 分派规则（规则不认形状，只认边）——**推荐**：
     边有 `kind`，规则的判据就是「某类边的完备性」，形状自动对上
   - (c) 两套规则集（环的规则 / 放射的规则）——**两份规则 = 两份事实**，不推荐
   > **倾向 (b)**：§5.5 已经证明「汇点/非汇点」不是判据，**`kind` 才是**。
   > 规则挂在 `kind` 上之后，环和放射不需要两套——放射域的 `handoff` 边本来就该有通道。
8. **（新增，2026-11-05 实测）多域下同一对盒子的边，要不要写两遍？**
   §3.3 ③ 说「一只盒子属于 N 个域，就带着它的边在 N 个域里各出现一次」。
   **那是同一对盒子的边写 N 份 = N 份事实 = 会漂。**
   而现在**没有任何盒子属于多个域**（`placement.domain` 是单个字符串，实测），
   所以多域是**纯未来能力**，**设计时极易忽略**。
   要不要规定：**跨域边只在一处声明，另一处引用**？引用怎么表达？
9. **（新增）域声明要不要版本化？** 附录 A4 实测：`release_id` 只固定**单只盒子**，
   **不固定域成员**。域级规则一旦成为资产就必须版本化——但**域声明自己**的版本号 /
   快照机制，稿子里一个字没写。
10. **（新增）规则失败了，谁在什么时候看？** 装配期报错？CI？看板上的一个「域体检」页面？
    （并行会话正在做的 `workshop/` 盒子体检台，**可能正好是那个落点**——
    但「盒子体检」和「域体检」是不是同一件事，还没讨论。）
11. **（新增）域声明谁维护？** `domains/<域>/` 是**业务方**维护还是**开发**维护？
    §5.3 那条「需求盒是豁免」的注释，现在住在**需求盒的注释**里，
    搬进域声明后——**它归谁改？** 这决定规则豁免会不会变成「谁都能加豁免」。

---

## 14. 实现时必须同时补的守卫

**这份稿子实现完成前，它描述的一切都还不存在。** 实现时必须同时上：

- **D1–D12**（§5）——域级装配期校验
- **D3c 必须用 `runtime.contract.validate_payload` 实跑**——
  守卫不许自己写一套形状比较（那会是第二份事实）
- **「域声明必须真的在装配」**——声明写在文件里没人装 = 没迁移
  （与 `architecture-upgrade.md` §5 的「新盒子必须真的在装配」同族）
- **「退休名不得回代码」**——`placement.domain` / `feeds_into` / `feedback_into`
  退休后，不得再被定义（判据：**不许再定义**，不是「字符串里不许有」——
  兼容层与别名表里出现旧名是故意的）
- **「成员与边对得上」**——域声明的 `members` 与 `edges` 的端点必须一致
- **「盒子不留域信息」**——新协议的 `BoxDeclaration` 里不许再出现
  `domain` / `feeds_into` / `feedback_into` 三个字段
- **「域级规则不是自证的」**——规则里不许出现「成员数 == N」这种数自己的读数；
  判据要引用**域声明之外**的事实（注册表 / 盒子的 schema）

---

## 附录 A：本文结论所依据的实测

### A1. 声明层 vs 消费层对不上

```
A.feeds_into ∋ B  ⇒  B 的投料 schema 里有没有 ref: 通道能接住 A 的产出形状？
```

实测（WMS 域 5 只盒子投给档案盒）：

```
⚠️  wms_analysis_box    -> wms_system_box: 下游投料里没有 ref: 通道
⚠️  wms_design_box      -> wms_system_box: 下游投料里没有 ref: 通道
⚠️  wms_development_box -> wms_system_box: 下游投料里没有 ref: 通道
⚠️  wms_ops_box         -> wms_system_box: 下游投料里没有 ref: 通道
⚠️  wms_testing_box     -> wms_system_box: 下游投料里没有 ref: 通道
```

档案盒投料 `schema_system_probe_input` 的全部字段：
`target_base_url` / `timeout_seconds` / `readonly_endpoints` —— **`ref:` 通道数 = 0**。

### A2. 悬空接线被静默丢弃

```python
# core/field_map.py
edges = [(b, t) for p in placements for t in p.feeds_into if t in idset]
#                                                        ^^^^^^^^^^^^^^
#                                          不在集合里的下游 → 直接消失
```

实测：`a → ghost_box`（ghost_box 不存在）得到

```
reason_code = star        ← 看起来一切正常
reason      = ''          ← 没有原因
```

### A3. runtime 从不碰场图

`runtime/` 的 import 清单里，core 的部分只有三处：
`core.beeline_models` / `core.conditions` / `core.models`。
**没有 `core.field_map`。** 全仓搜 `field_map` / `placement` / `feeds_into` /
`domain` / `BeeBoxMap`，`runtime/` 里只有一处，且是**注释**：

```
runtime/handoff.py:5:  `placement.feeds_into` 只是看板上的箭头，投料要人手工把上一只盒子的
```

> 这句注释已经过时——`handoff` 这个模块就是为了消灭手工 copy 才写的。
> 它点破的是：**`feeds_into` 和 runtime 的交接机制，从一开始就是两套东西，只是没人对账。**

### A4. release 不固定域成员

```python
release_id = f"{definition.id}@v{definition.version}"
```

固定的是**单只盒子**的 definition（含它自己的 `placement`），
**不固定「域里当时有哪些盒子」**。`logs/` 里**没有任何域级快照**。

所以现在回看一条历史履约，看到的是「这只盒子当时的版本」+「现在的接线图」——
两者从未被固定在一起。现在没人需要这个；域级规则一旦成为资产，就必须版本化。

---

## 附录 B：§5.2 那张清单的实测脚本（可复现）

**为什么要留下脚本**：这张表是本稿「域能测什么」的证据。
**口头说的数不算数**——它得能被任何人重跑一遍。

**怎么跑**（在仓库根目录，隔离 worktree 里也行）：

```bash
git worktree add --detach /tmp/beeos_dm HEAD
cd /tmp/beeos_dm && <repo>/.venv/bin/python /tmp/measure_channel2.py
```

**核心判据只有一段**：通道存不存在 = **上游产出能不能通过下游声明的那份形状的校验**。

```python
def synth(field, schemas, depth=0):
    """按 FieldDef 造一个类型合法的样例值（只为喂校验器）"""
    t = field.type
    if t == "string": return "x"
    if t in ("number", "number_or_na"): return 1
    if t == "integer": return 1
    if t in ("boolean", "boolean_or_na"): return True
    if t == "enum": return (field.values or ["x"])[0]
    if t == "object":
        return {p.name: synth(p, schemas, depth+1) for p in (field.properties or [])}
    if t == "array":
        return [synth(field.items, schemas, depth+1)] if field.items else []
    if t.startswith("ref:"):
        target = schemas.get(t[4:])
        if target is None: return {}
        return {f.name: synth(f, schemas, depth+1) for f in target.fields}
    return None

# 每条边：下游投料里每个 ref 通道，喂上游产出
for fname, target_id in downstream_channels(dst):
    target = sch[dst].get(target_id)
    if target is None: continue
    errs = validate_payload(artifact, target, schemas=sch[dst])
    if not errs:  # ← 通道成立
        hit = (fname, target_id); break
```

**实测输出（2026-11-05，commit `9500a62`）**：

```
上游                   下游                   通道字段                  下游声明的形状
-------------------------------------------------------------------------------------------------
wms_design_box       wms_development_box  design_package_ref    schema_design_package_input     ✅
wms_design_box       wms_system_box       —                     下游 ref: []                      ❌
wms_ops_box          wms_analysis_box     observation_ref       schema_analysis_observation_input✅
wms_ops_box          wms_system_box       —                     下游 ref: []                      ❌
wms_requirement_box  wms_design_box       requirement_set_ref   schema_requirement_set_input    ✅
wms_development_box  wms_testing_box      artifact_ref          schema_build_artifact_input     ✅
wms_testing_box      wms_ops_box          artifact_ref          schema_ops_artifact_input       ✅
wms_testing_box      wms_system_box       —                     下游 ref: []                      ❌
wms_analysis_box     wms_system_box       —                     下游 ref: []                      ❌
wms_analysis_box     wms_requirement_box  —                     下游 ref: []                      ❌
-------------------------------------------------------------------------------------------------
声明边 11 条：真通道 5，无通道 6
  域内交接边（环上）: 5 条，真通道 5 条
  投汇点边（归档）: 5 条，真通道 0 条
  闭环反馈边: 1 条，真通道 0 条
```

**归档覆盖**：

```
=== 归档覆盖（投给汇点 wms_system_box）===
  5/6 = 83.3%
  漏掉的: ['wms_requirement_box']
```

> **注意**：这个 5/6 **不是 bug**——需求盒是**有意的例外**，理由写在
> `boxes/wms_requirement/definition.py` 的注释里（§1.3）。
> 它现在没有家；域声明的**豁免机制**就是它的家（§5.3）。

**这次实测推翻的一条旧结论**：本文早先写的是「归档覆盖 5/6 = 需求盒漏了 1 份」。
**错了**——需求盒是**主动不写**那条边的，理由就写在它的注释里。
真正的代价不是「漏了一份」，而是**这条域级规则的例外没有声明可以安放**（§1.3）。

### B2. 第二轮实测（2026-11-05）：Learning 域 / receipt 归属 / 多域现状

**三个疑点**：Learning 域的三条边通不通？`receipt` 跟 WMS 域有没有关系？
现在有没有盒子属于多个域？

**Learning 域的边**：

```
learning_math_box    -> learning_growth_box   subject_result: ref:schema_learning_subject_result  ✅
learning_chinese_box -> learning_growth_box   subject_result: ref:schema_learning_subject_result  ✅
learning_english_box -> learning_growth_box   subject_result: ref:schema_learning_subject_result  ✅
```

三科产出是**三个不同 schema**（`schema_learning_math_output` / `_chinese_` / `_english_`），
但**字段完全相同**（10 个，名字和类型都一样）：

```
math    产出: {record_id, subject, activity, observed_at, mastery, evidence, source_kind, detail, notes, ratio_exact}
growth 接料: {record_id, subject, activity, observed_at, mastery, evidence, source_kind, detail, notes}
                                      ↑ 上游的子集（9 个，去掉 ratio_exact，notes 放宽成可选）
```

**receipt 的归属**：

```
投给 receipt 的盒子:      []
feedback 给 receipt 的:   []
引用 schema_receipt_parsed 的 ref 通道:  （无 —— 没人能接它的产出）
receipt 产出字段（20 个）: merchant_name / receipt_no / receipt_date / receipt_time / items /
                           items_count / subtotal / tax / discount / total / payment_method /
                           field_completeness / line_arithmetic / sum_arithmetic /
                           total_arithmetic / date_wellformed / totals_match / low_confidence_fields
```

**多域现状**：

```
各域成员数: {'WMS': 7, 'Learning': 4, '': 1}
未归域的盒子: ['receipt_capture_box']
→ placement.domain 是**单个字符串**，现在**不可能**多域
```

> **这三条实测各推翻/修正了稿子里的一条**：
> Learning 域证明**规则不能只按环写**（§5.5）；
> receipt 证明 §6.1 那个例子是**假想的**、§6.2 的「有意的独立」**站不住**；
> 多域实测证明 §3.3 ③ 的「各写一份」是一个**没人踩过的坑**（§13 第 8 条）。

### B3. 第三轮实测（2026-11-05）：**域分组 == 边的连通分量**？

```
=== 声明的域分组（placement.domain）===
  Learning      4 只
  WMS           7 只
  (未归域)      1 只  [receipt_capture_box]

=== 连通分量 A：按全部边（feeds_into + feedback_into）===
  7 只 [wms_analysis, wms_development, wms_design, wms_ops, wms_requirement, wms_system, wms_testing]
  4 只 [learning_chinese, learning_english, learning_growth, learning_math]
  1 只 [receipt_capture]

→ **完全一致**。域分组恰好就是边的连通分量（3 个分量）。
```

**但它只在「所有边都算」的意义下成立**。一旦只按「有数据流的边」（handoff + feedback，
即排除 archive 边）算连通分量：

```
=== 按 handoff + feedback 算 ===
  6 只 [wms_analysis, wms_development, wms_design, wms_ops, wms_requirement, wms_testing]
  4 只 [learning_*]
  1 只 [receipt_capture]
  1 只 [wms_system]        ← 档案盒自己成为一个分量
```

**→ WMS 裂成了 6 + 1。** 因为档案盒**只被 archive 边连着**（5 条），
而它**一条 handoff 边都没有**（它不收上游产出）。

**这条实测把「域的成员是声明的还是推导的」变成了一个真问题**（§13 第 12 条）。
