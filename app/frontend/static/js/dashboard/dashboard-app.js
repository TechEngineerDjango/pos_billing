/**
 * Shared offset/limit pagination fetch — used by every search-driven list in
 * this dashboard (Menu, Inventory, Customers, Credit Book bill history) so
 * the fetch/total bookkeeping isn't copy-pasted per tab. Each page replaces
 * the list (numbered Prev/Next pages, not infinite-scroll "Load More").
 */
async function fetchPaginated(url, params, offset, resultKey) {
    const p = new URLSearchParams(params);
    p.set('offset', String(offset));
    const data = await ApiClient.request(`${url}?${p.toString()}`);
    return { items: data[resultKey], total: data.total };
}

// Shared "lazy-load a tab's data exactly once" guard, used by every tab's
// load() method. Centralizes a bug that existed identically in 8 copies:
// marking loadedKey true BEFORE the fetch resolved meant a failed request
// (realistic on a slow connection) permanently stranded that tab empty for
// the rest of the page session — no visible error, no retry, since the
// guard blocked every later load() call regardless of whether the first one
// actually succeeded. loadedKey is only set true after fetchFn() resolves
// WITHOUT throwing — which requires fetchFn to actually reject on failure,
// not just swallow the error internally (every searchXxx()/fetchXxx() method
// here does `throw e` at the end of its own catch for exactly this reason —
// they still set their own `error` field first, so pagination/search calls
// that call the same method directly, not through here, keep working
// unchanged; guardedLoad is the only caller that needs the rejection).
// loadingKey blocks a second overlapping fetch if the tab is switched away
// and back while the first request is still in flight. Field names are
// configurable because the two dashboardApp()-internal callers
// (Menu/Customers tabs) use menuLoading/customersLoading — names already
// read directly by the template — rather than the generic `loading` every
// other component uses.
async function guardedLoad(state, fetchFn, { loadedKey = '_loaded', loadingKey = 'loading' } = {}) {
    if (state[loadedKey] || state[loadingKey]) return;
    try {
        await fetchFn();
        state[loadedKey] = true;
    } catch (e) {
        // Already logged/recorded by fetchFn's own catch — nothing more to
        // do here; the guard staying false is what makes this retryable.
    }
}

function dashboardApp(currencySymbol) {
    const params = new URLSearchParams(window.location.search);
    if (params.get('error')) {
        alert(params.get('error'));
        // Remove error from URL without reload
        const url = new URL(window.location);
        url.searchParams.delete('error');
        window.history.replaceState({}, '', url);
    }

    return {
        currencySymbol,
        activeTab: params.get('tab') || 'overview',
        mobileMenuOpen: false,
        showAddModal: false,
        showStaffModal: false,
        showCustomerModal: false,

        // Menu tab — search-driven, capped + paginated result set (same
        // scalability treatment as Credit Book/Rate Cards pickers), not an
        // unbounded full-catalog embed.
        menuItems: [],
        menuSearch: '',
        _menuSearchTimer: null,
        menuLoading: false,
        menuOffset: 0,
        menuLimit: 10,
        menuTotal: 0,
        menuError: '',
        _menuLoaded: false,

        // Customers tab — same treatment, reuses the existing
        // /admin/customers/search endpoint built for the Credit Book/Rate
        // Cards pickers.
        customerRows: [],
        customerSearch: '',
        _customerSearchTimer: null,
        customersLoading: false,
        customerOffset: 0,
        customerLimit: 10,
        customerTotal: 0,
        customersError: '',
        _customersLoaded: false,
        // Browser-local calendar date, string-comparable against the
        // ISO next_due_date the server sends — only used for the
        // Customers-tab overdue-highlight styling, not authoritative
        // (the Credit Book tab's days_overdue is the real overdue source).
        todayIso: new Date().toISOString().slice(0, 10),

        formatCurrency(v) {
            return `${this.currencySymbol}${Number(v || 0).toFixed(2)}`;
        },

        onMenuSearchInput() {
            clearTimeout(this._menuSearchTimer);
            this._menuSearchTimer = setTimeout(() => { this.menuOffset = 0; this.searchMenuItems(); }, 300);
        },

        async searchMenuItems() {
            this.menuLoading = true;
            this.menuError = '';
            try {
                const result = await fetchPaginated(
                    '/admin/menu/search', { q: this.menuSearch, active_only: 'false', limit: this.menuLimit },
                    this.menuOffset, 'items',
                );
                this.menuItems = result.items;
                this.menuTotal = result.total;
            } catch (e) {
                this.menuError = e.message || 'Failed to load menu items';
                throw e;
            } finally {
                this.menuLoading = false;
            }
        },

        get menuPage() {
            return Math.floor(this.menuOffset / this.menuLimit) + 1;
        },
        get menuTotalPages() {
            return Math.max(1, Math.ceil(this.menuTotal / this.menuLimit));
        },
        goToMenuPage(page) {
            const clamped = Math.max(1, Math.min(page, this.menuTotalPages));
            this.menuOffset = (clamped - 1) * this.menuLimit;
            this.searchMenuItems();
        },
        changeMenuLimit() {
            this.menuOffset = 0;  // page size changed — restart from page 1
            this.searchMenuItems();
        },

        onCustomerSearchInput() {
            clearTimeout(this._customerSearchTimer);
            this._customerSearchTimer = setTimeout(() => { this.customerOffset = 0; this.searchCustomersTab(); }, 300);
        },

        async searchCustomersTab() {
            this.customersLoading = true;
            this.customersError = '';
            try {
                const result = await fetchPaginated(
                    '/admin/customers/search', { q: this.customerSearch, limit: this.customerLimit, include_due_date: true },
                    this.customerOffset, 'customers',
                );
                this.customerRows = result.items;
                this.customerTotal = result.total;
            } catch (e) {
                this.customersError = e.message || 'Failed to load customers';
                throw e;
            } finally {
                this.customersLoading = false;
            }
        },

        get customerPage() {
            return Math.floor(this.customerOffset / this.customerLimit) + 1;
        },
        get customerTotalPages() {
            return Math.max(1, Math.ceil(this.customerTotal / this.customerLimit));
        },
        goToCustomerPage(page) {
            const clamped = Math.max(1, Math.min(page, this.customerTotalPages));
            this.customerOffset = (clamped - 1) * this.customerLimit;
            this.searchCustomersTab();
        },
        changeCustomerLimit() {
            this.customerOffset = 0;  // page size changed — restart from page 1
            this.searchCustomersTab();
        },

        switchTab(t) {
            this.activeTab = t;
            this.mobileMenuOpen = false;
            const url = new URL(window.location);
            url.searchParams.set('tab', t);
            window.history.replaceState({}, '', url);
            this.loadTabData(t);
        },

        // Each tab's data fetch fires the first time it becomes active — not
        // on page load — so opening the Dashboard doesn't kick off a fetch
        // for every tab regardless of which one you're actually looking at.
        // See guardedLoad()'s own comment for why a failed first load must
        // stay retryable rather than getting stuck on an empty tab forever.
        loadTabData(t) {
            if (t === 'menu') {
                guardedLoad(this, () => this.searchMenuItems(), { loadedKey: '_menuLoaded', loadingKey: 'menuLoading' });
            } else if (t === 'customers') {
                guardedLoad(this, () => this.searchCustomersTab(), { loadedKey: '_customersLoaded', loadingKey: 'customersLoading' });
            } else if (t === 'reports') {
                this.$nextTick(() => {
                    loadChartJs().then(initCharts).catch(() => console.error('Failed to load Chart.js'));
                });
            }
        },

        init() {
            this.loadTabData(this.activeTab);
        }
    }
}

