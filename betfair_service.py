#!/usr/bin/env python3
"""
Betfair Live Odds Service

A Flask microservice that polls Betfair Exchange for live odds updates
and streams them via Server-Sent Events (SSE).

Environment Variables:
    BETFAIR_ENABLED - Set to 'true' to enable polling (default: false)
    BETFAIR_USERNAME - Betfair account username
    BETFAIR_PASSWORD - Betfair account password
    BETFAIR_APP_KEY - Betfair API application key
    BETFAIR_PEM_B64 - Optional: Base64-encoded PEM certificate for cert login
    BETFAIR_MARKET_IDS - Optional: Comma-separated list of market IDs to poll
    BETFAIR_POLL_INTERVAL - Polling interval in seconds (default: 2)
    BETFAIR_TLD - Betfair API TLD (default: com.au)
    PORT - Server port (default: 5001)
    DATABASE_URL - Database connection URL
"""

import os
import sys
import json
import time
import base64
import hashlib
import logging
import tempfile
import threading
from datetime import datetime
from typing import Optional, Dict, List, Any, Generator
from queue import Queue

from flask import Flask, Response, jsonify, request

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('betfair_service')

app = Flask(__name__)

# Configuration from environment
BETFAIR_ENABLED = os.environ.get('BETFAIR_ENABLED', 'false').lower() == 'true'
BETFAIR_USERNAME = os.environ.get('BETFAIR_USERNAME', '')
BETFAIR_PASSWORD = os.environ.get('BETFAIR_PASSWORD', '')
BETFAIR_APP_KEY = os.environ.get('BETFAIR_APP_KEY', '')
BETFAIR_PEM_B64 = os.environ.get('BETFAIR_PEM_B64', '')
BETFAIR_MARKET_IDS = os.environ.get('BETFAIR_MARKET_IDS', '')
BETFAIR_POLL_INTERVAL = int(os.environ.get('BETFAIR_POLL_INTERVAL', '2'))
BETFAIR_TLD = os.environ.get('BETFAIR_TLD', 'com.au')
PORT = int(os.environ.get('PORT', '5001'))
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///formanalyst.db')

# Fix for postgres:// vs postgresql://
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# Global state
_trading_client = None
_poller_thread = None
_subscribers: List[Queue] = []
_subscribers_lock = threading.Lock()
_last_payloads: Dict[str, str] = {}  # market_id -> hash of last payload for deduplication
_backoff_until: float = 0  # Timestamp until which we should back off


def get_pem_file_path() -> Optional[str]:
    """Decode base64 PEM and write to temp file if provided"""
    if not BETFAIR_PEM_B64:
        return None
    
    try:
        pem_data = base64.b64decode(BETFAIR_PEM_B64)
        # Create a temp file for the PEM
        fd, path = tempfile.mkstemp(prefix='betfair_cert_', suffix='.pem')
        with os.fdopen(fd, 'wb') as f:
            f.write(pem_data)
        logger.info(f"Decoded PEM certificate to temporary file")
        return path
    except Exception as e:
        logger.error(f"Failed to decode PEM certificate: {e}")
        return None


def create_trading_client():
    """Create and authenticate Betfair trading client"""
    global _trading_client
    
    try:
        import betfairlightweight
    except ImportError:
        logger.error("betfairlightweight not installed. Run: pip install -r requirements-betfair.txt")
        return None
    
    if not BETFAIR_USERNAME or not BETFAIR_PASSWORD or not BETFAIR_APP_KEY:
        logger.warning("Betfair credentials not configured")
        return None
    
    try:
        pem_path = get_pem_file_path()
        
        if pem_path:
            # Certificate-based login
            logger.info("Attempting certificate-based login...")
            _trading_client = betfairlightweight.APIClient(
                username=BETFAIR_USERNAME,
                password=BETFAIR_PASSWORD,
                app_key=BETFAIR_APP_KEY,
                certs=pem_path,
                locale=BETFAIR_TLD
            )
            _trading_client.login()
        else:
            # Username/password login (interactive)
            logger.info("Attempting username/password login...")
            _trading_client = betfairlightweight.APIClient(
                username=BETFAIR_USERNAME,
                password=BETFAIR_PASSWORD,
                app_key=BETFAIR_APP_KEY,
                locale=BETFAIR_TLD
            )
            _trading_client.login_interactive()
        
        logger.info("Successfully authenticated with Betfair")
        return _trading_client
        
    except Exception as e:
        logger.error(f"Betfair authentication failed: {e}")
        _trading_client = None
        return None


def get_trading_client():
    """Get or create trading client with re-auth on error"""
    global _trading_client
    
    if _trading_client is None:
        return create_trading_client()
    
    return _trading_client


def get_market_ids_from_db() -> List[str]:
    """Get market IDs from database (races with betfair_market_id set)"""
    try:
        from sqlalchemy import create_engine, text
        
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT DISTINCT betfair_market_id FROM races WHERE betfair_market_id IS NOT NULL"
            ))
            return [row[0] for row in result if row[0]]
    except Exception as e:
        logger.error(f"Failed to get market IDs from database: {e}")
        return []


