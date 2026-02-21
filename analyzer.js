// Mapping of equivalent jockey names
const jockeyMapping = {
    // Abbreviation → full name as used in tier lists
    "J B Mc Donald": "James McDonald",     // neutral (-9.8% ROI)
    "A Bullock": "Aaron Bullock",           // neutral (-32.8% ROI) - below Tier 4 threshold
    "W Pike": "William Pike",               // neutral (-4.4% ROI)
    "J Parr": "J Parr",                     // Tier 4 - maps to tier list key
    "J R Collett": "J R Collett",           // Tier 4 - maps to tier list key
    "M Zahra": "Mark Zahra",                // neutral (-77.5% ROI, only 10 rides - below threshold)
    "B Shinn": "Blake Shinn",               // neutral
    "C Williams": "Craig Williams",         // Tier 4
    "E Brown": "Ethan Brown",               // Tier 1
    "D Lane": "Damian Lane",                // Tier 4
    "B Melham": "B Melham",                 // Tier 3 - tier list uses "B Melham"
    "T Berry": "Tommy Berry",               // neutral (-9.9% ROI)
    "H Coffey": "H Coffey",                 // neutral - tier list uses "H Coffey" directly
    "B Avdulla": "Brenton Avdulla",         // neutral
    "J Ford": "J Ford",                     // Tier 4 - tier list uses "J Ford"
    "R King": "Rachel King",                // neutral (-43.8% ROI, below threshold)
    "H Bowman": "Hugh Bowman",              // neutral
    "K Mc Evoy": "K Mc Evoy",               // Tier 4 - tier list uses "K Mc Evoy"
    "A Livesey": "Alana Livesey",           // Tier 1
    "T Clark": "Tim Clark",                 // Tier 2
    "L Cartwright": "Luke Cartwright",      // Tier 1
    "T Schiller": "Tyler Schiller",         // Tier 3
    "A Warren": "Alysha Warren",            // neutral (-7.1% ROI)
    "C Tootell": "Caitlin Tootell",         // Tier 2
    "B Thompson": "Ben Thompson",           // neutral (-21.5% ROI)
    "Z Lloyd": "Zac Lloyd",                 // neutral (-19.4% ROI)
    "J McNeil": "Jye McNeil",               // Tier 4
    "R Maloney": "Ryan Maloney",            // Tier 4
    "J Childs": "Jordan Childs",            // Tier 4
    "H Nottle": "Holly Nottle",             // neutral (-41.6% ROI, below threshold)
    "D Gibbons": "Dylan Gibbons",           // Tier 3
    "J Radley": "Jackson Radley",           // Tier 4
    "L Neindorf": "Lachlan Neindorf",       // Tier 4
    "J Allen": "John Allen",                // Tier 4
    "L Bates": "Logan Bates",               // Tier 4
    "C Sutherland": "Corey Sutherland",     // Tier 4
    // Spelling normalization
    "T Nugent": "Theodore Nugent",          // neutral
    "Teodore Nugent": "Theodore Nugent",    // neutral
    "B Allen": "Ben Allen",                 // neutral (-33.0% ROI)
    "R Houston": "Ryan Houston",            // Tier 4
    "K Wilson-Taylor": "Kyle Wilson-Taylor", // Tier 4
    "E Pozman": "Emily Pozman",             // neutral (-47.7% ROI)
    "A Roper": "Anna Roper",                // Tier 4
    "T Stockdale": "Thomas Stockdale",      // Tier 4
    "C Parnham": "Chris Parnham",           // Tier 4
    "R Bayliss": "Regan Bayliss",           // Tier 4
    "A Morgan": "Ashley Morgan",            // Tier 4
    "S Grima": "Siena Grima",               // neutral (-37.5% ROI)
    "C Graham": "Cejay Graham",             // Tier 4
    "T Sherry": "Tom Sherry",               // Tier 4
    "C Hefel": "Carleen Hefel",             // Tier 4
    "K Crowther": "Kayla Crowther",         // Tier 4
    "D Thornton": "Damien Thornton",        // Tier 4
    "B Mertens": "Beau Mertens",            // Tier 4
    "H Watson": "Holly Watson",             // Tier 4
    "W Stanley": "William Stanley",         // Tier 4
    "R Jones": "Reece Jones",               // Tier 4
    // New additions
    "J Williams": "Jai Williams",           // Tier 1 - NEW
    "L Ramoly": "Laqdar Ramoly",            // Tier 3 - NEW
    "N Rawiller": "N Rawiller",             // Tier 4 - tier list uses abbreviation
};
// Mapping of equivalent trainer names
const trainerMapping = {
    // These map to tier list keys exactly
    'C Maher': 'C Maher',                               // Tier 1 - tier list uses 'C Maher'
    'C J Waller': 'C J Waller',                         // neutral - tier list uses 'C J Waller'
    'Ben Will & Jd Hayes': 'Ben Will & Jd Hayes',       // Tier 4 - tier list uses this exact string
    'G Waterhouse & A Bott': 'G Waterhouse & A Bott',   // neutral - tier list uses abbreviation
    'Gai Waterhouse & Adrian Bott': 'G Waterhouse & A Bott', // alternate full name → abbreviation
    'G M Begg': 'G M Begg',                             // Tier 4 - tier list uses abbreviation
    'P Stokes': 'P Stokes',                             // neutral - tier list uses 'P Stokes'
    'M M Laurie': 'M M Laurie',                         // Tier 4 - tier list uses abbreviation
    'K Lees': 'K A Lees',                               // Tier 4 - tier list uses 'K A Lees'
    'K A Lees': 'K A Lees',                             // Tier 4
    'J Cummings': 'J Cummings',                         // neutral
    'J Pride': 'Joseph Pride',                          // Tier 4 - tier list uses full name
    'J O\'Shea': 'John O\'Shea & Tom Charlton',         // Tier 3 - maps to partnership name
    'P Moody': 'P G Moody & Katherine Coleman',         // Tier 3 - maps to partnership name
    'R D Griffiths': 'R D Griffiths',                   // neutral
    'T Busuttin & N Young': 'T Busuttin & N Young',     // Tier 3
    'S W Kendrick': 'S W Kendrick',                     // neutral
    'T & C McEvoy': 'T & C McEvoy',                     // Tier 4
    'A & S Freedman': 'A & S Freedman',                 // Tier 3
    'R L Heathcote': 'R L Heathcote',                   // neutral
    'D T O\'Brien': 'D T O\'Brien',                     // Tier 4
    'Annabel & Rob Archibald': 'Annabel & Rob Archibald', // Tier 4
    'Matthew Smith': 'Matthew Smith',                   // Tier 4
    'Gavin Bedggood': 'Gavin Bedggood',                 // Tier 4
    'Chris & Corey Munce': 'Chris & Corey Munce',       // Tier 4
    'P G Moody & Katherine Coleman': 'P G Moody & Katherine Coleman', // Tier 3
    'T J Gollan': 'T J Gollan',                         // Tier 4
    'Ben Brisbourne': 'Ben Brisbourne',                 // Tier 4
    'G Ryan & S Alexiou': 'G Ryan & S Alexiou',         // Tier 4
    'Peter Snowden': 'Peter Snowden',                   // Tier 4
    'P Snowden': 'Peter Snowden',                       // Tier 4
    'M Price & M Kent Jnr': 'M Price & M Kent Jnr',     // Tier 4
    'Bjorn Baker': 'Bjorn Baker',                       // Tier 4
    'B Baker': 'Bjorn Baker',                           // Tier 4
    'Patrick & Michelle Payne': 'Patrick & Michelle Payne', // Tier 4
    'N D Parnham': 'N D Parnham',                       // Tier 4
    // New additions
    'M W & J Hawkes': 'M W & J Hawkes',                 // Tier 2 - NEW
    'Brett & Georgie Cavanough': 'Brett & Georgie Cavanough', // Tier 2 - NEW
    'Declan Maher': 'Declan Maher',                     // Tier 2 - NEW
    'Richard Litt': 'Richard Litt',                     // Tier 3 - NEW
    'Richard & Will Freedman': 'Richard & Will Freedman', // Tier 3 - NEW
    'L & T Corstens & W Larkin': 'L & T Corstens & W Larkin', // Tier 4 - NEW
    'Annabel & Rob Archibald': 'Annabel & Rob Archibald', // Tier 4 - NEW (big sample)
    'Mitchell Beer & George Carpenter': 'Mitchell Beer & George Carpenter', // Tier 4 - NEW
};
function convertCSV(data) {
    // Normalize line endings (convert CRLF and CR to LF)
    data = data.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

    // Replace semicolons with commas
    data = data.replace(/;/g, ',');

    // Remove extra whitespace around fields (line-level trim)
    data = data.replace(/^\s+|\s+$/gm, '');

    // Handle simple inconsistent quoting
    data = data.replace(/(^"|"$)/gm, ''); // Remove quotes at the start/end of lines
    data = data.replace(/"([^"]*)"/g, '$1'); // Remove quotes around fields

    // NOTE: the following aggressive replacement may break fields that legitimately contain commas.
    // It was in the original code but is generally unsafe. Leave commented unless you specifically need it.
    // data = data.replace(/([^,]+),([^,]+)/g, '"$1,$2"');

    return data;
}

