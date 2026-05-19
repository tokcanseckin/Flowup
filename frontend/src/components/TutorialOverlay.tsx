import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react'
import { useT } from '../i18n/LocalizationContext'

export type TutorialStep = {
  id: string
  /** CSS selector OR a function returning the element */
  target: string | (() => HTMLElement | null)
  text: string
  /** Padding around the highlight cutout (px) */
  padding?: number
  /** Force a side; otherwise auto-picked */
  side?: 'top' | 'bottom' | 'left' | 'right' | 'auto'
  /** Optional: scroll target into view before showing */
  scrollIntoView?: boolean
  /**
   * Interactive step: no dark backdrop, overlay doesn't block clicks.
   * The highlighted element is fully interactive.
   */
  interactive?: boolean
}

export type TutorialHandle = { advance: () => void }

type Props = {
  steps: TutorialStep[]
  open: boolean
  onClose: () => void
  onComplete: () => void
  /** Called whenever the active step index changes */
  onStepChange?: (index: number) => void
}

type Rect = { top: number; left: number; width: number; height: number }

const BUBBLE_MAX_W = 280
const BUBBLE_MIN_W = 180
const GAP = 12          // gap between target and bubble
const VIEWPORT_MARGIN = 16

export const TutorialOverlay = forwardRef<TutorialHandle, Props>(function TutorialOverlay(
  { steps, open, onClose, onComplete, onStepChange },
  ref
) {
  const [index, setIndex] = useState(0)
  const [targetRect, setTargetRect] = useState<Rect | null>(null)
  const [viewport, setViewport] = useState({ w: window.innerWidth, h: window.innerHeight })
  const bubbleRef = useRef<HTMLDivElement | null>(null)
  const [bubbleSize, setBubbleSize] = useState({ w: 240, h: 100 })
  const t = useT()

  const step = steps[index]
  const isLast = index === steps.length - 1
  const isInteractive = step?.interactive ?? false

  const handleNext = useCallback(() => {
    if (isLast) { onComplete(); return }
    setIndex(i => i + 1)
  }, [isLast, onComplete])

  useImperativeHandle(ref, () => ({ advance: handleNext }), [handleNext])

  useEffect(() => {
    onStepChange?.(index)
  }, [index, onStepChange])

  // Measure target whenever step changes / window changes
  useLayoutEffect(() => {
    if (!open || !step) return
    let raf = 0
    let retryTimer = 0
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(measure)
    })
    function measure() {
      const el = typeof step.target === 'string'
        ? document.querySelector<HTMLElement>(step.target)
        : step.target()
      if (!el) {
        setTargetRect(null)
        retryTimer = window.setTimeout(measure, 200)
        return
      }
      clearTimeout(retryTimer)
      if (step.scrollIntoView) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
      const r = el.getBoundingClientRect()
      setTargetRect({ top: r.top, left: r.left, width: r.width, height: r.height })
      ro.disconnect()
      ro.observe(el)
    }
    measure()
    const onResize = () => { setViewport({ w: innerWidth, h: innerHeight }); raf = requestAnimationFrame(measure) }
    window.addEventListener('resize', onResize)
    window.addEventListener('scroll', onResize, true)
    return () => {
      window.removeEventListener('resize', onResize)
      window.removeEventListener('scroll', onResize, true)
      ro.disconnect()
      cancelAnimationFrame(raf)
      clearTimeout(retryTimer)
    }
  }, [open, step])

  // Measure bubble size after render
  useLayoutEffect(() => {
    if (bubbleRef.current) {
      const r = bubbleRef.current.getBoundingClientRect()
      setBubbleSize({ w: r.width, h: r.height })
    }
  }, [step?.text, targetRect])

  // Keyboard: Esc to skip, Enter/→ next
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'Enter' || e.key === 'ArrowRight') handleNext()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, handleNext, onClose])

  if (!open || !step) return null

  const padding = step.padding ?? 6
  const cutout = targetRect ? {
    top: targetRect.top - padding,
    left: targetRect.left - padding,
    width: targetRect.width + padding * 2,
    height: targetRect.height + padding * 2,
  } : null

  const placement = computePlacement(cutout, bubbleSize, viewport, step.side ?? 'auto')

  return (
    // Outer wrapper: pointer-events none so all clicks pass through to the app.
    // Only the bubble itself gets pointer-events re-enabled.
    <div className="fixed inset-0 z-[9999]" style={{ pointerEvents: 'none' }}>
      {/* SVG mask backdrop with cutout */}
      <svg className="absolute inset-0 w-full h-full" style={{ pointerEvents: 'none' }}>
        <defs>
          <mask id="tutorial-mask">
            <rect width="100%" height="100%" fill="white" />
            {cutout && (
              <rect
                x={cutout.left} y={cutout.top}
                width={cutout.width} height={cutout.height}
                rx={Math.min(12, cutout.height / 4)}
                fill="black"
              />
            )}
          </mask>
        </defs>
        {/* Dark backdrop: hidden for interactive steps */}
        {!isInteractive && (
          <rect width="100%" height="100%" fill="rgba(0,0,0,0.45)" mask="url(#tutorial-mask)" />
        )}
        {/* Highlight ring */}
        {cutout && (
          <rect
            x={cutout.left} y={cutout.top}
            width={cutout.width} height={cutout.height}
            rx={Math.min(12, cutout.height / 4)}
            fill="none"
            stroke="rgb(236, 72, 153)"
            strokeWidth={2}
          />
        )}
      </svg>

      {/* Callout bubble */}
      {cutout && (
        <div
          ref={bubbleRef}
          className="absolute bg-pink-200 text-neutral-900 rounded-lg shadow-xl p-3 text-sm leading-snug"
          style={{
            top: placement.top,
            left: placement.left,
            maxWidth: BUBBLE_MAX_W,
            minWidth: BUBBLE_MIN_W,
            pointerEvents: 'auto',
          }}
        >
          {/* Step number badge */}
          <div className="absolute -top-3 -left-3 w-7 h-7 rounded-full bg-pink-500 text-white font-bold flex items-center justify-center text-xs shadow">
            {index + 1}
          </div>

          <p className="leading-snug">{renderStepText(step.text)}</p>

          <div className="flex items-center justify-between mt-3">
            <button onClick={onClose} className="text-xs text-neutral-600 hover:underline">
              {t('tutorial.skip')}
            </button>
            <button
              onClick={handleNext}
              className="text-xs font-semibold bg-neutral-900 text-white px-3 py-1.5 rounded"
            >
              {isLast ? t('tutorial.done') : t('tutorial.next')}
            </button>
          </div>

          {/* Arrow */}
          <Arrow side={placement.side} offset={placement.arrowOffset} />
        </div>
      )}
    </div>
  )
})

