const output = document.getElementById("output");
const authStatus = document.getElementById("authStatus");
const telegramIdInput = document.getElementById("telegramId");
const roleBadge = document.getElementById("userRoleBadge");

const meetingDateInput = document.getElementById("meetingDate");
const durationInput = document.getElementById("duration");
const bookingSlotsButton = document.getElementById("bookingSlotsButton");
const bookingNextButton = document.getElementById("bookingNextButton");
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

const requestIdInput = document.getElementById("requestId");
const rejectReasonInput = document.getElementById("rejectReason");
const alternativeSlotInput = document.getElementById("alternativeSlot");
const settingKeyInput = document.getElementById("settingKey");
const settingValueInput = document.getElementById("settingValue");
const oauthCodeInput = document.getElementById("oauthCode");
const adminRequestsList = document.getElementById("adminRequestsList");
const adminStatus = document.getElementById("adminStatus");

const editableStatuses = new Set(["pending_approval", "updated_by_user"]);

let token = null;
let currentRole = "guest";
let bookingConfig = null;
let bookingPageOffset = 0;

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

const ensureAdminRole = () => {
  ensureAuthorized();
  if (currentRole !== "admin") {
    throw new Error("Это действие доступно только администратору.");
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

const setStatus = (element, message, kind = "info", strict = false) => {
  if (!element) {
    return;
  }
  element.className = "muted status-line";
  if (kind === "error") {
    element.classList.add("error");
  }
  if (kind === "success") {
    element.classList.add("success");
  }
  if (strict) {
    element.classList.add("strict");
  }
  element.textContent = message;
};

const setBookingStatus = (message, kind = "info") => {
  setStatus(bookingStatus, message, kind, false);
};

const setAdminStatus = (message, kind = "info") => {
  setStatus(adminStatus, message, kind, true);
};

const showEmptyRequestsState = (enabled) => {
  if (!requestsEmptyState) {
    return;
  }
  requestsEmptyState.classList.toggle("hidden", !enabled);
};

const startOfLocalDay = (sourceDate) => {
  const date = new Date(sourceDate);
  date.setHours(0, 0, 0, 0);
  return date;
};

const addDays = (sourceDate, days) => {
  const date = new Date(sourceDate);
  date.setDate(date.getDate() + days);
  return date;
};

const toIsoDate = (sourceDate) => {
  const date = new Date(sourceDate);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const formatWeekDayMeta = (dateText) => {
  const parsed = new Date(`${dateText}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return {
      icon: "•",
      name: "ДЕНЬ",
      date: dateText,
    };
  }

  const iconByDayIndex = {
    1: "🌿",
    2: "💼",
    3: "📌",
    4: "🗓",
    5: "✨",
    6: "☕",
    0: "🌞",
  };
  const dayIndex = parsed.getDay();
  return {
    icon: iconByDayIndex[dayIndex] || "•",
    name: parsed.toLocaleDateString("ru-RU", { weekday: "short" }).replace(".", "").toUpperCase(),
    date: parsed.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }),
  };
};

const resetSlotSelection = (placeholder = "Сначала выберите день недели") => {
  slotSelect.innerHTML = "";
  const option = document.createElement("option");
  option.value = "";
  option.textContent = placeholder;
  slotSelect.append(option);
};

const setBookingStepState = (hasDateSelected) => {
  durationInput.disabled = !hasDateSelected;
  if (bookingSlotsButton) {
    bookingSlotsButton.disabled = !hasDateSelected;
  }

  if (!hasDateSelected) {
    resetSlotSelection("Сначала выберите день недели");
  }
};

const getMaxSelectableDate = () => {
  const horizonDays = Number(bookingConfig?.booking_horizon_days ?? NaN);
  if (!Number.isFinite(horizonDays)) {
    return null;
  }
  return addDays(startOfLocalDay(new Date()), Math.max(0, horizonDays));
};

const buildDatePage = (pageOffset) => {
  const startDate = addDays(startOfLocalDay(new Date()), pageOffset * 7);
  const maxDate = getMaxSelectableDate();
  const items = [];
  for (let index = 0; index < 7; index += 1) {
    const date = addDays(startDate, index);
    const disabled = maxDate ? date > maxDate : false;
    items.push({
      iso: toIsoDate(date),
      disabled,
    });
  }
  const canGoNext = maxDate ? addDays(startDate, 7) <= maxDate : true;
  return {
    items,
    canGoNext,
    start: toIsoDate(startDate),
  };
};

const parseSlotLabel = (slotEncoded) => {
  const [startRaw, endRaw] = String(slotEncoded).split("|", 2);
  const start = new Date(startRaw);
  const end = new Date(endRaw);

  const startText = Number.isNaN(start.getTime())
    ? String(startRaw).slice(11, 16)
    : start.toLocaleTimeString("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });

  const endText = Number.isNaN(end.getTime())
    ? String(endRaw).slice(11, 16)
    : end.toLocaleTimeString("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });

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

  setBookingStepState(Boolean(meetingDateInput.value));
};

const renderWeekDays = (dateItems) => {
  weekDays.innerHTML = "";

  if (!Array.isArray(dateItems) || dateItems.length === 0) {
    weekDays.innerHTML = '<p class="muted">Даты для записи не найдены.</p>';
    meetingDateInput.value = "";
    setBookingStepState(false);
    return;
  }

  dateItems.forEach((item) => {
    const dayMeta = formatWeekDayMeta(item.iso);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "week-day-btn";
    button.disabled = Boolean(item.disabled);
    button.innerHTML = `
      <span class="week-day-icon">${dayMeta.icon}</span>
      <span class="week-day-name">${dayMeta.name}</span>
      <span class="week-day-date">${dayMeta.date}</span>
    `;

    if (meetingDateInput.value === item.iso) {
      button.classList.add("active");
    }

    button.addEventListener("click", () => {
      if (item.disabled) {
        return;
      }
      meetingDateInput.value = item.iso;
      renderBookingPage({ logEvent: false });
      setBookingStepState(true);
      resetSlotSelection("Нажмите «Найти свободные слоты»");
      setBookingStatus(
        `Выбран день ${dayMeta.name} ${dayMeta.date}. Теперь выберите длительность и найдите свободные слоты.`
      );
    });

    weekDays.append(button);
  });
};

const renderBookingPage = ({ logEvent = true } = {}) => {
  const page = buildDatePage(bookingPageOffset);

  if (meetingDateInput.value) {
    const existsOnPage = page.items.some((item) => item.iso === meetingDateInput.value);
    if (!existsOnPage) {
      meetingDateInput.value = "";
      setBookingStepState(false);
    }
  }

  renderWeekDays(page.items);
  if (bookingNextButton) {
    bookingNextButton.disabled = !page.canGoNext;
  }

  if (logEvent) {
    write("Показаны дни для выбора", {
      page_offset: bookingPageOffset,
      page_start: page.start,
      dates: page.items,
      can_go_next: page.canGoNext,
    });
  }
};

const renderSlots = (slots, preserveSelection = true) => {
  const previousValue = preserveSelection ? slotSelect.value : "";
  slotSelect.innerHTML = "";

  const normalizedSlots = Array.isArray(slots) ? slots : [];
  if (normalizedSlots.length === 0) {
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

  normalizedSlots.forEach((slotEncoded) => {
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

const renderAdminRequests = (items) => {
  if (!adminRequestsList) {
    return;
  }

  if (!Array.isArray(items) || items.length === 0) {
    adminRequestsList.innerHTML = '<p class="muted">Заявок для обработки нет.</p>';
    return;
  }

  const markup = items
    .map((item) => {
      const requestId = Number(item.id);
      const statusLabel = escapeHtml(item.status_label || item.status || "-");
      const slotText = `${escapeHtml(item.meeting_date)} ${escapeHtml(item.start_time)}-${escapeHtml(
        item.end_time
      )}`;
      const tgUser = item.user?.telegram_user_id ? `tg:${escapeHtml(item.user.telegram_user_id)}` : "tg:-";
      const blocked = item.user?.is_blocked ? "заблокирован" : "активен";
      const goalText = escapeHtml(item.meeting_goal || "");

      return `
        <article class="request-item">
          <div class="request-item-head">
            <span class="request-id">#${requestId}</span>
            <span class="request-status">${statusLabel}</span>
          </div>
          <p class="request-meta">${slotText} • ${tgUser} • ${blocked}</p>
          <p class="request-goal">Цель: ${goalText}</p>
          <div class="request-actions">
            <button class="btn subtle strict" data-admin-select="true" data-request-id="${requestId}">Выбрать</button>
            <button class="btn secondary strict" data-admin-request-action="approve" data-request-id="${requestId}">Согласовать</button>
            <button class="btn warn" data-admin-request-action="reject" data-request-id="${requestId}">Отклонить</button>
            <button class="btn subtle strict" data-admin-request-action="history" data-request-id="${requestId}">История</button>
          </div>
        </article>
      `;
    })
    .join("");

  adminRequestsList.innerHTML = markup;
};

const parseRequestId = () => {
  const requestId = Number(requestIdInput.value || 0);
  if (!requestId) {
    throw new Error("Укажите корректный ID заявки.");
  }
  return requestId;
};

const extractOAuthCode = (rawValue) => {
  const trimmed = String(rawValue || "").trim();
  if (!trimmed) {
    return "";
  }

  try {
    const parsedUrl = new URL(trimmed);
    const fromQuery = parsedUrl.searchParams.get("code");
    if (fromQuery) {
      return fromQuery;
    }
  } catch {
    // not an URL, continue
  }

  const regexMatch = trimmed.match(/[?&]code=([^&#]+)/i);
  if (regexMatch?.[1]) {
    try {
      return decodeURIComponent(regexMatch[1]);
    } catch {
      return regexMatch[1];
    }
  }

  return trimmed;
};

const loadBookingConfig = async ({ logEvent = true } = {}) => {
  ensureAuthorized();
  const data = await api("/booking/config");
  bookingConfig = data;
  setDurationOptions(data.available_durations_minutes);
  if (logEvent) {
    write("Параметры записи", data);
  }
  return data;
};

const loadBookingWeek = async ({ logEvent = true } = {}) => {
  ensureAuthorized();
  const data = buildDatePage(bookingPageOffset);
  renderBookingPage({ logEvent: false });
  if (logEvent) {
    write("Доступные даты", data);
  }
  return data;
};

const loadBookingSlots = async ({ logEvent = true, preserveSelection = true } = {}) => {
  ensureAuthorized();

  if (!meetingDateInput.value) {
    setBookingStatus("Сначала выберите день ближайшей недели.", "error");
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

const loadAdminRequests = async ({ logEvent = true } = {}) => {
  ensureAdminRole();
  const data = await api("/admin/requests");
  renderAdminRequests(data.items);
  if (logEvent) {
    write("Список заявок (admin)", data);
  }
  return data;
};

const initializeAfterLogin = async () => {
  try {
    bookingPageOffset = 0;
    meetingDateInput.value = "";
    setBookingStepState(false);
    await loadBookingConfig({ logEvent: false });
    await loadBookingWeek({ logEvent: false });
    await refreshRequests({ logEvent: false });
    setBookingStatus("Выберите день ближайшей недели, затем длительность и свободный слот.", "success");

    if (currentRole === "admin") {
      await loadAdminRequests({ logEvent: false });
      setAdminStatus("Админ-очередь загружена.", "success");
    } else {
      setAdminStatus("Войдите как администратор для админ-действий.", "info");
    }
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
    if (currentRole === "admin") {
      await loadAdminRequests({ logEvent: false });
    }
  } catch (error) {
    if (error.message.includes("Selected slot is no longer available.")) {
      setBookingStatus(
        "Этот слот уже заняли. Мы обновили список, выберите новый слот и повторите отправку.",
        "error"
      );
      await loadBookingSlots({ logEvent: false, preserveSelection: false });
      write("Слот устарел при отправке", {
        error: error.message,
        meeting_date: meetingDateInput.value,
      });
      return;
    }

    setBookingStatus(error.message, "error");
    write("Ошибка создания заявки", { error: error.message });
  }
};

const runAdminRequestAction = async (action, requestId) => {
  ensureAdminRole();

  const actionMap = {
    approve: {
      method: "POST",
      path: `/admin/requests/${requestId}/approve`,
      title: "Согласование заявки",
      successText: `Заявка #${requestId} согласована.`,
      refresh: true,
    },
    reject: {
      method: "POST",
      path: `/admin/requests/${requestId}/reject`,
      body: { reason: (rejectReasonInput.value || "").trim() },
      title: "Отклонение заявки",
      successText: `Заявка #${requestId} отклонена.`,
      refresh: true,
    },
    alternative: {
      method: "POST",
      path: `/admin/requests/${requestId}/alternative`,
      body: { value: (alternativeSlotInput.value || "").trim() },
      title: "Предложение альтернативного слота",
      successText: `Для заявки #${requestId} предложен альтернативный слот.`,
      refresh: true,
    },
    history: {
      method: "GET",
      path: `/admin/requests/${requestId}/history`,
      title: "История статусов заявки",
      successText: `История заявки #${requestId} загружена.`,
      refresh: false,
    },
    block: {
      method: "POST",
      path: `/admin/requests/${requestId}/block`,
      title: "Блокировка пользователя",
      successText: `Пользователь по заявке #${requestId} заблокирован.`,
      refresh: true,
    },
    unblock: {
      method: "POST",
      path: `/admin/requests/${requestId}/unblock`,
      title: "Разблокировка пользователя",
      successText: `Пользователь по заявке #${requestId} разблокирован.`,
      refresh: true,
    },
    "manual-create": {
      method: "POST",
      path: `/admin/requests/${requestId}/manual-create`,
      title: "Ручное создание встречи",
      successText: `Для заявки #${requestId} выполнено ручное создание встречи.`,
      refresh: true,
    },
  };

  const config = actionMap[action];
  if (!config) {
    throw new Error(`Неизвестное админ-действие: ${action}`);
  }

  if (action === "reject" && !config.body.reason) {
    throw new Error("Укажите причину отклонения.");
  }
  if (action === "alternative" && !config.body.value) {
    throw new Error("Укажите альтернативный слот в формате YYYY-MM-DD HH:MM-HH:MM.");
  }

  const requestOptions = {
    method: config.method,
  };
  if (config.body) {
    requestOptions.body = JSON.stringify(config.body);
  }

  const data = await api(config.path, requestOptions);
  write(config.title, data);
  setAdminStatus(config.successText, "success");

  if (config.refresh) {
    await loadAdminRequests({ logEvent: false });
  }

  return data;
};

const updateAdminSettings = async () => {
  ensureAdminRole();

  const settingKey = String(settingKeyInput.value || "").trim();
  const value = String(settingValueInput.value || "").trim();

  if (!settingKey) {
    throw new Error("Выберите ключ настройки.");
  }
  if (!value) {
    throw new Error("Введите значение настройки.");
  }

  const data = await api("/admin/settings", {
    method: "PATCH",
    body: JSON.stringify({ setting_key: settingKey, value }),
  });

  write("Обновление настроек (admin)", data);
  setAdminStatus("Настройка применена.", "success");
  return data;
};

const runOAuthExchange = async () => {
  ensureAdminRole();

  const code = extractOAuthCode(oauthCodeInput.value);
  if (!code) {
    throw new Error("Вставьте код авторизации или полный callback URL.");
  }

  const data = await api("/admin/google/oauth/exchange", {
    method: "POST",
    body: JSON.stringify({ code }),
  });

  write("Google OAuth exchange", data);
  setAdminStatus("Код OAuth успешно обработан.", "success");
  return data;
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
    if (currentRole === "admin") {
      await loadAdminRequests({ logEvent: false });
    }
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
    currentRole = data.role;
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

durationInput.addEventListener("change", () => {
  if (!meetingDateInput.value) {
    return;
  }
  resetSlotSelection("Нажмите «Найти свободные слоты»");
  setBookingStatus("Длительность обновлена. Нажмите «Найти свободные слоты».");
});

if (bookingNextButton) {
  bookingNextButton.addEventListener("click", () => {
    if (bookingNextButton.disabled) {
      return;
    }
    bookingPageOffset += 1;
    meetingDateInput.value = "";
    setBookingStepState(false);
    renderBookingPage({ logEvent: true });
    setBookingStatus("Показаны следующие 7 дней. Выберите день для поиска слотов.");
  });
}

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

adminRequestsList.addEventListener("click", async (event) => {
  const selectButton = event.target.closest("button[data-admin-select='true']");
  if (selectButton) {
    const requestId = Number(selectButton.dataset.requestId || 0);
    if (requestId) {
      requestIdInput.value = String(requestId);
      setAdminStatus(`Выбрана заявка #${requestId}.`, "success");
    }
    return;
  }

  const actionButton = event.target.closest("button[data-admin-request-action]");
  if (!actionButton) {
    return;
  }

  try {
    const requestId = Number(actionButton.dataset.requestId || 0);
    if (!requestId) {
      throw new Error("Некорректный ID заявки.");
    }

    requestIdInput.value = String(requestId);
    await runAdminRequestAction(actionButton.dataset.adminRequestAction, requestId);
  } catch (error) {
    setAdminStatus(error.message, "error");
    write("Ошибка админ-действия из очереди", {
      action: actionButton.dataset.adminRequestAction,
      error: error.message,
    });
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
          await loadAdminRequests();
          break;
        case "admin-settings":
          ensureAdminRole();
          write("Настройки расписания (admin)", await api("/admin/settings"));
          setAdminStatus("Текущие настройки загружены.", "success");
          break;
        case "admin-settings-update":
          await updateAdminSettings();
          break;
        case "admin-oauth-url":
          ensureAdminRole();
          write("Google OAuth инструкция", await api("/admin/google/oauth/url"));
          setAdminStatus("Инструкция OAuth загружена.", "success");
          break;
        case "admin-oauth-exchange":
          await runOAuthExchange();
          break;
        case "admin-approve":
          await runAdminRequestAction("approve", parseRequestId());
          break;
        case "admin-reject":
          await runAdminRequestAction("reject", parseRequestId());
          break;
        case "admin-alternative":
          await runAdminRequestAction("alternative", parseRequestId());
          break;
        case "admin-history":
          await runAdminRequestAction("history", parseRequestId());
          break;
        case "admin-block":
          await runAdminRequestAction("block", parseRequestId());
          break;
        case "admin-unblock":
          await runAdminRequestAction("unblock", parseRequestId());
          break;
        case "admin-manual-create":
          await runAdminRequestAction("manual-create", parseRequestId());
          break;
        default:
          write("Неизвестное действие", { warning: `Неизвестное действие: ${action}` });
      }
    } catch (error) {
      write("Ошибка действия", { action, error: error.message });
      if (action.startsWith("booking")) {
        setBookingStatus(error.message, "error");
      }
      if (action.startsWith("admin")) {
        setAdminStatus(error.message, "error");
      }
    }
  });
});
