/**
 * Betfair Live Odds Client
 * ========================
 * 
 * Client-side JavaScript that connects to the Betfair SSE service
 * and updates the results table with live odds and race results.
 * 
 * This script is defensive - it only acts if:
 * 1. A results table exists on the page
 * 2. Horse rows have data-runner-id or data-selection-id attributes
 * 
 * Usage:
 *   Include this script on pages with results tables.
 *   Configure the SSE endpoint via window.BETFAIR_SSE_URL or
 *   data-betfair-sse-url attribute on the script tag.
 */

(function() {
    'use strict';

    // Configuration
    const DEFAULT_SSE_URL = 'http://127.0.0.1:5001/stream';
    
    // Get SSE URL from config or default
    function getSSEUrl() {
        // Check for global config
        if (window.BETFAIR_SSE_URL) {
            return window.BETFAIR_SSE_URL;
        }
        
        // Check for data attribute on script tag
        const scriptTag = document.currentScript || 
            document.querySelector('script[data-betfair-sse-url]');
        if (scriptTag && scriptTag.dataset.betfairSseUrl) {
            return scriptTag.dataset.betfairSseUrl;
        }
        
        return DEFAULT_SSE_URL;
    }

    // Check if Betfair integration should be active
    function shouldActivate() {
        // Only activate if there's a results table
        const table = document.querySelector('table');
        return table !== null;
    }

    // Find horse row by selection ID
    function findHorseRow(selectionId) {
        // Try data-runner-id first
        let row = document.querySelector(`tr[data-runner-id="${selectionId}"]`);
        if (row) return row;
        
        // Try data-selection-id
        row = document.querySelector(`tr[data-selection-id="${selectionId}"]`);
        if (row) return row;
        
        return null;
    }

    // Update odds cell in a row
    function updateOddsCell(row, odds, columnIndex) {
        if (!row) return;
        
        const cells = row.querySelectorAll('td');
        if (columnIndex < cells.length) {
            const cell = cells[columnIndex];
            if (cell && cell.classList.contains('betfair-odds')) {
                cell.textContent = odds ? odds.toFixed(2) : '-';
                cell.classList.add('odds-updated');
                
                // Remove animation class after animation completes
                setTimeout(() => {
                    cell.classList.remove('odds-updated');
                }, 500);
            }
        }
    }

    // Update result cell in a row
    function updateResultCell(row, result, columnIndex) {
        if (!row) return;
        
        const cells = row.querySelectorAll('td');
        if (columnIndex < cells.length) {
            const cell = cells[columnIndex];
            if (cell && cell.classList.contains('betfair-result')) {
                cell.textContent = result || '';
                
                // Add styling based on result
                cell.classList.remove('result-won', 'result-placed', 'result-lost');
                if (result === 'WON') {
                    cell.classList.add('result-won');
                } else if (result === 'PLACED') {
                    cell.classList.add('result-placed');
                } else if (result === 'LOST') {
                    cell.classList.add('result-lost');
                }
            }
        }
    }

    // Add Betfair columns to table headers
    function ensureBetfairColumns() {
        const tables = document.querySelectorAll('table');
        
        tables.forEach(table => {
            const headerRow = table.querySelector('thead tr');
            if (!headerRow) return;
            
            // Check if columns already exist
            if (headerRow.querySelector('.betfair-odds-header')) return;
            
            // Add Live Odds header
            const oddsHeader = document.createElement('th');
            oddsHeader.className = 'betfair-odds-header';
            oddsHeader.style.cssText = 'padding: 12px; text-align: center;';
            oddsHeader.textContent = 'Live Odds';
            
            // Add Result header
            const resultHeader = document.createElement('th');
            resultHeader.className = 'betfair-result-header';
            resultHeader.style.cssText = 'padding: 12px; text-align: center;';
            resultHeader.textContent = 'Result';
            
            headerRow.appendChild(oddsHeader);
            headerRow.appendChild(resultHeader);
            
            // Add cells to each body row
            const bodyRows = table.querySelectorAll('tbody tr');
            bodyRows.forEach(row => {
                // Add odds cell
                const oddsCell = document.createElement('td');
                oddsCell.className = 'betfair-odds';
                oddsCell.style.cssText = 'padding: 12px; text-align: center; font-weight: 600; color: #28a745; border-bottom: 1px solid #e2e8f0;';
                oddsCell.textContent = '-';
                
                // Add result cell
                const resultCell = document.createElement('td');
                resultCell.className = 'betfair-result';
                resultCell.style.cssText = 'padding: 12px; text-align: center; font-weight: 600; border-bottom: 1px solid #e2e8f0;';
                resultCell.textContent = '';
                
                row.appendChild(oddsCell);
                row.appendChild(resultCell);
            });
        });
    }

    // Process SSE data and update UI
    function processData(data) {
        if (!data || typeof data !== 'object') return;
        
        Object.keys(data).forEach(marketId => {
            const market = data[marketId];
            if (!market.runners) return;
            
            market.runners.forEach(runner => {
                const row = findHorseRow(runner.selectionId);
                if (!row) return;
                
                // Find column indices for Betfair cells
                const cells = row.querySelectorAll('td');
                let oddsIndex = -1;
                let resultIndex = -1;
                
                cells.forEach((cell, index) => {
                    if (cell.classList.contains('betfair-odds')) {
                        oddsIndex = index;
                    }
                    if (cell.classList.contains('betfair-result')) {
                        resultIndex = index;
                    }
                });
                
                // Update odds
                if (oddsIndex >= 0 && runner.backPrice) {
                    updateOddsCell(row, runner.backPrice, oddsIndex);
                }
                
                // Update result
                if (resultIndex >= 0 && runner.result) {
                    updateResultCell(row, runner.result, resultIndex);
                }
            });
        });
    }

    // Connect to SSE endpoint
    function connectSSE() {
        const url = getSSEUrl();
        
        console.log('[Betfair] Connecting to SSE:', url);
        
        const eventSource = new EventSource(url);
        
        eventSource.onopen = function() {
            console.log('[Betfair] SSE connection established');
        };
        
        eventSource.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                processData(data);
            } catch (e) {
                console.error('[Betfair] Error parsing SSE data:', e);
            }
        };
        
        eventSource.onerror = function(error) {
            console.error('[Betfair] SSE connection error:', error);
            
            // Reconnect after delay
            eventSource.close();
            setTimeout(connectSSE, 5000);
        };
        
        return eventSource;
    }

    // Add CSS for animations and styling
    function addStyles() {
        const style = document.createElement('style');
        style.textContent = `
            .betfair-odds.odds-updated {
                animation: odds-flash 0.5s ease-in-out;
            }
            
            @keyframes odds-flash {
                0% { background-color: transparent; }
                50% { background-color: rgba(40, 167, 69, 0.3); }
                100% { background-color: transparent; }
            }
            
            .betfair-result.result-won {
                color: #28a745 !important;
                background-color: rgba(40, 167, 69, 0.1);
            }
            
            .betfair-result.result-placed {
                color: #ffc107 !important;
                background-color: rgba(255, 193, 7, 0.1);
            }
            
            .betfair-result.result-lost {
                color: #6c757d !important;
            }
            
            .betfair-odds-header,
            .betfair-result-header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
            }
        `;
        document.head.appendChild(style);
    }

    // Initialize
    function init() {
        if (!shouldActivate()) {
            console.log('[Betfair] No results table found, not activating');
            return;
        }
        
        console.log('[Betfair] Initializing live odds integration');
        
        // Add styles
        addStyles();
        
        // Ensure columns exist
        ensureBetfairColumns();
        
        // Connect to SSE
        connectSSE();
    }

    // Run on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
