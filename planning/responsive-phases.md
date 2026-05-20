# Responsive Design: Phased Implementation Plan

## Overview

Implementation broken into 7 discrete phases by page/view. Each phase is independent and testable. Work in order for maximum impact.

---

## Phase 1: Header & Navigation (ALL PAGES)

**Priority:** CRITICAL — Affects every page, must be done first  
**Effort:** 30 minutes  
**Files:** `frontend/src/App.tsx` (lines ~1323, ~2761)

### Changes Required

1. **Main header (Browse/Player):**
```tsx
// Line ~1323
<div className="max-w-[972px] mx-auto w-full 
                px-3 py-3 md:px-4 md:py-4 
                flex items-center justify-between gap-2 md:gap-4">
  
  {/* Logo — reduce size on mobile */}
  <img src={singolingLogo} 
       className="h-6 md:h-8" 
       alt="SingoLing" />
  
  {/* Language selector — truncate on mobile */}
  <button className="text-sm text-zinc-400 hover:text-white 
                     truncate max-w-[100px] sm:max-w-[140px] md:max-w-[180px]">
    {currentLangName}
  </button>
</div>
```

2. **Player header:**
```tsx
// Line ~2761
<div className="max-w-[1200px] mx-auto w-full 
                px-3 py-3 md:px-4 md:py-4 
                flex items-center justify-between gap-2 md:gap-4">
  
  {/* Adjust spacing throughout */}
</div>
```

3. **User menu dropdown — full-width on mobile:**
```tsx
// Line ~2913 (approximate)
<div className="absolute right-0 top-10 z-50 
                w-screen md:w-auto md:min-w-[200px] 
                left-0 md:left-auto
                rounded-none md:rounded-xl 
                border-x-0 md:border-x border-zinc-700 
                py-1.5 shadow-2xl"
     style={{ background: '#18191f' }}>
  {/* menu items */}
</div>
```

### Testing Checklist
- [ ] Logo visible at 320px width without wrapping
- [ ] Language selector text truncates with ellipsis on narrow screens
- [ ] User menu spans full width on mobile, dropdown on desktop
- [ ] No horizontal scroll at any width
- [ ] All clickable elements have 44×44px touch targets

---

## Phase 2: Browse/Discover View

**Priority:** HIGH — Entry point, high visibility  
**Effort:** 45 minutes  
**Files:** `frontend/src/App.tsx` (lines ~1375-1650)

### Changes Required

1. **Language selector cards — single column on mobile:**
```tsx
// Line ~1500 (approximate)
<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 max-w-2xl">
  {learnLangs.map(code => (
    <button className="rounded-2xl border p-5 flex items-center gap-4 ...">
      {/* Keep existing content */}
    </button>
  ))}
</div>
```

2. **Playlist cards — fluid width, responsive grid:**
```tsx
// Line ~1620 (approximate)
// REMOVE fixed width style={{ width: 300 }}
<div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-5">
  {matchingPlaylists.map(pl => (
    <button
      className="w-full text-left rounded-2xl border border-zinc-700/70 
                 overflow-hidden transition-all hover:border-zinc-500/60"
      style={{ background: '#18191f' }}
      // NO width: 300 here
    >
      {/* Cover image */}
      {/* Info section */}
    </button>
  ))}
</div>
```

3. **Text truncation for long titles:**
```tsx
<h3 className="text-white font-semibold text-base leading-snug mb-2 
               line-clamp-2 break-words">
  {tc(pl.name)}
</h3>
```

4. **Remove placeholder slots on mobile:**
```tsx
{/* Only show placeholders on desktop */}
<div className="hidden lg:grid lg:grid-cols-3 gap-5">
  {Array.from({ length: Math.max(0, 3 - matchingPlaylists.length) }).map((_, i) => (
    <div key={`placeholder-${i}`} 
         className="rounded-2xl border border-dashed border-zinc-700/60"
         style={{ aspectRatio: '1 / 1.45', background: '#18191f' }} />
  ))}
</div>
```

