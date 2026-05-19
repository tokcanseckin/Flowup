# Subscription Layer Architecture

**Status:** Planning  
**Target Launch:** Web (Paddle) in 2 weeks, iOS/Android in 4 weeks  
**Last Updated:** May 19, 2026

---

## Overview

Multi-platform subscription system supporting:
- **Paddle** (web) — primary revenue channel, no Apple/Google cuts
- **Apple In-App Purchase** (iOS) — 30% cut (15% after year 1)
- **Google Play Billing** (Android) — 30% cut (15% after year 1)
- **Cross-platform sync** — subscribe anywhere, access everywhere

---

## Subscription Tiers

### Launch Strategy: Premium-Only

**Build for (future-ready infrastructure):**
1. **Free** — 2 songs per playlist with full features, song 3+ plays music but locks lyrics
2. **Language-Specific** (e.g., "Russian Access") — $4.99/mo
3. **Premium** — all languages + custom playlists — $9.99/mo
4. **Lifetime** — $199 one-time (optional launch offer)

**Launch with:** Premium only ($9.99/mo, $99.99/yr)

**Rationale:**
- Simplest onboarding path
- Filters for serious learners (higher retention)
- Language-specific tiers become upsell/downsell after usage data validates demand
- Infrastructure supports all tiers from day one (no migration pain)

---

## Free Tier: Showcase + Soft Paywall

### What Free Users Get
- Browse all playlists (full catalog visibility)
- **First 2 songs per playlist:** Full interactive experience (lyrics, word inspection, line translation, keyboard shortcuts)
- **Song 3+ in any playlist:** Music plays (YouTube/Apple Music), but lyrics section shows elegant lock screen:
  - Blurred lyrics background OR
  - Sleek overlay: "Unlock unlimited interactive lyrics and translations with SingoLing Premium"
  - Upgrade CTA button
  - Music continues playing normally (100% compliant with YouTube/Apple Music ToS)

### Why This Works
- **Generous trial:** Users experience full value on best 2 songs per playlist
- **Non-disruptive paywall:** Music doesn't stop → no frustration, just FOMO
- **Highlights value prop:** Lyrics/translation layer is the product, not the music access
- **Platform compliant:** YouTube/Apple Music playback never blocked (just the learning layer)
- **Higher conversion:** User hears song #3, wants to engage, hits paywall mid-flow → strong upgrade intent

### Implementation

**Backend logic:**
```python
def can_access_lyrics(user: User, song: Song, playlist: Playlist) -> bool:
    """Determine if user can see interactive lyrics."""
    # Premium tiers: unlimited
    if user.subscription_tier in ['premium', 'lifetime', 'premium_student']:
        if is_subscription_active(user):
            return True
    
    # Language-specific tier
    if user.subscription_tier == song.source_lang:
        if is_subscription_active(user):
            return True
    
    # Free tier: first 2 songs per playlist
    return song.position_in_playlist <= 2

def can_play_music(user: User, song: Song) -> bool:
    """Music playback always allowed (YouTube/Apple Music compliance)."""
    return True  # Never block music playback
```

**API response:**
```python
@app.get("/api/songs/{song_id}/data")
async def get_song_data(song_id: int, current_user: User = Depends(auth)):
    song = db.get_song(song_id)
    playlist = db.get_playlist_for_song(song_id)
    
    lyrics_unlocked = can_access_lyrics(current_user, song, playlist)
    
    if not lyrics_unlocked:
        track('Paywall Hit', {
            'song_id': song_id,
            'user_tier': current_user.subscription_tier,
            'playlist_id': playlist.id,
            'position': song.position_in_playlist
        })
    
    return {
        'song': song.metadata,
        'lyrics_unlocked': lyrics_unlocked,
        # Always return timed lyrics for auto-scroll (even when locked)
        'lyrics': song.lyrics,  # text + timestamps
        # Gate NLP layer behind paywall
        'line_translations': song.line_translations if lyrics_unlocked else None,
        'word_data': song.word_data if lyrics_unlocked else None,
        'upgrade_cta': None if lyrics_unlocked else get_upgrade_cta(current_user, song, playlist)
    }
```

**Frontend rendering:**
```tsx
{lyricsUnlocked ? (
  <LyricsPlayer lyrics={lyrics} translations={translations} wordData={wordData} />
) : (
  <LyricsLockScreen
    lyrics={lyrics}  // Pass timed lyrics for blur effect + auto-scroll
    message={upgradeCta.message}
    title={upgradeCta.title}
    features={upgradeCta.highlight_features}
    onUpgrade={() => navigate(upgradeCta.url)}
    onBackToTrial={() => navigate(upgradeCta.back_to_trial_url)}  // New: return to first song
    upgradeButtonText={upgradeCta.cta}
  />
)}
{/* Music player always renders, never blocked */}
<MusicPlayer songId={songId} />
```

