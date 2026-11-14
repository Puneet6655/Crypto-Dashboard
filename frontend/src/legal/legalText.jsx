// The privacy policy and terms of use shown at #/privacy and #/terms.
//
// TEMPLATE, NOT LEGAL ADVICE. Written for a student research dashboard and aimed at the UK GDPR / Data
// Protection Act 2018, the EU GDPR and the Australian Privacy Act 1988. Before opening sign-up to the public,
// set LEGAL_OPERATOR_NAME, LEGAL_CONTACT_EMAIL and LEGAL_JURISDICTION on the backend and have the text checked
// (for example by your university's privacy or legal office). When you change the meaning of either text,
// update TERMS_VERSION in backend/app/legal.py so everyone is asked to accept again.
//
// `info` is the answer from GET /api/auth/legal (operator, contact, retention), or null while loading.

export const DISCLAIMER =
  "Risk scores, labels and screening results are automated estimates from public data and research models. They can be wrong and are not legal, financial, investment or compliance advice.";

function days(n) {
  if (!n) return "until you delete it";
  if (n % 365 === 0) return `${n / 365} year${n === 365 ? "" : "s"}`;
  return `${n} days`;
}

function Operator({ info }) {
  const name = info?.operator_name || "[operator name not set: LEGAL_OPERATOR_NAME]";
  return <strong>{name}</strong>;
}

function Contact({ info }) {
  if (info?.contact_email) return <a href={`mailto:${info.contact_email}`}>{info.contact_email}</a>;
  return <strong>[contact email not set: LEGAL_CONTACT_EMAIL]</strong>;
}

export function PrivacyPolicy({ info }) {
  const r = info?.retention ?? {};
  const sessionDays = r.session_hours ? Math.round(r.session_hours / 24) : 7;
  return (
    <article className="legal-text">
      <h2>Privacy policy</h2>
      <p className="muted small">Version {info?.terms_version ?? "..."}</p>

      <h3>1. Who is responsible for your data</h3>
      <p>
        The Blockchain Intelligence dashboard is a student research project run by <Operator info={info} />, who decides
        how your personal data is used (the "data controller"). For any privacy question, to use your rights, or to
        complain, contact <Contact info={info} />.
      </p>

      <h3>2. What we collect</h3>
      <ul>
        <li><strong>Your account:</strong> name, email address, a one-way scrypt hash of your password (never the password
          itself), what kind of user you are (and your organisation, if you give it), when you accepted these terms, and
          that you confirmed you are at least {info?.min_age ?? 18}. We check your date of birth when you sign up but do
          not store it.</li>
        <li><strong>Sign-in sessions:</strong> a hash of your session token, when it was created and expires, and your
          browser's user-agent string.</li>
        <li><strong>Your work:</strong> cases you create, addresses and transactions you add to cases (with your notes),
          and addresses you add to the live-alert watchlist.</li>
        <li><strong>Audit log:</strong> a record of important actions (sign-up, password change, role change, case and
          source edits) with the email of the person who did them.</li>
        <li><strong>Technical data:</strong> your IP address reaches our hosting providers' request logs, and is held in
          memory for a short time to slow down password guessing.</li>
        <li><strong>Lookups:</strong> addresses and transactions you look up are saved with their results, but are not
          linked to your account.</li>
      </ul>

      <h3>3. Why we use it, and our lawful basis</h3>
      <ul>
        <li>To run your account and the dashboard for you (performance of our agreement with you, the terms of use).</li>
        <li>To keep the service secure and to know who changed what (our legitimate interest in security and
          accountability).</li>
        <li>To research how illicit blockchain activity can be detected (our legitimate interest in research). This uses
          public blockchain data, not your account data.</li>
        <li>Where the law requires us to keep or share information (legal obligation).</li>
      </ul>
      <p>We do not sell your data, show advertising, or use your account data to train models.</p>

      <h3>4. Blockchain data about other people</h3>
      <p>
        Public blockchain addresses and transactions can sometimes be linked to a person, so they may be personal data.
        The dashboard only shows what is already public on the blockchain or in the published lists named on the Sources
        page, for research into financial crime. If you believe an address shown here is linked to you and want to
        object, contact <Contact info={info} />.
      </p>

      <h3>5. Who else handles it</h3>
      <ul>
        <li><strong>Neon</strong> (database), <strong>Railway</strong> (backend hosting) and <strong>Vercel</strong>
          (website hosting) store or process data for us as service providers.</li>
        <li><strong>Google Fonts</strong> provides the typefaces, so your browser contacts Google, which sees your IP
          address.</li>
        <li>When you look something up, our server asks blockchain data providers (mempool.space, Blockstream, Etherscan,
          Blockscout, Alchemy). They see the address or transaction and our server, not who you are.</li>
      </ul>
      <p>
        Some of these providers are in the United States. Where data leaves the UK, EU or Australia, we rely on the
        safeguards the law allows, such as adequacy decisions (including the UK-US data bridge and EU-US Data Privacy
        Framework) or standard contractual clauses in the providers' data processing terms.
      </p>

      <h3>6. How long we keep it</h3>
      <ul>
        <li>Sign-in sessions: {sessionDays} days, or until you sign out.</li>
        <li>Audit log entries: {days(r.audit_days ?? 365)}.</li>
        <li>Your account and work: until you delete your account. Accounts with no sign-in for{" "}
          {days(r.inactive_account_days ?? 730)} are deleted automatically{r.inactive_account_days === 0 ? " (turned off)" : ""}.</li>
        <li>When you delete your account, your data is removed at once. Copies in database backups disappear when
          those backups expire (within the hosting provider's backup window).</li>
      </ul>

      <h3>7. Your rights</h3>
      <p>Depending on where you live (for example under the UK GDPR, EU GDPR or Australian Privacy Act), you can:</p>
      <ul>
        <li><strong>See and copy your data:</strong> Account page, "Download my data" (a JSON file).</li>
        <li><strong>Delete your data:</strong> Account page, "Delete account".</li>
        <li><strong>Correct it, restrict or object to its use:</strong> contact <Contact info={info} />.</li>
        <li><strong>Complain:</strong> to us first if you can, or to your data protection authority: the Information
          Commissioner's Office (ico.org.uk) in the UK, the Office of the Australian Information Commissioner
          (oaic.gov.au) in Australia, or your local authority in the EU.</li>
      </ul>
      <p>We answer requests within one month.</p>

      <h3>8. Cookies and browser storage</h3>
      <p>
        We use no cookies, analytics or tracking. Your browser's local storage keeps your sign-in token (so a reload
        keeps you signed in), whether you pinned the side menu, and that you closed the storage notice. These are
        strictly necessary or remember a choice you made, so they need no consent. Signing out removes the token.
      </p>

      <h3>9. Age</h3>
      <p>The dashboard is only for people aged {info?.min_age ?? 18} or over. If we learn an account belongs to someone
        younger, we delete it.</p>

      <h3>10. Automated scores</h3>
      <p>
        Risk scores are automated estimates about blockchain addresses and transactions, not about you. The dashboard
        never takes a decision with legal or similar effects about a person; any such decision needs human review.
      </p>

      <h3>11. Security</h3>
      <p>Passwords are stored only as salted scrypt hashes, session tokens only as SHA-256 hashes, and all traffic uses
        HTTPS. No system is perfectly secure; if a breach affects you, we will tell you and the regulator as the law
        requires.</p>

      <h3>12. Changes</h3>
      <p>If we change this policy in a way that matters, the version date changes and you are asked to read and accept
        it again when you next sign in.</p>
    </article>
  );
}

