# Betfair Live Odds Integration

This document describes how to set up and deploy the Betfair Live Odds integration for The Form Analyst website.

## Overview

The integration provides live odds updates and race results from Betfair Exchange to the results pages. It consists of:

1. **Backend Service** (`betfair_service.py`) - A Flask microservice that polls Betfair API and exposes an SSE (Server-Sent Events) endpoint
2. **Frontend JavaScript** (`static/js/betfair-live.js`) - Client-side code that connects to the SSE endpoint and updates the UI
3. **Template Partial** (`templates/_betfair_columns.html`) - Optional template include for table headers

## Prerequisites

Before you begin, you'll need:

1. **Betfair Developer Account**
   - Register at [https://developer.betfair.com/](https://developer.betfair.com/)
   - Create an application to get your App Key

2. **API Certificates**
   - Generate a self-signed certificate for API authentication
   - Follow Betfair's guide: [Getting Started with API-NG](https://docs.developer.betfair.com/display/1smk3cen4v3lu3yomq5qye0ni/Non-Interactive+%28bot%29+login)

3. **Python 3.8+** with pip

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and configure the following:

```bash
# Required credentials
BETFAIR_USERNAME=your_betfair_username
BETFAIR_PASSWORD=your_betfair_password
BETFAIR_APP_KEY=your_app_key_here

# Certificate - choose ONE option:
# Option 1: Combined PEM file
BETFAIR_PEM=/secure/path/to/betfair.pem

# Option 2: Separate cert and key files
BETFAIR_CERT_DIR=/secure/path/to/certs/

# Markets to monitor (comma-separated)
BETFAIR_MARKET_IDS=1.123456789,1.987654321

# Optional settings
BETFAIR_POLL_INTERVAL=5       # Polling interval in seconds
BETFAIR_TLD=com               # Use 'au' for Australian API
SERVICE_HOST=127.0.0.1        # Host to bind service to
SERVICE_PORT=5001             # Port for SSE endpoint

# Feature flag for main app
BETFAIR_ENABLED=true
```

### Certificate Setup

#### Creating Certificates

Generate a self-signed certificate:

```bash
# Generate private key and certificate
openssl genrsa -out client-2048.key 2048
openssl req -new -x509 -days 365 -key client-2048.key -out client-2048.crt

# Or create a combined PEM file
cat client-2048.crt client-2048.key > betfair.pem
```

#### Upload Certificate to Betfair

1. Log in to [Betfair Developer Portal](https://developer.betfair.com/)
2. Go to Account → Security Settings
3. Upload your certificate (the `.crt` file only)

### Finding Market IDs

To find market IDs for races you want to monitor:

1. Use Betfair's [API Demo Tool](https://docs.developer.betfair.com/visualisers/api-ng-account-operations)
2. Or use the `listMarketCatalogue` API endpoint
3. Market IDs look like: `1.123456789`

## Installation

### Local Development

```bash
# Install dependencies
pip install -r requirements-betfair.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your credentials

# Run the service
python betfair_service.py
```

The service will start on `http://127.0.0.1:5001` by default.

### Docker Deployment

Create a `Dockerfile.betfair`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements-betfair.txt .
RUN pip install --no-cache-dir -r requirements-betfair.txt

COPY betfair_service.py .

# Certificates should be mounted as volumes
ENV BETFAIR_CERT_DIR=/certs

EXPOSE 5001

CMD ["python", "betfair_service.py"]
```

Run with Docker:

```bash
docker build -f Dockerfile.betfair -t betfair-service .

docker run -d \
  --name betfair-service \
  -p 5001:5001 \
  -v /path/to/certs:/certs:ro \
  -e BETFAIR_USERNAME=your_username \
  -e BETFAIR_PASSWORD=your_password \
  -e BETFAIR_APP_KEY=your_app_key \
  -e BETFAIR_MARKET_IDS=1.123456789 \
  betfair-service
```

### Heroku Deployment

1. Add environment variables via Heroku Dashboard or CLI:

```bash
heroku config:set BETFAIR_USERNAME=your_username
heroku config:set BETFAIR_PASSWORD=your_password
heroku config:set BETFAIR_APP_KEY=your_app_key
heroku config:set BETFAIR_MARKET_IDS=1.123456789
heroku config:set BETFAIR_POLL_INTERVAL=5
```

2. For certificates, encode as base64 and decode on startup:

```bash
# Encode certificate
base64 -w 0 betfair.pem > betfair_pem_base64.txt

# Set as config var
heroku config:set BETFAIR_PEM_BASE64="$(cat betfair_pem_base64.txt)"
```

Add to `betfair_service.py` startup:

```python
import base64
import tempfile

# Decode cert if provided as base64
pem_base64 = os.environ.get('BETFAIR_PEM_BASE64', '')
if pem_base64:
    pem_data = base64.b64decode(pem_base64)
    with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.pem') as f:
        f.write(pem_data)
        os.environ['BETFAIR_PEM'] = f.name
```

### Railway/Render Deployment

Similar to Heroku - use environment variables for all configuration and either:
- Mount certificates as files/volumes
- Use base64-encoded certificate in environment variable

## Enabling in the Main App

### Option 1: Automatic (Recommended)

Set `BETFAIR_ENABLED=true` in your environment. The template include in `view_meeting.html` will automatically load the JavaScript when enabled.

### Option 2: Manual Include

Add the following to any template where you want live odds:

```html
{% if config.get('BETFAIR_ENABLED') %}
<script 
    src="{{ url_for('static', filename='js/betfair-live.js') }}"
    data-betfair-sse-url="http://your-betfair-service-url:5001/stream">
</script>
{% endif %}
```

### Configuring SSE URL

If running the Betfair service on a different host/port, configure the JavaScript:

```html
<script>
    window.BETFAIR_SSE_URL = 'https://your-betfair-service.example.com/stream';
</script>
<script src="{{ url_for('static', filename='js/betfair-live.js') }}"></script>
```

## Adding Selection IDs to Horse Rows

For the JavaScript to match horses with Betfair data, add `data-selection-id` or `data-runner-id` attributes to table rows:

```html
<tr data-selection-id="12345678">
    <td>Horse Name</td>
    <!-- ... other cells ... -->
</tr>
```

The selection ID is the Betfair runner/selection ID for that horse in the market.

## API Endpoints

The Betfair service exposes the following endpoints:

### GET /stream

Server-Sent Events endpoint for live odds updates.

**Response**: SSE stream with JSON payloads:

```json
{
    "1.123456789": {
        "marketId": "1.123456789",
        "status": "OPEN",
        "inplay": true,
        "runners": [
            {
                "selectionId": 12345678,
                "status": "ACTIVE",
                "backPrice": 3.50,
                "backSize": 1250.00,
                "layPrice": 3.55,
                "laySize": 500.00
            }
        ],
        "timestamp": "2024-01-15T10:30:00.000Z"
    }
}
```

### GET /health

Health check endpoint.

**Response**:
```json
{
    "status": "ok",
    "authenticated": true,
    "timestamp": "2024-01-15T10:30:00.000Z"
}
```

### GET /markets

Returns current market data snapshot (for debugging).

## Testing Checklist

Before deploying to production:

- [ ] Certificate is properly configured and not committed to git
- [ ] Environment variables are set in deployment platform
- [ ] Service starts without errors
- [ ] `/health` endpoint returns `authenticated: true`
- [ ] `/stream` endpoint connects and sends data
- [ ] Browser DevTools shows SSE connection (Network tab, filter by "EventStream")
- [ ] Live odds appear in the UI when data is received
- [ ] Results update correctly when races finish

## Troubleshooting

### Service won't start

- Check certificate paths are correct
- Verify environment variables are set
- Check logs for specific error messages

### Authentication fails

- Verify username/password are correct
- Ensure certificate is uploaded to Betfair
- Check App Key is valid and has correct permissions

### No data received

- Verify market IDs are valid and active
- Check Betfair API status
- Ensure your App Key has data access permissions

### CORS errors in browser

- Ensure the SSE service sets `Access-Control-Allow-Origin` header
- May need to configure a reverse proxy for production

### High API usage

- Increase `BETFAIR_POLL_INTERVAL` to reduce requests
- Remove inactive market IDs from configuration
- Consider using Betfair streaming API for high-volume needs

## Security Notes

1. **Never commit credentials** - Use environment variables or secrets management
2. **Protect certificates** - Store in secure locations with restricted permissions
3. **Use HTTPS in production** - Put the service behind a reverse proxy with TLS
4. **Restrict access** - Consider adding authentication to the SSE endpoint in production
5. **Monitor API usage** - Betfair has rate limits and data fees

## Data Weighting

This integration uses `EX_BEST_OFFERS` price projection, which has minimal data weighting. For high-volume applications, consider:

- Using the Betfair Streaming API instead of polling
- Reducing poll frequency
- Subscribing to fewer markets simultaneously

## Support

For Betfair API issues, refer to:
- [Betfair Developer Documentation](https://docs.developer.betfair.com/)
- [Betfair Developer Forum](https://forum.developer.betfair.com/)

For issues with this integration, please open an issue on GitHub.
