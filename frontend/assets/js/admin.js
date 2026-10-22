/**
 * admin.js – Full CRUD and Administrative Dashboard Management for ChatPDF Expert.
 * Exposes user management, role elevation, database inspection, and platform statistics.
 */

let isAdminPanelActive = false;
let adminUsersList = [];
let adminStats = null;
let currentEditingUserId = null;
let userToDelete = null;

// ── Initialize Admin Features ────────────────────────────────────────────────
function initAdminPanel() {
    console.log('[Admin] Inicializando panel de control para usuario con rol admin');
    const adminNavBar = document.getElementById('admin-nav-bar');
    if (adminNavBar) {
        adminNavBar.style.display = 'block';
    }
}

// ── Toggle between PDF Viewer and Admin Control Panel ─────────────────────────
function toggleAdminPanel(forceState = null) {
    if (forceState !== null) {
        isAdminPanelActive = forceState;
    } else {
        isAdminPanelActive = !isAdminPanelActive;
    }

    const adminPanel = document.getElementById('admin-panel-container');
    const welcomeView = document.getElementById('viewer-welcome');
    const toolbar = document.getElementById('viewer-toolbar');
    const tabs = document.getElementById('viewer-tabs');
    const summaryView = document.getElementById('tab-content-summary');
    const diagramView = document.getElementById('tab-content-diagram');
    const chunksView = document.getElementById('tab-content-chunks');
    const toggleLabel = document.getElementById('admin-toggle-label');
    const toggleBtn = document.getElementById('btn-toggle-admin-view');

    if (!adminPanel) return;

    if (isAdminPanelActive) {
        // Hide document viewers
        if (welcomeView) welcomeView.style.display = 'none';
        if (toolbar) toolbar.style.display = 'none';
        if (tabs) tabs.style.display = 'none';
        if (summaryView) summaryView.style.display = 'none';
        if (diagramView) diagramView.style.display = 'none';
        if (chunksView) chunksView.style.display = 'none';

        // Show admin panel
        adminPanel.style.display = 'block';
        if (toggleLabel) toggleLabel.textContent = 'Volver al visor';
        if (toggleBtn) {
            toggleBtn.style.background = 'linear-gradient(135deg, rgba(16,185,129,0.2), rgba(6,182,212,0.2))';
            toggleBtn.style.borderColor = 'rgba(16,185,129,0.4)';
            toggleBtn.style.color = '#B8005F';
        }

        // Load fresh data
        loadAdminStats();
        loadAdminUsers();
    } else {
        // Return to standard view
        adminPanel.style.display = 'none';
        if (toggleLabel) toggleLabel.textContent = 'Gestión de Usuarios';
        if (toggleBtn) {
            toggleBtn.style.background = 'linear-gradient(135deg, rgba(99,102,241,0.2), rgba(139,92,246,0.2))';
            toggleBtn.style.borderColor = 'rgba(99,102,241,0.3)';
            toggleBtn.style.color = '#1B1D52';
        }

        // Restore document or welcome state
        if (currentDocumentId) {
            if (toolbar) toolbar.style.display = 'flex';
            if (tabs) tabs.style.display = 'flex';
            switchViewerTab(currentViewerTab || 'summary');
        } else {
            if (welcomeView) welcomeView.style.display = 'flex';
        }
    }
}

