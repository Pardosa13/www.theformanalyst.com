"""
PuntingForm API Service
Handles all interactions with PuntingForm API for automated data fetching
Now uses V2 API with access to Speed Maps, Ratings, and more
"""
import os
import requests
from datetime import datetime

class PuntingFormService:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get('PUNTINGFORM_API_KEY')
        if not self.api_key:
            raise ValueError("PuntingForm API key not found in environment variables")
        
        # V2 API base URL
        self.base_url = 'https://api.puntingform.com.au/v2'
    
    def _make_request(self, endpoint, params=None):
        """Make authenticated request to PuntingForm V2 API"""
        if params is None:
            params = {}
        
        # Add API key to all requests
        params['apiKey'] = self.api_key
        
        try:
            url = f"{self.base_url}{endpoint}"
            response = requests.get(url, params=params, timeout=30)
            
            if not response.ok:
                raise Exception(
                    f"PuntingForm API error {response.status_code}: {response.text}"
                )
            
            # Return CSV or JSON based on endpoint
            if 'CSV' in endpoint:
                return response.text
            return response.json()
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"PuntingForm API error: {str(e)}")
    
    def get_meetings_list(self, date=None):
        """
        Get list of meetings (uses V2 API - no date param needed for today)
        
        Returns:
            Dict with meetings list including meeting_id for V2 API calls
        """
        response = self._make_request('/form/MeetingsList')
        
        # Convert V2 format to match your existing code expectations
        meetings = []
        if response.get('statusCode') == 200:
            for meeting in response.get('meetings', []):
                meetings.append({
                    'meeting_id': meeting['id'],
                    'track_name': meeting['track'],
                    'track_code': meeting.get('trackCode', ''),
                    'state': meeting.get('state', ''),
                    'race_count': meeting.get('raceCount', 0),
                    'date': meeting.get('date', ''),
                    'resulted': meeting.get('resulted', False)
                })
        
        return {'meetings': meetings}
    
    def get_fields_csv(self, meeting_id, race_number=None):
        """
        Get fields/runners data in CSV format (V2 API uses meeting_id)
        
        Args:
            meeting_id: Meeting ID from get_meetings_list()
            race_number: Optional race number (if None, gets all races)
        
        Returns:
            CSV string ready for your analyzer
        """
        params = {'meetingId': meeting_id}
        if race_number:
            params['raceNumber'] = race_number
        
        return self._make_request('/form/FieldsCSV', params)
    
    def get_results(self, meeting_id, race_number=None):
        """
        Get race results (V2 API uses meeting_id)
        
        Args:
            meeting_id: Meeting ID
            race_number: Optional race number
        
        Returns:
            Dict with results
        """
        params = {'meetingId': meeting_id}
        if race_number:
            params['raceNumber'] = race_number
        
        return self._make_request('/form/Results', params)
    
    def get_scratchings(self):
        """
        Get scratchings (late withdrawals)
        
        Returns:
            List of scratchings
        """
        return self._make_request('/updates/Scratchings')
    
    # ========== NEW V2 API METHODS ==========
    
    def get_speed_maps(self, meeting_id, race_number=None):
        """
        Get speed maps with position advantages
        
        Args:
            meeting_id: Meeting ID
            race_number: Optional race number
        
        Returns:
            Speed map data with mapA2E, jockeyA2E, settle positions, etc.
        """
        params = {'meetingId': meeting_id}
        if race_number:
            params['raceNumber'] = race_number
        
        return self._make_request('/User/Speedmaps', params)
    
    def get_ratings(self, meeting_id):
        """
        Get PuntingForm ratings for entire meeting
        
        Args:
            meeting_id: Meeting ID
        
        Returns:
            Ratings data with sectionals, class changes, PFAI predictions
        """
        return self._make_request('/Ratings/MeetingRatings', {'meetingId': meeting_id})
    
    def get_strike_rates(self, meeting_id=None):
        """
        Get jockey/trainer strike rates
        
        Args:
            meeting_id: Optional meeting ID
        
        Returns:
            Strike rate statistics
        """
        params = {}
        if meeting_id:
            params['meetingId'] = meeting_id
        
        return self._make_request('/form/StrikeRates', params)
    
    def get_conditions(self, meeting_id=None):
        """
        Get track conditions
        
        Args:
            meeting_id: Optional meeting ID
        
        Returns:
            Track condition data
        """
        params = {}
        if meeting_id:
            params['meetingId'] = meeting_id
        
        return self._make_request('/updates/Conditions', params)
    
    # ========== HELPER METHODS ==========
    
    def get_complete_race_data(self, meeting_id, race_number):
        """
        Get EVERYTHING for a specific race
        Perfect for feeding into your Partington Engine
        
        Args:
            meeting_id: Meeting ID
            race_number: Race number
        
        Returns:
            Dict with csv_data, speed_maps, ratings, strike_rates
        """
        return {
            'csv_data': self.get_fields_csv(meeting_id, race_number),
            'speed_maps': self.get_speed_maps(meeting_id, race_number),
            'ratings': self.get_ratings(meeting_id),
            'strike_rates': self.get_strike_rates(meeting_id)
        }
    def get_complete_meeting_data(self, meeting_id, race_number=None):
        """
        Get ALL data for a meeting from V2 API:
        - CSV form data
        - Speed maps
        - Ratings
        - Sectionals (if available)
        
        Returns dict with all data
        """
        import logging
        logger = logging.getLogger(__name__)
        
        result = {
            'csv_data': None,
            'speed_maps': None,
            'ratings': None,
            'sectionals': None
        }
        
        try:
            # Get CSV data
            csv_data = self.get_fields_csv(meeting_id, race_number)
            result['csv_data'] = csv_data
            
            # Get speed maps
            try:
                speed_maps = self.get_speed_maps(meeting_id, race_number)
                result['speed_maps'] = speed_maps
            except Exception as e:
                logger.warning(f"Could not fetch speed maps: {e}")
            
            # Get ratings
            try:
                ratings = self.get_ratings(meeting_id)
                result['ratings'] = ratings
            except Exception as e:
                logger.warning(f"Could not fetch ratings: {e}")
            
            # Get sectionals (only available with Pro/Modeller subscription)
            # Uncomment this when you upgrade subscription
            # try:
            #     sectionals = self.get_sectionals(meeting_id, race_number)
            #     result['sectionals'] = sectionals
            # except Exception as e:
            #     logger.warning(f"Could not fetch sectionals: {e}")
            
        except Exception as e:
            logger.error(f"Error fetching complete meeting data: {e}")
            raise
        
        return result
