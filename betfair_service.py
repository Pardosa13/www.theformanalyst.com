"""
Betfair Live Odds Service

A Flask-based microservice that connects to the Betfair Exchange API to stream
live odds and race results via Server-Sent Events (SSE).

This service requires BETFAIR_ENABLED=true to run and will not start without
proper Betfair API credentials configured.

Environment Variables:
- BETFAIR_ENABLED: Set to 'true' to enable the service (default: false)
- BETFAIR_USERNAME: Betfair account username
- BETFAIR_PASSWORD: Betfair account password
- BETFAIR_APP_KEY: Betfair API application key
- BETFAIR_PEM_B64: Base64-encoded PEM certificate content (preferred for deployment)
- BETFAIR_PEM: Path to PEM certificate file (alternative)
- BETFAIR_CERT_DIR: Directory containing cert files (alternative)
- BETFAIR_MARKET_IDS: Comma-separated list of market IDs to monitor
- BETFAIR_POLL_INTERVAL: Polling interval in seconds (default: 2)
- BETFAIR_TLD: Top-level domain for Betfair (default: .com.au, or .com for UK)
- PORT: HTTP port for the service (default: 5001)
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
from typing import Optional, Dict, Any, List

import requests
from flask import Flask, Response, jsonify

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration from environment variables
BETFAIR_ENABLED = os.environ.get('BETFAIR_ENABLED', 'false').lower() == 'true'
BETFAIR_USERNAME = os.environ.get('BETFAIR_USERNAME', '')
BETFAIR_PASSWORD = os.environ.get('BETFAIR_PASSWORD', '')
BETFAIR_APP_KEY = os.environ.get('BETFAIR_APP_KEY', '')
BETFAIR_PEM_B64 = os.environ.get('BETFAIR_PEM_B64', '')
BETFAIR_PEM = os.environ.get('BETFAIR_PEM', '')
BETFAIR_CERT_DIR = os.environ.get('BETFAIR_CERT_DIR', '')
BETFAIR_MARKET_IDS = os.environ.get('BETFAIR_MARKET_IDS', '')
BETFAIR_POLL_INTERVAL = float(os.environ.get('BETFAIR_POLL_INTERVAL', '2'))
BETFAIR_TLD = os.environ.get('BETFAIR_TLD', '.com.au')
PORT = int(os.environ.get('PORT', '5001'))

# Betfair API endpoints
CERT_LOGIN_URL = f"https://identitysso-cert.betfair{BETFAIR_TLD}/api/certlogin"
EXCHANGE_API_URL = f"https://api.betfair{BETFAIR_TLD}/exchange/betting/rest/v1.0/"

# Global state
session_token: Optional[str] = None
cert_file_path: Optional[str] = None
last_market_data: Dict[str, Any] = {}
backoff_time: float = BETFAIR_POLL_INTERVAL
MAX_BACKOFF: float = 60.0


def setup_certificate() -> Optional[str]:
    """
    Set up the certificate file for Betfair cert-login.
    Returns the path to the certificate file.
    """
    global cert_file_path
    
    if BETFAIR_PEM_B64:
        # Decode base64 PEM and write to temp file
        try:
            pem_content = base64.b64decode(BETFAIR_PEM_B64)
            # Create a secure temp file
            fd, cert_file_path = tempfile.mkstemp(suffix='.pem', prefix='betfair_')
            with os.fdopen(fd, 'wb') as f:
                f.write(pem_content)
            os.chmod(cert_file_path, 0o600)
            logger.info(f"Certificate decoded from BETFAIR_PEM_B64 and written to {cert_file_path}")
            return cert_file_path
        except Exception as e:
            logger.error(f"Failed to decode BETFAIR_PEM_B64: {e}")
            return None
    
    elif BETFAIR_PEM:
        # Use the provided PEM file path
        if os.path.exists(BETFAIR_PEM):
            cert_file_path = BETFAIR_PEM
            logger.info(f"Using certificate from BETFAIR_PEM: {cert_file_path}")
            return cert_file_path
        else:
            logger.error(f"Certificate file not found: {BETFAIR_PEM}")
            return None
    
    elif BETFAIR_CERT_DIR:
        # Look for cert files in the specified directory
        cert_path = os.path.join(BETFAIR_CERT_DIR, 'client-2048.pem')
        if os.path.exists(cert_path):
            cert_file_path = cert_path
            logger.info(f"Using certificate from BETFAIR_CERT_DIR: {cert_file_path}")
            return cert_file_path
        else:
            logger.error(f"Certificate not found in BETFAIR_CERT_DIR: {BETFAIR_CERT_DIR}")
            return None
    
    logger.error("No certificate configuration found. Set BETFAIR_PEM_B64, BETFAIR_PEM, or BETFAIR_CERT_DIR")
    return None


def authenticate() -> bool:
    """
    Authenticate with Betfair using certificate-based login.
    Returns True on success, False on failure.
    """
    global session_token
    
    if not cert_file_path:
        logger.error("No certificate file configured")
        return False
    
    if not all([BETFAIR_USERNAME, BETFAIR_PASSWORD, BETFAIR_APP_KEY]):
        logger.error("Missing Betfair credentials (username, password, or app key)")
        return False
    
    try:
        response = requests.post(
            CERT_LOGIN_URL,
            data={'username': BETFAIR_USERNAME, 'password': BETFAIR_PASSWORD},
            headers={'X-Application': BETFAIR_APP_KEY},
            cert=cert_file_path,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('loginStatus') == 'SUCCESS':
                session_token = data.get('sessionToken')
                logger.info("Successfully authenticated with Betfair")
                return True
            else:
                logger.error(f"Login failed: {data.get('loginStatus')}")
                return False
        else:
            logger.error(f"Authentication request failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        return False


def get_market_book(market_ids: List[str]) -> Optional[Dict[str, Any]]:
    """
    Fetch market book data from Betfair Exchange API.
    Uses minimal priceProjection (EX_BEST_OFFERS) for efficiency.
    """
    global session_token, backoff_time
    
    if not session_token:
        logger.warning("No session token, attempting re-authentication")
        if not authenticate():
            return None
    
    url = f"{EXCHANGE_API_URL}listMarketBook/"
    
    headers = {
        'X-Application': BETFAIR_APP_KEY,
        'X-Authentication': session_token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    params = {
        'marketIds': market_ids,
        'priceProjection': {
            'priceData': ['EX_BEST_OFFERS'],
            'virtualise': False
        }
    }
    
    try:
        response = requests.post(
            url,
            json=params,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            # Reset backoff on success
            backoff_time = BETFAIR_POLL_INTERVAL
            return response.json()
        
        elif response.status_code in [401, 403]:
            logger.warning("Session expired, re-authenticating...")
            session_token = None
            if authenticate():
                return get_market_book(market_ids)
            return None
        
        elif response.status_code == 429:
            # Rate limited - implement exponential backoff
            backoff_time = min(backoff_time * 2, MAX_BACKOFF)
            logger.warning(f"Rate limited (429). Backing off for {backoff_time}s")
            return None
        
        else:
            # Check for TOO_MUCH_DATA error
            try:
                error_data = response.json()
                if error_data.get('data', {}).get('APINGException', {}).get('errorCode') == 'TOO_MUCH_DATA':
                    backoff_time = min(backoff_time * 2, MAX_BACKOFF)
                    logger.warning(f"TOO_MUCH_DATA error. Backing off for {backoff_time}s")
                    return None
            except (json.JSONDecodeError, KeyError):
                pass
            
            logger.error(f"API request failed: {response.status_code} - {response.text}")
            return None
            
    except requests.Timeout:
        logger.error("API request timed out")
        return None
    except Exception as e:
        logger.error(f"API request error: {e}")
        return None


def compute_final_positions(market_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute final positions for all runners when a market is closed.
    Returns a result object with runner positions and settled prices.
    """
    result = {
        'market_id': market_data.get('marketId'),
        'status': 'CLOSED',
        'settled_at': datetime.utcnow().isoformat(),
        'runners': []
    }
    
    runners = market_data.get('runners', [])
    
    # Sort runners by final status - winners first
    for runner in runners:
        selection_id = runner.get('selectionId')
        status = runner.get('status')
        
        runner_result = {
            'selection_id': selection_id,
            'status': status,
            'final_position': None,
            'final_odds': None
        }
        
        # Get last traded price as final odds
        if runner.get('lastPriceTraded'):
            runner_result['final_odds'] = runner.get('lastPriceTraded')
        
        # Determine position based on status
        if status == 'WINNER':
            runner_result['final_position'] = 1
        elif status == 'PLACED':
            # For placed runners, we'd need additional data for exact position
            runner_result['final_position'] = None  # Will be determined from order
        elif status == 'LOSER':
            runner_result['final_position'] = None
        
        result['runners'].append(runner_result)
    
    # Try to determine positions for placed runners based on order
    winners = [r for r in result['runners'] if r['status'] == 'WINNER']
    placed = [r for r in result['runners'] if r['status'] == 'PLACED']
    
    if winners:
        for i, w in enumerate(winners):
            w['final_position'] = i + 1
    
    # Placed runners get positions after winners
    if placed:
        start_pos = len(winners) + 1
        for i, p in enumerate(placed):
            p['final_position'] = start_pos + i
    
    return result


