import { useEffect, useMemo, useState } from "react";

import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Bell,
  Check,
  CheckCircle2,
  CheckSquare,
  ChevronDown,
  Download,
  Edit3,
  Eye,
  FileSearch,
  FileText,
  GraduationCap,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  UploadCloud,
  Users,
  X,
  XCircle,
} from "lucide-react";

import "./styles/app.css";
import {
  approveQuestion,
  createQuizAttempt,
  createSopDocument,
  generateQuiz,
  getApprovedQuestionsForRole,
  getDashboardSummary,
  getJobRoles,
  getLearnerProfiles,
  getMe,
  getQuestions,
  getSopDocuments,
  getStoredToken,
  login,
  logout,
  processSopDocument,
  rejectQuestion,
  setAuthToken,
  submitQuizAttempt,
} from "./services/api";

const navigation = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "sop-library", label: "SOP Library", icon: FileText },
  { id: "generate-quiz", label: "Generate Quiz", icon: Sparkles, requires: "is_admin" },
  { id: "question-review", label: "Question Review", icon: CheckSquare, requires: "is_reviewer" },
  { id: "learner-quiz", label: "Learner Quiz", icon: GraduationCap },
  { id: "analytics", label: "Analytics", icon: BarChart3 },
  { id: "users", label: "Users & Roles", icon: Users },
];

const pageTitles = {
  dashboard: "Dashboard",
  "sop-library": "SOP Library",
  "generate-quiz": "Generate Quiz",
  "question-review": "Question Review",
  "learner-quiz": "Learner Quiz",
  analytics: "Analytics",
  users: "Users & Roles",
};

