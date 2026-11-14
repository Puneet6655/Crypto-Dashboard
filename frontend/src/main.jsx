import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles/app.css";
// Page and card animations (separate file; delete this line to remove them).
import "./motion/enableMotion.js";

// Find the empty <div id="root"> in index.html and draw the App inside it.
createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);