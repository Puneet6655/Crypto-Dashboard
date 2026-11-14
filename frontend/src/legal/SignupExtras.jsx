import "./legal.css";

// The extra sign-up questions, shared by the classic and Web3 sign-in pages and the "complete your account" page:
// date of birth (checked, never stored), what kind of user you are, organisation (some types), and consent.
// `value` is { dob, userType, organisation, accept }; `onChange` gets the whole new value.
// `askAge` false hides the date of birth (already confirmed); `askType` false hides type and organisation.

export const EMPTY_EXTRAS = { dob: "", userType: "", organisation: "", accept: false };

const FALLBACK_TYPES = {
  researcher: "Researcher",
  student: "Student",
  organisation: "Organisation or company",
  law_enforcement: "Law enforcement or government",
  other: "Other",
};

// Latest date of birth that is old enough today, as yyyy-mm-dd (for the date picker's max).
function latestBirthDate(minAge) {
  const d = new Date();
  d.setFullYear(d.getFullYear() - minAge);
  return d.toISOString().slice(0, 10);
}

// Same rule as the backend (backend/app/legal.py), so the person hears about it before submitting.
export function extrasProblem(value, minAge = 18, { askAge = true, askType = true } = {}) {
  if (askAge) {
    if (!value.dob) return "Please enter your date of birth.";
    if (value.dob > latestBirthDate(minAge)) return `You must be at least ${minAge} to use this dashboard.`;
  }
  if (askType && !value.userType) return "Please choose what describes you best.";
  if (!value.accept) return "Please read and accept the terms of use and privacy policy.";
  return "";
}

export function extrasBody(value) {
  return {
    date_of_birth: value.dob || null,
    user_type: value.userType || null,
    organisation: value.organisation.trim() || null,
    accept_terms: value.accept,
  };
}

export default function SignupExtras({ value, onChange, config, askAge = true, askType = true }) {
  const minAge = config?.min_age ?? 18;
  const types = config?.user_types ?? FALLBACK_TYPES;
  const orgTypes = config?.organisation_types ?? ["organisation", "law_enforcement", "researcher"];
  const set = (key) => (event) => {
    const v = event.target.type === "checkbox" ? event.target.checked : event.target.value;
    onChange({ ...value, [key]: v });
  };

  return (
    <>
      {askAge && (
        <label className="field">
          <span className="field-label">Date of birth</span>
          <input type="date" value={value.dob} onChange={set("dob")} required max={latestBirthDate(minAge)} min="1900-01-01"
            autoComplete="bday" />
          <span className="small muted">You must be {minAge} or over. We check it and do not store it.</span>
        </label>
      )}
      {askType && (
        <label className="field">
          <span className="field-label">What describes you best?</span>
          <select value={value.userType} onChange={set("userType")} required>
            <option value="" disabled>Choose one</option>
            {Object.entries(types).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </label>
      )}
      {askType && orgTypes.includes(value.userType) && (
        <label className="field">
          <span className="field-label">Organisation or university <span className="muted">(optional)</span></span>
          <input type="text" value={value.organisation} onChange={set("organisation")} maxLength={120} autoComplete="organization" />
        </label>
      )}
      <label className="consent-check">
        <input type="checkbox" checked={value.accept} onChange={set("accept")} required />
        <span>
          I am {minAge} or over and I agree to the <a href="#/terms" target="_blank" rel="noopener">terms of use</a> and
          the <a href="#/privacy" target="_blank" rel="noopener">privacy policy</a>. I understand risk scores are estimates,
          not legal or financial advice.
        </span>
      </label>
    </>
  );
}
