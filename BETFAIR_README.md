# Betfair Live Odds Integration

This guide explains how to set up and enable Betfair live odds integration for The Form Analyst.

## Overview

The Betfair integration provides:
- **Live odds streaming** - Real-time odds updates from Betfair Exchange
- **Final results** - Automatic capture of race results when markets close
- **Auto-mapping** - Best-effort matching of uploaded races to Betfair markets
- **Admin UI** - Manual mapping interface for unmatched races

## Prerequisites

1. A Betfair account with API access
2. A Betfair Application Key (get one from [Betfair Developer Portal](https://developer.betfair.com/))
3. For certificate login (recommended): A self-signed certificate linked to your Betfair account

## Environment Variables

All configuration is done through environment variables. **Never commit credentials to the repository.**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BETFAIR_ENABLED` | Yes | `false` | Set to `true` to enable integration |
| `BETFAIR_USERNAME` | Yes* | - | Your Betfair username |
| `BETFAIR_PASSWORD` | Yes* | - | Your Betfair password |
| `BETFAIR_APP_KEY` | Yes* | - | Your Betfair API application key |
| `BETFAIR_PEM_B64` | No | - | Base64-encoded PEM certificate (for cert login) |
| `BETFAIR_MARKET_IDS` | No | - | Comma-separated market IDs to poll |
| `BETFAIR_POLL_INTERVAL` | No | `2` | Polling interval in seconds |
| `BETFAIR_TLD` | No | `com.au` | Betfair API TLD (`com.au` for Australia, `com` for UK) |
| `BETFAIR_SERVICE_URL` | No | - | URL of Betfair service if running separately |
| `PORT` | No | `5001` | Port for Betfair service |

*Required if `BETFAIR_ENABLED=true`

## Railway Deployment

### Step 1: Set Environment Variables

In your Railway project dashboard:

1. Go to your service → **Variables**
2. Add the following variables:

```
BETFAIR_ENABLED=true
BETFAIR_USERNAME=your_betfair_username
BETFAIR_PASSWORD=your_betfair_password
BETFAIR_APP_KEY=your_app_key
BETFAIR_TLD=com.au
```

### Step 2: (Optional) Certificate-Based Login

Certificate login is more secure and has higher rate limits. To set it up:

1. Generate a self-signed certificate:
```bash
openssl genrsa -out betfair.key 2048
openssl req -new -x509 -days 365 -key betfair.key -out betfair.crt
cat betfair.key betfair.crt > betfair.pem
```

2. Link the certificate to your Betfair account at [Betfair API Accounts](https://developer.betfair.com/apps/)

3. Base64 encode the PEM file:
```bash
base64 -w0 betfair.pem > betfair_b64.txt
```

4. Add to Railway:
```
BETFAIR_PEM_B64=<contents of betfair_b64.txt>
```

### Step 3: Run Database Migration

The integration adds new columns to the database. Run the migration:

**Option A: Railway One-Off Command**

In Railway dashboard, go to your service and use the **Shell** or run:
```bash
python migrate_add_result_columns.py --confirm
```

**Option B: Run Locally**

Connect to your Railway database and run:
```bash
DATABASE_URL="your_railway_database_url" python migrate_add_result_columns.py --confirm
```

### Step 4: Redeploy

Trigger a redeploy in Railway to pick up the new environment variables.

## Local Development

### Step 1: Create .env File

Copy the example and fill in your credentials:
```bash
cp .env.example .env
# Edit .env with your credentials
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
pip install -r requirements-betfair.txt
```

### Step 3: Run Migration

```bash
python migrate_add_result_columns.py --confirm
```

### Step 4: Start Services

**Main App:**
```bash
python app.py
```

**Betfair Service (in separate terminal):**
```bash
python betfair_service.py
```

Or run both with separate ports:
```bash
# Terminal 1 - Main app on port 8080
PORT=8080 python app.py

# Terminal 2 - Betfair service on port 5001
PORT=5001 python betfair_service.py
```

## Testing the Integration

### 1. Check Service Health

```bash
curl http://localhost:5001/health
```

Expected response:
```json
{
  "status": "ok",
  "enabled": true,
  "authenticated": true,
  "market_ids": [],
  "subscribers": 0
}
```

### 2. View Live Odds

1. Upload a race meeting CSV
2. Map the races to Betfair markets via Admin → Betfair Mapping
3. View the meeting page - you should see "Live Odds Connected" indicator
4. Live odds will update in real-time

### 3. Check SSE Stream

```bash
curl -N http://localhost:5001/stream
```

You should see heartbeat messages and market updates.

## Admin Betfair Mapping

Access the mapping interface at `/admin/betfair-mapping` to:

1. **View unmapped races** - Races that need manual market assignment
2. **Review low-confidence mappings** - Auto-mapped races that need verification
3. **Assign market IDs** - Manually link races to Betfair markets

### Finding Market IDs

1. Go to [Betfair Exchange](https://www.betfair.com.au/exchange/plus/)
2. Navigate to the race you want to map
3. The market ID is in the URL: `https://www.betfair.com.au/exchange/plus/horse-racing/market/1.234567890`
4. Copy `1.234567890` as the market ID

## Troubleshooting

### Authentication Errors

- Verify credentials are correct
- Check your Betfair account is active and API-enabled
- Ensure `BETFAIR_TLD` matches your account region

### Rate Limiting (429 Errors)

- The service automatically backs off on rate limits
- Consider using certificate login for higher limits
- Increase `BETFAIR_POLL_INTERVAL` if frequent

### No Live Odds Showing

1. Check Betfair service is running (`/health` endpoint)
2. Verify races are mapped to market IDs
3. Check browser console for JavaScript errors
4. Ensure `BETFAIR_ENABLED=true` in environment

### Database Migration Errors

- Ensure DATABASE_URL is correctly set
- Check database permissions allow ALTER TABLE
- Run with `--confirm` flag to skip prompts

## Security Notes

- **Never commit credentials** to the repository
- Use Railway's encrypted environment variables
- The `.gitignore` excludes `.env` and certificate files
- All credentials are read at runtime from environment

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   Main Flask    │     │  Betfair Svc    │
│      App        │     │  (SSE Server)   │
│   (port 8080)   │     │   (port 5001)   │
└────────┬────────┘     └────────┬────────┘
         │                       │
         │    ┌──────────────────┤
         │    │                  │
         ▼    ▼                  ▼
    ┌─────────────┐        ┌──────────┐
    │  PostgreSQL │        │  Betfair │
    │  Database   │        │   API    │
    └─────────────┘        └──────────┘
```

The Betfair service:
1. Authenticates with Betfair API
2. Polls market books for configured market IDs
3. Broadcasts updates via SSE to connected clients
4. Persists final results to database when markets close
