"""Test for using three new node types in a valid DSL."""

from helpers import login_owner

# DSL with three new node types: trigger -> variable_aggregator -> template
# Trigger outputs a value, variable_aggregator passes it through, template formats it.
_THREE_NEW_NODE_DOC = {
    "version": "1",
    "nodes": [
        {"id": "trg", "type": "trigger",
         "params": {"kind": "manual", "config": {"value": "Hello, World!"}}},
        {"id": "va", "type": "variable_aggregator",
         "params": {"strategy": "first_non_null"}},
        {"id": "tpl", "type": "template",
         "params": {"template": "Value: {value}"}},
    ],
    "edges": [
        {"from": "trg", "to": "va"},
        {"from": "va", "to": "tpl"},
    ],
}


def test_three_new_node_validate_and_run(client):
    """Validate and run a DSL with three new node types."""
    headers = login_owner(client)
    # Validate
    r = client.post("/api/dsl-canvas/validate", json={"dsl": _THREE_NEW_NODE_DOC},
                    headers=headers)
    assert r.status_code == 200
    assert r.json() == {"valid": True, "topological_order": ["trg", "va", "tpl"]}

    # Run
    r = client.post("/api/dsl-canvas/runs", json={"dsl": _THREE_NEW_NODE_DOC},
                    headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "succeeded"
    # The output should be the result of the template node: "Value: Hello, World!"
    assert body["output"] == "Value: Hello, World!"

    # GET the run details to check logs
    run_id = body["run_id"]
    r = client.get(f"/api/dsl-canvas/runs/{run_id}", headers=headers)
    assert r.status_code == 200
    fetched = r.json()
    assert [l["node_id"] for l in fetched["logs"]] == ["trg", "va", "tpl"]
    # Check that the template node's output is as expected
    assert fetched["logs"][2]["output"] == "Value: Hello, World!"
