// Mapping of equivalent jockey names
const jockeyMapping = {
    "J B Mc Donald": "James McDonald",
    "A Bullock": "Aaron Bullock",
    "W Pike": "William Pike",
    "N Rawiller": "Nash Rawiller",
    "J Part": "Josh Parr",
    "J R Collett": "Jason Collett",
    "M Zahra": "Mark Zahra",
    "B Shinn": "Blake Shinn",
    "C Williams": "Craig Williams",
    "E Brown": "Ethan Brown",
    "D Lane": "Damian Lane",
    "B Melham": "Ben Melham",
    //"J Kah": "" ???
    // Add more mappings as needed
};


// Mapping of equivalent trainer names
const trainerMapping = {
    'C Maher': 'Ciaron Maher',
    'C J Waller': 'Chris Waller',
    'Ben Will & Jd Hayes': 'Ben, Will & J.D. Hayes',
    'G Waterhouse & A Bott': 'Gai Waterhouse & Adrian Bott',
    'G M Begg': 'Grahame Begg',
    'P Stokes': 'Phillip Stokes',
    'M M Laurie': 'Matthew Laurie'
};

   
function convertCSV(data) {
    // Normalize line endings (convert CRLF and CR to LF)
    data = data.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

    // Replace semicolons with commas
    data = data.replace(/;/g, ',');

    // Remove extra whitespace around fields
    data = data.replace(/^\s+|\s+$/gm, '');

    // Handle inconsistent quoting
    // Remove quotes if they are not needed
    data = data.replace(/(^"|"$)/g, ''); // Remove quotes at the start/end of the line
    data = data.replace(/"([^"]*)"/g, '$1'); // Remove quotes around fields

    // Optionally, you can add more specific handling for quoted fields
    // For example, if a field contains a comma, it should be quoted
    data = data.replace(/([^,]+),([^,]+)/g, '"$1,$2"');

    return data;
}
function calculateScore(horseRow, trackCondition, troubleshooting = false, averageFormPrice) {
    if (troubleshooting) console.log(`Calculating score for horse: ${horseRow['horse name']}`);

    var score = 0;
    var notes = '';

    // Check horse weight  and score
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

    // Check if horse trainer  is someone we like or not
    [a, b] = checkTrainers(horseRow['horse trainer']);
    score += a;
    notes += b;

   // Check if horse has won at this track (ENHANCED WEIGHTED SYSTEM)
    [a, b] = checkTrackForm(horseRow['horse record track']);
    score += a;
    notes += b;

   // Check if horse has won at this track+distance combo (ENHANCED WEIGHTED SYSTEM)
    [a, b] = checkTrackDistanceForm(horseRow['horse record track distance']);
    score += a;
    notes += b;

    // Check if horse has won at this distance (ENHANCED WEIGHTED SYSTEM)
    [a, b] = checkDistanceForm(horseRow['horse record distance']);
    score += a;
    notes += b;

    // Check if the last race the horse ran was longer, same or shorter distance
    [a, b] = checkLastDistance(horseRow);
    score += a;
    notes += b;
    
   // Check horse form on actual track condition (ENHANCED WEIGHTED SYSTEM)
    const formTrackCondition = 'horse record ' + trackCondition;
    [a, b] = checkTrackConditionForm(horseRow[formTrackCondition], trackCondition);
    score += a;
    notes += b;
    

   // Check horse current and former classes
var [a, b] = compareClasses(
    horseRow['class restrictions'], 
    horseRow['form class'],
    horseRow['race prizemoney'],
    horseRow['prizemoney']
);
score += a;
notes += b;

// Check days since last run
[a, b] = checkDaysSinceLastRun(horseRow['meeting date'], horseRow['form meeting date']);
score += a;
notes += b;

// Check last run margin
[a, b] = checkMargin(horseRow['form position'], horseRow['form margin']);
score += a;
notes += b;

    // Check form price
[a, b] = checkFormPrice(averageFormPrice);
score += a;
notes += b;

// Check first up / second up specialist
[a, b] = checkFirstUpSecondUp(horseRow);
score += a;
notes += b;

return [score, notes]; // Return the score based on the first letter
}


