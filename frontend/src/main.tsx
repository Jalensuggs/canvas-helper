import React, { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import DOMPurify from "dompurify";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Bell,
  BookOpen,
  Bot,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronRight,
  Circle,
  Clock3,
  CloudOff,
  FileText,
  FolderOpen,
  GraduationCap,
  History,
  LayoutDashboard,
  LoaderCircle,
  Menu,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  StickyNote,
  Trash2,
  Wifi,
  X,
} from "lucide-react";
import { ApiError, api, idOf, JsonObject, listFrom, streamChat, syncEventSource, text } from "./lib/api";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false },
  },
});

const nav = [
  { to: "/", label: "概览", icon: LayoutDashboard },
  { to: "/calendar", label: "日历", icon: CalendarDays },
  { to: "/announcements", label: "公告", icon: Bell },
  { to: "/courses", label: "课程", icon: BookOpen },
  { to: "/materials", label: "资料", icon: FolderOpen },
  { to: "/notes", label: "笔记", icon: StickyNote },
  { to: "/todos", label: "待办", icon: CheckCircle2 },
  { to: "/ai", label: "AI 工作区", icon: Bot },
];

function dateValue(value: unknown): Date | null {
  if (!value) return null;
  const result = new Date(String(value));
  return Number.isNaN(result.getTime()) ? null : result;
}

function formatDate(value: unknown, withTime = true) {
  const date = dateValue(value);
  if (!date) return "未设置";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function formatInterval(value: unknown) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "自适应";
  if (seconds < 3600) return `约 ${Math.round(seconds / 60)} 分钟`;
  return `约 ${Math.round(seconds / 3600)} 小时`;
}

function relativeDate(value: unknown) {
  const date = dateValue(value);
  if (!date) return "无截止日期";
  const diff = date.getTime() - Date.now();
  const days = Math.ceil(diff / 86_400_000);
  if (days < 0) return `已逾期 ${Math.abs(days)} 天`;
  if (days === 0) return "今天截止";
  if (days === 1) return "明天截止";
  return `${days} 天后`;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "发生未知错误";
}

function displayName(item: JsonObject, fallback = "未命名") {
  return text(item.name ?? item.title ?? item.course_name ?? item.subject, fallback);
}

function isDecorativeImage(item: JsonObject) {
  const citation = (item.citation ?? {}) as JsonObject;
  const mime = text(item.mime_type ?? citation.mime_type).toLowerCase();
  const title = displayName(item, "").toLowerCase();
  return mime.startsWith("image/") || /\.(?:png|jpe?g|gif|webp|svg|bmp|tiff?|heic)$/i.test(title);
}

function dueOf(item: JsonObject) {
  return item.due_at ?? item.due_date ?? item.end_at ?? item.date;
}

function isDone(item: JsonObject) {
  return Boolean(
    item.completed ?? item.done ?? item.is_complete ?? item.submitted ?? item.graded,
  );
}

function courseColor(item: JsonObject) {
  return text(item.color ?? item.course_color, "#46644b");
}

function SetupPage({ onReady }: { onReady: () => void }) {
  const [url, setUrl] = useState("https://canvas.uts.edu.au");
  const [token, setToken] = useState("");
  const mutation = useMutation({
    mutationFn: () => api.setupToken(url.trim().replace(/\/+$/, ""), token),
    onSuccess: () => {
      setToken("");
      queryClient.invalidateQueries();
      onReady();
    },
  });

  return (
    <main className="setup-shell">
      <section className="setup-visual">
        <div className="brand brand-light">
          <span className="brand-mark"><GraduationCap size={21} /></span>
          Canvas 助手
        </div>
        <div className="setup-copy">
          <span className="eyebrow pale">LOCAL · PRIVATE · FOCUSED</span>
          <h1>把课程、截止日期与思考，收拢在一个安静的地方。</h1>
          <p>数据优先保留在你的 Mac。本应用只连接你指定的 Canvas 实例。</p>
        </div>
        <div className="privacy-note"><CloudOff size={18} /> 本地优先，不在浏览器中保存 Token</div>
      </section>
      <section className="setup-panel">
        <div className="setup-form-wrap">
          <span className="step-label">首次设置 · 1 / 1</span>
          <h2>连接你的 Canvas</h2>
          <p className="muted">输入学校 Canvas 地址与访问令牌。令牌提交后不会再次显示。</p>
          <form
            className="form-stack"
            onSubmit={(event) => {
              event.preventDefault();
              mutation.mutate();
            }}
          >
            <label>
              <span>Canvas 地址</span>
              <input
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                type="url"
                required
                placeholder="https://canvas.example.edu"
              />
            </label>
            <label>
              <span>访问令牌</span>
              <input
                value={token}
                onChange={(event) => setToken(event.target.value)}
                type="password"
                autoComplete="new-password"
                required
                placeholder="粘贴 Token"
              />
            </label>
            {mutation.isError && <InlineError error={mutation.error} />}
            <button className="button primary wide" disabled={mutation.isPending}>
              {mutation.isPending ? <LoaderCircle className="spin" size={18} /> : <Wifi size={18} />}
              {mutation.isPending ? "正在验证…" : "验证并开始使用"}
            </button>
          </form>
          <p className="fine-print">凭证由本地后端安全存储。前端不会读取或回显已保存的 Token。</p>
        </div>
      </section>
    </main>
  );
}

function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [email, setEmail] = useState("");
  const requestLink = useMutation({ mutationFn: () => api.requestMagicLink(email) });
  const token = new URLSearchParams(window.location.search).get("magic_token");
  const verify = useMutation({
    mutationFn: (value: string) => api.verifyMagicLink(value),
    onSuccess: () => {
      window.history.replaceState({}, "", window.location.pathname);
      onAuthenticated();
    },
  });
  useEffect(() => {
    if (token && !verify.isPending && !verify.isSuccess && !verify.isError) {
      verify.mutate(token);
    }
  }, [token]); // The token is consumed only once by the server.

  return (
    <main className="setup-shell">
      <section className="setup-visual">
        <div className="brand brand-light"><span className="brand-mark"><GraduationCap size={21} /></span>Canvas 助手</div>
        <div className="setup-copy"><span className="eyebrow pale">SECURE · PERSONAL</span><h1>登录你的学习空间。</h1><p>我们会发送一条短时有效的一次性登录链接。</p></div>
      </section>
      <section className="setup-panel">
        <div className="setup-form-wrap">
          <h2>{token ? "正在验证登录链接" : "通过邮箱登录"}</h2>
          {token ? (
            verify.isError ? <InlineError error={verify.error} /> : <FullLoader label="正在安全登录" />
          ) : (
            <form className="form-stack" onSubmit={(event) => { event.preventDefault(); requestLink.mutate(); }}>
              <label><span>邮箱</span><input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
              {requestLink.isError && <InlineError error={requestLink.error} />}
              {requestLink.isSuccess && <p className="muted">登录链接已发送，请检查邮箱。</p>}
              <button className="button primary wide" disabled={requestLink.isPending}>{requestLink.isPending ? "正在发送…" : "发送登录链接"}</button>
            </form>
          )}
        </div>
      </section>
    </main>
  );
}

function App() {
  const [setupComplete, setSetupComplete] = useState(false);
  const auth = useQuery({ queryKey: ["auth"], queryFn: api.authMe, retry: false });
  const authenticated = Boolean(auth.data?.authenticated);
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false, enabled: authenticated });
  const configured = setupComplete || Boolean(me.data?.configured ?? me.data?.id ?? me.data?.name);

  if (auth.isPending || (authenticated && me.isPending && !setupComplete)) {
    return <FullLoader label="正在打开你的学习空间" />;
  }
  if (auth.data?.auth_required && !authenticated) {
    return <LoginPage onAuthenticated={() => queryClient.invalidateQueries()} />;
  }
  if (!configured && me.data?.configured === false) {
    return <SetupPage onReady={() => setSetupComplete(true)} />;
  }
  return <AppShell me={me.data ?? {}} offline={me.isError} />;
}