function calculateScore(horseRow, trackCondition, troubleshooting = false, averageFormPrice, sectionalDetails = null) {
    if (troubleshooting) console.log(`Calculating score for horse: ${horseRow['horse name']}`);

    var score = 0;
    var notes = '';
    
    // ==========================================
    // RUNNING POSITION SCORING (Speedmap)
    // (runningPosition injected into CSV in app.py)
    // ==========================================
    const runningPosition = (horseRow['runningposition'] || '').trim();

    // Distance can be "1200" or "1200m" depending on data source
    const raceDistanceRaw = horseRow['distance'] || '';
    const raceDistance = parseInt(String(raceDistanceRaw).replace(/[^\d]/g, ''), 10) || 0;

    const [rpScore, rpNote] = calculateRunningPositionScore(runningPosition, raceDistance);
    score += rpScore;
    notes += rpNote;
    // Check horse weight and score
    var [a, b] = checkWeight(horseRow['horse weight'], horseRow['horse claim']);
    score += a;
    notes += b;

    // Check horse places in last 10 runs
    if (troubleshooting) console.log(`Calculating last 10: ${horseRow['horse last10']}`);
    [a, b] = checkLast10runs(horseRow['horse last10']);
    score += a;
    notes += b;

    // Check if horse jockey is someone we like or not
    [a, b] = checkJockeys(horseRow['horse jockey']);
    score += a;
    notes += b;

    // Check if horse trainer is someone we like or not
    [a, b] = checkTrainers(horseRow['horse trainer']);
    score += a;
    notes += b;

    // Check if horse has won at this track (ENHANCED WEIGHTED SYSTEM)
    [a, b] = checkTrackForm(horseRow['horse record track']);
    score += a * 0.5;
    notes += b;

    // Check if horse has won at this track+distance combo (ENHANCED WEIGHTED SYSTEM)
    [a, b] = checkTrackDistanceForm(horseRow['horse record track distance']);
    score += a * 0.5;
    notes += b;

    // Check if horse has won at this distance (ENHANCED WEIGHTED SYSTEM)
    [a, b] = checkDistanceForm(horseRow['horse record distance']);
    score += a * 0.5;
    notes += b;

    // Check if the last race the horse ran was longer, same or shorter distance
    [a, b] = checkLastDistance(horseRow);
    score += a;
    notes += b;

    // ===== APPLY CONDITION CONTEXT =====
    // This adjusts sectional and condition scoring based on track condition match
    const conditionContext = applyConditionContext(horseRow, trackCondition, sectionalDetails);

    // Check horse form on actual track condition (ENHANCED WEIGHTED SYSTEM + CONTEXT MULTIPLIER)
    const formTrackCondition = 'horse record ' + trackCondition;
    [a, b] = checkTrackConditionForm(horseRow[formTrackCondition], trackCondition);

    // Apply condition multiplier
    const originalConditionScore = a;
    a = a * conditionContext.conditionMultiplier;
    score += a;
    notes += b;

    // Add context note if adjustments were made
    if (conditionContext.note) {
        if (conditionContext.conditionMultiplier !== 1.0) {
            notes += `ℹ️  Condition multiplier: ${originalConditionScore.toFixed(1)} × ${conditionContext.conditionMultiplier.toFixed(1)} = ${a.toFixed(1)}\n`;
        }
        notes += `ℹ️  ${conditionContext.note}\n`;
    }

    // Store the sectional weight for later use
    horseRow._sectionalWeight = conditionContext.sectionalWeight;

    // Calculate total weight advantage for class rise adjustment
    // Weight advantage comes from two sources:
    // 1. Being below race average (already calculated in weight scoring)
    // 2. Weight drop from last start (already calculated in weight scoring)
    // We need to extract these values to pass to compareClasses

    // Parse current weight and race average
    const currentWeight = parseFloat(horseRow['horse weight']);
    const lastWeight = parseFloat(horseRow['form weight']);

    // Calculate weight advantage points (matching the scoring in calculateWeightScores)
    let totalWeightAdvantage = 0;

    // This is a simplified calculation - in production you'd want to get the actual
    // race average, but we can estimate the advantage from the weight difference
    if (!isNaN(currentWeight) && !isNaN(lastWeight)) {
        const weightDrop = lastWeight - currentWeight;

        // Points for weight drop (matching calculateWeightScores logic)
        if (weightDrop >= 3) totalWeightAdvantage += 15;
        else if (weightDrop >= 2) totalWeightAdvantage += 10;
        else if (weightDrop >= 1) totalWeightAdvantage += 5;

        // Points for being below average (estimate: assume 55kg average)
        const estimatedAverage = 55.0;
        const diffFromAvg = estimatedAverage - currentWeight;

        if (diffFromAvg >= 3) totalWeightAdvantage += 15;
        else if (diffFromAvg >= 2) totalWeightAdvantage += 10;
        else if (diffFromAvg >= 1) totalWeightAdvantage += 6;
        else if (diffFromAvg >= 0.5) totalWeightAdvantage += 3;
    }

    // Check horse current and former classes (with weight advantage)
    var [cscore, cnote] = compareClasses(
        horseRow['class restrictions'],
        horseRow['form class'],
        horseRow['race prizemoney'],
        horseRow['prizemoney'],
        totalWeightAdvantage
    );
    score += cscore;
    notes += cnote;

    // Check days since last run
    [a, b] = checkDaysSinceLastRun(horseRow['meeting date'], horseRow['form meeting date']);
    score += a;
    notes += b;

    // Calculate recent form for last start context
    const last10 = String(horseRow['horse last10'] || '');
    let winsBeforeLast = 0;
    let runsBeforeLast = 0;

    // Count wins and runs BEFORE the last start (exclude rightmost character)
    if (last10.length > 1) {
        for (let i = last10.length - 2; i >= 0 && runsBeforeLast < 5; i--) {
            const char = last10[i];
            if (char !== 'X' && char !== 'x') {
                runsBeforeLast++;
                if (char === '1') {
                    winsBeforeLast++;
                }
            }
        }
    }

    const recentWinRate = runsBeforeLast > 0 ? winsBeforeLast / runsBeforeLast : 0;
    const recentFormData = {
        winsBeforeLast: winsBeforeLast,
        runsBeforeLast: runsBeforeLast,
        recentWinRate: recentWinRate
    };

    // Calculate class change (for last start context)
    const todayClassScore = calculateClassScore(horseRow['class restrictions'], horseRow['race prizemoney']);
    const lastClassScore = calculateClassScore(horseRow['form class'], horseRow['prizemoney']);
    const classChange = todayClassScore - lastClassScore; // Negative = dropping in class

    // Check last run margin (with class drop context)
    [a, b] = checkMargin(horseRow['form position'], horseRow['form margin'], classChange, recentFormData);
    score += a;
    notes += b;

    // Build specialist context for SP profile adjustment
    const specialistContext = {
        hasStrongConditionRecord: false,
        hasStrongTrackRecord: false,
        hasStrongDistanceRecord: false,
        hasPerfectRecord: false,
        isRecentConditionWinner: false,
        isClassDropperWithSpeed: false
    };

    // Check condition record
    const conditionField = 'horse record ' + trackCondition;
    const conditionRecord = horseRow[conditionField];
    if (conditionRecord && typeof conditionRecord === 'string') {
        const numbers = conditionRecord.split(/[:\-]/).map(s => Number(s.trim()));
        if (numbers.length === 4) {
            const [runs, wins, seconds, thirds] = numbers;
            const podiums = wins + seconds + thirds;
            const podiumRate = runs > 0 ? podiums / runs : 0;

            // Strong condition record = ≥50% podium with good confidence (N≥5)
            if (runs >= 5 && podiumRate >= 0.50) {
                specialistContext.hasStrongConditionRecord = true;
            }

            // Perfect record
            if (runs > 0 && (wins === runs || podiums === runs)) {
                specialistContext.hasPerfectRecord = true;
            }
        }
    }

    // Check track record
    const trackRecord = horseRow['horse record track'];
    if (trackRecord && typeof trackRecord === 'string') {
        const numbers = trackRecord.split(/[:\-]/).map(s => Number(s.trim()));
        if (numbers.length === 4) {
            const [runs, wins, seconds, thirds] = numbers;
            const podiums = wins + seconds + thirds;
            const podiumRate = runs > 0 ? podiums / runs : 0;

            if (runs >= 5 && podiumRate >= 0.50) {
                specialistContext.hasStrongTrackRecord = true;
            }
            if (runs > 0 && (wins === runs || podiums === runs)) {
                specialistContext.hasPerfectRecord = true;
            }
        }
    }

    // Check distance record
    const distanceRecord = horseRow['horse record distance'];
    if (distanceRecord && typeof distanceRecord === 'string') {
        const numbers = distanceRecord.split(/[:\-]/).map(s => Number(s.trim()));
        if (numbers.length === 4) {
            const [runs, wins, seconds, thirds] = numbers;
            const podiums = wins + seconds + thirds;
            const podiumRate = runs > 0 ? podiums / runs : 0;

            if (runs >= 5 && podiumRate >= 0.50) {
                specialistContext.hasStrongDistanceRecord = true;
            }
            if (runs > 0 && (wins === runs || podiums === runs)) {
                specialistContext.hasPerfectRecord = true;
            }
        }
    }

    // Check if recent condition winner
    const formPosition = parseInt(horseRow['form position']);
    const formCondition = String(horseRow['form track condition'] || '').toLowerCase();
    const todayCondition = trackCondition.toLowerCase();
    if (formPosition === 1 && formCondition === todayCondition) {
        specialistContext.isRecentConditionWinner = true;
    }

    // Check if class dropper with speed
    if (classChange < -10 && sectionalDetails) {
        const bestSectionalZ = sectionalDetails.bestRecent ? Math.abs(sectionalDetails.bestRecent / 15) : 0;
        if (bestSectionalZ > 0.8) {
            specialistContext.isClassDropperWithSpeed = true;
        }
    }

    // Check form price (with specialist context)
[a, b] = checkFormPrice(averageFormPrice, specialistContext);
score += a;
notes += b;

// Check first up / second up specialist
[a, b] = checkFirstUpSecondUp(horseRow);
score += a;
notes += b;

// ==========================================
// AGE/SEX SCORING - UPDATED 2025-01-30
// Based on 1203 race analysis
// ==========================================

const horseAge = parseInt(horseRow['horse age']);
const horseSex = String(horseRow['horse sex'] || '').trim();

if (!isNaN(horseAge)) {
    
    // === MAJOR EDGE: 5YO HORSES (ENTIRE MALES ONLY) ===
    // 164% ROI, 40% SR - HUGE DISCOVERY (geldings destroy value at -61.5% ROI)
    if (horseAge === 5 && horseSex === 'Horse') {
    score += 25;
    notes += '+25.0 : 5yo horse (164% ROI, 40% SR - elite age)\n';
    }
    
    // === MAJOR EDGE: 8YO MARES ===
    // 118% ROI, 7.7% SR - Specialist pattern
    if (horseAge === 8 && horseSex === 'Mare') {
        score += 20;
        notes += '+20.0 : 8yo Mare (118% ROI - specialist)\n';
    }
    
    // === STANDARD AGE BONUSES (REDUCED) ===
    // 3yo: Only -4.6% ROI - reduced from +5 to +3
    if (horseAge === 3) {
        score += 3;
        notes += '+ 3.0 : Prime age (3yo)\n';
    }
    
    // 4yo: -26.6% ROI - reduced from +3 to +2
    if (horseAge === 4) {
        score += 0;
        notes += '+ 0.0 : (4yo)\n';
    }
    
    // === NEW: MARE AGE PENALTIES ===
    
    // 5yo Mares: -51.3% ROI - MAJOR VALUE DESTROYER
    if (horseAge === 5 && horseSex === 'Mare') {
        score -= 15;
        notes += '-15.0 : 5yo Mare (-51.3% ROI)\n';
    }
    
    // 6-7yo Mares: -33% to -69% ROI
    if ((horseAge === 6 || horseAge === 7) && horseSex === 'Mare') {
        score -= 10;
        notes += '-10.0 : 6-7yo Mare (consistent value destroyer)\n';
    }
    
    // === OLD AGE PENALTY (INCREASED) ===
    // 7+: -40.2% ROI confirmed - increased from -20 to -25
    if (horseAge >= 7 && horseAge < 9) {
        score -= 25;
        notes += '-25.0 : Old age (7-8yo, 4.5% SR, -40.2% ROI)\n';
    }
    
    // === EXTREME AGE PENALTIES - ZERO WINNERS ===
    
    // 9yo: 17 runners, 0 wins, -100% ROI
    if (horseAge === 9) {
        score -= 35;
        notes += '-35.0 : 9yo - ZERO WINS from 17 runners (-100% ROI)\n';
    }
    
    // 10yo: 4 runners, 0 wins, -100% ROI  
    if (horseAge === 10) {
        score -= 40;
        notes += '-40.0 : 10yo - ZERO WINS from 4 runners (-100% ROI)\n';
    }
    
    // 11yo: 10 runners, 0 wins, -100% ROI
    if (horseAge === 11) {
        score -= 45;
        notes += '-45.0 : 11yo - ZERO WINS from 10 runners (-100% ROI)\n';
    }
    
    // 12yo: 6 runners, 0 wins, -100% ROI
    if (horseAge === 12) {
        score -= 50;
        notes += '-50.0 : 12yo - ZERO WINS from 6 runners (-100% ROI)\n';
    }
    
    // 13+yo: Extremely rare, assume disaster
    if (horseAge >= 13) {
        score -= 60;
        notes += '-60.0 : 13+yo - Ancient, virtually no chance\n';
    }
}
    
    // ==========================================
// SIRE BONUSES/PENALTIES - UPDATED 2025-01-30
// Based on 1203 race analysis
// ==========================================
const sire = String(horseRow['horse sire'] || '').trim();

const sireData = {
    // ELITE PERFORMERS (100%+ ROI)
    'Wootton Bassett': { runners: 65, roi: 284.4, strikeRate: 20.0 },
    'Rommel': { runners: 44, roi: 141.3, strikeRate: 20.5 },
    'The Mission': { runners: 24, roi: 113.8, strikeRate: 25.0 },
    'Night Of Thunder': { runners: 21, roi: 106.0, strikeRate: 38.1 },
    'Gold Standard': { runners: 32, roi: 288.8, strikeRate: 18.8 },    // NEW - was neutral before
    'Love Conquers All': { runners: 32, roi: 168.8, strikeRate: 15.6 }, // NEW
    'Russian Camelot': { runners: 21, roi: 124.5, strikeRate: 28.6 },   // NEW
    'Fighting Sun': { runners: 31, roi: 88.7, strikeRate: 6.5 },        // NEW - just under 100
    'I\'m All The Talk': { runners: 35, roi: 94.9, strikeRate: 17.1 },

    // STRONG VALUE (50-100% ROI)
    'Hallowed Crown': { runners: 39, roi: 56.4, strikeRate: 2.6 },      // NEW
    'Adelaide': { runners: 50, roi: 52.4, strikeRate: 6.0 },            // REVERSED - was disaster before!
    'Heroic Valour': { runners: 60, roi: 40.5, strikeRate: 8.3 },       // REVERSED - was disaster before!
    'Proisir': { runners: 45, roi: 45.4, strikeRate: 28.9 },
    'Hello Youmzain': { runners: 26, roi: 44.2, strikeRate: 23.1 },     // NEW
    'Tagaloa': { runners: 38, roi: 43.9, strikeRate: 18.4 },
    'Pierata': { runners: 98, roi: 34.0, strikeRate: 15.3 },
    'Star Turn': { runners: 119, roi: 31.8, strikeRate: 13.4 },
    'Contributer': { runners: 39, roi: 30.3, strikeRate: 20.5 },        // was neutral before
    'Starspangledbanner': { runners: 69, roi: 28.0, strikeRate: 7.2 },
    'Doubtland': { runners: 27, roi: 35.2, strikeRate: 14.8 },          // REVERSED - was disaster before!
    'Awesome Rock': { runners: 48, roi: 22.1, strikeRate: 12.5 },       // REVERSED - was moderate negative!
    'Shalaa': { runners: 124, roi: 24.0, strikeRate: 9.7 },             // REVERSED - was disaster before!

    // GOOD VALUE (10-30% ROI)
    'Shooting To Win': { runners: 90, roi: 16.0, strikeRate: 5.6 },     // REVERSED - was disaster before!
    'Reward For Effort': { runners: 53, roi: 14.0, strikeRate: 15.1 },
    'Stratum Star': { runners: 48, roi: 13.8, strikeRate: 16.7 },
    'Cable Bay': { runners: 68, roi: 12.5, strikeRate: 11.8 },          // was slight negative before
    'Sooboog': { runners: 55, roi: 12.2, strikeRate: 12.7 },            // NEW
    'Shocking': { runners: 20, roi: 12.0, strikeRate: 20.0 },
    'Lonhro': { runners: 81, roi: 11.9, strikeRate: 17.3 },
    'Alabama Express': { runners: 67, roi: 8.1, strikeRate: 19.4 },     // was slight negative before
    'Kobayashi': { runners: 46, roi: 8.0, strikeRate: 17.4 },           // was moderate negative before
    'Trapeze Artist': { runners: 124, roi: 4.7, strikeRate: 13.7 },
    'Territories': { runners: 76, roi: 4.3, strikeRate: 13.2 },

    // MARKET EFFICIENT (-10 to +5% ROI) - no score adjustment
    'Churchill': { runners: 112, roi: 0.8, strikeRate: 16.1 },
    'Tassort': { runners: 65, roi: 0.5, strikeRate: 20.0 },
    'Overshare': { runners: 65, roi: 0.2, strikeRate: 15.4 },
    'Russian Revolution': { runners: 159, roi: -1.9, strikeRate: 12.6 },
    'Showtime': { runners: 55, roi: -2.2, strikeRate: 14.5 },
    'Rothesay': { runners: 63, roi: -3.8, strikeRate: 15.9 },
    'Brave Smash': { runners: 58, roi: -4.5, strikeRate: 13.8 },
    'Safeguard': { runners: 32, roi: -4.7, strikeRate: 12.5 },
    'Snitzel': { runners: 223, roi: -4.8, strikeRate: 11.7 },
    'Tosen Stardom': { runners: 56, roi: -6.3, strikeRate: 7.1 },
    'Extreme Choice': { runners: 52, roi: -7.1, strikeRate: 13.5 },
    'Magnus': { runners: 112, roi: -7.4, strikeRate: 13.4 },
    'Frosted': { runners: 124, roi: -8.1, strikeRate: 11.3 },
    'Under The Louvre': { runners: 39, roi: -9.4, strikeRate: 17.9 },

    // SLIGHT NEGATIVE (-10 to -30% ROI)
    'Bull Point': { runners: 28, roi: -10.0, strikeRate: 14.3 },
    'Capitalist': { runners: 250, roi: -10.3, strikeRate: 13.6 },
    'Divine Prophet': { runners: 90, roi: -11.5, strikeRate: 21.1 },
    'Hellbent': { runners: 181, roi: -15.3, strikeRate: 12.7 },
    'Tivaci': { runners: 57, roi: -16.1, strikeRate: 10.5 },
    'Super Seth': { runners: 41, roi: -16.7, strikeRate: 22.0 },       // dropped from good value
    'Justify': { runners: 94, roi: -19.6, strikeRate: 14.9 },
    'Savabeel': { runners: 109, roi: -19.6, strikeRate: 11.9 },
    'Epaulette': { runners: 87, roi: -21.8, strikeRate: 10.3 },        // dropped from moderate value
    'Sessions': { runners: 74, roi: -22.0, strikeRate: 13.5 },
    'Exceed And Excel': { runners: 104, roi: -22.3, strikeRate: 12.5 },
    'Pierro': { runners: 183, roi: -23.0, strikeRate: 13.1 },
    'D\'argento': { runners: 61, roi: -23.8, strikeRate: 14.8 },
    'Smart Missile': { runners: 95, roi: -23.8, strikeRate: 9.5 },
    'Grunt': { runners: 72, roi: -24.4, strikeRate: 16.7 },
    'Castelvecchio': { runners: 65, roi: -25.1, strikeRate: 16.9 },
    'War Chant': { runners: 40, roi: -26.3, strikeRate: 7.5 },
    'All Too Hard': { runners: 137, roi: -27.4, strikeRate: 10.9 },
    'Fastnet Rock': { runners: 68, roi: -27.7, strikeRate: 14.7 },
    'Calyx': { runners: 45, roi: -27.8, strikeRate: 15.6 },
    'Blue Point': { runners: 102, roi: -28.4, strikeRate: 10.8 },
    'Yes Yes Yes': { runners: 84, roi: -28.9, strikeRate: 16.7 },
    'Winning Rupert': { runners: 62, roi: -29.0, strikeRate: 8.1 },
    'Palentino': { runners: 56, roi: -29.4, strikeRate: 8.9 },
    'Written Tycoon': { runners: 221, roi: -29.7, strikeRate: 13.1 },

    // MODERATE NEGATIVE (-30 to -50% ROI)
    'Astern': { runners: 71, roi: -30.4, strikeRate: 9.9 },
    'Saxon Warrior': { runners: 66, roi: -30.5, strikeRate: 16.7 },
    'Too Darn Hot': { runners: 105, roi: -30.7, strikeRate: 11.4 },
    'I Am Invincible': { runners: 203, roi: -32.3, strikeRate: 12.3 },
    'Highland Reel': { runners: 76, roi: -32.3, strikeRate: 9.2 },
    'I Am Immortal': { runners: 45, roi: -32.4, strikeRate: 13.3 },
    'Toronado': { runners: 283, roi: -32.5, strikeRate: 12.4 },
    'Sir Prancealot': { runners: 81, roi: -32.5, strikeRate: 6.2 },
    'Sidestep': { runners: 39, roi: -33.1, strikeRate: 7.7 },
    'Prized Icon': { runners: 43, roi: -34.9, strikeRate: 7.0 },
    'Press Statement': { runners: 87, roi: -35.1, strikeRate: 11.5 },
    'Rich Enuff': { runners: 76, roi: -35.3, strikeRate: 13.2 },
    'Microphone': { runners: 41, roi: -35.3, strikeRate: 9.8 },
    'Playing God': { runners: 103, roi: -35.3, strikeRate: 11.7 },
    'Exceedance': { runners: 56, roi: -35.7, strikeRate: 10.7 },
    'Puissance De Lune': { runners: 82, roi: -36.1, strikeRate: 9.8 },
    'Tarzino': { runners: 49, roi: -36.7, strikeRate: 6.1 },
    'Ilovethiscity': { runners: 44, roi: -37.0, strikeRate: 9.1 },
    'The Autumn Sun': { runners: 115, roi: -37.3, strikeRate: 12.2 },
    'Merchant Navy': { runners: 84, roi: -37.6, strikeRate: 8.3 },
    'Bivouac': { runners: 60, roi: -38.8, strikeRate: 18.3 },
    'Flying Artie': { runners: 143, roi: -40.7, strikeRate: 9.8 },
    'Xtravagant': { runners: 62, roi: -41.1, strikeRate: 14.5 },
    'Farnan': { runners: 64, roi: -42.4, strikeRate: 17.2 },
    'Cosmic Force': { runners: 56, roi: -43.4, strikeRate: 16.1 },
    'Harry Angel': { runners: 121, roi: -43.5, strikeRate: 13.2 },
    'Zoustar': { runners: 269, roi: -44.9, strikeRate: 10.0 },
    'Turffontein': { runners: 40, roi: -45.0, strikeRate: 7.5 },
    'Written By': { runners: 76, roi: -45.0, strikeRate: 11.8 },
    'Exosphere': { runners: 45, roi: -46.4, strikeRate: 6.7 },
    'Frankel': { runners: 38, roi: -46.6, strikeRate: 7.9 },
    'Spirit Of Boom': { runners: 137, roi: -47.9, strikeRate: 8.8 },
    'Dubious': { runners: 53, roi: -47.9, strikeRate: 11.3 },
    'Demerit': { runners: 25, roi: -48.0, strikeRate: 4.0 },
    'Lean Mean Machine': { runners: 41, roi: -48.0, strikeRate: 14.6 },
    'Kermadec': { runners: 42, roi: -48.5, strikeRate: 11.9 },
    'Dundeel': { runners: 211, roi: -48.6, strikeRate: 9.0 },
    'Deep Field': { runners: 165, roi: -49.1, strikeRate: 12.1 },
    'Better Than Ready': { runners: 166, roi: -49.4, strikeRate: 9.6 },

    // SEVERE NEGATIVE (-50 to -70% ROI)
    'Per Incanto': { runners: 63, roi: -51.5, strikeRate: 12.7 },
    'Pride Of Dubai': { runners: 108, roi: -51.7, strikeRate: 8.3 },
    'Street Boss': { runners: 90, roi: -51.7, strikeRate: 11.1 },
    'Supido': { runners: 71, roi: -51.8, strikeRate: 9.9 },
    'Maurice': { runners: 93, roi: -52.5, strikeRate: 10.8 },
    'So You Think': { runners: 230, roi: -52.5, strikeRate: 9.1 },
    'Denman': { runners: 48, roi: -52.9, strikeRate: 8.3 },
    'Shamus Award': { runners: 133, roi: -53.1, strikeRate: 13.5 },
    'Maschino': { runners: 72, roi: -54.8, strikeRate: 9.7 },
    'Vadamos': { runners: 33, roi: -54.8, strikeRate: 12.1 },
    'Headwater': { runners: 118, roi: -56.7, strikeRate: 8.5 },
    'Foxwedge': { runners: 74, roi: -56.9, strikeRate: 8.1 },
    'Ace High': { runners: 30, roi: -57.0, strikeRate: 6.7 },
    'Fiorente': { runners: 85, roi: -57.2, strikeRate: 11.8 },
    'Almanzor': { runners: 93, roi: -57.9, strikeRate: 11.8 },
    'Brazen Beau': { runners: 65, roi: -57.9, strikeRate: 12.3 },
    'Hanseatic': { runners: 39, roi: -57.9, strikeRate: 10.3 },
    'Vancouver': { runners: 57, roi: -59.3, strikeRate: 7.0 },
    'Nicconi': { runners: 103, roi: -61.3, strikeRate: 3.9 },
    'Invader': { runners: 70, roi: -61.4, strikeRate: 5.7 },
    'Anders': { runners: 39, roi: -62.6, strikeRate: 10.3 },
    'Tavistock': { runners: 38, roi: -62.9, strikeRate: 5.3 },
    'Brutal': { runners: 77, roi: -63.2, strikeRate: 10.4 },
    'American Pharoah': { runners: 63, roi: -63.4, strikeRate: 6.3 },
    'Spieth': { runners: 46, roi: -64.8, strikeRate: 8.7 },
    'Needs Further': { runners: 63, roi: -66.7, strikeRate: 12.7 },
    'Universal Ruler': { runners: 73, roi: -69.2, strikeRate: 2.7 },
    'Holler': { runners: 27, roi: -69.6, strikeRate: 7.4 },

    // DISASTER SIRES (-70%+ ROI)
    'Rubick': { runners: 111, roi: -70.0, strikeRate: 4.5 },
    'Star Witness': { runners: 74, roi: -71.4, strikeRate: 5.4 },
    'Alpine Eagle': { runners: 84, roi: -71.4, strikeRate: 6.0 },
    'Royal Meeting': { runners: 45, roi: -71.8, strikeRate: 6.7 },
    'Magna Grecia': { runners: 44, roi: -72.5, strikeRate: 6.8 },
    'Snippetson': { runners: 50, roi: -75.6, strikeRate: 4.0 },
    'Impending': { runners: 130, roi: -75.9, strikeRate: 4.6 },
    'Casino Prince': { runners: 73, roi: -76.6, strikeRate: 6.8 },
    'Pariah': { runners: 124, roi: -77.9, strikeRate: 4.0 },
    'Zousain': { runners: 104, roi: -77.9, strikeRate: 9.6 },
    'King\'s Legacy': { runners: 35, roi: -78.6, strikeRate: 5.7 },
    'Dissident': { runners: 45, roi: -80.0, strikeRate: 2.2 },
    'Ardrossan': { runners: 26, roi: -80.2, strikeRate: 7.7 },
    'North Pacific': { runners: 38, roi: -80.3, strikeRate: 5.3 },
    'Wandjina': { runners: 42, roi: -80.7, strikeRate: 4.8 },
    'Nostradamus': { runners: 32, roi: -81.3, strikeRate: 3.1 },
    'Turn Me Loose': { runners: 33, roi: -81.8, strikeRate: 3.0 },
    'Outreach': { runners: 57, roi: -84.2, strikeRate: 1.8 },
    'Super One': { runners: 86, roi: -87.4, strikeRate: 2.3 },
    'God Has Spoken': { runners: 22, roi: -87.7, strikeRate: 4.5 },
    'Your Song': { runners: 41, roi: -87.8, strikeRate: 2.4 },
    'Bel Esprit': { runners: 43, roi: -90.7, strikeRate: 2.3 },
    'Encryption': { runners: 43, roi: -91.6, strikeRate: 2.3 },
    'Lope De Vega': { runners: 30, roi: -93.3, strikeRate: 3.3 },
    // ZERO WINNERS (-100% ROI)
    'Reliable Man': { runners: 49, roi: -100.0, strikeRate: 0.0 },
    'Squamosa': { runners: 46, roi: -100.0, strikeRate: 0.0 },
    'Gingerbread Man': { runners: 36, roi: -100.0, strikeRate: 0.0 },
    'Power': { runners: 30, roi: -100.0, strikeRate: 0.0 },
    'Strasbourg': { runners: 29, roi: -100.0, strikeRate: 0.0 },
    'Prince Of Caviar': { runners: 25, roi: -100.0, strikeRate: 0.0 },
    'Menari': { runners: 25, roi: -100.0, strikeRate: 0.0 },
    'Iffraaj': { runners: 24, roi: -100.0, strikeRate: 0.0 },
    'Rebel Raider': { runners: 22, roi: -100.0, strikeRate: 0.0 },
    'Sizzling': { runners: 21, roi: -100.0, strikeRate: 0.0 },
    'Choisir': { runners: 20, roi: -100.0, strikeRate: 0.0 },
    'Scissor Kick': { runners: 20, roi: -100.0, strikeRate: 0.0 },
    'More Than Ready': { runners: 19, roi: -100.0, strikeRate: 0.0 },
    'Hualalai': { runners: 19, roi: -100.0, strikeRate: 0.0 },
    'Caravaggio': { runners: 19, roi: -100.0, strikeRate: 0.0 },
    'Ambidexter': { runners: 19, roi: -100.0, strikeRate: 0.0 },
    'Americain': { runners: 16, roi: -100.0, strikeRate: 0.0 },
    'Mendelssohn': { runners: 16, roi: -100.0, strikeRate: 0.0 },
    'Barbados': { runners: 16, roi: -100.0, strikeRate: 0.0 },
    'Real Impact': { runners: 16, roi: -100.0, strikeRate: 0.0 },
    'Sepoy': { runners: 18, roi: -100.0, strikeRate: 0.0 },
    'Camelot': { runners: 17, roi: -100.0, strikeRate: 0.0 },
    'Al Maher': { runners: 14, roi: -100.0, strikeRate: 0.0 },
    'Zebedee': { runners: 14, roi: -100.0, strikeRate: 0.0 },
    'Whittington': { runners: 14, roi: -100.0, strikeRate: 0.0 },
    'Jukebox': { runners: 14, roi: -100.0, strikeRate: 0.0 },
    'Dandino': { runners: 12, roi: -100.0, strikeRate: 0.0 },
    'Bolt D\'oro': { runners: 13, roi: -100.0, strikeRate: 0.0 },
    'Mongolian Khan': { runners: 13, roi: -100.0, strikeRate: 0.0 },
    'My Admiration': { runners: 13, roi: -100.0, strikeRate: 0.0 },
    'Leonardo Da Hinchi': { runners: 13, roi: -100.0, strikeRate: 0.0 },
    'War Decree': { runners: 13, roi: -100.0, strikeRate: 0.0 },
    'Wanted': { runners: 13, roi: -100.0, strikeRate: 0.0 },
    'Zed': { runners: 13, roi: -100.0, strikeRate: 0.0 },
    'Danerich': { runners: 10, roi: -100.0, strikeRate: 0.0 },
    'Bradbury\'s Luck': { runners: 10, roi: -100.0, strikeRate: 0.0 },
    'Choistar': { runners: 10, roi: -100.0, strikeRate: 0.0 },
    'Delago Deluxe': { runners: 10, roi: -100.0, strikeRate: 0.0 },
    'Australia': { runners: 11, roi: -100.0, strikeRate: 0.0 },
    'Wolf Cry': { runners: 11, roi: -100.0, strikeRate: 0.0 },
    'The Factor': { runners: 11, roi: -100.0, strikeRate: 0.0 },
    'Golden Archer': { runners: 12, roi: -100.0, strikeRate: 0.0 },
    'Real Steel': { runners: 15, roi: -100.0, strikeRate: 0.0 },
    'D J McCarthy': { runners: 15, roi: -100.0, strikeRate: 0.0 },
    'Ms S Royes': { runners: 12, roi: -100.0, strikeRate: 0.0 },
    'Wayed Zain': { runners: 12, roi: -100.0, strikeRate: 0.0 },
    'Ms S Trolove': { runners: 16, roi: -100.0, strikeRate: 0.0 },
};

// Calculate sire score - UPDATED THRESHOLDS
const data = sireData[sire];
if (data) {
    const { runners, roi, strikeRate } = data;
    let sireScore = 0;
    
    // Base ROI scoring - UPDATED 2025-01-30
    if (roi >= 100) sireScore = 15;        // Elite tier (Wootton Bassett, Rommel)
    else if (roi >= 50) sireScore = 12;    // Super elite
    else if (roi >= 30) sireScore = 10;    // Elite
    else if (roi >= 20) sireScore = 8;     // Strong
    else if (roi >= 10) sireScore = 6;     // Good
    else if (roi >= 5) sireScore = 3;      // Moderate
    else if (roi >= -5) sireScore = 0;     // Neutral
    else if (roi >= -20) sireScore = -2;   // Slight negative
    else if (roi >= -40) sireScore = -5;   // Bad
    else if (roi >= -60) sireScore = -10;  // Severe
    else sireScore = -15;                  // Disaster
    
    // Strike rate penalty for chronic underperformers
    if (strikeRate < 5 && runners >= 40) {
        sireScore -= 3;
    }
    
    // Sample size weighting
    if (runners >= 80) {
        // Full weight
    } else if (runners >= 50) {
        sireScore *= 0.9;
    } else if (runners >= 30) {
        sireScore *= 0.75;
    } else {
        sireScore *= 0.5;
    }
    
    sireScore = Math.round(sireScore);
    
    if (sireScore !== 0) {
        score += sireScore;
        notes += `${sireScore > 0 ? '+' : ''}${sireScore}.0: Sire ${sire} (${roi.toFixed(1)}% ROI, ${runners} runners)\n`;
    }
}
    // NEW: CAREER WIN RATE SCORING
const careerRecord = horseRow['horse record'];
if (careerRecord && typeof careerRecord === 'string') {
    const numbers = careerRecord.split(/[:\-]/).map(s => Number(s.trim()));
    if (numbers.length === 4) {
        const [careerStarts, careerWins] = numbers;
        if (careerStarts >= 5) {
            const careerWinPct = (careerWins / careerStarts) * 100;
            if (careerWinPct >= 40) {
                score += 0;
                notes += '+0.0 : Elite career win rate (40%+, 30.8% SR)\n';
            } else if (careerWinPct >= 30) {
                score += 0;
                notes += '+ 0.0 : Strong career win rate (30-40%)\n';
            } else if (careerWinPct < 10) {
                score -= 15;
                notes += '-15.0 : Poor career win rate (<10%)\n';
            }
        }
    }
}
    // CLOSE LOSS BONUS - UPDATED 2025-01-06 (increased from 5 to 7, extended to 2.5L)
const lastMargin = parseFloat(horseRow['form margin']);
const lastPosition = parseInt(horseRow['form position']);
if (!isNaN(lastMargin) && !isNaN(lastPosition) && lastPosition > 1 && lastMargin > 0 && lastMargin <= 2.5) {
    score += 0;  // INCREASED from 5
    notes += '+0.0: Close loss last start (0.5-2.5L) - not competitive\n';
}
    // === COLT BONUS SYSTEM (MUTUALLY EXCLUSIVE) ===
if (horseSex === 'Colt') {
    const sectionalMatch = String(horseRow['sectional'] || '').match(/(\d+\.?\d*)sec/);
    const rawSectional = sectionalMatch ? parseFloat(sectionalMatch[1]) : null;
    
    // Priority 1: Fast sectional Colt (most valuable)
    if (rawSectional && rawSectional < 34) {
        score += 15;
        notes += '+15.0 : Fast sectional + COLT combo (44.4% SR, +33% ROI)\n';
    }
    // Priority 2: 3yo Colt (age advantage)
    else if (horseAge === 3) {
        score += 20;
        notes += '+20.0 : 3yo COLT combo (44.4% SR, +33% ROI)\n';
    }
    // Priority 3: Base Colt bonus
    else {
        score += 20;
        notes += '+20.0 : COLT (82.7% ROI)\n';
    }
}

    return [score, notes]; // Return the score and notes
}

