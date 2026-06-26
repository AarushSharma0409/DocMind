# DocMind System Design & UI/UX Specification

**Design Language:** Glassmorphic Abyss Violet  
**Application Type:** Single-Page Application (SPA) Productivity Dashboard  
**Target Goal:** To provide a highly visual, trustworthy, and distraction-free document intelligence workspace.

---

## 1. Global Color Tokens

These design tokens must be mapped to your CSS custom properties or your Tailwind configuration (`tailwind.config.js`) under an extended theme.

| Token Name | Value | Usage |
| :--- | :--- | :--- |
| `--bg-main` | `#0a0714` | The foundational canvas background. |
| `--bg-surface-glass`| `rgba(26, 21, 48, 0.65)` | The frosted glass body for workspace panels. |
| `--border-glass` | `rgba(245, 245, 245, 0.12)` | Subtle edge styling to define glass boundaries. |
| `--accent-primary` | `#6366f1` | Indigo: Primary call-to-actions, drag-over highlights, send buttons. |
| `--accent-secondary`| `#c084fc` | Soft Magenta: Selection states, accent highlights, hovers. |
| `--text-primary` | `#f5f5f5` | Bright off-white: Maximum legibility for headings and main copy. |
| `--text-muted` | `#a1a1aa` | Muted cool gray: Subtitles, file metrics, and secondary details. |
| `--status-success` | `#10b981` | Emerald: High confidence indicators, successfully indexed files. |
| `--status-warning` | `#f59e0b` | Amber: Medium confidence indicators, file uploading/processing state. |
| `--status-danger` | `#ef4444` | Red/Rose: Serious actions, delete buttons, clear database triggers. |

---

## 2. Layout & Viewport Rules

*   **Fixed Canvas Layout:** The application behaves like a local desktop app using `100vh` and `100vw`. No global window scrolling is allowed; scroll areas are isolated to individual internal containers.
*   **The Split Architecture:**
    *   **Global Layout:** Flex row or CSS Grid (`grid-cols-[400px_1fr]` or `grid-cols-[1fr_3fr]`).
    *   **Left Panel (Document Center):** Fixed at `400px` max width. Manages file interactions and knowledge status.
    *   **Right Panel (Assistant Chat Area):** Flexes dynamically to fill the rest of the available viewport.

---

## 3. Glassmorphism Design Class (CSS Mixin)

To maintain structural cohesion across both workspaces, apply this standard styling combination to both panels:

```css
.glass-workspace-panel {
  background: var(--bg-surface-glass);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border: 1px solid var(--border-glass);
  border-radius: 16px;
  box-shadow: 0 12px 40px 0 rgba(0, 0, 0, 0.5);