function AppShell({ me, offline }: { me: JsonObject; offline: boolean }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();
  const title =
    location.pathname === "/" ? "今日概览" :
    location.pathname.startsWith("/calendar") ? "截止日历" :
    location.pathname.startsWith("/announcements") ? "课程公告" :
    location.pathname.startsWith("/courses") ? "课程" :
    location.pathname.startsWith("/assignments") ? "作业详情" :
    location.pathname.startsWith("/materials") ? "资料与搜索" :
    location.pathname.startsWith("/notes") ? "Markdown 笔记" :
    location.pathname.startsWith("/todos") ? "本地待办" :
    location.pathname.startsWith("/ai") ? "AI 工作区" : "同步与设置";

  return (
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="brand">
          <span className="brand-mark"><GraduationCap size={20} /></span>
          <span>Canvas 助手</span>
          <button className="icon-button mobile-close" onClick={() => setMenuOpen(false)} aria-label="关闭菜单"><X /></button>
        </div>
        <nav className="main-nav">
          {nav.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} onClick={() => setMenuOpen(false)}>
              <item.icon size={19} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <NavLink to="/settings" onClick={() => setMenuOpen(false)}><Settings size={19} />设置与同步</NavLink>
          <div className="profile">
            <span className="avatar">{displayName(me, "学").slice(0, 1)}</span>
            <span><strong>{displayName(me, "本地用户")}</strong><small>{offline ? "离线缓存" : "Canvas 已连接"}</small></span>
            <span className={`status-dot ${offline ? "offline" : ""}`} />
          </div>
        </div>
      </aside>
      {menuOpen && <button className="scrim" onClick={() => setMenuOpen(false)} aria-label="关闭菜单" />}
      <main className="content-shell">
        <header className="topbar">
          <button className="icon-button menu-button" onClick={() => setMenuOpen(true)}><Menu /></button>
          <div><span className="top-kicker">学习空间</span><h1>{title}</h1></div>
          <div className={`connection-pill ${offline ? "offline" : ""}`}>
            <span />{offline ? "离线模式" : "已同步"}
          </div>
        </header>
        <div className="page-wrap">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/calendar" element={<CalendarPage />} />
            <Route path="/announcements" element={<AnnouncementsPage />} />
            <Route path="/courses" element={<CoursesPage />} />
            <Route path="/courses/:courseId" element={<CourseDetail />} />
            <Route path="/assignments/:assignmentId" element={<AssignmentDetail />} />
            <Route path="/materials" element={<MaterialsPage />} />
            <Route path="/materials/:materialId" element={<MaterialDetail />} />
            <Route path="/notes" element={<NotesPage />} />
            <Route path="/todos" element={<TodosPage />} />
            <Route path="/ai" element={<AIPage />} />
            <Route path="/settings" element={<SettingsPage me={me} />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </main>
      <nav className="mobile-nav">
        {nav.slice(0, 5).map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === "/"}>
            <item.icon size={20} /><span>{item.label === "AI 工作区" ? "AI" : item.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

function Dashboard() {
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });
  const deadlines = useQuery({ queryKey: ["deadlines"], queryFn: () => api.deadlines() });
  const coursesQuery = useQuery({ queryKey: ["courses"], queryFn: api.courses });
  const items = listFrom<JsonObject>(deadlines.data, ["deadlines", "planner_items"]);
  const courses = listFrom<JsonObject>(coursesQuery.data, ["courses"]);
  const buckets = useMemo(() => {
    const now = new Date();
    const todayEnd = new Date(now); todayEnd.setHours(23, 59, 59, 999);
    const weekEnd = new Date(todayEnd); weekEnd.setDate(weekEnd.getDate() + 7);
    return {
      overdue: items.filter((item) => dateValue(dueOf(item)) && dateValue(dueOf(item))! < now && !isDone(item)),
      today: items.filter((item) => {
        const date = dateValue(dueOf(item)); return date && date >= now && date <= todayEnd && !isDone(item);
      }),
      week: items.filter((item) => {
        const date = dateValue(dueOf(item)); return date && date > todayEnd && date <= weekEnd && !isDone(item);
      }),
      later: items.filter((item) => {
        const date = dateValue(dueOf(item)); return date && date > weekEnd && !isDone(item);
      }),
    };
  }, [items]);

  if (dashboard.isPending && deadlines.isPending) return <PanelLoader />;
  if (dashboard.isError && deadlines.isError) return <ErrorState error={dashboard.error} retry={() => { dashboard.refetch(); deadlines.refetch(); }} />;

  const stats = dashboard.data ?? {};
  return (
    <div className="stack-xl">
      <section className="hero-row">
        <div>
          <span className="eyebrow">WEDNESDAY · 学习节奏</span>
          <h2>晚上好，先看清今天。</h2>
          <p>你有 <strong>{buckets.today.length}</strong> 项今天截止，之后还有 <strong>{buckets.week.length + buckets.later.length}</strong> 项待完成。</p>
        </div>
        <Link to="/ai" className="button soft"><Sparkles size={17} />规划我的一周</Link>
      </section>
      <section className="stat-grid">
        <StatCard tone="danger" label="已经逾期" value={buckets.overdue.length} hint="需要优先处理" />
        <StatCard tone="warm" label="今天截止" value={buckets.today.length} hint="保持专注" />
        <StatCard tone="green" label="未来待办" value={buckets.week.length + buckets.later.length} hint="包含本学期之后日期" />
        <StatCard tone="ink" label="进行中课程" value={Number(stats.active_courses ?? stats.course_count ?? courses.length)} hint="本学期" />
      </section>
      <div className="dashboard-grid">
        <section className="card span-two">
          <CardHeader title="接下来" subtitle="按紧迫程度排列" action={<Link to="/todos">全部待办 <ArrowRight size={15} /></Link>} />
          <div className="deadline-list">
            {[...buckets.overdue, ...buckets.today, ...buckets.week, ...buckets.later].slice(0, 8).map((item) => <DeadlineRow key={idOf(item) || displayName(item)} item={item} />)}
            {!items.length && <EmptyState icon={CalendarDays} title="日程很清静" text="同步后，临近的作业和学习事项会出现在这里。" />}
          </div>
        </section>
        <section className="card">
          <CardHeader title="我的课程" subtitle={`${courses.length} 门进行中`} action={<Link to="/courses">查看全部</Link>} />
          <div className="course-mini-list">
            {courses.slice(0, 5).map((course) => (
              <Link to={`/courses/${idOf(course)}`} key={idOf(course)}>
                <span className="course-swatch" style={{ background: courseColor(course) }} />
                <span><strong>{displayName(course)}</strong><small>{text(course.course_code ?? course.code ?? course.term_name, "当前学期")}</small></span>
                <ChevronRight size={17} />
              </Link>
            ))}
            {!courses.length && <EmptyState compact icon={BookOpen} title="暂无课程" text="运行同步以载入课程。" />}
          </div>
        </section>
      </div>
    </div>
  );
}

function StatCard({ tone, label, value, hint }: { tone: string; label: string; value: number; hint: string }) {
  return <div className={`stat-card ${tone}`}><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>;
}

function DeadlineRow({ item }: { item: JsonObject }) {
  const assignmentId = text(item.assignment_id ?? item.id);
  const target = item.assignment_id || item.type === "assignment" ? `/assignments/${assignmentId}` : "/todos";
  return (
    <Link className="deadline-row" to={target}>
      <span className={`urgency ${relativeDate(dueOf(item)).includes("逾期") ? "danger" : ""}`}><Clock3 size={15} /></span>
      <span className="grow"><strong>{displayName(item)}</strong><small>{text(item.course_name ?? item.context_name, "本地待办")}</small></span>
      <span className="deadline-time"><strong>{formatDate(dueOf(item))}</strong><small>{relativeDate(dueOf(item))}</small></span>
      <ChevronRight size={17} />
    </Link>
  );
}

function localDateKey(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

function CalendarPage() {
  const query = useQuery({ queryKey: ["deadlines", "calendar"], queryFn: () => api.deadlines(false) });
  const [month, setMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const items = listFrom<JsonObject>(query.data, ["deadlines", "planner_items"]);
  const days = useMemo(() => {
    const first = new Date(month.getFullYear(), month.getMonth(), 1);
    const mondayOffset = (first.getDay() + 6) % 7;
    const start = new Date(first);
    start.setDate(first.getDate() - mondayOffset);
    return Array.from({ length: 42 }, (_, index) => {
      const date = new Date(start);
      date.setDate(start.getDate() + index);
      return date;
    });
  }, [month]);
  const byDate = useMemo(() => {
    const grouped = new Map<string, JsonObject[]>();
    items.forEach((item) => {
      const date = dateValue(dueOf(item));
      if (!date) return;
      const key = localDateKey(date);
      grouped.set(key, [...(grouped.get(key) ?? []), item]);
    });
    return grouped;
  }, [items]);
  const nowKey = localDateKey(new Date());
  const moveMonth = (offset: number) =>
    setMonth((value) => new Date(value.getFullYear(), value.getMonth() + offset, 1));

  if (query.isPending) return <PanelLoader />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;

  return (
    <div className="stack-lg">
      <section className="section-heading calendar-heading">
        <div>
          <span className="eyebrow">本学期未完成事项</span>
          <h2>{new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long" }).format(month)}</h2>
          <p>作业、测验和本地待办按照 Canvas 截止时间显示。</p>
        </div>
        <div className="calendar-actions">
          <button className="button soft small" onClick={() => moveMonth(-1)}>上个月</button>
          <button className="button soft small" onClick={() => {
            const now = new Date(); setMonth(new Date(now.getFullYear(), now.getMonth(), 1));
          }}>今天</button>
          <button className="button soft small" onClick={() => moveMonth(1)}>下个月</button>
        </div>
      </section>
      <section className="calendar-card">
        <div className="calendar-weekdays">
          {["周一", "周二", "周三", "周四", "周五", "周六", "周日"].map((day) => <span key={day}>{day}</span>)}
        </div>
        <div className="calendar-grid">
          {days.map((date) => {
            const key = localDateKey(date);
            const dayItems = byDate.get(key) ?? [];
            const outside = date.getMonth() !== month.getMonth();
            return (
              <div className={`calendar-day ${outside ? "outside" : ""} ${key === nowKey ? "today" : ""}`} key={key}>
                <span className="calendar-date">{date.getDate()}</span>
                <div className="calendar-events">
                  {dayItems.slice(0, 3).map((item) => (
                    <Link
                      to={item.assignment_id ? `/assignments/${text(item.assignment_id)}` : "/todos"}
                      className="calendar-event"
                      key={`${idOf(item)}-${text(dueOf(item))}`}
                      title={`${displayName(item)} · ${formatDate(dueOf(item))}`}
                    >
                      <strong>{displayName(item)}</strong>
                      <small>{formatDate(dueOf(item)).split(" ")[1] ?? ""}</small>
                    </Link>
                  ))}
                  {dayItems.length > 3 && <span className="calendar-more">另有 {dayItems.length - 3} 项</span>}
                </div>
              </div>
            );
          })}
        </div>
      </section>
      {!items.length && <EmptyState icon={CalendarDays} title="本学期没有未完成截止事项" text="同步后，新的 Canvas 作业会自动出现在这里。" />}
    </div>
  );
}

function AnnouncementsPage() {
  const [courseId, setCourseId] = useState("");
  const coursesQuery = useQuery({ queryKey: ["courses"], queryFn: api.courses });
  const query = useQuery({
    queryKey: ["announcements", courseId],
    queryFn: () => api.announcements(courseId || undefined),
  });
  const courses = listFrom<JsonObject>(coursesQuery.data, ["courses"]);
  const rows = listFrom<JsonObject>(query.data, ["announcements"]);

  return (
    <div className="stack-lg">
      <section className="section-heading">
        <div>
          <span className="eyebrow">CANVAS ANNOUNCEMENTS</span>
          <h2>课程公告</h2>
          <p>同步老师发布的通知、说明和课程更新。</p>
        </div>
        <select className="announcement-filter" value={courseId} onChange={(event) => setCourseId(event.target.value)}>
          <option value="">全部本学期课程</option>
          {courses.map((course) => <option key={idOf(course)} value={idOf(course)}>{displayName(course)}</option>)}
        </select>
      </section>
      {query.isPending ? <PanelLoader /> :
       query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> :
       rows.length ? (
         <div className="announcement-list">
           {rows.map((item) => {
             const attachments = listFrom<JsonObject>(item.attachments);
             return (
               <article className={`announcement-card ${Boolean(item.unread) ? "unread" : ""}`} key={idOf(item)}>
                 <header>
                   <div className="grow">
                     <div className="announcement-meta">
                       <span>{text(item.course_name, "Canvas 公告")}</span>
                       {Boolean(item.unread) && <strong>未读</strong>}
                     </div>
                     <h3>{displayName(item)}</h3>
                     <p>{text(item.author_name, "课程教师")} · {formatDate(item.posted_at)}</p>
                   </div>
                   {Boolean(item.html_url) && <a className="icon-button" href={text(item.html_url)} target="_blank" rel="noreferrer" aria-label="在 Canvas 打开"><ArrowRight size={18} /></a>}
                 </header>
                 <div className="announcement-body" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(text(item.message_html)) }} />
                 {attachments.length > 0 && (
                   <div className="announcement-attachments">
                     <strong>附件</strong>
                     {attachments.map((attachment) => <span key={idOf(attachment) || displayName(attachment)}><FileText size={14} />{displayName(attachment, text(attachment.filename, "附件"))}</span>)}
                   </div>
                 )}
               </article>
             );
           })}
         </div>
       ) : <EmptyState icon={Bell} title="本学期暂无公告" text="点击“设置与同步”运行完整同步后，新公告会显示在这里。" />}
    </div>
  );
}

function CoursesPage() {
  const query = useQuery({ queryKey: ["courses"], queryFn: api.courses });
  const [filter, setFilter] = useState("");
  if (query.isPending) return <PanelLoader />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  const courses = listFrom<JsonObject>(query.data, ["courses"]).filter((course) =>
    displayName(course).toLowerCase().includes(filter.toLowerCase()),
  );
  return (
    <div className="stack-lg">
      <section className="section-heading">
        <div><h2>本学期课程</h2><p>浏览课程、作业与本地资料。</p></div>
        <SearchBox value={filter} onChange={setFilter} placeholder="筛选课程" />
      </section>
      {courses.length ? (
        <div className="course-grid">
          {courses.map((course) => (
            <Link className="course-card" to={`/courses/${idOf(course)}`} key={idOf(course)}>
              <div className="course-card-top" style={{ "--course-color": courseColor(course) } as React.CSSProperties}>
                <span className="course-code">{text(course.course_code ?? course.code, "COURSE")}</span>
                <BookOpen size={23} />
              </div>
              <div className="course-card-body">
                <span className="term">{text(course.term_name ?? (course.term as JsonObject)?.name, "当前学期")}</span>
                <h3>{displayName(course)}</h3>
                <p>{text(course.section_name ?? course.subtitle, "查看课程内容与学习进度")}</p>
                <span className="text-link">进入课程 <ArrowRight size={15} /></span>
              </div>
            </Link>
          ))}
        </div>
      ) : <EmptyState icon={BookOpen} title="没有找到课程" text={filter ? "试试其他关键词。" : "完成首次同步后，课程会显示在这里。"} />}
    </div>
  );
}

function CourseDetail() {
  const { courseId = "" } = useParams();
  const course = useQuery({ queryKey: ["course", courseId], queryFn: () => api.course(courseId) });
  const deadlines = useQuery({ queryKey: ["deadlines"], queryFn: () => api.deadlines() });
  if (course.isPending) return <PanelLoader />;
  if (course.isError) return <ErrorState error={course.error} retry={() => course.refetch()} />;
  const assignments = listFrom<JsonObject>(course.data, ["assignments", "upcoming"]);
  const global = listFrom<JsonObject>(deadlines.data, ["deadlines", "planner_items"])
    .filter((item) => text(item.course_id ?? item.context_id) === courseId);
  const shown = assignments.length ? assignments : global;
  return (
    <div className="stack-lg">
      <BackLink to="/courses">返回课程</BackLink>
      <section className="course-hero" style={{ "--course-color": courseColor(course.data) } as React.CSSProperties}>
        <span className="eyebrow">{text(course.data.course_code ?? course.data.code, "COURSE")}</span>
        <h2>{displayName(course.data)}</h2>
        <p>{text(course.data.term_name ?? course.data.section_name, "当前课程")}</p>
        <div className="hero-meta">
          <span><FileText size={16} /> {Number(course.data.material_count ?? 0)} 份资料</span>
          <span><CheckCircle2 size={16} /> {Number(course.data.assignment_count ?? shown.length)} 项作业</span>
        </div>
      </section>
      <div className="dashboard-grid">
        <section className="card span-two">
          <CardHeader title="作业与截止日期" subtitle="最近项目优先" />
          <div className="deadline-list">
            {shown.map((item) => <DeadlineRow item={item} key={idOf(item) || displayName(item)} />)}
            {!shown.length && <EmptyState compact icon={CalendarDays} title="暂无近期作业" text="课程同步完成后会显示作业。" />}
          </div>
        </section>
        <section className="card action-card">
          <span className="mini-icon"><FolderOpen /></span>
          <h3>课程资料</h3>
          <p>搜索页面、讲义、附件与本地下载内容。</p>
          <Link className="button soft wide" to={`/materials?course=${courseId}`}>打开资料库</Link>
          <NotePanel target={{ course_id: Number(courseId) }} label="课程笔记" />
        </section>
      </div>
    </div>
  );
}

function AssignmentDetail() {
  const { assignmentId = "" } = useParams();
  const query = useQuery({ queryKey: ["assignment", assignmentId], queryFn: () => api.assignment(assignmentId) });
  if (query.isPending) return <PanelLoader />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  const item = query.data;
  const html = text(item.description ?? item.body);
  const rubric = listFrom<JsonObject>(item.rubric, ["criteria"]);
  const attachments = listFrom<JsonObject>(item.attachments, ["files"]);
  return (
    <div className="stack-lg">
      <BackLink to={item.course_id ? `/courses/${text(item.course_id)}` : "/courses"}>返回课程</BackLink>
      <section className="assignment-header">
        <div>
          <span className="eyebrow">{text(item.course_name ?? item.course_code, "作业")}</span>
          <h2>{displayName(item)}</h2>
          <p>{relativeDate(dueOf(item))} · {formatDate(dueOf(item))}</p>
        </div>
        <span className={`status-badge ${isDone(item) ? "success" : ""}`}>
          {isDone(item) ? <Check size={15} /> : <Clock3 size={15} />}
          {isDone(item) ? "已完成" : text(item.workflow_state, "待完成")}
        </span>
      </section>
      <div className="detail-grid">
        <article className="card article-card">
          <CardHeader title="作业说明" subtitle={item.points_possible ? `满分 ${text(item.points_possible)} 分` : undefined} />
          {html ? <div className="rich-content" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }} /> :
            <EmptyState compact icon={FileText} title="暂无作业说明" text="Canvas 未返回正文内容。" />}
          {attachments.length > 0 && <div className="attachment-list"><h4>附件</h4>{attachments.map((file) => <div key={idOf(file)}><FileText size={17} /><span>{displayName(file)}</span><small>{text(file.size, "")}</small></div>)}</div>}
        </article>
        <aside className="stack-md">
          <section className="card meta-card">
            <h3>提交信息</h3>
            <InfoLine label="截止时间" value={formatDate(dueOf(item))} />
            <InfoLine label="提交类型" value={Array.isArray(item.submission_types) ? item.submission_types.join("、") : text(item.submission_type, "未指定")} />
            <InfoLine label="尝试次数" value={text(item.allowed_attempts, "不限")} />
            <InfoLine label="当前成绩" value={text((item.submission as JsonObject)?.score ?? item.score, "尚未评分")} />
          </section>
          {rubric.length > 0 && <section className="card meta-card"><h3>评分标准</h3>{rubric.map((row) => <div className="rubric-row" key={idOf(row) || displayName(row)}><span>{displayName(row, "评分项")}</span><strong>{text(row.points, "—")} 分</strong></div>)}</section>}
          <section className="card meta-card"><NotePanel target={{ assignment_id: Number(assignmentId) }} label="作业笔记" /></section>
          <Link className="button primary wide" to={`/ai?assignment=${assignmentId}`}><Sparkles size={17} />用 AI 分析作业</Link>
        </aside>
      </div>
    </div>
  );
}

function MaterialsPage() {
  const location = useLocation();
  const initialCourse = new URLSearchParams(location.search).get("course") ?? "";
  const [courseId, setCourseId] = useState(initialCourse);
  const [input, setInput] = useState("");
  const [queryText, setQueryText] = useState("");
  const [kind, setKind] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setQueryText(input.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [input]);
  const coursesQuery = useQuery({ queryKey: ["courses"], queryFn: api.courses });
  const materials = useQuery({
    queryKey: ["materials", courseId, kind],
    queryFn: () => api.materials(courseId || undefined, kind || undefined),
    enabled: !queryText,
  });
  const search = useQuery({
    queryKey: ["search", queryText, courseId, kind, dateFrom, dateTo],
    queryFn: () => api.search(
      queryText,
      courseId || undefined,
      kind || undefined,
      dateFrom ? `${dateFrom}T00:00:00Z` : undefined,
      dateTo ? `${dateTo}T23:59:59Z` : undefined,
    ),
    enabled: Boolean(queryText),
  });
  const courses = listFrom<JsonObject>(coursesQuery.data, ["courses"]);
  const active = queryText ? search : materials;
  const rows = listFrom<JsonObject>(active.data, queryText ? ["results"] : ["materials", "files", "pages"])
    .filter((item) => !isDecorativeImage(item));
  const groups = useMemo(() => {
    const labels: Record<string, string> = {
      file: "课程文件",
      canvas_file: "课程文件",
      page: "课程页面",
      assignment: "作业资料",
      announcement: "课程公告",
      discussion: "课程讨论",
      subheader: "课程章节",
    };
    const output = new Map<string, JsonObject[]>();
    rows.forEach((row) => {
      const rawLabel = text(row.source_kind ?? row.kind, "资料");
      const label = labels[rawLabel] ?? rawLabel;
      output.set(label, [...(output.get(label) ?? []), row]);
    });
    return [...output.entries()];
  }, [rows]);
  return (
    <div className="stack-lg">
      <section className="search-hero">
        <span className="eyebrow">你的课程知识库</span>
        <h2>资料和搜索</h2>
        <p>从讲义、页面、作业与已下载文件中找到你需要的内容。</p>
        <form className="search-bar-large" onSubmit={(event) => { event.preventDefault(); setQueryText(input.trim()); }}>
          <Search size={20} />
          <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="搜索概念、文件或课程内容…" />
          <button className="button primary">搜索</button>
        </form>
      </section>
      <div className="filter-row">
        <select value={courseId} onChange={(event) => setCourseId(event.target.value)}>
          <option value="">全部课程</option>
          {courses.map((course) => <option key={idOf(course)} value={idOf(course)}>{displayName(course)}</option>)}
        </select>
        <select value={kind} onChange={(event) => setKind(event.target.value)}>
          <option value="">全部类型</option>
          <option value="assignment">作业</option>
          <option value="announcement">公告</option>
          <option value="page">页面</option>
          <option value="canvas_file">文件</option>
        </select>
        <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} aria-label="开始日期" />
        <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} aria-label="结束日期" />
        {queryText && <button className="chip" onClick={() => { setQueryText(""); setInput(""); }}><X size={14} />清除“{queryText}”</button>}
      </div>
      {active.isPending ? <PanelLoader /> :
       active.isError ? <ErrorState error={active.error} retry={() => active.refetch()} /> :
       rows.length ? <div className="search-groups">{groups.map(([label, items]) => <section key={label}><h3>{label} <small>{items.length}</small></h3><div className="material-list">{items.map((item) => <MaterialRow key={text(item.id ?? item.chunk_id) || displayName(item)} item={item} />)}</div></section>)}</div> :
       <EmptyState icon={FolderOpen} title={queryText ? "没有找到匹配内容" : courseId ? "这门课还没有已索引资料" : "还没有同步到课程资料"} text={queryText ? "换一个关键词，或扩大课程范围。" : "请在“设置与同步”运行一次完整同步。"} />}
    </div>
  );
}

