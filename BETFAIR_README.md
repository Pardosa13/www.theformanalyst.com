# Betfair Live Odds Integration

This document explains how to set up and use the Betfair live odds and results integration for The Form Analyst.

## Overview

The Betfair integration provides:
- **Live odds streaming** from Betfair Exchange markets
- **Automatic result capture** when markets close
- **Race-to-market mapping** to connect your uploaded CSV races to Betfair markets
- **Admin UI** for managing unmapped races

## Prerequisites

1. A Betfair account with API access
2. A Betfair Application Key (get one from [Betfair Developer Program](https://developer.betfair.com/))
3. (Optional) A certificate for cert-based login (more secure, required for some regions)

## Setup Instructions

### 1. Run Database Migrations

Before enabling Betfair integration, you need to add the required database columns.

**Locally:**
```bash
# Set your DATABASE_URL if using PostgreSQL
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"

# Run the migration script
python migrate_add_result_columns.py
```

**On Railway:**
```bash
# Connect to your Railway service
railway run python migrate_add_result_columns.py
```

The script will show what columns will be added and ask for confirmation before applying changes.

### 2. Configure Environment Variables

Add the following environment variables to your deployment:

| Variable | Required | Description |
|----------|----------|-------------|
| `BETFAIR_ENABLED` | Yes | Set to `true` to enable the integration |
| `BETFAIR_USERNAME` | Yes | Your Betfair username |
| `BETFAIR_PASSWORD` | Yes | Your Betfair password |
| `BETFAIR_APP_KEY` | Yes | Your Betfair Application Key |
| `BETFAIR_PEM_B64` | No | Base64-encoded PEM certificate (for cert login) |
| `BETFAIR_MARKET_IDS` | No | Comma-separated market IDs to poll |
| `BETFAIR_POLL_INTERVAL` | No | Polling interval in seconds (default: 2) |
| `BETFAIR_TLD` | No | Betfair TLD: `com` or `com.au` (default: `com`) |
| `BETFAIR_PAYLOAD_DIR` | No | Directory to save raw payloads (disabled if empty) |

**On Railway:**
1. Go to your project settings
2. Click on "Variables"
3. Add each variable

### 3. Start the Betfair Service

The Betfair service runs as a separate microservice. You can run it alongside your main app.

**Locally:**
```bash
# Install dependencies
pip install -r requirements-betfair.txt

# Set environment variables
export BETFAIR_ENABLED=true
export BETFAIR_USERNAME=your_username
export BETFAIR_PASSWORD=your_password
export BETFAIR_APP_KEY=your_app_key

# Run the service
python betfair_service.py
```

**On Railway:**
Create a new service in your Railway project with:
- **Start Command:** `python betfair_service.py`
- **Environment Variables:** Same as above

### 4. Map Races to Betfair Markets

Once the integration is enabled:

1. Go to **Admin Panel** → **Betfair Race Mapping**
2. You'll see a list of unmapped races from your uploaded CSVs
3. For each race, enter the Betfair Market ID and click "Map"

**Finding Market IDs:**
- Go to Betfair Exchange and find the race you want to track
- The Market ID is in the URL: `https://www.betfair.com.au/exchange/horse-racing/market/1.234567890`
- The Market ID is `1.234567890`

### 5. Automatic Mapping (Future Enhancement)

The system attempts to automatically match uploaded races to Betfair markets based on:
- Race date/time
- Track name
- Race number
- Horse name fuzzy matching

If automatic mapping fails, the race will appear in the admin UI for manual mapping.

## How It Works

### Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Main App      │     │ Betfair Service  │     │ Betfair API     │
│   (Flask)       │────▶│ (Flask SSE)      │────▶│ (Exchange)      │
└─────────────────┘     └──────────────────┘     └─────────────────┘
        │                        │
        │                        │ SSE Stream
        │                        ▼
        │               ┌──────────────────┐
        │               │ Browser Client   │
        │               │ (betfair-live.js)│
        └──────────────▶└──────────────────┘
```

### Data Flow

1. **Polling:** The Betfair service polls the Exchange API every N seconds for market updates
2. **Streaming:** Updates are broadcast via Server-Sent Events (SSE) to connected clients
3. **Display:** The browser client updates the race table with live odds
4. **Persistence:** When a market closes, final positions and odds are saved to the database

### Database Schema

New columns added to existing tables:

**races table:**
- `betfair_market_id` (VARCHAR) - Betfair market identifier
- `betfair_mapped` (BOOLEAN) - Whether race is mapped to a Betfair market

**horses table:**
- `betfair_selection_id` (INTEGER) - Betfair selection/runner identifier
- `final_position` (INTEGER) - Final finishing position
- `final_odds` (FLOAT) - Final odds at market close
- `result_settled_at` (DATETIME) - When result was recorded
- `result_source` (VARCHAR) - Source of result (e.g., "betfair")

## Security Notes

- **Never commit credentials** to version control
- Use environment variables for all sensitive data
- The `.gitignore` file excludes `.env` and certificate files
- Consider using cert-based login for production (more secure)

## Troubleshooting

### "Betfair integration is not enabled"

Set `BETFAIR_ENABLED=true` in your environment variables.

### "Authentication failed"

- Check your username/password are correct
- Verify your Application Key is valid
- If using cert login, ensure `BETFAIR_PEM_B64` is correctly encoded

### "No market updates"

- Ensure races are mapped to valid market IDs
- Check the market is still active on Betfair
- Verify the polling service is running (`/health` endpoint)

### "Rate limited (429)"

The service automatically backs off when rate limited. If persistent:
- Increase `BETFAIR_POLL_INTERVAL` to reduce API calls
- Reduce the number of markets being polled

## API Endpoints (Betfair Service)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/stream` | GET | SSE endpoint for live updates |
| `/markets` | GET | List of currently polled market IDs |
| `/status` | GET | Detailed service status |

## ML Readiness

The persisted results enable future ML model training:

```python
from models import Horse, Prediction

# Get horses with both predictions and results
results = Horse.query.filter(
    Horse.final_position.isnot(None),
    Horse.prediction.isnot(None)
).all()

# Compare predictions vs actual results
for horse in results:
    predicted = horse.prediction.score
    actual_position = horse.final_position
    # ... train your model
```

## Support

For issues or questions:
1. Check the logs: `railway logs` or local console output
2. Verify environment variables are set correctly
3. Ensure database migrations have been run
4. Check the Betfair service health endpoint
