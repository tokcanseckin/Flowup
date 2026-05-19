"""
Paddle Billing Configuration
Maps Paddle price IDs to subscription tiers.
"""

# Price ID → Subscription Tier mapping
# Updated from Paddle API on 2026-05-19
PRICE_TO_TIER = {
    # Recurring subscriptions
    "pri_01ks0ddf0xdr1tskvcfr7n0dxq": "premium",        # Monthly (8.00 EUR/month)
    "pri_01ks0dfv5dxxeehzn75fs8nwa6": "premium",        # Annual (80.00 EUR/year)
    
    # One-time purchase
    "pri_01ks0dhetq3zrfm9cg43y64fhy": "lifetime",       # One-time (199.00 EUR)
}

# Product information for frontend
PADDLE_PRODUCTS = {
    "product_id": "pro_01ks0da8wzbzv9c7yta9dnm21k",
    "name": "SingoLing",
    "prices": {
        "monthly": {
            "id": "pri_01ks0ddf0xdr1tskvcfr7n0dxq",
            "amount": 800,  # cents
            "currency": "EUR",
            "display": "€8.00",
        },
        "annual": {
            "id": "pri_01ks0dfv5dxxeehzn75fs8nwa6",
            "amount": 8000,  # cents
            "currency": "EUR",
            "display": "€80.00",
        },
        "lifetime": {
            "id": "pri_01ks0dhetq3zrfm9cg43y64fhy",
            "amount": 19900,  # cents
            "currency": "EUR",
            "display": "€199.00",
        },
    }
}


def get_tier_for_price(price_id: str) -> str:
    """
    Returns the subscription tier for a given Paddle price ID.
    Defaults to 'premium' if price_id is not found.
    """
    return PRICE_TO_TIER.get(price_id, "premium")
