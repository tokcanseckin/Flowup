import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { SongDetail } from '../api/client'
import { useWordHistory } from '../hooks/useWordHistory'
import translateIconImg from '../../images/translate_icon@2x.png'
import { useT } from '../i18n/LocalizationContext'
import ReportModal from './ReportModal'

// ── Stop-word sets (keyed by language code) ─────────────────────────────────
// These words are still clickable, but don't consume a keyboard index (1-9).

const STOP_WORDS: Record<string, Set<string>> = {
  ru: new Set([
    // pronouns
    'я', 'ты', 'он', 'она', 'оно', 'мы', 'вы', 'они', 'себя', 'свой', 'своя', 'своё', 'свои',
    // prepositions
    'в', 'во', 'на', 'с', 'со', 'из', 'к', 'ко', 'у', 'от', 'за', 'по', 'до', 'при', 'без', 'для', 'о', 'об', 'обо', 'под', 'над',
    'перед', 'через', 'про', 'между', 'около', 'после', 'против', 'вокруг',
    // conjunctions & particles
    'и', 'или', 'но', 'а', 'да', 'же', 'ведь', 'даже', 'уже', 'ещё', 'еще', 'тоже', 'то',
    'что', 'как', 'так', 'не', 'ни', 'бы', 'ли', 'ну', 'вот', 'тут', 'там',
    // demonstratives
    'этот', 'эта', 'это', 'эти', 'тот', 'та', 'те', 'всё', 'весь', 'все', 'сам', 'сама', 'само', 'сами',
  ]),
  it: new Set([
    // pronouns
    'io', 'tu', 'lui', 'lei', 'noi', 'voi', 'loro', 'mi', 'ti', 'ci', 'vi', 'si',
    'lo', 'la', 'li', 'le', 'me', 'te', 'ne', 'gli', 'egli', 'ella', 'esso', 'essa',
    // prepositions
    'di', 'a', 'in', 'con', 'su', 'per', 'da', 'tra', 'fra', 'sotto', 'sopra',
    'dentro', 'fuori', 'dopo', 'prima', 'durante', 'senza', 'verso', 'contro', 'entro',
    // conjunctions & particles
    'e', 'o', 'ma', 'se', 'che', 'però', 'anche', 'pure', 'già', 'ancora', 'sempre',
    'mai', 'non', 'né', 'come', 'quando', 'dove', 'poi', 'ora', 'più', 'meno',
    // articles & demonstratives (lemma form)
    'il', 'lo', 'la', 'i', 'gli', 'le', 'un', 'uno', 'una',
    'questo', 'questa', 'questi', 'queste', 'quello', 'quella', 'quelli', 'quelle',
    'tutto', 'tutta', 'tutti', 'tutte', 'altro', 'altra', 'altri', 'altre',
    'ogni', 'qualche', 'quale', 'quali',
  ]),
}

/** Returns true if word should be excluded from numbered keyboard indexing. */
function isStopWord(lemma: string, langCode: string): boolean {
  const set = STOP_WORDS[langCode]
  if (!set) return false
  // Strip combining diacritics (stress marks, etc.) before lookup so that
  // lemmas like "на́" correctly match the plain "на" entry in the set.
  const normalized = lemma.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
  return set.has(normalized)
}

/**
 * Returns true when a definition string is meaningful — i.e. not null, empty,
 * or a bracket-wrapped pipeline placeholder like "[тёплый]" or "[love]".
 */
function isRealDefinition(def: string | null | undefined): def is string {
  if (!def) return false
  const t = def.trim()
  return !(t.startsWith('[') && t.endsWith(']'))
}

function stripBoundaryPunctuation(value: string): string {
  // Keep inner punctuation (e.g. don't -> don't), remove noisy edge punctuation.
  // Preserve leading ( and trailing ) since they appear in dictionary definitions like "(new) settlement".
  return value.replace(/^[^\p{L}\p{N}(]+|[^\p{L}\p{N})]+$/gu, '').trim()
}

