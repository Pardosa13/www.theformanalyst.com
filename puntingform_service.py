"""
PuntingForm API Service
Handles all interactions with PuntingForm API for automated data fetching
"""
import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

class PuntingFormService:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get('PUNTINGFORM_API_KEY')
        if not self.api_key:
            raise ValueError("PuntingForm API key not found in environment variables")
        
        self.base_url = 'https://api.puntingform.com.au/v2'
    
    def _today_au(self):
        """Get today's date in Australian Eastern time"""
        return datetime.now(ZoneInfo("Australia/Sydney")).strftime('%Y-%m-%d')
    
    def _make_request(self, endpoint, params=None):
        """Make authenticated request to PuntingForm API"""
        url = f"{self.base_url}/{endpoint}"
        
        # Initialize params if None
        if params is None:
            params = {}
        
        # PuntingForm uses API key as query parameter
        params['apikey'] = self.api_key
        
        headers = {
            'Accept': 'application/json'
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            # Better error handling - show actual API response
            if not response.ok:
                raise Exception(
                    f"PuntingForm API error {response.status_code}: {response.text}"
                )
            
            return response
        except requests.exceptions.RequestException as e:
            raise Exception(f"PuntingForm API error: {str(e)}")
    
    def get_meetings_list(self, date=None, jurisdiction=None):
        """
        Get list of meetings for a specific date
        
        Args:
            date: Date string in YYYY-MM-DD format (defaults to today in AU time)
            jurisdiction: Optional. 'NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT'
                         If None, returns all Australian meetings
        
        Returns:
            Dict with meetings list
        """
        if date is None:
            date = self._today_au()
        
        params = {'date': date}
        
        # Only add jurisdiction if specified
        if jurisdiction:
            params['jurisdiction'] = jurisdiction
        
        response = self._make_request('form/meetingslist', params)
        return response.json()
    
    def get_fields_csv(self, meeting_id, race_number=None):
        """
        Get fields/runners data in CSV format
        
        Args:
            meeting_id: PuntingForm meeting ID (from get_meetings_list)
            race_number: Optional race number (0 or None = all races)
        
        Returns:
            CSV string ready for your analyzer
        """
        params = {
            'meetingId': meeting_id
        }
        
        if race_number is not None:
            params['raceNumber'] = race_number
        
        response = self._make_request('form/fields/csv', params)
        return response.text  # Returns CSV string
    
    def get_results(self, meeting_id, race_number=None):
        """
        Get race results
        
        Args:
            meeting_id: PuntingForm meeting ID
            race_number: Optional race number
        
        Returns:
            Dict with results
        """
        params = {
            'meetingId': meeting_id
        }
        
        if race_number is not None:
            params['raceNumber'] = race_number
        
        response = self._make_request('form/results', params)
        return response.json()
    
    def get_scratchings(self, date=None):
        """
        Get scratchings (late withdrawals) for a date
        
        Args:
            date: Date string in YYYY-MM-DD format (defaults to today in AU time)
        
        Returns:
            Dict with scratchings
        """
        if date is None:
            date = self._today_au()
        
        params = {'date': date}
        response = self._make_request('updates/scratchings', params)
        return response.json()
