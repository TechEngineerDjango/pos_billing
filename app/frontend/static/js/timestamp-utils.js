/**
 * Centralized timestamp conversion utilities - Optimized for performance
 * Converts ISO 8601 timestamps to user's local timezone
 */

// Cached locale formatting options to avoid object creation in loops
const LOCALE_OPTIONS = Object.freeze({
    dateOnly: {
        day: '2-digit',
        month: 'short',
        year: 'numeric'
    },
    timestampFull: {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    }
});

/**
 * Convert all timestamps with data-iso or data-timestamp attributes to local timezone
 * Single pass optimized version handling both attribute types
 */
function convertTimestamps() {
    // Single selector for both attribute types - more efficient
    const elements = document.querySelectorAll('[data-iso], [data-timestamp]');

    elements.forEach(el => {
        if (el.dataset.converted) return; // Skip already converted

        const isoTime = el.getAttribute('data-iso') || el.getAttribute('data-timestamp');
        if (!isoTime) return;

        // Single date object creation
        const date = new Date(isoTime);
        if (isNaN(date.getTime())) return;

        // Format based on attribute type
        if (el.hasAttribute('data-iso')) {
            el.textContent = `${date.toLocaleDateString('en-US', LOCALE_OPTIONS.dateOnly)} | ${date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })}`;
        } else {
            el.textContent = date.toLocaleString('en-US', LOCALE_OPTIONS.timestampFull);
        }

        el.dataset.converted = 'true';
    });
}

// Debounce timer to prevent multiple rapid conversions
let convertTimestampsTimeout = null;

/**
 * Debounced conversion for dynamic content
 */
function debouncedConvertTimestamps() {
    clearTimeout(convertTimestampsTimeout);
    convertTimestampsTimeout = setTimeout(convertTimestamps, 50);
}

/**
 * Initialize timestamp conversion on page load
 * Optimized to run only when necessary
 */
function initializeTimestampConversion() {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', convertTimestamps);
    } else {
        convertTimestamps();
    }

    // Single delayed run for async content
    setTimeout(convertTimestamps, 150);
}

// Auto-initialize on script load
if (typeof document !== 'undefined') {
    initializeTimestampConversion();
}
