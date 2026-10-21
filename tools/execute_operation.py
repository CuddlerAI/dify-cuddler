"""MIT: native Dify tool entrypoint."""
from dify_plugin import Tool
from cuddler_dify.host import invoke


class CuddlerTool(Tool):
    def _invoke(self, tool_parameters):
        yield from invoke(self, 'execute_operation', tool_parameters)