function MaterialRow({ item }: { item: JsonObject }) {
  const citation = (item.citation ?? {}) as JsonObject;
  const documentId = text(item.document_id ?? citation.document_id ?? item.id);
  const target = text(item.source_kind) === "assignment"
    ? `/assignments/${text(item.source_id)}`
    : `/materials/${documentId}${item.id ? `?chunk=${text(item.id)}` : ""}`;
  return (
    <div className="material-row">
      <span className="file-icon"><FileText size={20} /></span>
      <Link className="grow" to={target}><h3>{displayName(item)}</h3>{item.snippet ? <p dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(text(item.snippet), { ALLOWED_TAGS: ["mark"] }) }} /> : <p>{text(item.excerpt ?? item.course_name, "课程资料")}</p>}<small>{text(item.source_kind ?? item.mime_type ?? item.kind, "文档")}{item.page ? ` · 第 ${text(item.page)} 页` : item.slide ? ` · 第 ${text(item.slide)} 张` : ""} · {text((item.course as JsonObject)?.name, "本地索引")}</small></Link>
      <QuickNoteButton target={{
        ...(documentId ? { document_id: Number(documentId) } : {}),
        ...(item.id && item.document_id ? { chunk_id: Number(item.id) } : {}),
        ...(item.page ? { page: Number(item.page) } : {}),
        ...(item.slide ? { slide: Number(item.slide) } : {}),
      }} />
      <Link to={target} aria-label="打开"><ChevronRight size={18} /></Link>
    </div>
  );
}

