// SYNTHETIC TEACHING FIXTURES
// Every value below is invented to show the result shape and test the screen.
// None of it is real research data, a real finding, or a real person's wallet.
// The ".test" domains are reserved names that can never be real websites.

const RETRIEVED = "2026-10-25T00:00:00Z";

export const FIXTURE_CASES = [
  {
    caseId: "demo-scam-giveaway",
    label: "Demo: crypto 'giveaway' website",
    mode: "scam_early_warning",
    input: { type: "url_domain", value: "example-giveaway.test" },
    result: {
      schema_version: "1.0",
      investigation_id: "demo-inv-0001",
      mode: "scam_early_warning",
      method: "fixture_review",
      data_mode: "synthetic",
      findings: [
        {
          id: "f1",
          title: "Page text promises to double any Bitcoin sent",
          kind: "synthetic_example",
          entity_ids: ["web:domain:example-giveaway.test"],
          evidence_ids: ["e1"],
        },
        {
          id: "f2",
          title: "Page shows a Bitcoin address for 'deposits'",
          kind: "synthetic_example",
          entity_ids: ["bitcoin:mainnet:address:bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"],
          evidence_ids: ["e1"],
        },
      ],
      indicators: [
        { id: "i1", rule: "doubling_promise_phrase", matched: true, reason: "Text contains 'send 0.1 BTC, get 0.2 BTC back' (synthetic text)" },
        { id: "i2", rule: "scam_domain_feed", matched: null, reason: "Not run: domain feed is added in Milestone 7" },
      ],
      graph: {
        nodes: [
          { id: "web:domain:example-giveaway.test", type: "domain", label: "example-giveaway.test" },
          { id: "bitcoin:mainnet:address:bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", type: "address", label: "bc1qw508...v8f3t4" },
        ],
        edges: [
          {
            id: "g1",
            source: "web:domain:example-giveaway.test",
            target: "bitcoin:mainnet:address:bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
            relation: "observed",
            label: "address shown on page",
            evidence_ids: ["e1"],
          },
        ],
        truncated: false,
        limits: { max_nodes: 50 },
      },
      evidence: [
        { id: "e1", source: "Synthetic page capture", source_url: "https://example-giveaway.test/", retrieved_at: RETRIEVED, method_version: "fixture_v1", excerpt: "Send 0.1 BTC to bc1qw508... and get 0.2 BTC back within 1 hour!" },
      ],
      source_timestamps: { "synthetic fixture": RETRIEVED },
      limitations: ["Synthetic teaching example, not a real website", "A persuasive phrase alone does not prove a scam"],
      uncertainty: { indicators_not_run: 1 },
      scorecard: { configured: false },
    },
  },
  {
    caseId: "demo-btc-address",
    label: "Demo: Bitcoin address with one payment",
    mode: "crypto_investigation",
    input: { type: "bitcoin_address", value: "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh" },
    result: {
      schema_version: "1.0",
      investigation_id: "demo-inv-0002",
      mode: "crypto_investigation",
      method: "fixture_review",
      data_mode: "synthetic",
      findings: [
        {
          id: "f1",
          title: "Address paid two outputs in one transaction",
          kind: "synthetic_example",
          entity_ids: ["bitcoin:mainnet:tx:" + "a".repeat(64)],
          evidence_ids: ["e1"],
        },
        {
          id: "f2",
          title: "One unconfirmed transaction is pending",
          kind: "synthetic_example",
          entity_ids: ["bitcoin:mainnet:address:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"],
          evidence_ids: ["e2"],
        },
      ],
      indicators: [
        { id: "i1", rule: "ofac_exact_match", matched: null, reason: "Not run: OFAC snapshot is added in Milestone 7" },
      ],
      graph: {
        nodes: [
          { id: "bitcoin:mainnet:address:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh", type: "address", label: "bc1qxy2k...x0wlh" },
          { id: "bitcoin:mainnet:tx:" + "a".repeat(64), type: "transaction", label: "tx aaaa...aaaa" },
          { id: "bitcoin:mainnet:address:1BoatSLRHtKNngkdXEeobR76b53LETtpyT", type: "address", label: "1BoatS...tpyT" },
        ],
        edges: [
          { id: "g1", source: "bitcoin:mainnet:address:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh", target: "bitcoin:mainnet:tx:" + "a".repeat(64), relation: "observed", label: "input, 100,000 sats", evidence_ids: ["e1"] },
          { id: "g2", source: "bitcoin:mainnet:tx:" + "a".repeat(64), target: "bitcoin:mainnet:address:1BoatSLRHtKNngkdXEeobR76b53LETtpyT", relation: "observed", label: "output, 60,000 sats", evidence_ids: ["e1"] },
          { id: "g3", source: "bitcoin:mainnet:tx:" + "a".repeat(64), target: "bitcoin:mainnet:address:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh", relation: "inferred", label: "output, 38,800 sats (possible change)", evidence_ids: ["e1"] },
        ],
        truncated: false,
        limits: { max_nodes: 50 },
      },
      evidence: [
        { id: "e1", source: "Synthetic transaction record", source_url: "fixture://demo-btc-address/tx", retrieved_at: RETRIEVED, method_version: "fixture_v1", excerpt: "1 input, 2 outputs, fee 1,200 sats, confirmed in block 800,000 (synthetic)" },
        { id: "e2", source: "Synthetic address summary", source_url: "fixture://demo-btc-address/address", retrieved_at: RETRIEVED, method_version: "fixture_v1", excerpt: "Confirmed: 4 tx. Unconfirmed: 1 tx (synthetic)" },
      ],
      source_timestamps: { "synthetic fixture": RETRIEVED },
      limitations: ["Synthetic teaching example", "'Possible change' is an inference, not proof the same person owns both addresses"],
      uncertainty: { unconfirmed_tx_count: 1 },
      scorecard: { configured: false },
    },
  },
  {
    caseId: "demo-x402-payment",
    label: "Demo: x402 paid API call",
    mode: "x402",
    input: { type: "x402_payment_id", value: "x402-demo-0001" },
    result: {
      schema_version: "1.0",
      investigation_id: "demo-inv-0003",
      mode: "x402",
      method: "fixture_review",
      data_mode: "synthetic",
      findings: [
        {
          id: "f1",
          title: "Service delivered before settlement was confirmed",
          kind: "synthetic_example",
          entity_ids: ["x402:request:x402-demo-0001"],
          evidence_ids: ["e1", "e2"],
        },
      ],
      indicators: [
        { id: "i1", rule: "status_order_check", matched: true, reason: "Delivery time is earlier than settlement confirmation time (synthetic records)" },
      ],
      x402_status: { request: "402 issued", verification: "valid", settlement: "pending", delivery: "delivered" },
      graph: {
        nodes: [
          { id: "eip155:84532:address:0x000000000000000000000000000000000000dEaD", type: "address", label: "payer 0x0000...dEaD" },
          { id: "x402:request:x402-demo-0001", type: "payment", label: "request x402-demo-0001" },
          { id: "web:endpoint:api.weather-demo.test/forecast", type: "service", label: "api.weather-demo.test" },
          { id: "x402:facilitator:demo-facilitator", type: "service", label: "demo facilitator" },
        ],
        edges: [
          { id: "g1", source: "eip155:84532:address:0x000000000000000000000000000000000000dEaD", target: "x402:request:x402-demo-0001", relation: "reported", label: "signed payment payload", evidence_ids: ["e1"] },
          { id: "g2", source: "x402:request:x402-demo-0001", target: "web:endpoint:api.weather-demo.test/forecast", relation: "reported", label: "paid request", evidence_ids: ["e1"] },
          { id: "g3", source: "x402:facilitator:demo-facilitator", target: "x402:request:x402-demo-0001", relation: "reported", label: "verified, settlement pending", evidence_ids: ["e2"] },
        ],
        truncated: false,
        limits: { max_nodes: 50 },
      },
      evidence: [
        { id: "e1", source: "Synthetic service log", source_url: "fixture://demo-x402/service-log", retrieved_at: RETRIEVED, method_version: "fixture_v1", excerpt: "02:10:04 request paid, 02:10:05 response 200 delivered (synthetic)" },
        { id: "e2", source: "Synthetic facilitator log", source_url: "fixture://demo-x402/facilitator-log", retrieved_at: RETRIEVED, method_version: "fixture_v1", excerpt: "02:10:04 verify ok, settlement submitted, not yet confirmed (synthetic)" },
      ],
      source_timestamps: { "synthetic fixture": RETRIEVED },
      limitations: ["Synthetic teaching example on a test network ID", "Not live x402 monitoring", "Bitcoin data cannot show x402 activity"],
      uncertainty: { settlement_confirmed: false },
      scorecard: { configured: false },
    },
  },
  {
    caseId: "demo-outage",
    label: "Demo: provider outage (shows the error state)",
    mode: "crypto_investigation",
    input: { type: "bitcoin_txid", value: "b".repeat(64) },
    simulateError: "Demo error: the data provider did not answer within 10 seconds.",
  },
];