**Watch-out:** Users still get music playback → less pressure to upgrade. **Mitigation:** Lock screen must be visually appealing (not punishing), auto-scroll blur effect showcases sync quality, "Back to Trial Songs" button reduces frustration, track conversion rate and iterate.

---

## Database Schema

---

## Database Schema

### Users Table Additions

```sql
ALTER TABLE users ADD COLUMN subscription_tier TEXT DEFAULT 'free'
  CHECK (subscription_tier IN ('free', 'ru', 'en', 'es', 'pt', 'de', 'it', 'premium', 'lifetime', 'premium_student'));

ALTER TABLE users ADD COLUMN subscription_status TEXT DEFAULT NULL
  CHECK (subscription_status IN ('active', 'past_due', 'canceled', 'trialing', NULL));

ALTER TABLE users ADD COLUMN subscription_platform TEXT DEFAULT NULL
  CHECK (subscription_platform IN ('paddle', 'apple', 'google', NULL));

ALTER TABLE users ADD COLUMN subscription_external_id TEXT DEFAULT NULL;
  -- Paddle: subscription_id
  -- Apple: original_transaction_id
  -- Google: purchase token

ALTER TABLE users ADD COLUMN subscription_started_at TIMESTAMP DEFAULT NULL;

ALTER TABLE users ADD COLUMN subscription_expires_at TIMESTAMP DEFAULT NULL;
  -- NULL for lifetime subscriptions
  -- Future timestamp for active/canceled (access until expiry)

ALTER TABLE users ADD COLUMN subscription_cancel_at_period_end BOOLEAN DEFAULT FALSE;

ALTER TABLE users ADD COLUMN original_platform TEXT DEFAULT NULL
  CHECK (original_platform IN ('paddle', 'apple', 'google', NULL));
  -- Tracks where lifetime deal was purchased (for support/refund tracking)

CREATE UNIQUE INDEX idx_subscription_external_id ON users(subscription_external_id)
  WHERE subscription_external_id IS NOT NULL;
  -- Prevents duplicate subscriptions across platforms
```

---

## Entitlement Logic

### New Module: `backend/entitlements.py`

```python
from datetime import datetime, timezone
from backend.models import User, Song, Playlist

def is_subscription_active(user: User) -> bool:
    """Check if user has valid active subscription."""
    if user.subscription_tier == 'free':
        return False
    if user.subscription_tier == 'lifetime':
        return True
    if user.subscription_status != 'active':
        return False
    if user.subscription_expires_at and user.subscription_expires_at < datetime.now(timezone.utc):
        return False
    return True

def can_play_music(user: User, song: Song) -> bool:
    """Music playback always allowed (YouTube/Apple Music compliance)."""
    return True

def can_access_lyrics(user: User, song: Song, playlist: Playlist) -> bool:
    """Determine if user can see interactive lyrics/translations."""
    # Premium/lifetime: full access
    if user.subscription_tier in ['premium', 'lifetime', 'premium_student']:
        if is_subscription_active(user):
            return True
    
    # Language-specific tier: match source language
    if user.subscription_tier == song.source_lang:
def get_upgrade_cta(user: User, song: Song, playlist: Playlist) -> dict:
    """Generate context-aware upgrade messaging for lyrics lock screen."""
    
    # Get first song in playlist for "back to trial" navigation
    first_song_id = playlist.songs[0].id if playlist and playlist.songs else None
    back_to_trial_url = f'/playlist/{playlist.id}/song/{first_song_id}' if first_song_id else f'/playlist/{playlist.id}'
    
    if user.subscription_tier == 'free':
        return {
            'title': 'Unlock Interactive Lyrics',
            'message': f'Upgrade to Premium for unlimited lyrics, translations, and word definitions across all songs.',
            'cta': 'See Premium Plans',
            'url': '/pricing',
            'back_to_trial_url': back_to_trial_url,  # Navigate back to first song
            'highlight_features': [
                'Interactive word-by-word translations',
                'Instant definitions with keyboard shortcuts',
                'Full-line translations',
                'Unlimited songs in all languages'
            ]
        }
    
    if user.subscription_status == 'past_due':
        return {
            'title': 'Payment Issue',
            'message': 'Update your payment method to continue learning.',
            'cta': 'Update Payment',
            'url': '/account/billing',
            'back_to_trial_url': back_to_trial_url
        }
    
    if user.subscription_status == 'canceled':
        return {
            'title': 'Subscription Ended',
            'message': 'Renew to regain full access to interactive lyrics.',
            'cta': 'Renew Subscription',
            'url': '/pricing',
            'back_to_trial_url': back_to_trial_url
        }
    
    return {
        'title': 'Lyrics Locked',
        'message': 'This song requires an active subscription.',
        'cta': 'Manage Subscription',
        'url': '/account',
        'back_to_trial_url': back_to_trial_url
    }
```

