import { useState } from 'react'
import { api } from '../api/client'
import type { ReportCreate } from '../api/client'

interface ReportModalProps {
  open: boolean
  onClose: () => void
  payload: Omit<ReportCreate, 'message'>
}

const CATEGORIES = [
  'Wrong translation',
  'Wrong definition',
  'Missing or incomplete data',
  'Audio issue',
  'Inappropriate content',
  'Other',
]

export default function ReportModal({ open, onClose, payload }: ReportModalProps) {
  const [category, setCategory] = useState('')
  const [message, setMessage] = useState('')
  const [status, setStatus] = useState<'idle' | 'sending' | 'done' | 'error'>('idle')

  if (!open) return null

  function handleClose() {
    setCategory('')
    setMessage('')
    setStatus('idle')
    onClose()
  }

  async function handleSubmit() {
    if (!category || status === 'sending') return
    setStatus('sending')
    const fullMessage = message.trim() ? `${category}\n\n${message.trim()}` : category
    try {
      await api.createReport({ ...payload, message: fullMessage })
      setStatus('done')
    } catch {
      setStatus('error')
    }
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={handleClose} />
      <div className="relative bg-zinc-900 border border-zinc-700 rounded-2xl shadow-2xl w-full max-w-sm p-6">
        {status === 'done' ? (
          <div className="flex flex-col items-center gap-4 py-4 text-center">
            <div className="w-12 h-12 rounded-full bg-green-500/15 flex items-center justify-center">
              <svg viewBox="0 0 24 24" className="w-6 h-6 text-green-400 fill-none stroke-current" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
            <div>
              <p className="text-white font-semibold text-base mb-1">Thanks for letting us know!</p>
              <p className="text-zinc-400 text-sm leading-relaxed">We'll look into it and take care of it — your feedback helps make Singoling better for everyone.</p>
            </div>
            <button
              onClick={handleClose}
              className="mt-1 px-6 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm transition-colors"
            >
              Close
            </button>
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-white font-semibold text-base">Report a problem</h2>
              <button
                onClick={handleClose}
                className="text-zinc-500 hover:text-zinc-300 transition-colors"
                aria-label="Close"
              >
                <svg viewBox="0 0 24 24" className="w-5 h-5 fill-none stroke-current" strokeWidth="2" strokeLinecap="round">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-zinc-400 text-xs mb-1.5 font-medium uppercase tracking-wide">Category</label>
                <select
                  value={category}
                  onChange={e => setCategory(e.target.value)}
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-zinc-500 transition-colors"
                >
                  <option value="" disabled>Select a category…</option>
                  {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>

              <div>
                <label className="block text-zinc-400 text-xs mb-1.5 font-medium uppercase tracking-wide">
                  Details <span className="normal-case text-zinc-600 font-normal">(optional)</span>
                </label>
                <textarea
                  value={message}
                  onChange={e => setMessage(e.target.value)}
                  rows={3}
                  placeholder="Describe the issue…"
                  className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder-zinc-600 focus:outline-none focus:border-zinc-500 transition-colors resize-none"
                />
              </div>

              {status === 'error' && (
                <p className="text-red-400 text-xs">Something went wrong. Please try again.</p>
              )}

              <div className="flex gap-3 pt-1">
                <button
                  onClick={handleClose}
                  className="flex-1 py-2.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSubmit}
                  disabled={!category || status === 'sending'}
                  className="flex-1 py-2.5 rounded-lg bg-red-500/90 hover:bg-red-500 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {status === 'sending' ? 'Sending…' : 'Submit'}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
