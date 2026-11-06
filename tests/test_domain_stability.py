from core.domain import assemble_domain
from core.models import EdgeKind
from domains import DOMAIN_REGISTRY
from kanban.trigger import box_facts


def test_handoff_cannot_opt_out_of_its_channel():
    declaration = DOMAIN_REGISTRY["WMS"].model_copy(deep=True)
    declaration.edges[0].via = None
    declaration.edges[0].no_channel = True
    assembled = assemble_domain(declaration, box_facts())
    assert not assembled.ok
    assert any("D3" in str(p) or "D12" in str(p) for p in assembled.problems)


def test_failed_business_rule_rejects_assembly_even_when_edges_are_valid():
    declaration = DOMAIN_REGISTRY["WMS"].model_copy(deep=True)
    removed = next(e for e in declaration.edges if e.kind == EdgeKind.archive)
    declaration.edges.remove(removed)
    assembled = assemble_domain(declaration, box_facts())
    failed = [r for r in assembled.rules_reading if not r.ok]
    assert any(r.kind == "all_archived" for r in failed)
    assert not assembled.ok
    assert any("all_archived" in str(p) for p in assembled.problems)


def test_existing_domains_remain_compatible():
    for declaration in DOMAIN_REGISTRY.values():
        assembled = assemble_domain(declaration, box_facts())
        assert assembled.ok, [str(p) for p in assembled.problems]
