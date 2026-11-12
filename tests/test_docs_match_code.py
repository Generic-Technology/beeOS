"""tests/test_docs_match_code.py — 文档与代码的对齐守卫

文档落后是必然的：代码在演进，文档靠人记。除非有东西盯着。

这条守卫盯九件**结构事实**（不盯散文措辞——措辞会漂，事实不会；
唯一的例外是第 9 条：**已经退休的说法不许回来**）：

1. **路径存在性**——文档里提到的仓库路径（``boxes/...`` / ``runtime/...`` 等）必须真实存在
2. **导入可解析**——文档里的 ``from boxes.x import y`` 必须真的能 import，且名字存在
3. **README 覆盖全部注册盒子**——新盒子注册进 BOX_REGISTRY，README 必须提到它
4. **退休名不得复活**——改名前的目录名 / 已删除的模块名一旦在文档里回来，文档就在骗人
5. **盒子的 README 里的条数必须等于实际条数**——判据 8 条就写 8 条，不写 5 条
6. **退休名不得回代码**——改名漏一半（代码改了文档没改，或反过来）同样是在骗人
7. **目录清单一致**——根 README 列的 ``core/`` ``runtime/`` ``scripts/`` ``docs/``
   必须等于实际目录内容：多一个漏一个都红
8. **盒子 README 的规模数字**——「N 跳 / N ops / N 个 schema」也必须等于实际
9. **退休的说法不得复活**——中文词没有「定义」可判，判据是「不许再出现」

第 5 条是从真实事故来的：``boxes/wms_design/README.md`` 写着「5 项机械 acceptance」，
实际是 8 条；写着「3 exception rules」，实际是 4 条。数字对不上，读者照着文档验收就会漏。

第 6-8 条来自 2026-11-04 的一次文档审计。那次审计发现：改名（modeling→design）
**漏了一半**——``schema_model_expectations`` 在代码里没改，文档里的 mermaid 图
还写着 ``model_evidence`` / ``business_model_package``，根 README 的目录清单
落后了 4 个文件、仓库结构图少了 4 只盒子。当时 118 条守卫全绿。
**绿不等于对**——守卫盯的地方有缝。

设计约束（跟本仓其它守卫同族）：
- **每条声明配一个会红的守卫**——本文件里的每条规则都能在历史上抓出真问题
- **不许只查词在不在**——路径要真的能打开，模块要真的能 import
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

from tests.conftest import requires_checkout

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

# 扫描文档时跳过的目录（vendor 里有一堆第三方 README）
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "vendor", "dist", "build", ".pytest_cache"}

# 文档里提到的路径，只有这些前缀算「仓库内路径」——别的（如 logs/、flows/）不管
PATH_PREFIXES = (
    "boxes", "runtime", "core", "kanban", "tests", "docs", "scripts",
    "fulfillment_scopes",
)

# 改名前 / 已删除的名字：文档里再出现就是在骗人
RETIRED_NAMES = (
    "mock_runner",
    "beelines/",
    "from beelines",
    "import beelines",
    "boxes/modeling",
    "boxes/requirement_capture",
    "boxes.modeling",
    "boxes.requirement_capture",
    "test_modeling_box",
    "test_modeling_binder",
    "test_modeling_expectations",
    "test_capture_box",
    "test_capture_extraction",
    # ---- 2026-11-04 审计补录：modeling→design 那次改名漏掉的 ----
    #
    # 那次改名改了 schema id / op id / 产出字段 / 指标名，**漏了这几个**：
    # 一个 schema id（`schema_model_expectations`）、六个 Python 常量、
    # 三处文档里的简写。改名的漏网和没改名一样是在骗人，
    # 所以退休名单要跟着改名走——每漏一个，就在这里补一条。
    "schema_model_expectations",
    "model_expectations",
    "model_evidence",
    "model_elements",
    "business_model_package",
    "MODEL_EXPECTATIONS",
    "MODEL_EXPECTATION",
    "MODEL_ELEMENTS",
    "MODEL_EVIDENCE",
    "MODEL_PACKAGE_ELEMENTS",
    "MODEL_TASK_INPUT_SCHEMA",
    "MODEL_PATTERNS",
    "BUSINESS_MODEL_PACKAGE",
    # 文档里写了一个**从来不存在过**的字段（ops 盒 README 说判据带它）
    "source_task_run_id",
    # ---- 2026-11-06 改名：协作边界 domain → fulfillment_scope ----
    #
    # 「域」这个名字让位给**真正的 domain**——
    # `boxes/wms_design/domain.py` 是 WMS 领域词表（DDD 领域本体），
    # 那才是 domain。而「一只盒子属于哪组、怎么交接、共同守什么规矩」
    # 这个协作边界，原先占了 domain 的名字。
    #
    # **判据是「定义」，不是「字符串里不许有」**：
    # 兼容层读旧产出时提到旧名是正当的。
    "DomainDeclaration",
    "AssembledDomain",
    "assemble_domain",
    "DOMAIN_REGISTRY",
    "domain_assemblies",
    "domains_of",
    "box_domains",
    "domain_wiring",
    "domain_layout",
    "ALL_DOMAINS_SCOPE",
    "core.domain",
    "domains/",
    # 跟着改名一起退休的**测试文件名**（测试文件也是「定义了新东西」）
    "test_domain_declaration",
    "test_domain_stability",
    # ---- 2026-11-08：盒子上下文那节，考虑过但**否掉**的两个名字 ----
    #
    # 有人在问「BOX CONTEXT 能不能改成 DOMAIN CONTEXT」——
    # 结论是不行，理由早就写在 `kanban/trigger.py::box_context()` 的
    # docstring 里，现在补进退休名单，让它**不再是只活在一段注释里**：
    #
    # - `domain` 现在指**领域知识**（DDD：`boxes/wms_design/domain.py`
    #   是 WMS 领域词表，设计盒那边还有 `renderDddDesign`）。
    #   叫 domain context 会跟 DDD 的 bounded context 撞车。
    # - `scope` 现在指**查看范围**（看板当前看的是哪个协作边界，
    #   URL `?scope=`）。叫 scope context 会跟「查看范围」撞车——
    #   而那已经是**看板自己的**一个轴了。
    #
    # 那一节根本不是领域模型，也不是范围：**它就是这只盒子自己的声明**
    # （吃什么、吐什么、跑得怎么样）。邻居也全是 `box_*`
    # （box_manifest / box_meta / box_metrics_readings），
    # 夹个别的词根在中间像个混血。
    #
    # **判据同样是「不许再定义」**——docstring 里提到这两个名字
    # （解释为什么不用它们）是正当的，不算定义。
    "domain_context",
    "scope_context",
)

#: **退休的散文词**（2026-11-07：学习事件 → 学习记录）
#:
#: 上面那套退休名判据是「不许再**定义**」——变量 / 函数 / 类 / 参数 / 声明 id。
#: **中文词没有「定义」这回事**：它是散文里的说法，
#: 于是判据只能换成「不许再**出现**」。
#:
#: ## 为什么这个词值得退休
#:
#: 同一个东西在仓库里有过**两个名字**：三只学科盒说「学习事件」，
#: 成长盒早就说「学习记录」。两套说法并存，
#: 读者得自己猜「这两个说的是不是同一件事」——而这里说的正是**追溯**这件事。
#:
#: ## 为什么新词是「学习记录」，不是「学习日记」
#:
#: - **「记录」是这盒真的会做的事**：产出的是一条**有出处的事实**
#:   （`record_id` 从内容推导、`evidence` 说得出处、`source_kind` 区分看到与听说）。
#: - **「日记」会削弱这条规矩**：日记是**连续的主观流水**，
#:   而这盒的规矩恰恰是「没有出处、没有分数的记录不入档」——
#:   叫「日记」，等于告诉读者「这里存的是随手写的流水账」。
#: - **「日记」是仓库里从没出现过的自造词**；「学习记录」早就在用
#:   （根 README 的场景名、成长盒的归档文案），接上去不用改第二遍。
#:
#: **判据用「不许再出现」，不是「不许再定义」**：中文词没有标识符可定义，
#: 它活在注释、docstring、描述文案、文档里——这些**全都是散文**。
#:
#: **要断言旧词不在某处的测试**，请从这里 import 这个常量，
#: 不要把旧词硬写进测试文件——否则守卫会把守自己的那条测试也判红。
RETIRED_PROSE_TERMS = {
    "学习事件": "学习记录",
    # ---- 2026-11-12：「插件」这个词整个留给新概念 ----
    #
    # 旧义指的是**盒子**（与继承式相对的那套组装方式），
    # 现在「插件」= 挂在组件上的横切扩展（见 docs/component-plugin-model.md）。
    #
    # **为什么要退休**：同一个词背两套语义，读者按名字猜，猜错就按错的东西找。
    # 而且撞名是**真会出事**的——新概念要往 core 塞一个叫「插件」的东西，
    # 而文档里那处旧义的「插件」指的是盒子，两边都在讲「插件」。
    #
    # **判据是「不许再出现」**：中文词没有「定义」可判（那是标识符那一套）。
    "组合式（协议式）插件": "协议式组装",
}

# **这次搬家的记录文档**：它讲的就是「从 A 改成 B」，
# 旧名是它的**内容**的一部分——写不出旧名，读者看不懂改了什么。
# 但代码里旧名**确实**退休了（`test_the_retired_names_are_not_back_in_code` 照常跑）。
MIGRATION_DOCS = {
    "docs/fulfillment-scope.md",
}

# 历史记录文档：它记录的是**当时**的事实，出现旧名不是骗人。
# 豁免只给自标「不是活文档」的实测记录——它已经明说了现状看根 README。
HISTORICAL_DOCS = {
    "docs/wms-closure-plan.md",
}

#: **退休的接线字段名**（2026-11-06）
#:
#: 接线搬进了协作边界声明（`fulfillment_scopes/<边界>/`），盒子自己那份声明就退休了。
#: 三个名字一起退：`placement`（那个壳）、`domain`（域归属）、`feeds_into`（交接）。
#:
#: **为什么「一只盒子不许同时留两套」**：两份接线迟早给出不同答案，
#: 而到那时没人知道该信哪份——看板读的是协作边界声明，
#: 于是「盒子上写的那份」会静静地失效，看着一切正常。
RETIRED_WIRING_FIELDS = ("placement", "feeds_into", "feedback_into")

#: **退休的接线字段，只许作为「历史说明」出现在文档里**
#:
#: 接线搬家这件事本身必须写得出旧名——不然读者看不懂「改了什么」，
#: 决策的理由也就跟着没了。所以豁免**那一篇讲这次搬家的文档**，
#: 别的文档提到旧名就是在教人走一条已经不通的路。
#:
#: **豁免的前提**：旧名在**代码**里确实已经退休
#: （`test_the_retired_wiring_fields_are_not_back_in_code` 照常跑）。
DOCS_MAY_MENTION_RETIRED_WIRING = {
    "docs/fulfillment-scope.md",
}

#: 接线字段在**盒子声明**里不许再出现的关键字
#:
#: `domain` 不在这份名单的「裸名」一栏里——它太泛
#: （`fulfillment_scope_wiring(domain)` 这种参数名是合法的）。
#: 它只在**声明的关键字**这一层被判：往 `BoxDeclaration(...)` 里写
#: `domain=` = 又在盒子身上留了一份接线。
RETIRED_WIRING_KEYWORDS = ("domain", "feeds_into", "feedback_into", "placement")

#: 声明这些关键字的调用（= 盒子的声明）
DECLARATION_CALLS = ("BoxDeclaration", "BeeBoxDefinition", "PlacementDef")

# 已经搬走 / 拆掉的模块名：import 它 = 文档在教人一条走不通的路
RETIRED_MODULES = ("beelines",)

# **已经退休的路径**：允许文档把它当历史记录提（「改名前叫什么」），
# 但仓库里必须**确实没有**这个路径。
#
# **为什么需要它**：设计稿里的「改名对照表」必须写得出旧名，
# 而旧名已经不在仓库里了——不许提的话，文档就只能把对照表删掉，
# 决策的理由也就跟着没了。
#
# **为什么这条豁免不是漏洞**：豁免的前提是「它真的退休了」。
# 仓库里要是又出现了这个路径，说明旧名复活了——
# 那时文档里的「改名前」就分不清说的是哪一次，
# 而「旧名不许回代码」正是这套纪律要防的事。
RETIRED_PATHS = {
    "core/field_map.py",
    "tests/test_field_map.py",
    # 内核里的 op 实现曾经住在这儿，搬进盒子之后就没了——
    # 代码注释里提到它们是**历史说明**（说「从这儿搬来的」）。
    "runtime/mock_runner.py",
    "runtime/capture_runner.py",
}
# 退休的**测试文件名**（裸文件名形式）
RETIRED_TEST_FILES = {
    "test_field_map.py",
}

# 路径 token：允许前缀目录 / 相对路径 / 通配
PATH_TOKEN = re.compile(
    r"(?:\.\./)*(?:\./)?(?:" + "|".join(PATH_PREFIXES) + r")/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*"
)
# 代码注释里的路径：比文档那条**多一个词边界**——
# 文档里 `score/total` 这种词组不会越界（行首就断了），
# 代码注释里它可能长在句子中间（`数学 score/total、语文 reading_score`），
# 不加边界会把 `core/total` 切出来当成路径。
CODE_PATH_TOKEN = re.compile(
    r"(?<![\w.\-])(?:\.\./|\./)?(?:"
    + "|".join(PATH_PREFIXES)
    + r")/[A-Za-z0-9_.\-/]+"
)
# 裸测试文件名（没有 tests/ 前缀）
TEST_FILE_TOKEN = re.compile(r"\btest_[a-z0-9_]+\.py\b")
# 内联代码里的 import
INLINE_IMPORT = re.compile(r"\bfrom\s+([A-Za-z_][\w.]*)\s+import\s+([A-Za-z_][\w,\s]*)")

# 「N 条判据 / N 项 acceptance / N exception」——用于核对盒子的实际条数
COUNT_PHRASE = re.compile(
    r"(\d+)\s*(?:条|项|个|条机械)?\s*(?:机械\s*)?"
    r"(判据|验收|例外|acceptance|acceptance rules?|exception|exception rules?)",
    re.IGNORECASE,
)


def _md_files() -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*.md"):
        if SELF in p.parents:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def _code_spans(text: str) -> list[str]:
    """内联代码片段（单行反引号）。"""
    return re.findall(r"`([^`\n]+)`", text)


def _python_blocks(text: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", text, re.S)


# --------------------------------------------------------------------------
# 1. 路径存在性
# --------------------------------------------------------------------------
@requires_checkout
@pytest.mark.parametrize("md", _md_files(), ids=_rel)
def test_md_referenced_paths_exist(md: Path) -> None:
    text = md.read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in PATH_TOKEN.finditer(line):
            token = match.group(0)
            if any(ch in token for ch in "*<>"):
                continue  # 通配 / 占位符，不是真实路径
            # **URL 路径不是仓内路径**。
            #
            # 这条守卫问的是「文档提到的**仓里那个文件**在不在」，
            # 而 `/api/boxes/health` 是 HTTP 路径——它里面的 `boxes/health`
            # 恰好长得像个仓内路径，于是被误报成「仓库里没有这个路径」。
            #
            # 豁免只给紧跟在 `api/` 后面的那一段：仓内路径从来不这么写，
            # 而 HTTP 接口路径全这么写。所以这条豁免**不会放过真的坏路径**。
            if line[: match.start()].endswith("api/"):
                continue
            raw = token
            if raw in RETIRED_PATHS:
                # 退休路径：**它必须真的不在仓库里了**。
                # 在的话就是旧名复活——文档里的「改名前」会指错人。
                assert not (ROOT / raw).exists(), (
                    f"{_rel(md)}:{lineno}: {raw} 是退休路径，"
                    f"但它又出现在仓库里了——旧名复活了，"
                    f"文档里的「改名前」就分不清说的是哪一次"
                )
                continue
            if raw.startswith(("./", "../")):
                target = (md.parent / raw).resolve()
            else:
                target = (ROOT / raw).resolve()
            if not target.exists():
                bad.append(f"{_rel(md)}:{lineno}: 文档提到 {raw} —— 仓库里没有这个路径")
    assert not bad, "\n".join(bad)


@requires_checkout
def test_code_referenced_paths_exist() -> None:
    """**代码注释里**引用的仓库内路径必须真的存在

    为什么单列一条：上面的路径守卫只扫**文档**（`_md_files()`）——
    **代码注释里的路径没人管**。

    实测踩过（2026-11-06）：`docs/domain-declaration.md` 改名为
    `docs/fulfillment-scope.md` 之后，**代码里 7 处注释还指着旧路径**——
    指向一个不存在的文件，**没有任何一条报错**。
    读代码的人点进去才发现。

    **为什么用 `tokenize` 而不是 `ast`**：Python 的语法树里**没有注释**——
    `ast` 扫不到注释里写的路径。这条要扫注释，就得上词法层。

    **只认真路径**：带 `*` / `<` / `>` 的是通配或占位符，不是某个具体文件。
    """
    import io
    import tokenize

    bad: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.resolve() == SELF:
            continue  # 这条守卫自己住在这儿（它会提到那些路径）
        try:
            with open(path, "rb") as fh:
                tokens = list(tokenize.tokenize(fh.readline))
        except (SyntaxError, tokenize.TokenError, UnicodeDecodeError):
            continue
        for tok in tokens:
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            text = tok.string
            for match in CODE_PATH_TOKEN.finditer(text):
                raw = match.group(0)
                if any(ch in raw for ch in "*<>"):
                    continue  # 通配 / 占位符
                if text[: match.start()].endswith("api/"):
                    continue  # HTTP 接口路径，不是仓内路径
                # **只认真路径**：具体文件（.py / .md）或显式目录（结尾是 /）。
                #
                # 不这么收就会把普通词组当成路径——
                # 「数学 score/total」里的 `core` 会被切出来当成 `core/total`，
                # 「runtime/classify 按 REQUIREMENT_TYPES 建全部 key」
                # 里的 `runtime/classify` 也不是文件。
                if not (raw.endswith((".py", ".md")) or raw.endswith("/")):
                    continue
                if raw in RETIRED_PATHS:
                    # 退休路径：它必须真的不在仓库里了
                    assert not (ROOT / raw).exists(), (
                        f"{_rel(path)}:{tok.start[0]}: {raw} 是退休路径，"
                        f"但它又出现在仓库里了——旧名复活了"
                    )
                    continue
                if raw.startswith(("./", "../")):
                    target = (path.parent / raw).resolve()
                else:
                    target = (ROOT / raw).resolve()
                if not target.exists():
                    bad.append(
                        f"{_rel(path)}:{tok.start[0]}: 代码注释提到 {raw}"
                        f" —— 仓库里没有这个路径"
                    )
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("md", _md_files(), ids=_rel)
def test_md_referenced_test_files_exist(md: Path) -> None:
    """文档里提到的测试文件名必须真实存在（裸文件名也算）。"""
    text = md.read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name in set(TEST_FILE_TOKEN.findall(line)):
            if name in RETIRED_TEST_FILES:
                # 退休的测试文件名：它必须真的不在 tests/ 下了（见 RETIRED_PATHS）
                assert not (ROOT / "tests" / name).exists(), (
                    f"{_rel(md)}:{lineno}: {name} 是退休的测试文件名，"
                    f"但它又出现在 tests/ 下了——旧名复活了"
                )
                continue
            if not (ROOT / "tests" / name).exists():
                bad.append(f"{_rel(md)}:{lineno}: 文档提到测试文件 {name} —— tests/ 下没有")
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------
# 2. 导入可解析
# --------------------------------------------------------------------------
def _check_import(module: str, names: list[str], where: str, bad: list[str]) -> None:
    root = module.split(".")[0]
    if root in RETIRED_MODULES:
        bad.append(f"{where}: `from {module} import ...` —— 这个模块已经不存在了")
        return
    if root not in {"boxes", "runtime", "core", "kanban"}:
        return
    try:
        mod = importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 — 文档里的 import 坏了就是坏了，报原样
        bad.append(f"{where}: `from {module} import ...` —— 真的 import 失败：{exc}")
        return
    for name in names:
        if not hasattr(mod, name):
            bad.append(f"{where}: `from {module} import {name}` —— {module} 里没有 {name}")


@pytest.mark.parametrize("md", _md_files(), ids=_rel)
def test_md_imports_are_resolvable(md: Path) -> None:
    text = md.read_text(encoding="utf-8")
    bad: list[str] = []

    for lineno, line in enumerate(text.splitlines(), 1):
        for module, names in INLINE_IMPORT.findall(line):
            _check_import(
                module,
                [n.strip() for n in names.split(",") if n.strip()],
                f"{_rel(md)}:{lineno}",
                bad,
            )

    for block in _python_blocks(text):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue  # 片段不是完整 python，跳过
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [a.name for a in node.names]
                _check_import(node.module, names, _rel(md), bad)

    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------
# 3. README 覆盖全部注册盒子
# --------------------------------------------------------------------------
def _registered_box_ids() -> list[str]:
    from kanban.trigger import BOX_REGISTRY

    return sorted(BOX_REGISTRY)


@requires_checkout
def test_readme_mentions_every_registered_box() -> None:
    """README 必须提到每一只已注册的盒子。

    **按整词匹配**：子串不算。`test_wms_ops_box.py` 里含有 `wms_ops_box`，
    但那不是「提到了这只盒子」——用子串判定会被文件名掩盖掉。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [
        b
        for b in _registered_box_ids()
        if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(b)}(?![A-Za-z0-9_])", readme)
    ]
    assert not missing, f"README.md 没提到这些已注册盒子：{missing}"


