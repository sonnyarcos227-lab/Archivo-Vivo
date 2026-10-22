/**
 * auth.js – Authentication logic for Login & Register views.
 * Handles form submission, JWT token management, and route protection.
 */

function getApiBase() {
    // If the frontend is served on port 8000 by FastAPI directly
    if (window.location.protocol.startsWith('http') && window.location.port === '8000') {
        return window.location.origin;
    }
    // If opened via VS Code Live Server (port 5500), file://, or another dev port, point to backend at 8000
    return 'http://localhost:8000';
}

const API_BASE = getApiBase();

// ── Check for existing session on load ──────────────────────────────────────
(function checkSession() {
    const token = localStorage.getItem('chatpdf_token');
    if (token && window.location.pathname !== '/dashboard') {
        window.location.href = '/dashboard';
    }
})();

// ── Tab Switching ───────────────────────────────────────────────────────────
function switchTab(tab) {
    const loginTab = document.getElementById('tab-login');
    const registerTab = document.getElementById('tab-register');
    const loginPanel = document.getElementById('panel-login');
    const registerPanel = document.getElementById('panel-register');
    const formTitle = document.getElementById('form-title');
    const formSubtitle = document.getElementById('form-subtitle');
    const alertBanner = document.getElementById('alert-banner');

    // Hide alert
    alertBanner.classList.remove('visible');

    if (tab === 'login') {
        loginTab.classList.add('active');
        registerTab.classList.remove('active');
        loginPanel.classList.add('active');
        registerPanel.classList.remove('active');
        formTitle.textContent = 'Bienvenido de vuelta';
        formSubtitle.textContent = 'Inicia sesión para acceder a tu espacio de trabajo inteligente';
    } else {
        registerTab.classList.add('active');
        loginTab.classList.remove('active');
        registerPanel.classList.add('active');
        loginPanel.classList.remove('active');
        formTitle.textContent = 'Crea tu cuenta';
        formSubtitle.textContent = 'Regístrate y comienza a analizar documentos con IA';
    }
}

// ── Show Alert ──────────────────────────────────────────────────────────────
function showAlert(message, type = 'error') {
    const banner = document.getElementById('alert-banner');
    const messageEl = document.getElementById('alert-message');
    
    banner.className = `alert-banner visible alert-${type}`;
    messageEl.textContent = message;

    // Update icon
    const iconEl = banner.querySelector('i');
    if (iconEl) {
        iconEl.setAttribute('data-lucide', type === 'error' ? 'alert-circle' : 'check-circle');
        lucide.createIcons();
    }
}

// ── Safe JSON / Text API response parser ─────────────────────────────────────
async function parseResponse(res) {
    const contentType = res.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
        try {
            return await res.json();
        } catch {
            return { detail: `Error inesperado al procesar JSON (HTTP ${res.status})` };
        }
    }
    const text = await res.text();
    return { detail: text || `Error del servidor (HTTP ${res.status}: ${res.statusText})` };
}

// ── Toggle Password Visibility ──────────────────────────────────────────────
function togglePassword(inputId, btn) {
    const input = document.getElementById(inputId);
    const icon = btn.querySelector('i');
    
    if (input.type === 'password') {
        input.type = 'text';
        icon.setAttribute('data-lucide', 'eye-off');
    } else {
        input.type = 'password';
        icon.setAttribute('data-lucide', 'eye');
    }
    lucide.createIcons();
}

// ── Quick Credentials Auto-fill ─────────────────────────────────────────────
function fillDemoCredentials(type) {
    switchTab('login');
    const emailInput = document.getElementById('login-email');
    const passwordInput = document.getElementById('login-password');
    if (type === 'admin') {
        emailInput.value = 'admin@chatpdf.com';
        passwordInput.value = 'admin123';
        showAlert('Credenciales de Administrador cargadas. Haz clic en "Iniciar Sesión".', 'success');
    }
}

// ── Set Button Loading State ────────────────────────────────────────────────
function setLoading(btnId, loading) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    if (loading) {
        btn.classList.add('loading');
        btn.disabled = true;
    } else {
        btn.classList.remove('loading');
        btn.disabled = false;
    }
}

// ── Handle Login ────────────────────────────────────────────────────────────
async function handleLogin(e) {
    e.preventDefault();
    
    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;

    if (!email || !password) {
        showAlert('Por favor completa todos los campos');
        return;
    }

    setLoading('btn-login', true);

    try {
        const res = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });

        const data = await parseResponse(res);

        if (!res.ok) {
            throw new Error(data.detail || 'Error al iniciar sesión');
        }

        // Store token and user data
        localStorage.setItem('chatpdf_token', data.access_token);
        localStorage.setItem('chatpdf_user', JSON.stringify(data.user));

        showAlert(`¡Bienvenido, ${data.user.full_name || data.user.username}! Redirigiendo...`, 'success');

        setTimeout(() => {
            window.location.href = '/dashboard';
        }, 500);

    } catch (err) {
        let msg = err.message || 'Error al iniciar sesión';
        if (err.name === 'TypeError' && (msg.includes('fetch') || msg.includes('Failed to fetch') || msg.includes('NetworkError'))) {
            msg = 'No se pudo conectar al servidor backend. Verifica que FastAPI esté corriendo en http://localhost:8000';
        }
        showAlert(msg);
    } finally {
        setLoading('btn-login', false);
    }
}

// ── Handle Register ─────────────────────────────────────────────────────────
async function handleRegister(e) {
    e.preventDefault();

    const fullName = document.getElementById('reg-fullname').value.trim();
    const username = document.getElementById('reg-username').value.trim();
    const email = document.getElementById('reg-email').value.trim();
    const password = document.getElementById('reg-password').value;

    // Validation
    if (!username || username.length < 3) {
        showAlert('El nombre de usuario debe tener al menos 3 caracteres');
        return;
    }
    if (!email) {
        showAlert('Por favor ingresa un correo electrónico válido');
        return;
    }
    if (!password || password.length < 6) {
        showAlert('La contraseña debe tener al menos 6 caracteres');
        return;
    }

    setLoading('btn-register', true);

    try {
        const res = await fetch(`${API_BASE}/api/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                email,
                password,
                full_name: fullName || null,
            }),
        });

        const data = await parseResponse(res);

        if (!res.ok) {
            throw new Error(data.detail || 'Error al crear la cuenta');
        }

        // Store token and user data
        localStorage.setItem('chatpdf_token', data.access_token);
        localStorage.setItem('chatpdf_user', JSON.stringify(data.user));

        showAlert('¡Cuenta creada exitosamente! Redirigiendo...', 'success');

        setTimeout(() => {
            window.location.href = '/dashboard';
        }, 500);

    } catch (err) {
        let msg = err.message || 'Error al crear la cuenta';
        if (err.name === 'TypeError' && (msg.includes('fetch') || msg.includes('Failed to fetch') || msg.includes('NetworkError'))) {
            msg = 'No se pudo conectar al servidor backend. Verifica que FastAPI esté corriendo en http://localhost:8000';
        }
        showAlert(msg);
    } finally {
        setLoading('btn-register', false);
    }
}
