"""
runtime.contract - 投料契约校验（Structural Contract Gate）

按 [beebox-design.md §3.4] 实现履约前第一道闸：

    投料口 → 结构契约校验 → BeelineExecutor → acceptance → 落盘

设计边界（重要）：
- 这一层只回答"形状对不对"——字段缺没缺、类型对不对、枚举值合不合法
- 这一层**不回答**"内容够不够"——那是 acceptance 的业务验收职责
- 因此本模块不 import 任何 boxes/ 业务知识：runtime 不该知道
  "业务流程 / 业务规则"怎么定义，那是 definition 声明的业务语义

一个空需求集在结构上是合法的（0 条违规）——它能通过本闸，然后在
acceptance 被业务判据拦下。两个闸各管一段，不互相替代。
"""

from __future__ import annotations

from typing import Any, Optional

from core.models import FieldDef, SchemaDef, is_not_applicable


# 媒体字段（media=image）的值能有多大。
#
# 8 MB 是**投料口那条线**的同一个数（前端 MEDIA_MAX_BYTES）——
# 两边不一样的话，就会出现「前端放行、后端拒」或者反过来，
# 而用户看到的报错会指向一个他改不动的地方。
MEDIA_MAX_BYTES = 8 * 1024 * 1024


# ============================================================
# 单字段校验
# ============================================================

# bool 是 int 的子类，python 里 isinstance(True, int) 为 True，
# 数字校验时必须先把 bool 排除，否则 boolean 会被当成 integer 放过
_PY_TYPE_CHECKS: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, (list, tuple)),
    # 三态布尔：true / false / "n/a"（判据对本次产出用不上）。
    #
    # 单独开一个类型，而不是让 "boolean" 顺带放行 "n/a"——
    # 后者会把**全系统**每一个布尔字段的口径都悄悄放松，而且放松得
    # 看不出来。只有确实会出现三态的字段才声明 boolean_or_na，
    # 声明它就是在说「这条判据有可能不适用」，这句话要显式。
    "boolean_or_na": lambda v: isinstance(v, bool) or is_not_applicable(v),
    # 三态数字：数字 / "n/a"（这次这个量用不上）。
    #
    # 跟 boolean_or_na 同一个道理，单独开一个类型：
    # 让 "number" 顺带放行 "n/a"，会把**全系统**每一个数值的口径都放松。
    #
    # 为什么需要它：`case_pass_rate` 的分母是「有确定结论的观测数」，
    # 一条都没验出来时分母是 0 —— 那是 **n/a**（没验到），
    # 不是 0%（全都没过）。可它**必须是个数字**才能过下游的交接校验
    # （运维盒声明的形状是 number）——「n/a」字符串会让交接直接失败，
    # 而失败原因离现场隔着两层。
    "number_or_na": (
        lambda v: (isinstance(v, (int, float)) and not isinstance(v, bool))
        or is_not_applicable(v)
    ),
}


def _check_scalar(field: FieldDef, value: Any, path: str, errors: list[str]) -> None:
    """校验标量字段（string / number / integer / boolean / enum）"""
    if field.type == "enum":
        if not isinstance(value, str):
            errors.append(f"{path}: expected enum string, got {type(value).__name__}")
        elif field.values and value not in field.values:
            errors.append(
                f"{path}: '{value}' not in {sorted(str(v) for v in field.values)}"
            )
        return

    check = _PY_TYPE_CHECKS.get(field.type)
    if check is None:  # ref:<schema_id> 等暂不做深校验，放行
        return
    if not check(value):
        errors.append(
            f"{path}: expected {field.type}, got {type(value).__name__}"
        )


def _resolve_ref(schema_id: str, schemas: Any) -> Optional[SchemaDef]:
    """按 id 找 `ref:` 指向的 schema

    `schemas` 可以是 SchemaDef 列表，也可以是 id → SchemaDef 的映射。
    """
    if not schemas:
        return None
    if isinstance(schemas, dict):
        found = schemas.get(schema_id)
        return found if isinstance(found, SchemaDef) else None
    for s in schemas:
        if isinstance(s, SchemaDef) and s.id == schema_id:
            return s
    return None


def _missing_field_error(path: str, field: FieldDef) -> str:
    """缺必填字段的报错：带上字段自己声明的说明

    光说「required field missing」是在**让投料方自己猜原因**：可能是忘了这个
    字段，也可能是字段**改了名**（旧产出里还是旧名）。

    字段的 description 就是这条契约想对投料方说的话——藏着不说，
    等于把「你大概不知道字段改名了」这件事也一起藏了。
    """
    prefix = f"{path}." if path else ""
    base = f"{prefix}{field.name}: required field missing"
    desc = (field.description or "").strip()
    return f"{base}（{desc}）" if desc else base


