import os
import requests
from datetime import datetime
from difflib import SequenceMatcher
import logging
import time

logger = logging.getLogger(__name__)

BETFAIR_USERNAME = os.environ.get('BETFAIR_USERNAME')
BETFAIR_PASSWORD = os.environ.get('BETFAIR_PASSWORD')
BETFAIR_APP_KEY = os.environ.get('BETFAIR_APP_KEY', 'amyMWFeTpLAxmSyo')

PROXY_HOST = os.environ.get('PROXY_HOST')
PROXY_USER = os.environ.get('PROXY_USER')
PROXY_PASS = os.environ.get('PROXY_PASS')

IDENTITY_URL = "https://identitysso.betfair.com/api/login"
BETTING_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

TRACK_MAPPING = {
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
    'Eagle Farm': 'Eagle Farm',
    'Doomben': 'Doomben',
    'Gold Coast': 'Gold Coast',
    'Sunshine Coast': 'Sunshine Coast',
    'Ipswich': 'Ipswich',
    'Morphettville': 'Morphettville',
    'Morphettville Parks': 'Morphettville Parks',
    'Murray Bridge GH': 'Murray Bridge',
    'Gawler': 'Gawler',
    'Ascot': 'Ascot',
    'Belmont Park': 'Belmont Park',
    'Bunbury': 'Bunbury',
    'Pinjarra Scarpside': 'Pinjarra Park',
    'Hobart': 'Hobart',
    'Launceston': 'Launceston',
    'Canberra': 'Canberra',
}


