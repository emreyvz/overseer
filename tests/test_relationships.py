from server.relationships import RelationshipGraph


def test_pairwise_co_occurrence() -> None:
    g = RelationshipGraph()
    g.observe_together(["P-1", "P-2", "P-3"], "Gate", now=0.0)   # 3 pairs
    assert len(g.graph(min_count=1)["edges"]) == 3
    a = {x["id"] for x in g.for_entity("P-1", min_count=1)}
    assert a == {"P-2", "P-3"}


def test_needs_two_subjects() -> None:
    g = RelationshipGraph()
    g.observe_together(["P-1"], "Gate", now=0.0)         # alone -> no edge
    g.observe_together([], "Gate", now=1.0)
    assert g.graph(min_count=1)["edges"] == []


def test_confidence_grows_with_count_and_cameras() -> None:
    g = RelationshipGraph(saturate=20)
    for i in range(3):
        g.observe_together(["A", "B"], "Gate", now=float(i))
    weak = g.for_entity("A", min_count=1)[0]["confidence"]
    for i in range(3, 25):
        cam = "Gate" if i % 2 else "Lobby"
        g.observe_together(["A", "B"], cam, now=float(i))
    strong = g.for_entity("A", min_count=1)[0]
    assert strong["confidence"] > weak
    assert strong["confidence"] <= 1.0
    assert set(strong["cameras"]) == {"Gate", "Lobby"}


def test_graph_min_count_filter_and_reset() -> None:
    g = RelationshipGraph()
    g.observe_together(["A", "B"], "Gate", now=0.0)        # count 1
    g.observe_together(["A", "C"], "Gate", now=1.0)
    g.observe_together(["A", "C"], "Gate", now=2.0)        # A-C count 2
    edges = g.graph(min_count=2)["edges"]
    assert len(edges) == 1 and {edges[0]["a"], edges[0]["b"]} == {"A", "C"}
    g.reset()
    assert g.graph(min_count=1)["edges"] == []