### Testing Checklist
- [ ] Language cards single column on phone (<640px)
- [ ] Playlist cards: 1 column mobile, 2 tablet, 3 desktop
- [ ] No fixed-width cards causing horizontal scroll
- [ ] Long playlist names truncate with ellipsis
- [ ] Card images scale proportionally
- [ ] Tap targets are 44×44px minimum
- [ ] Smooth scroll, no layout shift

---

## Phase 3: Playlist Detail View

**Priority:** HIGH — Core navigation flow  
**Effort:** 30 minutes  
**Files:** `frontend/src/App.tsx` (lines ~1430-1480)

### Changes Required

1. **Two-column → vertical stack on mobile:**
```tsx
// Line ~1430 (approximate)
<div className="flex flex-col lg:flex-row gap-6 lg:gap-8">
  
  {/* Left sidebar — top on mobile */}
  <div className="w-full lg:w-80 shrink-0">
    {/* Cover image — full width on mobile */}
    <div className="rounded-2xl overflow-hidden mb-4 w-full aspect-square">
      {/* cover */}
    </div>
    
    {/* Play button — full width on mobile */}
    <button className="w-full lg:w-auto px-6 py-3 rounded-xl 
                       bg-green-500 hover:bg-green-600 text-white 
                       font-semibold mb-4">
      {t('browser.play')}
    </button>
    
    {/* Stats */}
    <div className="rounded-2xl border border-zinc-700/70 divide-y ...">
      {/* Keep existing stats */}
    </div>
    
    {/* Description */}
    {activePlaylist.description && (
      <p className="text-gray-400 text-sm leading-relaxed mt-4">
        {tc(activePlaylist.description)}
      </p>
    )}
  </div>
  
  {/* Song list — below on mobile, right on desktop */}
  <div className="flex-1 min-w-0">
    <div className="mb-4 flex items-center justify-between gap-3">
      <h2 className="text-white font-semibold text-base md:text-lg">
        {t('browser.songs')}
      </h2>
    </div>
    {songList}
  </div>
</div>
```

2. **Song list items — increase touch targets:**
```tsx
// Each song button
<button className="w-full text-left p-4 md:p-3 rounded-xl 
                   hover:bg-zinc-800/50 transition-colors">
  {/* song info */}
</button>
```

### Testing Checklist
- [ ] Playlist info stacks above song list on mobile
- [ ] Cover image full-width on mobile, fixed 320px on desktop
- [ ] Play button full-width on mobile, auto-width on desktop
- [ ] Song list items have adequate touch spacing (4px min gap)
- [ ] Scroll works smoothly with finger swipe
- [ ] Back button remains accessible in header

---

## Phase 4: Player/Lyrics View

**Priority:** CRITICAL — Core learning experience  
**Effort:** 90 minutes  
**Files:** `frontend/src/components/LyricsPlayer.tsx`

### Changes Required

1. **Main layout — side-by-side → bottom sheet on mobile:**
```tsx
// Around line 800-900 (approximate, depends on component structure)
<div className="flex flex-col md:flex-row gap-4 h-full">
  
  {/* Lyrics panel — always visible */}
  <div className="flex-1 min-w-0 overflow-y-auto px-4 md:px-0">
    {/* Keep existing lyrics rendering */}
  </div>
  
  {/* Lookup panel — modal on mobile, sidebar on desktop */}
  {(activeWord || activeLine) && (
    <>
      {/* Mobile: full-screen overlay */}
      <div className="md:hidden fixed inset-0 bg-black/60 z-40" 
           onClick={closeLookup} />
      
      {/* Mobile: bottom sheet | Desktop: sidebar */}
      <div className={`
        fixed md:relative 
        inset-x-0 bottom-0 md:inset-auto 
        md:w-80 lg:w-96 
        bg-surface-card 
        rounded-t-3xl md:rounded-2xl 
        shadow-2xl md:shadow-none 
        z-50 md:z-auto
        max-h-[70vh] md:max-h-full 
        overflow-y-auto
        transition-transform duration-300 ease-out
        ${isClosing ? 'translate-y-full md:translate-y-0' : 'translate-y-0'}
      `}>
        {/* Drag handle — mobile only */}
        <div className="md:hidden sticky top-0 bg-surface-card 
                        pt-3 pb-2 flex justify-center">
          <div className="w-12 h-1.5 bg-gray-600 rounded-full" />
        </div>
        
        {/* Close button — mobile only */}
        <button 
          onClick={closeLookup}
          className="md:hidden absolute top-4 right-4 z-10 
                     w-10 h-10 rounded-full bg-zinc-800 
                     flex items-center justify-center">
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="white">
            <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
          </svg>
        </button>
        
        {/* Lookup content */}
        <div className="p-6 md:p-4">
          {/* Keep existing word/line lookup content */}
        </div>
      </div>
    </>
  )}
</div>
```

