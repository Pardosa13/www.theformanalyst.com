# Betfair Live Odds Integration

This document explains how to set up and deploy the Betfair live odds integration for The Form Analyst.

## Overview

The Betfair integration adds real-time odds and race results to the meeting view page. It consists of:

1. **betfair_service.py** - A Flask microservice that connects to the Betfair Exchange API
2. **static/js/betfair-live.js** - Frontend client that displays live odds via SSE
3. **templates/_betfair_columns.html** - Template partial for additional table columns

## Prerequisites

Before enabling the Betfair integration, you need:

1. A Betfair account with API access
2. A registered Betfair application (get your App Key from the Betfair Developer Portal)
3. A self-signed SSL certificate for API authentication (see below)

## Creating Your Betfair Certificate

Betfair requires certificate-based authentication for their API. Follow these steps:

### 1. Generate a Self-Signed Certificate

```bash
# Generate private key and certificate
openssl genrsa -out client-2048.key 2048
openssl req -new -key client-2048.key -out client.csr -subj "/CN=your-betfair-username"
openssl x509 -req -days 365 -in client.csr -signkey client-2048.key -out client-2048.crt

# Combine into PEM format
cat client-2048.key client-2048.crt > client-2048.pem
```

### 2. Upload Certificate to Betfair

1. Log in to the Betfair Developer Portal
2. Navigate to My Account > Security Settings
3. Upload your `client-2048.crt` file

### 3. Create Base64-Encoded PEM for Deployment

For Railway deployment, encode your PEM file as base64:

```bash
# On macOS/Linux
base64 -i client-2048.pem | tr -d '\n' > client-2048.pem.b64

# On Windows (PowerShell)
[Convert]::ToBase64String([System.IO.File]::ReadAllBytes("client-2048.pem")) | Out-File -FilePath client-2048.pem.b64 -Encoding ASCII
```

The contents of `client-2048.pem.b64` will be used for the `BETFAIR_PEM_B64` environment variable.

**IMPORTANT:** Never commit your certificate files or the base64 content to version control!

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BETFAIR_ENABLED` | No | `false` | Set to `true` to enable the integration |
| `BETFAIR_USERNAME` | Yes* | - | Your Betfair account username |
| `BETFAIR_PASSWORD` | Yes* | - | Your Betfair account password |
| `BETFAIR_APP_KEY` | Yes* | - | Your Betfair API application key |
| `BETFAIR_PEM_B64` | Yes* | - | Base64-encoded PEM certificate (recommended) |
| `BETFAIR_PEM` | Yes* | - | Path to PEM file (alternative) |
| `BETFAIR_CERT_DIR` | Yes* | - | Directory with cert files (alternative) |
| `BETFAIR_MARKET_IDS` | Yes* | - | Comma-separated market IDs to monitor |
| `BETFAIR_POLL_INTERVAL` | No | `2` | Polling interval in seconds |
| `BETFAIR_TLD` | No | `.com.au` | Betfair TLD (`.com.au` or `.com`) |
| `PORT` | No | `5001` | HTTP port for the service |

*Required only when `BETFAIR_ENABLED=true`

## Railway Deployment

### 1. Add Environment Variables

In your Railway project dashboard:

1. Go to your project's **Variables** section
2. Add all required Betfair environment variables
3. For `BETFAIR_PEM_B64`, paste the entire base64 string from the file you created

### 2. Deploy the Betfair Service

You can run the Betfair service in two ways:

#### Option A: Separate Service (Recommended)

Create a new Railway service in the same project:

1. Add a new service
2. Connect to the same repository
3. Set the start command to: `python betfair_service.py`
4. Add the Betfair environment variables to this service
5. Set `PORT` to let Railway assign a port automatically

Then set `BETFAIR_SERVICE_URL` in your main app to point to this service.

#### Option B: Combined with Main App

Modify your `Procfile` to run both services:

```
web: gunicorn app:app
betfair: python betfair_service.py
```

### 3. Update Main App Configuration

In your main app's environment variables, add:

```
BETFAIR_ENABLED=true
BETFAIR_SERVICE_URL=https://your-betfair-service.railway.app
```

## Database Migration

The Horse model has been updated with new fields for Betfair data. You need to run a migration to add these columns.

### Migration Instructions

**WARNING:** Always backup your database before running migrations!

#### Using Flask-Migrate (Alembic)

If you're using Flask-Migrate, create and run a migration:

```bash
# Initialize migrations (if not already done)
flask db init

# Generate migration
flask db migrate -m "Add Betfair fields to Horse model"

# Review the generated migration file, then apply it
flask db upgrade
```

#### Manual SQL Migration

If you prefer manual SQL, here are the statements for PostgreSQL:

```sql
-- Backup first!
-- pg_dump -U username -h hostname dbname > backup.sql