function MaterialDetail() {
  const { materialId = "" } = useParams();
  const query = useQuery({
    queryKey: ["material", materialId],
    queryFn: () => api.material(materialId),
  });
  const mimeGuess = text(query.data?.mime_type).toLowerCase();
  const nativeBinary = mimeGuess === "application/pdf" || (mimeGuess.startsWith("image/") && mimeGuess !== "image/svg+xml");
  const content = useQuery({
    queryKey: ["material-content", materialId],
    queryFn: () => api.materialContent(materialId),
    enabled: Boolean(query.data) && !nativeBinary,
  });
  if (query.isPending) return <PanelLoader />;
  if (query.isError) return <ErrorState error={query.error} retry={() => query.refetch()} />;
  const item = query.data;
  const mime = text(item.mime_type).toLowerCase();
  const previewUrl = `/api/materials/${encodeURIComponent(materialId)}/preview`;
  const downloadUrl = `/api/materials/${encodeURIComponent(materialId)}/download`;
  const image = mime.startsWith("image/") && mime !== "image/svg+xml";
  const pdf = mime === "application/pdf";
  const framed = Boolean(item.local_path) && (pdf || Boolean(item.preview_html));
  const rawContent = text(content.data);
  const htmlContent = /<(?:p|div|h[1-6]|ul|ol|li|table|blockquote|a|strong|em|br)\b/i.test(rawContent)
    ? rawContent
    : "";
  const safeRichHtml = htmlContent
    ? DOMPurify.sanitize(htmlContent, {
        FORBID_TAGS: ["script", "style", "form", "input", "button", "iframe", "object", "embed", "svg", "math"],
        FORBID_ATTR: ["style"],
      })
    : "";
  return (
    <div className="stack-lg">
      <BackLink to={item.course_id ? `/materials?course=${text(item.course_id)}` : "/materials"}>返回资料库</BackLink>
      <section className="material-detail-heading">
        <div><span className="eyebrow">{text(item.course_name, "课程资料")}</span><h2>{displayName(item)}</h2><p>{text(item.mime_type, text(item.kind, "文档"))}</p></div>
        {Boolean(item.local_path) && <a className="button soft" href={downloadUrl}>下载原文件</a>}
      </section>
      <div className="material-detail-grid">
        <section className="card material-preview">
          {image && item.local_path ? <img src={previewUrl} alt={displayName(item)} /> :
           framed ? <iframe title={`${displayName(item)} 预览`} src={previewUrl} sandbox="" /> :
           content.isFetching ? <PanelLoader /> :
           safeRichHtml ? <article className="rich-content canvas-page-content" dangerouslySetInnerHTML={{ __html: safeRichHtml }} /> :
           rawContent ? <pre>{rawContent}</pre> :
           <EmptyState compact icon={FileText} title="无法在应用内预览" text="此格式没有安全的内置预览；如有原文件可下载后使用本地应用打开。" />}
        </section>
        <aside className="card meta-card">
          <h3>文件信息</h3>
          <InfoLine label="类型" value={text(item.mime_type, text(item.kind, "未知"))} />
          <InfoLine label="大小" value={item.size ? `${(Number(item.size) / 1024).toFixed(1)} KB` : "未提供"} />
          <InfoLine label="版本" value={`v${text(item.version, "1")}`} />
          <InfoLine label="Canvas 更新" value={formatDate(item.source_updated_at)} />
          <InfoLine label="本地下载" value={formatDate(item.downloaded_at)} />
          <InfoLine label="内容提取" value={formatDate(item.extracted_at)} />
          <InfoLine label="SHA-256" value={text(item.sha256, "未下载").slice(0, 16) + (item.sha256 ? "…" : "")} />
          {!item.local_path && <div className="inline-error"><AlertCircle size={17} /><span>原文件尚未下载，当前仅显示已索引文本。</span></div>}
          <NotePanel target={{ document_id: Number(materialId) }} label="资料笔记" />
        </aside>
      </div>
    </div>
  );
}

