from abc import abstractmethod
import tools.ToolData as ToolData

class Tool:
    available_tools = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.available_tools.append(cls)

    def get_tool_definitions(self) -> list:
        tool_definitions = []
        for tool_class in self.available_tools:
            tool_definition = tool_class.tool_definition()
            tool_definitions.append(tool_definition.to_dict(tool_class.__name__))
        return tool_definitions


    @abstractmethod
    def execute(self, args):
        return

    @staticmethod
    @abstractmethod
    def tool_definition() -> ToolData:
        return []
