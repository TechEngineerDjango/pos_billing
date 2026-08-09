// Initialize Alpine feature store BEFORE posApp() runs
document.addEventListener('alpine:init', () => {
    const raw = JSON.parse(document.getElementById('pos-features-data')?.textContent || '{}');
    // Normalize: FeatureService returns {KEY: {enabled: bool, ...}} — flatten to {KEY: bool}
    const flat = {};
    for (const [k, v] of Object.entries(raw)) {
        flat[k] = (typeof v === 'object' && v !== null) ? (v.enabled === true) : Boolean(v);
    }
    Alpine.store('features', flat);
});

/**
 * Burger POS - Industrial Service-Oriented Architecture
 * Layers: Infrastructure (API) -> Domain (Logic) -> Controller (Alpine)
 * Note: bill.timestamp_display is pre-formatted server-side in the shop's configured timezone
 */

// --- 1. INFRASTRUCTURE LAYER: BILLING API (built on the shared ApiClient) ---
const BillingApi = {
    async searchCustomer(query, signal) {
        return ApiClient.request(`/billing/customer/search?query=${encodeURIComponent(query)}`, { signal });
    },

    async createBill(payload) {
        return ApiClient.request('/billing/create', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    },

    async updateBill(slug, payload) {
        return ApiClient.request(`/billing/update/${slug}`, {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    },

    async fetchRecentBills() {
        return ApiClient.request('/billing/recent-bills');
    },

    async fetchOrders() {
        return ApiClient.request('/billing/orders?limit=30');
    },

    async updateOrder(slug, payload) {
        return ApiClient.request(`/billing/orders/${slug}/update`, {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    }
};

// --- 2. DOMAIN LAYER: BUSINESS SERVICES ---
const CartService = {
    addItem(cart, product) {
        // Unit-based items (kg/g/liter/ml) never merge — each weighed qty is a separate line
        if (product.unit && product.unit !== 'piece') {
            return [...cart, { ...product }];
        }
        const existingIndex = cart.findIndex(i => i.id === product.id && (!i.unit || i.unit === 'piece'));
        if (existingIndex !== -1) {
            return cart.map((item, index) =>
                index === existingIndex ? { ...item, qty: item.qty + 1 } : item
            );
        }
        return [...cart, { ...product, qty: 1 }];
    },

    calculateSubtotal(cart) {
        return cart.reduce((sum, item) => sum + (item.price * item.qty), 0);
    },

    calculateTax(cart) {
        return cart.reduce((sum, item) => {
            const taxRate = parseFloat(item.tax_rate) || 0;
            return sum + (item.price * item.qty) * (taxRate / 100);
        }, 0);
    },

    calculateTotal(cart) {
        return this.calculateSubtotal(cart) + this.calculateTax(cart);
    }
};

const CashService = {
    DENOMINATIONS: [500, 200, 100, 50, 20, 10, 5, 2, 1],

    getDenominations(changeAmount) {
        let remaining = Math.round(changeAmount);
        if (remaining <= 0) return [];

        return this.DENOMINATIONS.map(d => {
            const count = Math.floor(remaining / d);
            remaining %= d;
            return count > 0 ? { value: d, count } : null;
        }).filter(Boolean);
    }
};

// --- 3. CONTROLLER LAYER: ALPINE COMPONENT ---
function posApp() {
    // Load static data from islands
    const itemsData = JSON.parse(document.getElementById('pos-items-data').textContent);
    const features = JSON.parse(document.getElementById('pos-features-data')?.textContent || '{}');
    // Currency symbol from server — safe, from DB shop config
    const currencySymbol = JSON.parse(document.getElementById('pos-currency-data')?.textContent || '"₹"');
    const shopData = JSON.parse(document.getElementById('pos-shop-data')?.textContent || '{}');

    return {
        // --- View State ---
        itemsData,
        features,
        loading: false,
        cart: [],
        heldCartItems: [],
        mobileCartOpen: false,
        selectedCategory: 'All',
        paymentMethod: 'Cash',
        cashReceived: 0,
        deliveryCharge: 0,
        isMobile: window.innerWidth < 1024, // Evaluated synchronously before DOM parse
        toastMessage: '',
        toastVisible: false,
        _toastTimer: null,
        shopName: shopData.name || '',
        shopUpiId: shopData.upi_id || '',
        shopCountryCode: shopData.country_code,
        currentBillId: null,

        // Customer State
        customerPhone: '',
        customerName: '',
        customerInfo: '',
        foundCustomer: null, // Resolves 'foundCustomer is not defined'
        customerId: null,
        isNewCustomer: false,

        // Country dial-code for customer phone (picker UI lives in the countryPicker Alpine component)
        customerCountryCode: shopData.country_code,

        // State
        searchQuery: '',
        activeCategory: 'all',
        _searchTimeout: null,
        _searchToken: 0,
        _searchAbortController: null,  // Holds the active AbortController for customer search
        _resizeHandler: null,

        recentBillsModal: { open: false, loading: false, bills: [] },
        ordersModal: { open: false, loading: false, orders: [] },
        billViewModal: { open: false, order: null },

        // --- Lifecycle ---
        init() {
            this.itemsData = typeof itemsData !== 'undefined' ? itemsData : [];
            this.setupResizeListener();

            this.$nextTick(() => {
                this.isMobile = window.innerWidth < 1024;
            });
        },

        destroy() {
            window.removeEventListener('resize', this._resizeHandler);
            if (this._searchTimeout) clearTimeout(this._searchTimeout);
            if (this._searchAbortController) this._searchAbortController.abort();
        },

        setupResizeListener() {
            this._resizeHandler = () => { this.isMobile = window.innerWidth < 1024; };
            window.addEventListener('resize', this._resizeHandler);
        },

        // --- Computed Properties ---
        get categories() {
            // Extracts unique categories from itemsData
            const cats = this.itemsData.map(i => i.category.toUpperCase());
            return [...new Set(cats)];
        },
        // Search matches name or SKU and overrides category filtering; empty search falls back to category tabs
        isItemVisible(id, category) {
            const q = this.searchQuery.trim().toLowerCase();
            if (q) {
                const item = this.itemsData.find(i => i.id === id);
                return !!item && (
                    item.name.toLowerCase().includes(q) ||
                    (item.sku || '').toLowerCase().includes(q)
                );
            }
            return this.selectedCategory === 'All' || category.toUpperCase() === this.selectedCategory.toUpperCase();
        },
        get hasSearchResults() {
            const q = this.searchQuery.trim().toLowerCase();
            if (!q) return true;
            return this.itemsData.some(i =>
                i.name.toLowerCase().includes(q) || (i.sku || '').toLowerCase().includes(q)
            );
        },
        getStock(id) {
            const item = this.itemsData.find(i => i.id === id);
            return item && item.available_stock !== null && item.available_stock !== undefined
                ? item.available_stock : null;
        },
        getCartQty(id) {
            return this.cart.filter(i => i.id === id).reduce((sum, i) => sum + i.qty, 0);
        },
        isOutOfStock(id) {
            const available = this.getAvailableStock(id);
            if (available === null) return false; // not tracked
            return available <= 0;
        },
        getMaxAllowedStock(id) {
            const stock = this.getStock(id);
            if (stock === null) return null;

            let heldQty = 0;
            if (this.currentBillId && this.heldCartItems.length > 0) {
                const heldItem = this.heldCartItems.find(i => i.id === id);
                if (heldItem) {
                    heldQty = heldItem.qty;
                }
            }
            return stock + heldQty;
        },
        getAvailableStock(id) {
            const maxAllowed = this.getMaxAllowedStock(id);
            if (maxAllowed === null) return null;
            return maxAllowed - this.getCartQty(id);
        },
        get hasCart() {
            return this.cart.length > 0;
        },
        get subtotalPrice() {
            const trigger = this.cart;
            return CartService.calculateSubtotal(trigger);
        },
        get taxAmount() {
            const trigger = this.cart;
            return CartService.calculateTax(trigger);
        },
        get totalPrice() {
            const trigger = this.cart; // Explicit reactivity hook
            return CartService.calculateTotal(trigger) + Math.max(0, this.deliveryCharge || 0);
        },
        get changeAmount() {
            const total = this.totalPrice;
            const received = this.cashReceived;
            return received - total;
        },
        get changeDenominations() {
            if (this.changeAmount <= 0) return [];
            return CashService.getDenominations(this.changeAmount);
        },
        get showSidebar() {
            return !this.isMobile || this.mobileCartOpen;
        },
        get showFloatingCart() {
            return this.isMobile && !this.mobileCartOpen;
        },
        get isCash() {
            return this.paymentMethod === 'Cash';
        },

        // Unit quantity modal state
        unitModal: {
            open: false,
            item: null,
            cartIndex: null,   // null = new item, number = editing existing
            inputQty: '',
        },

        // --- Actions ---
        // Route item click: fixed → add directly, unit-based → open qty modal
        openItem(id) {
            if (this.features['inventory_management'] && this.isOutOfStock(id)) {
                alert("This item is out of stock.");
                return;
            }
            const item = this.itemsData.find(i => i.id === id);
            if (!item) return;
            const isUnitBased = item.unit && item.unit !== 'piece';
            if (isUnitBased) {
                this.openUnitModal({ id: item.id, name: item.name, price: parseFloat(item.price), unit: item.unit, tax_rate: item.tax_rate || 0.0 }, null);
            } else {
                this.addToCartById(id);
            }
        },

        // Open modal for a new unit item (cartIndex=null) or editing existing (cartIndex=number)
        openUnitModal(item, cartIndex) {
            this.unitModal = {
                open: true,
                item: { ...item },
                cartIndex,
                inputQty: cartIndex !== null ? String(this.cart[cartIndex].qty) : '',
            };
        },

        confirmUnitQty() {
            let qty = parseFloat(this.unitModal.inputQty);
            if (!qty || qty <= 0) return;

            // Enforce integer for piece items
            if (!this.unitModal.item.unit || this.unitModal.item.unit === 'piece') {
                qty = Math.floor(qty);
            }

            if (this.features['inventory_management']) {
                const maxAllowed = this.getMaxAllowedStock(this.unitModal.item.id);
                if (maxAllowed !== null) {
                    const otherLinesQty = this.unitModal.cartIndex !== null ?
                        (this.getCartQty(this.unitModal.item.id) - this.cart[this.unitModal.cartIndex].qty) :
                        this.getCartQty(this.unitModal.item.id);

                    if (otherLinesQty + qty > maxAllowed) {
                        const remainingAllowed = maxAllowed - otherLinesQty;
                        alert(`Cannot increase quantity. Maximum allowed is ${remainingAllowed}.`);
                        return;
                    }
                }
            }

            if (this.unitModal.cartIndex !== null) {
                // Edit existing cart line
                this.cart = this.cart.map((c, i) =>
                    i === this.unitModal.cartIndex ? { ...c, qty } : c
                );
            } else {
                // Add new line
                this.cart = CartService.addItem(this.cart, { ...this.unitModal.item, qty });
            }
            this.unitModal = { open: false, item: null, cartIndex: null, inputQty: '' };
            if (!this.isMobile) this.showSidebarForced = true;
        },

        addToCartById(id) {
            const item = this.itemsData.find(i => i.id === id);
            if (item) {
                this.cart = CartService.addItem(this.cart, {
                    id: item.id,
                    name: item.name,
                    price: parseFloat(item.price),
                    unit: item.unit || null,
                    tax_rate: item.tax_rate || 0.0
                });
            }
        },

        changeQty(index, delta) {
            const currentItem = this.cart[index];
            const newQty = currentItem.qty + delta;
            if (newQty <= 0) {
                this.removeFromCart(index);
                return;
            }
            this.setExactQty(index, newQty.toString());
        },

        setExactQty(index, newQtyStr) {
            let qty = parseFloat(newQtyStr);
            if (!qty || qty <= 0) {
                if (newQtyStr !== "" && qty <= 0) {
                    this.removeFromCart(index);
                } else {
                    this.cart = [...this.cart]; // Force re-render to reset input value
                }
                return;
            }

            const currentItem = this.cart[index];
            if (!currentItem.unit || currentItem.unit === 'piece') {
                qty = Math.floor(qty);
            }

            if (this.features['inventory_management']) {
                const maxAllowed = this.getMaxAllowedStock(currentItem.id);
                if (maxAllowed !== null) {
                    const otherLinesQty = this.getCartQty(currentItem.id) - currentItem.qty;
                    if (otherLinesQty + qty > maxAllowed) {
                        const remainingAllowed = maxAllowed - otherLinesQty;
                        alert(`Cannot increase quantity. Maximum allowed is ${remainingAllowed}.`);
                        this.cart = [...this.cart]; // Force re-render to reset input value
                        return;
                    }
                }
            }

            this.cart = this.cart.map((c, i) => i === index ? { ...c, qty } : c);
        },

        handleScanResult(sku) {
            // Lookup SKU
            fetch(`/billing/item-by-sku/${encodeURIComponent(sku)}`)
                .then(r => {
                    if (!r.ok) throw new Error("Not Found");
                    return r.json();
                })
                .then(item => {
                    // Find item in itemsData to get actual ID mapped in pos
                    const posItem = this.itemsData.find(i => i.id === item.id);
                    if (posItem) {
                        this.openItem(posItem.id);
                    } else {
                        alert(`Item '${item.name}' found but not loaded in current menu.`);
                    }
                })
                .catch(() => alert(`SKU '${sku}' not found or active.`));
        },

        removeFromCart(index) {
            this.cart = this.cart.filter((_, idx) => idx !== index);
            if (!this.hasCart) this.mobileCartOpen = false;
        },

        clearCart() {
            this.cart = [];
            this.mobileCartOpen = false;
            this.cashReceived = 0;
            this.deliveryCharge = 0;
            this.paymentMethod = 'Cash';
            this.customerPhone = '';
            this.customerName = '';
            this.customerInfo = '';
            this.customerId = null;
            this.foundCustomer = null;
            this.currentBillId = null;
        },

        // --- Async Workflows ---
        async searchCustomer() {
            const token = ++this._searchToken;
            if (this._searchTimeout) clearTimeout(this._searchTimeout);

            // Abort any in-flight request immediately — prevents stale responses
            // from overwriting UI state when the user types faster than the server responds.
            if (this._searchAbortController) {
                this._searchAbortController.abort();
                this._searchAbortController = null;
            }

            if (this.customerPhone.length < 3) {
                this.resetCustomer();
                return;
            }

            this._searchTimeout = setTimeout(async () => {
                try {
                    const controller = new AbortController();
                    this._searchAbortController = controller;  // Store so it can be aborted by the next keystroke

                    const data = await BillingApi.searchCustomer(this.customerPhone, controller.signal);

                    // Only apply result if this is still the latest token (debounce guard)
                    if (token === this._searchToken && data) {
                        this._searchAbortController = null;
                        this.handleCustomerFound(data);
                    }
                } catch (e) {
                    if (e.name !== 'AbortError') {
                        // Silently ignore intentional aborts; log genuine failures
                        console.error('Customer search failed', e);
                    }
                }
            }, 300);
        },

        // Forces the debounced customer lookup to settle immediately instead of
        // waiting out its 300ms timer — called before Pay Later submissions so
        // foundCustomer (and the "Available credit" hint) reflects the phone
        // number actually being submitted, not whatever was found last.
        async ensureCustomerLookupSettled() {
            if (this._searchTimeout) {
                clearTimeout(this._searchTimeout);
                this._searchTimeout = null;
            }
            if (this.customerPhone.length < 3) return;
            if (this._searchAbortController) {
                this._searchAbortController.abort();
                this._searchAbortController = null;
            }
            try {
                const controller = new AbortController();
                this._searchAbortController = controller;
                const token = ++this._searchToken;
                const data = await BillingApi.searchCustomer(this.customerPhone, controller.signal);
                if (token === this._searchToken && data) {
                    this._searchAbortController = null;
                    this.handleCustomerFound(data);
                }
            } catch (e) {
                if (e.name !== 'AbortError') {
                    console.error('Customer search failed', e);
                }
            }
        },

        handleCustomerFound(data) {
            if (data.found) {
                this.foundCustomer = data; // Satisfies template expressions
                this.customerInfo = `✓ ${data.name}`;
                this.customerId = data.id;
                this.customerName = data.name;
                this.isNewCustomer = false;
            } else {
                this.foundCustomer = null;
                this.customerInfo = '+ New customer';
                this.customerId = null;
                this.customerName = '';
                this.isNewCustomer = true;
            }
            // Pay Later (payment method 'Credit' internally) is available for any
            // customer, not just registered credit accounts — only the credit-limit
            // check in validateSubmission cares about is_credit_customer.
        },

        selectCustomer() {
            if (this.foundCustomer) {
                this.customerPhone = this.foundCustomer.phone_number;
                this.customerName = this.foundCustomer.name;
                this.customerId = this.foundCustomer.id;
                this.isNewCustomer = false;
                this.customerInfo = `✓ ${this.foundCustomer.name}`;
            }
        },

        resetCustomer() {
            this.customerInfo = '';
            this.customerId = null;
            this.isNewCustomer = false;
            this.foundCustomer = null;
            if (this.paymentMethod === 'Credit') {
                this.paymentMethod = 'Cash';
            }
        },

        async submitBill(status = "Completed") {
            if (!this.validateSubmission()) return;

            // Open the WhatsApp tab synchronously, tied to this click, before ANY
            // await below (including the credit-lookup refresh) can burn through
            // the browser's brief window for allowing window.open() without it
            // being treated as a blocked popup. We redirect this tab once the
            // bill exists.
            //
            // Opening a new tab backgrounds this one, and browsers throttle
            // pending work (including in-flight requests settling) in
            // backgrounded tabs — most noticeably on mobile. That can add a
            // real, sometimes multi-second delay before the redirect below
            // fires. A pure about:blank tab during that wait looks broken, so
            // we write a visible "preparing" message into it immediately.
            let waWindow = null;
            const wantsWhatsApp = status !== 'Held' && this.customerPhone
                && this.customerPhone.trim().length >= 10 && this.features['whatsapp_billing'];
            if (wantsWhatsApp) {
                waWindow = window.open('', '_blank');
                if (waWindow) {
                    waWindow.document.title = 'Opening WhatsApp…';
                    Object.assign(waWindow.document.body.style, {
                        fontFamily: 'sans-serif', display: 'flex', alignItems: 'center',
                        justifyContent: 'center', height: '100vh', margin: '0',
                        color: '#333', textAlign: 'center', padding: '0 2rem'
                    });
                    const msg = waWindow.document.createElement('p');
                    msg.innerHTML = 'Preparing your WhatsApp message…<br>This tab will redirect automatically.';
                    waWindow.document.body.appendChild(msg);
                }
            }

            // Locks the submit/hold buttons immediately, synchronously — before
            // any await — so a fast double-tap can't re-enter this function and
            // create two bills.
            this.loading = true;
            try {
                if (this.paymentMethod === 'Credit') {
                    await this.ensureCustomerLookupSettled();
                }
                const payload = this.preparePayload();
                payload.status = status;

                let data;
                if (this.currentBillId) {
                    data = await BillingApi.updateBill(this.currentBillId, payload);
                } else {
                    data = await BillingApi.createBill(payload);
                }

                this.handleSuccess(data, status, waWindow);
            } catch (e) {
                if (waWindow) waWindow.close();
                alert(`Error: ${e.message}`);
            } finally {
                this.loading = false;
            }
        },

        validateSubmission() {
            if (!this.hasCart) return false;
            // If cashier hasn't entered a cash amount, default to exact total (most common case)
            if (this.isCash && this.cashReceived === 0) {
                this.cashReceived = this.totalPrice;
            }
            if (this.isCash && this.cashReceived < this.totalPrice) {
                alert('Cash received is less than the total amount');
                return false;
            }
            if (this.isNewCustomer && !this.customerName) {
                alert('Please enter customer name for new customer');
                return false;
            }
            if (this.paymentMethod === 'Credit' && !this.customerPhone) {
                alert('Pay Later requires a customer phone number');
                return false;
            }
            return true;
        },

        preparePayload() {
            return {
                items: this.cart.map(i => ({ id: i.id, qty: i.qty })),
                payment_method: this.paymentMethod,
                customer_phone: this.customerPhone,
                customer_name: this.customerName,
                customer_country_code: this.customerCountryCode,
                delivery_charge: Math.max(0, this.deliveryCharge || 0)
            };
        },

        handleSuccess(data, status, waWindow = null) {
            const phone = this.customerPhone;
            const billNum = data.bill_number;
            const billSlug = data.bill_id;

            // Capture cart state before clearing it
            const soldItems = this.cart.map(item => ({ id: item.id, qty: item.qty }));

            this.heldCartItems = [];
            this.clearCart();

            // Apply backend-provided stock values (single source of truth)
            if (data.updated_items && data.updated_items.length > 0) {
                this.itemsData = this.itemsData.map(item => {
                    const updated = data.updated_items.find(u => u.id === item.id);
                    return updated ? { ...item, available_stock: updated.available_stock } : item;
                });
            }

            if (status === 'Held') {
                if (waWindow) waWindow.close();
                this.showToast(`⏸️ Bill #${billNum} placed on Hold!`);
                return;
            }

            if (phone && phone.trim().length >= 10 && this.features['whatsapp_billing']) {
                this.sendWhatsAppMessage(phone.trim(), billSlug, waWindow);
            } else if (waWindow) {
                waWindow.close();
            }

            // A credit-limit warning (bill still succeeded) needs more than the
            // usual 2.5s toast to actually be read — longer duration, no blocking
            // alert() (one right here would risk the same popup-blocking issue
            // window.open()/WhatsApp already works around elsewhere in this flow).
            if (data.warning) {
                this.showToast(`Bill #${billNum} created. ${data.warning}`, 6000);
            } else {
                this.showToast(`Bill #${billNum} created successfully!`);
            }
        },

        // Non-blocking success notification — a native alert() forces the
        // cashier to click OK on every single sale, which doesn't scale at
        // real transaction volume. This auto-dismisses instead.
        showToast(message, duration = 2500) {
            clearTimeout(this._toastTimer);
            this.toastMessage = message;
            this.toastVisible = true;
            this._toastTimer = setTimeout(() => { this.toastVisible = false; }, duration);
        },

        async openRecentBills() {
            this.recentBillsModal.open = true;
            this.recentBillsModal.loading = true;
            try {
                const res = await BillingApi.fetchRecentBills();
                this.recentBillsModal.bills = res.bills || [];
            } catch(e) {
                console.error(e);
            } finally {
                this.recentBillsModal.loading = false;
            }
        },

        resumeBill(bill) {
            this.heldCartItems = [];
            this.clearCart();
            this.currentBillId = bill.id;
            this.cart = bill.items.map(i => ({...i}));
            // Store the original held items so the dynamic stock calculation doesn't double-deduct
            this.heldCartItems = bill.items.map(i => ({...i}));
            this.paymentMethod = bill.payment_method || 'Cash';
            this.deliveryCharge = bill.delivery_charge || 0;
            if (bill.customer_phone) {
                this.customerPhone = bill.customer_phone;
                this.searchCustomer();
            }

            this.recentBillsModal.open = false;
            // On mobile the cart/checkout panel is collapsed by default behind
            // the floating cart button — without this, editing a held bill
            // silently loads it into the cart but leaves the item grid on
            // screen, looking like nothing happened.
            if (this.isMobile) this.mobileCartOpen = true;
        },

        async cancelHeldBill(bill) {
            if (!confirm(`Are you sure you want to cancel the held bill #${bill.bill_number}? This will return the items to stock.`)) {
                return;
            }

            try {
                this.recentBillsModal.loading = true;
                const response = await ApiClient.request(`/billing/cancel/${bill.id}`, { method: 'POST' });
                alert(response.message || "Bill cancelled");

                // Apply backend-provided stock values (single source of truth)
                if (response.updated_items && response.updated_items.length > 0) {
                    this.itemsData = this.itemsData.map(item => {
                        const updated = response.updated_items.find(u => u.id === item.id);
                        return updated ? { ...item, available_stock: updated.available_stock } : item;
                    });
                }

                // Remove cancelled bill from the modal list
                this.recentBillsModal.bills = this.recentBillsModal.bills.filter(b => b.id !== bill.id);
                this.recentBillsModal.loading = false;
            } catch(e) {
                alert(`Error: ${e.message}`);
                this.recentBillsModal.loading = false;
            }
        },

        async openOrders() {
            this.ordersModal.open = true;
            this.ordersModal.loading = true;
            try {
                const res = await BillingApi.fetchOrders();
                this.ordersModal.orders = res.orders || [];
            } catch(e) {
                console.error(e);
            } finally {
                this.ordersModal.loading = false;
            }
        },

        // POS shortcut is a "what needs attention right now" quick view — once
        // an order is delivered AND paid it's done and drops out of the list
        // (the full history stays in the Dashboard Orders tab). Reactive so an
        // order disappears the moment its last status is set from this modal.
        get visibleOrders() {
            return this.ordersModal.orders.filter(
                o => !(o.delivery_status === 'Delivered' && o.payment_status === 'Paid')
            );
        },

        nextDeliveryStatus(current) {
            const stages = ['Pending', 'Out for Delivery', 'Delivered'];
            const idx = stages.indexOf(current);
            return stages[Math.min(idx + 1, stages.length - 1)];
        },

        openOrderView(order) {
            this.billViewModal.order = order;
            this.billViewModal.open = true;
        },

        upiQrUrl(order) {
            const amount = (Number(order.total_amount) - Number(order.amount_paid || 0)).toFixed(2);
            return upiQrDataUrl(this.shopUpiId, this.shopName, amount);
        },

        async advanceDeliveryStatus(order) {
            const next = this.nextDeliveryStatus(order.delivery_status);
            if (next === order.delivery_status) return;
            try {
                const res = await BillingApi.updateOrder(order.slug, { delivery_status: next });
                order.delivery_status = res.order.delivery_status;
            } catch(e) {
                alert(`Error: ${e.message}`);
            }
        },

        _idempotencyKey() {
            if (window.crypto && typeof window.crypto.randomUUID === 'function') {
                return window.crypto.randomUUID();
            }
            return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        },

        async markOrderPaid(order) {
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
                    const res = await BillingApi.updateOrder(order.slug, { mark_paid: true });
                    order.payment_status = res.order.payment_status;
                    order.amount_paid = res.order.amount_paid;
                }
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        },

        // Manual correction — set delivery/payment status to any value directly,
        // for undoing an accidental tap rather than only stepping forward.
        async setOrderDeliveryStatus(order, status) {
            if (status === order.delivery_status) return;
            try {
                const res = await BillingApi.updateOrder(order.slug, { delivery_status: status });
                order.delivery_status = res.order.delivery_status;
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        },

        async setOrderPaymentStatus(order, status) {
            if (status === order.payment_status) return;
            if (order.is_credit_customer) {
                alert('This order belongs to a credit account — record/adjust payments from the Dashboard Credit Book instead.');
                return;
            }
            try {
                const res = await BillingApi.updateOrder(order.slug, { payment_status: status });
                order.payment_status = res.order.payment_status;
                order.amount_paid = res.order.amount_paid;
            } catch (e) {
                alert(`Error: ${e.message}`);
            }
        },

        resendWhatsapp(bill) {
            let phone = bill.customer_phone;
            if (!phone) {
                phone = prompt("Enter customer WhatsApp number:", "");
            }
            if (phone && phone.trim().length >= 10) {
                this.sendWhatsAppMessage(phone.trim(), bill.id);
            }
        },

        resendOrderWhatsapp(order) {
            let phone = order.customer_phone;
            if (!phone) {
                phone = prompt("Enter customer WhatsApp number:", "");
            }
            if (phone && phone.trim().length >= 10) {
                this.sendWhatsAppMessage(phone.trim(), order.slug);
            }
        },

        sendWhatsAppMessage(phone, billSlug, waWindow = null) {
            const cleanPhone = phone.replace(/\D/g, '');
            if (!cleanPhone) {
                if (waWindow) waWindow.close();
                return;
            }
            // Use backend route to generate consistent, DRY WhatsApp message format.
            // Fully-qualified (not relative) because this can be assigned to a
            // pre-opened window's .location.href, and relative-URL resolution
            // against a foreign about:blank window's inherited origin isn't
            // consistent across browser engines.
            const url = `${window.location.origin}/admin/bill/${encodeURIComponent(billSlug)}/whatsapp-redirect?phone=${encodeURIComponent(cleanPhone)}`;
            if (waWindow) {
                waWindow.location.href = url;
            } else {
                window.open(url, '_blank');
            }
        },

        formatMoney(value) {
            return currencySymbol + parseFloat(value).toFixed(2);
        },

        // Whole numbers print as-is; fractional stock (weight/volume-sold items)
        // keeps up to 3 decimal places instead of showing raw JS float noise
        // (e.g. 3.7 - 0.8 === 2.9000000000000004) on the stock badge.
        formatQty(value) {
            if (value === null || value === undefined || Number.isNaN(Number(value))) return '';
            const n = Number(value);
            if (Number.isInteger(n)) return String(n);
            const s = n.toFixed(3).replace(/\.?0+$/, '');
            return s === '-0' ? '0' : s; // toFixed(3) on a tiny negative (e.g. -0.0001) rounds to "-0.000"
        }
    };
}
