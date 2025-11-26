# The Form Analyst - Deployment Guide

This guide will walk you through deploying your racing analysis web application to **Railway.app** (easiest option).

## Prerequisites

- Your domain: **theformanalyst.com** (already purchased ✅)
- A GitHub account (free)
- Railway.app account (free tier available)

---

## Part 1: Integrate Your Algorithm (REQUIRED)

**IMPORTANT:** The `analyzer.js` file currently contains placeholder code. You MUST copy your actual algorithm from v27.html before deploying.

### Step 1: Extract Your Algorithm

1. Open your `Partington_Probability_Engine_PTY_LTD_v27.html` file
2. Find all the JavaScript functions (lines ~1500-3500) that contain:
   - `calculateHorseScore()`
   - `checkWeight()`
   - `checkLast10runs()`
   - `checkJockey()`
   - `checkTrainer()`
   - `checkClass()`
   - `checkSectional()`
   - `checkDaysSinceLastRun()`
   - `checkMargin()`
   - `checkFormPrice()`
   - `checkFirstUpSecondUp()`
   - `getLowestSectionalsByRace()`
   - `calculateAverageFormPrices()`
   - `calculateDirichletOdds()`
   - All helper functions

### Step 2: Copy to analyzer.js

1. Open `analyzer.js` in this folder
2. Replace the placeholder `calculateHorseScore()` function with your actual functions
3. Make sure all dependencies are included
4. The structure should be:
   - Read CSV data via stdin
   - Parse with PapaParse
   - Run your scoring algorithm
   - Output JSON results to stdout

---

## Part 2: Set Up GitHub Repository

### Step 1: Create GitHub Account
1. Go to github.com
2. Sign up (free)
3. Verify your email

### Step 2: Create New Repository
1. Click "New Repository"
2. Name it: `theformanalyst`
3. Make it **Private** (to protect your algorithm)
4. Don't initialize with README
5. Click "Create repository"

### Step 3: Upload Code to GitHub

**Option A: Using GitHub Website (Easiest)**
1. On your repository page, click "uploading an existing file"
2. Drag and drop ALL files from the `theformanalyst` folder
3. Add commit message: "Initial commit"
4. Click "Commit changes"

**Option B: Using Git Command Line**
```bash
# Open terminal/command prompt
cd /path/to/theformanalyst
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/theformanalyst.git
git push -u origin main
```

---

## Part 3: Deploy to Railway

### Step 1: Create Railway Account
1. Go to railway.app
2. Click "Login" → "Login with GitHub"
3. Authorize Railway to access your GitHub

### Step 2: Create New Project
1. Click "New Project"
2. Select "Deploy from GitHub repo"
3. Choose your `theformanalyst` repository
4. Railway will automatically detect it's a Flask app

### Step 3: Add PostgreSQL Database
1. In your Railway project, click "New"
2. Select "Database" → "PostgreSQL"
3. Railway will provision a database
4. It will automatically set the `DATABASE_URL` environment variable

### Step 4: Configure Environment Variables
1. Click on your web service (not the database)
2. Go to "Variables" tab
3. Add these variables:

```
SECRET_KEY=your-random-secret-key-here-make-it-long-and-random
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-secure-password-here
ADMIN_EMAIL=your-email@example.com
```

To generate a secure SECRET_KEY:
```python
import secrets
print(secrets.token_hex(32))
```

Or use a password generator to create a random 64-character string.

### Step 5: Deploy
1. Railway will automatically build and deploy
2. Wait 3-5 minutes for the first deployment
3. Check the "Deployments" tab for progress
4. Once complete, you'll get a URL like: `theformanalyst.up.railway.app`

### Step 5.5: Run Database Migrations (if needed)

If your database is missing required columns (e.g., `races.market_id` or Betfair result columns on `horses`), you can run migrations in Railway:

**Option A: Run Alembic migration (preferred)**
1. In Railway, go to your service settings
2. Open the Railway shell or use a one-off command:
   ```bash
   alembic upgrade head
   ```

**Option B: Run fallback script (if Alembic is not configured)**
1. In Railway, run the following one-off command:
   ```bash
   python scripts/ensure_db_columns.py
   ```

Both options are idempotent and safe to run multiple times. Check the logs to confirm columns were added/verified.

### Step 6: Test the Deployment
1. Visit your Railway URL
2. You should see the login page
3. Log in with your ADMIN_USERNAME and ADMIN_PASSWORD
4. Test uploading a CSV file