class BetfairService:
    def __init__(self):
        self.session_token = None
        self.proxies = None
        self.max_retries = 2
        
        if PROXY_HOST and PROXY_USER and PROXY_PASS:
            self.proxies = {
                'http': f'http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}',
                'https': f'http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}'
            }
            logger.info(f"✓ Proxy configured: {PROXY_HOST}")
        else:
            logger.warning("⚠️ No proxy configured - Betfair may block requests")
    
    def login(self):
        """Login to Betfair with retry logic"""
        logger.info("=" * 60)
        logger.info("Attempting Betfair login...")
        logger.info(f"Username: {BETFAIR_USERNAME}")
        logger.info(f"App Key: {BETFAIR_APP_KEY}")
        logger.info(f"Using Proxy: {bool(self.proxies)}")
        
        if not BETFAIR_USERNAME or not BETFAIR_PASSWORD:
            logger.error("❌ Missing Betfair credentials")
            return False
        
        headers = {
            'X-Application': BETFAIR_APP_KEY,
            'Content-Type': 'application/x-www-form-urlencoded'
            'Accept': 'application/json'
        }
        
        payload = {
            'username': BETFAIR_USERNAME,
            'password': BETFAIR_PASSWORD
        }
        
        for attempt in range(self.max_retries):
            try:
                logger.info(f"Login attempt {attempt + 1}/{self.max_retries}...")
                
                if self.proxies and attempt == 0:
                    response = requests.post(
                        IDENTITY_URL,
                        data=payload,
                        headers=headers,
                        proxies=self.proxies,
                        timeout=30
                    )
                else:
                    logger.info("Trying direct connection (no proxy)...")
                    response = requests.post(
                        IDENTITY_URL,
                        data=payload,
                        headers=headers,
                        timeout=10
                    )
                
                logger.info(f"Response Status: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    status = data.get('loginStatus')
                    
                    logger.info(f"Login Status: {status}")
                    
                    if status == 'SUCCESS':
                        self.session_token = data.get('sessionToken')
                        logger.info("✅ Betfair login successful!")
                        logger.info("=" * 60)
                        return True
                    else:
                        logger.error(f"❌ Login failed: {status}")
                        logger.error(f"Response: {data}")
                        return False
                else:
                    logger.error(f"❌ HTTP Error: {response.status_code}")
                    logger.error(f"Response: {response.text[:500]}")
                    
                    if attempt < self.max_retries - 1:
                        time.sleep(2)
                        continue
                    return False
                    
            except requests.exceptions.Timeout:
                logger.error(f"❌ Request timed out (attempt {attempt + 1})")
                if attempt < self.max_retries - 1:
                    time.sleep(2)
                    continue
                return False
            except Exception as e:
                logger.error(f"❌ Login exception: {str(e)}")
                if attempt < self.max_retries - 1:
                    time.sleep(2)
                    continue
                return False
        
        logger.info("=" * 60)
        return False
    
    def _api_request(self, method, params):
        """Make authenticated API request with retry logic"""
        if not self.session_token:
            logger.warning("No session token, attempting login...")
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
        
        for attempt in range(self.max_retries):
            try:
                if self.proxies and attempt == 0:
                    response = requests.post(
                        BETTING_URL,
                        json=payload,
                        headers=headers,
                        proxies=self.proxies,
                        timeout=30
                    )
                else:
                    response = requests.post(
                        BETTING_URL,
                        json=payload,
                        headers=headers,
                        timeout=30
                    )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if 'result' in data:
                        return data['result']
                    elif 'error' in data:
                        error = data['error']
                        logger.error(f"❌ API error: {error}")
                        
                        if 'INVALID_SESSION_INFORMATION' in str(error):
                            logger.info("Session expired, re-logging in...")
                            if self.login():
                                headers['X-Authentication'] = self.session_token
                                continue
                        
                        return None
                else:
                    logger.error(f"❌ API HTTP error: {response.status_code}")
                    logger.error(f"Response: {response.text[:500]}")
                    return None
                    
            except Exception as e:
                logger.error(f"❌ API request exception (attempt {attempt + 1}): {str(e)}")
                if attempt < self.max_retries - 1:
                    time.sleep(2)
                    continue
                return None
        
        return None
    
    def find_markets_for_meeting(self, meeting_date, track_name):
        """Find Betfair markets for a specific meeting"""
        logger.info("=" * 60)
        logger.info(f"Searching Betfair for: {track_name} on {meeting_date}")
        
        betfair_track = TRACK_MAPPING.get(track_name, track_name)
        logger.info(f"Mapped to Betfair track: {betfair_track}")
        
        params = {
            "filter": {
                "eventTypeIds": ["7"],
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
        
        logger.info(f"Search params: Event Type=7, Country=AU, Date={meeting_date}")
        
        markets = self._api_request("SportsAPING/v1.0/listMarketCatalogue", params)
        
        if not markets:
            logger.warning(f"⚠️ No markets found for {track_name} on {meeting_date}")
            logger.info("=" * 60)
            return []
        
        logger.info(f"Found {len(markets)} total markets on {meeting_date}")
        
        matching_markets = []
        for market in markets:
            event_name = market.get('event', {}).get('name', '')
            
            if self._track_match(betfair_track, event_name):
                logger.info(f"  ✓ Matched: {event_name}")
                matching_markets.append(market)
        
        logger.info(f"✅ Found {len(matching_markets)} markets for {track_name}")
        logger.info("=" * 60)
        return matching_markets
    
    def _track_match(self, track_name, event_name):
        """Enhanced fuzzy matching"""
        track_lower = track_name.lower()
        event_lower = event_name.lower()
        
        if track_lower in event_lower:
            return True
        
        track_variations = [
            track_lower,
            track_lower.replace(' ', ''),
            track_lower.replace('-', ' '),
            track_lower.split()[0] if ' ' in track_lower else track_lower
        ]
        
        for variation in track_variations:
            if variation in event_lower:
                return True
        
        ratio = SequenceMatcher(None, track_lower, event_lower).ratio()
        return ratio > 0.6
    
    def match_race_to_market(self, race_number, markets):
        """Match race number to Betfair market"""
        logger.info(f"Matching race {race_number}...")
        
        for market in markets:
            market_name = market.get('marketName', '')
            market_id = market.get('marketId')
            
            patterns = [
                f"R{race_number} ",
                f"R{race_number}:",
                f"Race {race_number} ",
                f"Race {race_number}:",
                f" {race_number}R ",
            ]
            
            for pattern in patterns:
                if pattern in market_name:
                    logger.info(f"  ✓ Matched R{race_number} to: {market_name} (ID: {market_id})")
                    return market_id
        
        logger.warning(f"  ⚠️ Could not match race {race_number}")
        return None
    
    def get_race_results(self, market_id):
        """Fetch results for settled market"""
        logger.info(f"Fetching results for market {market_id}...")
        
        params = {
            "marketIds": [market_id],
            "priceProjection": {
                "priceData": ["SP_AVAILABLE", "SP_TRADED"]
            }
        }
        
        result = self._api_request("SportsAPING/v1.0/listMarketBook", params)
        
        if not result or len(result) == 0:
            logger.warning(f"⚠️ No results for market {market_id}")
            return None
        
        market_book = result[0]
        status = market_book.get('status')
        
        logger.info(f"Market status: {status}")
        
        if status != 'CLOSED':
            logger.warning(f"⚠️ Market not settled (status: {status})")
            return None
        
        runners = market_book.get('runners', [])
        logger.info(f"Found {len(runners)} runners")
        
        results = []
        for runner in runners:
            selection_id = runner.get('selectionId')
            runner_status = runner.get('status')
            
            sp_data = runner.get('sp', {})
            sp = sp_data.get('nearPrice') or sp_data.get('farPrice')
            
            if runner_status == 'WINNER':
                position = 1
            elif runner_status == 'PLACED':
                position = 2
            elif runner_status == 'LOSER':
                position = 5
            else:
                position = 0
            
            results.append({
                'selection_id': selection_id,
                'status': runner_status,
                'position': position,
                'sp': sp
            })
        
        logger.info(f"✅ Processed {len(results)} results")
        return results
    
    def get_runner_names(self, market_id):
        """Get runner names"""
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
        
        runner_map = {}
        for runner in runners:
            selection_id = runner.get('selectionId')
            horse_name = runner.get('runnerName', '')
            runner_map[selection_id] = horse_name
        
        return runner_map
    
    def match_horse_to_runner(self, horse_name, runner_names):
        """Fuzzy match horse to runner"""
        horse_lower = horse_name.lower().strip()
        
        best_match = None
        best_ratio = 0
        
        for selection_id, runner_name in runner_names.items():
            runner_lower = runner_name.lower().strip()
            
            if horse_lower == runner_lower:
                return selection_id
            
            ratio = SequenceMatcher(None, horse_lower, runner_lower).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = selection_id
        
        if best_ratio > 0.8:
            return best_match
        
        logger.warning(f"⚠️ Could not match horse '{horse_name}' (best: {best_ratio:.2f})")
        return None


def parse_meeting_name(meeting_name):
    """Parse meeting name"""
    try:
        parts = meeting_name.split('_')
        if len(parts) != 2:
            logger.warning(f"Invalid meeting name: {meeting_name}")
            return None, None
        
        date_str, track = parts
        
        year = int('20' + date_str[0:2])
        month = int(date_str[2:4])
        day = int(date_str[4:6])
        
        meeting_date = datetime(year, month, day).strftime('%Y-%m-%d')
        
        return meeting_date, track
        
    except Exception as e:
        logger.error(f"Error parsing '{meeting_name}': {e}")
        return None, None
