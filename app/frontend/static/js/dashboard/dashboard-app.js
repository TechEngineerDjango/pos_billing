function dashboardApp() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('error')) {
        alert(params.get('error'));
        // Remove error from URL without reload
        const url = new URL(window.location);
        url.searchParams.delete('error');
        window.history.replaceState({}, '', url);
    }

    return {
        activeTab: params.get('tab') || 'overview',
        showAddModal: false,
        showStaffModal: false,
        showCustomerModal: false,

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
