const output = document.getElementById("output");
const authStatus = document.getElementById("authStatus");
const authHint = document.getElementById("authHint");
const telegramIdInput = document.getElementById("telegramId");
const devLoginControls = document.getElementById("devLoginControls");
const devLoginButton = document.getElementById("devLoginButton");
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
const settingValueLabel = document.getElementById("settingValueLabel");
const settingValueInput = document.getElementById("settingValue");
const settingValueHint = document.getElementById("settingValueHint");
const workingDaysCalendarEditor = document.getElementById("workingDaysCalendarEditor");
const workingHoursEditor = document.getElementById("workingHoursEditor");
const workingHoursPresets = document.getElementById("workingHoursPresets");
const settingsDurationsEditor = document.getElementById("settingsDurationsEditor");
const settingsDurationOptions = document.getElementById("settingsDurationOptions");
const forbiddenDateEditor = document.getElementById("forbiddenDateEditor");
const forbiddenDateOptions = document.getElementById("forbiddenDateOptions");
const forbiddenPeriodEditor = document.getElementById("forbiddenPeriodEditor");
const forbiddenPeriodDateOptions = document.getElementById("forbiddenPeriodDateOptions");
const forbiddenPeriodTimeOptions = document.getElementById("forbiddenPeriodTimeOptions");
const settingsWeekDaysContainer = document.getElementById("settingsWeekDays");
const workdayStartTimeInput = document.getElementById("workdayStartTime");
const workdayEndTimeInput = document.getElementById("workdayEndTime");
const oauthCodeInput = document.getElementById("oauthCode");
const oauthInstructions = document.getElementById("oauthInstructions");
const adminRequestsList = document.getElementById("adminRequestsList");
const adminStatus = document.getElementById("adminStatus");

const editableStatuses = new Set(["pending_approval", "updated_by_user"]);

let token = null;
let currentRole = "guest";
let bookingConfig = null;
let bookingPageOffset = 0;
let selectedWorkingDays = new Set();
let selectedAdminDurations = new Set();
let selectedForbiddenPeriodDate = "";
let selectedForbiddenPeriodPreset = "workday";

const telegramWebApp = window.Telegram?.WebApp || null;
const extractTelegramInitData = () => {
  const sdkInitData = telegramWebApp?.initData || "";
  if (sdkInitData) {
    return sdkInitData;
  }

  const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const queryParams = new URLSearchParams(window.location.search);
  return hashParams.get("tgWebAppData") || queryParams.get("tgWebAppData") || "";
};
const telegramInitData = extractTelegramInitData();
const isLocalHost = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);

const adminWeekdayOptions = [
  { value: "monday", label: "ПН", icon: "🌿" },
  { value: "tuesday", label: "ВТ", icon: "💼" },
  { value: "wednesday", label: "СР", icon: "📌" },
  { value: "thursday", label: "ЧТ", icon: "🗓" },
  { value: "friday", label: "ПТ", icon: "✨" },
  { value: "saturday", label: "СБ", icon: "☕" },
  { value: "sunday", label: "ВС", icon: "🌞" },
];

const adminWorkingHourOptions = [
  "09:00-17:00",
  "09:00-18:00",
  "10:00-18:00",
  "10:00-19:00",
  "11:00-19:00",
  "12:00-20:00",
];

const adminDurationOptions = [15, 30, 45, 60, 90, 120];

const adminForbiddenPeriodOptions = [
  { value: "workday", label: "Весь рабочий день", interval: null },
  { value: "morning", label: "Утро 09:00-12:00", interval: ["09:00", "12:00"] },
  { value: "day", label: "День 12:00-15:00", interval: ["12:00", "15:00"] },
  { value: "evening", label: "Вечер 15:00-18:00", interval: ["15:00", "18:00"] },
];

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