2. **Line number circles — larger touch targets:**
```tsx
// Line translation button
<button className="w-10 h-10 md:w-6 md:h-6 rounded-full 
                   border-2 border-zinc-600 hover:border-white 
                   flex items-center justify-center shrink-0">
  <svg className="w-5 h-5 md:w-3.5 md:h-3.5" />
</button>
```

3. **Disable keyboard shortcuts on mobile:**
```tsx
// Around line 513
useEffect(() => {
  const isMobile = window.matchMedia('(max-width: 767px)').matches
  if (isMobile) {
    // Skip keyboard shortcut registration
    return
  }
  
  // Existing keyboard handler for desktop
  const handleKey = (e: KeyboardEvent) => {
    // ... existing code
  }
  window.addEventListener('keydown', handleKey)
  return () => window.removeEventListener('keydown', handleKey)
}, [/* deps */])
```

4. **Word wrapping — prevent overflow:**
```tsx
<span className="inline cursor-pointer hover:bg-blue-500/20 
               rounded px-0.5 -mx-0.5 break-words">
  {word}
</span>
```

### Testing Checklist
- [ ] Lyrics display full-width on mobile
- [ ] Word tap opens bottom sheet (not sidebar)
- [ ] Bottom sheet swipe-down closes on mobile
- [ ] Line circles are 40×40px touch targets
- [ ] Keyboard shortcuts disabled on mobile
- [ ] No horizontal scroll in lyrics
- [ ] Long words wrap correctly
- [ ] Bottom sheet doesn't block playback controls
- [ ] Desktop layout unchanged (sidebar)

---

## Phase 5: Pricing Page

**Priority:** HIGH — Monetization-critical  
**Effort:** 30 minutes  
**Files:** `frontend/src/components/PricingPage.tsx`

### Changes Required

1. **Modal positioning — bottom-aligned on mobile:**
```tsx
// Around line 150
const containerClass = isPage 
  ? "min-h-screen flex items-center justify-center p-4" 
  : "fixed inset-0 z-50 flex items-end md:items-center justify-center 
     bg-black/70 backdrop-blur-sm p-0 md:p-4"

const bgClass = isPage
  ? "w-full max-w-4xl"
  : "bg-white w-full md:max-w-4xl md:rounded-2xl 
     rounded-t-3xl md:rounded-b-2xl 
     max-h-[95vh] overflow-y-auto"
```

2. **Pricing cards — single column on mobile:**
```tsx
// Grid layout for cards
<div className="grid grid-cols-1 md:grid-cols-2 gap-6 md:gap-8 mb-8">
  
  {/* Monthly card */}
  <div className="rounded-2xl border p-6 md:p-8 ...">
    {/* Keep existing structure */}
    
    {/* Button — larger on mobile */}
    <button className="w-full py-4 md:py-3 px-6 rounded-xl 
                       text-base md:text-sm font-semibold 
                       bg-blue-600 hover:bg-blue-700 text-white">
      {isAnnual ? 'Upgrade Yearly' : 'Upgrade Monthly'}
    </button>
  </div>
  
  {/* Annual card - same structure */}
</div>
```

3. **Toggle switch — larger on mobile:**
```tsx
<div className="flex items-center justify-center gap-4 mb-8">
  <span className={`text-base md:text-sm ...`}>Monthly</span>
  
  <button
    onClick={() => setIsAnnual(!isAnnual)}
    className={`
      relative inline-flex h-8 w-16 md:h-7 md:w-14 items-center rounded-full
      transition-colors ${isAnnual ? 'bg-blue-600' : 'bg-gray-300'}
    `}>
    <span className={`
      inline-block h-6 w-6 md:h-5 md:w-5 rounded-full bg-white 
      transition-transform
      ${isAnnual ? 'translate-x-9 md:translate-x-8' : 'translate-x-1'}
    `} />
  </button>
  
  <span className={`text-base md:text-sm ...`}>Yearly</span>
</div>
```

