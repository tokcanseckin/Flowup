# Responsive Design Implementation Specification

## Overview

**Objective:** Optimize SingoLing for mobile (320-480px) and tablet (768-1024px) devices while maintaining the production-ready desktop experience (1200px+).

**Core Principles:**
1. **No horizontal scroll** — All content must fit viewport width
2. **Touch-friendly targets** — Minimum 44×44px for interactive elements
3. **Function-first prioritization** — Core learning experience takes precedence
4. **Vertical stacking** — Collapse multi-column layouts on small screens
5. **Progressive disclosure** — Hide/collapse secondary UI on mobile

---

## Breakpoints & Media Queries

Use Tailwind's responsive prefixes consistently:

```tsx
// Mobile-first approach
className="p-4 md:p-6 lg:p-8"

// Breakpoints:
// sm:  640px  — Large mobile / small tablet
// md:  768px  — Tablet
// lg:  1024px — Desktop
// xl:  1280px — Large desktop
```

**Critical breakpoint:** 768px (mobile vs. tablet/desktop split)

---

## Priority 1: Player/Lyrics View (CRITICAL)

**File:** `frontend/src/components/LyricsPlayer.tsx`

### Current Issues
- Fixed desktop layout with side-by-side panels
- Word lookup panel uses absolute positioning without mobile consideration
- Keyboard shortcuts (1-9) conflict with mobile number input
- Line number circles may be too small for touch

### Implementation

#### Mobile Layout (< 768px)
1. **Single-column stacking:**
   ```tsx
   // Replace side-by-side layout with vertical stack
   <div className="flex flex-col md:flex-row gap-4">
     {/* Lyrics panel — always visible */}
     <div className="w-full md:w-2/3">
       {/* lyrics content */}
     </div>
     
     {/* Lookup panel — bottom sheet on mobile */}
     {activeLookup && (
       <div className="fixed md:relative inset-x-0 bottom-0 md:w-1/3 
                       bg-surface-card rounded-t-3xl md:rounded-2xl 
                       shadow-2xl md:shadow-none z-50 max-h-[60vh] md:max-h-full 
                       overflow-y-auto">
         {/* lookup content */}
       </div>
     )}
   </div>
   ```

2. **Touch targets for line numbers:**
   ```tsx
   // Increase circle size on mobile
   <button className="w-8 h-8 md:w-6 md:h-6 rounded-full ..." />
   ```

3. **Word keyboard shortcuts:**
   ```tsx
   // Disable 1-9 shortcuts on mobile (detected via media query at line 513)
   const isMobile = window.matchMedia('(max-width: 767px)').matches
   if (!isMobile) {
     // Register keyboard shortcuts
   }
   ```

4. **Lookup panel close gesture:**
   ```tsx
   // Add swipe-down to close on mobile
   // Use drag handle at top of bottom sheet
   <div className="md:hidden w-12 h-1.5 bg-gray-600 rounded-full mx-auto mb-4" />
   ```

#### Tablet Layout (768-1024px)
- Keep side-by-side layout but reduce panel widths proportionally
- Maintain keyboard shortcuts
- Slightly larger touch targets than desktop (40×40px minimum)

---

## Priority 2: Browse/Discover View (HIGH)

**File:** `frontend/src/App.tsx` (lines 1375-1650)

### Current Issues
- Playlist cards are fixed 300px width with `flex-wrap`
- Cards don't adapt to viewport width on mobile
- Grid layout (`grid-cols-2 sm:grid-cols-3`) needs refinement
- Horizontal scrolling risk on cards with long titles

### Implementation

#### Mobile (< 640px)
1. **Single column layout:**
   ```tsx
   // Replace flex-wrap with responsive grid
   <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
     {matchingPlaylists.map(pl => (
       <button className="w-full rounded-2xl ..." />
     ))}
   </div>
   ```

2. **Playlist cards — fluid width:**
   ```tsx
   // Remove fixed 300px width
   style={{ width: 300 }} // ❌ REMOVE
   className="w-full"     // ✅ ADD
   ```

3. **Language selector cards:**
   ```tsx
   // Current: grid-cols-2 sm:grid-cols-3
   // Change to: grid-cols-1 sm:grid-cols-2 lg:grid-cols-3
   <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
   ```

4. **Text truncation:**
   ```tsx
   // Prevent title overflow
   <h3 className="text-white font-semibold text-base leading-snug mb-2 
                  line-clamp-2">
     {tc(pl.name)}
   </h3>
   ```

#### Playlist Detail View (Mobile < 768px)
**Current:** Two-column layout (playlist info left, song list right)