function QuickNoteButton({ target }: { target: JsonObject }) {
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: () => api.createNote({ title: "新建 Markdown 笔记", markdown: "", ...target }),
    onSuccess: (note) => {
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      navigate(`/notes?note=${idOf(note)}`);
    },
  });
  return <button className="icon-button" title="添加本地 Markdown 笔记" aria-label="添加本地 Markdown 笔记" onClick={() => mutation.mutate()} disabled={mutation.isPending}><StickyNote size={17} /></button>;
}

function NotePanel({ target, label }: { target: JsonObject; label: string }) {
  const query = useQuery({
    queryKey: ["notes", target],
    queryFn: () => api.notes(target as Record<string, string | number | boolean | undefined>),
  });
  const rows = listFrom<JsonObject>(query.data);
  return <div className="note-panel">
    <div><strong>{label}</strong><small>本地 Markdown 笔记 · 不会同步到 Canvas</small></div>
    {rows.slice(0, 2).map((note) => <Link key={idOf(note)} to={`/notes?note=${idOf(note)}`}>{displayName(note)}</Link>)}
    <QuickNoteButton target={target} />
  </div>;
}

function NotesPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const requested = new URLSearchParams(location.search).get("note") ?? "";
  const [showDeleted, setShowDeleted] = useState(false);
  const notes = useQuery({
    queryKey: ["notes", "workspace", showDeleted],
    queryFn: () => api.notes({ include_deleted: showDeleted || undefined }),
  });
  const rows = listFrom<JsonObject>(notes.data);
  const [selectedId, setSelectedId] = useState(requested);
  const selected = rows.find((row) => idOf(row) === selectedId) ?? rows[0];
  const [title, setTitle] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [version, setVersion] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState("已保存");
  const [conflict, setConflict] = useState<JsonObject | null>(null);
  const revisions = useQuery({
    queryKey: ["note-revisions", selectedId],
    queryFn: () => api.noteRevisions(selectedId),
    enabled: Boolean(selectedId),
  });
  useEffect(() => {
    if (!selected) return;
    setSelectedId(idOf(selected));
    setTitle(text(selected.title));
    setMarkdown(text(selected.markdown));
    setVersion(Number(selected.version));
    setDirty(false);
    setConflict(null);
  }, [selected?.id, selected?.version]);
  const save = useMutation({
    mutationFn: () => api.updateNote(selectedId, { title, markdown, version }),
    onSuccess: (note) => {
      setVersion(Number(note.version));
      setDirty(false);
      setSaveState("已保存");
      setConflict(null);
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      queryClient.invalidateQueries({ queryKey: ["note-revisions", selectedId] });
    },
    onError: (error) => {
      setSaveState("保存冲突");
      if (error instanceof ApiError && error.status === 409) {
        const envelope = error.detail as JsonObject;
        const detail = (envelope?.detail ?? {}) as JsonObject;
        setConflict((detail.note ?? null) as JsonObject | null);
      }
    },
  });
  useEffect(() => {
    if (!dirty || !selectedId || save.isPending || conflict) return;
    setSaveState("等待自动保存…");
    const timer = window.setTimeout(() => {
      setSaveState("正在保存…");
      save.mutate();
    }, 700);
    return () => window.clearTimeout(timer);
  }, [title, markdown, dirty, selectedId, version, conflict]);
  const create = useMutation({
    mutationFn: () => api.createNote({ title: "未命名笔记", markdown: "" }),
    onSuccess: (note) => {
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      setSelectedId(idOf(note));
      navigate(`/notes?note=${idOf(note)}`, { replace: true });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deleteNote(selectedId, version),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notes"] }),
  });
  const restore = useMutation({
    mutationFn: () => api.restoreNote(selectedId, version),
    onSuccess: (note) => {
      setVersion(Number(note.version));
      queryClient.invalidateQueries({ queryKey: ["notes"] });
    },
  });
  const restoreRevision = useMutation({
    mutationFn: (revisionVersion: number) => api.restoreNoteRevision(selectedId, revisionVersion, version),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notes"] }),
  });
  const selectNote = (id: string) => {
    setSelectedId(id);
    navigate(`/notes?note=${id}`, { replace: true });
  };
  return <div className="stack-lg">
    <section className="section-heading"><div><span className="eyebrow">LOCAL MARKDOWN WORKSPACE</span><h2>笔记</h2><p>笔记用于长文思考；待办用于可完成事项；Canvas Planner Notes 是远端日历提醒。</p></div><button className="button primary" onClick={() => create.mutate()}><Plus size={17} />新建笔记</button></section>
    <div className="notes-layout">
      <aside className="card notes-list">
        <label className="deleted-toggle"><input type="checkbox" checked={showDeleted} onChange={(event) => setShowDeleted(event.target.checked)} />显示已删除</label>
        {rows.map((note) => <button className={idOf(note) === selectedId ? "active" : ""} key={idOf(note)} onClick={() => selectNote(idOf(note))}><strong>{displayName(note)}</strong><small>v{text(note.version)} · {formatDate(note.updated_at)}{note.deleted_at ? " · 已删除" : ""}</small></button>)}
        {!rows.length && <EmptyState compact icon={StickyNote} title="还没有笔记" text="创建本地 Markdown 笔记开始记录。" />}
      </aside>
      <section className="card note-editor">
        {!selected ? <EmptyState icon={StickyNote} title="选择一则笔记" text="或创建新的 Markdown 笔记。" /> : <>
          <header><input value={title} disabled={Boolean(selected.deleted_at)} onChange={(event) => { setTitle(event.target.value); setDirty(true); }} aria-label="笔记标题" /><span className={conflict ? "save-conflict" : ""}>{saveState}</span><a className="button soft small" href={`/api/notes/${selectedId}/export`}>导出 .md</a>{selected.deleted_at ? <button className="button soft small" onClick={() => restore.mutate()}>恢复</button> : <button className="icon-button danger-button" onClick={() => remove.mutate()} aria-label="移到已删除"><Trash2 size={17} /></button>}</header>
          {conflict && <div className="conflict-banner"><AlertCircle size={18} /><span><strong>检测到另一处修改</strong><small>你的草稿尚未覆盖服务器版本。可载入服务器版本后继续编辑。</small></span><button className="button soft small" onClick={() => { setTitle(text(conflict.title)); setMarkdown(text(conflict.markdown)); setVersion(Number(conflict.version)); setDirty(false); setConflict(null); setSaveState("已载入服务器版本"); }}>载入服务器版本</button></div>}
          <textarea className="markdown-editor" value={markdown} disabled={Boolean(selected.deleted_at)} onChange={(event) => { setMarkdown(event.target.value); setDirty(true); }} placeholder="# 开始写作…" />
          <details className="revision-history"><summary><History size={15} />版本历史</summary>{listFrom<JsonObject>(revisions.data).map((revision) => <div key={idOf(revision)}><span>v{text(revision.version)} · {text(revision.reason)} · {formatDate(revision.created_at)}</span>{!selected.deleted_at && Number(revision.version) !== version && <button onClick={() => restoreRevision.mutate(Number(revision.version))}>恢复此版本</button>}</div>)}</details>
        </>}
      </section>
    </div>
    <PlannerNoteComposer />
  </div>;
}

