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

## Support

See **DEPLOYMENT.md** for detailed instructions.

## License

Proprietary - All rights reserved.  
© 2024 Partington Probability Ltd
