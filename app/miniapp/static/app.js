const output = document.getElementById("output");
const authStatus = document.getElementById("authStatus");
const telegramIdInput = document.getElementById("telegramId");
const roleBadge = document.getElementById("userRoleBadge");
const requestIdInput = document.getElementById("requestId");
const weekOffsetInput = document.getElementById("weekOffset");
const meetingDateInput = document.getElementById("meetingDate");
const durationInput = document.getElementById("duration");
const slotSelect = document.getElementById("slotSelect");
const weekDays = document.getElementById("weekDays");
const bookingStatus = document.getElementById("bookingStatus");
const fullNameInput = document.getElementById("fullName");
const phoneInput = document.getElementById("phone");
const emailInput = document.getElementById("email");
const meetingGoalInput = document.getElementById("meetingGoal");
const personalConsentInput = document.getElementById("personalConsent");
const createRequestButton = document.getElementById("createRequestButton");
const requestsList = document.getElementById("requestsList");
const requestsEmptyState = document.getElementById("requestsEmptyState");

const editableStatuses = new Set(["pending_approval", "updated_by_user"]);

let token = null;
let latestSlots = [];

const write = (title, payload) => {
  output.textContent = [
    `${new Date().toISOString()} • ${title}`,
    JSON.stringify(payload, null, 2),
    "",
    output.textContent,
  ].join("\n");
};

const escapeHtml = (value) =>
  String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");

