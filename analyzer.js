// Mapping of equivalent jockey names
const jockeyMapping = {
    "J B Mc Donald": "James McDonald",
    "A Bullock": "Aaron Bullock",
    "W Pike": "William Pike",
    "N Rawiller": "Nash Rawiller",
    "J Parr": "Josh Parr",
    "J R Collett": "Jason Collett",
    "M Zahra": "Mark Zahra",
    "B Shinn": "Blake Shinn",
    "C Williams": "Craig Williams",
    "E Brown": "Ethan Brown",
    "D Lane": "Damian Lane",
    "B Melham": "Ben Melham",
    "T Berry": "Tommy Berry",
    "H Coffey": "Hollie Coffey",
    "B Avdulla": "Brenton Avdulla",
    "J Ford": "Jay Ford",
    "R King": "Rachel King",
    "H Bowman": "Hugh Bowman",
    "K Mc Evoy": "Kerrin McEvoy",
    "A Livesey": "Alana Livesey",
    "T Clark": "Tim Clark",
    "L Cartwright": "Luke Cartwright",
    "T Schiller": "Tyler Schiller",
    "A Warren": "Alysha Warren",
    "C Tootell": "Caitlin Tootell",
    "B Thompson": "Ben Thompson",
    "Z Lloyd": "Zac Lloyd",
    "J McNeil": "Jye McNeil",
    "R Maloney": "Ryan Maloney",
    "J Childs": "Jordan Childs",
    "H Nottle": "Holly Nottle",
    "D Gibbons": "Dylan Gibbons",
    "J Radley": "Jackson Radley",
    "L Neindorf": "Lachlan Neindorf",
    "J Allen": "John Allen",
    "L Bates": "Logan Bates",
    "C Sutherland": "Corey Sutherland",
    "T Nugent": "Teodore Nugent",
    "B Allen": "Ben Allen",
    "R Houston": "Ryan Houston",
    "K Wilson-Taylor": "Kyle Wilson-Taylor",
    "E Pozman": "Emily Pozman",
    "A Roper": "Anna Roper",
    "T Stockdale": "Thomas Stockdale",
    "C Parnham": "Chris Parnham",
    "R Bayliss": "Regan Bayliss",
    "A Morgan": "Ashley Morgan",
    "S Grima": "Siena Grima",
    "C Graham": "Cejay Graham",
    "T Sherry": "Tom Sherry",
    "C Hefel": "Carleen Hefel",
    "K Crowther": "Kayla Crowther",
    "D Thornton": "Damien Thornton",
    "B Mertens": "Beau Mertens",
    "H Watson": "Holly Watson",
    "W Stanley": "William Stanley",
    "R Jones": "Reece Jones",
};