### API Middleware
        'url': '/account'
    }
```
### API Middleware

Return song data with `lyrics_unlocked` flag — always include timed lyrics for auto-scroll, but gate NLP layer:
```python
# In backend/main.py
@app.get("/api/songs/{song_id}/data")
async def get_song_data(song_id: int, current_user: User = Depends(get_current_user)):
    song = get_song(song_id)
    playlist = get_playlist_for_song(song_id)
    
    lyrics_unlocked = can_access_lyrics(current_user, song, playlist)
    
    # Track paywall hits for analytics
    if not lyrics_unlocked:
        track('Paywall Hit', {
            'song_id': song_id,
            'user_tier': current_user.subscription_tier,
            'playlist_id': playlist.id,
            'position_in_playlist': song.position_in_playlist,
            'source_lang': song.source_lang
        })
    
    return {
        'id': song.id,
        'title': song.title,
        'artist': song.artist,
        'source_lang': song.source_lang,
        'youtube_url': song.youtube_url,
        'apple_music_url': song.apple_music_url,
        
        # Always return timed lyrics (needed for auto-scroll under blur)
        'lyrics': song.lyrics,  # Contains text + timestamps for sync
        
        # Gate expensive NLP layer behind paywall
        'lyrics_unlocked': lyrics_unlocked,
        'line_translations': song.line_translations if lyrics_unlocked else None,
        'word_data': song.word_data if lyrics_unlocked else None,
        'word_definitions': song.word_definitions if lyrics_unlocked else None,
        'word_translations': song.word_translations if lyrics_unlocked else None,
        
        'upgrade_cta': None if lyrics_unlocked else get_upgrade_cta(current_user, song, playlist)
    }