function PlannerNoteComposer() {
  const [title, setTitle] = useState("");
  const [details, setDetails] = useState("");
  const [todoDate, setTodoDate] = useState("");
  const [courseId, setCourseId] = useState("");
  const [noteId, setNoteId] = useState("");
  const [preview, setPreview] = useState<JsonObject | null>(null);
  const coursesQuery = useQuery({ queryKey: ["courses"], queryFn: api.courses });
  const courses = listFrom<JsonObject>(coursesQuery.data);
  const action = noteId ? "planner.note.update" : "planner.note.create";
  const previewMutation = useMutation({
    mutationFn: () => api.previewAction(action, {
      title, details, todo_date: new Date(todoDate).toISOString(),
      ...(courseId ? { course_id: Number(courseId) } : {}),
      ...(noteId ? { planner_note_id: Number(noteId) } : {}),
    }),
    onSuccess: setPreview,
  });
  const execute = useMutation({
    mutationFn: () => api.executeAction(action, text(preview?.confirm_token)),
    onSuccess: () => { setPreview(null); setTitle(""); setDetails(""); setTodoDate(""); setNoteId(""); },
  });
  return <section className="card planner-note">
    <CardHeader title="Canvas Planner Note" subtitle="远端日历提醒 · 与本地 Markdown 笔记和待办不同" />
    <div className="planner-note-form">
      <input value={title} onChange={(event) => { setTitle(event.target.value); setPreview(null); }} placeholder="提醒标题" />
      <textarea value={details} onChange={(event) => { setDetails(event.target.value); setPreview(null); }} placeholder="详情" />
      <input type="datetime-local" value={todoDate} onChange={(event) => { setTodoDate(event.target.value); setPreview(null); }} />
      <select value={courseId} onChange={(event) => { setCourseId(event.target.value); setPreview(null); }}><option value="">无课程</option>{courses.map((course) => <option value={idOf(course)} key={idOf(course)}>{displayName(course)}</option>)}</select>
      <input value={noteId} onChange={(event) => { setNoteId(event.target.value); setPreview(null); }} inputMode="numeric" placeholder="已有 Planner Note ID（更新时填写）" />
      {!preview ? <button className="button soft" disabled={!title.trim() || !todoDate || previewMutation.isPending} onClick={() => previewMutation.mutate()}>预览远端操作</button> : <div className="action-confirm"><span><strong>{text(preview.summary)}</strong><small>将发送到 {text(preview.remote_endpoint)}。确认后只执行一次，并读取 Canvas 响应核验。</small></span><button className="button primary" disabled={execute.isPending} onClick={() => execute.mutate()}>确认执行</button><button className="button soft" onClick={() => setPreview(null)}>取消</button></div>}
      {previewMutation.isError && <InlineError error={previewMutation.error} />}{execute.isError && <InlineError error={execute.error} />}{execute.isSuccess && <p className="success-line">Canvas 已回读并确认 Planner Note。</p>}
    </div>
  </section>;
}

