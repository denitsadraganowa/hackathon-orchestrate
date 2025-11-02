import requests

from typing import List
from tools.Tool import Tool
from tools.ToolData import ToolData

class PriceEngineTool(Tool):
    def execute(self, args):
        """
        Execute the price engine tool to get the price of a product for a customer.
        
        Args:
            args: An object containing customerId and productId
            
        Returns:
            dict: The price information for the product
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Basic dGVzdDpRZENTWjFjOE00dTkxbXdkUVlFaA=="
        }
        response = requests.post("https://1e3fc531b5c7.ngrok-free.app/get/ViewServlet", headers=headers, json={
            "shopid": 1000,
            "customerid": args['customer_id'],
            "productids": [args['product_id']]
        })

        data = response.json()
        return data['body'][0]['prc']

    @staticmethod
    def tool_definition() -> ToolData:
        """
        Define the tool's metadata and parameters.
        
        Returns:
            List[ToolData]: A list containing the tool definition
        """
        return ToolData(
            description="Get price of a product for a customer. Prices are in EUR",
            parameters=[
                ToolData.Parameter("customer_id", "The id of the customer.", "string", True),
                ToolData.Parameter("product_id", "The id of the product.", "string", True)
            ]
        )

