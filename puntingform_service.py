"""
PuntingForm API Service
Handles all interactions with PuntingForm API for automated data fetching
"""

import os
import requests
from datetime import datetime, timedelta
import csv
from io import StringIO

PUNTINGFORM_API_KEY = os.environ.get('PUNTINGFORM_API_KEY')
BASE_URL = 'https://api.puntingform.com.au/v2'

class PuntingFormService:
    def __init__(self, api_key=None):
        self.api_key = api_key or PUNTINGFORM_API_KEY
        if not self.api_key:
            raise ValueError("PuntingForm API key not found in environment variables")
        
        self.headers = {
            'X-API-KEY': self.api_key,
            'Accept': 'application/json'
        }
    
    def _make_request(self, endpoint, params=None):
        """Make authenticated request to PuntingForm API"""
        url = f"{BASE_URL}/{endpoint}"
        
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"PuntingForm API error: {e}")
            return None
    
    def get_meetings(self, date=None, jurisdiction='all'):
        """
        Get list of meetings for a specific date
        
        Args:
            date: Date string in YYYY-MM-DD format (defaults to today)
            jurisdiction: 'VIC', 'NSW', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT', or 'all'
        
        Returns:
            List of meetings with basic info
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        params = {'date': date}
        if jurisdiction != 'all':
            params['jurisdiction'] = jurisdiction
        
        data = self._make_request('meetings/list', params)
        
        if not data:
            return []
        
        # Parse response to extract meeting info
        meetings = []
        for meeting in data.get('meetings', []):
            meetings.append({
                'meeting_id': meeting.get('MeetingId'),
                'track_name': meeting.get('Track'),
                'date': meeting.get('MeetingDate'),
                'jurisdiction': meeting.get('Jurisdiction'),
                'track_condition': meeting.get('TrackCondition', 'good').lower(),
                'race_count': meeting.get('RaceCount', 0)
            })
        
        return meetings
    
    def get_meeting_data_csv(self, meeting_id):
        """
        Get full meeting data in CSV format matching your current upload structure
        
        Args:
            meeting_id: PuntingForm meeting ID
        
        Returns:
            CSV string ready to be processed by your analyzer
        """
        # Get full meeting data
        data = self._make_request(f'meetings/{meeting_id}/runners')
        
        if not data or 'runners' not in data:
            return None
        
        # Convert to CSV format matching your existing structure
        csv_buffer = StringIO()
        writer = csv.writer(csv_buffer)
        
        # Write header (matching your existing CSV structure)
        headers = [
            'race number', 'horse name', 'barrier', 'horse weight', 'horse claim',
            'horse jockey', 'horse trainer', 'horse last10', 'horse record',
            'horse record track', 'horse record track distance', 'horse record distance',
            'horse record good', 'horse record soft', 'horse record heavy', 
            'horse record firm', 'horse record synthetic',
            'horse record first up', 'horse record second up',
            'distance', 'class restrictions', 'race prizemoney',
            'meeting date', 'form meeting date', 'form distance', 'form class',
            'prizemoney', 'form position', 'form margin', 'form weight',
            'form track condition', 'form price', 'sectional',
            'horse age', 'horse sex', 'horse sire', 'horse dam'
        ]
        writer.writerow(headers)
        
        # Write runner data
        for runner in data.get('runners', []):
            race_number = runner.get('RaceNumber', '')
            
            # Basic info
            horse_name = runner.get('HorseName', '')
            barrier = runner.get('Barrier', '')
            weight = runner.get('Weight', '')
            claim = runner.get('Claim', 0)
            jockey = runner.get('Jockey', '')
            trainer = runner.get('Trainer', '')
            
            # Form
            last10 = runner.get('Last10', '')
            career_record = self._format_record(runner.get('CareerRecord'))
            track_record = self._format_record(runner.get('TrackRecord'))
            track_distance_record = self._format_record(runner.get('TrackDistanceRecord'))
            distance_record = self._format_record(runner.get('DistanceRecord'))
            
            # Track conditions
            good_record = self._format_record(runner.get('GoodRecord'))
            soft_record = self._format_record(runner.get('SoftRecord'))
            heavy_record = self._format_record(runner.get('HeavyRecord'))
            firm_record = self._format_record(runner.get('FirmRecord'))
            synthetic_record = self._format_record(runner.get('SyntheticRecord'))
            
            # First/second up
            first_up_record = self._format_record(runner.get('FirstUpRecord'))
            second_up_record = self._format_record(runner.get('SecondUpRecord'))
            
            # Race details
            distance = runner.get('Distance', '')
            race_class = runner.get('Class', '')
            prizemoney = runner.get('Prizemoney', '')
            
            # Meeting details
            meeting_date = runner.get('MeetingDate', '')
            
            # Last start details
            last_start = runner.get('LastStart', {})
            form_date = last_start.get('Date', '')
            form_distance = last_start.get('Distance', '')
            form_class = last_start.get('Class', '')
            form_prizemoney = last_start.get('Prizemoney', '')
            form_position = last_start.get('Position', '')
            form_margin = last_start.get('Margin', '')
            form_weight = last_start.get('Weight', '')
            form_condition = last_start.get('TrackCondition', '')
            form_price = last_start.get('StartingPrice', '')
            
            # Sectional
            sectional = runner.get('Sectional', '')
            
            # Horse details
            age = runner.get('Age', '')
            sex = runner.get('Sex', '')
            sire = runner.get('Sire', '')
            dam = runner.get('Dam', '')
            
            # Write row
            row = [
                race_number, horse_name, barrier, weight, claim,
                jockey, trainer, last10, career_record,
                track_record, track_distance_record, distance_record,
                good_record, soft_record, heavy_record, firm_record, synthetic_record,
                first_up_record, second_up_record,
                distance, race_class, prizemoney,
                meeting_date, form_date, form_distance, form_class,
                form_prizemoney, form_position, form_margin, form_weight,
                form_condition, form_price, sectional,
                age, sex, sire, dam
            ]
            writer.writerow(row)
        
        return csv_buffer.getvalue()
    
    def get_race_results(self, meeting_id, race_number=None):
        """
        Get results for a meeting or specific race
        
        Args:
            meeting_id: PuntingForm meeting ID
            race_number: Optional specific race number (1-12)
        
        Returns:
            Dict of race results keyed by race number
        """
        data = self._make_request(f'meetings/{meeting_id}/results')
        
        if not data or 'results' not in data:
            return {}
        
        results = {}
        
        for result in data.get('results', []):
            race_num = result.get('RaceNumber')
            
            if race_number and race_num != race_number:
                continue
            
            if race_num not in results:
                results[race_num] = []
            
            results[race_num].append({
                'horse_name': result.get('HorseName'),
                'finish_position': result.get('Position'),
                'starting_price': result.get('StartingPrice'),
                'margin': result.get('Margin'),
                'barrier': result.get('Barrier')
            })
        
        return results
    
    def check_results_available(self, meeting_id):
        """
        Check if results are available for a meeting
        
        Returns:
            Boolean - True if any results available
        """
        results = self.get_race_results(meeting_id)
        return len(results) > 0
    
    def get_meeting_status(self, meeting_id):
        """
        Get the current status of a meeting
        
        Returns:
            Dict with meeting status info
        """
        data = self._make_request(f'meetings/{meeting_id}/status')
        
        if not data:
            return {'status': 'unknown'}
        
        return {
            'status': data.get('Status', 'unknown'),  # 'upcoming', 'in_progress', 'completed'
            'races_completed': data.get('RacesCompleted', 0),
            'total_races': data.get('TotalRaces', 0),
            'next_race': data.get('NextRace'),
            'last_updated': data.get('LastUpdated')
        }

    
    def _format_record(self, record_dict):
        """
        Format record dict to string format: "starts:wins-seconds-thirds"
        """
        if not record_dict:
            return "0:0-0-0"
        
        starts = record_dict.get('Starts', 0)
        wins = record_dict.get('Wins', 0)
        seconds = record_dict.get('Seconds', 0)
        thirds = record_dict.get('Thirds', 0)
        
        return f"{starts}:{wins}-{seconds}-{thirds}"
    
    def parse_meeting_name(self, track_name, date):
        """
        Create meeting name in your existing format: YYMMDD_TrackName
        
        Args:
            track_name: Track name from API
            date: Date string YYYY-MM-DD
        
        Returns:
            Meeting name string like "250201_Flemington"
        """
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        date_prefix = date_obj.strftime('%y%m%d')
        
        # Clean track name (remove spaces, etc)
        clean_track = track_name.replace(' ', '')
        
        return f"{date_prefix}_{clean_track}"