```

**Rationale:**
- **Timed lyrics always sent:** Free users need timing data for blur effect to scroll with music (better UX, showcases what they're missing)
- **NLP layer gated:** Translations, definitions, word lookups only for paid users (saves bandwidth, prevents network inspection abuse, protects value)
- **Security:** Even if free user inspects network tab, they only see raw lyrics text (which they can already see blurred), not the learning data

---
## Payment Platform: Paddle (Web)

### Why Paddle > Stripe

| Concern | Paddle | Stripe |
|---|---|---|
| **VAT/Sales Tax** | Auto-handled (merchant of record) | You file in every jurisdiction |
| **Compliance** | Paddle owns PCI-DSS, GDPR invoicing | You own all compliance |
| **Pricing Display** | Gross (tax-inclusive) | Net (requires JS tax calculation) |
| **Payouts** | Consolidated, all regions | Multi-currency complexity |
| **Promo Codes** | Built-in dashboard | Manual API setup |
| **Dunning** | Automatic retry + customer emails | You build it (or complex Billing setup) |
| **Cost** | 5% + card fees | 2.9% + card fees + tax tool ($100+/mo) |

**For solo dev in Europe, global audience:** Paddle saves 20+ hours/month on tax filings and dunning logic. 2% premium is worth it until $50k MRR.

**Alternative:** Stripe if US-only or existing tax infrastructure. Better for usage-based pricing (not relevant here).

---

## Pricing Structure

### Monthly vs Annual
- **Premium Monthly:** $9.99
- **Premium Annual:** $99.99 (17% discount — 2 months free)
- **Language Monthly:** $4.99
- **Language Annual:** $49.99 (17% discount)
- **Lifetime:** $199 (optional launch offer)

### Discounts & Promos

**1. Launch Discount (Paddle Checkout-Level)**
- 20% off first month via `?discount=LAUNCH20` parameter
- Set expiry in Paddle dashboard (e.g., June 15, 2026)
- No backend code needed

**2. Regional Pricing (Paddle Automatic)**
- Purchasing-power parity for Turkey, Russia, Brazil, etc.
- Paddle handles via IP geolocation — zero code

**3. Promo Codes (Paddle Dashboard)**
- Generate bulk codes for partnerships, affiliates, giveaways
- Apply to checkout: `?coupon=POLYGLOT50`
- Paddle tracks redemption analytics

**4. Student Discount (50% off)**
- Email domain whitelist (`.edu`, `.ac.uk`, etc.)
- Verification flow (see Student Discount section below)
- Tier: `premium_student` (same access, different price + analytics tracking)

---

## Multi-Platform Architecture

### Three Payment Systems

| Platform | Provider | Subscription Source | Validation Method |
|---|---|---|---|
| **Web** | Paddle | Paddle subscription ID | Webhook → DB |
| **iOS** | Apple IAP | App Store receipt | Server-side validation via Apple API |
| **Android** | Google Play | Purchase token | Server-side validation via Play Developer API |

**Core Principle:** One subscription unlocks all platforms. Subscribe on web → works on iOS. Subscribe on iOS → works on web + Android.

---

## Cross-Platform Sync

### Flow Overview

1. **User subscribes on any platform** → payment provider validates → sends webhook/receipt to backend
2. **Backend validates** (signature verification) → updates `users` table → sets `subscription_tier`, `expires_at`
3. **All platforms query** `/api/users/me` → check `subscription_tier` + `expires_at` → grant/deny lyrics access

### Platform-Specific Validation

#### Web (Paddle)
- **Purchase:** Paddle Checkout → webhook `subscription.created` → update DB
- **Renewal:** Webhook `subscription.payment_succeeded` → extend `expires_at`
- **Cancel:** Webhook `subscription.canceled` → set `cancel_at_period_end = true`, keep access until `expires_at`
- **API Check:** Every request checks `expires_at > now()` (no real-time validation needed)

#### iOS (Apple IAP)
- **Purchase:** StoreKit transaction → app sends receipt to `/api/subscriptions/apple/verify`
- **Backend:** Call Apple's `verifyReceipt` API → parse `latest_receipt_info` → update DB
- **Renewal:** Apple Server-to-Server notifications → webhook → update `expires_at`
- **Restore:** User taps "Restore Purchases" → app sends receipt → backend re-validates
- **Caveat:** Must validate on every app launch (or cache for 24h max)

#### Android (Google Play)
- **Purchase:** BillingClient purchase → app sends token to `/api/subscriptions/google/verify`
- **Backend:** Call Play Developer API `purchases.subscriptions.get` → update DB
- **Renewal:** Real-Time Developer Notifications (RTDN) → webhook → update `expires_at`
- **Restore:** Query Google API with user's linked account

### Race Condition Handling

**Scenario:** User subscribes on iOS → immediately opens web app. iOS receipt validation takes 2s → web shows lyrics lock screen.

**Solution:**
1. iOS app sends receipt to backend immediately after purchase
2. Backend queues async validation job, returns `{ status: 'pending' }`
3. App polls `/api/users/me` every 2s until `subscription_status == 'active'`
4. Web app checks same endpoint → sees active subscription → unlocks lyrics
5. All platforms converge within 2-5 seconds

**DB Constraint:** `UNIQUE(subscription_external_id)` prevents duplicate subscriptions if user tries to buy on multiple platforms (first purchase wins, others gracefully fail with "Already subscribed").

---

## Platform Fee Economics

### Revenue Split (Per $9.99/mo Premium)

| Platform | Gross | Platform Fee | Processing | Net to You | % Retained |
|---|---|---|---|---|---|
| **Web (Paddle)** | $9.99 | 5% ($0.50) | 3% ($0.30) | **$9.19** | **92%** |
| **iOS (Apple)** | $9.99 | 30% ($3.00) | Included | **$6.99** | **70%** |
| **Android (Google)** | $9.99 | 30% ($3.00) | Included | **$6.99** | **70%** |

**After Year 1 (per subscriber):**
- iOS/Android: 15% cut → **$8.49 net** (85%)

**Strategic Implication:** Drive web signups via SEO, content marketing, YouTube tutorials. App Store is discovery + mobile UX, not primary revenue driver.

### Price Parity (Required by Apple/Google)

- In-app price must be ≤ web price (Apple rejects if web is cheaper)
- **Strategy:** Same nominal price ($9.99) across all platforms
- Effective cost: 22% lower margin on mobile, but unavoidable for App Store presence

---

## Billing Flows

### Web (Paddle) — Full Flow

1. **Frontend:** User clicks "Upgrade to Premium" on lyrics lock screen
   ```tsx
   <button onClick={() => {
     track('Upgrade Clicked', { source: 'lyrics_lock_screen', song_id })
     window.open(`https://buy.paddle.com/product/premium?passthrough=${user.id}`)
   }}>
     Upgrade to Premium
   </button>
   ```

2. **Paddle Checkout:** Hosted page (PCI-compliant, localized, mobile-optimized)
   - User enters payment details
   - Paddle handles fraud detection, tax calculation, 3DS authentication
   - `passthrough` param carries your `user.id` → webhook knows who subscribed

3. **Webhook: `subscription.created`**
   ```python
   @app.post("/api/webhooks/paddle")
   async def paddle_webhook(request: Request):
       signature = request.headers.get("Paddle-Signature")
       body = await request.body()
       
       # Verify signature (Paddle docs: HMAC SHA256)
       if not verify_paddle_signature(signature, body):
           raise HTTPException(401)
       
       event = parse_paddle_event(body)
       
       if event.type == "subscription.created":
           user_id = event.passthrough
           user = get_user(user_id)
           user.subscription_tier = 'premium'
           user.subscription_status = 'active'
           user.subscription_platform = 'paddle'
           user.subscription_external_id = event.subscription_id
           user.subscription_started_at = event.created_at
           user.subscription_expires_at = event.next_billing_date
           save_user(user)
           
           track('Subscription Activated', {
               'user_id': user_id,
               'platform': 'paddle',
               'tier': 'premium'
           })
   ```

4. **Frontend Redirect:** Paddle redirects to `https://singoling.com/welcome?subscribed=true`
   - Show success message, "Start learning" CTA
   - App polls `/api/users/me` until subscription appears (handles webhook delay)
   - Lyrics unlock immediately on next song load