function parseHueFromColor(color: string | undefined): number | null {
  if (!color) return null
  const m = color.match(/hsla?\(\s*([0-9.]+)/i)
  if (!m) return null
  const hue = Number(m[1])
  if (!Number.isFinite(hue)) return null
  return ((hue % 360) + 360) % 360
}

/** Returns the word.key values of the indexable (non-stop, has-definition) words in a line, max 9. */
function computeIndexedKeys(words: WordType[], langCode: string, _filterStopWords: boolean): number[] {
  // Collect all words that have a real definition, preserving original order.
  const candidates = words.filter(w => isRealDefinition(w.dictionary_definition))

  // If 9 or fewer words have definitions, index them all — no stop words skipped.
  if (candidates.length <= 9) return candidates.slice(0, 9).map(w => w.key)

  // More than 9: we need to drop some. Sacrifice stop words one-by-one, shortest
  // lemma first (ties broken by position), until we're at 9.
  const stopSet = candidates
    .filter(w => isStopWord(w.lemma, langCode))
    // Sort stops: shorter stripped lemma first, then by original position.
    .sort((a, b) => {
      const la = a.lemma.normalize('NFD').replace(/[\u0300-\u036f]/g, '').length
      const lb = b.lemma.normalize('NFD').replace(/[\u0300-\u036f]/g, '').length
      return la - lb
    })

  const sacrificed = new Set<number>()
  let idx = 0
  while (candidates.length - sacrificed.size > 9 && idx < stopSet.length) {
    sacrificed.add(stopSet[idx].key)
    idx++
  }

  return candidates.filter(w => !sacrificed.has(w.key)).slice(0, 9).map(w => w.key)
}

type LineType = SongDetail['lines'][number]
type WordType = LineType['words'][number]

type InspectTarget =
  | { type: 'line'; lineIndex: number }
  | { type: 'word'; lineIndex: number; wordKey: number }

interface InspectState {
  target: InspectTarget
  mode: 'pinned' | 'hold'
}

type InspectInfo =
  | { kind: 'line'; line: LineType }
  | { kind: 'word'; line: LineType; word: WordType }

const HOLD_DELAY_MS = 220
const BREAK_THRESHOLD_MS = 5_000

function useAnimatedBackground(background: string, durationMs = 420) {
  const [baseBg, setBaseBg] = useState(background)
  const [nextBg, setNextBg] = useState(background)
  const [showNext, setShowNext] = useState(false)
  const activeBgRef = useRef(background)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    if (background === activeBgRef.current) return

    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }

    setBaseBg(activeBgRef.current)
    setNextBg(background)
    setShowNext(false)

    const raf = requestAnimationFrame(() => setShowNext(true))
    timerRef.current = window.setTimeout(() => {
      setBaseBg(background)
      setShowNext(false)
      activeBgRef.current = background
      timerRef.current = null
    }, durationMs)

    return () => {
      cancelAnimationFrame(raf)
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
  }, [background, durationMs])

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
      }
    }
  }, [])

  return { baseBg, nextBg, showNext }
}

// ── Break indicator ───────────────────────────────────────────────────────────

interface BreakProps {
  startMs: number
  endMs: number
  currentPositionMs: number
  isPlaying: boolean
  label?: string
}

