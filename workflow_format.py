from typing import Literal

def adjust_workflow_format(workflow: dict) -> Literal['ui', 'api', 'invalid']:
    """
    Determine if workflow is UI or API format.
    Args:
        workflow (dict): workflow object
    Returns:
        Literal['ui', 'api', 'invalid']: 'ui' if UI format, 'api' if API format, 'invalid' if neither
    """
    if isinstance(workflow, dict):
        if 'nodes' in workflow and isinstance(workflow['nodes'], list):
            return 'ui'
        if workflow and all(isinstance(key, str) and isinstance(node, dict) and
                            isinstance(node.get('class_type'), str) and isinstance(node.get('inputs', {}), dict)
                            for key, node in workflow.items()):
            return 'api'
    return 'invalid' 