const parseTelegramUserIdInput = (rawValue) => {
  const value = String(rawValue || "").trim();
  if (!value) {
    throw new Error("Введите Telegram ID.");
  }
  if (!/^\d+$/.test(value)) {
    throw new Error("Telegram ID должен содержать только цифры.");
  }
  return value;
};

const applyAuthResponse = (data, sourceLabel) => {
  token = data.access_token;
  currentRole = data.role;
  roleBadge.textContent = data.role;
  authStatus.textContent = `Вы вошли как ${data.role}. Telegram ID: ${data.telegram_user_id}.`;
  write("Авторизация", { source: sourceLabel, auth: data });
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

const setOAuthInstructions = (text) => {
  if (!oauthInstructions) {
    return;
  }
  oauthInstructions.textContent = String(text || "").trim() || "Инструкция пока не загружена.";
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

const settingPlaceholders = {
  working_days: "Выберите рабочие дни в календаре выше",
  working_hours: "Выберите рабочие часы выше",
  durations: "Пример: 15,30,45,90",
  min_notice: "Пример: 120",
  buffer: "Пример: 60",
  daily_limit: "Пример: 3",
  horizon: "Пример: 28",
  forbidden_date: "Пример: 2026-06-10 | отпуск",
  forbidden_period: "Выберите дату и период кнопками ниже",
  new_request_text: "Пример: Новая заявка от пользователя",
};

const weekdayAliases = {
  monday: "monday",
  mon: "monday",
  "понедельник": "monday",
  "пн": "monday",
  tuesday: "tuesday",
  tue: "tuesday",
  "вторник": "tuesday",
  "вт": "tuesday",
  wednesday: "wednesday",
  wed: "wednesday",
  "среда": "wednesday",
  "ср": "wednesday",
  thursday: "thursday",
  thu: "thursday",
  "четверг": "thursday",
  "чт": "thursday",
  friday: "friday",
  fri: "friday",
  "пятница": "friday",
  "пт": "friday",
  saturday: "saturday",
  sat: "saturday",
  "суббота": "saturday",
  "сб": "saturday",
  sunday: "sunday",
  sun: "sunday",
  "воскресенье": "sunday",
  "вс": "sunday",
};

const startOfWeekMonday = (sourceDate) => {
  const dayStart = startOfLocalDay(sourceDate);
  const mondayIndex = (dayStart.getDay() + 6) % 7;
  return addDays(dayStart, -mondayIndex);
};

const formatDayMonth = (sourceDate) =>
  new Date(sourceDate).toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
  });

const sortWorkingDays = (days) => {
  const daySet = new Set((Array.isArray(days) ? days : []).map((item) => String(item).toLowerCase()));
  return adminWeekdayOptions
    .map((item) => item.value)
    .filter((value) => daySet.has(value));
};

const setWorkingDaysFromRawValue = (rawValue) => {
  const items = String(rawValue || "")
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  const normalized = items
    .map((item) => weekdayAliases[item] || item)
    .filter((item) => adminWeekdayOptions.some((day) => day.value === item));
  selectedWorkingDays = new Set(sortWorkingDays(normalized));
};

const renderWorkingDaysCalendar = () => {
  if (!settingsWeekDaysContainer) {
    return;
  }

  const weekStart = startOfWeekMonday(new Date());
  settingsWeekDaysContainer.innerHTML = "";

  adminWeekdayOptions.forEach((dayMeta, index) => {
    const date = addDays(weekStart, index);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "week-day-btn setting-day-btn";
    button.innerHTML = `
      <span class="week-day-icon">${dayMeta.icon}</span>
      <span class="week-day-name">${dayMeta.label}</span>
      <span class="week-day-date">${formatDayMonth(date)}</span>
    `;
    if (selectedWorkingDays.has(dayMeta.value)) {
      button.classList.add("active");
    }
    button.addEventListener("click", () => {
      if (selectedWorkingDays.has(dayMeta.value)) {
        selectedWorkingDays.delete(dayMeta.value);
      } else {
        selectedWorkingDays.add(dayMeta.value);
      }
      renderWorkingDaysCalendar();
      syncSettingValueFromStructuredEditors();
    });
    settingsWeekDaysContainer.append(button);
  });
};

