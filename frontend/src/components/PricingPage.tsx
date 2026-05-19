import React, { useState, useEffect } from 'react'
import { track } from '../analytics'
import { BackendUser, PricingData, api } from '../api/client'

interface PricingPageProps {
  user: BackendUser | null
  onClose: () => void
}

// Paddle.js types (Paddle Billing v2)
declare global {
  interface Window {
    Paddle?: {
      Environment: {
        set: (env: 'sandbox' | 'production') => void
      }
      Initialize: (options: { 
        token: string
        eventCallback?: (data: any) => void 
      }) => void
      Checkout: {
        open: (options: {
          items?: Array<{ priceId: string; quantity: number }>
          customData?: Record<string, any>
          customer?: { email?: string }
          settings?: {
            successUrl?: string
          }
        }) => void
      }
    }
  }
}

const PricingPage: React.FC<PricingPageProps> = ({ user, onClose }) => {
  const [isAnnual, setIsAnnual] = useState(false)
  const [paddleLoaded, setPaddleLoaded] = useState(false)
  const [pricing, setPricing] = useState<PricingData | null>(null)
  const [loading, setLoading] = useState(true)

  // Fetch pricing from backend
  useEffect(() => {
    api.getPricing()
      .then(data => {
        setPricing(data)
        setLoading(false)
        // Load Paddle.js once we have the client token
        if (data.client_token && !paddleLoaded) {
          initializePaddle(data.client_token)
        }
      })
      .catch(err => {
        console.error('Failed to fetch pricing:', err)
        setLoading(false)
      })
  }, [])

  const initializePaddle = (clientToken: string) => {
    // Load Paddle.js v2 for Paddle Billing
    const script = document.createElement('script')
    script.src = 'https://cdn.paddle.com/paddle/v2/paddle.js'
    script.async = true
    script.onload = () => {
      if (window.Paddle) {
        // Set environment BEFORE Initialize (required for sandbox)
        if (clientToken.startsWith('test_')) {
          window.Paddle.Environment.set('sandbox')
        }
        window.Paddle.Initialize({ 
          token: clientToken,
          eventCallback: (data) => {
            console.log('[Paddle Event]', data)
            if (data.name === 'checkout.completed') {
              track('Checkout Completed', { 
                transaction_id: data.data?.transaction_id 
              })
            }
          }
        })
        setPaddleLoaded(true)
      }
    }
    document.body.appendChild(script)
  }

  const handleUpgrade = (tier: 'monthly' | 'annual') => {
    if (!paddleLoaded || !window.Paddle) {
      alert('Payment system is loading. Please try again in a moment.')
      return
    }

    if (!pricing || !pricing.monthly || !pricing.annual) {
      alert('Pricing information unavailable. Please try again later.')
      return
    }

    track('Checkout Initiated', {
      tier,
      source: 'pricing_page',
      user_id: user?.id ?? 0,
    })

    const priceId = tier === 'monthly' ? pricing.monthly.id : pricing.annual.id

    window.Paddle.Checkout.open({
      items: [{ priceId, quantity: 1 }],
      customData: { user_id: user?.id ?? 0 },
      customer: { email: user?.email || undefined },
      settings: {
        successUrl: window.location.origin + '/browse?subscribed=true',
      },
    })
  }

  const features = [
    'Interactive word-by-word translations',
    'Instant definitions with keyboard shortcuts (D key)',
    'Full-line translations in your language',
    'Unlimited songs in all languages',
    'Ad-free experience',
    'Priority support',
  ]

  // Format price from cents to display format
  const formatPrice = (amount: number, currency: string) => {
    const value = (amount / 100).toFixed(2)
    const symbol = currency === 'EUR' ? '€' : currency === 'USD' ? '$' : currency
    return `${symbol}${value}`
  }

  const monthlyPrice = pricing?.monthly ? formatPrice(pricing.monthly.amount, pricing.monthly.currency) : '€8.00'
  const annualPrice = pricing?.annual ? formatPrice(pricing.annual.amount, pricing.annual.currency) : '€80.00'
  
  // Calculate savings
  const monthlySavings = pricing?.monthly && pricing?.annual 
    ? formatPrice((pricing.monthly.amount * 12) - pricing.annual.amount, pricing.annual.currency)
    : '€16'
  const monthlyEquivalent = pricing?.annual
    ? formatPrice(Math.floor(pricing.annual.amount / 12), pricing.annual.currency)
    : '€6.67'

  if (loading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
        <div className="bg-white rounded-2xl p-8">
          <div className="text-gray-900 text-lg">Loading pricing...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="relative w-full max-w-5xl mx-4 bg-white rounded-2xl shadow-2xl overflow-hidden">
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 z-10 text-gray-400 hover:text-gray-600 transition-colors"
          aria-label="Close"
        >
          <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <div className="p-8 md:p-12">
          {/* Header */}
          <div className="text-center mb-8">
            <h1 className="text-4xl md:text-5xl font-bold text-gray-900 mb-4">
              Upgrade to Premium
            </h1>
            <p className="text-lg text-gray-600 max-w-2xl mx-auto">
              Unlock unlimited interactive lyrics, translations, and word definitions across all songs
            </p>
          </div>

          {/* Annual/Monthly toggle */}
          <div className="flex items-center justify-center gap-4 mb-8">
            <button
              onClick={() => setIsAnnual(false)}
              className={`px-6 py-2 rounded-full font-medium transition-all ${
                !isAnnual
                  ? 'bg-purple-600 text-white shadow-lg'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              Monthly
            </button>
            <button
              onClick={() => setIsAnnual(true)}
              className={`px-6 py-2 rounded-full font-medium transition-all ${
                isAnnual
                  ? 'bg-purple-600 text-white shadow-lg'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              Annual <span className="ml-2 text-sm">(Save 17%)</span>
            </button>
          </div>

          {/* Pricing card */}
          <div className="max-w-md mx-auto mb-8">
            <div className="bg-gradient-to-br from-purple-50 to-blue-50 rounded-xl p-8 border-2 border-purple-200">
              <div className="text-center mb-6">
                <div className="text-5xl font-bold text-gray-900 mb-2">
                  {isAnnual ? annualPrice : monthlyPrice}
                  <span className="text-2xl text-gray-600 font-normal">
                    /{isAnnual ? 'year' : 'month'}
                  </span>
                </div>
                {isAnnual && (
                  <p className="text-sm text-purple-600 font-medium">
                    Just {monthlyEquivalent}/month — Save {monthlySavings}/year
                  </p>
                )}
              </div>

              <button
                onClick={() => handleUpgrade(isAnnual ? 'annual' : 'monthly')}
                disabled={!paddleLoaded || !pricing}
                className="w-full bg-purple-600 hover:bg-purple-700 text-white font-semibold py-4 px-8 rounded-lg shadow-lg hover:shadow-xl transition-all disabled:opacity-50 disabled:cursor-not-allowed mb-4"
              >
                {!paddleLoaded ? 'Loading...' : !pricing ? 'Pricing unavailable' : 'Start Learning Now'}
              </button>

              <p className="text-center text-sm text-gray-500">
                Cancel anytime • No questions asked
              </p>
            </div>
          </div>

          {/* Features list */}
          <div className="max-w-2xl mx-auto">
            <h3 className="text-xl font-semibold text-gray-900 mb-4 text-center">
              Everything you need to master a new language
            </h3>
            <div className="grid md:grid-cols-2 gap-4">
              {features.map((feature, i) => (
                <div key={i} className="flex items-start gap-3">
                  <svg
                    className="w-6 h-6 text-purple-600 flex-shrink-0 mt-0.5"
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
                  <span className="text-gray-700">{feature}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Free trial info */}
          <div className="mt-8 text-center text-sm text-gray-500">
            <p>
              Already subscribed?{' '}
              <button
                onClick={onClose}
                className="text-purple-600 hover:text-purple-700 font-medium underline"
              >
                Continue learning
              </button>
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

export default PricingPage