def get_market_ids() -> List[str]:
    """Get market IDs to poll from env var or database"""
    if BETFAIR_MARKET_IDS:
        return [mid.strip() for mid in BETFAIR_MARKET_IDS.split(',') if mid.strip()]
    return get_market_ids_from_db()


def payload_hash(payload: dict) -> str:
    """Create hash of payload for deduplication using SHA-256"""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def broadcast_message(message: dict):
    """Broadcast message to all SSE subscribers"""
    data = json.dumps(message)
    with _subscribers_lock:
        dead_queues = []
        for q in _subscribers:
            try:
                q.put_nowait(data)
            except Exception:
                dead_queues.append(q)
        # Clean up dead queues
        for q in dead_queues:
            _subscribers.remove(q)


def update_horse_result(selection_id: int, market_id: str, position: int, odds: float):
    """Update horse record with final result"""
    try:
        from sqlalchemy import create_engine, text
        
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE horses 
                SET final_position = :position,
                    final_odds = :odds,
                    result_settled_at = :settled_at,
                    result_source = :result_source
                WHERE betfair_selection_id = :selection_id
            """), {
                'position': position,
                'odds': odds,
                'settled_at': datetime.utcnow(),
                'result_source': 'betfair',
                'selection_id': selection_id
            })
            conn.commit()
            logger.info(f"Updated result for selection {selection_id}: position={position}, odds={odds}")
    except Exception as e:
        logger.error(f"Failed to update horse result: {e}")


def process_market_book(market_book) -> Optional[dict]:
    """Process a market book response and return formatted data"""
    global _last_payloads
    
    market_id = market_book.market_id
    market_status = market_book.status
    
    runners_data = []
    for runner in market_book.runners:
        runner_data = {
            'selectionId': runner.selection_id,
            'status': runner.status,
            'lastPriceTraded': runner.last_price_traded,
        }
        
        # Get best back/lay prices
        if runner.ex and runner.ex.available_to_back:
            runner_data['bestBackPrice'] = runner.ex.available_to_back[0].price
            runner_data['bestBackSize'] = runner.ex.available_to_back[0].size
        
        if runner.ex and runner.ex.available_to_lay:
            runner_data['bestLayPrice'] = runner.ex.available_to_lay[0].price
            runner_data['bestLaySize'] = runner.ex.available_to_lay[0].size
        
        runners_data.append(runner_data)
    
    payload = {
        'type': 'market_update',
        'marketId': market_id,
        'status': market_status,
        'inplay': market_book.inplay,
        'totalMatched': market_book.total_matched,
        'runners': runners_data,
        'timestamp': datetime.utcnow().isoformat()
    }
    
    # Check for deduplication
    current_hash = payload_hash(payload)
    if _last_payloads.get(market_id) == current_hash:
        return None  # No change
    
    _last_payloads[market_id] = current_hash
    
    # If market is closed, compute final positions
    if market_status == 'CLOSED':
        payload['type'] = 'market_closed'
        
        # Sort runners by position (WINNER first, then PLACED, etc.)
        position_order = {'WINNER': 1, 'PLACED': 2, 'LOSER': 3, 'REMOVED': 99}
        sorted_runners = sorted(
            runners_data,
            key=lambda r: (position_order.get(r['status'], 50), -r.get('lastPriceTraded', 0))
        )
        
        # Assign positions and update database
        position = 1
        for runner in sorted_runners:
            if runner['status'] == 'REMOVED':
                runner['finalPosition'] = None
            else:
                runner['finalPosition'] = position
                # Update database
                update_horse_result(
                    selection_id=runner['selectionId'],
                    market_id=market_id,
                    position=position,
                    odds=runner.get('lastPriceTraded', 0)
                )
                position += 1
        
        payload['runners'] = sorted_runners
    
    return payload


def poll_markets():
    """Poll Betfair markets for updates"""
    global _backoff_until, _trading_client
    
    # Check backoff
    if time.time() < _backoff_until:
        return
    
    client = get_trading_client()
    if not client:
        return
    
    market_ids = get_market_ids()
    if not market_ids:
        return
    
    try:
        # Request market books with best offers
        price_projection = {
            'priceData': ['EX_BEST_OFFERS'],
            'virtualise': False
        }
        
        market_books = client.betting.list_market_book(
            market_ids=market_ids,
            price_projection=price_projection
        )
        
        for market_book in market_books:
            payload = process_market_book(market_book)
            if payload:
                broadcast_message(payload)
                logger.debug(f"Broadcast update for market {payload['marketId']}")
        
    except Exception as e:
        error_str = str(e).lower()
        
        # Handle rate limiting (429)
        if '429' in error_str or 'rate' in error_str:
            _backoff_until = time.time() + 30  # Back off for 30 seconds
            logger.warning(f"Rate limited, backing off for 30 seconds")
            return
        
        # Handle auth errors (401/403)
        if '401' in error_str or '403' in error_str or 'auth' in error_str:
            logger.warning("Authentication error, attempting re-auth...")
            _trading_client = None  # Force re-auth on next poll
            return
        
        logger.error(f"Error polling markets: {e}")


def poller_loop():
    """Main polling loop"""
    logger.info(f"Starting poller with {BETFAIR_POLL_INTERVAL}s interval")
    
    while True:
        try:
            poll_markets()
        except Exception as e:
            logger.error(f"Poller error: {e}")
        
        time.sleep(BETFAIR_POLL_INTERVAL)


def start_poller():
    """Start the background poller thread"""
    global _poller_thread
    
    if _poller_thread and _poller_thread.is_alive():
        return
    
    _poller_thread = threading.Thread(target=poller_loop, daemon=True)
    _poller_thread.start()
    logger.info("Poller thread started")


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'enabled': BETFAIR_ENABLED,
        'authenticated': _trading_client is not None,
        'market_ids': get_market_ids()[:5],  # First 5 for brevity
        'subscribers': len(_subscribers)
    })


@app.route('/stream')
def stream():
    """SSE endpoint for streaming market updates"""
    def generate() -> Generator[str, None, None]:
        q: Queue = Queue()
        
        with _subscribers_lock:
            _subscribers.append(q)
        
        try:
            # Send initial connection message
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': datetime.utcnow().isoformat()})}\n\n"
            
            while True:
                try:
                    data = q.get(timeout=30)
                    yield f"data: {data}\n\n"
                except Exception:
                    # Send heartbeat on timeout
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.utcnow().isoformat()})}\n\n"
        finally:
            with _subscribers_lock:
                if q in _subscribers:
                    _subscribers.remove(q)
    
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'  # Disable nginx buffering
        }
    )


@app.route('/markets')
def list_markets():
    """List available markets (for admin/debugging)"""
    client = get_trading_client()
    if not client:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        # Get today's horse racing markets
        from datetime import timedelta
        
        market_filter = {
            'eventTypeIds': ['7'],  # Horse Racing
            'marketStartTime': {
                'from': datetime.utcnow().isoformat(),
                'to': (datetime.utcnow() + timedelta(days=1)).isoformat()
            }
        }
        
        markets = client.betting.list_market_catalogue(
            filter=market_filter,
            market_projection=['EVENT', 'RUNNER_DESCRIPTION'],
            max_results=100
        )
        
        result = []
        for market in markets:
            result.append({
                'marketId': market.market_id,
                'marketName': market.market_name,
                'event': market.event.name if market.event else None,
                'runners': [
                    {'selectionId': r.selection_id, 'runnerName': r.runner_name}
                    for r in (market.runners or [])
                ]
            })
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/search_markets')
def search_markets():
    """Search for markets by venue/track and date"""
    client = get_trading_client()
    if not client:
        return jsonify({'error': 'Not authenticated'}), 401
    
    venue = request.args.get('venue', '')
    date_str = request.args.get('date', '')
    
    try:
        from datetime import timedelta
        
        # Parse date or use today
        if date_str:
            target_date = datetime.strptime(date_str, '%Y-%m-%d')
        else:
            target_date = datetime.utcnow()
        
        market_filter = {
            'eventTypeIds': ['7'],  # Horse Racing
            'marketStartTime': {
                'from': target_date.replace(hour=0, minute=0, second=0).isoformat(),
                'to': (target_date + timedelta(days=1)).isoformat()
            }
        }
        
        if venue:
            market_filter['venues'] = [venue]
        
        markets = client.betting.list_market_catalogue(
            filter=market_filter,
            market_projection=['EVENT', 'RUNNER_DESCRIPTION', 'MARKET_START_TIME'],
            max_results=200
        )
        
        result = []
        for market in markets:
            result.append({
                'marketId': market.market_id,
                'marketName': market.market_name,
                'startTime': market.market_start_time.isoformat() if market.market_start_time else None,
                'event': market.event.name if market.event else None,
                'venue': market.event.venue if market.event else None,
                'runners': [
                    {'selectionId': r.selection_id, 'runnerName': r.runner_name}
                    for r in (market.runners or [])
                ]
            })
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def should_start_poller() -> bool:
    """Check if poller should start based on configuration"""
    if not BETFAIR_ENABLED:
        logger.info("Betfair integration disabled (BETFAIR_ENABLED != true)")
        return False
    
    has_credentials = bool(BETFAIR_USERNAME and BETFAIR_PASSWORD and BETFAIR_APP_KEY)
    has_market_ids = bool(BETFAIR_MARKET_IDS)
    
    if not has_credentials and not has_market_ids:
        logger.warning("No Betfair credentials or market IDs configured")
        return False
    
    return True


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("Betfair Live Odds Service")
    logger.info("=" * 60)
    logger.info(f"BETFAIR_ENABLED: {BETFAIR_ENABLED}")
    logger.info(f"BETFAIR_TLD: {BETFAIR_TLD}")
    logger.info(f"BETFAIR_POLL_INTERVAL: {BETFAIR_POLL_INTERVAL}s")
    logger.info(f"PORT: {PORT}")
    
    if should_start_poller():
        # Attempt initial authentication
        if create_trading_client():
            start_poller()
        else:
            logger.warning("Failed to authenticate, poller not started")
    
    # Run Flask app
    app.run(host='0.0.0.0', port=PORT, threaded=True)
