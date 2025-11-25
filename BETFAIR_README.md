# Betfair Live Odds Integration

This document describes how to set up and enable the Betfair live odds integration for The Form Analyst.

## Overview

The Betfair integration provides:
- Real-time live odds from Betfair Exchange
- Automatic result recording when markets close
- SSE (Server-Sent Events) streaming to the frontend

## Prerequisites

1. A Betfair account with API access
2. A Betfair Application Key (create one at [Betfair Developer Portal](https://developer.betfair.com/))
3. Your Betfair username and password

## Environment Variables

Set the following environment variables (e.g., in Railway's Variables section):

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `BETFAIR_ENABLED` | Set to `true` to enable the integration | `true` |
| `BETFAIR_USERNAME` | Your Betfair username | `myusername` |
| `BETFAIR_PASSWORD` | Your Betfair password | `mypassword` |
| `BETFAIR_APP_KEY` | Your Betfair application key | `abc123def456` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `BETFAIR_PEM_B64` | Base64-encoded PEM certificate for cert-login | (empty) |
| `BETFAIR_TLD` | Betfair TLD (com for international, com.au for Australia) | `com` |
| `BETFAIR_POLL_INTERVAL` | Polling interval in seconds | `2` |
| `BETFAIR_MARKET_IDS` | Comma-separated list of market IDs to poll (for testing) | (empty) |
| `BETFAIR_SERVICE_URL` | URL of the Betfair service | `http://localhost:8081` |
| `PORT` | Port for the Betfair service | `8081` |

## Setup Instructions

### 1. Database Migration

Before enabling Betfair integration, run the migration script to add the required database columns:

**Local:**
```bash
python migrate_add_result_columns.py
```

**Railway (one-off command):**
```bash
railway run python migrate_add_result_columns.py
```

The script will:
1. Show you what changes will be made
2. Ask for confirmation
3. Add nullable columns to support Betfair data

### 2. Set Environment Variables

**Railway:**
1. Go to your Railway project
2. Click on the service
3. Go to "Variables"
4. Add the required variables:
   - `BETFAIR_ENABLED=true`
   - `BETFAIR_USERNAME=your_username`
   - `BETFAIR_PASSWORD=your_password`
   - `BETFAIR_APP_KEY=your_app_key`

### 3. Deploy the Betfair Service

The Betfair service (`betfair_service.py`) can be deployed as a separate Railway service:

1. Create a new service in Railway
2. Link it to the same repository
3. Set the start command: `python betfair_service.py`
4. Add the same Betfair environment variables
5. Set `PORT=8081` (or your preferred port)

### 4. Configure the Main App

Add `BETFAIR_SERVICE_URL` to your main app's environment variables, pointing to the Betfair service URL.

## Testing

### Local Testing

1. Copy `.env.example` to `.env`
2. Fill in your Betfair credentials
3. Run the migration: `python migrate_add_result_columns.py`
4. Start the Betfair service: `python betfair_service.py`
5. Start the main app: `python app.py`
6. Navigate to a meeting page to see live odds (if you have mapped market IDs)

### Testing the SSE Endpoint

You can test the SSE endpoint directly:

```bash
curl -N http://localhost:8081/stream
```

Or check the health endpoint:

```bash
curl http://localhost:8081/health
```

### Testing with Specific Markets

Set `BETFAIR_MARKET_IDS` to test with specific markets:

```bash
export BETFAIR_MARKET_IDS="1.234567890,1.234567891"
python betfair_service.py
```

## Mapping Races to Betfair Markets

### Automatic Mapping

When you upload a CSV, the system will attempt to auto-match races to Betfair markets based on:
- Race date/time
- Track name
- Race number
- Horse names (fuzzy matching)

### Manual Mapping

For races that couldn't be auto-mapped:

1. Go to Admin → Betfair Mapping (`/admin/betfair-mapping`)
2. Find the unmapped race
3. Click "Map"
4. Enter the Betfair Market ID
5. Optionally enter Selection IDs for each horse
6. Save

## Security Notes

⚠️ **IMPORTANT: DO NOT commit your `.env` file or any credentials to the repository.**

- All credentials should be provided via environment variables at runtime
- The `.gitignore` file is configured to ignore `.env` and certificate files
- Never share your Betfair API credentials

## Troubleshooting

### "Authentication failed"

- Check your Betfair username and password
- Ensure your Betfair account has API access enabled
- Verify your app key is correct

### "Rate limited"

- The service implements automatic backoff on 429 errors
- Consider increasing `BETFAIR_POLL_INTERVAL`

### "No market IDs to poll"

- Ensure you have mapped races to Betfair markets
- Or set `BETFAIR_MARKET_IDS` for testing

### Live odds not updating

- Check if `BETFAIR_ENABLED=true` is set
- Verify the Betfair service is running
- Check browser console for SSE connection errors

## Architecture

```
┌─────────────────┐      ┌─────────────────────┐
│   Main App      │      │  Betfair Service    │
│   (Flask)       │──────│  (Flask + SSE)      │
│   Port 8080     │      │  Port 8081          │
└────────┬────────┘      └──────────┬──────────┘
         │                          │
         │    /betfair/stream       │
         ├──────────────────────────┤
         │         Proxy            │
         │                          │
┌────────▼────────┐      ┌──────────▼──────────┐
│   Browser       │      │  Betfair Exchange   │
│   (JavaScript)  │      │  API                │
└─────────────────┘      └─────────────────────┘
```

## Dependencies

The Betfair integration requires these additional dependencies (in `requirements-betfair.txt`):

```
requests>=2.31.0
```

Install with:
```bash
pip install -r requirements-betfair.txt
```
