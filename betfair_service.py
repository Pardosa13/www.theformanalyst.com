#!/usr/bin/env python3
"""
Betfair Live Odds Service

A Flask-based microservice that:
- Authenticates with Betfair API
- Polls Exchange listMarketBook for mapped markets
- Streams updates via SSE (Server-Sent Events)
- Persists final results when markets close

Environment Variables:
- BETFAIR_ENABLED: Enable/disable the service (default: false)
- BETFAIR_USERNAME: Betfair username
- BETFAIR_PASSWORD: Betfair password
- BETFAIR_APP_KEY: Betfair application key
- BETFAIR_PEM_B64: Base64-encoded PEM certificate (optional, for cert login)
- BETFAIR_MARKET_IDS: Comma-separated market IDs (optional)
- BETFAIR_POLL_INTERVAL: Polling interval in seconds (default: 2)
- BETFAIR_TLD: Betfair TLD for API endpoints (default: com)
- DATABASE_URL: Database connection string
- PORT: HTTP server port (default: 5001)
"""

import os
import sys
import json
import time
import base64
import logging
import tempfile
import threading
from datetime import datetime
from queue import Queue

import requests
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
BETFAIR_TLD = os.environ.get('BETFAIR_TLD', 'com')
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///formanalyst.db')
PORT = int(os.environ.get('PORT', '5001'))
BETFAIR_PAYLOAD_DIR = os.environ.get('BETFAIR_PAYLOAD_DIR', '')

# Fix PostgreSQL URL
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# API URLs
BETFAIR_CERT_LOGIN_URL = f"https://identitysso-cert.betfair.{BETFAIR_TLD}/api/certlogin"
BETFAIR_LOGIN_URL = f"https://identitysso.betfair.{BETFAIR_TLD}/api/login"
BETFAIR_BETTING_API = f"https://api.betfair.{BETFAIR_TLD}/exchange/betting/rest/v1.0/"
BETFAIR_KEEP_ALIVE_URL = f"https://identitysso.betfair.{BETFAIR_TLD}/api/keepAlive"

# Global state
session_token = None
session_lock = threading.Lock()
last_payloads = {}
sse_clients = []
sse_lock = threading.Lock()
polling_thread = None
stop_polling = threading.Event()


def get_db_connection():
    """Create database connection using SQLAlchemy."""
    from sqlalchemy import create_engine
    return create_engine(DATABASE_URL)


def decode_pem_to_file():
    """Decode base64 PEM to a temporary file."""
    if not BETFAIR_PEM_B64:
        return None
    
    try:
        pem_data = base64.b64decode(BETFAIR_PEM_B64)
        fd, path = tempfile.mkstemp(suffix='.pem')
        with os.fdopen(fd, 'wb') as f:
            f.write(pem_data)
        logger.info(f"Created temporary PEM file: {path}")
        return path
    except Exception as e:
        logger.error(f"Failed to decode PEM: {e}")
        return None


