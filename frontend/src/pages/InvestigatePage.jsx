import { METHODS } from "../config/modes.js";
import ControlsPanel from "../components/ControlsPanel.jsx";
import GraphView from "../components/GraphView.jsx";
import EntityDetails from "../components/EntityDetails.jsx";
import ResultTabs from "../components/ResultTabs.jsx";
import PageHeader from "../components/PageHeader.jsx";
import { AddToCaseButton } from "../components/AddToCaseMenu.jsx";
import { WEB3_DESIGN } from "../config/w3Design.js";
import RiskPanel from "../investigate/RiskPanel.jsx";
import W3SearchPanel from "../w3/W3SearchPanel.jsx";

// The original dashboard: controls, graph and details, with the result tabs below.
// Its state lives in App (useInvestigation), so the result survives a visit to another page.
export default function InvestigatePage({ investigation: inv }) {
  const { result } = inv;
  const subject = result ? investigatedItem(result, inv.form) : null;
  // "Full explanation" in the score panel: open the Why Flagged? tab and bring it into view.
  const showWhy = () => {
    inv.setActiveTab("Why Flagged?");
    requestAnimationFrame(() => document.querySelector(".tabs-card")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  return (
    <>
      <PageHeader
        icon="investigate"
        title="Investigate"
        subtitle={WEB3_DESIGN
          ? "Look up an address, transaction or website on Bitcoin, Ethereum, Base or Solana. Click anything in the graph for its actions."
          : "Look up a Bitcoin address or transaction, or open a demo case. Right-click any address or transaction to add it to a case."}
      >
        {subject && <AddToCaseButton item={subject} />}
        {graphLink(result) && (
          <a className="secondary" href={graphLink(result)} title="Open this address in advanced graph mode (thousands of nodes)">
            Advanced graph
          </a>
        )}
        <span className={`data-badge data-${result?.data_mode ?? "none"}`}>
          Data: {result ? result.data_mode.toUpperCase() : "none yet"}
        </span>
      </PageHeader>

      {WEB3_DESIGN ? (
        // Web3 and Soft designs: one smart search bar across the top, then the graph with the risk score
        // and the details beside it. Bitcoin addresses get the live model score automatically.
        <>
          <W3SearchPanel layout="bar" form={inv.form} onFormChange={inv.handleFormChange} onStart={inv.handleStart} isLoading={inv.status === "loading"} />
          <div className="inv-layout">
            <GraphView
              status={inv.status}
              error={inv.error}
              result={result}
              selectedId={inv.selectedId}
              onSelect={inv.setSelectedId}
              onCancel={inv.handleCancel}
              startedAt={inv.startedAt}
              methodLabel={METHODS.find((m) => m.id === inv.form.method)?.label ?? inv.form.method}
              expand={inv}
            />
            <div className="w3-side inv-side">
              {inv.status === "done" && (
                <RiskPanel key={result?.investigation_id} inv={inv} result={result} selectedId={inv.selectedId} onShowWhy={showWhy} />
              )}
              <div key={inv.selectedId || "none"} className="w3-slide-in">
                <EntityDetails result={result} selectedId={inv.selectedId} onSelect={inv.setSelectedId} onEvidenceClick={inv.handleEvidenceClick} expand={inv} />
              </div>
            </div>
          </div>
        </>
      ) : (
        <div className="layout">
          <ControlsPanel
            form={inv.form}
            onFormChange={inv.handleFormChange}
            onCaseChange={inv.handleCaseChange}
            onStart={inv.handleStart}
            isLoading={inv.status === "loading"}
          />
          <GraphView
            status={inv.status}
            error={inv.error}
            result={result}
            selectedId={inv.selectedId}
            onSelect={inv.setSelectedId}
            onCancel={inv.handleCancel}
            startedAt={inv.startedAt}
            methodLabel={METHODS.find((m) => m.id === inv.form.method)?.label ?? inv.form.method}
            expand={inv}
          />
          <EntityDetails
            result={result}
            selectedId={inv.selectedId}
            onSelect={inv.setSelectedId}
            onEvidenceClick={inv.handleEvidenceClick}
            expand={inv}
          />
        </div>
      )}

      <ResultTabs
        result={result}
        active={inv.activeTab}
        onTabChange={inv.setActiveTab}
        highlightedEvidence={inv.highlightedEvidence}
        onEvidenceClick={inv.handleEvidenceClick}
        onSelect={inv.setSelectedId}
      />
    </>
  );
}

// #/graph/<chain>/<address> when the investigation was about an address graph mode supports.
function graphLink(result) {
  const { type, value } = result?.input ?? {};
  const chain = ["bitcoin", "ethereum", "base", "solana"].find((c) => type === `${c}_address`);
  return chain && value ? `#/graph/${chain}/${encodeURIComponent(value)}` : null;
}

// The address or transaction the investigation started from, as a case item.
// Demo results do not carry their input, so fall back to what the form ran.
function investigatedItem(result, form) {
  const { type, value } = result.input ?? { type: form.inputType, value: form.value.trim() };
  if (!value) return null;
  if (type === "bitcoin_address") return { item_type: "address", value, network: "bitcoin", label: null, source: result.investigation_id };
  if (type === "bitcoin_txid") return { item_type: "transaction", value: value.toLowerCase(), network: "bitcoin", label: null, source: result.investigation_id };
  for (const chain of ["ethereum", "base"]) {
    if (type === `${chain}_address`) return { item_type: "address", value: value.toLowerCase(), network: chain, label: null, source: result.investigation_id };
    if (type === `${chain}_txid`) return { item_type: "transaction", value: value.toLowerCase(), network: chain, label: null, source: result.investigation_id };
  }
  if (type === "url_domain") return { item_type: "domain", value, network: "web", label: null, source: result.investigation_id };
  if (type === "x402_payment_id") return { item_type: "payment", value, network: "other", label: null, source: result.investigation_id };
  return null;
}