function BreakIndicator({ startMs, endMs, currentPositionMs, isPlaying, label }: BreakProps) {
  const duration = endMs - startMs
  const isActive = currentPositionMs >= startMs && currentPositionMs < endMs
  const isPast   = currentPositionMs >= endMs

  // ── Seek detection ────────────────────────────────────────────────────────
  // If the position jumps more than 1 s from the linearly-expected value,
  // bump seekKey so the fill div remounts and the animation restarts correctly.
  const seekKeyRef = useRef(0)
  const prevPosRef = useRef(currentPositionMs)
  const prevTsRef  = useRef(Date.now())
  const now = Date.now()
  if (isActive) {
    const expected = prevPosRef.current + (now - prevTsRef.current)
    if (Math.abs(currentPositionMs - expected) > 1000) seekKeyRef.current++
  }
  prevPosRef.current = currentPositionMs
  prevTsRef.current  = now

  // ── Keep fresh values in refs so callback ref sees them at mount ──────────
  const isPlayingRef  = useRef(isPlaying)
  const currentPosRef = useRef(currentPositionMs)
  isPlayingRef.current  = isPlaying
  currentPosRef.current = currentPositionMs

  // fillElRef: stable handle to the fill bar DOM node
  const fillElRef = useRef<HTMLDivElement | null>(null)

  // Callback ref: fires ONLY when the element mounts (or remounts after seek).
  // Sets animation-delay from position at that instant — never updated again,
  // so the browser compositor runs the animation freely at 60 fps.
  const fillRefCallback = useCallback((el: HTMLDivElement | null) => {
    fillElRef.current = el
    if (!el) return
    const elapsed = Math.max(0, currentPosRef.current - startMs)
    el.style.animationName         = 'break-fill'
    el.style.animationDuration     = `${duration}ms`
    el.style.animationDelay        = `-${elapsed}ms`
    el.style.animationTimingFunction = 'linear'
    el.style.animationFillMode     = 'forwards'
    el.style.animationPlayState    = isPlayingRef.current ? 'running' : 'paused'
  }, [startMs, duration]) // stable; reads live values via refs

  // Update animationPlayState only — never touch animation-delay again.
  useEffect(() => {
    const el = fillElRef.current
    if (!el) return
    el.style.animationPlayState = isPlaying ? 'running' : 'paused'
  }, [isPlaying])

  return (
    <div className="px-6 py-[0.9rem]">
      <div className="flex items-center gap-3">
        <div className="w-4 shrink-0" />
        <div className="flex items-center gap-3 flex-1 min-w-0">
          {label && (
            <span className={`text-[11px] shrink-0 font-mono uppercase tracking-[0.18em] ${isActive ? 'text-white/75' : isPast ? 'text-white/45' : 'text-white/70'}`}>{label}</span>
          )}
          <div className="relative flex-1 h-1 rounded-full bg-white/20 overflow-hidden mr-7">
            {isPast && (
              <div className="absolute inset-0 rounded-full bg-white/45" />
            )}
            {isActive && (
              <div
                key={seekKeyRef.current}
                ref={fillRefCallback}
                className="absolute inset-y-0 left-0 rounded-full bg-white"
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function findActiveLineIndex(lines: SongDetail['lines'], posMs: number): number {
  if (posMs <= 0) return -1
  for (let i = lines.length - 1; i >= 0; i--) {
    if (posMs >= lines[i].start_time_ms && posMs < lines[i].end_time_ms) return i
  }
  return -1
}

function sameTarget(a: InspectTarget, b: InspectTarget): boolean {
  if (a.type !== b.type) return false
  if (a.lineIndex !== b.lineIndex) return false
  if (a.type === 'word' && b.type === 'word') return a.wordKey === b.wordKey
  return true
}

function resolveInspectInfo(lines: SongDetail['lines'], state: InspectState | null): InspectInfo | null {
  if (!state) return null

  const target = state.target
  const line = lines[target.lineIndex]
  if (!line) return null

  if (target.type === 'line') {
    return { kind: 'line', line }
  }

  const word = line.words.find(w => w.key === target.wordKey)
  if (!word) return null
  return { kind: 'word', line, word }
}

interface Props {
  currentPositionMs: number
  durationMs?: number
  isPlaying: boolean
  songData: SongDetail
  targetLang?: string
  themeBackground?: string
  themeAsideBackground?: string
  accentTextColor?: string
  filterStopWordsForIndexing?: boolean
  onInfoVisibilityChange?: (visible: boolean) => void
  onSeek?: (ms: number) => void
  onTogglePlayback?: () => void
}

export default function LyricsPlayer({
  currentPositionMs,
  durationMs = 0,
  isPlaying,
  songData,
  targetLang = 'en',
  themeBackground,
  themeAsideBackground,
  accentTextColor,
  filterStopWordsForIndexing = true,
  onInfoVisibilityChange,
  onSeek,
  onTogglePlayback,
}: Props) {
  const t = useT()
  const { lines, language } = songData
  const isRTL = language.direction === 'rtl'
  const langCode = language.code

  const activeIndex = findActiveLineIndex(lines, currentPositionMs)
  const activeLine = activeIndex >= 0 ? lines[activeIndex] : null

  // Pre-compute break slots: gaps > 5 s before/between/after lines
  const breakSlots = useMemo(() => {
    const slots: { startMs: number; endMs: number; label?: string; beforeLineIndex: number }[] = []
    if (lines.length === 0) return slots
    // Intro
    const firstStart = lines[0].start_time_ms
    if (firstStart >= BREAK_THRESHOLD_MS) {
      slots.push({ startMs: 0, endMs: firstStart, label: 'intro', beforeLineIndex: 0 })
    }
    // Between lines: end[i] == start[i+1] (lrclib format), so each line "owns"
    // the silence after it. Detect breaks by comparing each line's duration
    // against the typical (median) line duration.
    const allDurations = lines.slice(0, -1).map(l => l.end_time_ms - l.start_time_ms)
    const normalDurations = allDurations.filter(d => d < 12_000).sort((a, b) => a - b)
    const medianDuration = normalDurations.length > 0
      ? normalDurations[Math.floor(normalDurations.length / 2)]
      : 3_000
    for (let i = 0; i < lines.length - 1; i++) {
      const lineDuration = lines[i].end_time_ms - lines[i].start_time_ms
      const excessMs = lineDuration - medianDuration
      if (excessMs >= BREAK_THRESHOLD_MS) {
        // Break starts after the estimated singing portion, ends at next line start
        const breakStart = lines[i].start_time_ms + medianDuration
        const breakEnd = lines[i].end_time_ms
        slots.push({ startMs: breakStart, endMs: breakEnd, beforeLineIndex: i + 1 })
      }
    }
    // Outro
    const lastEnd = lines[lines.length - 1].end_time_ms
    const totalMs = durationMs > lastEnd ? durationMs : 0
    if (totalMs > 0 && totalMs - lastEnd >= BREAK_THRESHOLD_MS) {
      slots.push({ startMs: lastEnd, endMs: totalMs, label: 'outro', beforeLineIndex: lines.length })
    }
    return slots
  }, [lines, durationMs])

  // Pre-compute indexed (non-stop) word keys for the active line
  const indexedWordKeys = useMemo(
    () => activeLine ? computeIndexedKeys(activeLine.words, langCode, filterStopWordsForIndexing) : [],
    [activeLine, langCode, filterStopWordsForIndexing]
  )

  const [isPhone, setIsPhone] = useState(false)
  const [inspectState, setInspectState] = useState<InspectState | null>(null)

  const containerRef = useRef<HTMLDivElement | null>(null)
  const lineRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const scrollAnimRef = useRef<number | null>(null)

  const pointerPressRef = useRef<{
    timer: ReturnType<typeof setTimeout> | null
    holdShown: boolean
    target: InspectTarget | null
  }>({ timer: null, holdShown: false, target: null })

  const keyPressRef = useRef<{
    timer: ReturnType<typeof setTimeout> | null
    key: string | null
    holdShown: boolean
    target: InspectTarget | null
  }>({ timer: null, key: null, holdShown: false, target: null })

  const inspectInfo = resolveInspectInfo(lines, inspectState)
  const { entries: wordHistoryEntries, recordLookup } = useWordHistory(true)
  // Only underline words looked up in the same language as this song
  const lookedUpLemmas = useMemo(
    () => new Set(wordHistoryEntries.filter(e => e.language === langCode && e.target_lang === targetLang).map(e => e.lemma)),
    [wordHistoryEntries, langCode, targetLang],
  )
  const prevInspectStateRef = useRef<InspectState | null>(null)
  const panelBackground = themeBackground ?? 'linear-gradient(180deg, #1a57bf 0%, #0f46a8 100%)'
  const asideBackground = themeAsideBackground ?? '#184f9b'
  const auraHue = parseHueFromColor(accentTextColor) ?? 320
  const panelBgAnim = useAnimatedBackground(panelBackground)
  const asideBgAnim = useAnimatedBackground(asideBackground)

  const clearInspect = useCallback(() => {
    setInspectState(null)
  }, [])

  const togglePinned = useCallback((target: InspectTarget) => {
    setInspectState(prev => {
      if (prev && prev.mode === 'pinned' && sameTarget(prev.target, target)) return null
      return { target, mode: 'pinned' }
    })
  }, [])

  const startPointerPress = useCallback((target: InspectTarget) => {
    const ref = pointerPressRef.current
    if (ref.timer) clearTimeout(ref.timer)

    ref.target = target
    ref.holdShown = false
    ref.timer = setTimeout(() => {
      ref.holdShown = true
      setInspectState({ target, mode: 'hold' })
    }, HOLD_DELAY_MS)
  }, [])

  const endPointerPress = useCallback((cancelOnly = false) => {
    const ref = pointerPressRef.current
    if (ref.timer) {
      clearTimeout(ref.timer)
      ref.timer = null
    }

    const target = ref.target
    if (!target) return

    if (ref.holdShown) {
      clearInspect()
    } else if (!cancelOnly) {
      togglePinned(target)
    }

    ref.target = null
    ref.holdShown = false
  }, [clearInspect, togglePinned])

  const keyboardTargetFor = useCallback((key: string): InspectTarget | null => {
    if (key === '0') {
      if (activeIndex < 0) return null
      return { type: 'line', lineIndex: activeIndex }
    }

    const num = parseInt(key, 10)
    if (Number.isNaN(num) || num < 1 || num > 9 || !activeLine) return null

    // Map keyboard number to the nth indexable (non-stop) word
    const wordKey = indexedWordKeys[num - 1]
    if (wordKey === undefined) return null

    return { type: 'word', lineIndex: activeIndex, wordKey }
  }, [activeIndex, activeLine, indexedWordKeys])

  useEffect(() => {
    const media = window.matchMedia('(max-width: 767px)')
    const onChange = () => setIsPhone(media.matches)
    onChange()
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    onInfoVisibilityChange?.(inspectState !== null)
  }, [inspectState, onInfoVisibilityChange])

  useEffect(() => {
    if (!inspectState) return
    if (inspectState.target.lineIndex !== activeIndex) clearInspect()
  }, [activeIndex, inspectState, clearInspect])

  // Record word lookups when a word becomes pinned
  useEffect(() => {
    const prev = prevInspectStateRef.current
    prevInspectStateRef.current = inspectState
    if (!inspectState || inspectState.mode !== 'pinned' || inspectState.target.type !== 'word') return
    // Skip if same word is already pinned (no change)
    if (
      prev?.mode === 'pinned' &&
      prev.target.type === 'word' &&
      prev.target.lineIndex === inspectState.target.lineIndex &&
      prev.target.wordKey === inspectState.target.wordKey
    ) return
    const line = lines[inspectState.target.lineIndex]
    const wordTarget = inspectState.target
    const word = wordTarget.type === 'word' ? line?.words.find(w => w.key === wordTarget.wordKey) : undefined
    if (word && targetLang !== langCode && isRealDefinition(word.dictionary_definition)) recordLookup(word, songData, targetLang)
  }, [inspectState, lines, songData, recordLookup, targetLang, langCode])

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement ||
        (e.target instanceof HTMLElement && e.target.isContentEditable)
      ) return

      // Escape = close inspect panel
      if (e.key === 'Escape') {
        e.preventDefault()
        clearInspect()
        return
      }

      // Space = toggle playback
      if (e.key === ' ' || e.key === 'Spacebar' || e.code === 'Space') {
        e.preventDefault()
        onTogglePlayback?.()
        return
      }

      // Q = previous line, E = next line
      if (e.key === 'q' || e.key === 'Q' || e.key === 'e' || e.key === 'E') {
        e.preventDefault()
        if (!onSeek || lines.length === 0) return
        const isPrev = e.key === 'q' || e.key === 'Q'
        const targetIndex = isPrev
          ? Math.max(0, activeIndex <= 0 ? 0 : activeIndex - 1)
          : Math.min(lines.length - 1, activeIndex < 0 ? 0 : activeIndex + 1)
        onSeek(lines[targetIndex].start_time_ms)
        return
      }

      if (e.metaKey || e.ctrlKey) return
      const target = keyboardTargetFor(e.key)
      if (!target) return
      e.preventDefault()

      if (e.repeat) return

      const ref = keyPressRef.current
      if (ref.timer) clearTimeout(ref.timer)

      ref.key = e.key
      ref.target = target
      ref.holdShown = false
      ref.timer = setTimeout(() => {
        ref.holdShown = true
        setInspectState({ target, mode: 'hold' })
      }, HOLD_DELAY_MS)
    }

    const onKeyUp = (e: KeyboardEvent) => {
      const ref = keyPressRef.current
      if (ref.key !== e.key || !ref.target) return

      if (ref.timer) {
        clearTimeout(ref.timer)
        ref.timer = null
      }

      if (ref.holdShown) {
        clearInspect()
      } else {
        togglePinned(ref.target)
      }

      ref.key = null
      ref.target = null
      ref.holdShown = false
    }

    const onBlur = () => {
      const ref = keyPressRef.current
      if (ref.timer) {
        clearTimeout(ref.timer)
        ref.timer = null
      }
      if (ref.holdShown) clearInspect()
      ref.key = null
      ref.target = null
      ref.holdShown = false
    }

    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', onBlur)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', onBlur)
    }
  }, [keyboardTargetFor, clearInspect, togglePinned, lines, activeIndex, onSeek, onTogglePlayback])

  useEffect(() => {
    return () => {
      if (pointerPressRef.current.timer) clearTimeout(pointerPressRef.current.timer)
      if (keyPressRef.current.timer) clearTimeout(keyPressRef.current.timer)
      if (scrollAnimRef.current !== null) cancelAnimationFrame(scrollAnimRef.current)
    }
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    if (scrollAnimRef.current !== null) {
      cancelAnimationFrame(scrollAnimRef.current)
      scrollAnimRef.current = null
    }

    container.scrollTop = 0
  }, [songData.id])

  const setLineRef = useCallback((index: number, el: HTMLDivElement | null) => {
    if (el) lineRefs.current.set(index, el)
    else lineRefs.current.delete(index)
  }, [])

  useEffect(() => {
    if (activeIndex < 0) return

    const container = containerRef.current
    const activeEl = lineRefs.current.get(activeIndex)
    if (!container || !activeEl) return

    if (scrollAnimRef.current !== null) {
      cancelAnimationFrame(scrollAnimRef.current)
      scrollAnimRef.current = null
    }

    const containerRect = container.getBoundingClientRect()
    const activeRect = activeEl.getBoundingClientRect()
    const rawTargetScrollTop =
      container.scrollTop + (activeRect.top - containerRect.top) - containerRect.height / 2 + activeRect.height / 2
    const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight)
    const targetScrollTop = Math.max(0, Math.min(rawTargetScrollTop, maxScrollTop))

    const startScrollTop = container.scrollTop
    const distance = targetScrollTop - startScrollTop
    if (Math.abs(distance) < 1) return

    const durationMs = Math.min(520, Math.max(240, 220 + Math.abs(distance) * 0.18))
    const startTs = performance.now()

    const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3)

    const step = (ts: number) => {
      const elapsed = ts - startTs
      const t = Math.min(1, elapsed / durationMs)
      container.scrollTop = startScrollTop + distance * easeOutCubic(t)
      if (t < 1) {
        scrollAnimRef.current = requestAnimationFrame(step)
      } else {
        scrollAnimRef.current = null
      }
    }

    scrollAnimRef.current = requestAnimationFrame(step)
  }, [activeIndex])

  return (
    <div className={isPhone ? 'relative h-full min-h-0' : 'grid grid-cols-[minmax(0,1fr)_420px] gap-0 h-full min-h-0'}>
      <div
        className="relative h-full min-h-0"
        dir={language.direction}
        lang={language.code}
      >
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute inset-0" style={{ background: panelBgAnim.baseBg }} />
          <div
            className="absolute inset-0 transition-opacity duration-300 ease-out"
            style={{
              background: panelBgAnim.nextBg,
              opacity: panelBgAnim.showNext ? 1 : 0,
            }}
          />
          <div
            className="absolute -inset-[22%] opacity-60 blur-3xl animate-aurora-one"
            style={{
              background:
                `radial-gradient(circle at 26% 28%, hsla(${auraHue}, 100%, 62%, 0.55) 0%, hsla(${auraHue}, 100%, 62%, 0) 44%), radial-gradient(circle at 76% 72%, hsla(${auraHue}, 90%, 50%, 0.38) 0%, hsla(${auraHue}, 90%, 50%, 0) 42%)`,
            }}
          />
          <div
            className="absolute -inset-[18%] opacity-52 blur-3xl animate-aurora-two"
            style={{
              background:
                `radial-gradient(circle at 70% 18%, hsla(${auraHue}, 92%, 55%, 0.32) 0%, hsla(${auraHue}, 92%, 55%, 0) 48%), radial-gradient(circle at 14% 78%, hsla(${auraHue}, 85%, 48%, 0.25) 0%, hsla(${auraHue}, 85%, 48%, 0) 42%)`,
            }}
          />
        </div>

        <div
          ref={containerRef}
          className="relative z-10 h-full min-h-0 select-none overflow-y-auto no-scrollbar"
        >
          <div className="flex flex-col gap-1 pt-8 pb-8 px-0">
          {lines.map((line, idx) => {
            const isActive = idx === activeIndex
            const isPrev = idx === activeIndex - 1
            const isNext = idx === activeIndex + 1
            const lineTarget: InspectTarget = { type: 'line', lineIndex: idx }
            const circleActive = !!inspectState && inspectState.target.type === 'line' && inspectState.target.lineIndex === idx
            const breakBefore = breakSlots.find(b => b.beforeLineIndex === idx)

            return (
              <div key={idx}>
                {breakBefore && (
                  <BreakIndicator
                    startMs={breakBefore.startMs}
                    endMs={breakBefore.endMs}
                    currentPositionMs={currentPositionMs}
                    isPlaying={isPlaying}
                    label={breakBefore.label}
                  />
                )}
                <div
                ref={el => setLineRef(idx, el)}
                className={`transition-colors duration-200 ${isActive ? 'bg-white/12' : ''} ${isPrev ? 'opacity-75' : ''} ${isNext ? 'opacity-90' : ''}`}
              >
                <div className="flex items-start gap-3 px-6 py-3">
                  <div className="mt-1.5 h-4 w-4 shrink-0">
                    {isActive && (
                      <button
                        type="button"
                        className={`relative h-4 w-4 transition-opacity ${circleActive ? 'opacity-100' : 'opacity-60 hover:opacity-100'}`}
                        aria-label={`Inspect translation for line ${idx + 1}`}
                        onPointerDown={() => startPointerPress(lineTarget)}
                        onPointerUp={() => endPointerPress(false)}
                        onPointerCancel={() => endPointerPress(true)}
                        onPointerLeave={() => endPointerPress(true)}
                      >
                        <img src={translateIconImg} className="w-full h-full object-contain" alt="" />
                        <sup className="absolute -top-3.5 -right-2.5 text-[10px] font-mono font-medium leading-none text-yellow-200/95">
                          {0}
                        </sup>
                      </button>
                    )}
                  </div>

                  <div className="min-w-0 flex-1">
                    {isActive ? (
                      <ActiveLineContent
                        line={line}
                        lineIndex={idx}
                        isRTL={isRTL}
                        hideWordIndexes={isPhone}
                        indexedWordKeys={indexedWordKeys}
                        startPointerPress={startPointerPress}
                        endPointerPress={endPointerPress}
                        lookedUpLemmas={lookedUpLemmas}
                      />
                    ) : (
                      <span
                        className={`stressed lyrics-text text-lg leading-tight ${
                          isPrev ? 'text-white/60' : isNext ? 'text-white/80' : (activeIndex === -1 || idx < activeIndex ? 'text-white/45' : 'text-white/70')
                        } ${onSeek ? 'cursor-pointer hover:text-gray-200 transition-colors duration-120' : ''}`}
                        onClick={() => onSeek?.(line.start_time_ms)}
                      >
                        {line.original_line}
                      </span>
                    )}
                  </div>
                </div>
                </div>
              </div>
            )
          })}
          {(() => {
            const outro = breakSlots.find(b => b.beforeLineIndex === lines.length)
            return outro ? (
              <BreakIndicator
                startMs={outro.startMs}
                endMs={outro.endMs}
                currentPositionMs={currentPositionMs}
                isPlaying={isPlaying}
                label={outro.label}
              />
            ) : null
          })()}
          </div>

          {lines.length === 0 && (
            <div className="flex flex-col items-center justify-center gap-2 py-16">
              <p className="text-gray-600 text-sm">{t('player.waitingForPlayback')}</p>
              <p className="text-gray-700 text-xs">{t('player.loadAndPlay')}</p>
            </div>
          )}
        </div>

        {isPhone && inspectInfo && (
          <div className="absolute inset-x-3 bottom-3 z-40">
            <InspectPanel
              key={inspectInfo.kind === 'word' ? `w-${inspectInfo.line.start_time_ms}-${inspectInfo.word.key}` : `l-${inspectInfo.line.start_time_ms}`}
              info={inspectInfo}
              compact
              onClose={clearInspect}
              accentTextColor={accentTextColor}
              songTitle={songData.title}
              songId={songData.id}
            />
          </div>
        )}
      </div>

      {!isPhone && (
        <aside className="relative p-6 h-full min-h-0 overflow-hidden">
          <div className="pointer-events-none absolute inset-0">
            <div className="absolute inset-0" style={{ background: asideBgAnim.baseBg }} />
            <div
              className="absolute inset-0 transition-opacity duration-300 ease-out"
              style={{
                background: asideBgAnim.nextBg,
                opacity: asideBgAnim.showNext ? 1 : 0,
              }}
            />
            <div
              className="absolute -inset-[20%] opacity-50 blur-3xl animate-aurora-two"
              style={{
                background:
                  `radial-gradient(circle at 82% 14%, hsla(${auraHue}, 100%, 61%, 0.42) 0%, hsla(${auraHue}, 100%, 61%, 0) 48%), radial-gradient(circle at 16% 74%, hsla(${auraHue}, 90%, 52%, 0.30) 0%, hsla(${auraHue}, 90%, 52%, 0) 42%)`,
              }}
            />
            <div
              className="absolute -inset-[16%] opacity-44 blur-3xl animate-aurora-one"
              style={{
                background:
                  `radial-gradient(circle at 24% 24%, hsla(${auraHue}, 88%, 54%, 0.26) 0%, hsla(${auraHue}, 88%, 54%, 0) 50%), radial-gradient(circle at 78% 70%, hsla(${auraHue}, 82%, 48%, 0.20) 0%, hsla(${auraHue}, 82%, 48%, 0) 44%)`,
              }}
            />
          </div>

          <div className="relative z-10">
            {inspectInfo ? (
              <InspectPanel
                key={inspectInfo.kind === 'word' ? `w-${inspectInfo.line.start_time_ms}-${inspectInfo.word.key}` : `l-${inspectInfo.line.start_time_ms}`}
                info={inspectInfo}
                onClose={clearInspect}
                accentTextColor={accentTextColor}
                songTitle={songData.title}
                songId={songData.id}
              />
            ) : (
              <div className="rounded-xl border border-white/20 px-8 py-7 text-white/85 animate-panel-in">
                <p className="text-white/50 font-semibold uppercase tracking-widest text-[12px] mb-4">{t('inspect.title')}</p>
                <div className="flex flex-col gap-3 text-[13px] leading-snug">
                  <div className="flex items-start gap-3">
                    <div className="flex gap-1 shrink-0 mt-0.5">
                      {['1','–','9'].map(k => (
                        <kbd key={k} className={`inline-flex items-center justify-center rounded px-1.5 py-0.5 text-[11px] font-mono font-medium leading-none ${k === '–' ? 'bg-transparent text-white/30 px-0' : 'bg-white/10 text-white/70 border border-white/15'}`}>{k}</kbd>
                      ))}
                    </div>
                    <span className="text-white/60">{t('inspect.numberedWord')}</span>
                  </div>
                  <div className="flex items-start gap-3">
                    <kbd className="shrink-0 mt-0.5 inline-flex items-center justify-center rounded px-1.5 py-0.5 text-[11px] font-mono font-medium leading-none bg-white/10 text-white/70 border border-white/15">0</kbd>
                    <span className="text-white/60">{t('inspect.sentenceTranslation')}</span>
                  </div>
                  <div className="flex items-start gap-3">
                    <span className="shrink-0 mt-0.5 text-white/30 text-[11px] font-medium leading-none">{t('inspect.hold')}</span>
                    <span className="text-white/60">{t('inspect.peekWithoutPinning')}</span>
                  </div>
                  <div className="flex items-start gap-3">
                    <kbd className="shrink-0 mt-0.5 inline-flex items-center justify-center rounded px-2 py-0.5 text-[11px] font-mono font-medium leading-none bg-white/10 text-white/70 border border-white/15">Space</kbd>
                    <span className="text-white/60">{t('inspect.playPause')}</span>
                  </div>
                  <div className="flex items-start gap-3">
                    <div className="flex gap-1 shrink-0 mt-0.5">
                      {['Q','E'].map(k => (
                        <kbd key={k} className="inline-flex items-center justify-center rounded px-1.5 py-0.5 text-[11px] font-mono font-medium leading-none bg-white/10 text-white/70 border border-white/15">{k}</kbd>
                      ))}
                    </div>
                    <span className="text-white/60">{t('inspect.seekPrevNextLine')}</span>
                  </div>
                  <div className="flex items-start gap-3">
                    <div className="flex gap-1 shrink-0 mt-0.5">
                      {['←','→'].map(k => (
                        <kbd key={k} className="inline-flex items-center justify-center rounded px-1.5 py-0.5 text-[11px] font-mono font-medium leading-none bg-white/10 text-white/70 border border-white/15">{k}</kbd>
                      ))}
                    </div>
                    <span className="text-white/60">{t('inspect.prevNextSong')}</span>
                  </div>
                </div>
              </div>
            )}
          </div>
        </aside>
      )}
    </div>
  )
}