function checkWeight(weight, claim) {
    let addScore = 0;
    let note = '';

    const act_weight = weight;
    if (act_weight > 65) {
        addScore += 0;
        note += 'ERR : weight less claim, above 65kg\n';
    } else if (act_weight < 49) {
        addScore += 0;
        note += 'ERR : Weight less claim, below 49kg\n';
    } else if (act_weight >= 49 && act_weight <=65) {
        addScore += (65.0 - act_weight) * 2;
        note += '+ ' +  addScore + ' : Weight less claim = ' + act_weight + 'kg\n';
    } else {
        addScore += 0;
        note += 'ERR : Weight invalid\n';
    }
    return [addScore, note]; // Return the score from this function
}

function checkLast10runs(last10) {
    last10 = String(last10).trim();
    
    if (last10.length > 99) {
        throw new Error("String must be 99 characters or less.");
    }

    let addScore = 0;
    let count = 0;
    let note2 = '';
    let note = '';
    let hasWin = last10.includes('1');

    for (let i = last10.length - 1; i >= 0; i--) {
        let char = last10[i];
        if (char != 'X' && char != 'x' && count < 5) {
            count++;
            if (char === '1') {
                addScore += 10;
                note2 += ' 1st';
            }
            if (char === '2') {
                addScore += 5;
                note2 += ' 2nd';
            }
            if (char === '3') {
                addScore += 2;
                note2 += ' 3rd';
            }
        }    
    }

    // FORMAT THE NOTE *BEFORE* APPLYING THE NO-WINS PENALTY
    if (addScore > 0) {
        note = '+' + addScore + '.0 : Ran places:' + note2 + '\n';
    }

    // NOW APPLY THE NO-WINS PENALTY
    if (!hasWin && last10.length > 0) {
        addScore -= 15;
        note += '-15.0 : No wins in last 10 starts - non-winner\n';
    }
    
    // Handle the case where we had places but ended negative
    if (addScore < 0 && note2) {
        // Note already formatted above, just return
    }
     
    return [addScore, note];
}

function normalizeJockeyName(jockeyName) {
    // Function used by checkJockeys to substitute known variations with standard names
    // I should probably merge this with normalizeClassName....
    for (const [key, value] of Object.entries(jockeyMapping)) {
        if (jockeyName.startsWith(key)) {
            return jockeyName.replace(key, value);
        }
    }
    return jockeyName; // Return the original if no mapping is found
}
function checkJockeys(JockeyName) {
    // Function for checking jockey name against lists
    var addScore = 0;
    var note = '';

    // Check for known spelling/name changes
    JockeyName = normalizeJockeyName(JockeyName)

    const tenPointJockeys = [
        'Blake Shinn',
        'James McDonald',
        'Jason Collett',
        'Mark Zahra',
        'Craig Williams',
        'Nash Rawiller',
        'Tim Clark'
    ]
    const fivePointJockeys = [
        'Aaron Bullock',
        'Damian Lane',
        'Ethan Brown',
        'Ben Melham',
        'Jamie Melham',
        'Josh Parr',
        'William Pike',
        'Zac Lloyd',
        'J Kah'
    ]
    const negativeJockeys = [
        'Kerrin McEvoy'
    ]

    if (tenPointJockeys.includes(JockeyName)) {
        addScore += 10;
        note += '+10.0 : Love the Jockey\n';
    }
    if (fivePointJockeys.includes(JockeyName)) {
        addScore += 5;
        note += '+ 5.0 : Like the Jockey\n';
    }
    if (negativeJockeys.includes(JockeyName)) {
        addScore -= 5;
        note += '- 5.0 : Kerrin Useless McEvoy\n';
    }
    return [addScore, note];
}

function normalizeTrainerName(trainerName) {
    // Function used by checkJockeys to substitute known variations with standard names
    // I should probably merge this with normalizeClassName....
    for (const [key, value] of Object.entries(trainerMapping)) {
        if (trainerName.startsWith(key)) {
            return trainerName.replace(key, value);
        }
    }
    return trainerName; // Return the original if no mapping is found
}

function checkTrainers(trainerName) {
    // Function for checking jockey name against lists
    var addScore = 0;
    var note = '';

    // Check for known spelling/name changes
    trainerName = normalizeTrainerName(trainerName)

   const fivePointTrainers = [
    'Ciaron Maher',
    'Chris Waller',
    'Ben, Will & J.D. Hayes',
    'Annabel & Rob Archibald',
    'Bjorn Baker',
    'Gai Waterhouse & Adrian Bott',
    'Grahame Begg',
    'Matthew Laurie',
    'Phillip Stokes'
]
   
    if (fivePointTrainers.includes(trainerName)) {
        addScore += 5;
        note += '+ 5.0 : Like the Trainer\n';
    }
   
    return [addScore, note];
}

