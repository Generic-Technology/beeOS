"""tests.test_box_naming - 盒子目录名 ↔ box_id 的命名守卫

**为什么要有这条**：目录名曾经和盒子对不上——
`boxes/modeling` 里住的是 `wms_design_box`，`boxes/requirement_capture` 里住的是
`wms_requirement_box`。而 `boxes/modeling` 这个名字还**在骗人**：
它不是通用设计盒，它的 `domain.py` 里是 31 处 WMS 专属词表，
2 条判据也只对 WMS 有意义。

**目录名是给人看的**——它得说清这只盒子是什么盒。
「名字该跟着事实走」这条，在看板层已经犯过一次
（BOX_META 那张表被搬回各盒的 definition），目录名是同一族。

这组守卫把规则钉死，且**规则本身从代码推出来，不另立一张表**：
- WMS 域的盒子：目录名 == box_id 去掉 `_box`
- 非 WMS 域的盒子：不许占用 `wms_` 前缀
- 打包清单（pyproject）里的包，必须跟磁盘上的目录一一对应
"""

from __future__ import annotations

import ast
import fnmatch
import pathlib
import re
import tomllib

import pytest

from domains import domains_of
from kanban.trigger import BOX_REGISTRY
from tests.conftest import requires_checkout

REPO = pathlib.Path(__file__).resolve().parent.parent
BOXES_DIR = REPO / "boxes"