function renderStepText(text: string): React.ReactNode {
  const parts = text.split(/(\{[^}]+\})/)
  return parts.map((part, i) => {
    if (part.startsWith('{') && part.endsWith('}')) {
      return (
        <span key={i} className="inline-flex items-center rounded bg-pink-400/35 border border-pink-500/25 px-1.5 py-0.5 text-[11px] font-mono font-semibold leading-none whitespace-nowrap align-middle">
          {part.slice(1, -1)}
        </span>
      )
    }
    return part.split('\n').map((line, j, arr) => (
      <span key={`${i}-${j}`}>
        {line}
        {j < arr.length - 1 && <br />}
      </span>
    ))
  })
}

function Arrow({ side, offset }: { side: 'top'|'bottom'|'left'|'right'; offset: number }) {
  const base = 'absolute w-3 h-3 bg-pink-200 rotate-45'
  const style: React.CSSProperties = {}
  if (side === 'top')    { style.bottom = -6; style.left = offset }
  if (side === 'bottom') { style.top = -6;    style.left = offset }
  if (side === 'left')   { style.right = -6;  style.top = offset }
  if (side === 'right')  { style.left = -6;   style.top = offset }
  return <div className={base} style={style} />
}

function computePlacement(
  target: Rect | null,
  bubble: { w: number; h: number },
  vp: { w: number; h: number },
  preferred: 'top' | 'bottom' | 'left' | 'right' | 'auto'
) {
  if (!target) return { top: 0, left: 0, side: 'bottom' as const, arrowOffset: 16 }

  const space = {
    top:    target.top,
    bottom: vp.h - (target.top + target.height),
    left:   target.left,
    right:  vp.w - (target.left + target.width),
  }

  const side = preferred !== 'auto'
    ? preferred
    : (Object.entries(space).sort((a, b) => b[1] - a[1])[0][0] as keyof typeof space)

  let top = 0, left = 0
  if (side === 'top')    { top = target.top - bubble.h - GAP;       left = target.left + target.width / 2 - bubble.w / 2 }
  if (side === 'bottom') { top = target.top + target.height + GAP;  left = target.left + target.width / 2 - bubble.w / 2 }
  if (side === 'left')   { left = target.left - bubble.w - GAP;     top = target.top + target.height / 2 - bubble.h / 2 }
  if (side === 'right')  { left = target.left + target.width + GAP; top = target.top + target.height / 2 - bubble.h / 2 }

  // Clamp inside viewport
  left = Math.max(VIEWPORT_MARGIN, Math.min(left, vp.w - bubble.w - VIEWPORT_MARGIN))
  top  = Math.max(VIEWPORT_MARGIN, Math.min(top,  vp.h - bubble.h - VIEWPORT_MARGIN))

  // Arrow follows target after clamp
  const targetCenterX = target.left + target.width / 2
  const targetCenterY = target.top + target.height / 2
  const arrowOffset = (side === 'top' || side === 'bottom')
    ? Math.max(12, Math.min(bubble.w - 24, targetCenterX - left - 6))
    : Math.max(12, Math.min(bubble.h - 24, targetCenterY - top - 6))

  return { top, left, side, arrowOffset }
}