-- Add Betfair columns to horses table
ALTER TABLE horses ADD COLUMN betfair_selection_id INTEGER;
ALTER TABLE horses ADD COLUMN final_position INTEGER;
ALTER TABLE horses ADD COLUMN final_odds FLOAT;
ALTER TABLE horses ADD COLUMN result_settled_at TIMESTAMP;
ALTER TABLE horses ADD COLUMN result_source VARCHAR(50);

-- Create index for selection_id lookups
CREATE INDEX ix_horses_betfair_selection_id ON horses (betfair_selection_id);
```

For SQLite:

```sql
-- SQLite doesn't support ALTER TABLE ADD COLUMN with constraints
ALTER TABLE horses ADD COLUMN betfair_selection_id INTEGER;
ALTER TABLE horses ADD COLUMN final_position INTEGER;
ALTER TABLE horses ADD COLUMN final_odds REAL;
ALTER TABLE horses ADD COLUMN result_settled_at DATETIME;
ALTER TABLE horses ADD COLUMN result_source TEXT;

-- Create index
CREATE INDEX ix_horses_betfair_selection_id ON horses (betfair_selection_id);
```

## Mapping Horses to Betfair Selection IDs

The integration requires mapping between your Horse records and Betfair selection IDs. This currently requires manual setup:

### Admin Mapping Process

1. Get the market catalog from Betfair for your race:
   - Use the Betfair API `listMarketCatalogue` endpoint
   - This returns runner names and their `selectionId` values

2. Update your Horse records with the corresponding `betfair_selection_id`:

```python
# Example: Map a horse to its Betfair selection ID
from app import db
from models import Horse

# Find the horse by name and race
horse = Horse.query.filter_by(horse_name="Black Caviar", race_id=123).first()
if horse:
    horse.betfair_selection_id = 12345678  # The Betfair selectionId
    db.session.commit()
```

3. Alternatively, you can add a simple admin interface to do this mapping through the UI.

### Future Enhancement

A future enhancement could include automatic name matching between your horse database and Betfair runners, but this requires careful handling of name variations (e.g., "Horse Name (NZ)" vs "HORSE NAME").

## Local Development

### 1. Create a .env File

```bash
cp .env.example .env
```

Edit `.env` with your Betfair credentials (for testing only).

### 2. Install Dependencies

```bash
pip install -r requirements.txt
pip install -r requirements-betfair.txt
```

### 3. Run the Betfair Service

In a terminal:

```bash
BETFAIR_ENABLED=true python betfair_service.py
```

### 4. Run the Main App

In another terminal:

```bash
BETFAIR_ENABLED=true BETFAIR_SERVICE_URL=http://localhost:5001 python app.py
```

### 5. Test the SSE Stream

Open a browser and navigate to `http://localhost:5001/stream` to see the raw SSE messages.

Or use curl:

```bash
curl -N http://localhost:5001/stream
```

## Testing Checklist

- [ ] Certificate is generated and uploaded to Betfair Developer Portal
- [ ] BETFAIR_PEM_B64 is correctly encoded (no newlines)
- [ ] Service starts without errors when BETFAIR_ENABLED=true
- [ ] `/health` endpoint returns `authenticated: true`
- [ ] `/stream` endpoint sends SSE messages
- [ ] Frontend receives and displays live odds
- [ ] Race results are correctly shown when market closes
- [ ] Connection reconnects automatically on disconnect

## Troubleshooting

### "Authentication failed"

- Verify your username, password, and app key are correct
- Check that your certificate is properly uploaded to Betfair
- Ensure BETFAIR_PEM_B64 doesn't contain newlines

### "TOO_MUCH_DATA" errors

- Reduce the number of markets in BETFAIR_MARKET_IDS
- Increase BETFAIR_POLL_INTERVAL

### SSE connection keeps disconnecting

- Check network connectivity
- Verify the service URL is correct
- Look at browser console for CORS errors

### Odds not updating in the table

- Ensure horses have `betfair_selection_id` populated
- Check browser console for errors
- Verify the service is receiving market data (check `/health`)

## Security Notes

1. **Never commit secrets** - All credentials should be in environment variables
2. **Protect your certificate** - The PEM file grants API access to your account
3. **Use HTTPS** - Always use HTTPS URLs in production
4. **Rate limits** - The service implements exponential backoff to respect Betfair rate limits
5. **Session management** - The service automatically re-authenticates when sessions expire

## Support

For issues with the Betfair API:
- [Betfair Developer Documentation](https://docs.developer.betfair.com/)
- [Betfair Developer Forum](https://forum.developer.betfair.com/)

For issues with this integration:
- Open an issue in the repository
- Check the service logs for error messages
