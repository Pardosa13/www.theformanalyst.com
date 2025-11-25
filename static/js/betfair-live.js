/**
 * Betfair Live Odds Client
 * 
 * Connects to the Betfair SSE stream and updates the race table
 * with live odds and final results.
 * 
 * This script only loads if BETFAIR_ENABLED is set in the template.
 */

(function() {
    'use strict';

    // Configuration
    const BETFAIR_SERVICE_URL = window.BETFAIR_SERVICE_URL || '';
    const RECONNECT_DELAY = 5000;  // 5 seconds
    const MAX_RECONNECT_ATTEMPTS = 10;

    // State
    let eventSource = null;
    let reconnectAttempts = 0;
    let reconnectTimeout = null;

    /**
     * Normalize horse name for matching
     */
    function normalizeName(name) {
        if (!name) return '';
        return name
            .toLowerCase()
            .replace(/[^a-z0-9]/g, '')  // Remove non-alphanumeric
            .trim();
    }

    /**
     * Find table row by selection ID or horse name
     */
    function findRowBySelection(selectionId, runnerName) {
        // First, try to find by data-selection-id attribute
        const byId = document.querySelector(`tr[data-selection-id="${selectionId}"]`);
        if (byId) return byId;

        // Fallback: try to match by horse name
        if (!runnerName) return null;

        const normalizedRunnerName = normalizeName(runnerName);
        const rows = document.querySelectorAll('table tbody tr');
        
        for (const row of rows) {
            const nameCell = row.querySelector('td:nth-child(2)');
            if (!nameCell) continue;

            const horseName = nameCell.querySelector('div')?.textContent || nameCell.textContent;
            if (normalizeName(horseName) === normalizedRunnerName) {
                // Set the selection ID for future lookups
                row.setAttribute('data-selection-id', selectionId);
                return row;
            }
        }

        return null;
    }

    /**
     * Get or create the Odds cell in a row
     */
    function getOddsCell(row) {
        let cell = row.querySelector('.betfair-odds-cell');
        if (cell) return cell;

        // Find the position to insert (after Win % column, index 4)
        const cells = row.querySelectorAll('td');
        if (cells.length < 5) return null;

        cell = document.createElement('td');
        cell.className = 'betfair-odds-cell';
        cell.style.cssText = 'padding: 12px; text-align: center; border-bottom: 1px solid #e2e8f0; font-weight: 600;';
        
        // Insert after Win % (index 4)
        if (cells[5]) {
            cells[5].parentNode.insertBefore(cell, cells[5]);
        } else {
            row.appendChild(cell);
        }

        return cell;
    }

    /**
     * Get or create the Result cell in a row
     */
    function getResultCell(row) {
        let cell = row.querySelector('.betfair-result-cell');
        if (cell) return cell;

        // Find the position to insert (after Odds cell)
        const oddsCell = getOddsCell(row);
        if (!oddsCell) return null;

        cell = document.createElement('td');
        cell.className = 'betfair-result-cell';
        cell.style.cssText = 'padding: 12px; text-align: center; border-bottom: 1px solid #e2e8f0; font-weight: 600;';
        
        oddsCell.parentNode.insertBefore(cell, oddsCell.nextSibling);

        return cell;
    }

    /**
     * Add Betfair column headers to tables
     */
    function addTableHeaders() {
        const headerRows = document.querySelectorAll('table thead tr');
        
        for (const headerRow of headerRows) {
            // Check if headers already added
            if (headerRow.querySelector('.betfair-odds-header')) continue;

            const headers = headerRow.querySelectorAll('th');
            if (headers.length < 5) continue;

            // Add Odds header after Win % (index 4)
            const oddsHeader = document.createElement('th');
            oddsHeader.className = 'betfair-odds-header';
            oddsHeader.textContent = 'Live Odds';
            oddsHeader.style.cssText = 'padding: 12px; text-align: center;';

            const resultHeader = document.createElement('th');
            resultHeader.className = 'betfair-result-header';
            resultHeader.textContent = 'Result';
            resultHeader.style.cssText = 'padding: 12px; text-align: center;';

            if (headers[5]) {
                headers[5].parentNode.insertBefore(oddsHeader, headers[5]);
                headers[5].parentNode.insertBefore(resultHeader, headers[5]);
            } else {
                headerRow.appendChild(oddsHeader);
                headerRow.appendChild(resultHeader);
            }
        }
    }

    /**
     * Update odds display for a runner
     */
    function updateRunnerOdds(runner) {
        const row = findRowBySelection(runner.selectionId, runner.runnerName);
        if (!row) return;

        const oddsCell = getOddsCell(row);
        if (!oddsCell) return;

        // Format odds display
        let oddsDisplay = '-';
        let oddsColor = '#6c757d';

        if (runner.status === 'REMOVED') {
            oddsDisplay = 'SCR';
            oddsColor = '#dc3545';
        } else if (runner.bestBackPrice) {
            oddsDisplay = runner.bestBackPrice.toFixed(2);
            oddsColor = '#28a745';
        } else if (runner.lastPriceTraded) {
            oddsDisplay = runner.lastPriceTraded.toFixed(2);
            oddsColor = '#17a2b8';
        }

        oddsCell.textContent = oddsDisplay;
        oddsCell.style.color = oddsColor;

        // Add tooltip with more info
        if (runner.bestBackPrice && runner.bestLayPrice) {
            oddsCell.title = `Back: ${runner.bestBackPrice.toFixed(2)} / Lay: ${runner.bestLayPrice.toFixed(2)}`;
        }
    }

    /**
     * Update result display for a runner
     */
    function updateRunnerResult(runner) {
        const row = findRowBySelection(runner.selectionId, runner.runnerName);
        if (!row) return;

        const resultCell = getResultCell(row);
        if (!resultCell) return;

        let resultDisplay = '-';
        let resultColor = '#6c757d';

        if (runner.status === 'REMOVED') {
            resultDisplay = 'SCR';
            resultColor = '#dc3545';
        } else if (runner.finalPosition !== undefined && runner.finalPosition !== null) {
            if (runner.finalPosition === 1) {
                resultDisplay = '🥇 1st';
                resultColor = '#ffc107';
                row.style.backgroundColor = '#d4edda';
            } else if (runner.finalPosition === 2) {
                resultDisplay = '🥈 2nd';
                resultColor = '#6c757d';
                row.style.backgroundColor = '#fff3cd';
            } else if (runner.finalPosition === 3) {
                resultDisplay = '🥉 3rd';
                resultColor = '#cd7f32';
                row.style.backgroundColor = '#ffe4b3';
            } else {
                resultDisplay = runner.finalPosition.toString();
            }
        }

        resultCell.textContent = resultDisplay;
        resultCell.style.color = resultColor;
    }

    /**
     * Handle market update message
     */
    function handleMarketUpdate(data) {
        if (!data.runners) return;

        for (const runner of data.runners) {
            updateRunnerOdds(runner);
        }
    }

    /**
     * Handle market closed message
     */
    function handleMarketClosed(data) {
        if (!data.runners) return;

        for (const runner of data.runners) {
            updateRunnerOdds(runner);
            updateRunnerResult(runner);
        }

        // Show notification
        showNotification(`Race settled! Market ${data.marketId}`);
    }

    /**
     * Show a notification toast
     */
    function showNotification(message) {
        const toast = document.createElement('div');
        toast.className = 'betfair-toast';
        toast.textContent = message;
        toast.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 12px 24px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            z-index: 9999;
            animation: slideIn 0.3s ease;
        `;

        document.body.appendChild(toast);

        setTimeout(() => {
            toast.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    /**
     * Update connection status indicator
     */
    function updateConnectionStatus(connected) {
        let indicator = document.getElementById('betfair-status');
        
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'betfair-status';
            indicator.style.cssText = `
                position: fixed;
                top: 80px;
                right: 20px;
                padding: 8px 16px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: 600;
                z-index: 9999;
                display: flex;
                align-items: center;
                gap: 8px;
            `;
            document.body.appendChild(indicator);
        }

        if (connected) {
            indicator.innerHTML = '<span style="width:8px;height:8px;background:#28a745;border-radius:50%;display:inline-block;"></span> Live Odds Connected';
            indicator.style.background = 'rgba(40, 167, 69, 0.1)';
            indicator.style.color = '#28a745';
            indicator.style.border = '1px solid #28a745';
        } else {
            indicator.innerHTML = '<span style="width:8px;height:8px;background:#dc3545;border-radius:50%;display:inline-block;"></span> Connecting...';
            indicator.style.background = 'rgba(220, 53, 69, 0.1)';
            indicator.style.color = '#dc3545';
            indicator.style.border = '1px solid #dc3545';
        }
    }

    /**
     * Connect to SSE stream
     */
    function connect() {
        if (eventSource) {
            eventSource.close();
        }

        const streamUrl = BETFAIR_SERVICE_URL ? `${BETFAIR_SERVICE_URL}/stream` : '/stream';
        
        console.log('[Betfair] Connecting to', streamUrl);
        updateConnectionStatus(false);

        eventSource = new EventSource(streamUrl);

        eventSource.onopen = function() {
            console.log('[Betfair] Connected');
            reconnectAttempts = 0;
            updateConnectionStatus(true);
        };

        eventSource.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);

                switch (data.type) {
                    case 'connected':
                        console.log('[Betfair] Stream connected');
                        break;
                    case 'heartbeat':
                        // Keep alive
                        break;
                    case 'market_update':
                        handleMarketUpdate(data);
                        break;
                    case 'market_closed':
                        handleMarketClosed(data);
                        break;
                    default:
                        console.log('[Betfair] Unknown message type:', data.type);
                }
            } catch (e) {
                console.error('[Betfair] Error parsing message:', e);
            }
        };

        eventSource.onerror = function(error) {
            console.error('[Betfair] Connection error:', error);
            updateConnectionStatus(false);

            eventSource.close();

            // Reconnect with exponential backoff
            if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                reconnectAttempts++;
                const delay = RECONNECT_DELAY * Math.pow(2, reconnectAttempts - 1);
                console.log(`[Betfair] Reconnecting in ${delay}ms (attempt ${reconnectAttempts})`);
                
                clearTimeout(reconnectTimeout);
                reconnectTimeout = setTimeout(connect, delay);
            } else {
                console.error('[Betfair] Max reconnect attempts reached');
                updateConnectionStatus(false);
            }
        };
    }

    /**
     * Initialize the Betfair live odds client
     */
    function init() {
        console.log('[Betfair] Initializing live odds client');

        // Add CSS for animations
        const style = document.createElement('style');
        style.textContent = `
            @keyframes slideIn {
                from { transform: translateX(100%); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes slideOut {
                from { transform: translateX(0); opacity: 1; }
                to { transform: translateX(100%); opacity: 0; }
            }
            .betfair-odds-cell, .betfair-result-cell {
                transition: all 0.3s ease;
            }
            .betfair-odds-cell.updated {
                animation: pulse 0.5s ease;
            }
            @keyframes pulse {
                0% { background-color: transparent; }
                50% { background-color: rgba(40, 167, 69, 0.2); }
                100% { background-color: transparent; }
            }
        `;
        document.head.appendChild(style);

        // Add table headers
        addTableHeaders();

        // Connect to stream
        connect();

        // Clean up on page unload
        window.addEventListener('beforeunload', function() {
            if (eventSource) {
                eventSource.close();
            }
        });
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