def test_every_box_dir_has_a_readme() -> None:
    missing = [name for name in _box_dirs() if not (ROOT / "boxes" / name / "README.md").exists()]
    assert not missing, f"这些盒子目录没有 README.md：{missing}"


def _box_dirs() -> list[str]:
    return [
        d.name
        for d in sorted((ROOT / "boxes").iterdir())
        if d.is_dir() and not d.name.startswith("__")
    ]


def test_box_readme_mentions_its_own_box_id() -> None:
    """盒子的 README 必须能对上是哪只盒子（靠 box_id，不靠目录名猜）。

    box_id 从本目录的 definition 里读——**不查注册表**：未注册的盒子（代码保留、
    看板不显示）也得有自己的 README，不能因为没注册就没人管。
    """
    bad: list[str] = []
    for name in _box_dirs():
        readme = ROOT / "boxes" / name / "README.md"
        if not readme.exists():
            continue
        module = importlib.import_module(f"boxes.{name}")
        box_id = module.get_definition().id
        if box_id not in readme.read_text(encoding="utf-8"):
            bad.append(f"boxes/{name}/README.md 没提到自己的 box_id（{box_id}）")
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------
# 4. 退休名不得复活
# --------------------------------------------------------------------------
@pytest.mark.parametrize("md", _md_files(), ids=_rel)
def test_md_has_no_retired_names(md: Path) -> None:
    if _rel(md) in HISTORICAL_DOCS:
        pytest.skip(f"{_rel(md)} 是历史实测记录，旧名是当时的事实")
    if _rel(md) in MIGRATION_DOCS:
        pytest.skip(f"{_rel(md)} 是这次搬家的记录文档，写得出旧名")
    text = md.read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name in RETIRED_NAMES:
            if name in line:
                bad.append(f"{_rel(md)}:{lineno}: 退休名 `{name}` 出现在文档里 —— 这个东西已经不存在了")
    assert not bad, "\n".join(bad)


