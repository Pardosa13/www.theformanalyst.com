/**
 * Betfair Live Odds Client
 * 
 * This script connects to the Betfair SSE service and updates the meeting results
 * table with live odds and race results.
 * 
 * The script is defensive - it only runs if a suitable table exists on the page
 * and matches runners using data attributes or horse names.
 */

(function() {
    'use strict';

    // Configuration
    const BETFAIR_SERVICE_URL = window.BETFAIR_SERVICE_URL || '/betfair';
    const RECONNECT_DELAY = 5000; // 5 seconds
    const MAX_RECONNECT_ATTEMPTS = 10;

    // State
    let eventSource = null;
    let reconnectAttempts = 0;
    let isConnected = false;

    /**
     * Initialize the Betfair live odds client
     */
    function init() {
        // Check if we should run
        if (!shouldRun()) {
            console.log('[Betfair] No suitable tables found, not initializing');
            return;
        }

        console.log('[Betfair] Initializing live odds client');
        
        // Add Odds and Result columns to tables
        addColumnsToTables();
        
        // Connect to SSE stream
        connect();
    }

    /**
     * Check if the script should run on this page
     */
    function shouldRun() {
        // Look for race result tables
        const tables = document.querySelectorAll('table');
        return tables.length > 0;
    }

    /**
     * Add Odds and Result columns to all race tables
     */
    function addColumnsToTables() {
        const tables = document.querySelectorAll('table');
        
        tables.forEach(table => {
            const headerRow = table.querySelector('thead tr');
            if (!headerRow) return;

            // Check if columns already exist
            const existingHeaders = Array.from(headerRow.querySelectorAll('th')).map(th => th.textContent.trim().toLowerCase());
            
            // Add Live Odds column if not present
            if (!existingHeaders.includes('live odds')) {
                const liveOddsHeader = document.createElement('th');
                liveOddsHeader.style.cssText = 'padding: 12px; text-align: center;';
                liveOddsHeader.textContent = 'Live Odds';
                liveOddsHeader.setAttribute('data-betfair-column', 'live-odds');
                headerRow.appendChild(liveOddsHeader);
            }

            // Add Result column if not present
            if (!existingHeaders.includes('result')) {
                const resultHeader = document.createElement('th');
                resultHeader.style.cssText = 'padding: 12px; text-align: center;';
                resultHeader.textContent = 'Result';
                resultHeader.setAttribute('data-betfair-column', 'result');
                headerRow.appendChild(resultHeader);
            }

            // Add cells to each row
            const bodyRows = table.querySelectorAll('tbody tr');
            bodyRows.forEach(row => {
                // Add Live Odds cell
                if (!row.querySelector('[data-betfair-cell="live-odds"]')) {
                    const liveOddsCell = document.createElement('td');
                    liveOddsCell.style.cssText = 'padding: 12px; text-align: center; font-weight: 600; border-bottom: 1px solid #e2e8f0;';
                    liveOddsCell.setAttribute('data-betfair-cell', 'live-odds');
                    liveOddsCell.innerHTML = '<span class="betfair-odds">-</span>';
                    row.appendChild(liveOddsCell);
                }

                // Add Result cell
                if (!row.querySelector('[data-betfair-cell="result"]')) {
                    const resultCell = document.createElement('td');
                    resultCell.style.cssText = 'padding: 12px; text-align: center; font-weight: 600; border-bottom: 1px solid #e2e8f0;';
                    resultCell.setAttribute('data-betfair-cell', 'result');
                    resultCell.innerHTML = '<span class="betfair-result">-</span>';
                    row.appendChild(resultCell);
                }
            });
        });
    }

    /**
     * Connect to the Betfair SSE stream
     */
    function connect() {
        if (eventSource) {
            eventSource.close();
        }

        const streamUrl = BETFAIR_SERVICE_URL + '/stream';
        console.log('[Betfair] Connecting to SSE stream:', streamUrl);

        try {
            eventSource = new EventSource(streamUrl);

            eventSource.onopen = function() {
                console.log('[Betfair] SSE connection opened');
                isConnected = true;
                reconnectAttempts = 0;
                updateConnectionStatus('connected');
            };

            eventSource.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    handleMessage(data);
                } catch (e) {
                    console.error('[Betfair] Error parsing message:', e);
                }
            };

            eventSource.onerror = function(error) {
                console.error('[Betfair] SSE connection error:', error);
                isConnected = false;
                updateConnectionStatus('disconnected');
                
                eventSource.close();
                
                // Attempt reconnection
                if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                    reconnectAttempts++;
                    console.log(`[Betfair] Reconnecting in ${RECONNECT_DELAY}ms (attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`);
                    setTimeout(connect, RECONNECT_DELAY);
                } else {
                    console.error('[Betfair] Max reconnection attempts reached');
                    updateConnectionStatus('failed');
                }
            };
        } catch (e) {
            console.error('[Betfair] Error creating EventSource:', e);
        }
    }

    /**
     * Handle incoming SSE messages
     */
    function handleMessage(data) {
        console.log('[Betfair] Received message:', data.type);

        switch (data.type) {
            case 'connected':
                console.log('[Betfair] Connected to market IDs:', data.market_ids);
                break;

            case 'market_update':
                handleMarketUpdate(data);
                break;

            case 'result':
                handleResult(data);
                break;

            case 'error':
                console.error('[Betfair] Error from server:', data.message);
                break;

            default:
                console.log('[Betfair] Unknown message type:', data.type);
        }
    }

    /**
     * Handle market update messages - update live odds
     */
    function handleMarketUpdate(data) {
        if (!data.runners) return;

        data.runners.forEach(runner => {
            const row = findRunnerRow(runner.selection_id);
            if (!row) return;

            // Update live odds
            const oddsCell = row.querySelector('[data-betfair-cell="live-odds"] .betfair-odds');
            if (oddsCell) {
                let oddsValue = null;
                
                // Prefer best back price, fall back to last traded price
                if (runner.best_back && runner.best_back.price) {
                    oddsValue = runner.best_back.price;
                } else if (runner.last_price_traded) {
                    oddsValue = runner.last_price_traded;
                }

                // Only update if we have valid odds
                if (oddsValue !== null && oddsValue > 0) {
                    const previousOdds = parseFloat(oddsCell.getAttribute('data-previous-odds') || '0');
                    const currentOdds = parseFloat(oddsValue);
                    
                    // Add visual indicator for odds movement
                    if (previousOdds > 0 && currentOdds > 0 && !isNaN(previousOdds) && !isNaN(currentOdds)) {
                        if (currentOdds < previousOdds) {
                            // Odds shortened (better for backers)
                            oddsCell.style.color = '#27ae60'; // Green
                        } else if (currentOdds > previousOdds) {
                            // Odds drifted
                            oddsCell.style.color = '#e74c3c'; // Red
                        }
                        
                        // Reset color after a short delay
                        setTimeout(() => {
                            oddsCell.style.color = '#333';
                        }, 2000);
                    }

                    oddsCell.textContent = formatOdds(currentOdds);
                    oddsCell.setAttribute('data-previous-odds', String(currentOdds));
                }
            }
        });
    }

    /**
     * Handle race result messages
     */
    function handleResult(data) {
        if (!data.result || !data.result.runners) return;

        console.log('[Betfair] Processing race result');

        data.result.runners.forEach(runner => {
            const row = findRunnerRow(runner.selection_id);
            if (!row) return;

            // Update result
            const resultCell = row.querySelector('[data-betfair-cell="result"] .betfair-result');
            if (resultCell) {
                const position = runner.final_position;
                
                if (position === 1) {
                    resultCell.innerHTML = '<span style="color: #27ae60; font-weight: bold;">🏆 Winner</span>';
                    row.classList.add('betfair-winner');
                } else if (position === 2) {
                    resultCell.innerHTML = '<span style="color: #3498db; font-weight: bold;">🥈 2nd</span>';
                    row.classList.add('betfair-placed');
                } else if (position === 3) {
                    resultCell.innerHTML = '<span style="color: #e67e22; font-weight: bold;">🥉 3rd</span>';
                    row.classList.add('betfair-placed');
                } else if (runner.status === 'LOSER') {
                    resultCell.innerHTML = '<span style="color: #95a5a6;">-</span>';
                }
            }

            // Update final odds if available
            if (runner.final_odds) {
                const oddsCell = row.querySelector('[data-betfair-cell="live-odds"] .betfair-odds');
                if (oddsCell) {
                    oddsCell.textContent = formatOdds(runner.final_odds);
                    oddsCell.style.color = '#333';
                }
            }
        });
    }

    /**
     * Find a table row for a runner by selection ID or horse name
     */
    function findRunnerRow(selectionId) {
        // Try to find by data-selection-id
        let row = document.querySelector(`tr[data-selection-id="${selectionId}"]`);
        if (row) return row;

        // Try to find by data-runner-id
        row = document.querySelector(`tr[data-runner-id="${selectionId}"]`);
        if (row) return row;

        // Try to find by data-betfair-selection-id
        row = document.querySelector(`tr[data-betfair-selection-id="${selectionId}"]`);
        if (row) return row;

        return null;
    }

    /**
     * Find a table row by horse name (fallback matching)
     * Uses exact name matching to avoid false positives
     */
    function findRunnerRowByName(horseName) {
        if (!horseName) return null;

        const normalizedName = horseName.toLowerCase().trim();
        const rows = document.querySelectorAll('tbody tr');

        for (const row of rows) {
            // Look for horse name in the row
            const nameCell = row.querySelector('td:nth-child(2)');
            if (nameCell) {
                // Get the first line of text (horse name without jockey/trainer info)
                const cellText = nameCell.textContent || '';
                const firstLine = cellText.split('\n')[0].toLowerCase().trim();
                
                // Use exact match or check if the cell's first line exactly matches
                if (firstLine === normalizedName) {
                    return row;
                }
            }
        }

        return null;
    }

    /**
     * Format odds for display
     */
    function formatOdds(odds) {
        if (!odds || odds === 0) return '-';
        return parseFloat(odds).toFixed(2);
    }

    /**
     * Update connection status indicator
     */
    function updateConnectionStatus(status) {
        let statusIndicator = document.getElementById('betfair-connection-status');
        
        if (!statusIndicator) {
            // Create status indicator
            statusIndicator = document.createElement('div');
            statusIndicator.id = 'betfair-connection-status';
            statusIndicator.style.cssText = `
                position: fixed;
                bottom: 20px;
                right: 20px;
                padding: 10px 15px;
                border-radius: 8px;
                font-size: 12px;
                font-weight: 600;
                z-index: 9999;
                box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            `;
            document.body.appendChild(statusIndicator);
        }

        switch (status) {
            case 'connected':
                statusIndicator.textContent = '🟢 Live Odds Connected';
                statusIndicator.style.backgroundColor = '#d4edda';
                statusIndicator.style.color = '#155724';
                // Hide after 5 seconds
                setTimeout(() => {
                    statusIndicator.style.opacity = '0';
                    statusIndicator.style.transition = 'opacity 0.5s';
                }, 5000);
                break;

            case 'disconnected':
                statusIndicator.textContent = '🔴 Reconnecting...';
                statusIndicator.style.backgroundColor = '#fff3cd';
                statusIndicator.style.color = '#856404';
                statusIndicator.style.opacity = '1';
                break;

            case 'failed':
                statusIndicator.textContent = '❌ Connection Failed';
                statusIndicator.style.backgroundColor = '#f8d7da';
                statusIndicator.style.color = '#721c24';
                statusIndicator.style.opacity = '1';
                break;
        }
    }

    // CSS styles for Betfair integration
    const styles = `
        .betfair-winner {
            background: linear-gradient(90deg, rgba(39, 174, 96, 0.2), rgba(39, 174, 96, 0.1)) !important;
            border-left: 4px solid #27ae60 !important;
        }
        
        .betfair-placed {
            background: linear-gradient(90deg, rgba(52, 152, 219, 0.15), rgba(52, 152, 219, 0.05)) !important;
            border-left: 4px solid #3498db !important;
        }
        
        .betfair-odds {
            font-family: 'Monaco', 'Consolas', monospace;
            transition: color 0.3s ease;
        }
        
        [data-betfair-column] {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
            color: white !important;
        }
    `;

    // Inject styles
    const styleSheet = document.createElement('style');
    styleSheet.textContent = styles;
    document.head.appendChild(styleSheet);

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose API for external use
    window.BetfairLive = {
        connect: connect,
        isConnected: function() { return isConnected; },
        findRunnerRow: findRunnerRow,
        findRunnerRowByName: findRunnerRowByName
    };

})();