const renderWorkingHoursPresets = () => {
  if (!workingHoursPresets) {
    return;
  }

  const currentValue = String(settingValueInput?.value || "").trim();
  workingHoursPresets.innerHTML = "";
  adminWorkingHourOptions.forEach((interval) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "setting-chip-btn";
    button.textContent = interval;
    if (currentValue === interval) {
      button.classList.add("active");
    }
    button.addEventListener("click", () => {
      const [start, end] = interval.split("-");
      if (workdayStartTimeInput) {
        workdayStartTimeInput.value = start;
      }
      if (workdayEndTimeInput) {
        workdayEndTimeInput.value = end;
      }
      syncSettingValueFromStructuredEditors();
      renderWorkingHoursPresets();
      setAdminStatus(`Выбраны рабочие часы: ${interval}. Нажмите «Обновить настройку».`, "info");
    });
    workingHoursPresets.append(button);
  });
};

const sortDurations = (durations) => {
  const durationSet = new Set(
    (Array.isArray(durations) ? durations : [])
      .map((item) => Number(item))
      .filter((item) => Number.isInteger(item) && item > 0)
  );
  const knownDurations = adminDurationOptions.filter((item) => durationSet.has(item));
  const extraDurations = Array.from(durationSet)
    .filter((item) => !adminDurationOptions.includes(item))
    .sort((left, right) => left - right);
  return [...knownDurations, ...extraDurations];
};

const setAdminDurationsFromRawValue = (rawValue) => {
  const durations = String(rawValue || "")
    .split(",")
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isInteger(item) && item > 0);
  selectedAdminDurations = new Set(sortDurations(durations));
};

const renderDurationOptions = () => {
  if (!settingsDurationOptions) {
    return;
  }

  settingsDurationOptions.innerHTML = "";
  adminDurationOptions.forEach((duration) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "setting-chip-btn";
    button.textContent = `${duration} мин`;
    if (selectedAdminDurations.has(duration)) {
      button.classList.add("active");
    }
    button.addEventListener("click", () => {
      if (selectedAdminDurations.has(duration)) {
        selectedAdminDurations.delete(duration);
      } else {
        selectedAdminDurations.add(duration);
      }
      selectedAdminDurations = new Set(sortDurations(Array.from(selectedAdminDurations)));
      syncSettingValueFromStructuredEditors();
      renderDurationOptions();
    });
    settingsDurationOptions.append(button);
  });
};

const setForbiddenDateValue = (dateText) => {
  if (!settingValueInput) {
    return;
  }

  const currentValue = String(settingValueInput.value || "");
  const [, reasonPart = ""] = currentValue.split("|", 2);
  const reason = reasonPart.trim();
  settingValueInput.value = reason ? `${dateText} | ${reason}` : dateText;
};

const renderForbiddenDateOptions = () => {
  if (!forbiddenDateOptions) {
    return;
  }

  const today = startOfLocalDay(new Date());
  const currentDate = String(settingValueInput?.value || "").split("|", 1)[0].trim();
  forbiddenDateOptions.innerHTML = "";

  for (let offset = 0; offset < 14; offset += 1) {
    const day = addDays(today, offset);
    const dateText = toIsoDate(day);
    const dayMeta = formatWeekDayMeta(dateText);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "week-day-btn setting-day-btn";
    button.innerHTML = `
      <span class="week-day-icon">${dayMeta.icon}</span>
      <span class="week-day-name">${dayMeta.name}</span>
      <span class="week-day-date">${dayMeta.date}</span>
    `;
    if (currentDate === dateText) {
      button.classList.add("active");
    }
    button.addEventListener("click", () => {
      setForbiddenDateValue(dateText);
      renderForbiddenDateOptions();
      setAdminStatus(`Выбрана запрещенная дата: ${dateText}. Нажмите «Обновить настройку».`, "info");
    });
    forbiddenDateOptions.append(button);
  }
};

