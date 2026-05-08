import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { SongDetail } from '../api/client'

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

/** Returns the word.key values of the indexable (non-stop, has-definition) words in a line, max 9. */
function computeIndexedKeys(words: WordType[], langCode: string, filterStopWords: boolean): number[] {
  const result: number[] = []
  for (const w of words) {
    if (!isRealDefinition(w.dictionary_definition)) continue
    if (!filterStopWords || !isStopWord(w.lemma, langCode)) result.push(w.key)
    if (result.length === 9) break
  }
  return result
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
  songData: SongDetail
  filterStopWordsForIndexing?: boolean
  onInfoVisibilityChange?: (visible: boolean) => void
  onSeek?: (ms: number) => void
  onTogglePlayback?: () => void
}

export default function LyricsPlayer({
  currentPositionMs,
  songData,
  filterStopWordsForIndexing = true,
  onInfoVisibilityChange,
  onSeek,
  onTogglePlayback,
}: Props) {
  const { lines, language } = songData
  const isRTL = language.direction === 'rtl'
  const langCode = language.code

  const activeIndex = findActiveLineIndex(lines, currentPositionMs)
  const activeLine = activeIndex >= 0 ? lines[activeIndex] : null

  // Pre-compute indexed (non-stop) word keys for the active line
  const indexedWordKeys = useMemo(
    () => activeLine ? computeIndexedKeys(activeLine.words, langCode, filterStopWordsForIndexing) : [],
    [activeLine, langCode, filterStopWordsForIndexing]
  )

  const [isPhone, setIsPhone] = useState(false)
  const [inspectState, setInspectState] = useState<InspectState | null>(null)

  const containerRef = useRef<HTMLDivElement | null>(null)
  const lineRefs = useRef<Map<number, HTMLDivElement>>(new Map())

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
    if (inspectState.mode !== 'hold') return
    if (inspectState.target.lineIndex !== activeIndex) clearInspect()
  }, [activeIndex, inspectState, clearInspect])

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement ||
        (e.target instanceof HTMLElement && e.target.isContentEditable)
      ) return

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
    }
  }, [])

  const setLineRef = useCallback((index: number, el: HTMLDivElement | null) => {
    if (el) lineRefs.current.set(index, el)
    else lineRefs.current.delete(index)
  }, [])

  useEffect(() => {
    if (activeIndex < 0) return

    const container = containerRef.current
    const activeEl = lineRefs.current.get(activeIndex)
    if (!container || !activeEl) return

    const containerRect = container.getBoundingClientRect()
    const activeRect = activeEl.getBoundingClientRect()
    const targetScrollTop =
      container.scrollTop + (activeRect.top - containerRect.top) - containerRect.height / 2 + activeRect.height / 2

    container.scrollTo({ top: targetScrollTop, behavior: 'smooth' })
  }, [activeIndex])

  return (
    <div className={isPhone ? 'relative h-full' : 'grid grid-cols-[minmax(0,1fr)_300px] gap-4 h-full'}>
      <div
        ref={containerRef}
        className="relative select-none overflow-y-auto h-full"
        dir={language.direction}
        lang={language.code}
      >
        <div className="flex flex-col gap-1 py-4 px-3">
          {lines.map((line, idx) => {
            const isActive = idx === activeIndex
            const lineTarget: InspectTarget = { type: 'line', lineIndex: idx }
            const circleActive = !!inspectState && inspectState.target.type === 'line' && inspectState.target.lineIndex === idx

            return (
              <div
                key={idx}
                ref={el => setLineRef(idx, el)}
                className={`rounded-xl transition-colors duration-200 ${isActive ? 'bg-indigo-900/30' : ''}`}
              >
                <div className="flex items-start gap-3 px-3 py-3">
                  <button
                    type="button"
                    className={`mt-1 h-5 w-5 shrink-0 rounded-full border transition-colors ${
                      circleActive
                        ? 'border-indigo-300 bg-indigo-500/30'
                        : 'border-gray-600 bg-gray-800/30 hover:border-indigo-500/70'
                    }`}
                    aria-label={`Inspect translation for line ${idx + 1}`}
                    onPointerDown={() => startPointerPress(lineTarget)}
                    onPointerUp={() => endPointerPress(false)}
                    onPointerCancel={() => endPointerPress(true)}
                    onPointerLeave={() => endPointerPress(true)}
                  />

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
                      />
                    ) : (
                      <span
                        className={`stressed lyrics-text text-lg leading-tight ${
                          activeIndex === -1 || idx < activeIndex ? 'text-gray-600' : 'text-gray-400'
                        } ${onSeek ? 'cursor-pointer hover:text-gray-200 transition-colors duration-150' : ''}`}
                        onClick={() => onSeek?.(line.start_time_ms)}
                      >
                        {line.original_line}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        {activeIndex === -1 && (
          <div className="flex flex-col items-center justify-center gap-2 py-16">
            <p className="text-gray-600 text-sm">Waiting for playback...</p>
            <p className="text-gray-700 text-xs">Load a track and press Play</p>
          </div>
        )}

        {isPhone && inspectInfo && (
          <div className="absolute inset-x-3 bottom-3 z-40">
            <InspectPanel info={inspectInfo} compact onClose={clearInspect} />
          </div>
        )}
      </div>

      {!isPhone && (
        <aside className="rounded-2xl border border-gray-800/80 p-4 h-fit sticky top-3" style={{ background: '#12121f' }}>
          {inspectInfo ? (
            <InspectPanel info={inspectInfo} onClose={clearInspect} />
          ) : (
            <div className="text-sm text-gray-500 leading-relaxed">
              <p className="text-gray-400 font-medium mb-2">Inspect lyrics</p>
              <p>Click a word or press 1-9 to inspect a word.</p>
              <p className="mt-1">Click a circle or press 0 for sentence translation.</p>
              <p className="mt-1">Hold tap/click/key for temporary peek.</p>
            </div>
          )}
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
}

function ActiveLineContent({
  line,
  lineIndex,
  isRTL,
  hideWordIndexes,
  indexedWordKeys,
  startPointerPress,
  endPointerPress,
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

          return (
            <button
              type="button"
              key={word.key}
              className="relative inline-flex items-start group cursor-pointer rounded-md px-0.5"
              onPointerDown={() => startPointerPress(target)}
              onPointerUp={() => endPointerPress(false)}
              onPointerCancel={() => endPointerPress(true)}
              onPointerLeave={() => endPointerPress(true)}
            >
              <span className="stressed lyrics-text text-white text-2xl font-semibold tracking-wide">
                {word.display_form}
              </span>
              {!hideWordIndexes && displayIdx >= 0 && (
                <sup
                  className="
                    ml-0.5 text-[10px] font-mono font-medium leading-none
                    text-indigo-400/70 group-hover:text-indigo-300
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

      {!hideWordIndexes && (
        <p className="mt-1.5 text-gray-700 text-xs">press number keys for words, 0 for line</p>
      )}
    </div>
  )
}

interface InspectPanelProps {
  info: InspectInfo
  onClose: () => void
  compact?: boolean
}

function InspectPanel({ info, onClose, compact = false }: InspectPanelProps) {
  const isWord = info.kind === 'word'

  return (
    <div
      className={`rounded-2xl border shadow-2xl ${compact ? 'px-4 py-3' : 'px-5 py-4'}`}
      style={{
        background: '#1e1e35',
        borderColor: 'rgba(99,102,241,0.35)',
        boxShadow: '0 20px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(99,102,241,0.1)',
      }}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-[10px] font-mono font-medium text-indigo-500 uppercase tracking-wider">
          {isWord ? 'Definition' : 'Translation'}
        </p>
        <button
          type="button"
          onClick={onClose}
          className="text-xs text-gray-400 hover:text-gray-200 transition-colors"
          aria-label="Close inspector"
        >
          Close
        </button>
      </div>

      {isWord ? (
        <>
          {/* ── Definition — most prominent ── */}
          {isRealDefinition(info.word.dictionary_definition) ? (() => {
            const meanings = info.word.dictionary_definition!.split(';').map(s => s.trim()).filter(Boolean)
            return meanings.length === 1
              ? <p className="text-lg font-semibold text-yellow-200 leading-snug mt-1">{meanings[0]}</p>
              : (
                <ol className="mt-1 space-y-0.5 list-none pl-0">
                  {meanings.map((m, i) => (
                    <li key={i} className="flex gap-1.5 text-base font-semibold text-yellow-200 leading-snug">
                      <span className="text-[11px] font-mono text-yellow-500/70 mt-0.5 shrink-0">{i + 1}.</span>
                      <span>{m}</span>
                    </li>
                  ))}
                </ol>
              )
          })() : <p className="text-gray-500 italic text-base font-normal mt-1">No definition yet</p>}

          <div className="my-3 border-t border-indigo-900/50" />

          {/* ── Tapped word form ── */}
          <p className="stressed text-2xl font-bold text-white leading-tight">{info.word.display_form}</p>

          {/* ── POS + morphological details ── */}
          {info.word.grammar && (() => {
            const firstReading = info.word.grammar.split(' / ')[0]
            const parts = firstReading.split(',').map(s => s.trim())
            const pos = parts[0]
            const ASPECTS = new Set(['Perfective', 'Imperfective'])
            const aspect = parts.find(p => ASPECTS.has(p)) ?? null
            const detail = parts.slice(1).filter(p => !ASPECTS.has(p)).join(' · ')
            return (
              <div className="mt-2 flex flex-wrap items-baseline gap-2">
                <span className="rounded-md bg-indigo-900/50 px-2 py-0.5 text-[11px] font-semibold text-indigo-300 uppercase tracking-wide">
                  {pos}
                </span>
                {aspect && (
                  <span className="rounded-md bg-violet-900/50 px-2 py-0.5 text-[11px] font-semibold text-violet-300 uppercase tracking-wide">
                    {aspect}
                  </span>
                )}
                {detail && (
                  <span className="text-xs text-gray-400">{detail}</span>
                )}
              </div>
            )
          })()}

          {/* ── Nominative / infinitive (lemma) ── */}
          <p className="text-sm text-violet-300 font-medium mt-2">
            <span className="text-[10px] font-mono text-gray-600 uppercase tracking-wide mr-1.5">
              {info.word.grammar?.startsWith('Verb') ? 'infinitive' : 'nominative'}
            </span>
            {info.word.lemma}
          </p>
        </>
      ) : (
        <>
          <p className="text-indigo-200 text-lg font-semibold leading-snug mt-1">
            {info.line.translation || 'No translation available for this line yet'}
          </p>
          <div className="my-3 border-t border-indigo-900/50" />
          <p className="stressed text-white text-sm leading-relaxed opacity-70">{info.line.original_line}</p>
        </>
      )}
    </div>
  )
}
