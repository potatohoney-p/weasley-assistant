"""Router failures stay observable and use the graph's existing recovery path."""
import pytest

from weasley_assistant.graph import END, START, Graph, Node, run_graph


def failing_route(_state):
    raise ValueError("synthetic routing failure")


def fixture(route, recovery=None):
    graph = Graph("router-failure")
    graph.add_node(Node("decide", lambda s: {"decision_completed": True}, on_error=recovery))
    graph.add_node(Node("normal", lambda s: {"normal_ran": True}))
    graph.add_node(Node("recover", lambda s: {"recovered_error": s["errors"]["decide"]}))
    graph.add_edge(START, "decide")
    graph.add_edge("decide", "normal")
    graph.add_router("decide", route, {"normal": "normal"})
    return graph


@pytest.mark.parametrize("route, diagnostic", [
    (failing_route, "ValueError"),
    (lambda s: s["missing"], "KeyError"),
    (lambda s: [], "TypeError"),
    (lambda s: {}, "TypeError"),
    (lambda s: None, "TypeError"),
    (lambda s: 0, "TypeError"),
    (lambda s: object(), "TypeError"),
    (lambda s: "unknown", "unknown label"),
])
@pytest.mark.parametrize("recovery", [None, "recover"])
def test_router_failure_drains_or_recovers_with_final_trace(route, diagnostic, recovery):
    graph = fixture(route, recovery)
    events = []
    state = run_graph(graph, {}, lambda kind, event: events.append((kind, event)))
    assert state["decision_completed"] is True
    assert "normal_ran" not in state
    assert diagnostic in state["errors"]["decide"]
    if recovery:
        assert state["recovered_error"] == state["errors"]["decide"]
    else:
        assert "recovered_error" not in state
    route_event = next(event for kind, event in events if kind == "route")
    assert route_event["target"] == (recovery or END)
    assert route_event["error"] == state["errors"]["decide"]
    assert events[-1][0] == "graph_end"
    assert events[-1][1]["error"] == state["errors"]["decide"]
    assert events[-1][1]["path"] == (["decide", "recover"] if recovery else ["decide"])
    # Computation completed successfully; routing is a distinct trace event.
    node_end = next(event for kind, event in events if kind == "node_end")
    assert node_end["error"] is None


def test_failed_router_does_not_discard_completed_parallel_branch():
    graph = fixture(failing_route, "recover")
    graph.add_node(Node("sibling", lambda s: {"sibling_result": 42}))
    graph.add_edge(START, "sibling")
    state = run_graph(graph, {})
    assert state["sibling_result"] == 42
    assert "ValueError" in state["recovered_error"]


@pytest.mark.parametrize("max_steps, expected_steps", [(25, 2), (1, 1)])
def test_error_recovery_cycles_still_obey_both_guards(max_steps, expected_steps):
    graph = Graph("recover-cycle")
    graph.add_node(Node("repeat", lambda s: {}, max_visits=2, on_error="repeat"))
    graph.add_edge(START, "repeat")
    graph.add_router("repeat", failing_route, {"done": END})
    events = []
    state = run_graph(graph, {}, lambda kind, ev: events.append((kind, ev)), max_steps=max_steps)
    assert events[-1][1]["steps"] == expected_steps
    assert "ValueError" in state["errors"]["repeat"]
    if max_steps == 1:
        assert "max_steps=1" in state["errors"]["engine"]


def test_observer_failure_is_not_misclassified_as_router_failure():
    graph = fixture(lambda s: "normal")

    def observer(kind, event):
        if kind == "route":
            raise RuntimeError("observer failed")

    with pytest.raises(RuntimeError, match="observer failed"):
        run_graph(graph, {}, observer)


def test_keyboard_interrupt_is_not_swallowed_as_a_router_error():
    def interrupt(state):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_graph(fixture(interrupt), {})


def test_empty_string_is_a_valid_configured_label():
    graph = fixture(lambda s: "")
    graph.add_router("decide", lambda s: "", {"": "normal"})
    state = run_graph(graph, {})
    assert state["normal_ran"] is True
    assert state["errors"] == {}
