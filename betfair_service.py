#!/usr/bin/env python3
"""
Minimal Betfair poller + SSE microservice.

See BETFAIR_README.md for configuration and usage.
"""
import os
import time
import json
import base64
import tempfile
import threading
import queue
import logging
from datetime import datetime
import requests
from flask import Flask, Response, stream_with_context, request

from sqlalchemy import create_engine, MetaData, Table, select

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("betfair_service")

BETFAIR_USERNAME = os.getenv("BETFAIR_USERNAME")
BETFAIR_PASSWORD = os.getenv("BETFAIR_PASSWORD")
BETFAIR_APP_KEY = os.getenv("BETFAIR_APP_KEY")
BETFAIR_PEM_B64 = os.getenv("BETFAIR_PEM_B64")
BETFAIR_MARKET_IDS = os.getenv("BETFAIR_MARKET_IDS", "")
BETFAIR_POLL_INTERVAL = float(os.getenv("BETFAIR_POLL_INTERVAL", "2"))
BETFAIR_TLD = os.getenv("BETFAIR_TLD", ".com")
BETFAIR_ENABLED = os.getenv("BETFAIR_ENABLED", "false").lower() in ("1", "true", "yes")
PORT = int(os.getenv("PORT", "5005"))
SQLALCHEMY_DATABASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI")

IDENTITY_CERT_URL = f"https://identitysso-cert.betfair{BETFAIR_TLD}/api/certlogin"
IDENTITY_LOGIN_URL = f"https://identitysso.betfair{BETFAIR_TLD}/api/login"
EXCHANGE_API_URL = f"https://api.betfair{BETFAIR_TLD}/exchange/betting/json-rpc/v1"

app = Flask(__name__)

publish_queue = queue.Queue()
clients = []
market_cache = {}

db_engine = None
race_table = None
horse_table = None
metadata = None

def init_db():
    global db_engine, metadata, race_table, horse_table
    if not SQLALCHEMY_DATABASE_URI:
        logger.info("No SQLALCHEMY_DATABASE_URI set; DB-backed market discovery is disabled.")
        return
    db_engine = create_engine(SQLALCHEMY_DATABASE_URI, pool_pre_ping=True)
    metadata = MetaData()
    metadata.reflect(bind=db_engine, only=None)
    if 'races' in metadata.tables:
        race_table = metadata.tables['races']
    elif 'race' in metadata.tables:
        race_table = metadata.tables['race']
    if 'horses' in metadata.tables:
        horse_table = metadata.tables['horses']
    elif 'horse' in metadata.tables:
        horse_table = metadata.tables['horse']
    logger.info("DB init complete. race_table=%s horse_table=%s", race_table, horse_table)

class BetfairClient:
    def __init__(self):
        self.session_token = None
        self.headers = {}
        self.pem_path = None
        self.auth_lock = threading.Lock()
        if BETFAIR_PEM_B64:
            try:
                decoded = base64.b64decode(BETFAIR_PEM_B64)
                fd, path = tempfile.mkstemp(prefix="betfair_cert_", suffix=".pem")
                with os.fdopen(fd, "wb") as f:
                    f.write(decoded)
                self.pem_path = path
                logger.info("Wrote decoded PEM to %s", self.pem_path)
            except Exception as e:
                logger.exception("Failed to decode BETFAIR_PEM_B64: %s", e)

    def cert_login(self):
        if not self.pem_path:
            return False
        logger.info("Attempting cert-login")
        try:
            data = {"username": BETFAIR_USERNAME, "password": BETFAIR_PASSWORD}
            r = requests.post(IDENTITY_CERT_URL, data=data, cert=self.pem_path, verify=True, timeout=10)
            if r.status_code != 200:
                logger.warning("Cert login failed status=%s body=%s", r.status_code, r.text)
                return False
            j = r.json()
            token = j.get("token") or j.get("sessionToken") or j.get("session_token")
            if token:
                self.session_token = token
                self.headers = {"X-Authentication": token, "X-Application": BETFAIR_APP_KEY, "Content-Type": "application/json"}
                logger.info("Cert login success")
                return True
            logger.warning("Cert login did not return token: %s", j)
        except Exception:
            logger.exception("Cert login exception")
        return False

    def userpass_login(self):
        if not (BETFAIR_USERNAME and BETFAIR_PASSWORD and BETFAIR_APP_KEY):
            logger.warning("Missing credentials for username/password login")
            return False
        logger.info("Attempting username/password login")
        try:
            payload = {"username": BETFAIR_USERNAME, "password": BETFAIR_PASSWORD}
            r = requests.post(IDENTITY_LOGIN_URL, data=payload, headers={"X-Application": BETFAIR_APP_KEY}, timeout=10)
            if r.status_code != 200:
                logger.warning("Login failed status=%s body=%s", r.status_code, r.text)
                return False
            token = None
            try:
                token = r.json().get("token")
            except Exception:
                token = r.text.strip()
            if token:
                self.session_token = token
                self.headers = {"X-Authentication": token, "X-Application": BETFAIR_APP_KEY, "Content-Type": "application/json"}
                logger.info("Userpass login success")
                return True
            logger.warning("Login did not return token: %s", r.text)
        except Exception:
            logger.exception("Userpass login exception")
        return False

    def ensure_auth(self):
        with self.auth_lock:
            if self.session_token:
                return True
            if self.pem_path and self.cert_login():
                return True
            if self.userpass_login():
                return True
            return False

    def list_market_book(self, market_ids):
        if not self.ensure_auth():
            raise RuntimeError("Not authenticated")
        payload = [{
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listMarketBook",
            "params": {
                "marketIds": market_ids,
                "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}
            },
            "id": 1
        }]
        try:
            r = requests.post(EXCHANGE_API_URL, json=payload, headers=self.headers, timeout=10)
            if r.status_code == 401 or r.status_code == 403:
                logger.warning("Auth error from exchange API (%s). Clearing token.", r.status_code)
                self.session_token = None
                raise RuntimeError("Auth error")
            if r.status_code == 429:
                logger.warning("Rate limited: 429")
                raise RuntimeError("Rate limited")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.exception("Error calling listMarketBook: %s", e)
            raise