// ── Fetch & Render Platform Statistics ──────────────────────────────────────
async function loadAdminStats() {
    try {
        const res = await fetch(`${API_BASE}/api/admin/stats`, {
            headers: getHeaders(),
        });

        if (!res.ok) {
            if (res.status === 403) return;
            throw new Error('Error al obtener estadísticas del sistema');
        }

        adminStats = await res.json();

        // Update DOM elements
        const totalUsersEl = document.getElementById('stat-total-users');
        const adminUsersEl = document.getElementById('stat-admin-users');
        const regularUsersEl = document.getElementById('stat-regular-users');
        const totalDocsEl = document.getElementById('stat-total-docs');
        const totalMsgsEl = document.getElementById('stat-total-msgs');
        const totalStorageEl = document.getElementById('stat-total-storage');

        if (totalUsersEl) totalUsersEl.textContent = adminStats.total_users;
        if (adminUsersEl) adminUsersEl.textContent = adminStats.admin_users;
        if (regularUsersEl) regularUsersEl.textContent = adminStats.regular_users;
        if (totalDocsEl) totalDocsEl.textContent = adminStats.total_documents;
        if (totalMsgsEl) totalMsgsEl.textContent = adminStats.total_messages;
        if (totalStorageEl) {
            const kb = adminStats.total_storage_kb || 0;
            totalStorageEl.textContent = kb > 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`;
        }
    } catch (err) {
        console.error('[Admin] Error cargando estadísticas:', err);
    }
}

// ── Fetch & Render Users Table ──────────────────────────────────────────────
async function loadAdminUsers() {
    const searchInput = document.getElementById('admin-search-input');
    const roleFilter = document.getElementById('admin-role-filter');
    const search = searchInput ? searchInput.value.trim() : '';
    const role = roleFilter ? roleFilter.value : 'all';

    const tbody = document.getElementById('admin-users-table-body');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" style="text-align:center; padding:32px; color:var(--zinc-400);">
                    <div style="display:inline-block; width:20px; height:20px; border:2px solid var(--emerald-500); border-top-color:transparent; border-radius:50%; animation:spin 0.6s linear infinite; margin-right:8px; vertical-align:middle;"></div>
                    Cargando usuarios desde la base de datos...
                </td>
            </tr>
        `;
    }

    try {
        let url = `${API_BASE}/api/admin/users?`;
        if (search) url += `search=${encodeURIComponent(search)}&`;
        if (role && role !== 'all') url += `role=${encodeURIComponent(role)}&`;

        const res = await fetch(url, { headers: getHeaders() });

        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || 'Error al listar usuarios');
        }

        adminUsersList = await res.json();
        renderAdminUsersTable(adminUsersList);

        // Update count badge
        const countBadge = document.getElementById('admin-users-count-badge');
        if (countBadge) countBadge.textContent = `${adminUsersList.length} usuarios`;

    } catch (err) {
        console.error('[Admin] Error:', err);
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" style="text-align:center; padding:24px; color:var(--red-400);">
                        Error al cargar usuarios: ${err.message}
                    </td>
                </tr>
            `;
        }
        showToast(err.message, 'error');
    }
}

// ── Render HTML Table Rows ──────────────────────────────────────────────────
function renderAdminUsersTable(users) {
    const tbody = document.getElementById('admin-users-table-body');
    if (!tbody) return;

    if (!users || users.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" style="text-align:center; padding:32px; color:var(--zinc-500);">
                    No se encontraron usuarios que coincidan con la búsqueda.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = users.map(user => {
        const isAdmin = user.role === 'admin';
        const isCurrent = currentUser && currentUser.id === user.id;
        const initial = (user.full_name || user.username || 'U').charAt(0).toUpperCase();
        const dateFormatted = user.created_at ? new Date(user.created_at).toLocaleDateString('es-ES', {
            year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
        }) : 'Reciente';

        const roleBadge = isAdmin
            ? `<span style="display:inline-flex; align-items:center; gap:4px; padding:3px 8px; border-radius:6px; font-size:0.72rem; font-weight:700; background:rgba(99,102,241,0.18); color:#00699A; border:1px solid rgba(99,102,241,0.35);">
                 <i data-lucide="shield" style="width:12px;height:12px;"></i> ADMIN
               </span>`
            : `<span style="display:inline-flex; align-items:center; gap:4px; padding:3px 8px; border-radius:6px; font-size:0.72rem; font-weight:600; background:rgba(16,185,129,0.15); color:#B8005F; border:1px solid rgba(16,185,129,0.25);">
                 <i data-lucide="user" style="width:12px;height:12px;"></i> USUARIO
               </span>`;

        const statusBadge = user.is_active === 1
            ? `<span style="font-size:0.7rem; color:var(--emerald-400); font-weight:500;">● Activo</span>`
            : `<span style="font-size:0.7rem; color:var(--red-400); font-weight:500;">● Inactivo</span>`;

        return `
            <tr style="border-bottom:1px solid var(--zinc-800); transition:background 0.2s;" onmouseover="this.style.background='rgba(255,255,255,0.02)'" onmouseout="this.style.background='transparent'">
                <!-- User Profile -->
                <td style="padding:14px 16px;">
                    <div style="display:flex; align-items:center; gap:12px;">
                        <div style="width:36px; height:36px; border-radius:10px; background:${user.avatar_color || '#10b981'}; display:flex; align-items:center; justify-content:center; font-weight:700; color:white; font-size:0.85rem; flex-shrink:0;">
                            ${initial}
                        </div>
                        <div>
                            <div style="font-size:0.85rem; font-weight:600; color:var(--zinc-100); display:flex; align-items:center; gap:6px;">
                                ${escapeHtml(user.full_name || user.username)}
                                ${isCurrent ? '<span style="font-size:0.65rem; background:var(--zinc-800); color:var(--zinc-400); padding:1px 5px; border-radius:4px;">Tú</span>' : ''}
                            </div>
                            <div style="font-size:0.73rem; color:var(--zinc-400);">@${escapeHtml(user.username)}</div>
                        </div>
                    </div>
                </td>

                <!-- Email & Status -->
                <td style="padding:14px 16px;">
                    <div style="font-size:0.82rem; color:var(--zinc-200);">${escapeHtml(user.email)}</div>
                    <div>${statusBadge}</div>
                </td>

                <!-- Role -->
                <td style="padding:14px 16px;">
                    ${roleBadge}
                </td>

                <!-- Documents / Chats -->
                <td style="padding:14px 16px;">
                    <div style="font-size:0.8rem; font-weight:600; color:var(--zinc-200); display:flex; align-items:center; gap:6px;">
                        <i data-lucide="file-text" style="width:14px;height:14px; color:var(--emerald-400);"></i>
                        ${user.documents_count} docs
                    </div>
                    <div style="font-size:0.72rem; color:var(--zinc-500);">
                        ${user.messages_count} mensajes
                    </div>
                </td>

                <!-- Registration Date -->
                <td style="padding:14px 16px; font-size:0.75rem; color:var(--zinc-400);">
                    ${dateFormatted}
                </td>

                <!-- Action Buttons -->
                <td style="padding:14px 16px; text-align:right;">
                    <div style="display:inline-flex; align-items:center; gap:6px;">
                        <!-- Role Switcher -->
                        <button onclick="toggleUserRole('${user.id}', '${user.role}')" 
                                ${isCurrent ? 'disabled title="No puedes cambiar tu propio rol"' : 'title="' + (isAdmin ? 'Degradar a Usuario' : 'Promover a Admin') + '"'}
                                style="background:var(--zinc-850); border:1px solid var(--zinc-700); color:var(--zinc-300); padding:5px 8px; border-radius:6px; font-size:0.72rem; cursor:${isCurrent ? 'not-allowed' : 'pointer'}; opacity:${isCurrent ? '0.4' : '1'}; transition:all 0.2s;"
                                onmouseover="if(!${isCurrent}) this.style.borderColor='var(--indigo-400)'"
                                onmouseout="this.style.borderColor='var(--zinc-700)'">
                            <i data-lucide="${isAdmin ? 'arrow-down-circle' : 'arrow-up-circle'}" style="width:14px;height:14px; vertical-align:-2px;"></i>
                            ${isAdmin ? 'Hacer Usuario' : 'Hacer Admin'}
                        </button>

                        <!-- Edit Button -->
                        <button onclick="openEditUserModal('${user.id}')" 
                                title="Editar datos del usuario"
                                style="background:var(--zinc-850); border:1px solid var(--zinc-700); color:var(--zinc-200); padding:5px 8px; border-radius:6px; font-size:0.72rem; cursor:pointer; transition:all 0.2s;"
                                onmouseover="this.style.borderColor='var(--emerald-500)'; this.style.color='var(--emerald-400)'"
                                onmouseout="this.style.borderColor='var(--zinc-700)'; this.style.color='var(--zinc-200)'">
                            <i data-lucide="edit-3" style="width:14px;height:14px; vertical-align:-2px;"></i>
                        </button>

                        <!-- Delete Button -->
                        <button onclick="openDeleteUserModal('${user.id}', '${escapeHtml(user.username)}')" 
                                ${isCurrent ? 'disabled title="No puedes eliminar tu propia cuenta"' : 'title="Eliminar usuario"'}
                                style="background:rgba(239,68,68,0.1); border:1px solid rgba(239,68,68,0.25); color:var(--red-400); padding:5px 8px; border-radius:6px; font-size:0.72rem; cursor:${isCurrent ? 'not-allowed' : 'pointer'}; opacity:${isCurrent ? '0.3' : '1'}; transition:all 0.2s;"
                                onmouseover="if(!${isCurrent}) this.style.background='rgba(239,68,68,0.25)'"
                                onmouseout="this.style.background='rgba(239,68,68,0.1)'">
                            <i data-lucide="trash-2" style="width:14px;height:14px; vertical-align:-2px;"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    lucide.createIcons();
}

