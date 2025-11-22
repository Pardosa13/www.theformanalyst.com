<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <!-- PDF Export Libraries -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf-autotable/3.5.31/jspdf.plugin.autotable.min.js"></script>
    <style>
    /* General Styles */
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Arial', sans-serif;
    background-color: #f5f7fa;
    color: #2d3748;
    line-height: 1.4;
    margin: 0;
    padding: 20px;
    min-height: 100vh;
}

/* Header */
h1 {
    font-size: 2.5em;
    margin: 0 0 25px 0;
    font-weight: 700;
    color: #2d3748;
    text-align: center;
}

/* Compact Layout Container */
.input-row {
    display: flex;
    align-items: center;
    gap: 20px;
    margin-bottom: 15px;
    flex-wrap: wrap;
}

.input-row-2 {
    display: flex;
    align-items: center;
    gap: 20px;
    margin-bottom: 15px;
    flex-wrap: wrap;
}

/* Labels */
label {
    font-size: 14px;
    font-weight: 600;
    color: #4a5568;
    white-space: nowrap;
}

/* File Input Styling */
input[type="file"] {
    padding: 8px 12px;
    border: 2px solid #e9ecef;
    border-radius: 8px;
    background-color: #f5f7fa;
    font-size: 14px;
    color: #4a5568;
    cursor: pointer;
    transition: all 0.3s ease;
    width: auto;
    min-width: fit-content;
}

input[type="file"]:hover {
    border-color: #667eea;
    box-shadow: 0 2px 8px rgba(102, 126, 234, 0.15);
    transform: translateY(-1px);
}

/* Select Dropdown Styling */
select {
    padding: 8px 12px;
    border: 2px solid #e9ecef;
    border-radius: 8px;
    background-color: #f5f7fa;
    font-size: 14px;
    color: #4a5568;
    cursor: pointer;
    transition: all 0.3s ease;
    width: auto;
    min-width: fit-content;
}

select:hover {
    border-color: #667eea;
    box-shadow: 0 2px 8px rgba(102, 126, 234, 0.15);
    transform: translateY(-1px);
}

select:focus {
    outline: none;
    border-color: #667eea;
    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* Advanced Toggle Container */
.toggle-container {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 12px;
    background-color: #f5f7fa;
    border-radius: 8px;
    border: 2px solid #e9ecef;
    transition: all 0.3s ease;
    width: fit-content;
}

.toggle-container:hover {
    border-color: #667eea;
    box-shadow: 0 2px 8px rgba(102, 126, 234, 0.15);
    transform: translateY(-1px);
}

/* Toggle Switch - Smaller */
.toggle-switch {
    position: relative;
    display: inline-block;
    width: 48px;
    height: 26px;
}

.toggle-switch input {
    opacity: 0;
    width: 0;
    height: 0;
}

.slider {
    position: absolute;
    cursor: pointer;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: #cbd5e0;
    transition: 0.3s ease;
    border-radius: 26px;
    box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.1);
}

.slider:before {
    position: absolute;
    content: "";
    height: 20px;
    width: 20px;
    left: 3px;
    bottom: 3px;
    background-color: white;
    transition: 0.3s ease;
    border-radius: 50%;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.2);
}

input:checked + .slider {
    background: linear-gradient(45deg, #667eea, #764ba2);
    box-shadow: 0 0 8px rgba(102, 126, 234, 0.4);
}

input:checked + .slider:before {
    transform: translateX(22px);
}

.toggle-label {
    font-size: 14px;
    font-weight: 600;
    color: #4a5568;
    user-select: none;
    cursor: pointer;
    transition: color 0.3s ease;
    margin: 0;
}

.toggle-container:hover .toggle-label {
    color: #667eea;
}

/* Regular Checkbox Styling */
input[type="checkbox"]:not(.toggle-switch input) {
    appearance: none;
    width: 16px;
    height: 16px;
    border: 2px solid #e9ecef;
    border-radius: 4px;
    background-color: #f5f7fa;
    cursor: pointer;
    transition: all 0.3s ease;
    position: relative;
    margin-right: 8px;
    vertical-align: middle;
}

input[type="checkbox"]:not(.toggle-switch input):hover {
    border-color: #667eea;
    box-shadow: 0 1px 4px rgba(102, 126, 234, 0.15);
    transform: translateY(-1px);
}

input[type="checkbox"]:not(.toggle-switch input):checked {
    background: linear-gradient(45deg, #667eea, #764ba2);
    border-color: #667eea;
}

input[type="checkbox"]:not(.toggle-switch input):checked::after {
    content: '✓';
    position: absolute;
    color: white;
    font-weight: bold;
    font-size: 12px;
    top: -1px;
    left: 1px;
}

/* Checkbox Label */
label[for="troubleshootingToggle"] {
    font-size: 14px;
    vertical-align: middle;
}

/* Button Styling */
button {
    background: #2d2d2d;
    color: white;
    padding: 8px 16px;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.3s ease;
    box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);
    margin-right: 15px;
    width: fit-content;
}

button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
}

button:active {
    transform: translateY(0);
}

/* PDF Download Button */
.pdf-download-btn {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 10px 20px;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.3s ease;
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
    margin-bottom: 15px;
    display: inline-flex;
    align-items: center;
    gap: 8px;
}

.pdf-download-btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(102, 126, 234, 0.5);
}

.pdf-download-btn:active {
    transform: translateY(0);
}

.pdf-download-btn::before {
    content: '📄';
    font-size: 16px;
}

/* Results Section */
#results {
    margin-top: 25px;
    padding: 20px;
    background-color: #f5f7fa;
    border-radius: 12px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

/* Analysis Results Heading */
#results h2 {
    color: #2d3748;
    font-size: 1.8em;
    margin-bottom: 15px;
}

/* Table Styles */
table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 15px;
    background-color: #f5f7fa;
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.08);
}

th, td {
    padding: 12px 15px;
    text-align: left;
    border-bottom: 1px solid #e2e8f0;
    font-size: 14px;
}

th {
    background-color: #edf2f7;
    font-weight: 600;
    color: #2d3748;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 2px solid #e2e8f0;
}

tr {
    transition: all 0.3s ease;
    background-color: #f5f7fa;
}