**Change to vertical stack:**
```tsx
<div className="flex flex-col lg:flex-row gap-8">
  {/* Playlist info — top on mobile, left on desktop */}
  <div className="w-full lg:w-80 shrink-0">
    {/* cover, play button, stats, description */}
  </div>

  {/* Song list — below on mobile, right on desktop */}
  <div className="flex-1 min-w-0">
    {songList}
  </div>
</div>
```

**Play button full-width on mobile:**
```tsx
<button className="w-full lg:w-auto px-6 py-3 rounded-xl ...">
  {t('browser.play')}
</button>
```

---

## Priority 3: Header & Navigation (HIGH)

**File:** `frontend/src/App.tsx` (lines 1323, 2761)

### Current Issues
- Header has `max-w-[972px]` and `max-w-[1200px]` in different places
- Logo and controls may overflow on narrow screens
- User menu needs touch-friendly sizing

### Implementation

#### Mobile Header (< 768px)
1. **Reduce padding and logo size:**
   ```tsx
   <header className="bg-surface border-b border-zinc-800/50">
     <div className="max-w-[1200px] mx-auto w-full 
                     px-3 py-3 md:px-4 md:py-4 
                     flex items-center justify-between gap-2">
       {/* Logo — smaller on mobile */}
       <img src={singolingLogo} 
            className="h-6 md:h-8" 
            alt="SingoLing" />
       
       {/* Controls — compact spacing */}
       <div className="flex items-center gap-2 md:gap-4">
         {/* buttons */}
       </div>
     </div>
   </header>
   ```

2. **Language selector — truncate text on mobile:**
   ```tsx
   <button className="text-sm text-zinc-400 hover:text-white 
                      truncate max-w-[120px] md:max-w-[180px]">
     {currentLangName}
   </button>
   ```

3. **User menu — full-width dropdown on mobile:**
   ```tsx
   <div className="absolute right-0 md:right-auto top-10 z-50 
                   w-screen md:w-auto md:min-w-[200px] 
                   left-0 md:left-auto
                   rounded-t-none md:rounded-xl 
                   border-x-0 md:border-x border-zinc-700 
                   py-1.5 shadow-2xl">
     {/* menu items */}
   </div>
   ```

---

## Priority 4: Pricing Page (HIGH)

**File:** `frontend/src/components/PricingPage.tsx`

### Current Issues
- Two-column pricing cards may be too wide for mobile
- Feature list bullet points need more vertical spacing on mobile
- Modal overlay may not be centered correctly on small screens

### Implementation

#### Mobile (< 640px)
1. **Single-column pricing cards:**
   ```tsx
   <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-4xl mx-auto">
     {/* Monthly card */}
     {/* Annual card */}
   </div>
   ```

2. **Increase padding and touch targets:**
   ```tsx
   <button className="w-full py-4 md:py-3 px-6 rounded-xl 
                      text-base md:text-sm font-semibold 
                      bg-blue-600 hover:bg-blue-700 text-white">
     Upgrade Now
   </button>
   ```

3. **Feature list spacing:**
   ```tsx
   <ul className="space-y-3 md:space-y-2">
     {features.map(f => (
       <li className="flex items-start gap-3" key={f}>
         <svg className="w-5 h-5 md:w-4 md:h-4 shrink-0 mt-0.5" />
         <span className="text-sm md:text-xs">{f}</span>
       </li>
     ))}
   </ul>
   ```

4. **Modal positioning:**
   ```tsx
   <div className="fixed inset-0 z-50 flex items-end md:items-center 
                   justify-center bg-black/70 backdrop-blur-sm 
                   p-0 md:p-4">
     <div className="bg-white rounded-t-3xl md:rounded-2xl 
                     w-full md:max-w-2xl 
                     p-6 md:p-8 
                     max-h-[90vh] overflow-y-auto">
       {/* content */}
     </div>
   </div>
   ```

---

## Priority 5: Lock Screen (MEDIUM)

**File:** `frontend/src/components/LyricsLockScreen.tsx`

### Current Issues
- Background blurred lyrics may perform poorly on mobile
- Button sizing adequate but could be more touch-friendly
- Message text may be too small on mobile

### Implementation

1. **Simplify background on mobile:**
   ```tsx
   // Reduce blur intensity or use solid background
   <div className="backdrop-blur-md md:backdrop-blur-xl bg-black/40 md:bg-black/60">
   ```

2. **Increase button sizing:**
   ```tsx
   <button className="px-6 py-4 md:px-6 md:py-3 
                      text-base md:text-sm font-semibold 
                      rounded-xl">
     Upgrade to Premium
   </button>
   ```

3. **Text sizing:**
   ```tsx
   <h2 className="text-2xl md:text-3xl font-bold text-white mb-4">
     {title}
   </h2>
   <p className="text-base md:text-lg text-gray-300 mb-6">
     {message}
   </p>
   ```