const getWorkdayTimeRange = () => [
  String(workdayStartTimeInput?.value || "10:00").trim() || "10:00",
  String(workdayEndTimeInput?.value || "18:00").trim() || "18:00",
];

const getReasonFromSettingValue = () => {
  const [, reasonPart = ""] = String(settingValueInput?.value || "").split("|", 2);
  return reasonPart.trim();
};

const parseForbiddenPeriodDateFromValue = () => {
  const match = String(settingValueInput?.value || "").match(/^(\d{4}-\d{2}-\d{2})\s+/);
  return match ? match[1] : "";
};

const getForbiddenPeriodInterval = (presetCode) => {
  const option = adminForbiddenPeriodOptions.find((item) => item.value === presetCode);
  if (!option) {
    return getWorkdayTimeRange();
  }
  return option.interval || getWorkdayTimeRange();
};

const setForbiddenPeriodValue = (dateText, presetCode) => {
  if (!settingValueInput || !dateText) {
    return;
  }

  const [start, end] = getForbiddenPeriodInterval(presetCode);
  const reason = getReasonFromSettingValue();
  const periodValue = `${dateText} ${start} - ${dateText} ${end}`;
  settingValueInput.value = reason ? `${periodValue} | ${reason}` : periodValue;
};

const renderForbiddenPeriodDateOptions = () => {
  if (!forbiddenPeriodDateOptions) {
    return;
  }

  const today = startOfLocalDay(new Date());
  const currentDate =
    selectedForbiddenPeriodDate || parseForbiddenPeriodDateFromValue() || toIsoDate(today);
  selectedForbiddenPeriodDate = currentDate;
  forbiddenPeriodDateOptions.innerHTML = "";

  for (let offset = 0; offset < 14; offset += 1) {
    const day = addDays(today, offset);
    const dateText = toIsoDate(day);
    const dayMeta = formatWeekDayMeta(dateText);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "week-day-btn setting-day-btn";
    button.innerHTML = `
      <span class="week-day-icon">${dayMeta.icon}</span>
      <span class="week-day-name">${dayMeta.name}</span>
      <span class="week-day-date">${dayMeta.date}</span>
    `;
    if (currentDate === dateText) {
      button.classList.add("active");
    }
    button.addEventListener("click", () => {
      selectedForbiddenPeriodDate = dateText;
      setForbiddenPeriodValue(selectedForbiddenPeriodDate, selectedForbiddenPeriodPreset);
      renderForbiddenPeriodDateOptions();
      renderForbiddenPeriodTimeOptions();
      setAdminStatus(
        `Выбрана дата периода: ${dateText}. Выберите период и нажмите «Обновить настройку».`,
        "info"
      );
    });
    forbiddenPeriodDateOptions.append(button);
  }
};

const renderForbiddenPeriodTimeOptions = () => {
  if (!forbiddenPeriodTimeOptions) {
    return;
  }

  forbiddenPeriodTimeOptions.innerHTML = "";
  adminForbiddenPeriodOptions.forEach((option) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "setting-chip-btn";
    const [start, end] = getForbiddenPeriodInterval(option.value);
    button.textContent =
      option.value === "workday" ? `${option.label} ${start}-${end}` : option.label;
    if (selectedForbiddenPeriodPreset === option.value) {
      button.classList.add("active");
    }
    button.addEventListener("click", () => {
      selectedForbiddenPeriodPreset = option.value;
      if (!selectedForbiddenPeriodDate) {
        selectedForbiddenPeriodDate = toIsoDate(startOfLocalDay(new Date()));
      }
      setForbiddenPeriodValue(selectedForbiddenPeriodDate, selectedForbiddenPeriodPreset);
      renderForbiddenPeriodTimeOptions();
      setAdminStatus(
        `Выбран период: ${button.textContent}. Нажмите «Обновить настройку».`,
        "info"
      );
    });
    forbiddenPeriodTimeOptions.append(button);
  });
};

