// Placeholder pages — Stage 2, routing verification only.
// Each is replaced with real UI in later Stage 2 pieces (welcome screen,
// chat view, knowledge base, settings). Their only job right now is to
// prove the router actually navigates between distinct routes.

export function WelcomePage() {
  return <div style={{ padding: "2rem" }}>Welcome page (/) — placeholder</div>;
}

export function ChatPage() {
  return (
    <div style={{ padding: "2rem" }}>
      Chat page (/chat/:conversationId) — placeholder
    </div>
  );
}

export function KnowledgeBasePage() {
  return (
    <div style={{ padding: "2rem" }}>Knowledge Base page — placeholder</div>
  );
}

export function SettingsPage() {
  return <div style={{ padding: "2rem" }}>Settings page — placeholder</div>;
}