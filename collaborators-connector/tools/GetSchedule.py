import os
import requests
from tools.Tool import Tool
from tools.ToolData import ToolData


class GetScheduleTool(Tool):
    def execute(self, args):
        """
        Retrieves the schedule of a person for a specific date using Microsoft Graph API.
        The schedule includes meetings, tasks, and time slots between 00:00 and 23:59 UTC.

       

        Returns:
            dict: Schedule details for the user or an error message
        """
        person_email = args.get("person_email", "me") 
        date = args.get("date")

       
        if not date:
            return {"error": "Missing required parameter: date"}

        start_datetime = f"{date}T00:00:00"
        end_datetime = f"{date}T23:59:59"

        access_token = os.environ.get("token")
        if not access_token:
            return {"error": "Missing Microsoft Graph access token in environment variable 'access_token'."}

        user_path = "me" if person_email == "me" else person_email
        url = f"https://graph.microsoft.com/v1.0/me/calendarview"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
        params = {
            "startDateTime": start_datetime,
            "endDateTime": end_datetime
        }

        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            events = data.get("value", [])

            if not events:
                return {
                    "date": date,
                    "person": person_email,
                    "schedule": [],
                    "message": f"No events found for {person_email} on {date}."
                }

            schedule = [
                {
                    "time": event.get("start", {}).get("dateTime", "Unknown"),
                    "event": event.get("subject", "No subject")
                }
                for event in events
            ]

            return {
                "date": date,
                "person": person_email,
                "schedule": schedule
            }

        except requests.exceptions.RequestException as e:
            return {"error": f"Failed to fetch schedule: {str(e)}"}

    @staticmethod
    def tool_definition() -> ToolData:
        return ToolData(
            description=(
                "Returns the schedule of a person for a specific date, including meetings, "
                "tasks, and calendar events using Microsoft Graph. Emails must end with @copaco.com."
            ),
            parameters=[
                ToolData.Parameter(
                    name="person_email",
                    description=(
                    "Optional: The email of the person whose schedule to retrieve. "
                    "If omitted, the currently signed-in user's schedule will be returned."
                ),type="string",
                    required=False
                ),
                ToolData.Parameter(
                    name="date",
                    description="The date for the schedule lookup (format: YYYY-MM-DD).",
                    type="string",
                    required=True
                )
            ]
        )