const syncSettingValueFromStructuredEditors = () => {
  const key = String(settingKeyInput?.value || "").trim();
  if (!key || !settingValueInput) {
    return;
  }

  if (key === "working_days") {
    const orderedDays = sortWorkingDays(Array.from(selectedWorkingDays));
    settingValueInput.value = orderedDays.join(",");
    return;
  }

  if (key === "working_hours") {
    const start = String(workdayStartTimeInput?.value || "").trim();
    const end = String(workdayEndTimeInput?.value || "").trim();
    settingValueInput.value = start && end ? `${start}-${end}` : "";
    return;
  }

  if (key === "durations") {
    const orderedDurations = sortDurations(Array.from(selectedAdminDurations));
    settingValueInput.value = orderedDurations.join(",");
    return;
  }

  if (key === "forbidden_period") {
    if (!selectedForbiddenPeriodDate) {
      selectedForbiddenPeriodDate = parseForbiddenPeriodDateFromValue();
    }
    if (selectedForbiddenPeriodDate) {
      setForbiddenPeriodValue(selectedForbiddenPeriodDate, selectedForbiddenPeriodPreset);
    }
  }
};

const applySettingEditorMode = () => {
  const key = String(settingKeyInput?.value || "").trim();
  const isWorkingDays = key === "working_days";
  const isWorkingHours = key === "working_hours";
  const isDurations = key === "durations";
  const isForbiddenDate = key === "forbidden_date";
  const isForbiddenPeriod = key === "forbidden_period";

  workingDaysCalendarEditor?.classList.toggle("hidden", !isWorkingDays);
  workingHoursEditor?.classList.toggle("hidden", !isWorkingHours);
  settingsDurationsEditor?.classList.toggle("hidden", !isDurations);
  forbiddenDateEditor?.classList.toggle("hidden", !isForbiddenDate);
  forbiddenPeriodEditor?.classList.toggle("hidden", !isForbiddenPeriod);

  if (settingValueLabel) {
    settingValueLabel.textContent =
      isWorkingDays || isWorkingHours || isDurations
        ? "Значение (автоматически)"
        : "Значение";
  }

  if (settingValueInput) {
    settingValueInput.readOnly = isWorkingDays || isWorkingHours || isDurations;
    settingValueInput.placeholder = settingPlaceholders[key] || "Введите значение настройки";
  }

  if (settingValueHint) {
    settingValueHint.classList.toggle(
      "hidden",
      !(isWorkingDays || isWorkingHours || isDurations || isForbiddenDate || isForbiddenPeriod)
    );
  }

  if (isWorkingDays) {
    if (selectedWorkingDays.size === 0) {
      setWorkingDaysFromRawValue(settingValueInput.value);
    }
    renderWorkingDaysCalendar();
  }

  if (isWorkingHours) {
    const currentValue = String(settingValueInput.value || "").trim();
    const match = currentValue.match(/^(\d{2}:\d{2})-(\d{2}:\d{2})$/);
    if (match) {
      workdayStartTimeInput.value = match[1];
      workdayEndTimeInput.value = match[2];
    }
    renderWorkingHoursPresets();
  }

  if (isDurations) {
    if (selectedAdminDurations.size === 0) {
      setAdminDurationsFromRawValue(settingValueInput.value);
    }
    renderDurationOptions();
  }

  if (isForbiddenDate) {
    renderForbiddenDateOptions();
  }

  if (isForbiddenPeriod) {
    selectedForbiddenPeriodDate =
      parseForbiddenPeriodDateFromValue() || selectedForbiddenPeriodDate;
    renderForbiddenPeriodDateOptions();
    renderForbiddenPeriodTimeOptions();
  }

  syncSettingValueFromStructuredEditors();
};

