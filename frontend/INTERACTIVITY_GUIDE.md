# How the interactive frontend works (and how to change it yourself)

This guide explains every change made in this round, in the order the data flows.
Read it with the files open next to it.

## 0. The big idea

React draws the page from **state** (data that can change). When state changes, React redraws.
The graph is different: **Cytoscape.js** draws on a `<canvas>` and keeps its own state (positions, zoom).
So `GraphCanvas.jsx` is a bridge: React hands Cytoscape an empty `<div>`, then **effects** (`useEffect`)
tell Cytoscape what to do whenever React state changes.

```
App.jsx (state: result, selectedId, activeTab, status)
  ├─ ControlsPanel   (form)
  ├─ GraphView       (toolbar, filters, search)  → GraphCanvas (Cytoscape)
  ├─ EntityDetails   (side panel)
  └─ ResultTabs      (bottom tabs)
```

`selectedId` lives in `App`. The graph, the side panel and the tabs all read and set it, which is why
clicking anywhere updates everywhere. This is called **lifting state up**: put shared state in the nearest common parent.

## 1. Installing Cytoscape

```
cd frontend
npm install cytoscape@3.33.1 --save-exact
```
`--save-exact` pins the version in `package.json`, matching how the other dependencies are pinned.
Then `import cytoscape from "cytoscape"`.

## 2. `src/config/graphStyle.js`: how the graph looks

Two lookup tables, `NODE_TYPES` and `RELATIONS`, hold colour, shape and line style.
The legend, the filters and the Cytoscape stylesheet all read from them.

**To add a new node type** (say `contract`): add one line to `NODE_TYPES`. It gets a colour, shape, legend chip and filter automatically.

`buildStylesheet()` is like CSS: `selector` + `style`. Later rules win, so `.selected` and `.faded` come last.
`node[type = "address"]` means "nodes whose data `type` is address". Docs: https://js.cytoscape.org/#style

`LAYOUTS` is the layout menu. Each entry's `options` goes straight to `cy.layout()`.

## 3. `GraphCanvas.jsx`: the interactive graph

### 3a. Converting data: `toElements`
Cytoscape wants `{ data: { id, ... } }` for nodes and `{ data: { id, source, target } }` for edges.
Two safety details:
- Edges whose ends are missing are skipped (Cytoscape throws on those).
- **Bundling.** A real transaction had 131 inputs from one address, which drew 131 stacked lines. Links with the same source, target and relation are merged into one thicker line (`count` data, `mapData(count, 2, 60, 3, 10)` makes thickness proportional). `bundleOf` remembers which line each original link belongs to, so selecting a link by ID still highlights the right line.

### 3b. Six effects, each with one job
| # | Runs when | Does |
|---|-----------|------|
| 1 | data changes | creates the Cytoscape instance, attaches mouse events, destroys it on cleanup |
| 2 | layout choice / "Re-arrange" | runs the layout |
| 3 | filters change | adds class `filtered` (CSS `display: none`) |
| 4 | `selectedId` changes | adds `selected` to it, `faded` to everything not next to it |
| 5 | search text changes | adds `match` outline |
| 6 | "Link labels" toggled | adds `labelled` to edges |

**Pattern to copy:** don't change colours by hand. Add/remove a **class** and let the stylesheet define what the class looks like. Splitting into one effect per concern keeps each simple.

Every effect that starts something returns a **cleanup function** (`return () => ...`). React runs it before the next run and on unmount. Effect 1 stops any layout animation before `cy.destroy()`, otherwise Cytoscape keeps drawing into a destroyed graph and throws.

### 3c. Mouse and touch features
- **Zoom / pan / drag nodes**: built into Cytoscape. We only set `minZoom`/`maxZoom`.
- **Click** (`cy.on("tap", "node, edge", ...)`): calls `onSelect`. Click empty space clears the selection.
- **Double-click** a node: animates to fit that node and its neighbours.
- **Hover tooltip**: `mouseover` stores `{x, y, text}` in React state; a `<div>` is positioned at that spot with CSS `position: absolute`. `pointer-events: none` stops the tooltip from stealing the mouse.
- **`onSelectRef`**: Cytoscape handlers are attached once, so they would call the *first* `onSelect` forever (a "stale closure"). A ref always holds the latest function.

## 4. `GraphView.jsx`: states, toolbar, filters

- **Loading state** (`LoadingState`): spinner, live seconds counter (a `setInterval` in an effect, cleared on cleanup) and a **Cancel** button.
- **Filters**: `hiddenTypes` and `hiddenRelations` are `Set`s. The legend chips toggle membership. We always create a *new* Set (`new Set(old)`) because React only notices changes to a new object, never to one changed in place.
- **Search**: `useMemo` computes the matching node IDs from the text. Enter selects the first match; effect 4 pans to it if off screen.
- **Toolbar**: zoom in/out/fit call `cy.animate(...)`; PNG uses `cy.png()` and a temporary `<a download>` link.
- **`key={result.investigation_id}`** on `GraphExplorer`: changing a React `key` throws the component away and builds a fresh one, so filters and search reset for each new investigation.
- A plain **list of links** is kept in a `<details>` so keyboard and screen-reader users can do everything without the canvas.

## 5. Cancel and loading (`App.jsx`, `api/client.js`)

- `requestRef = useRef(null)` holds the current `AbortController`. A ref is used (not state) because changing it should not redraw the page.
- `handleCancel` calls `abort()`. `startInvestigation(form, { signal })` forwards that abort to its own controller, which also handles the 20-second timeout.
- After `await`, `if (controller.signal.aborted) return;` throws away a late answer so an old request cannot overwrite a newer one.
- `ControlsPanel` is now a `<form onSubmit>`, so pressing **Enter** in the text box starts the investigation.

## 6. `EntityDetails.jsx`: the side panel

- Shows a node **or** an edge (it looks the ID up in both lists).
- "Connected to" rows are buttons that call `onSelect`, so you can walk the graph from the panel.
- Neighbours are grouped by `direction|otherId`, so 131 links from one address show as one row.
- Copy button uses `navigator.clipboard.writeText` inside `try/catch` because browsers can block it.

## 7. `ResultTabs.jsx`: linked tabs

The open tab now lives in `App` (`activeTab`), so clicking evidence anywhere (`handleEvidenceClick`) opens the **Evidence** tab and highlights that item. An effect scrolls it into view with `scrollIntoView`. Findings also have "Show in graph" buttons.

## 8. Recipes: changes you can try

1. **Change a colour**: edit `color` in `NODE_TYPES`.
2. **New layout**: add an entry to `LAYOUTS`; options are listed at https://js.cytoscape.org/#layouts
3. **Bigger canvas**: change `height: 460px` in `.graph-canvas` (`app.css`).
4. **Show fee in the tooltip**: find `describe()` in `GraphCanvas.jsx` and push another line.
5. **New toolbar button**: add a `<button onClick={...}>` in `GraphView`, using `cyRef.current` to reach Cytoscape.
6. **New highlight style**: add a rule like `{ selector: ".mine", style: {...} }` to `buildStylesheet`, then `cy.getElementById(id).addClass("mine")` in an effect.

## 9. Debugging tips

- Open browser DevTools (F12) → Console. Red text is an error with the file and line.
- In the console on the running app: `document.querySelector('.graph-canvas')._cyreg.cy` gives you the live Cytoscape object; try `.nodes().length` or `.zoom()`.
- If something does not update, ask: *is this React state (use setState) or Cytoscape state (use an effect)?*
- A blank canvas usually means the zoom limit is too tight for the layout size (this happened here: `minZoom` was 0.2 and had to become 0.05).