def _check_media(field: FieldDef, value: Any, path: str, errors: list[str]) -> None:
    """媒体字段的值：必须是一张**认得的**图（data URL 或 http 直链）

    报错都要带能照着做的材料：收什么格式、怎么改。
    """
    if not isinstance(value, str) or not value.strip():
        errors.append(
            f"{path}: 媒体字段要一张图片（data URL 或 http 直链），现在拿到的是 "
            f"{type(value).__name__}"
        )
        return

    text = value.strip()
    if text.lower().startswith("data:"):
        head = text[5:].split(";")[0].strip().lower()
        allowed = field.accepted_media_types()
        if head not in allowed:
            errors.append(
                f"{path}: 不收 {head or '（没写 MIME）'}——这里只收 "
                f"{' / '.join(allowed)}。"
                "手机拍的照片默认是 HEIC，先在相册里另存为 JPEG 再投。"
            )
            return
        # base64 长度 ≈ 原始字节 × 4/3
        payload = text.partition(",")[2]
        approx = int(len(payload) * 3 / 4)
        if approx > MEDIA_MAX_BYTES:
            errors.append(
                f"{path}: 图片约 {approx / 1048576:.1f} MB，超过 "
                f"{MEDIA_MAX_BYTES // 1048576} MB 上限——"
                "拍小一点，或者裁掉没用的部分再投"
            )
        return

    if text.lower().startswith(("http://", "https://")):
        return

    errors.append(
        f"{path}: 这不是图片——媒体字段只收 data URL 或 http(s) 直链，"
        f"现在拿到的是 {text[:40]!r}"
    )


def _check_field(
    field: FieldDef, value: Any, path: str, errors: list[str], schemas: Any = None
) -> None:
    """校验单个字段（含 object / array / ref 递归）"""
    if value is None:
        # required=True 时显式 None 视同缺失；缺字段由上层 required 检查兜
        return

    # ---- media: 媒体字段 ----
    #
    # 媒体字段的值是一张图（data URL 或 http 直链）。
    # 形状上它就是个字符串，所以**光靠类型校验它会一路放行到模型接口**，
    # 到那儿才来一句 400（invalid param: image data url media type
    # [...] not supported）——那是确定性拒绝，重试无用，
    # 而且用户等了一轮才看到一句读不懂的 JSON。
    #
    # 所以在这一层就查两件事：**是什么格式**（在字段自己声明的白名单里吗）
    # 和 **多大**。白名单归字段所有（`media_accept`）——
    # 盒子说「我收 HEIC」（它在盒子里转 JPEG），前端就不该替它改口径。
    if field.media:
        _check_media(field, value, path, errors)
        return

    # ---- ref: 引用类型 ----
    #
    # 以前这里是「ref:<schema_id> 等暂不做深校验，放行」——
    # **声明了约束但没人执行**。后果是：投料里那个字段装什么都过，
    # 交接时传一份完全不相干的对象也能跑完整条链。
    #
    # 现在深校验：值必须真的符合被引用 schema 的形状。
    # 引用解析不到 → **报错，不放行**：一条校验不了的约束等于没有约束，
    # 而「没有约束」比「约束错了」更难发现。
    if field.type.startswith("ref:"):
        target_id = field.type[len("ref:"):]
        target = _resolve_ref(target_id, schemas)
        if target is None:
            errors.append(
                f"{path}: ref:{target_id} 无法解析——"
                f"校验方没有提供可解析的 schemas，或该 id 不在其中"
            )
            return
        _check_schema(value, target, path, errors, schemas)
        return

    if field.type == "object":
        if not isinstance(value, dict):
            errors.append(
                f"{path}: expected object, got {type(value).__name__}"
            )
            return
        for sub in field.properties or []:
            if sub.name not in value:
                if sub.required:
                    errors.append(_missing_field_error(path, sub))
                continue
            _check_field(sub, value[sub.name], f"{path}.{sub.name}", errors, schemas)
        # 未声明的多余字段不报错（模型可扩展）
        return

    if field.type == "array":
        if not isinstance(value, (list, tuple)):
            errors.append(
                f"{path}: expected array, got {type(value).__name__}"
            )
            return
        if field.items is not None:
            for i, item in enumerate(value):
                _check_field(field.items, item, f"{path}[{i}]", errors, schemas)
        return

    _check_scalar(field, value, path, errors)


def _check_schema(
    value: Any, schema: SchemaDef, path: str, errors: list[str], schemas: Any = None
) -> None:
    """按一个 SchemaDef 校验一份值（ref: 深校验的落点）"""
    if not isinstance(value, dict):
        errors.append(
            f"{path}: expected object matching {schema.id}, "
            f"got {type(value).__name__}"
        )
        return
    for field in schema.fields:
        if field.name not in value:
            if field.required:
                errors.append(_missing_field_error(path, field))
            continue
        _check_field(field, value[field.name], f"{path}.{field.name}", errors, schemas)


# ============================================================
# 对外：按 SchemaDef 校验 payload
# ============================================================


def validate_payload(payload: Any, schema: SchemaDef, *, schemas: Any = None) -> list[str]:
    """按业务 schema 校验投料 payload

    Args:
        payload: 投料内容（dict）
        schema: definition.schemas 里的 schema 定义
        schemas: **本 definition 的全部 schemas**（列表或 id→SchemaDef 映射）。
            用来解析 `ref:<schema_id>`。
            **不传就等于放弃校验 ref 字段**——所以本模块选择
            「ref 解析不到就报错」，逼调用方把它交出来。

    Returns:
        错误描述列表；空列表 = 通过结构契约
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return [f"payload: expected object, got {type(payload).__name__}"]

    for field in schema.fields:
        if field.name not in payload:
            if field.required:
                errors.append(_missing_field_error("", field))
            continue
        _check_field(field, payload[field.name], field.name, errors, schemas)

    return errors
