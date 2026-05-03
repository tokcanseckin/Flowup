import { useEffect, useRef, useState, useCallback } from 'react'
import { SongDetail } from '../api/client'

interface TooltipState {
  word: SongDetail['lines'][number]['words'][number]
  x: number   // page-relative X centre of the word element
  y: number   // page-relative top of the word element
  exiting: boolean
}

// ── Constants ─────────────────────────────────────────────────────────────────

const LINE_HEIGHT_PX  = 88
const VISIBLE_RADIUS  = 3
const TOOLTIP_LINGER_MS = 2500

// ── Helpers ───────────────────────────────────────────────────────────────────

function findActiveLineIndex(lines: SongDetail['lines'], posMs: number): number {
  if (posMs <= 0) return -1
  for (let i = lines.length - 1; i >= 0; i--) {
    if (posMs >= lines[i].start_time_ms && posMs < lines[i].end_time_ms) return i
  }
  return -1
}

function lineStyle(distance: number): { opacity: number; scale: number } {
  if (distance === 0) return { opacity: 1,    scale: 1    }
  if (distance === 1) return { opacity: 0.45, scale: 0.9  }
  if (distance === 2) return { opacity: 0.2,  scale: 0.82 }
  return                     { opacity: 0,    scale: 0.75 }
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  currentPositionMs: number
  songData: SongDetail
}

export default function LyricsPlayer({ currentPositionMs, songData }: Props) {
  const { lines, language } = songData
  const isRTL = language.direction === 'rtl'

  const activeIndex = findActiveLineIndex(lines, currentPositionMs)
  const activeLine  = activeIndex >= 0 ? lines[activeIndex] : null

  const wordRefs = useRef<Map<number, HTMLSpanElement>>(new Map())

  const [tooltip, setTooltip] = useState<TooltipState | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const exitRef  = useRef<ReturnType<typeof setTimeout> | null>(null)

  const dismissTooltip = useCallback(() => {
    setTooltip(prev => prev ? { ...prev, exiting: true } : null)
    exitRef.current = setTimeout(() => setTooltip(null), 260)
  }, [])

  // ── Keyboard handler ───────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return

      const num = parseInt(e.key, 10)
      if (isNaN(num) || num < 1 || num > 9) return
      if (!activeLine) return

      const word = activeLine.words.find(w => w.key === num)
      if (!word) return

      const el   = wordRefs.current.get(num)
      const rect = el?.getBoundingClientRect()

      if (timerRef.current) clearTimeout(timerRef.current)
      if (exitRef.current)  clearTimeout(exitRef.current)

      setTooltip({
        word,
        x: rect ? rect.left + rect.width / 2 : window.innerWidth / 2,
        y: rect ? rect.top  + window.scrollY  : 200,
        exiting: false,
      })
      timerRef.current = setTimeout(dismissTooltip, TOOLTIP_LINGER_MS)
    }

    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [activeLine, dismissTooltip])

  // Dismiss and clear refs on line change
  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    if (exitRef.current)  clearTimeout(exitRef.current)
    setTooltip(null)
    wordRefs.current.clear()
  }, [activeIndex])

  const setWordRef = useCallback((key: number, el: HTMLSpanElement | null) => {
    if (el) wordRefs.current.set(key, el)
    else    wordRefs.current.delete(key)
  }, [])

  // ── Tape geometry ──────────────────────────────────────────────────────────
  const CONTAINER_H   = 420
  const CENTER_OFFSET = CONTAINER_H / 2 - LINE_HEIGHT_PX / 2
  const tapeY = activeIndex >= 0
    ? CENTER_OFFSET - activeIndex * LINE_HEIGHT_PX
    : CENTER_OFFSET

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div
      className="relative select-none"
      style={{ height: CONTAINER_H }}
      dir={language.direction}
      lang={language.code}
    >
      {/* Fade masks */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-24 z-10"
           style={{ background: 'linear-gradient(to bottom, #12121f, transparent)' }} />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 z-10"
           style={{ background: 'linear-gradient(to top, #12121f, transparent)' }} />

      {/* Scrolling tape */}
      <div
        className="absolute inset-x-0 transition-transform duration-500 ease-out"
        style={{ transform: `translateY(${tapeY}px)` }}
      >
        {lines.map((line, idx) => {
          const distance = Math.abs(idx - activeIndex)
          if (distance > VISIBLE_RADIUS) return null

          const { opacity, scale } = lineStyle(distance)
          const isActive = distance === 0

          return (
            <div
              key={idx}
              className="flex flex-col items-center justify-center text-center px-8 transition-all duration-500"
              style={{ height: LINE_HEIGHT_PX, opacity, transform: `scale(${scale})` }}
            >
              {isActive ? (
                <ActiveLineContent
                  line={line}
                  isRTL={isRTL}
                  setWordRef={setWordRef}
                />
              ) : (
                <span className="stressed lyrics-text text-gray-400 text-lg leading-tight">
                  {line.original_line}
                </span>
              )}
            </div>
          )
        })}
      </div>

      {/* Empty state */}
      {activeIndex === -1 && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
          <p className="text-gray-600 text-sm">Waiting for playback…</p>
          <p className="text-gray-700 text-xs">Load a track and press Play</p>
        </div>
      )}

      {tooltip && <Tooltip state={tooltip} onDismiss={dismissTooltip} />}
    </div>
  )
}