const ensureAuthorized = () => {
  if (!token) {
    throw new Error("Сначала выполните вход в Mini App.");
  }
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

const setBookingStatus = (message, kind = "info") => {
  if (!bookingStatus) {
    return;
  }
  bookingStatus.className = "muted status-line";
  if (kind === "error") {
    bookingStatus.classList.add("error");
  }
  if (kind === "success") {
    bookingStatus.classList.add("success");
  }
  bookingStatus.textContent = message;
};

const showEmptyRequestsState = (enabled) => {
  if (!requestsEmptyState) {
    return;
  }
  requestsEmptyState.classList.toggle("hidden", !enabled);
};

const formatDateChip = (dateText) => {
  const parsed = new Date(`${dateText}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return dateText;
  }
  return parsed.toLocaleDateString("ru-RU", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
  });
};

const parseSlotLabel = (slotEncoded) => {
  const [startRaw, endRaw] = slotEncoded.split("|", 2);
  const start = new Date(startRaw);
  const end = new Date(endRaw);

  const startText = Number.isNaN(start.getTime())
    ? String(startRaw).slice(11, 16)
    : start.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", hour12: false });
  const endText = Number.isNaN(end.getTime())
    ? String(endRaw).slice(11, 16)
    : end.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", hour12: false });

  return `${startText}-${endText}`;
};

const setDurationOptions = (durations) => {
  const currentValue = Number(durationInput.value || 0);
  durationInput.innerHTML = "";

  if (!durations || durations.length === 0) {
    const option = document.createElement("option");
    option.value = "30";
    option.textContent = "30";
    durationInput.append(option);
    return;
  }

  durations.forEach((value) => {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = String(value);
    if (value === currentValue) {
      option.selected = true;
    }
    durationInput.append(option);
  });

  if (!durationInput.value) {
    durationInput.value = String(durations[0]);
  }
};

const renderWeekDays = (days) => {
  weekDays.innerHTML = "";

  if (!Array.isArray(days) || days.length === 0) {
    weekDays.innerHTML = '<p class="muted">Нет доступных дат на выбранной неделе.</p>';
    return;
  }

  days.forEach((dateText) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "week-day-btn";
    button.textContent = formatDateChip(dateText);
    if (meetingDateInput.value === dateText) {
      button.classList.add("active");
    }

    button.addEventListener("click", async () => {
      meetingDateInput.value = dateText;
      renderWeekDays(days);
      await loadBookingSlots({ logEvent: true, preserveSelection: false });
    });

    weekDays.append(button);
  });
};

const renderSlots = (slots, preserveSelection = true) => {
  const previousValue = preserveSelection ? slotSelect.value : "";
  slotSelect.innerHTML = "";
  latestSlots = Array.isArray(slots) ? slots : [];

  if (latestSlots.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "На выбранную дату свободных слотов нет";
    slotSelect.append(option);
    return;
  }

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Выберите слот";
  slotSelect.append(placeholder);

  latestSlots.forEach((slotEncoded) => {
    const option = document.createElement("option");
    option.value = slotEncoded;
    option.textContent = parseSlotLabel(slotEncoded);
    if (slotEncoded === previousValue) {
      option.selected = true;
    }
    slotSelect.append(option);
  });
};

const canEditRequest = (status) => editableStatuses.has(status);

const renderRequests = (items) => {
  if (!requestsList) {
    return;
  }

  if (!Array.isArray(items) || items.length === 0) {
    requestsList.innerHTML = '<p class="muted">Заявок пока нет.</p>';
    return;
  }

  const markup = items
    .map((item) => {
      const requestId = Number(item.id);
      const statusLabel = escapeHtml(item.status_label || item.status || "-");
      const slotText = `${escapeHtml(item.meeting_date)} ${escapeHtml(item.start_time)}-${escapeHtml(
        item.end_time
      )}`;
      const goalText = escapeHtml(item.meeting_goal || "");

      let actions = "";
      if (canEditRequest(item.status)) {
        actions = `
          <div class="request-actions">
            <button class="btn subtle" data-request-action="edit-goal" data-request-id="${requestId}">Изменить цель</button>
            <button class="btn warn" data-request-action="cancel-request" data-request-id="${requestId}">Отменить заявку</button>
          </div>
        `;
      }

      return `
        <article class="request-item">
          <div class="request-item-head">
            <span class="request-id">Заявка #${requestId}</span>
            <span class="request-status">${statusLabel}</span>
          </div>
          <p class="request-meta">${slotText} • ${escapeHtml(item.duration_minutes)} мин</p>
          <p class="request-goal">Цель: ${goalText}</p>
          ${actions}
        </article>
      `;
    })
    .join("");

  requestsList.innerHTML = markup;
};

const loadBookingConfig = async ({ logEvent = true } = {}) => {
  ensureAuthorized();
  const data = await api("/booking/config");
  setDurationOptions(data.available_durations_minutes);
  if (logEvent) {
    write("Параметры записи", data);
  }
  return data;
};

const loadBookingWeek = async ({ logEvent = true } = {}) => {
  ensureAuthorized();
  const weekOffset = Number(weekOffsetInput.value || 0);
  const data = await api(`/booking/week?week_offset=${weekOffset}`);
  if ((!meetingDateInput.value || !data.days.includes(meetingDateInput.value)) && data.days.length > 0) {
    meetingDateInput.value = data.days[0];
  }
  renderWeekDays(data.days);
  if (logEvent) {
    write("Доступные даты", data);
  }
  return data;
};

const loadBookingSlots = async ({ logEvent = true, preserveSelection = true } = {}) => {
  ensureAuthorized();
  if (!meetingDateInput.value) {
    setBookingStatus("Сначала выберите дату.", "error");
    return null;
  }

  const durationMinutes = Number(durationInput.value || 0);
  if (!durationMinutes) {
    setBookingStatus("Сначала выберите длительность встречи.", "error");
    return null;
  }

  const data = await api(
    `/booking/slots?meeting_date=${encodeURIComponent(
      meetingDateInput.value
    )}&duration_minutes=${durationMinutes}`
  );
  renderSlots(data.slots, preserveSelection);

  if (!data.slots || data.slots.length === 0) {
    setBookingStatus("На эту дату нет свободных слотов, выберите другую дату.", "error");
  } else {
    setBookingStatus(`Найдено слотов: ${data.slots.length}.`, "success");
  }

  if (logEvent) {
    write("Свободные слоты", data);
  }

  return data;
};

const refreshRequests = async ({ logEvent = true } = {}) => {
  ensureAuthorized();
  const data = await api("/requests");
  renderRequests(data.items);
  showEmptyRequestsState(!data.items || data.items.length === 0);
  if (logEvent) {
    write("Мои заявки", data);
  }
  return data;
};

const initializeAfterLogin = async () => {
  try {
    await loadBookingConfig({ logEvent: false });
    await loadBookingWeek({ logEvent: false });
    await loadBookingSlots({ logEvent: false, preserveSelection: false });
    await refreshRequests({ logEvent: false });
    setBookingStatus("Данные загружены. Можно оформлять заявку.", "success");
  } catch (error) {
    setBookingStatus(error.message, "error");
    write("Ошибка инициализации после входа", { error: error.message });
  }
};

const createRequest = async () => {
  ensureAuthorized();

  const slotEncoded = slotSelect.value;
  if (!slotEncoded) {
    setBookingStatus("Сначала выберите свободный слот.", "error");
    return;
  }

  if (!personalConsentInput.checked) {
    setBookingStatus("Подтвердите согласие на обработку персональных данных.", "error");
    return;
  }

  const payload = {
    duration_minutes: Number(durationInput.value || 0),
    slot_encoded: slotEncoded,
    full_name: (fullNameInput.value || "").trim(),
    phone: (phoneInput.value || "").trim(),
    email: (emailInput.value || "").trim(),
    meeting_goal: (meetingGoalInput.value || "").trim(),
    personal_data_consent: true,
  };

  if (!payload.full_name || !payload.phone || !payload.email || !payload.meeting_goal) {
    setBookingStatus("Заполните ФИО, телефон, email и цель встречи.", "error");
    return;
  }

  try {
    const data = await api("/requests", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    setBookingStatus(`Заявка #${data.request.id} отправлена на согласование.`, "success");
    write("Заявка создана", data);
    await refreshRequests({ logEvent: false });
  } catch (error) {
    if (error.message.includes("Selected slot is no longer available.")) {
      setBookingStatus(
        "Этот слот уже заняли. Мы обновили список, выберите новый слот и повторите отправку.",
        "error"
      );
      await loadBookingSlots({ logEvent: false, preserveSelection: false });
      write("Слот устарел при отправке", { error: error.message, meeting_date: meetingDateInput.value });
      return;
    }

    setBookingStatus(error.message, "error");
    write("Ошибка создания заявки", { error: error.message });
  }
};

const requestActionHandlers = {
  "edit-goal": async (requestId) => {
    const requestNode = requestsList.querySelector(`[data-request-id='${requestId}']`);
    const currentGoalText = requestNode
      ?.closest(".request-item")
      ?.querySelector(".request-goal")
      ?.textContent?.replace(/^Цель:\s*/i, "")
      ?.trim();

    const nextGoal = window.prompt("Введите новую цель встречи", currentGoalText || "");
    if (nextGoal === null) {
      return;
    }
    if (!nextGoal.trim()) {
      setBookingStatus("Цель встречи не может быть пустой.", "error");
      return;
    }

    const data = await api(`/requests/${requestId}/goal`, {
      method: "PATCH",
      body: JSON.stringify({ meeting_goal: nextGoal.trim() }),
    });
    write("Цель заявки обновлена", data);
    await refreshRequests({ logEvent: false });
    setBookingStatus(`Заявка #${requestId}: цель обновлена.`, "success");
  },
  "cancel-request": async (requestId) => {
    const ok = window.confirm(`Отменить заявку #${requestId}?`);
    if (!ok) {
      return;
    }

    const data = await api(`/requests/${requestId}/cancel`, { method: "POST" });
    write("Заявка отменена", data);
    await refreshRequests({ logEvent: false });
    setBookingStatus(`Заявка #${requestId} отменена.`, "success");
  },
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
    await initializeAfterLogin();
  } catch (error) {
    authStatus.textContent = error.message;
    write("Ошибка авторизации", { error: error.message });
  }
});