function checkWeight(weight, claim) {
    // Weight scoring is now handled by calculateWeightScores() which compares to race average
    // This function is kept for compatibility but returns 0
    return [0, ''];
}
function checkLastDistance(horseRow) {
    let addScore = 0;
    let note = '';
    
    // Parse current race distance and last race distance
    const currentDistance = parseInt(horseRow['distance'] || horseRow['race distance'], 10);
    const lastDistance = parseInt(horseRow['form distance'], 10);
    
    // Validate inputs
    if (isNaN(currentDistance) || isNaN(lastDistance)) {
        return [0, ''];
    }
    
    const distanceChange = currentDistance - lastDistance;
    
    if (distanceChange > 400) {
        // Stepping up significantly in distance
        addScore = -5;
        note += `- 5.0 : Stepping up ${distanceChange}m in distance (${lastDistance}m → ${currentDistance}m)\n`;
    } else if (distanceChange > 200) {
        // Moderate step up
        addScore = -3;
        note += `- 3.0 : Stepping up ${distanceChange}m in distance (${lastDistance}m → ${currentDistance}m)\n`;
    } else if (distanceChange < -400) {
        // Dropping back significantly in distance
        addScore = 3;
        note += `+ 3.0 : Dropping back ${Math.abs(distanceChange)}m in distance (${lastDistance}m → ${currentDistance}m)\n`;
    } else if (distanceChange < -200) {
        // Moderate drop back
        addScore = 2;
        note += `+ 2.0 : Dropping back ${Math.abs(distanceChange)}m in distance (${lastDistance}m → ${currentDistance}m)\n`;
    }
    // Else: similar distance (-200 to +200), no bonus/penalty
    
    return [addScore, note];
}
function checkLast10runs(last10) {
    last10 = String(last10 || '').trim();
    if (last10.length > 99) {
        throw new Error("String must be 99 characters or less.");
    }
    let addScore = 0;
    let count = 0;
    let note2 = '';
    let note = '';
    
    // Recency weights - most recent gets full points, then decays
    const weights = [1.0, 0.8, 0.6, 0.4, 0.2]; // Index 0 = most recent
    
    for (let i = last10.length - 1; i >= 0; i--) {
        let char = last10[i];
        if (char !== 'X' && char !== 'x' && count < 5) {
            let weight = weights[count]; // Apply recency weight
            count++;
            
            if (char === '1') {
                addScore += 10 * weight;
                note2 = ' 1st' + note2;
            }
            if (char === '2') {
                addScore += 5 * weight;
                note2 = ' 2nd' + note2;
            }
            if (char === '3') {
                addScore += 2 * weight;
                note2 = ' 3rd' + note2;
            }
        }
    }
    
    if (addScore > 0) {
        note = '+' + addScore.toFixed(1) + ' : Ran places:' + note2 + '\n';
    }
    return [addScore, note];
}

// =============================================================================
// JOCKEY AND TRAINER FUNCTIONS - UPDATED 2025-01-06
// =============================================================================

function normalizeJockeyName(jockeyName) {
    if (!jockeyName || typeof jockeyName !== 'string') return jockeyName || '';
    const jr = jockeyName.trim().replace(/\s+/g, ' '); // collapse whitespace

    for (const [key, value] of Object.entries(jockeyMapping)) {
        if (jr.startsWith(key)) {
            return jr.replace(key, value);
        }
    }
    return jr;
}

function checkJockeys(JockeyName) {
    // ==========================================
    // JOCKEY SCORING - UPDATED 2025-01-30
    // Based on 1203 race analysis - COMPLETE DATA
    // ==========================================
    var addScore = 0;
    var note = '';
    
    // Normalize name for mapping
    JockeyName = normalizeJockeyName(JockeyName);
    
    // TIER 1: Elite Value (50%+ ROI) - MASSIVE EDGE
    const eliteValueJockeys = [
        'Amy Jo Hayes',           // 713.6% ROI, 27.3% SR, 11 rides - EXTRAORDINARY
        'Ethan Brown',            // 423.3% ROI, 28.0% SR, 50 rides
        'Ms L Stojakovic',        // 339.1% ROI, 4.3% SR, 23 rides
        'Gabrielle Johnston',     // 270.0% ROI, 20.0% SR, 10 rides
        'B P Ward',               // 221.4% ROI, 9.5% SR, 21 rides
        'Ms C Puls',              // 218.8% ROI, 6.3% SR, 16 rides
        'Liam Riordan',           // 207.1% ROI, 8.8% SR, 34 rides - NOTE: was in badJockeys before, now elite!
        'Sam Kennedy',            // 169.1% ROI, 11.8% SR, 34 rides
        'G Spokes',               // 161.5% ROI, 8.3% SR, 24 rides
        'Jai Williams',           // 150.1% ROI, 25.9% SR, 54 rides - BIG SAMPLE
        'Teaghan Martin',         // 135.7% ROI, 7.1% SR, 28 rides
        'Alana Livesey',          // 117.7% ROI, 11.7% SR, 60 rides - BIG SAMPLE
        'L Magorrian',            // 114.3% ROI, 17.5% SR, 40 rides
        'Madeline Owen',          // 113.3% ROI, 13.3% SR, 15 rides
        'Adin Thompson',          // 108.2% ROI, 18.2% SR, 22 rides
        'Robert Whearty',         // 108.0% ROI, 35.0% SR, 20 rides
        'Zac Wadick',             // 100.0% ROI, 7.1% SR, 28 rides
        'Zoe Hunt',               // 92.4% ROI, 14.3% SR, 21 rides
        'Brooke King',            // 88.4% ROI, 8.9% SR, 45 rides
        'Ms C Jones',             // 81.3% ROI, 20.8% SR, 24 rides
        'W G Satherley',          // 74.3% ROI, 27.3% SR, 22 rides
        'C J Hillier',            // 73.4% ROI, 27.6% SR, 29 rides
        'Ella Bent',              // 71.4% ROI, 28.6% SR, 14 rides
        'Jordan Turner',          // 71.4% ROI, 14.3% SR, 14 rides
        'Rocky Cheung',           // 68.8% ROI, 6.3% SR, 32 rides
        'S Tsaikos',              // 67.5% ROI, 10.0% SR, 20 rides
        'Ms K Adams',             // 65.0% ROI, 15.0% SR, 20 rides
        'Luke Cartwright',        // 59.8% ROI, 14.9% SR, 161 rides - MASSIVE SAMPLE
        'A Stead',                // 58.3% ROI, 8.3% SR, 12 rides
        'Ms S Tierney',           // 56.9% ROI, 38.5% SR, 13 rides
        'J Whiting',              // 52.9% ROI, 5.8% SR, 52 rides
        'Ms D Keane',             // 50.9% ROI, 12.5% SR, 32 rides
    ];
    
    // TIER 2: Strong Value (20-50% ROI) - PROVEN EDGE
    const strongValueJockeys = [
        'Natika Riordan',         // 47.7% ROI, 17.9% SR, 39 rides
        'E J Walsh',              // 46.8% ROI, 13.2% SR, 38 rides
        'Jade Mc Naught',         // 46.4% ROI, 3.6% SR, 28 rides
        'Codi Jordan',            // 46.3% ROI, 29.2% SR, 24 rides
        'R Mc Leod',              // 45.1% ROI, 7.5% SR, 53 rides
        'K Sanderson',            // 41.9% ROI, 25.0% SR, 16 rides
        'Boris Thornton',         // 39.2% ROI, 19.2% SR, 26 rides
        'Tim Clark',              // 38.7% ROI, 24.2% SR, 91 rides - BIG SAMPLE
        'Jett Newman',            // 38.2% ROI, 22.7% SR, 44 rides
        'Ms L Morrison',          // 32.5% ROI, 22.7% SR, 22 rides
        'A Adkins',               // 31.2% ROI, 11.6% SR, 69 rides
        'S Clipperton',           // 30.8% ROI, 16.4% SR, 55 rides
        'A Hyeronimus',           // 29.2% ROI, 13.6% SR, 59 rides
        'Claire Ramsbotham',      // 26.5% ROI, 18.5% SR, 27 rides
        'V Duric',                // 25.9% ROI, 21.9% SR, 32 rides
        'Martin Harley',          // 23.3% ROI, 11.1% SR, 54 rides
        'Caitlin Tootell',        // 23.3% ROI, 22.4% SR, 58 rides
        'A Farragher',            // 21.3% ROI, 9.4% SR, 32 rides
        'M Rodd',                 // 20.2% ROI, 14.6% SR, 48 rides
        'B Muhcu',                // 20.0% ROI, 10.0% SR, 20 rides
    ];
    
    // TIER 3: Profitable (0-20% ROI) - SMALL EDGE
    const profitableJockeys = [
        'M R Du Plessis',         // 19.3% ROI, 13.3% SR, 75 rides
        'A C Goindasamy',         // 18.1% ROI, 19.0% SR, 21 rides
        'Zephen Johnston-Porter', // 16.3% ROI, 4.1% SR, 49 rides
        'K Jennings',             // 14.9% ROI, 8.1% SR, 37 rides
        'Keshaw Dhurun',          // 14.8% ROI, 11.1% SR, 27 rides
        'J Noonan',               // 14.6% ROI, 13.2% SR, 76 rides
        'Laqdar Ramoly',          // 13.3% ROI, 8.8% SR, 57 rides
        'J Mott',                 // 13.2% ROI, 19.1% SR, 136 rides - MASSIVE SAMPLE
        'Cassidy Hill',           // 12.4% ROI, 9.5% SR, 21 rides
        'L P Rolls',              // 12.1% ROI, 13.4% SR, 67 rides
        'Natasha Faithfull',      // 11.3% ROI, 13.7% SR, 51 rides
        'Emma Ly',                // 11.0% ROI, 20.0% SR, 10 rides
        'Leanne Boyd',            // 9.1% ROI, 9.1% SR, 11 rides
        'Tahlia Fenlon',          // 8.3% ROI, 7.5% SR, 53 rides
        'Dylan Gibbons',          // 6.8% ROI, 13.6% SR, 110 rides - BIG SAMPLE
        'W Gordon',               // 6.7% ROI, 13.1% SR, 84 rides
        'Todd Pannell',           // 6.5% ROI, 14.9% SR, 47 rides
        'D Moor',                 // 6.1% ROI, 12.1% SR, 99 rides
        'J Duffy',                // 4.2% ROI, 11.6% SR, 43 rides
        'Tyler Schiller',         // 4.1% ROI, 13.6% SR, 103 rides - BIG SAMPLE
        'Teagan Voorham',         // 3.5% ROI, 14.3% SR, 56 rides
        'Jett Stanley',           // 3.0% ROI, 12.9% SR, 62 rides
        'D Yendall',              // 1.7% ROI, 17.0% SR, 53 rides
        'B Melham',               // 1.7% ROI, 18.2% SR, 33 rides
        'Kody Nestor',            // 1.4% ROI, 16.7% SR, 36 rides
        'Sophie Potter',          // 0.8% ROI, 16.0% SR, 25 rides
    ];
    
    // TIER 4: Value Destroyers (negative ROI) - AVOID
    const badJockeys = [
        // Catastrophic (-80%+ ROI)
        'K Mc Evoy',              // -88.8% ROI, 3.8% SR, 52 rides
        'Deanne Panya',           // -89.5% ROI, 4.5% SR, 22 rides
        'Brayden Gaerth',         // -89.7% ROI, 2.7% SR, 37 rides
        'Christopher Pang',       // -90.6% ROI, 2.9% SR, 34 rides
        'Nadia Daniels',          // -90.9% ROI, 4.3% SR, 23 rides
        'Billy Owen',             // -91.3% ROI, 4.3% SR, 23 rides
        'Tala Hutchinson',        // -91.4% ROI, 3.4% SR, 29 rides
        'R Dolan',                // -93.4% ROI, 2.7% SR, 37 rides
        'Olivia Chambers',        // -94.1% ROI, 4.5% SR, 22 rides
        // Very Bad (-70% to -80% ROI)
        'Chloe Azzopardi',        // -87.4% ROI, 5.3% SR, 19 rides
        'Ron Stewart',            // -87.7% ROI, 3.8% SR, 26 rides
        'S Guymer',               // -87.2% ROI, 2.8% SR, 36 rides
        'Sarah Field',            // -87.1% ROI, 5.0% SR, 40 rides
        'P Carbery',              // -84.8% ROI, 4.8% SR, 21 rides
        'Ms M Weir',              // -85.5% ROI, 4.5% SR, 66 rides
        'Reece Jones',            // -85.5% ROI, 4.1% SR, 98 rides - BIG SAMPLE
        'Alana Kelly',            // -85.4% ROI, 2.4% SR, 41 rides
        'B Looker',               // -84.2% ROI, 6.6% SR, 61 rides
        'V Le Boeuf',             // -83.9% ROI, 3.2% SR, 31 rides
        'D L Turner',             // -83.2% ROI, 6.5% SR, 31 rides
        'Caitlyn Munro',          // -87.0% ROI, 5.0% SR, 20 rides
        'C Mc Iver',              // -86.5% ROI, 4.3% SR, 23 rides
        'Jenny Duggan',           // -82.0% ROI, 4.9% SR, 41 rides
        'J Ford',                 // -81.8% ROI, 4.9% SR, 82 rides
        'Dylan Dunn',             // -81.6% ROI, 2.6% SR, 38 rides
        'Patrick Moloney',        // -81.4% ROI, 2.3% SR, 43 rides
        'S Parnham',              // -80.0% ROI, 3.4% SR, 59 rides
        'Jack Martin',            // -80.0% ROI, 2.9% SR, 35 rides
        'Jacob Stiff',            // -78.1% ROI, 3.8% SR, 52 rides
        'Sairyn Fawke',           // -79.2% ROI, 1.9% SR, 53 rides
        'Jag Guthmann-Chester',   // -75.0% ROI, 8.7% SR, 46 rides
        'S Mc Gruddy',            // -75.0% ROI, 2.9% SR, 34 rides
        'Jefferson Tsang',        // -75.6% ROI, 5.9% SR, 34 rides
        'Leeshelle Small',        // -75.9% ROI, 5.4% SR, 37 rides
        'Zac Spain',              // -76.0% ROI, 3.6% SR, 55 rides
        'J Grisedale',            // -76.3% ROI, 7.4% SR, 27 rides
        'Rochelle Milnes',        // -69.5% ROI, 8.2% SR, 61 rides
        'Ms T Johnstone',         // -69.8% ROI, 8.5% SR, 47 rides
        'Justin Huxtable',        // -70.0% ROI, 5.0% SR, 20 rides
        'Ms T Chambers',          // -71.1% ROI, 5.3% SR, 19 rides
        'Benjamin Osmond',        // -71.4% ROI, 2.9% SR, 35 rides
        'Grace Palmer',           // -71.4% ROI, 7.1% SR, 14 rides
        'Austin Galati',          // -71.6% ROI, 10.5% SR, 57 rides
        'D Pires',                // -71.6% ROI, 8.0% SR, 25 rides
        'P Gatt',                 // -71.7% ROI, 3.3% SR, 30 rides
        'Milos Bunjevac',         // -72.2% ROI, 3.7% SR, 27 rides
        'Ella Drew',              // -72.4% ROI, 9.1% SR, 33 rides
        'Craig Williams',         // -68.0% ROI, 10.8% SR, 83 rides
        'Jean Van Overmeire',     // -68.5% ROI, 3.7% SR, 27 rides
        'William Stanley',        // -68.6% ROI, 6.3% SR, 64 rides
        'Caitlin Hollowood',      // -67.4% ROI, 4.3% SR, 23 rides
        'A J Calder',             // -67.6% ROI, 5.9% SR, 17 rides
        'S H Sit',                // -66.7% ROI, 5.6% SR, 18 rides
        'J Holder',               // -66.5% ROI, 6.8% SR, 59 rides
        // Bad (-40% to -66% ROI) - larger samples worth flagging
        'Anna Roper',             // -60.8% ROI, 6.9% SR, 116 rides - BIG SAMPLE
        'J R Collett',            // -61.8% ROI, 12.9% SR, 116 rides - BIG SAMPLE (Jason Collett)
        'Beau Mertens',           // -62.0% ROI, 8.8% SR, 113 rides - BIG SAMPLE
        'A B Collett',            // -62.7% ROI, 5.0% SR, 120 rides - BIG SAMPLE
        'Corey Sutherland',       // -62.4% ROI, 4.0% SR, 75 rides
        'Damien Thornton',        // -54.1% ROI, 11.5% SR, 96 rides
        'Ashley Morgan',          // -55.3% ROI, 11.8% SR, 93 rides
        'Regan Bayliss',          // -55.1% ROI, 10.8% SR, 74 rides
        'Kayla Crowther',         // -54.9% ROI, 7.5% SR, 67 rides
        'Celine Gaudray',         // -54.4% ROI, 8.0% SR, 50 rides
        'Ryan Houston',           // -46.7% ROI, 11.9% SR, 101 rides - BIG SAMPLE
        'Tom Sherry',             // -58.4% ROI, 8.0% SR, 87 rides
        'B Rawiller',             // -58.6% ROI, 8.3% SR, 72 rides
        'Holly Watson',           // -59.8% ROI, 6.9% SR, 58 rides
        'Carleen Hefel',          // -60.0% ROI, 12.2% SR, 41 rides
        'D Caboche',              // -60.3% ROI, 5.6% SR, 36 rides
        'Ms J Taylor',            // -60.8% ROI, 7.9% SR, 38 rides
        'Chelsea Baker',          // -61.2% ROI, 9.1% SR, 33 rides
        'Brittany Button',        // -62.0% ROI, 8.6% SR, 35 rides
        'Rose Hammond',           // -64.9% ROI, 5.4% SR, 37 rides
        'Olivia Webb',            // -65.7% ROI, 2.9% SR, 35 rides
        'M J Dee',                // -42.3% ROI, 6.7% SR, 105 rides - BIG SAMPLE
        'Jake Toeroek',           // -48.0% ROI, 13.0% SR, 54 rides
        'Jabez Johnstone',        // -49.8% ROI, 12.2% SR, 41 rides
        'J Penza',                // -51.1% ROI, 6.8% SR, 44 rides
        'J Parr',                 // -51.4% ROI, 12.7% SR, 55 rides
        'Leslie Tilley',          // -52.4% ROI, 14.7% SR, 34 rides
        'Cejay Graham',           // -52.5% ROI, 8.5% SR, 129 rides - BIG SAMPLE
        'K S Latham',             // -53.0% ROI, 12.1% SR, 33 rides
        'S O\'Donnell',           // -53.8% ROI, 9.6% SR, 52 rides
        'Bailey Kinninmont',      // -40.6% ROI, 5.7% SR, 53 rides
        'T Turner',               // -41.9% ROI, 6.6% SR, 61 rides
        'Logan Bates',            // -42.6% ROI, 13.0% SR, 100 rides - BIG SAMPLE
        'Shannen Llewellyn',      // -35.7% ROI, 8.8% SR, 68 rides
        'A Gibbons',              // -36.0% ROI, 7.8% SR, 51 rides
        'Jackson Radley',         // -36.3% ROI, 14.9% SR, 87 rides
        'Jordan Childs',          // -38.0% ROI, 15.2% SR, 79 rides
        'Damian Lane',            // -38.2% ROI, 12.7% SR, 55 rides
        'B Mc Dougall',           // -46.2% ROI, 7.9% SR, 38 rides
        'R Wiggins',              // -46.2% ROI, 12.3% SR, 57 rides
        'Tom Madden',             // -47.6% ROI, 10.9% SR, 46 rides
        'J Pracey-Holmes',        // -44.6% ROI, 10.0% SR, 70 rides
        'Connor Murtagh',         // -44.6% ROI, 10.3% SR, 39 rides
        'John Allen',             // -45.2% ROI, 7.5% SR, 93 rides - BIG SAMPLE
        'Lauryn Bingley',         // -45.4% ROI, 8.3% SR, 24 rides
        'Ms J Beriman',           // -45.4% ROI, 12.5% SR, 24 rides
        'B Parnham',              // -45.6% ROI, 6.5% SR, 62 rides
        'Tiffani Brooker',        // -45.7% ROI, 5.7% SR, 35 rides
        'A Darmanin',             // -57.8% ROI, 16.7% SR, 18 rides
        'Madi Derrick',           // -60.3% ROI, 6.9% SR, 29 rides
        'Chris Parnham',          // -27.3% ROI, 13.6% SR, 66 rides
        'Thomas Stockdale',       // -28.0% ROI, 13.8% SR, 80 rides
        'Lachlan Neindorf',       // -29.0% ROI, 15.9% SR, 107 rides - BIG SAMPLE
        'Kyle Wilson-Taylor',     // -30.3% ROI, 8.9% SR, 79 rides
        'Jack Hill',              // -30.3% ROI, 13.9% SR, 36 rides
        'Amy Mc Lucas',           // -20.3% ROI, 8.1% SR, 37 rides
        'G Buckley',              // -18.5% ROI, 13.8% SR, 94 rides
        'A Mallyon',              // -19.2% ROI, 11.4% SR, 79 rides
        'N Rawiller',             // -22.6% ROI, 13.8% SR, 94 rides
        'Jye McNeil',             // -26.0% ROI, 9.7% SR, 113 rides - BIG SAMPLE
        'Ryan Maloney',           // -26.4% ROI, 23.6% SR, 89 rides
    ];
    
    // Apply scoring
    if (eliteValueJockeys.includes(JockeyName)) {
        addScore += 25;
        note += '+25.0: Elite value jockey (50%+ ROI proven)\n';
    }
    else if (strongValueJockeys.includes(JockeyName)) {
        addScore += 20;
        note += '+20.0: Strong value jockey (20-50% ROI)\n';
    }
    else if (profitableJockeys.includes(JockeyName)) {
        addScore += 10;
        note += '+10.0: Profitable jockey (0-20% ROI)\n';
    }
    else if (badJockeys.includes(JockeyName)) {
        addScore -= 15;
        note += '-15.0: Poor value jockey (destroys ROI)\n';
    }
    // All others get 0 points (market efficient)
    
    return [addScore, note];
}