def test_the_retired_names_are_not_back_in_code() -> None:
    """退休名不得回到**代码**里（文档那条只管文档）。

    **为什么要分两条**：文档和代码是两份东西，改名只改一半是常事——
    2026-11-04 那次审计就抓到 ``schema_model_expectations``
    在代码里没改、文档里的 mermaid 图也没改。两边都「自洽」，
    但自洽在一个已经不打算用的名字上。

    ## 只查「定义」，不查「读取」

    旧名在两种地方出现是**正当的**：

    - **向后兼容**——新运行时要读旧产出（旧字段名还在数据里）
    - **别名表 / 守旧名的测试**——``LEGACY_OP_IDS`` 里登记旧 op id，
      测试断言「旧产出仍能读出来」

    这两处出现旧名恰恰是**故意**的。所以判据不是「字符串里不许有旧名」，
    而是「**不许再定义**一个叫旧名的东西」：

    - 变量 / 常量赋值（``ast.Name`` 且 ctx 是 Store）
    - 函数名 / 类名 / 参数名
    - ``SchemaDef(id="...")`` 里的 id（**对外契约**，定义了一个新东西）

    读取（``get("x")`` / ``["x"]`` / ``.x``）与别名表的值都不算定义。

    豁免 ``MODEL_HANDLERS`` 这类 LLM 义的 MODEL_ 常量：
    那个「模型」指大模型，不是「设计」——它不在退休名单里。
    """
    bad: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.resolve() == SELF:
            continue  # 退休名单本身就住在这个文件里
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # 赋值：把旧名**定义**成一个新变量 / 常量
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                if node.id in RETIRED_NAMES:
                    bad.append(f"{_rel(path)}: 变量 `{node.id}` 是退休名")
            # 函数名 / 类名
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in RETIRED_NAMES:
                    bad.append(f"{_rel(path)}: 定义 `{node.name}` 是退休名")
            # 参数名
            if isinstance(node, ast.arg) and node.arg in RETIRED_NAMES:
                bad.append(f"{_rel(path)}: 参数 `{node.arg}` 是退休名")
            # schema id / 契约 id：**对外契约**，定义了一个新东西
            if (
                isinstance(node, ast.keyword)
                and node.arg in ("id", "schema_id")
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and node.value.value in RETIRED_NAMES
            ):
                bad.append(
                    f"{_rel(path)}: 声明了 id=`{node.value.value}` —— 这是退休名"
                )
    assert not bad, "\n".join(bad)


