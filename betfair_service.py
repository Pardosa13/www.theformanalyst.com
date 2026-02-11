import os
import betfairlightweight
import requests
from datetime import datetime
from difflib import SequenceMatcher
import logging

logger = logging.getLogger(__name__)

BETFAIR_USERNAME = os.environ.get('BETFAIR_USERNAME')
BETFAIR_PASSWORD = os.environ.get('BETFAIR_PASSWORD')
BETFAIR_APP_KEY = os.environ.get('BETFAIR_APP_KEY', 'amyMWFeTpLAxmSyo')

# SmartProxy Configuration
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
        self.trading = None
        self.logged_in = False
        
        if not BETFAIR_USERNAME or not BETFAIR_PASSWORD or not BETFAIR_APP_KEY:
            logger.error("❌ Missing Betfair credentials")
            return
        
        # Create a custom requests session with proxy
        session = requests.Session()
        
        if PROXY_HOST and PROXY_USER and PROXY_PASS:
            # Configure proxy
            proxy_url = f'http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}'
            session.proxies = {
                'http': proxy_url,
                'https': proxy_url
            }
            logger.info(f"✓ Proxy configured: {PROXY_HOST}")
        else:
            logger.warning("⚠️ No proxy configured - Betfair may block requests")
        
        # Set timeouts to prevent hanging
        session.request = self._timeout_wrapper(session.request)
        
        # Initialize the betfairlightweight client with custom session
        self.trading = betfairlightweight.APIClient(
            username=BETFAIR_USERNAME,
            password=BETFAIR_PASSWORD,
            app_key=BETFAIR_APP_KEY,
            session=session  # Pass custom session with proxy
        )
        
        logger.info("✓ Betfair client initialized")
    
    def _timeout_wrapper(self, original_request):
        """Wrap requests with timeout to prevent hanging"""
        def request_with_timeout(*args, **kwargs):
            if 'timeout' not in kwargs:
                kwargs['timeout'] = 10  # 10 second timeout
            return original_request(*args, **kwargs)
        return request_with_timeout
    
    def login(self):
        """Login to Betfair using interactive login (no certs needed)"""
        if not self.trading:
            logger.error("❌ Betfair client not initialized")
            return False
        
        if self.logged_in:
            return True
        
        try:
            logger.info("=" * 60)
            logger.info("Attempting Betfair login...")
            logger.info(f"Username: {BETFAIR_USERNAME}")
            logger.info(f"App Key: {BETFAIR_APP_KEY}")
            
            # Use interactive login (no SSL certificates required)
            self.trading.login_interactive()
            
            self.logged_in = True
            logger.info("✅ Betfair login successful!")
            logger.info(f"Session token: {self.trading.session_token[:20]}...")
            logger.info("=" * 60)
            return True
            
        except Exception as e:
            logger.error(f"❌ Betfair login failed: {str(e)}")
            logger.error("=" * 60)
            return False
    
    def find_markets_for_meeting(self, meeting_date, track_name):
        """Find Betfair markets for a specific meeting"""
        if not self.login():
            return []
        
        logger.info("=" * 60)
        logger.info(f"Searching Betfair for: {track_name} on {meeting_date}")
        
        betfair_track = TRACK_MAPPING.get(track_name, track_name)
        logger.info(f"Mapped to Betfair track: {betfair_track}")
        
        try:
            # Create market filter using betfairlightweight's filter system
            market_filter = betfairlightweight.filters.market_filter(
                event_type_ids=['7'],  # Horse Racing
                market_countries=['AU'],
                market_type_codes=['WIN'],
                market_start_time={
                    'from': f"{meeting_date}T00:00:00Z",
                    'to': f"{meeting_date}T23:59:59Z"
                }
            )
            
            # List market catalogue
            markets = self.trading.betting.list_market_catalogue(
                filter=market_filter,
                market_projection=['EVENT', 'MARKET_START_TIME', 'RUNNER_DESCRIPTION'],
                max_results=200
            )
            
            if not markets:
                logger.warning(f"⚠️ No markets found for {track_name} on {meeting_date}")
                logger.info("=" * 60)
                return []
            
            logger.info(f"Found {len(markets)} total markets on {meeting_date}")
            
            # Filter markets by track name
            matching_markets = []
            for market in markets:
                event_name = market.event.name if hasattr(market, 'event') else ''
                
                if self._track_match(betfair_track, event_name):
                    logger.info(f"  ✓ Matched: {event_name}")
                    matching_markets.append(market)
            
            logger.info(f"✅ Found {len(matching_markets)} markets for {track_name}")
            logger.info("=" * 60)
            return matching_markets
            
        except Exception as e:
            logger.error(f"❌ Error finding markets: {str(e)}")
            logger.info("=" * 60)
            return []
    
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
            market_name = market.market_name if hasattr(market, 'market_name') else ''
            market_id = market.market_id if hasattr(market, 'market_id') else ''
            
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
        
        try:
            # Get market book with SP prices
            price_projection = betfairlightweight.filters.price_projection(
                price_data=['SP_AVAILABLE', 'SP_TRADED']
            )
            
            market_books = self.trading.betting.list_market_book(
                market_ids=[market_id],
                price_projection=price_projection
            )
            
            if not market_books or len(market_books) == 0:
                logger.warning(f"⚠️ No results for market {market_id}")
                return None
            
            market_book = market_books[0]
            status = market_book.status
            
            logger.info(f"Market status: {status}")
            
            if status != 'CLOSED':
                logger.warning(f"⚠️ Market not settled (status: {status})")
                return None
            
            runners = market_book.runners
            logger.info(f"Found {len(runners)} runners")
            
            results = []
            for runner in runners:
                selection_id = runner.selection_id
                runner_status = runner.status
                
                # Get SP price
                sp = None
                if hasattr(runner, 'sp') and runner.sp:
                    sp = runner.sp.near_price or runner.sp.far_price
                
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
            
        except Exception as e:
            logger.error(f"❌ Error fetching results: {str(e)}")
            return None
    
    def get_runner_names(self, market_id):
        """Get runner names for a market"""
        if not self.login():
            return {}
        
        try:
            market_filter = betfairlightweight.filters.market_filter(
                market_ids=[market_id]
            )
            
            markets = self.trading.betting.list_market_catalogue(
                filter=market_filter,
                market_projection=['RUNNER_DESCRIPTION']
            )
            
            if not markets or len(markets) == 0:
                return {}
            
            market = markets[0]
            runners = market.runners if hasattr(market, 'runners') else []
            
            runner_map = {}
            for runner in runners:
                selection_id = runner.selection_id
                horse_name = runner.runner_name if hasattr(runner, 'runner_name') else ''
                runner_map[selection_id] = horse_name
            
            return runner_map
            
        except Exception as e:
            logger.error(f"❌ Error getting runner names: {str(e)}")
            return {}
    
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
