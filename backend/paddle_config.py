"""
Paddle Billing Configuration
Maps Paddle price IDs to subscription tiers and provides dynamic pricing.
"""

# Price ID → Subscription Tier mapping
PRICE_TO_TIER = {
    'pri_01ks0ddf0xdr1tskvcfr7n0dxq': 'premium',    # 8.00 EUR/month
    'pri_01ks0dfv5dxxeehzn75fs8nwa6': 'premium',    # 80.00 EUR/year
    'pri_01ks0dhetq3zrfm9cg43y64fhy': 'lifetime',   # 199.00 EUR one-time
}

# Cache for pricing data
_pricing_cache = None
_cache_timestamp = 0
CACHE_TTL = 3600  # 1 hour in seconds


def _fetch_paddle_pricing():
    """Fetch product and price information from Paddle API."""
    import requests
    import os
    from datetime import datetime
    
    api_key = os.environ.get('PADDLE_API_KEY')
    if not api_key:
        print('Warning: PADDLE_API_KEY not set, using hardcoded prices')
        return None
    
    try:
        # Fetch products
        products_response = requests.get(
            'https://sandbox-api.paddle.com/products',
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=10
        )
        products_response.raise_for_status()
        products = products_response.json().get('data', [])
        
        if not products:
            print('Warning: No products found in Paddle')
            return None
        
        # Use first product (SingoLing)
        product = products[0]
        product_id = product['id']
        product_name = product['name']
        
        # Fetch prices for this product
        prices_response = requests.get(
            f'https://sandbox-api.paddle.com/prices',
            params={'product_id': product_id},
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=10
        )
        prices_response.raise_for_status()
        prices = prices_response.json().get('data', [])
        
        # Organize prices by billing cycle
        pricing_data = {
            'product_id': product_id,
            'product_name': product_name,
            'monthly': None,
            'annual': None,
            'lifetime': None,
        }
        
        for price in prices:
            billing_cycle = price.get('billing_cycle')
            if billing_cycle:
                interval = billing_cycle.get('interval')
                if interval == 'month':
                    pricing_data['monthly'] = {
                        'id': price['id'],
                        'amount': int(price['unit_price']['amount']),
                        'currency': price['unit_price']['currency_code'],
                    }
                elif interval == 'year':
                    pricing_data['annual'] = {
                        'id': price['id'],
                        'amount': int(price['unit_price']['amount']),
                        'currency': price['unit_price']['currency_code'],
                    }
            else:
                # One-time payment (lifetime)
                pricing_data['lifetime'] = {
                    'id': price['id'],
                    'amount': int(price['unit_price']['amount']),
                    'currency': price['unit_price']['currency_code'],
                }
        
        print(f'Successfully fetched pricing from Paddle at {datetime.now()}')
        return pricing_data
        
    except Exception as e:
        print(f'Error fetching pricing from Paddle: {e}')
        return None


def get_current_pricing():
    """Get current pricing, using cache if valid or fetching fresh data."""
    global _pricing_cache, _cache_timestamp
    import time
    
    current_time = time.time()
    
    # Return cached data if still valid
    if _pricing_cache and (current_time - _cache_timestamp) < CACHE_TTL:
        return _pricing_cache
    
    # Fetch fresh data
    pricing_data = _fetch_paddle_pricing()
    
    if pricing_data:
        _pricing_cache = pricing_data
        _cache_timestamp = current_time
        return pricing_data
    
    # Fallback to hardcoded prices if API fails
    fallback = {
        'product_id': 'pro_01ks0da8wzbzv9c7yta9dnm21k',
        'product_name': 'SingoLing',
        'monthly': {
            'id': 'pri_01ks0ddf0xdr1tskvcfr7n0dxq',
            'amount': 800,
            'currency': 'EUR',
        },
        'annual': {
            'id': 'pri_01ks0dfv5dxxeehzn75fs8nwa6',
            'amount': 8000,
            'currency': 'EUR',
        },
        'lifetime': {
            'id': 'pri_01ks0dhetq3zrfm9cg43y64fhy',
            'amount': 19900,
            'currency': 'EUR',
        },
    }
    
    # Cache the fallback too
    _pricing_cache = fallback
    _cache_timestamp = current_time
    
    return fallback


def get_tier_for_price(price_id: str) -> str:
    """
    Returns the subscription tier for a given Paddle price ID.
    Defaults to 'premium' if price_id is not found.
    """
    return PRICE_TO_TIER.get(price_id, 'premium')