def _prose_carriers() -> list[Path]:
    """散文载体：文档 + 代码。

    **代码为什么也要扫**：中文词只可能活在注释、docstring、描述文案里——
    那些都是散文，而散文正是改名会漏的地方。
    """
    out: list[Path] = []
    for md in _md_files():
        rel = _rel(md)
        # 历史实测记录 / 搬家记录文档：它们讲的就是「从 A 改成 B」，
        # 写得出旧名是它们的**内容**的一部分。
        if rel in HISTORICAL_DOCS or rel in MIGRATION_DOCS:
            continue
        out.append(md)
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.resolve() == SELF:
            continue  # 退休名单本身写得出旧名（它就是登记处）
        out.append(path)
    return out


def test_the_retired_prose_terms_are_not_back() -> None:
    """退休的中文词不许再出现（文档 + 代码里的注释与文案）

    **为什么中文词要单列一条**：退休名那套判据是「不许再**定义**」
    （变量 / 函数 / 类 / 参数 / 声明 id）。中文词没有「定义」这回事——
    它是散文里的说法，所以判据换成「不许再**出现**」。

    **为什么值得盯**：改名只改一半是常事，而**两个名字并存**最坏——
    读者得自己猜说的是不是同一件事，猜错就按错的那个去找。

    **豁免**：历史实测记录 / 搬家记录文档（跟退休名同一套豁免）。
    """
    bad: list[str] = []
    for path in _prose_carriers():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for term, replacement in RETIRED_PROSE_TERMS.items():
                if term in line:
                    bad.append(
                        f"{_rel(path)}:{lineno}: 退休的说法「{term}」又出现了"
                        f"——现在叫「{replacement}」"
                    )
    assert not bad, "\n".join(bad)


