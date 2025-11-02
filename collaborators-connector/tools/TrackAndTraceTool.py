import requests
from tools.Tool import Tool
from tools.ToolData import ToolData
import os
class TrackAndTraceTool(Tool):
    def execute(self, args):
        """
        Execute the Track & Trace tool to retrieve shipment tracking information.

        This tool communicates with Copaco's logistics integration system to fetch
        tracking updates for a shipment. It uses the carrier and tracking number,
        and if the carrier is not provided, it tries to infer it based on common
        patterns of the tracking key.

        Args:
            args (dict): 
                - 'carrier' (optional): Name of the shipping provider (e.g. 'dhl', 'postnl').
                - 'key': The shipment tracking number.

        Returns:
            dict: A JSON structure containing tracking information or error details.
        """
        key = args.get('key')
        carrier = args.get('carrier')

        if not carrier:
            
            carrier = self.detect_carrier(key)

        if not carrier:
            return {
                "error": "Unable to determine carrier from tracking number. Please provide the carrier explicitly."
            }

        url = f"https://trackandtraceacc.copaco.com/trackandtraceorig?carrier={carrier}&key={key}"

        headers = {
            "X-API-KEY": os.getenv("X_API_KEY")
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            try:
                return response.json()
            except ValueError:
                return {"raw_response": response.text}
        except requests.exceptions.RequestException as e:
            return {"error": f"Failed to retrieve tracking info: {str(e)}"}

    def detect_carrier(self, key: str) -> str:
        """
        Attempts to guess the carrier based on the format of the tracking number.

        Args:
            key (str): The tracking number provided by the user.

        Returns:
            str: Detected carrier name (e.g. 'postnl', 'dhl') or None if unknown.
        """
        # Sample pattern rules (adjust and expand as needed)
        if key.startswith("JVGL") and len(key) > 20:
            return "postnl"
        elif key.startswith("3S") or key.startswith("CD"):
            return "postnl"
        elif key.startswith("JJD") or key.startswith("0034"):
            return "dhl"
        elif key.isdigit() and len(key) == 12:
            return "dhl"
        # Add more pattern rules here...

        return None

    @staticmethod
    def tool_definition() -> ToolData:
        """
        Define the tool's metadata and parameters for integration into an agent framework.

        Returns:
            ToolData: Contains description and parameter schema.
        """
        return ToolData(
            description=(
                "Retrieve shipment tracking information using a tracking number. "
                "If the carrier (e.g. 'dhl', 'postnl') is not provided, it will attempt "
                "to auto-detect the correct carrier based on the structure of the tracking number."
            ),
            parameters=[
                ToolData.Parameter(
                    "key", 
                    "The shipment tracking number provided by the carrier (e.g. 'JVGL05646203000841650889')", 
                    "string", 
                    True
                ),
                ToolData.Parameter(
                    "carrier", 
                    "Optional: The name of the carrier if known (e.g. 'dhl', 'postnl'). "
                    "If not provided, the tool will attempt to auto-detect it.", 
                    "string", 
                    False
                )
            ]
        )
