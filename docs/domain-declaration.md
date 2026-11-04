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
| F2 | **5 只盒子声明投给档案盒，而档案盒投料里 0 个 `ref:` 通道**——那 5 条边在拓扑上画着，一条也走不通。 | 档案盒投料 `schema_system_probe_input` = `target_base_url` / `timeout_seconds` / `readonly_endpoints`；它是**去线上读**的盒子，不收上游产出 |
| F3 | **悬空接线被静默丢弃**：`derive_field_map` 里 `edges = [... if t in idset]`，指向不存在盒子的 `feeds_into` 直接消失，无告警。 | 实测：`a → ghost_box` 得到 `reason_code=star`、`reason=''`，那条边人间蒸发 |

### 1.3 域级意图被复制了 5 份，并且已经开始漏

「本域的过程产物都沉淀进系统档案」是一条**域级意图**，但现在它被写成
**5 份盒子级声明**（design / development / testing / ops / analysis 各写一遍）。

**而需求盒没写。** 它的 `feeds_into` 只有 `["wms_design"]`，形态跟其它 6 只都不一样。

> 这条域级意图客观存在，现在没有家，只能靠每只盒子各写一遍——**复制就会漏，已经漏了。**

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

### 2.3 场图（接线视图）是派生层，不是事实源

`BeeBoxMap` 的 12 个字段**全部**从各盒子的 `placement` 派生，
**没有一个字段只属于它自己**。

所以它**不需要被管理**（没有需要协调的变更），也**不需要脚手架**（没有声明可装配）。

它只是**照片**。照片不拥有事实。

### 2.4 runtime 不执行域声明

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

class DomainDeclaration(BaseModel):
    id: str                          # "wms"
    display_name: str
    members: list[str]               # 本域有哪些盒子（唯一一份成员表）
    edges: list[Edge]                # 盒子之间怎么连
```

**core 只定形状，不含业务词。** 具体值（`"wms_system"`、`"via=design_package_ref"`）
在 `maps/<域>/` 侧填。

### 3.2 域声明（业务侧，一份样例）

```python
# maps/<wms>/<domain>.py（新建）
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

## 4. 派生层：场图怎么改

`core/field_map.py` 继续存在，**但输入变了**：

| | 现在 | 之后 |
|---|---|---|
| 输入 | `Sequence[PlacementView]`（手工抄的视图）| `DomainDeclaration` + 成员盒子的 definition |
| 事实源 | 各盒子的 `placement` | 域声明 |
| 位置 | `core/field_map.py` | 不变（仍是 core 的派生层）|

**它从「认 placement」改成「认域声明」——仍然不拥有事实。**

命名建议（避免「Map」既指事实又指视图）：

| 现在 | 之后 |
|---|---|
| `core/field_map.py` | `core/<wiring>.py`（或保留原名，但 docstring 写清它是派生层）|
| `BeeBoxMap` | `WiringView` |
| 前端 label `... · BeeBox Map` | `... · 场图` |
| 域声明（新） | **域**（Domain）——唯一的事实源 |

---

## 5. 装配期校验（域级）

与 `core/scaffold.py` 的 C1–C12 同构，域级校验同样在装配期报错：

| # | 校验 | 不这么做会怎样 |
|---|---|---|
| D1 | 每个 `members` 里的盒子都在注册表里存在 | 悬空成员 → 域的成员表里有个不存在的盒子 |
| D2 | 每条边的两端都在 `members` 里（或声明为 `external`）| 边指向域外的盒子却没说明 → 拓扑漏边 |
| D3 | `handoff` 边必须有 `via`，且 `via` 必须是下游 `task_schema` 里的 `ref:` 字段 | 声明了交接却没有通道 → **就是现在那 5 条假边** |
| D4 | `feedback` 边不进拓扑排序 | 闭环边混进主干 → 排序遇环 → **静默按原顺序收尾** |
| D5 | 汇点唯一 | 多个汇点 → 圆心算不出来 |
| D6 | 主干无环 | 环上顺序变成任意的，图照常渲染，结构是错的 |
| D7 | 每条边两端的 `kind` 合法（`archive` 不得有 `via`）| 归档归属被当成数据交接 |
| D8 | 同一对盒子不得有两条同 `kind` 的边 | 重复声明，含义不明 |
| D9 | 每只已注册盒子要么属于至少一个域，要么显式声明 `standalone` | 忘了归域 → 盒子不在任何域里，却没人知道 |

**D3 是这套校验里最有价值的一条**——它把「声明了交接但没有通道」从**静默**变成**装配期就拒**。

---

## 6. 连带要处理的三件事

### 6.1 跨域边：两端不共享任何域

例：「Learning 域的盒子要用票据盒（未归域）的产出」——这条边**不属于 WMS 也不属于 Learning**。

**解法**：域可以声明**外部边**，挂在**接收方**的域里：

```python
Edge(from_box="receipt_capture", to_box="wms_requirement",
     kind=EdgeKind.handoff, via="receipt_ref", external=True)
```

