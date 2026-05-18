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
          Welcome to SingoLing. These Terms of Service (the "Terms") form a binding agreement between you (the "User,"
          "you," or "your") and the operator of SingoLing (the "Service," "we," "us," or "our"), accessible at
          singoling.com. By creating an account, accessing, or using the Service, you agree to be bound by these Terms.
          If you do not agree, do not use the Service.
        </P>

        <Section n="1" title="Scope of Service">
          <P>
            SingoLing is a web-based language learning application that teaches foreign languages through music. The
            Service currently focuses on Russian-language instruction and provides synchronized lyric display, phonetic
            stress marks, real-time word lookup (morphology, grammar, definitions, and translations), curated playlists,
            and personal progress tracking.
          </P>
          <P>
            Audio playback is provided exclusively through integrations with third-party platforms, currently YouTube
            and Apple Music. SingoLing does not host, store, or distribute audio recordings. Your ability to play any
            given song depends on your access to and subscription status with those third-party platforms.
          </P>
          <P>
            The Service is provided primarily for educational and personal, non-commercial use. The Service is under
            active development. Features described in these Terms or on the website may be added, modified, suspended,
            or removed at any time, with or without notice.
          </P>
        </Section>

        <Section n="2" title="Eligibility and Account Requirements">
          <Sub n="2.1" title="Age Requirements">
            <P>
              You must be at least 13 years old to create an account. If you reside in the European Economic Area, the
              United Kingdom, or any other jurisdiction that sets a higher minimum age for digital consent, you must be
              at least 16 years old, or the minimum age required by your local law, whichever is higher. By creating an
              account, you represent that you meet these requirements.
            </P>
            <P>
              If you are under the age of majority in your jurisdiction, you may only use the Service with the consent
              and supervision of a parent or legal guardian who agrees to be bound by these Terms on your behalf.
            </P>
          </Sub>
          <Sub n="2.2" title="Account Registration">
            <P>
              To access most features of the Service, you must register for an account. You may register directly with
              an email address and password, or through a supported third-party authentication provider (currently
              Google and Apple). When registering, you agree to:
            </P>
            <UL items={[
              'Provide accurate, current, and complete information, including a valid email address and a display name;',
              'Keep your account information up to date;',
              'Maintain the security and confidentiality of your password and authentication credentials;',
              'Be responsible for all activity that occurs under your account, whether or not authorized by you;',
              <>Promptly notify us at <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a> of any unauthorized access to your account or any other suspected breach of security.</>,
            ]} />
            <P>We are not liable for any loss or damage arising from your failure to safeguard your credentials.</P>
          </Sub>
          <Sub n="2.3" title="One Account Per User">
            <P>
              You may not create or maintain more than one personal account, share your account with others, or transfer
              your account to any other person without our prior written consent.
            </P>
          </Sub>
        </Section>

        <Section n="3" title="Third-Party Services">
          <Sub n="3.1" title="Audio Playback Providers">
            <P>
              Music playback on SingoLing is performed by third-party services, including YouTube (via the YouTube
              IFrame Player API) and Apple Music (via MusicKit JS). Your use of these features is subject to:
            </P>
            <UL items={[
              <>YouTube's <a href="https://www.youtube.com/t/terms" className="text-indigo-400 hover:text-indigo-300">Terms of Service</a> and <a href="https://policies.google.com/privacy" className="text-indigo-400 hover:text-indigo-300">Privacy Policy</a>; and</>,
              <>Apple Media Services Terms and Conditions and Apple's Privacy Policy, including any requirement to maintain an active Apple Music subscription.</>,
            ]} />
            <P>
              SingoLing does not control these third-party services and is not responsible for their availability,
              content catalog, pricing, geographic restrictions, advertisements, or any modifications they make to their
              platforms. If a song becomes unavailable on a third-party platform, it may no longer be playable through
              the Service, even though its lyrics and learning content may remain visible.
            </P>
          </Sub>
          <Sub n="3.2" title="Authentication Providers">
            <P>
              If you choose to sign in using Google or Apple, you authorize us to receive and store certain identifiers
              and profile information from those providers as necessary to create and maintain your account. Your use of
              these providers is also subject to their respective terms.
            </P>
          </Sub>
          <Sub n="3.3" title="Other Third-Party Services">
            <P>
              To deliver the Service, we use additional third parties for analytics (Plausible Analytics), transactional
              email (Mailgun), lyrics retrieval (LRCLIB), and machine translation (DeepL). These services receive only
              the data necessary for their function, as described in our Privacy Policy. We are not responsible for the
              practices of any third-party service.
            </P>
          </Sub>
        </Section>

        <Section n="4" title="User Content and Conduct">
          <Sub n="4.1" title="User Content">
            <P>
              "User Content" means any information or material you submit to the Service, including problem reports,
              favorites, listening history, display name, and any feedback or messages you send to us. You retain
              ownership of your User Content. By submitting User Content, you grant SingoLing a worldwide,
              non-exclusive, royalty-free, sublicensable license to host, store, reproduce, modify, and use that content
              for the limited purposes of operating, improving, and securing the Service.
            </P>
            <P>
              You represent and warrant that you have all rights necessary to submit your User Content and that it does
              not violate any law or third-party right.
            </P>
          </Sub>
          <Sub n="4.2" title="Prohibited Conduct">
            <P>You agree not to, and not to attempt to:</P>
            <UL items={[
              'Use the Service for any unlawful purpose or in violation of these Terms or any applicable law;',
              'Use the Service to harass, defame, threaten, or otherwise harm any person;',
              'Upload or submit content that is unlawful, infringing, defamatory, obscene, or otherwise objectionable;',
              'Reverse engineer, decompile, disassemble, or otherwise attempt to derive the source code of the Service, except to the extent expressly permitted by applicable law;',
              'Scrape, crawl, harvest, or otherwise extract data from the Service by automated means without our prior written consent;',
              'Use the Service to train, fine-tune, or evaluate machine learning models without our prior written consent;',
              'Interfere with, disrupt, or place an undue load on the Service or its infrastructure, including by overwhelming any endpoint with requests or attempting to bypass rate limits or other technical safeguards;',
              'Probe, scan, or test the vulnerability of the Service or breach any security or authentication measures;',
              'Impersonate any person or entity, or misrepresent your affiliation with any person or entity;',
              'Resell, sublicense, rent, lease, or otherwise commercially exploit the Service without our prior written consent;',
              'Circumvent any geographic or platform restriction imposed by us or by a third-party audio provider;',
              'Use the Service to infringe the intellectual property rights, privacy rights, or other rights of any third party.',
            ]} />
            <P>
              We may investigate and respond to any suspected violation of this section, including by suspending or
              terminating your account.
            </P>
          </Sub>
          <Sub n="4.3" title="Responsible Reporting">
            <P>
              If you discover a security vulnerability or a serious content problem, please report it to{' '}
              <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a>{' '}
              rather than disclosing it publicly. Do not exploit the issue beyond the minimum necessary to demonstrate it.
            </P>
          </Sub>
        </Section>

        <Section n="5" title="Intellectual Property">
          <Sub n="5.1" title="SingoLing Materials">
            <P>
              Except for User Content and third-party content described below, all materials made available through the
              Service, including the user interface, software, design, graphics, logos, curated playlist selections, and
              original written content, are owned by us or our licensors and are protected by copyright, trademark, and
              other intellectual property laws. We grant you a limited, personal, non-exclusive, non-transferable,
              revocable license to access and use the Service for your personal, non-commercial educational use, subject
              to these Terms.
            </P>
          </Sub>
          <Sub n="5.2" title="Song Lyrics and Translations">
            <P>
              Song lyrics displayed on the Service are sourced from publicly available lyric databases (including
              LRCLIB) and are presented for educational, language-learning purposes. We do not claim ownership of the
              underlying lyrics, which remain the property of their respective rights holders. Translations and phonetic,
              morphological, and grammatical annotations generated by the Service are derived works provided for study
              purposes and may contain errors or imperfections.
            </P>
          </Sub>
          <Sub n="5.3" title="Audio Content">
            <P>
              We do not host or distribute audio recordings. All audio is streamed by third-party services directly to
              your device under your own account and license with those services. We make no representation that any
              particular song will remain available.
            </P>
          </Sub>
          <Sub n="5.4" title="Copyright Complaints">
            <P>
              We respect the intellectual property rights of others. If you believe that content available through the
              Service infringes your copyright, please send a written notice to{' '}
              <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a>{' '}
              that includes:
            </P>
            <UL items={[
              'Your contact information;',
              'A description of the copyrighted work you claim has been infringed;',
              'The specific URL or location of the allegedly infringing material on the Service;',
              'A statement, under penalty of perjury, that you are the rights holder or are authorized to act on the rights holder\'s behalf, and that you have a good-faith belief that the use is not authorized; and',
              'Your physical or electronic signature.',
            ]} />
            <P>
              We will review valid notices and may remove or disable access to the material in question. We may also
              terminate the accounts of users who are determined to be repeat infringers.
            </P>
          </Sub>
        </Section>

        <Section n="6" title="Privacy and Data Protection">
          <P>
            Your privacy is important to us. Our collection, use, storage, and disclosure of personal information is
            governed by our{' '}
            <a href="/privacy" className="text-indigo-400 hover:text-indigo-300" onClick={e => { e.preventDefault(); window.history.pushState(null, '', '/privacy'); window.dispatchEvent(new PopStateEvent('popstate')) }}>Privacy Policy</a>,
            which is incorporated into these Terms by reference. By using the Service, you acknowledge that you have
            read and understood the Privacy Policy.
          </P>
          <P>
            In summary, we collect account information (such as your email address, display name, hashed password, and
            authentication provider identifiers), authentication and session tokens, learning activity (songs listened
            to, words looked up, favorites, and problem reports), and limited security event data (such as rate-limit
            breach notifications). We do not collect payment information, device fingerprints, precise location data,
            or general IP-address access logs. Data is stored on infrastructure located in the European Economic Area.
          </P>
          <P>
            For information about your rights to access, correct, delete, or export your personal data, and about
            retention, please refer to the Privacy Policy.
          </P>
        </Section>

        <Section n="7" title="Educational Disclaimer">
          <P>
            SingoLing is a language-learning tool. Translations, definitions, phonetic stress marks, and grammatical
            analyses are generated by automated systems and curated content sources and may contain inaccuracies,
            omissions, or errors. The Service is not a substitute for a qualified language teacher, professional
            translator, or authoritative dictionary. You should not rely on the Service for legal, medical,
            professional, official, or safety-critical translations.
          </P>
        </Section>

        <Section n="8" title="Service Availability and Changes">
          <P>
            We strive to keep the Service available, but we do not guarantee uninterrupted, error-free, or secure
            access. The Service may be unavailable from time to time for maintenance, upgrades, technical failures, or
            reasons beyond our control, including outages of third-party services on which the Service depends.
          </P>
          <P>
            We may add, modify, suspend, or discontinue any feature of the Service at any time, including songs,
            playlists, supported languages, and third-party integrations. We are not liable for any loss arising from
            such changes.
          </P>
        </Section>

        <Section n="9" title="Suspension and Termination">
          <Sub n="9.1" title="By You">
            <P>
              You may stop using the Service at any time. Once account deletion is available in the Service, you may
              request deletion of your account through the in-product controls. Until then, you may request deletion by
              emailing <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a> from
              the address associated with your account.
            </P>
          </Sub>
          <Sub n="9.2" title="By Us">
            <P>
              We may suspend or terminate your access to the Service, in whole or in part, at any time and without prior
              notice if we reasonably believe that:
            </P>
            <UL items={[
              'You have violated these Terms or any applicable law;',
              'Your conduct poses a risk to other users, to third parties, or to the Service;',
              'We are required to do so by law or by a competent authority; or',
              'Continued provision of the Service to you is no longer commercially or technically feasible.',
            ]} />
            <P>
              We will use reasonable efforts to notify you of any termination, except where doing so would be unlawful
              or would compromise security.
            </P>
          </Sub>
          <Sub n="9.3" title="Effect of Termination">
            <P>
              Upon termination, your right to use the Service ends immediately. Provisions of these Terms that by their
              nature should survive termination, including those relating to intellectual property, disclaimers,
              limitation of liability, indemnification, and dispute resolution, will survive.
            </P>
          </Sub>
        </Section>

        <Section n="10" title="Disclaimers">
          <P>
            TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE,"
            WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
            PARTICULAR PURPOSE, NON-INFRINGEMENT, ACCURACY, OR UNINTERRUPTED OPERATION.
          </P>
          <P>
            Without limiting the foregoing, we do not warrant that translations, definitions, lyrics, phonetic
            annotations, or other educational content provided through the Service are accurate, complete, or up to
            date. We do not warrant that third-party services on which the Service depends will remain available or
            compatible.
          </P>
          <P>
            Some jurisdictions do not allow the exclusion of certain warranties. In those jurisdictions, the above
            exclusions apply to the maximum extent permitted by law, and you may have additional statutory rights that
            these Terms do not modify.
          </P>
        </Section>

        <Section n="11" title="Limitation of Liability">
          <P>
            TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT WILL SINGOLING, ITS OPERATORS, OR ITS
            LICENSORS BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES,
            OR FOR ANY LOSS OF PROFITS, REVENUE, DATA, GOODWILL, OR LEARNING PROGRESS, ARISING OUT OF OR RELATING TO
            THESE TERMS OR THE SERVICE, WHETHER BASED ON CONTRACT, TORT, STATUTE, OR ANY OTHER LEGAL THEORY, AND
            WHETHER OR NOT WE HAVE BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.
          </P>
          <P>
            TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, OUR TOTAL CUMULATIVE LIABILITY ARISING OUT OF OR
            RELATING TO THESE TERMS OR THE SERVICE WILL NOT EXCEED THE GREATER OF (A) THE AMOUNT YOU HAVE PAID TO US
            FOR THE SERVICE IN THE TWELVE (12) MONTHS PRECEDING THE EVENT GIVING RISE TO THE CLAIM AND (B) FIFTY EURO
            (€50).
          </P>
          <P>
            Nothing in these Terms limits or excludes liability that cannot be limited or excluded under applicable law,
            including liability for gross negligence, willful misconduct, fraud, or, where applicable, death or personal
            injury caused by negligence.
          </P>
        </Section>

        <Section n="12" title="Indemnification">
          <P>
            To the extent permitted by applicable law, you agree to indemnify, defend, and hold harmless SingoLing and
            its operators from and against any claims, liabilities, damages, losses, and expenses, including reasonable
            legal fees, arising out of or in any way connected with (a) your use of or access to the Service, (b) your
            violation of these Terms, (c) your User Content, or (d) your violation of any law or third-party right.
          </P>
        </Section>

        <Section n="13" title="Governing Law and Dispute Resolution">
          <P>
            These Terms and any dispute or claim arising out of or in connection with them or their subject matter or
            formation (including non-contractual disputes or claims) are governed by and construed in accordance with
            the laws of the Republic of Türkiye, without regard to its conflict-of-laws principles.
          </P>
          <P>
            If you are a consumer resident in the European Union, you may also have the right to bring proceedings in
            the courts of your country of residence and may have access to the European Commission's online dispute
            resolution platform at{' '}
            <a href="https://ec.europa.eu/consumers/odr" className="text-indigo-400 hover:text-indigo-300" target="_blank" rel="noopener noreferrer">https://ec.europa.eu/consumers/odr</a>.
          </P>
          <P>
            Nothing in this section prevents either party from seeking injunctive or other equitable relief in any
            court of competent jurisdiction to protect its intellectual property or confidential information.
          </P>
        </Section>

        <Section n="14" title="Changes to These Terms">
          <P>
            We may update these Terms from time to time. If we make a material change, we will provide reasonable
            advance notice, for example by email to the address associated with your account or by a prominent notice
            within the Service, at least fourteen (14) days before the change takes effect, unless a shorter period is
            required by law or by urgent security or legal considerations. Non-material changes (such as clarifications
            or formatting changes) take effect when posted.
          </P>
          <P>
            Your continued use of the Service after the effective date of an updated version of the Terms constitutes
            acceptance of the updated Terms. If you do not accept the updated Terms, you must stop using the Service
            and may close your account.
          </P>
        </Section>

        <Section n="15" title="Miscellaneous">
          <Sub n="15.1" title="Entire Agreement">
            <P>
              These Terms, together with the Privacy Policy and any additional terms expressly incorporated by
              reference, constitute the entire agreement between you and us regarding the Service and supersede any
              prior agreements on the same subject matter.
            </P>
          </Sub>
          <Sub n="15.2" title="Severability">
            <P>
              If any provision of these Terms is held to be invalid or unenforceable, that provision will be enforced
              to the maximum extent permissible, and the remaining provisions will remain in full force and effect.
            </P>
          </Sub>
          <Sub n="15.3" title="No Waiver">
            <P>Our failure to enforce any provision of these Terms is not a waiver of our right to do so later.</P>
          </Sub>
          <Sub n="15.4" title="Assignment">
            <P>
              You may not assign or transfer these Terms or any rights or obligations under them without our prior
              written consent. We may assign these Terms in connection with a merger, acquisition, reorganization, or
              sale of assets, or by operation of law, without your consent.
            </P>
          </Sub>
          <Sub n="15.5" title="No Agency">
            <P>
              Nothing in these Terms creates any agency, partnership, joint venture, or employment relationship between
              you and us.
            </P>
          </Sub>
          <Sub n="15.6" title="Force Majeure">
            <P>
              We are not liable for any failure or delay in performance caused by events beyond our reasonable control,
              including acts of God, war, terrorism, civil unrest, labor disputes, governmental action, internet or
              telecommunications failures, or failures of third-party services.
            </P>
          </Sub>
          <Sub n="15.7" title="Language">
            <P>
              These Terms are written in English. If we provide a translation, the English version will control in the
              event of any conflict, except where mandatory local law requires otherwise.
            </P>
          </Sub>
        </Section>

        <Section n="16" title="Contact Us">
          <P>If you have any questions, complaints, or notices regarding these Terms or the Service, please contact us at:</P>
          <UL items={[
            <>Email: <a href="mailto:support@singoling.com" className="text-indigo-400 hover:text-indigo-300">support@singoling.com</a></>,
            <>Website: <a href="https://singoling.com" className="text-indigo-400 hover:text-indigo-300">https://singoling.com</a></>,
          ]} />
          <P>By creating an account or using SingoLing, you acknowledge that you have read, understood, and agree to be bound by these Terms of Service.</P>
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

