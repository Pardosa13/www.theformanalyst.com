"""
Betfair API Service for The Form Analyst
Handles authentication, market matching, and results fetching
WITH PROXY SUPPORT
"""

import os
import requests
from datetime import datetime
from difflib import SequenceMatcher
import logging

logger = logging.getLogger(__name__)

# Betfair credentials from environment variables
BETFAIR_USERNAME = os.environ.get('BETFAIR_USERNAME')
BETFAIR_PASSWORD = os.environ.get('BETFAIR_PASSWORD')
BETFAIR_APP_KEY = os.environ.get('BETFAIR_APP_KEY', 'amyMWFeTpLAxmSyo')

# Proxy settings from environment variables
PROXY_HOST = os.environ.get('PROXY_HOST')  # proxy.smartproxy.net:3120
PROXY_USER = os.environ.get('PROXY_USER')
PROXY_PASS = os.environ.get('PROXY_PASS')

# API endpoints
IDENTITY_URL = "https://identitysso.betfair.com/api/login"
BETTING_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

# Track name mapping (Your CSV names -> Betfair API names)
TRACK_MAPPING = {
    # NSW Metro
    'Randwick': 'Randwick',
    'Randwick-Kensington': 'Randwick',
    'Rosehill': 'Rosehill Gardens',
    'Warwick Farm': 'Warwick Farm',
    'Canterbury': 'Canterbury Park',
    'Kembla Grange': 'Kembla Grange',
    'Gosford': 'Gosford',
    'Wyong': 'Wyong',
    'Newcastle': 'Newcastle',
    'Hawkesbury': 'Hawkesbury',
    
    # VIC Metro
    'Flemington': 'Flemington',
    'Caulfield': 'Caulfield',
    'Caulfield Heath': 'Caulfield',
    'Moonee Valley': 'Moonee Valley',
    'Sandown-Hillside': 'Sandown-Hillside',
    'Sandown-Lakeside': 'Sandown-Lakeside',
    'Cranbourne': 'Cranbourne',
    'Pakenham': 'Pakenham',
    'Mornington': 'Mornington',
    'Geelong': 'Geelong',
    'Werribee': 'Werribee',
    
    # QLD Metro
    'Eagle Farm': 'Eagle Farm',
    'Doomben': 'Doomben',
    'Gold Coast': 'Gold Coast',
    'Sunshine Coast': 'Sunshine Coast',
    'Ipswich': 'Ipswich',
    
    # SA Metro
    'Morphettville': 'Morphettville',
    'Morphettville Parks': 'Morphettville Parks',
    'Murray Bridge GH': 'Murray Bridge',
    'Gawler': 'Gawler',
    
    # WA Metro
    'Ascot': 'Ascot',
    'Belmont Park': 'Belmont Park',
    'Bunbury': 'Bunbury',
    'Pinjarra Scarpside': 'Pinjarra Park',
    
    # TAS
    'Hobart': 'Hobart',
    'Launceston': 'Launceston',
    
    # ACT
    'Canberra': 'Canberra',
    
    # NSW Country
    'Orange': 'Orange',
    'Bathurst': 'Bathurst',
    'Dubbo': 'Dubbo',
    'Wagga': 'Wagga Wagga',
    'Albury': 'Albury',
    'Moruya': 'Moruya',
    'Port Macquarie': 'Port Macquarie',
    'Taree': 'Taree',
    'Grafton': 'Grafton',
    'Lismore': 'Lismore',
    'Ballina': 'Ballina',
    'Scone': 'Scone',
    'Muswellbrook': 'Muswellbrook',
    'Tamworth': 'Tamworth',
    'Armidale': 'Armidale',
    'Gunnedah': 'Gunnedah',
    'Goulburn': 'Goulburn',
    'Queanbeyan': 'Queanbeyan',
    'Tuncurry': 'Tuncurry',
    
    # VIC Country
    'Ballarat': 'Ballarat',
    'Bendigo': 'Bendigo',
    'Sale': 'Sale',
    'Moe': 'Moe',
    'Warrnambool': 'Warrnambool',
    'Ararat': 'Ararat',
    'Kyneton': 'Kyneton',
    'Seymour': 'Seymour',
    'Wangaratta': 'Wangaratta',
    'Wodonga': 'Wodonga',
    'Terang': 'Terang',
    'Colac': 'Colac',
    'Yarra Glen': 'Yarra Valley',
    
    # QLD Country
    'Townsville': 'Townsville',
    'Rockhampton': 'Rockhampton',
    'Gatton': 'Gatton',
    'Cairns': 'Cairns',
    
    # SA Country
    'Strathalbyn': 'Strathalbyn',
    'Mt Gambier': 'Mount Gambier',
    'Port Lincoln': 'Port Lincoln',
    'Beaumont': 'Murray Bridge',
    
    # WA Country
    'Albany': 'Albany',
    'Geraldton': 'Geraldton',
}


