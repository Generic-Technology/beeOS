"""core.contract —— 形状契约的**两层判定**，共用同一份类型规则

## 两层不是一回事

| | 判什么 | 谁在用 | 结果 |
|---|---|---|---|
| **声明层** | 两份**声明**之间相不兼容（下游接料形状 ⊆ 上游产出形状？）| `check_schema_compatibility` | `compatible` / `incompatible` / **`unknown`** |
| **产物层** | **真产物**能不能进某份声明 | `validate_payload`（在 `runtime/contract.py`）| 通过 / 报错 |

**为什么必须是两个操作**（2026-11-05 实测，这是本模块存在的理由）：

```
上游允许：status ∈ {ready, blocked}
下游允许：status ∈ {ready}

合成样例 ready   ：两边都通过 ✅
合法产出 blocked ：符合上游，过不了下游 ❌
```

「**存在**一个合规产出能过」≠「**所有**合规产出都能过」。
所以**拿一份样例去试，不能当兼容判定用**——它只能证明「存在」。

## 类型规则为什么住在 core

`runtime/contract.py` 的产物层校验和本模块的声明层判定，
**必须对「什么类型算兼容」有同一个答案**。
各写一份 = 两份事实 = 漂（`core/conditions.py` 的教训：同一套解析写两遍，
两边会选出不同的字段，守卫验的是它自己那套解析，于是放行了一条实际永远不触发的规则）。

所以**类型规则住在 core**，两层都从这里取。

## `unknown` 是一等结果

「上游声明是 plain string」**推不出**「上游不会产出下游不接的值」。
把它当成 `compatible`，就是**用一个猜测换掉一个证明**。

**上游声明越精确，`unknown` 的地盘越小**——
所以发现 `unknown` 时的正确动作是**去改上游的声明**，不是加更多猜测。

**本模块不含任何业务知识**：它只认 `core.models` 的形状。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.models import FieldDef, SchemaDef

# ============================================================
# 结果类型
# ============================================================

COMPATIBLE = "compatible"
INCOMPATIBLE = "incompatible"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Compatibility:
    """一次声明层兼容判定的结果

    **`unknown` 不是失败，也不是通过**——它是「判不了」。
    把它当 `compatible` = 用猜测换证明；把它当 `incompatible` = 冤枉人。
    """

    status: str
    reason: str = ""
    # 判不出来的**具体位置**（字段路径），让人知道该去改哪份声明
    path: str = ""

    @property
    def ok(self) -> bool:
        return self.status == COMPATIBLE

    def __bool__(self) -> bool:  # pragma: no cover - 只是好用
        return self.ok


def _ok(reason: str = "") -> Compatibility:
    return Compatibility(COMPATIBLE, reason)


def _bad(reason: str, path: str = "") -> Compatibility:
    return Compatibility(INCOMPATIBLE, reason, path)


def _unknown(reason: str, path: str = "") -> Compatibility:
    return Compatibility(UNKNOWN, reason, path)


# ============================================================
# 类型规则（声明层与产物层共用）
# ============================================================

# 类型的「族」：跨族一定不兼容。
_FAMILY = {
    "string": "text",
    "enum": "text",
    "boolean": "bool",
    "boolean_or_na": "bool",
    "number": "num",
    "number_or_na": "num",
    "integer": "num",
}

# 「n/a」字面量：三态类型带的那一档。
_NA = "n/a"

# 下游要求 number 时，上游哪些类型**一定**给得出数
_NUM_OK = {"number", "integer", "number_or_na"}
# 下游要求 integer 时，上游哪些类型**一定**给得出整数
_INT_OK = {"integer"}
# 下游要求 boolean 时，上游哪些类型**一定**给得出布尔
_BOOL_OK = {"boolean"}


def scalar_compatibility(up: FieldDef, down: FieldDef, path: str = "") -> Compatibility:
    """标量类型之间的兼容判定（`enum` 单独处理）

    **判据是「上游可能产出的值集合 ⊆ 下游收下的值集合」**——
    所以带 `or_na` 的上游喂给不带的下游是**不兼容**（上游可能产 `"n/a"`），
    而上游 `integer` 喂给下游 `number` 是**兼容**（整数是数）。

    方向很重要：`up` 是**产出方**，而 `down` 是**接料方**。
    """
    up_t, down_t = up.type, down.type

    if up_t.startswith("ref:") or down_t.startswith("ref:"):
        # ref 的兼容要看被引用的那份声明，交给调用方递归处理
        return _unknown("ref: 的兼容要看被引用的声明", path)

    if down_t == "enum":
        return _enum_compatibility(up, down, path)

    up_fam = _FAMILY.get(up_t)
    down_fam = _FAMILY.get(down_t)
    if up_fam is None or down_fam is None:
        return _unknown(f"不认识的类型：上游 {up_t!r} / 下游 {down_t!r}", path)
    if up_fam != down_fam:
        return _bad(f"类型族不同：上游 {up_t!r}、下游 {down_t!r}", path)

    # 同族之内，细化
    if down_fam == "num":
        if down_t == "number":
            if up_t in _NUM_OK and up_t != "number_or_na":
                return _ok()
            if up_t == "number_or_na":
                return _bad(
                    f"上游是 {up_t!r}，可能产出 {_NA!r}——下游只收 number", path
                )
            return _bad(f"上游 {up_t!r} 喂不了下游 number", path)
        if down_t == "integer":
            if up_t in _INT_OK:
                return _ok()
            if up_t == "number":
                return _bad("上游是 number，可能产出小数——下游只收 integer", path)
            if up_t == "number_or_na":
                return _bad(
                    f"上游是 number_or_na，可能产出 {_NA!r} 或小数——下游只收 integer", path
                )
            return _bad(f"上游 {up_t!r} 喂不了下游 integer", path)
        # down == number_or_na：数、整数、三态数都收得下
        return _ok()

    if down_fam == "bool":
        if down_t == "boolean":
            if up_t in _BOOL_OK:
                return _ok()
            if up_t == "boolean_or_na":
                return _bad(
                    f"上游是 {up_t!r}，可能产出 {_NA!r}——下游只收 boolean", path
                )
            return _bad(f"上游 {up_t!r} 喂不了下游 boolean", path)
        # down == boolean_or_na
        return _ok()

    # text 族
    if down_t == "string":
        return _ok()
    return _unknown(f"下游 {down_t!r} 的兼容判不了", path)


def _enum_compatibility(up: FieldDef, down: FieldDef, path: str) -> Compatibility:
    """下游要 enum，上游能不能保证只产那几个值"""
    down_values = frozenset(str(v) for v in (down.values or []))
    if not down_values:
        return _unknown("下游 enum 没声明值集", path)

    if up.type == "enum":
        up_values = frozenset(str(v) for v in (up.values or []))
        if not up_values:
            return _unknown(
                "上游 enum 没声明值集——判不出它会不会产出下游不接的值", path
            )
        extra = up_values - down_values
        if extra:
            return _bad(
                "上游 enum 的值集不是下游的子集，多出："
                + "、".join(sorted(extra)),
                path,
            )
        return _ok()

    if up.type == "string":
        return _unknown(
            "上游是 plain string——判不出它会不会产出下游 enum 之外的值", path
        )
    if up.type in ("boolean", "boolean_or_na"):
        return _bad(f"上游 {up.type!r} 产布尔，下游要 enum 字符串", path)
    return _unknown(f"上游 {up.type!r} 喂不了下游 enum", path)


# ============================================================
# 声明层：两份 schema 的兼容判定
# ============================================================

def _resolve(schema_id: str, schemas: Any) -> Optional[SchemaDef]:
    """按 id 找 schema（schemas 可以是列表或 id→SchemaDef 映射）"""
    if not schemas:
        return None
    if isinstance(schemas, dict):
        found = schemas.get(schema_id)
        return found if isinstance(found, SchemaDef) else None
    for s in schemas:
        if isinstance(s, SchemaDef) and s.id == schema_id:
            return s
    return None


def _fields_of(schema: SchemaDef) -> dict[str, FieldDef]:
    """字段名 → 字段定义

    **不叫 `_field_map`**：那个名字属于已经退休的 `core/field_map.py`
    （它派生的是**接线**）。同名会让人以为这里也在做接线那件事。
    """
    return {f.name: f for f in schema.fields}


def check_schema_compatibility(
    upstream: SchemaDef,
    downstream: SchemaDef,
    *,
    upstream_schemas: Any = None,
    downstream_schemas: Any = None,
    path: str = "",
) -> Compatibility:
    """下游的接料形状，能不能被上游的产出形状**保证**满足

    判的是**声明之间的包含关系**，不是拿某一份产物去试。

    Args:
        upstream: 上游声明的产出形状
        downstream: 下游声明的接料形状
        upstream_schemas: 解析上游 `ref:` 用的 schema 集（上游自己的）
        downstream_schemas: 解析下游 `ref:` 用的 schema 集（下游自己的）

    Returns:
        `compatible` / `incompatible` / **`unknown`**
    """
    up_schemas = upstream_schemas if upstream_schemas is not None else [upstream]
    down_schemas = downstream_schemas if downstream_schemas is not None else [downstream]

    up_fields = _fields_of(upstream)
    down_fields = _fields_of(downstream)

    # 下游必填的字段，上游必须**有**
    for name, dfield in down_fields.items():
        here = f"{path}.{name}" if path else name
        ufield = up_fields.get(name)
        if ufield is None:
            if dfield.required:
                return _bad(
                    f"下游必填字段 {name!r}，上游产出形状里没有这个字段", here
                )
            continue
        # 上游把它声明成**可选** → 上游可能不产出它 →
        # 下游的必填要求就**保证不了**。
        #
        # **为什么不能当 compatible**：这个函数问的是
        # 「下游的接料形状能不能被上游的产出形状**保证**满足」。
        # 上游说「这个字段我可能不给」= 它明确说了自己会给不全，
        # 这不是「推不出来」（unknown），是**能推出来**——
        # 推出来的是「不保证」。
        #
        # **该修的是上游的声明**（把它标成 required），不是放宽这条判定：
        # 放宽 = 装配期放行一份保证不了的接线，
        # 后果是运行时才缺字段（那时看板已经照常渲染了）。
        if dfield.required and not ufield.required:
            return _bad(
                f"下游必填字段 {name!r}，上游把它声明成可选——"
                f"上游可能不产出它，交接会缺字段"
                f"（该把上游的声明标成必填）",
                here,
            )
        res = _field_compatibility(
            ufield, dfield, up_schemas, down_schemas, here
        )
        if res.status != COMPATIBLE:
            return res
    return _ok()


def _field_compatibility(
    up: FieldDef,
    down: FieldDef,
    up_schemas: Any,
    down_schemas: Any,
    path: str,
) -> Compatibility:
    """一对字段的兼容判定（含 object / array / ref 的递归）"""
    up_t, down_t = up.type, down.type

    # ---- ref: 递归到被引用的那份声明 ----
    if up_t.startswith("ref:"):
        target = _resolve(up_t[4:], up_schemas)
        if target is None:
            return _unknown(f"上游的 {up_t!r} 解析不到", path)
        up_resolved = target
    else:
        up_resolved = None

    if down_t.startswith("ref:"):
        target = _resolve(down_t[4:], down_schemas)
        if target is None:
            return _unknown(f"下游的 {down_t!r} 解析不到", path)
        # 上游也是 ref → 比两份被引用的声明
        if up_resolved is not None:
            return check_schema_compatibility(
                up_resolved,
                target,
                upstream_schemas=up_schemas,
                downstream_schemas=down_schemas,
                path=path,
            )
        # 上游不是 ref → 上游的产出得**长得像**下游引用的那份声明
        return check_schema_compatibility(
            SchemaDef(id=f"{up.name}_shape", fields=up.properties or []),
            target,
            upstream_schemas=up_schemas,
            downstream_schemas=down_schemas,
            path=path,
        )

    if up_resolved is not None:
        # 上游是 ref、下游不是 → 下游要的那份形状，得被上游引用的那份声明满足
        return check_schema_compatibility(
            up_resolved,
            SchemaDef(id=f"{down.name}_shape", fields=down.properties or []),
            upstream_schemas=up_schemas,
            downstream_schemas=down_schemas,
            path=path,
        )

    # ---- object：递归进 properties ----
    if up_t == "object" and down_t == "object":
        up_props = {p.name: p for p in (up.properties or [])}
        for p in (down.properties or []):
            here = f"{path}.{p.name}" if path else p.name
            u = up_props.get(p.name)
            if u is None:
                if p.required:
                    return _bad(
                        f"下游 object 必填子字段 {p.name!r}，上游没有", here
                    )
                continue
            # 跟顶层同一个道理：上游说「这个子字段我可能不给」
            if p.required and not u.required:
                return _bad(
                    f"下游 object 必填子字段 {p.name!r}，上游把它声明成可选——"
                    f"上游可能不产出它，交接会缺字段",
                    here,
                )
            res = _field_compatibility(u, p, up_schemas, down_schemas, here)
            if res.status != COMPATIBLE:
                return res
        return _ok()

    # ---- array：递归进 items ----
    if up_t == "array" and down_t == "array":
        if up.items is None or down.items is None:
            return _ok()  # 没声明 items → 没有更细的要求
        return _field_compatibility(
            up.items, down.items, up_schemas, down_schemas, f"{path}[]"
        )

    # ---- 标量 / enum ----
    return scalar_compatibility(up, down, path)
