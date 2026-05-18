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

export default function PrivacyPolicyPage({ onBack }: { onBack: () => void }) {
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
          <h1 className="text-white font-semibold text-sm">Privacy Policy</h1>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-3xl mx-auto px-6 py-10 space-y-10">
        {/* Header block */}
        <div className="space-y-2">
          <h1 className="text-2xl font-bold text-white">Privacy Policy</h1>
          <p className="text-indigo-400 font-medium">SingoLing</p>
          <p className="text-gray-500 text-sm">Effective Date: May 18, 2026 &nbsp;|&nbsp; Last Updated: May 18, 2026</p>
        </div>

        <P>
          This Privacy Policy explains how SingoLing ("SingoLing," "we," "us," or "our") collects, uses, stores, shares,
          and protects your personal information when you use our language-learning service available at singoling.com
          (the "Service"). It also describes the rights you have over your personal information. This Privacy Policy is
          incorporated into and forms part of our Terms of Service.
        </P>
        <P>
          We have tried to write this Policy in plain language. If anything is unclear or you have questions, please
          contact us at <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a>.
        </P>

        <Section n="1" title="Who We Are">
          <P>
            SingoLing is operated as an independent project. For the purposes of the EU and UK General Data Protection
            Regulation (the "GDPR") and the Turkish Law on the Protection of Personal Data No. 6698 ("KVKK"), we are
            the data controller of your personal information.
          </P>
          <P>Contact for privacy questions: <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a></P>
        </Section>

        <Section n="2" title="Summary at a Glance">
          <UL items={[
            'We collect only what we need to run a language-learning service: account details, learning activity, and a small amount of security data.',
            'We do not sell your personal information. We do not show advertising and we do not profile you for advertising.',
            'We do not collect payment information, device fingerprints, precise location data, or general access logs of your IP address.',
            'Your data is stored on servers located in the European Economic Area (Finland).',
            <>You can update most of your account information yourself, and you can request deletion or export of your data by emailing <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a>.</>,
            'Audio playback happens directly between your browser and YouTube or Apple Music. Those services have their own privacy policies, which apply to your use of them.',
          ]} />
        </Section>

        <Section n="3" title="Information We Collect">
          <P>We collect the following categories of personal information.</P>
          <Sub n="3.1" title="Information You Provide When You Register">
            <UL items={[
              'Email address.',
              'Display name.',
              'Password, which we store only as a salted bcrypt hash. We never store or have access to your password in plaintext.',
              'Preferred user interface language.',
              'If you sign in through a third-party provider: a Google User ID and/or Apple User ID, plus the email address and basic profile information supplied by that provider.',
            ]} />
          </Sub>
          <Sub n="3.2" title="Authentication and Session Information">
            <UL items={[
              'Session tokens used to keep you signed in.',
              'OAuth refresh tokens for Google or Apple, where applicable.',
              'Apple Music user token, encrypted at rest, if you choose to connect an Apple Music subscription.',
              'Password reset tokens, stored as SHA-256 hashes, valid for one hour and usable only once.',
            ]} />
          </Sub>
          <Sub n="3.3" title="Learning Activity">
            <UL items={[
              'Songs you have listened to, with timestamps.',
              'Words you have looked up, including the lemma, displayed form, grammatical information, definition shown, the song the word appeared in, and the timestamp.',
              'Songs you have favorited, with timestamps.',
              'Problem reports you submit about words, lines, or songs, including any optional message and the timestamp.',
            ]} />
          </Sub>
          <Sub n="3.4" title="Administrative Data">
            <UL items={[
              'The date and time your account was created.',
              'An internal administrator flag, used only for staff accounts.',
              'Feature-enablement flags for optional integrations (for example, whether you have connected Apple Music).',
            ]} />
          </Sub>
          <Sub n="3.5" title="Security-Event Data">
            <P>When a request to a sensitive endpoint exceeds our rate limits (for example, repeated failed login attempts), we record:</P>
            <UL items={[
              'The IP address making the request.',
              'The endpoint involved.',
              'A timestamp and the count of attempts.',
              'The browser user-agent string reported by the client.',
            ]} />
            <P>We use this information solely to detect and respond to abuse and unauthorized access. We do not record IP addresses for normal browsing or learning activity.</P>
          </Sub>
          <Sub n="3.6" title="Analytics Events">
            <P>
              We use Plausible Analytics, a privacy-respecting analytics service, to understand aggregate usage patterns
              (such as how many people view a page or start a song). Plausible does not use cookies, does not track you
              across sites, and does not collect personal information. The data it records is aggregate and anonymous.
            </P>
          </Sub>
          <Sub n="3.7" title="What We Do Not Collect">
            <UL items={[
              'Payment information. The Service has no paid plans at this time.',
              'Listening behavior from inside third-party platforms (we do not receive playback data back from YouTube or Apple Music).',
              'Device identifiers, browser fingerprints, or advertising IDs.',
              'Precise location data such as GPS coordinates.',
              'IP addresses for ordinary requests outside of the security events described above.',
              'Sensitive special categories of personal data such as health, religion, or political opinions.',
            ]} />
          </Sub>
        </Section>

        <Section n="4" title="How We Use Your Information">
          <P>We use your personal information only for the purposes described below.</P>
          <Sub n="4.1" title="To Provide the Service">
            <UL items={[
              'Create and maintain your account and keep you signed in.',
              'Display your learning activity, favorites, and progress.',
              'Generate the lyrics, translations, phonetic stress marks, and grammatical information you see in the player.',
              'Issue short-lived developer tokens that allow your browser to communicate with Apple Music on your behalf, if you have connected Apple Music.',
            ]} />
          </Sub>
          <Sub n="4.2" title="To Communicate With You">
            <UL items={[
              'Send transactional emails such as account confirmation, password reset, and responses to support requests.',
              'Notify you of material changes to these terms or the Service.',
            ]} />
            <P>We do not send marketing emails. You cannot opt out of security-critical messages such as password reset, because they are essential to operating your account.</P>
          </Sub>
          <Sub n="4.3" title="To Improve the Service">
            <UL items={[
              'Use aggregate, non-personal analytics from Plausible to understand which features are used and how to improve them.',
              'Review problem reports to fix errors in lyrics, translations, or annotations.',
            ]} />
          </Sub>
          <Sub n="4.4" title="To Protect the Service and Its Users">
            <UL items={[
              'Detect and prevent abuse, unauthorized access, fraud, and other security incidents.',
              'Enforce our Terms of Service.',
              'Comply with legal obligations.',
            ]} />
          </Sub>
        </Section>

        <Section n="5" title="Legal Bases (GDPR / KVKK)">
          <P>If you are in the European Economic Area, the United Kingdom, or Türkiye, we rely on the following legal bases to process your personal information:</P>
          <UL items={[
            <>Performance of a contract — to provide the Service you have requested by creating an account.</>,
            <>Legitimate interests — to keep the Service secure, prevent abuse, improve quality through aggregate analytics, and respond to support requests. Where we rely on legitimate interests, we have considered whether they are overridden by your rights and interests.</>,
            <>Consent — where you choose optional integrations, such as connecting your Apple Music subscription. You may withdraw consent at any time by disconnecting the integration.</>,
            <>Legal obligation — where we are required by law to retain or disclose certain information.</>,
          ]} />
        </Section>

        <Section n="6" title="Service Providers and Third Parties">
          <P>We share the minimum amount of personal information necessary with the following categories of service providers, who act either as our processors or as independent controllers under their own terms.</P>
          <Sub n="6.1" title="Infrastructure">
            <P>UPCloud (Finland) hosts our application server and PostgreSQL database. All your personal information stored on our servers resides on UPCloud infrastructure within the European Economic Area.</P>
          </Sub>
          <Sub n="6.2" title="Email Delivery">
            <P>Mailgun processes outbound transactional email. We share your email address and display name with Mailgun solely to deliver messages you have explicitly triggered, such as registration confirmation or password reset.</P>
          </Sub>
          <Sub n="6.3" title="Analytics">
            <P>Plausible Analytics (EU-based) processes aggregate, cookieless usage statistics. Plausible does not receive your account information.</P>
          </Sub>
          <Sub n="6.4" title="Content Sources">
            <P>LRCLIB is used to retrieve song lyrics. We send only song metadata such as artist and title, never your personal information. DeepL is used to generate translations. We send only the source text to be translated and an authenticated API key, never your personal information.</P>
          </Sub>
          <Sub n="6.5" title="Audio Playback">
            <P>YouTube (via the YouTube IFrame Player API): your browser connects directly to YouTube to stream audio. YouTube acts as an independent controller and applies its own privacy policy. We do not receive playback data from YouTube.</P>
            <P>Apple Music (via MusicKit JS): if you choose to connect an active Apple Music subscription, your browser communicates directly with Apple's servers. We issue a short-lived developer token to authorize the connection and store your Apple Music user token in encrypted form. Apple acts as an independent controller and applies its own privacy policy.</P>
          </Sub>
          <Sub n="6.6" title="Authentication">
            <P>Google and Apple authentication providers, if you choose to sign in through them, receive a sign-in request and return basic profile information to us as described in Section 3.1.</P>
          </Sub>
          <Sub n="6.7" title="Legal and Safety">
            <P>We may disclose personal information to law enforcement, regulators, courts, or other authorities if we are required by law to do so, or if we believe in good faith that disclosure is necessary to investigate, prevent, or respond to suspected fraud, abuse, or threats to the safety of users or the public, or to enforce our Terms of Service.</P>
          </Sub>
          <Sub n="6.8" title="We Do Not Sell Your Information">
            <P>We do not and will not sell your personal information. We do not share it with advertising networks or data brokers, and we do not profile you for advertising.</P>
          </Sub>
        </Section>

        <Section n="7" title="International Data Transfers">
          <P>
            Your personal information is stored on servers located in Finland, within the European Economic Area. Some
            of our processors and the third-party services described above (in particular YouTube, Apple, Google, and
            Mailgun) may transfer or process data in countries outside the EEA, the UK, or Türkiye. Where such transfers
            occur, they are protected by appropriate safeguards under applicable law, such as the European Commission's
            Standard Contractual Clauses, adequacy decisions, or equivalent mechanisms. You may request more information
            about these safeguards by contacting us.
          </P>
        </Section>

        <Section n="8" title="How Long We Keep Your Information">
          <P>We keep your personal information for as long as your account is active, and for as long as needed to provide the Service and comply with our legal obligations. Specifically:</P>
          <UL items={[
            'Account information and learning activity: retained for the life of your account. If you delete your account, this information is permanently deleted (see Section 9.3).',
            'Password reset tokens: deleted after use or after one hour, whichever comes first.',
            'Session and OAuth tokens: kept while a session is active and invalidated on logout or account deletion.',
            'Security-event records: retained for up to 12 months for abuse-detection and security purposes, then deleted or anonymized.',
            "Email delivery logs (held by Mailgun): retained according to Mailgun's standard retention policies.",
            'Aggregate analytics (Plausible): retained in non-personal, aggregated form.',
          ]} />
          <P>We may keep limited information for longer where required by law or to defend against legal claims.</P>
        </Section>

        <Section n="9" title="Your Rights">
          <P>Subject to applicable law, you have the following rights regarding your personal information.</P>
          <Sub n="9.1" title="Self-Service">
            <P>You can update your email address, display name, password, and preferred interface language at any time from your account settings.</P>
          </Sub>
          <Sub n="9.2" title="Access, Correction, Portability">
            <P>You may request a copy of the personal information we hold about you in a structured, commonly used, machine-readable format, and you may ask us to correct any information that is inaccurate or incomplete. We are building a self-service export feature; in the meantime, please email <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a> from the address associated with your account.</P>
          </Sub>
          <Sub n="9.3" title="Deletion">
            <P>You may ask us to delete your account and all associated personal information. We are building an in-product deletion feature. In the meantime, please email <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a> from the address associated with your account. Once deletion is processed:</P>
            <UL items={[
              'Your account record and all associated learning activity, favorites, problem reports, and tokens are permanently deleted from our database.',
              'Authentication tokens are invalidated immediately.',
              'Your email address is removed from our outbound mailing systems.',
              'Some information may persist for a short time in routine backups and in security-event logs (as described in Section 8) before being overwritten or expired in the ordinary course.',
            ]} />
          </Sub>
          <Sub n="9.4" title="Objection and Restriction">
            <P>Where we process personal information based on our legitimate interests, you may object to that processing on grounds relating to your particular situation. You may also request that we restrict processing in certain circumstances.</P>
          </Sub>
          <Sub n="9.5" title="Withdrawal of Consent">
            <P>Where we rely on your consent (for example, to connect Apple Music), you may withdraw that consent at any time. Withdrawal does not affect the lawfulness of processing carried out before withdrawal.</P>
          </Sub>
          <Sub n="9.6" title="How to Exercise Your Rights">
            <P>To exercise any of these rights, email <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a> from the address associated with your account. We may need to verify your identity before acting on a request. We will respond within the timeframes required by applicable law, normally within 30 days.</P>
          </Sub>
          <Sub n="9.7" title="Right to Complain">
            <P>If you believe we have not handled your personal information in accordance with the law, you have the right to lodge a complaint with a supervisory authority. In Türkiye, this is the Personal Data Protection Authority (Kişisel Verileri Koruma Kurumu, KVKK). In the European Economic Area, this is the supervisory authority of your country of residence. In the United Kingdom, this is the Information Commissioner's Office (ICO). We would, of course, appreciate the chance to address your concerns directly first.</P>
          </Sub>
        </Section>

        <Section n="10" title="How We Protect Your Information">
          <P>We take reasonable technical and organizational measures to protect your personal information from unauthorized access, alteration, disclosure, and destruction. These include:</P>
          <UL items={[
            'Storing passwords only as salted bcrypt hashes with a cost factor of 12.',
            'Enforcing HTTPS for all traffic between your browser and our servers.',
            'Encrypting sensitive third-party tokens (such as Apple Music user tokens) at rest.',
            'Rate-limiting sensitive endpoints to make brute-force attacks impractical.',
            'Issuing password-reset tokens that expire after one hour and can only be used once.',
            'Generating email alerts when rate-limit thresholds are exceeded so that we can investigate.',
            'Limiting access to production systems and personal data to authorized administrators.',
          ]} />
          <P>No security measure is perfect. If we become aware of a personal data breach that is likely to result in a risk to your rights and freedoms, we will notify you and the relevant supervisory authority in accordance with applicable law.</P>
        </Section>

        <Section n="11" title="Children's Privacy">
          <P>
            The Service is not directed to children under 13. If you reside in the European Economic Area, the United
            Kingdom, or another jurisdiction that sets a higher age of digital consent, you must be at least 16, or the
            minimum age set by your local law, whichever is higher. We do not knowingly collect personal information
            from anyone below these ages. If you believe we have collected information from a child without appropriate
            consent, please contact us at <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a> and we will delete it promptly.
          </P>
        </Section>

        <Section n="12" title="Cookies and Similar Technologies">
          <P>We use only a small number of strictly necessary cookies and similar technologies to operate the Service. These include:</P>
          <UL items={[
            'A session cookie or local storage entry to keep you signed in.',
            'A preference cookie or local storage entry for your interface language.',
          ]} />
          <P>We do not use advertising cookies, social-network tracking pixels, or cross-site analytics cookies. Our analytics provider (Plausible) is cookieless. Third-party services embedded in the Service, such as the YouTube and Apple Music players, may set their own cookies under their own privacy policies when you interact with them.</P>
        </Section>

        <Section n="13" title="Third-Party Links and Content">
          <P>The Service may contain links to, or embed content from, third-party services such as YouTube and Apple Music. Once you click a link or interact with embedded content, the third party may collect information about you under its own privacy policy. We are not responsible for the privacy practices of these third parties and we encourage you to review their policies.</P>
        </Section>

        <Section n="14" title="Automated Decision-Making">
          <P>We do not use your personal information to make decisions about you that have legal or similarly significant effects through purely automated means. Translations, definitions, and grammatical annotations shown to you are produced by automated systems, but those are content features of the Service, not decisions about you.</P>
        </Section>

        <Section n="15" title="Changes to This Privacy Policy">
          <P>We may update this Privacy Policy from time to time. If we make material changes, we will give you reasonable advance notice, for example by email to the address associated with your account or by a prominent notice within the Service, at least fourteen (14) days before the change takes effect, unless a shorter period is required by law or by urgent security or legal considerations. Non-material changes (such as clarifications or formatting changes) take effect when posted. The "Last Updated" date at the top of this Policy will always reflect the current version.</P>
          <P>Your continued use of the Service after a change takes effect means you accept the updated Privacy Policy. If you do not accept the changes, please stop using the Service and, if you wish, request deletion of your account.</P>
        </Section>

        <Section n="16" title="Contact Us">
          <P>If you have any questions about this Privacy Policy or about how we handle your personal information, or if you wish to exercise any of your rights, please contact us:</P>
          <UL items={[
            <>Email: <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a></>,
            <>Website: <a href="https://singoling.com" className="text-indigo-400 hover:text-indigo-300">https://singoling.com</a></>,
          ]} />
          <P>Thank you for trusting SingoLing with your information. We take that trust seriously.</P>
        </Section>

        {/* Footer */}
        <div className="border-t border-gray-800/60 pt-6 pb-4 text-center text-xs text-gray-600 space-x-4">
          <button type="button" onClick={onBack} className="hover:text-gray-400 transition-colors">Back to SingoLing</button>
          <span>·</span>
          <a href="/terms" className="hover:text-gray-400 transition-colors" onClick={e => { e.preventDefault(); window.history.pushState(null,'','/terms'); window.dispatchEvent(new PopStateEvent('popstate')) }}>Terms of Service</a>
        </div>
      </div>
    </div>
  )
}
