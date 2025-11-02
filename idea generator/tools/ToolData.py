from typing import List

class ToolData:
    class Parameter:
        def __init__(self, name: str, description: str, type: str, required: bool, items: str = None) -> None:
            self.name = name
            self.description = description
            self.type = type
            self.required = required
            self.items = items

        def to_dict(self) -> dict:
            schema = {
                "description": self.description,
                "type": self.type,
            }
            if self.type == "array" and self.items:
                schema["items"] = {"type": self.items}
            return schema

    def __init__(self, description: str, parameters: List[Parameter]) -> None:
        self.description = description
        self.parameters = parameters

    def to_dict(self, class_name) -> dict:
        return {
            "type": "function",
            "name": class_name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {param.name: param.to_dict() for param in self.parameters},
                "required": [param.name for param in self.parameters if param.required],
                "strict": True,
                "additionalProperties": False
            }
        }
