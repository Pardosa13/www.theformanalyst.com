"""
Betfair Live Odds Service

A Flask-based microservice that polls Betfair Exchange for live odds
and streams updates via Server-Sent Events (SSE).

Environment Variables Required:
- BETFAIR_USERNAME: Betfair account username
- BETFAIR_PASSWORD: Betfair account password
- BETFAIR_APP_KEY: Betfair API application key
- BETFAIR_PEM_B64: (Optional) Base64-encoded PEM certificate for cert-login
- BETFAIR_MARKET_IDS: (Optional) Comma-separated market IDs to poll
- BETFAIR_POLL_INTERVAL: Polling interval in seconds (default: 2)
- BETFAIR_TLD: Betfair TLD (default: com, use com.au for Australia)
- BETFAIR_ENABLED: Set to 'true' to enable the service
- PORT: Port to run the service on (default: 8081)

DO NOT commit any secrets or certificate files to the repository.
"""

import os
import sys
import json
import time
import base64
import logging
import tempfile
import hashlib
import threading
from datetime import datetime
from queue import Queue

import requests
from flask import Flask, Response, jsonify, stream_with_context

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('betfair_service')

app = Flask(__name__)

# ============================================================================
# Configuration
# ============================================================================

BETFAIR_ENABLED = os.environ.get('BETFAIR_ENABLED', 'false').lower() == 'true'
BETFAIR_USERNAME = os.environ.get('BETFAIR_USERNAME', '')
BETFAIR_PASSWORD = os.environ.get('BETFAIR_PASSWORD', '')
BETFAIR_APP_KEY = os.environ.get('BETFAIR_APP_KEY', '')
BETFAIR_PEM_B64 = os.environ.get('BETFAIR_PEM_B64', '')
BETFAIR_MARKET_IDS = os.environ.get('BETFAIR_MARKET_IDS', '')
BETFAIR_POLL_INTERVAL = int(os.environ.get('BETFAIR_POLL_INTERVAL', '2'))
BETFAIR_TLD = os.environ.get('BETFAIR_TLD', 'com')
PORT = int(os.environ.get('PORT', '8081'))

# Betfair API limits
BETFAIR_MAX_MARKETS_PER_REQUEST = 40  # Betfair API limit

# Betfair API endpoints
IDENTITY_URL = f'https://identitysso.betfair.{BETFAIR_TLD}/api'
IDENTITY_CERT_URL = f'https://identitysso-cert.betfair.{BETFAIR_TLD}/api'
EXCHANGE_URL = f'https://api.betfair.{BETFAIR_TLD}/exchange/betting/rest/v1.0'

# Global state
session_token = None
session_lock = threading.Lock()
last_payloads = {}  # Market ID -> hash of last payload (for deduplication)
subscribers = []  # List of SSE subscriber queues
subscribers_lock = threading.Lock()
backoff_until = 0  # Timestamp for rate limit backoff

# ============================================================================
# Betfair Authentication
# ============================================================================