def format_sse_message(data: Dict[str, Any]) -> str:
    """Format data as an SSE message."""
    return f"data: {json.dumps(data)}\n\n"


def market_data_changed(market_id: str, new_data: Dict[str, Any]) -> bool:
    """Check if market data has changed since last update."""
    global last_market_data
    
    old_data = last_market_data.get(market_id)
    
    if old_data is None:
        return True
    
    # Compare relevant fields
    def extract_key_data(data):
        return {
            'status': data.get('status'),
            'runners': [
                {
                    'selectionId': r.get('selectionId'),
                    'status': r.get('status'),
                    'lastPriceTraded': r.get('lastPriceTraded'),
                    'ex': r.get('ex')
                }
                for r in data.get('runners', [])
            ]
        }
    
    return extract_key_data(old_data) != extract_key_data(new_data)


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'ok',
        'enabled': BETFAIR_ENABLED,
        'authenticated': session_token is not None,
        'timestamp': datetime.utcnow().isoformat()
    })


@app.route('/stream')
def stream():
    """
    SSE endpoint that streams market updates and final results.
    Clients connect here to receive real-time odds updates.
    """
    if not BETFAIR_ENABLED:
        return jsonify({'error': 'Betfair integration is not enabled'}), 503
    
    if not BETFAIR_MARKET_IDS:
        return jsonify({'error': 'No market IDs configured'}), 400
    
    market_ids = [m.strip() for m in BETFAIR_MARKET_IDS.split(',') if m.strip()]
    
    if not market_ids:
        return jsonify({'error': 'No valid market IDs configured'}), 400
    
    def generate():
        global last_market_data, backoff_time
        
        # Ensure we're authenticated
        if not session_token and not authenticate():
            yield format_sse_message({
                'type': 'error',
                'message': 'Authentication failed',
                'timestamp': datetime.utcnow().isoformat()
            })
            return
        
        # Send initial connection message
        yield format_sse_message({
            'type': 'connected',
            'market_ids': market_ids,
            'timestamp': datetime.utcnow().isoformat()
        })
        
        while True:
            try:
                # Fetch market data
                data = get_market_book(market_ids)
                
                if data:
                    for market in data:
                        market_id = market.get('marketId')
                        market_status = market.get('status')
                        
                        # Check if data has changed
                        if market_data_changed(market_id, market):
                            # Update cached data
                            last_market_data[market_id] = market
                            
                            # Prepare the message
                            message = {
                                'type': 'market_update',
                                'market_id': market_id,
                                'status': market_status,
                                'timestamp': datetime.utcnow().isoformat(),
                                'runners': []
                            }
                            
                            # Add runner data
                            for runner in market.get('runners', []):
                                runner_data = {
                                    'selection_id': runner.get('selectionId'),
                                    'status': runner.get('status'),
                                    'last_price_traded': runner.get('lastPriceTraded')
                                }
                                
                                # Add best available odds
                                ex = runner.get('ex', {})
                                if ex.get('availableToBack'):
                                    runner_data['best_back'] = ex['availableToBack'][0] if ex['availableToBack'] else None
                                if ex.get('availableToLay'):
                                    runner_data['best_lay'] = ex['availableToLay'][0] if ex['availableToLay'] else None
                                
                                message['runners'].append(runner_data)
                            
                            yield format_sse_message(message)
                            
                            # If market is closed, send final results
                            if market_status == 'CLOSED':
                                result = compute_final_positions(market)
                                yield format_sse_message({
                                    'type': 'result',
                                    'market_id': market_id,
                                    'result': result,
                                    'timestamp': datetime.utcnow().isoformat()
                                })
                
                # Sleep with backoff
                time.sleep(backoff_time)
                
            except GeneratorExit:
                logger.info("Client disconnected from SSE stream")
                break
            except Exception as e:
                logger.error(f"Error in SSE stream: {e}")
                yield format_sse_message({
                    'type': 'error',
                    'message': str(e),
                    'timestamp': datetime.utcnow().isoformat()
                })
                time.sleep(backoff_time)
    
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@app.route('/')
def index():
    """Root endpoint with service information."""
    return jsonify({
        'service': 'Betfair Live Odds Service',
        'version': '1.0.0',
        'enabled': BETFAIR_ENABLED,
        'endpoints': {
            '/health': 'Health check',
            '/stream': 'SSE stream for market updates'
        },
        'documentation': 'See BETFAIR_README.md for setup instructions'
    })


