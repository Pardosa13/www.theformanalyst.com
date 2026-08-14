"""Shared parsing of analyzer.js prediction notes into scoring components.

Used by app.py (live routes) and backtest.py (nightly component analysis
cache) so the two never drift apart.
"""

import re

COMPONENT_KEY_OVERRIDES = {
    'Distance Change - Drop Back Moderate (200-400m)': 'drop_back_distance_200_400',
    'Drop back in distance (200-400m)': 'drop_back_distance_200_400',
}

COMPONENT_DISPLAY_BY_KEY = {
    'drop_back_distance_200_400': 'Drop back in distance (200-400m)',
}

def normalize_component_key(name):
    """Return a stable Best Bets component key for a display name or legacy component name."""
    if not name:
        return ''

    clean_name = str(name).strip()
    if clean_name in COMPONENT_KEY_OVERRIDES:
        return COMPONENT_KEY_OVERRIDES[clean_name]

    key = clean_name.lower()
    key = key.replace('≤', 'lte').replace('>=', 'gte').replace('>', 'gt').replace('<', 'lt')
    key = re.sub(r'[^a-z0-9]+', '_', key)
    return key.strip('_')

def component_display_name_for_key(component_key, fallback_name=None):
    """Return the preferred display name for a stable component key."""
    return COMPONENT_DISPLAY_BY_KEY.get(component_key) or fallback_name or component_key


