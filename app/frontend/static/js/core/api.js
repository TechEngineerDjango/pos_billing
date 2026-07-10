/**
 * Shared CSRF-aware fetch wrapper, used by every page's Alpine component.
 * Pages render one hidden <input name="csrf_token"> per form; querying the
 * first is intentional — they're all the same per-request value.
 */
const ApiClient = {
    getCsrfToken() {
        const el = document.querySelector('input[name="csrf_token"]');
        if (!el) {
            console.error('ApiClient: csrf_token input not found on page');
            return '';
        }
        return el.value;
    },

    async request(url, options = {}) {
        const isFormData = options.body instanceof FormData;
        const defaultHeaders = {
            'x-csrf-token': this.getCsrfToken(),
            ...(isFormData ? {} : { 'Content-Type': 'application/json' })
        };

        try {
            const response = await fetch(url, {
                ...options,
                headers: { ...defaultHeaders, ...options.headers }
            });
            if (!response.ok) {
                let detail = 'Network error occurred';
                try {
                    const errorData = await response.json();
                    detail = errorData.detail || detail;
                } catch (jsonErr) {}

                if (response.status === 403 && detail.toLowerCase().includes('csrf')) {
                    alert('Security token expired due to inactivity. The page will now refresh securely.');
                    window.location.reload();
                    return new Promise(() => {}); // halt execution while reloading
                }
                if (response.status === 401) {
                    alert('Your session has expired. Please log in again.');
                    window.location.href = '/auth/login';
                    return new Promise(() => {}); // halt execution while redirecting
                }

                throw new Error(detail);
            }
            return await response.json();
        } catch (e) {
            if (e.name === 'AbortError') return null;
            throw e;
        }
    }
};
