import { useRef, useState } from 'react'
import { api } from '../api/client'

export default function HelpButton() {
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState('')
  const [status, setStatus] = useState<'idle' | 'sending' | 'done' | 'error'>('idle')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  function handleOpen() {
    setOpen(true)
    setStatus('idle')
    setMessage('')
    setTimeout(() => textareaRef.current?.focus(), 50)
  }

  function handleDismiss() {
    setOpen(false)
    setStatus('idle')
    setMessage('')
  }

  async function handleSend() {
    const trimmed = message.trim()
    if (!trimmed || status === 'sending') return
    setStatus('sending')
    try {
      await api.createReport({ kind: 'support', message: trimmed })
      setStatus('done')
    } catch {
      setStatus('error')
    }
  }

  return (
    <>
      {/* Floating trigger button */}
      <button
        onClick={handleOpen}
        aria-label="Help"
        className="fixed bottom-6 right-6 z-[9998] w-12 h-12 rounded-full bg-indigo-600 hover:bg-indigo-500 shadow-lg shadow-indigo-900/40 flex items-center justify-center transition-all duration-200 hover:scale-105"
      >
        <svg viewBox="0 0 24 24" className="w-5 h-5 text-white fill-none stroke-current" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
          <line x1="12" y1="17" x2="12.01" y2="17" strokeWidth="3" strokeLinecap="round" />
        </svg>
      </button>

      {/* Popover */}
      {open && (
        <>
          {/* Backdrop to close on outside click */}
          <div
            className="fixed inset-0 z-[9998]"
            onClick={handleDismiss}
          />

          <div className="fixed bottom-22 right-6 z-[9999] w-80 rounded-2xl border border-zinc-700/60 bg-zinc-900 shadow-2xl shadow-black/60 p-5 flex flex-col gap-4">
            {status === 'done' ? (
              <div className="flex flex-col items-center gap-3 py-3 text-center">
                <div className="w-11 h-11 rounded-full bg-green-500/15 flex items-center justify-center">
                  <svg viewBox="0 0 24 24" className="w-5 h-5 text-green-400 fill-none stroke-current" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </div>
                <div>
                  <p className="text-white font-semibold text-sm mb-1">Message sent!</p>
                  <p className="text-zinc-400 text-xs leading-relaxed">Thanks for reaching out — we'll get back to you as soon as we can.</p>
                </div>
                <button
                  onClick={handleDismiss}
                  className="mt-1 px-5 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs transition-colors"
                >
                  Close
                </button>
              </div>
            ) : (
              <>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-white font-semibold text-sm leading-snug">Need help?</p>
                    <p className="text-zinc-400 text-xs leading-relaxed mt-1">
                      Got a question or running into something? Send us a message and we'll get back to you.
                    </p>
                  </div>
                  <button
                    onClick={handleDismiss}
                    aria-label="Dismiss"
                    className="shrink-0 text-zinc-600 hover:text-zinc-400 transition-colors mt-0.5"
                  >
                    <svg viewBox="0 0 24 24" className="w-4 h-4 fill-none stroke-current" strokeWidth="2" strokeLinecap="round">
                      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                </div>

                <textarea
                  ref={textareaRef}
                  value={message}
                  onChange={e => setMessage(e.target.value)}
                  rows={4}
                  placeholder="Describe your question or issue…"
                  className="w-full rounded-xl border border-zinc-700 bg-zinc-800 px-3 py-2.5 text-sm text-white placeholder-zinc-600 focus:outline-none focus:border-zinc-500 resize-none transition-colors"
                />

                {status === 'error' && (
                  <p className="text-red-400 text-xs -mt-2">Something went wrong — please try again.</p>
                )}

                <div className="flex gap-2">
                  <button
                    onClick={handleDismiss}
                    className="flex-1 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm transition-colors"
                  >
                    Dismiss
                  </button>
                  <button
                    onClick={handleSend}
                    disabled={!message.trim() || status === 'sending'}
                    className="flex-1 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {status === 'sending' ? 'Sending…' : 'Send'}
                  </button>
                </div>
              </>
            )}
          </div>
        </>
      )}
    </>
  )
}
