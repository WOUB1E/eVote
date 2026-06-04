const { useCallback, useEffect, useMemo, useRef, useState } = React;

const AUTH_KEY = "evote_auth";
const VK_AUTH_KEY = "evote_vk_oauth";
const THEME_KEY = "evote_theme";
const MAX_OPTIONS = 50;
const MAX_IMAGES_PER_FIELD = 5;
const DASHBOARD_PAGE_SIZE = 8;
const navItems = [
    ["dashboard", "Панель", "layout-dashboard"],
    ["editor", "Создать", "square-pen"],
    ["profile", "Профиль", "circle-user-round"],
    ["support", "Поддержка", "message-circle-question"],
    ["admin", "Админ", "shield-check"],
];

function cx(...items) {
    return items.filter(Boolean).join(" ");
}

function readAuth() {
    try {
        return JSON.parse(localStorage.getItem(AUTH_KEY) || "null");
    } catch {
        return null;
    }
}

function persistAuth(value) {
    if (value) {
        localStorage.setItem(AUTH_KEY, JSON.stringify(value));
    } else {
        localStorage.removeItem(AUTH_KEY);
    }
}

function firstImage(...values) {
    for (const value of values) {
        if (Array.isArray(value)) {
            const found = value.find(Boolean);
            if (found) {
                return found;
            }
        } else if (value) {
            return value;
        }
    }
    return null;
}

function cloudinaryVariantUrl(src, transformation) {
    if (!src || !transformation || !src.includes("/image/upload/")) {
        return src;
    }
    const marker = "/image/upload/";
    const [base, rest] = src.split(marker);
    if (!base || !rest) {
        return src;
    }
    return `${base}${marker}${transformation}/${rest}`;
}

function decodeUrlSafeJson(value) {
    const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const bytes = Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
}

function randomUrlToken(byteLength = 48) {
    const bytes = new Uint8Array(byteLength);
    if (window.crypto?.getRandomValues) {
        window.crypto.getRandomValues(bytes);
    } else {
        bytes.forEach((_, index) => {
            bytes[index] = Math.floor(Math.random() * 256);
        });
    }
    const raw = Array.from(bytes, (byte) => String.fromCharCode(byte)).join("");
    return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function readVkAuthState() {
    try {
        const value = JSON.parse(localStorage.getItem(VK_AUTH_KEY) || "null");
        if (!value?.state || !value?.codeVerifier || Number(value.expiresAt || 0) <= Date.now()) {
            localStorage.removeItem(VK_AUTH_KEY);
            return null;
        }
        return value;
    } catch {
        localStorage.removeItem(VK_AUTH_KEY);
        return null;
    }
}

function getOrCreateVkAuthState(force = false) {
    if (!force) {
        const existing = readVkAuthState();
        if (existing) {
            return existing;
        }
    }
    const value = {
        state: randomUrlToken(36),
        codeVerifier: randomUrlToken(64),
        expiresAt: Date.now() + 30 * 60 * 1000,
    };
    localStorage.setItem(VK_AUTH_KEY, JSON.stringify(value));
    return value;
}

function clearVkAuthState() {
    localStorage.removeItem(VK_AUTH_KEY);
}

function errorText(error, fallback) {
    if (error instanceof Error && error.message) {
        return error.message;
    }
    if (typeof error === "string" && error.trim()) {
        return error;
    }
    if (error && typeof error === "object") {
        const details = [error.error_description, error.error, error.text].filter(Boolean).join(": ");
        if (details) {
            return details;
        }
        try {
            const serialized = JSON.stringify(error);
            if (serialized && serialized !== "{}") {
                return serialized;
            }
        } catch {
            // ignore non-serializable SDK errors
        }
    }
    return fallback;
}

function readTheme() {
    return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
}

function toLocalInputValue(days = 7) {
    const date = new Date(Date.now() + days * 24 * 60 * 60 * 1000);
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function createInitialPollForm() {
    return {
        title: "",
        description: "",
        description_image: null,
        description_image_url: "",
        description_images: [],
        description_image_urls: [],
        access_type: "public",
        poll_type: "single",
        anonymity_level: "0",
        results_visibility: "after_end",
        max_votes: "",
        is_infinite: false,
        ends_at: toLocalInputValue(),
        options: [{ text: "", image: null, image_url: "", images: [], image_urls: [] }],
    };
}

function formatDate(value) {
    if (!value) {
        return "Не задано";
    }
    return new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    }).format(new Date(value));
}

function formatDateOnly(value) {
    if (!value) {
        return "не указана";
    }
    return new Intl.DateTimeFormat("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
    }).format(new Date(value));
}

function pollEndLabel(poll) {
    return poll?.ends_at ? formatDate(poll.ends_at) : "Бессрочно";
}

function pluralizeRu(value, one, few, many) {
    const absValue = Math.abs(value);
    const lastTwo = absValue % 100;
    const lastOne = absValue % 10;
    if (lastTwo >= 11 && lastTwo <= 19) {
        return many;
    }
    if (lastOne === 1) {
        return one;
    }
    if (lastOne >= 2 && lastOne <= 4) {
        return few;
    }
    return many;
}

function pollCountdownLabel(poll, now = Date.now()) {
    if (!poll?.ends_at) {
        return "Бессрочно";
    }
    const target = new Date(poll.ends_at).getTime();
    const diff = target - now;
    if (!Number.isFinite(target) || diff <= 0) {
        return "Завершён";
    }

    const minutes = Math.floor(diff / 60000);
    if (minutes < 60) {
        const value = Math.max(1, minutes);
        return `${value} ${pluralizeRu(value, "минута", "минуты", "минут")}`;
    }

    const hours = Math.floor(minutes / 60);
    if (hours < 24) {
        return `${hours} ${pluralizeRu(hours, "час", "часа", "часов")}`;
    }

    const days = Math.floor(hours / 24);
    return `${days} ${pluralizeRu(days, "день", "дня", "дней")}`;
}

function paginationItems(page, totalPages) {
    if (totalPages <= 7) {
        return Array.from({ length: totalPages }, (_, index) => index + 1);
    }

    const items = [1];
    const start = Math.max(2, page - 1);
    const end = Math.min(totalPages - 1, page + 1);

    if (start > 2) {
        items.push("ellipsis-start");
    }
    for (let index = start; index <= end; index += 1) {
        items.push(index);
    }
    if (end < totalPages - 1) {
        items.push("ellipsis-end");
    }
    items.push(totalPages);
    return items;
}

function accessLabel(value) {
    return {
        public: "Публичный",
        link: "По ссылке",
        limited: "Лимит",
    }[value] || "Доступ";
}

function anonymityLabel(level) {
    return {
        0: "Открытый",
        1: "Полу-анонимный",
        2: "Анонимный",
    }[Number(level)] || "Анонимность";
}

function genderLabel(value) {
    return {
        female: "женский",
        male: "мужской",
        other: "другое",
    }[value] || "не указан";
}

function authProviderLabel(value) {
    return {
        vk: "VK",
        yandex: "Я",
    }[value] || "";
}

function authProviderTitle(value) {
    return {
        vk: "Вход через VK ID",
        yandex: "Вход через Яндекс ID",
    }[value] || "";
}

function voterAgeMatches(age, filter) {
    if (filter === "all") {
        return true;
    }
    if (filter === "unknown") {
        return age === null || age === undefined;
    }
    if (typeof age !== "number") {
        return false;
    }
    return {
        "under18": age < 18,
        "18-24": age >= 18 && age <= 24,
        "25-34": age >= 25 && age <= 34,
        "35-44": age >= 35 && age <= 44,
        "45plus": age >= 45,
    }[filter] || true;
}

function voterMatchesFilters(voter, filters) {
    const genderOk = filters.gender === "all" || (filters.gender === "unknown" ? !voter.gender : voter.gender === filters.gender);
    return genderOk && voterAgeMatches(voter.age, filters.age);
}

function resultsVisibilityLabel(value) {
    return {
        always: "После голосования",
        after_end: "После голосования",
        manual: "После публикации",
        hidden: "Скрыты",
    }[value] || "Публикация";
}

function auditActionLabel(value) {
    return {
        created: "Создание",
        completed: "Завершение",
        activated: "Активация",
        archived: "Архивация",
        vote_cast: "Голос",
        results_settings_updated: "Публикация результатов",
    }[value] || value;
}

function pollStatus(poll) {
    if (poll.is_archived) {
        return { key: "archived", label: "В архиве", tone: "red", icon: "archive" };
    }
    if (!poll.is_active) {
        return { key: "stopped", label: "Остановлен", tone: "red", icon: "pause-circle" };
    }
    if (poll.has_ended) {
        return { key: "closed", label: "Завершён", tone: "red", icon: "circle-stop" };
    }
    if (poll.access_type === "limited" && poll.spots_left === 0) {
        return { key: "full", label: "Лимит исчерпан", tone: "amber", icon: "users-round" };
    }
    if (poll.access_type === "link") {
        return { key: "link", label: "По ссылке", tone: "blue", icon: "link" };
    }
    return { key: "active", label: "Активен", tone: "green", icon: "radio" };
}

function pollShareUrl(poll) {
    return `${window.location.origin}/poll/${poll.code}`;
}

function pollFlags(poll) {
    const status = pollStatus(poll);
    const anonymityIcons = { 0: "users-round", 1: "user-round", 2: "lock-keyhole" };
    const accessIcons = { public: "globe-2", link: "link", limited: "user-round-cog" };
    const flags = [
        status,
        {
            key: "anonymity",
            label: `Анонимность: ${anonymityLabel(poll.anonymity_level)}`,
            tone: Number(poll.anonymity_level) === 0 ? "blue" : Number(poll.anonymity_level) === 1 ? "amber" : "neutral",
            icon: anonymityIcons[Number(poll.anonymity_level)] || "shield-question",
        },
        {
            key: "results",
            label: `Результаты: ${resultsVisibilityLabel(poll.results_visibility)}${poll.results_visible ? ". Сейчас доступны" : ". Сейчас скрыты"}`,
            tone: poll.results_visible ? "green" : "neutral",
            icon: poll.results_visible ? "eye" : "eye-off",
        },
        {
            key: "access",
            label: `Доступ: ${accessLabel(poll.access_type)}${poll.max_votes ? ` · участников ${poll.participants}/${poll.max_votes}` : ""}`,
            tone: poll.access_type === "limited" ? "amber" : poll.access_type === "link" ? "blue" : "neutral",
            icon: accessIcons[poll.access_type] || "door-open",
        },
    ];
    return flags;
}

function pollCodeFromPath() {
    const match = window.location.pathname.match(/^\/poll\/([^/?#]+)/);
    return match ? decodeURIComponent(match[1]) : null;
}

function initialViewFromPath() {
    if (window.location.pathname === "/terms") {
        return "terms";
    }
    if (window.location.pathname === "/privacy") {
        return "privacy";
    }
    if (window.location.pathname === "/debug") {
        return "debug";
    }
    return "dashboard";
}

function Icon({ name, size = 18 }) {
    useEffect(() => {
        window.lucide?.createIcons({ attrs: { class: "icon" } });
    }, [name]);
    return <i data-lucide={name} style={{ width: size, height: size }} aria-hidden="true"></i>;
}

function useIcons() {
    useEffect(() => {
        window.lucide?.createIcons({ attrs: { class: "icon" } });
    });
}

async function requestJson(path, { method = "GET", auth, body } = {}) {
    const headers = {};
    if (body !== undefined) {
        headers["Content-Type"] = "application/json";
    }
    if (auth?.token) {
        headers.Authorization = `Bearer ${auth.token}`;
    }
    if (auth?.csrf && method !== "GET") {
        headers["X-CSRF-Token"] = auth.csrf;
    }

    const response = await fetch(path, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const details = Array.isArray(payload.details) ? payload.details.join(" ") : "";
        throw new Error(details || payload.error || "Запрос не выполнен.");
    }
    return payload;
}

function loadExternalScript(src) {
    return new Promise((resolve, reject) => {
        const existing = document.querySelector(`script[src="${src}"]`);
        if (existing) {
            if (existing.dataset.loaded === "true") {
                resolve();
            } else {
                existing.addEventListener("load", resolve, { once: true });
                existing.addEventListener("error", reject, { once: true });
            }
            return;
        }
        const script = document.createElement("script");
        script.src = src;
        script.async = true;
        script.dataset.loaded = "false";
        script.onload = () => {
            script.dataset.loaded = "true";
            resolve();
        };
        script.onerror = () => reject(new Error("Не удалось загрузить VK ID SDK."));
        document.head.appendChild(script);
    });
}

async function getVkSdk(config, authState = getOrCreateVkAuthState()) {
    if (!config?.vk_sdk_url) {
        throw new Error("VK ID не настроен.");
    }
    await loadExternalScript(config.vk_sdk_url);
    const VKID = window.VKIDSDK;
    if (!VKID) {
        throw new Error("Не удалось загрузить VK ID SDK.");
    }
    const initConfig = {
        app: Number(config.vk_client_id),
        redirectUrl: config.vk_redirect_uri,
        mode: VKID.ConfigAuthMode.InNewTab,
        responseMode: VKID.ConfigResponseMode.Callback,
        source: VKID.ConfigSource.LOWCODE,
        scope: "",
    };
    if (authState?.state) {
        initConfig.state = authState.state;
    }
    if (authState?.codeVerifier) {
        initConfig.codeVerifier = authState.codeVerifier;
    }
    VKID.Config.init(initConfig);
    return VKID;
}

async function exchangeVkAuthCode(config, code, deviceId, responseState = "") {
    if (!code || !deviceId) {
        throw new Error("VK ID не вернул код авторизации.");
    }
    const authState = readVkAuthState();
    if (!authState?.codeVerifier) {
        throw new Error("Не удалось подтвердить VK ID-сессию. Откройте окно входа и попробуйте снова.");
    }
    if (responseState && authState.state !== responseState) {
        clearVkAuthState();
        throw new Error("Не удалось подтвердить VK ID-сессию. Откройте окно входа и попробуйте снова.");
    }

    const VKID = await getVkSdk(config, authState);
    try {
        const token = await VKID.Auth.exchangeCode(code, deviceId, authState.codeVerifier);
        const info = await VKID.Auth.userInfo(token.access_token);
        clearVkAuthState();
        return { token, user: info.user || info || {} };
    } catch (error) {
        clearVkAuthState();
        throw new Error(errorText(error, "Не удалось выполнить обмен кода VK ID."));
    }
}

async function requestForm(path, { method = "POST", auth, body } = {}) {
    const headers = {};
    if (auth?.token) {
        headers.Authorization = `Bearer ${auth.token}`;
    }
    if (auth?.csrf && method !== "GET") {
        headers["X-CSRF-Token"] = auth.csrf;
    }

    const response = await fetch(path, { method, headers, body });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const details = Array.isArray(payload.details) ? payload.details.join(" ") : "";
        throw new Error(details || payload.error || "Запрос не выполнен.");
    }
    return payload;
}

async function requestBlob(path, { auth } = {}) {
    const headers = {};
    if (auth?.token) {
        headers.Authorization = `Bearer ${auth.token}`;
    }
    const response = await fetch(path, { headers });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || "Экспорт не выполнен.");
    }
    return response.blob();
}

function Badge({ tone = "neutral", children }) {
    return <span className={cx("badge", `badge--${tone}`)}>{children}</span>;
}

function FlagIcon({ flag }) {
    return (
        <span className={cx("flag-icon", `flag-icon--${flag.tone}`)} title={flag.label} aria-label={flag.label}>
            <Icon name={flag.icon} size={16} />
        </span>
    );
}

function RequiredMark() {
    return <span className="required-mark" title="Обязательное поле">*</span>;
}

function DateTimePicker({ value, onChange, required = false }) {
    const inputRef = useRef(null);
    const openPicker = () => {
        const input = inputRef.current;
        if (!input) {
            return;
        }
        input.focus();
        if (typeof input.showPicker === "function") {
            try {
                input.showPicker();
            } catch {
                // Some browsers allow showPicker only during direct user gestures.
            }
        }
    };

    return (
        <div className="datetime-field">
            <input
                ref={inputRef}
                className="input"
                type="datetime-local"
                value={value}
                min={toLocalInputValue(0)}
                required={required}
                onClick={openPicker}
                onChange={(event) => onChange(event.target.value)}
            />
            <button className="icon-button" type="button" onClick={openPicker} title="Выбрать дату и время">
                <Icon name="calendar-days" />
            </button>
        </div>
    );
}