let revenueChart = null;
let hourlyChart = null;

// Chart.js is only needed by the Reports tab — loaded on demand the first
// time that tab is opened instead of blocking every Dashboard page load.
let chartJsPromise = null;
function loadChartJs() {
    if (window.Chart) return Promise.resolve();
    if (!chartJsPromise) {
        chartJsPromise = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js';
            script.onload = resolve;
            script.onerror = () => { chartJsPromise = null; reject(new Error('Failed to load Chart.js')); };
            document.head.appendChild(script);
        });
    }
    return chartJsPromise;
}

function initCharts() {
    const analyticsEl = document.getElementById('analytics-data');
    if (!analyticsEl) return;

    const analytics = JSON.parse(analyticsEl.textContent || '{}');

    // Revenue Trend Chart
    const revenueCtx = document.getElementById('revenueChart');
    if (revenueCtx) {
        if (revenueChart) revenueChart.destroy();

        revenueChart = new Chart(revenueCtx, {
            type: 'line',
            data: {
                labels: analytics.chart_labels || [],
                datasets: [{
                    label: 'Revenue',
                    data: analytics.chart_revenue || [],
                    borderColor: '#a855f7',
                    backgroundColor: 'rgba(168, 85, 247, 0.1)',
                    fill: true,
                    tension: 0.4,
                    borderWidth: 3,
                    pointBackgroundColor: '#a855f7',
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2,
                    pointRadius: 6,
                    pointHoverRadius: 8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        backgroundColor: '#1e293b',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        padding: 12,
                        cornerRadius: 12,
                        displayColors: false
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: {
                            color: 'rgba(255,255,255,0.05)'
                        },
                        ticks: {
                            color: '#6b7280'
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        },
                        ticks: {
                            color: '#6b7280'
                        }
                    }
                }
            }
        });
    }

    // Hourly Distribution Chart
    const hourlyCtx = document.getElementById('hourlyChart');
    if (hourlyCtx) {
        if (hourlyChart) hourlyChart.destroy();

        // Find peak hour for highlighting
        const hourlyData = analytics.hourly_revenue || [];
        const maxValue = Math.max(...hourlyData);
        const colors = hourlyData.map(v => v === maxValue && maxValue > 0 ? '#22c55e' : 'rgba(168, 85, 247, 0.6)');

        hourlyChart = new Chart(hourlyCtx, {
            type: 'bar',
            data: {
                labels: ['12a', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12p', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11'],
                datasets: [{
                    label: 'Revenue',
                    data: hourlyData,
                    backgroundColor: colors,
                    borderRadius: 4,
                    borderSkipped: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        backgroundColor: '#1e293b',
                        padding: 12,
                        cornerRadius: 12
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: {
                            color: 'rgba(255,255,255,0.05)'
                        },
                        ticks: {
                            color: '#6b7280',
                            display: false
                        }
                    },
                    x: {
                        grid: {
                            display: false
                        },
                        ticks: {
                            color: '#6b7280',
                            font: {
                                size: 9
                            }
                        }
                    }
                }
            }
        });
    }
}