// Mapping of equivalent trainer names
const trainerMapping = {
    'C Maher': 'Ciaron Maher',
    'C J Waller': 'Chris Waller',
    'Ben Will & Jd Hayes': 'Ben, Will & J.D. Hayes',
    'G Waterhouse & A Bott': 'Gai Waterhouse & Adrian Bott',
    'G M Begg': 'Grahame Begg',
    'P Stokes': 'Phillip Stokes',
    'M M Laurie': 'Matthew Laurie',
    'K Lees': 'Kris Lees',
    'K A Lees': 'Kris Lees',
    'J Cummings': 'James Cummings',
    'J Pride': 'Joseph Pride',
    'J O\'Shea': 'John O\'Shea',
    'P Moody': 'Peter Moody',
    'R D Griffiths': 'R D Griffiths',
    'T Busuttin & N Young': 'T Busuttin & N Young',
    'S W Kendrick': 'S W Kendrick',
    'T & C McEvoy': 'T & C McEvoy',
    'A & S Freedman': 'A & S Freedman',
    'R L Heathcote': 'R L Heathcote',
    'D T O\'Brien': 'D T O\'Brien',
    'Annabel & Rob Archibald': 'Annabel & Rob Archibald',
    'Matthew Smith': 'Matthew Smith',
    'Gavin Bedggood': 'Gavin Bedggood',
    'Chris & Corey Munce': 'Chris & Corey Munce',
    'P G Moody & Katherine Coleman': 'P G Moody & Katherine Coleman',
    'T J Gollan': 'T J Gollan',
    'Ben Brisbourne': 'Ben Brisbourne',
    'G Ryan & S Alexiou': 'G Ryan & S Alexiou',
    'Peter Snowden': 'Peter Snowden',
    'P Snowden': 'Peter Snowden',
    'M Price & M Kent Jnr': 'M Price & M Kent Jnr',
    'Bjorn Baker': 'Bjorn Baker',
    'B Baker': 'Bjorn Baker',
    'Patrick & Michelle Payne': 'Patrick & Michelle Payne',
    'N D Parnham': 'N D Parnham',
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
    
    // === MAJOR EDGE: 5YO HORSES (NOT MARES) ===
    // 236% ROI, 36.4% SR - HUGE DISCOVERY
    if (horseAge === 5 && horseSex !== 'Mare') {
        score += 25;
        notes += '+25.0 : 5yo horse (236% ROI, 36.4% SR - elite age)\n';
    }
    
    // === MAJOR EDGE: 8YO MARES ===
    // 118% ROI, 7.7% SR - Specialist pattern
    if (horseAge === 8 && horseSex === 'Mare') {
        score += 20;
        notes += '+20.0 : 8yo Mare (118% ROI - specialist)\n';
    }
    
    // === COLT BONUS ===
    // 82.7% ROI, 20% SR - Proven edge
    if (horseSex === 'Colt') {
        score += 20;
        notes += '+20.0 : COLT (82.7% ROI)\n';
    }
    
    // === STANDARD AGE BONUSES (REDUCED) ===
    // 3yo: Only -4.6% ROI - reduced from +5 to +3
    if (horseAge === 3) {
        score += 3;
        notes += '+ 3.0 : Prime age (3yo)\n';
    }
    
    // 4yo: -26.6% ROI - reduced from +3 to +2
    if (horseAge === 4) {
        score += 2;
        notes += '+ 2.0 : Good age (4yo)\n';
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
    // ELITE PERFORMERS (50%+ ROI)
    'Wootton Bassett': { runners: 38, roi: 508.4, strikeRate: 21.1 },
    'Rommel': { runners: 31, roi: 221.9, strikeRate: 19.4 },
    'The Mission': { runners: 20, roi: 156.5, strikeRate: 30.0 },
    'I\'m All The Talk': { runners: 27, roi: 139.3, strikeRate: 18.5 },
    'Night Of Thunder': { runners: 17, roi: 129.7, strikeRate: 41.2 },
    'Starspangledbanner': { runners: 42, roi: 90.0, strikeRate: 9.5 },
    'Tagaloa': { runners: 28, roi: 89.8, strikeRate: 21.4 },
    'Trapeze Artist': { runners: 86, roi: 47.2, strikeRate: 17.4 },
    'Reward For Effort': { runners: 33, roi: 52.4, strikeRate: 18.2 },
    'Stratum Star': { runners: 35, roi: 47.4, strikeRate: 20.0 },
    
    // STRONG VALUE (20-50% ROI)
    'Pierata': { runners: 70, roi: 36.9, strikeRate: 18.6 },
    'Proisir': { runners: 32, roi: 36.7, strikeRate: 28.1 },
    'Lonhro': { runners: 56, roi: 30.3, strikeRate: 19.6 },
    'Rothesay': { runners: 38, roi: 30.8, strikeRate: 18.4 },
    'Super Seth': { runners: 31, roi: 29.8, strikeRate: 29.0 },
    'Russian Revolution': { runners: 106, roi: 26.0, strikeRate: 15.1 },
    
    // GOOD VALUE (10-20% ROI)
    'Epaulette': { runners: 55, roi: 17.8, strikeRate: 14.5 },
    'Territories': { runners: 55, roi: 14.2, strikeRate: 14.5 },
    'Star Turn': { runners: 90, roi: 13.7, strikeRate: 11.1 },
    'Divine Prophet': { runners: 62, roi: 11.3, strikeRate: 24.2 },
    
    // MODERATE VALUE (5-10% ROI)
    'Capitalist': { runners: 165, roi: 8.6, strikeRate: 13.3 },
    'Frosted': { runners: 87, roi: 8.2, strikeRate: 11.5 },
    'Tassort': { runners: 39, roi: 6.4, strikeRate: 20.5 },
    
    // MARKET EFFICIENT (-5 to +5% ROI)
    'Savabeel': { runners: 78, roi: -1.0, strikeRate: 14.1 },
    'Snitzel': { runners: 156, roi: 0.5, strikeRate: 11.5 },
    'Churchill': { runners: 80, roi: 0.3, strikeRate: 17.5 },
    "D'argento": { runners: 44, roi: -0.7, strikeRate: 18.2 },
    
    // MODERATE NEGATIVE (-20 to -40% ROI)
    'Pierro': { runners: 128, roi: -12.7, strikeRate: 14.1 },
    'Hellbent': { runners: 129, roi: -18.2, strikeRate: 11.6 },
    'Toronado': { runners: 184, roi: -18.2, strikeRate: 13.0 },
    'All Too Hard': { runners: 103, roi: -18.6, strikeRate: 11.7 },
    'Zoustar': { runners: 180, roi: -31.6, strikeRate: 10.6 },
    'I Am Invincible': { runners: 142, roi: -39.5, strikeRate: 10.6 },
    'So You Think': { runners: 155, roi: -30.5, strikeRate: 10.3 },
    
    // SEVERE DESTROYERS (-40 to -60% ROI)
    'Dundeel': { runners: 144, roi: -45.4, strikeRate: 9.0 },
    'The Autumn Sun': { runners: 75, roi: -48.3, strikeRate: 10.7 },
    'Maurice': { runners: 64, roi: -49.7, strikeRate: 12.5 },
    'Deep Field': { runners: 113, roi: -51.1, strikeRate: 12.4 },
    'Spirit Of Boom': { runners: 89, roi: -52.9, strikeRate: 6.7 },
    'Written Tycoon': { runners: 159, roi: -53.4, strikeRate: 11.3 },
    'Sir Prancealot': { runners: 57, roi: -54.9, strikeRate: 5.3 },
    'Farnan': { runners: 42, roi: -55.7, strikeRate: 16.7 },
    'Better Than Ready': { runners: 111, roi: -59.8, strikeRate: 8.1 },
    
    // DISASTER SIRES (-60%+ ROI)
    'Too Darn Hot': { runners: 78, roi: -61.8, strikeRate: 9.0 },
    'Almanzor': { runners: 58, roi: -64.0, strikeRate: 12.1 },
    'Bjorn Baker': { runners: 101, roi: -64.5, strikeRate: 11.9 },
    'Shamus Award': { runners: 95, roi: -65.8, strikeRate: 11.6 },
    'Shalaa': { runners: 81, roi: -67.0, strikeRate: 8.6 },
    'Alpine Eagle': { runners: 56, roi: -67.0, strikeRate: 7.1 },
    'Ocean Park': { runners: 67, roi: -70.4, strikeRate: 4.5 },
    'Pariah': { runners: 82, roi: -75.1, strikeRate: 3.7 },
    'Zousain': { runners: 71, roi: -76.7, strikeRate: 9.9 },
    'Impending': { runners: 86, roi: -78.6, strikeRate: 4.7 },
    'Star Witness': { runners: 48, roi: -80.2, strikeRate: 4.2 },
    'Rubick': { runners: 70, roi: -90.4, strikeRate: 2.9 },
    'Super One': { runners: 58, roi: -91.7, strikeRate: 1.7 },
    'Shooting To Win': { runners: 60, roi: -94.3, strikeRate: 1.7 },
    'Outreach': { runners: 39, roi: -100.0, strikeRate: 0.0 }
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
                score += 15;
                notes += '+15.0 : Elite career win rate (40%+, 30.8% SR)\n';
            } else if (careerWinPct >= 30) {
                score += 8;
                notes += '+ 8.0 : Strong career win rate (30-40%)\n';
            } else if (careerWinPct < 10) {
                score -= 10;
                notes += '-10.0 : Poor career win rate (<10%)\n';
            }
        }
    }
}
    // CLOSE LOSS BONUS - UPDATED 2025-01-06 (increased from 5 to 7, extended to 2.5L)
