/**
 * Betfair Live Odds Client
 * 
 * Connects to the Betfair SSE stream and updates the race results table
 * with live odds and final positions.
 * 
 * This script only runs if:
 * 1. The results table exists in the DOM
 * 2. BETFAIR_ENABLED is true (script won't be loaded otherwise)
 */

(function() {
    'use strict';

    // Configuration
    const BETFAIR_STREAM_URL = '/betfair/stream';  // Proxied through main app
    const RECONNECT_DELAY = 5000;  // 5 seconds
    const MAX_RECONNECT_ATTEMPTS = 10;

    // State
    let eventSource = null;
    let reconnectAttempts = 0;
    let isConnected = false;

    /**
     * Find the results table in the DOM
     */
    function findResultsTable() {
        // Look for tables in race cards
        const tables = document.querySelectorAll('table');
        for (const table of tables) {
            const header = table.querySelector('thead tr');
            if (header && header.textContent.includes('Horse')) {
                return table;
            }
        }
        return null;
    }

    /**
     * Find a row by betfair selection ID
     */
    function findRowBySelectionId(table, selectionId) {
        const rows = table.querySelectorAll('tbody tr');
        for (const row of rows) {
            const dataSelectionId = row.getAttribute('data-selection-id');
            if (dataSelectionId && parseInt(dataSelectionId) === selectionId) {
                return row;
            }
        }
        return null;
    }

    /**
     * Find a row by horse name (fallback)
     */
    function findRowByHorseName(table, horseName) {
        if (!horseName) return null;
        
        const rows = table.querySelectorAll('tbody tr');
        const normalizedName = horseName.toLowerCase().trim();
        
        for (const row of rows) {
            // Look for horse name in the second column (after position)
            const horseCell = row.querySelector('td:nth-child(2)');
            if (horseCell) {
                const nameDiv = horseCell.querySelector('div:first-child');
                if (nameDiv) {
                    const cellName = nameDiv.textContent.toLowerCase().trim();
                    if (cellName === normalizedName) {
                        return row;
                    }
                }
            }
        }
        return null;
    }

    /**
     * Find or create the Live Odds cell for a row
     */
    function getOrCreateLiveOddsCell(row) {
        let cell = row.querySelector('.betfair-live-odds');
        if (!cell) {
            cell = document.createElement('td');
            cell.className = 'betfair-live-odds';
            cell.style.cssText = 'padding: 12px; text-align: center; border-bottom: 1px solid #e2e8f0; font-weight: 600;';
            
            // Insert after the existing Odds column (4th column)
            const existingCells = row.querySelectorAll('td');
            if (existingCells.length >= 4) {
                existingCells[3].insertAdjacentElement('afterend', cell);
            } else {
                row.appendChild(cell);
            }
        }
        return cell;
    }

    /**
     * Find or create the Result cell for a row
     */
    function getOrCreateResultCell(row) {
        let cell = row.querySelector('.betfair-result');
        if (!cell) {
            cell = document.createElement('td');
            cell.className = 'betfair-result';
            cell.style.cssText = 'padding: 12px; text-align: center; border-bottom: 1px solid #e2e8f0; font-weight: bold;';
            
            // Insert after the Live Odds cell
            const liveOddsCell = row.querySelector('.betfair-live-odds');
            if (liveOddsCell) {
                liveOddsCell.insertAdjacentElement('afterend', cell);
            } else {
                row.appendChild(cell);
            }
        }
        return cell;
    }

    /**
     * Format odds for display
     */
    function formatOdds(backOdds, layOdds) {
        if (!backOdds && !layOdds) return '-';
        
        if (backOdds && layOdds) {
            return `${backOdds.toFixed(2)} / ${layOdds.toFixed(2)}`;
        }
        return backOdds ? backOdds.toFixed(2) : layOdds.toFixed(2);
    }

    /**
     * Format position for display
     */
    function formatPosition(status, position) {
        if (status === 'WINNER') {
            return '<span style="color: #28a745; font-size: 18px;">🏆 1st</span>';
        }
        if (status === 'REMOVED') {
            return '<span style="color: #dc3545;">SCR</span>';
        }
        if (position) {
            const suffix = position === 1 ? 'st' : position === 2 ? 'nd' : position === 3 ? 'rd' : 'th';
            return `${position}${suffix}`;
        }
        if (status === 'LOSER') {
            return '<span style="color: #6c757d;">-</span>';
        }
        return '-';
    }

    /**
     * Update the odds display for a runner
     */
    function updateRunnerOdds(table, runner) {
        let row = findRowBySelectionId(table, runner.selection_id);
        
        // If no row found by selection ID, we can't update (no fallback for live odds)
        if (!row) return;
        
        const cell = getOrCreateLiveOddsCell(row);
        
        // Color based on odds movement (would need previous value tracking)
        const oddsText = formatOdds(runner.best_back, runner.best_lay);
        cell.innerHTML = `<span style="color: #17a2b8;">${oddsText}</span>`;
    }

    /**
     * Update the result display for a runner
     */
    function updateRunnerResult(table, runner) {
        let row = findRowBySelectionId(table, runner.selection_id);
        
        if (!row) return;
        
        const cell = getOrCreateResultCell(row);
        cell.innerHTML = formatPosition(runner.status, runner.position);
        
        // Highlight winner row
        if (runner.status === 'WINNER') {
            row.style.backgroundColor = '#d4edda';
        }
    }

    /**
     * Handle odds update event
     */
    function handleOddsUpdate(data) {
        const tables = document.querySelectorAll('table');
        
        for (const table of tables) {
            // Check if this table has data for this market
            const tableMarketId = table.getAttribute('data-market-id');
            if (tableMarketId && tableMarketId !== data.market_id) {
                continue;
            }
            
            for (const runner of data.runners || []) {
                updateRunnerOdds(table, runner);
            }
        }
    }

    /**
     * Handle market closed event
     */
    function handleMarketClosed(data) {
        const tables = document.querySelectorAll('table');
        
        for (const table of tables) {
            const tableMarketId = table.getAttribute('data-market-id');
            if (tableMarketId && tableMarketId !== data.market_id) {
                continue;
            }
            
            for (const runner of data.runners || []) {
                updateRunnerResult(table, runner);
            }
        }
        
        console.log('Market closed:', data.market_id);
    }

    /**
     * Add Betfair column headers to tables
     */
    function addColumnHeaders() {
        const tables = document.querySelectorAll('table thead tr');
        
        for (const headerRow of tables) {
            // Check if headers already added
            if (headerRow.querySelector('.betfair-live-odds-header')) {
                continue;
            }
            
            // Find the existing Odds header (4th column typically)
            const headers = headerRow.querySelectorAll('th');
            if (headers.length < 4) continue;
            
            // Create Live Odds header
            const liveOddsHeader = document.createElement('th');
            liveOddsHeader.className = 'betfair-live-odds-header';
            liveOddsHeader.style.cssText = 'padding: 12px; text-align: center;';
            liveOddsHeader.textContent = 'Live Odds';
            
            // Create Result header
            const resultHeader = document.createElement('th');
            resultHeader.className = 'betfair-result-header';
            resultHeader.style.cssText = 'padding: 12px; text-align: center;';
            resultHeader.textContent = 'Result';
            
            // Insert after the existing Odds column
            headers[3].insertAdjacentElement('afterend', liveOddsHeader);
            liveOddsHeader.insertAdjacentElement('afterend', resultHeader);
        }
    }

    /**
     * Connect to the SSE stream
     */
    function connect() {
        if (eventSource) {
            eventSource.close();
        }

        console.log('Connecting to Betfair stream...');
        eventSource = new EventSource(BETFAIR_STREAM_URL);

        eventSource.addEventListener('connected', function(e) {
            console.log('Connected to Betfair stream');
            isConnected = true;
            reconnectAttempts = 0;
            showConnectionStatus('connected');
        });

        eventSource.addEventListener('odds_update', function(e) {
            try {
                const data = JSON.parse(e.data);
                handleOddsUpdate(data);
            } catch (err) {
                console.error('Error parsing odds update:', err);
            }
        });

        eventSource.addEventListener('market_closed', function(e) {
            try {
                const data = JSON.parse(e.data);
                handleMarketClosed(data);
            } catch (err) {
                console.error('Error parsing market closed:', err);
            }
        });

        eventSource.onerror = function(e) {
            console.error('SSE connection error:', e);
            isConnected = false;
            showConnectionStatus('disconnected');
            
            eventSource.close();
            
            if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                reconnectAttempts++;
                console.log(`Reconnecting in ${RECONNECT_DELAY}ms (attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`);
                setTimeout(connect, RECONNECT_DELAY);
            } else {
                console.error('Max reconnect attempts reached');
                showConnectionStatus('failed');
            }
        };
    }

    /**
     * Show connection status indicator
     */
    function showConnectionStatus(status) {
        let indicator = document.getElementById('betfair-connection-status');
        
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'betfair-connection-status';
            indicator.style.cssText = `
                position: fixed;
                bottom: 20px;
                right: 20px;
                padding: 8px 16px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: 600;
                z-index: 1000;
                display: flex;
                align-items: center;
                gap: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            `;
            document.body.appendChild(indicator);
        }
        
        switch (status) {
            case 'connected':
                indicator.style.backgroundColor = '#d4edda';
                indicator.style.color = '#155724';
                indicator.innerHTML = '<span style="display: inline-block; width: 8px; height: 8px; background: #28a745; border-radius: 50%;"></span> Live Odds Connected';
                break;
            case 'disconnected':
                indicator.style.backgroundColor = '#fff3cd';
                indicator.style.color = '#856404';
                indicator.innerHTML = '<span style="display: inline-block; width: 8px; height: 8px; background: #ffc107; border-radius: 50%;"></span> Reconnecting...';
                break;
            case 'failed':
                indicator.style.backgroundColor = '#f8d7da';
                indicator.style.color = '#721c24';
                indicator.innerHTML = '<span style="display: inline-block; width: 8px; height: 8px; background: #dc3545; border-radius: 50%;"></span> Connection Failed';
                break;
        }
    }

    /**
     * Initialize the client
     */
    function init() {
        // Only run if there's a results table
        const table = findResultsTable();
        if (!table) {
            console.log('No results table found, Betfair client not initialized');
            return;
        }

        console.log('Initializing Betfair live odds client');
        
        // Add column headers
        addColumnHeaders();
        
        // Connect to stream
        connect();
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Cleanup on page unload
    window.addEventListener('beforeunload', function() {
        if (eventSource) {
            eventSource.close();
        }
    });

})();