def test_the_retired_wiring_fields_are_not_back_in_code() -> None:
    """接线字段退休了（2026-11-06）——**不许再定义**

    **判据是「不许再定义」，不是「字符串里不许有」**：
    - 退休名出现在**读**的地方（兼容层读旧产出）没问题
    - 出现在**定义**的地方（变量 / 函数 / 类 / 参数 / 声明关键字）就是复活

    ## 为什么不留空壳

    留一个「能写但没人读」的字段 = **两套接线并存**。
    两套接线迟早给出不同答案，而看板读的是协作边界声明——
    所以「盒子上写的那份」会静静地失效，看着一切正常。

    ## `domain` 为什么不在裸名名单里

    `domain` 太泛：`fulfillment_scope_wiring(domain)` 这种参数名是合法的。
    它只在**声明的关键字**这一层被判——
    往 `BoxDeclaration(...)` 里写 `domain=` = 又在盒子身上留了一份接线。
    """
    bad: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.resolve() == SELF:
            continue  # 退休名单本身就住在这个文件里
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                if node.id in RETIRED_WIRING_FIELDS:
                    bad.append(f"{_rel(path)}: 变量 `{node.id}` 是退休的接线字段")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in RETIRED_WIRING_FIELDS:
                    bad.append(f"{_rel(path)}: 定义 `{node.name}` 是退休的接线字段")
                if node.name == "PlacementDef":
                    bad.append(
                        f"{_rel(path)}: `PlacementDef` 已整体退休——"
                        f"它当年存在的理由（接线是描述的一部分）已经没了"
                    )
            if isinstance(node, ast.arg) and node.arg in RETIRED_WIRING_FIELDS:
                bad.append(f"{_rel(path)}: 参数 `{node.arg}` 是退休的接线字段")
            # 盒子声明里的关键字：`BoxDeclaration(domain=...)` = 接线又回来了
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name in DECLARATION_CALLS:
                    for kw in node.keywords:
                        if kw.arg in RETIRED_WIRING_KEYWORDS:
                            bad.append(
                                f"{_rel(path)}:{node.lineno}: "
                                f"{name}(...) 里写了 `{kw.arg}=`——"
                                f"接线字段退休了，它只住在协作边界声明里"
                            )
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("md", _md_files(), ids=_rel)
def test_md_has_no_retired_wiring_fields(md: Path) -> None:
    """文档里不许把退休的接线字段当**现状**教人

    **豁免那一篇讲这次搬家的文档**：搬家这件事必须写得出旧名，
    不然读者看不懂「改了什么」。别的文档提到旧名 =
    在教人走一条已经不通的路。
    """
    if _rel(md) in HISTORICAL_DOCS or _rel(md) in DOCS_MAY_MENTION_RETIRED_WIRING:
        pytest.skip(f"{_rel(md)} 是这次搬家的记录文档，写得出旧名")
    text = md.read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name in RETIRED_WIRING_FIELDS:
            if name in line:
                bad.append(f"{_rel(md)}:{lineno}: 退休字段 `{name}` 出现在文档里")
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------
# 6. README 不许写死测试规模
# --------------------------------------------------------------------------
# 为什么不写死数字而写个守卫去核对数字？
# 因为核对数字这条规则**自己就是漂移源**：并行会话加一条测试，README 就过期，
# 红的是我的守卫、活的是别人的代码——这种守卫最后只会被人加白名单。
# 所以这里反过来：**README 不许写死条数**，规模用「量级」表述。
# 真实的条数由 pytest 自己报，文档不重复一份会过期的事实。
_STALE_COUNT = re.compile(
    r"\d+\s*(?:个测试文件|个测试|个用例|条(?:测试|用例|守卫)|tests?\b)",
    re.IGNORECASE,
)


