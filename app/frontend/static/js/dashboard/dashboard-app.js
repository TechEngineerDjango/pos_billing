/**
 * Shared offset/limit pagination fetch — used by every search-driven list in
 * this dashboard (Menu, Inventory, Customers, Credit Book bill history) so
 * the fetch/concat/total bookkeeping isn't copy-pasted per tab. Pass the
 * current offset and the list's existing items; on offset 0 it replaces the
 * list, otherwise it appends (a "Load More" click), matching how
 * superadmin's shop-billing list also accumulates from a capped, paginated
 * endpoint rather than embedding everything up front — just with an
 * infinite-scroll-style offset instead of that page's Prev/Next page
 * numbers, since these lists are typically browsed sequentially rather than
 * jumped into at an arbitrary page.
 */
async function fetchPaginated(url, params, offset, existingItems, resultKey) {
    const p = new URLSearchParams(params);
    p.set('offset', String(offset));
    const data = await ApiClient.request(`${url}?${p.toString()}`);
    return {
        items: offset === 0 ? data[resultKey] : existingItems.concat(data[resultKey]),
        total: data.total,
    };
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
        menuLimit: 50,
        menuTotal: 0,

        // Customers tab — same treatment, reuses the existing
        // /admin/customers/search endpoint built for the Credit Book/Rate
        // Cards pickers.
        customerRows: [],
        customerSearch: '',
        _customerSearchTimer: null,
        customersLoading: false,
        customerOffset: 0,
        customerLimit: 50,
        customerTotal: 0,
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
            try {
                const result = await fetchPaginated(
                    '/admin/menu/search', { q: this.menuSearch, active_only: 'false', limit: this.menuLimit },
                    this.menuOffset, this.menuItems, 'items',
                );
                this.menuItems = result.items;
                this.menuTotal = result.total;
            } catch (e) {
                console.error('Failed to search menu items', e);
            } finally {
                this.menuLoading = false;
            }
        },

        loadMoreMenuItems() {
            this.menuOffset += this.menuLimit;
            this.searchMenuItems();
        },

        onCustomerSearchInput() {
            clearTimeout(this._customerSearchTimer);
            this._customerSearchTimer = setTimeout(() => { this.customerOffset = 0; this.searchCustomersTab(); }, 300);
        },

        async searchCustomersTab() {
            this.customersLoading = true;
            try {
                const result = await fetchPaginated(
                    '/admin/customers/search', { q: this.customerSearch, limit: this.customerLimit, include_due_date: true },
                    this.customerOffset, this.customerRows, 'customers',
                );
                this.customerRows = result.items;
                this.customerTotal = result.total;
            } catch (e) {
                console.error('Failed to search customers', e);
            } finally {
                this.customersLoading = false;
            }
        },

        loadMoreCustomersTab() {
            this.customerOffset += this.customerLimit;
            this.searchCustomersTab();
        },

        switchTab(t) {
            this.activeTab = t;
            const url = new URL(window.location);
            url.searchParams.set('tab', t);
            window.history.replaceState({}, '', url);

            // Initialize charts when switching to reports tab
            if (t === 'reports') {
                this.$nextTick(() => {
                    initCharts();
                });
            }
        },

        init() {
            this.searchMenuItems();
            this.searchCustomersTab();

            // Initialize charts if already on reports tab
            if (this.activeTab === 'reports') {
                this.$nextTick(() => {
                    initCharts();
                });
            }
        }
    }
}