export function TermsOfUse({ info }) {
  return (
    <article className="legal-text">
      <h2>Terms of use</h2>
      <p className="muted small">Version {info?.terms_version ?? "..."}</p>

      <h3>1. About the dashboard</h3>
      <p>
        Blockchain Intelligence is a free student research tool run by <Operator info={info} /> for studying payments on
        public blockchains (Bitcoin, Ethereum, Base and Solana). By creating an account you agree to these terms and to
        the privacy policy.
      </p>

      <h3>2. Who can use it</h3>
      <p>You must be at least {info?.min_age ?? 18}, give accurate details, keep your password to yourself, and have only
        one account.</p>

      <h3>3. Acceptable use</h3>
      <ul>
        <li>Use the dashboard only for lawful research, education, compliance or investigation work.</li>
        <li>Do not use it to harass, threaten, expose or try to identify private individuals without a lawful reason, or
          to take action yourself against anyone it flags.</li>
        <li>Do not scrape it, download data in bulk, resell it, or use it in a product or service for others.</li>
        <li>Do not try to get around sign-in, roles or rate limits, or disrupt the service.</li>
        <li>Respect the licences of the data sources listed on the Sources page. Several allow non-commercial or personal
          and research use only (for example Etherscan's free API, Tranco, Elliptic2 and Dune's Spellbook), so the
          dashboard may not be used commercially.</li>
      </ul>

      <h3>4. Not advice</h3>
      <p>
        {DISCLAIMER} A match against a list means the address appeared in that snapshot of the list; no match does not
        mean an address or website is safe. Do not rely on the dashboard alone to accuse anyone, freeze or refuse funds,
        or make a report. Check the original sources and get professional advice where it matters.
      </p>

      <h3>5. Third-party data</h3>
      <p>Data comes from the providers and datasets on the Sources page, each under its own terms. Ethereum data is
        powered by Etherscan.io APIs. We do not control these sources and cannot promise they are complete or correct.</p>

      <h3>6. Your account</h3>
      <p>You can delete your account at any time from the Account page. We may suspend or remove accounts that break
        these terms, and we may change or stop the dashboard, for example when the research project ends.</p>

      <h3>7. No warranty and limits on liability</h3>
      <p>
        The dashboard is provided free, "as is" and "as available", without any warranty. To the extent the law allows,
        we are not liable for any loss arising from using it or relying on its results. Nothing in these terms limits
        rights or liability that cannot be limited by law, including your rights as a consumer.
      </p>

      <h3>8. Changes</h3>
      <p>If we change these terms in a way that matters, the version date changes and you are asked to accept them again
        when you next sign in.</p>

      <h3>9. Law</h3>
      <p>These terms are governed by the law of {info?.jurisdiction || "[jurisdiction not set: LEGAL_JURISDICTION]"}.
        Questions: <Contact info={info} />.</p>
    </article>
  );
}
