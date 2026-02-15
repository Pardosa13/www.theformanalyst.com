"""
PuntingForm API V1 Service (Beginner Subscription)
Handles all API interactions with PuntingForm's V1 endpoints
"""
import os
import requests
from datetime import datetime


class PuntingFormService:
    """Service for interacting with PuntingForm V1 API"""
    
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get('PUNTINGFORM_API_KEY')
        if not self.api_key:
            raise ValueError("PuntingForm API key not found in environment")
        
        # V1 API base URL
        self.base_url = 'https://www.puntingform.com.au/api/formdataservice'
    
    def _make_request(self, url):
        """Make authenticated request to V1 API"""
        try:
            response = requests.get(url, timeout=30)
            
            if not response.ok:
                raise Exception(
                    f"PuntingForm API error {response.status_code}: {response.text}"
                )
            
            return response
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"API request failed: {str(e)}")
    
    def get_meetings_list(self, date=None):
        """
        Get list of meetings for a specific date
        
        Args:
            date: Date string in YYYY-MM-DD format, or None for today
            
        Returns:
            Dictionary with 'meetings' array containing meeting info
        """
        if date is None:
            date_obj = datetime.now()
        else:
            # Parse YYYY-MM-DD format
            date_obj = datetime.strptime(date, '%Y-%m-%d')
        
        # V1 API requires DD-MMM-YYYY format (e.g., "15-Feb-2026")
        date_str = date_obj.strftime('%d-%b-%Y')
        
        # V1 endpoint with apikey as query parameter
        url = f"{self.base_url}/GetMeetingListExt/{date_str}?apikey={self.api_key}"
        
        response = self._make_request(url)
        data = response.json()
        
        # Check for API errors
        if data.get('IsError'):
            raise Exception(f"API returned error: {data}")
        
        # Convert V1 format to standardized format
        meetings = []
        for meeting in data.get('Result', []):
            # Skip barrier trials
            if meeting.get('IsBarrierTrial', False):
                continue
            
            meetings.append({
                'meeting_id': str(meeting['MeetingId']),
                'track_name': meeting['Track'],
                'track_code': meeting.get('TrackCode', ''),
                'state': meeting.get('State', ''),
                'race_count': meeting.get('RaceCount', 0),
                'date': date,  # Return in YYYY-MM-DD format for consistency
                'resulted': meeting.get('Resulted', False)
            })
        
        return {'meetings': meetings}
    
    def get_fields_csv(self, track, date, race_number=None):
        """
        Get CSV data for a track/meeting
        
        Args:
            track: Track name (e.g., "Bunbury", "Canterbury")
            date: Date in YYYY-MM-DD format
            race_number: Optional specific race number, or None for all races
            
        Returns:
            CSV string data
        """
        # Parse and convert to V1 date format
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        date_str = date_obj.strftime('%d-%b-%Y')  # DD-MMM-YYYY
        
        if race_number:
            # Get specific race
            url = f"{self.base_url}/GetFormText/{track}/{race_number}/{date_str}?apikey={self.api_key}"
            response = self._make_request(url)
            return response.text
        else:
            # Get all races - need to fetch meeting info first to know race numbers
            meeting_url = f"{self.base_url}/GetMeetingListExt/{date_str}?apikey={self.api_key}"
            meeting_response = self._make_request(meeting_url)
            meeting_data = meeting_response.json()
            
            # Find the target meeting
            target_meeting = None
            for meeting in meeting_data.get('Result', []):
                if meeting['Track'].lower() == track.lower():
                    target_meeting = meeting
                    break
            
            if not target_meeting:
                raise Exception(f"No meeting found for {track} on {date_str}")
            
            # Fetch CSV for each race
            all_csv = []
            race_numbers = target_meeting.get('RaceNumbers', [])
            
            for race_num in race_numbers:
                race_url = f"{self.base_url}/GetFormText/{track}/{race_num}/{date_str}?apikey={self.api_key}"
                race_response = self._make_request(race_url)
                all_csv.append(race_response.text)
            
            # Combine all race CSVs
            return '\n'.join(all_csv)
    
    def get_results(self, track, date):
        """
        Get results for a track/meeting
        
        Args:
            track: Track name (e.g., "Bunbury")
            date: Date in YYYY-MM-DD format
            
        Returns:
            Dictionary with results data
        """
        # Parse and convert to V1 date format
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        date_str = date_obj.strftime('%d-%b-%Y')  # DD-MMM-YYYY
        
        url = f"{self.base_url}/GetResults/{track}/{date_str}?apikey={self.api_key}"
        
        response = self._make_request(url)
        return response.json()
    
    def get_scratchings(self):
        """
        Get current scratchings across all meetings
        
        Returns:
            Dictionary with scratchings data
        """
        url = f"https://www.puntingform.com.au/api/ScratchingsService/GetAllScratchings?apikey={self.api_key}"
        
        response = self._make_request(url)
        return response.json()
