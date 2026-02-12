"""
PuntingForm API V1 Service
Handles all interactions with PuntingForm V1 API for automated data fetching
"""
import os
import requests
from datetime import datetime

class PuntingFormService:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get('PUNTINGFORM_API_KEY')
        if not self.api_key:
            raise ValueError("PuntingForm API key not found in environment variables")
        
        # V1 API base URL
        self.base_url = 'https://www.puntingform.com.au/api/formdataservice'
    
    def _make_request(self, url):
        """Make authenticated request to PuntingForm V1 API"""
        try:
            response = requests.get(url, timeout=30)
            
            if not response.ok:
                raise Exception(
                    f"PuntingForm API error {response.status_code}: {response.text}"
                )
            
            return response
        except requests.exceptions.RequestException as e:
            raise Exception(f"PuntingForm API error: {str(e)}")
    
    def get_meetings_list(self, date=None):
        """
        Get list of meetings for a specific date using V1 API
        
        Args:
            date: Date string in YYYY-MM-DD format (defaults to today)
        
        Returns:
            Dict with meetings list
        """
        if date is None:
            date_obj = datetime.now()
        else:
            # Parse YYYY-MM-DD to datetime
            date_obj = datetime.strptime(date, '%Y-%m-%d')
        
        # V1 API expects DD-MMM-YYYY format (e.g., "12-Feb-2026")
        date_str = date_obj.strftime('%d-%b-%Y')
        
        # V1 endpoint format
        url = f"{self.base_url}/GetMeetingListExt/{date_str}?apikey={self.api_key}"
        
        response = self._make_request(url)
        data = response.json()
        
        # V1 API returns data in 'Result' field
        if data.get('IsError'):
            raise Exception(f"PuntingForm API returned error: {data}")
        
        # Convert V1 format to something easier to work with
        meetings = []
        for meeting in data.get('Result', []):
            # Skip barrier trials
            if meeting.get('IsBarrierTrial', False):
                continue
                
            meetings.append({
                'meeting_id': str(meeting['MeetingId']),
                'track_name': meeting['Track'],
                'track_code': meeting['TrackCode'],
                'state': meeting['State'],
                'race_count': meeting['RaceCount'],
                'date': date_str,
                'resulted': meeting.get('Resulted', False)
            })
        
        return {'meetings': meetings}
    
    def get_fields_csv(self, track, date, race_number=None):
        """
        Get fields/runners data in CSV format from V1 API
        
        Args:
            track: Track name (e.g., 'Flemington', 'Randwick')
            date: Date string in YYYY-MM-DD format
            race_number: Optional race number (if None, gets all races)
        
        Returns:
            CSV string ready for your analyzer
        """
        # Parse date to correct format
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        date_str = date_obj.strftime('%d-%b-%Y')
        
        if race_number:
            # Get specific race in CSV format
            url = f"{self.base_url}/GetFormText/{track}/{race_number}/{date_str}?apikey={self.api_key}"
        else:
            # Get all races - need to loop through them
            # First get the meeting to find how many races
            meeting_url = f"{self.base_url}/GetMeetingListExt/{date_str}?apikey={self.api_key}"
            meeting_response = self._make_request(meeting_url)
            meeting_data = meeting_response.json()
            
            # Find this track's meeting
            target_meeting = None
            for meeting in meeting_data.get('Result', []):
                if meeting['Track'].lower() == track.lower():
                    target_meeting = meeting
                    break
            
            if not target_meeting:
                raise Exception(f"No meeting found for track {track} on {date_str}")
            
            # Get CSV for each race and combine
            all_csv = []
            for race_num in target_meeting['RaceNumbers']:
                race_url = f"{self.base_url}/GetFormText/{track}/{race_num}/{date_str}?apikey={self.api_key}"
                race_response = self._make_request(race_url)
                all_csv.append(race_response.text)
            
            return '\n'.join(all_csv)
        
        response = self._make_request(url)
        return response.text
    
    def get_results(self, track, date):
        """
        Get race results from V1 API
        
        Args:
            track: Track name
            date: Date string in YYYY-MM-DD format
        
        Returns:
            Dict with results
        """
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        date_str = date_obj.strftime('%d-%b-%Y')
        
        url = f"{self.base_url}/GetResults/{track}/{date_str}?apikey={self.api_key}"
        
        response = self._make_request(url)
        return response.json()
    
    def get_scratchings(self):
        """
        Get scratchings (late withdrawals)
        
        Returns:
            List of scratchings
        """
        url = f"https://www.puntingform.com.au/api/ScratchingsService/GetAllScratchings?apikey={self.api_key}"
        
        response = self._make_request(url)
        return response.json()