// ── Role Toggling ───────────────────────────────────────────────────────────
async function toggleUserRole(userId, currentRole) {
    const newRole = currentRole === 'admin' ? 'user' : 'admin';
    try {
        const res = await fetch(`${API_BASE}/api/admin/users/${userId}/role`, {
            method: 'PATCH',
            headers: getHeaders(),
            body: JSON.stringify({ role: newRole }),
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'Error al cambiar rol');
        }

        showToast(`Rol actualizado a "${newRole.toUpperCase()}" para @${data.username}`);
        loadAdminUsers();
        loadAdminStats();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// ── Open Modal for Creating User ─────────────────────────────────────────────
function openCreateUserModal() {
    currentEditingUserId = null;
    document.getElementById('modal-user-title').textContent = 'Crear Nuevo Usuario';
    document.getElementById('modal-user-subtitle').textContent = 'Agrega un nuevo usuario a la base de datos con rol asignable.';
    
    document.getElementById('modal-fullname').value = '';
    document.getElementById('modal-username').value = '';
    document.getElementById('modal-email').value = '';
    document.getElementById('modal-password').value = '';
    document.getElementById('modal-password').required = true;
    document.getElementById('modal-password-hint').textContent = 'Mínimo 6 caracteres (Obligatorio)';
    document.getElementById('modal-role').value = 'user';
    document.getElementById('modal-status-group').style.display = 'none';

    document.getElementById('modal-user-error').style.display = 'none';
    document.getElementById('admin-user-modal').style.display = 'flex';
    lucide.createIcons();
}

// ── Open Modal for Editing User ─────────────────────────────────────────────
function openEditUserModal(userId) {
    const user = adminUsersList.find(u => u.id === userId);
    if (!user) return;

    currentEditingUserId = userId;
    document.getElementById('modal-user-title').textContent = `Editar Usuario: @${user.username}`;
    document.getElementById('modal-user-subtitle').textContent = 'Modifica los datos personales, credenciales o permisos.';

    document.getElementById('modal-fullname').value = user.full_name || '';
    document.getElementById('modal-username').value = user.username;
    document.getElementById('modal-email').value = user.email;
    document.getElementById('modal-password').value = '';
    document.getElementById('modal-password').required = false;
    document.getElementById('modal-password-hint').textContent = 'Dejar en blanco para conservar contraseña actual';
    document.getElementById('modal-role').value = user.role;
    
    const statusGroup = document.getElementById('modal-status-group');
    statusGroup.style.display = 'block';
    document.getElementById('modal-status').value = user.is_active.toString();

    document.getElementById('modal-user-error').style.display = 'none';
    document.getElementById('admin-user-modal').style.display = 'flex';
    lucide.createIcons();
}

function closeAdminUserModal() {
    document.getElementById('admin-user-modal').style.display = 'none';
}

// ── Submit Create / Edit User ────────────────────────────────────────────────
async function handleAdminUserForm(e) {
    e.preventDefault();

    const fullName = document.getElementById('modal-fullname').value.trim();
    const username = document.getElementById('modal-username').value.trim();
    const email = document.getElementById('modal-email').value.trim();
    const password = document.getElementById('modal-password').value;
    const role = document.getElementById('modal-role').value;
    const isActive = parseInt(document.getElementById('modal-status').value || '1', 10);

    const errorEl = document.getElementById('modal-user-error');
    errorEl.style.display = 'none';

    const saveBtn = document.getElementById('btn-save-admin-user');
    saveBtn.disabled = true;
    saveBtn.textContent = 'Guardando...';

    try {
        if (!currentEditingUserId) {
            // Create New User (POST)
            const res = await fetch(`${API_BASE}/api/admin/users`, {
                method: 'POST',
                headers: getHeaders(),
                body: JSON.stringify({
                    username,
                    email,
                    password,
                    full_name: fullName || null,
                    role,
                }),
            });

            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Error al crear usuario');

            showToast(`Usuario @${data.username} creado correctamente`);
        } else {
            // Update Existing User (PUT)
            const payload = {
                username,
                email,
                full_name: fullName || null,
                role,
                is_active: isActive,
            };
            if (password && password.trim().length > 0) {
                payload.password = password;
            }

            const res = await fetch(`${API_BASE}/api/admin/users/${currentEditingUserId}`, {
                method: 'PUT',
                headers: getHeaders(),
                body: JSON.stringify(payload),
            });

            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Error al actualizar usuario');

            showToast(`Usuario @${data.username} actualizado correctamente`);

            // If updated current logged-in user, refresh local storage
            if (currentUser && currentUser.id === currentEditingUserId) {
                currentUser.username = data.username;
                currentUser.email = data.email;
                currentUser.full_name = data.full_name;
                currentUser.role = data.role;
                localStorage.setItem('chatpdf_user', JSON.stringify(currentUser));
                renderUserCard();
            }
        }

        closeAdminUserModal();
        loadAdminUsers();
        loadAdminStats();

    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.style.display = 'block';
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = 'Guardar Usuario';
    }
}

// ── Delete Confirmation ─────────────────────────────────────────────────────
function openDeleteUserModal(userId, username) {
    userToDelete = { id: userId, username };
    document.getElementById('delete-modal-username').textContent = `@${username}`;
    document.getElementById('admin-delete-modal').style.display = 'flex';
}

function closeDeleteUserModal() {
    userToDelete = null;
    document.getElementById('admin-delete-modal').style.display = 'none';
}

async function executeDeleteUser() {
    if (!userToDelete) return;
    const btn = document.getElementById('btn-confirm-delete');
    btn.disabled = true;
    btn.textContent = 'Eliminando...';

    try {
        const res = await fetch(`${API_BASE}/api/admin/users/${userToDelete.id}`, {
            method: 'DELETE',
            headers: getHeaders(),
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Error al eliminar usuario');

        showToast(data.detail || 'Usuario eliminado');
        closeDeleteUserModal();
        loadAdminUsers();
        loadAdminStats();
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Eliminar permanentemente';
    }
}

// Helper escape
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