_NOTES_COMPONENT_PATTERNS = [

    # ====== LAST 10 FORM ======
    (r'(?:([+-]?\s*[\d.]+)\s*:\s*)?Ran places:\s*([^\n]+)', '_ran_places_dynamic'),

    # ====== JOCKEYS ======
    # Live L100 strike rate patterns
    (r'\+\s*20\.0\s*:\s*Jockey hot form', 'Jockey - Hot Form (L100 25%+ SR)'),
    (r'\+\s*15\.0\s*:\s*Jockey solid form', 'Jockey - Solid Form (L100 18-25% SR)'),
    (r'[-−]\s*6\.0\s*:\s*Jockey poor form', 'Jockey - Poor Form (L100 6-11% SR)'),
    (r'[-−]\s*12\.0\s*:\s*Jockey cold', 'Jockey - Cold (L100 <6% SR)'),

    # ====== TRAINERS ======
    # Live L100 strike rate patterns
    (r'\+\s*20\.0\s*:\s*Trainer hot form', 'Trainer - Hot Form (L100 22%+ SR)'),
    (r'\+\s*15\.0\s*:\s*Trainer solid form', 'Trainer - Solid Form (L100 16-22% SR)'),
    (r'[-−]\s*5\.0\s*:\s*Trainer poor form', 'Trainer - Poor Form (L100 5-10% SR)'),
    (r'[-−]\s*10\.0\s*:\s*Trainer cold', 'Trainer - Cold (L100 <5% SR)'),

    # ====== TRACK RECORD - WIN RATES ======
    (r'\+\s*6\.0\s*:\s*Exceptional win rate.*at this track\b', 'Track Win Rate - Exceptional (51%+)'),
    (r'\+\s*5\.0\s*:\s*Strong win rate.*at this track\b', 'Track Win Rate - Strong (36-50%)'),
    (r'\+\s*4\.0\s*:\s*Good win rate.*at this track\b', 'Track Win Rate - Good (26-35%)'),
    (r'\+\s*2\.0\s*:\s*Moderate win rate.*at this track\b', 'Track Win Rate - Moderate (16-25%)'),
    (r'\+\s*1\.0\s*:\s*Low win rate.*at this track\b', 'Track Win Rate - Low (1-15%)'),
    (r'\+\s*0\.0\s*:\s*No wins at this track\b', 'Track Win Rate - No Wins'),
    (r'\+\s*0\.0\s*:\s*No runs at this track\b', 'Track - No Runs'),

    # ====== TRACK RECORD - PODIUM RATES ======
    (r'\+\s*6\.0\s*:\s*Elite podium rate.*at this track\b', 'Track Podium Rate - Elite (85%+)'),
    (r'\+\s*5\.0\s*:\s*Excellent podium rate.*at this track\b', 'Track Podium Rate - Excellent (70-84%)'),
    (r'\+\s*4\.0\s*:\s*Strong podium rate.*at this track\b', 'Track Podium Rate - Strong (55-69%)'),
    (r'\+\s*3\.0\s*:\s*Good podium rate.*at this track\b', 'Track Podium Rate - Good (40-54%)'),
    (r'\+\s*1\.0\s*:\s*Moderate podium rate.*at this track\b', 'Track Podium Rate - Moderate (25-39%)'),
    # FIX: also catches "Poor podium rate" phrasing
    (r'-\s*5\.0\s*:\s*Poor performance at this track|Poor podium rate.*at this track', 'Track - Poor Performance'),
    (r'=\s*([\d.]+)\s*:\s*Total track score', '_track_score_dynamic'),

    # ====== TRACK+DISTANCE RECORD - WIN RATES ======
    (r'\+\s*8\.0\s*:\s*Exceptional win rate.*at this track\+distance', 'Track+Distance Win Rate - Exceptional'),
    (r'\+\s*7\.0\s*:\s*Strong win rate.*at this track\+distance', 'Track+Distance Win Rate - Strong'),
    (r'\+\s*5\.0\s*:\s*Good win rate.*at this track\+distance', 'Track+Distance Win Rate - Good'),
    (r'\+\s*3\.0\s*:\s*Moderate win rate.*at this track\+distance', 'Track+Distance Win Rate - Moderate'),
    (r'\+\s*1\.0\s*:\s*Low win rate.*at this track\+distance', 'Track+Distance Win Rate - Low'),
    (r'\+\s*0\.0\s*:\s*No wins at this track\+distance', 'Track+Distance Win Rate - No Wins'),
    (r'\+\s*0\.0\s*:\s*No runs at this track\+distance', 'Track+Distance - No Runs'),

    # ====== TRACK+DISTANCE RECORD - PODIUM RATES ======
    (r'\+\s*8\.0\s*:\s*Elite podium rate.*at this track\+distance', 'Track+Distance Podium Rate - Elite'),
    (r'\+\s*7\.0\s*:\s*Excellent podium rate.*at this track\+distance', 'Track+Distance Podium Rate - Excellent'),
    (r'\+\s*6\.0\s*:\s*Strong podium rate.*at this track\+distance', 'Track+Distance Podium Rate - Strong'),
    (r'\+\s*4\.0\s*:\s*Good podium rate.*at this track\+distance', 'Track+Distance Podium Rate - Good'),
    (r'\+\s*2\.0\s*:\s*Moderate podium rate.*at this track\+distance', 'Track+Distance Podium Rate - Moderate'),
    (r'-\s*6\.0\s*:\s*Poor performance at this track\+distance', 'Track+Distance - Poor Performance'),
    (r'=\s*([\d.]+)\s*:\s*Total track\+distance score', '_td_score_dynamic'),

    # ====== DISTANCE RECORD - WIN RATES ======
    (r'\+\s*8\.0\s*:\s*Exceptional win rate.*at this distance\b', 'Distance Win Rate - Exceptional (51%+)'),
    (r'\+\s*7\.0\s*:\s*Strong win rate.*at this distance\b', 'Distance Win Rate - Strong (36-50%)'),
    (r'\+\s*5\.0\s*:\s*Good win rate.*at this distance\b', 'Distance Win Rate - Good (26-35%)'),
    (r'\+\s*3\.0\s*:\s*Moderate win rate.*at this distance\b', 'Distance Win Rate - Moderate (16-25%)'),
    (r'\+\s*1\.0\s*:\s*Low win rate.*at this distance\b', 'Distance Win Rate - Low (1-15%)'),
    (r'\+\s*0\.0\s*:\s*No wins at this distance\b', 'Distance Win Rate - No Wins'),
    (r'\+\s*0\.0\s*:\s*No runs at this distance\b', 'Distance - No Runs'),

    # ====== DISTANCE RECORD - PODIUM RATES ======
    (r'\+\s*8\.0\s*:\s*Elite podium rate.*at this distance\b', 'Distance Podium Rate - Elite (85%+)'),
    (r'\+\s*7\.0\s*:\s*Excellent podium rate.*at this distance\b', 'Distance Podium Rate - Excellent (70-84%)'),
    (r'\+\s*6\.0\s*:\s*Strong podium rate.*at this distance\b', 'Distance Podium Rate - Strong (55-69%)'),
    (r'\+\s*4\.0\s*:\s*Good podium rate.*at this distance\b', 'Distance Podium Rate - Good (40-54%)'),
    (r'\+\s*2\.0\s*:\s*Moderate podium rate.*at this distance\b', 'Distance Podium Rate - Moderate (25-39%)'),
    (r'-\s*6\.0\s*:\s*Poor performance at this distance\b', 'Distance - Poor Performance'),
    (r'=\s*([\d.]+)\s*:\s*Total distance score', '_dist_score_dynamic'),

    # ====== TRACK CONDITION - WIN RATES ======
    (r'\+\s*12\.0\s*:\s*Exceptional win rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - Exceptional (51%+)'),
    (r'\+\s*10\.0\s*:\s*Strong win rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - Strong (36-50%)'),
    (r'\+\s*8\.0\s*:\s*Good win rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - Good (26-35%)'),
    (r'\+\s*5\.0\s*:\s*Moderate win rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - Moderate (16-25%)'),
    (r'\+\s*2\.0\s*:\s*Low win rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - Low (1-15%)'),
    (r'\+\s*0\.0\s*:\s*No wins on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - No Wins'),
    (r'\+\s*0\.0\s*:\s*No runs on (good|soft|heavy|firm|synthetic)', 'Condition - No Runs'),

    # ====== TRACK CONDITION - PODIUM RATES ======
    (r'\+\s*12\.0\s*:\s*Elite podium rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Podium Rate - Elite (85%+)'),
    (r'\+\s*10\.0\s*:\s*Excellent podium rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Podium Rate - Excellent (70-84%)'),
    (r'\+\s*9\.0\s*:\s*Strong podium rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Podium Rate - Strong (55-69%)'),
    (r'\+\s*6\.0\s*:\s*Good podium rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Podium Rate - Good (40-54%)'),
    (r'\+\s*3\.0\s*:\s*Moderate podium rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Podium Rate - Moderate (25-39%)'),
    (r'-\s*8\.0\s*:\s*Poor performance on (good|soft|heavy|firm|synthetic)', 'Condition - Poor Performance'),
    (r'=\s*([\d.]+)\s*:\s*Total track condition score', '_cond_score_dynamic'),

    # ====== DISTANCE CHANGE ======
    # FIX: old patterns matched "Stepping up Xm in distance" — new format uses bracketed ranges
    # Also handle ~ prefix for near-baseline
    (r'[~+\-]\s*[\d.]+\s*:\s*Step(?:ping)? up.*\(400m\+\)', 'Distance Change - Step Up Large (400m+)'),
    (r'[~+\-]\s*[\d.]+\s*:\s*Step(?:ping)? up.*\(200-400m\)', 'Distance Change - Step Up Moderate (200-400m)'),
    (r'[~+\-]\s*[\d.]+\s*:\s*Drop(?:ping)? back.*\(400m\+\)', 'Distance Change - Drop Back Large (400m+)'),
    (r'([~+\-]\s*[\d.]+)\s*:\s*Drop(?:ping)? back in distance \(200-400m\)', 'Drop back in distance (200-400m)'),

    # ====== CLASS CHANGE ======
    (r'\+\s*([\d.]+):\s*Stepping DOWN', '_class_drop_dynamic'),
    (r'(-[\d.]+):\s*Stepping UP', '_class_rise_dynamic'),

    # ====== LAST START - WINNERS ======
    (r'\+\s*20\.0\s*:\s*Dominant last.?start win', 'Last Start - Dominant Win (5L+)'),
    (r'\+\s*15\.0\s*:\s*Comfortable last.?start win', 'Last Start - Comfortable Win (2-5L)'),
    (r'\+\s*5\.0\s*:\s*Narrow last.?start win', 'Last Start - Narrow Win (0.5-2L)'),
    (r'\+\s*15\.0\s*:\s*Last Start - Photo Win', 'Last Start - Photo Win (<0.5L)'),

    # ====== LAST START - PLACED ======
    (r'\+\s*5\.0\s*:\s*Narrow loss.*very competitive', 'Last Start - Narrow Loss (≤1L)'),
    (r'\+\s*3\.0\s*:\s*Close loss \(.*nd.*\)', 'Last Start - Close Loss 2nd (1-2L)'),
    (r'\+\s*3\.0\s*:\s*Close loss \(.*rd.*\)', 'Last Start - Close Loss 3rd (1-2L)'),

    # ====== LAST START - BEATEN ======
    (r'\+\s*0\.0\s*:\s*Competitive effort', 'Last Start - Competitive Effort (≤3L)'),
    (r'-\s*3\.0\s*:\s*Beaten clearly', 'Last Start - Beaten Clearly (3-6L)'),
    (r'-\s*5\.0\s*:\s*Beaten badly.*nd', 'Last Start - Beaten Badly Placed'),
    (r'\+\s*5\.0\s*:\s*Well beaten.*BUT major class drop', 'Last Start - Well Beaten + Class Drop'),
    (r'\+\s*5\.0\s*:\s*Beaten.*dropping in class significantly', 'Last Start - Beaten + Dropping Class'),
    (r'\+\s*0\.0\s*:\s*Beaten clearly.*BUT dropping in class', 'Last Start - Beaten Clearly + Dropping'),
    (r'-\s*7\.0\s*:\s*Well beaten', 'Last Start - Well Beaten (6-10L)'),
    (r'-\s*25\.0\s*:\s*Demolished', 'Last Start - Demolished (10L+)'),
    (r'\+\s*15\.0\s*:\s*Close loss last start', 'Last Start - Close Loss (0.5-2.5L)'),

    # ====== DAYS SINCE RUN ======
    # FIX: new format is "Fresh return - X days since last run (150-199 days, +ROI%)"
    # not "Too fresh (150+ days)" — match on the bracket ranges and also the old format
    (r'\+\s*0\.0\s*:\s*Quick backup', 'Days Since Run - Quick Backup (≤7 days)'),
    (r'[\d.]+\s*days?\s*since last run.*150-199 days|Too fresh.*150', 'Days Since Run - Fresh Return (150-199 days)'),
    (r'[\d.]+\s*days?\s*since last run.*200-249 days|Too fresh.*200', 'Days Since Run - Too Fresh (200+ days)'),
    (r'[\d.]+\s*days?\s*since last run.*250|Too fresh.*250', 'Days Since Run - Too Fresh (250+ days)'),
    (r'[\d.]+\s*days?\s*since last run.*(?:365|year|1\+\s*year)|Too fresh.*over 1 year', 'Days Since Run - Too Fresh (1+ year)'),

    # ====== FORM PRICE ======
    # FIX: old patterns used score magnitude to infer price bracket — unreliable.
    # Match on the price value directly from notes text instead.
    (r'Form price \$(\d+\.\d+)', '_form_price_dynamic'),

    # ====== FIRST UP / SECOND UP ======
    (r'\+\s*0\.0\s*:\s*First-?up winner', 'First Up - Has Won First Up'),
    (r'\+\s*0\.0\s*:\s*Strong first-?up podium', 'First Up - Strong Podium Rate'),
    (r'\+\s*3\.0\s*:\s*Second-?up winner', 'Second Up - Has Won Second Up'),
    (r'\+\s*2\.0\s*:\s*Strong second-?up podium', 'Second Up - Strong Podium Rate'),
    # FIX: old pattern required literal "(UNDEFEATED)" — new format is "(UNDEFEATED: 3:3-0-0)"
    (r'\+\s*15\.0\s*:\s*First-?up specialist.*UNDEFEATED', 'First Up - Specialist Undefeated'),
    (r'\+\s*15\.0\s*:\s*Second-?up specialist.*UNDEFEATED', 'Second Up - Specialist Undefeated'),
    (r'-\s*1\.0\s*:\s*Unclear spell', 'Spell Status - Unclear'),

    # ====== WEIGHT ======
    (r'\+\s*15\.0\s*:\s*Weight.*(?:BELOW|well below) race avg', 'Weight vs Field - Well Below (3kg+)'),
    (r'\+\s*10\.0\s*:\s*Weight.*below race avg', 'Weight vs Field - Below (2-3kg)'),
    (r'\+\s*6\.0\s*:\s*Weight.*below race avg', 'Weight vs Field - Slightly Below (1-2kg)'),
    (r'\+\s*3\.0\s*:\s*Weight.*below race avg', 'Weight vs Field - Marginally Below (0.5-1kg)'),
    (r'0\.0\s*:\s*Weight.*near race avg', 'Weight vs Field - Near Average'),
    (r'-\s*3\.0\s*:\s*Weight.*above race avg', 'Weight vs Field - Marginally Above'),
    (r'-\s*6\.0\s*:\s*Weight.*above race avg', 'Weight vs Field - Above (1-2kg)'),
    (r'-\s*10\.0\s*:\s*Weight.*above race avg', 'Weight vs Field - Well Above (2-3kg)'),
    (r'-\s*15\.0\s*:\s*Weight.*(?:ABOVE|well above) race avg', 'Weight vs Field - Well Above (3kg+)'),
    (r'\+\s*15\.0\s*:\s*Dropped.*from last start', 'Weight Change - Dropped 3kg+'),
    (r'\+\s*10\.0\s*:\s*Dropped.*from last start', 'Weight Change - Dropped 2-3kg'),
    (r'\+\s*5\.0\s*:\s*Dropped.*from last start', 'Weight Change - Dropped 1-2kg'),
    (r'-\s*5\.0\s*:\s*Up.*from last start', 'Weight Change - Up 1-2kg'),
    (r'-\s*10\.0\s*:\s*Up.*from last start', 'Weight Change - Up 2-3kg'),
    (r'-\s*15\.0\s*:\s*Up.*from last start', 'Weight Change - Up 3kg+'),

    # ====== CAREER WIN RATE ======
    (r'(?:\+\s*0\.0\s*:\s*)?Elite career win rate', 'Career Win Rate - Elite 40%+'),
    (r'\+\s*0\.0\s*:\s*Strong career win rate', 'Career Win Rate - Strong 30-40%'),
    (r'-\s*15\.0\s*:\s*Poor career win rate', 'Career Win Rate - Poor <10%'),

    # ====== AGE/SEX - BONUSES ======
    (r'\+\s*15\.0\s*:\s*5yo horse', 'Age/Sex - 5yo Horse (Entire)'),
    (r'\+\s*20\.0\s*:\s*8yo Mare', 'Age/Sex - 8yo Mare'),
    (r'\+\s*3\.0\s*:\s*Prime age \(3yo\)', 'Age/Sex - 3yo'),
    (r'\+\s*0\.0\s*:\s*\(4yo\)', 'Age/Sex - 4yo'),

    # ====== AGE/SEX - MARE PENALTIES ======
    (r'(?:-\s*15\.0\s*:\s*)?5yo Mare', 'Age/Sex - 5yo Mare Penalty'),
    (r'-\s*10\.0\s*:\s*6-7yo Mare', 'Age/Sex - 6-7yo Mare Penalty'),

    # ====== AGE/SEX - OLD AGE PENALTIES ======
    (r'-\s*25\.0\s*:\s*Old age \(7-8yo', 'Age/Sex - 7-8yo Penalty'),
    (r'-\s*35\.0\s*:\s*9yo - ZERO WINS', 'Age/Sex - 9yo Penalty'),
    (r'-\s*40\.0\s*:\s*10yo', 'Age/Sex - 10yo Penalty'),
    (r'-\s*45\.0\s*:\s*11yo', 'Age/Sex - 11yo Penalty'),
    (r'-\s*50\.0\s*:\s*12yo', 'Age/Sex - 12yo Penalty'),
    (r'-\s*60\.0\s*:\s*13\+yo', 'Age/Sex - 13+yo Penalty'),

    # ====== COLT BONUSES ======
    (r'\+\s*30\.0\s*:\s*3yo COLT', 'Colt - 3yo Colt'),
    (r'\+\s*20\.0\s*:\s*COLT base bonus', 'Colt - Base Bonus'),
    (r'\+\s*15\.0\s*:\s*Fast sectional \+ COLT combo', 'Colt - Fast Sectional + Colt'),

    # ====== SIRE SCORING ======
    # NEW: e.g. "+6.0: Sire Night Of Thunder (66.3% ROI, 26 runners)"
    (r'[+-][\d.]+\s*:\s*Sire\s+.+?\(([-\d.]+)%\s*ROI', '_sire_dynamic'),

    # ====== COUNTRY OF ORIGIN ======
    # NEW: e.g. "- 2.0 : Irish-bred (-11.0% ROI, 350 runners)"
    (r':\s*([\w][\w -]*?bred)\s*\(([-+\d.]+)%\s*ROI', '_country_dynamic'),

    # ====== SPECIALIST / PERFECT RECORD ======
    (r'\+\s*15\.0\s*:\s*Specialist - Undefeated Track\+Distance', 'Specialist - Undefeated Track+Distance'),
    (r'\+\s*15\.0\s*:\s*Specialist - Undefeated Distance(?!.*Track)', 'Specialist - Undefeated Distance'),
    (r'(?:\+\s*([\d.]+)\s*:\s*)?UNDEFEATED.*condition.*specialist', 'Specialist - Undefeated Condition'),
    (r'(?:\+\s*([\d.]+)\s*:\s*)?100% PODIUM.*track\+distance', 'Specialist - Perfect Podium Track+Distance'),
    (r'(?:\+\s*([\d.]+)\s*:\s*)?100% PODIUM.*track\b', 'Specialist - Perfect Podium Track'),
    (r'(?:\+\s*([\d.]+)\s*:\s*)?100% PODIUM.*distance', 'Specialist - Perfect Podium Distance'),
    (r'(?:\+\s*([\d.]+)\s*:\s*)?100% PODIUM.*condition', 'Specialist - Perfect Podium Condition'),

    # ====== EXACT TEXT COMPONENTS (DATA PAGE TRACKING) ======
    (r'Ran places:\s*2nd[\s,]+1st[\s,]+1st', 'Ran places: 2nd 1st 1st'),
    (r'100% PODIUM at track \(1/1\) - specialist bonus', '100% PODIUM at track (1/1) - specialist bonus'),
    (r'Ran places:\s*1st[\s,]+1st[\s,]+2nd', 'Ran places: 1st 1st 2nd'),
    (r'Ran places:\s*2nd[\s,]+2nd[\s,]+2nd', 'Ran places: 2nd 2nd 2nd'),
    (r'Ran places:\s*2nd[\s,]+2nd[\s,]+3rd', 'Ran places: 2nd 2nd 3rd'),
    (r'Ran places:\s*2nd[\s,]+1st[\s,]+3rd', 'Ran places: 2nd 1st 3rd'),
    (r'UNDEFEATED at good condition \(1/1\) - specialist bonus', 'UNDEFEATED at good condition (1/1) - specialist bonus'),
    (r'Ran places:\s*1st\s+1st\s+1st', 'Ran places: 1st 1st 1st'),
    (r'5yo Mare', '5yo Mare'),
    (r'Ran places:\s*3rd\s+2nd', 'Ran places: 3rd 2nd'),
    (r'Elite career win rate', 'Elite career win rate'),
    (r'Ran places:\s*1st', 'Ran places: 1st'),
    (r'Ran places:\s*2nd\s+1st', 'Ran places: 2nd 1st'),
    (r'Ran places:\s*2nd\s+2nd', 'Ran places: 2nd 2nd'),
    (r'Ran places:\s*1st\s+3rd', 'Ran places: 1st 3rd'),
    (r'Ran places:\s*2nd[\s,]+3rd[\s,]+2nd', 'Ran places: 2nd 3rd 2nd'),

    # ====== HISTORICAL SECTIONALS (CSV) ======
    # FIX: old pattern required leading + but new format uses +- prefix for negative z-scores
    (r'(\+[\d.]+)\s*:\s*weighted avg \(z=', 'Sectional History - Weighted Avg'),
    (r'(\+[\d.]+)\s*:\s*best of last \d+', 'Sectional History - Best Recent'),
    (r'\+\s*([\d.]+):\s*consistency - excellent', 'Sectional Consistency - Excellent'),
    (r'\+\s*([\d.]+):\s*consistency - good', 'Sectional Consistency - Good'),
    (r'[+\-]?\s*([\d.]+):\s*consistency - fair', 'Sectional Consistency - Fair'),
    (r'[+\-]?\s*([\d.]+):\s*consistency - poor', 'Sectional Consistency - Poor'),

    # ====== API SECTIONALS ======
    (r'[+\-]?\s*[\d.]+:\s*Last 200m \(Rank \d+.*ELITE', 'API Sectional - Last 200m Elite'),
    (r'[+\-]?\s*[\d.]+:\s*Last 200m \(Rank \d+.*VERY GOOD', 'API Sectional - Last 200m Very Good'),
    (r'[+\-]?\s*[\d.]+:\s*Last 200m \(Rank \d+.*\bGOOD\b', 'API Sectional - Last 200m Good'),
    (r'[+\-]?\s*[\d.]+:\s*Last 200m \(Rank \d+(?!.*(?:ELITE|VERY GOOD|GOOD)).*AVERAGE', 'API Sectional - Last 200m Average'),
    (r'[+\-]?\s*[\d.]+:\s*Last 200m \(Rank \d+(?!.*(?:ELITE|VERY GOOD|GOOD|AVERAGE)).*POOR', 'API Sectional - Last 200m Poor'),
    (r'[+\-]?\s*[\d.]+:\s*Last 400m \(Rank \d+.*ELITE', 'API Sectional - Last 400m Elite'),
    (r'[+\-]?\s*[\d.]+:\s*Last 400m \(Rank \d+.*VERY GOOD', 'API Sectional - Last 400m Very Good'),
    (r'[+\-]?\s*[\d.]+:\s*Last 400m \(Rank \d+.*\bGOOD\b', 'API Sectional - Last 400m Good'),
    (r'[+\-]?\s*[\d.]+:\s*Last 400m \(Rank \d+(?!.*(?:ELITE|VERY GOOD|GOOD)).*AVERAGE', 'API Sectional - Last 400m Average'),
    (r'[+\-]?\s*[\d.]+:\s*Last 400m \(Rank \d+(?!.*(?:ELITE|VERY GOOD|GOOD|AVERAGE)).*POOR', 'API Sectional - Last 400m Poor'),
    (r'[+\-]?\s*[\d.]+:\s*Last 600m \(Rank \d+.*ELITE', 'API Sectional - Last 600m Elite'),
    (r'[+\-]?\s*[\d.]+:\s*Last 600m \(Rank \d+.*VERY GOOD', 'API Sectional - Last 600m Very Good'),
    (r'[+\-]?\s*[\d.]+:\s*Last 600m \(Rank \d+.*\bGOOD\b', 'API Sectional - Last 600m Good'),
    (r'\+\s*([\d.]+):\s*IMPROVING TREND', 'API Sectional - Improving Trend'),

    # ====== RUNNING POSITION (SPEEDMAP) ======
    # ====== SPRINT LEADER RUN DOWN BONUS ======
    (r'\+\s*15\.0\s*:\s*Sprint Leader Run Down Bonus', 'Pace Angle - Sprint Leader Run Down'),

    # ====== RUNNING POSITION (SPEEDMAP) ======
    (r'[+\-]?\s*15\.0\s*:\s*LEADER in Sprint', 'Running Position - Leader Sprint'),
    (r'[+\-]?\s*8\.0\s*:\s*ONPACE in Sprint', 'Running Position - OnPace Sprint'),
    (r'[+\-]?\s*0\.0\s*:\s*MIDFIELD in Sprint', 'Running Position - Midfield Sprint'),
    (r'[+\-]?\s*8\.0\s*:\s*BACKMARKER in Sprint', 'Running Position - Backmarker Sprint'),
    (r'[+\-]?\s*15\.0\s*:\s*LEADER in Mile', 'Running Position - Leader Mile'),
    (r'[+\-]?\s*8\.0\s*:\s*ONPACE in Mile', 'Running Position - OnPace Mile'),
    (r'[+\-]?\s*2\.0\s*:\s*MIDFIELD in Mile', 'Running Position - Midfield Mile'),
    (r'[+\-]?\s*5\.0\s*:\s*BACKMARKER in Mile', 'Running Position - Backmarker Mile'),
    (r'[+\-]?\s*5\.0\s*:\s*LEADER in Middle distance', 'Running Position - Leader Middle'),
    (r'[+\-]?\s*5\.0\s*:\s*ONPACE in Middle distance', 'Running Position - OnPace Middle'),
    (r'[+\-]?\s*3\.0\s*:\s*MIDFIELD in Middle distance', 'Running Position - Midfield Middle'),
    (r'[+\-]?\s*0\.0\s*:\s*BACKMARKER in Middle distance', 'Running Position - Backmarker Middle'),
    (r'[+\-]?\s*20\.0\s*:\s*LEADER in Staying', 'Running Position - Leader Staying'),
    (r'[+\-]?\s*0\.0\s*:\s*ONPACE in Staying', 'Running Position - OnPace Staying'),
    (r'[+\-]?\s*0\.0\s*:\s*MIDFIELD in Staying', 'Running Position - Midfield Staying'),
    (r'[+\-]?\s*20\.0\s*:\s*BACKMARKER in Staying', 'Running Position - Backmarker Staying'),

    # ====== HIDDEN EDGE COMBINATION BONUSES ======
    (r'\+\s*[\d.]+\s*:\s*Hidden Edge.*Sprint leader.*last start favoured', 'Hidden Edge - Sprint Leader + Last Start Favoured'),
    (r'\+\s*[\d.]+\s*:\s*Hidden Edge.*Strong condition podium.*last start favourite', 'Hidden Edge - Condition Podium + Last Start Favourite'),

    # ====== INTERSTATE STATE MOVE ======
    (r'([+-]?\s*[\d.]+)\s*:\s*Interstate state move\s*[—-]\s*([A-Z_]+)\s*→\s*([A-Z_]+)([^\n]*)', '_interstate_state_move_dynamic'),

    # ====== PFAI BLEND ======
    (r'PFAI Score:\s*(9[0-9]|100)[\. ]', 'PFAI Score - 90+'),
    (r'PFAI Score:\s*(8[0-9])[\. ]', 'PFAI Score - 80-89'),
    (r'PFAI Score:\s*(7[0-9])[\. ]', 'PFAI Score - 70-79'),
    (r'PFAI Score:\s*(6[0-9])[\. ]', 'PFAI Score - 60-69'),
    (r'PFAI Score:\s*([0-5][0-9])[\. ]', 'PFAI Score - <60'),
    (r'\*\* SIGNALS AGREE', 'Signal Agreement - Both Signals Agree'),

    # ====== MARKET EXPECTATION ======
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(best market performer', 'Market Expectation - Best in Field'),
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(chronic overperformer', 'Market Expectation - Chronic Overperformer'),
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(strong overperformer', 'Market Expectation - Strong Overperformer'),
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(moderate outperformer', 'Market Expectation - Moderate Outperformer'),
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(above field average', 'Market Expectation - Above Average'),
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(worst market performer', 'Market Expectation - Worst in Field'),
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(chronic underperformer', 'Market Expectation - Chronic Underperformer'),
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(significant underperformer', 'Market Expectation - Significant Underperformer'),
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(mild underperformer', 'Market Expectation - Mild Underperformer'),
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(below field average', 'Market Expectation - Below Average'),
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(meeting expectations', 'Market Expectation - Neutral'),
    # FIX: "near field average" maps to Neutral (it's not a named bucket in old patterns)
    (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(near field average', 'Market Expectation - Neutral'),

    # ====== FORM TEMPO ======
    (r'\+\s*20\.0\s*:\s*Very Fast last race',  'Form Tempo - Very Fast (2s+ faster than par)'),
    (r'\+\s*15\.0\s*:\s*Fast last race',       'Form Tempo - Fast (1-2s faster than par)'),
    (r'\+\s*10\.0\s*:\s*Above Par last race',  'Form Tempo - Above Par (0.3-1s faster)'),
    (r'\+\s*5\.0\s*:\s*Par last race',         'Form Tempo - Par (±0.3s)'),
    (r'-\s*5\.0\s*:\s*Below Par last race',    'Form Tempo - Below Par (0.3-1s slower)'),
    (r'-\s*10\.0\s*:\s*Slow last race',        'Form Tempo - Slow (1-2s slower)'),
    (r'-\s*15\.0\s*:\s*Very Slow last race',   'Form Tempo - Very Slow (2s+ slower)'),

]

_COMPILED_NOTES_COMPONENT_PATTERNS = [
    (re.compile(_pattern, re.IGNORECASE | re.DOTALL), _name)
    for _pattern, _name in _NOTES_COMPONENT_PATTERNS
]


def parse_notes_components(notes):
    """
    Parse the notes field to extract individual scoring components.
    Returns a dict of component_name -> score_value
    """
    if not notes:
        return {}

    components = {}

    for pattern, name in _COMPILED_NOTES_COMPONENT_PATTERNS:
        match = pattern.search(notes)
        if match:
            # ---- Dynamic handlers ----
            if name == '_form_price_dynamic':
                try:
                    price = float(match.group(1))
                    if price <= 2.0:
                        components['Form Price - Very Short ($1-$2)'] = price
                    elif price <= 5.0:
                        components['Form Price - Short ($2-$5)'] = price
                    elif price <= 13.0:
                        components['Form Price - Backed ($5-$13)'] = price
                    elif price <= 14.5:
                        components['Form Price - Slight Value ($12-$14)'] = price
                    else:
                        components['Form Price - Outsider ($15+)'] = price
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_sire_dynamic':
                try:
                    roi = float(match.group(1))
                    if roi >= 50:
                        components['Sire - Elite ROI (50%+)'] = roi
                    elif roi >= 20:
                        components['Sire - Strong ROI (20-50%)'] = roi
                    elif roi >= 0:
                        components['Sire - Positive ROI (0-20%)'] = roi
                    else:
                        components['Sire - Negative ROI'] = roi
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_country_dynamic':
                try:
                    country = match.group(1).strip()
                    roi = float(match.group(2))
                    components[f'Country: {country}'] = roi
                except (ValueError, IndexError):
                    pass
                continue
            if name == '_interstate_state_move_dynamic':
                try:
                    score = float(match.group(1).replace(' ', '').replace('+', ''))
                    origin = match.group(2).strip().upper()
                    destination = match.group(3).strip().upper()
                    details = match.group(4) or ''
                    components[f'Interstate State Move - {origin} → {destination}'] = score
                    if 'no matrix sample' in details.lower():
                        components['Interstate State Move - Unknown Interstate'] = score
                    elif origin == destination:
                        components['Interstate State Move - Same State'] = score
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_track_score_dynamic':
                try:
                    val = float(match.group(1))
                    if val >= 8:
                        components['Track Score Total - Strong (8+)'] = val
                    elif val >= 4:
                        components['Track Score Total - Moderate (4-7)'] = val
                    else:
                        components['Track Score Total - Low (0-3)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_td_score_dynamic':
                try:
                    val = float(match.group(1))
                    if val >= 8:
                        components['Track+Distance Score Total - Strong (8+)'] = val
                    elif val >= 4:
                        components['Track+Distance Score Total - Moderate (4-7)'] = val
                    else:
                        components['Track+Distance Score Total - Low (0-3)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_dist_score_dynamic':
                try:
                    val = float(match.group(1))
                    if val >= 8:
                        components['Distance Score Total - Strong (8+)'] = val
                    elif val >= 4:
                        components['Distance Score Total - Moderate (4-7)'] = val
                    else:
                        components['Distance Score Total - Low (0-3)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_cond_score_dynamic':
                try:
                    val = float(match.group(1))
                    if val >= 8:
                        components['Track Condition Score Total - Strong (8+)'] = val
                    elif val >= 4:
                        components['Track Condition Score Total - Moderate (4-7)'] = val
                    else:
                        components['Track Condition Score Total - Low (0-3)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_ran_places_dynamic':
                try:
                    score_group = match.group(1)
                    if score_group:
                        val = float(score_group.replace(' ', '').replace('+', ''))
                    else:
                        places_text = (match.group(2) or '').lower()
                        tokens = re.findall(r'\b(\d+)(?:st|nd|rd|th)\b', places_text)
                        val = 0.0
                        for token in tokens:
                            place_num = int(token)
                            if place_num == 1:
                                val += 3.0
                            elif place_num == 2:
                                val += 2.0
                            elif place_num == 3:
                                val += 1.0
                    if val >= 8:
                        components['Ran Places - Strong (8+)'] = val
                    elif val >= 3:
                        components['Ran Places - Moderate (3-7)'] = val
                    else:
                        components['Ran Places - Low (0-2)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_class_drop_dynamic':
                try:
                    val = float(match.group(1))
                    if val >= 10:
                        components['Class Drop - Large (10+)'] = val
                    else:
                        components['Class Drop - Small (0-9)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_class_rise_dynamic':
                try:
                    val = float(match.group(1))
                    if val <= -10:
                        components['Class Rise - Large (10+)'] = val
                    else:
                        components['Class Rise - Small (0-9)'] = val
                except (ValueError, IndexError):
                    pass
                continue
            # ---- Standard score extraction ----
            try:
                raw_score = match.group(1)
                if raw_score is None:
                    raise ValueError
                score_str = raw_score.replace(' ', '').replace('+', '')
                score = float(score_str)
            except (IndexError, ValueError, AttributeError):
                score = 1.0
            components[name] = score

    return components


_PFAI_ANALYZER_RE = re.compile(
    r'Analyzer Score \(normalized\): ([\d.]+)',
    re.DOTALL
)


def parse_analyzer_score(notes):
    """Pull the 'Analyzer Score (normalized): X' value out of notes text, if present."""
    if not notes:
        return None
    m = _PFAI_ANALYZER_RE.search(notes)
    if m:
        return float(m.group(1))
    return None


SCORING_PREFIXES = (
    'Jockey', 'Trainer', 'Track Win Rate', 'Track Podium',
    'Track+Distance Win', 'Track+Distance Podium', 'Track+Distance Score',
    'Track+Distance -',
    'Distance Win', 'Distance Podium', 'Distance Change', 'Distance -',
    'Distance Score', 'Condition Win', 'Condition Podium', 'Condition -',
    'Class Drop', 'Class Rise', 'Last Start', 'Days Since Run -',
    'Form Price', 'First Up', 'Second Up', 'Weight vs Field',
    'Weight Change', 'Career Win Rate', 'Age/Sex', 'Colt', 'Sire',
    'Specialist', 'Sectional History', 'Sectional Consistency',
    'API Sectional', 'Running Position', 'Hidden Edge', 'PFAI Score',
    'Signal Agreement', 'Interstate State Move',
    'Market Expectation', 'Pace Angle', 'Ran Places', 'Track Score',
    'Track Condition Score',
)

NEGATIVE_COMPONENTS = {
    'Jockey - Poor Value',
    'Trainer - Poor Value',
    'Track - Poor Performance',
    'Distance - Poor Performance',
    'Condition - Poor Performance',
    'Last Start - Beaten Clearly (3-6L)',
    'Last Start - Well Beaten (6-10L)',
    'Last Start - Demolished (10L+)',
    'Last Start - Beaten Badly Placed',
    'Career Win Rate - Poor <10%',
    'Age/Sex - 5yo Mare Penalty',
    'Age/Sex - 6-7yo Mare Penalty',
    'Age/Sex - 7-8yo Penalty',
    'Age/Sex - 9yo Penalty',
    'Age/Sex - 10yo Penalty',
    'Age/Sex - 11yo Penalty',
    'Age/Sex - 12yo Penalty',
    'Age/Sex - 13+yo Penalty',
    'Market Expectation - Worst in Field',
    'Market Expectation - Chronic Underperformer',
    'Market Expectation - Significant Underperformer',
    'Market Expectation - Mild Underperformer',
    'Market Expectation - Below Average',
    'Sire - Negative ROI',
    'Interstate State Move - NSW_ACT → VIC',
    'Interstate State Move - QLD → NSW_ACT',
    'Interstate State Move - SA → VIC',
    'Interstate State Move - NSW_ACT → SA',
    'Interstate State Move - QLD → VIC',
    'Interstate State Move - Unknown Interstate',
}


def is_scoring_component(name):
    return any(name.startswith(p) for p in SCORING_PREFIXES)