function EmptyState({ icon = "inbox", title, children }) {
    return (
        <div className="empty">
            <Icon name={icon} size={22} />
            <strong>{title}</strong>
            {children ? <span>{children}</span> : null}
        </div>
    );
}

function Avatar({ user, username, image, size = "sm" }) {
    const name = user?.username ?? username ?? "";
    const imageUrl = user?.profile_image ?? image;
    const initials = (name || "?").slice(0, 2).toUpperCase();

    return (
        <span className={cx("avatar", `avatar--${size}`)} aria-hidden="true">
            {imageUrl ? <img src={imageUrl} alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} /> : <span>{initials}</span>}
            {imageUrl ? <span className="avatar__fallback">{initials}</span> : null}
        </span>
    );
}

function UserLink({ user, userId, username, profileImage, authProvider, onOpen }) {
    const id = user?.id ?? userId;
    const name = user?.username ?? username ?? "Профиль";
    const provider = user?.auth_provider ?? authProvider;
    const providerLabel = authProviderLabel(provider);
    const content = (
        <>
            <Avatar user={user} username={name} image={profileImage} />
            <span>{name}</span>
            {providerLabel ? <span className={cx("auth-provider-badge", `auth-provider-badge--${provider}`)} title={authProviderTitle(provider)}>{providerLabel}</span> : null}
        </>
    );
    if (!id || !onOpen) {
        return <span className="user-link user-link--static">{content}</span>;
    }

    return (
        <a
            className="user-link"
            href={`#user-${id}`}
            onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onOpen(id);
            }}
        >
            {content}
        </a>
    );
}