const applyAdminSettingsSnapshot = (settingsPayload) => {
  if (!settingsPayload || typeof settingsPayload !== "object") {
    return;
  }

  const workingDays = sortWorkingDays(settingsPayload.working_days);
  selectedWorkingDays = new Set(workingDays);

  if (workdayStartTimeInput && typeof settingsPayload.workday_start === "string") {
    workdayStartTimeInput.value = settingsPayload.workday_start;
  }
  if (workdayEndTimeInput && typeof settingsPayload.workday_end === "string") {
    workdayEndTimeInput.value = settingsPayload.workday_end;
  }
  if (Array.isArray(settingsPayload.available_durations_minutes)) {
    selectedAdminDurations = new Set(sortDurations(settingsPayload.available_durations_minutes));
  }

  applySettingEditorMode();
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

const normalizeDurationValues = (durations) => {
  const values = (Array.isArray(durations) ? durations : [])
    .map((item) => Number(item))
    .filter((item) => Number.isInteger(item) && item > 0);
  return values.length > 0 ? values : [30];
};

const hasSelectableDurationOption = () =>
  Array.from(durationInput.options).some((option) => Number(option.value || 0) > 0);

const fillDurationOptions = (durations) => {
  const currentValue = Number(durationInput.value || 0);
  const values = normalizeDurationValues(durations);
  durationInput.innerHTML = "";

  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = String(value);
    if (value === currentValue) {
      option.selected = true;
    }
    durationInput.append(option);
  });

  if (!durationInput.value) {
    durationInput.value = String(values[0]);
  }
};

