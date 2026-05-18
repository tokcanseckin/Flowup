import React from 'react'

function Section({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-indigo-400 font-semibold text-base">{n}. {title}</h2>
      <div className="space-y-3 text-gray-300 leading-relaxed text-sm">{children}</div>
    </section>
  )
}

function Sub({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2 pl-1">
      <h3 className="text-gray-200 font-medium text-sm">{n} {title}</h3>
      <div className="space-y-2 text-gray-400 text-sm leading-relaxed">{children}</div>
    </div>
  )
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-gray-300 text-sm leading-relaxed">{children}</p>
}

function UL({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="list-disc list-inside space-y-1 text-gray-400 text-sm leading-relaxed pl-2">
      {items.map((item, i) => <li key={i}>{item}</li>)}
    </ul>
  )
}

export default function TermsOfServicePage({ onBack }: { onBack: () => void }) {
  return (
    <div className="min-h-screen" style={{ background: '#0d0d14' }}>
      {/* Sticky header */}
      <div
        className="sticky top-0 z-10 border-b border-gray-800/60 backdrop-blur-sm"
        style={{ background: 'rgba(13,13,20,0.92)' }}
      >
        <div className="max-w-3xl mx-auto px-6 h-14 flex items-center gap-4">
          <button
            type="button"
            onClick={onBack}
            className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-white transition-colors"
          >
            <svg viewBox="0 0 24 24" className="w-4 h-4 fill-current">
              <path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/>
            </svg>
            Back
          </button>
          <span className="text-gray-600">|</span>
          <h1 className="text-white font-semibold text-sm">Terms of Service</h1>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-3xl mx-auto px-6 py-10 space-y-10">
        {/* Header block */}
        <div className="space-y-2">
          <h1 className="text-2xl font-bold text-white">Terms of Service</h1>
          <p className="text-indigo-400 font-medium">SingoLing</p>
          <p className="text-gray-500 text-sm">Effective Date: May 18, 2026 &nbsp;|&nbsp; Last Updated: May 18, 2026</p>
        </div>

        <P>
          Welcome to SingoLing ("SingoLing," "we," "us," or "our"). These Terms of Service (the "Terms") are a legally
          binding agreement between you and SingoLing. They govern your access to and use of the SingoLing website and
          service available at singoling.com (the "Service"). Please read them carefully.
        </P>
        <P>
          By creating an account or by accessing or using the Service, you agree to be bound by these Terms and our
          Privacy Policy. If you do not agree, do not use the Service.
        </P>

        <Section n="1" title="Scope of the Service">
          <P>
            SingoLing is a language-learning platform that helps you learn languages through music. It provides an
            interactive lyrics player with synchronized word-by-word translations, phonetic stress marks, and grammatical
            information. It is designed for personal, non-commercial, educational use only.
          </P>
          <P>
            The Service integrates with third-party audio platforms (currently YouTube and Apple Music) solely to
            enable audio playback in your browser. We do not host, store, or distribute the audio files of any songs.
            Audio playback is governed by the terms of the respective platforms.
          </P>
        </Section>

        <Section n="2" title="Eligibility">
          <Sub n="2.1" title="Age Requirements">
            <P>You must be at least 13 years old to use the Service. If you reside in a country in the European Economic Area, you must be at least 16, or the minimum age required by your country's national law for digital consent, whichever is higher. By creating an account, you represent that you meet this requirement.</P>
            <P>If you are under 18, your parent or legal guardian must review these Terms and consent on your behalf.</P>
          </Sub>
          <Sub n="2.2" title="Registration">
            <P>You must create an account to use most features of the Service. You agree to provide accurate, current, and complete information during registration and to keep your account information up to date. You are responsible for keeping your password confidential and for all activity that occurs under your account.</P>
          </Sub>
          <Sub n="2.3" title="One Account Per Person">
            <P>You may maintain only one account per person. You may not create accounts on behalf of others without authorization, and you may not allow others to use your account.</P>
          </Sub>
        </Section>

        <Section n="3" title="Third-Party Services and Audio Playback">
          <Sub n="3.1" title="YouTube and Apple Music">
            <P>The Service relies on YouTube (via the YouTube IFrame Player API) and Apple Music (via MusicKit JS) for audio playback. By using these features, you agree to the terms and policies of those services:</P>
            <UL items={[
              <>YouTube: <a href="https://www.youtube.com/t/terms" className="text-indigo-400 hover:text-indigo-300">YouTube Terms of Service</a> and <a href="https://policies.google.com/privacy" className="text-indigo-400 hover:text-indigo-300">Google Privacy Policy</a>.</>,
              <>Apple Music: <a href="https://www.apple.com/legal/internet-services/itunes/" className="text-indigo-400 hover:text-indigo-300">Apple Media Services Terms and Conditions</a> and <a href="https://www.apple.com/legal/privacy/" className="text-indigo-400 hover:text-indigo-300">Apple Privacy Policy</a>.</>,
            ]} />
            <P>Apple Music playback requires an active Apple Music subscription, which must be purchased separately from Apple. We do not provide or sell music access, and we have no control over Apple's pricing, availability, or licensing.</P>
            <P>We are not affiliated with, endorsed by, or sponsored by YouTube, Google, or Apple.</P>
          </Sub>
          <Sub n="3.2" title="Authentication Providers">
            <P>If you sign in with Google or Apple, your use of those sign-in methods is also subject to those providers' terms. We receive only the information described in our Privacy Policy.</P>
          </Sub>
          <Sub n="3.3" title="Other Third Parties">
            <P>We source song lyrics from LRCLIB and translations from DeepL. We have no control over the content or availability of those services. The accuracy of lyrics and translations is not guaranteed; see Section 7.</P>
          </Sub>
        </Section>

        <Section n="4" title="User Content and Conduct">
          <Sub n="4.1" title="Content You Submit">
            <P>You may submit content through the Service such as problem reports about lyrics, translations, or annotations. By submitting content, you grant us a worldwide, non-exclusive, royalty-free, perpetual, and irrevocable license to use, reproduce, display, and distribute that content solely to improve the Service. You represent that any content you submit does not violate any law or the rights of any third party.</P>
          </Sub>
          <Sub n="4.2" title="Prohibited Conduct">
            <P>You agree not to:</P>
            <UL items={[
              'Use the Service for any purpose other than personal, non-commercial, educational use.',
              'Reproduce, publicly perform, distribute, or make available any songs, lyrics, or translations obtained through the Service in a manner that infringes copyright.',
              'Attempt to circumvent any technical limitations, access controls, rate limits, or security measures of the Service.',
              'Scrape, crawl, or systematically extract data from the Service through automated means.',
              'Use the Service to transmit spam, malware, phishing content, or other harmful or disruptive material.',
              'Impersonate any person or entity, or falsely represent your affiliation with any person or entity.',
              'Use the Service for any unlawful purpose or in violation of any applicable laws.',
              'Interfere with or disrupt the integrity or performance of the Service, its servers, or its networks.',
            ]} />
          </Sub>
          <Sub n="4.3" title="Responsible Reporting">
            <P>If you become aware of a security vulnerability or other serious issue with the Service, please report it to <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a>. Do not exploit vulnerabilities, attempt unauthorized access, or publicly disclose vulnerabilities before we have had a reasonable opportunity to address them.</P>
          </Sub>
        </Section>

        <Section n="5" title="Intellectual Property">
          <Sub n="5.1" title="SingoLing Materials">
            <P>All software, design, text, graphics, interfaces, trademarks, and other content that we create (the "SingoLing Materials") are owned by or licensed to SingoLing and protected by copyright, trademark, and other intellectual property laws. You may not copy, reproduce, modify, distribute, or create derivative works from the SingoLing Materials except as expressly permitted in these Terms.</P>
          </Sub>
          <Sub n="5.2" title="Lyrics and Translations">
            <P>Song lyrics are owned by their respective rights holders. Translations provided by the Service are for personal educational use only. You may not copy, reproduce, or redistribute lyrics or translations obtained through the Service beyond what is permitted by applicable copyright law, including fair use or quotation rights.</P>
          </Sub>
          <Sub n="5.3" title="Audio Content">
            <P>Audio content streamed through YouTube or Apple Music is owned by its respective rights holders. Your access to audio is governed exclusively by your agreement with YouTube and/or Apple. We claim no ownership of, and grant no rights to, any audio content.</P>
          </Sub>
          <Sub n="5.4" title="Copyright Complaints">
            <P>If you believe that any content available through the Service infringes your copyright, please contact us at <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a> with a description of the work, the URL where the allegedly infringing content appears, and your contact information. We will investigate and take appropriate action.</P>
          </Sub>
        </Section>

        <Section n="6" title="Privacy">
          <P>Your use of the Service is also governed by our <a href="/privacy" className="text-indigo-400 hover:text-indigo-300" onClick={e => { e.preventDefault(); window.history.pushState(null,'','/privacy'); window.dispatchEvent(new PopStateEvent('popstate')) }}>Privacy Policy</a>, which is incorporated into and forms part of these Terms. Please review it carefully to understand how we collect, use, and protect your information.</P>
        </Section>

        <Section n="7" title="Educational Disclaimer">
          <P>
            SingoLing is an independent educational tool. All linguistic content — including translations, definitions,
            phonetic transcriptions, grammatical labels, and stress marks — is generated automatically and has not been
            reviewed by professional linguists or certified translators. This content is provided for personal
            educational purposes only and may contain errors. SingoLing makes no representation that the linguistic
            content is accurate, complete, current, or suitable for any purpose other than casual language learning.
          </P>
          <P>You should not rely on SingoLing for certified translation, professional language instruction, or official examination preparation.</P>
        </Section>

        <Section n="8" title="Service Availability">
          <P>
            We make no guarantee that the Service will be available at any particular time or location, uninterrupted,
            error-free, or free of viruses or other harmful components. We may modify, suspend, or discontinue any part
            of the Service at any time without notice and without liability. We are also dependent on third-party
            services (YouTube, Apple Music, DeepL, LRCLIB) whose availability we do not control.
          </P>
        </Section>

        <Section n="9" title="Suspension and Termination">
          <Sub n="9.1" title="By You">
            <P>You may stop using the Service and request deletion of your account at any time by contacting us at <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a> from the email address associated with your account.</P>
          </Sub>
          <Sub n="9.2" title="By Us">
            <P>We may suspend or terminate your account or your access to the Service at any time and without notice if we reasonably believe you have violated these Terms, if required by law, or if we determine in our sole discretion that such action is necessary to protect the Service or other users. We will attempt to notify you where reasonably practical.</P>
          </Sub>
          <Sub n="9.3" title="Effect of Termination">
            <P>Upon termination, your right to use the Service ceases immediately. Sections 4, 5, 7, 10, 11, 12, and 13 survive termination. If your account is terminated because you violated these Terms, you may not create a new account without our prior written consent.</P>
          </Sub>
        </Section>

        <Section n="10" title="Disclaimers">
          <P>
            THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE," WITHOUT WARRANTY OF ANY KIND. TO THE FULLEST EXTENT
            PERMITTED BY APPLICABLE LAW, SINGOLING DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING BUT NOT
            LIMITED TO WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, NON-INFRINGEMENT,
            AND ACCURACY.
          </P>
          <P>
            WE DO NOT WARRANT THAT THE SERVICE WILL BE UNINTERRUPTED, SECURE, OR FREE OF ERRORS; THAT DEFECTS WILL BE
            CORRECTED; OR THAT THE LINGUISTIC CONTENT PROVIDED BY THE SERVICE IS ACCURATE OR COMPLETE. YOUR USE OF
            THE SERVICE IS AT YOUR OWN RISK.
          </P>
        </Section>

        <Section n="11" title="Limitation of Liability">
          <P>
            TO THE FULLEST EXTENT PERMITTED BY APPLICABLE LAW, SINGOLING AND ITS OPERATORS, EMPLOYEES, AGENTS, AND
            LICENSORS WILL NOT BE LIABLE TO YOU FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR
            PUNITIVE DAMAGES — INCLUDING BUT NOT LIMITED TO LOSS OF DATA, LOSS OF REVENUE, LOSS OF PROFITS, OR LOSS
            OF GOODWILL — ARISING OUT OF OR IN CONNECTION WITH THESE TERMS OR YOUR USE OF OR INABILITY TO USE THE
            SERVICE, EVEN IF WE HAVE BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.
          </P>
          <P>
            TO THE FULLEST EXTENT PERMITTED BY APPLICABLE LAW, OUR TOTAL CUMULATIVE LIABILITY TO YOU FOR ALL CLAIMS
            ARISING OUT OF OR RELATING TO THESE TERMS OR THE SERVICE, WHETHER IN CONTRACT, TORT, STATUTE, OR OTHERWISE,
            WILL NOT EXCEED THE GREATER OF: (A) FIFTY EUROS (€50); OR (B) THE TOTAL AMOUNT YOU HAVE PAID US FOR THE
            SERVICE IN THE TWELVE MONTHS IMMEDIATELY PRECEDING THE EVENT GIVING RISE TO THE CLAIM.
          </P>
          <P>Nothing in these Terms limits liability that cannot be limited under applicable mandatory law, including liability for death or personal injury caused by negligence, fraud, or fraudulent misrepresentation.</P>
        </Section>

        <Section n="12" title="Indemnification">
          <P>
            You agree to indemnify, defend, and hold harmless SingoLing and its operators, employees, agents, licensors,
            and service providers from and against any and all claims, liabilities, damages, losses, costs, and expenses
            (including reasonable legal fees) arising out of or in connection with: (a) your use of the Service in
            violation of these Terms; (b) any content you submit through the Service; (c) your infringement of any
            intellectual property or other rights of any third party; or (d) your violation of any applicable law.
          </P>
        </Section>

        <Section n="13" title="Governing Law and Dispute Resolution">
          <P>
            These Terms are governed by and construed in accordance with the laws of the Republic of Türkiye, without
            regard to its conflict of laws principles, except to the extent that mandatory consumer protection laws of
            your country of residence apply. Any dispute arising out of or in connection with these Terms or the Service
            will be submitted to the exclusive jurisdiction of the courts of Istanbul, Türkiye, except that if you are
            a consumer resident in the European Union, you may also bring proceedings in the courts of your country
            of residence.
          </P>
          <P>
            If you are an EU consumer, you may also use the European Commission's Online Dispute Resolution (ODR)
            platform: <a href="https://ec.europa.eu/consumers/odr" className="text-indigo-400 hover:text-indigo-300" target="_blank" rel="noopener noreferrer">https://ec.europa.eu/consumers/odr</a>. Our contact email for ODR purposes is <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a>.
          </P>
        </Section>

        <Section n="14" title="Changes to These Terms">
          <P>
            We may update these Terms from time to time. If we make material changes, we will give you at least fourteen
            (14) days' advance notice before the updated Terms take effect, either by email or by a prominent notice
            within the Service, unless a shorter period is required by law or urgent legal or security circumstances.
            Immaterial changes (such as grammar corrections or clarifications) take effect when posted.
          </P>
          <P>Your continued use of the Service after updated Terms take effect means you accept them. If you do not accept the updated Terms, please stop using the Service and request account deletion.</P>
        </Section>

        <Section n="15" title="Miscellaneous">
          <Sub n="15.1" title="Entire Agreement">
            <P>These Terms, together with the Privacy Policy and any other policies incorporated by reference, constitute the entire agreement between you and SingoLing with respect to the Service and supersede all prior agreements, representations, and understandings.</P>
          </Sub>
          <Sub n="15.2" title="Severability">
            <P>If any provision of these Terms is found by a court of competent jurisdiction to be invalid, illegal, or unenforceable, that provision will be limited or eliminated to the minimum extent necessary, and the remaining provisions will continue in full force and effect.</P>
          </Sub>
          <Sub n="15.3" title="No Waiver">
            <P>Our failure to enforce any provision of these Terms on one occasion will not be deemed a waiver of our right to enforce it on any future occasion.</P>
          </Sub>
          <Sub n="15.4" title="Assignment">
            <P>You may not assign or transfer these Terms or any rights or obligations under them without our prior written consent. We may assign these Terms in connection with a merger, acquisition, reorganization, or sale of all or substantially all of our assets.</P>
          </Sub>
          <Sub n="15.5" title="Force Majeure">
            <P>We will not be liable for any failure or delay in performance caused by circumstances beyond our reasonable control, including natural disasters, acts of government, internet outages, or failures of third-party services.</P>
          </Sub>
          <Sub n="15.6" title="Contact">
            <P>If you have any questions about these Terms, please contact us at <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a>.</P>
          </Sub>
        </Section>

        {/* Footer */}
        <div className="border-t border-gray-800/60 pt-6 pb-4 text-center text-xs text-gray-600 space-x-4">
          <button type="button" onClick={onBack} className="hover:text-gray-400 transition-colors">Back to SingoLing</button>
          <span>·</span>
          <a href="/privacy" className="hover:text-gray-400 transition-colors" onClick={e => { e.preventDefault(); window.history.pushState(null,'','/privacy'); window.dispatchEvent(new PopStateEvent('popstate')) }}>Privacy Policy</a>
        </div>
      </div>
    </div>
  )
}