function App() {
    useIcons();
    const [auth, setAuth] = useState(readAuth());
    const [theme, setTheme] = useState(readTheme());
    const [view, setView] = useState(initialViewFromPath());
    const [polls, setPolls] = useState([]);
    const [users, setUsers] = useState([]);
    const [activity, setActivity] = useState([]);
    const [supportTickets, setSupportTickets] = useState([]);
    const [adminReports, setAdminReports] = useState([]);
    const [adminTickets, setAdminTickets] = useState([]);
    const [activePoll, setActivePoll] = useState(null);
    const [profileUser, setProfileUser] = useState(null);
    const [reportTarget, setReportTarget] = useState(null);
    const [authModal, setAuthModal] = useState({ open: false, mode: "login" });
    const [authModalError, setAuthModalError] = useState("");
    const [authTargetView, setAuthTargetView] = useState(null);
    const [adminMenuRequest, setAdminMenuRequest] = useState(null);
    const [status, setStatus] = useState({ type: "info", text: "" });
    const [loading, setLoading] = useState(false);
    const [editorDirty, setEditorDirty] = useState(false);
    const [clockNow, setClockNow] = useState(() => Date.now());

    const notify = useCallback((text, type = "info") => setStatus({ text, type }), []);

    const updateAuth = useCallback((nextAuth) => {
        persistAuth(nextAuth);
        setAuth(nextAuth);
    }, []);

    const openAuthModal = useCallback((targetView = null) => {
        setAuthTargetView(targetView);
        setAuthModalError("");
        setAuthModal({ open: true, mode: "login" });
    }, []);

    const scrollToTop = useCallback(() => {
        window.requestAnimationFrame(() => {
            window.scrollTo({ top: 0, left: 0, behavior: "smooth" });
        });
    }, []);

    useEffect(() => {
        if (!window.location.hash) {
            return;
        }
        const params = new URLSearchParams(window.location.hash.slice(1));
        const authPayload = params.get("yandex_auth");
        const vkPayload = params.get("vk_auth");
        const authError = params.get("auth_error");

        if (authPayload) {
            try {
                const data = decodeUrlSafeJson(authPayload);
                updateAuth({ token: data.token, csrf: data.csrf_token, user: data.user });
                setAuthModalError("");
                setAuthModal({ open: false, mode: "login" });
                setAuthTargetView(null);
                setView("dashboard");
                notify("Вход через Яндекс выполнен.", "success");
            } catch {
                notify("Не удалось обработать ответ Яндекса.", "danger");
            }
            window.history.replaceState({}, "", "/");
            scrollToTop();
            return;
        }

        if (vkPayload) {
            (async () => {
                setLoading(true);
                try {
                    const data = decodeUrlSafeJson(vkPayload);
                    const config = await requestJson("/api/auth/config");
                    const vkAuth = await exchangeVkAuthCode(config, data.code, data.device_id, data.state);
                    const authData = await requestJson("/api/auth/vk", { method: "POST", body: vkAuth });
                    updateAuth({ token: authData.token, csrf: authData.csrf_token, user: authData.user });
                    setAuthModalError("");
                    setAuthModal({ open: false, mode: "login" });
                    setAuthTargetView(null);
                    setView("dashboard");
                    notify("Вход через VK ID выполнен.", "success");
                } catch (error) {
                    notify(errorText(error, "Не удалось обработать ответ VK ID."), "danger");
                } finally {
                    setLoading(false);
                    window.history.replaceState({}, "", "/");
                    scrollToTop();
                }
            })();
            return;
        }

        if (authError) {
            notify(authError, "danger");
            window.history.replaceState({}, "", "/");
        }
    }, [notify, scrollToTop, updateAuth]);

    const confirmLeaveEditor = useCallback(() => {
        if (view !== "editor" || !editorDirty) {
            return true;
        }
        return window.confirm("Форма создания опроса не сохранена. Покинуть страницу и потерять введённые данные?");
    }, [view, editorDirty]);

    useEffect(() => {
        document.documentElement.dataset.theme = theme;
        localStorage.setItem(THEME_KEY, theme);
    }, [theme]);

    useEffect(() => {
        const timer = window.setInterval(() => setClockNow(Date.now()), 60000);
        return () => window.clearInterval(timer);
    }, []);

    useEffect(() => {
        if (!status.text) {
            return undefined;
        }
        const timer = window.setTimeout(() => {
            setStatus({ type: "info", text: "" });
        }, 4200);
        return () => window.clearTimeout(timer);
    }, [status.text, status.type]);

    useEffect(() => {
        if (!editorDirty) {
            return undefined;
        }
        const handleBeforeUnload = (event) => {
            event.preventDefault();
            event.returnValue = "";
        };
        window.addEventListener("beforeunload", handleBeforeUnload);
        return () => window.removeEventListener("beforeunload", handleBeforeUnload);
    }, [editorDirty]);

    const loadPolls = useCallback(async (silent = false) => {
        if (!silent) {
            setLoading(true);
        }
        try {
            const data = await requestJson("/api/polls", { auth });
            const nextPolls = data.polls || [];
            setPolls(nextPolls);
            if (activePoll) {
                const updated = nextPolls.find((poll) => poll.code === activePoll.code);
                if (updated) {
                    try {
                        const details = await requestJson(`/api/polls/${activePoll.code}`, { auth });
                        setActivePoll(details.poll);
                    } catch {
                        setActivePoll(updated);
                    }
                }
            }
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    }, [auth?.token, activePoll?.code]);

    const loadActivity = useCallback(async () => {
        if (!auth) {
            setActivity([]);
            return;
        }
        try {
            const data = await requestJson("/api/activity", { auth });
            setActivity(data.activity || []);
        } catch {
            setActivity([]);
        }
    }, [auth?.token]);

    const loadUsers = useCallback(async () => {
        if (!auth?.user || auth.user.role !== "admin") {
            setUsers([]);
            return;
        }
        try {
            const data = await requestJson("/api/users", { auth });
            setUsers(data.users || []);
        } catch (error) {
            notify(error.message, "danger");
        }
    }, [auth?.token, auth?.user?.role]);

    const loadSupport = useCallback(async () => {
        if (!auth) {
            setSupportTickets([]);
            return;
        }
        try {
            const data = await requestJson("/api/support", { auth });
            setSupportTickets(data.tickets || []);
        } catch (error) {
            notify(error.message, "danger");
        }
    }, [auth?.token]);

    const loadAdminModeration = useCallback(async () => {
        if (!auth?.user || auth.user.role !== "admin") {
            setAdminReports([]);
            setAdminTickets([]);
            return;
        }
        try {
            const [reportsData, supportData] = await Promise.all([
                requestJson("/api/admin/reports", { auth }),
                requestJson("/api/admin/support", { auth }),
            ]);
            setAdminReports(reportsData.reports || []);
            setAdminTickets(supportData.tickets || []);
        } catch (error) {
            notify(error.message, "danger");
        }
    }, [auth?.token, auth?.user?.role]);

    useEffect(() => {
        const verify = async () => {
            if (!auth?.token) {
                return;
            }
            try {
                const data = await requestJson("/api/me", { auth });
                updateAuth({ token: auth.token, csrf: data.csrf_token || auth.csrf, user: data.user });
            } catch {
                updateAuth(null);
            }
        };
        verify();
    }, []);

    useEffect(() => {
        loadPolls(true);
    }, [auth?.token]);

    useEffect(() => {
        const code = pollCodeFromPath();
        if (!code) {
            return;
        }

        let cancelled = false;
        const loadLinkedPoll = async () => {
            setLoading(true);
            try {
                const data = await requestJson(`/api/polls/${code}`, { auth });
                if (!cancelled) {
                    setActivePoll(data.poll);
                    setView("analytics");
                    scrollToTop();
                }
            } catch (error) {
                if (!cancelled) {
                    notify(error.message, "danger");
                }
            } finally {
                if (!cancelled) {
                    setLoading(false);
                }
            }
        };
        loadLinkedPoll();
        return () => {
            cancelled = true;
        };
    }, [auth?.token]);

    useEffect(() => {
        if (view === "profile") {
            loadActivity();
        }
        if (view === "support") {
            loadSupport();
        }
        if (view === "admin") {
            loadUsers();
            loadAdminModeration();
        }
    }, [view, auth?.token, auth?.user?.role]);

    const openPoll = async (poll, options = {}) => {
        setLoading(true);
        try {
            const data = await requestJson(`/api/polls/${poll.code}`, { auth });
            setActivePoll(data.poll);
            setAdminMenuRequest(options.adminMenu ? `${data.poll.code}-${Date.now()}` : null);
            setView("analytics");
            window.history.pushState({}, "", `/poll/${data.poll.code}`);
            scrollToTop();
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const openUser = async (userOrId) => {
        const userId = typeof userOrId === "object" ? userOrId.id : userOrId;
        if (!userId) {
            return;
        }
        setLoading(true);
        try {
            const data = await requestJson(`/api/users/${userId}/profile`, { auth });
            setProfileUser(data.profile);
            setView("user");
            scrollToTop();
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const handleAuth = async (mode, payload) => {
        setLoading(true);
        try {
            const data = await requestJson(`/api/auth/${mode}`, { method: "POST", body: payload });
            updateAuth({ token: data.token, csrf: data.csrf_token, user: data.user });
            setAuthModalError("");
            notify(mode === "vk" ? "Вход через VK ID выполнен." : (mode === "login" ? "Вход выполнен." : "Аккаунт создан."), "success");
            if (authTargetView) {
                setView(authTargetView);
                setAuthTargetView(null);
                scrollToTop();
            }
            setAuthModal({ open: false, mode: "login" });
        } catch (error) {
            setAuthModalError(error.message || "Не удалось выполнить вход.");
        } finally {
            setLoading(false);
        }
    };

    const debugLogin = async (role) => {
        setLoading(true);
        try {
            const data = await requestJson("/api/debug/login", { method: "POST", body: { role } });
            updateAuth({ token: data.token, csrf: data.csrf_token, user: data.user });
            notify(role === "admin" ? "Debug-вход: администратор." : "Debug-вход: пользователь.", "success");
            setView("dashboard");
            window.history.pushState({}, "", "/");
            scrollToTop();
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const uploadAvatar = async (file) => {
        if (!auth || !file) {
            return;
        }
        setLoading(true);
        try {
            const formData = new FormData();
            formData.append("avatar", file);
            const data = await requestForm("/api/me/avatar", { method: "POST", auth, body: formData });
            updateAuth({ ...auth, user: data.user });
            setProfileUser((current) => current?.user?.id === data.user.id ? { ...current, user: data.user } : current);
            notify("Аватар обновлён.", "success");
            await loadPolls(true);
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const uploadPollImage = async (file) => {
        if (!auth || !file) {
            throw new Error("Нужен вход для загрузки изображения.");
        }
        const formData = new FormData();
        formData.append("image", file);
        const data = await requestForm("/api/uploads/poll-image", { method: "POST", auth, body: formData });
        return data;
    };

    const updatePrivacy = async (hideActivity) => {
        if (!auth) {
            return;
        }
        setLoading(true);
        try {
            const data = await requestJson("/api/me/privacy", {
                method: "PATCH",
                auth,
                body: { hide_activity: hideActivity },
            });
            updateAuth({ ...auth, user: data.user });
            setProfileUser((current) => current?.user?.id === data.user.id ? { ...current, user: data.user } : current);
            notify(hideActivity ? "Активность скрыта." : "Активность открыта.", "success");
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const updateUsername = async (username) => {
        if (!auth) {
            return;
        }
        setLoading(true);
        try {
            const data = await requestJson("/api/me/username", {
                method: "PATCH",
                auth,
                body: { username },
            });
            updateAuth({ token: data.token || auth.token, csrf: data.csrf_token || auth.csrf, user: data.user });
            setProfileUser((current) => current?.user?.id === data.user.id ? { ...current, user: data.user } : current);
            notify("Никнейм обновлён.", "success");
            await loadPolls(true);
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const openReport = (target) => {
        if (!auth) {
            openAuthModal(null);
            return;
        }
        setReportTarget(target);
    };

    const submitReport = async (payload) => {
        setLoading(true);
        try {
            await requestJson("/api/reports", { method: "POST", auth, body: payload });
            notify("Жалоба отправлена администратору.", "success");
            setReportTarget(null);
            if (auth?.user?.role === "admin") {
                await loadAdminModeration();
            }
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const createSupportTicket = async (payload) => {
        if (!auth) {
            openAuthModal("support");
            return;
        }
        setLoading(true);
        try {
            const data = await requestJson("/api/support", { method: "POST", auth, body: payload });
            setSupportTickets((current) => [data.ticket, ...current]);
            notify("Обращение создано.", "success");
            if (auth.user.role === "admin") {
                await loadAdminModeration();
            }
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const sendSupportMessage = async (ticket, body) => {
        setLoading(true);
        try {
            const data = await requestJson(`/api/support/${ticket.id}/messages`, {
                method: "POST",
                auth,
                body: { body },
            });
            setSupportTickets((current) => current.map((item) => item.id === data.ticket.id ? data.ticket : item));
            setAdminTickets((current) => current.map((item) => item.id === data.ticket.id ? data.ticket : item));
            notify("Сообщение отправлено.", "success");
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const reviewReport = async (report, payload) => {
        setLoading(true);
        try {
            const data = await requestJson(`/api/admin/reports/${report.id}`, {
                method: "PATCH",
                auth,
                body: payload,
            });
            setAdminReports((current) => current.map((item) => item.id === data.report.id ? data.report : item));
            notify("Жалоба обновлена.", "success");
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const updateSupportStatus = async (ticket, status) => {
        setLoading(true);
        try {
            const data = await requestJson(`/api/admin/support/${ticket.id}`, {
                method: "PATCH",
                auth,
                body: { status },
            });
            setAdminTickets((current) => current.map((item) => item.id === data.ticket.id ? data.ticket : item));
            notify("Статус обращения обновлен.", "success");
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const logout = () => {
        updateAuth(null);
        setActivePoll(null);
        setProfileUser(null);
        setActivity([]);
        setSupportTickets([]);
        setAdminReports([]);
        setAdminTickets([]);
        setUsers([]);
        notify("Сеанс завершён.", "info");
    };

    const createPoll = async (payload) => {
        if (!auth) {
            openAuthModal(null);
            return;
        }
        setLoading(true);
        try {
            const data = await requestJson("/api/polls", { method: "POST", auth, body: payload });
            setActivePoll(data.poll);
            notify("Опрос создан.", "success");
            setEditorDirty(false);
            setView("analytics");
            window.history.pushState({}, "", `/poll/${data.poll.code}`);
            scrollToTop();
            await loadPolls(true);
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const vote = async (poll, optionIds) => {
        if (!auth) {
            openAuthModal(null);
            return;
        }
        setLoading(true);
        try {
            const data = await requestJson(`/api/polls/${poll.code}/vote`, {
                method: "POST",
                auth,
                body: { option_ids: optionIds },
            });
            setActivePoll(data.poll);
            notify("Голос учтён.", "success");
            await loadPolls(true);
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const managePoll = async (poll, action) => {
        setLoading(true);
        try {
            const method = action === "delete" || action === "delete_hard" ? "DELETE" : "POST";
            const path = action === "delete_hard" ? `/api/polls/${poll.code}?hard=1` : action === "delete" ? `/api/polls/${poll.code}` : `/api/polls/${poll.code}/${action}`;
            const data = await requestJson(path, { method, auth });
            if (action === "delete" || action === "delete_hard") {
                setActivePoll(action === "delete_hard" ? null : data.poll || null);
                notify(action === "delete_hard" ? "Опрос удалён." : "Опрос перемещён в архив.", "success");
            } else {
                setActivePoll(data.poll);
                notify(action === "complete" ? "Опрос завершён." : "Опрос активирован.", "success");
            }
            await loadPolls(true);
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const updateResultsSettings = async (poll, payload) => {
        if (!auth) {
            openAuthModal(null);
            return;
        }
        setLoading(true);
        try {
            const data = await requestJson(`/api/polls/${poll.code}/results`, {
                method: "POST",
                auth,
                body: payload,
            });
            setActivePoll(data.poll);
            notify("Настройки публикации обновлены.", "success");
            await loadPolls(true);
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const addComment = async (poll, body) => {
        if (!auth) {
            openAuthModal(null);
            return false;
        }
        setLoading(true);
        try {
            const data = await requestJson(`/api/polls/${poll.code}/comments`, {
                method: "POST",
                auth,
                body: { body },
            });
            setActivePoll(data.poll);
            notify("Комментарий добавлен.", "success");
            return true;
        } catch (error) {
            notify(error.message, "danger");
            return false;
        } finally {
            setLoading(false);
        }
    };

    const deleteComment = async (commentId) => {
        if (!auth?.user || auth.user.role !== "admin") {
            return;
        }
        setLoading(true);
        try {
            const data = await requestJson(`/api/comments/${commentId}`, { method: "DELETE", auth });
            setActivePoll((current) => current?.id === data.poll?.id ? data.poll : current);
            notify("Комментарий удалён.", "success");
            if (auth.user.role === "admin") {
                await loadAdminModeration();
            }
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const exportPoll = async (poll, format) => {
        if (!auth) {
            openAuthModal(null);
            return;
        }
        setLoading(true);
        try {
            const blob = await requestBlob(`/api/polls/${poll.code}/export.${format}`, { auth });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = `poll_${poll.code}.${format}`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            notify(`Экспорт ${format.toUpperCase()} готов.`, "success");
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const changeRole = async (user, role) => {
        setLoading(true);
        try {
            await requestJson(`/api/users/${user.id}/role`, {
                method: "PATCH",
                auth,
                body: { role },
            });
            notify("Роль обновлена.", "success");
            await loadUsers();
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const updateUserBlock = async (user, blocked) => {
        setLoading(true);
        try {
            await requestJson(`/api/users/${user.id}/block`, {
                method: "PATCH",
                auth,
                body: { blocked },
            });
            notify(blocked ? "Пользователь заблокирован." : "Пользователь разблокирован.", "success");
            await loadUsers();
            await loadAdminModeration();
        } catch (error) {
            notify(error.message, "danger");
        } finally {
            setLoading(false);
        }
    };

    const visibleNav = navItems.filter(([key]) => key !== "admin" || auth?.user?.role === "admin");
    const switchView = (key) => {
        if (key !== view && !confirmLeaveEditor()) {
            return;
        }
        if ((key === "profile" || key === "editor") && !auth) {
            openAuthModal(key);
            return;
        }
        if (key === "terms" || key === "privacy" || key === "debug") {
            window.history.pushState({}, "", key === "terms" ? "/terms" : key === "privacy" ? "/privacy" : "/debug");
        } else if (key !== "analytics" && window.location.pathname !== "/") {
            window.history.pushState({}, "", "/");
        }
        setView(key);
        scrollToTop();
    };

    const goBack = () => {
        if (!confirmLeaveEditor()) {
            return;
        }
        if (view === "analytics") {
            setView("dashboard");
            setActivePoll(null);
            if (window.location.pathname.startsWith("/poll/")) {
                window.history.pushState({}, "", "/");
            }
            scrollToTop();
            return;
        }
        if (view === "user") {
            setView("dashboard");
            setProfileUser(null);
            scrollToTop();
            return;
        }
        if (view !== "dashboard") {
            setView("dashboard");
            if (window.location.pathname !== "/") {
                window.history.pushState({}, "", "/");
            }
            scrollToTop();
            return;
        }
        window.history.back();
    };

    return (
        <div className="app">
            <aside className="sidebar">
                <button className="brand brand--button" type="button" onClick={() => switchView("dashboard")}>
                    <div className="brand__mark"><Icon name="check-check" /></div>
                    <div>
                        <strong>eVote</strong>
                        <span>электронное голосование</span>
                    </div>
                </button>

                <nav className="nav">
                    {visibleNav.map(([key, label, icon]) => (
                        <button key={key} className={cx("nav__button", view === key && "is-active")} onClick={() => switchView(key)} type="button">
                            <Icon name={icon} />
                            <span>{label}</span>
                        </button>
                    ))}
                </nav>

                <div className="sidebar__footer">
                    {auth ? (
                        <>
                            <div className="user-block">
                                <strong><UserLink user={auth.user} onOpen={openUser} /></strong>
                                <span>{auth.user.role === "admin" ? "Администратор" : "Пользователь"}</span>
                            </div>
                            <button className="button button--ghost" type="button" onClick={logout}>
                                <Icon name="log-out" />
                                Выйти
                            </button>
                        </>
                    ) : (
                        <button className="button button--primary" type="button" onClick={() => openAuthModal(null)}>
                            <Icon name="log-in" />
                            Войти
                        </button>
                    )}
                </div>
            </aside>

            <main className="workspace">
                <header className="topbar">
                    <div>
                        <h1>{viewTitle(view)}</h1>
                        <span>{new Intl.DateTimeFormat("ru-RU", { dateStyle: "full" }).format(new Date())}</span>
                    </div>
                    <div className="topbar__actions">
                        <button className="button button--ghost" type="button" onClick={goBack}>
                            <Icon name="arrow-left" />
                            Назад
                        </button>
                        <button className="button button--ghost" type="button" onClick={() => setTheme((value) => value === "dark" ? "light" : "dark")}>
                            <Icon name={theme === "dark" ? "sun" : "moon"} />
                            {theme === "dark" ? "Светлая" : "Тёмная"}
                        </button>
                        <button className="button button--ghost" type="button" onClick={() => loadPolls()}>
                            <Icon name="refresh-cw" />
                            Обновить
                        </button>
                    </div>
                </header>

                {status.text ? <div className={cx("notice", `notice--${status.type}`)} role="status">{status.text}</div> : null}
                {loading ? <div className="loader"><span></span></div> : null}

                {view === "dashboard" ? <Dashboard polls={polls} auth={auth} now={clockNow} onOpen={openPoll} onOpenUser={openUser} /> : null}
                {view === "editor" ? <PollEditor auth={auth} onSubmit={createPoll} onDirtyChange={setEditorDirty} onUploadImage={uploadPollImage} notify={notify} /> : null}
                {view === "analytics" ? <Analytics polls={polls} activePoll={activePoll} auth={auth} adminMenuRequest={adminMenuRequest} onAdminMenuConsumed={() => setAdminMenuRequest(null)} onOpen={openPoll} onVote={vote} onManage={managePoll} onResultsSettings={updateResultsSettings} onComment={addComment} onDeleteComment={deleteComment} onExport={exportPoll} onOpenUser={openUser} onReport={openReport} /> : null}
                {view === "profile" && auth ? <Profile auth={auth} activity={activity} onAvatar={uploadAvatar} onPrivacy={updatePrivacy} onUsername={updateUsername} onOpenPoll={openPoll} /> : null}
                {view === "support" ? <SupportCenter auth={auth} tickets={supportTickets} onCreate={createSupportTicket} onSend={sendSupportMessage} /> : null}
                {view === "user" ? <UserProfile profile={profileUser} auth={auth} onOpenPoll={openPoll} onOpenUser={openUser} onReport={openReport} /> : null}
                {view === "terms" ? <LegalPage type="terms" /> : null}
                {view === "privacy" ? <LegalPage type="privacy" /> : null}
                {view === "debug" ? <DebugPage auth={auth} onLogin={debugLogin} /> : null}
                {view === "admin" ? <Admin users={users} polls={polls} reports={adminReports} tickets={adminTickets} auth={auth} onRoleChange={changeRole} onBlockUser={updateUserBlock} onManage={managePoll} onOpen={openPoll} onDeleteComment={deleteComment} onOpenUser={openUser} onReviewReport={reviewReport} onSendSupport={sendSupportMessage} onSupportStatus={updateSupportStatus} /> : null}
            </main>
            <footer className="site-footer">
                <div>
                    <strong>eVote</strong>
                    <span>учебный сервис электронного голосования</span>
                </div>
                <nav>
                    <button type="button" onClick={() => switchView("dashboard")}>Главная</button>
                    <button type="button" onClick={() => switchView("terms")}>Пользовательское соглашение</button>
                    <button type="button" onClick={() => switchView("privacy")}>Персональные данные</button>
                    <button type="button" onClick={() => switchView("support")}>Поддержка</button>
                    <button type="button" onClick={() => switchView("debug")}>Debug</button>
                </nav>
            </footer>
            {reportTarget ? (
                <ReportModal
                    target={reportTarget}
                    onClose={() => setReportTarget(null)}
                    onSubmit={submitReport}
                />
            ) : null}
            {authModal.open ? (
                <AuthModal
                    mode={authModal.mode}
                    error={authModalError}
                    onMode={(mode) => { setAuthModalError(""); setAuthModal({ open: true, mode }); }}
                    onClose={() => { setAuthTargetView(null); setAuthModalError(""); setAuthModal({ open: false, mode: "login" }); }}
                    onAuth={handleAuth}
                />
            ) : null}
        </div>
    );
}

function viewTitle(view) {
    return {
        dashboard: "Панель голосований",
        editor: "Редактор опроса",
        analytics: "Страница голосования",
        profile: "Профиль",
        support: "Поддержка",
        user: "Профиль пользователя",
        terms: "Правила сервиса",
        privacy: "Персональные данные",
        debug: "Debug-вход",
        admin: "Администрирование",
    }[view] || "Голосование";
}

function Dashboard({ polls, auth, now, onOpen, onOpenUser }) {
    const [query, setQuery] = useState("");
    const [filters, setFilters] = useState({
        status: "all",
        access: "all",
        anonymity: "all",
        results: "all",
        ownership: "all",
    });
    const [sortBy, setSortBy] = useState("newest");
    const [page, setPage] = useState(1);
    const normalized = query.trim().toLowerCase();
    const setFilter = (field, value) => {
        setPage(1);
        setFilters((current) => ({ ...current, [field]: value }));
    };
    const resetFilters = () => {
        setFilters({ status: "all", access: "all", anonymity: "all", results: "all", ownership: "all" });
        setSortBy("newest");
        setPage(1);
    };
    const activeFilterCount = Object.values(filters).filter((value) => value !== "all").length + (sortBy !== "newest" ? 1 : 0);

    const filteredPolls = polls.filter((poll) => {
        const matchesQuery = !normalized || poll.title.toLowerCase().includes(normalized);
        const isClosed = !poll.is_active || poll.has_ended || poll.is_archived;
        const isFull = poll.access_type === "limited" && poll.spots_left === 0;
        const matchesStatus =
            filters.status === "all" ||
            (filters.status === "active" && poll.is_active && !poll.has_ended && !isFull && !poll.is_archived) ||
            (filters.status === "archive" && poll.is_archived) ||
            (filters.status === "closed" && isClosed && !poll.is_archived) ||
            (filters.status === "stopped" && !poll.is_active) ||
            (filters.status === "ended" && poll.has_ended) ||
            (filters.status === "full" && isFull) ||
            (filters.status === "votable" && poll.can_vote && !poll.is_archived);
        const matchesAccess = filters.access === "all" || poll.access_type === filters.access;
        const matchesAnonymity = filters.anonymity === "all" || Number(poll.anonymity_level) === Number(filters.anonymity);
        const matchesResults =
            filters.results === "all" ||
            (filters.results === "visible" && poll.results_visible) ||
            (filters.results === "hidden_now" && !poll.results_visible) ||
            (filters.results === "mode_hidden" && poll.results_visibility === "hidden") ||
            poll.results_visibility === filters.results;
        const matchesOwnership =
            filters.ownership === "all" ||
            (filters.ownership === "mine" && auth?.user && poll.creator.id === auth.user.id) ||
            (filters.ownership === "voted" && auth?.user && poll.has_voted);
        return matchesQuery && matchesStatus && matchesAccess && matchesAnonymity && matchesResults && matchesOwnership;
    });
    const sortedPolls = [...filteredPolls].sort((a, b) => {
        if (sortBy === "ending") {
            return new Date(a.ends_at || "9999-12-31") - new Date(b.ends_at || "9999-12-31");
        }
        if (sortBy === "votes") {
            return b.total_votes - a.total_votes;
        }
        if (sortBy === "views") {
            return (b.views_count || 0) - (a.views_count || 0);
        }
        if (sortBy === "participants") {
            return b.participants - a.participants;
        }
        if (sortBy === "title") {
            return a.title.localeCompare(b.title, "ru");
        }
        return new Date(b.created_at || 0) - new Date(a.created_at || 0);
    });
    const totalPages = Math.max(1, Math.ceil(sortedPolls.length / DASHBOARD_PAGE_SIZE));
    const currentPage = Math.min(page, totalPages);
    const pageItems = sortedPolls.length > DASHBOARD_PAGE_SIZE ? paginationItems(currentPage, totalPages) : [];
    const pageStart = sortedPolls.length ? ((currentPage - 1) * DASHBOARD_PAGE_SIZE) + 1 : 0;
    const pageEnd = Math.min(currentPage * DASHBOARD_PAGE_SIZE, sortedPolls.length);
    const pagedPolls = sortedPolls.slice((currentPage - 1) * DASHBOARD_PAGE_SIZE, currentPage * DASHBOARD_PAGE_SIZE);
    const goToPage = (value) => {
        setPage(Math.max(1, Math.min(totalPages, value)));
    };
    const emptyText = ["mine", "voted"].includes(filters.ownership) && !auth ? "Войдите, чтобы увидеть свои голосования" : "Нет голосований";

    return (
        <div className="stack">
            <div className="dashboard-shell">
                <section className="panel feed-panel">
                    <div className="section-head">
                        <div>
                            <h2>Опросы</h2>
                            <span>{sortedPolls.length} из {polls.length}</span>
                        </div>
                        <div className="tools feed-toolbar">
                            <input className="input input--search" value={query} onChange={(event) => {
                                    setQuery(event.target.value);
                                    setPage(1);
                                }} placeholder="Поиск по названию" />
                        </div>
                    </div>

                    <div className="poll-grid">
                        {pagedPolls.map((poll) => <PollCard key={poll.id} poll={poll} now={now} onOpen={() => onOpen(poll)} onOpenUser={onOpenUser} />)}
                        {!sortedPolls.length ? <EmptyState title={emptyText} icon={["mine", "voted"].includes(filters.ownership) && !auth ? "lock-keyhole" : "search"} /> : null}
                    </div>
                    {sortedPolls.length > DASHBOARD_PAGE_SIZE ? (
                        <div className="pagination-bar">
                            <div className="pagination-bar__summary">
                                <strong>Страница {currentPage} из {totalPages}</strong>
                                <span>Показано {pageStart}–{pageEnd} из {sortedPolls.length}</span>
                            </div>
                            <div className="pagination-bar__controls">
                                <button className="button button--ghost pagination-nav" type="button" onClick={() => goToPage(currentPage - 1)} disabled={currentPage === 1}>
                                    <Icon name="chevron-left" />
                                    <span>Назад</span>
                                </button>
                                <div className="pagination-bar__pages">
                                    {pageItems.map((item, index) => (
                                        item === "ellipsis-start" || item === "ellipsis-end" ? (
                                            <span className="pagination-ellipsis" key={`${item}-${index}`}>…</span>
                                        ) : (
                                            <button
                                                className={cx("button", "button--ghost", "pagination-page", item === currentPage && "is-active")}
                                                key={`${item}-${index}`}
                                                type="button"
                                                aria-current={item === currentPage ? "page" : undefined}
                                                onClick={() => goToPage(item)}
                                            >
                                                {item}
                                            </button>
                                        )
                                    ))}
                                </div>
                                <button className="button button--ghost pagination-nav" type="button" onClick={() => goToPage(currentPage + 1)} disabled={currentPage === totalPages}>
                                    <span>Вперёд</span>
                                    <Icon name="chevron-right" />
                                </button>
                            </div>
                        </div>
                    ) : null}
                </section>

                <aside className="panel filter-panel">
                    <div className="section-head">
                        <div>
                            <h2>Сортировка</h2>
                            <span>{activeFilterCount ? `${activeFilterCount} выбрано` : "без фильтров"}</span>
                        </div>
                        <button className="icon-button" type="button" onClick={resetFilters} title="Сбросить">
                            <Icon name="rotate-ccw" />
                        </button>
                    </div>
                    <div className="filter-stack">
                        <div className="field">
                            <label>Порядок</label>
                            <select className="select" value={sortBy} onChange={(event) => {
                                    setSortBy(event.target.value);
                                    setPage(1);
                                }}>
                                <option value="newest">Сначала новые</option>
                                <option value="ending">Скоро завершатся</option>
                                <option value="votes">Больше ответов</option>
                                <option value="views">Больше просмотров</option>
                                <option value="participants">Больше участников</option>
                                <option value="title">По названию</option>
                            </select>
                        </div>
                        <div className="field">
                            <label>Статус</label>
                            <select className="select" value={filters.status} onChange={(event) => setFilter("status", event.target.value)}>
                                <option value="all">Любой</option>
                                <option value="active">Активные</option>
                                <option value="votable">Доступны для голоса</option>
                                <option value="archive">Архив</option>
                                <option value="closed">Закрытые</option>
                                <option value="stopped">Остановленные</option>
                                <option value="ended">Истёк срок</option>
                                <option value="full">Лимит исчерпан</option>
                            </select>
                        </div>
                        <div className="field">
                            <label>Доступ</label>
                            <select className="select" value={filters.access} onChange={(event) => setFilter("access", event.target.value)}>
                                <option value="all">Любой</option>
                                <option value="public">Публичные</option>
                                <option value="link">По ссылке</option>
                                <option value="limited">С лимитом</option>
                            </select>
                        </div>
                        <div className="field">
                            <label>Анонимность</label>
                            <select className="select" value={filters.anonymity} onChange={(event) => setFilter("anonymity", event.target.value)}>
                                <option value="all">Любая</option>
                                <option value="0">Открытые</option>
                                <option value="1">Полу-анонимные</option>
                                <option value="2">Анонимные</option>
                            </select>
                        </div>
                        <div className="field">
                            <label>Результаты</label>
                            <select className="select" value={filters.results} onChange={(event) => setFilter("results", event.target.value)}>
                                <option value="all">Любые</option>
                                <option value="visible">Сейчас видны</option>
                                <option value="hidden_now">Сейчас скрыты</option>
                                <option value="after_end">После голосования</option>
                                <option value="manual">Ручная публикация</option>
                                <option value="mode_hidden">Скрыты от участников</option>
                            </select>
                        </div>
                        <div className="field">
                            <label>Участие</label>
                            <select className="select" value={filters.ownership} onChange={(event) => setFilter("ownership", event.target.value)}>
                                <option value="all">Все доступные</option>
                                <option value="mine">Созданные мной</option>
                                <option value="voted">Где я голосовал</option>
                            </select>
                        </div>
                    </div>
                </aside>
            </div>
        </div>
    );
}

function PollCard({ poll, now, onOpen, onOpenUser }) {
    const status = pollStatus(poll);
    const flags = pollFlags(poll);
    const cardImage = firstImage(poll.description_images, poll.description_image);
    const cardCoverImage = cloudinaryVariantUrl(cardImage, "c_fill,w_176,h_116,q_auto,f_auto");
    const handleKeyDown = (event) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onOpen();
        }
    };
    const copyLink = (event) => {
        event.preventDefault();
        event.stopPropagation();
        navigator.clipboard?.writeText(pollShareUrl(poll));
    };

    return (
        <article className={cx("poll-card", `poll-card--${status.key}`)} role="button" tabIndex="0" onClick={onOpen} onKeyDown={handleKeyDown}>
            <div className="poll-card__top">
                <div>
                    <h3>{poll.title}</h3>
                    <span><UserLink user={poll.creator} onOpen={onOpenUser} /></span>
                </div>
                {cardImage ? (
                    <div className="poll-card__cover" aria-hidden="true">
                        <img className="poll-card__image" src={cardCoverImage} alt="" loading="lazy" decoding="async" fetchPriority="low" />
                    </div>
                ) : null}
                <Icon name="chevron-right" />
            </div>
            {poll.description ? <p>{poll.description}</p> : null}
            <div className="flag-row">
                {flags.map((flag) => <FlagIcon key={`${poll.id}-${flag.key}`} flag={flag} />)}
            </div>
            <div className="poll-card__stats">
                <span title="Участники"><Icon name="users-round" />{poll.participants}</span>
                <span title="Просмотры"><Icon name="eye" />{poll.views_count || 0}</span>
                <span title="Комментарии"><Icon name="message-square" />{poll.comments_count ?? poll.comments?.length ?? 0}</span>
                <span title="Срок голосования"><Icon name="clock-3" />{pollCountdownLabel(poll, now)}</span>
                {poll.access_type === "link" ? (
                    <button className="text-tool" type="button" onClick={copyLink}>
                        <Icon name="link" />
                        Ссылка
                    </button>
                ) : null}
            </div>
        </article>
    );
}

function ImagePreview({ src, images, onRemove }) {
    const items = (images?.length ? images : [src]).filter(Boolean);
    if (!items.length) {
        return null;
    }
    return (
        <div className="image-preview-list">
            {items.map((item, index) => (
                <div className="image-preview" key={`${item}-${index}`}>
                    <img src={item} alt="" />
                    <button className="icon-button" type="button" onClick={() => onRemove?.(index)} title="Убрать изображение">
                        <Icon name="x" />
                    </button>
                </div>
            ))}
        </div>
    );
}

function ZoomImageButton({ src, alt = "", className, imageClassName, onZoom }) {
    if (!src) {
        return null;
    }
    return (
        <button
            className={cx("image-zoom-trigger", className)}
            type="button"
            onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onZoom?.({ src, alt });
            }}
            title="Открыть изображение"
        >
            <img className={imageClassName} src={src} alt="" loading="lazy" decoding="async" />
            <span className="image-zoom-trigger__icon"><Icon name="zoom-in" size={14} /></span>
        </button>
    );
}

function ImageZoomModal({ image, onClose }) {
    const [zoom, setZoom] = useState(1);
    useEffect(() => {
        setZoom(1);
    }, [image?.src]);
    useEffect(() => {
        const handleKeyDown = (event) => {
            if (event.key === "Escape") {
                onClose();
            }
        };
        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [onClose]);
    if (!image?.src) {
        return null;
    }
    const zoomIn = () => setZoom((value) => Math.min(3, Number((value + 0.25).toFixed(2))));
    const zoomOut = () => setZoom((value) => Math.max(0.5, Number((value - 0.25).toFixed(2))));
    return (
        <div className="modal-layer modal-layer--image" role="presentation" onClick={onClose}>
            <section className="image-viewer" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
                <div className="image-viewer__head">
                    <strong>{image.alt || "Изображение"}</strong>
                    <div className="image-viewer__tools">
                        <button className="icon-button" type="button" onClick={zoomOut} title="Уменьшить">
                            <Icon name="zoom-out" />
                        </button>
                        <span>{Math.round(zoom * 100)}%</span>
                        <button className="icon-button" type="button" onClick={zoomIn} title="Увеличить">
                            <Icon name="zoom-in" />
                        </button>
                        <button className="icon-button" type="button" onClick={() => setZoom(1)} title="Сбросить масштаб">
                            <Icon name="rotate-ccw" />
                        </button>
                        <button className="icon-button" type="button" onClick={onClose} title="Закрыть">
                            <Icon name="x" />
                        </button>
                    </div>
                </div>
                <div className="image-viewer__canvas">
                    <img src={image.src} alt="" style={{ width: `${zoom * 100}%` }} />
                </div>
            </section>
        </div>
    );
}

function PollEditor({ auth, onSubmit, onDirtyChange, onUploadImage, notify }) {
    const initialFormRef = useRef(null);
    if (!initialFormRef.current) {
        initialFormRef.current = createInitialPollForm();
    }
    const [form, setForm] = useState(initialFormRef.current);
    const [uploading, setUploading] = useState(null);

    useEffect(() => {
        const initial = initialFormRef.current;
        const dirty = (
            form.title.trim() ||
            form.description.trim() ||
            form.access_type !== initial.access_type ||
            form.poll_type !== initial.poll_type ||
            form.anonymity_level !== initial.anonymity_level ||
            form.results_visibility !== initial.results_visibility ||
            form.max_votes !== initial.max_votes ||
            form.is_infinite !== initial.is_infinite ||
            form.ends_at !== initial.ends_at ||
            form.description_image ||
            form.description_images.length ||
            form.options.length !== initial.options.length ||
            form.options.some((option) => option.text.trim() || option.image || option.image_url || option.images?.length)
        );
        onDirtyChange?.(Boolean(dirty));
    }, [form, onDirtyChange]);

    useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

    const setField = (field, value) => setForm((current) => ({ ...current, [field]: value }));
    const setOption = (index, value) => {
        setForm((current) => ({
            ...current,
            options: current.options.map((option, optionIndex) => optionIndex === index ? { ...option, text: value } : option),
        }));
    };
    const addOption = () => {
        setForm((current) => current.options.length >= MAX_OPTIONS ? current : { ...current, options: [...current.options, { text: "", image: null, image_url: "", images: [], image_urls: [] }] });
    };

    const removeOption = (index) => {
        setForm((current) => current.options.length <= 1 ? current : { ...current, options: current.options.filter((_, optionIndex) => optionIndex !== index) });
    };

    const uploadImages = async (files, target, index = null) => {
        const selectedFiles = Array.from(files || []).slice(0, MAX_IMAGES_PER_FIELD);
        if (!selectedFiles.length || !onUploadImage) {
            return;
        }
        setUploading(target);
        try {
            const uploaded = [];
            for (const file of selectedFiles) {
                uploaded.push(await onUploadImage(file));
            }
            if (target === "description") {
                setForm((current) => {
                    const images = [...(current.description_images || []), ...uploaded.map((item) => item.filename)].slice(0, MAX_IMAGES_PER_FIELD);
                    const urls = [...(current.description_image_urls || []), ...uploaded.map((item) => item.url)].slice(0, MAX_IMAGES_PER_FIELD);
                    return {
                        ...current,
                        description_images: images,
                        description_image_urls: urls,
                        description_image: images[0] || null,
                        description_image_url: urls[0] || "",
                    };
                });
            } else {
                setForm((current) => ({
                    ...current,
                    options: current.options.map((option, optionIndex) => {
                        if (optionIndex !== index) {
                            return option;
                        }
                        const images = [...(option.images || []), ...uploaded.map((item) => item.filename)].slice(0, MAX_IMAGES_PER_FIELD);
                        const urls = [...(option.image_urls || []), ...uploaded.map((item) => item.url)].slice(0, MAX_IMAGES_PER_FIELD);
                        return {
                            ...option,
                            images,
                            image_urls: urls,
                            image: images[0] || null,
                            image_url: urls[0] || "",
                        };
                    }),
                }));
            }
        } catch (error) {
            notify?.(error.message, "danger");
        } finally {
            setUploading(null);
        }
    };

    const submit = (event) => {
        event.preventDefault();
        onSubmit({
            ...form,
            max_votes: form.access_type === "limited" ? form.max_votes : null,
            is_anonymous: form.anonymity_level !== "0",
            is_infinite: form.is_infinite,
            ends_at: form.is_infinite ? null : form.ends_at,
            description_image: firstImage(form.description_images, form.description_image),
            description_images: form.description_images,
            options: form.options.map((option) => ({
                text: option.text,
                image: firstImage(option.images, option.image),
                images: option.images || [],
                image_url: option.image ? "" : option.image_url,
            })),
        });
    };

    if (!auth) {
        return <EmptyState icon="lock-keyhole" title="Нужен вход" />;
    }

    return (
        <form className="editor" onSubmit={submit}>
            <section className="panel editor__main">
                <div className="field">
                    <label>Название <RequiredMark /></label>
                    <input className="input input--xl" value={form.title} required onChange={(event) => setField("title", event.target.value)} />
                </div>
                <div className="field">
                    <label>Описание</label>
                    <textarea className="textarea" rows="5" value={form.description} onChange={(event) => setField("description", event.target.value)}></textarea>
                    <div className="upload-line">
                        <label className="button button--ghost">
                            <Icon name="image-up" />
                            Изображение описания
                            <input className="sr-only" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple onChange={(event) => {
                                uploadImages(event.target.files, "description");
                                event.target.value = "";
                            }} />
                        </label>
                        {uploading === "description" ? <span>Загрузка...</span> : null}
                    </div>
                    <ImagePreview
                        images={form.description_image_urls}
                        onRemove={(imageIndex) => setForm((current) => {
                            const images = current.description_images.filter((_, index) => index !== imageIndex);
                            const urls = current.description_image_urls.filter((_, index) => index !== imageIndex);
                            return { ...current, description_images: images, description_image_urls: urls, description_image: images[0] || null, description_image_url: urls[0] || "" };
                        })}
                    />
                </div>
                <div className="option-list">
                    <div className="section-head">
                        <div>
                            <h2>Варианты <RequiredMark /></h2>
                            <span>минимум 1 · {form.options.length} / {MAX_OPTIONS}</span>
                        </div>
                        <button className="button button--ghost" type="button" onClick={addOption}>
                            <Icon name="plus" />
                            Добавить
                        </button>
                    </div>
                    {form.options.map((option, index) => (
                        <div className="option-edit" key={index}>
                            <span>{index + 1}{index === 0 ? <RequiredMark /> : null}</span>
                            <div className="option-edit__body">
                                <input className="input" value={option.text} required={index === 0} placeholder={index === 0 ? "Обязательный вариант" : "Дополнительный вариант"} onChange={(event) => setOption(index, event.target.value)} />
                                {uploading === `option-${index}` ? <span className="upload-status">Загрузка...</span> : null}
                                <ImagePreview
                                    images={option.image_urls || []}
                                    onRemove={(imageIndex) => {
                                        const images = (option.images || []).filter((_, itemIndex) => itemIndex !== imageIndex);
                                        const urls = (option.image_urls || []).filter((_, itemIndex) => itemIndex !== imageIndex);
                                        setForm((current) => ({
                                            ...current,
                                            options: current.options.map((item, optionIndex) => optionIndex === index ? { ...item, images, image_urls: urls, image: images[0] || null, image_url: urls[0] || "" } : item),
                                        }));
                                    }}
                                />
                            </div>
                            <label className="icon-button option-image-upload" title={option.image_url ? "Заменить картинку" : "Загрузить картинку"}>
                                <Icon name={option.image_url ? "image-check" : "image-plus"} />
                                <input className="sr-only" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple onChange={(event) => {
                                    uploadImages(event.target.files, `option-${index}`, index);
                                    event.target.value = "";
                                }} />
                            </label>
                            <button className="icon-button" type="button" onClick={() => removeOption(index)} disabled={form.options.length <= 1} title="Удалить">
                                <Icon name="trash-2" />
                            </button>
                        </div>
                    ))}
                </div>
            </section>

            <aside className="panel editor__settings">
                <div className="field">
                    <label>Доступ</label>
                    <select className="select" value={form.access_type} onChange={(event) => setField("access_type", event.target.value)}>
                        <option value="public">Публичный</option>
                        <option value="link">По ссылке</option>
                        <option value="limited">С лимитом участников</option>
                    </select>
                </div>
                {form.access_type === "limited" ? (
                    <div className="field">
                        <label>Сколько человек может проголосовать <RequiredMark /></label>
                        <input className="input" type="number" min="1" placeholder="Например, 10" value={form.max_votes} onChange={(event) => setField("max_votes", event.target.value)} />
                    </div>
                ) : null}
                <div className="field">
                    <label>Выбор</label>
                    <select className="select" value={form.poll_type} onChange={(event) => setField("poll_type", event.target.value)}>
                        <option value="single">Один вариант</option>
                        <option value="multiple">Несколько вариантов</option>
                    </select>
                </div>
                <div className="field">
                    <label>Анонимность</label>
                    <select className="select" value={form.anonymity_level} onChange={(event) => setField("anonymity_level", event.target.value)}>
                        <option value="2">Анонимный: скрыть участников</option>
                        <option value="1">Полу-анонимный: видны участники</option>
                        <option value="0">Открытый: видно кто и за что</option>
                    </select>
                </div>
                <div className="field">
                    <label>Окончание {form.is_infinite ? null : <RequiredMark />}</label>
                    <label className="checkbox-line">
                        <input type="checkbox" checked={form.is_infinite} onChange={(event) => setField("is_infinite", event.target.checked)} />
                        <span>Бессрочный опрос</span>
                    </label>
                    {!form.is_infinite ? <DateTimePicker value={form.ends_at} required onChange={(value) => setField("ends_at", value)} /> : null}
                </div>
                <div className="field">
                    <label>Публикация результатов</label>
                    <select className="select" value={form.results_visibility} onChange={(event) => setField("results_visibility", event.target.value)}>
                        <option value="after_end">После голосования</option>
                        <option value="manual">После ручной публикации</option>
                        <option value="hidden">Скрыть от участников</option>
                    </select>
                </div>
                <button className="button button--primary button--wide" type="submit">
                    <Icon name="save" />
                    Создать опрос
                </button>
            </aside>
        </form>
    );
}

function Analytics({ polls, activePoll, auth, adminMenuRequest, onAdminMenuConsumed, onOpen, onVote, onManage, onResultsSettings, onComment, onDeleteComment, onExport, onOpenUser, onReport }) {
    const poll = activePoll || polls[0];
    return (
        <div className="poll-page">
            {poll ? <PollDetail poll={poll} auth={auth} adminMenuRequest={adminMenuRequest} onAdminMenuConsumed={onAdminMenuConsumed} onVote={onVote} onManage={onManage} onResultsSettings={onResultsSettings} onComment={onComment} onDeleteComment={onDeleteComment} onExport={onExport} onOpenUser={onOpenUser} onReport={onReport} /> : <EmptyState title="Выберите голосование в ленте" icon="chart-no-axes-column-increasing" />}
        </div>
    );
}

function PollDetail({ poll, auth, adminMenuRequest, onAdminMenuConsumed, onVote, onManage, onResultsSettings, onComment, onDeleteComment, onExport, onOpenUser, onReport }) {
    const [selected, setSelected] = useState([]);
    const [voterPopup, setVoterPopup] = useState(null);
    const [historyOpen, setHistoryOpen] = useState(false);
    const [auditOpen, setAuditOpen] = useState(false);
    const [manageOpen, setManageOpen] = useState(false);
    const [moreOpen, setMoreOpen] = useState(false);
    const [linkCopied, setLinkCopied] = useState(false);
    const [commentText, setCommentText] = useState("");
    const [zoomImage, setZoomImage] = useState(null);
    const [voterFilters, setVoterFilters] = useState({ gender: "all", age: "all" });
    const [resultFilters, setResultFilters] = useState({ gender: "all", age: "all" });
    useEffect(() => {
        setSelected([]);
        setVoterPopup(null);
        setHistoryOpen(false);
        setAuditOpen(false);
        setManageOpen(false);
        setMoreOpen(false);
        setLinkCopied(false);
        setCommentText("");
        setZoomImage(null);
        setVoterFilters({ gender: "all", age: "all" });
        setResultFilters({ gender: "all", age: "all" });
    }, [poll.id]);
    useEffect(() => {
        if (adminMenuRequest && poll.can_manage) {
            setManageOpen(true);
            onAdminMenuConsumed?.();
        }
    }, [adminMenuRequest, poll.can_manage]);
    const status = pollStatus(poll);
    const flags = pollFlags(poll);
    const shareUrl = pollShareUrl(poll);
    const showHistoryButton = Number(poll.anonymity_level) === 1 && poll.participant_names_visible && poll.results_visible;
    const showResults = Boolean(poll.results_visible && (poll.has_voted || poll.can_manage));
    const canGuestAttemptVote = Boolean(
        !auth &&
        poll.is_active &&
        !poll.is_archived &&
        !poll.has_ended &&
        (poll.access_type !== "limited" || poll.spots_left !== 0)
    );
    const showVoteForm = Boolean((auth && poll.can_vote) || canGuestAttemptVote);
    const descriptionImages = (poll.description_images?.length ? poll.description_images : [poll.description_image]).filter(Boolean);
    const filteredPopupVoters = (voterPopup?.voters || []).filter((voter) => voterMatchesFilters(voter, voterFilters));
    const canFilterResults = poll.options.some((option) => Array.isArray(option.voters) && option.voters.length);
    const resultFiltersActive = resultFilters.gender !== "all" || resultFilters.age !== "all";
    const resultChoiceTotal = poll.choices_count ?? poll.total_votes;
    const filteredResultTotal = canFilterResults && resultFiltersActive
        ? poll.options.reduce((sum, option) => sum + (option.voters || []).filter((voter) => voterMatchesFilters(voter, resultFilters)).length, 0)
        : resultChoiceTotal;
    const resultOptions = poll.options.map((option) => {
        if (!canFilterResults || !resultFiltersActive) {
            return option;
        }
        const voters = (option.voters || []).filter((voter) => voterMatchesFilters(voter, resultFilters));
        const votesCount = voters.length;
        return {
            ...option,
            voters,
            votes_count: votesCount,
            percent: filteredResultTotal ? Math.round((votesCount / filteredResultTotal) * 1000) / 10 : 0,
        };
    });
    const copyLink = async () => {
        await navigator.clipboard?.writeText(shareUrl);
        setLinkCopied(true);
        window.setTimeout(() => setLinkCopied(false), 1800);
    };

    const toggle = (optionId) => {
        if (poll.poll_type === "multiple") {
            setSelected((current) => current.includes(optionId) ? current.filter((id) => id !== optionId) : [...current, optionId]);
        } else {
            setSelected([optionId]);
        }
    };
    const submitComment = async (event) => {
        event.preventDefault();
        const body = commentText.trim();
        if (!body) {
            return;
        }
        const submitted = await onComment(poll, body);
        if (submitted) {
            setCommentText("");
        }
    };

    return (
        <section className="panel poll-detail">
            <div className="poll-detail__head">
                <div className="poll-detail__title">
                    <h2>{poll.title}</h2>
                    <span><UserLink user={poll.creator} onOpen={onOpenUser} /> · {pollEndLabel(poll)}</span>
                </div>
                <div className="poll-detail__side">
                    <div className="poll-detail__topline">
                        <div className="poll-detail__stats" aria-label="Сводка голосования">
                            {showResults ? <span title="Участники"><Icon name="users-round" />{poll.participants}</span> : null}
                            <span title="Просмотры"><Icon name="eye" />{poll.views_count || 0}</span>
                            <span title="Комментарии"><Icon name="message-square" />{poll.comments?.length || 0}</span>
                            {showResults ? <span title="Голоса"><Icon name="chart-no-axes-column-increasing" />{poll.total_votes}</span> : null}
                        </div>
                        <div className="poll-actions poll-actions--compact">
                            <button className="icon-button" type="button" onClick={copyLink} title={linkCopied ? "Ссылка скопирована" : "Поделиться"}>
                                <Icon name={linkCopied ? "check" : "share-2"} />
                            </button>
                            {showHistoryButton ? (
                                <button className="icon-button" type="button" onClick={() => setHistoryOpen(true)} title="История голосования">
                                    <Icon name="history" />
                                </button>
                            ) : null}
                            <button className="icon-button" type="button" onClick={() => setMoreOpen(true)} title="Ещё">
                                <Icon name="ellipsis" />
                            </button>
                        </div>
                    </div>
                    <div className="flag-row flag-row--detail">
                        {flags.map((flag) => <FlagIcon key={flag.key} flag={flag} />)}
                    </div>
                </div>
            </div>

            {poll.description ? <p className="poll-description">{poll.description}</p> : null}
            {descriptionImages.length ? (
                <div className="image-gallery image-gallery--detail">
                    {descriptionImages.map((src, index) => (
                        <ZoomImageButton
                            key={`${src}-${index}`}
                            src={src}
                            alt={poll.title}
                            className="image-zoom-trigger--detail"
                            imageClassName="poll-detail__image"
                            onZoom={setZoomImage}
                        />
                    ))}
                </div>
            ) : null}

            {showVoteForm ? (
                <form className="vote-box" onSubmit={(event) => { event.preventDefault(); onVote(poll, selected); }}>
                    {poll.options.map((option) => {
                        const optionImage = firstImage(option.images, option.image, option.image_url);
                        return (
                            <label key={option.id} className={cx("vote-choice", selected.includes(option.id) && "is-selected")}>
                                <input
                                    type={poll.poll_type === "multiple" ? "checkbox" : "radio"}
                                    checked={selected.includes(option.id)}
                                    onChange={() => toggle(option.id)}
                                />
                                <ZoomImageButton
                                    src={optionImage}
                                    alt={option.text}
                                    className="image-zoom-trigger--option"
                                    imageClassName="option-image"
                                    onZoom={setZoomImage}
                                />
                                <span>{option.text}</span>
                                {(option.images || []).slice(1).length ? (
                                    <div className="option-gallery">
                                        {(option.images || []).slice(1).map((src, imageIndex) => (
                                            <ZoomImageButton
                                                key={`${option.id}-${src}-${imageIndex}`}
                                                src={src}
                                                alt={option.text}
                                                className="image-zoom-trigger--inline"
                                                imageClassName="option-image option-image--sm"
                                                onZoom={setZoomImage}
                                            />
                                        ))}
                                    </div>
                                ) : null}
                            </label>
                        );
                    })}
                    <button className="button button--primary" type="submit" disabled={!selected.length}>
                        <Icon name={auth ? "send" : "log-in"} />
                        {auth ? "Проголосовать" : "Войти и проголосовать"}
                    </button>
                </form>
            ) : null}

            {showResults ? (
                <div className="results">
                    {canFilterResults ? (
                        <div className="result-filters">
                            <div>
                                <strong>Фильтр результатов</strong>
                                <span>{filteredResultTotal} голосов в выборке</span>
                            </div>
                            <select className="select select--compact" value={resultFilters.gender} onChange={(event) => setResultFilters((current) => ({ ...current, gender: event.target.value }))}>
                                <option value="all">Все полы</option>
                                <option value="female">Женский</option>
                                <option value="male">Мужской</option>
                                <option value="other">Другое</option>
                                <option value="unknown">Не указан</option>
                            </select>
                            <select className="select select--compact" value={resultFilters.age} onChange={(event) => setResultFilters((current) => ({ ...current, age: event.target.value }))}>
                                <option value="all">Любой возраст</option>
                                <option value="under18">До 18</option>
                                <option value="18-24">18-24</option>
                                <option value="25-34">25-34</option>
                                <option value="35-44">35-44</option>
                                <option value="45plus">45+</option>
                                <option value="unknown">Не указан</option>
                            </select>
                        </div>
                    ) : null}
                    {resultOptions.map((option) => {
                        const optionImage = firstImage(option.images, option.image, option.image_url);
                        return (
                            <div className="result-row" key={option.id}>
                                <div className="result-row__top">
                                    <strong>
                                        <ZoomImageButton
                                            src={optionImage}
                                            alt={option.text}
                                            className="image-zoom-trigger--inline"
                                            imageClassName="option-image option-image--sm"
                                            onZoom={setZoomImage}
                                        />
                                        {option.text}
                                    </strong>
                                    <button
                                        className="vote-count"
                                        type="button"
                                        disabled={!option.voters?.length}
                                        onClick={() => {
                                            setVoterFilters({ gender: "all", age: "all" });
                                            setVoterPopup(option);
                                        }}
                                        title={option.voters?.length ? "Показать список голосов" : "Список недоступен"}
                                    >
                                        {option.votes_count} · {option.percent}%
                                    </button>
                                </div>
                                <div className="bar"><span style={{ width: `${option.percent}%` }}></span></div>
                            </div>
                        );
                    })}
                </div>
            ) : null}

            <section className="comments-block">
                <div className="section-head">
                    <div>
                        <h2>Комментарии</h2>
                        <span>{poll.comments?.length || 0}</span>
                    </div>
                </div>
                <form className="comment-form" onSubmit={submitComment}>
                    <textarea className="textarea textarea--comment" value={commentText} onChange={(event) => setCommentText(event.target.value)} placeholder="Напишите комментарий" maxLength="1000"></textarea>
                    <button className="button button--primary" type="submit" disabled={!commentText.trim()}>
                        <Icon name={auth ? "message-square-plus" : "log-in"} />
                        {auth ? "Отправить" : "Комментировать"}
                    </button>
                </form>
                <div className="comment-list">
                    {(poll.comments || []).map((comment) => (
                        <article className="comment-item" key={comment.id}>
                            <div>
                                <UserLink user={comment.user} onOpen={onOpenUser} />
                                {auth ? (
                                    <button className="text-tool" type="button" onClick={() => onReport?.({ target_type: "comment", target_id: comment.id, title: `Комментарий: ${comment.body.slice(0, 60)}` })}>
                                        <Icon name="flag" />
                                        Жалоба
                                    </button>
                                ) : null}
                                {auth?.user && (auth.user.role === "admin" || auth.user.id === comment.user.id) ? (
                                    <button className="text-tool text-tool--danger" type="button" onClick={() => onDeleteComment?.(comment.id)}>
                                        <Icon name="trash-2" />
                                        Удалить
                                    </button>
                                ) : null}
                            </div>
                            <p>{comment.body}</p>
                            <time>{formatDate(comment.created_at)}</time>
                        </article>
                    ))}
                    {!poll.comments?.length ? <EmptyState icon="messages-square" title="Комментариев пока нет" /> : null}
                </div>
            </section>

            {voterPopup ? (
                <div className="modal-layer" role="presentation" onClick={() => setVoterPopup(null)}>
                    <div className="vote-popover" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
                        <div className="vote-popover__head">
                            <div>
                                <strong>{voterPopup.text}</strong>
                                <span>{filteredPopupVoters.length} из {voterPopup.voters.length} голосов</span>
                            </div>
                            <button className="icon-button" type="button" onClick={() => setVoterPopup(null)} title="Закрыть">
                                <Icon name="x" />
                            </button>
                        </div>
                        <div className="voter-filters">
                            <select className="select select--compact" value={voterFilters.gender} onChange={(event) => setVoterFilters((current) => ({ ...current, gender: event.target.value }))}>
                                <option value="all">Все полы</option>
                                <option value="female">Женский</option>
                                <option value="male">Мужской</option>
                                <option value="other">Другое</option>
                                <option value="unknown">Не указан</option>
                            </select>
                            <select className="select select--compact" value={voterFilters.age} onChange={(event) => setVoterFilters((current) => ({ ...current, age: event.target.value }))}>
                                <option value="all">Любой возраст</option>
                                <option value="under18">До 18</option>
                                <option value="18-24">18-24</option>
                                <option value="25-34">25-34</option>
                                <option value="35-44">35-44</option>
                                <option value="45plus">45+</option>
                                <option value="unknown">Не указан</option>
                            </select>
                        </div>
                        <div className="voter-list">
                            {filteredPopupVoters.map((voter) => (
                                <div className="voter-item" key={voter.vote_id}>
                                    <UserLink userId={voter.user_id} username={voter.username} profileImage={voter.profile_image} authProvider={voter.auth_provider} onOpen={onOpenUser} />
                                    <span>{formatDate(voter.voted_at)}</span>
                                </div>
                            ))}
                            {!filteredPopupVoters.length ? <EmptyState icon="users-round" title="Под этот фильтр никто не подходит" /> : null}
                        </div>
                    </div>
                </div>
            ) : null}
            {historyOpen ? (
                <HistoryModal poll={poll} onClose={() => setHistoryOpen(false)} onOpenUser={onOpenUser} />
            ) : null}
            {auditOpen ? (
                <AuditModal poll={poll} onClose={() => setAuditOpen(false)} onOpenUser={onOpenUser} />
            ) : null}
            {moreOpen ? (
                <PollMoreModal
                    poll={poll}
                    auth={auth}
                    onClose={() => setMoreOpen(false)}
                    onReport={() => {
                        setMoreOpen(false);
                        onReport?.({ target_type: "poll", target_id: poll.id, title: poll.title });
                    }}
                    onManage={() => {
                        setMoreOpen(false);
                        setManageOpen(true);
                    }}
                />
            ) : null}
            {manageOpen ? (
                <ManagePollModal
                    poll={poll}
                    onClose={() => setManageOpen(false)}
                    onAudit={() => {
                        setManageOpen(false);
                        setAuditOpen(true);
                    }}
                    onResultsSettings={onResultsSettings}
                    onExport={onExport}
                    onManage={(action) => {
                        setManageOpen(false);
                        onManage(poll, action);
                    }}
                />
            ) : null}
            {zoomImage ? <ImageZoomModal image={zoomImage} onClose={() => setZoomImage(null)} /> : null}
        </section>
    );
}

function HistoryModal({ poll, onClose, onOpenUser }) {
    const rows = Number(poll.anonymity_level) === 0
        ? (poll.public_votes || []).map((vote) => ({
            key: `vote-${vote.id}`,
            user: <UserLink userId={vote.user_id} username={vote.user} profileImage={vote.profile_image} onOpen={onOpenUser} />,
            detail: vote.option,
            time: formatDate(vote.voted_at),
        }))
        : (poll.participation_log || []).map((log) => ({
            key: `log-${log.id}`,
            user: <UserLink userId={log.user_id} username={log.user} profileImage={log.profile_image} onOpen={onOpenUser} />,
            detail: Number(poll.anonymity_level) === 1 ? "Выбор скрыт" : "Участие зафиксировано",
            time: formatDate(log.voted_at),
        }));

    return (
        <div className="modal-layer" role="presentation" onClick={onClose}>
            <div className="vote-popover history-popover" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
                <div className="vote-popover__head">
                    <div>
                        <strong>История голосования</strong>
                        <span>{anonymityLabel(poll.anonymity_level)}</span>
                    </div>
                    <button className="icon-button" type="button" onClick={onClose} title="Закрыть">
                        <Icon name="x" />
                    </button>
                </div>
                <div className="history-list">
                    {rows.map((row) => (
                        <div className="history-item" key={row.key}>
                            <div>{row.user}<span>{row.detail}</span></div>
                            <time>{row.time}</time>
                        </div>
                    ))}
                    {!rows.length ? <EmptyState icon="history" title="История пока пустая" /> : null}
                </div>
            </div>
        </div>
    );
}

function PollMoreModal({ poll, auth, onClose, onReport, onManage }) {
    return (
        <div className="modal-layer" role="presentation" onClick={onClose}>
            <div className="vote-popover manage-popover" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
                <div className="vote-popover__head">
                    <div>
                        <strong>Ещё</strong>
                        <span>{poll.title}</span>
                    </div>
                    <button className="icon-button" type="button" onClick={onClose} title="Закрыть">
                        <Icon name="x" />
                    </button>
                </div>
                <div className="manage-grid">
                    {auth ? (
                        <button className="button button--ghost" type="button" onClick={onReport}>
                            <Icon name="flag" />
                            Жалоба
                        </button>
                    ) : null}
                    {poll.can_manage ? (
                        <button className="button button--ghost" type="button" onClick={onManage}>
                            <Icon name="settings" />
                            Управление
                        </button>
                    ) : null}
                    {!auth && !poll.can_manage ? <EmptyState icon="ellipsis" title="Действий пока нет" /> : null}
                </div>
            </div>
        </div>
    );
}

function ManagePollModal({ poll, onClose, onAudit, onResultsSettings, onExport, onManage }) {
    return (
        <div className="modal-layer" role="presentation" onClick={onClose}>
            <div className="vote-popover manage-popover" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
                <div className="vote-popover__head">
                    <div>
                        <strong>Управление опросом</strong>
                        <span>{poll.title}</span>
                    </div>
                    <button className="icon-button" type="button" onClick={onClose} title="Закрыть">
                        <Icon name="x" />
                    </button>
                </div>

                <div className="manage-section">
                    <div className="field">
                        <label>Публикация результатов</label>
                        <select
                            className="select"
                            value={poll.results_visibility === "always" ? "after_end" : poll.results_visibility}
                            onChange={(event) => onResultsSettings(poll, { results_visibility: event.target.value })}
                        >
                            <option value="after_end">После голосования</option>
                            <option value="manual">После ручной публикации</option>
                            <option value="hidden">Скрыть от участников</option>
                        </select>
                    </div>
                    {poll.results_visibility === "manual" ? (
                        <button className="button button--ghost button--wide" type="button" onClick={() => onResultsSettings(poll, { results_published: !poll.results_published })}>
                            <Icon name={poll.results_published ? "eye-off" : "eye"} />
                            {poll.results_published ? "Снять публикацию" : "Опубликовать результаты"}
                        </button>
                    ) : null}
                </div>

                <div className="manage-grid">
                    <button className="button button--ghost" type="button" onClick={onAudit}>
                        <Icon name="clipboard-list" />
                        Аудит
                    </button>
                    <button className="button button--ghost" type="button" onClick={() => onExport(poll, "csv")}>
                        <Icon name="file-spreadsheet" />
                        CSV
                    </button>
                    <button className="button button--ghost" type="button" onClick={() => onExport(poll, "pdf")}>
                        <Icon name="file-text" />
                        PDF
                    </button>
                    <button className="button button--ghost" type="button" onClick={() => onManage(poll.is_active ? "complete" : "activate")}>
                        <Icon name={poll.is_active ? "circle-check" : "play"} />
                        {poll.is_active ? "Завершить" : "Активировать"}
                    </button>
                    <button className="button button--danger" type="button" onClick={() => onManage("delete")} disabled={poll.is_archived}>
                        <Icon name="archive" />
                        {poll.is_archived ? "В архиве" : "В архив"}
                    </button>
                </div>
            </div>
        </div>
    );
}

function AuditModal({ poll, onClose, onOpenUser }) {
    const [filter, setFilter] = useState("all");
    const logs = poll.audit_logs || [];
    const filteredLogs = logs.filter((log) => (
        filter === "all" ||
        (filter === "votes" && log.category === "vote") ||
        (filter === "changes" && log.category !== "vote")
    ));

    const renderAuditDetails = (log) => {
        const details = log.details || {};
        if (log.category === "vote") {
            const options = details.options || [];
            return (
                <div className="audit-details">
                    {details.voter ? <span>Голосующий: <UserLink user={details.voter} onOpen={onOpenUser} /></span> : <span>Голосующий скрыт</span>}
                    {options.length ? <span>Выбор: <strong>{options.map((option) => option.text).join(", ")}</strong></span> : <span>Выбор скрыт</span>}
                </div>
            );
        }

        const changes = details.changes || [];
        if (changes.length) {
            return (
                <div className="audit-change-list">
                    {changes.map((change) => (
                        <div className="audit-change" key={`${log.id}-${change.field}`}>
                            <strong>{change.label || change.field}</strong>
                            <span>{String(change.old ?? "пусто")} → {String(change.new ?? "пусто")}</span>
                        </div>
                    ))}
                </div>
            );
        }

        const snapshot = log.snapshot || {};
        return (
            <>
                <div className="audit-grid">
                    <span>Доступ: <strong>{accessLabel(snapshot.access_type)}</strong></span>
                    <span>Анонимность: <strong>{anonymityLabel(snapshot.anonymity_level)}</strong></span>
                    <span>Выбор: <strong>{snapshot.selection_type === "multiple" ? "несколько" : "один"}</strong></span>
                    <span>Результаты: <strong>{resultsVisibilityLabel(snapshot.results_visibility)}</strong></span>
                    <span>Лимит: <strong>{snapshot.max_votes || "нет"}</strong></span>
                    <span>Опубликовано: <strong>{snapshot.results_published ? "да" : "нет"}</strong></span>
                </div>
                <div className="audit-options">
                    {(snapshot.options || []).map((option, index) => <Badge key={`${log.id}-${index}`} tone="neutral">{option}</Badge>)}
                </div>
            </>
        );
    };

    return (
        <div className="modal-layer" role="presentation" onClick={onClose}>
            <div className="vote-popover audit-popover" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
                <div className="vote-popover__head">
                    <div>
                        <strong>Аудит опроса</strong>
                        <span>{filteredLogs.length} из {logs.length} записей</span>
                    </div>
                    <button className="icon-button" type="button" onClick={onClose} title="Закрыть">
                        <Icon name="x" />
                    </button>
                </div>
                <div className="tabs tabs--compact">
                    <button className={cx(filter === "all" && "is-active")} type="button" onClick={() => setFilter("all")}>Все</button>
                    <button className={cx(filter === "votes" && "is-active")} type="button" onClick={() => setFilter("votes")}>Голоса</button>
                    <button className={cx(filter === "changes" && "is-active")} type="button" onClick={() => setFilter("changes")}>Изменения</button>
                </div>
                <div className="history-list">
                    {filteredLogs.map((log) => (
                        <article className="audit-item" key={log.id}>
                            <div className="audit-item__top">
                                <strong>{auditActionLabel(log.action)}</strong>
                                <time>{formatDate(log.created_at)}</time>
                            </div>
                            <div className="audit-actor">
                                {log.actor ? <UserLink user={log.actor} onOpen={onOpenUser} /> : <span>system</span>}
                            </div>
                            {renderAuditDetails(log)}
                        </article>
                    ))}
                    {!filteredLogs.length ? <EmptyState icon="clipboard-list" title="Записей нет" /> : null}
                </div>
            </div>
        </div>
    );
}

function ReportModal({ target, onClose, onSubmit }) {
    const [reason, setReason] = useState("Нарушение правил");
    const [body, setBody] = useState("");
    return (
        <div className="modal-layer" role="presentation" onClick={onClose}>
            <section className="auth-dialog" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
                <div className="vote-popover__head">
                    <div>
                        <strong>Жалоба</strong>
                        <span>{target.title}</span>
                    </div>
                    <button className="icon-button" type="button" onClick={onClose} title="Закрыть">
                        <Icon name="x" />
                    </button>
                </div>
                <form className="auth-form" onSubmit={(event) => {
                    event.preventDefault();
                    onSubmit({ target_type: target.target_type, target_id: target.target_id, reason, body });
                }}>
                    <label>Причина
                        <select className="select" value={reason} onChange={(event) => setReason(event.target.value)}>
                            <option>Нарушение правил</option>
                            <option>Оскорбление или спам</option>
                            <option>Недостоверная информация</option>
                            <option>Нарушение персональных данных</option>
                            <option>Другое</option>
                        </select>
                    </label>
                    <label>Комментарий<textarea className="textarea" rows="4" value={body} maxLength="1000" onChange={(event) => setBody(event.target.value)}></textarea></label>
                    <button className="button button--primary button--wide" type="submit">
                        <Icon name="flag" />
                        Отправить жалобу
                    </button>
                </form>
            </section>
        </div>
    );
}

function LegalPage({ type }) {
    const isPrivacy = type === "privacy";
    return (
        <section className="panel legal-page">
            <div className="section-head">
                <div>
                    <h2>{isPrivacy ? "Политика конфиденциальности и обработки персональных данных" : "Пользовательское соглашение и правила сервиса"}</h2>
                    <span>Дата вступления в силу: 26 мая 2026 года · действующая редакция учебного проекта eVote</span>
                </div>
            </div>
            {isPrivacy ? (
                <div className="legal-text">
                    <p className="legal-note"><strong>Назначение документа.</strong> Настоящая политика определяет порядок, цели и технические особенности обработки персональных данных в рамках учебной экосистемы eVote. Безопасность данных обеспечивается программными методами, заложенными в архитектуру Flask-приложения.</p>

                    <h3>1. Общие положения</h3>
                    <p>1.1. Обработка персональных данных пользователей осуществляется администратором учебного проекта на принципах законности, добровольности, минимизации и прозрачности исключительно для обеспечения работоспособности сервиса eVote.</p>
                    <p>1.2. Прохождение регистрации, авторизации через Яндекс ID или использование debug-входа в учебной среде означает согласие пользователя с настоящей политикой и указанными в ней условиями обработки информации.</p>

                    <h3>2. Состав и категории обрабатываемых данных</h3>
                    <table className="legal-table">
                        <tbody>
                            <tr>
                                <th>Категория</th>
                                <th>Состав данных</th>
                                <th>Назначение</th>
                            </tr>
                            <tr>
                                <td>Обязательные учетные данные</td>
                                <td>Никнейм, системная роль, дата регистрации, идентификатор пользователя; при парольной авторизации — хэш пароля; при входе через Яндекс — идентификатор, email, имя, фамилия и аватар, если они переданы провайдером.</td>
                                <td>Идентификация, аутентификация, разграничение прав доступа и отображение пользователя в интерфейсе.</td>
                            </tr>
                            <tr>
                                <td>Анкетные данные</td>
                                <td>Дата рождения, пол, город и аватар, если такие сведения уже были переданы пользователем или внешним провайдером авторизации.</td>
                                <td>Заполнение профиля, отображение отметки заполненности и автоматический расчет возраста.</td>
                            </tr>
                            <tr>
                                <td>Системный и контентный след</td>
                                <td>Созданные опросы, факты участия в голосованиях, комментарии, жалобы, обращения в поддержку, служебные статусы и записи аудита.</td>
                                <td>Предотвращение повторного голосования, аудит действий, обработка жалоб, поддержка пользователей и модерация.</td>
                            </tr>
                        </tbody>
                    </table>

                    <h3>3. Цели и правовые основания обработки</h3>
                    <p>3.1. Обработка данных осуществляется на основании согласия пользователя и необходимости исполнения пользовательского соглашения.</p>
                    <ul>
                        <li>обеспечение регистрации, входа и управления пользовательской сессией;</li>
                        <li>техническое ограничение повторного голосования;</li>
                        <li>отображение профиля, аватара, комментариев и пользовательского контента;</li>
                        <li>рассмотрение жалоб, обращений в поддержку и служебных комментариев администратора;</li>
                        <li>обеспечение кибербезопасности учебного проекта и защита от спам-атак.</li>
                    </ul>

                    <h3>4. Архитектурные механизмы анонимности голосований</h3>
                    <p>4.1. eVote поддерживает несколько уровней конфиденциальности голосований. В анонимных режимах публичное отображение связи конкретного пользователя с выбранным вариантом блокируется на уровне серверной выдачи и клиентского интерфейса.</p>
                    <p>4.2. Техническая запись о факте участия может сохраняться отдельно от публичных результатов либо в виде хэшированного идентификатора. Она используется для контроля уникальности голоса и предотвращения повторного участия.</p>

                    <h3>5. Технические меры защиты</h3>
                    <p>5.1. Пароли пользователей не хранятся в открытом виде. При парольной авторизации хранение осуществляется в виде криптографических хэшей на стороне бэкенда.</p>
                    <p>5.2. Для изменяющих запросов используется проверка CSRF-токенов и авторизационных токенов, что снижает риск выполнения несанкционированных действий от имени пользователя.</p>
                    <p>5.3. При загрузке изображений сервис выполняет проверку допустимого формата и бинарной сигнатуры файла, чтобы ограничить загрузку вредоносных или неподдерживаемых файлов.</p>
                    <p>5.4. Доступ к административным функциям ограничен ролью администратора.</p>

                    <h3>6. Права пользователя и уничтожение данных</h3>
                    <p>6.1. Пользователь может изменить никнейм, аватар и настройку скрытия собственной активности через личный кабинет. Анкетные данные, полученные ранее или от провайдера авторизации, не редактируются вручную в интерфейсе; запрос на исправление или удаление таких данных может быть направлен через поддержку.</p>
                    <p>6.2. Пользователь вправе отозвать согласие на обработку данных путем обращения в поддержку. Администратор рассматривает запрос и при технической возможности удаляет учетную запись либо обезличивает связанные с ней персональные данные в срок до 30 дней, за исключением обезличенных агрегированных результатов уже завершенных голосований и данных, которые необходимо сохранить для защиты сервиса от злоупотреблений.</p>
                </div>
            ) : (
                <div className="legal-text">
                    <p className="legal-note"><strong>Важное примечание.</strong> eVote является некоммерческим учебным проектом, созданным в рамках дипломной работы. Функционал предоставляется безвозмездно по принципу «как есть». Результаты голосований не имеют юридической, государственной, муниципальной или корпоративной силы.</p>

                    <h3>1. Термины и определения</h3>
                    <p>1.1. <strong>Сервис</strong> — интерактивная программная система электронного голосования eVote, доступная в сети Интернет и разработанная на базе Python, Flask, React, SQLAlchemy и PostgreSQL.</p>
                    <p>1.2. <strong>Администратор</strong> — создатель и сопровождающее лицо учебного проекта, осуществляющее техническое обслуживание, модерацию и управление сервисом.</p>
                    <p>1.3. <strong>Пользователь</strong> — физическое лицо, прошедшее авторизацию или регистрацию и получившее доступ к функциям сервиса.</p>
                    <p>1.4. <strong>Голосование или опрос</strong> — электронный объект, содержащий вопрос, варианты ответов, настройки доступа, анонимности, публикации результатов и срока действия.</p>

                    <h3>2. Предмет соглашения и условия использования</h3>
                    <p>2.1. Администратор предоставляет пользователю простое, неисключительное, безвозмездное и отзывное право использовать сервис в ознакомительных, исследовательских и демонстрационных целях.</p>
                    <p>2.2. Пользователь может использовать следующие функции:</p>
                    <ul>
                        <li>создание электронных голосований и опросов различных конфигураций;</li>
                        <li>участие в доступных публичных или ограниченных голосованиях;</li>
                        <li>размещение комментариев, текстовых материалов и изображений;</li>
                        <li>просмотр статистических результатов в соответствии с настройками конкретного опроса;</li>
                        <li>направление жалоб на спорный контент и обращений в поддержку.</li>
                    </ul>
                    <p>2.3. Использование сервиса является бесплатным. Администратор не взимает плату за регистрацию, вычислительные ресурсы или доступ к учебному функционалу.</p>

                    <h3>3. Регистрация и безопасность учетной записи</h3>
                    <p>3.1. Для полноценного использования eVote пользователь проходит регистрацию, авторизацию через Яндекс ID или использует debug-вход, если он включен в учебной среде.</p>
                    <p>3.2. Пользователь отвечает за сохранность учетных данных. Все действия, выполненные из учетной записи пользователя, считаются действиями этого пользователя.</p>
                    <p>3.3. Пользователю запрещается передавать доступ к учетной записи третьим лицам, пытаться получить несанкционированный доступ к чужим профилям или обходить механизмы авторизации.</p>

                    <h3>4. Размещение контента и создание голосований</h3>
                    <p>4.1. Пользователь самостоятельно определяет содержание создаваемых голосований, комментариев и загружаемых изображений.</p>
                    <p>4.2. Пользователю запрещается:</p>
                    <ul>
                        <li>публиковать оскорбительные, клеветнические, дискриминационные, заведомо ложные или вводящие в заблуждение материалы;</li>
                        <li>размещать спам, несанкционированную рекламу, вредоносный код и ссылки на фишинговые ресурсы;</li>
                        <li>нарушать авторские и смежные права третьих лиц при загрузке изображений;</li>
                        <li>публиковать конфиденциальную информацию, пароли, государственные тайны или персональные данные третьих лиц без их явного согласия.</li>
                    </ul>

                    <h3>5. Участие в голосованиях и защита от злоупотреблений</h3>
                    <p>5.1. Пользователь обязуется участвовать в голосованиях добросовестно и не искажать результаты искусственными способами.</p>
                    <p>5.2. Запрещены действия, направленные на обход ограничений сервиса, включая использование скриптов, ботов, поддельных учетных записей, повторное голосование, вмешательство в базу данных, SQL-инъекции и эксплуатацию уязвимостей программного кода.</p>

                    <h3>6. Права администратора и модерация</h3>
                    <p>6.1. Администратор управляет сервисом, ролями пользователей, опросами, комментариями, жалобами и обращениями поддержки.</p>
                    <p>6.2. При выявлении нарушений администратор может заблокировать пользователя, удалить комментарий, удалить опрос, изменить статус жалобы или обращения, а также ограничить доступ к отдельным функциям сервиса.</p>
                    <p>6.3. Пользователь может обратиться в поддержку с просьбой деактивировать профиль или удалить персональные данные. Такие обращения рассматриваются администратором вручную.</p>

                    <h3>7. Отказ от гарантий и ограничение ответственности</h3>
                    <p>7.1. Сервис предоставляется по принципу «как есть». Администратор не гарантирует бесперебойную, безошибочную и постоянную работу eVote.</p>
                    <p>7.2. eVote не предназначен для проведения юридически значимых государственных, муниципальных, корпоративных или иных официальных выборов и референдумов.</p>
                    <p>7.3. В максимально допустимой законом степени пользователь соглашается, что администратор учебного проекта не отвечает за убытки, упущенную выгоду, технические сбои, потерю данных или недоступность сервиса, возникшие при использовании eVote.</p>
                </div>
            )}
        </section>
    );
}

function SupportCenter({ auth, tickets, onCreate, onSend }) {
    const [subject, setSubject] = useState("");
    const [body, setBody] = useState("");
    const [creating, setCreating] = useState(false);
    const [activeId, setActiveId] = useState(null);
    const [reply, setReply] = useState("");
    const activeTicket = tickets.find((ticket) => ticket.id === activeId) || tickets[0];

    useEffect(() => {
        if (activeId && !tickets.some((ticket) => ticket.id === activeId)) {
            setActiveId(null);
        }
    }, [activeId, tickets]);

    if (!auth) {
        return <EmptyState icon="lock-keyhole" title="Войдите, чтобы написать в поддержку" />;
    }

    return (
        <section className="panel support-panel">
            <div className="section-head">
                <div>
                    <h2>Мои обращения</h2>
                    <span>{tickets.length}</span>
                </div>
                <button className="button button--primary" type="button" onClick={() => setCreating((value) => !value)}>
                    <Icon name={creating ? "x" : "message-square-plus"} />
                    {creating ? "Отмена" : "Новое обращение"}
                </button>
            </div>
            {creating ? (
                <form className="support-create auth-form" onSubmit={(event) => {
                    event.preventDefault();
                    onCreate({ subject: subject.trim(), body: body.trim() });
                    setSubject("");
                    setBody("");
                    setCreating(false);
                }}>
                    <label>Тема<input className="input" value={subject} maxLength="160" onChange={(event) => setSubject(event.target.value)} /></label>
                    <label>Сообщение<textarea className="textarea" rows="4" value={body} maxLength="2000" onChange={(event) => setBody(event.target.value)}></textarea></label>
                    <button className="button button--primary" type="submit" disabled={!subject.trim() || !body.trim()}>
                        <Icon name="send" />
                        Создать чат
                    </button>
                </form>
            ) : null}
            <div className="support-layout support-layout--wide">
                <div className="support-list">
                    {tickets.map((ticket) => (
                        <button className={cx("admin-row admin-row--button", activeTicket?.id === ticket.id && "is-selected")} type="button" key={ticket.id} onClick={() => setActiveId(ticket.id)}>
                            <div>
                                <strong>{ticket.subject}</strong>
                                <span>{formatDate(ticket.updated_at)} · {ticket.status}</span>
                            </div>
                        </button>
                    ))}
                    {!tickets.length ? <EmptyState icon="message-circle-question" title="Обращений пока нет" /> : null}
                </div>
                {activeTicket ? (
                    <div className="chat-panel">
                        <div className="chat-messages">
                            {(activeTicket.messages || []).map((message) => (
                                <div className={cx("chat-message", message.sender.id === auth.user.id && "is-own")} key={message.id}>
                                    <strong>{message.sender.username}</strong>
                                    <p>{message.body}</p>
                                    <span>{formatDate(message.created_at)}</span>
                                </div>
                            ))}
                        </div>
                        <form className="comment-form" onSubmit={(event) => {
                            event.preventDefault();
                            if (reply.trim()) {
                                onSend(activeTicket, reply.trim());
                                setReply("");
                            }
                        }}>
                            <textarea className="textarea textarea--comment" value={reply} onChange={(event) => setReply(event.target.value)} placeholder="Ответить" maxLength="2000"></textarea>
                            <button className="button button--primary" type="submit" disabled={!reply.trim()}><Icon name="send" />Ответить</button>
                        </form>
                    </div>
                ) : null}
            </div>
        </section>
    );
}

function DebugPage({ auth, onLogin }) {
    return (
        <section className="panel debug-page">
            <div className="section-head">
                <div>
                    <h2>Debug-вход</h2>
                    <span>локальная авторизация без Яндекс ID</span>
                </div>
                {auth?.user ? <Badge tone={auth.user.role === "admin" ? "blue" : "green"}>{auth.user.username}</Badge> : null}
            </div>
            <div className="debug-actions">
                <button className="button button--primary" type="button" onClick={() => onLogin("user")}>
                    <Icon name="user" />
                    Войти как пользователь
                </button>
                <button className="button button--ghost" type="button" onClick={() => onLogin("admin")}>
                    <Icon name="shield-check" />
                    Войти как админ
                </button>
            </div>
        </section>
    );
}

function AuthModal({ error = "", onClose, onAuth }) {
    const vkContainerRef = useRef(null);
    const [vkError, setVkError] = useState("");

    useEffect(() => {
        let active = true;
        const initVk = async () => {
            try {
                const config = await requestJson("/api/auth/config");
                if (!active || !config.vk_client_id || !config.vk_sdk_url || !vkContainerRef.current) {
                    return;
                }
                const vkAuthState = getOrCreateVkAuthState(true);
                const VKID = await getVkSdk(config, vkAuthState);
                if (!active || !vkContainerRef.current) {
                    return;
                }
                vkContainerRef.current.innerHTML = "";
                const oneTap = new VKID.OneTap();
                oneTap.render({
                    container: vkContainerRef.current,
                    fastAuthEnabled: false,
                    showAlternativeLogin: true,
                })
                    .on(VKID.WidgetEvents.ERROR, () => setVkError("Не удалось открыть VK ID."))
                    .on(VKID.OneTapInternalEvents.LOGIN_SUCCESS, async (payload) => {
                        try {
                            await onAuth?.("vk", await exchangeVkAuthCode(config, payload.code, payload.device_id, payload.state));
                        } catch (error) {
                            setVkError(errorText(error, "Не удалось выполнить вход через VK ID."));
                        }
                    });
            } catch (error) {
                if (active) {
                    setVkError(errorText(error, "VK ID не настроен."));
                }
            }
        };
        initVk();
        return () => {
            active = false;
        };
    }, [onAuth]);

    return (
        <div className="modal-layer modal-layer--auth" role="presentation" onClick={onClose}>
            <section className="auth-dialog" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
                <div className="vote-popover__head">
                    <div>
                        <strong>Вход</strong>
                        <span>Единый профиль для голосования</span>
                    </div>
                    <button className="icon-button" type="button" onClick={onClose} title="Закрыть">
                        <Icon name="x" />
                    </button>
                </div>
                <div className="auth-form auth-form--yandex">
                    <a className="button button--primary button--wide" href="/auth/yandex">
                        <Icon name="key-round" />
                        Продолжить с Яндекс ID
                    </a>
                    {error ? <p className="auth-error-line auth-error-line--modal">{error}</p> : null}
                    <div className="vk-auth-box" ref={vkContainerRef}>
                        <span>Загрузка VK ID...</span>
                    </div>
                    {vkError ? <p className="auth-error-line">{vkError}</p> : null}
                    <p className="auth-legal-line">
                        Продолжая, вы соглашаетесь с <a href="/terms">Пользовательским соглашением</a> и <a href="/privacy">Политикой обработки персональных данных</a>
                    </p>
                </div>
            </section>
        </div>
    );
}

function Profile({ auth, activity, onAvatar, onPrivacy, onUsername, onOpenPoll }) {
    const avatarInputRef = useRef(null);
    const [username, setUsername] = useState(auth.user.username || "");
    useEffect(() => {
        setUsername(auth.user.username || "");
    }, [auth.user.id, auth.user.username]);
    const handleAvatarChange = (event) => {
        const file = event.target.files?.[0];
        if (file) {
            onAvatar(file);
            event.target.value = "";
        }
    };
    const trimmedUsername = username.trim();
    const usernameChanged = trimmedUsername !== auth.user.username;

    return (
        <div className="profile-grid">
            <section className="panel profile-card">
                <button className="profile-avatar-button" type="button" onClick={() => avatarInputRef.current?.click()} title="Сменить аватар">
                    <Avatar user={auth.user} size="lg" />
                    <span className="profile-avatar-button__overlay"><Icon name="image-up" /></span>
                </button>
                <input
                    ref={avatarInputRef}
                    className="sr-only"
                    type="file"
                    accept="image/png,image/jpeg,image/gif,image/webp"
                    onChange={handleAvatarChange}
                />
                <h2>{auth.user.username}</h2>
                <Badge tone={auth.user.role === "admin" ? "blue" : "neutral"}>{auth.user.role}</Badge>
                <span>{formatDate(auth.user.created_at)}</span>
                <label className="privacy-toggle">
                    <input
                        className="sr-only"
                        type="checkbox"
                        checked={Boolean(auth.user.hide_activity)}
                        onChange={(event) => onPrivacy(event.target.checked)}
                    />
                    <span className="toggle-control" aria-hidden="true"><span></span></span>
                    <span className="privacy-toggle__text">
                        <strong>Скрыть активность</strong>
                        <small>Профиль не будет показывать участия в опросах другим пользователям.</small>
                    </span>
                </label>
            </section>
            <section className="panel">
                <div className="section-head">
                    <div>
                        <h2>Никнейм</h2>
                        <span>Отображается в опросах, комментариях и профиле.</span>
                    </div>
                </div>
                <form className="auth-form" onSubmit={(event) => { event.preventDefault(); if (usernameChanged) onUsername(trimmedUsername); }}>
                    <label>Никнейм<input className="input" value={username} minLength="3" maxLength="80" onChange={(event) => setUsername(event.target.value)} /></label>
                    <button className="button button--primary" type="submit" disabled={!usernameChanged || trimmedUsername.length < 3}>
                        <Icon name="save" />
                        Сохранить
                    </button>
                </form>
            </section>
            <section className="panel">
                <div className="section-head">
                    <div>
                        <h2>История участия</h2>
                        <span className="count-pill">{activity.length} записей</span>
                    </div>
                </div>
                <div className="activity-list">
                    {activity.map((item, index) => (
                        <button className="activity-item activity-item--button" type="button" key={index} onClick={() => onOpenPoll(item.poll)}>
                            <strong>{item.poll.title}</strong>
                            <span>{formatDate(item.voted_at)}</span>
                        </button>
                    ))}
                    {!activity.length ? <EmptyState title="Пока пусто" icon="history" /> : null}
                </div>
            </section>
        </div>
    );
}

function UserProfile({ profile, auth, onOpenPoll, onOpenUser, onReport }) {
    const [tab, setTab] = useState("recent");
    if (!profile) {
        return <EmptyState icon="user-round" title="Профиль не загружен" />;
    }

    const user = profile.user;
    const createdPolls = profile.created_polls || [];
    const participatedPolls = profile.participated_polls || [];
    const recentActivity = profile.recent_activity || [];
    const listTitle = {
        recent: "Последняя активность",
        created: "Созданные опросы",
        participated: "Участия",
    }[tab];
    const pollRows = tab === "created" ? createdPolls : participatedPolls;

    return (
        <div className="profile-grid">
            <section className="panel profile-card">
                <Avatar user={user} size="lg" />
                <h2><UserLink user={user} onOpen={onOpenUser} /></h2>
                <Badge tone={user.role === "admin" ? "blue" : "neutral"}>{user.role}</Badge>
                <span>{formatDate(user.created_at)}</span>
                {auth && auth.user.id !== user.id ? (
                    <button className="button button--ghost button--wide" type="button" onClick={() => onReport?.({ target_type: "user", target_id: user.id, title: user.username })}>
                        <Icon name="flag" />
                        Жалоба на профиль
                    </button>
                ) : null}
            </section>
            <section className="panel">
                <div className="section-head">
                    <div>
                        <h2>Публичный профиль</h2>
                        <span>{profile.visible_created_count} опросов видно</span>
                    </div>
                </div>
                <div className="summary-strip">
                    <span><strong>{profile.created_count}</strong> создано</span>
                    <span><strong>{profile.participation_count}</strong> участий</span>
                    <span><strong>{profile.public_votes_count}</strong> открытых голосов</span>
                </div>
                {profile.activity_hidden ? (
                    <div className="privacy-note">
                        <Icon name="eye-off" />
                        Активность скрыта пользователем.
                    </div>
                ) : null}
                <div className="profile-tabs" role="radiogroup" aria-label="Раздел профиля">
                    <label className={cx(tab === "recent" && "is-active")}>
                        <input type="radio" name="profile-tab" checked={tab === "recent"} onChange={() => setTab("recent")} />
                        Последняя активность
                    </label>
                    <label className={cx(tab === "created" && "is-active")}>
                        <input type="radio" name="profile-tab" checked={tab === "created"} onChange={() => setTab("created")} />
                        {profile.created_count} создано
                    </label>
                    <label className={cx(tab === "participated" && "is-active")}>
                        <input type="radio" name="profile-tab" checked={tab === "participated"} onChange={() => setTab("participated")} />
                        {profile.participation_count} участий
                    </label>
                </div>
                <div className="section-head section-head--compact">
                    <div>
                        <h2>{listTitle}</h2>
                        <span>{tab === "recent" ? recentActivity.length : pollRows.length}</span>
                    </div>
                </div>
                <div className="profile-polls">
                    {tab === "recent" ? recentActivity.map((item, index) => (
                        <button className="admin-row admin-row--button" type="button" key={`${item.poll.id}-${index}`} onClick={() => onOpenPoll(item.poll)}>
                            <div>
                                <strong>{item.poll.title}</strong>
                                <span>{formatDate(item.voted_at)} · {item.poll.total_votes} голосов</span>
                            </div>
                            <Badge tone={pollStatus(item.poll).tone}>{pollStatus(item.poll).label}</Badge>
                        </button>
                    )) : pollRows.map((poll) => (
                        <button className="admin-row admin-row--button" type="button" key={poll.id} onClick={() => onOpenPoll(poll)}>
                            <div>
                                <strong>{poll.title}</strong>
                                <span>{poll.total_votes} голосов · {poll.comments_count || 0} комментариев</span>
                            </div>
                            <Badge tone={pollStatus(poll).tone}>{pollStatus(poll).label}</Badge>
                        </button>
                    ))}
                    {tab === "recent" && !recentActivity.length ? <EmptyState title="Активности пока нет" icon="history" /> : null}
                    {tab !== "recent" && !pollRows.length ? <EmptyState title="Нет доступных опросов" icon="folder-open" /> : null}
                </div>
            </section>
        </div>
    );
}

const reportStatusLabels = {
    pending: "Новая",
    reviewing: "В работе",
    resolved: "Решена",
    rejected: "Отклонена",
};

const reportTargetLabels = {
    poll: "Опрос",
    comment: "Комментарий",
    user: "Профиль",
};

function Admin({ users, polls, reports = [], tickets = [], auth, onRoleChange, onBlockUser, onManage, onOpen, onDeleteComment, onOpenUser, onReviewReport, onSendSupport, onSupportStatus }) {
    const [section, setSection] = useState("users");
    const [query, setQuery] = useState("");
    const [pollSort, setPollSort] = useState({ field: "created_at", dir: "desc" });
    const [reportNotes, setReportNotes] = useState({});
    const [ticketReplies, setTicketReplies] = useState({});
    if (auth?.user?.role !== "admin") {
        return <EmptyState icon="shield-alert" title="Недостаточно прав" />;
    }

    const normalized = query.trim().toLowerCase();
    const userPollCounts = polls.reduce((acc, poll) => {
        acc[poll.creator.id] = (acc[poll.creator.id] || 0) + 1;
        return acc;
    }, {});
    const filteredUsers = users.filter((user) => (
        !normalized ||
        user.username.toLowerCase().includes(normalized) ||
        user.role.toLowerCase().includes(normalized)
    ));
    const filteredPolls = polls.filter((poll) => (
        !normalized ||
        poll.title.toLowerCase().includes(normalized) ||
        (poll.description || "").toLowerCase().includes(normalized) ||
        poll.creator.username.toLowerCase().includes(normalized)
    ));
    const togglePollSort = (field) => {
        setPollSort((current) => ({
            field,
            dir: current.field === field && current.dir === "asc" ? "desc" : "asc",
        }));
    };
    const pollSortValue = (poll, field) => {
        if (field === "title") return poll.title || "";
        if (field === "creator") return poll.creator?.username || "";
        if (field === "ends_at") return poll.ends_at || "";
        if (field === "state") return pollStatus(poll).label || "";
        return poll.created_at || "";
    };
    const sortedAdminPolls = [...filteredPolls].sort((a, b) => {
        const aValue = pollSortValue(a, pollSort.field);
        const bValue = pollSortValue(b, pollSort.field);
        const result = String(aValue).localeCompare(String(bValue), "ru", { numeric: true });
        return pollSort.dir === "asc" ? result : -result;
    });
    const filteredReports = reports.filter((report) => (
        !normalized ||
        report.reason.toLowerCase().includes(normalized) ||
        report.status.toLowerCase().includes(normalized) ||
        report.reporter.username.toLowerCase().includes(normalized) ||
        (report.target?.title || "").toLowerCase().includes(normalized)
    ));
    const filteredTickets = tickets.filter((ticket) => (
        !normalized ||
        ticket.subject.toLowerCase().includes(normalized) ||
        ticket.status.toLowerCase().includes(normalized) ||
        ticket.user.username.toLowerCase().includes(normalized)
    ));
    const sectionCount = {
        users: `${filteredUsers.length} из ${users.length} пользователей`,
        polls: `${filteredPolls.length} из ${polls.length} опросов`,
        reports: `${filteredReports.length} из ${reports.length} жалоб`,
        support: `${filteredTickets.length} из ${tickets.length} обращений`,
    }[section];
    const deleteReportedPoll = (report) => {
        const title = report.target?.title || "этот опрос";
        if (window.confirm(`Удалить опрос «${title}»? Это действие нельзя отменить.`)) {
            onManage({ code: report.target.code }, "delete_hard");
        }
    };

    return (
        <section className="panel admin-panel">
            <div className="section-head admin-head">
                <div>
                    <h2>Администрирование</h2>
                    <span>{sectionCount}</span>
                </div>
                <div className="admin-controls">
                    <div className="admin-switch" role="radiogroup" aria-label="Раздел администрирования">
                        <label className={cx(section === "users" && "is-active")}>
                            <input type="radio" name="admin-section" value="users" checked={section === "users"} onChange={() => setSection("users")} />
                            <Icon name="users-round" />
                            Пользователи
                        </label>
                        <label className={cx(section === "polls" && "is-active")}>
                            <input type="radio" name="admin-section" value="polls" checked={section === "polls"} onChange={() => setSection("polls")} />
                            <Icon name="list-checks" />
                            Опросы
                        </label>
                        <label className={cx(section === "reports" && "is-active")}>
                            <input type="radio" name="admin-section" value="reports" checked={section === "reports"} onChange={() => setSection("reports")} />
                            <Icon name="flag" />
                            Жалобы
                        </label>
                        <label className={cx(section === "support" && "is-active")}>
                            <input type="radio" name="admin-section" value="support" checked={section === "support"} onChange={() => setSection("support")} />
                            <Icon name="messages-square" />
                            Поддержка
                        </label>
                    </div>
                    <input className="input input--search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск" />
                </div>
            </div>

            {section === "users" ? (
                <div className="admin-list">
                    {filteredUsers.map((user) => (
                        <div className="admin-row admin-row--rich" key={user.id}>
                            <div>
                                <strong><UserLink user={user} onOpen={onOpenUser} /></strong>
                                <span>{formatDate(user.created_at)} · {userPollCounts[user.id] || 0} опросов{user.is_blocked ? " · заблокирован" : ""}</span>
                            </div>
                            <div className="admin-row__actions">
                                <select className="select select--compact" value={user.role} onChange={(event) => onRoleChange(user, event.target.value)}>
                                    <option value="user">user</option>
                                    <option value="admin">admin</option>
                                </select>
                                <button className={cx("button", user.is_blocked ? "button--ghost" : "button--danger")} type="button" onClick={() => onBlockUser(user, !user.is_blocked)} disabled={auth.user.id === user.id}>
                                    <Icon name={user.is_blocked ? "unlock" : "ban"} />
                                    {user.is_blocked ? "Разблокировать" : "Блокировать"}
                                </button>
                                <button className="button button--ghost" type="button" onClick={() => onOpenUser(user.id)}>
                                    <Icon name="circle-user-round" />
                                    Профиль
                                </button>
                            </div>
                        </div>
                    ))}
                    {!filteredUsers.length ? <EmptyState title="Пользователи не найдены" icon="search" /> : null}
                </div>
            ) : section === "polls" ? (
                <div className="admin-table">
                    <div className="admin-table__head">
                        <button type="button" onClick={() => togglePollSort("title")}>Название {pollSort.field === "title" ? (pollSort.dir === "asc" ? "↑" : "↓") : ""}</button>
                        <button type="button" onClick={() => togglePollSort("creator")}>Создатель {pollSort.field === "creator" ? (pollSort.dir === "asc" ? "↑" : "↓") : ""}</button>
                        <button type="button" onClick={() => togglePollSort("created_at")}>Дата создания {pollSort.field === "created_at" ? (pollSort.dir === "asc" ? "↑" : "↓") : ""}</button>
                        <button type="button" onClick={() => togglePollSort("ends_at")}>Дата окончания {pollSort.field === "ends_at" ? (pollSort.dir === "asc" ? "↑" : "↓") : ""}</button>
                        <button type="button" onClick={() => togglePollSort("state")}>Состояние {pollSort.field === "state" ? (pollSort.dir === "asc" ? "↑" : "↓") : ""}</button>
                        <span>Управление</span>
                    </div>
                    <div className="admin-table__body">
                    {sortedAdminPolls.map((poll) => {
                        const status = pollStatus(poll);
                        return (
                            <article className="admin-table__row" key={poll.id} role="button" tabIndex="0" onClick={() => onOpen(poll, { adminMenu: true })} onKeyDown={(event) => {
                                if (event.key === "Enter" || event.key === " ") {
                                    event.preventDefault();
                                    onOpen(poll, { adminMenu: true });
                                }
                            }}>
                                <div>
                                    <strong>{poll.title}</strong>
                                    <span>{poll.participants} участников · {poll.views_count || 0} просмотров</span>
                                </div>
                                <div onClick={(event) => event.stopPropagation()}><UserLink user={poll.creator} onOpen={onOpenUser} /></div>
                                <span>{formatDate(poll.created_at)}</span>
                                <span>{pollEndLabel(poll)}</span>
                                <Badge tone={status.tone}>{status.label}</Badge>
                                <div className="admin-row__actions" onClick={(event) => event.stopPropagation()}>
                                    <button className="button button--ghost" type="button" onClick={() => onOpen(poll, { adminMenu: true })}>
                                        <Icon name="settings" />
                                        Управление
                                    </button>
                                    <button className="button button--ghost" type="button" onClick={() => onManage(poll, poll.is_active ? "complete" : "activate")}>
                                        <Icon name={poll.is_active ? "circle-check" : "play"} />
                                        {poll.is_active ? "Завершить" : "Активировать"}
                                    </button>
                                    <button className="button button--danger" type="button" onClick={() => onManage(poll, "delete_hard")}>
                                        <Icon name="trash-2" />
                                        Удалить
                                    </button>
                                </div>
                            </article>
                        );
                    })}
                    {!sortedAdminPolls.length ? <EmptyState title="Опросы не найдены" icon="search" /> : null}
                    </div>
                </div>
            ) : section === "reports" ? (
                <div className="moderation-list">
                    {filteredReports.map((report) => (
                        <article className="moderation-card" key={report.id}>
                            <div className="moderation-card__head">
                                <div>
                                    <strong>{report.reason}</strong>
                                    <span>{reportTargetLabels[report.target?.type] || report.target?.type} · {report.target?.title} · от {report.reporter.username} · {formatDate(report.created_at)}</span>
                                </div>
                                <Badge tone={report.status === "pending" ? "amber" : report.status === "resolved" ? "green" : report.status === "rejected" ? "red" : "blue"}>{reportStatusLabels[report.status] || report.status}</Badge>
                            </div>
                            <div className="moderation-card__body">
                                {report.body ? <p>{report.body}</p> : null}
                                {report.admin_note ? <small>Заметка администратора: {report.admin_note}</small> : null}
                            </div>
                            <div className="moderation-card__actions">
                                {report.target?.type === "poll" && report.target.code ? (
                                    <>
                                        <button className="button button--ghost" type="button" onClick={() => onOpen({ code: report.target.code }, { adminMenu: true })}>
                                            <Icon name="external-link" />
                                            Открыть опрос
                                        </button>
                                        <button className="button button--danger" type="button" onClick={() => deleteReportedPoll(report)}>
                                            <Icon name="trash-2" />
                                            Удалить опрос
                                        </button>
                                    </>
                                ) : null}
                                {report.target?.type === "comment" ? (
                                    <button className="button button--danger" type="button" onClick={() => onDeleteComment(report.target.id)}>
                                        <Icon name="trash-2" />
                                        Удалить комментарий
                                    </button>
                                ) : null}
                                {report.target?.type === "user" ? (
                                    <>
                                        <button className="button button--ghost" type="button" onClick={() => onOpenUser(report.target.id)}>
                                            <Icon name="circle-user-round" />
                                            Профиль
                                        </button>
                                        <button className={cx("button", report.target.is_blocked ? "button--ghost" : "button--danger")} type="button" onClick={() => onBlockUser({ id: report.target.id }, !report.target.is_blocked)} disabled={auth.user.id === report.target.id}>
                                            <Icon name={report.target.is_blocked ? "unlock" : "ban"} />
                                            {report.target.is_blocked ? "Разблокировать" : "Блокировать"}
                                        </button>
                                    </>
                                ) : null}
                                <input className="input input--compact" value={reportNotes[report.id] ?? report.admin_note ?? ""} placeholder="Заметка администратора" onChange={(event) => setReportNotes({ ...reportNotes, [report.id]: event.target.value })} />
                                <button className="button button--ghost" type="button" onClick={() => onReviewReport(report, { status: "reviewing", admin_note: reportNotes[report.id] || report.admin_note || "" })}>
                                    <Icon name="eye" />
                                    В работу
                                </button>
                                <button className="button button--primary" type="button" onClick={() => onReviewReport(report, { status: "resolved", admin_note: reportNotes[report.id] || report.admin_note || "" })}>
                                    <Icon name="check" />
                                    Решено
                                </button>
                                <button className="button button--ghost" type="button" onClick={() => onReviewReport(report, { status: "rejected", admin_note: reportNotes[report.id] || report.admin_note || "" })}>
                                    <Icon name="x" />
                                    Отклонить
                                </button>
                            </div>
                        </article>
                    ))}
                    {!filteredReports.length ? <EmptyState title="Жалоб нет" icon="flag" /> : null}
                </div>
            ) : (
                <div className="admin-list">
                    {filteredTickets.map((ticket) => (
                        <article className="admin-row admin-row--rich admin-row--stack" key={ticket.id}>
                            <div>
                                <strong>{ticket.subject}</strong>
                                <span>{ticket.user.username} · {ticket.status} · {formatDate(ticket.updated_at)}</span>
                            </div>
                            <div className="chat-messages chat-messages--admin">
                                {(ticket.messages || []).map((message) => (
                                    <div className={cx("chat-message", message.sender.id === auth.user.id && "is-own")} key={message.id}>
                                        <strong>{message.sender.username}</strong>
                                        <p>{message.body}</p>
                                        <span>{formatDate(message.created_at)}</span>
                                    </div>
                                ))}
                            </div>
                            <div className="admin-row__actions">
                                <select className="select select--compact" value={ticket.status} onChange={(event) => onSupportStatus(ticket, event.target.value)}>
                                    <option value="open">open</option>
                                    <option value="answered">answered</option>
                                    <option value="closed">closed</option>
                                </select>
                                <input className="input" value={ticketReplies[ticket.id] || ""} placeholder="Ответ пользователю" onChange={(event) => setTicketReplies({ ...ticketReplies, [ticket.id]: event.target.value })} />
                                <button className="button button--primary" type="button" onClick={() => {
                                    const reply = (ticketReplies[ticket.id] || "").trim();
                                    if (reply) {
                                        onSendSupport(ticket, reply);
                                        setTicketReplies({ ...ticketReplies, [ticket.id]: "" });
                                    }
                                }}>
                                    <Icon name="send" />
                                    Ответить
                                </button>
                            </div>
                        </article>
                    ))}
                    {!filteredTickets.length ? <EmptyState title="Обращений нет" icon="message-circle-question" /> : null}
                </div>
            )}
        </section>
    );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
