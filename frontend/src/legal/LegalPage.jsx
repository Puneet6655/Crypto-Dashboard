import { useEffect, useState } from "react";
import { apiFetch } from "../api/client.js";
import { navigate } from "../hooks/useHashRoute.js";
import PageHeader from "../components/PageHeader.jsx";
import { PrivacyPolicy, TermsOfUse } from "./legalText.jsx";
import "./legal.css";

// Who runs the dashboard and the retention periods, from the backend (public, no sign-in needed).
export function useLegalInfo() {
  const [info, setInfo] = useState(null);
  useEffect(() => {
    let live = true;
    apiFetch("/api/auth/legal").then((data) => live && setInfo(data)).catch(() => live && setInfo({}));
    return () => { live = false; };
  }, []);
  return info;
}

// #/privacy and #/terms. Works signed in (inside the dashboard) and signed out (`standalone`, from the sign-in page).
// Uses only shared classes, so it looks right in both the classic and the Web3 design.
export default function LegalPage({ page, standalone = false }) {
  const info = useLegalInfo();
  const showing = page === "terms" ? "terms" : "privacy";
  const tabs = (
    <div className="segmented" role="tablist" aria-label="Legal documents">
      {[["privacy", "Privacy policy"], ["terms", "Terms of use"]].map(([id, label]) => (
        <button key={id} type="button" role="tab" aria-selected={showing === id} className={showing === id ? "active" : ""}
          onClick={() => navigate(`/${id}`)}>{label}</button>
      ))}
    </div>
  );

  const body = (
    <>
      {info && info.configured === false && (
        <p className="notice warn small">
          The operator name and contact email are not set yet (LEGAL_OPERATOR_NAME and LEGAL_CONTACT_EMAIL on the
          backend). This text is a template, not legal advice: have it checked before opening sign-up to the public.
        </p>
      )}
      <section className="card legal-card">
        {showing === "terms" ? <TermsOfUse info={info} /> : <PrivacyPolicy info={info} />}
      </section>
    </>
  );

  if (!standalone) {
    return (
      <>
        <PageHeader icon="shield" title="Privacy and terms" subtitle="How your data is used, and the rules for using the dashboard.">
          {tabs}
        </PageHeader>
        {body}
      </>
    );
  }
  return (
    <div className="legal-standalone">
      <div className="legal-standalone-inner">
        <header className="legal-standalone-head">
          <span className="brand-name">Blockchain Intelligence</span>
          {tabs}
          <button type="button" className="secondary" onClick={() => navigate("/")}>Back to sign in</button>
        </header>
        {body}
      </div>
    </div>
  );
}
