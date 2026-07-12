/**
 * Environment-Aware Client Configuration
 *
 * Dynamically calculates the backend API URL based on the current hostname.
 * For local development (localhost/127.0.0.1), uses localhost:5000.
 * For production, uses the Render web service URL.
 */

(function() {
    'use strict';

    /**
     * Get the API base URL based on the current hostname.
     *
     * @returns {string} The API base URL
     */
    function getApiBaseUrl() {
        const hostname = window.location.hostname;

        // Local development: localhost or 127.0.0.1
        if (hostname === 'localhost' || hostname === '127.0.0.1') {
            return 'http://localhost:5000';
        }

        // Production: use the current host (assumes Render web service)
        return window.location.origin;
    }

    // Export configuration
    window.APP_CONFIG = {
        API_BASE_URL: getApiBaseUrl(),
        API_VERSION: 'v1',

        /**
         * Get full API endpoint path
         * @param {string} endpoint - API endpoint (e.g., 'search')
         * @returns {string} Full URL to the API endpoint
         */
        getApiEndpoint: function(endpoint) {
            return `${this.API_BASE_URL}/api/${this.API_VERSION}/${endpoint}`;
        }
    };
})();