### iOS (Apple IAP) — Full Flow

1. **App:** User taps "Upgrade" on lyrics lock screen → StoreKit presents system purchase sheet
2. **Transaction:** Apple processes payment → returns receipt
3. **App → Backend:**
   ```swift
   let receiptData = try Data(contentsOf: Bundle.main.appStoreReceiptURL!)
   let base64Receipt = receiptData.base64EncodedString()
   
   api.post("/api/subscriptions/apple/verify", body: ["receipt": base64Receipt])
   ```

4. **Backend Validation:**
   ```python
   @app.post("/api/subscriptions/apple/verify")
   async def verify_apple_receipt(receipt: str, current_user: User):
       # Call Apple's verifyReceipt API (production or sandbox)
       response = requests.post(
           "https://buy.itunes.apple.com/verifyReceipt",
           json={"receipt-data": receipt, "password": APPLE_SHARED_SECRET}
       )
       
       data = response.json()
       if data['status'] != 0:
           raise HTTPException(400, "Invalid receipt")
       
       latest_info = data['latest_receipt_info'][0]
       
       current_user.subscription_tier = 'premium'
       current_user.subscription_status = 'active'
       current_user.subscription_platform = 'apple'
       current_user.subscription_external_id = latest_info['original_transaction_id']
       current_user.subscription_expires_at = parse_apple_timestamp(latest_info['expires_date_ms'])
       save_user(current_user)
   ```

5. **Auto-Renewal:** Apple sends Server-to-Server notification → webhook updates `expires_at`

### Android (Google Play) — Similar to iOS

- Use `BillingClient` API instead of StoreKit
- Validate via Google Play Developer API instead of Apple's endpoint
- Real-Time Developer Notifications (RTDN) instead of Apple's S2S

---

## Lifetime Deal Implementation

### Schema
```python
subscription_tier = 'lifetime'
subscription_expires_at = None  # NULL = never expires
original_platform = 'paddle'    # Track purchase source
```

### Platform-Specific Setup

**Paddle (Web):**
- Create one-time payment product (not recurring subscription)
- Webhook `order.succeeded` → set `tier = 'lifetime'`, `status = 'active'`, `expires_at = NULL`