4. **Feature list — more spacing on mobile:**
```tsx
<ul className="space-y-3 md:space-y-2 mb-8">
  {features.map(f => (
    <li key={f} className="flex items-start gap-3">
      <svg className="w-5 h-5 md:w-4 md:h-4 shrink-0 mt-0.5 text-green-500" />
      <span className="text-sm md:text-xs text-gray-700">{f}</span>
    </li>
  ))}
</ul>
```

5. **Close button — larger touch target:**
```tsx
<button
  onClick={onClose}
  className="absolute top-4 right-4 md:top-6 md:right-6 
             w-10 h-10 md:w-8 md:h-8 
             rounded-full bg-gray-200 hover:bg-gray-300 
             flex items-center justify-center">
  <svg className="w-5 h-5 md:w-4 md:h-4" />
</button>
```

### Testing Checklist
- [ ] Modal slides up from bottom on mobile
- [ ] Cards stack vertically on mobile (<768px)
- [ ] Upgrade buttons full-width and easily tappable
- [ ] Toggle switch 32×64px on mobile (easy thumb reach)
- [ ] Feature list readable with adequate line spacing
- [ ] Close button 40×40px minimum
- [ ] No content cut off at bottom
- [ ] Smooth scroll within modal on mobile

---

## Phase 6: Lock Screen

**Priority:** MEDIUM — Monetization-adjacent  
**Effort:** 20 minutes  
**Files:** `frontend/src/components/LyricsLockScreen.tsx`

### Changes Required

1. **Reduce blur on mobile (performance):**
```tsx
// Main container
<div className="absolute inset-0 flex flex-col items-center justify-center 
                backdrop-blur-md md:backdrop-blur-xl 
                bg-black/50 md:bg-black/60 z-10 p-6 md:p-8">
```

2. **Text sizing — larger on mobile:**
```tsx
<h2 className="text-2xl sm:text-3xl md:text-4xl font-bold text-white 
               text-center mb-4 md:mb-6">
  {title}
</h2>

<p className="text-base sm:text-lg md:text-xl text-gray-300 text-center 
              mb-6 md:mb-8 max-w-md">
  {message}
</p>
```

3. **Feature list — more spacing:**
```tsx
{features && (
  <ul className="mb-8 md:mb-10 space-y-3 md:space-y-2 max-w-md">
    {features.map((f, i) => (
      <li key={i} className="flex items-start gap-3 text-gray-200">
        <svg className="w-5 h-5 md:w-4 md:h-4 shrink-0 mt-0.5" />
        <span className="text-sm md:text-xs">{f}</span>
      </li>
    ))}
  </ul>
)}
```

4. **Buttons — larger touch targets:**
```tsx
<div className="flex flex-col sm:flex-row gap-4 w-full max-w-md">
  {/* Primary button */}
  <button
    onClick={onUpgrade}
    className="flex-1 px-6 py-4 md:py-3 rounded-xl 
               text-base md:text-sm font-semibold 
               bg-blue-600 hover:bg-blue-700 text-white">
    Upgrade to Premium
  </button>
  
  {/* Secondary button */}
  {onBackToTrial && (
    <button
      onClick={onBackToTrial}
      className="flex-1 px-6 py-4 md:py-3 rounded-xl 
                 text-base md:text-sm font-semibold 
                 border-2 border-white/30 hover:border-white/50 
                 text-white">
      Back to Trial Songs
    </button>
  )}
</div>
```

### Testing Checklist
- [ ] Background blur doesn't lag on mobile
- [ ] Text readable and well-spaced
- [ ] Buttons stack vertically on narrow screens (<640px)
- [ ] Both buttons 48px height on mobile (easy tap)
- [ ] Content centered and doesn't overflow
- [ ] Buttons remain above virtual keyboard if triggered