// ── Active line ───────────────────────────────────────────────────────────────

interface ActiveLineProps {
  line: SongDetail['lines'][number]
  isRTL: boolean
  setWordRef: (key: number, el: HTMLSpanElement | null) => void
}

function ActiveLineContent({ line, isRTL, setWordRef }: ActiveLineProps) {
  return (
    <div className="animate-line-pop">
      <div
        className="flex flex-wrap items-baseline justify-center gap-x-2 gap-y-1"
        style={{ flexDirection: isRTL ? 'row-reverse' : 'row' }}
      >
        {line.words.map(word => (
          <span
            key={word.key}
            ref={el => setWordRef(word.key, el)}
            className="relative inline-flex items-start group cursor-default"
          >
            <span className="stressed lyrics-text text-white text-2xl font-semibold tracking-wide">
              {word.display_form}
            </span>
            <sup
              className="
                ml-0.5 text-[10px] font-mono font-medium leading-none
                text-indigo-400/70 group-hover:text-indigo-300
                transition-colors duration-150
              "
            >
              {word.key}
            </sup>
          </span>
        ))}
      </div>

      {/* Translation */}
      <p className="mt-3 text-indigo-300/80 text-sm italic tracking-wide">
        {line.translation}
      </p>

      <p className="mt-1.5 text-gray-700 text-xs">press a number key to inspect</p>
    </div>
  )
}

// ── Tooltip ───────────────────────────────────────────────────────────────────

interface TooltipProps {
  state: TooltipState
  onDismiss: () => void
}

function Tooltip({ state, onDismiss }: TooltipProps) {
  const { word, x, y, exiting } = state

  return (
    <div
      className={`fixed z-50 pointer-events-auto ${exiting ? 'animate-tooltip-exit' : 'animate-tooltip-enter'}`}
      style={{ left: x, top: y + window.scrollY, transform: 'translate(-50%, calc(-100% - 14px))' }}
      onClick={onDismiss}
    >
      {/* Arrow */}
      <div
        className="absolute left-1/2 bottom-0 -mb-[7px] -translate-x-1/2 w-3 h-3 rotate-45 rounded-sm"
        style={{ background: '#1e1e35', borderRight: '1px solid rgba(99,102,241,0.4)', borderBottom: '1px solid rgba(99,102,241,0.4)' }}
      />

      {/* Card */}
      <div
        className="rounded-2xl border px-5 py-4 shadow-2xl min-w-[200px] max-w-[280px]"
        style={{ background: '#1e1e35', borderColor: 'rgba(99,102,241,0.35)', boxShadow: '0 20px 60px rgba(0,0,0,0.6), 0 0 0 1px rgba(99,102,241,0.1)' }}
      >
        {/* Inflected / display form */}
        <p className="stressed text-2xl font-bold text-white leading-tight">
          {word.display_form}
        </p>

        {/* Lemma (dictionary base form) */}
        <p className="stressed text-base text-violet-300 font-medium mt-1">
          {word.lemma}
        </p>

        <div className="my-3 border-t border-indigo-900/50" />

        {/* Grammar */}
        <div className="flex items-start gap-2">
          <span className="shrink-0 text-[10px] font-mono font-medium text-indigo-500 uppercase tracking-wider mt-0.5">
            Grammar
          </span>
          <p className="text-gray-300 text-xs leading-snug">{word.grammar}</p>
        </div>

        {/* Definition */}
        <div className="flex items-start gap-2 mt-2">
          <span className="shrink-0 text-[10px] font-mono font-medium text-indigo-500 uppercase tracking-wider mt-0.5">
            Meaning
          </span>
          <p className="text-yellow-200 text-sm font-medium leading-snug">
            {word.dictionary_definition}
          </p>
        </div>

        <p className="mt-3 text-gray-700 text-[10px] text-right">click to dismiss</p>
      </div>
    </div>
  )
}