@requires_checkout
def test_readme_has_no_hardcoded_test_count() -> None:
    """README 里出现「120 个测试通过」这种话，就是一份会过期的事实。

    曾经的真实事故：README 写着「120 个测试通过」，实际已经 960 条。
    现在的规则：**不写**——规模用量级表述，真实条数由 pytest 报。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(readme.splitlines(), 1):
        for hit in _STALE_COUNT.findall(line):
            bad.append(f"README.md:{lineno}: 「{hit.strip()}」——测试规模不许写死数字")
    assert not bad, "\n".join(bad)


# 盒子数量也是同一类数字：它由「仓里注册了几只盒子」决定，
# 而盒子是**会长的**（脚手架接一个新协作边界就是加一只）。
# 写死的那一刻就开始过期，而过期的不是数字、是文档在骗人。
_STALE_BOX_COUNT = re.compile(r"\d+\s*只(?:盒子|盒)\b")


@requires_checkout
def test_readme_has_no_hardcoded_box_count() -> None:
    """根 README 不许写死「N 只盒子」

    **为什么盯根 README 这一份**：它是脚手架会改的那一份
    （`scripts/new_box.py --apply` 往盒子清单表里加一行）。
    加一只盒子就要同步改一处写死的数字 = 把「加盒子」这件事
    变成「改产品 + 还守卫的债」。

    根 README 改成「**全部已注册**（清单见下表）」：
    清单就在下面一行，数字是**推导**出来的，不必抄。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    bad: list[str] = []
    for lineno, line in enumerate(readme.splitlines(), 1):
        for hit in _STALE_BOX_COUNT.findall(line):
            bad.append(f"README.md:{lineno}: 「{hit.strip()}」——盒子数量不许写死")
    assert not bad, "\n".join(bad)


