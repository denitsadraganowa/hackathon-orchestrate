import requests
from tools.Tool import Tool
from tools.ToolData import ToolData

import os
class GetCustomerInfoTool(Tool):
    def execute(self, args):
        """
        Retrieves customer information from Copaco's customer system based on one or more
        of the following: customer ID, Dutch Chamber of Commerce number (KVK), or VAT number.

        Args:
            args (dict):
                - IP_CUSTOMER (str): Customer ID (e.g., '0000001062')
                - IP_KVK_NUMBER (str): Dutch Chamber of Commerce number
                - IP_VAT_NUMBER (str): VAT number of the customer

        Returns:
            dict: API response containing customer details or error info
        """
        customer_id = args.get("IP_CUSTOMER", "")
        kvk = args.get("IP_KVK_NUMBER", "")
        vat = args.get("IP_VAT_NUMBER", "")

        if not (customer_id or kvk or vat):
            return {"error": "At least one of IP_CUSTOMER, IP_KVK_NUMBER, or IP_VAT_NUMBER must be provided."}

        url = "https://connect.copaco.com/API/AIgetcustomer-test"

        payload = {
            "IP_CUSTOMER": customer_id,
            "IP_KVK_NUMBER": kvk,
            "IP_VAT_NUMBER": vat
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Basic YWl0b29sczpvVUdrQnpXNEFiUmg="  
        }

        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()

            try:
                result = response.json()
            except ValueError:
                result = {"raw_response": response.text}

            return {
                "status_code": response.status_code,
                "response": result
            }

        except requests.exceptions.RequestException as e:
            return {
                "error": f"Failed to retrieve customer info: {str(e)}"
            }

    @staticmethod
    def tool_definition() -> ToolData:
        return ToolData(
            description=(
                "Retrieve customer information from Copaco using customer ID, KVK number, or VAT number. "
                "At least one of the fields must be provided."
            ),
            parameters=[
                ToolData.Parameter(
                    name="IP_CUSTOMER",
                    description="Customer ID (e.g. '0000001062')",
                    type="string",
                    required=False
                ),
                ToolData.Parameter(
                    name="IP_KVK_NUMBER",
                    description="Dutch Chamber of Commerce number (KVK)",
                    type="string",
                    required=False
                ),
                ToolData.Parameter(
                    name="IP_VAT_NUMBER",
                    description="VAT number of the customer",
                    type="string",
                    required=False
                )
            ]
        )
