"""Site-wide security settings for the web app.

Kept in one place so app.py only has to call `init_security(app)`:

- HTTPS: plain-http visits are sent to https, and browsers are told to stay
  on https (HSTS).
- Security headers on every response, including a Content-Security-Policy
  that only lets scripts, styles and fonts load from this site and the two
  CDNs the templates use.
- Railway's proxy address is replaced by the visitor's real address, so rate
  limits and logs count each visitor separately.
- Request size caps, so one upload cannot exhaust a worker's memory.
- Rate-limit keys: a logged-in member is counted by account, anyone else by
  IP address.
- A `SECURITY|` log line for every refused login, blocked cross-site post,
  access-denied response and rate-limit hit, so they can be searched in the
  Railway logs.
"""
import hashlib
import hmac
import logging
import math
import os
from datetime import timedelta

from flask import current_app, redirect, request
from flask_login import current_user

security_log = logging.getLogger('security')

# Only these outside hosts may supply scripts, styles and fonts. Inline
# <script> and style= are still allowed because every template uses them, so
# escaping data before it goes into the page (each page's esc() helper)
# remains the main defence against XSS. The policy still stops a page from
# loading a script from anywhere else, being framed by another site, or
# posting a form to another site.
CONTENT_SECURITY_POLICY = '; '.join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
    "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com",
    "img-src 'self' data: blob: https:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
])

SECURITY_HEADERS = {
    'Content-Security-Policy': CONTENT_SECURITY_POLICY,
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
    'Cross-Origin-Opener-Policy': 'same-origin',
}

# One year. Subdomains are left out on purpose: nothing here says every
# subdomain of the site serves https.
HSTS_HEADER = 'max-age=31536000'

# Upload and form size caps. The largest legitimate upload is a meeting CSV
# (well under 1 MB); 16 MB leaves plenty of room.
MAX_CONTENT_LENGTH = 16 * 1024 * 1024
MAX_FORM_MEMORY_SIZE = 8 * 1024 * 1024
MAX_FORM_PARTS = 5000

# Statuses worth a SECURITY| log line.
_LOGGED_STATUSES = {401: 'unauthorised', 403: 'forbidden', 413: 'too-large', 429: 'rate-limited'}


def behind_proxy():
    """True on Railway (or when TRUST_PROXY is set), where a proxy sits in front."""
    return bool(os.environ.get('RAILWAY_ENVIRONMENT')) or os.environ.get('TRUST_PROXY', '').lower() in ('1', 'true', 'yes')


def client_ip():
    return request.remote_addr or 'unknown'


def _clean(value, limit=80):
    """Make a value safe to put on one log line."""
    text = str(value if value is not None else '')
    text = ''.join(ch if ch.isprintable() and ch not in '|' else '?' for ch in text)
    return text[:limit]


def log_security_event(event, **fields):
    """Write one `SECURITY|event|ip=...|path=...|...` line to the logs."""
    parts = [f'SECURITY|{event}', f'ip={_clean(client_ip(), 64)}']
    try:
        parts.append(f'path={_clean(request.path, 200)}')
        parts.append(f'method={request.method}')
    except RuntimeError:
        pass
    try:
        if current_user and current_user.is_authenticated:
            parts.append(f'user_id={current_user.id}')
    except Exception:
        pass
    for key, value in fields.items():
        parts.append(f'{key}={_clean(value)}')
    security_log.warning('|'.join(parts))


def finite_float(value):
    """float(value), refusing NaN and infinity. Raises ValueError or TypeError."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('number must be finite')
    return number


def rate_limit_key():
    """Count a logged-in member by account, anyone else by IP address."""
    try:
        if current_user and current_user.is_authenticated:
            return f'user:{current_user.id}'
    except Exception:
        pass
    return f'ip:{client_ip()}'


def default_rate_limit():
    """Generous for members (pages poll live odds), tight for strangers."""
    try:
        if current_user and current_user.is_authenticated:
            return '5000 per day;1000 per hour'
    except Exception:
        pass
    return '1000 per day;200 per hour'


def login_username_key():
    """Rate-limit key for login attempts against one username, from any IP."""
    username = (request.form.get('username') or '').strip().lower()
    return f'login-user:{username[:150]}'


def session_fingerprint(password_hash):
    """Short keyed digest of a password hash, stored in the login cookie.

    The login cookie names the account and this digest. When the password
    changes, the digest changes, so every other browser logged in to that
    account is logged out. The hash itself never goes into the cookie
    (Flask cookies are signed, not encrypted).
    """
    key = (current_app.secret_key or '').encode() if isinstance(current_app.secret_key, str) else (current_app.secret_key or b'')
    return hmac.new(key, (password_hash or '').encode(), hashlib.sha256).hexdigest()[:20]


def init_security(app):
    """Apply every setting above to the Flask app."""
    if behind_proxy():
        # Railway's edge proxy adds one X-Forwarded-For / X-Forwarded-Proto
        # entry. Trust exactly one hop so a visitor cannot fake their address
        # by sending the header themselves.
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    app.config.update(
        MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
        MAX_FORM_MEMORY_SIZE=MAX_FORM_MEMORY_SIZE,
        MAX_FORM_PARTS=MAX_FORM_PARTS,
        # "Remember me" lasted a year by default. A month is plenty.
        REMEMBER_COOKIE_DURATION=timedelta(days=30),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        REMEMBER_COOKIE_REFRESH_EACH_REQUEST=False,
    )

    @app.before_request
    def _force_https():
        # Only act when the proxy says the visitor came in over plain http.
        # Requests with no such header (Railway health checks, internal
        # calls, the test client, local runs) are left alone.
        if request.headers.get('X-Forwarded-Proto', '').split(',')[0].strip().lower() != 'http':
            return None
        if not behind_proxy():
            return None
        url = request.url.replace('http://', 'https://', 1)
        # 308 keeps the method and body, so a POST stays a POST.
        return redirect(url, code=308)

    @app.after_request
    def _security_headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.is_secure or request.headers.get('X-Forwarded-Proto', '').lower().startswith('https'):
            response.headers.setdefault('Strict-Transport-Security', HSTS_HEADER)
        # Logged-in pages hold private data (bets, budget). Do not let the
        # browser or any shared cache keep a copy.
        try:
            logged_in = current_user and current_user.is_authenticated
        except Exception:
            logged_in = False
        if logged_in and request.endpoint != 'static' and 'Cache-Control' not in response.headers:
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.after_request
    def _log_refusals(response):
        reason = _LOGGED_STATUSES.get(response.status_code)
        if reason and request.endpoint != 'static':
            log_security_event(reason, status=response.status_code)
        return response
