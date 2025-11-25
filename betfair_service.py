"""
Betfair Live Odds Service
========================

A Flask-based microservice that polls Betfair Exchange API for live odds
and streams updates to the frontend via Server-Sent Events (SSE).

Configuration via environment variables:
- BETFAIR_USERNAME: Betfair account username
- BETFAIR_PASSWORD: Betfair account password
- BETFAIR_APP_KEY: Betfair application key
- BETFAIR_PEM: Path to combined PEM file (cert + key)
- BETFAIR_CERT_DIR: Directory containing client-2048.crt and client-2048.key
- BETFAIR_MARKET_IDS: Comma-separated list of market IDs to monitor
- BETFAIR_POLL_INTERVAL: Polling interval in seconds (default: 5)
- BETFAIR_TLD: API TLD - 'com' for international, 'au' for Australia
- SERVICE_HOST: Host to bind to (default: 127.0.0.1)
- SERVICE_PORT: Port to bind to (default: 5001)

Usage:
    python betfair_service.py

SSE Endpoint:
    GET /stream - Returns Server-Sent Events with odds updates
"""

import json
import os
import threading
import time
from datetime import datetime
from urllib.parse import urlencode

import requests
from flask import Flask, Response, jsonify

# Configuration from environment
BETFAIR_USERNAME = os.environ.get('BETFAIR_USERNAME', '')
BETFAIR_PASSWORD = os.environ.get('BETFAIR_PASSWORD', '')
BETFAIR_APP_KEY = os.environ.get('BETFAIR_APP_KEY', '')
BETFAIR_PEM = os.environ.get('BETFAIR_PEM', '')
BETFAIR_CERT_DIR = os.environ.get('BETFAIR_CERT_DIR', '')
BETFAIR_MARKET_IDS = os.environ.get('BETFAIR_MARKET_IDS', '')
BETFAIR_POLL_INTERVAL = int(os.environ.get('BETFAIR_POLL_INTERVAL', '5'))
BETFAIR_TLD = os.environ.get('BETFAIR_TLD', 'com')
SERVICE_HOST = os.environ.get('SERVICE_HOST', '127.0.0.1')
SERVICE_PORT = int(os.environ.get('SERVICE_PORT', '5001'))

# CORS configuration - restrict to specific origins in production
# Use comma-separated list of allowed origins or '*' for development only
CORS_ALLOWED_ORIGINS = os.environ.get('CORS_ALLOWED_ORIGINS', '')

# Betfair API endpoints
IDENTITY_SSO_URL = f'https://identitysso-cert.betfair.{BETFAIR_TLD}/api/certlogin'
BETTING_API_URL = f'https://api.betfair.{BETFAIR_TLD}/exchange/betting/rest/v1.0/'

app = Flask(__name__)

# Global state
session_token = None
session_lock = threading.Lock()
latest_data = {}
latest_data_lock = threading.Lock()
last_payload_hash = None


def get_cert_config():
    """Get certificate configuration for requests."""
    if BETFAIR_PEM and os.path.exists(BETFAIR_PEM):
        return BETFAIR_PEM
    elif BETFAIR_CERT_DIR:
        cert_path = os.path.join(BETFAIR_CERT_DIR, 'client-2048.crt')
        key_path = os.path.join(BETFAIR_CERT_DIR, 'client-2048.key')
        if os.path.exists(cert_path) and os.path.exists(key_path):
            return (cert_path, key_path)
    return None


