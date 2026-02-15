import os
import requests
from datetime import datetime

class PuntingFormService:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get('PUNTINGFORM_API_KEY')
        if not self.api_key:
            raise ValueError("PuntingForm API key not found")
        self.base_url = 'https://www.puntingform.com.au/api/formdataservice'
    
    def _make_request(self, url):
        """Make authenticated request"""
        try:
            response = requests.get(url, timeout=30)
            if not response.ok:
                raise Exception(f"PuntingForm API error {response.status_code}: {response.text}")
            return response
        except requests.exceptions.RequestException as e:
            raise Exception(f"API request failed: {str(e)}")
    
    def get_meetings_list(self, date=None):
        """Get meetings list - V1 format"""
        if date is None:
            date_obj = datetime.now()
        else:
            date_obj = datetime.strptime(date, '%Y-%m-%d')
        
        date_str = date_obj.strftime('%d-%b-%Y')  # V1 format: 15-Feb-2026
        url = f"{self.base_url}/GetMeetingListExt/{date_str}?apikey={self.api_key}"
        
        response = self._make_request(url)
        data = response.json()
        
        if data.get('IsError'):
            raise Exception(f"API returned error: {data}")
        
        # Convert V1 format to standardized format
        meetings = []
        for meeting in data.get('Result', []):
            if meeting.get('IsBarrierTrial', False):
                continue
            meetings.append({
                'meeting_id': str(meeting['MeetingId']),
                'track_name': meeting['Track'],
                'track_code': meeting['TrackCode'],
                'state': meeting['State'],
                'race_count': meeting['RaceCount'],
                'date': date_obj.strftime('%Y-%m-%d'),
                'resulted': meeting.get('Resulted', False)
            })
        
        return {'meetings': meetings}
    
    def get_fields_csv(self, track, date, race_number=None):
        """Get CSV data - V1 format"""
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        date_str = date_obj.strftime('%d-%b-%Y')
        
        if race_number:
            url = f"{self.base_url}/GetFormText/{track}/{race_number}/{date_str}?apikey={self.api_key}"
            response = self._make_request(url)
            return response.text
        else:
            # Get all races - need to loop
            meeting_url = f"{self.base_url}/GetMeetingListExt/{date_str}?apikey={self.api_key}"
            meeting_response = self._make_request(meeting_url)
            meeting_data = meeting_response.json()
            
            target_meeting = None
            for meeting in meeting_data.get('Result', []):
                if meeting['Track'].lower() == track.lower():
                    target_meeting = meeting
                    break
            
            if not target_meeting:
                raise Exception(f"No meeting found for {track} on {date_str}")
            
            all_csv = []
            for race_num in target_meeting['RaceNumbers']:
                race_url = f"{self.base_url}/GetFormText/{track}/{race_num}/{date_str}?apikey={self.api_key}"
                race_response = self._make_request(race_url)
                all_csv.append(race_response.text)
            
            return '\n'.join(all_csv)
    
    def get_results(self, track, date):
        """Get results - V1 format"""
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        date_str = date_obj.strftime('%d-%b-%Y')
        url = f"{self.base_url}/GetResults/{track}/{date_str}?apikey={self.api_key}"
        response = self._make_request(url)
        return response.json()
    
    def get_scratchings(self):
        """Get scratchings"""
        url = f"https://www.puntingform.com.au/api/ScratchingsService/GetAllScratchings?apikey={self.api_key}"
        response = self._make_request(url)
        return response.json()