interface ActiveLineProps {
  line: LineType
  lineIndex: number
  isRTL: boolean
  hideWordIndexes: boolean
  indexedWordKeys: number[]
  startPointerPress: (target: InspectTarget) => void
  endPointerPress: (cancelOnly?: boolean) => void
  lookedUpLemmas: Set<string>
}

function ActiveLineContent({
  line,
  lineIndex,
  isRTL,
  hideWordIndexes,
  indexedWordKeys,
  startPointerPress,
  endPointerPress,
  lookedUpLemmas,
}: ActiveLineProps) {
  return (
    <div className="animate-line-pop">
      <div
        className="flex flex-wrap items-baseline gap-x-2 gap-y-1"
        style={{
          flexDirection: isRTL ? 'row-reverse' : 'row',
          justifyContent: isRTL ? 'flex-end' : 'flex-start',
        }}
      >
        {line.words.map(word => {
          const target: InspectTarget = { type: 'word', lineIndex, wordKey: word.key }
          const displayIdx = indexedWordKeys.indexOf(word.key)  // -1 for stop words
          const isLooked = lookedUpLemmas.has(word.lemma)

          return (
            <button
              type="button"
              key={word.key}
              className="relative inline-flex items-start group cursor-pointer rounded-md px-0.5 transition-transform duration-150 hover:-translate-y-[1px]"
              onPointerDown={() => startPointerPress(target)}
              onPointerUp={() => endPointerPress(false)}
              onPointerCancel={() => endPointerPress(true)}
              onPointerLeave={() => endPointerPress(true)}
            >
              <span
                className="stressed lyrics-text text-white text-2xl font-semibold tracking-wide"
                style={isLooked ? {
                  textDecorationLine: 'underline',
                  textDecorationColor: 'rgba(74,222,128,0.55)',
                  textDecorationThickness: '1.5px',
                  textUnderlineOffset: '4px',
                } : undefined}
              >
                {word.display_form}
              </span>
              {!hideWordIndexes && displayIdx >= 0 && (
                <sup
                  className="
                    ml-0.5 text-[10px] font-mono font-medium leading-none
                    text-yellow-200/95 group-hover:text-yellow-100
                    transition-colors duration-150
                  "
                >
                  {displayIdx + 1}
                </sup>
              )}
            </button>
          )
        })}
      </div>

    </div>
  )
}