function normalizeTrainerName(trainerName) {
    if (!trainerName || typeof trainerName !== 'string') return trainerName || '';
    const tr = trainerName.trim();
    for (const [key, value] of Object.entries(trainerMapping)) {
        if (tr.startsWith(key)) {
            return tr.replace(key, value);
        }
    }
    return tr; // Return the original if no mapping is found
}

function checkTrainers(trainerName) {
    // ==========================================
    // TRAINER SCORING - UPDATED 2025-01-30
    // Based on 1203 race analysis - COMPLETE DATA
    // ==========================================
    var addScore = 0;
    var note = '';
    
    // Normalize name for mapping
    trainerName = normalizeTrainerName(trainerName);
    
    // TIER 1: Elite Value (50%+ ROI) - MASSIVE EDGE
    const eliteValueTrainers = [
        'C Maher',                          // 46.1% ROI, 15.2% SR, 315 runners - NOTE: dropped just below 50%, kept in T1 due to massive sample
        'David Jolly & Justin Potter',      // 232.0% ROI, 60.0% SR, 10 runners
        'Andrew Bobbin',                    // 245.9% ROI, 10.8% SR, 37 runners
        'Tony & Maddysen Sears',            // 163.6% ROI, 17.9% SR, 28 runners
        'Mark & Levi Kavanagh',             // 134.1% ROI, 17.2% SR, 29 runners
        'J Dunn & K Bishop',                // 122.7% ROI, 6.1% SR, 33 runners
        'J G Sargent',                      // 56.9% ROI, 12.5% SR, 32 runners
        'Jamie Edwards',                    // 52.2% ROI, 21.7% SR, 23 runners
    ];
    
    // TIER 2: Strong Value (20-50% ROI) - PROVEN EDGE
    const strongValueTrainers = [
        'Brett & Georgie Cavanough',        // 28.1% ROI, 19.0% SR, 58 runners - REVERSED from old bad list!
        'M W & J Hawkes',                   // 29.6% ROI, 18.5% SR, 54 runners
        'G J Stevenson',                    // 33.7% ROI, 31.6% SR, 19 runners
        'D R Harrison',                     // 36.0% ROI, 20.0% SR, 20 runners
        'Declan Maher',                     // 34.0% ROI, 20.0% SR, 20 runners
        'P Kearney',                        // 32.1% ROI, 10.7% SR, 28 runners
        'C Lundholm',                       // 20.5% ROI, 22.7% SR, 22 runners - REVERSED from old bad list!
        'G Portelli',                       // 20.4% ROI, 17.9% SR, 56 runners
        'Shane Jackson',                    // 20.3% ROI, 12.9% SR, 31 runners
    ];
    
    // TIER 3: Profitable (0-20% ROI) - SMALL EDGE
    const profitableTrainers = [
        'John O\'Shea & Tom Charlton',      // 18.6% ROI, 18.1% SR, 72 runners
        'P G Moody & Katherine Coleman',    // 13.2% ROI, 20.3% SR, 118 runners - BIG SAMPLE
        'Richard & Will Freedman',          // 11.4% ROI, 12.5% SR, 56 runners
        'J F Moloney',                      // 9.7% ROI, 9.7% SR, 31 runners
        'M C Kent',                         // 8.9% ROI, 15.8% SR, 19 runners
        'S I Singleton',                    // 7.3% ROI, 15.2% SR, 46 runners
        'Grant Young',                      // 7.3% ROI, 12.5% SR, 24 runners
        'P Messara & L Gavranich',          // 6.8% ROI, 22.7% SR, 22 runners
        'Richard Litt',                     // 5.8% ROI, 12.1% SR, 58 runners
        'A & S Freedman',                   // 5.3% ROI, 23.6% SR, 89 runners - BIG SAMPLE
        'T Busuttin & N Young',             // 4.4% ROI, 17.1% SR, 70 runners
        'E & D Browne',                     // 4.2% ROI, 8.3% SR, 24 runners
        'N J Olive',                        // 2.4% ROI, 21.2% SR, 33 runners
        'K R Kemp',                         // 2.1% ROI, 17.2% SR, 29 runners
        'A P Scally',                       // 1.8% ROI, 30.0% SR, 20 runners
        'M D Griffith',                     // 1.4% ROI, 22.2% SR, 18 runners
    ];
    
    // TIER 4: Value Destroyers (negative ROI) - AVOID
    const badTrainers = [
        // Catastrophic (-70%+ ROI)
        'N D Parnham',                      // -72.0% ROI, 4.2% SR, 71 runners
        'Bjorn Baker',                      // -72.2% ROI, 9.6% SR, 136 runners - BIG SAMPLE
        'Peter Gelagotis',                  // -91.9% ROI, 4.8% SR, 21 runners
        'Danielle Seib',                    // -93.7% ROI, 5.0% SR, 20 runners
        'E Jusufovic',                      // -92.4% ROI, 2.4% SR, 41 runners
        'P L Shailer',                      // -95.0% ROI, 3.1% SR, 32 runners
        'Stirling Osland',                  // -87.9% ROI, 3.0% SR, 33 runners
        'L J Gough',                        // -88.8% ROI, 4.8% SR, 21 runners
        'Brendan McShane',                  // -89.3% ROI, 7.1% SR, 14 runners
        'M W Walker',                       // -83.2% ROI, 7.1% SR, 42 runners
        'Andrew Dale',                      // -82.6% ROI, 4.7% SR, 43 runners
        'R K Cowl',                         // -80.7% ROI, 7.4% SR, 27 runners
        'Sarah Rutten',                     // -80.4% ROI, 3.6% SR, 28 runners
        'Jake Hull',                        // -80.5% ROI, 10.5% SR, 19 runners
        'Corey & Kylie Geran',              // -79.5% ROI, 2.6% SR, 39 runners
        'S V Vahala',                       // -82.5% ROI, 6.3% SR, 16 runners
        'R G Lipp',                         // -82.0% ROI, 5.0% SR, 20 runners
        'Todd Smart',                       // -84.4% ROI, 6.3% SR, 16 runners
        'Charlotte Littlefield',            // -76.1% ROI, 11.1% SR, 27 runners
        'M M Laurie',                       // -76.4% ROI, 8.2% SR, 49 runners
        'G W Egan',                         // -76.9% ROI, 6.3% SR, 16 runners
        'Clinton Taylor',                   // -77.3% ROI, 8.3% SR, 24 runners
        'Edward Cummings',                  // -77.5% ROI, 8.3% SR, 24 runners
        'S B Laming',                       // -72.9% ROI, 7.1% SR, 14 runners
        'T S Howlett',                      // -74.8% ROI, 8.7% SR, 23 runners
        'B Joseph & P & M Jones',           // -74.7% ROI, 5.7% SR, 35 runners
        'S Gandy',                          // -74.6% ROI, 8.6% SR, 35 runners
        'G Ryan & S Alexiou',               // -66.1% ROI, 7.8% SR, 64 runners
        'G M Begg',                         // -66.7% ROI, 10.4% SR, 48 runners
        'Luke Oliver',                      // -67.5% ROI, 5.0% SR, 20 runners
        'D & B Pearce',                     // -68.2% ROI, 4.5% SR, 44 runners
        'Lloyd Kennewell',                  // -68.2% ROI, 10.7% SR, 28 runners
        'Brett Pope',                       // -68.9% ROI, 7.1% SR, 28 runners
        'K J Parker',                       // -69.1% ROI, 4.5% SR, 44 runners
        'Ms S Grills',                      // -68.0% ROI, 6.7% SR, 15 runners
        'Georgie Holt',                     // -64.5% ROI, 13.6% SR, 22 runners
        'G R Nickson',                      // -65.7% ROI, 7.1% SR, 14 runners
        'W F Francis & G Kent',             // -63.3% ROI, 9.5% SR, 21 runners
        'John Thompson',                    // -61.4% ROI, 7.9% SR, 63 runners
        'K M Schweida',                     // -61.1% ROI, 10.0% SR, 70 runners
        'S J Morrisey',                     // -61.6% ROI, 10.5% SR, 19 runners
        'Allan Kehoe',                      // -61.8% ROI, 5.9% SR, 17 runners
        'R J Quinton',                      // -62.1% ROI, 15.4% SR, 26 runners
        'Jack Bruce',                       // -62.4% ROI, 11.4% SR, 79 runners
        'J A Sprague',                      // -69.2% ROI, 8.3% SR, 12 runners
        'K & K Keys',                       // -69.4% ROI, 12.5% SR, 16 runners
        'Ms K Waugh',                       // -71.4% ROI, 7.1% SR, 14 runners
        'P P Jenkins',                      // -72.1% ROI, 7.1% SR, 14 runners
        'Jessie Bazan',                     // -72.2% ROI, 5.6% SR, 18 runners
        'Joseph Ible',                      // -60.0% ROI, 15.4% SR, 13 runners
        'Ben Brisbourne',                   // -58.3% ROI, 8.3% SR, 84 runners - BIG SAMPLE
        'Ms K Buchanan',                    // -58.3% ROI, 6.7% SR, 30 runners
        'C I Brown',                        // -58.6% ROI, 10.3% SR, 29 runners
        'Ben Will & Jd Hayes',              // -56.0% ROI, 10.4% SR, 241 runners - MASSIVE SAMPLE
        'Aaron Purcell',                    // -55.4% ROI, 7.1% SR, 28 runners
        'Reece Goodwin',                    // -55.5% ROI, 9.1% SR, 33 runners
        'L F Birchley',                     // -55.8% ROI, 9.7% SR, 31 runners
        'Ms T Bateup',                      // -53.9% ROI, 9.1% SR, 33 runners
        'Adam Trinder',                     // -52.9% ROI, 11.8% SR, 17 runners
        'David Vandyke',                    // -51.3% ROI, 6.5% SR, 31 runners
        'Nacim Dilmi',                      // -51.2% ROI, 15.0% SR, 40 runners
        'W T Wilkes',                       // -51.0% ROI, 6.9% SR, 29 runners
        'D L Morton',                       // -45.8% ROI, 15.8% SR, 38 runners
        'Patrick & Michelle Payne',         // -41.1% ROI, 12.1% SR, 66 runners
        'S & J Casey',                      // -42.4% ROI, 15.4% SR, 39 runners
        'Ms K Gavenlock',                   // -42.9% ROI, 11.8% SR, 17 runners
        'A G Durrant',                      // -49.2% ROI, 14.6% SR, 48 runners
        'J K Blacker',                      // -47.3% ROI, 12.5% SR, 56 runners
        'R & L Price',                      // -48.7% ROI, 14.3% SR, 35 runners
        'Jason Warren',                     // -48.8% ROI, 7.0% SR, 43 runners
        'Nathan Doyle',                     // -44.8% ROI, 17.0% SR, 47 runners
        'Simon Miller',                     // -44.3% ROI, 13.6% SR, 44 runners
        'T M Andrews',                      // -44.7% ROI, 15.8% SR, 19 runners
        'Lachie Manzelmann',                // -57.6% ROI, 6.5% SR, 46 runners
        'Garret Lynch',                     // -37.5% ROI, 7.7% SR, 52 runners
        'Brad Widdup',                      // -38.2% ROI, 14.0% SR, 57 runners
        'Joseph Pride',                     // -38.8% ROI, 18.3% SR, 60 runners
        'Annabel & Rob Archibald',          // -39.1% ROI, 12.4% SR, 218 runners - MASSIVE SAMPLE
        'G & A Williams',                   // -40.3% ROI, 13.8% SR, 29 runners
        'R & C Jolly',                      // -40.7% ROI, 9.1% SR, 44 runners
        'L Smith',                          // -41.8% ROI, 20.6% SR, 34 runners
        'R P Northam',                      // -42.0% ROI, 15.2% SR, 46 runners
        'Mitchell Beer & George Carpenter', // -34.0% ROI, 14.3% SR, 63 runners
        'M Price & M Kent Jnr',             // -36.6% ROI, 16.7% SR, 96 runners - BIG SAMPLE
        'Gavin Bedggood',                   // -36.7% ROI, 11.0% SR, 73 runners
        'Simon Zahra',                      // -35.3% ROI, 14.0% SR, 43 runners
        'Peter Snowden',                    // -34.9% ROI, 18.1% SR, 83 runners
        'L & T Corstens & W Larkin',        // -31.4% ROI, 18.5% SR, 54 runners
        'Chris & Corey Munce',              // -31.1% ROI, 11.1% SR, 81 runners
        'T J Gollan',                       // -28.0% ROI, 15.2% SR, 191 runners - MASSIVE SAMPLE
        'M J Dunn',                         // -28.6% ROI, 13.8% SR, 80 runners
        'K A Lees',                         // -20.0% ROI, 16.0% SR, 119 runners - BIG SAMPLE
        'T & C McEvoy',                     // -21.1% ROI, 11.5% SR, 61 runners
        'M A Currie',                       // -21.9% ROI, 14.0% SR, 86 runners - REVERSED from old Tier 2!
        'Matthew Smith',                    // -22.5% ROI, 13.0% SR, 77 runners
        'D T O\'Brien',                     // -23.2% ROI, 16.9% SR, 65 runners
        'Tom Dabernig',                     // -23.7% ROI, 18.8% SR, 64 runners - REVERSED from old Tier 3!
        'G Waterhouse & A Bott',            // -4.3% ROI — borderline, kept neutral (removed from Tier 3)
    ];
    
    // Apply scoring
    if (eliteValueTrainers.includes(trainerName)) {
        addScore += 20;
        note += '+20.0: Elite value trainer (50%+ ROI proven)\n';
    }
    else if (strongValueTrainers.includes(trainerName)) {
        addScore += 15;
        note += '+15.0: Strong value trainer (20-50% ROI)\n';
    }
    else if (profitableTrainers.includes(trainerName)) {
        addScore += 10;
        note += '+10.0: Profitable trainer (5-20% ROI)\n';
    }
    else if (badTrainers.includes(trainerName)) {
        addScore -= 15;
        note += '-15.0: Poor value trainer (destroys ROI)\n';
    }
    // All others get 0 points (market efficient)
    
    return [addScore, note];
}
function checkRacingForm(racingForm, runType) {
    // Check if horse has won, or got 5th or better twice, in the past 3 runs at this distance
    let addScore = 0; // Initialize score
    var note = '';
    // Ensure each item is a string before processing
    if (typeof racingForm !== 'string') {
        const err = typeof racingForm;
        note += `Racing form ${runType} not string. Received type: ` + err;
        return [addScore, note];
    }
    // Split the string by '-' and convert to numbers
    const numbers = racingForm.split(/[:\-]/).map(s => Number(s.trim()));

    if (numbers.length != 4) {
        note += 'Racing form not correct format. Received: ' + racingForm;
        return [addScore, note];
    }

    // Check number of podiums is equal or less than races
    if ((numbers[1] + numbers[2] + numbers[3]) > numbers[0]) {
        note += 'More podiums than runs?? Received: ' + racingForm;
        return [addScore, note];
    }

    // Check if horse has any wins
    if (numbers[1] > 0) {
        addScore += 5; // Add 5 points if any number is '1'
        note += '+ 5.0 : Had at least 1 win at ' + runType + '\n';
    }

    // SEPARATELY check for podium rate (can award points in addition to win bonus)
    const count = (numbers[1] + numbers[2] + numbers[3]) / numbers[0];
    if (count >= 0.5) {
        addScore += 5; // Add 5 points if more than half of runs are places or wins
        note += '+ 5.0 : Podium >=50% of runs at ' + runType + '\n';
    }

    return [addScore, note]; // Return the total score
}