def _imports_module(tree: ast.AST, module: str) -> bool:
    """这个文件里有没有真的 import 了这个模块（含 importlib 动态导入）。

    认三种写法：`import a.b`、`from a.b import x`、`importlib.import_module("a.b")`。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module or alias.name.startswith(module + "."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                if node.module == module or node.module.startswith(module + "."):
                    return True
        elif isinstance(node, ast.Call):
            func = node.func
            is_import_module = (
                isinstance(func, ast.Attribute) and func.attr == "import_module"
            ) or (
                isinstance(func, ast.Name) and func.id == "import_module"
            )
            if is_import_module and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value == module or arg.value.startswith(module + "."):
                        return True
    return False


def _package_of(box_id: str) -> str:
    """这只盒子住在哪个包里（从装载函数的 __module__ 读，不另立映射表）"""
    loader = BOX_REGISTRY[box_id][0]
    module = loader.__module__          # 例：boxes.wms_design.definition
    return module.split(".")[1]         # 例：wms_design


class TestTheDirectoryNameMatchesTheBox:
    @pytest.mark.parametrize("box_id", sorted(BOX_REGISTRY))
    def test_a_wms_box_lives_in_a_directory_named_after_its_id(self, box_id):
        """WMS 域的盒子：目录名 == box_id 去掉 `_box`

        wms_design_box → boxes/wms_design
        wms_requirement_box → boxes/wms_requirement
        """
        if "WMS" not in domains_of(box_id):
            pytest.skip("不是 WMS 域的盒子，不受这条约束")
        expected = box_id[:-len("_box")]
        assert _package_of(box_id) == expected, (
            f"{box_id} 住在 boxes/{_package_of(box_id)}/，"
            f"按命名规则应该是 boxes/{expected}/"
        )

    @pytest.mark.parametrize("box_id", sorted(BOX_REGISTRY))
    def test_only_wms_boxes_take_the_wms_prefix(self, box_id):
        """非 WMS 域的盒子不许占用 `wms_` 前缀

        否则「wms_* = WMS 域的盒子」这条规则又会失效——
        而读者正是靠它来判断一只盒子属于哪个域。
        """
        package = _package_of(box_id)
        if "WMS" in domains_of(box_id):
            assert package.startswith("wms_")
        else:
            assert not package.startswith("wms_"), (
                f"{box_id}（域={', '.join(domains_of(box_id)) or '未归域'}）"
                f"用了 wms_ 前缀——这会让「wms_* 就是 WMS 域」这条规则失效"
            )

    def test_no_directory_still_uses_the_old_names(self):
        """老目录名不许复活

        改名前是 boxes/modeling + boxes/requirement_capture；
        2026-10-28 又删掉了 boxes/inventory_shortage（订单异常样例盒）。
        留着它们 = 同一只盒子有两套地址 = 迟早有人 import 错的那个。
        """
        for old in ("modeling", "requirement_capture", "inventory_shortage"):
            assert not (BOXES_DIR / old).exists(), (
                f"boxes/{old} 又出现了——它不是产品，删掉的东西不该回来"
            )

    def test_the_retired_sample_box_is_not_registered(self):
        """退役的样例盒不许回到注册表

        订单异常盒（order_exception_box）2026-10-28 删除：它是样例不是产品，
        op 全是模拟返回值。它的回归只能靠测试层的合成夹具盒，
        而不是把样例重新挂上看板。
        """
        from kanban.trigger import BOX_REGISTRY

        assert "order_exception_box" not in BOX_REGISTRY
        assert "inventory_shortage" not in BOX_REGISTRY

    def test_nothing_still_imports_the_old_paths(self):
        """全仓不许还 import 老路径

        改名是一次性的：**引用改漏一处，症状是 ImportError**——
        而更糟的情况是有人顺手把老目录建回来，两套地址并存。

        **用 ast 解析，不做字符串 find**：这条守卫要验的是「有没有 import」，
        就该按 import 的方式读文件。曾经用 `old in text`，
        结果把「在字符串常量里列出退休名做禁词表」的守卫文件也判成 offender——
        守卫与守卫互相打架，最后两边都得加白名单。**验什么就用什么的方式读。**
        """
        offenders: list[str] = []
        for path in REPO.rglob("*.py"):
            if "__pycache__" in path.parts or ".git" in path.parts:
                continue
            if path.name == "test_box_naming.py":
                continue  # 这条守卫自己写着老路径字符串
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for old in ("boxes.modeling", "boxes.requirement_capture"):
                if _imports_module(tree, old):
                    offenders.append(f"{path.relative_to(REPO)} → {old}")
        assert not offenders, "还在 import 老路径：\n  " + "\n  ".join(offenders)


class TestThePackagingListMatchesDisk:
    @requires_checkout
    def test_the_declared_packages_all_exist(self):
        """pyproject 里写的包必须真的存在

        指向不存在的目录 → 打包直接报错；
        而它以前就写着三个早就不存在的 `beelines.*`。
        """
        data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        packages = data["tool"]["setuptools"]["packages"]
        missing = [p for p in packages if not (REPO / p.replace(".", "/")).is_dir()]
        assert not missing, f"pyproject 里这些包在磁盘上不存在：{missing}"

    @requires_checkout
    def test_every_box_package_is_declared(self):
        """每个盒子目录都得在打包清单里

        漏一个 → 装出来的 wheel 里没有那只盒子。
        """
        data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        packages = set(data["tool"]["setuptools"]["packages"])
        on_disk = {
            f"boxes.{p.name}"
            for p in BOXES_DIR.iterdir()
            if p.is_dir() and p.name != "__pycache__"
        }
        missing = sorted(on_disk - packages)
        assert not missing, f"这些盒子目录没写进 pyproject 的 packages：{missing}"

    @requires_checkout
    def test_every_package_on_disk_is_declared(self):
        """磁盘上**每一个包**都得在打包清单里——不只是盒子

        **这条守卫是补一次真漏**：打包清单漏了 `domains`（域声明）、
        `workshop`、`tests.fixtures`——装出来的 wheel 里
        **一个域声明都没有**，`from domains import DOMAIN_REGISTRY`
        直接 ModuleNotFoundError。而本地跑测试是绿的
        （测试从仓根跑，仓根什么都有）。

        **判据是「磁盘上带 `__init__.py` 的目录」**，
        不是「我这次想到的几个」——后者每次都会漏。
        """
        data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        packages = set(data["tool"]["setuptools"]["packages"])

        on_disk: set[str] = set()
        for init in REPO.rglob("__init__.py"):
            parts = init.relative_to(REPO).parts[:-1]
            if any(p in ("__pycache__", ".venv", "build", "dist") for p in parts):
                continue
            on_disk.add(".".join(parts))

        missing = sorted(on_disk - packages)
        assert not missing, (
            f"这些包没写进 pyproject 的 packages：{missing}\n"
            f"漏一个 = 装出来的 wheel 里没有它（本地测试照样全绿）"
        )

    @requires_checkout
    def test_every_package_data_file_is_declared(self):
        """包里的**非 .py 文件**都得被 package-data 声明

        漏一个 → 装出来的 wheel 里没有那个文件，而本地照样绿：
        - `kanban/vendor/*.js` → 看板的三维场景起不来
        - `boxes/wms_system/baseline/*.json` → 系统档案盒读不到基线
        - `tests/fixtures/` 的图片和真值表 → 票据语料测试拿不到样本
          （是「报错」，不是「跳过」）

        **怎么判**：一个文件归哪个包，取决于它所在目录是不是一个包
        （带 `__init__.py`）。不是包的那些子目录（比如 `fixtures/receipts/`）
        归它的**上一级包**管。
        """
        data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        packages = set(data["tool"]["setuptools"]["packages"])
        package_data = data["tool"]["setuptools"].get("package-data", {})
        # `"*"` 是 setuptools 的通配键（对每个包都生效）——它也要算进来
        wildcard = package_data.get("*", [])

        missing: list[str] = []
        for pkg in sorted(packages):
            pkg_dir = REPO / pkg.replace(".", "/")
            patterns = list(package_data.get(pkg, [])) + list(wildcard)
            for path in sorted(pkg_dir.rglob("*")):
                if not path.is_file():
                    continue
                if path.suffix == ".py":
                    continue
                if any(p in ("__pycache__", ".pytest_cache") for p in path.parts):
                    continue
                if path.name == ".DS_Store":
                    continue
                # 它属于哪个包：往上找**最深**那个带 __init__.py 的子目录。
                # （注意：`rel.parents` 里第一项是 `.`，
                #   那不是子目录——踩过这个坑：一度把
                #   「直接躺在包目录里的文件」全当成子包的，跳过了。）
                rel = path.relative_to(pkg_dir)
                parts = rel.parts[:-1]
                owner = pkg
                for i in range(len(parts), 0, -1):
                    if (pkg_dir / pathlib.Path(*parts[:i]) / "__init__.py").exists():
                        owner = f"{pkg}.{'.'.join(parts[:i])}"
                        break
                if owner != pkg:
                    continue  # 归子包管
                rel_str = str(rel)
                if not any(fnmatch.fnmatch(rel_str, pat) for pat in patterns):
                    missing.append(f"{pkg}/{rel_str}")

        assert not missing, (
            "这些文件没写进 package-data（装出来的 wheel 里没有它们）：\n  "
            + "\n  ".join(missing)
        )


class TestTheNamingRuleIsNotASecondTable:
    def test_the_rule_is_derived_not_declared(self):
        """规则从 box_id 和**域声明**推出来，不另立一张对照表

        另立一张表 = **第二份事实**：它会跟盒子自己的声明漂，
        而漂了没人报错——跟以前 BOX_META 那张表一个下场。
        """
        source = (REPO / "tests" / "test_box_naming.py").read_text(encoding="utf-8")
        # 除了本文件自己，不该有第二处硬编码的「目录名 ↔ box_id」对照
        offenders: list[str] = []
        for path in REPO.rglob("*.py"):
            if path.name == "test_box_naming.py" or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r'"wms_design"\s*:\s*"wms_design_box"', text) or \
               re.search(r'"modeling"\s*:\s*"wms_design_box"', text):
                offenders.append(str(path.relative_to(REPO)))
        assert not offenders, (
            "出现了硬编码的目录名↔box_id 对照表："
            f"{offenders}——规则应该从 box_id 自己推出来"
        )
        assert source  # 保持引用（这条守卫本身是活的）