def cleanup():
    """Clean up temporary files on exit."""
    global cert_file_path
    
    # Only remove cert file if it was created from BETFAIR_PEM_B64
    if cert_file_path and BETFAIR_PEM_B64 and os.path.exists(cert_file_path):
        try:
            os.remove(cert_file_path)
            logger.info(f"Cleaned up temporary certificate file: {cert_file_path}")
        except Exception as e:
            logger.error(f"Failed to clean up certificate file: {e}")


if __name__ == '__main__':
    import atexit
    atexit.register(cleanup)
    
    if not BETFAIR_ENABLED:
        logger.warning("Betfair integration is disabled. Set BETFAIR_ENABLED=true to enable.")
        logger.info("Starting service in disabled mode (health endpoint only)...")
    else:
        # Validate required configuration
        if not BETFAIR_MARKET_IDS:
            logger.warning("BETFAIR_MARKET_IDS is not set. Service will start but /stream will return an error.")
        
        # Set up certificate
        if not setup_certificate():
            logger.error("Failed to set up certificate. Authentication will fail.")
        else:
            # Attempt initial authentication
            if authenticate():
                logger.info("Initial authentication successful")
            else:
                logger.warning("Initial authentication failed. Will retry on first request.")
    
    logger.info(f"Starting Betfair Live Odds Service on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, threaded=True)