---

## Priority 6: Settings Page (LOW)

**File:** `frontend/src/App.tsx` (SettingRow component around line 1700+)

### Current Issues
- Toggle switches are desktop-sized
- Setting rows may need more vertical padding on mobile

### Implementation

1. **Increase padding:**
   ```tsx
   <div className="rounded-2xl border border-gray-800/80 
                   p-5 md:p-4" 
        style={{ background: '#12121f' }}>
   ```

2. **Toggle switch sizing:**
   ```tsx
   <button className="h-8 w-14 md:h-7 md:w-12 rounded-full">
     <span className="h-6 w-6 md:h-5 md:w-5 rounded-full" />
   </button>
   ```

---

## Priority 7: Auth Screens (LOW)

**Files:** Google/Apple auth buttons, login/signup forms

### Implementation

1. **Full-width buttons on mobile:**
   ```tsx
   <button className="w-full md:w-auto min-w-[200px] 
                      px-6 py-3 rounded-xl">
     Sign in with Google
   </button>
   ```

2. **Form inputs — larger touch targets:**
   ```tsx
   <input className="w-full px-4 py-4 md:py-3 
                     rounded-xl text-base md:text-sm" />
   ```

---

## Testing Checklist

### Mobile (375×667 — iPhone SE)
- [ ] Header fits without horizontal scroll
- [ ] Browse view: playlist cards stack vertically, no overflow
- [ ] Browse view: language selectors display in single column
- [ ] Playlist detail: info and song list stack vertically
- [ ] Player: lyrics display full-width, lookup panel is bottom sheet
- [ ] Player: line number circles are tappable (44×44px minimum)
- [ ] Player: word tap opens bottom sheet, swipe-down closes
- [ ] Pricing page: cards stack vertically, buttons full-width
- [ ] Lock screen: buttons are thumb-reachable, text readable
- [ ] Settings: toggle switches are large enough to tap accurately
- [ ] No text is cut off or requires horizontal scroll

### Tablet Portrait (768×1024 — iPad)
- [ ] Header spacing balanced
- [ ] Browse view: 2-column playlist grid
- [ ] Playlist detail: side-by-side layout maintained
- [ ] Player: side-by-side lyrics + lookup panel
- [ ] Pricing page: 2-column card layout
- [ ] All touch targets minimum 40×40px

### Tablet Landscape (1024×768)
- [ ] Identical to desktop experience
- [ ] Header max-width applied correctly
- [ ] All layouts match desktop at 1200px+

### Rotation Handling
- [ ] Player state persists on orientation change
- [ ] Lookup panel repositions correctly
- [ ] Scroll position maintained where appropriate

### Performance
- [ ] No layout shift (CLS) on viewport resize
- [ ] Smooth scrolling on 60Hz mobile devices
- [ ] Backdrop blur effects don't lag on mid-range Android

---

## Implementation Order

1. **Header & Navigation** (30 min) — Affects every page, foundational
2. **Browse/Discover View** (45 min) — Entry point, high visibility
3. **Player/Lyrics View** (90 min) — Core experience, most complex
4. **Pricing Page** (30 min) — Monetization-critical
5. **Lock Screen** (20 min) — Monetization-adjacent
6. **Settings Page** (15 min) — Secondary UI
7. **Auth Screens** (15 min) — Entry point, quick wins

**Total estimated effort:** 4 hours (focused implementation)

---

## Code Review Criteria

Before submitting:
1. Test on real devices (iPhone, Android phone, iPad)
2. Use Chrome DevTools device emulation for quick iteration
3. Verify no horizontal scroll at any viewport width (320px minimum)
4. Check touch target sizes with browser accessibility tools
5. Validate against WCAG 2.1 touch target guidelines (44×44px)
6. Test with slow 3G throttling (ensure no layout flash)
7. Verify Tailwind responsive classes used consistently (no inline media queries)

---

## Additional Notes

- **Don't break desktop:** All changes must use responsive prefixes (md:, lg:) to preserve desktop behavior
- **Use existing Tailwind theme:** Colors, spacing, and shadows already defined in `tailwind.config.js`
- **Maintain existing functionality:** No regressions in word lookup, line translation, playback controls
- **Analytics unchanged:** All tracking events continue firing as before
- **Consider PWA install prompt:** Mobile users will see "Add to Home Screen" banner — ensure UI works in standalone mode

---

## Future Enhancements (Not in Scope)

- Swipe gestures for next/previous song
- Pull-to-refresh on browse page
- Haptic feedback on word tap (iOS only)
- Dark/light theme toggle (currently dark-only)
- Font size preferences (accessibility)
- Landscape-optimized player UI

