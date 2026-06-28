import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Upload, X, Send, FileText, ChevronRight, Trash2, UserCircle2, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/workspace")({
  head: () => ({
    meta: [
      { title: "Workspace — DocMind" },
      { name: "description", content: "Upload documents and ask questions." },
    ],
  }),
  component: WorkspacePage,
});

const VIDEO_URL =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260328_065045_c44942da-53c6-4804-b734-f9e07fc22e08.mp4";

const API_BASE =
  (import.meta.env.VITE_DOCMIND_API_BASE as string | undefined) ??
  "http://127.0.0.1:8000";
  const API_KEY = (import.meta.env.VITE_DOCMIND_API_KEY as string | undefined) ?? "";

const AUTH_HEADERS: Record<string, string> = {
  "X-API-Key": API_KEY,
};

function genId() {
  return "id-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function useVideoFadeLoop() {
  const ref = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    const FADE = 500;
    let raf = 0;
    const tick = () => {
      if (!video.duration || Number.isNaN(video.duration)) {
        raf = requestAnimationFrame(tick);
        return;
      }
      const t = video.currentTime;
      const d = video.duration;
      let op = 1;
      if (t < FADE / 1000) op = t / (FADE / 1000);
      else if (t > d - FADE / 1000) op = Math.max(0, (d - t) / (FADE / 1000));
      video.style.opacity = String(op);
      raf = requestAnimationFrame(tick);
    };
    const onEnded = () => {
      video.style.opacity = "0";
      window.setTimeout(() => {
        video.currentTime = 0;
        video.play().catch(() => {});
      }, 100);
    };
    video.style.opacity = "0";
    video.play().catch(() => {});
    raf = requestAnimationFrame(tick);
    video.addEventListener("ended", onEnded);
    return () => {
      cancelAnimationFrame(raf);
      video.removeEventListener("ended", onEnded);
    };
  }, []);
  return ref;
}

type Doc = { id: string; name: string; size: number; status: "indexed" | "indexing" | "failed" };
type Citation = { source: string; page?: number | string };
type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  confidence?: "high" | "medium" | "low";
  citations?: Citation[];
};

// ─── Root page ────────────────────────────────────────────────────────────────

function WorkspacePage() {
  const videoRef = useVideoFadeLoop();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [onlineStatus, setOnlineStatus] = useState<"online" | "offline" | "checking">("checking");

  useEffect(() => {
    const check = () => {
      fetch(`${API_BASE}/documents/`)
        .then((r) => setOnlineStatus(r.ok ? "online" : "offline"))
        .catch(() => setOnlineStatus("offline"));
    };
    check();
    const id = setInterval(check, 15_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background p-0">
      {/* Video background */}
      <div className="fixed inset-0 z-0 overflow-hidden">
        <video
          ref={videoRef}
          src={VIDEO_URL}
          muted
          playsInline
          autoPlay
          className="absolute inset-0 h-full w-full object-cover"
          style={{ opacity: 0 }}
        />
        <div className="absolute inset-0 bg-background/40" />
      </div>

      {/* ── Header ── */}
      <header className="relative z-30 flex h-14 shrink-0 items-center justify-between px-5">
        {/* Logo */}
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-500/15 border border-violet-400/20 shadow-[0_0_12px_rgba(139,92,246,0.15)]">
            <FileText className="h-4 w-4 text-violet-300" />
          </div>
          <span className="font-display text-base font-semibold tracking-tight text-foreground">DocMind</span>
        </div>

        {/* Right side: status pill + avatar */}
        <div className="flex items-center gap-3">
          <div className={cn(
            "flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium",
            onlineStatus === "online"
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
              : onlineStatus === "offline"
              ? "border-rose-500/30 bg-rose-500/10 text-rose-300"
              : "border-amber-500/30 bg-amber-500/10 text-amber-300"
          )}>
            <motion.span
              animate={onlineStatus === "online" ? { scale: [1, 1.4, 1], opacity: [1, 0.5, 1] } : {}}
              transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                onlineStatus === "online" ? "bg-emerald-400" :
                onlineStatus === "offline" ? "bg-rose-400" : "bg-amber-400"
              )}
            />
            {onlineStatus === "online"
              ? `Active · ${docs.length} doc${docs.length === 1 ? "" : "s"}`
              : onlineStatus === "offline" ? "Offline" : "Connecting…"}
          </div>

          {/* Avatar */}
          <button className="flex h-8 w-8 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.04] text-foreground/50 hover:bg-white/[0.08] transition-colors">
            <UserCircle2 className="h-4.5 w-4.5" />
          </button>
        </div>
      </header>

      {/* ── Body ── */}
      <div className="relative z-10 flex flex-1 gap-3 overflow-hidden p-3 pt-2">
        <LeftPanel docs={docs} setDocs={setDocs} />
        <RightPanel docs={docs} />
      </div>
    </div>
  );
}