function inventoryApp() {
    return {
        // Whole numbers print as-is; fractional stock (weight/volume-sold items,
        // e.g. 0.75 kg) keeps up to 3 decimal places instead of being truncated
        // to 0 — Math.trunc() used to make an in-stock item read as "0".
        formatQty(value) {
            if (value === null || value === undefined || Number.isNaN(Number(value))) return '';
            const n = Number(value);
            if (Number.isInteger(n)) return String(n);
            const s = n.toFixed(3).replace(/\.?0+$/, '');
            return s === '-0' ? '0' : s; // toFixed(3) on a tiny negative (e.g. -0.0001) rounds to "-0.000"
        },
        showRestockModal: false,
        restockItemId: null,
        restockItemName: '',
        restockCurrentStock: 0,
        restockPaymentMethod: 'Cash',
        restockSaving: false,
        restockError: '',

        items: [],
        search: '',
        stockStatus: '',
        filtersOpen: false,
        _searchTimer: null,
        loading: false,
        error: '',
        offset: 0,
        limit: 10,
        total: 0,
        _loaded: false,

        // Called from x-init once this tab first becomes active, not on
        // page load — see guardedLoad()'s comment for the retry-on-failure
        // reasoning shared by every tab that uses this pattern.
        load() {
            guardedLoad(this, () => this.searchItems());
        },

        onSearchInput() {
            clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(() => { this.offset = 0; this.searchItems(); }, 300);
        },

        onStockStatusChange() {
            this.offset = 0;
            this.searchItems();
        },

        async searchItems() {
            this.loading = true;
            this.error = '';
            try {
                const filterParams = { q: this.search, active_only: 'false', limit: this.limit };
                if (this.stockStatus) filterParams.stock_status = this.stockStatus;
                const result = await fetchPaginated(
                    '/admin/menu/search', filterParams,
                    this.offset, 'items',
                );
                this.items = result.items;
                this.total = result.total;
            } catch (e) {
                this.error = e.message || 'Failed to load inventory';
                throw e;
            } finally {
                this.loading = false;
            }
        },

        get page() {
            return Math.floor(this.offset / this.limit) + 1;
        },
        get totalPages() {
            return Math.max(1, Math.ceil(this.total / this.limit));
        },
        goToPage(page) {
            const clamped = Math.max(1, Math.min(page, this.totalPages));
            this.offset = (clamped - 1) * this.limit;
            this.searchItems();
        },
        changeLimit() {
            this.offset = 0;  // page size changed — restart from page 1
            this.searchItems();
        },

        openRestockModal(itemId, itemName, currentStock) {
            this.restockItemId = itemId;
            this.restockItemName = itemName;
            this.restockCurrentStock = currentStock;
            this.restockPaymentMethod = 'Cash';
            this.restockError = '';
            this.showRestockModal = true;
        },

        // Submitted via fetch (not a native form POST) so a restock doesn't
        // navigate the page — a full-page reload would reset the search/
        // stock-status filters and pagination back to defaults.
        async submitRestock(event) {
            this.restockSaving = true;
            this.restockError = '';
            try {
                const response = await fetch(event.target.action, {
                    method: 'POST',
                    body: new FormData(event.target),
                    headers: { 'x-csrf-token': ApiClient.getCsrfToken() },
                });
                if (!response.ok) throw new Error('Failed to restock item');
                this.showRestockModal = false;
                event.target.reset();
                this.restockPaymentMethod = 'Cash';
                await this.searchItems();
            } catch (e) {
                this.restockError = e.message || 'Failed to restock item';
            } finally {
                this.restockSaving = false;
            }
        },

        handleScanResult(sku) {
            fetch(`/admin/inventory/by-sku/${encodeURIComponent(sku)}`)
                .then(r => r.json())
                .then(item => {
                    if (item.id) {
                        this.openRestockModal(item.id, item.name, item.stock_quantity ?? 0);
                    } else {
                        alert(`SKU '${sku}' not found in this shop`);
                    }
                })
                .catch(() => alert(`SKU '${sku}' not found`));
        }
    };
}

function expensesApp(currencySymbol, defaultCategory) {
    return {
        currencySymbol,
        defaultCategory,
        expenses: [],
        search: '',
        _searchTimer: null,
        loading: false,
        error: '',
        filters: { category: '', date_from: '', date_to: '' },
        filtersOpen: false,

        offset: 0,
        limit: 10,
        total: 0,
        // Sum across every filtered row (server-computed, voided excluded) —
        // NOT just the current page, so pagination doesn't make the "Total
        // (filtered)" card silently wrong.
        totalAmountFiltered: 0,

        showModal: false,
        editingSlug: null,
        saving: false,
        formError: '',
        form: { category: '', description: '', amount: '', tax_amount: '0.00', vendor_name: '', expense_date: '', payment_method: 'Cash' },
        _loaded: false,

        load() {
            guardedLoad(this, () => this.fetchExpenses());
        },

        formatCurrency(v) {
            return `${this.currencySymbol}${Number(v || 0).toFixed(2)}`;
        },

        totalAmount() {
            return this.totalAmountFiltered;
        },

        get page() {
            return Math.floor(this.offset / this.limit) + 1;
        },
        get totalPages() {
            return Math.max(1, Math.ceil(this.total / this.limit));
        },
        goToPage(page) {
            const clamped = Math.max(1, Math.min(page, this.totalPages));
            this.offset = (clamped - 1) * this.limit;
            this.fetchExpenses();
        },
        changeLimit() {
            this.offset = 0;  // page size changed — restart from page 1
            this.fetchExpenses();
        },

        onSearchInput() {
            clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(() => { this.offset = 0; this.fetchExpenses(); }, 300);
        },

        async fetchExpenses() {
            this.loading = true;
            this.error = '';
            const params = new URLSearchParams();
            params.set('q', this.search);
            params.set('limit', String(this.limit));
            params.set('offset', String(this.offset));
            params.set('include_voided', 'true');
            if (this.filters.category) params.set('category', this.filters.category);
            if (this.filters.date_from) params.set('date_from', this.filters.date_from);
            if (this.filters.date_to) params.set('date_to', this.filters.date_to);
            try {
                const data = await ApiClient.request(`/admin/expenses?${params.toString()}`);
                this.expenses = data.expenses;
                this.total = data.total;
                this.totalAmountFiltered = data.total_amount;
            } catch (e) {
                this.error = e.message || 'Failed to load expenses';
                throw e;
            } finally {
                this.loading = false;
            }
        },

        openAddModal() {
            this.editingSlug = null;
            this.formError = '';
            const today = new Date().toISOString().slice(0, 10);
            this.form = { category: this.defaultCategory, description: '', amount: '', tax_amount: '0.00', vendor_name: '', expense_date: today, payment_method: 'Cash' };
            this.showModal = true;
        },

        openEditModal(expense) {
            this.editingSlug = expense.slug;
            this.formError = '';
            this.form = {
                category: expense.category,
                description: expense.description,
                amount: expense.amount,
                tax_amount: expense.tax_amount,
                vendor_name: expense.vendor_name || '',
                expense_date: expense.expense_date,
                payment_method: expense.payment_method,
            };
            this.showModal = true;
        },

        async saveExpense() {
            this.formError = '';
            if (!this.form.description || !this.form.amount || !this.form.expense_date) {
                this.formError = 'Description, amount, and date are required';
                return;
            }
            this.saving = true;
            const isNew = !this.editingSlug;
            const url = this.editingSlug ? `/admin/expenses/update/${this.editingSlug}` : '/admin/expenses/add';
            try {
                await ApiClient.request(url, { method: 'POST', body: JSON.stringify(this.form) });
                this.showModal = false;
                if (isNew) this.offset = 0;  // new expense sorts near the top — jump back to page 1 to show it
                await this.fetchExpenses();
            } catch (e) {
                this.formError = e.message || 'Failed to save expense';
            } finally {
                this.saving = false;
            }
        },

        async voidExpense(expense) {
            if (!confirm(`Void this expense (${this.formatCurrency(expense.amount)})? This cannot be undone.`)) return;
            try {
                await ApiClient.request(`/admin/expenses/void/${expense.slug}`, { method: 'POST' });
                await this.fetchExpenses();
            } catch (e) {
                this.error = e.message || 'Failed to void expense';
            }
        }
    };
}

