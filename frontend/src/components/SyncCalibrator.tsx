/**
 * SyncCalibrator — inline YouTube playback + live lyrics sync preview for
 * the admin song-edit page.
 *
 * Features:
 *  • Embeds the YouTube player directly so lyrics can be tested without
 *    leaving the editor.
 *  • Tracks the playback position (250 ms resolution from the IFrame API)
 *    and highlights whichever lyric line is currently active.
 *  • Lets the editor apply a global offset (ms) to all line timestamps:
 *      positive offset → lyrics play later (shift timestamps forward)
 *      negative offset → lyrics play earlier (shift timestamps back)
 *  • Clicking a lyric line seeks the player to that line's start.
 *  • "Apply offset & Save" bakes the offset into lyricsDraft in the parent
 *    and immediately persists.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import type { AdminSongDetail } from '../api/client'
import YouTubePlayer, { YouTubePlayerHandle } from './YouTubePlayer'

type Line = AdminSongDetail['lines'][number]

interface Props {
  youtubeUrl: string
  lines: Line[]
  /**
   * Called with the current offset (ms). The parent must shift all line
   * timestamps by offsetMs and persist them atomically in one call.
   */
  onApplyAndSave: (offsetMs: number) => Promise<void>
  saving: boolean
}

// ── helpers ───────────────────────────────────────────────────────────────────

function formatMs(ms: number): string {
  const sign = ms < 0 ? '-' : ''
  const abs = Math.abs(Math.round(ms))
  const min = Math.floor(abs / 60000)
  const sec = Math.floor((abs % 60000) / 1000)
  const tenth = Math.floor((abs % 1000) / 100)
  if (min > 0) return `${sign}${min}:${String(sec).padStart(2, '0')}.${tenth}`
  return `${sign}${sec}.${tenth}s`
}

// ── component ─────────────────────────────────────────────────────────────────

export default function SyncCalibrator({ youtubeUrl, lines, onApplyAndSave, saving }: Props) {
  const playerRef = useRef<YouTubePlayerHandle>(null)
  const lineEls = useRef<(HTMLButtonElement | null)[]>([])

  const [positionMs, setPositionMs] = useState(0)
  const [offsetMs, setOffsetMs] = useState(0)
  const [applying, setApplying] = useState(false)

  // ── active line ────────────────────────────────────────────────────────────
  // Compute which line should be highlighted using the *shifted* position so
  // the user sees the effect of the offset live before committing it.
  // adjusted = positionMs - offsetMs maps real playback time back to the
  // coordinate space of the original timestamps.
  const adjusted = positionMs - offsetMs
  let activeLineIndex = -1
  for (let i = lines.length - 1; i >= 0; i--) {
    if (adjusted >= lines[i].start_time_ms) {
      activeLineIndex = i
      break
    }
  }

  // Auto-scroll active line into view
  useEffect(() => {
    if (activeLineIndex >= 0) {
      lineEls.current[activeLineIndex]?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [activeLineIndex])

  // ── controls ───────────────────────────────────────────────────────────────
  const nudge = (delta: number) => setOffsetMs(prev => prev + delta)

  const seekToLine = useCallback((line: Line) => {
    // Seek to the line's start, shifted by the current preview offset.
    playerRef.current?.seekTo(line.start_time_ms + offsetMs)
  }, [offsetMs])

  const handleApplyAndSave = useCallback(async () => {
    setApplying(true)
    try {
      await onApplyAndSave(offsetMs)
      setOffsetMs(0)
    } finally {
      setApplying(false)
    }
  }, [offsetMs, onApplyAndSave])

  const busy = applying || saving

  // ── render ─────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-4">
      {/* ── YouTube player ─────────────────────────────────────────────────── */}
      <div style={{ maxWidth: 480 }}>
        <YouTubePlayer
          ref={playerRef}
          youtubeUrl={youtubeUrl}
          onTimeUpdate={setPositionMs}
        />
      </div>

      {/* ── position readout ───────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-4 text-xs font-mono text-gray-500">
        <span>Position <span className="text-gray-300">{formatMs(positionMs)}</span></span>
        <span>Effective <span className="text-gray-300">{formatMs(adjusted)}</span></span>
        {offsetMs !== 0 && (
          <span className={`font-semibold ${offsetMs > 0 ? 'text-amber-400' : 'text-sky-400'}`}>
            Offset {offsetMs > 0 ? '+' : ''}{offsetMs} ms
          </span>
        )}
      </div>

      {/* ── offset controls ────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-gray-500 mr-1">Offset</span>
        {[-1000, -500, -100, -50].map(d => (
          <button
            key={d}
            type="button"
            onClick={() => nudge(d)}
            className="rounded-lg border border-gray-700 bg-gray-900/60 px-2.5 py-1 text-xs text-gray-300 hover:border-gray-500 hover:text-white transition-colors"
          >
            {d} ms
          </button>
        ))}
        <input
          type="number"
          value={offsetMs}
          onChange={e => setOffsetMs(Number(e.target.value))}
          step={50}
          className="w-28 rounded-lg border border-gray-700 bg-gray-900/70 px-2 py-1 text-center text-xs text-white focus:outline-none focus:border-indigo-500"
        />
        {[50, 100, 500, 1000].map(d => (
          <button
            key={d}
            type="button"
            onClick={() => nudge(d)}
            className="rounded-lg border border-gray-700 bg-gray-900/60 px-2.5 py-1 text-xs text-gray-300 hover:border-gray-500 hover:text-white transition-colors"
          >
            +{d} ms
          </button>
        ))}
        <button
          type="button"
          onClick={() => setOffsetMs(0)}
          disabled={offsetMs === 0}
          className="rounded-lg border border-gray-700 bg-gray-900/60 px-2.5 py-1 text-xs text-gray-400 hover:border-gray-500 disabled:opacity-30 transition-colors"
        >
          Reset
        </button>
      </div>

      {/* ── live lyrics preview ────────────────────────────────────────────── */}
      <div className="max-h-56 overflow-y-auto rounded-2xl border border-gray-800 bg-gray-950/30 p-3 space-y-0.5">
        {lines.length === 0 && (
          <p className="px-3 py-2 text-xs text-gray-600">No lyrics loaded yet.</p>
        )}
        {lines.map((line, index) => (
          <button
            key={line.id}
            type="button"
            ref={el => { lineEls.current[index] = el }}
            onClick={() => seekToLine(line)}
            className={`w-full text-left rounded-lg px-3 py-1.5 text-sm leading-snug transition-colors ${
              index === activeLineIndex
                ? 'bg-indigo-600/30 text-white font-semibold ring-1 ring-inset ring-indigo-500/40'
                : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/40'
            }`}
          >
            <span className="text-[10px] font-mono text-gray-600 mr-2">{formatMs(line.start_time_ms)}</span>
            {line.original_line}
          </button>
        ))}
      </div>

      {/* ── apply & save ──────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => void handleApplyAndSave()}
          disabled={busy}
          className="rounded-xl bg-indigo-600 px-5 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:bg-gray-800 disabled:text-gray-500 transition-colors"
        >
          {busy
            ? 'Saving...'
            : offsetMs !== 0
              ? `Apply ${offsetMs > 0 ? '+' : ''}${offsetMs} ms & Save`
              : 'Save lyrics'}
        </button>
        {offsetMs !== 0 && (
          <p className="text-xs text-gray-500">
            Shifts every line's timestamps by <span className="text-amber-300">{offsetMs > 0 ? '+' : ''}{offsetMs} ms</span>
          </p>
        )}
      </div>
    </div>
  )
}