let revenueChart = null;
let hourlyChart = null;

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
        showRestockModal: false,
        restockItemId: null,
        restockItemName: '',
        restockCurrentStock: 0,

        items: [],
        search: '',
        stockStatus: '',
        _searchTimer: null,
        loading: false,
        offset: 0,
        limit: 50,
        total: 0,

        init() {
            this.searchItems();
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
            try {
                const filterParams = { q: this.search, active_only: 'false', limit: this.limit };
                if (this.stockStatus) filterParams.stock_status = this.stockStatus;
                const result = await fetchPaginated(
                    '/admin/menu/search', filterParams,
                    this.offset, this.items, 'items',
                );
                this.items = result.items;
                this.total = result.total;
            } catch (e) {
                console.error('Failed to search inventory items', e);
            } finally {
                this.loading = false;
            }
        },

        loadMoreItems() {
            this.offset += this.limit;
            this.searchItems();
        },

        openRestockModal(itemId, itemName, currentStock) {
            this.restockItemId = itemId;
            this.restockItemName = itemName;
            this.restockCurrentStock = currentStock;
            this.showRestockModal = true;
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
        loading: false,
        error: '',
        filters: { category: '', date_from: '', date_to: '' },

        showModal: false,
        editingSlug: null,
        saving: false,
        formError: '',
        form: { category: '', description: '', amount: '', tax_amount: '0.00', vendor_name: '', expense_date: '', payment_method: 'Cash' },

        init() {
            this.fetchExpenses();
        },

        formatCurrency(v) {
            return `${this.currencySymbol}${Number(v || 0).toFixed(2)}`;
        },

        filteredExpenses() {
            const q = this.search.trim().toLowerCase();
            if (!q) return this.expenses;
            return this.expenses.filter(e =>
                (e.description || '').toLowerCase().includes(q) || (e.vendor_name || '').toLowerCase().includes(q)
            );
        },

        totalAmount() {
            return this.filteredExpenses().filter(e => !e.is_voided).reduce((sum, e) => sum + Number(e.amount || 0), 0);
        },

        async fetchExpenses() {
            this.loading = true;
            this.error = '';
            const params = new URLSearchParams();
            params.set('include_voided', 'true');
            if (this.filters.category) params.set('category', this.filters.category);
            if (this.filters.date_from) params.set('date_from', this.filters.date_from);
            if (this.filters.date_to) params.set('date_to', this.filters.date_to);
            try {
                const data = await ApiClient.request(`/admin/expenses?${params.toString()}`);
                this.expenses = data.expenses;
            } catch (e) {
                this.error = e.message || 'Failed to load expenses';
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
            const url = this.editingSlug ? `/admin/expenses/update/${this.editingSlug}` : '/admin/expenses/add';
            try {
                await ApiClient.request(url, { method: 'POST', body: JSON.stringify(this.form) });
                this.showModal = false;
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

function creditBookApp(currencySymbol, shopUpiId, shopName) {
    return {
        currencySymbol,
        shopUpiId,
        shopName,
        accounts: [],
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

        view: 'list',   // 'list' | 'detail' — Collect navigates to a detail view, not a modal
        selectedCustomer: null,
        ledger: null,
        ledgerLoading: false,

        paymentTarget: null,   // { type: 'bill' | 'statement', obj } — set while the inline payment panel is open
        paymentForm: { amount: '', payment_method: 'Cash', note: '' },
        saving: false,
        formError: '',

        bills: [],
        billsTotal: 0,
        billsLoading: false,
        billsOffset: 0,
        billsLimit: 25,
        billsFilters: { date_from: '', date_to: '', min_amount: '', max_amount: '', status: '' },

        init() {
            this.fetchAccounts();
        },

        onSearchInput() {
            clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(() => this.fetchAccounts(), 300);
        },

        async fetchAccounts() {
            this.loading = true;
            this.error = '';
            try {
                const params = new URLSearchParams({
                    q: this.search, sort_by: this.sortBy, order: this.order,
                    overdue_only: this.overdueOnly ? 'true' : 'false',
                });
                if (this.paymentTermType) params.set('payment_term_type', this.paymentTermType);
                if (this.dueFrom) params.set('due_from', this.dueFrom);
                if (this.dueTo) params.set('due_to', this.dueTo);
                const data = await ApiClient.request(`/admin/credit/accounts?${params.toString()}`);
                this.accounts = data.accounts;
                this.stats = data.stats;
            } catch (e) {
                this.error = e.message || 'Failed to load credit accounts';
            } finally {
                this.loading = false;
            }
        },

        clearFilters() {
            this.search = '';
            this.overdueOnly = false;
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
            this.paymentTarget = null;
            this.ledger = null;
            this.ledgerLoading = true;
            this.error = '';
            this.billsFilters = { date_from: '', date_to: '', min_amount: '', max_amount: '', status: '' };
            this.billsOffset = 0;
            this.bills = [];
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
                    this.billsOffset, this.bills, 'bills',
                );
                this.bills = result.items;
                this.billsTotal = result.total;
            } catch (e) {
                this.error = e.message || 'Failed to load bill history';
            } finally {
                this.billsLoading = false;
            }
        },

        loadMoreBills() {
            this.billsOffset += this.billsLimit;
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
            if (!this.shopUpiId) return null;
            const amount = Number(this.paymentForm.amount || 0).toFixed(2);
            const upiLink = `upi://pay?pa=${this.shopUpiId}&pn=${encodeURIComponent(this.shopName || '')}&am=${amount}&cu=INR`;
            return `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(upiLink)}`;
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

        init() {
            this.searchCustomers();
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