function Sidebar({ activePage, onNavigate, currentUser }) {
  const roles = currentUser?.roles || {};
  const visibleNavigation = navigation.filter((item) => !item.requires || roles[item.requires]);

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <div className="sidebar__brand-mark">
          <ShieldCheck size={16} />
        </div>
        <span>GxP Tutor</span>
      </div>

      <nav className="sidebar__nav" aria-label="Primary navigation">
        {visibleNavigation.map(({ id, label, icon: Icon }) => (
          <button
            className={`sidebar__link${activePage === id ? " active" : ""}`}
            key={id}
            onClick={() => onNavigate(id)}
            type="button"
          >
            <Icon size={16} />
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar__footer">v1.0 · Compliance Suite</div>
    </aside>
  );
}

function initialsFor(user) {
  const first = user?.first_name?.[0] || user?.username?.[0] || "?";
  const last = user?.last_name?.[0] || "";
  return `${first}${last}`.toUpperCase();
}

function Topbar({ title, currentUser, onLogout }) {
  const displayName = currentUser
    ? [currentUser.first_name, currentUser.last_name].filter(Boolean).join(" ") || currentUser.username
    : "Guest";
  const displayRole = currentUser?.is_staff
    ? "Admin / QA"
    : currentUser?.learner_profile?.job_role?.name || "No role assigned";

  return (
    <header className="topbar">
      <div className="topbar__title">{title}</div>
      <label className="topbar__search">
        <Search size={15} className="icon" />
        <input placeholder="Search SOPs, questions, learners..." />
      </label>
      <div className="topbar__actions">
        <button className="icon-btn" aria-label="Notifications" type="button">
          <Bell size={16} />
          <span className="dot" />
        </button>
        <button className="avatar" type="button">
          <span className="avatar__img">{initialsFor(currentUser)}</span>
          <span>
            <span className="avatar__name">{displayName}</span>
            <span className="avatar__role">{displayRole}</span>
          </span>
          <ChevronDown size={14} color="#64748B" />
        </button>
        <button className="icon-btn" aria-label="Log out" onClick={onLogout} type="button">
          <LogOut size={16} />
        </button>
      </div>
    </header>
  );
}

function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await onLogin(username, password);
    } catch (err) {
      setError(err.message || "Login failed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-card__brand">
          <ShieldCheck size={22} />
          <span>GxP Tutor</span>
        </div>
        <p className="muted-small">Sign in with your training account to continue.</p>
        <form onSubmit={handleSubmit}>
          <label className="field">
            <span>Username</span>
            <input onChange={(event) => setUsername(event.target.value)} type="text" value={username} />
          </label>
          <label className="field">
            <span>Password</span>
            <input onChange={(event) => setPassword(event.target.value)} type="password" value={password} />
          </label>
          {error && <p className="text-error">{error}</p>}
          <button className="btn btn--primary" disabled={isSubmitting} type="submit">
            {isSubmitting ? "Signing in..." : "Sign In"}
          </button>
        </form>
        <p className="muted-small">
          Demo accounts (seed data): rohit / priya / arun / sneha / karan / anjali — password demo12345
        </p>
      </div>
    </div>
  );
}

const fallbackStats = [
  { label: "SOPs Processed", value: "142", delta: "+8 this week", icon: FileText },
  { label: "Questions Approved", value: "1,284", delta: "+126 this week", icon: CheckSquare },
  { label: "Learner Attempts", value: "3,672", delta: "+312 this week", icon: GraduationCap },
  { label: "Average Score", value: "82.4%", delta: "+1.6%", icon: BarChart3 },
];

const workflow = [
  { n: 1, label: "SOP Upload", meta: "PDF / DOCX", icon: Upload },
  { n: 2, label: "Text Extraction", meta: "OCR + parsing", icon: FileSearch },
  { n: 3, label: "AI Quiz Draft", meta: "Role-specific", icon: Sparkles },
  { n: 4, label: "QA Review", meta: "SME approval", icon: ShieldCheck },
  { n: 5, label: "Published Quiz", meta: "Assigned to roles", icon: Send },
  { n: 6, label: "Learner Attempt", meta: "Scored attempts", icon: CheckSquare },
  { n: 7, label: "Feedback", meta: "Gaps and retraining", icon: Activity },
];

const activity = [
  { text: "SOP-217 Aseptic Gowning Procedure was processed", meta: "2 minutes ago · System" },
  { text: "Anjali Rao approved 12 questions for SOP-204", meta: "28 minutes ago · QA Review" },
  { text: "Equipment Cleaning v3 quiz published to Production Operators", meta: "1 hour ago · Training" },
  { text: "Rohit Mehta completed GDP Basics with 88%", meta: "3 hours ago · Learner" },
  { text: "SOP-198 upload failed due to unsupported file format", meta: "Yesterday · System" },
];

const compliance = [
  { label: "Production Operators", value: 92 },
  { label: "QA Analysts", value: 87 },
  { label: "Warehouse Staff", value: 74 },
  { label: "Lab Technicians", value: 81 },
  { label: "Maintenance", value: 66 },
];

function Dashboard({ summary, apiStatus }) {
  const stats = summary
    ? [
        { label: "SOPs Processed", value: summary.processed_sops, delta: `${summary.sops} total SOPs`, icon: FileText },
        { label: "Questions Approved", value: summary.approved_questions, delta: `${summary.questions} generated`, icon: CheckSquare },
        { label: "Learner Attempts", value: summary.attempts, delta: `${summary.completion_rate}% completion`, icon: GraduationCap },
        { label: "Average Score", value: `${summary.average_score}%`, delta: "Live backend data", icon: BarChart3 },
      ]
    : fallbackStats;
  const dashboardActivity = summary?.recent_activity?.length ? summary.recent_activity : activity;
  const dashboardCompliance = summary?.attempts_by_role?.length
    ? summary.attempts_by_role.map((item) => ({ label: item.role, value: Math.round(item.average_score) }))
    : compliance;

  return (
    <div className="page">
      <PageHeader
        title="Training Overview"
        subtitle="Monitor SOP processing, question approval, and learner performance."
        action={<DataStatus status={apiStatus} />}
      />

      <div className="stats">
        {stats.map((item) => (
          <div className="stat" key={item.label}>
            <div className="stat__icon">
              <item.icon size={20} />
            </div>
            <div>
              <div className="stat__label">{item.label}</div>
              <div className="stat__value">{item.value}</div>
              <div className="stat__delta">{item.delta}</div>
            </div>
          </div>
        ))}
      </div>

      <section className="card section-gap">
        <div className="card__title">
          Training Workflow <small>End-to-end SOP-to-learner pipeline</small>
        </div>
        <div className="workflow">
          {workflow.map((step) => (
            <div className="workflow__step" key={step.n}>
              <div className="workflow__topline">
                <span className="num">{step.n}</span>
                <step.icon size={14} color="#1D4ED8" />
              </div>
              <div className="label">{step.label}</div>
              <div className="meta">{step.meta}</div>
            </div>
          ))}
        </div>
      </section>

      <div className="grid-2">
        <section className="card">
          <div className="card__title">
            Recent Activity <small>Last 24 hours</small>
          </div>
          <div className="activity-list">
            {dashboardActivity.map((item) => (
              <div className="activity__item" key={item.text}>
                <div className="activity__icon">
                  <Activity size={14} />
                </div>
                <div>
                  <div className="activity__text">{item.text}</div>
                  <div className="activity__meta">{item.meta}</div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="card">
          <div className="card__title">
            Compliance Status <small>By role</small>
          </div>
          <div className="compliance">
            {dashboardCompliance.map((item) => (
              <div key={item.label}>
                <div className="compliance__row">
                  <span>{item.label}</span>
                  <span>{item.value}%</span>
                </div>
                <div className="bar">
                  <span style={{ width: `${item.value}%` }} />
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

const fallbackSops = [
  { code: "SOP-217", title: "Aseptic Gowning Procedure", department: "Production", version: "v2.1", status: "Processed", date: "2026-06-12" },
  { code: "SOP-214", title: "Equipment Cleaning and Sanitization", department: "Production", version: "v3.0", status: "Processed", date: "2026-06-10" },
  { code: "SOP-211", title: "Deviation Reporting", department: "Quality Assurance", version: "v1.4", status: "Processed", date: "2026-06-08" },
  { code: "SOP-208", title: "Warehouse Material Receipt", department: "Warehouse", version: "v2.0", status: "Uploaded", date: "2026-06-07" },
  { code: "SOP-204", title: "HPLC Calibration", department: "Quality Control", version: "v1.2", status: "Processed", date: "2026-06-05" },
  { code: "SOP-198", title: "Preventive Maintenance Log", department: "Engineering", version: "v1.0", status: "Failed", date: "2026-06-03" },
];

const sopBadge = {
  Processed: "badge badge--processed",
  Uploaded: "badge badge--uploaded",
  Failed: "badge badge--failed",
};

const emptySopForm = { title: "", sop_code: "", version: "", department: "", file: null };

function SopLibrary({ documents, apiStatus, canUpload, onUploaded }) {
  const [form, setForm] = useState(emptySopForm);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  const documentsToShow = documents?.length
    ? documents.map((item) => ({
        code: item.sop_code,
        title: item.title,
        department: item.department,
        version: item.version,
        status: item.status_label,
        date: new Date(item.created_at).toISOString().slice(0, 10),
      }))
    : fallbackSops;

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function handleUpload() {
    if (!form.file || !form.title || !form.sop_code || !form.version || !form.department) {
      setError("Fill in every field and choose a file before uploading.");
      setMessage(null);
      return;
    }
    setIsUploading(true);
    setError(null);
    setMessage(null);
    try {
      const data = new FormData();
      data.append("title", form.title);
      data.append("sop_code", form.sop_code);
      data.append("version", form.version);
      data.append("department", form.department);
      data.append("file", form.file);
      const created = await createSopDocument(data);
      const processed = await processSopDocument(created.id);
      setMessage(`${form.sop_code} uploaded and processed into ${processed.chunks} chunk(s).`);
      setForm(emptySopForm);
      onUploaded?.();
    } catch (err) {
      setError(err.message || "Upload failed.");
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <div className="page">
      <PageHeader
        title="SOP Library"
        subtitle="Upload and manage Standard Operating Procedures for quiz generation."
        action={<DataStatus status={apiStatus} />}
      />

      {canUpload ? (
        <section className="card section-gap">
          <div className="card__title">Upload a New SOP</div>
          <div className="form-grid">
            <label className="field">
              <span>Title</span>
              <input
                onChange={(event) => updateField("title", event.target.value)}
                placeholder="Aseptic Gowning Procedure"
                type="text"
                value={form.title}
              />
            </label>
            <label className="field">
              <span>SOP Code</span>
              <input
                onChange={(event) => updateField("sop_code", event.target.value)}
                placeholder="SOP-217"
                type="text"
                value={form.sop_code}
              />
            </label>
            <label className="field">
              <span>Version</span>
              <input
                onChange={(event) => updateField("version", event.target.value)}
                placeholder="v2.1"
                type="text"
                value={form.version}
              />
            </label>
            <label className="field">
              <span>Department</span>
              <input
                onChange={(event) => updateField("department", event.target.value)}
                placeholder="Production"
                type="text"
                value={form.department}
              />
            </label>
          </div>

          <label className="uploader" htmlFor="sop-file-input">
            <UploadCloud size={28} color="#1D4ED8" />
            <strong>{form.file ? form.file.name : "Drop SOP file here or click to browse"}</strong>
            <span>PDF, DOCX, TXT, or MD. The file is extracted and chunked immediately after upload.</span>
            <input
              accept=".pdf,.docx,.txt,.md"
              id="sop-file-input"
              onChange={(event) => updateField("file", event.target.files?.[0] || null)}
              style={{ display: "none" }}
              type="file"
            />
          </label>

          {error && <p className="text-error">{error}</p>}
          {message && <p className="text-success">{message}</p>}

          <div className="button-row">
            <button className="btn btn--primary" disabled={isUploading} onClick={handleUpload} type="button">
              <UploadCloud size={15} /> {isUploading ? "Uploading..." : "Upload & Process"}
            </button>
          </div>
        </section>
      ) : (
        <section className="card section-gap">
          <p className="muted-small">Only Training/QA Admin accounts can upload new SOPs.</p>
        </section>
      )}

      <section className="card">
        <div className="card__title">
          All SOPs <small>{documentsToShow.length} documents</small>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>SOP Code</th>
                <th>Title</th>
                <th>Department</th>
                <th>Version</th>
                <th>Status</th>
                <th>Uploaded Date</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {documentsToShow.map((item) => (
                <tr key={item.code}>
                  <td className="strong-cell">{item.code}</td>
                  <td>{item.title}</td>
                  <td>{item.department}</td>
                  <td>{item.version}</td>
                  <td><span className={sopBadge[item.status]}>{item.status}</span></td>
                  <td>{item.date}</td>
                  <td>
                    <div className="actions">
                      <button className="link-btn" aria-label="View SOP" type="button"><Eye size={14} /></button>
                      <button className="link-btn" aria-label="Download SOP" type="button"><Download size={14} /></button>
                      <button className="link-btn danger" aria-label="Delete SOP" type="button"><Trash2 size={14} /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

const difficultyBadge = {
  Easy: "badge badge--easy",
  Medium: "badge badge--medium",
  Hard: "badge badge--hard",
};

function GenerateQuiz({ documents, jobRoles, onGenerated, onNavigate }) {
  const sopOptions = documents?.filter((doc) => doc.status === "processed") ?? [];
  const roleOptions = jobRoles ?? [];

  const [sopId, setSopId] = useState("");
  const [roleId, setRoleId] = useState("");
  const [difficulty, setDifficulty] = useState("medium");
  const [count, setCount] = useState(5);
  const [questions, setQuestions] = useState([]);
  const [source, setSource] = useState(null);
  const [skippedDuplicates, setSkippedDuplicates] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!sopId && sopOptions.length) {
      setSopId(String(sopOptions[0].id));
    }
  }, [sopOptions, sopId]);

  useEffect(() => {
    if (!roleId && roleOptions.length) {
      setRoleId(String(roleOptions[0].id));
    }
  }, [roleOptions, roleId]);

  async function handleGenerate() {
    if (!sopId || !roleId) {
      setError("Select a processed SOP and a job role first.");
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const result = await generateQuiz({ sop: Number(sopId), jobRole: Number(roleId), count: Number(count), difficulty });
      setQuestions(result.questions);
      setSource(result.source);
      setSkippedDuplicates(result.skipped_duplicates || 0);
      await onGenerated?.();
    } catch (err) {
      setError(err.message || "Generation failed.");
    } finally {
      setIsLoading(false);
    }
  }

  function handleReset() {
    setQuestions([]);
    setSource(null);
    setSkippedDuplicates(0);
    setError(null);
  }

  return (
    <div className="page">
      <PageHeader
        title="Generate Quiz"
        subtitle="Create role-specific quiz drafts directly from processed SOPs."
      />

      <section className="card section-gap">
        <div className="card__title">Generation Parameters</div>
        <div className="form-grid">
          <label className="field">
            <span>Select SOP</span>
            <select onChange={(event) => setSopId(event.target.value)} value={sopId}>
              {sopOptions.length === 0 && <option value="">No processed SOPs yet</option>}
              {sopOptions.map((doc) => (
                <option key={doc.id} value={doc.id}>{doc.sop_code} - {doc.title}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Job Role</span>
            <select onChange={(event) => setRoleId(event.target.value)} value={roleId}>
              {roleOptions.length === 0 && <option value="">No job roles yet</option>}
              {roleOptions.map((role) => (
                <option key={role.id} value={role.id}>{role.name}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Difficulty</span>
            <select onChange={(event) => setDifficulty(event.target.value)} value={difficulty}>
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
          </label>
          <label className="field">
            <span>Number of Questions</span>
            <input
              max={20}
              min={1}
              onChange={(event) => setCount(event.target.value)}
              type="number"
              value={count}
            />
          </label>
        </div>
        {error && <p className="text-error">{error}</p>}
        <div className="button-row">
          <button className="btn btn--primary" disabled={isLoading || !sopId || !roleId} onClick={handleGenerate} type="button">
            <Sparkles size={15} /> {isLoading ? "Generating..." : "Generate Quiz"}
          </button>
          <button className="btn" disabled={isLoading} onClick={handleReset} type="button">
            <RefreshCw size={14} /> Reset
          </button>
        </div>
      </section>

      <section className="card">
        <div className="card__title">
          Generated Preview{" "}
          <small>
            {questions.length
              ? `${questions.length} question${questions.length === 1 ? "" : "s"} · Draft, not yet reviewed`
              : "Nothing generated yet"}
            {source === "nvidia_nim" && " · generated live by NVIDIA NIM"}
            {source === "mock" && " · offline mock generator (no NVIDIA NIM key, or the live call failed)"}
            {source === "mixed" && " · partially generated offline (some AI calls failed)"}
            {skippedDuplicates > 0 &&
              ` · skipped ${skippedDuplicates} duplicate${skippedDuplicates === 1 ? "" : "s"} already in the review queue`}
          </small>
        </div>
        {questions.length === 0 && source && !isLoading && (
          <p className="muted-small">
            Every question this run generated already exists for this SOP and role. Try a different SOP, role, or
            increase the count.
          </p>
        )}
        {questions.length === 0 && !source && !isLoading && (
          <p className="muted-small">Choose a processed SOP and a job role, then click Generate Quiz.</p>
        )}
        {questions.map((item, index) => (
          <div className="preview-question" key={item.id}>
            <div className="preview-question__title">
              <strong>{index + 1}. {item.question_text}</strong>
              <span className={difficultyBadge[titleCase(item.difficulty)]}>{titleCase(item.difficulty)}</span>
            </div>
            <OptionList
              correct={item.options.findIndex((option) => option.is_correct)}
              options={item.options.map((option) => option.option_text)}
            />
            <div className="q-card__explanation"><strong>Explanation:</strong> {item.explanation}</div>
          </div>
        ))}
        {questions.length > 0 && (
          <div className="button-row">
            <button className="btn btn--primary" onClick={() => onNavigate?.("question-review")} type="button">
              Send to Review
            </button>
            <button className="btn" disabled={isLoading} onClick={handleGenerate} type="button">
              Regenerate
            </button>
          </div>
        )}
      </section>
    </div>
  );
}

const reviewQuestions = [
  {
    question: "During aseptic gowning, at what point should sterile gloves be donned?",
    role: "Production Operator",
    difficulty: "Medium",
    source: "SOP-217 · Section 4.3 · Gowning Sequence",
    options: [
      "Before entering the airlock",
      "After donning the sterile coverall and goggles",
      "After sanitizing hands but before the coverall",
      "Only after entering the Grade B area",
    ],
    correct: 1,
    explanation: "Per SOP-217 Section 4.3, sterile gloves are donned last to prevent contamination of the outer glove surface during gowning.",
  },
  {
    question: "What is the acceptable HPLC system suitability tailing factor as per SOP-204?",
    role: "QC Chemist",
    difficulty: "Hard",
    source: "SOP-204 · Section 6.2 · System Suitability",
    options: ["Less than or equal to 1.0", "Less than or equal to 1.5", "Less than or equal to 2.0", "Less than or equal to 3.0"],
    correct: 2,
    explanation: "SOP-204 specifies a tailing factor of less than or equal to 2.0 for HPLC system suitability acceptance.",
  },
];

function QuestionReview({ questions, apiStatus, canReview, onApproveQuestion, onRejectQuestion }) {
  const questionsToShow = questions?.length
    ? questions.map((item) => ({
        id: item.id,
        question: item.question_text,
        role: item.job_role_name,
        difficulty: titleCase(item.difficulty),
        source: `${item.sop_code} · ${item.source_section || item.sop_title || "SOP source"}`,
        options: item.options.map((option) => option.option_text),
        correct: Math.max(0, item.options.findIndex((option) => option.is_correct)),
        explanation: item.explanation,
        status: titleCase(item.status),
        isLive: true,
      }))
    : reviewQuestions.map((item, index) => ({ ...item, id: `fallback-${index}`, status: "Pending Review", isLive: false }));

  const pendingCount = questions?.filter((item) => item.status === "draft").length ?? 12;
  const approvedCount = questions?.filter((item) => item.status === "approved").length ?? 48;

  return (
    <div className="page">
      <PageHeader
        title="Question Review"
        subtitle="Review AI-generated questions before publishing them to learners."
        action={
          <div className="badge-row">
            <DataStatus status={apiStatus} />
            <span className="badge badge--pending">{pendingCount} pending</span>
            <span className="badge badge--approved">{approvedCount} approved</span>
          </div>
        }
      />

      {questionsToShow.map((item, index) => (
        <article className="q-card" key={item.id}>
          <div className="q-card__head">
            <div className="q-card__tags">
              <span className="badge badge--uploaded">{item.role}</span>
              <span className={difficultyBadge[item.difficulty]}>{item.difficulty}</span>
              <span className={questionStatusBadge(item.status)}>{item.status}</span>
            </div>
            <div className="muted-small">Question #{index + 1}</div>
          </div>

          <div className="q-card__question">{item.question}</div>
          <div className="q-card__source"><strong>Source:</strong> {item.source}</div>
          <OptionList options={item.options} correct={item.correct} />
          <div className="q-card__explanation"><strong>Explanation:</strong> {item.explanation}</div>

          <div className="q-card__actions">
            <button
              className="btn btn--success"
              disabled={!canReview || !item.isLive || item.status === "Approved"}
              onClick={() => onApproveQuestion(item.id)}
              type="button"
            >
              <Check size={14} /> Approve
            </button>
            <button className="btn" type="button"><Edit3 size={14} /> Edit</button>
            <button
              className="btn btn--danger"
              disabled={!canReview || !item.isLive || item.status === "Rejected"}
              onClick={() => onRejectQuestion(item.id)}
              type="button"
            >
              <X size={14} /> Reject
            </button>
          </div>
          {!canReview && (
            <p className="muted-small">Only Admin or SME Reviewer accounts can approve or reject questions.</p>
          )}
        </article>
      ))}
    </div>
  );
}

function LearnerQuiz({ currentUser, onSubmitted }) {
  const jobRole = currentUser?.learner_profile?.job_role;
  const [approvedQuestions, setApprovedQuestions] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [sopId, setSopId] = useState("");
  const [attempt, setAttempt] = useState(null);
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState({});
  const [result, setResult] = useState(null);
  const [isStarting, setIsStarting] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionError, setActionError] = useState(null);

  useEffect(() => {
    if (!jobRole) {
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setLoadError(null);
    getApprovedQuestionsForRole(jobRole.id)
      .then((data) => {
        if (!cancelled) {
          setApprovedQuestions(Array.isArray(data) ? data : data.results || []);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setLoadError(err.message || "Could not load quiz questions.");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [jobRole]);

  const sopGroups = useMemo(() => {
    const groups = new Map();
    approvedQuestions.forEach((question) => {
      if (!groups.has(question.sop)) {
        groups.set(question.sop, { id: question.sop, code: question.sop_code, title: question.sop_title, count: 0 });
      }
      groups.get(question.sop).count += 1;
    });
    return Array.from(groups.values());
  }, [approvedQuestions]);

  useEffect(() => {
    if (!sopId && sopGroups.length) {
      setSopId(String(sopGroups[0].id));
    }
  }, [sopGroups, sopId]);

  async function handleStart() {
    if (!sopId) {
      return;
    }
    setActionError(null);
    setIsStarting(true);
    try {
      const created = await createQuizAttempt({ sop: Number(sopId), jobRole: jobRole.id });
      const snapshot = approvedQuestions.filter((question) => String(question.sop) === String(sopId));
      setAttempt({ id: created.id, questions: snapshot });
      setIndex(0);
      setAnswers({});
      setResult(null);
    } catch (err) {
      setActionError(err.message || "Could not start the quiz.");
    } finally {
      setIsStarting(false);
    }
  }

  async function handleSubmit() {
    setActionError(null);
    setIsSubmitting(true);
    try {
      const payload = attempt.questions.map((question) => ({
        question: question.id,
        selected_option: answers[question.id] ?? null,
      }));
      const response = await submitQuizAttempt(attempt.id, payload);
      setResult(response);
      await onSubmitted?.();
    } catch (err) {
      setActionError(err.message || "Submit failed.");
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleRetake() {
    setAttempt(null);
    setResult(null);
    setActionError(null);
  }

  if (!currentUser) {
    return (
      <div className="page">
        <PageHeader title="Learner Quiz" subtitle="Log in with a learner account to take an assigned quiz." />
      </div>
    );
  }

  if (!jobRole) {
    return (
      <div className="page">
        <PageHeader
          title="Learner Quiz"
          subtitle="Your account has no job role assigned yet. Ask an admin to link a job role to your profile in Users & Roles."
        />
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="page">
        <PageHeader title="Learner Quiz" subtitle={`Loading approved questions for ${jobRole.name}...`} />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="page">
        <PageHeader title="Learner Quiz" subtitle={`Reference role: ${jobRole.name}`} />
        <section className="card"><p className="text-error">{loadError}</p></section>
      </div>
    );
  }

  if (result) {
    const total = result.answers.length;
    const correctCount = result.answers.filter((answer) => answer.is_correct).length;
    return (
      <div className="page">
        <PageHeader title="Quiz Result" subtitle={`${jobRole.name} · ${sopGroups.find((g) => String(g.id) === String(sopId))?.code || ""}`} />

        <section className="card">
          <div className="result-score">
            <div className="result-score__circle">{Math.round(Number(result.score))}%</div>
            <div>
              <div className="result-score__title">{correctCount} of {total} correct</div>
              <div className="result-score__subtitle">
                {Number(result.score) >= 80 ? "Passed - competency demonstrated." : "Below pass mark - retraining recommended."}
              </div>
            </div>
          </div>

          <div className="card__title">Review</div>
          {attempt.questions.map((question, questionIndex) => {
            const answer = result.answers.find((item) => item.question === question.id);
            const isCorrect = Boolean(answer?.is_correct);
            const selectedOption = question.options.find((option) => option.id === answer?.selected_option);
            const correctOption = question.options.find((option) => option.is_correct);
            return (
              <div className="result-review" key={question.id}>
                <div className="result-review__question">
                  {isCorrect ? <CheckCircle2 size={16} color="#16A34A" /> : <XCircle size={16} color="#DC2626" />}
                  <strong>{questionIndex + 1}. {question.question_text}</strong>
                </div>
                {!isCorrect && (
                  <div className="result-review__details">
                    <div>Your answer: <span className="text-error">{selectedOption?.option_text || "Not answered"}</span></div>
                    <div>Correct answer: <span className="text-success">{correctOption?.option_text}</span></div>
                    <p>{question.explanation}</p>
                  </div>
                )}
              </div>
            );
          })}

          <button className="btn" onClick={handleRetake} type="button">
            Take Another Quiz
          </button>
        </section>
      </div>
    );
  }

  if (!attempt) {
    return (
      <div className="page">
        <PageHeader title="Learner Quiz" subtitle={`Approved quizzes available for ${jobRole.name}`} />
        <section className="card">
          {sopGroups.length === 0 ? (
            <p className="muted-small">No approved questions are available for your role yet. Check back after QA review.</p>
          ) : (
            <>
              <div className="form-grid">
                <label className="field">
                  <span>Select SOP Quiz</span>
                  <select onChange={(event) => setSopId(event.target.value)} value={sopId}>
                    {sopGroups.map((group) => (
                      <option key={group.id} value={group.id}>
                        {group.code} - {group.title} ({group.count} question{group.count === 1 ? "" : "s"})
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {actionError && <p className="text-error">{actionError}</p>}
              <div className="button-row">
                <button className="btn btn--primary" disabled={isStarting} onClick={handleStart} type="button">
                  <Send size={14} /> {isStarting ? "Starting..." : "Start Quiz"}
                </button>
              </div>
            </>
          )}
        </section>
      </div>
    );
  }

  const total = attempt.questions.length;
  const current = attempt.questions[index];
  const currentOptions = current.options;

  return (
    <div className="page">
      <PageHeader title={`${jobRole.name} Quiz`} subtitle={`Reference: ${current.sop_code}`} />

      <section className="card">
        <div className="quiz-progress">
          <div className="quiz-progress__label">Question {index + 1} of {total}</div>
          <div className="quiz-progress__bar">
            <span style={{ width: `${((index + 1) / total) * 100}%` }} />
          </div>
          <div className="muted-small">{Math.round(((index + 1) / total) * 100)}%</div>
        </div>

        <div className="quiz-question">{current.question_text}</div>

        <div className="q-options">
          {currentOptions.map((option) => (
            <button
              className={`q-option${answers[current.id] === option.id ? " selected" : ""}`}
              key={option.id}
              onClick={() => setAnswers((currentAnswers) => ({ ...currentAnswers, [current.id]: option.id }))}
              type="button"
            >
              <span className="marker">{String.fromCharCode(65 + currentOptions.indexOf(option))}</span>
              <span>{option.option_text}</span>
            </button>
          ))}
        </div>

        {actionError && <p className="text-error">{actionError}</p>}

        <div className="quiz-actions">
          <button className="btn" disabled={index === 0} onClick={() => setIndex((value) => Math.max(0, value - 1))} type="button">
            Previous
          </button>
          {index < total - 1 ? (
            <button className="btn btn--primary" onClick={() => setIndex((value) => Math.min(value + 1, total - 1))} type="button">
              Next <ArrowRight size={14} />
            </button>
          ) : (
            <button className="btn btn--primary" disabled={isSubmitting} onClick={handleSubmit} type="button">
              <Send size={14} /> {isSubmitting ? "Submitting..." : "Submit Quiz"}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

const statusBadge = {
  Passed: "badge badge--processed",
  Failed: "badge badge--failed",
  "In Progress": "badge badge--pending",
};

function Analytics({ summary, apiStatus }) {
  const roleRows = summary?.attempts_by_role ?? [];
  const learnerRows = summary?.learner_progress ?? [];
  const weakTopics = summary?.weak_topics ?? [];
  const atRiskCount = learnerRows.filter((item) => item.status === "Failed").length;

  return (
    <div className="page">
      <PageHeader
        title="Analytics"
        subtitle="Training performance and learner progress."
        action={<DataStatus status={apiStatus} />}
      />

      <div className="stats stats--three">
        <StatCard
          delta={summary ? `${summary.attempts} total attempts` : "No data yet"}
          icon={BarChart3}
          label="Average Score"
          value={summary ? `${summary.average_score}%` : "-"}
        />
        <StatCard
          delta={summary ? `${summary.questions} questions generated` : "No data yet"}
          icon={CheckCircle2}
          label="Completion Rate"
          value={summary ? `${summary.completion_rate}%` : "-"}
        />
        <StatCard
          delta="Completed attempts scored below pass mark"
          icon={AlertTriangle}
          label="At-Risk Learners"
          value={atRiskCount}
          warning={atRiskCount > 0}
        />
      </div>

      <div className="grid-2 section-gap">
        <section className="card">
          <div className="card__title">Role-wise Performance</div>
          {roleRows.length === 0 ? (
            <p className="muted-small">No quiz attempts recorded yet.</p>
          ) : (
            <SimpleTable
              columns={["Role", "Attempts", "Avg Score"]}
              rows={roleRows.map((item) => [item.role, item.total, `${item.average_score}%`])}
            />
          )}
        </section>

        <section className="card">
          <div className="card__title">
            Weak Topics <small>Lowest correct rate across all learner attempts</small>
          </div>
          {weakTopics.length === 0 ? (
            <p className="muted-small">Not enough scored attempts yet to identify weak topics.</p>
          ) : (
            <ul className="weak-list">
              {weakTopics.map((item) => (
                <li key={item.question_id}>
                  <strong>{item.sop_code} · {item.topic}</strong>
                  <span>{item.correct_rate}% correct ({item.attempts} attempt{item.attempts === 1 ? "" : "s"})</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="card">
        <div className="card__title">Learner Progress</div>
        {learnerRows.length === 0 ? (
          <p className="muted-small">No learner attempts recorded yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Learner</th>
                  <th>Role</th>
                  <th>SOP</th>
                  <th>Score</th>
                  <th>Status</th>
                  <th>Completed Date</th>
                </tr>
              </thead>
              <tbody>
                {learnerRows.map((item, itemIndex) => (
                  <tr key={`${item.learner}-${itemIndex}`}>
                    <td className="strong-cell">{item.learner}</td>
                    <td>{item.role}</td>
                    <td>{item.sop}</td>
                    <td>{item.status === "In Progress" ? "-" : `${item.score}%`}</td>
                    <td><span className={statusBadge[item.status]}>{item.status}</span></td>
                    <td>{item.completed_date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function UsersRoles({ jobRoles, learnerProfiles }) {
  const roleCounts = useMemo(() => {
    const counts = new Map();
    learnerProfiles.forEach((profile) => {
      if (profile.job_role) {
        counts.set(profile.job_role, (counts.get(profile.job_role) || 0) + 1);
      }
    });
    return counts;
  }, [learnerProfiles]);

  return (
    <div className="page">
      <PageHeader title="Users & Roles" subtitle="Job roles and learners currently in the system." />

      <div className="grid-12">
        <section className="card">
          <div className="card__title">Job Roles</div>
          {jobRoles.length === 0 ? (
            <p className="muted-small">No job roles defined yet.</p>
          ) : (
            <ul className="role-list">
              {jobRoles.map((role) => (
                <li key={role.id}>
                  <strong>{role.name}</strong>
                  <span>
                    {roleCounts.get(role.id) || 0} learner{roleCounts.get(role.id) === 1 ? "" : "s"} · {role.department}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card">
          <div className="card__title">Learners</div>
          {learnerProfiles.length === 0 ? (
            <p className="muted-small">No learner profiles yet.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Employee Code</th>
                  </tr>
                </thead>
                <tbody>
                  {learnerProfiles.map((profile) => (
                    <tr key={profile.id}>
                      <td className="strong-cell">
                        {[profile.first_name, profile.last_name].filter(Boolean).join(" ") || profile.username}
                      </td>
                      <td>{profile.email}</td>
                      <td>{profile.job_role_name || "Unassigned"}</td>
                      <td>{profile.employee_code || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function PageHeader({ title, subtitle, action }) {
  return (
    <div className="page__header">
      <div>
        <h1 className="page__title">{title}</h1>
        <p className="page__subtitle">{subtitle}</p>
      </div>
      {action}
    </div>
  );
}

function DataStatus({ status }) {
  if (status === "connected") {
    return <span className="badge badge--processed">Backend connected</span>;
  }
  if (status === "loading") {
    return <span className="badge badge--pending">Loading API</span>;
  }
  return <span className="badge badge--draft">Using demo fallback</span>;
}

function titleCase(value = "") {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function questionStatusBadge(status) {
  if (status === "Approved") {
    return "badge badge--approved";
  }
  if (status === "Rejected") {
    return "badge badge--rejected";
  }
  return "badge badge--draft";
}

function OptionList({ options, correct }) {
  return (
    <div className="q-options">
      {options.map((option, index) => (
        <div className={`q-option${index === correct ? " correct" : ""}`} key={option}>
          <span className="marker">{String.fromCharCode(65 + index)}</span>
          <span>{option}</span>
          {index === correct && <span className="correct-label">Correct answer</span>}
        </div>
      ))}
    </div>
  );
}

function StatCard({ icon: Icon, label, value, delta, warning = false }) {
  return (
    <div className="stat">
      <div className={`stat__icon${warning ? " stat__icon--warning" : ""}`}>
        <Icon size={20} />
      </div>
      <div>
        <div className="stat__label">{label}</div>
        <div className="stat__value">{value}</div>
        <div className={`stat__delta${warning ? " warn" : ""}`}>{delta}</div>
      </div>
    </div>
  );
}

function SimpleTable({ columns, rows }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.join("-")}>
              {row.map((cell, index) => (
                <td className={index === 0 ? "strong-cell" : ""} key={`${cell}-${index}`}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function App() {
  const [activePage, setActivePage] = useState("dashboard");
  const [summary, setSummary] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [jobRoles, setJobRoles] = useState([]);
  const [learnerProfiles, setLearnerProfiles] = useState([]);
  const [apiStatus, setApiStatus] = useState("loading");
  const [currentUser, setCurrentUser] = useState(null);
  const [isCheckingSession, setIsCheckingSession] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function restoreSession() {
      if (getStoredToken()) {
        try {
          const user = await getMe();
          if (!cancelled) {
            setCurrentUser(user);
          }
        } catch {
          setAuthToken(null);
        }
      }
      if (!cancelled) {
        setIsCheckingSession(false);
      }
    }
    restoreSession();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadApiData() {
      try {
        const [summaryData, sopData, questionData, jobRoleData, learnerProfileData] = await Promise.all([
          getDashboardSummary(),
          getSopDocuments(),
          getQuestions(),
          getJobRoles(),
          getLearnerProfiles(),
        ]);
        if (!cancelled) {
          setSummary(summaryData);
          setDocuments(Array.isArray(sopData) ? sopData : sopData.results || []);
          setQuestions(Array.isArray(questionData) ? questionData : questionData.results || []);
          setJobRoles(Array.isArray(jobRoleData) ? jobRoleData : jobRoleData.results || []);
          setLearnerProfiles(Array.isArray(learnerProfileData) ? learnerProfileData : learnerProfileData.results || []);
          setApiStatus("connected");
        }
      } catch {
        if (!cancelled) {
          setApiStatus("fallback");
        }
      }
    }
    loadApiData();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleLogin(username, password) {
    const user = await login(username, password);
    setCurrentUser(user);
  }

  async function handleLogout() {
    try {
      await logout();
    } catch {
      // Clear local session regardless of whether the server call succeeded.
    }
    setCurrentUser(null);
    setActivePage("dashboard");
  }

  async function handleQuestionStatus(id, action) {
    try {
      const updated = action === "approve" ? await approveQuestion(id) : await rejectQuestion(id);
      setQuestions((current) => current.map((item) => (item.id === id ? updated : item)));
      const summaryData = await getDashboardSummary();
      setSummary(summaryData);
      setApiStatus("connected");
    } catch {
      setApiStatus("fallback");
    }
  }

  async function refreshAfterSopChange() {
    try {
      const [sopData, summaryData] = await Promise.all([getSopDocuments(), getDashboardSummary()]);
      setDocuments(Array.isArray(sopData) ? sopData : sopData.results || []);
      setSummary(summaryData);
      setApiStatus("connected");
    } catch {
      setApiStatus("fallback");
    }
  }

  async function refreshAfterGeneration() {
    try {
      const [questionData, summaryData] = await Promise.all([getQuestions(), getDashboardSummary()]);
      setQuestions(Array.isArray(questionData) ? questionData : questionData.results || []);
      setSummary(summaryData);
      setApiStatus("connected");
    } catch {
      setApiStatus("fallback");
    }
  }

  async function refreshAfterAttempt() {
    try {
      const summaryData = await getDashboardSummary();
      setSummary(summaryData);
      setApiStatus("connected");
    } catch {
      setApiStatus("fallback");
    }
  }

  if (isCheckingSession) {
    return (
      <div className="login-screen">
        <div className="login-card">
          <div className="login-card__brand">
            <ShieldCheck size={22} />
            <span>GxP Tutor</span>
          </div>
          <p className="muted-small">Loading...</p>
        </div>
      </div>
    );
  }

  if (!currentUser) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  const roles = currentUser.roles || {};
  const pageRequirement = navigation.find((item) => item.id === activePage)?.requires;
  const effectivePage = pageRequirement && !roles[pageRequirement] ? "dashboard" : activePage;

  const content = {
    dashboard: <Dashboard summary={summary} apiStatus={apiStatus} />,
    "sop-library": (
      <SopLibrary apiStatus={apiStatus} canUpload={roles.is_admin} documents={documents} onUploaded={refreshAfterSopChange} />
    ),
    "generate-quiz": (
      <GenerateQuiz
        documents={documents}
        jobRoles={jobRoles}
        onGenerated={refreshAfterGeneration}
        onNavigate={setActivePage}
      />
    ),
    "question-review": (
      <QuestionReview
        canReview={roles.is_reviewer}
        questions={questions}
        apiStatus={apiStatus}
        onApproveQuestion={(id) => handleQuestionStatus(id, "approve")}
        onRejectQuestion={(id) => handleQuestionStatus(id, "reject")}
      />
    ),
    "learner-quiz": <LearnerQuiz currentUser={currentUser} onSubmitted={refreshAfterAttempt} />,
    analytics: <Analytics apiStatus={apiStatus} summary={summary} />,
    users: <UsersRoles jobRoles={jobRoles} learnerProfiles={learnerProfiles} />,
  }[effectivePage];

  return (
    <div className="app">
      <Sidebar activePage={effectivePage} currentUser={currentUser} onNavigate={setActivePage} />
      <main className="main">
        <Topbar currentUser={currentUser} onLogout={handleLogout} title={pageTitles[effectivePage]} />
        {content}
      </main>
    </div>
  );
}

export default App;