def authenticate_cert(pem_path):
    """Authenticate using certificate-based login."""
    global session_token
    
    payload = {
        'username': BETFAIR_USERNAME,
        'password': BETFAIR_PASSWORD
    }
    headers = {
        'X-Application': BETFAIR_APP_KEY,
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    try:
        resp = requests.post(
            BETFAIR_CERT_LOGIN_URL,
            data=payload,
            headers=headers,
            cert=pem_path,
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        
        if data.get('loginStatus') == 'SUCCESS':
            with session_lock:
                session_token = data.get('sessionToken')
            logger.info("Certificate authentication successful")
            return True
        else:
            logger.error(f"Certificate auth failed: {data.get('loginStatus')}")
            return False
    except Exception as e:
        logger.error(f"Certificate auth error: {e}")
        return False


def authenticate_password():
    """Authenticate using username/password login."""
    global session_token
    
    payload = {
        'username': BETFAIR_USERNAME,
        'password': BETFAIR_PASSWORD
    }
    headers = {
        'X-Application': BETFAIR_APP_KEY,
        'Accept': 'application/json',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    try:
        resp = requests.post(
            BETFAIR_LOGIN_URL,
            data=payload,
            headers=headers,
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        
        if data.get('status') == 'SUCCESS':
            with session_lock:
                session_token = data.get('token')
            logger.info("Password authentication successful")
            return True
        else:
            logger.error(f"Password auth failed: {data.get('status')}")
            return False
    except Exception as e:
        logger.error(f"Password auth error: {e}")
        return False


def authenticate():
    """Authenticate with Betfair using cert or password."""
    pem_path = decode_pem_to_file()
    
    if pem_path:
        success = authenticate_cert(pem_path)
        try:
            os.unlink(pem_path)
        except Exception:
            pass
        if success:
            return True
    
    return authenticate_password()


def keep_alive():
    """Keep the session alive."""
    global session_token
    
    with session_lock:
        token = session_token
    
    if not token:
        return False
    
    headers = {
        'X-Application': BETFAIR_APP_KEY,
        'X-Authentication': token,
        'Accept': 'application/json'
    }
    
    try:
        resp = requests.get(BETFAIR_KEEP_ALIVE_URL, headers=headers, timeout=10)
        data = resp.json()
        return data.get('status') == 'SUCCESS'
    except Exception as e:
        logger.error(f"Keep alive failed: {e}")
        return False


def get_market_ids_from_db():
    """Get mapped market IDs from the database."""
    try:
        from sqlalchemy import text
        engine = get_db_connection()
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT DISTINCT betfair_market_id FROM races "
                "WHERE betfair_market_id IS NOT NULL AND betfair_mapped = TRUE"
            ))
            return [row[0] for row in result if row[0]]
    except Exception as e:
        logger.error(f"Failed to get market IDs from DB: {e}")
        return []


def get_market_ids():
    """Get market IDs to poll from env var or database."""
    market_ids = []
    
    # From environment variable
    if BETFAIR_MARKET_IDS:
        market_ids.extend([mid.strip() for mid in BETFAIR_MARKET_IDS.split(',') if mid.strip()])
    
    # From database
    db_ids = get_market_ids_from_db()
    for mid in db_ids:
        if mid not in market_ids:
            market_ids.append(mid)
    
    return market_ids


def list_market_book(market_ids):
    """Call Betfair listMarketBook API."""
    global session_token
    
    with session_lock:
        token = session_token
    
    if not token:
        logger.warning("No session token, attempting to authenticate")
        if not authenticate():
            return None
        with session_lock:
            token = session_token
    
    headers = {
        'X-Application': BETFAIR_APP_KEY,
        'X-Authentication': token,
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
    
    payload = {
        'marketIds': market_ids,
        'priceProjection': {
            'priceData': ['EX_BEST_OFFERS']
        }
    }
    
    try:
        resp = requests.post(
            f"{BETFAIR_BETTING_API}listMarketBook/",
            json=payload,
            headers=headers,
            timeout=30
        )
        
        if resp.status_code == 401 or resp.status_code == 403:
            logger.warning(f"Auth error ({resp.status_code}), re-authenticating")
            if authenticate():
                return list_market_book(market_ids)
            return None
        
        if resp.status_code == 429:
            logger.warning("Rate limited, backing off")
            time.sleep(5)
            return None
        
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"listMarketBook error: {e}")
        return None


def is_payload_changed(market_id, new_payload):
    """Check if payload has changed since last poll."""
    global last_payloads
    
    new_hash = json.dumps(new_payload, sort_keys=True)
    old_hash = last_payloads.get(market_id)
    
    if new_hash != old_hash:
        last_payloads[market_id] = new_hash
        return True
    return False


def save_raw_payload(market_id, payload):
    """Optionally save raw market payload to disk."""
    if not BETFAIR_PAYLOAD_DIR:
        return
    
    try:
        os.makedirs(BETFAIR_PAYLOAD_DIR, exist_ok=True)
        filename = f"{market_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(BETFAIR_PAYLOAD_DIR, filename)
        with open(filepath, 'w') as f:
            json.dump(payload, f)
    except Exception as e:
        logger.error(f"Failed to save payload: {e}")


def persist_results(market_id, runners):
    """Persist final results to database."""
    try:
        from sqlalchemy import text
        engine = get_db_connection()
        
        with engine.connect() as conn:
            for runner in runners:
                selection_id = runner.get('selectionId')
                status = runner.get('status')
                
                # Determine final position from status
                final_position = None
                if status == 'WINNER':
                    final_position = 1
                elif status == 'PLACED':
                    # Could be 2nd, 3rd, etc. - Betfair doesn't give exact position for placed
                    final_position = 2  # Approximate
                elif status == 'LOSER':
                    final_position = None  # Unknown position for losers
                
                # Get last traded price
                final_odds = None
                last_price_traded = runner.get('lastPriceTraded')
                if last_price_traded:
                    final_odds = last_price_traded
                
                # Update horse record
                if selection_id:
                    conn.execute(text("""
                        UPDATE horses 
                        SET final_position = :position,
                            final_odds = :odds,
                            result_settled_at = :settled_at,
                            result_source = 'betfair'
                        WHERE betfair_selection_id = :selection_id
                    """), {
                        'position': final_position,
                        'odds': final_odds,
                        'settled_at': datetime.utcnow(),
                        'selection_id': selection_id
                    })
            
            conn.commit()
            logger.info(f"Persisted results for market {market_id}")
    except Exception as e:
        logger.error(f"Failed to persist results: {e}")


def broadcast_sse(event_type, data):
    """Broadcast message to all SSE clients."""
    message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    
    with sse_lock:
        dead_clients = []
        for i, queue in enumerate(sse_clients):
            try:
                queue.put(message)
            except Exception:
                dead_clients.append(i)
        
        # Clean up dead clients
        for i in reversed(dead_clients):
            sse_clients.pop(i)


def poll_markets():
    """Main polling loop for market updates."""
    logger.info("Starting market polling loop")
    
    while not stop_polling.is_set():
        try:
            market_ids = get_market_ids()
            
            if not market_ids:
                logger.debug("No market IDs to poll")
                time.sleep(BETFAIR_POLL_INTERVAL * 5)
                continue
            
            # Poll in batches of 10 (Betfair limit)
            for i in range(0, len(market_ids), 10):
                batch = market_ids[i:i+10]
                markets = list_market_book(batch)
                
                if markets:
                    for market in markets:
                        market_id = market.get('marketId')
                        status = market.get('status')
                        runners = market.get('runners', [])
                        
                        # Check if payload changed
                        if not is_payload_changed(market_id, market):
                            continue
                        
                        # Save raw payload if configured
                        save_raw_payload(market_id, market)
                        
                        # Prepare update message
                        update = {
                            'marketId': market_id,
                            'status': status,
                            'runners': []
                        }
                        
                        for runner in runners:
                            runner_update = {
                                'selectionId': runner.get('selectionId'),
                                'status': runner.get('status'),
                                'lastPriceTraded': runner.get('lastPriceTraded'),
                                'totalMatched': runner.get('totalMatched')
                            }
                            
                            # Include best available prices
                            ex = runner.get('ex', {})
                            back = ex.get('availableToBack', [])
                            lay = ex.get('availableToLay', [])
                            
                            if back:
                                runner_update['backPrice'] = back[0].get('price')
                                runner_update['backSize'] = back[0].get('size')
                            if lay:
                                runner_update['layPrice'] = lay[0].get('price')
                                runner_update['laySize'] = lay[0].get('size')
                            
                            update['runners'].append(runner_update)
                        
                        # Broadcast update
                        broadcast_sse('market_update', update)
                        logger.debug(f"Broadcasted update for market {market_id}")
                        
                        # Handle closed markets
                        if status == 'CLOSED':
                            persist_results(market_id, runners)
                            broadcast_sse('market_closed', {
                                'marketId': market_id,
                                'runners': update['runners']
                            })
                            logger.info(f"Market {market_id} closed, results persisted")
            
            # Keep session alive periodically
            if not keep_alive():
                logger.warning("Keep alive failed, re-authenticating")
                authenticate()
            
        except Exception as e:
            logger.error(f"Polling error: {e}")
        
        time.sleep(BETFAIR_POLL_INTERVAL)
    
    logger.info("Polling loop stopped")


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'ok',
        'enabled': BETFAIR_ENABLED,
        'authenticated': session_token is not None,
        'polling': polling_thread is not None and polling_thread.is_alive()
    })