interface InspectPanelProps {
  info: InspectInfo
  onClose: () => void
  compact?: boolean
  accentTextColor?: string
  songTitle?: string
  songId?: number | null
}

function InspectPanel({ info, onClose, compact = false, accentTextColor = 'hsl(320, 88%, 38%)', songId }: InspectPanelProps) {
  const t = useT()
  const isWord = info.kind === 'word'
  const cleanDisplayForm = isWord ? stripBoundaryPunctuation(info.word.display_form) : null

  const [menuOpen, setMenuOpen] = useState(false)
  const [showReportModal, setShowReportModal] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [menuOpen])

  const reportPayload = isWord
    ? { kind: 'word' as const, song_id: songId ?? undefined, word: info.word.display_form, lemma: info.word.lemma, context: info.line.original_line }
    : { kind: 'line' as const, song_id: songId ?? undefined, context: info.line.original_line }

  return (
    <div
      className={`rounded-xl border shadow-2xl animate-panel-in ${compact ? 'px-4 py-3' : 'px-10 py-8'}`}
      style={{
        background: '#f2f2f2',
        borderColor: 'rgba(0,0,0,0.16)',
        boxShadow: '0 12px 32px rgba(0,0,0,0.28)',
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs leading-none font-bold text-zinc-900 uppercase tracking-wide">
          {isWord ? t('inspect.definition') : t('inspect.translation')}
        </p>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onClose}
            className="text-xs text-gray-500 hover:text-gray-700 transition-colors"
            aria-label="Close inspector"
          >
            {t('inspect.close')}
          </button>
          <div className={`relative ${compact ? '-mr-1' : '-mr-2'}`} ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen(o => !o)}
              className="text-gray-400 hover:text-gray-600 transition-colors p-0.5 rounded"
              aria-label="More options"
            >
              <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
                <circle cx="12" cy="5" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="12" cy="19" r="1.5" />
              </svg>
            </button>
            {menuOpen && (
              <div className="absolute right-0 top-full mt-1 z-50 min-w-[160px] rounded-lg border border-gray-200 bg-white shadow-lg py-1">
                <button
                  type="button"
                  onClick={() => { setMenuOpen(false); setShowReportModal(true) }}
                  className="w-full text-left px-3 py-2 text-xs text-red-500 hover:bg-red-50 transition-colors flex items-center gap-2"
                >
                  <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 shrink-0 fill-none stroke-current" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
                  </svg>
                  {t('browser.reportProblem')}
                </button>
              </div>
            )}
            <ReportModal open={showReportModal} onClose={() => setShowReportModal(false)} payload={reportPayload} />
          </div>
        </div>
      </div>

      {isWord ? (
        <>
          {/* ── Definition — most prominent ── */}
          {isRealDefinition(info.word.dictionary_definition) ? (() => {
            const meanings = info.word.dictionary_definition!
              .split(';')
              .map(s => stripBoundaryPunctuation(s.trim()))
              .filter(Boolean)
            return meanings.length === 1
              ? <p className="text-2xl leading-tight font-bold mt-2" style={{ color: accentTextColor }}>{meanings[0]}</p>
              : (
                <ol className="mt-2 space-y-1 list-none pl-0">
                  {meanings.map((m, i) => (
                    <li key={i} className="grid grid-cols-[1.75rem_minmax(0,1fr)] items-start gap-1.5 text-xl leading-tight font-bold" style={{ color: accentTextColor }}>
                      <span className="leading-tight font-bold text-right tabular-nums" style={{ color: accentTextColor }}>{i + 1}.</span>
                      <span className="leading-tight">{m}</span>
                    </li>
                  ))}
                </ol>
              )
          })() : <p className="text-gray-500 italic text-base font-normal mt-1">{t('inspect.noDefinition')}</p>}

          <div className="my-4 border-t border-zinc-300" />

          {/* ── Tapped word form ── */}
          <p className="stressed text-xl leading-tight font-semibold text-black">{cleanDisplayForm || info.word.display_form}</p>

          {/* ── POS + morphological details ── */}
          {info.word.grammar && (() => {
            const firstReading = info.word.grammar.split(' / ')[0]
            const parts = firstReading.split(',').map(s => s.trim())
            const pos = parts[0]
            const ASPECTS = new Set(['Perfective', 'Imperfective'])
            const aspect = parts.find(p => ASPECTS.has(p)) ?? null
            // Translate a grammar token via localization key grammar.<term>
            const tg = (term: string) => {
              const key = 'grammar.' + term.replace(/\s+/g, '_').replace(/[()]/g, '').replace(/_+/g, '_').replace(/_$/, '')
              const translated = t(key)
              return translated !== key ? translated : term
            }
            const detail = parts.slice(1).filter(p => !ASPECTS.has(p)).map(tg).join(' · ')
            return (
              <div className="mt-3 flex flex-wrap items-baseline gap-2">
                <span className="px-0 py-0 text-base leading-none font-bold text-sky-500 uppercase tracking-wide">
                  {tg(pos)}
                </span>
                {aspect && (
                  <span className="px-0 py-0 text-base leading-none font-semibold text-emerald-400 uppercase tracking-wide">
                    {tg(aspect)}
                  </span>
                )}
                {detail && (
                  <span className="text-sm text-zinc-700">{detail}</span>
                )}
              </div>
            )
          })()}

          {/* ── Nominative / infinitive (lemma) ── */}
          <p className="text-base leading-tight font-semibold mt-3" style={{ color: accentTextColor }}>
            <span className="text-[11px] leading-tight font-medium text-zinc-500 uppercase tracking-wide mr-2">
              {info.word.grammar?.startsWith('Verb') ? t('inspect.infinitive') : t('inspect.nominative')}
            </span>
            {info.word.lemma}
          </p>
        </>
      ) : (
        <>
          <p className="text-lg font-semibold leading-snug mt-1" style={{ color: accentTextColor }}>
            {info.line.translation || t('inspect.noTranslation')}
          </p>
          <div className="my-3 border-t border-zinc-300" />
          <p className="stressed text-zinc-700 text-base leading-relaxed opacity-80">{info.line.original_line}</p>
        </>
      )}
    </div>
  )
}