**Apple IAP:**
- Product type: "Non-Consumable" (one-time purchase)
- Receipt validation: check `product_id == 'com.singoling.lifetime'` → grant lifetime
- **Caveat:** Must implement "Restore Purchases" (non-consumables don't auto-restore on new devices)

**Google Play:**
- Product type: "Non-Consumable"
- Validate via `purchases.products.get` (not subscriptions API)

### Cross-Platform Lifetime Access

**Problem:** User buys lifetime on web → signs in on iOS. How does app know?

**Solution:**
1. Backend `/api/users/me` returns `subscription_tier`
2. App checks on launch: `if tier == 'lifetime' → unlock everything`
3. No receipt validation needed (backend is source of truth)

**Refund Edge Case:** User buys lifetime on iOS → refunds via Apple → backend receives refund webhook → revoke access. Same for all platforms.

---

## Student Discount Strategy

### Verification Flow (Email Whitelist)

1. **User clicks** "Get Student Discount" on pricing page
2. **Frontend** → `/api/subscriptions/verify-student`
3. **Backend:**
   - Check email domain against whitelist (`.edu`, `.ac.uk`, `.edu.au`, etc.)
   - If valid: generate verification link with signed token
   - Send email: "Verify your student status"
4. **User clicks link** → backend validates token → generates 50% off Paddle coupon → applies to checkout

### Implementation
```python
STUDENT_DOMAINS = {'.edu', '.ac.uk', '.edu.au', '.edu.tr', '.edu.ru'}

@app.post("/api/subscriptions/verify-student")
async def verify_student(email: str, current_user: User):
    domain = email.split('@')[-1].lower()
    
    if not any(domain.endswith(edu) for edu in STUDENT_DOMAINS):
        raise HTTPException(400, "Email must be from a recognized educational institution")
    
    token = generate_signed_token(current_user.id, expiry=24h)
    send_email(
        to=email,
        subject="Verify your student status",
        body=f"Click here: https://singoling.com/verify-student?token={token}"
    )
    
    return {"message": "Verification email sent"}

@app.get("/verify-student")
async def confirm_student(token: str):
    user_id = verify_signed_token(token)
    user = get_user(user_id)
    
    # Generate 50% off coupon in Paddle dashboard (pre-created)
    # Or dynamically via Paddle API
    coupon_code = "STUDENT50"
    
    redirect_to = f"https://buy.paddle.com/product/premium?coupon={coupon_code}&passthrough={user_id}"
    return RedirectResponse(redirect_to)
```

### Tier Tracking
```python
user.subscription_tier = 'premium_student'  # Same access, different analytics label
```

**Future Expansion:** SheerID integration ($1/verification) for global coverage once you have 100+ student signups validating demand.

---

## Rollout Plan (Agent-Assisted)

### Phase 1: Web Subscriptions (Paddle) — M, ~3 days
- [ ] DB migration (add subscription columns)
- [ ] `backend/entitlements.py` module (`can_access_lyrics` logic)
- [ ] Paddle webhook endpoint + signature verification
- [ ] API: return `lyrics_unlocked` flag in `/api/songs/{id}/data`
- [ ] Test in Paddle Sandbox (fake purchases, webhook replay)

**AI shortcuts:** Schema DDL via prompt, webhook boilerplate from Paddle docs, entitlement logic is <50 LOC.

### Phase 2: Frontend (Pricing + Lyrics Lock Screen) — M, ~3 days
- [ ] Pricing page (`/pricing`) with Paddle checkout buttons
- [ ] `LyricsLockScreen` component (elegant overlay with upgrade CTA)
- [ ] Conditional rendering: show lock screen when `lyrics_unlocked === false`
- [ ] Account settings: subscription status, Paddle cancellation link
- [ ] Success page (`/welcome?subscribed=true`)
- [ ] Localize all copy (6 languages)

**AI shortcuts:** React components from design sketch, i18n strings via prompt, Tailwind styling for blur/overlay.

**LyricsLockScreen Component Spec:**
```tsx
interface LyricsLockScreenProps {
  lyrics: TimedLyric[];  // For blur effect with auto-scroll
  title: string;
  message: string;
  features?: string[];  // Optional feature highlights
  onUpgrade: () => void;
  onBackToTrial: () => void;  // Navigate to first song in playlist
  upgradeButtonText: string;
}

// Renders sleek overlay with:
// 1. Blurred lyrics scrolling in background (uses timed_lyrics for sync)
// 2. Dark gradient overlay (80% opacity)
// 3. Title + message (centered)
// 4. Optional feature list (if provided)
// 5. Primary button: "See Premium Plans" (or upgradeButtonText)
// 6. Secondary button: "Back to Trial Songs" (navigates to first song)
```

**UX Rationale:**
- **Blurred auto-scroll:** Showcases sync quality without giving away translations
- **Back button:** Reduces frustration, lets users return to free songs without blocking
- **Two-button pattern:** Primary action (upgrade) + escape hatch (back to trial)
- **No blocking music:** YouTube/Apple Music continues playing in background


### Phase 3: iOS In-App Purchases — L, ~5 days
- [ ] StoreKit integration in iOS app (assumes separate repo)
- [ ] Backend: `/api/subscriptions/apple/verify` endpoint
- [ ] Apple Server-to-Server webhook endpoint
- [ ] Receipt validation logic (handle Apple's verbose JSON)
- [ ] "Restore Purchases" flow

**Bottleneck:** App Store Connect manual setup (product IDs, pricing tiers, tax forms) — 2-3 hours UI work.

### Phase 4: Android In-App Purchases — L, ~5 days
- [ ] BillingClient integration in Android app
- [ ] Backend: `/api/subscriptions/google/verify` endpoint
- [ ] Google Real-Time Developer Notifications webhook
- [ ] Purchase token validation via Play Developer API
- [ ] "Restore Purchases" flow

**Bottleneck:** Google Play Console setup — similar manual work as iOS.

### Phase 5: Cross-Platform Sync Testing — S, ~2 days
- [ ] Integration tests: subscribe on web → verify on iOS
- [ ] Subscribe on iOS → verify on web + Android
- [ ] Race condition testing (parallel subscription attempts)
- [ ] Refund flow testing (Paddle/Apple/Google sandboxes)
- [ ] `UNIQUE(subscription_external_id)` constraint validation

### Phase 6: Lifetime Deal Support — S, ~1 day
- [ ] Paddle one-time payment product setup
- [ ] Apple/Google non-consumable product setup
- [ ] Backend logic: `tier = 'lifetime'`, `expires_at = NULL`
- [ ] iOS/Android restore flow (lifetime purchases must be manually restored)

### Phase 7: Student Discounts — S, ~1 day
- [ ] Email domain whitelist (`.edu`, `.ac.uk`, etc.)
- [ ] Verification endpoint + email sending
- [ ] Discount code application (Paddle/Apple/Google)
- [ ] `premium_student` tier analytics tracking

**Defer SheerID** until 100+ student signups validate demand.

### Phase 8: Observability — S, ~1 day
- [ ] Plausible events:
  - `Paywall Hit` (song #3+, props: playlist_id, source_lang, position_in_playlist)
  - `Lyrics Lock Screen Viewed` (props: song_id, user_tier)
  - `Upgrade Clicked` (props: platform, tier, source: lyrics_lock_screen)
  - `Subscription Activated` (props: platform, tier)
  - `Subscription Canceled` (props: platform, reason)
- [ ] Revenue dashboard (query Paddle + Apple + Google APIs daily)
- [ ] MRR tracking + cohort analysis
- [ ] Conversion funnel: Paywall Hit → Lock Screen Viewed → Upgrade Clicked → Subscription Activated

---

## Technical Debt & Edge Cases

### Critical Risks

**1. Receipt Validation Failures** (Apple/Google APIs have 1-2% error rate)
- **Mitigation:** Retry logic (3x exponential backoff), fallback to cached validation for 24h

**2. Subscription Transfer Abuse** (user shares account across devices)
- **Mitigation:** Device limit (3 active sessions per account), token-based invalidation

**3. Refund Window Gaming** (subscribe, access all songs, refund within 7 days)
- **Mitigation:** Track `lyrics_unlocked_count` per song, flag accounts with >50 songs + refund → ban
- **Note:** Less concern now that music plays for free (value is in lyrics, harder to abuse)

**4. Currency Arbitrage** (VPN to cheaper region, subscribe, switch back)
- **Detection:** Log `subscription_currency` + `ip_country` on purchase → flag mismatches
- **Response:** Monitor if >5% of signups (enforcement cost > loss initially)

**5. Platform Lock-In UX** (user on iOS wants to pay on web to avoid Apple cut)
- **Solution:** Show banner in app: "Also available at singoling.com" (carefully worded to avoid App Store rejection)
- **Apple's rule:** Can't link to web checkout, but can mention website

**6. YouTube/Apple Music ToS Compliance**
- **Risk:** Blocking playback might violate API terms
- **Mitigation:** Music always plays, only lyrics layer is gated → 100% compliant

### Recovery Flow (iOS/Android)

**Scenario:** User subscribes on iOS → deletes app → reinstalls 6 months later. Apple receipt still valid, but backend `subscription_status = 'canceled'` (missed renewal webhook).

**Solution (Hybrid Approach):**
1. **Cache:** Validate receipt once/day, cache locally (fast UX)
2. **Background refresh:** Re-validate every 6 hours in background (covers webhook gaps)
3. **Manual button:** "Restore Purchases" triggers immediate validation (user-initiated fix)

**Recommendation:** Option 3 (hybrid). Most resilient, standard iOS pattern.

---

## Next Steps (Priority Order)

1. ✅ **Planning complete** (this document)
2. **Create Paddle sandbox** (today, 15 min) → test webhook flow before building
3. **Phase 1+2** (web subscriptions + lyrics lock screen UI) → shippable Premium-only web launch
4. **iOS App Store setup** (submit before building IAP — 2-3 day approval, may need iterations)
5. **Phase 3** (iOS IAP) → test in TestFlight
6. **Phase 4** (Android) → test in internal track
7. **Phase 5-8** (cross-platform sync + observability)

**Earliest revenue:** ~2 weeks (web-only Paddle).  
**Full cross-platform:** ~4 weeks.

---

## Open Questions (Needs Decision Before Phase 3)

1. **Grandfather existing users?** No users yet, non-issue for now. Future: grandfather if <50 active users at paywall launch.
2. **Lifetime deal at launch?** Optional $199 offer. Requires UI slot on pricing page + A/B test (lifetime vs. no-lifetime conversion).
3. **Apple IAP recovery:** Hybrid approach (cache + background refresh + manual "Restore"). Standard pattern.
4. **Student demand validation:** Start with email whitelist (free), upgrade to SheerID at 100+ student conversions.
5. **Lyrics lock screen design:** Blur + overlay OR full lock screen with preview? A/B test after launch.

---

## Appendix: Entitlement Pseudocode

### Complete `backend/entitlements.py` Contract

```python
from datetime import datetime, timezone
from backend.models import User, Song, Playlist

def is_subscription_active(user: User) -> bool:
    """Core subscription validity check."""
    if user.subscription_tier == 'free':
        return False
    if user.subscription_tier == 'lifetime':
        return True
    if user.subscription_status != 'active':
        return False
    if user.subscription_expires_at:
        if user.subscription_expires_at < datetime.now(timezone.utc):
            return False
    return True

def can_play_music(user: User, song: Song) -> bool:
    """Music playback always allowed (YouTube/Apple Music compliance)."""
    return True

def can_access_lyrics(user: User, song: Song, playlist: Playlist) -> bool:
    """Lyrics/translations gating logic."""
    # Premium tiers: unlimited
    if user.subscription_tier in ['premium', 'lifetime', 'premium_student']:
        if is_subscription_active(user):
            return True
    
    # Language-specific tier
    if user.subscription_tier == song.source_lang:
        if is_subscription_active(user):
            return True
    
    # Free tier: first 2 songs per playlist
    if song.position_in_playlist <= 2:
        return True
def get_upgrade_cta(user: User, song: Song, playlist: Playlist) -> dict:
    """Context-aware upgrade CTA for lyrics lock screen."""
    
    # Get first song in playlist for "back to trial" navigation
    first_song_id = playlist.songs[0].id if playlist and playlist.songs else None
    back_to_trial_url = f'/playlist/{playlist.id}/song/{first_song_id}' if first_song_id else f'/playlist/{playlist.id}'
    
    if user.subscription_tier == 'free':
        return {
            'title': 'Unlock Interactive Lyrics',
            'message': 'Upgrade to Premium for unlimited lyrics, translations, and word definitions across all songs.',
            'cta': 'See Premium Plans',
            'url': '/pricing',
            'back_to_trial_url': back_to_trial_url,
            'highlight_features': [
                'Interactive word-by-word translations',
                'Instant definitions with keyboard shortcuts',
                'Full-line translations',
                'Unlimited songs in all languages'
            ]
        }
    
    if user.subscription_status == 'past_due':
        return {
            'title': 'Payment Issue',
            'message': 'Update your payment method to continue learning.',
            'cta': 'Update Payment',
            'url': '/account/billing',
            'back_to_trial_url': back_to_trial_url
        }
    
    if user.subscription_status == 'canceled':
        return {
            'title': 'Subscription Ended',
            'message': 'Renew to regain full access to interactive lyrics.',
            'cta': 'Renew Subscription',
            'url': '/pricing',
            'back_to_trial_url': back_to_trial_url
        }
    
    return {
        'title': 'Lyrics Locked',
        'message': 'This song requires an active subscription.',
        'cta': 'Manage Subscription',
        'url': '/account',
        'back_to_trial_url': back_to_trial_url
    }

# Usage in API routes
        'title': 'Lyrics Locked',
        'message': 'This song requires an active subscription.',
        'cta': 'Manage Subscription',
        })
    
    return {
        'id': song.id,
        'title': song.title,
        'artist': song.artist,
        'source_lang': song.source_lang,
        'youtube_url': song.youtube_url,
        'apple_music_url': song.apple_music_url,
        
        # Always return timed lyrics (needed for auto-scroll under blur)
        'lyrics': song.lyrics,  # Contains text + timestamps for sync
        
        # Gate expensive NLP layer behind paywall
        'lyrics_unlocked': lyrics_unlocked,
        'line_translations': song.line_translations if lyrics_unlocked else None,
        'word_data': song.word_data if lyrics_unlocked else None,
        'word_definitions': song.word_definitions if lyrics_unlocked else None,
        'word_translations': song.word_translations if lyrics_unlocked else None,
        
        'upgrade_cta': None if lyrics_unlocked else get_upgrade_cta(current_user, song, playlist)
    }
```

---

## Appendix: Webhook Signature Verification
            'position_in_playlist': song.position_in_playlist
        })
    
    return {
        'id': song.id,
        'title': song.title,
        'artist': song.artist,
        'source_lang': song.source_lang,
        'youtube_url': song.youtube_url,
        'apple_music_url': song.apple_music_url,
        'lyrics_unlocked': lyrics_unlocked,
        'lyrics': song.lyrics if lyrics_unlocked else None,
        'line_translations': song.line_translations if lyrics_unlocked else None,
        'word_data': song.word_data if lyrics_unlocked else None,
        'upgrade_cta': None if lyrics_unlocked else get_upgrade_cta(current_user, song, playlist)
    }
```

---

## Appendix: Webhook Signature Verification

### Paddle
```python
import hmac
import hashlib

def verify_paddle_signature(signature: str, body: bytes) -> bool:
    """Verify Paddle webhook signature (HMAC SHA256)."""
    secret = os.getenv("PADDLE_WEBHOOK_SECRET")
    expected = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
```

### Apple (S2S Notifications)
```python
import jwt

def verify_apple_notification(token: str) -> dict:
    """Verify Apple Server-to-Server notification JWT."""
    # Apple provides JWK set at https://appleid.apple.com/auth/keys
    # Decode JWT with Apple's public key
    payload = jwt.decode(
        token,
        options={"verify_signature": True},
        algorithms=["ES256"],
        # Fetch public key from Apple's JWKS endpoint
    )
    return payload
```

### Google (RTDN)
```python
import base64
import json

def verify_google_notification(message: str, signature: str) -> dict:
    """Verify Google Real-Time Developer Notification."""
    # Google sends Pub/Sub messages (base64-encoded)
    decoded = base64.b64decode(message)
    data = json.loads(decoded)
    
    # Signature verification via Google Cloud Pub/Sub
    # (Pub/Sub handles signature check at infrastructure level)
    return data
```

---

**End of Document**