const setBookingStepState = (hasDateSelected) => {
  if (hasDateSelected && !hasSelectableDurationOption()) {
    fillDurationOptions(bookingConfig?.available_durations_minutes);
  }

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
  fillDurationOptions(durations);
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

  setBookingStepState(true);
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

const loadOAuthInstructions = async ({ logEvent = true } = {}) => {
  ensureAdminRole();
  const data = await api("/admin/google/oauth/url");
  const instructions = String(data.instructions || "").trim();
  setOAuthInstructions(instructions);
  if (logEvent) {
    write("Google OAuth инструкция", data);
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
      await loadOAuthInstructions({ logEvent: false });
      setAdminStatus("Админ-очередь и OAuth-инструкция загружены.", "success");
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
  syncSettingValueFromStructuredEditors();

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
  try {
    const data = await api("/admin/google/oauth/exchange", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
    const successMessage = String(data.status || "").trim() || "Код OAuth успешно обработан.";
    write("Google OAuth exchange", data);
    setAdminStatus(successMessage, "success");
    return data;
  } catch (error) {
    const baseMessage = String(error.message || "Не удалось выполнить OAuth reconnect.");
    const reconnectHint = [
      baseMessage,
      "Проверьте, что вы вставили либо `code`, либо полный callback URL из адресной строки.",
      "Если ошибка про `refresh_token`, нажмите «Показать инструкцию OAuth», повторно откройте OAuth URL и подтвердите доступ.",
    ].join("\n");
    setAdminStatus(reconnectHint, "error");
    write("Google OAuth exchange error", { error: baseMessage });
    return null;
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
    if (currentRole === "admin") {
      await loadAdminRequests({ logEvent: false });
    }
    setBookingStatus(`Заявка #${requestId} отменена.`, "success");
  },
};

const authenticateWithTelegram = async () => {
  if (!telegramInitData) {
    return false;
  }

  try {
    telegramWebApp?.ready?.();
    telegramWebApp?.expand?.();
    authStatus.textContent = "Входим через Telegram...";

    const data = await api("/auth/telegram", {
      method: "POST",
      body: JSON.stringify({ init_data: telegramInitData }),
    });

    devLoginControls?.classList.add("hidden");
    if (authHint) {
      authHint.textContent = "Вход выполнен через Telegram.";
    }
    applyAuthResponse(data, "telegram");
    await initializeAfterLogin();
    return true;
  } catch (error) {
    authStatus.textContent = `Не удалось войти через Telegram: ${error.message}`;
    write("Ошибка Telegram-авторизации", { error: error.message });
    return false;
  }
};

const configureAuthPanel = async () => {
  if (telegramInitData) {
    devLoginControls?.classList.add("hidden");
    if (authHint) {
      authHint.textContent = "Проверяем подпись Telegram и выполняем вход.";
    }
    await authenticateWithTelegram();
    return;
  }

  if (isLocalHost) {
    devLoginControls?.classList.remove("hidden");
    if (authHint) {
      authHint.textContent = "Локальная dev-проверка: введите Telegram ID и нажмите «Войти».";
    }
    authStatus.textContent = "Dev login доступен только при включенной настройке окружения.";
    return;
  }

  devLoginControls?.classList.add("hidden");
  authStatus.textContent = "Откройте Mini App через кнопку меню в @plangoogle_bot. Вход по Telegram ID отключен в проде.";
};

devLoginButton?.addEventListener("click", async () => {
  let telegramUserId;
  try {
    telegramUserId = parseTelegramUserIdInput(telegramIdInput.value);
  } catch (error) {
    authStatus.textContent = error.message;
    return;
  }

  try {
    const data = await api("/auth/dev-login", {
      method: "POST",
      body: JSON.stringify({ telegram_user_id: telegramUserId }),
    });

    applyAuthResponse(data, "dev-login");
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

settingKeyInput?.addEventListener("change", () => {
  applySettingEditorMode();

  const key = String(settingKeyInput.value || "").trim();
  if (key === "working_days") {
    setAdminStatus("Выберите рабочие дни прямо в календаре ниже.", "info");
  } else if (key === "working_hours") {
    setAdminStatus("Выберите готовый интервал кнопкой или укажите время вручную.", "info");
  } else if (key === "durations") {
    setAdminStatus("Выберите длительности кнопками ниже.", "info");
  } else if (key === "forbidden_date") {
    setAdminStatus("Выберите ближайшую запрещенную дату кнопкой или введите дату вручную.", "info");
  } else if (key === "forbidden_period") {
    setAdminStatus("Выберите дату и период кнопками ниже.", "info");
  }
});

workdayStartTimeInput?.addEventListener("change", () => {
  syncSettingValueFromStructuredEditors();
  renderWorkingHoursPresets();
  if (String(settingKeyInput?.value || "").trim() === "forbidden_period") {
    renderForbiddenPeriodTimeOptions();
  }
});

workdayEndTimeInput?.addEventListener("change", () => {
  syncSettingValueFromStructuredEditors();
  renderWorkingHoursPresets();
  if (String(settingKeyInput?.value || "").trim() === "forbidden_period") {
    renderForbiddenPeriodTimeOptions();
  }
});

settingValueInput?.addEventListener("input", () => {
  const key = String(settingKeyInput?.value || "").trim();
  if (key === "forbidden_date") {
    renderForbiddenDateOptions();
  }
  if (key === "forbidden_period") {
    selectedForbiddenPeriodDate = parseForbiddenPeriodDateFromValue() || selectedForbiddenPeriodDate;
    renderForbiddenPeriodDateOptions();
  }
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
          {
            const settingsData = await api("/admin/settings");
            applyAdminSettingsSnapshot(settingsData);
            write("Настройки расписания (admin)", settingsData);
          }
          setAdminStatus("Текущие настройки загружены.", "success");
          break;
        case "admin-settings-update":
          await updateAdminSettings();
          break;
        case "admin-oauth-url":
          await loadOAuthInstructions();
          setAdminStatus("Инструкция OAuth загружена и обновлена в форме.", "success");
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

applySettingEditorMode();
void configureAuthPanel();