// ─── Left panel ───────────────────────────────────────────────────────────────

function LeftPanel({
  docs,
  setDocs,
}: {
  docs: Doc[];
  setDocs: React.Dispatch<React.SetStateAction<Doc[]>>;
}) {
  const [dragOver, setDragOver] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [highlighted, setHighlighted] = useState<string[]>([]);
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [deletingAll, setDeletingAll] = useState(false);
  const cardRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    const onHighlight = (e: Event) => {
      const sources = ((e as CustomEvent).detail?.sources ?? []) as string[];
      const norm = (s: string) => s.toLowerCase().split(/[\\/]/).pop()!.trim();
      const wanted = sources.map(norm);
      const matchedIds = docs.filter((d) => wanted.includes(norm(d.name))).map((d) => d.id);
      setHighlighted(matchedIds);
      if (matchedIds[0]) {
        cardRefs.current[matchedIds[0]]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    };
    const onClear = () => setHighlighted([]);
    window.addEventListener("docmind:highlight", onHighlight);
    window.addEventListener("docmind:highlight-clear", onClear);
    return () => {
      window.removeEventListener("docmind:highlight", onHighlight);
      window.removeEventListener("docmind:highlight-clear", onClear);
    };
  }, [docs]);

  useEffect(() => {
    fetch(`${API_BASE}/documents/`, { headers: AUTH_HEADERS })
      .then((r) => (r.ok ? r.json() : { documents: [] }))
      .then((data) => {
        const names: string[] = Array.isArray(data.documents) ? data.documents : [];
        const list: Doc[] = names.map((name, i) => ({
          id: String(i) + "-" + name,
          name,
          size: 0,
          status: "indexed",
        }));
        if (list.length) setDocs(list);
      })
      .catch(() => {});
  }, []);

  // Poll /documents/status every 3s while any doc is still indexing.
  // Surfaces backend ingestion failures as a red "Failed" badge instead
  // of leaving the card stuck on "Indexing…" indefinitely.
  useEffect(() => {
    const poll = () => {
      const hasIndexing = docs.some((d) => d.status === "indexing");
      if (!hasIndexing) return;
      fetch(`${API_BASE}/documents/status`, { headers: AUTH_HEADERS })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!data?.status) return;
          setDocs((prev) =>
            prev.map((doc) => {
              const s = data.status[doc.name];
              if (s === "failed" && doc.status === "indexing") return { ...doc, status: "failed" as const };
              if (s === "indexed" && doc.status === "indexing") return { ...doc, status: "indexed" as const };
              return doc;
            })
          );
        })
        .catch(() => {});
    };
    const id = setInterval(poll, 3000);
    return () => clearInterval(id);
  }, [docs]);

  const uploadFile = useCallback(async (file: File) => {
    const id = genId();
    setProgress(0);
    setDocs((d) => [...d, { id, name: file.name, size: file.size, status: "indexing" }]);
    try {
      const form = new FormData();
      form.append("file", file);
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_BASE}/documents/upload`);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) setProgress(Math.round((e.loaded / e.total) * 100));
        };
        xhr.onload = () =>
          xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(xhr.statusText));
        xhr.onerror = () => reject(new Error("Network error"));
        xhr.send(form);
      });
      setDocs((d) => d.map((x) => (x.id === id ? { ...x, status: "indexed" } : x)));
    } catch {
      setDocs((d) => d.filter((x) => x.id !== id));
      alert(`Failed to upload ${file.name}`);
    } finally {
      setTimeout(() => setProgress(null), 600);
    }
  }, [setDocs]);

  const handleFiles = (files: FileList | null) => {
    if (!files) return;
    Array.from(files).forEach(uploadFile);
  };

  const deleteDoc = useCallback(async (doc: Doc) => {
    setDeleting(doc.id);
    try {
      await fetch(`${API_BASE}/documents/${encodeURIComponent(doc.name)}`, { method: "DELETE" });
    } catch {}
    finally {
      setDocs((d) => d.filter((x) => x.id !== doc.id));
      setDeleting(null);
    }
  }, [setDocs]);

  const deleteAll = useCallback(async () => {
    setDeletingAll(true);
    setConfirmDeleteAll(false);
    try {
      await fetch(`${API_BASE}/documents/`, { method: "DELETE" });
    } catch {
      await Promise.allSettled(
        docs.map((doc) =>
          fetch(`${API_BASE}/documents/${encodeURIComponent(doc.name)}`, { method: "DELETE" })
        )
      );
    } finally {
      setDocs([]);
      setDeletingAll(false);
    }
  }, [docs, setDocs]);

  return (
    <aside className="flex w-[280px] shrink-0 flex-col rounded-2xl border border-white/[0.04] bg-white/[0.015] backdrop-blur-sm shadow-[0_8px_32px_rgba(0,0,0,0.12),inset_0_1px_0_rgba(255,255,255,0.03)]">
      {/* Panel heading */}
      <div className="px-5 pt-5 pb-3">
        <h2 className="text-sm font-semibold text-foreground">Document Center</h2>
        <p className="mt-0.5 text-[11px] text-foreground/45">Manage your indexed knowledge base.</p>
      </div>

      {/* 1. Drag & drop zone */}
      <div className="px-3 pb-3">
        <input
          id="file-upload-input"
          type="file"
          multiple
          accept=".pdf,.docx,.txt"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        <motion.label
          htmlFor="file-upload-input"
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files); }}
          animate={dragOver ? { scale: 1.02 } : { scale: 1 }}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed py-6 text-center transition-all",
            dragOver
              ? "border-violet-400/60 bg-violet-500/[0.08] shadow-[0_0_40px_rgba(139,92,246,0.25)]"
              : "border-white/[0.1] bg-white/[0.02] hover:border-violet-500/30 hover:bg-violet-500/[0.03]"
          )}
        >
          <Upload className="h-6 w-6 text-foreground/50" />
          <div>
            <p className="text-sm font-medium text-foreground/80">Drag & drop files here...</p>
            <p className="mt-0.5 text-xs text-foreground/35">PDF, DOCX, TXT · up to 25MB</p>
          </div>
        </motion.label>

        {progress !== null && (
          <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-white/10">
            <motion.div
              className="h-full bg-gradient-to-r from-violet-500 to-indigo-400"
              initial={{ width: 0 }}
              animate={{ width: `${progress}%` }}
              transition={{ ease: "easeOut" }}
            />
          </div>
        )}
      </div>

      {/* 2. Document list */}
      <div className="flex min-h-0 flex-1 flex-col px-3">
        {/* "DOCUMENTS · N" label row */}
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-foreground/35">
            Documents
          </span>
          <span className="text-[10px] font-semibold text-foreground/35">{docs.length}</span>
        </div>

        <div className="flex-1 space-y-2 overflow-y-auto pr-0.5">
          <AnimatePresence>
            {docs.map((doc) => (
              <motion.div
                key={doc.id}
                ref={(el) => { cardRefs.current[doc.id] = el; }}
                layout
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, x: -16 }}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg border p-2.5 transition-all duration-300",
                  highlighted.includes(doc.id)
                    ? "border-violet-400/40 bg-violet-500/[0.07] shadow-[0_0_20px_rgba(139,92,246,0.2)]"
                    : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.05]"
                )}
              >
                <FileText className="h-4 w-4 shrink-0 text-foreground/50" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium text-foreground/85">{doc.name}</p>
                  {doc.size > 0 && (
                    <p className="text-[10px] text-foreground/35">{(doc.size / 1024).toFixed(1)} KB</p>
                  )}
                </div>
                {/* Status badge */}
                <span className={cn(
                  "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold",
                  doc.status === "indexed"
                    ? "bg-emerald-400/15 text-emerald-400"
                    : doc.status === "failed"
                    ? "bg-rose-400/15 text-rose-400"
                    : "bg-amber-400/15 text-amber-400"
                )}>
                  {doc.status === "indexed" ? "Indexed" : doc.status === "failed" ? "Failed" : "Indexing…"}
                </span>
                {/* Per-doc delete */}
                <button
                  onClick={() => deleteDoc(doc)}
                  disabled={deleting === doc.id || deletingAll}
                  className="shrink-0 rounded-lg p-1 text-foreground/30 transition-colors hover:text-rose-400 disabled:opacity-30"
                >
                  {deleting === doc.id ? (
                    <motion.span
                      animate={{ rotate: 360 }}
                      transition={{ duration: 0.7, repeat: Infinity, ease: "linear" }}
                      className="block h-3.5 w-3.5 rounded-full border-2 border-rose-400/40 border-t-rose-400"
                    />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" />
                  )}
                </button>
              </motion.div>
            ))}
          </AnimatePresence>
          {docs.length === 0 && (
            <p className="pt-8 text-center text-xs text-foreground/25">No documents yet.</p>
          )}
        </div>
      </div>

      {/* 3. Clear All — pinned bottom, flat text button style */}
      <div className="p-3 pt-2">
        <AnimatePresence mode="wait">
          {confirmDeleteAll ? (
            <motion.div
              key="confirm"
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              className="flex items-center justify-between rounded-xl border border-rose-400/20 bg-rose-400/[0.05] px-4 py-3"
            >
              <span className="text-xs text-foreground/60">Delete all documents?</span>
              <div className="flex gap-2">
                <button
                  onClick={deleteAll}
                  disabled={deletingAll}
                  className="rounded-lg px-3 py-1 text-xs font-semibold text-rose-400 border border-rose-400/30 bg-rose-400/10 hover:bg-rose-400/20 transition-colors disabled:opacity-40"
                >
                  Yes, clear
                </button>
                <button
                  onClick={() => setConfirmDeleteAll(false)}
                  className="rounded-lg px-3 py-1 text-xs text-foreground/50 border border-white/10 hover:bg-white/5 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </motion.div>
          ) : (
            <motion.button
              key="trigger"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => docs.length > 0 && setConfirmDeleteAll(true)}
              disabled={docs.length === 0 || deletingAll}
              className="flex w-full items-center justify-center gap-2 py-2 text-xs text-foreground/30 transition-colors hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-25"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Clear All Documents
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </aside>
  );
}

// ─── Right panel ──────────────────────────────────────────────────────────────

function RightPanel({ docs }: { docs: Doc[] }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const send = useCallback(async (q: string) => {
    const query = q.trim();
    if (!query || loading) return;
    setInput("");
    setMessages((m) => [...m, { id: genId(), role: "user", content: query }]);
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/query/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const data = await res.json();
      const confLevel = data.confidence?.level ?? "medium";
      const conf: "high" | "medium" | "low" =
        confLevel === "high" ? "high" : confLevel === "low" ? "low" : "medium";
      const citations = (data.citations ?? []).map((c: any) => ({
        source: c.source_file ?? c.source ?? "Unknown",
        page: c.page_number ?? c.page,
      }));
      setMessages((m) => [
        ...m,
        {
          id: genId(),
          role: "assistant",
          content: data.answer ?? data.response ?? "No answer returned.",
          confidence: conf,
          citations,
        },
      ]);
    } catch {
      setMessages((m) => [
        ...m,
        {
          id: genId(),
          role: "assistant",
          content: "Could not reach the backend at " + API_BASE + ". Make sure it's running.",
          confidence: "low",
          citations: [],
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, [loading]);

  const firstDoc = docs[0]?.name ?? null;
  const quickPrompts = firstDoc
    ? [`Summarize the latest document`, "Find key risks", "List action items"]
    : ["Summarize the latest document", "Find key risks", "List action items"];

  return (
    <section className="flex flex-1 flex-col overflow-hidden rounded-2xl border border-white/[0.04] bg-white/[0.01] backdrop-blur-sm shadow-[0_8px_32px_rgba(0,0,0,0.1),inset_0_1px_0_rgba(255,255,255,0.02)]">
      {/* Right panel header — "Interactive Assistant" + question count */}
      <div className="flex items-center justify-between border-b border-white/[0.05] px-5 py-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-violet-300" />
          <span className="font-semibold text-foreground">Interactive Assistant</span>
        </div>
        <span className="text-xs text-foreground/30">
          {messages.filter((m) => m.role === "user").length} question{messages.filter((m) => m.role === "user").length === 1 ? "" : "s"} asked
        </span>
      </div>

      {/* Chat history stream */}
      <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-5 py-5">
        {messages.length === 0 && !loading && (
          <div className="m-auto flex flex-col items-center gap-3 text-center">
            <Sparkles className="h-8 w-8 text-foreground/20" />
            <p className="text-sm text-foreground/35">
              Ask a question about your uploaded documents.
            </p>
          </div>
        )}
        <AnimatePresence initial={false}>
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
        </AnimatePresence>
        {loading && <TypingIndicator />}
        <div ref={endRef} />
      </div>

      {/* Quick prompt chips */}
      <div className="px-5 pb-3">
        <div className="flex flex-wrap gap-2">
          {quickPrompts.map((prompt) => (
            <button
              key={prompt}
              onClick={() => send(prompt)}
              disabled={loading}
              className="rounded-full border border-white/[0.08] bg-white/[0.04] px-3.5 py-1.5 text-xs font-medium text-foreground/60 transition-all hover:border-violet-400/25 hover:bg-white/[0.07] hover:text-foreground/90 disabled:opacity-40"
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>

      {/* Input bar — dark card style */}
      <div className="px-5 pb-4">
        <div className="flex items-center gap-3 rounded-xl border border-white/[0.07] bg-white/[0.03] px-4 py-2.5 backdrop-blur-sm shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
          <span className="text-foreground/25">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
          </span>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send(input)}
            placeholder="Ask a question..."
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-foreground/30 outline-none"
          />
          <button
            onClick={() => send(input)}
            disabled={loading || !input.trim()}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-500/70 text-white transition-all hover:bg-violet-400/80 shadow-[0_0_12px_rgba(139,92,246,0.3)] disabled:opacity-30 disabled:shadow-none"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </section>
  );
}

// ─── Shared sub-components ────────────────────────────────────────────────────

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className={cn("flex", isUser ? "justify-end" : "justify-start")}
    >
      <div
        className={cn(
          "max-w-[78%] rounded-2xl px-4 py-3 text-sm",
          isUser
            ? "bg-violet-500/60 text-white backdrop-blur-sm shadow-[0_4px_16px_rgba(139,92,246,0.2)]"
            : "border border-white/[0.06] bg-white/[0.03] text-foreground backdrop-blur-sm"
        )}
      >
        <p className="whitespace-pre-wrap leading-relaxed">{message.content}</p>
        {!isUser && message.confidence && (
          <div className="mt-3 flex items-center gap-2">
            <ConfidenceBadge level={message.confidence} />
          </div>
        )}
        {!isUser && message.citations && message.citations.length > 0 && (
          <Citations citations={message.citations} />
        )}
      </div>
    </motion.div>
  );
}

function ConfidenceBadge({ level }: { level: "high" | "medium" | "low" }) {
  const cls = {
    high: "bg-emerald-400/15 text-emerald-400 border-emerald-400/40",
    medium: "bg-amber-400/15 text-amber-400 border-amber-400/40",
    low: "bg-rose-400/15 text-rose-400 border-rose-400/40",
  }[level];
  const label = { high: "High confidence", medium: "Medium confidence", low: "Low confidence" }[level];
  return (
    <motion.span
      initial={{ scale: 0.9, opacity: 0 }}
      animate={{ scale: [0.9, 1.08, 1], opacity: 1 }}
      transition={{ duration: 0.5 }}
      className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium", cls)}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full",
        level === "high" ? "bg-emerald-400" : level === "medium" ? "bg-amber-400" : "bg-rose-400"
      )} />
      {label}
    </motion.span>
  );
}

function Citations({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent(open ? "docmind:highlight" : "docmind:highlight-clear", {
        detail: { sources: citations.map((c) => c.source) },
      })
    );
  }, [open, citations]);
  useEffect(() => {
    return () => { window.dispatchEvent(new CustomEvent("docmind:highlight-clear")); };
  }, []);

  return (
    <div className="mt-3 border-t border-white/10 pt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="group flex w-full items-center gap-1.5 rounded-md px-1 py-1 text-xs font-medium text-foreground/60 transition-colors hover:text-foreground"
      >
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={{ duration: 0.2 }} className="inline-flex">
          <ChevronRight className="h-3.5 w-3.5" />
        </motion.span>
        <span>{citations.length} source{citations.length === 1 ? "" : "s"}</span>
        <span className="ml-auto text-[10px] uppercase tracking-wider text-foreground/30 group-hover:text-foreground/50">
          {open ? "Hide" : "Show"}
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="citations"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ height: { duration: 0.28, ease: [0.16, 1, 0.3, 1] }, opacity: { duration: 0.18 } }}
            className="overflow-hidden"
          >
            <ul className="mt-2 space-y-1.5">
              {citations.map((c, i) => (
                <motion.li
                  key={i}
                  initial={{ opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.05 + i * 0.04, duration: 0.22 }}
                  className="flex items-center gap-2 rounded-md border border-white/[0.05] bg-white/[0.03] px-2.5 py-1.5 text-xs text-foreground/75 hover:border-violet-400/25 transition-colors"
                >
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-violet-500/15 text-[10px] font-semibold text-violet-300">
                    {i + 1}
                  </span>
                  <FileText className="h-3.5 w-3.5 shrink-0 text-foreground/40" />
                  <span className="truncate font-medium">{c.source}</span>
                  {c.page != null && (
                    <span className="ml-auto shrink-0 rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-foreground/50">
                      p. {c.page}
                    </span>
                  )}
                </motion.li>
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function TypingIndicator() {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex justify-start">
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.03] px-4 py-3 backdrop-blur-sm">
        <div className="flex gap-1.5">
          {[0, 1, 2].map((i) => (
            <motion.span
              key={i}
              className="h-2 w-2 rounded-full bg-violet-400/60"
              animate={{ y: [0, -4, 0], opacity: [0.4, 1, 0.4] }}
              transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15 }}
            />
          ))}
        </div>
      </div>
    </motion.div>
  );
}