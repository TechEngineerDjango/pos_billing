function superAdminApp() {
    return {
        activeTab: new URLSearchParams(window.location.search).get('tab') || 'fleet',
        selectedShopId: new URLSearchParams(window.location.search).get('shop_id') || '',
        showShopModal: false,
        showPlanModal: false,
        showUserModal: false,
        showInvoiceModal: false,
        currentInvoice: null,
        saving: false,
        billingData: { items: [], total: 0, page: 1, pages: 1, size: 10 },
        billingFilters: { search: '' },

        init() {
            const urlParams = new URLSearchParams(window.location.search);
            const errorMsg = urlParams.get('error');
            if (errorMsg) {
                setTimeout(() => alert(decodeURIComponent(errorMsg)), 100);
            }

            const dataEl = document.getElementById('superadmin-shops-data');
            if (dataEl) {
                try {
                    this.shopsData = JSON.parse(dataEl.textContent);
                } catch(e) {
                    console.error('Failed to parse shops data:', e);
                }
            }

            if (this.selectedShopId) {
                this.onShopSelect();
            }

            if (this.activeTab === 'billing') {
                this.fetchInvoices(1);
            }
        },

        designConfig: {
            id: null,
            name: '',
            accent_color: '#f97316',
            header_color: '#1e293b',
            header_text_color: '#ffffff',
            font_color: '#ffffff',
            background_color: '#0f172a',
            card_bg_color: '#1e293b',
            cart_bg_color: '#0f172a',
            logo_size: 40,
            watermark_opacity: 0.1,
            currency_symbol: '₹',
            nav_font_family: "'Outfit', sans-serif",
            pos_card_width: '100%',
            pos_card_width_num: 100,
            pos_card_height: '200px',
            pos_card_height_num: 200,
            pos_card_image_width: '100%',
            pos_card_image_width_num: 100,
            pos_card_image_height: '8rem',
            pos_card_image_height_num: 8,
            panel_font_color: '#ffffff',
            panel_bg_color: '#1e293b',
            billing_font_color: '#ffffff',
            billing_card_bg_color: '#1e293b',
            billing_card_font_color: '#ffffff',
            inc_dec_button_color: '#f97316',
            cash_upi_option_color: '#1e293b',
            cash_upi_font_color: '#ffffff',
            upi_id: '',
            receipt_footer: '',
            printer_paper_width: '80mm',
            printer_alignment: 'center'
        },
        shopsData: [],
        previewUrl: 'about:blank',


        switchTab(t) {
            this.activeTab = t;
            const url = new URL(window.location);
            url.searchParams.set('tab', t);
            window.history.replaceState({}, '', url);

            if (t === 'billing') {
                this.fetchInvoices(1);
            }
        },

        selectForDesign(shop) {
            this.selectedShopId = shop.id;
            this.onShopSelect();
            this.activeTab = 'design';
        },

        onShopSelect() {
            if (!this.selectedShopId) {
                this.designConfig = { id: null };
                this.previewUrl = 'about:blank';
                return;
            }
            const shop = this.shopsData.find(s => s.id == this.selectedShopId);
            if (shop) {
                this.designConfig = { ...shop };
                // Parse numeric values for sliders
                this.designConfig.pos_card_width_num = parseInt(shop.pos_card_width) || 100;
                this.designConfig.pos_card_height_num = parseInt(shop.pos_card_height) || 200;
                this.designConfig.pos_card_image_width_num = parseInt(shop.pos_card_image_width) || 100;
                this.designConfig.pos_card_image_height_num = parseInt(shop.pos_card_image_height) || 8;
                this.previewUrl = '/billing/?shop_slug=' + shop.id + '&preview=1';
            }
        },

        // Live preview - sends updates to iframe
        liveUpdate() {
            if (!this.$refs.previewFrame || !this.$refs.previewFrame.contentWindow) return;
            if (!this.designConfig || !this.designConfig.id) return;

            try {
                const doc = this.$refs.previewFrame.contentDocument || this.$refs.previewFrame.contentWindow.document;
                if (!doc || !doc.body) return;

                // Target body element since layout.html sets CSS variables there via inline styles
                const target = doc.body;
                const cfg = this.designConfig;

                // Apply CSS variables directly (match layout.html names)
                target.style.setProperty('--bg-color', cfg.background_color);
                target.style.setProperty('--header-color', cfg.header_color);
                target.style.setProperty('--header-text-color', cfg.header_text_color);
                target.style.setProperty('--accent-color', cfg.accent_color);
                target.style.setProperty('--card-bg', cfg.card_bg_color);
                target.style.setProperty('--panel-bg', cfg.panel_bg_color);
                target.style.setProperty('--panel-font', cfg.panel_font_color);
                target.style.setProperty('--cart-bg', cfg.cart_bg_color);
                target.style.setProperty('--sidebar-bg', cfg.sidebar_bg_color);
                target.style.setProperty('--billing-font', cfg.billing_font_color);
                target.style.setProperty('--billing-card-bg', cfg.billing_card_bg_color);
                target.style.setProperty('--billing-card-font', cfg.billing_card_font_color);
                target.style.setProperty('--inc-dec-btn', cfg.inc_dec_button_color);
                target.style.setProperty('--cash-upi-bg', cfg.cash_upi_option_color);
                target.style.setProperty('--cash-upi-font', cfg.cash_upi_font_color);
                target.style.setProperty('--border-color', cfg.border_color);
                target.style.setProperty('--price-card-bg', cfg.price_card_bg);
                target.style.setProperty('--nav-font', cfg.nav_font_family);
                target.style.setProperty('--pos-card-w', cfg.pos_card_width);
                target.style.setProperty('--pos-card-h', cfg.pos_card_height);
                target.style.setProperty('--pos-card-img-w', cfg.pos_card_image_width);
                target.style.setProperty('--pos-card-img-h', cfg.pos_card_image_height);
                target.style.setProperty('--logo-size', cfg.logo_size + 'px');

            } catch (e) {
                // Cross-origin or not loaded yet - silently fail
                console.log('Live preview not available:', e.message);
            }
        },

        async saveDesign() {
            this.saving = true;
            const formData = new FormData();

            // Append all text fields
            for (const key in this.designConfig) {
                if (this.designConfig[key] !== null && key !== 'logo_url' && key !== 'menu_icon_url' && key !== 'favicon_url') {
                    formData.append(key, this.designConfig[key]);
                }
            }

            // Handle File Upload: Logo
            const fileInput = this.$refs.logoInput;
            if (fileInput && fileInput.files.length > 0) {
                formData.append('logo', fileInput.files[0]);
            }

            // Handle File Upload: Menu Icon
            const menuIconInput = this.$refs.menuIconInput;
            if (menuIconInput && menuIconInput.files.length > 0) {
                formData.append('menu_icon', menuIconInput.files[0]);
            }

            // Handle File Upload: Favicon
            const faviconInput = this.$refs.faviconInput;
            if (faviconInput && faviconInput.files.length > 0) {
                formData.append('favicon', faviconInput.files[0]);
            }

            // NOTE: not routed through ApiClient.request — this endpoint replies with a
            // 303 redirect (HTML), not JSON, and ApiClient.request always parses the body
            // as JSON on success. Still uses the shared CSRF getter to avoid duplicating it.
            try {
                const resp = await fetch('/superadmin/shops/update/' + this.designConfig.id, {
                    method: 'POST',
                    headers: {
                        'x-csrf-token': ApiClient.getCsrfToken()
                    },
                    body: formData
                });
                if (resp.ok) {
                    const idx = this.shopsData.findIndex(s => s.id == this.designConfig.id);
                    if (idx !== -1) {
                        this.shopsData[idx] = { ...this.designConfig };
                    }
                    // Refresh iframe
                    if (this.$refs.previewFrame) {
                        this.$refs.previewFrame.contentWindow.location.reload();
                    }
                    // alert('System visuals updated successfully.'); // Optional: remove alert for smoother flow
                } else {
                    const err = await resp.json();
                    alert('Update failed: ' + (err.detail || 'Internal error'));
                }
            } catch (e) {
                console.error(e);
                alert('Visual sync failed. Check network stability.');
            } finally {
                this.saving = false;
            }
        },

        async fetchInvoices(page = 1) {
            const params = new URLSearchParams();
            params.append('page', page);
            if (this.billingFilters.search) params.append('search', this.billingFilters.search);

            try {
                const resp = await fetch(`/api/platform-billing/shops-status?${params.toString()}`);
                if (resp.ok) {
                    this.billingData = await resp.json();
                } else {
                    console.error('Failed to fetch billing status');
                }
            } catch (e) {
                console.error(e);
            }
        },

        async viewInvoice(invoiceId) {
            if (!invoiceId) return;
            try {
                const resp = await fetch(`/api/platform-billing/invoices/${invoiceId}`);
                if (resp.ok) {
                    this.currentInvoice = await resp.json();
                    this.showInvoiceModal = true;
                } else {
                    alert("Failed to load invoice details.");
                }
            } catch (e) {
                console.error(e);
            }
        },

        async markInvoicePaid(invoiceId) {
            if (!confirm("Are you sure you want to mark this invoice as paid?")) return;
            try {
                await ApiClient.request(`/api/platform-billing/invoices/${invoiceId}/pay`, {
                    method: 'POST'
                });
                this.fetchInvoices(this.billingData.page);
            } catch (e) {
                alert('Error: ' + e.message);
            }
        },

        async sendWhatsAppInvoice(invoiceId) {
            try {
                const data = await ApiClient.request(`/api/platform-billing/invoices/${invoiceId}/notify`, {
                    method: 'POST'
                });
                if (data.whatsapp_url) {
                    window.open(data.whatsapp_url, '_blank');
                } else {
                    alert("Notification logged, but no phone number found for WhatsApp link.");
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }
    };
}