const lastMargin = parseFloat(horseRow['form margin']);
const lastPosition = parseInt(horseRow['form position']);
if (!isNaN(lastMargin) && !isNaN(lastPosition) && lastPosition > 1 && lastMargin > 0 && lastMargin <= 2.5) {
    score += 7;  // INCREASED from 5
    notes += '+7.0: Close loss last start (0.5-2.5L) - very competitive\n';
}
    // NEW: 3YO COLT COMBO
if (horseSex === 'Colt' && horseAge === 3) {
    score += 20;
    notes += '+20.0 : 3yo COLT combo (44.4% SR, +33% ROI)\n';
}
    // Check for perfect record specialist bonus
    const perfectRecordResult = calculatePerfectRecordBonus ? calculatePerfectRecordBonus(horseRow, trackCondition) : { bonus: 0, note: '' };
    if (perfectRecordResult && perfectRecordResult.bonus > 0) {
        score += perfectRecordResult.bonus;
        notes += perfectRecordResult.note;
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
    // guard against undefined/null
    if (!jockeyName || typeof jockeyName !== 'string') return jockeyName || '';
    const jr = jockeyName.trim();
    for (const [key, value] of Object.entries(jockeyMapping)) {
        if (jr.startsWith(key)) {
            return jr.replace(key, value);
        }
    }
    return jr; // Return the original if no mapping is found
}