# 根 README 的盒子清单表：`| [`目录/`](路径) | `box_id` | 协作边界 | 判据 | 例外 | 成熟度 |`
_ROOT_TABLE_ROW = re.compile(
    r"^\s*\|\s*\[`[^`]+/`\]\(boxes/[^)]+\)\s*\|\s*"
    r"`(?P<box_id>[a-z0-9_]+)`\s*\|[^|]*\|\s*"
    r"(?P<acceptance>\d+)\s*\|\s*(?P<exceptions>\d+)\s*\|\s*"
    r"(?P<maturity>[a-z]+)\s*\|\s*$",
    re.M,
)


@requires_checkout
def test_the_root_readme_box_table_matches_the_definitions() -> None:
    """根 README 盒子清单里的**条数和成熟度**必须等于实际 definition

    **为什么根 README 也得查**：它是脚手架会改的那一份
    （`scripts/new_box.py --apply` 往表里加一行）。
    数字一抄就过期——**真实事故**：`wms_design` 的例外从 4 条变成 5 条，
    表里还写着 4，而**没有任何东西会报错**：读者照着表核对，
    差的那一条就被漏掉了。

    早年的理由是「表格里有多只盒子，数字不带归属，拿不准就不查」——
    但每行都带着自己的 `box_id`，**带得准**。

    协作边界那一列不查：空协作边界在表里显示成「未归范围」，那是**显示约定**，
    不是盒子声明的值。
    """
    from kanban.trigger import BOX_REGISTRY

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    rows = list(_ROOT_TABLE_ROW.finditer(readme))
    assert rows, "根 README 的盒子清单表没解析出任何一行"

    bad: list[str] = []
    for row in rows:
        box_id = row.group("box_id")
        entry = BOX_REGISTRY.get(box_id)
        if entry is None:
            bad.append(f"{box_id}: 表里有这只盒子，注册表里没有")
            continue
        d = entry[0]()
        actual = (
            len(d.result.acceptance),
            len(d.result.exceptions or []),
            d.result.maturity,
        )
        claimed = (
            int(row.group("acceptance")),
            int(row.group("exceptions")),
            row.group("maturity"),
        )
        if actual != claimed:
            bad.append(
                f"{box_id}: 表里写着 {claimed[0]} 条判据 / {claimed[1]} 条例外 / "
                f"{claimed[2]}，实际是 {actual[0]} / {actual[1]} / {actual[2]}"
            )
    assert not bad, "\n".join(bad)


def _box_facts() -> dict[str, tuple[int, int]]:
    """目录 → (判据条数, 例外条数)。按目录读 definition，不靠目录名猜。"""
    facts: dict[str, tuple[int, int]] = {}
    for name in _box_dirs():
        module = importlib.import_module(f"boxes.{name}")
        d = module.get_definition()
        facts[name] = (len(d.result.acceptance), len(d.result.exceptions or []))
    return facts


_COUNT_KIND = {
    "判据": 0,
    "验收": 0,
    "acceptance": 0,
    "acceptance rule": 0,
    "acceptance rules": 0,
    "例外": 1,
    "exception": 1,
    "exception rule": 1,
    "exception rules": 1,
}


@pytest.mark.parametrize("box_dir", sorted(_box_facts()), ids=lambda d: d)
def test_box_readme_counts_match_definition(box_dir: str) -> None:
    """盒子 README 里写的「N 条判据 / N 条例外」必须等于 definition 里的实际条数。

    只在**盒子自己的 README** 上生效——根 README 的表格里有多只盒子，数字不带归属，
    拿不准就不查（拿不准就判红是给判据的规矩，这里是给人看的文档，规矩不一样）。
    """
    facts = _box_facts()
    if box_dir not in facts:
        pytest.skip(f"{box_dir} 不是已注册盒子")
    acc_n, exc_n = facts[box_dir]
    readme = ROOT / "boxes" / box_dir / "README.md"
    if not readme.exists():
        pytest.skip(f"boxes/{box_dir}/README.md 不存在")

    allowed = {0: {acc_n}, 1: {exc_n}}
    bad: list[str] = []
    for lineno, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), 1):
        for number, word in COUNT_PHRASE.findall(line):
            kind = _COUNT_KIND.get(word.lower().strip())
            if kind is None:
                continue
            if int(number) not in allowed[kind]:
                label = "判据" if kind == 0 else "例外"
                bad.append(
                    f"boxes/{box_dir}/README.md:{lineno}: 写了「{number} {word}」，"
                    f"实际是 {acc_n} 条判据 / {exc_n} 条例外"
                )
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------
# 7. 目录清单一致（根 README 列的 = 实际目录里的）
# --------------------------------------------------------------------------
#
# **为什么盯这个**：2026-11-04 审计发现根 README 的目录清单落后了 4 个文件
# （core/scaffold.py、core/conditions.py、core/field_map.py、runtime/resume.py
# 等），而「docs/ 9 份」实际是 10 份。清单漂了不报错，
# 读者照着 README 找文件会找不到。
#
# **为什么不写死每份清单**（跟「不许写死盒子数量」同族）：
# 那会变成「加一个文件就要改 README」——加产品的人替守卫还债。
# 所以反过来：**README 列了就必须存在，实际有就必须列上**。
# 加文件忘了写进 README → 清单守卫红（提醒补一句）。

