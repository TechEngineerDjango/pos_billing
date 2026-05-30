# Feature definitions for subscription-based gating
# Each feature has a key (used in code), name (display), description, and tier

FEATURES = {
    # Core POS Features (Free tier)
    "pos_basic": {
        "name": "Basic POS",
        "description": "Core point-of-sale functionality",
        "tier": "free",
        "category": "pos"
    },
    "menu_management": {
        "name": "Menu Management",
        "description": "Add, edit, and delete menu items",
        "tier": "free",
        "category": "pos"
    },
    
    # Business Features (Basic tier)
    "cash_calculator": {
        "name": "Cash Change Calculator",
        "description": "Calculate change for cash payments",
        "tier": "basic",
        "category": "billing"
    },
    "customer_management": {
        "name": "Customer Management",
        "description": "Track and manage customer information",
        "tier": "basic",
        "category": "crm"
    },
    "sales_reports": {
        "name": "Sales Reports",
        "description": "View sales history and reports",
        "tier": "basic",
        "category": "reports"
    },
    "staff_management": {
        "name": "Staff Management",
        "description": "Add and manage staff accounts",
        "tier": "basic",
        "category": "admin"
    },
    
    # Pro Features (Pro tier)
    "whatsapp_billing": {
        "name": "WhatsApp Billing",
        "description": "Send bills via WhatsApp",
        "tier": "pro",
        "category": "billing"
    },
    "branding_studio": {
        "name": "Branding Studio",
        "description": "Full customization of POS appearance",
        "tier": "pro",
        "category": "customization"
    },
    "custom_logo": {
        "name": "Custom Logo",
        "description": "Upload and display custom shop logo",
        "tier": "pro",
        "category": "customization"
    },
    "advanced_analytics": {
        "name": "Advanced Analytics",
        "description": "Detailed sales analytics and insights",
        "tier": "pro",
        "category": "reports"
    },
    "inventory_management": {
        "name": "Inventory Management",
        "description": "Stock tracking, low stock alerts, QR/barcode scanner for restocking and billing",
        "tier": "pro",
        "category": "ops"
    },
    
    # Enterprise Features
    "multi_location": {
        "name": "Multi-Location Support",
        "description": "Manage multiple shop locations",
        "tier": "enterprise",
        "category": "admin"
    },
    "api_access": {
        "name": "API Access",
        "description": "REST API access for integrations",
        "tier": "enterprise",
        "category": "integrations"
    },
    "priority_support": {
        "name": "Priority Support",
        "description": "24/7 priority customer support",
        "tier": "enterprise",
        "category": "support"
    },
}

# Tier hierarchy - higher tiers include all features from lower tiers
TIER_HIERARCHY = {
    "free": 0,
    "basic": 1,
    "pro": 2,
    "enterprise": 3
}

# Default features for each tier
DEFAULT_TIER_FEATURES = {
    "free": ["pos_basic", "menu_management"],
    "basic": ["pos_basic", "menu_management", "cash_calculator", "customer_management", "sales_reports", "staff_management"],
    "pro": ["pos_basic", "menu_management", "cash_calculator", "customer_management", "sales_reports", "staff_management",
            "whatsapp_billing", "branding_studio", "custom_logo", "advanced_analytics", "inventory_management"],
    "enterprise": list(FEATURES.keys())  # All features
}


def has_feature(shop, feature_key: str) -> bool:
    """Check if a shop has access to a specific feature based on subscription."""
    if not shop:
        return False
    
    # If no subscription, only free tier features
    if not shop.subscription:
        return feature_key in DEFAULT_TIER_FEATURES.get("free", [])
    
    # Check if feature is in the subscription's enabled features
    # Support both clean lists and legacy comma-separated strings
    raw_features = shop.subscription.enabled_features or []
    enabled_features = set()
    for f in raw_features:
        if isinstance(f, str) and "," in f:
            for part in f.split(","):
                if part.strip(): enabled_features.add(part.strip())
        elif isinstance(f, str):
            if f.strip(): enabled_features.add(f.strip())
            
    return feature_key in enabled_features


def get_shop_features(shop) -> dict:
    """Get all features with their enabled status for a shop."""
    result = {}
    for key, feature in FEATURES.items():
        result[key] = {
            **feature,
            "key": key,
            "enabled": has_feature(shop, key)
        }
    return result


def get_available_features() -> list:
    """Get list of all available features for subscription configuration."""
    return [{"key": k, **v} for k, v in FEATURES.items()]


def get_features_by_category() -> dict:
    """Group features by category for display."""
    categories = {}
    for key, feature in FEATURES.items():
        cat = feature.get("category", "other")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({"key": key, **feature})
    return categories