这比现在 `scope_field_map` 的「跨域整体无图，给一句原因」更细：
**不是「跨域整体无图」，而是「跨域的那一条边挂在接收方域里」。**

### 6.2 「未归域」的盒子（`receipt` 现在就是）

它就是「不出现在任何域的 `members` 里」。但要区分**「有意的独立」**和**「忘了加」**。

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
| 2 | 建 `maps/<wms>/` `maps/<learning>/`（域声明 + 成员 + 边） | 边逐条对着现有 `feeds_into` 搬，**不新增不删除** |
| 3 | 盒子上摘掉 `domain` / `feeds_into` / `feedback_into` | 一只盒子不许同时留两套 |
| 4 | `core/field_map.py` 改成吃域声明 | 派生层仍然不拥有事实 |
| 5 | 看板改读新来源 | 画布上的虚线边界只是它的一张照片 |
| 6 | 补 D1–D9 守卫 + 「域声明必须真的在装配」守卫 | 声明了却没人装 = 没迁移 |

**第 2 步最关键**：搬的时候**逐条对着搬**，那 5 条假边在第 3 步会被 D3 抓出来，
然后**由人决定**它们是「该删」还是「该补通道」还是「该改成 `archive` 边」。

---

## 8. 收益（这一趟换到了什么）

| 现在 | 之后 |
|---|---|
| 闭环边靠每只盒子自己记得写进 `feedback_into` | 域声明一次性处理，**混了装配期就拒**（D4）|
| 「产物进档案」被复制成 5 份盒子声明，需求盒漏了 1 份 | 1 条 `archive` 边 |
| `feeds_into` 混着「数据交接」和「归档归属」两种语义 | `kind` 强制区分三种边（D7）|
| 声明了交接但没有通道 → 静默失效 | D3 装配期就拒 |
| 悬空 `feeds_into` → 静默丢弃 | D1/D2 装配期就拒 |
| 「域」只是个字符串标签，域级规则无处可写 | 域声明 + `rules` |

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
- **不在 `maps/` 里放场图**——场图是 `core` 的派生产物；`maps/` 装的是**声明**
- **不扫描目录发现域**——域是显式声明的（与盒子注册表同一条纪律）
- **不把 `domain` 一词继续复用**——分组标签、DDD 领域本体、DDD 构件类型是三个东西

---

## 11. 命名对照

| 现在的名字 | 之后 | 是什么 |
|---|---|---|
| `placement.domain` | **退休** | 成员归属 → 域声明的 `members` |
| `placement.feeds_into` | **退休** | 交接边 → 域声明的 `edges`（`kind=handoff`）|
| `placement.feedback_into` | **退休** | 闭环边 → 域声明的 `edges`（`kind=feedback`）|
| （新）| 域声明 `DomainDeclaration` | **唯一事实源** |
| `BeeBoxMap` | `WiringView` | 派生视图 |
| `core/field_map.py` | `core/<wiring>.py` | 派生算法 |
| `boxes/wms_design/domain.py` | 视情况 → `maps/<wms>/<vocabulary>.py` | DDD 领域本体（**升格与否取决于是否被多只盒子需要**）|
| `domain_kind` | 保留 | DDD 构件类型（`entity`/`domain_service`/…）|
| 前端 `· BeeBox Map` | `· 场图` | 画出来的那张照片 |

---

## 12. 待定（需要人拍板）

1. **`maps/` 还是 `domains/`？** 目录名。它装的是「域声明」，不是「场图」——
   叫 `maps/` 会让「Map」这个词同时指事实和视图。
2. **域级规则（`rules`）第一版要不要有？** 现在一条都没有。
   若第一条规则是「本域必须有且只有一只系统档案盒」，那它就是 `rules` 的样例。
3. **`core/field_map.py` 改名吗？** 改名会让 `kanban/` `scripts/` `tests/` 三处 import 跟着改，
   趁现在改比以后改便宜。
4. **`standalone` 标记放哪？** 盒子自己的字段（`BoxDeclaration`），还是独立清单？
5. **WMS 词表升格不升格？** 现在只被设计盒自己用 → 留在盒子里；
   一旦开发盒/测试盒也要读，就升格到 `maps/<wms>/`。

---

## 13. 实现时必须同时补的守卫

**这份稿子实现完成前，它描述的一切都还不存在。** 实现时必须同时上：

- **D1–D9**（§5）——域级装配期校验
- **「域声明必须真的在装配」**——声明写在文件里没人装 = 没迁移
  （与 `architecture-upgrade.md` §5 的「新盒子必须真的在装配」同族）
- **「退休名不得回代码」**——`placement.domain` / `feeds_into` / `feedback_into`
  退休后，不得再被定义（判据：**不许再定义**，不是「字符串里不许有」——
  兼容层与别名表里出现旧名是故意的）
- **「成员与边对得上」**——域声明的 `members` 与 `edges` 的端点必须一致
- **「盒子不留域信息」**——新协议的 `BoxDeclaration` 里不许再出现
  `domain` / `feeds_into` / `feedback_into` 三个字段

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
