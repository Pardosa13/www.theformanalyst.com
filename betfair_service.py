import os
import requests
from datetime import datetime
from difflib import SequenceMatcher
import logging

logger = logging.getLogger(__name__)

BETFAIR_USERNAME = os.environ.get('BETFAIR_USERNAME')
BETFAIR_PASSWORD = os.environ.get('BETFAIR_PASSWORD')
BETFAIR_APP_KEY = os.environ.get('BETFAIR_APP_KEY', 'amyMWFeTpLAxmSyo')

# SmartProxy Configuration (Optional - will try without first)
PROXY_HOST = os.environ.get('PROXY_HOST')
PROXY_USER = os.environ.get('PROXY_USER')
PROXY_PASS = os.environ.get('PROXY_PASS')

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
        self.logged_in = False
        
        if not BETFAIR_USERNAME or not BETFAIR_PASSWORD or not BETFAIR_APP_KEY:
            logger.error("❌ Missing Betfair credentials")
            return
        
        logger.info("✓ Betfair service initialized")
    
    def login(self):
        """
        Login to Betfair using Interactive Login endpoint
        Tries direct connection first, then proxy if that fails
        """
        if self.logged_in and self.session_token:
            return True
        
        logger.info("=" * 60)
        logger.info("Attempting Betfair login...")
        logger.info(f"Username: {BETFAIR_USERNAME}")
        logger.info(f"App Key: {BETFAIR_APP_KEY}")
        
        # Try direct connection first (no proxy)
        if self._try_login(use_proxy=False):
            return True
        
        # If direct fails and proxy is configured, try with proxy
        if PROXY_HOST and PROXY_USER and PROXY_PASS:
            logger.info("Direct connection failed, trying proxy...")
            if self._try_login(use_proxy=True):
                return True
        
        logger.error("❌ All login attempts failed")
        logger.info("=" * 60)
        return False
    
    def _try_login(self, use_proxy=False):
        """Attempt login with or without proxy"""
        try:
            headers = {
                'X-Application': BETFAIR_APP_KEY,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
            
            data = {
                'username': BETFAIR_USERNAME,
                'password': BETFAIR_PASSWORD
            }
            
            proxies = None
            if use_proxy:
                proxy_url = f'http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}'
                proxies = {
                    'http': proxy_url,
                    'https': proxy_url
                }
                logger.info(f"Using proxy: {PROXY_HOST}")
            else:
                logger.info("Trying direct connection (no proxy)")
            
            # Interactive login endpoint
            url = 'https://identitysso.betfair.com/api/login'
            
            response = requests.post(
                url,
                headers=headers,
                data=data,
                proxies=proxies,
                timeout=10
            )
            
            logger.info(f"Response status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                
                if result.get('status') == 'SUCCESS':
                    self.session_token = result.get('token')
                    self.logged_in = True
                    logger.info("✅ Betfair login successful!")
                    logger.info(f"Session token: {self.session_token[:20]}...")
                    logger.info("=" * 60)
                    return True
                else:
                    logger.error(f"❌ Login failed: {result.get('error', 'Unknown error')}")
                    return False
            else:
                logger.error(f"❌ HTTP {response.status_code}: {response.text[:200]}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Login exception: {str(e)}")
            return False
    
    def _make_request(self, method, url, data=None):
        """Make authenticated API request"""
        if not self.session_token:
            if not self.login():
                return None
        
        headers = {
            'X-Application': BETFAIR_APP_KEY,
            'X-Authentication': self.session_token,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        try:
            if method == 'POST':
                response = requests.post(url, headers=headers, json=data, timeout=15)
            else:
                response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"API request failed: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"API request exception: {str(e)}")
            return None
    
    def find_markets_for_meeting(self, meeting_date, track_name):
        """Find Betfair markets for a specific meeting"""
        if not self.login():
            return []
        
        logger.info("=" * 60)
        logger.info(f"Searching Betfair for: {track_name} on {meeting_date}")
        
        betfair_track = TRACK_MAPPING.get(track_name, track_name)
        logger.info(f"Mapped to Betfair track: {betfair_track}")
        
        # Build filter
        filter_data = {
            'filter': {
                'eventTypeIds': ['7'],  # Horse Racing
                'marketCountries': ['AU'],
                'marketTypeCodes': ['WIN'],
                'marketStartTime': {
                    'from': f"{meeting_date}T00:00:00Z",
                    'to': f"{meeting_date}T23:59:59Z"
                }
            },
            'marketProjection': ['EVENT', 'MARKET_START_TIME', 'RUNNER_DESCRIPTION'],
            'maxResults': 200
        }
        
        url = 'https://api.betfair.com/exchange/betting/rest/v1.0/listMarketCatalogue/'
        result = self._make_request('POST', url, filter_data)
        
        if not result:
            logger.warning(f"⚠️ No markets found for {track_name} on {meeting_date}")
            logger.info("=" * 60)
            return []
        
        logger.info(f"Found {len(result)} total markets on {meeting_date}")
        
        # Filter markets by track name
        matching_markets = []
        for market in result:
            event_name = market.get('event', {}).get('name', '')
            
            if self._track_match(betfair_track, event_name):
                logger.info(f"  ✓ Matched: {event_name}")
                matching_markets.append(market)
        
        logger.info(f"✅ Found {len(matching_markets)} markets for {track_name}")
        logger.info("=" * 60)
        return matching_markets
    
    def _track_match(self, track_name, event_name):
        """Enhanced fuzzy matching for track names"""
        track_lower = track_name.lower()
        event_lower = event_name.lower()
        
        # Exact substring match
        if track_lower in event_lower:
            return True
        
        # Try variations
        track_variations = [
            track_lower,
            track_lower.replace(' ', ''),
            track_lower.replace('-', ' '),
            track_lower.split()[0] if ' ' in track_lower else track_lower
        ]
        
        for variation in track_variations:
            if variation in event_lower:
                return True
        
        # Fuzzy match with ratio threshold
        ratio = SequenceMatcher(None, track_lower, event_lower).ratio()
        return ratio > 0.6
    
    def match_race_to_market(self, race_number, markets):
        """Match race number to Betfair market"""
        logger.info(f"Matching race {race_number}...")
        
        for market in markets:
            market_name = market.get('marketName', '')
            market_id = market.get('marketId', '')
            
            # Common race number patterns
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
        if not self.login():
            return None
        
        logger.info(f"Fetching results for market {market_id}...")
        
        request_data = {
            'marketIds': [market_id],
            'priceProjection': {
                'priceData': ['SP_AVAILABLE', 'SP_TRADED']
            }
        }
        
        url = 'https://api.betfair.com/exchange/betting/rest/v1.0/listMarketBook/'
        result = self._make_request('POST', url, request_data)
        
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
            
            # Get SP price
            sp = None
            if 'sp' in runner and runner['sp']:
                sp = runner['sp'].get('nearPrice') or runner['sp'].get('farPrice')
            
            # Determine position based on status
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
        """Get runner names for a market"""
        if not self.login():
            return {}
        
        filter_data = {
            'filter': {
                'marketIds': [market_id]
            },
            'marketProjection': ['RUNNER_DESCRIPTION']
        }
        
        url = 'https://api.betfair.com/exchange/betting/rest/v1.0/listMarketCatalogue/'
        result = self._make_request('POST', url, filter_data)
        
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
        """Fuzzy match horse name to Betfair runner"""
        horse_lower = horse_name.lower().strip()
        
        best_match = None
        best_ratio = 0
        
        for selection_id, runner_name in runner_names.items():
            runner_lower = runner_name.lower().strip()
            
            # Exact match
            if horse_lower == runner_lower:
                return selection_id
            
            # Fuzzy match
            ratio = SequenceMatcher(None, horse_lower, runner_lower).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = selection_id
        
        # Return if match is strong enough
        if best_ratio > 0.8:
            return best_match
        
        logger.warning(f"⚠️ Could not match horse '{horse_name}' (best: {best_ratio:.2f})")
        return None


def parse_meeting_name(meeting_name):
    """Parse meeting name in format YYMMDD_TrackName"""
    try:
        parts = meeting_name.split('_')
        if len(parts) != 2:
            logger.warning(f"Invalid meeting name format: {meeting_name}")
            return None, None
        
        date_str, track = parts
        
        # Parse date: YYMMDD
        year = int('20' + date_str[0:2])
        month = int(date_str[2:4])
        day = int(date_str[4:6])
        
        meeting_date = datetime(year, month, day).strftime('%Y-%m-%d')
        
        return meeting_date, track
        
    except Exception as e:
        logger.error(f"Error parsing meeting name '{meeting_name}': {e}")
        return None, None
