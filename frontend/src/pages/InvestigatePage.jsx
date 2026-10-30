import { METHODS } from "../config/modes.js";
import ControlsPanel from "../components/ControlsPanel.jsx";
import GraphView from "../components/GraphView.jsx";
import EntityDetails from "../components/EntityDetails.jsx";
import ResultTabs from "../components/ResultTabs.jsx";
import PageHeader from "../components/PageHeader.jsx";
import { AddToCaseButton } from "../components/AddToCaseMenu.jsx";

// The original dashboard: controls, graph and details, with the result tabs below.
// Its state lives in App (useInvestigation), so the result survives a visit to another page.
export default function InvestigatePage({ investigation: inv }) {
  const { result } = inv;
  const subject = result ? investigatedItem(result, inv.form) : null;

  return (
    <>
      <PageHeader
        icon="investigate"
        title="Investigate"
        subtitle="Look up a Bitcoin address or transaction, or open a demo case. Right-click any address or transaction to add it to a case."
      >
        {subject && <AddToCaseButton item={subject} />}
        <span className={`data-badge data-${result?.data_mode ?? "none"}`}>
          Data: {result ? result.data_mode.toUpperCase() : "none yet"}
        </span>
      </PageHeader>

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
        />
        <EntityDetails
          result={result}
          selectedId={inv.selectedId}
          onSelect={inv.setSelectedId}
          onEvidenceClick={inv.handleEvidenceClick}
        />
      </div>

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

// The address or transaction the investigation started from, as a case item.
// Demo results do not carry their input, so fall back to what the form ran.
function investigatedItem(result, form) {
  const { type, value } = result.input ?? { type: form.inputType, value: form.value.trim() };
  if (!value) return null;
  if (type === "bitcoin_address") return { item_type: "address", value, network: "bitcoin", label: null, source: result.investigation_id };
  if (type === "bitcoin_txid") return { item_type: "transaction", value: value.toLowerCase(), network: "bitcoin", label: null, source: result.investigation_id };
  if (type === "url_domain") return { item_type: "domain", value, network: "web", label: null, source: result.investigation_id };
  if (type === "x402_payment_id") return { item_type: "payment", value, network: "other", label: null, source: result.investigation_id };
  return null;
}