@app.route('/stream')
def stream():
    """SSE endpoint for live market updates."""
    if not BETFAIR_ENABLED:
        return jsonify({'error': 'Betfair integration is disabled'}), 503
    
    def generate():
        queue = Queue()
        
        with sse_lock:
            sse_clients.append(queue)
        
        try:
            # Send initial connection message
            yield f"event: connected\ndata: {json.dumps({'status': 'connected'})}\n\n"
            
            while True:
                try:
                    message = queue.get(timeout=30)
                    yield message
                except Exception:
                    # Send heartbeat
                    yield f"event: heartbeat\ndata: {json.dumps({'time': datetime.utcnow().isoformat()})}\n\n"
        except GeneratorExit:
            pass
        finally:
            with sse_lock:
                if queue in sse_clients:
                    sse_clients.remove(queue)
    
    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    return response


@app.route('/markets')
def markets():
    """Get current market IDs being polled."""
    return jsonify({
        'market_ids': get_market_ids()
    })


@app.route('/status')
def status():
    """Get detailed service status."""
    return jsonify({
        'enabled': BETFAIR_ENABLED,
        'authenticated': session_token is not None,
        'polling': polling_thread is not None and polling_thread.is_alive(),
        'market_count': len(get_market_ids()),
        'client_count': len(sse_clients),
        'poll_interval': BETFAIR_POLL_INTERVAL
    })