function checkJockeys(JockeyName) {
    // ==========================================
    // JOCKEY SCORING - UPDATED 2025-01-30
    // Based on 1203 race analysis
    // ==========================================
    var addScore = 0;
    var note = '';
    
    // Normalize name for mapping
    JockeyName = normalizeJockeyName(JockeyName);
    
    // TIER 1: Elite Value (50%+ ROI) - MASSIVE EDGE
    const eliteValueJockeys = [
        'Ethan Brown',        // 498% ROI, 22% SR - EXTRAORDINARY
        'Tim Clark',          // 61% ROI, 24.2% SR
        'R Mc Leod',          // 70.9% ROI, 8.9% SR
        'C J Hillier',        // 73.4% ROI, 27.6% SR
        'Robert Whearty',     // 72.9% ROI, 29.4% SR
        'Codi Jordan',        // 67.1% ROI, 33.3% SR
    ];
    
    // TIER 2: Strong Value (20-50% ROI) - PROVEN EDGE
    const strongValueJockeys = [
        'L Magorrian',        // 151% ROI, 14.8% SR
        'Alana Livesey',      // 119% ROI, 12% SR
        'A Adkins',           // 102% ROI, 16.3% SR
        'S Clipperton',       // 96.8% ROI, 20.6% SR
        'Luke Cartwright',    // 31.1% ROI, 12.9% SR
        'Caitlin Tootell',    // 26.6% ROI, 23.4% SR
        'Tyler Schiller',     // 23.6% ROI, 14.3% SR
        'M R Du Plessis',     // 38.5% ROI, 13.2% SR
        'M Rodd',             // 31.1% ROI, 15.9% SR
    ];
    
    // TIER 3: Profitable (0-20% ROI) - SMALL EDGE
    const profitableJockeys = [
        'J Mott',             // 19.5% ROI, 20.2% SR
        'W Gordon',           // 19.2% ROI, 9.6% SR
        'Alysha Warren',      // 18.3% ROI, 17% SR
        'G Buckley',          // 10.4% ROI, 17.6% SR
        'Ben Thompson',       // 5% ROI, 16.4% SR
        'H Coffey',           // 4.9% ROI, 16.5% SR
        'L Currie',           // 3.7% ROI, 14.1% SR
    ];
    
    // TIER 4: Value Destroyers (-40%+ ROI) - AVOID
    const badJockeys = [
        'K Mc Evoy',          // -88.8% ROI, 3.8% SR - DISASTER
        'Craig Williams',     // -76.2% ROI, 7.7% SR
        'J Ford',             // -73.3% ROI, 7.1% SR
        'S Parnham',          // -73.2% ROI, 4.5% SR
        'A B Collett',        // -72.6% ROI, 5.6% SR
        'Damien Thornton',    // -64.7% ROI, 8.7% SR
        'J R Collett',        // -63.3% ROI, 12.3% SR - Jason Collett
        'Beau Mertens',       // -62.2% ROI, 8.2% SR
        'Carleen Hefel',      // -61.9% ROI, 11.6% SR
        'Kayla Crowther',     // -55% ROI, 4% SR
        'Holly Watson',       // -52.4% ROI, 8.2% SR
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
    // Based on 1203 race analysis
    // ==========================================
    var addScore = 0;
    var note = '';
    
    // Normalize name for mapping
    trainerName = normalizeTrainerName(trainerName);
    
    // TIER 1: Elite Value (50%+ ROI) - MASSIVE EDGE
    const eliteValueTrainers = [
        'C Maher', 'Ciaron Maher',              // 59.4% ROI, 14.2% SR, 225 runners - THE KING
        'Richard & Will Freedman',              // 55.1% ROI, 16.2% SR, 37 runners
        'D R Harrison',                         // 51.1% ROI, 22.2% SR
        'G J Stevenson',                        // 49.4% ROI, 35.3% SR
    ];
    
    // TIER 2: Strong Value (20-50% ROI) - PROVEN EDGE
    const strongValueTrainers = [
        'T Busuttin & N Young',                 // 26.4% ROI, 17.4% SR, 46 runners
        'M A Currie',                           // 18.5% ROI, 18.2% SR, 44 runners
        'S I Singleton',                        // 18.3% ROI, 15.8% SR, 38 runners
    ];
    
    // TIER 3: Profitable (5-20% ROI) - SMALL EDGE
    const profitableTrainers = [
        'Craig Weeding',                        // 12.4% ROI, 10.3% SR
        'G Portelli',                           // 12% ROI, 20% SR, 40 runners
        'R D Griffiths',                        // 11.3% ROI, 9.6% SR, 52 runners
        'John O\'Shea & Tom Charlton',         // 8.9% ROI, 20.5% SR, 44 runners
        'S W Kendrick',                         // 8.7% ROI, 10.9% SR, 55 runners
        'G Waterhouse & A Bott',                // 7.2% ROI, 23% SR, 74 runners
        'Gai Waterhouse & Adrian Bott',        // 7.2% ROI, 23% SR, 74 runners
        'R L Heathcote',                        // 6.2% ROI, 12.5% SR, 64 runners
        'Tom Dabernig',                         // 6.1% ROI, 28.6% SR, 35 runners
    ];
    
    // TIER 4: Value Destroyers (-40%+ ROI) - AVOID
    const badTrainers = [
        'N D Parnham',                          // -100% ROI, 0% SR, 53 runners - DISASTER
        'Patrick & Michelle Payne',             // -67.6% ROI, 6.8% SR, 44 runners
        'Bjorn Baker', 'B Baker',              // -64.5% ROI, 11.9% SR, 101 runners
        'Ben Will & Jd Hayes',                  // -59.3% ROI, 9.5% SR, 189 runners
        'Peter Snowden', 'P Snowden',          // -59.3% ROI, 12.5% SR, 56 runners
        'G Ryan & S Alexiou',                   // -49.5% ROI, 11.6% SR, 43 runners
        'Ben Brisbourne',                       // -43.5% ROI, 11.3% SR, 62 runners
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
        undefeatedBonus = 20;
        note += '+ 20.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + '! (5+ runs) [+154% ROI]\n';
    } else if (runs >= 4) {
        undefeatedBonus = 17;
        note += '+ 17.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + '! (4 runs)\n';
    } else if (runs >= 3) {
        undefeatedBonus = 15;
        note += '+ 15.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + '! (3 runs)\n';
    } else {
        undefeatedBonus = 10;
        note += '+ 10.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + '! (2 runs)\n';
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
        addScore = 15;
        note += `+15.0 : Quick backup - only ${daysSinceLastRun} days since last run (market underrates!)\n`;
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

function checkFirstUpSecondUp(horseRow) {
    let addScore = 0;
    let note = '';

    const last10 = String(horseRow['horse last10'] || '');
    const firstUpRecord = horseRow['horse record first up'];
    const secondUpRecord = horseRow['horse record second up'];

    // Determine if horse is first up or second up today
    let isFirstUp = false;
    let isSecondUp = false;

    // If the most recent character is 'x' (or 'X'), treat as first-up marker
    if (last10.toLowerCase().endsWith('x')) {
        isFirstUp = true;
    } else if (last10.length >= 2) {
        const lastChar = last10.charAt(last10.length - 1);
        const secondLastChar = last10.charAt(last10.length - 2);
        // Second up means: previous run was 'x' and current run is a single digit
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
                addScore += 4;
                note += `+ 4.0 : First-up winner(s) in ${wins} of ${runs} runs\n`;
            }
            if (podiumRate >= 0.5) {
                addScore += 3;
                note += `+ 3.0 : Strong first-up podium rate (${(podiumRate*100).toFixed(0)}%)\n`;
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

    if (!isFirstUp && !isSecondUp && last10.length > 0 && /x/i.test(last10)) {
        // Rare/unusual pattern - mild penalty for uncertain spell markers
        addScore -= 1;
        note += `- 1.0 : Unclear spell/return status (markers present but pattern not first/second-up)\n`;
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
            const baseBonus = 20; // base specialist bonus
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
                addScore += 4;
                note += `+ 4.0 : First-up winner(s) in ${wins} of ${runs} runs\n`;
            }
            if (podiumRate >= 0.5) {
                addScore += 3;
                note += `+ 3.0 : Strong first-up podium rate (${(podiumRate*100).toFixed(0)}%)\n`;
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
            horseScores[h.horseName].note += `+${points.toFixed(1)}: best of last ${h.fromLast} (z=${zScoreText})\n  └─ ${rawVsAdj} (${adjText}) in ${classInfo} (${classScore.toFixed(0)} pts)\n`;
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

// Calculate "true" odds from scores (Dirichlet-ish approach)
function calculateTrueOdds(results, priorStrength = 0.05, troubleshooting = false, maxRatio = 300.0) {
    const raceGroups = results.reduce((acc, obj) => {
        const raceNumber = obj.horse['race number'];
        if (!acc[raceNumber]) acc[raceNumber] = [];
        acc[raceNumber].push(obj);
        return acc;
    }, {});

    Object.values(raceGroups).forEach(raceHorses => {
        const scores = raceHorses.map(h => h.score);
        const minScore = Math.min(...scores);
        const maxScore = Math.max(...scores);
        const range = maxScore - minScore;
        const minShiftForRatio = range > 0 ? range / (maxRatio - 1) : 1.0;
        const basicShift = minScore < 0 ? Math.abs(minScore) + 0.01 : 0;
        const shift = Math.max(basicShift, minShiftForRatio * 0.5);

        const adjustedScores = scores.map(s => s + shift);
        const posteriorCounts = adjustedScores.map(score => score + priorStrength);
        const totalCounts = posteriorCounts.reduce((s, v) => s + v, 0);

        const baseProbability = priorStrength / totalCounts;

        raceHorses.forEach((horse, index) => {
            const winProbability = posteriorCounts[index] / totalCounts;
            const trueOdds = 1 / (winProbability * 1.10); // scale factor to avoid direct normalization issues

            horse.winProbability = (winProbability * 110).toFixed(1) + '%';
            horse.baseProbability = (baseProbability * 100).toFixed(1) + '%';
            horse.trueOdds = `$${trueOdds.toFixed(2)}`;
            horse.rawWinProbability = winProbability * 1.10;
            horse.performanceComponent = ((adjustedScores[index] / totalCounts) * 100).toFixed(1) + '%';
            horse.adjustedScore = adjustedScores[index];
        });

        const totalProb = raceHorses.reduce((s, h) => s + (h.rawWinProbability || 0), 0);
        if (totalProb < 1.09 || totalProb > 1.11) {
            throw new Error(`Race ${raceHorses[0].horse['race number']} probabilities adding to ${(totalProb*100).toFixed(2)}%`);
        }

        if (troubleshooting) {
            // Optional logs...
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

// Main analysis function (cleaned)
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
        if (!horse['meeting date'] || !horse['horse name']) return;
        const compositeKey = `${horse['horse name']}-${horse['race number']}`;
        const avgFormPrice = averageFormPrices[compositeKey];
        const raceNumber = horse['race number'];
        const horseName = horse['horse name'];
        const matchingHorseForContext = filteredDataSectional.find(h => parseInt(h.race) === parseInt(raceNumber) && h.name.toLowerCase().trim() === horseName.toLowerCase().trim());
        const sectionalDetailsForContext = matchingHorseForContext ? {
            bestRecent: matchingHorseForContext.sectionalDetails?.bestRecent || 0,
            weightedAvg: matchingHorseForContext.sectionalDetails?.weightedAvg || 0
        } : null;
        let [score, notes] = calculateScore(horse, trackCondition, false, avgFormPrice, sectionalDetailsForContext);
        const matchingHorse = filteredDataSectional.find(h => parseInt(h.race) === parseInt(raceNumber) && h.name.toLowerCase().trim() === horseName.toLowerCase().trim());
        if (matchingHorse) {
            const sectionalWeight = horse._sectionalWeight || 1.0;
            const originalSectionalScore = matchingHorse.sectionalScore;
            const adjustedSectionalScore = originalSectionalScore * sectionalWeight;
            score += adjustedSectionalScore;
            if (sectionalWeight !== 1.0) notes += `ℹ️  Sectional weight applied: ${originalSectionalScore.toFixed(1)} × ${sectionalWeight.toFixed(2)} = ${adjustedSectionalScore.toFixed(1)}\n`;
            notes += matchingHorse.sectionalNote;
            // NEW: Fast sectional + Colt combo bonus
            const horseSex = String(horse['horse sex'] || '').trim();
            const rawSectional = parseFloat(String(horse['sectional'] || '').match(/(\d+\.?\d*)sec/)?.[1]);
            
            if (horseSex === 'Colt' && rawSectional && rawSectional < 34) {
                score += 25;
                notes += '+25.0 : Fast sectional + COLT combo (44.4% SR, +33% ROI)\n';
            }
            // NEW: Major class drop + slow sectional combo
            const todayClassScore = calculateClassScore(horse['class restrictions'], horse['race prizemoney']);
            const lastClassScore = calculateClassScore(horse['form class'], horse['prizemoney']);
            const classChange = todayClassScore - lastClassScore;
            
            if (classChange < -30 && rawSectional && rawSectional >= 37) {
                score += 30;
                notes += '+30.0 : Major class drop + slow sectional combo (100% SR, +85% ROI - elite to easier)\n';
            }
        }  // CLOSE if (matchingHorse)
        
        const matchingWeight = weightScores.find(w => parseInt(w.race) === parseInt(raceNumber) && w.name.toLowerCase().trim() === horseName.toLowerCase().trim());
        if (matchingWeight) {
            score += matchingWeight.weightScore;
            notes += matchingWeight.weightNote;
        }
        analysisResults.push({ horse, score, notes });
    });

    // De-duplicate by horse name AND race number (preserve dual nominations)
let uniqueResults = Array.from(
    new Map(
        analysisResults.map(item => [
            `${item.horse['horse name']}-${item.horse['race number']}`, 
            item
        ])
    ).values()
);

    uniqueResults = calculateTrueOdds(uniqueResults, 1, false);

    uniqueResults.sort((a, b) =>
        (parseInt(a.horse['race number'], 10) - parseInt(b.horse['race number'], 10)) ||
        (b.score - a.score)
    );

    return uniqueResults;
}

// STDIN/STDOUT handler
let inputData = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { inputData += chunk; });
process.stdin.on('end', () => {
    if (!inputData.trim()) {
        console.error('Error: No input data received.');
        process.exit(1);
    }
    let input;
    try { input = JSON.parse(inputData); } catch (err) {
        console.error('Error: Invalid JSON input.', err.message);
        process.exit(1);
    }
    const csvData = input.csv_data || '';
    const trackCondition = input.track_condition || 'good';
    const isAdvanced = input.is_advanced || false;
    try {
        const results = analyzeCSV(csvData, trackCondition, isAdvanced);
        console.log(JSON.stringify(results));
    } catch (error) {
        console.error('Error processing input:', (error && error.message) || error);
        process.exit(1);
    }
});
