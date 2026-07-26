const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000/api";
const TOKEN_STORAGE_KEY = "gxp_auth_token";

let authToken = localStorage.getItem(TOKEN_STORAGE_KEY) || null;

export function getStoredToken() {
  return authToken;
}

export function setAuthToken(token) {
  authToken = token;
  if (token) {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
  }
}

function authHeaders(extra = {}) {
  return authToken ? { ...extra, Authorization: `Token ${authToken}` } : extra;
}

async function parseErrorAndThrow(response, path) {
  const errorBody = await response.json().catch(() => ({}));
  throw new Error(errorBody.error || `Request failed: ${path}`);
}

async function request(path) {
  const response = await fetch(`${API_BASE_URL}${path}`, { headers: authHeaders() });
  if (!response.ok) {
    await parseErrorAndThrow(response, path);
  }
  return response.json();
}

async function patch(path, body) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    await parseErrorAndThrow(response, path);
  }
  return response.json();
}

async function postJson(path, body) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    await parseErrorAndThrow(response, path);
  }
  return response.json();
}

async function del(path) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!response.ok) {
    await parseErrorAndThrow(response, path);
  }
}

async function postForm(path, formData) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  if (!response.ok) {
    await parseErrorAndThrow(response, path);
  }
  return response.json();
}

export async function login(username, password) {
  const data = await postJson("/accounts/login/", { username, password });
  setAuthToken(data.token);
  return data.user;
}

export async function logout() {
  try {
    await postJson("/accounts/logout/", {});
  } finally {
    setAuthToken(null);
  }
}

export async function getMe() {
  return request("/accounts/me/");
}

export async function getDashboardSummary() {
  return request("/analytics/dashboard-summary/");
}

export async function getRecommendedRefresher() {
  return request("/analytics/recommended-refresher/");
}

export async function getAutoAssignedRetraining() {
  return request("/attempts/auto-assigned/");
}

export async function getRetrainingStatus() {
  return request("/attempts/retraining-status/");
}

export async function getSopDocuments() {
  return request("/sops/documents/");
}

export async function getQuestions() {
  return request("/quiz/questions/");
}

export async function getApprovedQuestionsForRole(jobRoleId) {
  return request(`/quiz/questions/?job_role=${jobRoleId}&status=approved`);
}

export async function getJobRoles() {
  return request("/accounts/job-roles/");
}

export async function getLearnerProfiles() {
  return request("/accounts/learner-profiles/");
}

export async function approveQuestion(id, password) {
  return patch(`/quiz/questions/${id}/approve/`, { password });
}

export async function rejectQuestion(id, password) {
  return patch(`/quiz/questions/${id}/reject/`, { password });
}

export async function downloadAuditLogCsv() {
  const response = await fetch(`${API_BASE_URL}/audit/logs/export/`, { headers: authHeaders() });
  if (!response.ok) {
    await parseErrorAndThrow(response, "/audit/logs/export/");
  }
  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `gxp-audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

export async function createSopDocument(formData) {
  return postForm("/sops/documents/", formData);
}

export async function processSopDocument(id) {
  return postJson(`/sops/documents/${id}/process/`, {});
}

export async function deleteSopDocument(id) {
  return del(`/sops/documents/${id}/`);
}

export async function updateQuestion(id, updates) {
  return patch(`/quiz/questions/${id}/`, updates);
}

export async function generateQuiz({ sop, jobRole, count, difficulty }) {
  return postJson("/ai_engine/generate/", { sop, job_role: jobRole, count, difficulty });
}

export async function askSopQuestion({ sop, question }) {
  return postJson("/ai_engine/sop-chat/", { sop, question });
}

export async function createQuizAttempt({ sop, jobRole }) {
  return postJson("/attempts/quiz-attempts/", { sop, job_role: jobRole });
}

export async function submitQuizAttempt(attemptId, answers) {
  return postJson(`/attempts/quiz-attempts/${attemptId}/submit/`, { answers });
}