def start_polling():
    """Start the polling thread."""
    global polling_thread
    
    if polling_thread and polling_thread.is_alive():
        return
    
    stop_polling.clear()
    polling_thread = threading.Thread(target=poll_markets, daemon=True)
    polling_thread.start()
    logger.info("Polling thread started")


def stop_polling_thread():
    """Stop the polling thread."""
    stop_polling.set()
    if polling_thread:
        polling_thread.join(timeout=5)
    logger.info("Polling thread stopped")


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Betfair Live Odds Service")
    logger.info("=" * 60)
    
    if not BETFAIR_ENABLED:
        logger.warning("BETFAIR_ENABLED is false. Service will start but not poll.")
        logger.info("Set BETFAIR_ENABLED=true and configure credentials to enable.")
    
    # Validate credentials
    has_credentials = bool(BETFAIR_USERNAME and BETFAIR_PASSWORD and BETFAIR_APP_KEY)
    has_markets = bool(BETFAIR_MARKET_IDS or get_market_ids_from_db())
    
    if BETFAIR_ENABLED:
        if not has_credentials:
            logger.error("Missing credentials. Required: BETFAIR_USERNAME, BETFAIR_PASSWORD, BETFAIR_APP_KEY")
            logger.info("Service will start but polling will be disabled until credentials are set.")
        else:
            # Authenticate
            if authenticate():
                logger.info("Initial authentication successful")
                if has_markets:
                    start_polling()
                else:
                    logger.info("No market IDs configured. Polling will start when markets are mapped.")
            else:
                logger.error("Initial authentication failed. Check credentials.")
    
    logger.info(f"Starting HTTP server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, threaded=True)


if __name__ == '__main__':
    main()
