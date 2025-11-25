/**
 * Betfair Live Odds Client
 * 
 * Connects to the SSE /stream endpoint and updates the race results table
 * with live odds and final positions.
 * 
 * This script is defensive and only runs if:
 * - The results table exists on the page
 * - BETFAIR_ENABLED is true (checked via data attribute)
 */

(function() {
    'use strict';

    // Configuration
    const SSE_ENDPOINT = '/stream';
    const RECONNECT_DELAY = 5000;
    const MAX_RECONNECT_ATTEMPTS = 10;

    let eventSource = null;
    let reconnectAttempts = 0;
    let isConnected = false;

    /**
     * Initialize the Betfair live odds client
     */
    function init() {
        // Check if Betfair integration is enabled
        const container = document.getElementById('betfair-live-container');
        if (!container) {
            console.log('[Betfair] Container not found, skipping initialization');
            return;
        }

        // Check if tables exist on the page
        const tables = document.querySelectorAll('table tbody');
        if (!tables.length) {
            console.log('[Betfair] No tables found, skipping initialization');
            return;
        }

        console.log('[Betfair] Initializing live odds client');
        connect();
    }

    /**
     * Connect to the SSE endpoint
     */
    function connect() {
        if (eventSource) {
            eventSource.close();
        }

        try {
            eventSource = new EventSource(SSE_ENDPOINT);

            eventSource.onopen = function() {
                console.log('[Betfair] Connected to stream');
                isConnected = true;
                reconnectAttempts = 0;
                updateConnectionStatus('connected');
            };

            eventSource.onerror = function(e) {
                console.error('[Betfair] Connection error', e);
                isConnected = false;
                updateConnectionStatus('disconnected');
                handleReconnect();
            };

            eventSource.addEventListener('connected', function(e) {
                console.log('[Betfair] Received connected event');
            });

            eventSource.addEventListener('market_update', function(e) {
                try {
                    const data = JSON.parse(e.data);
                    handleMarketUpdate(data);
                } catch (err) {
                    console.error('[Betfair] Failed to parse market_update', err);
                }
            });

            eventSource.addEventListener('market_closed', function(e) {
                try {
                    const data = JSON.parse(e.data);
                    handleMarketClosed(data);
                } catch (err) {
                    console.error('[Betfair] Failed to parse market_closed', err);
                }
            });

            eventSource.addEventListener('heartbeat', function(e) {
                // Silent heartbeat handling
            });

        } catch (err) {
            console.error('[Betfair] Failed to create EventSource', err);
            handleReconnect();
        }
    }

    /**
     * Handle reconnection with exponential backoff
     */
    function handleReconnect() {
        if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
            console.error('[Betfair] Max reconnect attempts reached');
            updateConnectionStatus('error');
            return;
        }

        reconnectAttempts++;
        const delay = RECONNECT_DELAY * Math.pow(1.5, reconnectAttempts - 1);
        console.log(`[Betfair] Reconnecting in ${delay}ms (attempt ${reconnectAttempts})`);

        setTimeout(connect, delay);
    }

    /**
     * Update connection status indicator
     */
    function updateConnectionStatus(status) {
        const indicator = document.getElementById('betfair-connection-status');
        if (!indicator) return;

        indicator.className = 'betfair-status betfair-status-' + status;
        indicator.title = 'Betfair: ' + status;

        const text = indicator.querySelector('.betfair-status-text');
        if (text) {
            switch (status) {
                case 'connected':
                    text.textContent = 'Live';
                    break;
                case 'disconnected':
                    text.textContent = 'Reconnecting...';
                    break;
                case 'error':
                    text.textContent = 'Offline';
                    break;
            }
        }
    }

    /**
     * Handle market update event
     */
    function handleMarketUpdate(data) {
        const marketId = data.marketId;
        const runners = data.runners || [];

        runners.forEach(function(runner) {
            updateRunner(runner);
        });
    }

    /**
     * Handle market closed event (final results)
     */
    function handleMarketClosed(data) {
        const marketId = data.marketId;
        const runners = data.runners || [];

        console.log('[Betfair] Market closed:', marketId);

        // Sort runners by status to determine positions
        const winners = runners.filter(r => r.status === 'WINNER');
        const placed = runners.filter(r => r.status === 'PLACED');
        const losers = runners.filter(r => r.status === 'LOSER');

        // Update winner(s)
        winners.forEach(function(runner) {
            updateRunnerResult(runner, 1);
        });

        // Update placed runners (typically 2nd, 3rd)
        placed.forEach(function(runner, index) {
            updateRunnerResult(runner, 2 + index);
        });

        // Mark losers
        losers.forEach(function(runner) {
            updateRunnerResult(runner, null);
        });
    }

    /**
     * Find table row for a runner
     */
    function findRunnerRow(selectionId, horseName) {
        // First try to find by data-selection-id
        if (selectionId) {
            const row = document.querySelector(`tr[data-selection-id="${selectionId}"]`);
            if (row) return row;
        }

        // Fallback: find by horse name
        if (horseName) {
            const normalizedName = normalizeHorseName(horseName);
            const rows = document.querySelectorAll('table tbody tr');
            
            for (let row of rows) {
                const nameCell = row.querySelector('td:nth-child(2)');
                if (nameCell) {
                    const rowName = nameCell.textContent.trim();
                    const rowHorseName = rowName.split('\n')[0].trim();
                    
                    if (normalizeHorseName(rowHorseName) === normalizedName) {
                        return row;
                    }
                }
            }
        }

        return null;
    }

    /**
     * Normalize horse name for comparison
     */
    function normalizeHorseName(name) {
        if (!name) return '';
        return name.toLowerCase()
            .replace(/[^a-z0-9]/g, '')
            .trim();
    }

    /**
     * Update runner with live odds
     */
    function updateRunner(runner) {
        const row = findRunnerRow(runner.selectionId, runner.runnerName);
        if (!row) return;

        // Find or create odds cell
        let oddsCell = row.querySelector('.betfair-odds');
        if (!oddsCell) {
            oddsCell = findOddsCell(row);
        }

        if (oddsCell) {
            const backPrice = runner.backPrice;
            const layPrice = runner.layPrice;

            if (backPrice) {
                oddsCell.innerHTML = formatOdds(backPrice);
                oddsCell.classList.add('betfair-odds-updated');
                
                // Remove highlight after animation
                setTimeout(function() {
                    oddsCell.classList.remove('betfair-odds-updated');
                }, 1000);
            }
        }
    }

    /**
     * Update runner with final result
     */
    function updateRunnerResult(runner, position) {
        const row = findRunnerRow(runner.selectionId, runner.runnerName);
        if (!row) return;

        // Find or create result cell
        let resultCell = row.querySelector('.betfair-result');
        if (!resultCell) {
            resultCell = findResultCell(row);
        }

        if (resultCell) {
            if (position === 1) {
                resultCell.innerHTML = '<span class="betfair-winner">🏆 1st</span>';
                row.classList.add('betfair-winner-row');
            } else if (position === 2) {
                resultCell.innerHTML = '<span class="betfair-placed">🥈 2nd</span>';
                row.classList.add('betfair-placed-row');
            } else if (position === 3) {
                resultCell.innerHTML = '<span class="betfair-placed">🥉 3rd</span>';
                row.classList.add('betfair-placed-row');
            } else if (position) {
                resultCell.innerHTML = '<span class="betfair-placed">' + position + 'th</span>';
            } else {
                resultCell.innerHTML = '<span class="betfair-loser">-</span>';
            }
        }

        // Update final odds
        if (runner.lastPriceTraded) {
            let oddsCell = row.querySelector('.betfair-odds');
            if (!oddsCell) {
                oddsCell = findOddsCell(row);
            }
            if (oddsCell) {
                oddsCell.innerHTML = formatOdds(runner.lastPriceTraded);
                oddsCell.classList.add('betfair-final');
            }
        }
    }

    /**
     * Find the odds cell in a row (for updating)
     */
    function findOddsCell(row) {
        // Look for cell with betfair-odds class
        let cell = row.querySelector('.betfair-odds');
        if (cell) return cell;

        // Look for header to find column index
        const table = row.closest('table');
        if (!table) return null;

        const headers = table.querySelectorAll('thead th');
        for (let i = 0; i < headers.length; i++) {
            const headerText = headers[i].textContent.toLowerCase();
            if (headerText.includes('live odds') || headerText.includes('betfair')) {
                const cells = row.querySelectorAll('td');
                if (cells[i]) {
                    cells[i].classList.add('betfair-odds');
                    return cells[i];
                }
            }
        }

        return null;
    }

    /**
     * Find the result cell in a row (for updating)
     */
    function findResultCell(row) {
        // Look for cell with betfair-result class
        let cell = row.querySelector('.betfair-result');
        if (cell) return cell;

        // Look for header to find column index
        const table = row.closest('table');
        if (!table) return null;

        const headers = table.querySelectorAll('thead th');
        for (let i = 0; i < headers.length; i++) {
            const headerText = headers[i].textContent.toLowerCase();
            if (headerText.includes('result') || headerText.includes('position')) {
                const cells = row.querySelectorAll('td');
                if (cells[i]) {
                    cells[i].classList.add('betfair-result');
                    return cells[i];
                }
            }
        }

        return null;
    }

    /**
     * Format odds value for display
     */
    function formatOdds(odds) {
        if (!odds || odds <= 0) return '-';
        return '$' + parseFloat(odds).toFixed(2);
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose for debugging
    window.BetfairLive = {
        connect: connect,
        isConnected: function() { return isConnected; }
    };

})();
