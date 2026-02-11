"""
PuntingForm API Service
Handles all interactions with PuntingForm API for automated data fetching
"""

import os
import requests
from datetime import datetime

class PuntingFormService:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get('PUNTINGFORM_API_KEY')
        if not self.api_key:
            raise ValueError("PuntingForm API key not found in environment variables")
        
        self.base_url = 'https://api.puntingform.com.au/v2'
    
    def _make_request(self, endpoint, params=None):
        """Make authenticated request to PuntingForm API"""
        url = f"{self.base_url}/{endpoint}"
        
        # CORRECT authentication header
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Accept': 'application/json'
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            raise Exception(f"PuntingForm API error: {str(e)}")
    
    def get_meetings_list(self, date=None, jurisdiction='AU'):
        """
        Get list of meetings for a specific date
        
        Args:
            date: Date string in YYYY-MM-DD format (defaults to today)
            jurisdiction: 'AU', 'NZ', 'HK', 'SG'
        
        Returns:
            Dict with meetings list
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        params = {
            'date': date,
            'jurisdiction': jurisdiction
        }
        
        # CORRECT endpoint
        response = self._make_request('form/meetingslist', params)
        return response.json()
    
    def get_fields_csv(self, date, track, race=None):
        """
        Get fields/runners data in CSV format
        This returns the SAME format as your manual CSV uploads
        
        Args:
            date: Date string in YYYY-MM-DD format
            track: Track name (e.g., 'Flemington', 'Randwick')
            race: Optional race number
        
        Returns:
            CSV string ready for your analyzer
        """
        params = {
            'date': date,
            'track': track
        }
        
        if race:
            params['race'] = race
        
        # CORRECT endpoint - returns CSV directly
        response = self._make_request('form/fields/csv', params)
        return response.text  # Returns CSV string
    
    def get_results(self, date, track, race=None):
        """
        Get race results
        
        Args:
            date: Date string in YYYY-MM-DD format
            track: Track name
            race: Optional race number
        
        Returns:
            Dict with results
        """
        params = {
            'date': date,
            'track': track
        }
        
        if race:
            params['race'] = race
        
        # CORRECT endpoint
        response = self._make_request('form/results', params)
        return response.json()
    
    def get_scratchings(self, date=None):
        """
        Get scratchings (late withdrawals) for a date
        
        Args:
            date: Date string in YYYY-MM-DD format (defaults to today)
        
        Returns:
            Dict with scratchings
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        params = {'date': date}
        response = self._make_request('updates/scratchings', params)
        return response.json()
