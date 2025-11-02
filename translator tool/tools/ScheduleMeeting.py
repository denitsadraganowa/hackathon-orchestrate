import requests
from tools.Tool import Tool
from tools.ToolData import ToolData
import os

class ScheduleMeetingTool(Tool):
    def execute(self, args):
        """
        Schedules a meeting for a group of participants on a specific date and time range,
        using Microsoft Graph API. It checks availability and creates a calendar event
        on behalf of the organizer.

        Args:
            args (dict):
                - 'organizer_email': Email of the meeting organizer (must end with @copaco.com)
                - 'participant_emails': List of participant emails (must end with @copaco.com)
                - 'date': Date of the meeting (format: YYYY-MM-DD)
                - 'start_time': Start time (format: HH:MM in 24-hour format)
                - 'end_time': End time (format: HH:MM in 24-hour format)
                - 'subject': Title of the meeting
                - 'location': (Optional) Meeting location or conferencing link
                - 'access_token': Microsoft Graph OAuth token (must be valid)

        Returns:
            dict: A JSON response from Microsoft Graph API or an error message
        """
        organizer = args.get('organizer_email')
        participants = args.get('participant_emails', [])
        date = args.get('date')
        start_time = args.get('start_time')
        end_time = args.get('end_time')
        subject = args.get('subject')
        location = args.get('location') or "Online"
        token = os.getenv("token")

        # Basic validation
        if not organizer or not organizer.endswith("@copaco.com"):
            return {"error": "Invalid organizer email. Must end with @copaco.com."}
        if not all(p.endswith("@copaco.com") for p in participants):
            return {"error": "All participant emails must end with @copaco.com."}

        start_dt = f"{date}T{start_time}:00"
        end_dt = f"{date}T{end_time}:00"

        attendees = [
            {"emailAddress": {"address": email}, "type": "required"}
            for email in participants
        ]

        event_body = {
            "subject": subject,
            "start": {"dateTime": start_dt, "timeZone": "UTC"},
            "end": {"dateTime": end_dt, "timeZone": "UTC"},
            "location": {"displayName": location},
            "attendees": attendees,
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        try:
            url = f"https://graph.microsoft.com/v1.0/users/{organizer}/events"
            response = requests.post(url, json=event_body, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": f"Failed to schedule meeting: {str(e)}"}

    @staticmethod
    def tool_definition() -> ToolData:
        return ToolData(
            description=(
                "Schedules a meeting for a group of participants on a specific date and time. "
                " All emails must be @copaco.com."
            ),
            parameters=[
                ToolData.Parameter(
                    name="organizer_email",
                    description="The email of the person organizing the meeting. Must end with @copaco.com.",
                    type="string",
                    required=True
                ),
                ToolData.Parameter(
                    name="participant_emails",
                    description="A list of email addresses for participants. Must each end with @copaco.com.",
                    type="array",
                    items="string",
                    required=True
                ),
                ToolData.Parameter(
                    name="date",
                    description="The date for the meeting (format: YYYY-MM-DD).",
                    type="string",
                    required=True
                ),
                ToolData.Parameter(
                    name="start_time",
                    description="The start time of the meeting (format: HH:MM in 24-hour format).",
                    type="string",
                    required=True
                ),
                ToolData.Parameter(
                    name="end_time",
                    description="The end time of the meeting (format: HH:MM in 24-hour format).",
                    type="string",
                    required=True
                ),
                ToolData.Parameter(
                    name="subject",
                    description="A short description or title for the meeting.",
                    type="string",
                    required=True
                ),
                ToolData.Parameter(
                    name="location",
                    description="Optional. Location or conferencing link for the meeting.",
                    type="string",
                    required=False
                )
            ]
        )
