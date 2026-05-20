import { useRef, useState } from 'react'
import { api } from '../api/client'
import { useT } from '../i18n/LocalizationContext'
import { track } from '../analytics'

export default function HelpButton() {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState('')
  const [status, setStatus] = useState<'idle' | 'sending' | 'done' | 'error'>('idle')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  function handleOpen() {
    setOpen(true)
    setStatus('idle')
    setMessage('')
    track('Help Button Clicked')
    track('Support Form Opened')
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
      track('Support Ticket Submitted', { category: 'help_button' })
    } catch {
      setStatus('error')
    }
  }

  return (
    <>
      {/* Backdrop — lower z so button stays clickable */}
      {open && (
        <div
          className="fixed inset-0 z-[9990]"
          onClick={handleDismiss}
        />
      )}

      {/* Popover — above backdrop; always in DOM so CSS transition works */}
      <div
        className={`fixed right-6 z-[9991] w-80 rounded-2xl border border-zinc-700/60 bg-zinc-900 shadow-2xl shadow-black/60 p-5 flex flex-col gap-4 transition-all duration-200 ease-out origin-bottom-right ${
          open ? 'opacity-100 scale-100 pointer-events-auto' : 'opacity-0 scale-95 pointer-events-none'
        }`}
        style={{ bottom: '7rem' }}
        onClick={e => e.stopPropagation()}
      >
          {status === 'done' ? (
            <div className="flex flex-col items-center gap-3 py-3 text-center">
              <div className="w-11 h-11 rounded-full bg-green-500/15 flex items-center justify-center">
                <svg viewBox="0 0 24 24" className="w-5 h-5 text-green-400 fill-none stroke-current" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </div>
              <div>
                <p className="text-white font-semibold text-sm mb-1">{t('help.messageSent')}</p>
                <p className="text-zinc-400 text-xs leading-relaxed">{t('help.messageSentDesc')}</p>
              </div>
              <button
                onClick={handleDismiss}
                className="mt-1 px-5 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs transition-colors"
              >
                {t('help.close')}
              </button>
            </div>
          ) : (
            <>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-white font-semibold text-sm leading-snug">{t('help.title')}</p>
                  <p className="text-zinc-400 text-xs leading-relaxed mt-1">
                    {t('help.description')}
                  </p>
                </div>
                <button
                  onClick={handleDismiss}
                  aria-label={t('help.dismiss')}
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
                placeholder={t('help.placeholder')}
                className="w-full rounded-xl border border-zinc-700 bg-zinc-800 px-3 py-2.5 text-sm text-white placeholder-zinc-600 focus:outline-none focus:border-zinc-500 resize-none transition-colors"
              />

              {status === 'error' && (
                <p className="text-red-400 text-xs -mt-2">{t('help.error')}</p>
              )}

              <div className="flex gap-2">
                <button
                  onClick={handleDismiss}
                  className="flex-1 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm transition-colors"
                >
                  {t('help.dismiss')}
                </button>
                <button
                  onClick={handleSend}
                  disabled={!message.trim() || status === 'sending'}
                  className="flex-1 py-2 rounded-xl text-white text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  style={{ backgroundColor: 'rgb(0, 109, 54)' }}
                >
                  {status === 'sending' ? t('help.sending') : t('help.send')}
                </button>
              </div>
            </>
          )}
      </div>

      {/* Trigger button — highest z so it's always clickable */}
      <button
        onClick={open ? handleDismiss : handleOpen}
        aria-label="Help"
        className="hidden md:flex fixed bottom-6 right-6 z-[9992] w-14 h-14 rounded-full shadow-lg shadow-black/40 items-center justify-center transition-all duration-200 hover:scale-105"
        style={{ backgroundColor: 'rgb(0, 109, 54)' }}
      >
        {/* Speech bubble icon in white */}
        <svg viewBox="0 0 24 24" className="w-8 h-8" fill="none">
          <path
            d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
            fill="white"
          />
          <circle cx="9" cy="10" r="1.2" fill="rgb(0, 109, 54)" />
          <circle cx="12" cy="10" r="1.2" fill="rgb(0, 109, 54)" />
          <circle cx="15" cy="10" r="1.2" fill="rgb(0, 109, 54)" />
        </svg>
      </button>
    </>
  )
}
