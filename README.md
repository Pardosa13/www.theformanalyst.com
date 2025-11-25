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

## Quick Start

### 1. Complete Your Algorithm
Open `analyzer.js` and copy your full scoring algorithm from v27.html (see DEPLOYMENT.md)

### 2. Deploy
Follow the step-by-step guide in **DEPLOYMENT.md**

### 3. Create Users
Login as admin and create accounts for your friends

## File Structure

```
theformanalyst/
├── app.py              # Main Flask application
├── models.py           # Database models
├── auth.py             # Authentication
├── analyzer.py         # Analysis engine
├── analyzer.js         # YOUR ALGORITHM (copy from v27)
├── requirements.txt    # Python dependencies
├── package.json        # Node.js dependencies
├── .env.example        # Environment variables template
├── templates/          # HTML pages
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── admin.html
│   ├── history.html
│   └── meeting.html
└── static/            # CSS, JS, images
```

## Important: Algorithm Integration

**Before deploying**, you MUST copy your actual algorithm from v27.html into `analyzer.js`. 

The current `analyzer.js` is a placeholder. Your scoring functions need to be integrated for the application to work properly.

## Admin Access

Default admin credentials (change these!):
- Username: `admin`
- Password: `changeme123`

Set these via environment variables in Railway.

## Security

- ✅ All passwords hashed with Werkzeug
- ✅ Server-side algorithm execution
- ✅ HTTPS encryption
- ✅ Private GitHub repository
- ✅ Invite-only user system
- ✅ Admin-controlled access

## Support

See **DEPLOYMENT.md** for detailed instructions.
force deploy

## License

Proprietary - All rights reserved.  
© 2024 Partington Probability Ltd
