"use client";

import {
  Activity,
  Archive,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Bell,
  Check,
  ClipboardCheck,
  Clock3,
  Inbox as InboxIcon,
  LayoutDashboard,
  ListTodo,
  Mail as MailIcon,
  RefreshCw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  Star,
  Zap,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import {
  api,
  ApiError,
  type Approval,
  type Audit,
  type Dashboard,
  type Detail,
  type Draft,
  type Inbox,
  type Items,
  type Mail,
  type Preferences,
  type Profile,
  type Task,
  type Thread,
} from "./api";

const navigation = [
  ["Overview", LayoutDashboard],
  ["Inbox", InboxIcon],
  ["Approvals", ClipboardCheck],
  ["Tasks", ListTodo],
  ["Audit log", Activity],
  ["Settings", Settings],
] as const;
type View = (typeof navigation)[number][0];
const categories = [
  "customer_support",
  "sales",
  "work",
  "finance",
  "meeting",
  "personal",
  "newsletter",
  "spam_suspected",
  "other",
];
const tones = ["professional", "friendly", "concise"];
const pageSize = 12;
const human = (text: string) =>
  text.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
const senderName = (sender: string) =>
  (sender.split("<")[0].trim() || sender).replace(/^"|"$/g, "");
const initials = (sender: string) =>
  senderName(sender)
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join("") || "?";
function time(value: string, zone?: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: zone,
  }).format(new Date(value));
}
function Badge({ value }: { value: string }) {
  return <span className={`badge badge-${value}`}>{human(value)}</span>;
}
function Avatar({ sender }: { sender: string }) {
  return (
    <span
      className={`avatar color-${[...sender].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 5}`}
      aria-hidden="true"
    >
      {initials(sender)}
    </span>
  );
}
function Empty({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <InboxIcon size={21} />
      </span>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}
function Events({
  items,
  zone,
  full = false,
}: {
  items: Audit[];
  zone?: string;
  full?: boolean;
}) {
  if (!items.length) return <Empty title="No activity yet" />;
  return (
    <ul className="events">
      {items.map((item) => (
        <li key={item.id}>
          <span className={`event-icon ${item.outcome}`}>
            {item.outcome === "succeeded" ? (
              <Check size={15} />
            ) : (
              <Clock3 size={15} />
            )}
          </span>
          <div>
            <strong>{item.action}</strong>
            <small>
              {time(item.created_at, zone)}
              {full && item.metadata.code
                ? ` · ${human(String(item.metadata.code))}`
                : ""}
              {full && item.metadata.provider_id
                ? ` · Provider ID ending ${String(item.metadata.provider_id).slice(-8)}`
                : ""}
            </small>
          </div>
          {full && <Badge value={item.outcome} />}
        </li>
      ))}
    </ul>
  );
}
function MessageRow({
  mail,
  zone,
  selected,
  checked,
  toggle,
  open,
}: {
  mail: Mail;
  zone?: string;
  selected?: boolean;
  checked?: boolean;
  toggle?: () => void;
  open: () => void;
}) {
  return (
    <div className={`message-row ${selected ? "selected" : ""}`}>
      {toggle && (
        <input
          type="checkbox"
          checked={checked}
          onChange={toggle}
          aria-label={`Select ${mail.subject}`}
        />
      )}
      <button type="button" className="message-button" onClick={open}>
        <Avatar sender={mail.sender} />
        <span className="row-text">
          <span className="sender">
            {mail.unread && <span className="unread-dot" aria-label="Unread" />}
            {senderName(mail.sender)}
          </span>
          <strong>{mail.subject}</strong>
          <small>
            {mail.analysis ? human(mail.analysis.category) + " · " : ""}
            {mail.preview}
          </small>
        </span>
        <span className="row-meta">
          <small>{time(mail.received_at, zone)}</small>
          {mail.priority ? (
            <Badge value={mail.priority} />
          ) : (
            <span className="untriaged">Untriaged</span>
          )}
        </span>
      </button>
    </div>
  );
}
function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <Send size={20} strokeWidth={2.3} />
      </span>
      <span>
        InboxPilot <b>AI</b>
      </span>
    </div>
  );
}
export default function Workspace() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [boot, setBoot] = useState(true);
  const [available, setAvailable] = useState({
    gmail_available: false,
    demo_available: true,
  });
  const [view, setView] = useState<View>("Overview");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [approvalList, setApprovalList] = useState<Approval[]>([]);
  const [taskList, setTaskList] = useState<Task[]>([]);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [settings, setSettings] = useState<Preferences | null>(null);
  const [vip, setVip] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [thread, setThread] = useState<Thread[]>([]);
  const [activeId, setActiveId] = useState("");
  const [draftId, setDraftId] = useState("");
  const [edit, setEdit] = useState<Draft | null>(null);
  const [tone, setTone] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [priority, setPriority] = useState("");
  const [sort, setSort] = useState("newest");
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(1);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [rev, setRev] = useState(0);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const zone = prefs?.timezone;

  useEffect(() => {
    let active = true;
    api<Profile>("/me")
      .then((data) => {
        if (active) setProfile(data);
      })
      .catch(async (cause) => {
        if (!active) return;
        if (!(cause instanceof ApiError) || cause.status !== 401)
          setError(cause.message);
        const options = await api<typeof available>("/auth/options").catch(
          () => null,
        );
        if (active && options) setAvailable(options);
      })
      .finally(() => {
        if (!active) return;
        if (
          new URLSearchParams(window.location.search).get("gmail") === "failed"
        )
          setError(
            "Gmail connection failed. Check the account and permissions, then try again.",
          );
        setBoot(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!profile) return;
    let active = true;
    api<Preferences>("/preferences")
      .then((data) => {
        if (active) {
          setPrefs(data);
          setSettings(data);
          setVip(data.vip_senders.join(", "));
        }
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      });
    return () => {
      active = false;
    };
  }, [profile]);

  useEffect(() => {
    if (!profile) return;
    let active = true;
    api<Dashboard>("/dashboard")
      .then((data) => {
        if (active) setDashboard(data);
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      });
    return () => {
      active = false;
    };
  }, [profile, rev]);

  useEffect(() => {
    if (!profile || view === "Overview" || view === "Settings") return;
    let active = true;
    const timer = window.setTimeout(
      async () => {
        setLoading(true);
        try {
          if (view === "Inbox") {
            const params = new URLSearchParams({
              q: query,
              sort,
              page: String(page),
              limit: String(pageSize),
            });
            if (category) params.set("category", category);
            if (priority) params.set("priority", priority);
            if (filter === "unread") params.set("unread", "true");
            if (filter === "archived") params.set("archived", "true");
            const data = await api<Inbox>(`/emails?${params}`);
            if (active) setInbox(data);
          }
          if (view === "Approvals") {
            const data = await api<Items<Approval>>(
              `/approvals?offset=${offset}&limit=30`,
            );
            if (active) setApprovalList(data.items);
          }
          if (view === "Tasks") {
            const data = await api<Items<Task>>(
              `/tasks?offset=${offset}&limit=30`,
            );
            if (active) setTaskList(data.items);
          }
          if (view === "Audit log") {
            const data = await api<Items<Audit>>(
              `/audit?offset=${offset}&limit=30`,
            );
            if (active) setAudit(data.items);
          }
        } catch (cause) {
          if (active) setError((cause as Error).message);
        } finally {
          if (active) setLoading(false);
        }
      },
      view === "Inbox" ? 180 : 0,
    );
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [
    profile,
    view,
    query,
    category,
    priority,
    sort,
    filter,
    page,
    rev,
    offset,
  ]);

  useEffect(() => {
    if (!profile || !activeId || view !== "Inbox") return;
    let active = true;
    const timer = window.setTimeout(async () => {
      setDetailLoading(true);
      try {
        const [message, context] = await Promise.all([
          api<Detail>(
            `/emails/${activeId}${draftId ? `?draft_id=${encodeURIComponent(draftId)}` : ""}`,
          ),
          api<Items<Thread>>(`/emails/${activeId}/thread`),
        ]);
        if (active) {
          setDetail(message);
          setThread(context.items.filter((item) => item.id !== activeId));
          setEdit((previous) =>
            previous?.id === message.draft?.id &&
            previous?.version === message.draft?.version
              ? previous
              : message.draft,
          );
        }
      } catch (cause) {
        if (active) setError((cause as Error).message);
      } finally {
        if (active) setDetailLoading(false);
      }
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [profile, activeId, draftId, view, rev]);

  function navigate(target: View) {
    setView(target);
    setOffset(0);
    setError("");
    setLoading(target !== "Overview" && target !== "Settings");
  }
  function open(id: string, targetDraft = "") {
    if (id !== activeId || targetDraft !== draftId) {
      setDetail(null);
      setEdit(null);
      setDetailLoading(true);
    }
    setActiveId(id);
    setDraftId(targetDraft);
    navigate("Inbox");
  }
  function act(label: string, work: () => Promise<void>) {
    setBusy(label);
    setError("");
    setNotice("");
    void work()
      .catch((cause) => {
        if (cause instanceof ApiError && cause.status === 401) setProfile(null);
        setError((cause as Error).message);
      })
      .finally(() => setBusy(""));
  }
  function write<T>(path: string, data?: unknown, method = "POST") {
    return api<T>(path, method, data, profile?.csrf);
  }
  function refresh(message: string) {
    setRev((value) => value + 1);
    setNotice(message);
  }
  function analyze(id: string) {
    act("Analyzing…", async () => {
      const result = await write<{ status: string }>(`/emails/${id}/analyze`);
      if (result.status !== "succeeded") {
        setRev((value) => value + 1);
        throw new Error(
          "Analysis failed. Try again or review the run history.",
        );
      }
      refresh("Analysis saved.");
    });
  }
  function sync() {
    act("Syncing…", async () => {
      const result = await write<{ synced: number; failed: number }>(
        "/emails/sync",
      );
      refresh(
        `Synced ${result.synced} messages${result.failed ? ` · ${result.failed} unavailable` : ""}.`,
      );
    });
  }
  function startDemo() {
    act("Creating workspace…", async () => {
      await api("/auth/demo", "POST");
      setProfile(await api<Profile>("/me"));
      navigate("Overview");
    });
  }
  function resetPage() {
    setPage(1);
    setSelected([]);
  }
  function saveSettings(event: FormEvent) {
    event.preventDefault();
    if (!settings) return;
    act("Saving…", async () => {
      const updated = await write<Preferences>(
        "/preferences",
        {
          timezone: settings.timezone,
          tone: settings.tone,
          vip_senders: vip
            .split(/[,\n]+/)
            .map((item) => item.trim())
            .filter(Boolean),
          follow_up_days: settings.follow_up_days,
          ai_consent: settings.ai_consent,
        },
        "PATCH",
      );
      setPrefs(updated);
      setSettings(updated);
      setVip(updated.vip_senders.join(", "));
      refresh("Preferences saved.");
    });
  }

  if (boot)
    return (
      <main className="onboarding">
        <Brand />
        <p role="status">Opening workspace…</p>
      </main>
    );
  if (!profile)
    return (
      <main className="onboarding">
        <Brand />
        <div className="welcome panel">
          <span className="welcome-icon">
            <Sparkles size={26} />
          </span>
          <h1>Your email workspace</h1>
          <p>
            Prioritize messages, turn requests into tasks, and review replies
            before they go out.
          </p>
          {error && (
            <div role="alert" className="alert error">
              {error}
            </div>
          )}
          {available.demo_available && (
            <button
              className="button primary"
              disabled={!!busy}
              onClick={startDemo}
            >
              {busy || "Start demo workspace"}
              <ArrowRight size={17} />
            </button>
          )}
          {available.gmail_available ? (
            <a className="button" href="/api/v1/auth/google/start">
              <MailIcon size={17} /> Connect Gmail
            </a>
          ) : (
            <p className="muted">
              Gmail connection requires server configuration.
            </p>
          )}
          <small>
            Demo messages are synthetic. Gmail starts with read-only access and
            stores synchronized email on the server.
          </small>
        </div>
      </main>
    );

  const descriptions: Record<View, string> = {
    Overview:
      "Track the messages, tasks, and decisions that need your attention.",
    Inbox: "Triage messages, inspect analysis, and prepare replies.",
    Approvals: "Review the exact payload before authorizing each action.",
    Tasks: "Track requests, confirmed due dates, and suggested follow-ups.",
    "Audit log": "Review workspace activity and action outcomes.",
    Settings: "Manage your account, permissions, and assistant preferences.",
  };
  const metrics = [
    ["Analyzed", dashboard?.analyzed, Sparkles, "violet"],
    ["Urgent", dashboard?.urgent, Zap, "rose"],
    ["Awaiting reply", dashboard?.awaiting_reply, MailIcon, "blue"],
    ["Pending approvals", dashboard?.pending, ShieldCheck, "amber"],
    ["Open tasks", dashboard?.open_tasks, ListTodo, "green"],
  ] as const;

  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand />
        <nav aria-label="Workspace">
          {navigation.map(([label, Icon]) => (
            <button
              key={label}
              className={view === label ? "current" : ""}
              aria-current={view === label ? "page" : undefined}
              onClick={() => navigate(label)}
            >
              <Icon size={17} />
              <span>{label}</span>
              {label === "Approvals" && !!dashboard?.pending && (
                <small>{dashboard.pending}</small>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="mode-icon">
            <ShieldCheck size={17} />
          </div>
          <div>
            <strong>
              {profile.mode === "demo" ? "Demo workspace" : "Gmail workspace"}
            </strong>
            <small>
              {profile.mode === "demo"
                ? "Simulated email actions"
                : profile.email}
            </small>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <span>/</span> <strong>{view}</strong>
          </div>
          <div className="top-actions">
            <Badge
              value={
                profile.status === "disconnected"
                  ? "disconnected"
                  : profile.mode === "demo"
                    ? "demo mode"
                    : "connected"
              }
            />
            <button
              className="icon-button"
              aria-label="Open audit log"
              onClick={() => navigate("Audit log")}
            >
              <Bell size={17} />
            </button>
            <Avatar
              sender={profile.mode === "demo" ? "Demo user" : profile.email}
            />
          </div>
        </header>
        <main className="content">
          <div className="heading">
            <div>
              <h1>{view}</h1>
              <p>{descriptions[view]}</p>
            </div>
            {(view === "Overview" || view === "Inbox") && (
              <button
                className="button primary"
                disabled={
                  !!busy ||
                  (profile.mode === "gmail" && profile.status !== "connected")
                }
                onClick={
                  profile.mode === "gmail" ? sync : () => navigate("Inbox")
                }
              >
                {profile.mode === "gmail" ? (
                  <RefreshCw size={17} />
                ) : (
                  <InboxIcon size={17} />
                )}
                {profile.mode === "gmail" ? "Sync inbox" : "Open inbox"}
              </button>
            )}
          </div>
          {error && (
            <div role="alert" className="alert error">
              {error}
            </div>
          )}
          {(busy || notice) && (
            <div role="status" className="alert">
              {busy || notice}
            </div>
          )}
          {profile.status === "disconnected" && (
            <div className="alert">
              Account disconnected. Locally stored messages remain available.
            </div>
          )}

          {view === "Overview" && (
            <>
              <div className="metrics">
                {metrics.map(([label, value, Icon, color]) => (
                  <section className="panel metric" key={label}>
                    <span className={`metric-icon ${color}`}>
                      <Icon size={19} />
                    </span>
                    <div>
                      <p>{label}</p>
                      <strong>{value ?? "–"}</strong>
                      {label === "Analyzed" && dashboard && (
                        <small> / {dashboard.total}</small>
                      )}
                    </div>
                  </section>
                ))}
              </div>
              <div className="overview-grid">
                <section className="panel">
                  <div className="section-head">
                    <div>
                      <h2>Priority inbox</h2>
                      <p>Highest-priority messages first</p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => navigate("Inbox")}
                    >
                      View inbox <ArrowUpRight size={15} />
                    </button>
                  </div>
                  {dashboard ? (
                    dashboard.priority_inbox.length ? (
                      dashboard.priority_inbox.map((mail) => (
                        <MessageRow
                          key={mail.id}
                          mail={mail}
                          zone={zone}
                          open={() => open(mail.id)}
                        />
                      ))
                    ) : (
                      <Empty title="Your inbox is empty">
                        Sync Gmail to bring in recent messages.
                      </Empty>
                    )
                  ) : (
                    <Empty title="Loading inbox…" />
                  )}
                </section>
                <section className="panel">
                  <div className="section-head">
                    <div>
                      <h2>Recent activity</h2>
                      <p>Latest workspace changes</p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => navigate("Audit log")}
                    >
                      View log <ArrowUpRight size={15} />
                    </button>
                  </div>
                  <Events items={dashboard?.activity || []} zone={zone} />
                </section>
              </div>
              <div className="info-strip">
                <ShieldCheck size={19} />
                <div>
                  <strong>Every email action needs your approval</strong>
                  <p>
                    Review recipients, message content, and mailbox changes in
                    the approval center.
                  </p>
                </div>
                <button
                  className="text-button"
                  onClick={() => navigate("Approvals")}
                >
                  Review approvals <ArrowRight size={16} />
                </button>
              </div>
            </>
          )}

          {view === "Inbox" && (
            <>
              <div className="panel toolbar">
                <label className="search">
                  <Search size={17} />
                  <input
                    aria-label="Search inbox"
                    placeholder="Search sender, subject, or message"
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      resetPage();
                    }}
                  />
                </label>
                <select
                  aria-label="Category"
                  value={category}
                  onChange={(e) => {
                    setCategory(e.target.value);
                    resetPage();
                  }}
                >
                  <option value="">All categories</option>
                  {categories.map((item) => (
                    <option key={item} value={item}>
                      {human(item)}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Priority"
                  value={priority}
                  onChange={(e) => {
                    setPriority(e.target.value);
                    resetPage();
                  }}
                >
                  <option value="">All priorities</option>
                  {["critical", "high", "normal", "low", "untriaged"].map(
                    (item) => (
                      <option key={item} value={item}>
                        {human(item)}
                      </option>
                    ),
                  )}
                </select>
                <select
                  aria-label="Mailbox filter"
                  value={filter}
                  onChange={(e) => {
                    setFilter(e.target.value);
                    resetPage();
                  }}
                >
                  <option value="">Inbox</option>
                  <option value="unread">Unread</option>
                  <option value="archived">Archived</option>
                </select>
                <select
                  aria-label="Sort order"
                  value={sort}
                  onChange={(e) => {
                    setSort(e.target.value);
                    resetPage();
                  }}
                >
                  <option value="newest">Newest</option>
                  <option value="oldest">Oldest</option>
                  <option value="priority">Priority</option>
                </select>
              </div>
              <div className={`inbox-grid ${activeId ? "message-open" : ""}`}>
                <section className="panel inbox-list">
                  <div className="section-head">
                    <h2>{inbox?.total ?? "–"} messages</h2>
                    {selected.length > 0 && (
                      <button
                        className="button small"
                        disabled={!!busy}
                        onClick={() =>
                          act("Analyzing selected messages…", async () => {
                            const result = await write<
                              Items<{ status: string }>
                            >("/emails/analyze-batch", { ids: selected });
                            const success = result.items.filter(
                              (item) => item.status === "succeeded",
                            ).length;
                            setSelected([]);
                            refresh(
                              `Analyzed ${success} of ${result.items.length} messages.`,
                            );
                          })
                        }
                      >
                        <Sparkles size={15} /> Analyze {selected.length}
                      </button>
                    )}
                  </div>
                  {loading && (
                    <p className="loading" role="status">
                      Loading messages…
                    </p>
                  )}
                  {!loading && !inbox?.items.length && (
                    <Empty title="No matching messages">
                      Try another filter or sync your mailbox.
                    </Empty>
                  )}
                  {inbox?.items.map((mail) => (
                    <MessageRow
                      key={mail.id}
                      mail={mail}
                      zone={zone}
                      selected={activeId === mail.id}
                      checked={selected.includes(mail.id)}
                      toggle={() =>
                        setSelected((ids) =>
                          ids.includes(mail.id)
                            ? ids.filter((id) => id !== mail.id)
                            : ids.length < 10
                              ? [...ids, mail.id]
                              : ids,
                        )
                      }
                      open={() => open(mail.id)}
                    />
                  ))}
                  <div className="pagination">
                    <button
                      className="button small"
                      disabled={page <= 1 || loading}
                      onClick={() => {
                        setPage(page - 1);
                        setSelected([]);
                      }}
                    >
                      Previous
                    </button>
                    <span>Page {page}</span>
                    <button
                      className="button small"
                      disabled={
                        loading || page * pageSize >= (inbox?.total || 0)
                      }
                      onClick={() => {
                        setPage(page + 1);
                        setSelected([]);
                      }}
                    >
                      Next
                    </button>
                  </div>
                </section>
                <section className="panel detail">
                  {activeId && (
                    <button
                      className="text-button mobile-back"
                      onClick={() => setActiveId("")}
                    >
                      <ArrowLeft size={16} /> Back to inbox
                    </button>
                  )}
                  {detailLoading ? (
                    <Empty title="Loading message…" />
                  ) : detail && activeId ? (
                    <>
                      <div className="detail-header">
                        <div className="detail-sender">
                          <Avatar sender={detail.sender} />
                          <div>
                            <strong>{senderName(detail.sender)}</strong>
                            <small>{detail.sender}</small>
                          </div>
                          {detail.priority && <Badge value={detail.priority} />}
                        </div>
                        <h2>{detail.subject}</h2>
                        <small>{time(detail.received_at, zone)}</small>
                        <div className="actions">
                          <button
                            className="button primary small"
                            disabled={!!busy}
                            onClick={() => analyze(detail.id)}
                          >
                            <Sparkles size={16} />
                            {detail.analysis ? "Run analysis" : "Analyze email"}
                          </button>
                          <button
                            className="button small"
                            disabled={!!busy}
                            onClick={() =>
                              act("Updating message…", async () => {
                                await write(
                                  `/emails/${detail.id}`,
                                  { unread: !detail.unread },
                                  "PATCH",
                                );
                                refresh(
                                  detail.unread
                                    ? "Marked as read."
                                    : "Marked as unread.",
                                );
                              })
                            }
                          >
                            {detail.unread ? "Mark read" : "Mark unread"}
                          </button>
                        </div>
                      </div>
                      <p className="mail-body">
                        {detail.body || "No plain-text message body."}
                      </p>
                      {thread.length > 0 && (
                        <details className="fold">
                          <summary>Thread context ({thread.length})</summary>
                          {thread.map((item) => (
                            <article key={item.id}>
                              <strong>{item.subject}</strong>
                              <small>
                                {senderName(item.sender)} ·{" "}
                                {time(item.received_at, zone)}
                              </small>
                              <p className="mail-body">{item.body}</p>
                            </article>
                          ))}
                        </details>
                      )}
                      {detail.analysis && (
                        <section className="analysis-card">
                          <div className="inline">
                            <Sparkles size={18} />
                            <h3>Message analysis</h3>
                            <Badge value={detail.analysis.category} />
                          </div>
                          <p>{detail.analysis.summary}</p>
                          <p className="muted">{detail.analysis.intent}</p>
                          <p>
                            <strong>
                              {detail.analysis.priority_score}/100
                            </strong>{" "}
                            · Importance {detail.analysis.importance}/4 ·
                            Urgency {detail.analysis.urgency}/4 · Reply{" "}
                            {detail.analysis.reply_required
                              ? "needed"
                              : "optional"}{" "}
                            · VIP {detail.analysis.vip ? "yes" : "no"}
                          </p>
                          {detail.analysis.evidence.map((item, index) => (
                            <blockquote key={index}>{item}</blockquote>
                          ))}
                          {!!detail.analysis.tasks.length && (
                            <ul className="extracted">
                              {detail.analysis.tasks.map((item, index) => (
                                <li key={index}>
                                  {item.title}{" "}
                                  <small>
                                    ·{" "}
                                    {item.due_at ||
                                      (item.date_text
                                        ? `Date unknown (${item.date_text})`
                                        : "No stated deadline")}
                                  </small>
                                </li>
                              ))}
                            </ul>
                          )}
                          {detail.analysis.follow_up_at && (
                            <p>
                              Suggested follow-up{" "}
                              {time(detail.analysis.follow_up_at, zone)}
                            </p>
                          )}
                          <div className="actions">
                            {detail.analysis.recommended_actions.map((item) => (
                              <Badge key={item} value={item} />
                            ))}
                          </div>
                        </section>
                      )}
                      <form
                        key={detail.id + String(detail.priority_override)}
                        className="actions detail-tools"
                        onSubmit={(event) => {
                          event.preventDefault();
                          const value = new FormData(event.currentTarget).get(
                            "override",
                          );
                          act("Saving priority…", async () => {
                            await write(
                              `/emails/${detail.id}`,
                              {
                                priority_override:
                                  value === "" ? null : Number(value),
                              },
                              "PATCH",
                            );
                            refresh("Priority updated.");
                          });
                        }}
                      >
                        <label>
                          Priority override{" "}
                          <input
                            name="override"
                            type="number"
                            min="0"
                            max="100"
                            placeholder="Auto"
                            defaultValue={detail.priority_override ?? ""}
                          />
                        </label>
                        <button className="button small" disabled={!!busy}>
                          Save
                        </button>
                        {(["archive", "label"] as const).map((action) => (
                          <button
                            type="button"
                            className="button small"
                            key={action}
                            disabled={!!busy}
                            onClick={() =>
                              act("Creating proposal…", async () => {
                                const proposed = await write<Approval>(
                                  `/emails/${detail.id}/propose`,
                                  { action },
                                );
                                navigate("Approvals");
                                refresh(
                                  proposed.status === "pending"
                                    ? "Proposal ready to review."
                                    : `Proposal is ${proposed.status}.`,
                                );
                              })
                            }
                          >
                            {action === "archive" ? (
                              <Archive size={15} />
                            ) : (
                              <Star size={15} />
                            )}
                            {action === "archive"
                              ? "Propose archive"
                              : "Propose star"}
                          </button>
                        ))}
                      </form>
                      <div className="composer">
                        <div className="inline spread">
                          <h3>Reply draft</h3>
                          <select
                            aria-label="Reply tone"
                            value={tone || prefs?.tone || "professional"}
                            onChange={(e) => setTone(e.target.value)}
                          >
                            {tones.map((item) => (
                              <option key={item} value={item}>
                                {human(item)}
                              </option>
                            ))}
                          </select>
                        </div>
                        <button
                          className="button small"
                          disabled={!!busy}
                          onClick={() =>
                            act("Generating draft…", async () => {
                              const generated = await write<Draft>(
                                `/emails/${detail.id}/draft`,
                                { tone: tone || prefs?.tone || "professional" },
                              );
                              setEdit(generated);
                              setDraftId(generated.id);
                              refresh("Draft generated.");
                            })
                          }
                        >
                          <Sparkles size={15} /> Generate draft
                        </button>
                        {edit && (
                          <form
                            onSubmit={(event) => {
                              event.preventDefault();
                              act("Saving draft…", async () => {
                                setEdit(
                                  await write<Draft>(
                                    `/drafts/${edit.id}`,
                                    {
                                      subject: edit.subject,
                                      body: edit.body,
                                      version: edit.version,
                                    },
                                    "PATCH",
                                  ),
                                );
                                refresh("Draft saved.");
                              });
                            }}
                          >
                            <p className="muted">
                              From {profile.email} · To{" "}
                              {detail.reply_to || "Unavailable"}
                            </p>
                            <label>
                              Subject
                              <input
                                required
                                maxLength={250}
                                value={edit.subject}
                                disabled={edit.status !== "editing"}
                                onChange={(e) =>
                                  setEdit({ ...edit, subject: e.target.value })
                                }
                              />
                            </label>
                            <label>
                              Body
                              <textarea
                                required
                                maxLength={8000}
                                rows={7}
                                value={edit.body}
                                disabled={edit.status !== "editing"}
                                onChange={(e) =>
                                  setEdit({ ...edit, body: e.target.value })
                                }
                              />
                            </label>
                            <div className="actions">
                              <Badge value={edit.status} />
                              <button
                                className="button small"
                                disabled={!!busy || edit.status !== "editing"}
                              >
                                Save draft
                              </button>
                              <button
                                className="button primary small"
                                type="button"
                                disabled={
                                  !!busy ||
                                  edit.status !== "editing" ||
                                  !detail.reply_to
                                }
                                onClick={() =>
                                  act("Creating approval…", async () => {
                                    const saved =
                                      edit.subject !== detail.draft?.subject ||
                                      edit.body !== detail.draft?.body
                                        ? await write<Draft>(
                                            `/drafts/${edit.id}`,
                                            {
                                              subject: edit.subject,
                                              body: edit.body,
                                              version: edit.version,
                                            },
                                            "PATCH",
                                          )
                                        : edit;
                                    setEdit(saved);
                                    const proposed = await write<Approval>(
                                      `/drafts/${saved.id}/propose-send`,
                                    );
                                    navigate("Approvals");
                                    refresh(
                                      proposed.status === "pending"
                                        ? "Reply ready for approval."
                                        : `Approval is ${proposed.status}.`,
                                    );
                                  })
                                }
                              >
                                Request send approval <ArrowRight size={15} />
                              </button>
                            </div>
                          </form>
                        )}
                      </div>
                      {detail.runs.length > 0 && (
                        <details className="fold">
                          <summary>
                            Analysis history ({detail.runs.length})
                          </summary>
                          {detail.runs.map((run) => (
                            <p key={run.id}>
                              {time(run.started_at, zone)} · {run.model} ·{" "}
                              {human(run.status)}
                              {run.error_code
                                ? ` · ${human(run.error_code)}`
                                : ""}
                            </p>
                          ))}
                        </details>
                      )}
                    </>
                  ) : (
                    <Empty title="Select a message">
                      Open an email to view its content and analysis.
                    </Empty>
                  )}
                </section>
              </div>
            </>
          )}

          {view === "Approvals" && (
            <section className="panel collection">
              {loading ? (
                <Empty title="Loading approvals…" />
              ) : !approvalList.length ? (
                <Empty title="No approvals on this page">
                  Proposed replies and mailbox changes appear here.
                </Empty>
              ) : (
                approvalList.map((item) => (
                  <article key={item.id} className="collection-item">
                    <div className="inline spread">
                      <h2>
                        {item.action_type === "send"
                          ? "Send reply"
                          : item.action_type === "archive"
                            ? "Archive message"
                            : "Star message"}
                      </h2>
                      <Badge value={item.status} />
                    </div>
                    <p>
                      <strong>Subject</strong> {item.payload.subject}
                    </p>
                    {item.action_type === "send" ? (
                      <>
                        <p>
                          <strong>From</strong> {item.payload.from} ·{" "}
                          <strong>To</strong> {item.payload.to} ·{" "}
                          <strong>Draft version</strong> {item.payload.version}
                        </p>
                        <div className="approval-body">{item.payload.body}</div>
                      </>
                    ) : (
                      <p>
                        {item.action_type === "archive"
                          ? "Remove this message from the inbox."
                          : "Apply the Gmail Starred label to this message."}{" "}
                        Account {item.payload.from}.
                      </p>
                    )}
                    {item.error_code && (
                      <p className="muted">
                        {human(item.error_code)}
                        {item.status === "unknown" &&
                          " · Check the provider before sending another reply."}
                      </p>
                    )}
                    {item.status === "pending" && (
                      <div className="actions">
                        {item.action_type === "send" && (
                          <button
                            className="button small"
                            disabled={!!busy}
                            onClick={() =>
                              open(item.email_id, item.draft_id || "")
                            }
                          >
                            Edit draft
                          </button>
                        )}
                        {(["reject", "approve"] as const).map((decision) => (
                          <button
                            key={decision}
                            className={`button small ${decision === "approve" ? "primary" : ""}`}
                            disabled={!!busy}
                            onClick={() =>
                              act("Recording decision…", async () => {
                                const result = await write<Approval>(
                                  `/approvals/${item.id}/${decision}`,
                                  { payload_hash: item.payload_hash },
                                );
                                refresh(
                                  result.status === "succeeded" &&
                                    profile.mode === "demo"
                                    ? "Simulated action succeeded."
                                    : `Action ${result.status}.`,
                                );
                              })
                            }
                          >
                            {decision === "reject"
                              ? "Reject"
                              : item.action_type === "send"
                                ? profile.mode === "demo"
                                  ? "Approve simulated send"
                                  : "Approve and send"
                                : `Approve ${item.action_type === "label" ? "star" : "archive"}`}
                          </button>
                        ))}
                      </div>
                    )}
                  </article>
                ))
              )}
            </section>
          )}

          {view === "Tasks" && (
            <section className="panel collection">
              {loading ? (
                <Empty title="Loading tasks…" />
              ) : !taskList.length ? (
                <Empty title="No tasks on this page">
                  Analyze a message with an actionable request to extract tasks.
                </Empty>
              ) : (
                taskList.map((task) => (
                  <article className="collection-item task" key={task.id}>
                    <div className="inline spread">
                      <h2>{task.title}</h2>
                      <Badge value={task.status} />
                    </div>
                    <button
                      className="text-button"
                      onClick={() => open(task.email_id)}
                    >
                      {task.subject} <ArrowUpRight size={14} />
                    </button>
                    <p>
                      {task.original_date_text &&
                        `Date mentioned: ${task.original_date_text}. `}
                      {!task.due_at && "Confirmed due date unknown. "}
                      {task.follow_up_at &&
                        `Suggested follow-up ${time(task.follow_up_at, zone)}.`}
                    </p>
                    <div className="actions">
                      <label>
                        Due date{" "}
                        <input
                          type="date"
                          key={task.due_at || "unknown"}
                          defaultValue={task.due_at || ""}
                          disabled={!!busy}
                          onChange={(e) => {
                            const value = e.target.value;
                            act("Updating task…", async () => {
                              await write(
                                `/tasks/${task.id}`,
                                { due_at: value || null },
                                "PATCH",
                              );
                              refresh("Task date updated.");
                            });
                          }}
                        />
                      </label>
                      <button
                        className="button small"
                        disabled={!!busy}
                        onClick={() =>
                          act("Updating task…", async () => {
                            await write(
                              `/tasks/${task.id}`,
                              {
                                status:
                                  task.status === "open" ? "completed" : "open",
                              },
                              "PATCH",
                            );
                            refresh("Task updated.");
                          })
                        }
                      >
                        {task.status === "open" ? (
                          <>
                            <Check size={15} /> Complete task
                          </>
                        ) : (
                          "Reopen task"
                        )}
                      </button>
                    </div>
                  </article>
                ))
              )}
            </section>
          )}

          {view === "Audit log" && (
            <section className="panel">
              {loading ? (
                <Empty title="Loading activity…" />
              ) : (
                <Events items={audit} zone={zone} full />
              )}
            </section>
          )}
          {["Tasks", "Approvals", "Audit log"].includes(view) && (
            <div className="pagination">
              <button
                className="button small"
                disabled={!offset || loading}
                onClick={() => setOffset(offset - 30)}
              >
                Previous
              </button>
              <span>Page {offset / 30 + 1}</span>
              <button
                className="button small"
                disabled={
                  loading ||
                  (view === "Tasks"
                    ? taskList
                    : view === "Approvals"
                      ? approvalList
                      : audit
                  ).length < 30
                }
                onClick={() => setOffset(offset + 30)}
              >
                Next
              </button>
            </div>
          )}

          {view === "Settings" && (
            <div className="settings-grid">
              <section className="panel collection-item">
                <h2>Email account</h2>
                <p>
                  {profile.email} · {human(profile.status)}
                </p>
                <p>
                  {profile.mode === "demo"
                    ? "This workspace uses synthetic email and simulated actions."
                    : "Synced message content is stored on this server. Sync is manually triggered."}
                </p>
                {!!profile.scopes.length && (
                  <ul className="scopes">
                    {profile.scopes.map((item) => (
                      <li key={item}>{item.split(".").at(-1)}</li>
                    ))}
                  </ul>
                )}
                <div className="actions">
                  {profile.mode === "gmail" &&
                    profile.status === "connected" && (
                      <>
                        <button
                          className="button small"
                          disabled={!!busy}
                          onClick={sync}
                        >
                          <RefreshCw size={15} /> Sync inbox
                        </button>
                        {!profile.scopes.some((scope) =>
                          /gmail\.(?:send|modify)$/.test(scope),
                        ) && (
                          <a
                            className="button small"
                            href="/api/v1/auth/google/start?permission=send"
                          >
                            Grant sending access
                          </a>
                        )}
                        {!profile.scopes.some((scope) =>
                          scope.endsWith("gmail.modify"),
                        ) && (
                          <a
                            className="button small"
                            href="/api/v1/auth/google/start?permission=modify"
                          >
                            Grant mailbox changes
                          </a>
                        )}
                        <button
                          className="button small"
                          disabled={!!busy}
                          onClick={() =>
                            act("Disconnecting…", async () => {
                              const result = await write<{ revoked: boolean }>(
                                "/accounts/disconnect",
                              );
                              setProfile(await api<Profile>("/me"));
                              refresh(
                                result.revoked
                                  ? "Gmail disconnected."
                                  : "Gmail disconnected locally. Provider revocation failed.",
                              );
                            })
                          }
                        >
                          Disconnect Gmail
                        </button>
                      </>
                    )}
                  {profile.gmail_available &&
                    (profile.mode === "demo" ||
                      profile.status === "disconnected") && (
                      <a
                        className="button small"
                        href="/api/v1/auth/google/start"
                      >
                        {profile.mode === "demo"
                          ? "Connect Gmail"
                          : "Reconnect Gmail"}
                      </a>
                    )}
                  <button
                    className="button small"
                    disabled={!!busy}
                    onClick={() =>
                      act("Signing out…", async () => {
                        await write("/auth/logout");
                        setAvailable(
                          await api<typeof available>("/auth/options"),
                        );
                        setProfile(null);
                        setDashboard(null);
                        setActiveId("");
                      })
                    }
                  >
                    Sign out
                  </button>
                </div>
              </section>
              <section className="panel collection-item">
                <h2>Assistant preferences</h2>
                {settings ? (
                  <form className="preferences" onSubmit={saveSettings}>
                    <label>
                      Timezone
                      <input
                        required
                        value={settings.timezone}
                        placeholder="Asia/Karachi"
                        onChange={(e) =>
                          setSettings({ ...settings, timezone: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      Default tone
                      <select
                        value={settings.tone}
                        onChange={(e) =>
                          setSettings({ ...settings, tone: e.target.value })
                        }
                      >
                        {tones.map((item) => (
                          <option key={item} value={item}>
                            {human(item)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      VIP senders
                      <input
                        value={vip}
                        placeholder="name@example.com, team@example.com"
                        onChange={(e) => setVip(e.target.value)}
                      />
                    </label>
                    <label>
                      Follow-up interval (days)
                      <input
                        type="number"
                        required
                        min="1"
                        max="30"
                        value={settings.follow_up_days}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            follow_up_days: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    {profile.mode === "gmail" &&
                      profile.llm_provider === "openai" && (
                        <label className="checkbox">
                          <input
                            type="checkbox"
                            checked={settings.ai_consent}
                            onChange={(e) =>
                              setSettings({
                                ...settings,
                                ai_consent: e.target.checked,
                              })
                            }
                          />{" "}
                          Allow sending selected email and thread text to OpenAI
                          for analysis and drafting.
                        </label>
                      )}
                    <button className="button primary small" disabled={!!busy}>
                      Save preferences
                    </button>
                  </form>
                ) : (
                  <p role="status">Loading preferences…</p>
                )}
              </section>
              <section className="panel collection-item">
                <h2>Delete local data</h2>
                <p>
                  Removes stored emails, tasks, drafts, approvals, activity, and
                  sessions for this workspace.
                </p>
                <form
                  className="actions"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const confirmation = new FormData(event.currentTarget).get(
                      "confirmation",
                    );
                    act("Deleting data…", async () => {
                      const result = await write<{ revoked: boolean }>(
                        "/me/data",
                        { confirm: confirmation },
                        "DELETE",
                      );
                      setAvailable(
                        await api<typeof available>("/auth/options"),
                      );
                      setProfile(null);
                      setDashboard(null);
                      setActiveId("");
                      if (!result.revoked)
                        setError(
                          "Local data was deleted. Provider revocation failed.",
                        );
                    });
                  }}
                >
                  <label>
                    Type DELETE
                    <input name="confirmation" required pattern="DELETE" />
                  </label>
                  <button className="button danger small" disabled={!!busy}>
                    Delete workspace data
                  </button>
                </form>
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
