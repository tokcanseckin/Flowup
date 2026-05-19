import React, { useEffect, useRef } from 'react'
import { SongLine } from '../api/client'

interface LyricsLockScreenProps {
  /** Timed lyrics for blur effect with auto-scroll */
  lyrics: SongLine[]
  /** Current playback time in milliseconds */
  currentTime: number
  /** Title of the upgrade prompt */
  title: string
  /** Message explaining the upgrade */
  message: string
  /** Optional feature highlights */
  features?: string[]
  /** Callback when upgrade button clicked */
  onUpgrade: () => void
  /** Callback when back to trial button clicked */
  onBackToTrial?: () => void
  /** Text for upgrade button */
  upgradeButtonText?: string
  /** Text for back to trial button */
  backButtonText?: string
}

const LyricsLockScreen: React.FC<LyricsLockScreenProps> = ({
  lyrics,
  currentTime,
  title,
  message,
  features,
  onUpgrade,
  onBackToTrial,
  upgradeButtonText = 'See Premium Plans',
  backButtonText = 'Back to Trial Songs',
}) => {
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const activeLineRef = useRef<number>(-1)

  // Auto-scroll to current line
  useEffect(() => {
    if (!scrollContainerRef.current || lyrics.length === 0) return

    // Find the active line based on currentTime
    const currentLineIndex = lyrics.findIndex((line, i) => {
      const nextLine = lyrics[i + 1]
      return (
        currentTime >= line.start_time_ms &&
        (!nextLine || currentTime < nextLine.start_time_ms)
      )
    })

    if (currentLineIndex === activeLineRef.current) return
    activeLineRef.current = currentLineIndex

    // Scroll to the active line
    if (currentLineIndex >= 0) {
      const lineElements = scrollContainerRef.current.querySelectorAll('[data-line-index]')
      const targetElement = lineElements[currentLineIndex] as HTMLElement
      if (targetElement) {
        targetElement.scrollIntoView({
          behavior: 'smooth',
          block: 'center',
        })
      }
    }
  }, [currentTime, lyrics])

  return (
    <div className="absolute inset-0 z-40 overflow-hidden backdrop-blur-sm">
      {/* Blurred lyrics background with auto-scroll */}
      <div
        ref={scrollContainerRef}
        className="absolute inset-0 overflow-y-auto overflow-x-hidden scrollbar-hide"
        style={{
          filter: 'blur(12px)',
          opacity: 0.4,
        }}
      >
        <div className="px-8 py-24 space-y-6">
          {lyrics.map((line, i) => (
            <div
              key={line.id}
              data-line-index={i}
              className="text-2xl text-white font-medium leading-relaxed transition-opacity duration-300"
              style={{
                opacity:
                  currentTime >= line.start_time_ms &&
                  (i === lyrics.length - 1 || currentTime < lyrics[i + 1].start_time_ms)
                    ? 1
                    : 0.5,
              }}
            >
              {line.original_line}
            </div>
          ))}
        </div>
      </div>

      {/* Dark gradient overlay with backdrop blur */}
      <div className="absolute inset-0 bg-gradient-to-b from-black/80 via-black/85 to-black/90 backdrop-blur-md" />

      {/* Lock screen content */}
      <div className="absolute inset-0 flex items-center justify-center p-4">
        <div className="max-w-xl w-full text-center space-y-4">
          {/* Lock icon */}
          <div className="flex justify-center mb-2">
            <div className="w-12 h-12 bg-purple-500/20 rounded-full flex items-center justify-center backdrop-blur-sm">
              <svg
                className="w-6 h-6 text-purple-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                />
              </svg>
            </div>
          </div>

          {/* Title */}
          <h2 className="text-2xl md:text-3xl font-bold text-white leading-tight">
            {title}
          </h2>

          {/* Message */}
          <p className="text-base text-gray-300 leading-relaxed max-w-lg mx-auto">
            {message}
          </p>

          {/* Features list (if provided) */}
          {features && features.length > 0 && (
            <div className="bg-white/10 backdrop-blur-md rounded-xl p-4 max-w-4xl mx-auto">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
                {features.map((feature, i) => (
                  <div key={i} className="flex items-start gap-2 text-left">
                    <svg
                      className="w-4 h-4 text-purple-400 flex-shrink-0 mt-0.5"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                    <span className="text-gray-200 text-sm">{feature}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Action buttons */}
          <div className="flex flex-col sm:flex-row items-center justify-center gap-3 pt-2">
            {/* Primary: Upgrade */}
            <button
              onClick={onUpgrade}
              className="w-full sm:w-auto px-6 py-3 bg-purple-600 hover:bg-purple-700 text-white font-semibold rounded-lg shadow-lg hover:shadow-xl transition-all transform hover:scale-105"
            >
              {upgradeButtonText}
            </button>

            {/* Secondary: Back to trial */}
            {onBackToTrial && (
              <button
                onClick={onBackToTrial}
                className="w-full sm:w-auto px-6 py-3 bg-white/10 hover:bg-white/20 text-white font-medium rounded-lg backdrop-blur-sm transition-all border border-white/20"
              >
                {backButtonText}
              </button>
            )}
          </div>

          {/* Note about music continuing */}
          <p className="text-xs text-gray-400 pt-2">
            Music continues playing • Your progress is saved
          </p>
        </div>
      </div>

      {/* CSS to hide scrollbar */}
      <style>{`
        .scrollbar-hide::-webkit-scrollbar {
          display: none;
        }
        .scrollbar-hide {
          -ms-overflow-style: none;
          scrollbar-width: none;
        }
      `}</style>
    </div>
  )
}

export default LyricsLockScreen