def authenticate_with_cert(pem_path):
    """Authenticate using certificate-based login."""
    global session_token
    
    url = f'{IDENTITY_CERT_URL}/certlogin'
    headers = {
        'X-Application': BETFAIR_APP_KEY,
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    data = {
        'username': BETFAIR_USERNAME,
        'password': BETFAIR_PASSWORD
    }
    
    try:
        response = requests.post(
            url, 
            headers=headers, 
            data=data, 
            cert=pem_path,
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        
        if result.get('loginStatus') == 'SUCCESS':
            session_token = result.get('sessionToken')
            logger.info('Certificate authentication successful')
            return True
        else:
            logger.error(f"Cert login failed: {result.get('loginStatus')}")
            return False
    except requests.RequestException as e:
        logger.error(f"Cert authentication request failed: {e}")
        return False


def authenticate_with_password():
    """Authenticate using username/password login."""
    global session_token
    
    url = f'{IDENTITY_URL}/login'
    headers = {
        'X-Application': BETFAIR_APP_KEY,
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }
    data = {
        'username': BETFAIR_USERNAME,
        'password': BETFAIR_PASSWORD
    }
    
    try:
        response = requests.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        if result.get('status') == 'SUCCESS':
            session_token = result.get('token')
            logger.info('Password authentication successful')
            return True
        else:
            logger.error(f"Password login failed: {result.get('status')}")
            return False
    except requests.RequestException as e:
        logger.error(f"Password authentication request failed: {e}")
        return False


def authenticate():
    """Authenticate with Betfair, trying cert-login first if available."""
    global session_token
    
    with session_lock:
        # Try certificate login first if PEM is provided
        if BETFAIR_PEM_B64:
            try:
                pem_data = base64.b64decode(BETFAIR_PEM_B64)
                with tempfile.NamedTemporaryFile(
                    mode='wb', suffix='.pem', delete=False
                ) as pem_file:
                    pem_file.write(pem_data)
                    pem_path = pem_file.name
                
                try:
                    if authenticate_with_cert(pem_path):
                        return True
                finally:
                    # Clean up temp file
                    try:
                        os.unlink(pem_path)
                    except OSError:
                        pass
                        
                logger.warning('Cert login failed, falling back to password login')
            except Exception as e:
                logger.error(f"Error decoding PEM certificate: {e}")
        
        # Fallback to password login
        return authenticate_with_password()


def ensure_authenticated():
    """Ensure we have a valid session, re-authenticating if needed."""
    global session_token
    
    if session_token:
        return True
    
    return authenticate()


# ============================================================================
# Betfair API Calls
# ============================================================================


def call_betfair_api(operation, params):
    """Make a call to the Betfair Exchange API."""
    global session_token, backoff_until
    
    # Check rate limit backoff
    if time.time() < backoff_until:
        wait_time = backoff_until - time.time()
        logger.warning(f"Rate limited, waiting {wait_time:.1f}s")
        time.sleep(wait_time)
    
    if not ensure_authenticated():
        logger.error('Failed to authenticate with Betfair')
        return None
    
    url = f'{EXCHANGE_URL}/{operation}/'
    headers = {
        'X-Application': BETFAIR_APP_KEY,
        'X-Authentication': session_token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    try:
        response = requests.post(
            url, 
            headers=headers, 
            json=params, 
            timeout=30
        )
        
        # Handle rate limiting
        if response.status_code == 429:
            backoff_until = time.time() + 60  # Back off for 60 seconds
            logger.warning('Rate limited by Betfair, backing off for 60s')
            return None
        
        # Handle auth errors - re-authenticate
        if response.status_code in (401, 403):
            logger.warning('Authentication expired, re-authenticating')
            session_token = None
            if ensure_authenticated():
                # Retry the request
                headers['X-Authentication'] = session_token
                response = requests.post(
                    url, 
                    headers=headers, 
                    json=params, 
                    timeout=30
                )
            else:
                return None
        
        response.raise_for_status()
        return response.json()
        
    except requests.RequestException as e:
        logger.error(f"Betfair API request failed: {e}")
        return None


def get_market_book(market_ids):
    """Get market book (live odds) for specified market IDs."""
    params = {
        'marketIds': market_ids,
        'priceProjection': {
            'priceData': ['EX_BEST_OFFERS'],
            'virtualise': False
        }
    }
    return call_betfair_api('listMarketBook', params)


def get_market_ids_from_db():
    """
    Get market IDs from the database (Race.market_id).
    This requires database access - import models dynamically.
    """
    try:
        # Try to import from the main app's models
        from models import Race, db
        from app import app as main_app
        
        with main_app.app_context():
            races = Race.query.filter(
                Race.market_id.isnot(None),
                Race.market_id != ''
            ).all()
            return [race.market_id for race in races]
    except ImportError:
        logger.warning('Could not import models for DB market IDs')
        return []
    except Exception as e:
        logger.error(f"Error getting market IDs from DB: {e}")
        return []


def get_market_ids():
    """Get list of market IDs to poll."""
    # First check environment variable
    if BETFAIR_MARKET_IDS:
        return [m.strip() for m in BETFAIR_MARKET_IDS.split(',') if m.strip()]
    
    # Otherwise get from database
    return get_market_ids_from_db()


# ============================================================================
# Result Processing
# ============================================================================


def process_closed_market(market_data):
    """
    Process a closed market and update Horse records with final results.
    """
    market_id = market_data.get('marketId')
    runners = market_data.get('runners', [])
    
    logger.info(f"Processing closed market: {market_id}")
    
    # Sort runners by final position (if available) or status
    results = []
    for runner in runners:
        selection_id = runner.get('selectionId')
        status = runner.get('status')  # WINNER, LOSER, REMOVED
        
        # Try to get final position from metadata if available
        # Betfair doesn't always provide explicit positions
        position = None
        if status == 'WINNER':
            position = 1
        
        results.append({
            'selection_id': selection_id,
            'status': status,
            'position': position,
            'last_price_traded': runner.get('lastPriceTraded')
        })
    
    # Update database records
    try:
        from models import Race, Horse, db
        from app import app as main_app
        
        with main_app.app_context():
            race = Race.query.filter_by(market_id=market_id).first()
            if race:
                for result in results:
                    horse = Horse.query.filter_by(
                        race_id=race.id,
                        betfair_selection_id=result['selection_id']
                    ).first()
                    
                    if horse:
                        if result['position']:
                            horse.final_position = result['position']
                        horse.final_odds = result['last_price_traded']
                        horse.result_settled_at = datetime.utcnow()
                        horse.result_source = 'betfair'
                        logger.info(
                            f"Updated horse {horse.horse_name}: "
                            f"position={result['position']}, "
                            f"odds={result['last_price_traded']}"
                        )
                
                db.session.commit()
                logger.info(f"Committed results for market {market_id}")
            else:
                logger.warning(f"No race found for market {market_id}")
                
    except ImportError:
        logger.warning('Could not import models for result processing')
    except Exception as e:
        logger.error(f"Error processing closed market results: {e}")


# ============================================================================
# SSE Broadcasting
# ============================================================================


def broadcast_message(message):
    """Broadcast a message to all SSE subscribers."""
    with subscribers_lock:
        dead_subscribers = []
        for queue in subscribers:
            try:
                queue.put_nowait(message)
            except Exception:
                dead_subscribers.append(queue)
        
        # Clean up dead subscribers
        for queue in dead_subscribers:
            try:
                subscribers.remove(queue)
            except ValueError:
                pass


def create_sse_message(event_type, data):
    """Create an SSE-formatted message."""
    return {
        'event': event_type,
        'data': data,
        'timestamp': datetime.utcnow().isoformat()
    }


# ============================================================================
# Polling Loop
# ============================================================================


def compute_payload_hash(payload):
    """Compute a hash of the payload for deduplication."""
    json_str = json.dumps(payload, sort_keys=True)
    return hashlib.md5(json_str.encode()).hexdigest()


def poll_markets():
    """Main polling loop for market data."""
    global last_payloads
    
    logger.info('Starting market polling loop')
    
    while True:
        try:
            market_ids = get_market_ids()
            
            if not market_ids:
                logger.debug('No market IDs to poll')
                time.sleep(BETFAIR_POLL_INTERVAL * 5)  # Longer sleep when no markets
                continue
            
            # Process markets in batches
            for i in range(0, len(market_ids), BETFAIR_MAX_MARKETS_PER_REQUEST):
                batch = market_ids[i:i + BETFAIR_MAX_MARKETS_PER_REQUEST]
                
                market_books = get_market_book(batch)
                
                if not market_books:
                    continue
                
                for market in market_books:
                    market_id = market.get('marketId')
                    market_status = market.get('status')
                    
                    # Compute payload hash for deduplication
                    payload_hash = compute_payload_hash(market)
                    
                    if last_payloads.get(market_id) == payload_hash:
                        continue  # No change, skip broadcast
                    
                    last_payloads[market_id] = payload_hash
                    
                    # Check if market is closed
                    if market_status == 'CLOSED':
                        logger.info(f"Market {market_id} is CLOSED")
                        process_closed_market(market)
                        
                        # Broadcast final result
                        broadcast_message(create_sse_message(
                            'market_closed',
                            {
                                'market_id': market_id,
                                'runners': [
                                    {
                                        'selection_id': r.get('selectionId'),
                                        'status': r.get('status'),
                                        'last_price_traded': r.get('lastPriceTraded')
                                    }
                                    for r in market.get('runners', [])
                                ]
                            }
                        ))
                    else:
                        # Broadcast live odds update
                        runners_data = []
                        for runner in market.get('runners', []):
                            best_back = None
                            best_lay = None
                            
                            ex = runner.get('ex', {})
                            available_to_back = ex.get('availableToBack', [])
                            available_to_lay = ex.get('availableToLay', [])
                            
                            if available_to_back:
                                best_back = available_to_back[0].get('price')
                            if available_to_lay:
                                best_lay = available_to_lay[0].get('price')
                            
                            runners_data.append({
                                'selection_id': runner.get('selectionId'),
                                'status': runner.get('status'),
                                'best_back': best_back,
                                'best_lay': best_lay,
                                'last_price_traded': runner.get('lastPriceTraded')
                            })
                        
                        broadcast_message(create_sse_message(
                            'odds_update',
                            {
                                'market_id': market_id,
                                'status': market_status,
                                'runners': runners_data
                            }
                        ))
            
            time.sleep(BETFAIR_POLL_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error in polling loop: {e}", exc_info=True)
            time.sleep(BETFAIR_POLL_INTERVAL * 2)  # Back off on errors


# ============================================================================
# Flask Routes
# ============================================================================


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'ok',
        'enabled': BETFAIR_ENABLED,
        'timestamp': datetime.utcnow().isoformat()
    })


@app.route('/stream')
def stream():
    """SSE endpoint for live market updates."""
    def generate():
        # Create a queue for this subscriber
        queue = Queue()
        
        with subscribers_lock:
            subscribers.append(queue)
        
        try:
            # Send initial connection message
            yield 'event: connected\n'
            yield f'data: {{"status": "connected", "timestamp": "{datetime.utcnow().isoformat()}"}}\n\n'
            
            while True:
                try:
                    # Wait for messages with timeout
                    message = queue.get(timeout=30)
                    
                    yield f"event: {message['event']}\n"
                    yield f"data: {json.dumps(message['data'])}\n\n"
                    
                except Exception:
                    # Send keepalive ping
                    yield f': keepalive {datetime.utcnow().isoformat()}\n\n'
                    
        finally:
            with subscribers_lock:
                try:
                    subscribers.remove(queue)
                except ValueError:
                    pass
    
    response = Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',  # Disable nginx buffering
            'Access-Control-Allow-Origin': '*'
        }
    )
    return response


