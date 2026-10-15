from search_agent.core.signature import intent_signature
from search_agent.models.ir import (
    CandidateSearchIR,
    IRMeta,
    SemanticConditionIR,
    StructuredConditionIR,
    WhereNode,
)


def _leaf(field, op, values):
    return WhereNode(
        type="leaf", condition=StructuredConditionIR(field=field, operator=op, values=values)
    )


def _and(*children):
    return WhereNode(type="and", children=list(children))


def _ir(where, semantic=None, meta=None):
    return CandidateSearchIR(where=where, semantic=semantic or [], meta=meta or IRMeta())


def test_and_children_order_independent():
    a = _ir(_and(_leaf("skill", "any", ["Python", "Java"]), _leaf("city", "eq", ["北京"])))
    b = _ir(_and(_leaf("city", "eq", ["北京"]), _leaf("skill", "any", ["Python", "Java"])))
    assert intent_signature(a) == intent_signature(b)


def test_different_where_different_signature():
    assert intent_signature(_ir(_leaf("skill", "any", ["Python"]))) != intent_signature(
        _ir(_leaf("skill", "any", ["Java"]))
    )


def test_weight_softtext_required_dropped():
    c1 = StructuredConditionIR(
        field="skill", operator="any", values=["Python"], required=True, weight=2.0, softText="x"
    )
    c2 = StructuredConditionIR(
        field="skill", operator="any", values=["Python"], required=False, weight=1.0
    )
    assert intent_signature(_ir(WhereNode(type="leaf", condition=c1))) == intent_signature(
        _ir(WhereNode(type="leaf", condition=c2))
    )


def test_value_casefold_and_order():
    a = _ir(_leaf("skill", "any", ["python", "Java"]))
    b = _ir(_leaf("skill", "any", ["JAVA", "Python"]))
    assert intent_signature(a) == intent_signature(b)


def test_semantic_difference_matters():
    s1 = SemanticConditionIR(text="team mgmt", fields=["work_vector"])
    s2 = SemanticConditionIR(text="leadership", fields=["work_vector"])
    assert intent_signature(_ir(_leaf("work_years", "gte", [5]), [s1])) != intent_signature(
        _ir(_leaf("work_years", "gte", [5]), [s2])
    )


def test_meta_expandscope_matters():
    m1 = IRMeta(expandScope="peers", peerHint="industry_peers")
    m2 = IRMeta()
    assert intent_signature(_ir(_leaf("company", "any", ["玛氏"]), meta=m1)) != intent_signature(
        _ir(_leaf("company", "any", ["玛氏"]), meta=m2)
    )
