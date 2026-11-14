import { Suspense, lazy, useState } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext.jsx";
import { CasesProvider } from "./cases/CasesContext.jsx";
import { ToastProvider } from "./components/Toasts.jsx";
import Sidebar, { PAGES } from "./components/Sidebar.jsx";
import AddToCaseMenu from "./components/AddToCaseMenu.jsx";
import Icon from "./components/Icon.jsx";
import { navigate, useHashRoute } from "./hooks/useHashRoute.js";
import { useInvestigation } from "./hooks/useInvestigation.js";
import { useLiveAlerts } from "./hooks/useLiveAlerts.js";
import AlertsPage from "./pages/AlertsPage.jsx";
import X402Page from "./pages/X402Page.jsx";
import InvestigatePage from "./pages/InvestigatePage.jsx";
import CasesPage from "./pages/CasesPage.jsx";
import StatusPage from "./pages/StatusPage.jsx";
import ModelsPage from "./pages/ModelsPage.jsx";
import SourcesPage from "./pages/SourcesPage.jsx";
import BigQueryPage from "./pages/BigQueryPage.jsx";
import UsersPage from "./pages/UsersPage.jsx";
import AccountPage from "./pages/AccountPage.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import LegalPage from "./legal/LegalPage.jsx";
import CompleteProfile from "./legal/CompleteProfile.jsx";
import { LegalFooter, StorageNotice } from "./legal/LegalBits.jsx";
import { WEB3_DESIGN } from "./config/w3Design.js";
import W3Backdrop from "./w3/W3Backdrop.jsx";
import { useEdgeReveal } from "./w3/useEdgeReveal.js";
// Web3 shell styles. Every rule is scoped to html.w3, which is only set when Web3 mode is on.
import "./w3/w3-shell.css";
import "./w3/w3-pages.css";

if (WEB3_DESIGN) document.documentElement.classList.add("w3");

// Web3-style login page, loaded only when the Web3 design is on (config/w3Design.js), so the original page is
// unchanged without it. On with VITE_LOGIN_DESIGN=web3 (Vercel) or ?web3; ?classic forces the original.
const Web3LoginPage = lazy(() => import("./pages/Web3LoginPage.jsx"));
const WEB3_LOGIN = WEB3_DESIGN;

// Graph explorer pulls in WebGL drawing (Sigma) and a layout worker: load it only when opened.
const GraphExplorerPage = lazy(() => import("./pages/GraphExplorerPage.jsx"));
// Optional animated overview banner, kept in its own files. Only shown at #/overview.
const OverviewHero = lazy(() => import("./overview/OverviewHero.jsx"));

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <CasesProvider>
          <Gate />
          <StorageNotice />
        </CasesProvider>
      </AuthProvider>
    </ToastProvider>
  );
}

// Shows the sign-in page until someone is signed in, then the dashboard.
// Pages anyone can open, signed in or not (linked from the sign-up form).
const PUBLIC_PAGES = ["privacy", "terms"];

function Gate() {
  const { state, user, offlineReason, check } = useAuth();
  const route = useHashRoute();
  if (PUBLIC_PAGES.includes(route.page) && !user && state !== "checking") return <LegalPage page={route.page} standalone />;
  if (state === "checking") {
    return (
      <div className="fullscreen-state">
        <div className="spinner" aria-hidden="true" />
        <p className="muted">Connecting to the API...</p>
      </div>
    );
  }
  if (state === "offline") {
    return (
      <div className="fullscreen-state">
        <Icon name="alert" size={32} />
        <h1>The dashboard cannot reach its API</h1>
        <p className="muted">{offlineReason}</p>
        <button type="button" className="primary" onClick={check}>Try again</button>
      </div>
    );
  }
  if (!user) return WEB3_LOGIN ? <Suspense fallback={null}><Web3LoginPage /></Suspense> : <LoginPage />;
  // Accounts made before the age, user-type and consent questions, or after the terms changed.
  if (user.needs?.length) return <CompleteProfile />;
  return <Shell />;
}

function Shell() {
  const route = useHashRoute();
  const { can } = useAuth();
  const investigation = useInvestigation();
  const [menuOpen, setMenuOpen] = useState(false);
  const live = useLiveAlerts({ onOpen: () => navigate("/alerts") });
  const reveal = useEdgeReveal(WEB3_DESIGN); // Web3 mode: side menu appears when the mouse reaches the left edge

  const known = PAGES.find((p) => p.id === route.page);
  const allowed = route.page === "account" || route.page === "overview" || PUBLIC_PAGES.includes(route.page)
    || (known && (!known.role || can(known.role)));
  const page = allowed ? route.page : "investigate";

  // From a case: look the item up live and switch to the Investigate page.
  function investigate(inputType, value, mode, method) {
    investigation.investigateLive(inputType, value, mode, method);
    navigate("/investigate");
  }

  return (
    <div className={`shell ${WEB3_DESIGN && reveal.pinned ? "w3-pinned" : ""}`}>
      {WEB3_DESIGN && <W3Backdrop mode={page === "overview" ? "full" : "calm"} pulse={live.pulse} />}
      {WEB3_DESIGN && <div className={`w3-edge ${reveal.shown ? "hidden" : ""}`} aria-hidden="true" />}
      <Sidebar page={page} open={menuOpen} onClose={() => setMenuOpen(false)} unseenAlerts={page === "alerts" ? 0 : live.unseen}
        reveal={WEB3_DESIGN ? reveal : null} />
      <div className="main">
        <div className="mobile-bar">
          <button type="button" className="ghost-icon" aria-label="Open menu" onClick={() => setMenuOpen(true)}>
            <Icon name="menu" size={20} />
          </button>
          <span className="brand-name dark">Blockchain Intelligence</span>
        </div>
        <main className="page">
          {page === "overview" && (
            <Suspense fallback={null}>
              <OverviewHero />
            </Suspense>
          )}
          {page === "investigate" && <InvestigatePage investigation={investigation} />}
          {page === "graph" && (
            <Suspense fallback={<div className="card state">Loading the graph explorer...</div>}>
              <GraphExplorerPage route={route} />
            </Suspense>
          )}
          {page === "cases" && <CasesPage caseId={route.params[0]} onInvestigate={investigate} />}
          {page === "alerts" && <AlertsPage live={live} onInvestigate={investigate} />}
          {page === "x402" && <X402Page onInvestigate={investigate} />}
          {page === "models" && <ModelsPage />}
          {page === "status" && <StatusPage />}
          {page === "sources" && <SourcesPage />}
          {page === "bigquery" && <BigQueryPage />}
          {page === "users" && <UsersPage />}
          {page === "account" && <AccountPage />}
          {PUBLIC_PAGES.includes(page) && <LegalPage page={page} />}
          <LegalFooter />
        </main>
      </div>
      <AddToCaseMenu />
    </div>
  );
}