function TodosPage() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["todos"], queryFn: api.todos });
  const [title, setTitle] = useState("");
  const [dueAt, setDueAt] = useState("");
  const create = useMutation({
    mutationFn: () => api.createTodo({ title: title.trim(), due_at: dueAt || null }),
    onSuccess: () => { setTitle(""); setDueAt(""); queryClient.invalidateQueries({ queryKey: ["todos"] }); },
  });
  const update = useMutation({
    mutationFn: ({ id, completed }: { id: string; completed: boolean }) => api.updateTodo(id, { completed }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["todos"] }),
  });
  const remove = useMutation({
    mutationFn: api.deleteTodo,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["todos"] }),
  });
  const rows = listFrom<JsonObject>(query.data, ["todos"]);
  return (
    <div className="stack-lg">
      <section className="section-heading"><div><h2>本地待办</h2><p>不会离开这台设备的私人学习清单。</p></div></section>
      <form className="quick-add card" onSubmit={(event) => { event.preventDefault(); if (title.trim()) create.mutate(); }}>
        <span className="add-icon"><Plus size={19} /></span>
        <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="添加一个学习事项…" aria-label="待办标题" />
        <input value={dueAt} onChange={(event) => setDueAt(event.target.value)} type="datetime-local" aria-label="截止时间" />
        <button className="button primary" disabled={!title.trim() || create.isPending}>添加</button>
      </form>
      {create.isError && <InlineError error={create.error} />}
      {query.isPending ? <PanelLoader /> : query.isError ? <ErrorState error={query.error} retry={() => query.refetch()} /> :
        <section className="card todo-card">
          <CardHeader title="我的清单" subtitle={`${rows.filter((item) => !isDone(item)).length} 项未完成`} />
          <div className="todo-list">
            {rows.map((item) => (
              <div className={`todo-row ${isDone(item) ? "done" : ""}`} key={idOf(item)}>
                <button className="todo-check" onClick={() => update.mutate({ id: idOf(item), completed: !isDone(item) })}>
                  {isDone(item) ? <CheckCircle2 size={21} /> : <Circle size={21} />}
                </button>
                <span className="grow"><strong>{displayName(item)}</strong><small>{dueOf(item) ? `${formatDate(dueOf(item))} · ${relativeDate(dueOf(item))}` : "无截止日期"}</small></span>
                <button className="icon-button danger-button" onClick={() => remove.mutate(idOf(item))} aria-label="删除"><Trash2 size={17} /></button>
              </div>
            ))}
            {!rows.length && <EmptyState icon={CheckCircle2} title="清单是空的" text="在上方添加第一个学习事项。" compact />}
          </div>
        </section>}
    </div>
  );
}

function AIPage() {
  const location = useLocation();
  const assignmentId = new URLSearchParams(location.search).get("assignment");
  const [message, setMessage] = useState("");
  const [courseId, setCourseId] = useState("");
  const coursesQuery = useQuery({ queryKey: ["courses"], queryFn: api.courses });
  const courses = listFrom<JsonObject>(coursesQuery.data, ["courses"]);
  const [messages, setMessages] = useState<Array<{
    role: "user" | "assistant";
    content: string;
    citations?: JsonObject[];
    usage?: JsonObject;
  }>>([]);
  const mutation = useMutation({
    mutationFn: (content: string) => streamChat(
      content,
      assignmentId ? { assignment_id: assignmentId } : courseId ? { course_id: courseId } : {},
      (event) => setMessages((old) => {
        const next = [...old];
        const index = next.length - 1;
        const current = next[index];
        if (!current || current.role !== "assistant") return old;
        if (event.type === "text_delta") next[index] = { ...current, content: current.content + event.delta };
        if (event.type === "citation") next[index] = { ...current, citations: [...(current.citations ?? []), event.citation] };
        if (event.type === "usage") next[index] = { ...current, usage: event.usage };
        if (event.type === "error") next[index] = { ...current, content: `请求失败：${event.error}` };
        return next;
      }),
    ),
    onError: (error) => setMessages((old) => {
      const next = [...old];
      if (next.at(-1)?.role === "assistant") next[next.length - 1] = { role: "assistant", content: `请求失败：${errorMessage(error)}` };
      return next;
    }),
  });
  const submit = (content: string) => {
    if (!content.trim() || mutation.isPending) return;
    setMessages((old) => [...old, { role: "user", content: content.trim() }, { role: "assistant", content: "" }]);
    setMessage("");
    mutation.mutate(content.trim());
  };
  const suggestions = assignmentId ? ["拆解这份作业的要求", "根据评分标准生成检查清单", "帮我制定完成计划"] : ["总结本周课程更新", "规划未来 7 天的学习任务", "找出最紧急的三件事"];
  return (
    <div className="ai-layout">
      <section className="ai-main card">
        <div className="ai-title"><span className="ai-orb"><Sparkles /></span><div><h2>学习搭档</h2><p>{assignmentId ? "已将当前作业加入上下文" : "可以使用你选择的课程资料与日程"}</p></div></div>
        <div className="chat-thread">
          {!messages.length && <div className="ai-welcome"><span className="eyebrow">从哪里开始？</span><h3>把复杂的学习任务，变成清晰的下一步。</h3><div className="suggestion-grid">{suggestions.map((suggestion) => <button key={suggestion} onClick={() => submit(suggestion)}>{suggestion}<ArrowRight size={16} /></button>)}</div></div>}
          {messages.map((entry, index) => <div className={`message ${entry.role}`} key={index}><span>{entry.role === "assistant" ? <Sparkles size={16} /> : "你"}</span><div>{entry.content || (mutation.isPending && index === messages.length - 1 ? <span className="typing"><i /><i /><i /></span> : "")}{entry.citations && entry.citations.length > 0 && <div className="citation-list">{entry.citations.map((citation) => <span className="citation-item" key={text(citation.chunk_id)}><Link to={`/materials/${text(citation.document_id)}?chunk=${text(citation.chunk_id)}`}>[{text(citation.index)}] {text(citation.title, "来源")}{citation.page ? ` · p.${text(citation.page)}` : citation.slide ? ` · slide ${text(citation.slide)}` : ""}</Link><QuickNoteButton target={{ document_id: Number(citation.document_id), chunk_id: Number(citation.chunk_id), ...(citation.page ? { page: Number(citation.page) } : {}), ...(citation.slide ? { slide: Number(citation.slide) } : {}) }} /></span>)}</div>}{entry.usage && <small className="usage-line">{text(entry.usage.input_tokens, "0")} 输入 · {text(entry.usage.output_tokens, "0")} 输出 tokens</small>}</div></div>)}
        </div>
        <form className="composer" onSubmit={(event) => { event.preventDefault(); submit(message); }}>
          <textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder="询问课程、分析作业、制定计划…" rows={2} />
          <div><small>AI 输出可能有误，请核对重要信息。</small><button className="button primary" disabled={!message.trim() || mutation.isPending}>发送 <ArrowRight size={16} /></button></div>
        </form>
      </section>
      <aside className="context-panel card">
        <h3>本次上下文</h3>
        <p>只会发送完成当前请求所需的内容。</p>
        {!assignmentId && <select className="scope-select" value={courseId} onChange={(event) => setCourseId(event.target.value)}><option value="">全部课程</option>{courses.map((course) => <option key={idOf(course)} value={idOf(course)}>{displayName(course)}</option>)}</select>}
        <div className="context-item"><span className="mini-icon"><BookOpen /></span><span><strong>{assignmentId ? "当前作业" : courseId ? "选定课程" : "全部课程"}</strong><small>{assignmentId ? `作业 #${assignmentId}` : courseId ? displayName(courses.find((course) => idOf(course) === courseId) ?? {}) : "FTS 检索前 8 个来源"}</small></span><Check size={16} /></div>
        <div className="privacy-strip"><CloudOff size={17} /><span><strong>敏感信息会被清除</strong><small>Token 与临时链接不会发给模型</small></span></div>
      </aside>
    </div>
  );
}