tr:hover {
    background: linear-gradient(135deg, #f0f4ff 0%, #e6f3ff 100%);
    transform: translateX(4px);
    box-shadow: 4px 0 12px rgba(102, 126, 234, 0.1);
    border: 2px solid #667eea;
    border-radius: 8px;
}

tr:nth-child(even) {
    background-color: #edf2f7;
}

tr:nth-child(even):hover {
    background: linear-gradient(135deg, #f0f4ff 0%, #e6f3ff 100%);
}

/* Responsive Design */
@media (max-width: 768px) {
    body {
        padding: 15px;
    }
    
    h1 {
        font-size: 2em;
    }
    
    .input-row, .input-row-2 {
        flex-direction: column;
        align-items: flex-start;
        gap: 10px;
    }
    
    input[type="file"], select {
        width: 100%;
    }
    
    .toggle-container {
        width: 100%;
        justify-content: center;
    }
    
    button {
        width: 100%;
        margin: 10px 0;
    }
}

/* Meeting Tabs Container */
.meeting-tabs-container {
    position: sticky;
    top: 0;
    z-index: 1000;
    background: #f5f7fa;
    padding: 10px 0;
    border-bottom: 2px solid #e9ecef;
    margin-bottom: 10px;
}

.meeting-tabs {
    display: flex;
    gap: 5px;
    flex-wrap: wrap;
}

.meeting-tab {
    background: #2d2d2d;
    color: white;
    padding: 10px 15px;
    border-radius: 8px 8px 0 0;
    cursor: pointer;
    font-size: 14px;
    font-weight: 600;
    border: 2px solid transparent;
    transition: all 0.3s ease;
    display: flex;
    align-items: center;
    gap: 10px;
}

.meeting-tab:hover {
    background: #3d3d3d;
    transform: translateY(-2px);
}

.meeting-tab.active {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    border-bottom: 2px solid #f5f7fa;
}

.tab-close {
    background: rgba(255, 255, 255, 0.2);
    border: none;
    color: white;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    cursor: pointer;
    font-size: 14px;
    line-height: 1;
    transition: all 0.2s ease;
}

.tab-close:hover {
    background: rgba(255, 0, 0, 0.6);
    transform: scale(1.1);
}

/* Race Navigation Bar */
.race-nav-container {
    position: sticky;
    top: 60px;
    z-index: 999;
    background: #ffffff;
    padding: 15px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
    margin-bottom: 20px;
}

.race-nav-buttons {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 15px;
}

.race-nav-button {
    background: #f5f7fa;
    color: #4a5568;
    padding: 8px 15px;
    border: 2px solid #e9ecef;
    border-radius: 6px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 600;
    transition: all 0.3s ease;
}

.race-nav-button:hover {
    background: #667eea;
    color: white;
    border-color: #667eea;
    transform: translateY(-2px);
}

.race-nav-button.current {
    background: linear-gradient(45deg, #667eea, #764ba2);
    color: white;
    border-color: #667eea;
}

.action-buttons {
    display: flex;
    gap: 10px;
    padding-top: 10px;
    border-top: 1px solid #e9ecef;
}

.action-button {
    background: #2d2d2d;
    color: white;
    padding: 8px 16px;
    border: none;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.3s ease;
}

.action-button:hover {
    background: #667eea;
    transform: translateY(-1px);
}

/* Race Container */
.race-container {
    margin-bottom: 30px;
}

/* Race Dashboard */
.race-dashboard {
    background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
color: white;
    padding: 20px;
    border-radius: 12px;
    margin-bottom: 10px;
    cursor: pointer;
    transition: all 0.3s ease;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.race-dashboard:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.2);
}

.race-dashboard-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 15px;
}

.race-title {
    font-size: 1.5em;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 10px;
}

.collapse-arrow {
    font-size: 1.2em;
    transition: transform 0.3s ease;
}

.collapse-arrow.collapsed {
    transform: rotate(-90deg);
}

/* Top 3 Horses Display */
.top-horses {
    margin-bottom: 15px;
}

.horse-row {
    display: flex;
    align-items: center;
    gap: 15px;
    padding: 10px;
    background: rgba(255, 255, 255, 0.05);
    border-radius: 8px;
    margin-bottom: 8px;
}

.position-badge {
    width: 35px;
    height: 35px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: bold;
    font-size: 16px;
    color: white;
    flex-shrink: 0;
}

.position-badge.first {
    background: linear-gradient(135deg, #FFD700, #FFA500);
    box-shadow: 0 2px 8px rgba(255, 215, 0, 0.4);
}

.position-badge.second {
    background: linear-gradient(135deg, #C0C0C0, #A8A8A8);
    box-shadow: 0 2px 8px rgba(192, 192, 192, 0.4);
}

.position-badge.third {
    background: linear-gradient(135deg, #CD7F32, #B8733E);
    box-shadow: 0 2px 8px rgba(205, 127, 50, 0.4);
}

.horse-name {
    font-weight: 700;
    font-size: 1.1em;
    min-width: 150px;
}

.horse-stats {
    display: flex;
    gap: 20px;
    font-size: 0.95em;
}

.stat-item {
    display: flex;
    gap: 5px;
}

.stat-label {
    opacity: 0.8;
}

.stat-value {
    font-weight: 600;
}

/* Race Stats Bar */
.race-stats-bar {
    display: flex;
    gap: 30px;
    padding: 12px;
    background: rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    border-top: 1px solid rgba(255, 255, 255, 0.2);
    font-size: 0.95em;
}

.race-stat {
    display: flex;
    gap: 8px;
}

.race-stat-label {
    opacity: 0.8;
}

.race-stat-value {
    font-weight: 700;
}

.worst-score {
    color: #ff6b6b;
}

/* Race Table Container */
.race-table-container {
    overflow: hidden;
    transition: max-height 0.3s ease;
}

.race-table-container.collapsed {
    max-height: 0;
}

.race-table-container.expanded {
    max-height: 5000px;
}

/* Sticky Race Header (when scrolling) */
.sticky-race-header {
    position: fixed;
    top: 60px;
    left: 0;
    right: 0;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 12px 20px;
    font-weight: 600;
    font-size: 1.1em;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
    z-index: 998;
    display: none;
}

.sticky-race-header.visible {
    display: block;
}

/* Meeting Content Container */
.meeting-content {
    display: none;
}

.meeting-content.active {
    display: block;
}

/* ====== FILTER BAR STYLES ====== */
.filter-bar-container {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    padding: 20px;
    border-radius: 12px;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.filter-bar-header {
    display: flex;
    align-items: center;
    gap: 15px;
    margin-bottom: 15px;
    flex-wrap: wrap;
}

.filter-bar-title {
    font-size: 16px;
    font-weight: 700;
    color: white;
    display: flex;
    align-items: center;
    gap: 8px;
}

.filter-dropdown {
    padding: 8px 12px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    border-radius: 8px;
    background-color: rgba(255, 255, 255, 0.9);
    font-size: 14px;
    color: #2d3748;
    cursor: pointer;
    transition: all 0.3s ease;
    min-width: 200px;
}

.filter-dropdown:hover {
    border-color: white;
    background-color: white;
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.filter-dropdown:focus {
    outline: none;
    border-color: white;
    box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.3);
}

.clear-filters-btn {
    background: rgba(255, 255, 255, 0.2);
    color: white;
    padding: 8px 16px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.3s ease;
}

.clear-filters-btn:hover {
    background: rgba(255, 255, 255, 0.3);
    border-color: white;
    transform: translateY(-1px);
}

.active-filters-container {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
}

.active-filters-label {
    color: white;
    font-size: 14px;
    font-weight: 600;
    opacity: 0.9;
}

.filter-tag {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: rgba(255, 255, 255, 0.95);
    color: #2d3748;
    padding: 6px 12px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
    transition: all 0.2s ease;
}

.filter-tag:hover {
    background: white;
    transform: translateY(-1px);
    box-shadow: 0 3px 8px rgba(0, 0, 0, 0.15);
}

.filter-tag-remove {
    cursor: pointer;
    color: #e53e3e;
    font-weight: bold;
    font-size: 16px;
    line-height: 1;
    transition: color 0.2s ease;
}

.filter-tag-remove:hover {
    color: #c53030;
}

.filter-match-counter {
    color: white;
    font-size: 14px;
    font-weight: 600;
    background: rgba(255, 255, 255, 0.2);
    padding: 6px 12px;
    border-radius: 20px;
    backdrop-filter: blur(10px);
}

/* ====== GREEN HIGHLIGHTING FOR FILTERED HORSES ====== */
.horse-row-match {
    background: linear-gradient(90deg, rgba(72, 187, 120, 0.3), rgba(72, 187, 120, 0.15)) !important;
    border-left: 4px solid #48bb78 !important;
    box-shadow: 0 2px 8px rgba(72, 187, 120, 0.3) !important;
}

table tr.horse-row-match {
    background: linear-gradient(90deg, rgba(72, 187, 120, 0.25), rgba(72, 187, 120, 0.1)) !important;
    border-left: 4px solid #48bb78;
}

table tr.horse-row-match td {
    font-weight: 600;
}

/* Optional: Add a checkmark icon to matching rows */
table tr.horse-row-match td:first-child::before {
    content: '✓ ';
    color: #48bb78;
    font-weight: bold;
    margin-right: 5px;
}
    </style>
    <title>Partington Probability Engine PTY LTD</title>
</head>
<body>
    <h1>Partington Probability Engine PTY LTD</h1>

    <!-- First row: File upload and track condition -->
    <div class="input-row">
        <label for="fileInput">Upload CSV File:</label>
        <input type="file" id="fileInput" accept=".csv" multiple required>
        
        <div id="trackConditions">
            <!-- Dynamic track condition selectors will be added here -->
        </div>
    </div>

    <!-- Second row: Toggle and controls -->
    <div class="input-row-2">
        <div class="toggle-container">
            <label class="toggle-switch">
                <input type="checkbox" id="advancedToggle">
                <span class="slider"></span>
            </label>
            <label for="advancedToggle" class="toggle-label">Advanced Mode</label>
        </div>

        <button id="analyzeButton">Analyze</button>
        
        <label for="troubleshootingToggle">
            <input type="checkbox" id="troubleshootingToggle"> Enable Troubleshooting Mode
        </label>
    </div>

    <div id="results"></div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/PapaParse/5.3.0/papaparse.min.js"></script>

<script>
// Global state management
let meetings = [];
let activeMeetingIndex = 0;
let maxTabs = 10;

// Meeting Tab Management
function createMeeting(fileName) {
    if (meetings.length >= maxTabs) {
        alert(`Maximum of ${maxTabs} meetings can be open at once. Please close a meeting first.`);
        return null;
    }
    
    const meeting = {
        id: Date.now(),
        fileName: fileName,
        races: [],
        analysisResults: []
    };
    
    meetings.push(meeting);
    activeMeetingIndex = meetings.length - 1;
    renderMeetingTabs();
    return meeting;
}

function closeMeeting(index) {
    if (confirm(`Close ${meetings[index].fileName}?`)) {
        meetings.splice(index, 1);
        if (activeMeetingIndex >= meetings.length) {
            activeMeetingIndex = Math.max(0, meetings.length - 1);
        }
        renderMeetingTabs();
        renderResults();
    }
}

function switchMeeting(index) {
    activeMeetingIndex = index;
    renderMeetingTabs();
    renderResults();
}

function renderMeetingTabs() {
    let tabsContainer = document.getElementById('meeting-tabs-container');
    
    if (!tabsContainer) {
        tabsContainer = document.createElement('div');
        tabsContainer.id = 'meeting-tabs-container';
        tabsContainer.className = 'meeting-tabs-container';
        document.body.insertBefore(tabsContainer, document.getElementById('results'));
    }
    
    if (meetings.length === 0) {
        tabsContainer.style.display = 'none';
        return;
    }
    
    tabsContainer.style.display = 'block';
    tabsContainer.innerHTML = '<div class="meeting-tabs"></div>';
    const tabsDiv = tabsContainer.querySelector('.meeting-tabs');
    
    meetings.forEach((meeting, index) => {
        const tab = document.createElement('div');
        tab.className = 'meeting-tab' + (index === activeMeetingIndex ? ' active' : '');
        
        const tabName = document.createElement('span');
        tabName.textContent = meeting.fileName;
        
        const closeBtn = document.createElement('button');
        closeBtn.className = 'tab-close';
        closeBtn.innerHTML = '✕';
        closeBtn.onclick = (e) => {
            e.stopPropagation();
            closeMeeting(index);
        };
        
        tab.appendChild(tabName);
        tab.appendChild(closeBtn);
        tab.onclick = () => switchMeeting(index);
        
        tabsDiv.appendChild(tab);
    });
}


// ====== RACE NAVIGATION & ACTION FUNCTIONS ======
function createRaceNavigation(races) {
    const navContainer = document.createElement('div');
    navContainer.className = 'race-nav-container';
    navContainer.id = 'race-nav-container';

    const navButtons = document.createElement('div');
    navButtons.className = 'race-nav-buttons';

    const uniqueRaces = [...new Set(races.map(r => r['race number']))].sort((a, b) => a - b);

    uniqueRaces.forEach(raceNum => {
        const btn = document.createElement('button');
        btn.className = 'race-nav-button';
        btn.textContent = `Race ${raceNum}`;
        btn.onclick = () => scrollToRace(raceNum);
        navButtons.appendChild(btn);
    });

    navContainer.appendChild(navButtons);

    // Action buttons
    const actionDiv = document.createElement('div');
    actionDiv.className = 'action-buttons';

    const clearBtn = document.createElement('button');
    clearBtn.className = 'action-button';
    clearBtn.textContent = 'Clear All';
    clearBtn.onclick = clearAllResults;

    const expandBtn = document.createElement('button');
    expandBtn.className = 'action-button';
    expandBtn.textContent = 'Expand All';
    expandBtn.onclick = () => toggleAllRaces(true);

    const collapseBtn = document.createElement('button');
    collapseBtn.className = 'action-button';
    collapseBtn.textContent = 'Collapse All';
    collapseBtn.onclick = () => toggleAllRaces(false);

    actionDiv.appendChild(clearBtn);
    actionDiv.appendChild(expandBtn);
    actionDiv.appendChild(collapseBtn);

    navContainer.appendChild(actionDiv);

    return navContainer;
}

// ====== RACE NAVIGATION ACTION FUNCTIONS ======
function scrollToRace(raceNum) {
    const target = document.getElementById(`race-${raceNum}`);
    if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

function toggleAllRaces(expand = true) {
    document.querySelectorAll('.race-table-container').forEach(div => {
        if (expand) {
            div.classList.add('expanded');
            div.classList.remove('collapsed');
        } else {
            div.classList.add('collapsed');
            div.classList.remove('expanded');
        }
    });

    // Toggle the arrow icons if present
    document.querySelectorAll('.collapse-arrow').forEach(arrow => {
        if (expand) arrow.classList.remove('collapsed');
        else arrow.classList.add('collapsed');
    });
}


function clearAllResults() {
    if (meetings.length === 0) return;
    
    if (confirm('Clear all results? This cannot be undone.')) {
        meetings = [];
        activeMeetingIndex = 0;
        renderMeetingTabs();
        document.getElementById('results').innerHTML = '';
    }
}

// ====== FILTER SYSTEM FUNCTIONS ======

// Master list of all available filter criteria
const FILTER_CRITERIA = {
    droppingInClass: {
        label: 'Dropping in Class',
        detect: (notes) => notes.includes('Stepping DOWN') || notes.includes('COMBO BONUS')
    },
    risingInClass: {
        label: 'Rising in Class',
        detect: (notes) => notes.includes('Stepping UP')
    },
    fastestSectionalAvg: {
        label: 'Fastest Sectional (Avg Last 3)',
        detect: (notes) => notes.includes('fastest avg sectional')
    },
    secondFastestSectionalAvg: {
        label: '2nd Fastest Sectional (Avg Last 3)',
        detect: (notes) => notes.includes('2nd fastest avg sectional')
    },
    thirdFastestSectionalAvg: {
        label: '3rd Fastest Sectional (Avg Last 3)',
        detect: (notes) => notes.includes('3rd fastest avg sectional')
    },
    fastestLastStart: {
        label: 'Fastest Last Start Sectional',
        detect: (notes) => notes.includes('fastest last start sectional')
    },
    secondFastestLastStart: {
        label: '2nd Fastest Last Start Sectional',
        detect: (notes) => notes.includes('2nd fastest last start sectional')
    },
    thirdFastestLastStart: {
        label: '3rd Fastest Last Start Sectional',
        detect: (notes) => notes.includes('3rd fastest last start sectional')
    },
    comboBonus: {
        label: 'Combo Bonus (Sectional + Dropping Class)',
        detect: (notes) => notes.includes('COMBO BONUS')
    },
    wonLastStart: {
        label: 'Won Last Start',
        detect: (notes) => notes.includes('Dominant last start win') || notes.includes('Comfortable last start win') || notes.includes('Narrow last start win') || notes.includes('Photo finish last start win')
    },
    narrowLoss: {
        label: 'Narrow Loss (2nd/3rd)',
        detect: (notes) => notes.includes('Narrow loss') || notes.includes('very competitive')
    },
    closeLoss: {
        label: 'Close Loss (2nd/3rd)',
        detect: (notes) => notes.includes('Close loss')
    },
    ranPlaces: {
        label: 'Ran Places in Last 10',
        detect: (notes) => notes.includes('Ran places:')
    },
    recentWinner: {
        label: 'Recent Winner (Last 10)',
        detect: (notes) => notes.includes('wins in last 10')
    },
    freshAndReady: {
        label: 'Fresh and Ready (0-21 days)',
        detect: (notes) => notes.includes('Fresh and ready')
    },
    quickBackup: {
        label: 'Quick Back-up (< 14 days)',
        detect: (notes) => notes.includes('Quick back-up')
    },
    idealSpacing: {
        label: 'Ideal Spacing (21-60 days)',
        detect: (notes) => notes.includes('Ideal spacing')
    },
    undefeatedDistance: {
        label: 'Undefeated at Distance',
        detect: (notes) => notes.includes('UNDEFEATED') && notes.includes('at this distance')
    },
    undefeatedTrack: {
        label: 'Undefeated at Track',
        detect: (notes) => notes.includes('UNDEFEATED') && notes.includes('at this track')
    },
    exceptionalWinRate: {
        label: 'Exceptional Win Rate',
        detect: (notes) => notes.includes('Exceptional win rate')
    },
    strongWinRate: {
        label: 'Strong Win Rate',
        detect: (notes) => notes.includes('Strong win rate')
    },
    goodWinRate: {
        label: 'Good Win Rate',
        detect: (notes) => notes.includes('Good win rate')
    },
    elitePodiumRate: {
        label: 'Elite Podium Rate',
        detect: (notes) => notes.includes('Elite podium rate')
    },
    excellentPodiumRate: {
        label: 'Excellent Podium Rate',
        detect: (notes) => notes.includes('Excellent podium rate')
    },
    strongPodiumRate: {
        label: 'Strong Podium Rate',
        detect: (notes) => notes.includes('Strong podium rate')
    },
    goodPodiumRate: {
        label: 'Good Podium Rate',
        detect: (notes) => notes.includes('Good podium rate')
    },
    optimalWeight: {
        label: 'Optimal Weight (52-56kg)',
        detect: (notes) => notes.includes('Optimal weight')
    },
    goodWeight: {
        label: 'Good Weight Range',
        detect: (notes) => /Weight less claim = 5[2-6]kg/.test(notes)
    },
    loveTheJockey: {
        label: 'Elite Jockey (Love the Jockey)',
        detect: (notes) => notes.includes('Love the Jockey')
    },
    topJockey: {
        label: 'Top Jockey',
        detect: (notes) => notes.includes('Good Jockey')
    },
    loveTheTrainer: {
        label: 'Elite Trainer (Love the Trainer)',
        detect: (notes) => notes.includes('Love the Trainer')
    },
    topTrainer: {
        label: 'Top Trainer',
        detect: (notes) => notes.includes('Good Trainer')
    },
    firstUpSpecialist: {
        label: 'First Up Specialist',
        detect: (notes) => notes.includes('first-up specialist')
    },
    secondUpSpecialist: {
        label: 'Second Up Specialist',
        detect: (notes) => notes.includes('second-up specialist')
    },
    longerDistance: {
        label: 'Longer Distance Than Previous',
        detect: (notes) => notes.includes('Longer dist than previous')
    },
    shorterDistance: {
        label: 'Shorter Distance Than Previous',
        detect: (notes) => notes.includes('Shorter dist than previous')
    },
    sameDistance: {
        label: 'Same Distance as Previous',
        detect: (notes) => notes.includes('Same dist as previous')
    },
    positiveFormPrice: {
        label: 'Positive Form Price',
        detect: (notes) => /\+\d+\.\d+ : Form price/.test(notes)
    },
    goodBarrier: {
        label: 'Good Barrier (1-4)',
        detect: (notes) => notes.includes('Excellent barrier') || notes.includes('Good barrier')
    }
};

// Parse a horse's notes and return which criteria they meet
function parseHorseCriteria(notes) {
    if (!notes || typeof notes !== 'string') {
        return {};
    }
    
    const criteria = {};
    
    // Check each filter criterion
    Object.keys(FILTER_CRITERIA).forEach(key => {
        criteria[key] = FILTER_CRITERIA[key].detect(notes);
    });
    
    return criteria;
}

// Global state for active filters
let activeFilters = [];

// Create the filter bar UI
function createFilterBar() {
    const filterBar = document.createElement('div');
    filterBar.className = 'filter-bar-container';
    filterBar.id = 'filter-bar';

    // Header section with dropdown and clear button
    const headerDiv = document.createElement('div');
    headerDiv.className = 'filter-bar-header';

    // Title with icon
    const title = document.createElement('div');
    title.className = 'filter-bar-title';
    title.innerHTML = '🔍 Add Filter:';

    // Dropdown selector
    const dropdown = document.createElement('select');
    dropdown.className = 'filter-dropdown';
    dropdown.id = 'filter-dropdown';

    // Default option
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = 'Select a filter...';
    dropdown.appendChild(defaultOption);

    // Add all filter options
    Object.keys(FILTER_CRITERIA).forEach(key => {
        const option = document.createElement('option');
        option.value = key;
        option.textContent = FILTER_CRITERIA[key].label;
        dropdown.appendChild(option);
    });

    // Clear all filters button
    const clearBtn = document.createElement('button');
    clearBtn.className = 'clear-filters-btn';
    clearBtn.textContent = 'Clear All Filters';
    clearBtn.onclick = clearAllFilters;

    headerDiv.appendChild(title);
    headerDiv.appendChild(dropdown);
    headerDiv.appendChild(clearBtn);
    filterBar.appendChild(headerDiv);

    // Active filters display section
    const activeFiltersDiv = document.createElement('div');
    activeFiltersDiv.className = 'active-filters-container';
    activeFiltersDiv.id = 'active-filters-container';
    filterBar.appendChild(activeFiltersDiv);

    // Event listener for dropdown selection
    dropdown.addEventListener('change', function() {
        if (this.value) {
            addFilter(this.value);
            this.value = ''; // Reset dropdown
        }
    });

    return filterBar;
}

// Update the active filters display
function updateActiveFiltersDisplay() {
    const container = document.getElementById('active-filters-container');
    if (!container) return;

    container.innerHTML = '';

    if (activeFilters.length === 0) {
        return; // Don't show anything if no filters
    }

    // "Active Filters:" label
    const label = document.createElement('span');
    label.className = 'active-filters-label';
    label.textContent = 'Active Filters:';
    container.appendChild(label);

    // Create a tag for each active filter
    activeFilters.forEach(filterKey => {
        const tag = document.createElement('div');
        tag.className = 'filter-tag';

        const tagText = document.createElement('span');
        tagText.textContent = FILTER_CRITERIA[filterKey].label;

        const removeBtn = document.createElement('span');
        removeBtn.className = 'filter-tag-remove';
        removeBtn.textContent = '×';
        removeBtn.onclick = () => removeFilter(filterKey);

        tag.appendChild(tagText);
        tag.appendChild(removeBtn);
        container.appendChild(tag);
    });

    // Match counter
    const matchCounter = document.createElement('span');
    matchCounter.className = 'filter-match-counter';
    matchCounter.id = 'filter-match-counter';
    matchCounter.textContent = 'Calculating matches...';
    container.appendChild(matchCounter);

    // Update the counter after a brief delay to let DOM update
    setTimeout(updateMatchCounter, 100);
}

// Update the match counter
function updateMatchCounter() {
    const counter = document.getElementById('filter-match-counter');
    if (!counter) return;

    // Count matching horses
    const matchingRows = document.querySelectorAll('.horse-row-match');
    const matchCount = matchingRows.length;

    // Count races with matches
    const racesWithMatches = new Set();
    matchingRows.forEach(row => {
        // Find which race this row belongs to
        const raceContainer = row.closest('.race-container');
        if (raceContainer) {
            racesWithMatches.add(raceContainer.id);
        }
    });

    if (activeFilters.length === 0) {
        counter.textContent = 'No filters active';
    } else if (matchCount === 0) {
        counter.textContent = 'No horses match all filters';
    } else if (matchCount === 1) {
        counter.textContent = `1 horse matches in ${racesWithMatches.size} race${racesWithMatches.size !== 1 ? 's' : ''}`;
    } else {
        counter.textContent = `${matchCount} horses match across ${racesWithMatches.size} race${racesWithMatches.size !== 1 ? 's' : ''}`;
    }
}

// Add a filter to the active filters list
function addFilter(filterKey) {
    // Don't add if already active
    if (activeFilters.includes(filterKey)) {
        return;
    }

    // Add to active filters
    activeFilters.push(filterKey);

    // Update the display
    updateActiveFiltersDisplay();

    // Apply filters to highlight horses
    applyFilters();
}

// Remove a filter from the active filters list
function removeFilter(filterKey) {
    // Remove from active filters
    activeFilters = activeFilters.filter(key => key !== filterKey);

    // Update the display
    updateActiveFiltersDisplay();

    // Re-apply filters (or clear highlights if no filters)
    applyFilters();
}

// Clear all filters
function clearAllFilters() {
    // Clear the active filters array
    activeFilters = [];

    // Update the display
    updateActiveFiltersDisplay();

    // Remove all highlighting
    applyFilters();
}

// Apply filters and highlight matching horses
function applyFilters() {
    // Get all horse rows from all race tables
    const allRows = document.querySelectorAll('table tr');

    // If no filters are active, remove all highlighting
    if (activeFilters.length === 0) {
        allRows.forEach(row => {
            row.classList.remove('horse-row-match');
        });
        return;
    }

    // Loop through each row
    allRows.forEach(row => {
        // Skip header rows (they don't have horse data)
        const cells = row.querySelectorAll('td');
        if (cells.length === 0) return;

        // Get the notes cell (last cell in the row)
        const notesCell = cells[cells.length - 1];
        if (!notesCell) return;

        const notes = notesCell.innerText || notesCell.textContent;

        // Parse what criteria this horse meets
        const horseCriteria = parseHorseCriteria(notes);

        // Check if horse meets ALL active filters (AND logic)
        const meetsAllFilters = activeFilters.every(filterKey => {
            return horseCriteria[filterKey] === true;
        });

        // Apply or remove highlighting
        if (meetsAllFilters) {
            row.classList.add('horse-row-match');
        } else {
            row.classList.remove('horse-row-match');
        }
    });

    // Update the match counter
    updateMatchCounter();
}

// Add event listener for file input changes
document.getElementById('fileInput').addEventListener('change', function() {
    const files = this.files;
    const trackConditionsDiv = document.getElementById('trackConditions');
    
    // Clear existing dropdowns
    trackConditionsDiv.innerHTML = '';
    
    // Create dropdown for each selected file
    for (let i = 0; i < files.length; i++) {
        const fileName = files[i].name.replace('.csv', '');
        
        const container = document.createElement('div');
        container.style.cssText = 'display: flex; align-items: center; gap: 10px; margin-bottom: 10px;';
        
        const label = document.createElement('label');
        label.textContent = `${fileName} track condition:`;
        label.style.cssText = 'font-size: 14px; font-weight: 600; color: #4a5568; min-width: 200px;';
        
        const select = document.createElement('select');
        select.id = `trackCondition_${i}`;
        select.style.cssText = 'padding: 8px 12px; border: 2px solid #e9ecef; border-radius: 8px; background-color: #f5f7fa; font-size: 14px; color: #4a5568;';
        
        // Add options
        const options = ['firm', 'good', 'soft', 'heavy', 'synthetic'];
        options.forEach(option => {
            const optionElement = document.createElement('option');
            optionElement.value = option;
            optionElement.textContent = option.charAt(0).toUpperCase() + option.slice(1);
            select.appendChild(optionElement);
        });
        
        container.appendChild(label);
        container.appendChild(select);
        trackConditionsDiv.appendChild(container);
    }
});

// Add event listener for analyze button
document.getElementById('analyzeButton').addEventListener('click', function() {
    const fileInput = document.getElementById('fileInput');
    const files = fileInput.files;
    
    // Capture track conditions for each file
    const trackConditions = [];
    for (let i = 0; i < files.length; i++) {
        const dropdown = document.getElementById(`trackCondition_${i}`);
        if (dropdown) {
            trackConditions.push(dropdown.value);
        } else {
            trackConditions.push('good'); // default fallback
        }
    }
    
    const isAdvanced = document.getElementById('advancedToggle').checked;
    const troubleshooting = document.getElementById('troubleshootingToggle').checked;

    if (files.length > 0) {
        let allData = [];
        let filesProcessed = 0;
        
        for (let i = 0; i < files.length; i++) {
            const fileName = files[i].name;
            const trackCondition = trackConditions[i];
            
            Papa.parse(files[i], {
                delimiter: ",",
                header: true,
                complete: function(results) {
                    const data = results.data;
                    const analysisResults = [];

                    // Get the header row for comparison
                    const headerRow = results.meta.fields;

                    // Filter out rows that have any undefined values or are repeats of the header row
                    var filteredData = data.filter(row => {
                        const isHeaderRow = Object.values(row).every((value, index) => value === headerRow[index]);
                        return !isHeaderRow;
                    });

                    // Calculate score for horses using (1) multi-row analysis and (2) single row analysis
                    const filteredDataSectional = getLowestSectionalsByRace(filteredData);
                    const averageFormPrices = calculateAverageFormPrices(filteredData);

                    // Make filteredData only the latest entry for each horse
                    const getUniqueHorsesOnly = (data) => {
                        const latestByComposite = new Map();
                        
                        data.forEach(entry => {
                            const compositeKey = `${entry['horse name']}-${entry['race number']}`;
                            const currentDate = entry['form meeting date'];
                            
                            if (!latestByComposite.has(compositeKey) || 
                                currentDate > new Date(latestByComposite.get(compositeKey)['form meeting date'])) {
                                latestByComposite.set(compositeKey, entry);
                            }
                        });
                        
                        return Array.from(latestByComposite.values());
                    };

                    filteredData = getUniqueHorsesOnly(filteredData);

                    // Process each horse entry
                    filteredData.forEach((horse, index) => {
                        if (horse['meeting date'] === undefined || !horse['horse name']) return;
                        
                        const compositeKey = `${horse['horse name']}-${horse['race number']}`;
                        const avgFormPrice = averageFormPrices[compositeKey];

                        var [score, notes] = calculateScore(horse, trackCondition, troubleshooting, avgFormPrice);

                        const raceNumber = horse['race number'];
                        const horseName = horse['horse name'];

                        const matchingHorse = filteredDataSectional.find(horse => 
                            parseInt(horse.race) === parseInt(raceNumber) && 
                            horse.name.toLowerCase().trim() === horseName.toLowerCase().trim()
                        );

                        if (matchingHorse) {
                            score += matchingHorse.sectionalScore;
                            notes += matchingHorse.sectionalNote;
                            
                            if (matchingHorse.hasAverage1st && matchingHorse.hasLastStart1st) {
                                const [classScore, classNotes] = compareClasses(
                                    horse['class restrictions'], 
                                    horse['form class'],
                                    horse['race prizemoney'],
                                    horse['prizemoney']
                                );
                                if (classScore > 0) {
                                    score += 15;
                                    notes += '+15.0 : COMBO BONUS - Fastest sectional + dropping in class\n';
                                    
                                    if (troubleshooting) {
                                        console.log(`Combo bonus awarded to ${horseName}: Fastest sectional + dropping in class (class score: ${classScore})`);
                                    }
                                }
                            }
                        } else {
                            console.log(`No matching horse found for ${horseName} in race ${raceNumber}`);
                        }

                        analysisResults.push({ horse, score, notes });
                    });

                    var uniqueResults = Array.from(new Map(analysisResults.map(item => [item.horse['horse name'], item])).values());
                    uniqueResults = calculateTrueOdds(uniqueResults, priorStrength=1, troubleshooting);
                    uniqueResults.sort((a,b)=> (a.horse['race number'] - b.horse['race number'] || b.score - a.score));

                    // Create or get meeting
                    let currentMeeting = createMeeting(fileName);
                    if (!currentMeeting) return;

                    currentMeeting.races = filteredData;
                    currentMeeting.analysisResults = uniqueResults;

                    // Render results for active meeting
                    renderResults();
                }
            });
        }
    } else {
        alert('Please upload a CSV file.');
    }
});

// ====== FINAL RENDER RESULTS WRAPPER (preserves scores + enables controls) ======
function renderResults() {
    const resultsDiv = document.getElementById('results');
    resultsDiv.innerHTML = '';

    if (meetings.length === 0) {
        resultsDiv.innerHTML = '<p>No meetings loaded.</p>';
        return;
    }

    const activeMeeting = meetings[activeMeetingIndex];
    if (!activeMeeting || !activeMeeting.analysisResults) {
        resultsDiv.innerHTML = '<p>No analysis results available.</p>';
        return;
    }

    // Step 1 — Render all results normally (so we keep your full scoring view)
    displayResults(activeMeeting.analysisResults, false, activeMeeting.fileName);

    // Step 2 — Inject the navigation panel above
    const navBar = createRaceNavigation(activeMeeting.races);
    resultsDiv.prepend(navBar);

    // Step 2.5 — Inject the filter bar between navigation and races
    const filterBar = createFilterBar();
    navBar.insertAdjacentElement('afterend', filterBar);

    // Re-apply any active filters to the newly rendered results
    if (activeFilters.length > 0) {
        applyFilters();
    }

    // Step 4 — Re-bind Expand / Collapse All buttons (from your new UI)
    const expandAllBtn = document.getElementById('expandAll');
    const collapseAllBtn = document.getElementById('collapseAll');
    if (expandAllBtn) {
        expandAllBtn.onclick = () => {
            document.querySelectorAll('.race-table-container').forEach(div => {
                div.classList.add('expanded');
                div.classList.remove('collapsed');
            });
        };
    }

    if (collapseAllBtn) {
        collapseAllBtn.onclick = () => {
            document.querySelectorAll('.race-table-container').forEach(div => {
                div.classList.add('collapsed');
                div.classList.remove('expanded');
            });
        };
    }

    // Step 5 — Re-bind race navigation scroll buttons
    document.querySelectorAll('.race-nav-btn').forEach(btn => {
        btn.onclick = () => {
            const target = document.getElementById(`race-${btn.dataset.race}`);
            if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        };
    });
}function displayResults(analysisResults, isAdvanced, fileName) {
    const resultsDiv = document.getElementById('results');
    
    // If this is the first meeting, clear previous results
    if (resultsDiv.children.length === 0) {
        resultsDiv.innerHTML = '<h2>Analysis Results</h2>';
    }
    
    // Add meeting header
    const meetingHeader = document.createElement('div');
    meetingHeader.style.cssText = 'margin-top: 30px; margin-bottom: 20px;';
    meetingHeader.innerHTML = `<h3 style="color: #2d3748; font-size: 1.5em; border-bottom: 2px solid #667eea; padding-bottom: 10px;">${fileName.replace('.csv', '')}</h3>`;
    resultsDiv.appendChild(meetingHeader);

    // Calculate dashboard statistics
    const dashboardStats = calculateDashboardStats(analysisResults);
    
    // Add PDF download button
    const pdfButtonContainer = document.createElement('div');
    pdfButtonContainer.style.cssText = 'margin-bottom: 15px;';
    const pdfButton = document.createElement('button');
    pdfButton.className = 'pdf-download-btn';
    pdfButton.textContent = 'Download PDF Report';
    pdfButton.onclick = () => {
        // Group horses by race number for PDF
        const raceGroups = {};
        analysisResults.forEach(result => {
            const raceNum = result.horse['race number'];
            if (!raceGroups[raceNum]) {
                raceGroups[raceNum] = [];
            }
            raceGroups[raceNum].push(result);
        });
        generatePDF(fileName, dashboardStats, raceGroups, isAdvanced);
    };
    pdfButtonContainer.appendChild(pdfButton);
    resultsDiv.appendChild(pdfButtonContainer);
    
    // Create and display dashboard
    const dashboard = createDashboard(dashboardStats);
    resultsDiv.appendChild(dashboard);

    // Group horses by race number
    const raceGroups = {};
    analysisResults.forEach(result => {
        const raceNum = result.horse['race number'];
        if (!raceGroups[raceNum]) {
            raceGroups[raceNum] = [];
        }
        raceGroups[raceNum].push(result);
    });

    // Sort race numbers
    const sortedRaceNumbers = Object.keys(raceGroups).sort((a, b) => parseInt(a) - parseInt(b));

    // Create a section for each race
    sortedRaceNumbers.forEach(raceNum => {
        const raceResults = raceGroups[raceNum];
        
        // Create race container
        const raceContainer = document.createElement('div');
        raceContainer.className = 'race-container';
        raceContainer.id = `race-${raceNum}`; // THIS IS THE KEY ID FOR SCROLLING!

        // Create race dashboard (clickable header)
        const raceDashboard = document.createElement('div');
        raceDashboard.className = 'race-dashboard';

        // Race dashboard header
        const dashboardHeader = document.createElement('div');
        dashboardHeader.className = 'race-dashboard-header';

        // Race title with collapse arrow
        const raceTitle = document.createElement('div');
        raceTitle.className = 'race-title';
        raceTitle.innerHTML = `
            <span class="collapse-arrow">▼</span>
            <span>Race ${raceNum}</span>
        `;

        dashboardHeader.appendChild(raceTitle);
        raceDashboard.appendChild(dashboardHeader);

        // Top 3 horses preview
        const topHorsesDiv = document.createElement('div');
        topHorsesDiv.className = 'top-horses';

        const top3 = raceResults.slice(0, 3);
        top3.forEach((result, index) => {
            const horseRow = document.createElement('div');
            horseRow.className = 'horse-row';

            const positionBadge = document.createElement('div');
            positionBadge.className = `position-badge ${index === 0 ? 'first' : index === 1 ? 'second' : 'third'}`;
            positionBadge.textContent = index + 1;

            const horseName = document.createElement('div');
            horseName.className = 'horse-name';
            horseName.textContent = result.horse['horse name'];

            const horseStats = document.createElement('div');
            horseStats.className = 'horse-stats';
            horseStats.innerHTML = `
                <div class="stat-item">
                    <span class="stat-label">Score:</span>
                    <span class="stat-value">${result.score.toFixed(1)}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Odds:</span>
                    <span class="stat-value">${result.trueOdds}</span>
                </div>
            `;

            horseRow.appendChild(positionBadge);
            horseRow.appendChild(horseName);
            horseRow.appendChild(horseStats);
            topHorsesDiv.appendChild(horseRow);
        });

        raceDashboard.appendChild(topHorsesDiv);

        // Race stats bar
        const raceStatsBar = document.createElement('div');
        raceStatsBar.className = 'race-stats-bar';

        const scores = raceResults.map(r => r.score);
        const highestScore = Math.max(...scores);
        const lowestScore = Math.min(...scores);
        const gap = highestScore - (scores.length > 1 ? scores[1] : 0);

        raceStatsBar.innerHTML = `
            <div class="race-stat">
                <span class="race-stat-label">Horses:</span>
                <span class="race-stat-value">${raceResults.length}</span>
            </div>
            <div class="race-stat">
                <span class="race-stat-label">High Score:</span>
                <span class="race-stat-value">${highestScore.toFixed(1)}</span>
            </div>
            <div class="race-stat">
                <span class="race-stat-label">Score Gap:</span>
                <span class="race-stat-value">${gap.toFixed(1)}</span>
            </div>
            <div class="race-stat">
                <span class="race-stat-label">Worst Score:</span>
                <span class="race-stat-value worst-score">${lowestScore.toFixed(1)}</span>
            </div>
        `;

        raceDashboard.appendChild(raceStatsBar);
        raceContainer.appendChild(raceDashboard);

        // Create collapsible table container
        const tableContainer = document.createElement('div');
        tableContainer.className = 'race-table-container expanded'; // Start expanded

        // Create table for this race
        const table = document.createElement('table');
        table.border = "1";

        // Create header row
        const headerRow = table.insertRow();
        headerRow.insertCell().innerText = "Position";
        headerRow.insertCell().innerText = "Horse Name";
        headerRow.insertCell().innerText = "Score";
        headerRow.insertCell().innerText = "Odds";
        
        if (isAdvanced) {
            headerRow.insertCell().innerText = "Win Prob.";
            headerRow.insertCell().innerText = "Performance Prob.";
            headerRow.insertCell().innerText = "Base Prob.";
        }
        
        headerRow.insertCell().innerText = "Notes";

        // Populate table with horses from this race only
        raceResults.forEach((result, index) => {
            const row = table.insertRow();
            row.insertCell().innerText = index + 1;
            row.insertCell().innerText = result.horse['horse name'];
            row.insertCell().innerText = result.score.toFixed(1);
            row.insertCell().innerText = result.trueOdds;
            
            if (isAdvanced) {
                row.insertCell().innerText = result.winProbability;
                row.insertCell().innerText = result.performanceComponent;
                row.insertCell().innerText = result.baseProbability;
            }
            
            row.insertCell().innerText = result.notes;
        });

        tableContainer.appendChild(table);
        raceContainer.appendChild(tableContainer);

        // Add click handler to toggle collapse
        raceDashboard.onclick = () => {
            const arrow = raceDashboard.querySelector('.collapse-arrow');
            if (tableContainer.classList.contains('expanded')) {
                tableContainer.classList.remove('expanded');
                tableContainer.classList.add('collapsed');
                arrow.classList.add('collapsed');
            } else {
                tableContainer.classList.remove('collapsed');
                tableContainer.classList.add('expanded');
                arrow.classList.remove('collapsed');
            }
        };

        resultsDiv.appendChild(raceContainer);
    });
}

function calculateDashboardStats(analysisResults) {
    // Group horses by race number
    const raceGroups = analysisResults.reduce((acc, result) => {
        const raceNumber = result.horse['race number'];
        if (!acc[raceNumber]) {
            acc[raceNumber] = [];
        }
        acc[raceNumber].push(result);
        return acc;
    }, {});

    const raceStats = [];
    let overallHighestScore = -Infinity;
    let overallLargestGap = 0;
    let bestRaceForScore = null;
    let bestRaceForGap = null;

    // Calculate stats for each race
    Object.keys(raceGroups).forEach(raceNumber => {
        const horses = raceGroups[raceNumber];
        const scores = horses.map(horse => horse.score);
        
        const sortedScores = [...scores].sort((a, b) => b - a); // Sort scores highest to lowest
        const highestScore = sortedScores[0];
        const secondHighestScore = sortedScores[1] || 0; // Handle case with only 1 horse
        const gap = highestScore - secondHighestScore;
        
        // Find the horse with highest score in this race
        const topHorse = horses.find(horse => horse.score === highestScore);
        
        raceStats.push({
            raceNumber: parseInt(raceNumber),
            highestScore: highestScore,
            secondHighestScore: secondHighestScore,
            gap: gap,
            topHorse: topHorse.horse['horse name'],
            horseCount: horses.length
        });

        // Track overall meeting stats
        if (highestScore > overallHighestScore) {
            overallHighestScore = highestScore;
            bestRaceForScore = {
                raceNumber: raceNumber,
                horseName: topHorse.horse['horse name'],
                score: highestScore
            };
        }

        if (gap > overallLargestGap) {
            overallLargestGap = gap;
            bestRaceForGap = {
                raceNumber: raceNumber,
                gap: gap,
                highestScore: highestScore,
                secondHighestScore: secondHighestScore
            };
        }
    });

    // Sort race stats by race number
    raceStats.sort((a, b) => a.raceNumber - b.raceNumber);

    return {
        raceStats: raceStats,
        overallHighestScore: overallHighestScore,
        overallLargestGap: overallLargestGap,
        bestRaceForScore: bestRaceForScore,
        bestRaceForGap: bestRaceForGap,
        totalRaces: raceStats.length
    };
}

function createDashboard(stats) {
    const dashboard = document.createElement('div');
    dashboard.style.cssText = `
        background: #2d2d2d;
        color: black;
        padding: 20px;
        border-radius: 12px;
        margin-bottom: 25px;
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
    `;

    // Overall meeting stats
    const overallStats = document.createElement('div');
    overallStats.style.cssText = `
        display: flex;
        justify-content: space-around;
        margin-bottom: 20px;
        flex-wrap: wrap;
        gap: 15px;
    `;

    const createStatCard = (title, value, subtitle = '') => {
        const card = document.createElement('div');
        card.style.cssText = `
            background: rgba(255, 255, 255, 0.1);
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            min-width: 150px;
            backdrop-filter: blur(10px);
        `;
        
        card.innerHTML = `
            <div style="font-size: 14px; opacity: 0.9; margin-bottom: 5px; color: white;">${title}</div>
            <div style="font-size: 24px; font-weight: bold; margin-bottom: 5px; color: white;">${value}</div>
            ${subtitle ? `<div style="font-size: 12px; opacity: 0.8; color: white;">${subtitle}</div>` : ''}
        `;
        return card;
    };

    // Add overall stats cards
    overallStats.appendChild(createStatCard(
        'Meeting High Score', 
        stats.overallHighestScore.toFixed(1),
        `${stats.bestRaceForScore.horseName} (Race ${stats.bestRaceForScore.raceNumber})`
    ));

    overallStats.appendChild(createStatCard(
        'Largest Score Gap', 
        stats.overallLargestGap.toFixed(1),
        `Race ${stats.bestRaceForGap.raceNumber}`
    ));

    overallStats.appendChild(createStatCard(
        'Total Races', 
        stats.totalRaces
    ));

    dashboard.appendChild(overallStats);

    // Race-by-race breakdown
    const raceBreakdown = document.createElement('div');
    raceBreakdown.innerHTML = '<h3 style="margin: 0 0 15px 0; font-size: 18px;">Race Breakdown</h3>';
    
    const raceTable = document.createElement('table');
    raceTable.style.cssText = `
        width: 100%;
        border-collapse: collapse;
        background: rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        overflow: hidden;
    `;

    // Create race table header
    const raceHeaderRow = raceTable.insertRow();
    raceHeaderRow.style.background = 'rgba(255, 255, 255, 0.2)';
    ['Race', 'Top Horse', 'High Score', 'Score Gap', 'Horses'].forEach(text => {
        const cell = raceHeaderRow.insertCell();
        cell.innerText = text;
        cell.style.cssText = 'padding: 10px; font-weight: bold; text-align: center;';
    });

    // Populate race table
    stats.raceStats.forEach(raceStat => {
        const row = raceTable.insertRow();
        row.style.cssText = 'border-bottom: 1px solid rgba(255, 255, 255, 0.1);';
        
        // Race number
        const raceCell = row.insertCell();
        raceCell.innerText = raceStat.raceNumber;
        raceCell.style.cssText = 'padding: 8px; text-align: center; font-weight: bold;';

        // Top horse
        const horseCell = row.insertCell();
        horseCell.innerText = raceStat.topHorse;
        horseCell.style.cssText = 'padding: 8px; text-align: left;';

        // High score
        const scoreCell = row.insertCell();
        scoreCell.innerText = raceStat.highestScore.toFixed(1);
        scoreCell.style.cssText = 'padding: 8px; text-align: center; font-weight: bold;';
        
        // Highlight if this is the meeting high score
        if (raceStat.highestScore === stats.overallHighestScore) {
            scoreCell.style.background = 'rgba(0, 255, 0, 0.3)';
            scoreCell.style.border = '2px solid green';
        }

        // Score gap
        const gapCell = row.insertCell();
        gapCell.innerText = raceStat.gap.toFixed(1);
        gapCell.style.cssText = 'padding: 8px; text-align: center;';
        
        // Highlight if this is the largest gap
        if (raceStat.gap === stats.overallLargestGap) {
            gapCell.style.background = 'rgba(0, 255, 0, 0.3)';
            gapCell.style.border = '2px solid green';
        }

        // Horse count
        const countCell = row.insertCell();
        countCell.innerText = raceStat.horseCount;
        countCell.style.cssText = 'padding: 8px; text-align: center;';
    });

    raceBreakdown.appendChild(raceTable);
    dashboard.appendChild(raceBreakdown);

    return dashboard;
}

function generatePDF(fileName, dashboardStats, raceGroups, isAdvanced) {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    
    // Title
    const meetingName = fileName.replace('.csv', '');
    doc.setFontSize(18);
    doc.setFont(undefined, 'bold');
    doc.text(meetingName, 14, 20);
    
    // Subtitle
    doc.setFontSize(12);
    doc.setFont(undefined, 'normal');
    doc.text('Partington Probability Engine Analysis', 14, 28);
    
    let yPosition = 40;
    
    // Meeting Summary
    doc.setFontSize(14);
    doc.setFont(undefined, 'bold');
    doc.text('Meeting Summary', 14, yPosition);
    yPosition += 8;
    
    doc.setFontSize(10);
    doc.setFont(undefined, 'normal');
    doc.text(`Meeting High Score: ${dashboardStats.overallHighestScore.toFixed(1)} - ${dashboardStats.bestRaceForScore.horseName} (Race ${dashboardStats.bestRaceForScore.raceNumber})`, 14, yPosition);
    yPosition += 6;
    doc.text(`Largest Score Gap: ${dashboardStats.overallLargestGap.toFixed(1)} points (Race ${dashboardStats.bestRaceForGap.raceNumber})`, 14, yPosition);
    yPosition += 6;
    doc.text(`Total Races: ${dashboardStats.totalRaces}`, 14, yPosition);
    yPosition += 12;
    
    // Race Breakdown Summary Table
    doc.setFontSize(12);
    doc.setFont(undefined, 'bold');
    doc.text('Race Breakdown', 14, yPosition);
    yPosition += 6;
    
    const summaryTableData = dashboardStats.raceStats.map(raceStat => [
        raceStat.raceNumber,
        raceStat.topHorse,
        raceStat.highestScore.toFixed(1),
        raceStat.gap.toFixed(1),
        raceStat.horseCount
    ]);
    
    doc.autoTable({
        startY: yPosition,
        head: [['Race', 'Top Horse', 'High Score', 'Gap', 'Horses']],
        body: summaryTableData,
        theme: 'striped',
        headStyles: { fillColor: [45, 45, 45], textColor: [255, 255, 255], fontStyle: 'bold' },
        styles: { fontSize: 9, cellPadding: 3 },
        columnStyles: {
            0: { halign: 'center', fontStyle: 'bold' },
            2: { halign: 'center' },
            3: { halign: 'center' },
            4: { halign: 'center' }
        }
    });
    
    // Get position after summary table
    yPosition = doc.lastAutoTable.finalY + 15;
    
    // Sort race numbers
    const sortedRaceNumbers = Object.keys(raceGroups).sort((a, b) => parseInt(a) - parseInt(b));
    
    // Add each race details
    sortedRaceNumbers.forEach((raceNum, index) => {
        const raceResults = raceGroups[raceNum];
        
        // Add new page if needed
        if (yPosition > 250 || index > 0) {
            doc.addPage();
            yPosition = 20;
        }
        
        // Race header
        doc.setFontSize(14);
        doc.setFont(undefined, 'bold');
        doc.text(`Race ${raceNum}`, 14, yPosition);
        yPosition += 8;
        
        // Build table headers
        const headers = ['Pos', 'Horse Name', 'Score', 'Odds'];
        if (isAdvanced) {
            headers.push('Win Prob.', 'Perf. Prob.', 'Base Prob.');
        }
        headers.push('Notes');
        
        // Build table data
        const tableData = raceResults.map((result, idx) => {
            const row = [
                idx + 1,
                result.horse['horse name'],
                result.score.toFixed(1),
                result.trueOdds
            ];
            
            if (isAdvanced) {
                row.push(
                    result.winProbability,
                    result.performanceComponent,
                    result.baseProbability
                );
            }
            
            row.push(result.notes);
            return row;
        });
        
        // Create table
        doc.autoTable({
            startY: yPosition,
            head: [headers],
            body: tableData,
            theme: 'grid',
            headStyles: { 
                fillColor: [102, 126, 234], 
                textColor: [255, 255, 255],
                fontStyle: 'bold',
                fontSize: 9
            },
            styles: { 
                fontSize: 8,
                cellPadding: 2
            },
            columnStyles: {
                0: { halign: 'center', cellWidth: 12 },
                2: { halign: 'center', fontStyle: 'bold' },
                3: { halign: 'center' }
            },
            didParseCell: function(data) {
                // Highlight top 3 positions
                if (data.section === 'body' && data.column.index === 0) {
                    const position = parseInt(data.cell.text[0]);
                    if (position === 1) {
                        data.cell.styles.fillColor = [46, 213, 115]; // Green
                        data.cell.styles.textColor = [255, 255, 255];
                        data.cell.styles.fontStyle = 'bold';
                    } else if (position === 2) {
                        data.cell.styles.fillColor = [255, 234, 167]; // Yellow
                    } else if (position === 3) {
                        data.cell.styles.fillColor = [255, 159, 67]; // Orange
                    }
                }
            }
        });
        
        yPosition = doc.lastAutoTable.finalY + 15;
    });
    
    // Add footer to all pages
    const pageCount = doc.internal.getNumberOfPages();
    for (let i = 1; i <= pageCount; i++) {
        doc.setPage(i);
        doc.setFontSize(8);
        doc.setFont(undefined, 'normal');
        doc.setTextColor(150);
        doc.text(`Generated by Partington Probability Engine - Page ${i} of ${pageCount}`, 14, 285);
        doc.text(new Date().toLocaleString(), 200, 285, { align: 'right' });
    }
    
    // Save the PDF
    doc.save(`${meetingName}_Analysis.pdf`);
}
    </script>
</body>
</html>
