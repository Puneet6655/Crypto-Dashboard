// Web3 design switch, shared by the files that change with it.
// On with VITE_LOGIN_DESIGN=web3 (set on Vercel for the live site; run-web3.bat sets it locally) or ?web3 in the URL.
// ?classic in the URL (or VITE_LOGIN_DESIGN unset / anything else) shows the original design, which still works as before.
const params = new URLSearchParams(window.location.search);
export const WEB3_DESIGN = !params.has("classic") && (import.meta.env.VITE_LOGIN_DESIGN === "web3" || params.has("web3"));
