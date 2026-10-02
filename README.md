# The Form Analyst

Professional horse racing analysis web application with protected algorithm and user management.

## Features

✅ **Secure Authentication** - Login system, invite-only access  
✅ **Protected Algorithm** - Your 4-month scoring system runs server-side  
✅ **Admin Control Panel** - Create/manage users, view activity  
✅ **Historical Storage** - All analyses saved to database  
✅ **CSV Upload** - Same workflow as v27  
✅ **Professional Interface** - Clean, modern design  
✅ **PDF Export** - Print/save results  

## Technology Stack

- **Backend:** Python Flask
- **Database:** PostgreSQL
- **Frontend:** HTML/CSS/JavaScript
- **Algorithm:** Your v27 JavaScript (server-side)
- **Hosting:** Railway.app
- **Domain:** theformanalyst.com

## Required environment variables

Set these in Railway before deploying:

- `DATABASE_URL` - Postgres connection string
- `SECRET_KEY` - long random string; the app refuses to start in production without it
- `ADMIN_PASSWORD` - password for the first `admin` user; no admin is created without it
- `ANTHROPIC_API_KEY` - for the chat assistant
- Optional: `RATELIMIT_STORAGE_URI` (e.g. a Redis URL) so rate limits are shared across workers

## Running the tests

```
pip install -r requirements.txt pytest
python -m pytest tests
```

The same tests run on every push and pull request (`.github/workflows/tests.yml`).

## Security

- ✅ All passwords hashed with Werkzeug
- ✅ Server-side algorithm execution
- ✅ HTTPS encryption
- ✅ Private GitHub repository
- ✅ Invite-only user system
- ✅ Admin-controlled access
- ✅ Login attempts rate limited
- ✅ Cross-site form posts blocked (Origin/Referer check)
- ✅ Database export and model download are admin only

## Repository layout

```
Front end   templates/         every page (HTML, page CSS and JS)
            static/            shared browser scripts and icons
Back end    app.py             the Flask app
            models.py          database tables
            analyzer.js        scoring algorithm (run via Node)
            *.py (root)        racing, ML, AFL, MMA and tracker modules
            scripts/           one-off audits, repairs and migrations
            migrations/        database schema migrations
Tests       tests/
Docs        docs/              front end, back end, deployment, security
```

Python modules stay in the root because Railway starts the app from there.

## Documentation

Start at **[docs/README.md](docs/README.md)**:

- [Front end guide](docs/frontend/README.md): pages, scripts, design rules
- [Back end guide](docs/backend/README.md): what each Python file does
- [Deployment guide](docs/deployment/deployment-guide.md)

## License

Proprietary - All rights reserved.  
© 2024 Partington Probability Ltd
