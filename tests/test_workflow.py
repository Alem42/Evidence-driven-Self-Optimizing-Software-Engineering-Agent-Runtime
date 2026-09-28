import unittest

from masa.domain.models import Graph, MasaError, Node
from masa.runtime.graph import default_policy, ready_nodes, validate


class WorkflowTests(unittest.TestCase):
    def test_roundtrip_and_failed_dependency_gate(self):
        graph = Graph.from_dict(default_policy().to_dict())
        validate(graph)
        self.assertEqual([n.id for n in ready_nodes(graph, {"verify": "failed", "gate": "pending"})], ["gate"])
        self.assertEqual(ready_nodes(graph, {"verify": "running", "gate": "pending"}), [])

    def test_reject_invalid_graphs(self):
        invalid = [
            Graph((Node("x", "shell"), Node("gate", "gate", ("x",), "all_terminal"))),
            Graph((Node("x", "agent", ("missing",)), Node("gate", "gate", ("x",), "all_terminal"))),
            Graph((Node("a", "agent", ("b",)), Node("b", "agent", ("a",)), Node("gate", "gate", ("b",), "all_terminal"))),
            Graph((Node("a", "agent"), Node("a", "tool"), Node("gate", "gate", ("a",), "all_terminal"))),
            Graph((Node("x", "agent"), Node("uncovered", "tool"), Node("gate", "gate", ("x",), "all_terminal"))),
            Graph((Node("x", "agent"),)),
        ]
        for graph in invalid:
            with self.subTest(graph=graph), self.assertRaises(MasaError):
                validate(graph)