function checkTrackConditionForm(racingForm, trackCondition) {
    let addScore = 0;
    let note = '';

    if (typeof racingForm !== 'string') {
        note += 'Track condition form not string. Received type: ' + typeof racingForm + '\n';
        return [addScore, note];
    }

    const numbers = racingForm.split(/[:\-]/).map(s => Number(s.trim()));

    if (numbers.length != 4) {
        note += 'Track condition form incorrect format. Received: ' + racingForm + '\n';
        return [addScore, note];
    }

    const runs = numbers[0];
    const wins = numbers[1];
    const seconds = numbers[2];
    const thirds = numbers[3];
    const podiums = wins + seconds + thirds;

    if (podiums > runs) {
        note += 'More podiums than runs?? Received: ' + racingForm + '\n';
        return [addScore, note];
    }

    if (runs === 0) {
        note += '+ 0.0 : No runs on ' + trackCondition + ' track\n';
        return [addScore, note];
    }

    const winRate = wins / runs;
    const podiumRate = podiums / runs;

    let winScore = 0;
    if (winRate >= 0.51) {
        winScore = 12;
        note += '+ 12.0 : Exceptional win rate (' + (winRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    } else if (winRate >= 0.36) {
        winScore = 10;
        note += '+ 10.0 : Strong win rate (' + (winRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    } else if (winRate >= 0.26) {
        winScore = 8;
        note += '+ 8.0 : Good win rate (' + (winRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    } else if (winRate >= 0.16) {
        winScore = 5;
        note += '+ 5.0 : Moderate win rate (' + (winRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    } else if (winRate >= 0.01) {
        winScore = 2;
        note += '+ 2.0 : Low win rate (' + (winRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    } else {
        winScore = 0;
        note += '+ 0.0 : No wins on ' + trackCondition + '\n';
    }

    let podiumScore = 0;
    if (podiumRate >= 0.85) {
        podiumScore = 12;
        note += '+ 12.0 : Elite podium rate (' + (podiumRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    } else if (podiumRate >= 0.70) {
        podiumScore = 10;
        note += '+ 10.0 : Excellent podium rate (' + (podiumRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    } else if (podiumRate >= 0.55) {
        podiumScore = 9;
        note += '+ 9.0 : Strong podium rate (' + (podiumRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    } else if (podiumRate >= 0.40) {
        podiumScore = 6;
        note += '+ 6.0 : Good podium rate (' + (podiumRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    } else if (podiumRate >= 0.25) {
        podiumScore = 3;
        note += '+ 3.0 : Moderate podium rate (' + (podiumRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    } else {
        podiumScore = 0;
        note += '+ 0.0 : Poor podium rate (' + (podiumRate * 100).toFixed(0) + '%) on ' + trackCondition + '\n';
    }

    let undefeatedBonus = 0;
    if (wins === runs && runs >= 2) {
        if (runs >= 5) {
            undefeatedBonus = 5;
            note += '+ 5.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + ' (reduced - undefeated records show -7% to -79% ROI)\n';
        } else if (runs >= 3) {
            undefeatedBonus = 4;
            note += '+ 4.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + '\n';
        } else {
            undefeatedBonus = 3;
            note += '+ 3.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + '\n';
        }
    }

    let subtotal = winScore + podiumScore + undefeatedBonus;

    let confidenceMultiplier = 1.0;
    let confidenceNote = '';

    if (trackCondition === 'good') {
        if (runs >= 16) {
            confidenceMultiplier = 1.0;
            confidenceNote = ' [High confidence: ' + runs + ' runs]';
        } else if (runs >= 11) {
            confidenceMultiplier = 0.95;
            confidenceNote = ' [Good confidence: ' + runs + ' runs]';
        } else if (runs >= 6) {
            confidenceMultiplier = 0.85;
            confidenceNote = ' [Medium confidence: ' + runs + ' runs]';
        } else {
            confidenceMultiplier = 0.7;
            confidenceNote = ' [Low confidence: ' + runs + ' runs]';
        }
    } else if (trackCondition === 'soft') {
        if (runs >= 11) {
            confidenceMultiplier = 1.0;
            confidenceNote = ' [High confidence: ' + runs + ' runs]';
        } else if (runs >= 7) {
            confidenceMultiplier = 0.95;
            confidenceNote = ' [Good confidence: ' + runs + ' runs]';
        } else if (runs >= 4) {
            confidenceMultiplier = 0.85;
            confidenceNote = ' [Medium confidence: ' + runs + ' runs]';
        } else {
            confidenceMultiplier = 0.7;
            confidenceNote = ' [Low confidence: ' + runs + ' runs]';
        }
    } else if (trackCondition === 'heavy') {
        if (runs >= 7) {
            confidenceMultiplier = 1.0;
            confidenceNote = ' [High confidence: ' + runs + ' runs]';
        } else if (runs >= 5) {
            confidenceMultiplier = 0.9;
            confidenceNote = ' [Good confidence: ' + runs + ' runs]';
        } else if (runs >= 3) {
            confidenceMultiplier = 0.8;
            confidenceNote = ' [Medium confidence: ' + runs + ' runs]';
        } else {
            confidenceMultiplier = 0.6;
            confidenceNote = ' [Low confidence: ' + runs + ' runs]';
        }
    } else if (trackCondition === 'firm') {
        if (runs >= 5) {
            confidenceMultiplier = 1.0;
            confidenceNote = ' [High confidence: ' + runs + ' runs]';
        } else if (runs >= 3) {
            confidenceMultiplier = 0.85;
            confidenceNote = ' [Medium confidence: ' + runs + ' runs]';
        } else if (runs >= 2) {
            confidenceMultiplier = 0.7;
            confidenceNote = ' [Low confidence: ' + runs + ' runs]';
        } else {
            confidenceMultiplier = 0.5;
            confidenceNote = ' [Very low confidence: ' + runs + ' run]';
        }
    } else if (trackCondition === 'synthetic') {
        if (runs >= 7) {
            confidenceMultiplier = 1.0;
            confidenceNote = ' [High confidence: ' + runs + ' runs]';
        } else if (runs >= 4) {
            confidenceMultiplier = 0.85;
            confidenceNote = ' [Medium confidence: ' + runs + ' runs]';
        } else if (runs >= 2) {
            confidenceMultiplier = 0.7;
            confidenceNote = ' [Low confidence: ' + runs + ' runs]';
        } else {
            confidenceMultiplier = 0.5;
            confidenceNote = ' [Very low confidence: ' + runs + ' run]';
        }
    }

    addScore = subtotal * confidenceMultiplier;

    if (runs >= 5 && wins === 0 && podiumRate < 0.20) {
        addScore -= 8;
        note += '- 8.0 : Poor performance on ' + trackCondition + ' (' + runs + ' runs, 0 wins, <20% podium)\n';
    }

    note += '= ' + addScore.toFixed(1) + ' : Total track condition score' + confidenceNote + '\n';

    return [addScore, note];
}

function checkDistanceForm(racingForm) {
    let addScore = 0;
    let note = '';

    if (typeof racingForm !== 'string') {
        note += 'Distance form not string. Received type: ' + typeof racingForm + '\n';
        return [addScore, note];
    }

    const numbers = racingForm.split(/[:\-]/).map(s => Number(s.trim()));

    if (numbers.length != 4) {
        note += 'Distance form incorrect format. Received: ' + racingForm + '\n';
        return [addScore, note];
    }

    const runs = numbers[0];
    const wins = numbers[1];
    const seconds = numbers[2];
    const thirds = numbers[3];
    const podiums = wins + seconds + thirds;

    if (podiums > runs) {
        note += 'More podiums than runs?? Received: ' + racingForm + '\n';
        return [addScore, note];
    }

    if (runs === 0) {
        note += '+ 0.0 : No runs at this distance\n';
        return [addScore, note];
    }

    const winRate = wins / runs;
    const podiumRate = podiums / runs;

    let winScore = 0;
    if (winRate >= 0.51) {
        winScore = 8;
        note += '+ 8.0 : Exceptional win rate (' + (winRate * 100).toFixed(0) + '%) at this distance\n';
    } else if (winRate >= 0.36) {
        winScore = 7;
        note += '+ 7.0 : Strong win rate (' + (winRate * 100).toFixed(0) + '%) at this distance\n';
    } else if (winRate >= 0.26) {
        winScore = 5;
        note += '+ 5.0 : Good win rate (' + (winRate * 100).toFixed(0) + '%) at this distance\n';
    } else if (winRate >= 0.16) {
        winScore = 3;
        note += '+ 3.0 : Moderate win rate (' + (winRate * 100).toFixed(0) + '%) at this distance\n';
    } else if (winRate >= 0.01) {
        winScore = 1;
        note += '+ 1.0 : Low win rate (' + (winRate * 100).toFixed(0) + '%) at this distance\n';
    } else {
        winScore = 0;
        note += '+ 0.0 : No wins at this distance\n';
    }

    let podiumScore = 0;
    if (podiumRate >= 0.85) {
        podiumScore = 8;
        note += '+ 8.0 : Elite podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this distance\n';
    } else if (podiumRate >= 0.70) {
        podiumScore = 7;
        note += '+ 7.0 : Excellent podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this distance\n';
    } else if (podiumRate >= 0.55) {
        podiumScore = 6;
        note += '+ 6.0 : Strong podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this distance\n';
    } else if (podiumRate >= 0.40) {
        podiumScore = 4;
        note += '+ 4.0 : Good podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this distance\n';
    } else if (podiumRate >= 0.25) {
        podiumScore = 2;
        note += '+ 2.0 : Moderate podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this distance\n';
    } else {
        podiumScore = 0;
        note += '+ 0.0 : Poor podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this distance\n';
    }

    let subtotal = winScore + podiumScore;

    let confidenceMultiplier = 1.0;
    let confidenceNote = '';

    if (runs >= 10) {
        confidenceMultiplier = 1.0;
        confidenceNote = ' [High confidence: ' + runs + ' runs]';
    } else if (runs >= 6) {
        confidenceMultiplier = 0.9;
        confidenceNote = ' [Good confidence: ' + runs + ' runs]';
    } else if (runs >= 3) {
        confidenceMultiplier = 0.75;
        confidenceNote = ' [Medium confidence: ' + runs + ' runs]';
    } else {
        confidenceMultiplier = 0.6;
        confidenceNote = ' [Low confidence: ' + runs + ' runs]';
    }

    addScore = subtotal * confidenceMultiplier;

    if (runs >= 5 && wins === 0 && podiumRate < 0.20) {
        addScore -= 6;
        note += '- 6.0 : Poor performance at this distance (' + runs + ' runs, 0 wins, <20% podium)\n';
    }

    note += '= ' + addScore.toFixed(1) + ' : Total distance score' + confidenceNote + '\n';

    return [addScore, note];
}

function checkTrackForm(racingForm) {
    let addScore = 0;
    let note = '';

    if (typeof racingForm !== 'string') {
        note += 'Track form not string. Received type: ' + typeof racingForm + '\n';
        return [addScore, note];
    }

    const numbers = racingForm.split(/[:\-]/).map(s => Number(s.trim()));

    if (numbers.length != 4) {
        note += 'Track form incorrect format. Received: ' + racingForm + '\n';
        return [addScore, note];
    }

    const runs = numbers[0];
    const wins = numbers[1];
    const seconds = numbers[2];
    const thirds = numbers[3];
    const podiums = wins + seconds + thirds;

    if (podiums > runs) {
        note += 'More podiums than runs?? Received: ' + racingForm + '\n';
        return [addScore, note];
    }

    if (runs === 0) {
        note += '+ 0.0 : No runs at this track\n';
        return [addScore, note];
    }

    const winRate = wins / runs;
    const podiumRate = podiums / runs;

    let winScore = 0;
    if (winRate >= 0.51) {
        winScore = 6;
        note += '+ 6.0 : Exceptional win rate (' + (winRate * 100).toFixed(0) + '%) at this track\n';
    } else if (winRate >= 0.36) {
        winScore = 5;
        note += '+ 5.0 : Strong win rate (' + (winRate * 100).toFixed(0) + '%) at this track\n';
    } else if (winRate >= 0.26) {
        winScore = 4;
        note += '+ 4.0 : Good win rate (' + (winRate * 100).toFixed(0) + '%) at this track\n';
    } else if (winRate >= 0.16) {
        winScore = 2;
        note += '+ 2.0 : Moderate win rate (' + (winRate * 100).toFixed(0) + '%) at this track\n';
    } else if (winRate >= 0.01) {
        winScore = 1;
        note += '+ 1.0 : Low win rate (' + (winRate * 100).toFixed(0) + '%) at this track\n';
    } else {
        winScore = 0;
        note += '+ 0.0 : No wins at this track\n';
    }

    let podiumScore = 0;
    if (podiumRate >= 0.85) {
        podiumScore = 6;
        note += '+ 6.0 : Elite podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track\n';
    } else if (podiumRate >= 0.70) {
        podiumScore = 5;
        note += '+ 5.0 : Excellent podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track\n';
    } else if (podiumRate >= 0.55) {
        podiumScore = 4;
        note += '+ 4.0 : Strong podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track\n';
    } else if (podiumRate >= 0.40) {
        podiumScore = 3;
        note += '+ 3.0 : Good podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track\n';
    } else if (podiumRate >= 0.25) {
        podiumScore = 1;
        note += '+ 1.0 : Moderate podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track\n';
    } else {
        podiumScore = 0;
        note += '+ 0.0 : Poor podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track\n';
    }

    let subtotal = winScore + podiumScore;

    let confidenceMultiplier = 1.0;
    let confidenceNote = '';

    if (runs >= 7) {
        confidenceMultiplier = 1.0;
        confidenceNote = ' [High confidence: ' + runs + ' runs]';
    } else if (runs >= 4) {
        confidenceMultiplier = 0.85;
        confidenceNote = ' [Good confidence: ' + runs + ' runs]';
    } else if (runs >= 2) {
        confidenceMultiplier = 0.7;
        confidenceNote = ' [Medium confidence: ' + runs + ' run]';
    } else {
        confidenceMultiplier = 0.6;
        confidenceNote = ' [Low confidence: ' + runs + ' run]';
    }

    addScore = subtotal * confidenceMultiplier;

    if (runs >= 5 && wins === 0 && podiumRate < 0.20) {
        addScore -= 5;
        note += '- 5.0 : Poor performance at this track (' + runs + ' runs, 0 wins, <20% podium)\n';
    }

    note += '= ' + addScore.toFixed(1) + ' : Total track score' + confidenceNote + '\n';

    return [addScore, note];
}

function checkTrackDistanceForm(racingForm) {
    let addScore = 0;
    let note = '';

    if (typeof racingForm !== 'string') {
        note += 'Track+Distance form not string. Received type: ' + typeof racingForm + '\n';
        return [addScore, note];
    }

    const numbers = racingForm.split(/[:\-]/).map(s => Number(s.trim()));

    if (numbers.length != 4) {
        note += 'Track+Distance form incorrect format. Received: ' + racingForm + '\n';
        return [addScore, note];
    }

    const runs = numbers[0];
    const wins = numbers[1];
    const seconds = numbers[2];
    const thirds = numbers[3];
    const podiums = wins + seconds + thirds;

    if (podiums > runs) {
        note += 'More podiums than runs?? Received: ' + racingForm + '\n';
        return [addScore, note];
    }

    if (runs === 0) {
        note += '+ 0.0 : No runs at this track+distance\n';
        return [addScore, note];
    }

    const winRate = wins / runs;
    const podiumRate = podiums / runs;

    let winScore = 0;
    if (winRate >= 0.51) {
        winScore = 8;
        note += '+ 8.0 : Exceptional win rate (' + (winRate * 100).toFixed(0) + '%) at this track+distance\n';
    } else if (winRate >= 0.36) {
        winScore = 7;
        note += '+ 7.0 : Strong win rate (' + (winRate * 100).toFixed(0) + '%) at this track+distance\n';
    } else if (winRate >= 0.26) {
        winScore = 5;
        note += '+ 5.0 : Good win rate (' + (winRate * 100).toFixed(0) + '%) at this track+distance\n';
    } else if (winRate >= 0.16) {
        winScore = 3;
        note += '+ 3.0 : Moderate win rate (' + (winRate * 100).toFixed(0) + '%) at this track+distance\n';
    } else if (winRate >= 0.01) {
        winScore = 1;
        note += '+ 1.0 : Low win rate (' + (winRate * 100).toFixed(0) + '%) at this track+distance\n';
    } else {
        winScore = 0;
        note += '+ 0.0 : No wins at this track+distance\n';
    }

    let podiumScore = 0;
    if (podiumRate >= 0.85) {
        podiumScore = 8;
        note += '+ 8.0 : Elite podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track+distance\n';
    } else if (podiumRate >= 0.70) {
        podiumScore = 7;
        note += '+ 7.0 : Excellent podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track+distance\n';
    } else if (podiumRate >= 0.55) {
        podiumScore = 6;
        note += '+ 6.0 : Strong podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track+distance\n';
    } else if (podiumRate >= 0.40) {
        podiumScore = 4;
        note += '+ 4.0 : Good podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track+distance\n';
    } else if (podiumRate >= 0.25) {
        podiumScore = 2;
        note += '+ 2.0 : Moderate podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track+distance\n';
    } else {
        podiumScore = 0;
        note += '+ 0.0 : Poor podium rate (' + (podiumRate * 100).toFixed(0) + '%) at this track+distance\n';
    }

    let subtotal = winScore + podiumScore;

    let confidenceMultiplier = 1.0;
    let confidenceNote = '';

    if (runs >= 5) {
        confidenceMultiplier = 1.0;
        confidenceNote = ' [High confidence: ' + runs + ' runs]';
    } else if (runs >= 3) {
        confidenceMultiplier = 0.8;
        confidenceNote = ' [Good confidence: ' + runs + ' runs]';
    } else if (runs >= 2) {
        confidenceMultiplier = 0.6;
        confidenceNote = ' [Medium confidence: ' + runs + ' runs]';
    } else {
        confidenceMultiplier = 0.5;
        confidenceNote = ' [Low confidence: ' + runs + ' run]';
    }

    addScore = subtotal * confidenceMultiplier;

    if (runs >= 5 && wins === 0 && podiumRate < 0.20) {
        addScore -= 6;
        note += '- 6.0 : Poor performance at this track+distance (' + runs + ' runs, 0 wins, <20% podium)\n';
    }

    note += '= ' + addScore.toFixed(1) + ' : Total track+distance score' + confidenceNote + '\n';

    return [addScore, note];
}

function checkDaysSinceLastRun(meetingDate, formMeetingDate) {
    let addScore = 0;
    let note = '';

    // Validate inputs
    if (!meetingDate || !formMeetingDate) {
        return [0, ''];
    }

    // Parse dates - meeting date is DD/MM/YYYY, form date might be DD/MM/YY or DD/MM/YYYY
    const parseDate = (dateStr) => {
        if (!dateStr) return null;

        // Remove time portion if present: "11/10/2025 00:00:00" → "11/10/2025"
        const datePart = String(dateStr).split(' ')[0];

        // Split DD/MM/YYYY or DD/MM/YY
        const parts = datePart.split('/');
        if (parts.length !== 3) return null;

        const day = parseInt(parts[0], 10);
        const month = parseInt(parts[1], 10) - 1; // JavaScript months are 0-indexed
        let year = parseInt(parts[2], 10);

        // If year is 2-digit, convert to 4-digit
        if (year < 100) {
            // Assume years 00-50 are 2000-2050, and 51-99 are 1951-1999
            year += (year <= 50) ? 2000 : 1900;
        }

        return new Date(year, month, day);
    };

    const todayDate = parseDate(meetingDate);
    const lastRunDate = parseDate(formMeetingDate);

    if (!todayDate || !lastRunDate) {
        return [0, ''];
    }

    // Calculate days difference
    const millisecondsPerDay = 1000 * 60 * 60 * 24;
    const daysSinceLastRun = Math.floor((todayDate - lastRunDate) / millisecondsPerDay);

    // Apply penalties/bonuses based on days since last run
    if (daysSinceLastRun >= 365) {
        // Over a year - massive penalty
        addScore = -30;
        note += `-30.0 : Too fresh - ${daysSinceLastRun} days since last run (over 1 year!)\n`;
    } else if (daysSinceLastRun >= 250) {
        // 250+ days - MASSIVE penalty
        addScore = -30;  // WAS -25
        note += `-30.0 : Too fresh - ${daysSinceLastRun} days since last run (250+ days)\n`;
    } else if (daysSinceLastRun >= 200) {
        // 200+ days - very big penalty
        addScore = -25;  // WAS -20
        note += `-25.0 : Too fresh - ${daysSinceLastRun} days since last run\n`;
    } else if (daysSinceLastRun >= 150) {
        // 150+ days - significant penalty
        addScore = -20;  // WAS -15
        note += `-20.0 : Too fresh - ${daysSinceLastRun} days since last run\n`;
    } else if (daysSinceLastRun <= 7) {
        // 7 days or less - quick backup, BIG bonus (strongly outperforms market)
        addScore = 0;
        note += `+0.0 : Quick backup - only ${daysSinceLastRun} days since last run (no longer positive roi!)\n`;
    }
    // 8-149 days is the sweet spot - no penalty or bonus
    return [addScore, note];
}
function checkMargin(formPosition, formMargin, classChange = 0, recentForm = null) {
    let addScore = 0;
    let note = '';
    
    // Validate inputs
    if (!formPosition || !formMargin) {
        return [0, ''];
    }
    
    // Parse position and margin
    const position = parseInt(formPosition, 10);
    const margin = parseFloat(formMargin);
    
    // Check if parsing was successful
    if (isNaN(position) || isNaN(margin)) {
        return [0, ''];
    }
    
    // WINNERS (position = 1)
    if (position === 1) {
        if (margin >= 5.0) {
            addScore = 10;
            note += `+10.0 : Dominant last start win by ${margin.toFixed(1)}L\n`;
        } else if (margin >= 2.0) {
            addScore = 7;
            note += `+ 7.0 : Comfortable last start win by ${margin.toFixed(1)}L\n`;
        } else if (margin >= 0.5) {
            addScore = 5;
            note += `+ 5.0 : Narrow last start win by ${margin.toFixed(1)}L\n`;
        } else {
            addScore = 3;
            note += `+ 3.0 : Photo finish last start win by ${margin.toFixed(1)}L\n`;
        }
    }
    // PLACE GETTERS (position = 2 or 3)
    else if (position === 2 || position === 3) {
        if (margin <= 1.0) {
            addScore = 5;
            note += `+ 5.0 : Narrow loss (${position}${position === 2 ? 'nd' : 'rd'}) by ${margin.toFixed(1)}L - very competitive\n`;
        } else if (margin <= 2.0) {
            addScore = 3;
            note += `+ 3.0 : Close loss (${position}${position === 2 ? 'nd' : 'rd'}) by ${margin.toFixed(1)}L\n`;
        } else if (margin <= 3.5) {
            addScore = 0;
        } else {
            // Apply class drop context for place getters beaten badly
            if (classChange < -10) {
                addScore = 5;
                note += `+ 5.0 : Beaten (${position}${position === 2 ? 'nd' : 'rd'}) by ${margin.toFixed(1)}L BUT dropping in class significantly\n`;
            } else {
                addScore = -5;
                note += `- 5.0 : Beaten badly (${position}${position === 2 ? 'nd' : 'rd'}) by ${margin.toFixed(1)}L\n`;
            }
        }
    }
    // MIDFIELD OR BACK (position 4+)
    else if (position >= 4) {
        if (margin <= 3.0) {
            addScore = 0;
            note += `+ 0.0 : Competitive effort (${position}th) by ${margin.toFixed(1)}L\n`;
        } else if (margin <= 6.0) {
            // Check for class drop
            if (classChange < -15) {
                addScore = 0;
                note += `+ 0.0 : Beaten clearly (${position}th) by ${margin.toFixed(1)}L BUT dropping in class\n`;
            } else {
                addScore = -3;
                note += `- 3.0 : Beaten clearly (${position}th) by ${margin.toFixed(1)}L\n`;
            }
        } else if (margin <= 10.0) {
            // Check for significant class drop
            if (classChange < -20) {
                addScore = 5;
                note += `+ 5.0 : Well beaten (${position}th) by ${margin.toFixed(1)}L BUT major class drop (+14.5% ROI pattern)\n`;
            } else {
                addScore = -7;
                note += `- 7.0 : Well beaten (${position}th) by ${margin.toFixed(1)}L\n`;
            }
        } else {
            // Demolished horses - this pattern loses money even with class drops
            addScore = -25;  // WAS -15, REMOVED the class drop bonus
            note += `-25.0 : Demolished (${position}th) by ${margin.toFixed(1)}L - very poor form\n`;
        }
    }
    
    // Apply class drop context if we have a penalty
    if (addScore < 0 && classChange !== 0 && recentForm !== null) {
        const adjustment = adjustLastStartPenaltyForClassDrop(addScore, classChange, recentForm);
        addScore = adjustment.adjustedPenalty;
        if (adjustment.note) {
            note += `  ℹ️  ${adjustment.note}\n`;
        }
    }
    
    return [addScore, note];
}
// ========================================
// CLASS SCORING SYSTEM (0-130 Scale)
// ========================================

function parseClassType(classString) {
    if (!classString) return null;

    const str = String(classString).trim();

    // Group races
    if (/Group\s*[123]/i.test(str)) {
        const level = parseInt(str.match(/[123]/)[0], 10);
        return { type: 'Group', level: level, raw: str };
    }

    // Listed
    if (/Listed/i.test(str)) {
        return { type: 'Listed', level: null, raw: str };
    }

    // Benchmark (handles "Benchmark 80" or "Bench. 80" or "BM80")
    const bmMatch = str.match(/(?:Benchmark|Bench\.?|BM)\s*(\d+)/i);
    if (bmMatch) {
        const level = parseInt(bmMatch[1], 10);
        return { type: 'Benchmark', level: level, raw: str };
    }

    // Class (handles "Class 3" or "Cls 2")
    const classMatch = str.match(/(?:Class|Cls)\s*(\d+)/i);
    if (classMatch) {
        const level = parseInt(classMatch[1], 10);
        return { type: 'Class', level: level, raw: str };
    }

    // Restricted (handles "Rest. 62")
    const restMatch = str.match(/Rest\.?\s*(\d+)/i);
    if (restMatch) {
        const level = parseInt(restMatch[1], 10);
        return { type: 'Restricted', level: level, raw: str };
    }

    // Rating (handles "RS105" or "Rating 0-105")
    const ratingMatch = str.match(/(?:RS|Rating)\s*(?:0-)?(\d+)/i);
    if (ratingMatch) {
        const level = parseInt(ratingMatch[1], 10);
        return { type: 'Rating', level: level, raw: str };
    }

    // Maiden
    if (/Maiden|Mdn/i.test(str)) {
        return { type: 'Maiden', level: null, raw: str };
    }

    // Open (catch-all for Opens)
    if (/Open/i.test(str)) {
        return { type: 'Open', level: null, raw: str };
    }

    // Highway
    if (/Highway/i.test(str)) {
        return { type: 'Highway', level: null, raw: str };
    }

    // Special/Novice/etc
    if (/Special|Spec|Nov/i.test(str)) {
        return { type: 'Special', level: null, raw: str };
    }

    // Unknown
    return { type: 'Unknown', level: null, raw: str };
}

function extractFirstPrize(prizeString) {
    if (!prizeString) return null;

    // Match pattern: "1st $82500" or "1st  $82,500"
    const match = String(prizeString).match(/1st\s+\$([0-9,]+)/i);
    if (match) {
        // Remove commas and convert to number
        return parseInt(match[1].replace(/,/g, ''), 10);
    }
    return null;
}

function calculateClassScore(classString, prizeString) {
    const parsed = parseClassType(classString);
    if (!parsed) return 0;

    // Benchmark: Use BM number directly (BM1-BM100)
    if (parsed.type === 'Benchmark' && parsed.level !== null) {
        return Math.min(100, Math.max(1, parsed.level));
    }

    // Group races: Fixed scores
    if (parsed.type === 'Group') {
        if (parsed.level === 1) return 130;
        if (parsed.level === 2) return 122;
        if (parsed.level === 3) return 115;
    }

    // Listed: Fixed score
    if (parsed.type === 'Listed') {
        return 108;
    }

    // Everything else: Use prize money
    const prize = extractFirstPrize(prizeString);
    if (!prize) {
        // Fallback estimates if no prize money available
        if (parsed.type === 'Class') {
            if (parsed.level === 1) return 40;
            if (parsed.level === 2) return 55;
            if (parsed.level === 3) return 65;
            if (parsed.level === 4) return 75;
            if (parsed.level === 5) return 85;
            if (parsed.level === 6) return 92;
        }
        if (parsed.type === 'Maiden') return 50;
        if (parsed.type === 'Open') return 95;
        if (parsed.type === 'Restricted') return parsed.level ? parsed.level - 10 : 50;
        if (parsed.type === 'Rating') return parsed.level ? Math.min(100, parsed.level - 5) : 80;
        if (parsed.type === 'Highway') return 70;
        if (parsed.type === 'Special') return 45;
        return 50; // Default unknown
    }

    // Prize money to score conversion
    if (prize >= 2000000) return 130;
    if (prize >= 1000000) return 125 + ((prize - 1000000) / 1000000) * 5;
    if (prize >= 600000) return 120 + ((prize - 600000) / 400000) * 5;
    if (prize >= 400000) return 115 + ((prize - 400000) / 200000) * 5;
    if (prize >= 250000) return 110 + ((prize - 250000) / 150000) * 5;
    if (prize >= 150000) return 105 + ((prize - 150000) / 100000) * 5;
    if (prize >= 100000) return 100 + ((prize - 100000) / 50000) * 5;
    if (prize >= 80000) return 95 + ((prize - 80000) / 20000) * 5;
    if (prize >= 60000) return 88 + ((prize - 60000) / 20000) * 7;
    if (prize >= 45000) return 80 + ((prize - 45000) / 15000) * 8;
    if (prize >= 35000) return 72 + ((prize - 35000) / 10000) * 8;
    if (prize >= 25000) return 64 + ((prize - 25000) / 10000) * 8;
    if (prize >= 18000) return 56 + ((prize - 18000) / 7000) * 8;
    if (prize >= 12000) return 48 + ((prize - 12000) / 6000) * 8;
    if (prize >= 8000) return 40 + ((prize - 8000) / 4000) * 8;
    if (prize >= 5000) return 32 + ((prize - 5000) / 3000) * 8;
    return 25 + (prize / 5000) * 7;
}

function compareClasses(newClass, formClass, newPrizemoneyString, formPrizemoneyString, weightAdvantage = 0) {
    // Calculate scores using our 0-130 system
    const todayScore = calculateClassScore(newClass, newPrizemoneyString);
    const lastScore = calculateClassScore(formClass, formPrizemoneyString);

    // Calculate the difference
    const scoreDiff = todayScore - lastScore;

    let addScore = 0;
    let note = '';

    // Interpret the score difference
    if (scoreDiff > 0) {
        // Stepping UP in class (harder race)
        const basePenalty = -scoreDiff; // Negative points for stepping up

        // Check if weight advantage enables the class rise
        if (scoreDiff > 10 && weightAdvantage > 0) {
            const adjustment = adjustClassRiseForWeight(scoreDiff, weightAdvantage);
            addScore = adjustment.adjustedPenalty;
            note += addScore.toFixed(1) + ': Stepping UP ' + scoreDiff.toFixed(1) + ' class points; "' + formClass + '" (' + lastScore.toFixed(1) + ') to "' + newClass + '" (' + todayScore.toFixed(1) + ')\n';
            if (adjustment.note) {
                note += '  ℹ️  ' + adjustment.note + '\n';
            }
        } else {
            addScore = basePenalty;
            note += basePenalty.toFixed(1) + ': Stepping UP ' + scoreDiff.toFixed(1) + ' class points; "' + formClass + '" (' + lastScore.toFixed(1) + ') to "' + newClass + '" (' + todayScore.toFixed(1) + ')\n';
        }
    } else if (scoreDiff < 0) {
        // Stepping DOWN in class (easier race)
        addScore = Math.abs(scoreDiff);
        note += '+ ' + addScore.toFixed(1) + ': Stepping DOWN ' + Math.abs(scoreDiff).toFixed(1) + ' class points; "' + formClass + '" (' + lastScore.toFixed(1) + ') to "' + newClass + '" (' + todayScore.toFixed(1) + ')\n';
    } else {
        // Same class level
        note += '0.0: Same class level; "' + formClass + '" to "' + newClass + '" (both ' + todayScore.toFixed(1) + ')\n';
    }

    return [addScore, note];
}

// Form Price Scoring System: +50 to -50 points
const formPriceScores = {
    1.01: 50, 1.02: 50, 1.03: 50, 1.04: 49, 1.05: 49, 1.06: 49, 1.07: 49, 1.08: 48, 1.09: 48, 1.1: 48,
    1.11: 48, 1.12: 47, 1.13: 47, 1.14: 47, 1.15: 47, 1.16: 46, 1.17: 46, 1.18: 46, 1.19: 46, 1.2: 45,
    1.21: 45, 1.22: 45, 1.23: 45, 1.24: 44, 1.25: 44, 1.26: 44, 1.27: 44, 1.28: 43, 1.29: 43, 1.3: 43,
    1.31: 43, 1.32: 42, 1.33: 42, 1.34: 42, 1.35: 42, 1.36: 41, 1.37: 41, 1.38: 41, 1.39: 41, 1.4: 40,
    1.41: 40, 1.42: 40, 1.43: 40, 1.44: 39, 1.45: 39, 1.46: 39, 1.47: 39, 1.48: 38, 1.49: 38, 1.5: 38,
    1.51: 38, 1.52: 37, 1.53: 37, 1.54: 37, 1.55: 37, 1.56: 36, 1.57: 36, 1.58: 36, 1.59: 36, 1.6: 35,
    1.61: 35, 1.62: 35, 1.63: 35, 1.64: 34, 1.65: 34, 1.66: 34, 1.67: 34, 1.68: 33, 1.69: 33, 1.7: 33,
    1.71: 33, 1.72: 32, 1.73: 32, 1.74: 32, 1.75: 32, 1.76: 31, 1.77: 31, 1.78: 31, 1.79: 31, 1.8: 30,
    1.81: 30, 1.82: 30, 1.83: 30, 1.84: 29, 1.85: 29, 1.86: 29, 1.87: 29, 1.88: 28, 1.89: 28, 1.9: 28,
    1.91: 28, 1.92: 27, 1.93: 27, 1.94: 27, 1.95: 27, 1.96: 26, 1.97: 26, 1.98: 26, 1.99: 26, 2: 25,
    2.02: 25, 2.04: 24, 2.06: 24, 2.08: 24, 2.1: 23, 2.12: 23, 2.14: 23, 2.16: 22, 2.18: 22, 2.2: 22,
    2.22: 21, 2.24: 21, 2.26: 21, 2.28: 20, 2.3: 20, 2.32: 20, 2.34: 19, 2.36: 19, 2.38: 19, 2.4: 18,
    2.42: 18, 2.44: 18, 2.46: 17, 2.48: 17, 2.5: 17, 2.52: 16, 2.54: 16, 2.56: 16, 2.58: 15, 2.6: 15,
    2.62: 15, 2.64: 14, 2.66: 14, 2.68: 14, 2.7: 13, 2.72: 13, 2.74: 13, 2.76: 12, 2.78: 12, 2.8: 12,
    2.82: 11, 2.84: 11, 2.86: 11, 2.88: 10, 2.9: 10, 2.92: 10, 2.94: 9, 2.96: 9, 2.98: 9, 3: 8,
    3.05: 8, 3.1: 8, 3.15: 8, 3.2: 7, 3.25: 7, 3.3: 7, 3.35: 7, 3.4: 6, 3.45: 6, 3.5: 6,
    3.55: 6, 3.6: 5, 3.65: 5, 3.7: 5, 3.75: 5, 3.8: 4, 3.85: 4, 3.9: 4, 3.95: 4, 4: 3,
    4.1: 3, 4.2: 3, 4.3: 3, 4.4: 3, 4.5: 3, 4.6: 3, 4.7: 3, 4.8: 3, 4.9: 3, 5: 3,
    5.1: 2, 5.2: 2, 5.3: 2, 5.4: 2, 5.5: 2, 5.6: 2, 5.7: 2, 5.8: 2, 5.9: 2, 6: 2,
    6.2: 2, 6.4: 2, 6.6: 2, 6.8: 2, 7: 2, 7.2: 2, 7.4: 2, 7.6: 2, 7.8: 2, 8: 2,
    8.2: 2, 8.4: 2, 8.6: 2, 8.8: 2, 9: 2, 9.2: 2, 9.4: 2, 9.6: 2, 9.8: 2, 10: 2,
    10.5: 2, 11: 2, 11.5: 2, 12: 1, 12.5: 1, 13: 1, 13.5: 1, 14: 0, 14.5: 0, 15: 0,
    15.5: -1, 16: -2, 16.5: -2, 17: -3, 17.5: -4, 18: -5, 18.5: -5, 19: -6, 19.5: -7, 20: -9,
    21: -11, 22: -12, 23: -13, 24: -14, 25: -16, 26: -17, 27: -18, 28: -20, 29: -21, 30: -22,
    32: -25, 34: -26, 36: -28, 38: -29, 40: -31, 42: -32, 44: -34, 46: -35, 48: -37, 50: -38,
    55: -40, 60: -41, 65: -42, 70: -43, 75: -44, 80: -44, 85: -45, 90: -45, 95: -46, 100: -46,
    110: -47, 120: -47, 130: -48, 140: -48, 150: -49, 160: -49, 170: -50, 180: -50, 190: -50, 200: -50,
    250: -50, 300: -50, 350: -50, 400: -50, 450: -50, 500: -50
};

function checkFormPrice(formPrice, specialistContext = null) {
    let addScore = 0;
    let note = '';

    // Handle no valid form price case
    if (formPrice === null || formPrice === undefined) {
        return [0, 'Error: No form price available\n'];
    }

    // Convert to number if it's a string
    const numericPrice = typeof formPrice === 'string' ? parseFloat(formPrice) : formPrice;

    // Validate it's a valid number
    if (isNaN(numericPrice)) {
        return [0, `Error: Form price "${formPrice}" is not a valid number\n`];
    }

    // Validate range
    if (numericPrice < 1.01 || numericPrice > 500.0) {
        return [0, `Error: Form price $${numericPrice} outside valid range (1.01-500.00)\n`];
    }

    // Round to 2 decimal places for lookup
    const roundedPrice = Math.round(numericPrice * 100) / 100;

    // Try exact lookup first (object keys are coerced to strings)
    if (formPriceScores[roundedPrice] !== undefined) {
        addScore = formPriceScores[roundedPrice];
    } else {
        // Handle prices not in the lookup table with interpolation
        const sortedPrices = Object.keys(formPriceScores).map(Number).sort((a, b) => a - b);
        const closestLower = sortedPrices.filter(p => p < roundedPrice).pop();
        const closestHigher = sortedPrices.filter(p => p > roundedPrice)[0];

        if (closestLower !== undefined && closestHigher !== undefined) {
            // Linear interpolation
            const lowerScore = formPriceScores[closestLower];
            const higherScore = formPriceScores[closestHigher];
            const ratio = (roundedPrice - closestLower) / (closestHigher - closestLower);
            addScore = Math.round(lowerScore + (higherScore - lowerScore) * ratio);
        } else {
            return [0, `Error: Form price $${roundedPrice.toFixed(2)} could not be scored\n`];
        }
    }

    if (addScore > 0) {
        note += `+${addScore}.0 : Form price $${roundedPrice.toFixed(2)} (well-backed)\n`;
    } else if (addScore === 0) {
        note += `+0.0 : Form price $${roundedPrice.toFixed(2)} (neutral)\n`;
    } else {
        note += `${addScore}.0 : Form price $${roundedPrice.toFixed(2)} (interpolated)\n`;
    }

    // Apply specialist context if available and score is negative
    if (specialistContext !== null && addScore < 0) {
        const adjustment = adjustSPProfileForSpecialist(addScore, specialistContext);
        addScore = adjustment.adjustedPenalty;
        if (adjustment.note) {
            note += `  ℹ️  ${adjustment.note}\n`;
        }
    }

    return [addScore, note];
}

// TRACK CONDITION CONTEXT FOR SECTIONALS
function applyConditionContext(horse, raceCondition, sectionalDetails) {
    let sectionalWeight = 1.0;
    let conditionMultiplier = 1.0;
    let note = '';

    const conditionField = 'horse record ' + raceCondition;
    const conditionRecord = horse[conditionField];

    if (!conditionRecord || typeof conditionRecord !== 'string') {
        return { sectionalWeight, conditionMultiplier, note };
    }

    const numbers = conditionRecord.split(/[:\-]/).map(s => Number(s.trim()));
    if (numbers.length !== 4) {
        return { sectionalWeight, conditionMultiplier, note };
    }

    const runs = numbers[0];
    const wins = numbers[1];
    const seconds = numbers[2];
    const thirds = numbers[3];
    const podiums = wins + seconds + thirds;
    const podiumRate = runs > 0 ? podiums / runs : 0;

    if (raceCondition === 'soft' || raceCondition === 'heavy') {
        if (runs >= 5) {
            if (podiumRate >= 0.50) {
                conditionMultiplier = 2.0;
                sectionalWeight = 1.0;
                note = `Proven ${raceCondition} performer (${(podiumRate*100).toFixed(0)}% podium in ${runs} runs) - condition score doubled, sectionals kept`;
            } else if (podiumRate < 0.30) {
                conditionMultiplier = 2.0;
                sectionalWeight = 0.4;
                note = `Poor ${raceCondition} record (${(podiumRate*100).toFixed(0)}% podium in ${runs} runs) - sectionals reduced 60%`;
            } else {
                conditionMultiplier = 1.5;
                sectionalWeight = 0.7;
                note = `Average ${raceCondition} record (${(podiumRate*100).toFixed(0)}% podium in ${runs} runs) - sectionals reduced 30%`;
            }
        } else {
            let bestSectionalZ = 0;
            if (sectionalDetails && sectionalDetails.bestRecent) {
                bestSectionalZ = Math.abs(sectionalDetails.bestRecent / 15);
            }

            const formClassString = horse['form class'] || '';
            const formPrizeString = horse['prizemoney'] || '';
            const formClassScore = calculateClassScore(formClassString, formPrizeString);

            const raceClassString = horse['class restrictions'] || '';
            const racePrizeString = horse['race prizemoney'] || '';
            const raceClassScore = calculateClassScore(raceClassString, racePrizeString);

            if (bestSectionalZ > 1.2 && formClassScore > raceClassScore + 20) {
                sectionalWeight = 1.0;
                note = `Elite speed (z=${bestSectionalZ.toFixed(2)}) from higher class - sectionals kept despite limited ${raceCondition} data (${runs} runs)`;
            } else {
                sectionalWeight = 0.7;
                note = `Limited ${raceCondition} data (${runs} runs) - sectionals reduced 30%`;
            }
        }
    } else {
        sectionalWeight = 1.0;
        conditionMultiplier = 1.0;
        note = '';
    }

    return { sectionalWeight, conditionMultiplier, note };
}

// ==========================================
// WEIGHT-ENABLED CLASS RISE
// ==========================================
function adjustClassRiseForWeight(classChange, weightAdvantage) {
    // classChange: positive number of class points stepping up
    if (classChange <= 0) {
        // Not stepping up - no adjustment needed
        return { adjustedPenalty: -classChange, note: '' };
    }

    const basePenalty = -classChange; // negative penalty
    let reductionPercent = 0;
    let note = '';

    if (weightAdvantage >= 25) {
        reductionPercent = 0.75; // 75% reduction
    } else if (weightAdvantage >= 15) {
        reductionPercent = 0.5; // 50% reduction
    } else {
        reductionPercent = 0;
    }

    const adjustedPenalty = basePenalty * (1 - reductionPercent);
    if (reductionPercent > 0) {
        note = `Weight-enabled class rise: ${basePenalty.toFixed(1)} → ${adjustedPenalty.toFixed(1)} (${(reductionPercent*100).toFixed(0)}% reduction for ${weightAdvantage.toFixed(0)} weight points)`;
    }

    return { adjustedPenalty, note };
}

// ==========================================
// LAST START CONTEXT FOR CLASS DROPPERS
// ==========================================
function adjustLastStartPenaltyForClassDrop(lastStartPenalty, classChange, recentForm) {
    // lastStartPenalty expected negative when it's a penalty
    if (lastStartPenalty >= 0) return { adjustedPenalty: lastStartPenalty, note: '' };

    const isSignificantDrop = classChange <= -15; // Dropping 15+ class points
    const isModerateDrop = classChange <= -10;     // Dropping 10+ class points

    if (!isSignificantDrop && !isModerateDrop) {
        return { adjustedPenalty: lastStartPenalty, note: '' };
    }

    const hasStrongPreviousForm = recentForm && (recentForm.winsBeforeLast >= 2 || recentForm.recentWinRate >= 0.5);

    let reductionPercent = 0;
    let note = '';

    if (isSignificantDrop && hasStrongPreviousForm) {
        reductionPercent = 0.75;
        note = `Last start context: ${lastStartPenalty.toFixed(1)} → ${ (lastStartPenalty*(1 - reductionPercent)).toFixed(1) } (75% reduction - poor run at elite level, strong previous form)`;
    } else if (isSignificantDrop) {
        reductionPercent = 0.5;
        note = `Last start context: ${lastStartPenalty.toFixed(1)} → ${ (lastStartPenalty*(1 - reductionPercent)).toFixed(1) } (50% reduction - poor run at much higher class)`;
    } else if (isModerateDrop && hasStrongPreviousForm) {
        reductionPercent = 0.5;
        note = `Last start context: ${lastStartPenalty.toFixed(1)} → ${ (lastStartPenalty*(1 - reductionPercent)).toFixed(1) } (50% reduction - moderate class drop with strong form)`;
    } else {
        return { adjustedPenalty: lastStartPenalty, note: '' };
    }

    const adjustedPenalty = lastStartPenalty * (1 - reductionPercent);
    return { adjustedPenalty, note };
}

// ==========================================
// SP PROFILE CONTEXT FOR SPECIALISTS
// ==========================================
function adjustSPProfileForSpecialist(spPenalty, specialistContext) {
    if (spPenalty >= 0) return { adjustedPenalty: spPenalty, note: '' };

    const isSpecialist =
        (specialistContext && (
            specialistContext.hasStrongConditionRecord ||
            specialistContext.hasStrongTrackRecord ||
            specialistContext.hasStrongDistanceRecord ||
            specialistContext.hasPerfectRecord ||
            specialistContext.isRecentConditionWinner ||
            specialistContext.isClassDropperWithSpeed
        ));

    if (!isSpecialist) {
        return { adjustedPenalty: spPenalty, note: '' };
    }

    // Cap the penalty at -10 for specialists
    const cappedPenalty = Math.max(spPenalty, -10);
    if (cappedPenalty !== spPenalty) {
        // Determine reason
        let reason = '';
        if (specialistContext.hasStrongConditionRecord) reason = 'strong condition record';
        else if (specialistContext.hasStrongTrackRecord) reason = 'strong track record';
        else if (specialistContext.hasStrongDistanceRecord) reason = 'strong distance record';
        else if (specialistContext.hasPerfectRecord) reason = 'perfect record';
        else if (specialistContext.isRecentConditionWinner) reason = 'recent condition winner';
        else if (specialistContext.isClassDropperWithSpeed) reason = 'class dropper with proven speed';

        const note = `SP profile context: ${spPenalty.toFixed(1)} → ${cappedPenalty.toFixed(1)} (capped for ${reason})`;
        return { adjustedPenalty: cappedPenalty, note };
    }

    return { adjustedPenalty: spPenalty, note: '' };
}

// ==========================================
// PERFECT RECORD SPECIALIST BONUS
// Returns { bonus, note }
// ==========================================
function calculatePerfectRecordBonus(horse, trackCondition) {
    let totalBonus = 0;
    let notes = [];
    const perfectRecords = [];

    // Helper to evaluate a record string "runs:wins-seconds-thirds"
    const evalRecord = (recordStr, label) => {
        if (!recordStr || typeof recordStr !== 'string') return;
        const numbers = recordStr.split(/[:\-]/).map(s => Number(s.trim()));
        if (numbers.length !== 4) return;
        const [runs, wins, seconds, thirds] = numbers;
        const podiums = wins + seconds + thirds;
        if (runs > 0 && (wins === runs || podiums === runs)) {
            perfectRecords.push({
                type: label,
                runs,
                isPerfectWin: wins === runs,
                isPerfectPodium: podiums === runs
            });
        }
    };

    evalRecord(horse['horse record track'], 'track');
    evalRecord(horse['horse record track distance'], 'track+distance');
    evalRecord(horse['horse record distance'], 'distance');
    evalRecord(horse['horse record ' + trackCondition], 'condition');

    // Award bonuses per perfect record (simple rule set)
    // Perfect win bonuses (larger for more runs), perfect podium smaller.
    for (const rec of perfectRecords) {
        if (rec.isPerfectWin) {
            if (rec.runs >= 5) {
                totalBonus += 15;
                notes.push(`+15.0 : UNDEFEATED (${rec.type}) in ${rec.runs} runs`);
            } else if (rec.runs >= 3) {
                totalBonus += 12;
                notes.push(`+12.0 : UNDEFEATED (${rec.type}) in ${rec.runs} runs`);
            } else {
                totalBonus += 8;
                notes.push(`+8.0 : UNDEFEATED (${rec.type}) in ${rec.runs} runs`);
            }
        } else if (rec.isPerfectPodium) {
            if (rec.runs >= 5) {
                totalBonus += 10;
                notes.push(`+10.0 : 100% PODIUM (${rec.type}) in ${rec.runs} runs`);
            } else if (rec.runs >= 3) {
                totalBonus += 7;
                notes.push(`+7.0 : 100% PODIUM (${rec.type}) in ${rec.runs} runs`);
            } else {
                totalBonus += 4;
                notes.push(`+4.0 : 100% PODIUM (${rec.type}) in ${rec.runs} runs`);
            }
        }
    }

    const note = notes.length ? notes.join('; ') + '\n' : '';
    return { bonus: totalBonus, note };
}
    
// analyzer.js — cleaned final chunk (replace the corresponding region)

// PERFECT RECORD SPECIALIST BONUS (merged and robust)
function calculatePerfectRecordBonus(horse, trackCondition) {
    let totalBonus = 0;
    let notes = [];
    const perfectRecords = [];

    // Helper to evaluate a record string "runs:wins-seconds-thirds"
    const evalRecord = (recordStr, label) => {
        if (!recordStr || typeof recordStr !== 'string') return;
        const numbers = recordStr.split(/[:\-]/).map(s => Number(s.trim()));
        if (numbers.length !== 4) return;
        const [runs, wins, seconds, thirds] = numbers;
        const podiums = wins + seconds + thirds;
        if (runs > 0 && (wins === runs || podiums === runs)) {
            perfectRecords.push({
                type: label,
                runs,
                isPerfectWin: wins === runs,
                isPerfectPodium: podiums === runs
            });
        }
    };

    evalRecord(horse['horse record track'], 'track');
    evalRecord(horse['horse record track distance'], 'track+distance');
    evalRecord(horse['horse record distance'], 'distance');
    evalRecord(horse['horse record ' + trackCondition], `${trackCondition} condition`);

    // Award bonuses per perfect record (confidence-weighted)
    if (perfectRecords.length > 0) {
        perfectRecords.forEach(record => {
            const baseBonus = 5; // WAS 20 - data shows undefeated records are -37% to -79% ROI
            let confidenceMultiplier = 1.0;

            if (record.runs <= 2) confidenceMultiplier = 0.5;
            else if (record.runs <= 4) confidenceMultiplier = 0.6;
            else if (record.runs <= 6) confidenceMultiplier = 0.8;
            else confidenceMultiplier = 1.0;

            const bonus = baseBonus * confidenceMultiplier;
            totalBonus += bonus;

            const recordType = record.isPerfectWin ? 'UNDEFEATED' : '100% PODIUM';
            notes.push(`+${bonus.toFixed(1)} : ${recordType} at ${record.type} (${record.runs}/${record.runs}) - specialist bonus`);
        });

        if (perfectRecords.length > 1) {
            notes.push(`ℹ️  Multiple perfect records (${perfectRecords.length}) - strong specialist pattern`);
        }
    }

    return {
        bonus: totalBonus,
        note: notes.length ? notes.join('; ') + '\n' : '',
        recordCount: perfectRecords.length
    };
}

// Utility to detect undefeated record string "runs:wins-seconds-thirds"
function isUndefeatedRecord(record) {
    if (typeof record !== 'string') return false;
    const numbers = record.split(/[:\-]/).map(s => Number(s.trim()));
    if (numbers.length !== 4) return false;
    const [runs, wins, seconds, thirds] = numbers;
    return runs > 0 && wins === runs && seconds === 0 && thirds === 0;
}

// FIRST-UP / SECOND-UP specialist — cleaned and closed
function checkFirstUpSecondUp(horseRow) {
    let addScore = 0;
    let note = '';

    const last10 = String(horseRow['horse last10'] || '');
    const firstUpRecord = horseRow['horse record first up'];
    const secondUpRecord = horseRow['horse record second up'];

    // Determine first-up / second-up
    let isFirstUp = false;
    let isSecondUp = false;

    if (last10.toLowerCase().endsWith('x')) {
        isFirstUp = true;
    } else if (last10.length >= 2) {
        const lastChar = last10.charAt(last10.length - 1);
        const secondLastChar = last10.charAt(last10.length - 2);
        if (secondLastChar.toLowerCase() === 'x' && /\d/.test(lastChar)) {
            isSecondUp = true;
        }
    }

    // Score based on first-up record
    if (isFirstUp && typeof firstUpRecord === 'string') {
        const nums = firstUpRecord.split(/[:\-]/).map(s => Number(s.trim()));
        if (nums.length === 4 && nums[0] > 0) {
            const runs = nums[0], wins = nums[1], seconds = nums[2], thirds = nums[3];
            const podiumRate = (wins + seconds + thirds) / runs;
            if (wins > 0) {
                addScore += 0;
                note += `+ 0.0 : First-up winner(s) in ${wins} of ${runs} runs\n`;
            }
            if (podiumRate >= 0.5) {
                addScore += 0;
                note += `+ 0.0 : Strong first-up podium rate (${(podiumRate*100).toFixed(0)}%)\n`;
            }
        }
    }

    // Score based on second-up record
    if (isSecondUp && typeof secondUpRecord === 'string') {
        const nums2 = secondUpRecord.split(/[:\-]/).map(s => Number(s.trim()));
        if (nums2.length === 4 && nums2[0] > 0) {
            const runs = nums2[0], wins = nums2[1], seconds = nums2[2], thirds = nums2[3];
            const podiumRate = (wins + seconds + thirds) / runs;
            if (wins > 0) {
                addScore += 3;
                note += `+ 3.0 : Second-up winners in ${wins} of ${runs} runs\n`;
            }
            if (podiumRate >= 0.5) {
                addScore += 2;
                note += `+ 2.0 : Strong second-up podium rate (${(podiumRate*100).toFixed(0)}%)\n`;
            }
        }
    }

    // Undefeated specialist bonuses
    if (isFirstUp && isUndefeatedRecord(firstUpRecord)) {
        addScore += 15;
        note += `+15.0 : First-up specialist (UNDEFEATED: ${firstUpRecord})\n`;
    }
    if (isSecondUp && isUndefeatedRecord(secondUpRecord)) {
        addScore += 15;
        note += `+15.0 : Second-up specialist (UNDEFEATED: ${secondUpRecord})\n`;
    }

    // Mild penalty for unclear markers
    if (!isFirstUp && !isSecondUp && last10.length > 0 && /x/i.test(last10)) {
        addScore -= 1;
        note += `- 1.0 : Unclear spell/return status (markers present but pattern not first/second-up)\n`;
    }

    return [addScore, note];
}

// Calculate average form prices per horse-race composite key
function calculateAverageFormPrices(data) {
    const formPriceGroups = {};

    data.forEach(entry => {
        const compositeKey = `${entry['horse name']}-${entry['race number']}`;
        const formPrice = parseFloat(entry['form price']);
        if (!formPriceGroups[compositeKey]) formPriceGroups[compositeKey] = [];
        if (!isNaN(formPrice) && formPrice >= 1.01 && formPrice <= 500.00) {
            formPriceGroups[compositeKey].push(formPrice);
        }
    });

    const averages = {};
    Object.keys(formPriceGroups).forEach(key => {
        const prices = formPriceGroups[key];
        if (prices.length > 0) {
            const sum = prices.reduce((s, v) => s + v, 0);
            averages[key] = Math.round((sum / prices.length) * 100) / 100;
        } else {
            averages[key] = null;
        }
    });

    return averages;
}

function parseLastInteger(sectional) {
    if (!sectional || typeof sectional !== 'string') return null;
    const match = sectional.match(/(\d+)m$/);
    return match ? parseInt(match[1], 10) : null;
}

// SECTIONAL CONSTANTS
const SECTIONAL_DISTANCE_TOLERANCE = 400;  // ← Changed from 200 to 400
const SECTIONAL_WEIGHT_ADJUSTMENT_FACTOR = 0.06;
const SECTIONAL_CLASS_ADJUSTMENT_FACTOR = 0.015;
const SECTIONAL_BASELINE_CLASS_SCORE = 70;
const SECTIONAL_RECENCY_WEIGHTS = [1.0, 0.7, 0.5];
// SECTIONAL_DEFAULT_RACE_DISTANCE removed - now throws error if distance missing

// ==========================================
// API SECTIONAL PRICE/RANK SCORING
// ==========================================
function calculateApiSectionalScore(runner, raceDistance) {
    let score = 0;
    let notes = '';
    
    // Extract API data
    const last200Price = parseFloat(runner['last200timeprice']) || 900;
    const last200Rank = parseInt(runner['last200timerank']) || 25;
    const last400Price = parseFloat(runner['last400timeprice']) || 900;
    const last400Rank = parseInt(runner['last400timerank']) || 25;
    const last600Price = parseFloat(runner['last600timeprice']) || 900;
    const last600Rank = parseInt(runner['last600timerank']) || 25;
    
    // Check if we have any valid data
    const hasData = (last200Price < 900 || last400Price < 900 || last600Price < 900);
    if (!hasData) {
        return { score: 0, note: '' };
    }
    
    // Determine race type and sectional weights
    let raceType = 'MILE';
    let last200Weight = 0.3;
    let last400Weight = 0.4;
    let last600Weight = 0.3;
    
    if (raceDistance <= 1200) {
        raceType = 'SPRINT';
        last200Weight = 0.5;
        last400Weight = 0.3;
        last600Weight = 0.2;
    } else if (raceDistance > 1600) {
        raceType = 'STAYING';
        last200Weight = 0.2;
        last400Weight = 0.3;
        last600Weight = 0.5;
    }
    
    notes += `📏 ${raceType} (${raceDistance}m)\n`;
    
    // Scoring function based on rank (primary) and price (secondary)
    function scoreFromRankAndPrice(rank, price, maxScore) {
        if (price >= 900 || rank >= 25) return 0;
        
        // Rank-based scoring (more reliable)
        let rankScore = 0;
        if (rank === 1) rankScore = maxScore;
        else if (rank <= 3) rankScore = maxScore * 0.85;
        else if (rank <= 5) rankScore = maxScore * 0.65;
        else if (rank <= 8) rankScore = maxScore * 0.4;
        else if (rank <= 12) rankScore = maxScore * 0.15;
        else rankScore = maxScore * -0.1; // Slight penalty for poor ranks
        
        // Price bonus/penalty (adds nuance)
        let priceModifier = 0;
        if (price < 3) priceModifier = 0.2; // Elite price
        else if (price < 5) priceModifier = 0.15;
        else if (price < 10) priceModifier = 0.1;
        else if (price > 50) priceModifier = -0.15; // Poor price
        else if (price > 30) priceModifier = -0.1;
        
        return rankScore * (1 + priceModifier);
    }
    
    // Score each sectional
    if (last200Price < 900) {
        const sectionalScore = scoreFromRankAndPrice(last200Rank, last200Price, 20 * last200Weight);
        score += sectionalScore;
        
        let rating = 'ELITE';
        if (last200Rank > 12) rating = 'POOR';
        else if (last200Rank > 8) rating = 'AVERAGE';
        else if (last200Rank > 5) rating = 'GOOD';
        else if (last200Rank > 3) rating = 'VERY GOOD';
        
        notes += `${sectionalScore >= 0 ? '+' : ''}${sectionalScore.toFixed(1)}: Last 200m (Rank ${last200Rank}, $${last200Price.toFixed(2)}) - ${rating} [${(last200Weight*100).toFixed(0)}%]\n`;
    }
    
    if (last400Price < 900) {
        const sectionalScore = scoreFromRankAndPrice(last400Rank, last400Price, 20 * last400Weight);
        score += sectionalScore;
        
        let rating = 'ELITE';
        if (last400Rank > 12) rating = 'POOR';
        else if (last400Rank > 8) rating = 'AVERAGE';
        else if (last400Rank > 5) rating = 'GOOD';
        else if (last400Rank > 3) rating = 'VERY GOOD';
        
        notes += `${sectionalScore >= 0 ? '+' : ''}${sectionalScore.toFixed(1)}: Last 400m (Rank ${last400Rank}, $${last400Price.toFixed(2)}) - ${rating} [${(last400Weight*100).toFixed(0)}%]\n`;
    }
    
    if (last600Price < 900) {
        const sectionalScore = scoreFromRankAndPrice(last600Rank, last600Price, 20 * last600Weight);
        score += sectionalScore;
        
        let rating = 'ELITE';
        if (last600Rank > 12) rating = 'POOR';
        else if (last600Rank > 8) rating = 'AVERAGE';
        else if (last600Rank > 5) rating = 'GOOD';
        else if (last600Rank > 3) rating = 'VERY GOOD';
        
        notes += `${sectionalScore >= 0 ? '+' : ''}${sectionalScore.toFixed(1)}: Last 600m (Rank ${last600Rank}, $${last600Price.toFixed(2)}) - ${rating} [${(last600Weight*100).toFixed(0)}%]\n`;
    }
    
    // Improving trend bonus
    if (last200Rank < 900 && last400Rank < 900 && last600Rank < 900) {
        if (last200Rank < last400Rank && last400Rank < last600Rank) {
            const trendBonus = 5;
            score += trendBonus;
            notes += `+${trendBonus.toFixed(1)}: IMPROVING TREND (Ranks: ${last600Rank} → ${last400Rank} → ${last200Rank})\n`;
        }
    }
    
    // Data sufficiency
    const validSectionals = [last200Price, last400Price, last600Price].filter(p => p < 900).length;
    if (validSectionals === 1) {
        score *= 0.6;
        notes += `⚠️  Only 1 valid sectional - score reduced 40%\n`;
    } else if (validSectionals === 2) {
        score *= 0.8;
        notes += `⚠️  Only 2 valid sectionals - score reduced 20%\n`;
    }
    
    return { score: Math.round(score * 10) / 10, note: notes };
}

// SECTIONAL HELPERS
function calculateMean(values) {
    if (!values || values.length === 0) return 0;
    return values.reduce((sum, v) => sum + v, 0) / values.length;
}
function calculateStdDev(values, mean) {
    if (!values || values.length <= 1) return 0;
    const sq = values.map(v => Math.pow(v - mean, 2));
    const variance = sq.reduce((s, v) => s + v, 0) / values.length;
    return Math.sqrt(variance);
}
function calculateZScore(value, mean, stdDev) {
    if (!stdDev) return 0;
    return (value - mean) / stdDev;
}
function parseWeightSectional(weightRestriction) {
    if (!weightRestriction) return null;
    const match = String(weightRestriction).match(/[\d.]+/);
    return match ? parseFloat(match[0]) : null;
}
function parsePrizeMoney(prizeString) {
    if (!prizeString) return null;
    const cleaned = String(prizeString).replace(/[$,\s]/g, '');
    const direct = parseInt(cleaned, 10);
    if (!isNaN(direct) && direct > 0) return direct;
    const match = String(prizeString).match(/1st\s+\$([0-9,]+)/i);
    if (match) return parseInt(match[1].replace(/,/g, ''), 10);
    return null;
}
function getTypicalWeightForClass(classScore) {
    if (classScore >= 115) return 57.0;
    if (classScore >= 100) return 56.5;
    if (classScore >= 85) return 56.0;
    if (classScore >= 70) return 55.0;
    if (classScore >= 55) return 54.5;
    if (classScore >= 40) return 54.0;
    return 53.5;
}

// Get lowest sectionals by race (cleaned)
function getLowestSectionalsByRace(data) {
    const validData = data.filter(entry => {
        if (!entry['race number'] || !entry['horse name'] || !entry['sectional']) return false;
        const horseName = String(entry['horse name']).trim().toLowerCase();
        if (!horseName || horseName === 'horse name' || ['nan','null','undefined',''].includes(horseName)) return false;
        const raceNum = parseInt(entry['race number'], 10);
        if (isNaN(raceNum) || raceNum <= 0) return false;
        const sectionalMatch = String(entry['sectional']).match(/^(\d+\.?\d*)sec (\d+)m$/);
        if (!sectionalMatch) return false;
        return true;
    });

    const raceGroups = {};
    validData.forEach(entry => {
        const raceNum = parseInt(entry['race number'], 10);
        if (!raceGroups[raceNum]) raceGroups[raceNum] = [];
        raceGroups[raceNum].push(entry);
    });

    const results = [];

    Object.keys(raceGroups).forEach(raceNumKey => {
        const raceNum = parseInt(raceNumKey, 10);
        const raceData = raceGroups[raceNum];
        const parsedData = [];
        const sectionalDistances = new Set();

        raceData.forEach(entry => {
            const sectionalMatch = String(entry.sectional).match(/^(\d+\.?\d*)sec (\d+)m$/);
            if (!sectionalMatch) return;
            const time = parseFloat(sectionalMatch[1]);
            const sectionalDistance = parseInt(sectionalMatch[2], 10);
            const formDistance = parseInt(entry['form distance'], 10);
            if (time > 0) sectionalDistances.add(sectionalDistance);
            parsedData.push({
                ...entry,
                time,
                sectionalDistance,
                formDistance
            });
        });

        // Determine target sectional distance
        let targetSectionalDistance = null;
        if (sectionalDistances.size > 1) {
            const counts = {};
            parsedData.forEach(e => {
                if (e.time > 0) {
                    counts[e.sectionalDistance] = (counts[e.sectionalDistance] || 0) + 1;
                }
            });
            let bestDist = null;
            let bestCount = -1;
            Object.keys(counts).forEach(d => {
                if (counts[d] > bestCount) { bestCount = counts[d]; bestDist = parseInt(d, 10); }
            });
            targetSectionalDistance = bestDist;
        } else if (sectionalDistances.size === 1) {
            targetSectionalDistance = [...sectionalDistances][0];
        }

        // Determine today's race distance robustly
        let todaysRaceDistance = parseInt(raceData[0]?.distance, 10);
        if (isNaN(todaysRaceDistance)) todaysRaceDistance = parseInt(raceData[0]?.['race distance'], 10);
        if (isNaN(todaysRaceDistance)) todaysRaceDistance = targetSectionalDistance;
        if (isNaN(todaysRaceDistance) || !todaysRaceDistance) {
    console.error(`Race ${raceNum}: No valid race distance found in CSV data - skipping sectional analysis`);
    return;  // Skip this race, move to next one
}

        // Build per-horse historical adjusted times
        const horseData = {};
        parsedData.forEach(entry => {
            const horseName = entry['horse name'];
            const rawTime = entry.time;
            const formDistance = entry.formDistance;
            const sectionalDistance = entry.sectionalDistance;

            const formClassScore = calculateClassScore(entry['form class'] || '', entry['prizemoney'] || '');
            const pastWeightCarried = parseWeightSectional(entry['form weight']);
            if (pastWeightCarried === null) return;

            const typicalWeightForClass = getTypicalWeightForClass(formClassScore);
            const isDistanceRelevant = !isNaN(formDistance) && !isNaN(todaysRaceDistance) &&
                Math.abs(formDistance - todaysRaceDistance) <= SECTIONAL_DISTANCE_TOLERANCE;

            const isCorrectSectionalDistance = targetSectionalDistance === null || sectionalDistance === targetSectionalDistance;

            if (rawTime > 0 && isDistanceRelevant && isCorrectSectionalDistance) {
                const weightDiff = pastWeightCarried - typicalWeightForClass;
                const weightAdjustment = weightDiff * SECTIONAL_WEIGHT_ADJUSTMENT_FACTOR;
                const classPointsDiff = formClassScore - SECTIONAL_BASELINE_CLASS_SCORE;
                const classAdjustment = classPointsDiff * SECTIONAL_CLASS_ADJUSTMENT_FACTOR;
                const adjustedTime = rawTime - weightAdjustment - classAdjustment;

                horseData[horseName] = horseData[horseName] || [];
                horseData[horseName].push({
                    time: adjustedTime,
                    rawTime,
                    weight: pastWeightCarried,
                    typicalWeight: typicalWeightForClass,
                    formClass: entry['form class'],
                    formClassScore,
                    date: entry['form meeting date'],
                    adjustments: {
                        weight: -weightAdjustment,
                        weightDiff,
                        class: -classAdjustment,
                        classScore: formClassScore,
                        total: -(weightAdjustment + classAdjustment)
                    }
                });
            }
        });

        // Sort each horse's entries by date desc
        Object.keys(horseData).forEach(hn => horseData[hn].sort((a,b) => new Date(b.date) - new Date(a.date)));

        // SYSTEM 1: Weighted average of last 3
        const weightedAvgData = [];
        Object.keys(horseData).forEach(hn => {
            const times = horseData[hn];
            if (times.length === 0) return;
            const last3 = times.slice(0, 3);
            let weightedSum = 0, totalW = 0;
            last3.forEach((e, i) => {
                const w = SECTIONAL_RECENCY_WEIGHTS[i] || 0.5;
                weightedSum += e.time * w;
                totalW += w;
            });
            const weightedAverage = weightedSum / totalW;
            const avgWeightAdj = last3.reduce((s,e)=>s+e.adjustments.weight,0)/last3.length;
            const avgWeightDiff = last3.reduce((s,e)=>s+e.adjustments.weightDiff,0)/last3.length;
            const avgClassAdj = last3.reduce((s,e)=>s+e.adjustments.class,0)/last3.length;
            const avgClassScore = last3.reduce((s,e)=>s+e.formClassScore,0)/last3.length;

            weightedAvgData.push({
                horseName: hn,
                averageTime: weightedAverage,
                runsUsed: last3.length,
                avgWeightAdj, avgWeightDiff, avgClassAdj, avgClassScore
            });
        });

        // SYSTEM 2: Best recent (best of last 5)
        const bestRecentData = [];
        Object.keys(horseData).forEach(hn => {
            const times = horseData[hn];
            if (times.length === 0) return;
            const last5 = times.slice(0, 5);
            const bestEntry = last5.reduce((best, cur) => cur.time < best.time ? cur : best, last5[0]);
            bestRecentData.push({
                horseName: hn,
                bestTime: bestEntry.time,
                rawBestTime: bestEntry.rawTime,
                fromLast: last5.length,
                weight: bestEntry.weight,
                typicalWeight: bestEntry.typicalWeight,
                formClass: bestEntry.formClass,
                adjustments: bestEntry.adjustments
            });
        });

        // SYSTEM 3: Consistency (std dev of last up to 5)
        const consistencyData = [];
        Object.keys(horseData).forEach(hn => {
            const times = horseData[hn];
            if (times.length >= 3) {
                const last5Times = times.slice(0, 5).map(e => e.time);
                const mean = calculateMean(last5Times);
                const stdDev = calculateStdDev(last5Times, mean);
                consistencyData.push({ horseName: hn, stdDev, runsUsed: last5Times.length });
            }
        });

        // Z-score and points (REDUCED 50% - historical scores have weak correlation)
        const avgTimes = weightedAvgData.map(h=>h.averageTime);
        const avgMean = calculateMean(avgTimes);
        const avgStdDev = calculateStdDev(avgTimes, avgMean);
        weightedAvgData.forEach(h => { h.zScore = calculateZScore(h.averageTime, avgMean, avgStdDev); h.points = h.zScore * -1.8; });  // WAS -3.6

        const bestTimes = bestRecentData.map(h=>h.bestTime);
        const bestMean = calculateMean(bestTimes);
        const bestStdDev = calculateStdDev(bestTimes, bestMean);
        bestRecentData.forEach(h => { h.zScore = calculateZScore(h.bestTime, bestMean, bestStdDev); h.points = h.zScore * -7.5; });  // WAS -15

        consistencyData.forEach(h => { h.points = Math.max(0, 10 - (h.stdDev * 8)); });

        // Aggregate scores
        const horseScores = {};
        const allHorses = [...new Set(raceData.map(e => e['horse name']))];
        allHorses.forEach(hn => horseScores[hn] = { score: 0, note: '', dataSufficiency: 1.0, details: { weightedAvg:0, bestRecent:0, consistency:0 } });

        weightedAvgData.forEach(h => {
            const points = Math.round(h.points*10)/10;
            horseScores[h.horseName].score += points;
            horseScores[h.horseName].details.weightedAvg = points;
            const zScoreText = (h.zScore * -1).toFixed(2);
            const weightInfo = h.avgWeightDiff >= 0 ? `+${h.avgWeightDiff.toFixed(1)}kg heavier` : `${Math.abs(h.avgWeightDiff).toFixed(1)}kg lighter`;
            const adjText = `wgt:${h.avgWeightAdj >= 0 ? '+' : ''}${h.avgWeightAdj.toFixed(2)}s (${weightInfo}), class:${h.avgClassAdj >= 0 ? '+' : ''}${h.avgClassAdj.toFixed(2)}s (avg ${h.avgClassScore.toFixed(0)} pts)`;
            horseScores[h.horseName].note += `+${points.toFixed(1)}: weighted avg (z=${zScoreText}, ${h.runsUsed} runs)\n  └─ adj: ${adjText}\n`;
        });

        bestRecentData.forEach(h => {
    const points = Math.round(h.points*10)/10;
    horseScores[h.horseName].score += points;
    horseScores[h.horseName].details.bestRecent = points;
    const zScoreText = (h.zScore * -1).toFixed(2);
    const rawVsAdj = `${h.rawBestTime.toFixed(2)}s → ${h.bestTime.toFixed(2)}s`;
    const classInfo = h.formClass || 'unknown';
    const classScore = h.adjustments.classScore || 0;
    const weightCarried = h.weight.toFixed(1);
    const typicalWeight = h.typicalWeight.toFixed(1);
    const adjText = `wgt:${h.adjustments.weight >= 0 ? '+' : ''}${h.adjustments.weight.toFixed(2)}s (carried ${weightCarried}kg vs ~${typicalWeight}kg typical), class:${h.adjustments.class >= 0 ? '+' : ''}${h.adjustments.class.toFixed(2)}s`;
    
    // NEW: Get actual historical times for this horse (oldest to newest)
    const horseTimes = horseData[h.horseName] || [];
    const last5 = horseTimes.slice(0, 5);
    const adjustedTimesArray = last5.map(entry => entry.time.toFixed(2)).reverse();  // Reverse: oldest first
    const rawTimesArray = last5.map(entry => entry.rawTime.toFixed(2)).reverse();
    
    // Add the history arrays to the notes
    horseScores[h.horseName].note += `+${points.toFixed(1)}: best of last ${h.fromLast} (z=${zScoreText})\n  └─ ${rawVsAdj} (${adjText}) in ${classInfo} (${classScore.toFixed(0)} pts)\n  └─ HISTORY_ADJ: [${adjustedTimesArray.join(', ')}]\n  └─ HISTORY_RAW: [${rawTimesArray.join(', ')}]\n`;
});

        consistencyData.forEach(h => {
            const points = Math.round(h.points*10)/10;
            const reducedPoints = points * 0.5;  // 50% reduction like other historical scores
            horseScores[h.horseName].score += reducedPoints;
            horseScores[h.horseName].details.consistency = reducedPoints;
            let consistencyRating = 'excellent';
            if (h.stdDev > 0.8) consistencyRating = 'poor';
            else if (h.stdDev > 0.5) consistencyRating = 'fair';
            else if (h.stdDev > 0.3) consistencyRating = 'good';
            horseScores[h.horseName].note += `+${reducedPoints.toFixed(1)}: consistency - ${consistencyRating} (SD=${h.stdDev.toFixed(2)}s) [50% weight]\n`;
        });

        // Data sufficiency penalties
Object.keys(horseData).forEach(hn => {
    const validRuns = horseData[hn].length;
    let penalty = 1.0;
    let penaltyNote = '';
    
    if (validRuns === 0) { 
        penalty = 0; 
        penaltyNote = `⚠️  No sectionals at relevant distance (±${SECTIONAL_DISTANCE_TOLERANCE}m from ${todaysRaceDistance}m)\n`; 
    }
    else if (validRuns === 1) { penalty = 0.5; penaltyNote = `⚠️  Only 1 relevant sectional (score ×${penalty})\n`; }
    else if (validRuns === 2) { penalty = 0.7; penaltyNote = `⚠️  Only 2 relevant sectionals (score ×${penalty})\n`; }
    else if (validRuns === 3) { penalty = 0.85; penaltyNote = `⚠️  Only 3 relevant sectionals (score ×${penalty})\n`; }
    else if (validRuns === 4) { penalty = 0.95; penaltyNote = `ℹ️  4 relevant sectionals (score ×${penalty})\n`; }

    horseScores[hn].dataSufficiency = penalty;
    
    // NEW LOGIC: Handle zero penalty specially
    if (penalty === 0) {
        horseScores[hn].score = 0;  // Explicitly set to 0 (neutral)
        horseScores[hn].note = penaltyNote;
    } else if (penalty < 1.0) {
        horseScores[hn].score *= penalty;
        horseScores[hn].note += penaltyNote;
    }
});
// Convert to results with compatibility fields
        Object.keys(horseScores).forEach(hn => {
          const finalScore = Math.round(horseScores[hn].score * 10) / 10;  // Allow negative scores
            results.push({
                race: raceNum,
                name: hn,
                sectionalScore: finalScore,
                sectionalNote: horseScores[hn].note.trim(),
                sectionalDetails: horseScores[hn].details,
                dataSufficiency: horseScores[hn].dataSufficiency,
                hasAverage1st: false,
                hasLastStart1st: false
            });
        });
    });

    // Mark winners per race (combo flags)
    const raceGroupsForCombo = results.reduce((acc, r) => {
        acc[r.race] = acc[r.race] || [];
        acc[r.race].push(r);
        return acc;
    }, {});
    Object.values(raceGroupsForCombo).forEach(raceHorses => {
        let bestWeightedAvg = null, bestWeightedAvgScore = -Infinity;
        let bestRecent = null, bestRecentScore = -Infinity;
        raceHorses.forEach(h => {
            const w = h.sectionalDetails?.weightedAvg || 0;
            if (w > bestWeightedAvgScore) { bestWeightedAvgScore = w; bestWeightedAvg = h.name; }
            const rscore = h.sectionalDetails?.bestRecent || 0;
            if (rscore > bestRecentScore) { bestRecentScore = rscore; bestRecent = h.name; }
        });
        raceHorses.forEach(h => {
            if (h.name === bestWeightedAvg) h.hasAverage1st = true;
            if (h.name === bestRecent) h.hasLastStart1st = true;
        });
    });

    return results;
}

// Calculate weight-based scores relative to race average
function calculateWeightScores(data) {
    const results = [];
    const raceGroups = {};
    data.forEach(entry => {
        const raceNum = entry['race number'];
        if (!raceGroups[raceNum]) raceGroups[raceNum] = [];
        raceGroups[raceNum].push(entry);
    });

    Object.keys(raceGroups).forEach(raceNum => {
        const raceHorses = raceGroups[raceNum];
        const uniqueHorses = [];
        const seen = new Set();
        raceHorses.forEach(h => {
            const name = h['horse name'];
            if (!seen.has(name)) { seen.add(name); uniqueHorses.push(h); }
        });

        let totalWeight = 0, validWeights = 0;
        uniqueHorses.forEach(h => {
            const w = parseFloat(h['horse weight']);
            if (!isNaN(w) && w >= 49 && w <= 65) { totalWeight += w; validWeights++; }
        });
        const avgWeight = validWeights > 0 ? totalWeight / validWeights : 55;

        uniqueHorses.forEach(h => {
            const horseName = h['horse name'];
            const currentWeight = parseFloat(h['horse weight']);
            const lastWeight = parseFloat(h['form weight']);
            let score = 0;
            let note = '';

            if (!isNaN(currentWeight) && currentWeight >= 49 && currentWeight <= 65) {
                const diffFromAvg = avgWeight - currentWeight;
                if (diffFromAvg >= 3) { score += 15; note += `+15.0 : Weight ${currentWeight}kg is ${diffFromAvg.toFixed(1)}kg BELOW race avg (${avgWeight.toFixed(1)}kg)\n`; }
                else if (diffFromAvg >= 2) { score += 10; note += `+10.0 : Weight ${currentWeight}kg is ${diffFromAvg.toFixed(1)}kg below race avg (${avgWeight.toFixed(1)}kg)\n`; }
                else if (diffFromAvg >= 1) { score += 6; note += `+ 6.0 : Weight ${currentWeight}kg is ${diffFromAvg.toFixed(1)}kg below race avg (${avgWeight.toFixed(1)}kg)\n`; }
                else if (diffFromAvg >= 0.5) { score += 3; note += `+ 3.0 : Weight ${currentWeight}kg is ${diffFromAvg.toFixed(1)}kg below race avg (${avgWeight.toFixed(1)}kg)\n`; }
                else if (diffFromAvg > -0.5) { note += `  0.0 : Weight ${currentWeight}kg is near race avg (${avgWeight.toFixed(1)}kg)\n`; }
                else if (diffFromAvg > -1) { score -= 3; note += `- 3.0 : Weight ${currentWeight}kg is ${Math.abs(diffFromAvg).toFixed(1)}kg above race avg (${avgWeight.toFixed(1)}kg)\n`; }
                else if (diffFromAvg > -2) { score -= 6; note += `- 6.0 : Weight ${currentWeight}kg is ${Math.abs(diffFromAvg).toFixed(1)}kg above race avg (${avgWeight.toFixed(1)}kg)\n`; }
                else if (diffFromAvg > -3) { score -= 10; note += `-10.0 : Weight ${currentWeight}kg is ${Math.abs(diffFromAvg).toFixed(1)}kg above race avg (${avgWeight.toFixed(1)}kg)\n`; }
                else { score -= 15; note += `-15.0 : Weight ${currentWeight}kg is ${Math.abs(diffFromAvg).toFixed(1)}kg ABOVE race avg (${avgWeight.toFixed(1)}kg)\n`; }
            } else {
                note += `  0.0 : Weight invalid or out of range\n`;
            }

            if (!isNaN(currentWeight) && !isNaN(lastWeight) && lastWeight >= 49 && lastWeight <= 65) {
                const weightChange = lastWeight - currentWeight;
                if (weightChange >= 3) { score += 15; note += `+15.0 : Dropped ${weightChange.toFixed(1)}kg from last start (${lastWeight}kg → ${currentWeight}kg)\n`; }
                else if (weightChange >= 2) { score += 10; note += `+10.0 : Dropped ${weightChange.toFixed(1)}kg from last start (${lastWeight}kg → ${currentWeight}kg)\n`; }
                else if (weightChange >= 1) { score += 5; note += `+ 5.0 : Dropped ${weightChange.toFixed(1)}kg from last start (${lastWeight}kg → ${currentWeight}kg)\n`; }
                else if (weightChange > -1) {}
                else if (weightChange > -2) { score -= 5; note += `- 5.0 : Up ${Math.abs(weightChange).toFixed(1)}kg from last start (${lastWeight}kg → ${currentWeight}kg)\n`; }
                else if (weightChange > -3) { score -= 10; note += `-10.0 : Up ${Math.abs(weightChange).toFixed(1)}kg from last start (${lastWeight}kg → ${currentWeight}kg)\n`; }
                else { score -= 15; note += `-15.0 : Up ${Math.abs(weightChange).toFixed(1)}kg from last start (${lastWeight}kg → ${currentWeight}kg)\n`; }
            }

            results.push({ race: raceNum, name: horseName, weightScore: score, weightNote: note });
        });
    });

    return results;
}

// ==========================================
// RUNNING POSITION SCORING (Speedmap)
// ==========================================
function calculateRunningPositionScore(runningPosition, raceDistance) {
    if (!runningPosition) return [0, ''];
    
    const pos = String(runningPosition).toUpperCase().trim();
    let score = 0;
    let note = '';
    
    const isSprint = raceDistance <= 1200;
    const isMile = raceDistance >= 1300 && raceDistance <= 1700;
    const isMiddle = raceDistance >= 1800 && raceDistance <= 2200;
    const isStaying = raceDistance > 2200;
    
    if (isSprint) {
        if (pos === 'LEADER')      { score = 12; note = '+12.0 : LEADER in Sprint (≤1200m)\n'; }
        else if (pos === 'ONPACE') { score = 8;  note = '+ 8.0 : ONPACE in Sprint\n'; }
        else if (pos === 'MIDFIELD')   { score = 0;  note = '  0.0 : MIDFIELD in Sprint\n'; }
        else if (pos === 'BACKMARKER') { score = -8; note = '- 8.0 : BACKMARKER in Sprint\n'; }
    } else if (isMile) {
        if (pos === 'LEADER')      { score = 6;  note = '+ 6.0 : LEADER in Mile (1300-1700m)\n'; }
        else if (pos === 'ONPACE') { score = 8;  note = '+ 8.0 : ONPACE in Mile - sweet spot\n'; }
        else if (pos === 'MIDFIELD')   { score = 2;  note = '+ 2.0 : MIDFIELD in Mile\n'; }
        else if (pos === 'BACKMARKER') { score = -5; note = '- 5.0 : BACKMARKER in Mile\n'; }
    } else if (isMiddle) {
        if (pos === 'LEADER')      { score = -5; note = '- 5.0 : LEADER in Middle distance (1800-2200m) - may tire\n'; }
        else if (pos === 'ONPACE') { score = 5;  note = '+ 5.0 : ONPACE in Middle distance\n'; }
        else if (pos === 'MIDFIELD')   { score = 3;  note = '+ 3.0 : MIDFIELD in Middle distance\n'; }
        else if (pos === 'BACKMARKER') { score = 0;  note = '  0.0 : BACKMARKER in Middle distance\n'; }
    } else if (isStaying) {
        if (pos === 'LEADER')      { score = -8; note = '- 8.0 : LEADER in Staying race (2400m+) - likely to tire\n'; }
        else if (pos === 'ONPACE') { score = 3;  note = '+ 3.0 : ONPACE in Staying race\n'; }
        else if (pos === 'MIDFIELD')   { score = 5;  note = '+ 5.0 : MIDFIELD in Staying race\n'; }
        else if (pos === 'BACKMARKER') { score = 2;  note = '+ 2.0 : BACKMARKER in Staying race\n'; }
    }
    
    return [score, note];
}

// Calculate "true" odds from scores (Dirichlet-ish approach)
function calculateTrueOdds(results, priorStrength = 0.01, troubleshooting = false, maxRatio = 300.0) {
    const raceGroups = results.reduce((acc, obj) => {
        const raceNumber = obj.horse['race number'];
        if (!acc[raceNumber]) acc[raceNumber] = [];
        acc[raceNumber].push(obj);
        return acc;
    }, {});

    Object.values(raceGroups).forEach(raceHorses => {
        const scores = raceHorses.map(h => h.score);
        
        // Z-SCORE NORMALIZATION TO 0-100 SCALE
        const mean = scores.reduce((sum, s) => sum + s, 0) / scores.length;
        const variance = scores.reduce((sum, s) => sum + Math.pow(s - mean, 2), 0) / scores.length;
        const stdDev = Math.sqrt(variance);
        
        const normalizedScores = scores.map(s => {
            if (stdDev === 0) return 50;
            const zScore = (s - mean) / stdDev;
            let normalized = 50 + (zScore * 16.67);
            return Math.max(0, Math.min(100, normalized));
        });
        
        // ✨ EXPONENTIAL PROBABILITY WEIGHTING (like bookmakers)
        // Use exp(score/k) where k controls steepness
        // k=15 is aggressive, k=20 is moderate, k=25 is conservative
        const k = 15;  // Steepness factor - lower = more aggressive odds spread
        
        const blendedScores = normalizedScores.map((analyzerScore, index) => {
            const pfaiScore = raceHorses[index].pfaiScore || 0;
            return pfaiScore > 0 ? (analyzerScore * 0.7) + (pfaiScore * 0.3) : analyzerScore;
        });
        const expScores = blendedScores.map(score => Math.exp(score / k));
        const totalExp = expScores.reduce((sum, e) => sum + e, 0);
        
        // Convert to probabilities
        const probabilities = expScores.map(e => e / totalExp);
        
        // Add overround (bookmaker margin of 10%)
        const totalProb = probabilities.reduce((sum, p) => sum + p, 0);
        const overround = 1.10;
        
        raceHorses.forEach((horse, index) => {
            const winProbability = probabilities[index];
            const trueOdds = 1 / (winProbability * overround);

            horse.winProbability = (winProbability * 110).toFixed(1) + '%';
            horse.baseProbability = (1 / normalizedScores.length * 100).toFixed(1) + '%';
            horse.trueOdds = `$${trueOdds.toFixed(2)}`;
            horse.rawWinProbability = winProbability * overround;
            horse.performanceComponent = ((normalizedScores[index] / 100) * 100).toFixed(1) + '%';
            horse.score = blendedScores[index];
            horse.rawScore = scores[index];  // Keep original

            // ✨ ADD THIS BLOCK HERE ↓
const pfaiScore = horse.pfaiScore || 0;
if (pfaiScore > 0) {
    const analyzerNormalized = normalizedScores[index];
    const blendedFinal = blendedScores[index];
    let blendNote = '\n=== PFAI BLEND ===\n';
    blendNote += `Analyzer Score (normalized): ${analyzerNormalized.toFixed(1)} (70% weight)\n`;
    blendNote += `PFAI Score: ${pfaiScore.toFixed(1)} (30% weight)\n`;
    blendNote += `Final Blended Score: ${blendedFinal.toFixed(1)}\n`;
    blendNote += `Calculation: (${analyzerNormalized.toFixed(1)} × 0.7) + (${pfaiScore.toFixed(1)} × 0.3) = ${blendedFinal.toFixed(1)}\n`;
    horse.notes = (horse.notes || '') + blendNote;
}
        });

        const totalCheck = raceHorses.reduce((s, h) => s + (h.rawWinProbability || 0), 0);
        if (totalCheck < 1.09 || totalCheck > 1.11) {
            throw new Error(`Race ${raceHorses[0].horse['race number']} probabilities adding to ${(totalCheck*100).toFixed(2)}%`);
        }
    });

    return results;
}
// Simple CSV parser and helpers
function parseCSVLine(line) {
    const result = [];
    let current = '';
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
        const char = line[i];
        if (char === '"') inQuotes = !inQuotes;
        else if (char === ',' && !inQuotes) { result.push(current); current = ''; }
        else current += char;
    }
    result.push(current);
    return result;
}

function parseCSV(csvString) {
    const lines = String(csvString).trim().split('\n');
    if (lines.length === 0) return [];
    const headers = parseCSVLine(lines[0]);
    const data = [];
    for (let i = 1; i < lines.length; i++) {
        const values = parseCSVLine(lines[i]);
        if (values.length === headers.length) {
            const row = {};
            headers.forEach((h, idx) => { row[h.trim().toLowerCase()] = values[idx].trim(); });
            data.push(row);
        }
    }
    return data;
}

function parseDate(dateStr) {
    if (!dateStr) return new Date(0);
    const datePart = String(dateStr).split(' ')[0];
    const parts = datePart.split('/');
    if (parts.length !== 3) return new Date(0);
    const day = parseInt(parts[0], 10);
    const month = parseInt(parts[1], 10) - 1;
    let year = parseInt(parts[2], 10);
    if (year < 100) year += (year <= 50) ? 2000 : 1900;
    return new Date(year, month, day);
}

function getUniqueHorsesOnly(data) {
    const latestByComposite = new Map();
    data.forEach(entry => {
        const compositeKey = `${entry['horse name']}-${entry['race number']}`;
        const currentDate = entry['form meeting date'];
        if (!latestByComposite.has(compositeKey) || parseDate(currentDate) > parseDate(latestByComposite.get(compositeKey)['form meeting date'])) {
            latestByComposite.set(compositeKey, entry);
        }
    });
    return Array.from(latestByComposite.values());
}

// Main analysis function
function analyzeCSV(csvData, trackCondition = 'good', isAdvanced = false) {
    let data = parseCSV(csvData);
    if (!data || data.length === 0) return [];
    data = data.filter(row => {
        const horseName = String(row['horse name'] || '').trim().toLowerCase();
        if (!horseName || horseName === 'horse name') return false;
        const raceNum = String(row['race number'] || '').trim();
        if (!raceNum || isNaN(parseInt(raceNum, 10)) || raceNum.toLowerCase() === 'race number') return false;
        return true;
    });
    if (!data.length) return [];
    
    const analysisResults = [];
    const filteredDataSectional = getLowestSectionalsByRace(data);
    const averageFormPrices = calculateAverageFormPrices(data);
    const weightScores = calculateWeightScores(data);
    const uniqueHorses = getUniqueHorsesOnly(data);

        uniqueHorses.forEach(horse => {
    if (!horse['horse name']) return;
    
    const compositeKey = `${horse['horse name']}-${horse['race number']}`;
    const avgFormPrice = averageFormPrices[compositeKey];
    const raceNumber = horse['race number'];
    const horseName = horse['horse name'];
    
    const matchingHorseForContext = filteredDataSectional.find(h => 
        parseInt(h.race) === parseInt(raceNumber) && 
        h.name.toLowerCase().trim() === horseName.toLowerCase().trim()
    );
    
    const sectionalDetailsForContext = matchingHorseForContext ? {
        bestRecent: matchingHorseForContext.sectionalDetails?.bestRecent || 0,
        weightedAvg: matchingHorseForContext.sectionalDetails?.weightedAvg || 0
    } : null;
    
    let [score, notes] = calculateScore(horse, trackCondition, false, avgFormPrice, sectionalDetailsForContext);
        
        // ==========================================
        // SECTIONAL SCORING - API PRIMARY, CSV FALLBACK
        // ==========================================
        
       const hasApiSectionalData = horse['last200timeprice'] && parseFloat(horse['last200timeprice']) > 0;
        
        if (hasApiSectionalData) {
        // USE API SECTIONAL PRICE/RANK SCORING
        const raceDistance = parseInt(horse['distance'] || horse['race distance'], 10) || 1400;
        const apiSectionalResult = calculateApiSectionalScore(horse, raceDistance);
    
        score += apiSectionalResult.score;
    
        // ENSURE notes exist and append API sectionals
        if (!notes) notes = '';
        notes += '\n=== SECTIONAL ANALYSIS (API) ===\n';
        notes += apiSectionalResult.note || '';
    
        console.error(`DEBUG API SECTIONALS: ${horse['horse name']}: score=${apiSectionalResult.score}, note length=${apiSectionalResult.note?.length || 0}`);

        }
        
        // ALWAYS run CSV sectional z-score scoring (history + consistency)
        const matchingHorse = filteredDataSectional.find(h => 
            parseInt(h.race) === parseInt(raceNumber) && 
            h.name.toLowerCase().trim() === horseName.toLowerCase().trim()
        );
        
        if (matchingHorse) {
            const sectionalWeight = horse._sectionalWeight || 1.0;
            const originalSectionalScore = matchingHorse.sectionalScore;
            const adjustedSectionalScore = originalSectionalScore * sectionalWeight;
            score += adjustedSectionalScore;
            if (sectionalWeight !== 1.0) {
                notes += `ℹ️  Sectional weight applied: ${originalSectionalScore.toFixed(1)} × ${sectionalWeight.toFixed(2)} = ${adjustedSectionalScore.toFixed(1)}\n`;
            }
            notes += matchingHorse.sectionalNote;
        }
        
        // CLASS DROP + SLOW SECTIONAL COMBO (works with both API and CSV)
        const todayClassScore = calculateClassScore(horse['class restrictions'], horse['race prizemoney']);
        const lastClassScore = calculateClassScore(horse['form class'], horse['prizemoney']);
        const classChange = todayClassScore - lastClassScore;
        
        // Parse raw sectional for the combo detection
        const sectionalMatch = String(horse['sectional'] || '').match(/(\d+\.?\d*)sec/);
        const rawSectional = sectionalMatch ? parseFloat(sectionalMatch[1]) : null;
        
        if (classChange < -30 && rawSectional && rawSectional >= 37) {
            score += 30;
            notes += '+30.0 : Major class drop + slow sectional combo (100% SR, +85% ROI - elite to easier)\n';
        }
        
        // WEIGHT SCORING
        const matchingWeight = weightScores.find(w => 
            parseInt(w.race) === parseInt(raceNumber) && 
            w.name.toLowerCase().trim() === horseName.toLowerCase().trim()
        );
        
        if (matchingWeight) {
            score += matchingWeight.weightScore;
            notes += matchingWeight.weightNote;
        }

        analysisResults.push({ horse, score, notes, pfaiScore: parseFloat(horse['pfaiscore']) || 0 });
    });
    
    return calculateTrueOdds(analysisResults, 0.05, false, 300);
}

// Read from stdin
let inputData = '';
process.stdin.on('data', chunk => { inputData += chunk; });
process.stdin.on('end', () => {
    try {
        // Try parsing as JSON first (new format)
        const input = JSON.parse(inputData);
        const results = analyzeCSV(input.csv_data, input.track_condition, input.is_advanced);
        console.log(JSON.stringify(results));
    } catch (jsonError) {
        // Fallback to old format: CSV + track condition on last line
        const lines = inputData.trim().split('\n');
        const csvData = lines.slice(0, -1).join('\n');
        const trackCondition = lines[lines.length - 1] || 'good';
        const results = analyzeCSV(csvData, trackCondition);
        console.log(JSON.stringify(results));
    }
});