bf_client = BetfairClient()

@app.route('/stream')
def stream():
    def gen():
        logger.info("SSE client connected")
        q = queue.Queue()
        clients.append(q)
        try:
            while True:
                try:
                    payload = q.get(timeout=30)
                    yield f"data: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    yield ":\n\n"
        finally:
            try:
                clients.remove(q)
            except ValueError:
                pass
            logger.info("SSE client disconnected")
    headers = {'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive'}
    return Response(stream_with_context(gen()), headers=headers)

def publish(payload):
    mid = payload.get("marketId")
    last = market_cache.get(mid)
    if last and json.dumps(last, sort_keys=True) == json.dumps(payload, sort_keys=True):
        return
    market_cache[mid] = payload
    for q in list(clients):
        try:
            q.put_nowait(payload)
        except Exception:
            logger.exception("Failed to put to client queue")

def get_active_market_ids():
    env_ids = [m.strip() for m in BETFAIR_MARKET_IDS.split(",") if m.strip()]
    if env_ids:
        return env_ids
    if not db_engine or not race_table:
        return []
    try:
        with db_engine.connect() as conn:
            s = select([race_table.c.market_id]).where(race_table.c.market_id != None)
            rows = conn.execute(s).fetchall()
            return list({r[0] for r in rows})
    except Exception:
        logger.exception("Error reading market ids from DB")
        return []

def persist_final_results(market_payload):
    if not db_engine or not horse_table:
        logger.debug("DB not configured; skipping persistence of final results")
        return
    mid = market_payload.get("marketId")
    runners = market_payload.get("runners", [])
    with db_engine.begin() as conn:
        for r in runners:
            sel = r.get("selectionId")
            final_odds = r.get("lastPriceTraded") if r.get("lastPriceTraded") else None
            final_position = r.get("status")
            try:
                stmt = horse_table.update().where(horse_table.c.betfair_selection_id == sel).values(
                    final_position=final_position,
                    final_odds=final_odds,
                    result_settled_at=datetime.utcnow(),
                    result_source='betfair'
                )
                res = conn.execute(stmt)
                if res.rowcount:
                    logger.info("Persisted result for selection %s rows=%s", sel, res.rowcount)
            except Exception:
                logger.exception("Failed to persist result for selection %s", sel)

def poller_loop():
    backoff = 1
    while True:
        try:
            if not BETFAIR_ENABLED:
                time.sleep(5)
                continue
            market_ids = get_active_market_ids()
            if not market_ids:
                time.sleep(2)
                continue
            chunk = market_ids[:40]
            logger.debug("Polling markets: %s", chunk)
            resp = bf_client.list_market_book(chunk)
            results = []
            if isinstance(resp, list):
                for item in resp:
                    r = item.get("result")
                    if r:
                        results.extend(r)
            elif isinstance(resp, dict) and resp.get("result"):
                results = resp.get("result")
            else:
                logger.debug("Unexpected listMarketBook response: %s", resp)
            for market in results:
                payload = {"type": "marketUpdate", "marketId": market.get("marketId"), "status": market.get("status"), "runners": []}
                runners = market.get("runners", [])
                for r in runners:
                    runner_payload = {"selectionId": r.get("selectionId"), "lastPriceTraded": r.get("lastPriceTraded"), "status": r.get("status"), "runnerName": r.get("runnerName")}
                    payload["runners"].append(runner_payload)
                publish(payload)
                if market.get("status") == "CLOSED":
                    persist_final_results(payload)
            backoff = 1
        except RuntimeError as e:
            logger.warning("Runtime error in poller: %s", e)
            backoff = min(backoff * 2, 64)
            time.sleep(backoff)
        except Exception:
            logger.exception("Unexpected poller exception; backing off")
            backoff = min(backoff * 2, 64)
            time.sleep(backoff)
        time.sleep(BETFAIR_POLL_INTERVAL)

def start_background_tasks():
    init_db()
    t = threading.Thread(target=poller_loop, daemon=True)
    t.start()
    logger.info("Started poller thread (daemon)")

@app.route('/health')
def health():
    return {"status": "ok", "betfair_enabled": BETFAIR_ENABLED}

if __name__ == '__main__':
    logger.info("Starting betfair_service (port=%s) BETFAIR_ENABLED=%s", PORT, BETFAIR_ENABLED)
    start_background_tasks()
    app.run(host='0.0.0.0', port=PORT, threaded=True)