function ordersTabApp(currencySymbol, shopUpiId, shopName) {
    return {
        currencySymbol,
        shopUpiId,
        shopName,
        orders: [],
        total: 0,
        offset: 0,
        limit: 10,
        filters: { date_from: '', date_to: '', min_amount: '', max_amount: '', payment_status: '', delivery_status: '' },
        loading: false,
        error: '',
        viewModal: { open: false, order: null },
        filtersOpen: false,
        showCompleted: false,
        _loaded: false,

        load() {
            guardedLoad(this, () => this.fetchOrders());
        },

        formatCurrency(v) {
            return `${this.currencySymbol}${Number(v || 0).toFixed(2)}`;
        },

        // Actionable orders first — in progress, then just-needs-payment, then not-yet-started —
        // so the delivery worker sees "what to do next" without scanning a flat list.
        get activeOrders() {
            const priority = (o) => {
                if (o.delivery_status === 'Out for Delivery') return 0;
                if (o.delivery_status === 'Delivered' && o.payment_status !== 'Paid') return 1;
                return 2;
            };
            return this.orders
                .filter(o => !(o.delivery_status === 'Delivered' && o.payment_status === 'Paid'))
                .sort((a, b) => priority(a) - priority(b));
        },
        get completedOrders() {
            return this.orders.filter(o => o.delivery_status === 'Delivered' && o.payment_status === 'Paid');
        },

        // One next-action button per order — no menu of options to read under pressure.
        primaryButton(order) {
            const due = () => this.formatCurrency(order.total_amount - order.amount_paid);
            if (order.delivery_status === 'Delivered') {
                if (order.payment_status === 'Paid') return null;
                return { label: 'Collect ' + due(), cls: 'bg-emerald-500 hover:bg-emerald-600', action: () => this.markPaid(order) };
            }
            if (order.delivery_status === 'Out for Delivery') {
                if (order.payment_status !== 'Paid' && !order.is_credit_customer) {
                    return { label: 'Delivered — Collect ' + due(), cls: 'bg-emerald-500 hover:bg-emerald-600', action: () => this.completeDelivery(order) };
                }
                return { label: 'Mark Delivered', cls: 'bg-blue-500 hover:bg-blue-600', action: () => this.advanceDeliveryStatus(order) };
            }
            return { label: 'Start Delivery', cls: 'bg-blue-500 hover:bg-blue-600', action: () => this.advanceDeliveryStatus(order) };
        },

        openView(order) {
            this.viewModal.order = order;
            this.viewModal.open = true;
        },

        upiQrUrl(order) {
            const amount = (Number(order.total_amount) - Number(order.amount_paid || 0)).toFixed(2);
            return upiQrDataUrl(this.shopUpiId, this.shopName, amount);
        },

        onFilterChange() {
            this.offset = 0;
            this.fetchOrders();
        },

        clearFilters() {
            this.filters = { date_from: '', date_to: '', min_amount: '', max_amount: '', payment_status: '', delivery_status: '' };
            this.onFilterChange();
        },

        async fetchOrders() {
            this.loading = true;
            try {
                const f = this.filters;
                const filterParams = { limit: this.limit };
                if (f.date_from) filterParams.date_from = f.date_from;
                if (f.date_to) filterParams.date_to = f.date_to;
                if (f.min_amount) filterParams.min_amount = f.min_amount;
                if (f.max_amount) filterParams.max_amount = f.max_amount;
                if (f.payment_status) filterParams.payment_status = f.payment_status;
                if (f.delivery_status) filterParams.delivery_status = f.delivery_status;
                const result = await fetchPaginated('/billing/orders', filterParams, this.offset, 'orders');
                this.orders = result.items;
                this.total = result.total;
            } catch (e) {
                this.error = e.message || 'Failed to load orders';
                throw e;
            } finally {
                this.loading = false;
            }
        },

        get page() {
            return Math.floor(this.offset / this.limit) + 1;
        },
        get totalPages() {
            return Math.max(1, Math.ceil(this.total / this.limit));
        },
        goToPage(page) {
            const clamped = Math.max(1, Math.min(page, this.totalPages));
            this.offset = (clamped - 1) * this.limit;
            this.fetchOrders();
        },
        changeLimit() {
            this.offset = 0;
            this.fetchOrders();
        },

        _idempotencyKey() {
            if (window.crypto && typeof window.crypto.randomUUID === 'function') {
                return window.crypto.randomUUID();
            }
            return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        },

        nextDeliveryStatus(current) {
            const stages = ['Pending', 'Out for Delivery', 'Delivered'];
            const idx = stages.indexOf(current);
            return stages[Math.min(idx + 1, stages.length - 1)];
        },

        async advanceDeliveryStatus(order) {
            const next = this.nextDeliveryStatus(order.delivery_status);
            if (next === order.delivery_status) return;
            try {
                const res = await ApiClient.request(`/billing/orders/${order.slug}/update`, {
                    method: 'POST', body: JSON.stringify({ delivery_status: next }),
                });
                order.delivery_status = res.order.delivery_status;
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        },

        async completeDelivery(order) {
            try {
                const res = await ApiClient.request(`/billing/orders/${order.slug}/update`, {
                    method: 'POST', body: JSON.stringify({ delivery_status: 'Delivered', mark_paid: true }),
                });
                order.delivery_status = res.order.delivery_status;
                order.payment_status = res.order.payment_status;
                order.amount_paid = res.order.amount_paid;
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        },

        async markPaid(order) {
            try {
                if (order.is_credit_customer) {
                    // Real credit account — goes through the same payment-recording
                    // endpoint the Credit Book's own "Record Payment" panel uses, so
                    // the CreditPayment audit trail and credit_balance stay correct.
                    const outstanding = Number(order.total_amount) - Number(order.amount_paid);
                    await ApiClient.request('/admin/credit/payments', {
                        method: 'POST',
                        body: JSON.stringify({
                            bill_slug: order.slug,
                            amount: outstanding.toFixed(2),
                            payment_method: 'Cash',
                            idempotency_key: this._idempotencyKey(),
                        }),
                    });
                    order.payment_status = 'Paid';
                    order.amount_paid = order.total_amount;
                } else {
                    const res = await ApiClient.request(`/billing/orders/${order.slug}/update`, {
                        method: 'POST', body: JSON.stringify({ mark_paid: true }),
                    });
                    order.payment_status = res.order.payment_status;
                    order.amount_paid = res.order.amount_paid;
                }
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        },

        // Manual correction — set delivery/payment status to any value directly,
        // for undoing an accidental tap rather than only stepping forward.
        async setDeliveryStatus(order, status) {
            if (status === order.delivery_status) return;
            try {
                const res = await ApiClient.request(`/billing/orders/${order.slug}/update`, {
                    method: 'POST', body: JSON.stringify({ delivery_status: status }),
                });
                order.delivery_status = res.order.delivery_status;
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        },

        async setPaymentStatus(order, status) {
            if (status === order.payment_status) return;
            if (order.is_credit_customer) {
                alert('This order belongs to a credit account — record/adjust payments from the Dashboard Credit Book instead.');
                return;
            }
            try {
                const res = await ApiClient.request(`/billing/orders/${order.slug}/update`, {
                    method: 'POST', body: JSON.stringify({ payment_status: status }),
                });
                order.payment_status = res.order.payment_status;
                order.amount_paid = res.order.amount_paid;
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        },

        resendWhatsapp(order) {
            let phone = order.customer_phone;
            if (!phone) {
                phone = prompt("Enter customer WhatsApp number:", "");
            }
            if (phone && phone.trim().length >= 10) {
                window.open(`/admin/bill/${encodeURIComponent(order.slug)}/whatsapp-redirect?phone=${encodeURIComponent(phone.trim())}`, '_blank');
            }
        },
    };
}

function transactionsApp(currencySymbol) {
    return {
        currencySymbol,
        transactions: [], total: 0, offset: 0, limit: 25,
        filters: { date_from: '', date_to: '', customer_id: '' },
        filtersOpen: false, loading: false, error: '', _loaded: false,
        customerSearch: '', customerResults: [], customerSearchLoading: false,
        _customerSearchTimer: null, selectedCustomer: null,
        viewBillId: null,

        load() {
            guardedLoad(this, () => this.fetchTransactions());
        },

        formatCurrency(v) {
            return `${this.currencySymbol}${Number(v || 0).toFixed(2)}`;
        },

        viewBill(id) {
            this.viewBillId = id;
        },

        closeBillModal() {
            this.viewBillId = null;
        },

        onFilterChange() {
            this.offset = 0;
            this.fetchTransactions();
        },

        clearFilters() {
            this.filters = { date_from: '', date_to: '', customer_id: '' };
            this.selectedCustomer = null;
            this.customerSearch = '';
            this.customerResults = [];
            this.onFilterChange();
        },

        async fetchTransactions() {
            this.loading = true;
            try {
                const f = this.filters;
                const filterParams = { limit: this.limit };
                if (f.date_from) filterParams.date_from = f.date_from;
                if (f.date_to) filterParams.date_to = f.date_to;
                if (f.customer_id) filterParams.customer_id = f.customer_id;
                const result = await fetchPaginated('/billing/transactions', filterParams, this.offset, 'transactions');
                this.transactions = result.items;
                this.total = result.total;
            } catch (e) {
                this.error = e.message || 'Failed to load transactions';
                throw e;
            } finally {
                this.loading = false;
            }
        },

        get page() { return Math.floor(this.offset / this.limit) + 1; },
        get totalPages() { return Math.max(1, Math.ceil(this.total / this.limit)); },
        goToPage(page) {
            const clamped = Math.max(1, Math.min(page, this.totalPages));
            this.offset = (clamped - 1) * this.limit;
            this.fetchTransactions();
        },
        changeLimit() {
            this.offset = 0;
            this.fetchTransactions();
        },

        onCustomerSearchInput() {
            clearTimeout(this._customerSearchTimer);
            this._customerSearchTimer = setTimeout(() => this.searchCustomersForFilter(), 300);
        },
        async searchCustomersForFilter() {
            this.customerSearchLoading = true;
            try {
                const params = new URLSearchParams({ q: this.customerSearch, limit: '10' });
                const data = await ApiClient.request(`/admin/customers/search?${params.toString()}`);
                this.customerResults = data.customers;
            } catch (e) {
                console.error('Failed to search customers', e);
            } finally {
                this.customerSearchLoading = false;
            }
        },
        selectCustomerFilter(c) {
            this.selectedCustomer = c;
            this.filters.customer_id = c.id;
            this.customerResults = [];
            this.customerSearch = '';
            this.onFilterChange();
        },
        clearCustomerFilter() {
            this.selectedCustomer = null;
            this.filters.customer_id = '';
            this.onFilterChange();
        },
    };
}

function creditBookApp(currencySymbol, shopUpiId, shopName) {
    return {
        currencySymbol,
        shopUpiId,
        shopName,
        accounts: [],
        accountsTotal: 0,
        accountsOffset: 0,
        accountsLimit: 10,
        stats: { total_outstanding: 0, overdue_amount: 0, overdue_count: 0, active_accounts: 0 },
        search: '',
        _searchTimer: null,
        sortBy: 'urgency',
        order: 'desc',
        overdueOnly: false,
        paymentTermType: '',
        dueFrom: '',
        dueTo: '',
        loading: false,
        error: '',
        notice: '',
        generating: false,
        filtersOpen: false,        // accounts-list toolbar filters
        historyFiltersOpen: false, // Bill History sub-panel filters (independent — see openDetail/closeDetail)

        view: 'list',   // 'list' | 'detail' — Collect navigates to a detail view, not a modal
        selectedCustomer: null,
        ledger: null,
        ledgerLoading: false,
        ledgerTab: 'unpaid',   // 'unpaid' | 'statements' | 'history' — detail view shows one at a time

        paymentTarget: null,   // { type: 'bill' | 'statement', obj } — set while the inline payment panel is open
        paymentForm: { amount: '', payment_method: 'Cash', note: '' },
        saving: false,
        formError: '',

        bills: [],
        billsTotal: 0,
        billsLoading: false,
        billsOffset: 0,
        billsLimit: 10,
        billsFilters: { date_from: '', date_to: '', min_amount: '', max_amount: '', status: '' },
        _loaded: false,

        load() {
            guardedLoad(this, () => this.fetchAccounts());
        },

        onSearchInput() {
            clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(() => { this.accountsOffset = 0; this.fetchAccounts(); }, 300);
        },

        get accountsPage() {
            return Math.floor(this.accountsOffset / this.accountsLimit) + 1;
        },
        get accountsTotalPages() {
            return Math.max(1, Math.ceil(this.accountsTotal / this.accountsLimit));
        },
        goToAccountsPage(page) {
            const clamped = Math.max(1, Math.min(page, this.accountsTotalPages));
            this.accountsOffset = (clamped - 1) * this.accountsLimit;
            this.fetchAccounts();
        },
        changeAccountsLimit() {
            this.accountsOffset = 0;  // page size changed — restart from page 1
            this.fetchAccounts();
        },

        async fetchAccounts() {
            this.loading = true;
            this.error = '';
            try {
                const params = new URLSearchParams({
                    q: this.search, sort_by: this.sortBy, order: this.order,
                    overdue_only: this.overdueOnly ? 'true' : 'false',
                    limit: String(this.accountsLimit), offset: String(this.accountsOffset),
                });
                if (this.paymentTermType) params.set('payment_term_type', this.paymentTermType);
                if (this.dueFrom) params.set('due_from', this.dueFrom);
                if (this.dueTo) params.set('due_to', this.dueTo);
                const data = await ApiClient.request(`/admin/credit/accounts?${params.toString()}`);
                this.accounts = data.accounts;
                this.accountsTotal = data.accounts_total;
                this.stats = data.stats;
            } catch (e) {
                this.error = e.message || 'Failed to load credit accounts';
                throw e;
            } finally {
                this.loading = false;
            }
        },

        clearFilters() {
            this.search = '';
            this.overdueOnly = false;
            this.accountsOffset = 0;
            this.paymentTermType = '';
            this.dueFrom = '';
            this.dueTo = '';
            this.sortBy = 'urgency';
            this.order = 'desc';
            this.fetchAccounts();
        },

        formatCurrency(v) {
            return `${this.currencySymbol}${Number(v || 0).toFixed(2)}`;
        },

        ringColor(a) {
            if (a.days_overdue > 0) return '#f43f5e';
            if (a.utilization_pct == null) return '#94a3b8';
            if (a.utilization_pct >= 80) return '#f43f5e';
            if (a.utilization_pct >= 50) return '#f59e0b';
            return '#10b981';
        },

        ringStyle(a) {
            const pct = Math.max(0, Math.min(100, a.utilization_pct ?? 0));
            return `background: conic-gradient(${this.ringColor(a)} ${pct * 3.6}deg, rgba(148,163,184,0.25) 0deg);`;
        },

        openWhatsApp(a) {
            window.open(`/admin/credit/customers/${a.slug}/whatsapp-reminder?phone=${encodeURIComponent(a.phone_number)}`, '_blank');
        },

        async openDetail(a) {
            this.selectedCustomer = a;
            this.view = 'detail';
            this.ledgerTab = 'unpaid';
            this.paymentTarget = null;
            this.ledger = null;
            this.ledgerLoading = true;
            this.error = '';
            this.billsFilters = { date_from: '', date_to: '', min_amount: '', max_amount: '', status: '' };
            this.billsOffset = 0;
            this.bills = [];
            this.historyFiltersOpen = false;
            try {
                this.ledger = await ApiClient.request(`/admin/credit/customers/${a.slug}/ledger`);
            } catch (e) {
                this.error = e.message || 'Failed to load ledger';
            } finally {
                this.ledgerLoading = false;
            }
            await this.fetchBills();
        },

        closeDetail() {
            this.view = 'list';
            this.selectedCustomer = null;
            this.ledger = null;
            this.paymentTarget = null;
            this.bills = [];
            this.historyFiltersOpen = false;
        },

        onBillsFilterChange() {
            this.billsOffset = 0;
            this.fetchBills();
        },

        clearBillsFilters() {
            this.billsFilters = { date_from: '', date_to: '', min_amount: '', max_amount: '', status: '' };
            this.billsOffset = 0;
            this.fetchBills();
        },

        async fetchBills() {
            if (!this.selectedCustomer) return;
            this.billsLoading = true;
            try {
                const f = this.billsFilters;
                const filterParams = { limit: this.billsLimit };
                if (f.date_from) filterParams.date_from = f.date_from;
                if (f.date_to) filterParams.date_to = f.date_to;
                if (f.min_amount) filterParams.min_amount = f.min_amount;
                if (f.max_amount) filterParams.max_amount = f.max_amount;
                if (f.status) filterParams.status = f.status;
                const result = await fetchPaginated(
                    `/admin/credit/customers/${this.selectedCustomer.slug}/bills`, filterParams,
                    this.billsOffset, 'bills',
                );
                this.bills = result.items;
                this.billsTotal = result.total;
            } catch (e) {
                this.error = e.message || 'Failed to load bill history';
            } finally {
                this.billsLoading = false;
            }
        },

        get billsPage() {
            return Math.floor(this.billsOffset / this.billsLimit) + 1;
        },
        get billsTotalPages() {
            return Math.max(1, Math.ceil(this.billsTotal / this.billsLimit));
        },
        goToBillsPage(page) {
            const clamped = Math.max(1, Math.min(page, this.billsTotalPages));
            this.billsOffset = (clamped - 1) * this.billsLimit;
            this.fetchBills();
        },
        changeBillsLimit() {
            this.billsOffset = 0;  // page size changed — restart from page 1
            this.fetchBills();
        },

        async refreshLedger() {
            if (!this.selectedCustomer) return;
            try {
                this.ledger = await ApiClient.request(`/admin/credit/customers/${this.selectedCustomer.slug}/ledger`);
            } catch (e) {
                this.error = e.message || 'Failed to load ledger';
            }
        },

        startPayment(type, obj) {
            this.paymentTarget = { type, obj };
            const outstanding = Number(obj.total_amount) - Number(obj.amount_paid);
            this.paymentForm = { amount: outstanding.toFixed(2), payment_method: 'Cash', note: '' };
            this.formError = '';
        },

        startFullSettlement() {
            if (!this.ledger || !(Number(this.ledger.credit_balance) > 0)) return;
            this.paymentTarget = { type: 'full', obj: null };
            this.paymentForm = { amount: Number(this.ledger.credit_balance).toFixed(2), payment_method: 'Cash', note: '' };
            this.formError = '';
        },

        cancelPayment() {
            this.paymentTarget = null;
            this.formError = '';
        },

        paymentTargetLabel() {
            if (!this.paymentTarget) return '';
            const { type, obj } = this.paymentTarget;
            if (type === 'bill') return `Paying bill ${obj.bill_number}`;
            if (type === 'statement') return `Paying statement ${obj.statement_number}`;
            return 'Settling the full outstanding balance';
        },

        // wa.me-style UPI deep link rendered as a scannable QR — same pattern as the POS checkout screen.
        upiQrUrl() {
            const amount = Number(this.paymentForm.amount || 0).toFixed(2);
            return upiQrDataUrl(this.shopUpiId, this.shopName, amount);
        },

        _idempotencyKey() {
            // crypto.randomUUID() requires a secure context and a modern
            // browser — fall back rather than throw outside the try/catch
            // below, which would leave `saving` stuck true forever (a
            // payment button that looks like it's doing nothing).
            if (window.crypto && typeof window.crypto.randomUUID === 'function') {
                return window.crypto.randomUUID();
            }
            return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        },

        async submitPayment() {
            this.formError = '';
            const isFull = this.paymentTarget.type === 'full';
            if (!isFull && (!this.paymentForm.amount || Number(this.paymentForm.amount) <= 0)) {
                this.formError = 'Enter a valid amount';
                return;
            }
            this.saving = true;
            try {
                if (isFull) {
                    const body = {
                        payment_method: this.paymentForm.payment_method,
                        note: this.paymentForm.note || null,
                        idempotency_key: this._idempotencyKey(),
                    };
                    await ApiClient.request(
                        `/admin/credit/customers/${this.selectedCustomer.slug}/settle-full-balance`,
                        { method: 'POST', body: JSON.stringify(body) },
                    );
                } else {
                    const body = {
                        amount: this.paymentForm.amount,
                        payment_method: this.paymentForm.payment_method,
                        note: this.paymentForm.note || null,
                        idempotency_key: this._idempotencyKey(),
                    };
                    if (this.paymentTarget.type === 'bill') {
                        body.bill_slug = this.paymentTarget.obj.slug;
                    } else {
                        body.statement_slug = this.paymentTarget.obj.slug;
                    }
                    await ApiClient.request('/admin/credit/payments', { method: 'POST', body: JSON.stringify(body) });
                }
                this.paymentTarget = null;
                await this.refreshLedger();
                await this.fetchAccounts();
                this.billsOffset = 0;
                await this.fetchBills();
            } catch (e) {
                this.formError = e.message || 'Failed to record payment';
            } finally {
                this.saving = false;
            }
        },

        async generateStatements() {
            this.generating = true;
            this.error = '';
            this.notice = '';
            try {
                const data = await ApiClient.request('/admin/credit/statements/generate', { method: 'POST' });
                this.notice = `Generated ${data.generated} statement(s).`;
                await this.fetchAccounts();
            } catch (e) {
                this.error = e.message || 'Failed to generate statements';
            } finally {
                this.generating = false;
            }
        }
    };
}

function rateCardsApp(currencySymbol) {
    return {
        currencySymbol,
        customers: [],
        search: '',
        searchLoading: false,
        _searchTimer: null,
        selectedCustomer: null,
        prices: [],
        loading: false,
        error: '',

        showModal: false,
        editingSlug: null,   // null = adding a new rate; else editing this row's slug
        itemSearch: '',
        itemResults: [],
        itemSearchLoading: false,
        _itemSearchTimer: null,
        form: { menu_item_id: '', price: '', valid_from: '', valid_to: '' },
        formError: '',
        saving: false,
        _loaded: false,

        load() {
            guardedLoad(this, () => this.searchCustomers(), { loadingKey: 'searchLoading' });
        },

        onSearchInput() {
            clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(() => this.searchCustomers(), 300);
        },

        async searchCustomers() {
            this.searchLoading = true;
            try {
                const params = new URLSearchParams({ q: this.search, limit: '20' });
                const data = await ApiClient.request(`/admin/customers/search?${params.toString()}`);
                this.customers = data.customers;
            } catch (e) {
                this.error = e.message || 'Failed to search customers';
                throw e;
            } finally {
                this.searchLoading = false;
            }
        },

        formatCurrency(v) {
            return `${this.currencySymbol}${Number(v || 0).toFixed(2)}`;
        },

        async selectCustomer(c) {
            this.selectedCustomer = c;
            this.error = '';
            await this.fetchPrices();
        },

        async fetchPrices() {
            if (!this.selectedCustomer) return;
            this.loading = true;
            try {
                const data = await ApiClient.request(`/admin/customers/${this.selectedCustomer.slug}/prices`);
                this.prices = data.prices;
            } catch (e) {
                this.error = e.message || 'Failed to load rate cards';
            } finally {
                this.loading = false;
            }
        },

        // Full price-validity table: one row per stored rate, item names
        // resolved server-side (no full menu catalog fetched client-side).
        // "effectiveValidTo" is display-only — per task.yaml FEAT-3, never
        // persisted: a row's own valid_to if set, else the next rate's
        // valid_from minus a day, else "Ongoing".
        tableRows() {
            const byItem = {};
            for (const p of this.prices) {
                (byItem[p.menu_item_id] ??= []).push(p);
            }
            const rows = [];
            for (const itemId in byItem) {
                const group = byItem[itemId].slice().sort((a, b) => a.valid_from.localeCompare(b.valid_from));
                group.forEach((row, i) => {
                    let effectiveValidTo;
                    if (row.valid_to) {
                        effectiveValidTo = row.valid_to;
                    } else if (i + 1 < group.length) {
                        const next = new Date(group[i + 1].valid_from + 'T00:00:00');
                        next.setDate(next.getDate() - 1);
                        effectiveValidTo = next.toISOString().slice(0, 10);
                    } else {
                        effectiveValidTo = 'Ongoing';
                    }
                    rows.push({ ...row, effectiveValidTo });
                });
            }
            rows.sort((a, b) => a.menu_item_name.localeCompare(b.menu_item_name) || b.valid_from.localeCompare(a.valid_from));
            return rows;
        },

        onItemSearchInput() {
            clearTimeout(this._itemSearchTimer);
            this._itemSearchTimer = setTimeout(() => this.searchMenuItems(), 300);
        },

        async searchMenuItems() {
            this.itemSearchLoading = true;
            try {
                const params = new URLSearchParams({ q: this.itemSearch, limit: '20' });
                const data = await ApiClient.request(`/admin/menu/search?${params.toString()}`);
                this.itemResults = data.items;
            } catch (e) {
                this.formError = e.message || 'Failed to search items';
            } finally {
                this.itemSearchLoading = false;
            }
        },

        pickItem(item) {
            this.form.menu_item_id = item.id;
            this.itemSearch = item.name;
            this.itemResults = [];
        },

        openAddModal() {
            if (!this.selectedCustomer) return;
            this.editingSlug = null;
            this.formError = '';
            const today = new Date().toISOString().slice(0, 10);
            this.form = { menu_item_id: '', price: '', valid_from: today, valid_to: '' };
            this.itemSearch = '';
            this.itemResults = [];
            this.showModal = true;
            this.searchMenuItems();
        },

        openEditModal(row) {
            this.editingSlug = row.slug;
            this.formError = '';
            this.form = { menu_item_id: row.menu_item_id, price: row.price, valid_from: row.valid_from, valid_to: row.valid_to || '' };
            this.itemSearch = row.menu_item_name;
            this.itemResults = [];
            this.showModal = true;
        },

        async savePrice() {
            this.formError = '';
            if (!this.editingSlug && !this.form.menu_item_id) {
                this.formError = 'Select an item';
                return;
            }
            if (!this.form.price || !this.form.valid_from) {
                this.formError = 'Price and valid-from date are required';
                return;
            }
            this.saving = true;
            const body = { price: this.form.price, valid_from: this.form.valid_from, valid_to: this.form.valid_to || null };
            try {
                if (this.editingSlug) {
                    await ApiClient.request(`/admin/customers/${this.selectedCustomer.slug}/prices/${this.editingSlug}`, {
                        method: 'PUT', body: JSON.stringify(body)
                    });
                } else {
                    body.menu_item_id = Number(this.form.menu_item_id);
                    await ApiClient.request(`/admin/customers/${this.selectedCustomer.slug}/prices`, {
                        method: 'POST', body: JSON.stringify(body)
                    });
                }
                this.showModal = false;
                await this.fetchPrices();
            } catch (e) {
                this.formError = e.message || 'Failed to save price';
            } finally {
                this.saving = false;
            }
        },

        async deletePrice(row) {
            if (!confirm(`Delete the ${this.formatCurrency(row.price)} rate for ${row.menu_item_name} (from ${row.valid_from})?`)) return;
            try {
                await ApiClient.request(`/admin/customers/${this.selectedCustomer.slug}/prices/${row.slug}`, { method: 'DELETE' });
                await this.fetchPrices();
            } catch (e) {
                this.error = e.message || 'Failed to delete price';
            }
        }
    };
}