def authenticate():
    """Authenticate with Betfair using certificate login."""
    global session_token
    
    cert_config = get_cert_config()
    if not cert_config:
        print('[Betfair] ERROR: No certificate configured')
        return False
    
    if not BETFAIR_USERNAME or not BETFAIR_PASSWORD:
        print('[Betfair] ERROR: Username or password not configured')
        return False
    
    try:
        # Use urlencode for proper encoding of special characters in credentials
        payload = urlencode({
            'username': BETFAIR_USERNAME,
            'password': BETFAIR_PASSWORD
        })
        headers = {
            'X-Application': BETFAIR_APP_KEY,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        response = requests.post(
            IDENTITY_SSO_URL,
            data=payload,
            cert=cert_config,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            resp_json = response.json()
            if resp_json.get('loginStatus') == 'SUCCESS':
                with session_lock:
                    session_token = resp_json.get('sessionToken')
                print(f'[Betfair] Authenticated successfully at {datetime.now().isoformat()}')
                return True
            else:
                print(f'[Betfair] Login failed: {resp_json.get("loginStatus")}')
        else:
            print(f'[Betfair] Auth request failed with status {response.status_code}')
    except requests.RequestException as e:
        print(f'[Betfair] Authentication error: {e}')
    
    return False


def call_api(operation, params=None):
    """Call Betfair Exchange API."""
    global session_token
    
    with session_lock:
        token = session_token
    
    if not token:
        if not authenticate():
            return None
        with session_lock:
            token = session_token
    
    headers = {
        'X-Application': BETFAIR_APP_KEY,
        'X-Authentication': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    url = BETTING_API_URL + operation + '/'
    
    try:
        response = requests.post(
            url,
            json=params or {},
            headers=headers,
            timeout=30
        )
        
        if response.status_code in (401, 403):
            print('[Betfair] Session expired, re-authenticating...')
            with session_lock:
                session_token = None
            if authenticate():
                return call_api(operation, params)
            return None
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f'[Betfair] API error: {response.status_code} - {response.text}')
    except requests.RequestException as e:
        print(f'[Betfair] API request error: {e}')
    
    return None


def fetch_market_book(market_ids):
    """Fetch market book data with minimal projection."""
    if not market_ids:
        return None
    
    params = {
        'marketIds': market_ids,
        'priceProjection': {
            'priceData': ['EX_BEST_OFFERS'],
            'virtualise': False
        }
    }
    
    return call_api('listMarketBook', params)


def parse_market_data(market_book):
    """Parse market book data into a simplified format for the frontend."""
    if not market_book:
        return {}
    
    result = {}
    
    for market in market_book:
        market_id = market.get('marketId')
        status = market.get('status', 'UNKNOWN')
        
        runners = []
        for runner in market.get('runners', []):
            selection_id = runner.get('selectionId')
            runner_status = runner.get('status', 'ACTIVE')
            
            # Extract best back/lay prices
            ex = runner.get('ex', {})
            back_prices = ex.get('availableToBack', [])
            lay_prices = ex.get('availableToLay', [])
            
            best_back = back_prices[0] if back_prices else {}
            best_lay = lay_prices[0] if lay_prices else {}
            
            runner_data = {
                'selectionId': selection_id,
                'status': runner_status,
                'backPrice': best_back.get('price'),
                'backSize': best_back.get('size'),
                'layPrice': best_lay.get('price'),
                'laySize': best_lay.get('size')
            }
            
            # Include result info if race is complete
            if runner_status == 'WINNER':
                runner_data['result'] = 'WON'
            elif runner_status == 'LOSER':
                runner_data['result'] = 'LOST'
            elif runner_status == 'PLACED':
                runner_data['result'] = 'PLACED'
            
            runners.append(runner_data)
        
        result[market_id] = {
            'marketId': market_id,
            'status': status,
            'inplay': market.get('inplay', False),
            'runners': runners,
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }
    
    return result


def poller_thread():
    """Background thread that polls Betfair for market data."""
    global latest_data, last_payload_hash
    
    market_ids = [mid.strip() for mid in BETFAIR_MARKET_IDS.split(',') if mid.strip()]
    
    if not market_ids:
        print('[Betfair] No market IDs configured, poller idle')
        return
    
    print(f'[Betfair] Starting poller for markets: {market_ids}')
    
    while True:
        try:
            market_book = fetch_market_book(market_ids)
            
            if market_book:
                parsed = parse_market_data(market_book)
                payload_str = json.dumps(parsed, sort_keys=True)
                payload_hash = hash(payload_str)
                
                # Only update if data changed (de-duplication)
                if payload_hash != last_payload_hash:
                    with latest_data_lock:
                        latest_data = parsed
                    last_payload_hash = payload_hash
                    print(f'[Betfair] Data updated at {datetime.now().isoformat()}')
        except Exception as e:
            print(f'[Betfair] Poller error: {e}')
        
        time.sleep(BETFAIR_POLL_INTERVAL)


def generate_sse():
    """Generator for Server-Sent Events stream."""
    last_sent_hash = None
    
    while True:
        with latest_data_lock:
            data = latest_data.copy()
        
        if data:
            data_str = json.dumps(data)
            data_hash = hash(data_str)
            
            # Only send if data changed
            if data_hash != last_sent_hash:
                yield f'data: {data_str}\n\n'
                last_sent_hash = data_hash
        
        time.sleep(1)  # Check for updates every second


def get_cors_headers():
    """Get CORS headers based on configuration."""
    if CORS_ALLOWED_ORIGINS:
        # Use configured origins (can be comma-separated list or single origin)
        return {'Access-Control-Allow-Origin': CORS_ALLOWED_ORIGINS.split(',')[0].strip()}
    # No CORS header if not configured - same-origin only
    return {}


@app.route('/stream')
def stream():
    """SSE endpoint for live odds updates."""
    headers = {
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'X-Accel-Buffering': 'no'  # Disable nginx buffering
    }
    headers.update(get_cors_headers())
    
    return Response(
        generate_sse(),
        mimetype='text/event-stream',
        headers=headers
    )


@app.route('/health')
def health():
    """Health check endpoint."""
    with session_lock:
        authenticated = session_token is not None
    
    return jsonify({
        'status': 'ok',
        'authenticated': authenticated,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })


@app.route('/markets')
def markets():
    """Return current market data snapshot."""
    with latest_data_lock:
        data = latest_data.copy()
    
    return jsonify(data)


def main():
    """Main entry point."""
    # Validate configuration
    if not BETFAIR_APP_KEY:
        print('[Betfair] WARNING: BETFAIR_APP_KEY not set')
    
    if not get_cert_config():
        print('[Betfair] WARNING: No certificate configured (set BETFAIR_PEM or BETFAIR_CERT_DIR)')
    
    if not BETFAIR_MARKET_IDS:
        print('[Betfair] WARNING: BETFAIR_MARKET_IDS not set, service will not poll')
    
    # Start background poller thread
    if BETFAIR_MARKET_IDS:
        poller = threading.Thread(target=poller_thread, daemon=True)
        poller.start()
    
    # Run Flask app
    print(f'[Betfair] Starting SSE service on {SERVICE_HOST}:{SERVICE_PORT}')
    app.run(host=SERVICE_HOST, port=SERVICE_PORT, threaded=True)


if __name__ == '__main__':
    main()