function checkRacingForm(racingForm, runType) {
    // Check if horse has won, or got 5th or better twice, in the past 3 runs at this distance
    let addScore = 0; // Initialize score
    var note = '';
    // Ensure each item is a string before processing
    if (typeof racingForm !== 'string') {
        const err = typeof racingForm
        note += 'Racing form ${runType} not string. Received type: ' + err;
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
            undefeatedBonus = 15;
            note += '+ 15.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + '! (5+ runs)\n';
        } else if (runs >= 4) {
            undefeatedBonus = 14;
            note += '+ 14.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + '! (4 runs)\n';
        } else if (runs >= 3) {
            undefeatedBonus = 12;
            note += '+ 12.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + '! (3 runs)\n';
        } else {
            undefeatedBonus = 8;
            note += '+ 8.0 : UNDEFEATED in ' + runs + ' runs on ' + trackCondition + '! (2 runs)\n';
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
    
    let undefeatedBonus = 0;
    if (wins === runs && runs >= 2) {
        if (runs >= 5) {
            undefeatedBonus = 12;
            note += '+ 12.0 : UNDEFEATED in ' + runs + ' runs at this distance! (5+ runs)\n';
        } else if (runs >= 4) {
            undefeatedBonus = 11;
            note += '+ 11.0 : UNDEFEATED in ' + runs + ' runs at this distance! (4 runs)\n';
        } else if (runs >= 3) {
            undefeatedBonus = 9;
            note += '+ 9.0 : UNDEFEATED in ' + runs + ' runs at this distance! (3 runs)\n';
        } else {
            undefeatedBonus = 6;
            note += '+ 6.0 : UNDEFEATED in ' + runs + ' runs at this distance! (2 runs)\n';
        }
    }
    
    let subtotal = winScore + podiumScore + undefeatedBonus;
    
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
    
    let undefeatedBonus = 0;
    if (wins === runs && runs >= 2) {
        if (runs >= 5) {
            undefeatedBonus = 10;
            note += '+ 10.0 : UNDEFEATED in ' + runs + ' runs at this track! (5+ runs)\n';
        } else if (runs >= 4) {
            undefeatedBonus = 9;
            note += '+ 9.0 : UNDEFEATED in ' + runs + ' runs at this track! (4 runs)\n';
        } else if (runs >= 3) {
            undefeatedBonus = 7;
            note += '+ 7.0 : UNDEFEATED in ' + runs + ' runs at this track! (3 runs)\n';
        } else {
            undefeatedBonus = 5;
            note += '+ 5.0 : UNDEFEATED in ' + runs + ' runs at this track! (2 runs)\n';
        }
    }
    
    let subtotal = winScore + podiumScore + undefeatedBonus;
    
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
        confidenceNote = ' [Medium confidence: ' + runs + ' runs]';
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
    
    let undefeatedBonus = 0;
    if (wins === runs && runs >= 2) {
        if (runs >= 5) {
            undefeatedBonus = 12;
            note += '+ 12.0 : UNDEFEATED in ' + runs + ' runs at this track+distance! (5+ runs)\n';
        } else if (runs >= 4) {
            undefeatedBonus = 11;
            note += '+ 11.0 : UNDEFEATED in ' + runs + ' runs at this track+distance! (4 runs)\n';
        } else if (runs >= 3) {
            undefeatedBonus = 9;
            note += '+ 9.0 : UNDEFEATED in ' + runs + ' runs at this track+distance! (3 runs)\n';
        } else {
            undefeatedBonus = 6;
            note += '+ 6.0 : UNDEFEATED in ' + runs + ' runs at this track+distance! (2 runs)\n';
        }
    }
    
    let subtotal = winScore + podiumScore + undefeatedBonus;
    
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


function checkLastDistance(horseToCheck) {
    const dist = horseToCheck['distance']
    const prevDist = horseToCheck['form distance']
    var addScore = 0;
    var note = '';
    if (prevDist > 0) {
        if (dist > prevDist) {
            // if current distance longer than previous distance
            addScore += 1;
            note += '+ 1.0 : Longer dist than previous\n';
        }
        if (dist < prevDist) {
            // if current distance shorter than previous distance
            addScore -= 1;
            note += '- 1.0 : Shorter dist than previous\n';
        }
    }
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
        let datePart = dateStr.split(' ')[0];
        
        // Split DD/MM/YYYY or DD/MM/YY
        const parts = datePart.split('/');
        if (parts.length !== 3) return null;
        
        const day = parseInt(parts[0]);
        const month = parseInt(parts[1]) - 1; // JavaScript months are 0-indexed
        let year = parseInt(parts[2]);
        
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
        // 250+ days - very big penalty
        addScore = -25;
        note += `-25.0 : Too fresh - ${daysSinceLastRun} days since last run\n`;
    } else if (daysSinceLastRun >= 200) {
        // 200+ days - big penalty
        addScore = -20;
        note += `-20.0 : Too fresh - ${daysSinceLastRun} days since last run\n`;
    } else if (daysSinceLastRun >= 150) {
        // 150+ days - significant penalty
        addScore = -15;
        note += `-15.0 : Too fresh - ${daysSinceLastRun} days since last run\n`;
    } else if (daysSinceLastRun <= 7) {
        // 7 days or less - quick backup, BIG bonus (strongly outperforms market)
        addScore = 15;
        note += `+15.0 : Quick backup - only ${daysSinceLastRun} days since last run (market underrates!)\n`;
    }
    // 8-149 days is the sweet spot - no penalty or bonus
    
    return [addScore, note];
}
function checkMargin(formPosition, formMargin) {
    let addScore = 0;
    let note = '';
    
    // Validate inputs
    if (!formPosition || !formMargin) {
        return [0, ''];
    }
    
    // Parse position and margin
    const position = parseInt(formPosition);
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
            note += '';
        } else {
            addScore = -5;
            note += `- 5.0 : Beaten badly (${position}${position === 2 ? 'nd' : 'rd'}) by ${margin.toFixed(1)}L\n`;
        }
    }
    // MIDFIELD OR BACK (position 4+)
    else if (position >= 4) {
        if (margin <= 3.0) {
            addScore = 0;
            note += '';
        } else if (margin <= 6.0) {
            addScore = -3;
            note += `- 3.0 : Beaten clearly (${position}th) by ${margin.toFixed(1)}L\n`;
        } else if (margin <= 10.0) {
            addScore = -7;
            note += `- 7.0 : Well beaten (${position}th) by ${margin.toFixed(1)}L\n`;
        } else {
            addScore = -15;
            note += `-15.0 : Demolished (${position}th) by ${margin.toFixed(1)}L - not competitive\n`;
        }
    }
    
    return [addScore, note];
}

