const output = document.getElementById("output");
const authStatus = document.getElementById("authStatus");
const telegramIdInput = document.getElementById("telegramId");
const roleBadge = document.getElementById("userRoleBadge");
const requestIdInput = document.getElementById("requestId");
const weekOffsetInput = document.getElementById("weekOffset");
const meetingDateInput = document.getElementById("meetingDate");
const durationInput = document.getElementById("duration");
const requestsEmptyState = document.getElementById("requestsEmptyState");

let token = null;

const write = (title, payload) => {
  output.textContent = [
    `${new Date().toISOString()} • ${title}`,
    JSON.stringify(payload, null, 2),
    "",
    output.textContent,
  ].join("\n");
};

const api = async (path, options = {}) => {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(`/api/miniapp${path}`, {
    ...options,
    headers,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Request failed: ${response.status}`);
  }
  return data;
};

const showEmptyRequestsState = (enabled) => {
  if (!requestsEmptyState) {
    return;
  }
  requestsEmptyState.classList.toggle("hidden", !enabled);
};

document.getElementById("devLoginButton").addEventListener("click", async () => {
  const telegramUserId = Number(telegramIdInput.value || 0);
  if (!telegramUserId) {
    authStatus.textContent = "Введите корректный Telegram ID.";
    return;
  }
  try {
    const data = await api("/auth/dev-login", {
      method: "POST",
      body: JSON.stringify({ telegram_user_id: telegramUserId }),
    });
    token = data.access_token;
    roleBadge.textContent = data.role;
    authStatus.textContent = `Вы вошли как ${data.role}. Telegram ID: ${data.telegram_user_id}.`;
    write("Авторизация", { auth: data });
  } catch (error) {
    authStatus.textContent = error.message;
    write("Ошибка авторизации", { error: error.message });
  }
});

document.getElementById("clearOutput").addEventListener("click", () => {
  output.textContent = "";
});

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    const action = button.dataset.action;
    try {
      switch (action) {
        case "booking-config":
          write("Параметры записи", await api("/booking/config"));
          break;
        case "my-requests":
          {
            const data = await api("/requests");
            showEmptyRequestsState(!data.items || data.items.length === 0);
            write("Мои заявки", data);
          }
          break;
        case "profile":
          write("Профиль", await api("/me"));
          break;
        case "support":
          write("Поддержка", await api("/support"));
          break;
        case "notifications":
          write("Уведомления", await api("/notifications"));
          break;
        case "booking-week":
          write(
            "Доступные даты",
            await api(`/booking/week?week_offset=${Number(weekOffsetInput.value || 0)}`)
          );
          break;
        case "booking-slots":
          write(
            "Свободные слоты",
            await api(
              `/booking/slots?meeting_date=${encodeURIComponent(
                meetingDateInput.value
              )}&duration_minutes=${Number(durationInput.value || 30)}`
            )
          );
          break;
        case "admin-requests":
          write("Список заявок (admin)", await api("/admin/requests"));
          break;
        case "admin-settings":
          write("Настройки расписания (admin)", await api("/admin/settings"));
          break;
        case "admin-oauth-url":
          write("Google OAuth инструкция", await api("/admin/google/oauth/url"));
          break;
        case "admin-approve":
          write(
            "Согласование заявки",
            await api(`/admin/requests/${Number(requestIdInput.value || 0)}/approve`, {
              method: "POST",
            })
          );
          break;
        case "admin-history":
          write(
            "История статусов заявки",
            await api(`/admin/requests/${Number(requestIdInput.value || 0)}/history`)
          );
          break;
        default:
          write("Неизвестное действие", { warning: `Неизвестное действие: ${action}` });
      }
    } catch (error) {
      write("Ошибка действия", { action, error: error.message });
    }
  });
});