class BetfairService:
    def __init__(self):
        self.session_token = None
        self.proxies = None
        
        # Setup proxy if credentials available
        if PROXY_HOST and PROXY_USER and PROXY_PASS:
            self.proxies = {
                'http': f'http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}',
                'https': f'http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}'
            }
            logger.info(f"✓ Proxy configured: {PROXY_HOST} (Melbourne, AU)")
        else:
            logger.warning("⚠️ No proxy configured - may be blocked by Betfair")
        
    def login(self):
        """Login to Betfair and get session token"""
        try:
            logger.info("Attempting Betfair login...")
            logger.info(f"Username: {BETFAIR_USERNAME}")
            logger.info(f"App Key: {BETFAIR_APP_KEY}")
            logger.info(f"Endpoint: {IDENTITY_URL}")
            logger.info(f"Using Proxy: {bool(self.proxies)}")
            
            headers = {
                'X-Application': BETFAIR_APP_KEY,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            payload = {
                'username': BETFAIR_USERNAME,
                'password': BETFAIR_PASSWORD
            }
            
            # Make request with or without proxy
            if self.proxies:
                response = requests.post(IDENTITY_URL, data=payload, headers=headers, 
                                       proxies=self.proxies, timeout=30)
            else:
                response = requests.post(IDENTITY_URL, data=payload, headers=headers, timeout=10)
            
            logger.info(f"Response Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Login Status: {data.get('loginStatus')}")
                if data.get('loginStatus') == 'SUCCESS':
                    self.session_token = data.get('sessionToken')
                    logger.info("✓ Betfair login successful")
                    return True
                else:
                    logger.error(f"Login failed: {data.get('loginStatus')}")
                    logger.error(f"Full response: {data}")
                    return False
            else:
                logger.error(f"Login HTTP error: {response.status_code}")
                logger.error(f"Response text: {response.text[:500]}")  # First 500 chars
                return False
                
        except Exception as e:
            logger.error(f"Login exception: {str(e)}")
            return False
    
    def _api_request(self, method, params):
        """Make authenticated API request to Betfair"""
        if not self.session_token:
            if not self.login():
                return None
        
        headers = {
            'X-Application': BETFAIR_APP_KEY,
            'X-Authentication': self.session_token,
            'Content-Type': 'application/json'
        }
        
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1
        }
        
        try:
            # Make request with or without proxy
            if self.proxies:
                response = requests.post(BETTING_URL, json=payload, headers=headers, 
                                       proxies=self.proxies, timeout=30)
            else:
                response = requests.post(BETTING_URL, json=payload, headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if 'result' in data:
                    return data['result']
                elif 'error' in data:
                    logger.error(f"API error: {data['error']}")
                    return None
            else:
                logger.error(f"API HTTP error: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"API request exception: {str(e)}")
            return None
    
    def find_markets_for_meeting(self, meeting_date, track_name):
        """Find Betfair markets for a specific meeting"""
        logger.info(f"Searching Betfair for {track_name} on {meeting_date}")
        
        # Map track name
        betfair_track = TRACK_MAPPING.get(track_name, track_name)
        
        # Search for horse racing markets on this date
        params = {
            "filter": {
                "eventTypeIds": ["7"],  # 7 = Horse Racing
                "marketCountries": ["AU"],
                "marketTypeCodes": ["WIN"],
                "marketStartTime": {
                    "from": f"{meeting_date}T00:00:00Z",
                    "to": f"{meeting_date}T23:59:59Z"
                }
            },
            "marketProjection": ["EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION"],
            "maxResults": "200"
        }
        
        markets = self._api_request("SportsAPING/v1.0/listMarketCatalogue", params)
        
        if not markets:
            logger.warning(f"No markets found for {track_name} on {meeting_date}")
            return []
        
        # Filter markets by track name (fuzzy matching)
        matching_markets = []
        for market in markets:
            event_name = market.get('event', {}).get('name', '')
            
            # Check if track name appears in event name
            if self._track_match(betfair_track, event_name):
                matching_markets.append(market)
        
        logger.info(f"Found {len(matching_markets)} markets for {track_name}")
        return matching_markets
    
    def _track_match(self, track_name, event_name):
        """Fuzzy match track name in event name"""
        track_lower = track_name.lower()
        event_lower = event_name.lower()
        
        # Direct substring match
        if track_lower in event_lower:
            return True
        
        # Fuzzy match with threshold
        ratio = SequenceMatcher(None, track_lower, event_lower).ratio()
        return ratio > 0.6
    
    def match_race_to_market(self, race_number, markets):
        """Match a race number to a Betfair market"""
        # Betfair market names usually contain "R1", "R2", etc.
        for market in markets:
            market_name = market.get('marketName', '')
            
            # Extract race number from market name (handles "R1", "Race 1", "1st", etc.)
            if f"R{race_number}" in market_name or f"Race {race_number}" in market_name:
                return market.get('marketId')
        
        logger.warning(f"Could not match race {race_number} to Betfair market")
        return None
    
    def get_race_results(self, market_id):
        """Fetch results for a settled market"""
        logger.info(f"Fetching results for market {market_id}")
        
        params = {
            "marketIds": [market_id],
            "priceProjection": {
                "priceData": ["SP_AVAILABLE", "SP_TRADED"]
            }
        }
        
        result = self._api_request("SportsAPING/v1.0/listMarketBook", params)
        
        if not result or len(result) == 0:
            logger.warning(f"No results for market {market_id}")
            return None
        
        market_book = result[0]
        
        # Check if market is settled
        if market_book.get('status') != 'CLOSED':
            logger.warning(f"Market {market_id} not yet settled (status: {market_book.get('status')})")
            return None
        
        runners = market_book.get('runners', [])
        
        results = []
        for runner in runners:
            selection_id = runner.get('selectionId')
            status = runner.get('status')
            
            # Get SP (Starting Price)
            sp_data = runner.get('sp', {})
            sp = sp_data.get('nearPrice') or sp_data.get('farPrice')
            
            # Determine finish position
            if status == 'WINNER':
                position = 1
            elif status == 'PLACED':
                position = 2  # We'll refine this later
            elif status == 'LOSER':
                position = 5  # Unplaced
            else:
                position = 0  # Scratched or non-runner
            
            results.append({
                'selection_id': selection_id,
                'status': status,
                'position': position,
                'sp': sp
            })
        
        return results
    
    def get_runner_names(self, market_id):
        """Get runner (horse) names for a market"""
        params = {
            "filter": {
                "marketIds": [market_id]
            },
            "marketProjection": ["RUNNER_DESCRIPTION"]
        }
        
        result = self._api_request("SportsAPING/v1.0/listMarketCatalogue", params)
        
        if not result or len(result) == 0:
            return {}
        
        market = result[0]
        runners = market.get('runners', [])
        
        # Build mapping: selection_id -> horse_name
        runner_map = {}
        for runner in runners:
            selection_id = runner.get('selectionId')
            horse_name = runner.get('runnerName', '')
            runner_map[selection_id] = horse_name
        
        return runner_map
    
    def match_horse_to_runner(self, horse_name, runner_names):
        """Fuzzy match horse name to Betfair runner name"""
        horse_lower = horse_name.lower().strip()
        
        best_match = None
        best_ratio = 0
        
        for selection_id, runner_name in runner_names.items():
            runner_lower = runner_name.lower().strip()
            
            # Direct match
            if horse_lower == runner_lower:
                return selection_id
            
            # Fuzzy match
            ratio = SequenceMatcher(None, horse_lower, runner_lower).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = selection_id
        
        # Only return match if confidence is high enough
        if best_ratio > 0.8:
            return best_match
        
        logger.warning(f"Could not match horse '{horse_name}' (best ratio: {best_ratio})")
        return None


def parse_meeting_name(meeting_name):
    """Parse meeting name to extract date and track"""
    try:
        parts = meeting_name.split('_')
        if len(parts) != 2:
            return None, None
        
        date_str, track = parts
        
        # Parse YYMMDD format
        year = int('20' + date_str[0:2])
        month = int(date_str[2:4])
        day = int(date_str[4:6])
        
        meeting_date = datetime(year, month, day).strftime('%Y-%m-%d')
        
        return meeting_date, track
        
    except Exception as e:
        logger.error(f"Error parsing meeting name '{meeting_name}': {e}")
        return None, None
