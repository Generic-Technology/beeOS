import pytest

from core.contract import COMPATIBLE, INCOMPATIBLE, check_schema_compatibility
from core.models import FieldDef, SchemaDef
from runtime.contract import validate_payload


@pytest.mark.parametrize("container", ["root", "object", "array", "ref"])
def test_optional_producer_cannot_guarantee_required_consumer(container):
    def schema(required):
        leaf = FieldDef(name="id", type="string", required=required)
        if container == "root":
            return SchemaDef(id="record", fields=[leaf])
        if container == "ref":
            return SchemaDef(id="record", fields=[leaf])
        field = FieldDef(name="record", type="object", properties=[leaf])
        if container == "array":
            field = FieldDef(name="records", type="array", items=field)
        return SchemaDef(id="record", fields=[field])

    up, down = schema(False), schema(True)
    payload = {"root": {}, "object": {"record": {}},
               "array": {"records": [{}]}, "ref": {}}[container]
    if container == "ref":
        wrapper = SchemaDef(id="wrapper", fields=[
            FieldDef(name="record", type="ref:record"),
        ])
        result = check_schema_compatibility(
            wrapper, wrapper, upstream_schemas=[up, wrapper],
            downstream_schemas=[down, wrapper],
        )
    else:
        result = check_schema_compatibility(up, down)
    assert validate_payload(payload, up) == []
    assert validate_payload(payload, down)
    assert result.status == INCOMPATIBLE
    assert result.path.endswith("id")


def test_consumer_may_relax_requiredness():
    up = SchemaDef(id="up", fields=[FieldDef(name="id", type="string")])
    down = SchemaDef(id="down", fields=[
        FieldDef(name="id", type="string", required=False),
    ])
    assert check_schema_compatibility(up, down).status == COMPATIBLE