---

## Part 4: Connect Your Domain

### Step 1: Get Railway Domain
1. In Railway, click on your service
2. Go to "Settings" tab
3. Scroll to "Domains"
4. Click "Generate Domain" (you'll get something like `theformanalyst.up.railway.app`)

### Step 2: Add Custom Domain
1. Still in Railway "Domains" section
2. Click "Custom Domain"
3. Enter: `theformanalyst.com`
4. Railway will give you CNAME instructions

### Step 3: Update Namecheap DNS
1. Log in to Namecheap
2. Find your domain `theformanalyst.com`
3. Click "Manage" → "Advanced DNS"
4. Add/Edit these records:

**Add CNAME Record:**
- Type: CNAME
- Host: www
- Value: [your-railway-url] (from Railway)
- TTL: Automatic

**Add URL Redirect Record:**
- Type: URL Redirect
- Host: @
- Value: https://www.theformanalyst.com
- TTL: Automatic

### Step 4: Wait for DNS Propagation
- Usually takes 10-30 minutes
- Can take up to 24 hours
- Check status at: whatsmydns.net

---

## Part 5: Initialize Database & Create Users

### Step 1: Initialize Database
Railway should automatically run database migrations on first deploy. If not:

1. In Railway, go to your service
2. Click "Settings" → "Deploy"
3. The app will create tables automatically on startup

### Step 2: Login as Admin
1. Go to theformanalyst.com
2. Login with your ADMIN_USERNAME and ADMIN_PASSWORD
3. You should see the admin panel link in navigation

### Step 3: Create User Accounts for Friends
1. Click "Admin" in the navigation
2. Scroll to "Create New User"
3. Enter details for each friend:
   - Username (they'll use this to login)
   - Email
   - Password (give them this securely)
   - Check "Admin User" if they need admin access (not recommended)
4. Click "Create User"
5. Repeat for each friend

### Step 4: Share Credentials
Send each friend:
- Website: https://theformanalyst.com
- Their username
- Their password
- Instructions: "Go to the website, login, upload CSV files to analyze races"

---

## Troubleshooting

### "Module not found" errors
- Make sure `requirements.txt` has all dependencies
- Railway should install them automatically
- Check deployment logs in Railway

### "Database connection failed"
- Ensure PostgreSQL is added to your Railway project
- Check that DATABASE_URL is set automatically
- Try redeploying

### Algorithm not working / Wrong results
- Check that you copied your FULL algorithm to analyzer.js
- Make sure Node.js dependencies installed (package.json)
- Check Railway logs for errors

### Can't login
- Verify ADMIN_USERNAME and ADMIN_PASSWORD in Railway variables
- Try resetting: delete variable and re-add
- Check deployment logs

### Domain not working
- Wait 24 hours for DNS propagation
- Check CNAME is pointing to Railway URL
- Try https://www.theformanalyst.com (with www)

---

## Costs

### Railway Pricing
- **Free Tier:** $5 credit/month (usually enough for 5-10 users)
- **Hobby Plan:** $5/month (if you need more)
- **Pro Plan:** $20/month (unlimited)

Start with free tier, upgrade if needed.

### Total Monthly Cost
- Domain: $0.83/month ($10/year)
- Railway: $0-5/month
- **Total: $0.83-5.83/month**

---

## Maintenance

### Adding New Users
1. Login as admin
2. Go to Admin panel
3. Create new user

### Disabling Users
1. Admin panel
2. Click "Disable" next to user
3. They can't login anymore

### Viewing Usage
1. Admin panel shows statistics
2. History page shows all analyses
3. Each meeting stored permanently

### Updating Code
1. Make changes to files
2. Push to GitHub: `git push`
3. Railway auto-deploys new version
4. Takes 2-3 minutes

---

## Security Notes

- ✅ Algorithm is server-side (protected)
- ✅ Users can't see source code
- ✅ All passwords hashed in database
- ✅ HTTPS encryption automatic
- ✅ Private GitHub repo
- ✅ Only you can create accounts

---

## Support

If you get stuck:
1. Check Railway deployment logs
2. Check browser console (F12) for errors
3. Come back to Claude with specific error messages
4. I can help debug and fix issues

---

## Next Steps

1. ✅ Copy your algorithm to analyzer.js
2. ✅ Create GitHub repo and upload code
3. ✅ Deploy to Railway
4. ✅ Connect domain
5. ✅ Create user accounts
6. ✅ Test with friends!

Good luck! 🚀
