import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import {
  WelcomePage,
  ChatPage,
  KnowledgeBasePage,
  SettingsPage,
} from "./pages/PlaceholderPages";

// Route structure only — no layout, sidebar, or real UI yet. This exists
// to verify navigation works in isolation before anything is built on
// top of it. /chat/:conversationId (not a static /workspace) because
// conversation history is client-side only (no backend persistence
// endpoint exists in query.py/documents.py) — each conversation gets
// its own address so it's shareable/bookmarkable within a session and
// "New Chat" has somewhere real to navigate to.
export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<WelcomePage />} />
        <Route path="/chat/:conversationId" element={<ChatPage />} />
        <Route path="/knowledge-base" element={<KnowledgeBasePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        {/* Unknown paths fall back to the welcome screen rather than a blank 404 */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}