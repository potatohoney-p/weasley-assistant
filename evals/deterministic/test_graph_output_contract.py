"""Malformed custom-node outputs use the same recovery path as node errors."""

import pytest

from weasley_assistant.graph import START, Graph, Node, run_graph


@pytest.mark.parametrize("output", ["answer", ["key"], 7, [], "", False, 0,
                                   {"valid": 1, 2: "invalid"}, {None: "invalid"}])
def test_invalid_output_is_a_node_error_and_recovery_runs(output):
    graph = Graph("invalid-output")
    graph.add_node(Node("bad", lambda state: output, on_error="recover"))
    graph.add_node(Node("recover", lambda state: {"recovered": True}))
    graph.add_node(Node("after", lambda state: {"should_not_run": True}))
    graph.add_edge(START, "bad")
    graph.add_edge("bad", "after")
    events = []
    state = run_graph(graph, {}, observer=lambda kind, event: events.append((kind, event)))
    assert state["recovered"] is True
    assert "should_not_run" not in state
    assert "valid" not in state
    assert "TypeError" in state["errors"]["bad"]
    bad_end = next(event for kind, event in events if kind == "node_end" and event["node"] == "bad")
    assert bad_end["keys"] == []
    assert bad_end["error"] == state["errors"]["bad"]
    assert events[-1][0] == "graph_end"
    assert events[-1][1]["error"] == state["errors"]["bad"]
    assert events[-1][1]["path"] == ["bad", "recover"]


def test_invalid_output_drains_but_parallel_sibling_completes():
    graph = Graph("parallel-invalid-output")
    graph.add_node(Node("bad", lambda state: ["wrong"]))
    graph.add_node(Node("good", lambda state: {"ok": 1}))
    graph.add_node(Node("join", lambda state: {"joined": True}))
    for name in ("bad", "good"):
        graph.add_edge(START, name)
        graph.add_edge(name, "join")
    events = []
    state = run_graph(graph, {}, observer=lambda kind, event: events.append((kind, event)))
    assert state["ok"] == 1
    assert "joined" not in state
    assert "TypeError" in state["errors"]["bad"]
    assert events[-1][1]["path"] == ["bad", "good"]


@pytest.mark.parametrize("output", [None, {}, {"ok": 1, "_private": 2}])
def test_none_and_string_keyed_dict_outputs_remain_valid(output):
    graph = Graph("valid-output")
    graph.add_node(Node("source", lambda state: output))
    graph.add_node(Node("after", lambda state: {"ran": True}))
    graph.add_edge(START, "source")
    graph.add_edge("source", "after")
    state = run_graph(graph, {})
    assert state["ran"] is True
    assert state["errors"] == {}
    assert "_private" not in state
    if output:
        assert state["ok"] == 1