document.getElementById("clearOutput").addEventListener("click", () => {
  output.textContent = "";
});

createRequestButton.addEventListener("click", async () => {
  try {
    await createRequest();
  } catch (error) {
    setBookingStatus(error.message, "error");
    write("Ошибка пользовательского сценария", { error: error.message });
  }
});

requestsList.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-request-action]");
  if (!button) {
    return;
  }

  try {
    ensureAuthorized();
    const requestId = Number(button.dataset.requestId || 0);
    if (!requestId) {
      throw new Error("Некорректный ID заявки.");
    }

    const action = button.dataset.requestAction;
    const handler = requestActionHandlers[action];
    if (!handler) {
      throw new Error(`Неизвестное действие с заявкой: ${action}`);
    }

    await handler(requestId);
  } catch (error) {
    setBookingStatus(error.message, "error");
    write("Ошибка действия по заявке", { error: error.message });
  }
});

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    const action = button.dataset.action;

    try {
      switch (action) {
        case "booking-config":
          await loadBookingConfig();
          break;
        case "booking-week":
          await loadBookingWeek();
          break;
        case "booking-slots":
          await loadBookingSlots();
          break;
        case "my-requests":
          await refreshRequests();
          break;
        case "profile":
          ensureAuthorized();
          write("Профиль", await api("/me"));
          break;
        case "support":
          ensureAuthorized();
          write("Поддержка", await api("/support"));
          break;
        case "notifications":
          ensureAuthorized();
          write("Уведомления", await api("/notifications"));
          break;
        case "admin-requests":
          ensureAuthorized();
          write("Список заявок (admin)", await api("/admin/requests"));
          break;
        case "admin-settings":
          ensureAuthorized();
          write("Настройки расписания (admin)", await api("/admin/settings"));
          break;
        case "admin-oauth-url":
          ensureAuthorized();
          write("Google OAuth инструкция", await api("/admin/google/oauth/url"));
          break;
        case "admin-approve":
          ensureAuthorized();
          write(
            "Согласование заявки",
            await api(`/admin/requests/${Number(requestIdInput.value || 0)}/approve`, {
              method: "POST",
            })
          );
          break;
        case "admin-history":
          ensureAuthorized();
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
      if (action.startsWith("booking")) {
        setBookingStatus(error.message, "error");
      }
    }
  });
});