// ========================================
// CLASS SCORING SYSTEM (0-130 Scale)
// ========================================

// === PARSE CLASS STRING ===
function parseClassType(classString) {
    if (!classString) return null;
    
    const str = classString.trim();
    
    // Group races
    if (/Group\s*[123]/i.test(str)) {
        const level = parseInt(str.match(/[123]/)[0]);
        return { type: 'Group', level: level, raw: str };
    }
    
    // Listed
    if (/Listed/i.test(str)) {
        return { type: 'Listed', level: null, raw: str };
    }
    
    // Benchmark (handles "Benchmark 80" or "Bench. 80" or "BM80")
    const bmMatch = str.match(/(?:Benchmark|Bench\.?|BM)\s*(\d+)/i);
    if (bmMatch) {
        const level = parseInt(bmMatch[1]);
        return { type: 'Benchmark', level: level, raw: str };
    }
    
    // Class (handles "Class 3" or "Cls 2")
    const classMatch = str.match(/(?:Class|Cls)\s*(\d+)/i);
    if (classMatch) {
        const level = parseInt(classMatch[1]);
        return { type: 'Class', level: level, raw: str };
    }
    
    // Restricted (handles "Rest. 62")
    const restMatch = str.match(/Rest\.?\s*(\d+)/i);
    if (restMatch) {
        const level = parseInt(restMatch[1]);
        return { type: 'Restricted', level: level, raw: str };
    }
    
    // Rating (handles "RS105" or "Rating 0-105")
    const ratingMatch = str.match(/(?:RS|Rating)\s*(?:0-)?(\d+)/i);
    if (ratingMatch) {
        const level = parseInt(ratingMatch[1]);
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

// === EXTRACT PRIZE MONEY ===
function extractFirstPrize(prizeString) {
    if (!prizeString) return null;
    
    // Match pattern: "1st $82500" or "1st  $82,500"
    const match = prizeString.match(/1st\s+\$([0-9,]+)/i);
    if (match) {
        // Remove commas and convert to number
        return parseInt(match[1].replace(/,/g, ''));
    }
    return null;
}

// === CALCULATE SCORE (0-130) ===
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

// === COMPARE CLASSES ===
function compareClasses(newClass, formClass, newPrizemoneyString, formPrizemoneyString) {
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
        addScore = scoreDiff * -1.0; // Negative points for stepping up
        note += addScore.toFixed(1) + ': Stepping UP ' + scoreDiff.toFixed(1) + ' class points; "' + formClass + '" (' + lastScore.toFixed(1) + ') to "' + newClass + '" (' + todayScore.toFixed(1) + ')\n';
    } else if (scoreDiff < 0) {
        // Stepping DOWN in class (easier race)
        addScore = Math.abs(scoreDiff) * 1.0; // Positive points for stepping down
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

function checkFormPrice(formPrice) {
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
    if (numericPrice < 1.01 || numericPrice > 500.00) {
        return [0, `Error: Form price $${numericPrice} outside valid range (1.01-500.00)\n`];
    }
    
    // Round to 2 decimal places for lookup
    const roundedPrice = Math.round(numericPrice * 100) / 100;
    
    // Try exact lookup first
    if (formPriceScores[roundedPrice] !== undefined) {
        addScore = formPriceScores[roundedPrice];
        if (addScore > 0) {
            note += `+${addScore}.0 : Form price $${roundedPrice.toFixed(2)} (well-backed)\n`;
        } else if (addScore === 0) {
            note += `+0.0 : Form price $${roundedPrice.toFixed(2)} (neutral)\n`;
        } else {
            note += `${addScore}.0 : Form price $${roundedPrice.toFixed(2)} (outsider penalty)\n`;
        }
    } else {
        // Handle prices not in the lookup table with interpolation
        const sortedPrices = Object.keys(formPriceScores).map(Number).sort((a, b) => a - b);
        const closestLower = sortedPrices.filter(p => p < roundedPrice).pop();
        const closestHigher = sortedPrices.filter(p => p > roundedPrice)[0];
        
        if (closestLower && closestHigher) {
            // Linear interpolation
            const lowerScore = formPriceScores[closestLower];
            const higherScore = formPriceScores[closestHigher];
            const ratio = (roundedPrice - closestLower) / (closestHigher - closestLower);
            addScore = Math.round(lowerScore + (higherScore - lowerScore) * ratio);
            
            if (addScore > 0) {
                note += `+${addScore}.0 : Form price $${roundedPrice.toFixed(2)} (interpolated)\n`;
            } else if (addScore === 0) {
                note += `+0.0 : Form price $${roundedPrice.toFixed(2)} (interpolated)\n`;
            } else {
                note += `${addScore}.0 : Form price $${roundedPrice.toFixed(2)} (interpolated)\n`;
            }
        } else {
            return [0, `Error: Form price $${roundedPrice.toFixed(2)} could not be scored\n`];
        }
    }
    
    return [addScore, note];
}
// Add this function after the checkFormPrice function
function checkFirstUpSecondUp(horseRow) {
    let addScore = 0;
    let note = '';
    
    const last10 = String(horseRow['horse last10'] || '');
    const firstUpRecord = horseRow['horse record first up'];
    const secondUpRecord = horseRow['horse record second up'];
    
    // Determine if horse is first up or second up today
    let isFirstUp = false;
    let isSecondUp = false;
    
    // Check the most recent run (rightmost character) for first up detection
    if (last10.toLowerCase().endsWith('x')) {
        isFirstUp = true;
    }
    // Check for second up: exactly one run after most recent 'x'
    // Pattern: ...x[digit] at the end (exactly 2 characters from spell)
    else if (last10.length >= 2) {
        const lastChar = last10.charAt(last10.length - 1);
        const secondLastChar = last10.charAt(last10.length - 2);
        
        // Second up means: previous run was 'x' and current run is a single digit
        if (secondLastChar.toLowerCase() === 'x' && /\d/.test(lastChar)) {
            isSecondUp = true;
        }
    }
    
    // Function to check if a record is undefeated
    const isUndefeated = (record) => {
        if (typeof record !== 'string') return false;
        
        const numbers = record.split(/[:\-]/).map(Number);
        if (numbers.length !== 4) return false;
        
        const [runs, wins, seconds, thirds] = numbers;
        
        // Must have at least 1 run, all runs must be wins, no seconds or thirds
        return runs > 0 && wins === runs && seconds === 0 && thirds === 0;
    };
    
    // Check first up specialist
    if (isFirstUp && isUndefeated(firstUpRecord)) {
        addScore += 15;
        note += `+15.0 : First up specialist (${firstUpRecord})\n`;
    }
    
    // Check second up specialist
    if (isSecondUp && isUndefeated(secondUpRecord)) {
        addScore += 15;
        note += `+15.0 : Second up specialist (${secondUpRecord})\n`;
    }
    
    return [addScore, note];
}

function calculateAverageFormPrices(data) {
    const formPriceGroups = {};
    
    // Group form prices by composite key (horse name + race number)
    data.forEach(entry => {
        const compositeKey = `${entry['horse name']}-${entry['race number']}`;
        const formPrice = parseFloat(entry['form price']);
        
        // Initialize array for this horse-race combination if it doesn't exist
        if (!formPriceGroups[compositeKey]) {
            formPriceGroups[compositeKey] = [];
        }
        
        // Only add valid form prices within the expected range
        if (!isNaN(formPrice) && formPrice >= 1.01 && formPrice <= 500.00) {
            formPriceGroups[compositeKey].push(formPrice);
        }
    });
    
    // Calculate averages for each horse-race combination
    const averages = {};
    Object.keys(formPriceGroups).forEach(key => {
        const prices = formPriceGroups[key];
        if (prices.length > 0) {
            // Calculate average and round to 2 decimal places
            const sum = prices.reduce((total, price) => total + price, 0);
            const average = sum / prices.length;
            averages[key] = Math.round(average * 100) / 100;
        } else {
            // No valid prices found for this horse
            averages[key] = null;
        }
    });
    
    return averages;
}

function parseLastInteger(sectional) {
    const match = sectional.match(/(\d+)m$/);
    return match ? parseInt(match[1], 10) : null; // Return the integer or null if not found
}
function getLowestSectionalsByRace(data) {
  // Filter out invalid rows first
  const validData = data.filter(entry => {
    // Check if all required fields exist
    if (!entry['race number'] || !entry['horse name'] || !entry['sectional']) {
      return false;
    }
    
    // Check if horse name is valid (not just the header text)
    const horseName = entry['horse name'].toString().trim().toLowerCase();
    if (horseName === 'horse name' || horseName === '' || horseName === 'nan' || horseName === 'null' || horseName === 'undefined') {
      return false;
    }
    
    // Check if race number is valid
    const raceNum = parseInt(entry['race number']);
    if (isNaN(raceNum) || raceNum <= 0) {
      return false;
    }
    
    // Check if sectional format is recognizable (even if 0 seconds)
    const sectionalMatch = entry['sectional'].toString().match(/^(\d+\.?\d*)sec (\d+)m$/);
    if (!sectionalMatch) {
      return false;
    }
    
    return true;
  });

  // Group data by race number
  const raceGroups = {};
  validData.forEach(entry => {
    const raceNum = entry['race number'];
    if (!raceGroups[raceNum]) {
      raceGroups[raceNum] = [];
    }
    raceGroups[raceNum].push(entry);
  });

  const results = [];

  // Process each race
  Object.keys(raceGroups).forEach(raceNum => {
    const raceData = raceGroups[raceNum];
    
    // Parse sectional data and check for consistency
    const parsedData = [];
    const distances = new Set();

    raceData.forEach(entry => {
      const sectionalMatch = entry.sectional.match(/^(\d+\.?\d*)sec (\d+)m$/);
      if (sectionalMatch) {
        const time = parseFloat(sectionalMatch[1]);
        const distance = parseInt(sectionalMatch[2]);
        
        // Only consider non-zero sectionals for distance checking
        if (time > 0) {
          distances.add(distance);
        }
        
        parsedData.push({
          ...entry,
          time: time,
          distance: distance
        });
      }
    });

    // If multiple distances, find the most common one
    let targetDistance = null;
    if (distances.size > 1) {
      // Count horses per distance
      const distanceHorseCounts = {};
      distances.forEach(dist => {
        const horsesAtDistance = new Set();
        parsedData.forEach(entry => {
          if (entry.time > 0 && entry.distance === dist) {
            horsesAtDistance.add(entry['horse name']);
          }
        });
        distanceHorseCounts[dist] = horsesAtDistance.size;
      });
      
      // Find distance with most horses
      targetDistance = Object.keys(distanceHorseCounts).reduce((a, b) => 
        distanceHorseCounts[a] > distanceHorseCounts[b] ? a : b
      );
      targetDistance = parseInt(targetDistance);
    } else if (distances.size === 1) {
      targetDistance = [...distances][0];
    }

    // Collect sectional times at target distance with dates
    const horseData = {};
    const allHorses = [...new Set(raceData.map(entry => entry['horse name']))];
    
    // Initialize all horses
    allHorses.forEach(horseName => {
      horseData[horseName] = [];
    });
    
    // Collect sectional times with dates at target distance
    parsedData.forEach(entry => {
      const horseName = entry['horse name'];
      // Only include non-zero sectionals at the target distance
      if (entry.time > 0 && (targetDistance === null || entry.distance === targetDistance)) {
        horseData[horseName].push({
          time: entry.time,
          date: entry['form meeting date']
        });
      }
    });

    // Sort by date (most recent first) for each horse
    Object.keys(horseData).forEach(horseName => {
      horseData[horseName].sort((a, b) => {
        return new Date(b.date) - new Date(a.date);
      });
    });

    // SYSTEM 1: Average of Last 3 Runs
    const averageLast3Data = [];
    const horsesWithoutAverage = [];
    
    Object.keys(horseData).forEach(horseName => {
      const times = horseData[horseName];
      if (times.length > 0) {
        // Take up to 3 most recent runs
        const last3Times = times.slice(0, 3).map(entry => entry.time);
        const average = last3Times.reduce((sum, time) => sum + time, 0) / last3Times.length;
        averageLast3Data.push({
          horseName: horseName,
          averageTime: average,
          runsUsed: last3Times.length
        });
      } else {
        horsesWithoutAverage.push(horseName);
      }
    });

    // Sort by average time (fastest first)
    averageLast3Data.sort((a, b) => a.averageTime - b.averageTime);

    // SYSTEM 2: Best Single Sectional from Last Start Only
    const lastStartData = [];
    const horsesWithoutLastStart = [];
    
    Object.keys(horseData).forEach(horseName => {
      const times = horseData[horseName];
      if (times.length > 0) {
        // Take only the most recent run (index 0 after sorting)
        const lastStartTime = times[0].time;
        lastStartData.push({
          horseName: horseName,
          lastStartTime: lastStartTime
        });
      } else {
        horsesWithoutLastStart.push(horseName);
      }
    });

    // Sort by last start time (fastest first)
    lastStartData.sort((a, b) => a.lastStartTime - b.lastStartTime);

    // Create a map to store scores for each horse
    const horseScores = {};
    allHorses.forEach(horseName => {
      horseScores[horseName] = {
        score: 0,
        note: '',
        hasAverage1st: false,
        hasLastStart1st: false
      };
    });

    // Assign points for System 1: Average of Last 3
    averageLast3Data.forEach((horse, index) => {
      let score = 0;
      let note = '';
      if (index === 0) {
        score = 20;
        note = `+20.0: fastest avg sectional (last ${horse.runsUsed} runs)\n`;
        horseScores[horse.horseName].hasAverage1st = true;
      } else if (index === 1) {
        score = 10;
        note = `+10.0: 2nd fastest avg sectional (last ${horse.runsUsed} runs)\n`;
      } else if (index === 2) {
        score = 5;
        note = `+ 5.0: 3rd fastest avg sectional (last ${horse.runsUsed} runs)\n`;
      }
      horseScores[horse.horseName].score += score;
      horseScores[horse.horseName].note += note;
    });

    // Assign points for System 2: Last Start Only
    lastStartData.forEach((horse, index) => {
      let score = 0;
      let note = '';
      if (index === 0) {
        score = 20;
        note = `+20.0: fastest last start sectional\n`;
        horseScores[horse.horseName].hasLastStart1st = true;
      } else if (index === 1) {
        score = 10;
        note = `+10.0: 2nd fastest last start sectional\n`;
      } else if (index === 2) {
        score = 5;
        note = `+ 5.0: 3rd fastest last start sectional\n`;
      }
      horseScores[horse.horseName].score += score;
      horseScores[horse.horseName].note += note;
    });

    // Add horses without sectionals
    horsesWithoutAverage.forEach(horseName => {
      if (!horseScores[horseName].note) {
        horseScores[horseName].note = '??: No valid sectional\n';
      }
    });

    // Convert to results array
    Object.keys(horseScores).forEach(horseName => {
      results.push({
        'race': raceNum,
        'name': horseName,
        'sectionalScore': horseScores[horseName].score,
        'sectionalNote': horseScores[horseName].note,
        'hasAverage1st': horseScores[horseName].hasAverage1st,
        'hasLastStart1st': horseScores[horseName].hasLastStart1st
      });
    });
  });
  
  return results;
}


function calculateTrueOdds(results, priorStrength = 0.05, troubleshooting, maxRatio = 300.0) {
    // Group horses by race
    const raceGroups = results.reduce((acc, obj) => {
        const raceNumber = obj.horse['race number'];
        if (!acc[raceNumber]) acc[raceNumber] = [];
        acc[raceNumber].push(obj);
        return acc;
    }, {});
    
    // Process each race separately
    Object.values(raceGroups).forEach(raceHorses => {
        const numHorses = raceHorses.length;
        const scores = raceHorses.map(h => h.score);
        
        // Calculate proportional shift to control max ratio
        const minScore = Math.min(...scores);
        const maxScore = Math.max(...scores);
        const range = maxScore - minScore;
        
        // Calculate shift to ensure max ratio doesn't exceed maxRatio
        // If range is small, use a larger shift to prevent extreme ratios
       const minShiftForRatio = range > 0 ? range / (maxRatio - 1) : 1.0;
const basicShift = minScore < 0 ? Math.abs(minScore) + 0.01 : 0;
const shift = Math.max(basicShift, minShiftForRatio * 0.5);
        
        // Apply Dirichlet method
        const adjustedScores = scores.map(s => s + shift);
        const posteriorCounts = adjustedScores.map(score => score + priorStrength);
        const totalCounts = posteriorCounts.reduce((sum, count) => sum + count, 0);
        
        // Calculate base probability (same for all horses in this race)
        const baseProbability = priorStrength / totalCounts;
        
        // Calculate final probabilities and odds for each horse
        raceHorses.forEach((horse, index) => {
            // Win probability
            const winProbability = posteriorCounts[index] / totalCounts;
            
            // Base probability component (same for all horses in race)
            const baseProbabilityPercent = (baseProbability * 100).toFixed(1) + '%';
            
            // Performance-based probability component
            const performanceProbability = (adjustedScores[index] / totalCounts);
            
            // True dollar odds (no bookmaker margin)
            const trueOdds = 1 / (winProbability * 1.10);
            
            // Add all calculated values to horse object
            horse.winProbability = (winProbability * 110).toFixed(1) + '%';
            horse.baseProbability = baseProbabilityPercent; // Same for all horses in this race
            horse.trueOdds = `$${trueOdds.toFixed(2)}`;
            
            // Additional useful info
            horse.rawWinProbability = winProbability * 1.10; // For calculations
            horse.performanceComponent = (performanceProbability * 100).toFixed(1) + '%';
            horse.adjustedScore = adjustedScores[index]; // For debugging
        });
        
        // Verify probabilities sum to 110%
        const totalProb = raceHorses.reduce((sum, horse) => sum + horse.rawWinProbability, 0);
        if (totalProb < 1.09 || totalProb > 1.11) {
            throw new Error(`Race ${raceHorses[0].horse['race number']} probabilities adding to ${(totalProb*100).toFixed(2)}%`);
        }

        // Log race summary if troubleshooting on
        if (troubleshooting) {
            console.log(`\n📊 RACE ${raceHorses[0].horse['race number']} SUMMARY:`);
            console.log(`Horses: ${numHorses}`);
            console.log(`Score range: ${minScore.toFixed(2)} to ${maxScore.toFixed(2)} (range: ${range.toFixed(2)})`);
            console.log(`Max ratio limit: ${maxRatio}:1`);
            console.log(`Score shift applied: ${shift.toFixed(2)}`);
            console.log(`Base probability per horse: ${(baseProbability * 100).toFixed(1)}%`);
            console.log(`Prior strength: ${priorStrength}`);
            
            // Show actual min/max adjusted score ratio
            const minAdjusted = Math.min(...adjustedScores);
            const maxAdjusted = Math.max(...adjustedScores);
            const actualRatio = maxAdjusted / minAdjusted;
            console.log(`Actual adjusted score ratio: ${actualRatio.toFixed(2)}:1`);
            console.log(`Total probability check: ${(totalProb * 100).toFixed(1)}%`);
            
            // Show odds range
            const allOdds = raceHorses.map(h => parseFloat(h.trueOdds.replace('$', '')));
            const minOdds = Math.min(...allOdds);
            const maxOdds = Math.max(...allOdds);
            console.log(`Odds range: $${minOdds.toFixed(2)} to $${maxOdds.toFixed(2)}`);
        }
    });
    
    return results;
}