function SettingsPage({ me }: { me: JsonObject }) {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["sync-status"], queryFn: api.syncStatus });
  useEffect(() => {
    const events = syncEventSource();
    events.addEventListener("sync", (event) => {
      const next = JSON.parse((event as MessageEvent).data) as JsonObject;
      const previous = queryClient.getQueryData<JsonObject>(["sync-status"]);
      queryClient.setQueryData(["sync-status"], next);
      if (text(next.last_sync_at) && text(next.last_sync_at) !== text(previous?.last_sync_at)) {
        queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "sync-status" });
      }
    });
    return () => events.close();
  }, [queryClient]);
  const doctor = useQuery({ queryKey: ["doctor"], queryFn: api.doctor, enabled: false });
  const sync = useMutation({
    mutationFn: () => api.sync("all"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sync-status"] }),
  });
  const jobs = listFrom<JsonObject>(status.data, ["jobs", "runs"]);
  const running = listFrom<JsonObject>(status.data?.running);
  const freshness = (status.data?.freshness ?? {}) as JsonObject;
  const schedules = Object.entries(freshness).map(([job, value]) => [job, value as JsonObject] as const);
  const scheduleLabels: Record<string, string> = {
    announcements: "公告",
    planner: "待办与日历",
    deadlines: "作业截止时间",
    materials: "课程资料",
    courses: "课程列表",
  };
  const isSyncing = sync.isPending || running.length > 0;
  return (
    <div className="settings-grid">
      <div className="stack-lg">
        <section className="card settings-section">
          <CardHeader title="Canvas 连接" subtitle="凭证保存在本机安全存储中" />
          <div className="connection-card">
            <span className="avatar large">{displayName(me, "学").slice(0, 1)}</span>
            <span className="grow"><strong>{displayName(me, "Canvas 用户")}</strong><small>{text(me.canvas_url ?? me.domain, "Canvas 已连接")}</small></span>
            <span className="status-badge success"><Check size={14} />已连接</span>
          </div>
          <div className="token-row"><span><strong>访问令牌</strong><small>出于安全原因不会显示已保存内容</small></span><code>••••••••••••••••</code></div>
        </section>
        <section className="card settings-section">
          <CardHeader title="同步" subtitle={status.data?.last_sync_at ? `数据更新于 ${formatDate(status.data.last_sync_at)}` : "保持课程数据最新"} action={<button className="button primary small" onClick={() => sync.mutate()} disabled={isSyncing}><RefreshCw className={isSyncing ? "spin" : ""} size={16} />{isSyncing ? "同步中" : "立即同步"}</button>} />
          {sync.isError && <InlineError error={sync.error} />}
          <div className={`auto-sync-card ${schedules.length ? "enabled" : ""}`}>
            <span className="auto-sync-icon"><RefreshCw size={18} /></span>
            <span className="grow">
              <strong>{schedules.length ? "自动同步已开启" : "正在初始化自动同步"}</strong>
              <small>{schedules.length ? "应用运行时会自动检查 Canvas，有变化时会自适应加快更新。" : "保持此页面打开片刻，或重新连接 Canvas。"}</small>
            </span>
            {schedules.length > 0 && <span className="status-badge success"><Check size={14} />运行中</span>}
          </div>
          {schedules.length > 0 && <div className="schedule-grid">
            {schedules.map(([job, schedule]) => (
              <div key={job}>
                <span><strong>{scheduleLabels[job] ?? job}</strong><small>{formatInterval(schedule.interval_seconds)}自动更新</small></span>
                <span><small>下次检查</small><strong>{formatDate(schedule.next_run_at)}</strong></span>
              </div>
            ))}
          </div>}
          <div className="job-list">
            {jobs.map((job) => <div key={idOf(job) || displayName(job)}><span className={`job-dot ${text(job.status)}`} /><span className="grow"><strong>{displayName(job, text(job.job, "同步任务"))}</strong><small>{job.status === "running" ? `第 ${text(job.attempts, "1")} 次尝试` : formatDate(job.finished_at ?? job.updated_at)}</small></span><span>{text(job.status, "等待")}</span></div>)}
            {!jobs.length && <p className="muted">尚无同步记录。点击“立即同步”开始。</p>}
          </div>
        </section>
      </div>
      <aside className="stack-lg">
        <section className="card settings-section">
          <h3>连接诊断</h3><p className="muted">检查 API、数据库与 Canvas 凭证状态。</p>
          <button className="button soft wide" onClick={() => doctor.refetch()} disabled={doctor.isFetching}>{doctor.isFetching ? <LoaderCircle className="spin" size={17} /> : <Wifi size={17} />}运行诊断</button>
          {doctor.data && <div className="doctor-result"><CheckCircle2 size={18} /><span><strong>诊断完成</strong><small>{text(doctor.data.message, "本地服务响应正常")}</small></span></div>}
          {doctor.isError && <InlineError error={doctor.error} />}
        </section>
        <section className="card settings-section">
          <h3>本地优先</h3>
          <div className="setting-toggle"><span><strong>离线缓存</strong><small>断网时继续浏览已有数据</small></span><span className="toggle on"><i /></span></div>
          <div className="setting-toggle"><span><strong>匿名化无关姓名</strong><small>发送 AI 请求前处理</small></span><span className="toggle on"><i /></span></div>
        </section>
      </aside>
    </div>
  );
}

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (value: string) => void; placeholder: string }) {
  return <label className="search-box"><Search size={17} /><input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} /></label>;
}

function CardHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return <header className="card-header"><div><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</div>{action && <div className="card-action">{action}</div>}</header>;
}

function InfoLine({ label, value }: { label: string; value: string }) {
  return <div className="info-line"><span>{label}</span><strong>{value}</strong></div>;
}

function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return <Link to={to} className="back-link"><ArrowLeft size={16} />{children}</Link>;
}

function InlineError({ error }: { error: unknown }) {
  return <div className="inline-error"><AlertCircle size={17} /><span>{errorMessage(error)}</span></div>;
}

function FullLoader({ label }: { label: string }) {
  return <div className="full-loader"><span className="brand-mark"><GraduationCap /></span><LoaderCircle className="spin" /><p>{label}</p></div>;
}

function PanelLoader() {
  return <div className="panel-loader"><LoaderCircle className="spin" /><span>正在载入本地数据…</span></div>;
}

function ErrorState({ error, retry }: { error: unknown; retry: () => void }) {
  const offline = error instanceof ApiError && error.status === 0;
  return <div className="state-card"><span className="state-icon error"><AlertCircle /></span><h3>{offline ? "本地服务未连接" : "这部分暂时无法载入"}</h3><p>{errorMessage(error)}</p><button className="button soft" onClick={retry}><RefreshCw size={16} />重试</button></div>;
}

function EmptyState({ icon: Icon, title, text: detail, compact = false }: { icon: React.ElementType; title: string; text: string; compact?: boolean }) {
  return <div className={`empty-state ${compact ? "compact" : ""}`}><span><Icon /></span><h3>{title}</h3><p>{detail}</p></div>;
}

function DesktopLifecycle() {
  useEffect(() => {
    const resync = () => {
      void api.sync("all").catch(() => undefined);
      void queryClient.invalidateQueries();
    };
    window.addEventListener("canvas-helper-resume", resync);
    return () => window.removeEventListener("canvas-helper-resume", resync);
  }, []);
  return null;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <DesktopLifecycle />
      <BrowserRouter><App /></BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