@app.route('/status')
def status():
    """Get service status."""
    return jsonify({
        'enabled': BETFAIR_ENABLED,
        'authenticated': session_token is not None,
        'subscribers': len(subscribers),
        'market_ids': get_market_ids()[:10],  # First 10 only
        'poll_interval': BETFAIR_POLL_INTERVAL,
        'timestamp': datetime.utcnow().isoformat()
    })


# ============================================================================
# Main Entry Point
# ============================================================================


def start_polling_thread():
    """Start the background polling thread."""
    if not BETFAIR_ENABLED:
        logger.warning('Betfair service is disabled (BETFAIR_ENABLED=false)')
        return
    
    if not BETFAIR_APP_KEY:
        logger.error('BETFAIR_APP_KEY is required')
        return
    
    if not BETFAIR_USERNAME or not BETFAIR_PASSWORD:
        if not BETFAIR_MARKET_IDS:
            logger.error(
                'Credentials required: BETFAIR_USERNAME and BETFAIR_PASSWORD, '
                'or provide BETFAIR_MARKET_IDS'
            )
            return
    
    # Authenticate first
    if BETFAIR_USERNAME and BETFAIR_PASSWORD:
        if not authenticate():
            logger.error('Initial authentication failed')
            return
    
    # Start polling thread
    poll_thread = threading.Thread(target=poll_markets, daemon=True)
    poll_thread.start()
    logger.info('Started Betfair polling thread')


if __name__ == '__main__':
    logger.info(f'Starting Betfair service on port {PORT}')
    logger.info(f'BETFAIR_ENABLED: {BETFAIR_ENABLED}')
    
    if BETFAIR_ENABLED:
        start_polling_thread()
    else:
        logger.warning(
            'Betfair polling disabled. Set BETFAIR_ENABLED=true to enable.'
        )
    
    app.run(host='0.0.0.0', port=PORT, threaded=True)
