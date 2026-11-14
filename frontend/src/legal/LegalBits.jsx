import { useState } from "react";
import { DISCLAIMER } from "./legalText.jsx";
import "./legal.css";

const NOTICE_KEY = "pd-storage-notice-closed";

function readClosed() {
  try { return localStorage.getItem(NOTICE_KEY) === "1"; } catch { return false; }
}

// A one-time note about browser storage. Information only: everything stored is strictly necessary (the sign-in
// token) or remembers a choice the person made, so UK PECR / the EU ePrivacy rules need no consent banner.
export function StorageNotice() {
  const [closed, setClosed] = useState(readClosed);
  if (closed) return null;
  function close() {
    try { localStorage.setItem(NOTICE_KEY, "1"); } catch { /* private window: shown again next time */ }
    setClosed(true);
  }
  return (
    <div className="storage-notice" role="region" aria-label="Browser storage notice">
      <p>
        No cookies or tracking here. Your browser stores only your sign-in token and your menu setting, which the
        dashboard needs to work. Fonts load from Google. <a href="#/privacy" target="_blank" rel="noopener">Privacy policy</a>
      </p>
      <button type="button" className="secondary" onClick={close}>OK</button>
    </div>
  );
}

// The line at the bottom of every dashboard page: the not-advice disclaimer, Etherscan's required attribution,
// and links to the legal pages.
export function LegalFooter() {
  return (
    <footer className="legal-footer">
      <p>{DISCLAIMER}</p>
      <p>
        <a href="#/privacy">Privacy policy</a>
        <span aria-hidden="true"> · </span>
        <a href="#/terms">Terms of use</a>
        <span aria-hidden="true"> · </span>
        <a href="#/sources">Sources and licences</a>
        <span aria-hidden="true"> · </span>
        <a href="https://etherscan.io" target="_blank" rel="noopener noreferrer">Powered by Etherscan.io APIs</a>
      </p>
    </footer>
  );
}

// Links under the sign-in form (both designs). Opens in a new tab so a half-filled form is not lost.
export function LoginLegalLinks() {
  return (
    <p className="small muted center legal-login-links">
      <a href="#/privacy" target="_blank" rel="noopener">Privacy policy</a>
      <span aria-hidden="true"> · </span>
      <a href="#/terms" target="_blank" rel="noopener">Terms of use</a>
    </p>
  );
}