---

## Phase 7: Settings & Auth

**Priority:** LOW — Secondary UI  
**Effort:** 30 minutes  
**Files:** `frontend/src/App.tsx` (Settings), auth components

### Settings Changes

1. **Setting rows — more padding on mobile:**
```tsx
<div className="rounded-2xl border border-gray-800/80 
                p-5 md:p-4" 
     style={{ background: '#12121f' }}>
  <div className="flex flex-col sm:flex-row items-start sm:items-center 
                  justify-between gap-4">
    <div className="flex-1">
      <p className="text-white font-medium text-base md:text-sm">{title}</p>
      <p className="text-sm md:text-xs text-gray-500 mt-1 leading-relaxed">
        {description}
      </p>
    </div>
    
    {/* Toggle — larger on mobile */}
    <button className="shrink-0 inline-flex h-8 w-14 md:h-7 md:w-12 
                       items-center rounded-full ...">
      <span className="inline-block h-6 w-6 md:h-5 md:w-5 
                       rounded-full bg-white ..." />
    </button>
  </div>
</div>
```

### Auth Changes

1. **Auth buttons — full-width on mobile:**
```tsx
<button className="w-full md:w-auto md:min-w-[280px] 
                   px-6 py-4 md:py-3 rounded-xl 
                   text-base md:text-sm font-semibold 
                   border border-gray-300 hover:border-gray-400 
                   flex items-center justify-center gap-3">
  <img src={googleIcon} className="w-6 h-6 md:w-5 md:h-5" />
  Sign in with Google
</button>
```

2. **Form inputs — larger on mobile:**
```tsx
<input
  type="email"
  placeholder="Email"
  className="w-full px-4 py-4 md:py-3 rounded-xl 
             text-base md:text-sm 
             border border-gray-300 focus:border-blue-500"
/>
```

### Testing Checklist
- [ ] Setting rows stack vertically on mobile
- [ ] Toggle switches 32×56px on mobile
- [ ] Auth buttons full-width on mobile, fixed-width on desktop
- [ ] Form inputs 48px height minimum
- [ ] All text readable (16px minimum on mobile to prevent zoom)
- [ ] No horizontal scroll on any settings screen

---

## Implementation Strategy

### For Each Phase:

1. **Make changes** in the specified file(s)
2. **Test locally** with Chrome DevTools device emulation:
   - iPhone SE (375×667) — smallest common phone
   - iPad (768×1024) — tablet
   - Desktop (1440×900) — ensure no regressions
3. **Test on real device** (at least iPhone or Android)
4. **Commit changes** with descriptive message
5. **Deploy** (optional after each phase, or bundle 2-3 phases)

### Commit Message Template:
```
Responsive Phase N: [Page Name]

- [Change 1]
- [Change 2]
- Tested on mobile/tablet/desktop
```

### If Issues Arise:
- Test in isolation: temporarily hide other content
- Use border colors to debug layout: `border-2 border-red-500`
- Check browser console for layout warnings
- Verify Tailwind classes compiled (check Network tab for updated CSS)

---

## Estimated Timeline

| Phase | Effort | Cumulative |
|-------|--------|------------|
| 1. Header & Navigation | 30 min | 30 min |
| 2. Browse/Discover | 45 min | 1h 15min |
| 3. Playlist Detail | 30 min | 1h 45min |
| 4. Player/Lyrics | 90 min | 3h 15min |
| 5. Pricing Page | 30 min | 3h 45min |
| 6. Lock Screen | 20 min | 4h 5min |
| 7. Settings & Auth | 30 min | 4h 35min |

**Total:** ~4.5 hours (including testing time)

You can do 1-2 phases per session, test thoroughly, and iterate before moving to the next.

---

## Success Criteria

After all phases complete:
- ✅ No horizontal scroll at 320px width
- ✅ All interactive elements ≥44×44px
- ✅ Desktop experience unchanged
- ✅ Smooth scrolling on 60Hz mobile devices
- ✅ Text readable without zoom (16px base size)
- ✅ Touch gestures work naturally (tap, scroll, swipe)
- ✅ Consistent behavior across iOS Safari, Chrome Android

