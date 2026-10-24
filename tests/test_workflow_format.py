from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from workflow_format import adjust_workflow_format


class WorkflowFormatTests(unittest.TestCase):
    def test_api_graph_accepts_numeric_and_named_node_ids(self):
        for node_id in ('1', 'noise'):
            self.assertEqual(adjust_workflow_format({node_id: {'class_type': 'RandomNoise', 'inputs': {'noise_seed': 42}}}), 'api')

    def test_ui_workflows_remain_distinct(self):
        self.assertEqual(adjust_workflow_format({'nodes': []}), 'ui')

    def test_non_graph_payloads_remain_invalid(self):
        for value in ({}, None, [], {'prompt': 'text'}, {'noise': {'inputs': {}}}, {'noise': {'class_type': 'RandomNoise', 'inputs': 'wrong'}}):
            self.assertEqual(adjust_workflow_format(value), 'invalid')
