"""
PuntingForm API Service

Handles all interactions with the PuntingForm API using documented endpoints
and parameters.

Key notes:
- meetingslist does NOT accept 'AU' as a jurisdiction
- fields/csv REQUIRES meetingId (not date/track)
- raceNumber = 0 returns all races
"""

import os
import requests
from datetime import datetime
from typing import Optional, Dict, List


class PuntingFormService:
    BASE_URL = "https://api.puntingform.com.au/v2"

    # Valid Australian jurisdictions for meetingslist
    AU_JURISDICTIONS = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"]

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("PUNTINGFORM_API_KEY")
        if not self.api_key:
            raise ValueError("PUNTINGFORM_API_KEY is not set")

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------
    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> requests.Response:
        url = f"{self.BASE_URL}/{endpoint}"
        params = params or {}
        params["apikey"] = self.api_key

        response = requests.get(url, params=params, timeout=30)

        if not response.ok:
            raise Exception(
                f"PuntingForm API error {response.status_code}: {response.text}"
            )

        return response

    # ------------------------------------------------------------------
    # Meetings
    # ------------------------------------------------------------------
    def get_meetings_list(
        self,
        date: Optional[str] = None,
        jurisdiction: Optional[str] = None
    ) -> Dict:
        """
        Get meetings for a given date.

        Args:
            date: YYYY-MM-DD (defaults to today)
            jurisdiction: State code (NSW, VIC, QLD, etc). If None, returns all.

        Returns:
            JSON response containing meetings
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        params = {"date": date}

        if jurisdiction:
            params["jurisdiction"] = jurisdiction

        response = self._make_request("form/meetingslist", params)
        return response.json()

    def get_all_au_meetings(self, date: Optional[str] = None) -> List[Dict]:
        """
        Get meetings for all Australian jurisdictions and merge results.
        """
        all_meetings = []

        for state in self.AU_JURISDICTIONS:
            data = self.get_meetings_list(date=date, jurisdiction=state)
            all_meetings.extend(data.get("meetings", []))

        return all_meetings

    # ------------------------------------------------------------------
    # Fields (CSV)
    # ------------------------------------------------------------------
    def get_fields_csv(
        self,
        meeting_id: int,
        race_number: Optional[int] = None
    ) -> str:
        """
        Get runner fields in CSV format.

        Args:
            meeting_id: PuntingForm meetingId
            race_number: Race number (0 or None = all races)

        Returns:
            CSV string
        """
        params = {"meetingId": meeting_id}

        if race_number is not None:
            params["raceNumber"] = race_number

        response = self._make_request("form/fields/csv", params)
        return response.text

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------
    def get_results(
        self,
        meeting_id: int,
        race_number: Optional[int] = None
    ) -> Dict:
        """
        Get race results for a meeting.

        Args:
            meeting_id: PuntingForm meetingId
            race_number: Optional race number

        Returns:
            JSON response with results
        """
        params = {"meetingId": meeting_id}

        if race_number is not None:
            params["raceNumber"] = race_number

        response = self._make_request("form/results", params)
        return response.json()

    # ------------------------------------------------------------------
    # Scratchings
    # ------------------------------------------------------------------
    def get_scratchings(self, date: Optional[str] = None) -> Dict:
        """
        Get scratchings (late withdrawals).

        Args:
            date: YYYY-MM-DD (defaults to today)

        Returns:
            JSON response
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        params = {"date": date}
        response = self._make_request("updates/scratchings", params)
        return response.json()