_LISTED_DIRS = ("core/", "runtime/", "scripts/", "docs/", "fulfillment_scopes/")

# 清单行：`- \`xxx.py\` — 说明`（要求后面是破折号或行尾）
# 不写成「段内所有反引号」：段里可能有「见 `runtime/models.py`」这种行内引用，
# 那是**引用**不是清单，混进来会误报。
_LIST_ITEM = re.compile(r"^\s*-\s+`([^`\n]+\.(?:py|md|sh))`\s*(?:—|--|-|$)", re.M)


def _readme_listed_files(readme_text: str, directory: str) -> set[str]:
    """根 README 的「- **`dir/`**」段落里列出的文件名。"""
    head = re.search(r"^- \*\*`" + re.escape(directory) + r"`\*\*", readme_text, re.M)
    if head is None:
        return set()
    rest = readme_text[head.end():]
    nxt = re.search(r"^- \*\*`", rest, re.M)
    block = rest[: nxt.start()] if nxt else rest
    return {Path(name).name for name in _LIST_ITEM.findall(block)}


def _actual_dir_files(directory: str) -> set[str]:
    """目录里真实存在的文件（递归，忽略缓存与包入口）。"""
    out: set[str] = set()
    for p in (ROOT / directory).rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix not in (".py", ".md", ".sh"):
            continue
        out.add(p.name)
    return out


@pytest.mark.parametrize("directory", _LISTED_DIRS, ids=lambda d: d.rstrip("/"))
@requires_checkout
def test_the_root_readme_directory_listing_matches_the_disk(directory: str) -> None:
    """根 README 的目录清单必须等于实际目录内容。

    多一个（README 写了不存在的文件）或少一个（有文件没写）都红。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    listed = _readme_listed_files(readme, directory)
    actual = _actual_dir_files(directory)

    missing = sorted(actual - listed)   # 磁盘上有，README 没写
    ghost = sorted(listed - actual)     # README 写了，磁盘上没有

    bad: list[str] = []
    if missing:
        bad.append(f"{directory} 有这些文件但 README 没列：{missing}")
    if ghost:
        bad.append(f"{directory} README 列了这些文件但磁盘上没有：{ghost}")
    assert not bad, "\n".join(bad)


# --------------------------------------------------------------------------
# 8. 盒子 README 的规模数字（跳数 / op 数 / schema 数）
# --------------------------------------------------------------------------
#
# 判据条数早就守了（第 5 条），但**规模**没守：
# 「## 3 跳 beeline」「- \`schemas.py\` — 6 个 schema」「beeline 7 ops」
# 这些数字对不上时，读者照着文档理解盒子的复杂度和文档实际讲的东西不是一回事。
#
# 两条排除：
# - 「第 4 跳」是**序号**不是跳数（wms_requirement README 里有）→ 负向前查
# - 「N 个 op 的 handler」和「beeline N ops」指的是同一个数（op 数）

_HOPS_RE = re.compile(r"(?<!第 )(\d+)\s*跳")
_OPS_RE = re.compile(r"(\d+)\s*(?:ops\b|个\s*op\b)")
_SCHEMA_RE = re.compile(r"(\d+)\s*个\s*schema\b")


def _box_scale(box_dir: str) -> tuple[int, int]:
    """目录 → (op 数 = 跳数, schema 数)。"""
    module = importlib.import_module(f"boxes.{box_dir}")
    d = module.get_definition()
    b = module.get_beeline()
    return len(b.operations), len(d.schemas)


@pytest.mark.parametrize("box_dir", sorted(_box_dirs()), ids=lambda d: d)
def test_box_readme_scale_numbers_match_definition(box_dir: str) -> None:
    """盒子 README 里的「N 跳 / N ops / N 个 schema」必须等于实际。"""
    readme = ROOT / "boxes" / box_dir / "README.md"
    if not readme.exists():
        pytest.skip(f"boxes/{box_dir}/README.md 不存在")

    ops_n, schemas_n = _box_scale(box_dir)
    allowed = {
        "跳": {ops_n},
        "op": {ops_n},
        "schema": {schemas_n},
    }
    patterns = (("跳", _HOPS_RE), ("op", _OPS_RE), ("schema", _SCHEMA_RE))

    bad: list[str] = []
    for lineno, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), 1):
        for kind, pattern in patterns:
            for number in pattern.findall(line):
                if int(number) not in allowed[kind]:
                    bad.append(
                        f"boxes/{box_dir}/README.md:{lineno}: 写了「{number}」"
                        f"（{kind}），实际是 {ops_n} 跳 / {ops_n} 个 op / "
                        f"{schemas_n} 个 schema"
                    )
    assert not bad, "\n".join(bad)
