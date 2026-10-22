/**
 * app.js – Lógica del panel de Archivo Vivo.
 * Base de conocimiento compartida (PDFs, audios y videos), visor de PDF,
 * reproductor con transcripción, chat con un archivo o con toda la base,
 * citas clicables y render de Markdown + Mermaid.
 */

// ══════════════════════════════════════════════════════════════════════════════
// CONFIGURATION
// ══════════════════════════════════════════════════════════════════════════════
function getApiBase() {
    if (window.location.protocol.startsWith('http') && window.location.port === '8000') {
        return window.location.origin;
    }
    return 'http://localhost:8000';
}
const API_BASE = getApiBase();
let currentUser = null;
let currentDocumentId = null;
let currentDocument = null;
let isProcessing = false;
let currentZoom = 100;
let currentPage = 1;
let isDemoPlaying = false;
let demoStepIndex = 0;
let demoInterval = null;
let currentViewerTab = 'pdf';

// ── Base de conocimiento ────────────────────────────────────────────────────
const AUDIO_EXT = ['mp3', 'wav', 'm4a', 'ogg', 'flac', 'aac', 'opus', 'wma'];
const VIDEO_EXT = ['mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v', 'wmv'];
const IMAGE_EXT = ['jpg', 'jpeg', 'png', 'webp', 'bmp', 'gif', 'tif', 'tiff'];
const MAX_UPLOAD_MB = 200;
let allDocs = [];            // todos los archivos de la base (compartida)
let chatMode = 'kb';         // 'kb' = toda la base · 'doc' = solo el archivo abierto
let mediaUrl = null;         // blob URL del audio/video abierto
let pendingSeek = null;      // segundo al que saltar cuando cargue el reproductor
let syncTimer = null;

// ── Chunks de marcado y escenas ─────────────────────────────────────────────
const msgCtx = {};           // contexto de cada respuesta: fuentes, pregunta y respuesta
let msgSeq = 0;
let currentMarks = null;     // { docId, data, src } lo que hay que subrayar en el visor
let markToken = 0;
let docChunksCache = [];     // fragmentos del archivo abierto (explorador / transcripción)
let currentScene = null;
let audioCtx = null;
const SCENE_NAME = { pdf: 'Imprenta', video: 'Moviola', image: 'Mesa de luz', audio: 'Estudio de radio', kb: 'Toda la base' };
const REDUCED_MOTION = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function fileKind(name) {
    const ext = (name.split('.').pop() || '').toLowerCase();
    if (ext === 'pdf') return 'pdf';
    if (AUDIO_EXT.includes(ext)) return 'audio';
    if (VIDEO_EXT.includes(ext)) return 'video';
    if (IMAGE_EXT.includes(ext)) return 'image';
    return null;
}

function fmtTime(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s2 = sec % 60;
    const mm = String(m).padStart(2, '0'), ss = String(s2).padStart(2, '0');
    return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function parseTime(txt) {
    const parts = String(txt).split(':').map(n => parseInt(n, 10));
    if (parts.some(isNaN)) return null;
    return parts.reduce((acc, n) => acc * 60 + n, 0);
}

const KIND_ICON = { pdf: 'file-text', audio: 'audio-lines', video: 'clapperboard', image: 'image' };
const KIND_NAME = { pdf: 'Documento', audio: 'Audio', video: 'Video', image: 'Imagen' };

// ── Visor PDF (PDF.js) ──────────────────────────────────────────────────────
let pdfDoc = null;          // documento PDF.js cargado
let pdfLoadToken = 0;       // evita que una carga vieja pise a una nueva
let pdfRenderTask = null;   // render en curso (se cancela si llega otro)
if (window.pdfjsLib) {
    pdfjsLib.GlobalWorkerOptions.workerSrc =
        'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
}

// ══════════════════════════════════════════════════════════════════════════════
// SESSION MANAGEMENT
// ══════════════════════════════════════════════════════════════════════════════
function getToken() {
    return localStorage.getItem('chatpdf_token');
}

function getHeaders() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getToken()}`,
    };
}

function requireAuth() {
    const token = getToken();
    if (!token) {
        window.location.href = '/login';
        return false;
    }
    const userStr = localStorage.getItem('chatpdf_user');
    if (userStr) {
        currentUser = JSON.parse(userStr);
        renderUserCard();
    }
    return true;
}

async function refreshCurrentUser() {
    try {
        const res = await fetch(`${API_BASE}/api/auth/me`, { headers: getHeaders() });
        if (res.ok) {
            const freshUser = await res.json();
            currentUser = freshUser;
            localStorage.setItem('chatpdf_user', JSON.stringify(freshUser));
            renderUserCard();
            if (freshUser.role === 'admin' && typeof initAdminPanel === 'function') {
                initAdminPanel();
            }
        }
    } catch (e) {
        console.warn('Could not sync user profile:', e);
    }
}

function logout() {
    localStorage.removeItem('chatpdf_token');
    localStorage.removeItem('chatpdf_user');
    window.location.href = '/';
}

function renderUserCard() {
    if (!currentUser) return;
    const avatar = document.getElementById('user-avatar');
    const nameEl = document.getElementById('user-display-name');
    const emailEl = document.getElementById('user-email');

    const initial = (currentUser.full_name || currentUser.username || 'U').charAt(0).toUpperCase();
    avatar.textContent = initial;
    avatar.style.background = currentUser.avatar_color || '#10b981';

    const isAdmin = currentUser.role === 'admin';
    const roleBadgeHtml = isAdmin 
        ? '<span style="display:inline-block; font-size:0.62rem; font-weight:800; background:rgba(99,102,241,0.25); color:#a5b4fc; border:1px solid rgba(99,102,241,0.4); padding:1px 6px; border-radius:4px; margin-left:6px; vertical-align:middle;">ADMIN</span>' 
        : '';

    nameEl.innerHTML = `${currentUser.full_name || currentUser.username} ${roleBadgeHtml}`;
    emailEl.textContent = currentUser.email;

    const adminNavBar = document.getElementById('admin-nav-bar');
    if (adminNavBar) {
        adminNavBar.style.display = isAdmin ? 'block' : 'none';
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// TOAST NOTIFICATIONS
// ══════════════════════════════════════════════════════════════════════════════
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icon = type === 'success' ? 'check-circle' : 'alert-circle';
    toast.innerHTML = `<i data-lucide="${icon}"></i><span>${message}</span>`;
    container.appendChild(toast);
    lucide.createIcons();

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ══════════════════════════════════════════════════════════════════════════════
// DRAG & DROP + FILE UPLOAD
// ══════════════════════════════════════════════════════════════════════════════
function initUploadZone() {
    const zone = document.getElementById('upload-zone');

    zone.addEventListener('dragover', (e) => {
        e.preventDefault();
        zone.classList.add('dragover');
    });
    zone.addEventListener('dragleave', () => {
        zone.classList.remove('dragover');
    });
    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) uploadFile(files[0]);
    });
}

function handleFileSelect(event) {
    const file = event.target.files[0];
    if (file) {
        uploadFile(file);
        event.target.value = '';
    }
}

async function uploadFile(file) {
    if (isProcessing) {
        showToast('Ya hay un archivo en proceso', 'error');
        return;
    }
    const kind = fileKind(file.name);
    if (!kind) {
        showToast('Formato no soportado: sube un PDF, una imagen, un audio o un video', 'error');
        return;
    }
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
        showToast(`El archivo excede el límite de ${MAX_UPLOAD_MB} MB`, 'error');
        return;
    }

    isProcessing = true;
    const progressEl = document.getElementById('upload-progress');
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');
    progressEl.classList.add('active');
    progressFill.style.width = '15%';

    const steps = {
        pdf: ['Subiendo el PDF...', 'Leyendo el texto y describiendo las imágenes (puede tardar unos minutos)...',
              'Vectorizando con EmbeddingGemma 2 (llama.cpp)...'],
        audio: ['Subiendo el audio...', 'Transcribiendo con Whisper...', 'Vectorizando con EmbeddingGemma 2 (llama.cpp)...'],
        video: ['Subiendo el video...', 'Transcribiendo el audio y describiendo fotogramas...',
                'Vectorizando con EmbeddingGemma 2 (llama.cpp)...'],
        image: ['Subiendo la imagen...', 'Leyendo el texto y describiendo la imagen con qwen2.5vl (puede tardar un minuto)...',
                'Vectorizando con EmbeddingGemma 2 (llama.cpp)...'],
    }[kind];
    let step = 0;
    progressText.textContent = steps[0];
    const ticker = setInterval(() => {
        step = Math.min(step + 1, steps.length - 1);
        progressText.textContent = steps[step];
        progressFill.style.width = `${15 + step * 30}%`;
    }, 6000);

    const formData = new FormData();
    formData.append('file', file);
    try {
        const res = await fetch(`${API_BASE}/api/pdf/upload`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getToken()}` },
            body: formData,
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Error al subir el archivo');
        }
        const doc = await res.json();
        progressFill.style.width = '100%';
        progressText.textContent = '¡Archivo agregado a la base de conocimiento!';
        setTimeout(() => {
            progressEl.classList.remove('active');
            progressFill.style.width = '0%';
        }, 1500);
        showToast(`"${doc.original_name}" agregado a la base`);
        await loadDocuments();
        selectDocument(doc.id);
    } catch (err) {
        showToast(err.message, 'error');
        progressEl.classList.remove('active');
        progressFill.style.width = '0%';
    } finally {
        clearInterval(ticker);
        isProcessing = false;
    }
}

// ── Indexar la carpeta base_conocimiento/ ───────────────────────────────────
async function syncFolder() {
    try {
        const res = await fetch(`${API_BASE}/api/kb/sync`, { method: 'POST', headers: getHeaders() });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'No se pudo iniciar la indexación');
        const p = data.pending;
        if (p && p.nuevos + p.cambiados + p.eliminados === 0) {
            showToast('La carpeta ya está al día: no hay archivos nuevos');
        } else {
            showToast(data.detail);
        }
        pollSync();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

async function pollSync() {
    clearTimeout(syncTimer);
    const box = document.getElementById('kb-sync-status');
    const btn = document.getElementById('btn-sync-folder');
    try {
        const res = await fetch(`${API_BASE}/api/kb/status`, { headers: getHeaders() });
        if (!res.ok) return;
        const st = await res.json();
        const sync = st.sync || {};
        const pend = st.pending || {};
        const nPend = (pend.nuevos || []).length + (pend.cambiados || []).length + (pend.eliminados || []).length;
        if (sync.running) {
            btn && (btn.disabled = true);
            box.innerHTML = `<b>Indexando ${sync.done + 1}/${sync.total}:</b> ${escapeHtml(sync.current || '')}<br><i>${escapeHtml(sync.step || '')}</i>`;
            box.style.display = 'block';
            syncTimer = setTimeout(pollSync, 2000);
            return;
        }
        btn && (btn.disabled = false);
        let html = '';
        if (sync.finished_at) {
            const parts = [];
            if (sync.added.length) parts.push(`${sync.added.length} nuevo(s)`);
            if (sync.updated.length) parts.push(`${sync.updated.length} actualizado(s)`);
            if (sync.removed.length) parts.push(`${sync.removed.length} quitado(s)`);
            html += `<b>Última indexación:</b> ${parts.join(' · ') || 'sin cambios'}`;
            if (sync.errors.length) {
                html += `<br><span class="kb-err">${sync.errors.map(e => `${escapeHtml(e.file)}: ${escapeHtml(e.error)}`).join('<br>')}</span>`;
            }
        }
        if (nPend) {
            html += `${html ? '<br>' : ''}<b>${nPend} archivo(s) en la carpeta</b> sin indexar o con cambios.`;
        }
        box.innerHTML = html;
        box.style.display = html ? 'block' : 'none';
        if (sync.finished_at && !box.dataset.reloaded) {
            box.dataset.reloaded = sync.finished_at;
            await loadDocuments();
        } else if (sync.finished_at && box.dataset.reloaded !== sync.finished_at) {
            box.dataset.reloaded = sync.finished_at;
            await loadDocuments();
        }
    } catch (e) {
        console.warn('Estado de la base:', e);
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// DOCUMENT LIST
// ══════════════════════════════════════════════════════════════════════════════
async function loadDocuments() {
    try {
        const res = await fetch(`${API_BASE}/api/pdf/documents`, { headers: getHeaders() });
        if (!res.ok) {
            if (res.status === 401) { logout(); return; }
            throw new Error('Error al cargar la base');
        }
        allDocs = await res.json();
        renderDocumentList(allDocs);
        refreshChatState();
    } catch (err) {
        console.error('Error loading documents:', err);
    }
}

function docMeta(doc) {
    if ((doc.media_type || 'pdf') === 'pdf') return `${doc.page_count} págs`;
    if (doc.media_type === 'image') return 'Imagen';
    return `${doc.media_type === 'video' ? 'Video' : 'Audio'} · ${fmtTime(doc.duration_sec)}`;
}

function renderDocumentList(docs) {
    const listEl = document.getElementById('doc-list');
    const countEl = document.getElementById('doc-count');
    countEl.textContent = docs.length;

    if (docs.length === 0) {
        listEl.innerHTML = `
            <div style="padding:24px 12px;text-align:center;">
                <p style="font-size:0.78rem;color:var(--zinc-500);">
                    La base está vacía.<br>Sube un PDF, un audio o un video, o pulsa "Indexar carpeta".
                </p>
            </div>
        `;
        return;
    }

    listEl.innerHTML = docs.map(doc => {
        const active = doc.id === currentDocumentId ? 'active' : '';
        const date = new Date(doc.uploaded_at).toLocaleDateString('es', { day: 'numeric', month: 'short' });
        const name = doc.original_name.length > 30 ? doc.original_name.substring(0, 27) + '...' : doc.original_name;
        const kind = doc.media_type || 'pdf';
        const del = doc.can_delete
            ? `<button class="doc-item-delete" onclick="event.stopPropagation(); deleteDoc('${doc.id}')" title="Eliminar de la base">
                   <i data-lucide="x" style="width:14px;height:14px;"></i>
               </button>` : '';
        return `
            <div class="doc-item ${active}" onclick="selectDocument('${doc.id}')" data-doc-id="${doc.id}" data-kind="${kind}">
                <div class="doc-item-icon">
                    <i data-lucide="${KIND_ICON[kind] || 'file'}"></i>
                </div>
                <div class="doc-item-info">
                    <h5 title="${escapeHtml(doc.source_path || doc.original_name)}">${escapeHtml(name)}</h5>
                    <p>${docMeta(doc)} · ${date}</p>
                </div>
                ${del}
            </div>
        `;
    }).join('');

    lucide.createIcons();
}

async function deleteDoc(docId) {
    if (!confirm('¿Quitar este archivo de la base de conocimiento? También se borra de la carpeta.')) return;
    try {
        const res = await fetch(`${API_BASE}/api/pdf/documents/${docId}`, { method: 'DELETE', headers: getHeaders() });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Error al eliminar');
        }
        showToast('Archivo eliminado de la base');
        if (currentDocumentId === docId) {
            currentDocumentId = null;
            currentDocument = null;
            showWelcome();
        }
        await loadDocuments();
    } catch (err) {
        showToast(err.message, 'error');
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// DOCUMENT SELECTION & VIEWER
// ══════════════════════════════════════════════════════════════════════════════
async function selectDocument(docId) {
    let doc = allDocs.find(d => d.id === docId);
    if (!doc) {
        await loadDocuments();
        doc = allDocs.find(d => d.id === docId);
    }
    if (!doc) return;

    const changed = currentDocumentId !== docId;
    currentDocumentId = docId;
    currentDocument = doc;
    document.querySelectorAll('.doc-item').forEach(el => {
        el.classList.toggle('active', el.dataset.docId === docId);
    });
    if (changed) renderDocumentView(doc);
    refreshChatState();
    if (chatMode === 'doc' && changed) await loadCurrentChat();
    closeMobilePanels();
}

function showWelcome() {
    pdfDoc = null;
    pdfLoadToken++;
    stopMedia();
    document.getElementById('viewer-welcome').style.display = 'flex';
    document.getElementById('viewer-document').classList.remove('active');
    document.getElementById('viewer-toolbar').style.display = 'none';
    const viewerTabs = document.getElementById('viewer-tabs');
    if (viewerTabs) viewerTabs.style.display = 'none';
    if (chatMode === 'doc') setChatMode('kb');
    else refreshChatState();
}

function renderDocumentView(doc) {
    if (!currentMarks || currentMarks.docId !== doc.id) clearMarks();
    // Hide welcome, show document view
    document.getElementById('viewer-welcome').style.display = 'none';
    document.getElementById('viewer-document').classList.add('active');
    document.getElementById('viewer-toolbar').style.display = 'flex';
    const viewerTabs = document.getElementById('viewer-tabs');
    if (viewerTabs) viewerTabs.style.display = 'flex';

    const kind = doc.media_type || 'pdf';
    const isMedia = kind !== 'pdf';
    const isImage = kind === 'image';
    document.getElementById('viewer').classList.toggle('is-media', isMedia);

    // Toolbar info
    document.getElementById('toolbar-doc-name').textContent = doc.original_name;
    document.getElementById('toolbar-doc-meta').textContent = isImage
        ? `Imagen · ${doc.chunk_count} fragmentos · ${doc.file_size_kb} KB`
        : isMedia
        ? `${doc.media_type === 'video' ? 'Video' : 'Audio'} · ${fmtTime(doc.duration_sec)} · ${doc.chunk_count} fragmentos · ${doc.file_size_kb} KB`
        : `${doc.page_count} páginas · ${doc.chunk_count} fragmentos · ${doc.file_size_kb} KB`;
    const tabLabel = document.getElementById('vtab-pdf-label');
    if (tabLabel) tabLabel.textContent = KIND_NAME[kind] || 'Documento';

    // Paginator reset + selector "Ir a página"
    currentPage = 1;
    const pageSelect = document.getElementById('toolbar-page-select');
    if (pageSelect) {
        const total = doc.page_count || 1;
        pageSelect.innerHTML = Array.from({ length: total }, (_, i) =>
            `<option value="${i + 1}">Página ${i + 1}</option>`).join('');
    }
    updatePageIndicator();

    // Reset Zoom
    resetDocZoom();

    // Cargar el PDF o el reproductor de audio/video
    if (isImage) loadImageForViewer(doc);
    else if (isMedia) loadMediaForViewer(doc);
    else loadPdfForViewer(doc);

    // Complexity badge
    const complexityEl = document.getElementById('toolbar-complexity');
    if (doc.complexity_level) {
        const level = doc.complexity_level.toLowerCase()
            .normalize("NFD").replace(/[\u0300-\u036f]/g, ""); // Remove accents
        complexityEl.style.display = 'inline-flex';
        complexityEl.className = `complexity-badge complexity-${level}`;
        const icons = { basico: 'zap', intermedio: 'flame', avanzado: 'atom', experto: 'brain' };
        complexityEl.innerHTML = `<i data-lucide="${icons[level] || 'gauge'}"></i>${doc.complexity_level}`;
    } else {
        complexityEl.style.display = 'none';
    }

    // Stats
    const statPages = document.getElementById('stat-pages');
    statPages.textContent = isImage ? 'IMG' : isMedia ? fmtTime(doc.duration_sec) : doc.page_count;
    if (statPages.nextElementSibling) statPages.nextElementSibling.textContent = isImage ? 'Tipo' : isMedia ? 'Duración' : 'Páginas';
    document.getElementById('stat-chunks').textContent = doc.chunk_count;
    document.getElementById('stat-size').textContent = doc.file_size_kb;
    document.getElementById('stat-complexity').textContent = doc.complexity_level || '–';

    // Summary
    document.getElementById('doc-summary').textContent = doc.summary || 'No hay resumen disponible.';

    // Key Concepts
    const tagsEl = document.getElementById('concept-tags');
    const concepts = doc.key_concepts || [];
    if (concepts.length > 0) {
        document.getElementById('concepts-section').style.display = 'block';
        tagsEl.innerHTML = concepts.map(c =>
            `<span class="concept-tag">${c}</span>`
        ).join('');
    } else {
        document.getElementById('concepts-section').style.display = 'none';
    }

    // Pestaña por defecto: el documento real
    switchViewerTab('pdf');

    // Generate dynamic Mermaid diagram and chunks preview
    renderDocMermaid(doc);
    renderDocChunks(doc);

    lucide.createIcons();
}

// ── Visor PDF real ──────────────────────────────────────────────────────────
async function loadPdfForViewer(doc) {
    const token = ++pdfLoadToken;
    pdfDoc = null;
    stopMedia();
    const statusEl = document.getElementById('pdf-status');
    const canvas = document.getElementById('pdf-canvas');
    document.getElementById('pdf-stage').style.display = '';
    document.getElementById('media-stage').style.display = 'none';
    canvas.style.display = 'none';
    statusEl.style.display = 'block';
    statusEl.textContent = 'Cargando documento...';

    if (!window.pdfjsLib) {
        statusEl.textContent = 'No se pudo cargar el visor de PDF (revisa tu conexión a internet).';
        return;
    }
    try {
        const res = await fetch(`${API_BASE}/api/pdf/documents/${doc.id}/file`, {
            headers: { 'Authorization': `Bearer ${getToken()}` },
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Error ${res.status}`);
        }
        const data = await res.arrayBuffer();
        const loaded = await pdfjsLib.getDocument({ data }).promise;
        if (token !== pdfLoadToken) return;   // el usuario ya cambió de documento
        pdfDoc = loaded;
        await renderPdfPage();
    } catch (e) {
        if (token !== pdfLoadToken) return;
        statusEl.textContent = `No se pudo mostrar el PDF: ${e.message}`;
    }
}

// ── Reproductor de audio / video con transcripción ──────────────────────────
function stopMedia() {
    const stage = document.getElementById('media-stage');
    if (stage) stage.classList.remove('is-image');
    const holder = document.getElementById('media-player');
    if (holder) holder.innerHTML = '';
    if (mediaUrl) { URL.revokeObjectURL(mediaUrl); mediaUrl = null; }
}

function getPlayer() {
    return document.querySelector('#media-player video, #media-player audio');
}

async function loadMediaForViewer(doc) {
    const token = ++pdfLoadToken;
    pdfDoc = null;
    stopMedia();
    document.getElementById('pdf-stage').style.display = 'none';
    const stage = document.getElementById('media-stage');
    stage.style.display = 'block';
    const holder = document.getElementById('media-player');
    holder.innerHTML = `<div class="pdf-status">Cargando ${doc.media_type === 'video' ? 'video' : 'audio'}...</div>`;
    document.getElementById('media-notes-title').textContent = doc.media_type === 'video' ? 'Transcripción y fotogramas' : 'Transcripción';
    document.getElementById('media-transcript').innerHTML = '';
    try {
        const res = await fetch(`${API_BASE}/api/pdf/documents/${doc.id}/file`, {
            headers: { 'Authorization': `Bearer ${getToken()}` },
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Error ${res.status}`);
        }
        const blob = await res.blob();
        if (token !== pdfLoadToken) return;
        mediaUrl = URL.createObjectURL(blob);
        const tag = doc.media_type === 'video' ? 'video' : 'audio';
        holder.innerHTML = (tag === 'audio' ? '<canvas class="eq" id="eq" aria-hidden="true"></canvas>' : '')
            + `<${tag} controls preload="metadata" src="${mediaUrl}"></${tag}>`;
        const player = getPlayer();
        player.addEventListener('loadedmetadata', () => {
            if (pendingSeek !== null) { player.currentTime = pendingSeek; pendingSeek = null; }
        });
        player.addEventListener('timeupdate', () => { highlightTranscript(); updateMediaHead(); });
        if (tag === 'audio') attachEq(player, document.getElementById('eq'));
        renderMediaMarks();
    } catch (e) {
        if (token !== pdfLoadToken) return;
        holder.innerHTML = `<div class="pdf-status">No se pudo cargar el archivo: ${escapeHtml(e.message)}</div>`;
    }
}

async function loadImageForViewer(doc) {
    const token = ++pdfLoadToken;
    pdfDoc = null;
    stopMedia();
    document.getElementById('pdf-stage').style.display = 'none';
    const stage = document.getElementById('media-stage');
    stage.style.display = 'block';
    stage.classList.add('is-image');
    const holder = document.getElementById('media-player');
    holder.innerHTML = '<div class="pdf-status">Cargando imagen...</div>';
    document.getElementById('media-notes-title').textContent = 'Lo que el modelo ve y lee';
    document.getElementById('media-transcript').innerHTML = '';
    try {
        const res = await fetch(`${API_BASE}/api/pdf/documents/${doc.id}/file`, {
            headers: { 'Authorization': `Bearer ${getToken()}` },
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Error ${res.status}`);
        }
        const blob = await res.blob();
        if (token !== pdfLoadToken) return;
        mediaUrl = URL.createObjectURL(blob);
        holder.innerHTML = `<div class="img-sheet" id="img-sheet" data-name="${escapeHtml(doc.original_name)}">
                <img id="kb-img" src="${mediaUrl}" alt="${escapeHtml(doc.original_name)}">
                <div class="mk-layer" id="img-marks"></div>
                <div class="loupe" aria-hidden="true"></div>
            </div>`;
        const img = document.getElementById('kb-img');
        img.addEventListener('load', () => { drawImageMarks(); attachLoupe(document.getElementById('img-sheet'), img); });
    } catch (e) {
        if (token !== pdfLoadToken) return;
        holder.innerHTML = `<div class="pdf-status">No se pudo cargar la imagen: ${escapeHtml(e.message)}</div>`;
    }
}

function renderImageNotes(chunks) {
    const el = document.getElementById('media-transcript');
    if (!el) return;
    el.innerHTML = chunks.map(c => {
        const isDesc = c.kind === 'image';
        const text = c.text.replace(/^\[[^\]]*\]\s*/, '');
        return `<div class="tr-line ${isDesc ? 'tr-frame' : 'tr-transcript'}">
                    <span class="tr-time">${isDesc ? 'Ve' : 'Lee'}</span>
                    <span class="tr-kind">${isDesc ? 'Descripción' : 'Texto (OCR)'}</span>
                    <span class="tr-text">${escapeHtml(text)}</span>
                </div>`;
    }).join('');
}

function renderTranscript(chunks) {
    const el = document.getElementById('media-transcript');
    if (!el) return;
    el.innerHTML = chunks.map(c => {
        const sec = c.start_sec || 0;
        const label = c.kind === 'frame' ? 'Fotograma' : 'Transcripción';
        const text = c.text.replace(/^\[[^\]]*\]\s*/, '');
        return `<button class="tr-line tr-${c.kind}" data-chunk="${c.chunk_index ?? c.index - 1}" data-start="${sec}" data-end="${c.end_sec || sec}" onclick="seekMedia(${sec})">
                    <span class="tr-time">${fmtTime(sec)}</span>
                    <span class="tr-kind">${label}</span>
                    <span class="tr-text">${escapeHtml(text)}</span>
                </button>`;
    }).join('');
}

function highlightTranscript() {
    const player = getPlayer();
    if (!player) return;
    const t = player.currentTime;
    document.querySelectorAll('#media-transcript .tr-transcript').forEach(el => {
        const a = parseFloat(el.dataset.start), b = parseFloat(el.dataset.end);
        el.classList.toggle('playing', t >= a && t < b);
    });
}

function seekMedia(sec) {
    if (!currentDocument || ['pdf', 'image'].includes(currentDocument.media_type || 'pdf')) return;
    if (currentViewerTab !== 'pdf') switchViewerTab('pdf');
    const player = getPlayer();
    if (player && player.readyState >= 1) {
        player.currentTime = sec;
        player.play().catch(() => {});
    } else {
        pendingSeek = sec;
    }
    if (typeof closeMobilePanels === 'function' && window.innerWidth <= 900) closeMobilePanels();
}

// Abre la fuente de una cita (otro archivo de la base, una página o un minuto)
async function openSource(docId, page, sec) {
    if (docId && docId !== currentDocumentId) {
        await selectDocument(docId);
    }
    if (sec !== null && sec !== undefined && sec !== '') {
        seekMedia(Number(sec));
    } else if (page) {
        goToDocPage(Number(page));
    }
}

async function openCitation(name, unit, value, ctxId) {
    const doc = allDocs.find(d => d.original_name === name)
        || allDocs.find(d => d.original_name.toLowerCase() === name.toLowerCase());
    if (!doc) { showToast(`"${name}" no está en la base`, 'error'); return; }
    const ctx = msgCtx[ctxId] || {};
    const isTime = /^min/i.test(unit);
    const page = isTime ? null : parseInt(value, 10);
    const sec = isTime ? parseTime(value) : null;
    const src = (ctx.sources || []).find(s => s && typeof s === 'object' && s.doc_id === doc.id &&
        (isTime ? Math.abs((s.start_sec ?? -99) - sec) < 1.5 : s.page === page))
        || { page, start_sec: sec, chunks: [] };
    await openSource(doc.id, page, sec);
    highlightFor(doc.id, src, ctx);
}

// ══════════════════════════════════════════════════════════════════════════════
// CHUNKS DE MARCADO – subraya en el visor de dónde salió la respuesta
// ══════════════════════════════════════════════════════════════════════════════
// Índice de la fuente que coincide con la primera cita de la respuesta ([Página 1], [archivo.pdf, página 3], [Minuto 01:20])
function citedSourceIndex(answer, sources) {
    const text = answer || '';
    const m = text.match(/\[(?:([^\[\],]+?\.[a-z0-9]{2,4}),\s*)?(p[áa]gina|p[áa]g\.?|minuto|min\.?)\s*([\d:]+)\]/i);
    if (!m) return 0;
    const [, name, unit, val] = m;
    const isTime = /^min/i.test(unit);
    const target = isTime ? parseTime(val) : parseInt(val, 10);
    const idx = sources.findIndex(s => s && typeof s === 'object' &&
        (!name || !s.name || s.name.toLowerCase() === name.trim().toLowerCase()) &&
        (isTime ? Math.abs((s.start_sec ?? -99) - target) < 1.5 : s.page === target));
    return idx >= 0 ? idx : 0;
}

async function openSourceIdx(ctxId, i) {
    const ctx = msgCtx[ctxId];
    if (!ctx) return;
    const src = ctx.sources[i];
    if (typeof src !== 'object' || src === null) {          // historial antiguo (solo página)
        markPage(parseInt(src, 10), ctxId);
        return;
    }
    const docId = src.doc_id || currentDocumentId;
    if (!docId) return;
    await openSource(docId, src.page, src.start_sec ?? null);
    highlightFor(docId, src, ctx);
}

function markPage(page, ctxId) {
    if (!currentDocument) return;
    const ctx = msgCtx[ctxId] || {};
    const src = (ctx.sources || []).find(s => s && typeof s === 'object' && (!s.doc_id || s.doc_id === currentDocumentId) && s.page === page)
        || { page, chunks: [] };
    goToDocPage(page);
    highlightFor(currentDocumentId, src, ctx);
}

function markTime(sec, ctxId) {
    if (!currentDocument) return;
    const ctx = msgCtx[ctxId] || {};
    const src = (ctx.sources || []).find(s => s && typeof s === 'object' && (!s.doc_id || s.doc_id === currentDocumentId) &&
        Math.abs((s.start_sec ?? -99) - sec) < 1.5) || { start_sec: sec, chunks: [] };
    seekMedia(sec);
    highlightFor(currentDocumentId, src, ctx);
}

async function highlightFor(docId, src, ctx) {
    const token = ++markToken;
    const legend = document.getElementById('mark-legend');
    if (legend) {
        legend.innerHTML = '<span class="ml-swatch ml-busy"></span><b>Ubicando de dónde salió la respuesta…</b>'
            + '<span class="ml-where">en páginas escaneadas el primer marcado tarda unos segundos</span>';
        legend.hidden = false;
    }
    try {
        const res = await fetch(`${API_BASE}/api/pdf/documents/${docId}/highlight`, {
            method: 'POST', headers: getHeaders(),
            body: JSON.stringify({
                chunks: src.chunks || [], page: src.page || null, start_sec: src.start_sec ?? null,
                text: `${ctx.question || ''}\n${ctx.answer || ''}`,
                question: ctx.question || '', answer: ctx.answer || '',
            }),
        });
        if (!res.ok) throw new Error(`Error ${res.status}`);
        const data = await res.json();
        if (token !== markToken || docId !== currentDocumentId) return;
        const found = (data.boxes || []).length + (data.marks || []).length + (data.segments || []).length + (data.whole ? 1 : 0);
        if (!found) {
            if (legend) legend.innerHTML = '<b>No se pudo ubicar el fragmento exacto</b><span class="ml-where">la respuesta usó esta parte del archivo</span>'
                + '<button class="ml-clear" onclick="clearMarks()">Cerrar</button>';
            return;
        }
        currentMarks = { docId, data, src };
        applyMarks();
    } catch (e) {
        console.warn('No se pudo marcar la fuente:', e);
        if (token === markToken && legend) legend.hidden = true;
    }
}

function clearMarks() {
    currentMarks = null;
    ['pdf-marks', 'img-marks'].forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = ''; });
    document.querySelectorAll('.tr-line.cited, .chunk-card.cited').forEach(el => el.classList.remove('cited'));
    document.querySelectorAll('mark.mk-word').forEach(m => m.replaceWith(document.createTextNode(m.textContent)));
    const legend = document.getElementById('mark-legend');
    if (legend) legend.hidden = true;
    renderMediaMarks();
}

function applyMarks() {
    const m = currentMarks;
    if (!m) return;
    const d = m.data;
    if (currentViewerTab !== 'pdf') switchViewerTab('pdf');
    if (d.type === 'pdf') {
        if (d.page && d.page !== currentPage) goToDocPage(d.page);   // al dibujar la página se marcan
        else drawPdfMarks();
    } else if (d.type === 'image') {
        drawImageMarks();
    } else if (d.type === 'media') {
        markTranscript();
    }
    applyChunkMarks();
    renderMarkLegend();
}

function jsNorm(w) {
    return w.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9ñ]/g, '');
}

function isTerm(tok, terms) {
    if (!tok || tok.length < 3) return false;
    return terms.some(t => tok === t || (t.length >= 5 && tok.length >= 5 && (tok.startsWith(t.slice(0, 5)) || t.startsWith(tok.slice(0, 5)))));
}

function markTerms(text, terms) {
    if (!terms || !terms.length) return escapeHtml(text);
    return text.split(/(\s+)/).map(part => {
        const tok = jsNorm(part);
        return isTerm(tok, terms) ? `<mark class="mk-word">${escapeHtml(part)}</mark>` : escapeHtml(part);
    }).join('');
}

function markItems(list, cls, offset = 0) {
    return list.map((b, i) => `<i class="${cls}" style="left:${b.x * 100}%;top:${b.y * 100}%;width:${b.w * 100}%;height:${b.h * 100}%;--i:${Math.min(i + offset, 24)}"${b.text ? ` title="${escapeHtml(b.text)}"` : ''}></i>`).join('');
}

function drawPdfMarks() {
    const layer = document.getElementById('pdf-marks');
    if (!layer) return;
    layer.innerHTML = '';
    const m = currentMarks;
    if (!m || m.docId !== currentDocumentId || m.data.type !== 'pdf' || m.data.page !== currentPage) return;
    const boxes = m.data.boxes || [];
    layer.innerHTML = markItems(boxes, 'mk-box') + markItems(m.data.marks || [], 'mk-key', boxes.length);
    const first = layer.querySelector('.mk-key, .mk-box');
    if (first) setTimeout(() => first.scrollIntoView({ block: 'center', behavior: REDUCED_MOTION ? 'auto' : 'smooth' }), 120);
}

function drawImageMarks() {
    const layer = document.getElementById('img-marks');
    const img = document.getElementById('kb-img');
    if (!layer || !img || !img.complete) return;
    layer.innerHTML = '';
    const m = currentMarks;
    if (!m || m.docId !== currentDocumentId || m.data.type !== 'image') return;
    let html = '';
    if (m.data.whole) html += '<i class="mk-frame"><span>Descripción usada en la respuesta</span></i>';
    html += markItems(m.data.boxes || [], 'mk-box');
    html += markItems(m.data.marks || [], 'mk-ring', (m.data.boxes || []).length);
    layer.innerHTML = html;
}

function markTranscript() {
    const m = currentMarks;
    if (!m || m.data.type !== 'media') return;
    const cited = new Set((m.data.segments || []).map(s => s.chunk));
    const terms = m.data.terms || [];
    let first = null;
    document.querySelectorAll('#media-transcript .tr-line').forEach(el => {
        const on = cited.has(Number(el.dataset.chunk));
        el.classList.toggle('cited', on);
        const t = el.querySelector('.tr-text');
        if (t) t.innerHTML = on ? markTerms(t.textContent, terms) : escapeHtml(t.textContent);
        if (on && !first) first = el;
    });
    if (first) first.scrollIntoView({ block: 'nearest', behavior: REDUCED_MOTION ? 'auto' : 'smooth' });
    renderMediaMarks();
}

// Línea de tiempo bajo el reproductor: tramos citados + cabezal (en el estudio de radio es el dial)
function renderMediaMarks() {
    const bar = document.getElementById('media-marks');
    if (!bar) return;
    const doc = currentDocument;
    if (!doc || !['audio', 'video'].includes(doc.media_type)) { bar.hidden = true; bar.innerHTML = ''; return; }
    const dur = doc.duration_sec || 1;
    const m = currentMarks && currentMarks.docId === doc.id && currentMarks.data.type === 'media' ? currentMarks.data : null;
    const ticks = Array.from({ length: 11 }, (_, i) => `<span class="mm-tick" style="left:${i * 10}%"><em>${fmtTime(dur * i / 10)}</em></span>`).join('');
    const segs = (m ? m.segments : []).map(s => {
        const left = Math.max(0, s.start / dur * 100);
        const width = Math.max(1.2, (Math.max(s.end, s.start + 1) - s.start) / dur * 100);
        return `<button class="mm-seg" style="left:${left}%;width:${Math.min(width, 100 - left)}%" onclick="seekMedia(${s.start})" title="Ir al ${fmtTime(s.start)}"></button>`;
    }).join('');
    bar.innerHTML = `<div class="mm-track" onclick="seekFromBar(event)">${ticks}${segs}<span class="mm-head" id="mm-head"></span></div>`;
    bar.hidden = false;
    updateMediaHead();
}

function seekFromBar(ev) {
    if (ev.target.classList.contains('mm-seg')) return;
    const track = ev.currentTarget;
    const r = track.getBoundingClientRect();
    const ratio = Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1);
    seekMedia(ratio * (currentDocument?.duration_sec || 0));
}

function updateMediaHead() {
    const head = document.getElementById('mm-head');
    const player = getPlayer();
    if (!head || !currentDocument) return;
    const dur = currentDocument.duration_sec || 1;
    const t = player ? player.currentTime : 0;
    head.style.left = `${Math.min(t / dur * 100, 100)}%`;
}

function applyChunkMarks() {
    const m = currentMarks;
    if (!m) return;
    const cited = new Set(m.data.type === 'media' ? (m.data.segments || []).map(s => s.chunk) : (m.src.chunks || []));
    document.querySelectorAll('#doc-chunks-list .chunk-card').forEach(card => {
        const on = cited.has(Number(card.dataset.chunk));
        card.classList.toggle('cited', on);
        const p = card.querySelector('p');
        if (p) p.innerHTML = on ? markTerms(p.textContent, m.data.terms || []) : escapeHtml(p.textContent);
    });
}

function renderMarkLegend() {
    const el = document.getElementById('mark-legend');
    const m = currentMarks;
    if (!el || !m) return;
    const terms = (m.data.terms || []).slice(0, 6);
    const what = m.data.type === 'pdf' ? `Página ${m.data.page}${m.data.ocr ? ' (texto leído con OCR)' : ''}`
        : m.data.type === 'image' ? (m.data.marks?.length ? 'Texto encontrado en la imagen' : 'Descripción de la imagen')
        : `${(m.data.segments || []).length} tramo(s) de la grabación`;
    el.innerHTML = `<span class="ml-swatch"></span><b>De aquí salió la respuesta</b><span class="ml-where">${what}</span>
        ${terms.map(t => `<span class="ml-term">${escapeHtml(t)}</span>`).join('')}
        <button class="ml-clear" onclick="clearMarks()" title="Quitar el marcado">Quitar marcado</button>`;
    el.hidden = false;
}

// ══════════════════════════════════════════════════════════════════════════════
// ESCENAS – la interfaz cambia según el tipo de archivo con el que se conversa
// ══════════════════════════════════════════════════════════════════════════════
function applyScene() {
    const viewer = document.getElementById('viewer');
    if (viewer) viewer.dataset.kind = currentDocument ? (currentDocument.media_type || 'pdf') : 'none';
    const scene = chatMode === 'kb' ? 'kb' : ((currentDocument && currentDocument.media_type) || 'pdf');
    if (scene === currentScene) return;
    const first = currentScene === null;
    currentScene = scene;
    const curtain = document.getElementById('scene-curtain');
    if (first || REDUCED_MOTION || !curtain) {
        document.body.dataset.scene = scene;
        return;
    }
    curtain.dataset.to = scene;
    curtain.querySelector('span').textContent = SCENE_NAME[scene];
    curtain.classList.remove('run');
    void curtain.offsetWidth;            // reinicia la animación
    curtain.classList.add('run');
    setTimeout(() => {
        document.body.dataset.scene = scene;
        if (pdfDoc && currentViewerTab === 'pdf') renderPdfPage();   // el ancho del visor puede cambiar
    }, 420);
}

// Ecualizador del estudio de radio: barras que se mueven con el audio real (Web Audio API)
function attachEq(player, canvas) {
    const draw = () => {
        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        const W = canvas.width = canvas.clientWidth * dpr;
        const H = canvas.height = canvas.clientHeight * dpr;
        const styles = getComputedStyle(canvas);
        const c1 = styles.getPropertyValue('--mag').trim() || '#FF8A3D';
        const c2 = styles.getPropertyValue('--cyan').trim() || '#FFC857';
        const n = 32;
        const data = player._an ? new Uint8Array(player._an.frequencyBinCount) : null;
        if (data) player._an.getByteFrequencyData(data);
        ctx.clearRect(0, 0, W, H);
        const bw = W / n;
        for (let i = 0; i < n; i++) {
            const v = data ? data[Math.floor(i * data.length / n)] / 255 : 0.06 + 0.04 * Math.sin(i / 2);
            const h = Math.max(2 * dpr, v * H);
            const segs = Math.floor(h / (6 * dpr));
            for (let s = 0; s < segs; s++) {
                ctx.fillStyle = s > (H / (6 * dpr)) * 0.7 ? c1 : c2;
                ctx.fillRect(i * bw + bw * 0.15, H - (s + 1) * 6 * dpr, bw * 0.7, 4 * dpr);
            }
        }
        if (!player.paused && document.body.contains(canvas)) requestAnimationFrame(draw);
    };
    player.addEventListener('play', () => {
        try {
            if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            audioCtx.resume();
            if (!player._an) {
                const node = audioCtx.createMediaElementSource(player);
                const an = audioCtx.createAnalyser();
                an.fftSize = 128;
                node.connect(an);
                an.connect(audioCtx.destination);
                player._an = an;
            }
        } catch (e) { console.warn('Ecualizador no disponible:', e); }
        requestAnimationFrame(draw);
    });
    draw();
}

// Lupa de la mesa de luz: amplía la imagen bajo el cursor
function attachLoupe(sheet, img) {
    const loupe = sheet.querySelector('.loupe');
    if (!loupe) return;
    const Z = 2.6;
    sheet.addEventListener('pointermove', ev => {
        if (ev.pointerType === 'touch') return;
        const r = img.getBoundingClientRect();
        const x = ev.clientX - r.left, y = ev.clientY - r.top;
        if (x < 0 || y < 0 || x > r.width || y > r.height) { loupe.classList.remove('on'); return; }
        const size = loupe.offsetWidth || 160;
        loupe.style.left = `${x + (r.left - sheet.getBoundingClientRect().left) - size / 2}px`;
        loupe.style.top = `${y + (r.top - sheet.getBoundingClientRect().top) - size / 2}px`;
        loupe.style.backgroundImage = `url(${img.src})`;
        loupe.style.backgroundSize = `${r.width * Z}px ${r.height * Z}px`;
        loupe.style.backgroundPosition = `${-(x * Z - size / 2)}px ${-(y * Z - size / 2)}px`;
        loupe.classList.add('on');
    });
    sheet.addEventListener('pointerleave', () => loupe.classList.remove('on'));
}

async function renderPdfPage() {
    if (!pdfDoc) return;
    const canvas = document.getElementById('pdf-canvas');
    const stage = document.getElementById('pdf-stage');
    const statusEl = document.getElementById('pdf-status');

    const pageNum = Math.min(Math.max(currentPage, 1), pdfDoc.numPages);
    const page = await pdfDoc.getPage(pageNum);

    // Ajustar al ancho disponible y luego aplicar el zoom del usuario
    const base = page.getViewport({ scale: 1 });
    const available = Math.max((stage.clientWidth || 800) - 8, 300);
    const cssScale = (available / base.width) * (currentZoom / 100);
    const dpr = window.devicePixelRatio || 1;
    const viewport = page.getViewport({ scale: cssScale * dpr });

    if (pdfRenderTask) {
        try { pdfRenderTask.cancel(); } catch (_) {}
    }
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    canvas.style.width = `${Math.floor(viewport.width / dpr)}px`;
    canvas.style.height = `${Math.floor(viewport.height / dpr)}px`;

    pdfRenderTask = page.render({ canvasContext: canvas.getContext('2d'), viewport });
    try {
        await pdfRenderTask.promise;
    } catch (e) {
        if (e && e.name === 'RenderingCancelledException') return;
        throw e;
    }
    statusEl.style.display = 'none';
    canvas.style.display = 'block';
    document.getElementById('viewer-document').scrollTop = 0;
    drawPdfMarks();
}

let _pdfResizeTimer = null;
window.addEventListener('resize', () => {
    clearTimeout(_pdfResizeTimer);
    _pdfResizeTimer = setTimeout(() => {
        if (pdfDoc && currentViewerTab === 'pdf') renderPdfPage();
    }, 200);
});

// ── Zoom Controls ───────────────────────────────────────────────────────────
function applyZoom() {
    // El zoom solo afecta al PDF (se vuelve a dibujar nítido, no se estira)
    const indicator = document.getElementById('toolbar-zoom-indicator');
    if (indicator) indicator.textContent = `${currentZoom}%`;
    if (currentViewerTab === 'pdf' && pdfDoc) renderPdfPage();
}

function zoomDoc(delta) {
    currentZoom = Math.max(50, Math.min(200, currentZoom + delta));
    if (currentViewerTab !== 'pdf') {
        switchViewerTab('pdf');   // ya aplica el zoom al mostrar el documento
    } else {
        applyZoom();
    }
}

function resetDocZoom() {
    currentZoom = 100;
    applyZoom();
}

// ── Paginator Controls ──────────────────────────────────────────────────────
function updatePageIndicator() {
    const totalPages = currentDocument ? (currentDocument.page_count || 1) : 1;
    const indicator = document.getElementById('toolbar-page-indicator');
    if (indicator) {
        indicator.textContent = `Pág. ${currentPage} / ${totalPages}`;
    }
    const select = document.getElementById('toolbar-page-select');
    if (select) select.value = String(currentPage);
    const prev = document.getElementById('btn-prev-page');
    const next = document.getElementById('btn-next-page');
    if (prev) prev.disabled = currentPage <= 1;
    if (next) next.disabled = currentPage >= totalPages;
}

function goToDocPage(page) {
    if (currentDocument && currentDocument.media_type === 'image') {   // imagen suelta: mostrarla
        if (currentViewerTab !== 'pdf') switchViewerTab('pdf');
        return;
    }
    if (currentDocument && (currentDocument.media_type || 'pdf') !== 'pdf') return;
    const totalPages = currentDocument ? (currentDocument.page_count || 1) : 1;
    if (!page || isNaN(page)) return;
    currentPage = Math.min(Math.max(page, 1), totalPages);
    updatePageIndicator();
    if (currentViewerTab !== 'pdf') {
        switchViewerTab('pdf');   // switchViewerTab ya dibuja la página
    } else {
        renderPdfPage();
    }
    // En móvil, cerrar el chat para que se vea el visor
    if (typeof closeMobilePanels === 'function' && window.innerWidth <= 900) closeMobilePanels();
}

function prevDocPage() {
    goToDocPage(currentPage - 1);
}

function nextDocPage() {
    goToDocPage(currentPage + 1);
}

// ── View Tab Switcher ───────────────────────────────────────────────────────
function switchViewerTab(tab) {
    currentViewerTab = tab;
    ['pdf', 'summary', 'diagram', 'chunks'].forEach(t => {
        const btn = document.getElementById(`vtab-${t}`);
        const panel = document.getElementById(`vpanel-${t}`);
        if (btn) btn.classList.toggle('active', t === tab);
        if (panel) panel.style.display = (t === tab) ? 'block' : 'none';
    });
    applyZoom();
}

// ── Mermaid Diagram Generator ───────────────────────────────────────────────
async function renderDocMermaid(doc) {
    const container = document.getElementById('doc-mermaid-diagram');
    if (!container) return;

    const concepts = (doc.key_concepts || []).slice(0, 5);
    const safeDocName = (doc.original_name || 'Documento').replace(/[^a-zA-Z0-9_\s]/g, '').substring(0, 24);

    const isMedia = (doc.media_type || 'pdf') !== 'pdf';
    const icon = { pdf: '📄', audio: '🎧', video: '🎬', image: '🖼️' }[doc.media_type || 'pdf'];
    const metaTxt = doc.media_type === 'image' ? `Imagen | ${doc.chunk_count} Fragmentos` : isMedia ? `${fmtTime(doc.duration_sec).replace(':', 'm')}s | ${doc.chunk_count} Fragmentos`
                            : `${doc.page_count} Páginas | ${doc.chunk_count} Fragmentos`;
    let nodes = `graph TD\n    Root["${icon} ${safeDocName}"] --> Meta["📊 ${metaTxt}"]\n`;
    concepts.forEach((concept, idx) => {
        const safeConcept = concept.replace(/[^a-zA-Z0-9_\s]/g, '').trim();
        if (safeConcept) {
            nodes += `    Root --> C${idx}["🔑 ${safeConcept}"]\n`;
            nodes += `    C${idx} --> RAG${idx}["⚡ EmbeddingGemma 2 · pgvector"]\n`;
        }
    });
    nodes += `    Meta --> Inference["🧠 Respuesta con Groq"]\n`;

    try {
        const id = `mermaid-doc-${Date.now()}`;
        const { svg } = await mermaid.render(id, nodes);
        container.innerHTML = svg;
    } catch (e) {
        console.warn('Error rendering document Mermaid:', e);
    }
}

// ── Chunk Explorer Renderer ─────────────────────────────────────────────────
async function renderDocChunks(doc) {
    const listEl = document.getElementById('doc-chunks-list');
    if (!listEl) return;
    listEl.innerHTML = `<div style="padding:14px;color:var(--zinc-400);font-size:0.8rem;">Cargando fragmentos...</div>`;
    try {
        const res = await fetch(`${API_BASE}/api/pdf/documents/${doc.id}/chunks`, { headers: getHeaders() });
        if (!res.ok) throw new Error(`Error ${res.status}`);
        const chunks = await res.json();
        if (doc.id !== currentDocumentId) return;
        if (doc.media_type === 'image') renderImageNotes(chunks);
        else if ((doc.media_type || 'pdf') !== 'pdf') renderTranscript(chunks);
        const reapplyMarks = () => {
            if (currentMarks && currentMarks.docId === doc.id) {
                applyChunkMarks();
                if (currentMarks.data.type === 'media') markTranscript();
            }
        };
        setTimeout(reapplyMarks, 0);
        if (!chunks.length) {
            listEl.innerHTML = `<div style="padding:14px;color:var(--zinc-400);font-size:0.8rem;">Este documento no tiene fragmentos indexados.</div>`;
            return;
        }
        const KIND_LABEL = { text: 'Texto', image: 'Imagen', transcript: 'Transcripción', frame: 'Fotograma' };
        const isImageDoc = doc.media_type === 'image';
        docChunksCache = chunks;
        listEl.innerHTML = chunks.map(c => {
            const loc = (c.start_sec !== null && c.start_sec !== undefined)
                ? `<span class="page-link" style="font-size:0.68rem;padding:2px 8px;border-radius:6px;" onclick="seekMedia(${c.start_sec})">Ir al ${fmtTime(c.start_sec)}</span>`
                : isImageDoc
                ? `<span class="page-link" style="font-size:0.68rem;padding:2px 8px;border-radius:6px;" onclick="switchViewerTab('pdf')">Ver imagen</span>`
                : `<span class="page-link" style="font-size:0.68rem;padding:2px 8px;border-radius:6px;" onclick="goToDocPage(${c.page})">Ver página ${c.page}</span>`;
            return `
            <div class="chunk-card" data-chunk="${c.chunk_index ?? c.index - 1}" style="padding:14px;background:var(--zinc-850);border:1px solid var(--zinc-800);border-radius:10px;display:flex;flex-direction:column;gap:6px;">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
                    <span style="font-size:0.75rem;font-weight:600;color:var(--emerald-400);">Fragmento #${c.index} · ${KIND_LABEL[c.kind] || c.kind} · ${c.words} palabras${c.dims ? ` · vector(${c.dims})` : ''}</span>
                    ${loc}
                </div>
                <p style="font-size:0.75rem;color:var(--zinc-300);line-height:1.5;white-space:pre-wrap;max-height:9em;overflow:auto;">${escapeHtml(c.text)}</p>
            </div>`;
        }).join('');
    } catch (e) {
        listEl.innerHTML = `<div style="padding:14px;color:var(--zinc-400);font-size:0.8rem;">No se pudieron cargar los fragmentos: ${escapeHtml(e.message)}</div>`;
    }
}

// ── Interactive Multimedia Demo Player Logic ────────────────────────────────
const demoSteps = [
    {
        title: '1. Ingesta multimodal de la carpeta',
        desc: 'PDFs: texto por página + imágenes descritas con qwen2.5vl. Audios y videos: transcripción con Whisper y fotogramas descritos con el modelo de visión.',
        progress: 33,
        time: '01:00 / 03:00',
    },
    {
        title: '2. EmbeddingGemma 2 (llama.cpp) + PostgreSQL / pgvector',
        desc: 'Cada fragmento (con su página o su minuto) se vectoriza con EmbeddingGemma 2 servido por llama.cpp y se guarda en pgvector (768 dimensiones).',
        progress: 66,
        time: '02:00 / 03:00',
    },
    {
        title: '3. Consulta semántica en toda la base',
        desc: 'La pregunta se busca por similitud coseno en todos los archivos y el LLM (Groq) responde citando archivo y página o minuto exacto.',
        progress: 100,
        time: '03:00 / 03:00',
    },
];

function selectDemoStep(idx) {
    demoStepIndex = idx;
    const step = demoSteps[idx];
    const titleEl = document.getElementById('demo-slide-title');
    const descEl = document.getElementById('demo-slide-desc');
    const progressEl = document.getElementById('demo-progress-bar');
    const timeEl = document.getElementById('demo-time-display');

    if (titleEl) titleEl.textContent = step.title;
    if (descEl) descEl.textContent = step.desc;
    if (progressEl) progressEl.style.width = `${step.progress}%`;
    if (timeEl) timeEl.textContent = step.time;

    document.querySelectorAll('.video-tour-tab').forEach((tab, i) => {
        tab.classList.toggle('active', i === idx);
    });
}

function toggleDemoPlayback() {
    isDemoPlaying = !isDemoPlaying;
    const playBtnIcon = document.getElementById('video-play-icon');
    const ctrlPlayBtn = document.getElementById('btn-demo-ctrl-play');

    if (isDemoPlaying) {
        if (playBtnIcon) playBtnIcon.setAttribute('data-lucide', 'pause');
        if (ctrlPlayBtn) ctrlPlayBtn.innerHTML = '<i data-lucide="pause" style="width:14px;height:14px;"></i>';
        showToast('Reproduciendo demostración interactiva', 'success');

        demoInterval = setInterval(() => {
            demoStepIndex = (demoStepIndex + 1) % demoSteps.length;
            selectDemoStep(demoStepIndex);
        }, 4000);
    } else {
        if (playBtnIcon) playBtnIcon.setAttribute('data-lucide', 'play');
        if (ctrlPlayBtn) ctrlPlayBtn.innerHTML = '<i data-lucide="play" style="width:14px;height:14px;"></i>';
        clearInterval(demoInterval);
    }
    lucide.createIcons();
}

function seekDemoProgress(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    const clickX = event.clientX - rect.left;
    const ratio = clickX / rect.width;
    const stepIdx = Math.min(2, Math.floor(ratio * 3));
    selectDemoStep(stepIdx);
}

function restartDemoPlayback() {
    clearInterval(demoInterval);
    isDemoPlaying = false;
    selectDemoStep(0);
    const playBtnIcon = document.getElementById('video-play-icon');
    const ctrlPlayBtn = document.getElementById('btn-demo-ctrl-play');
    if (playBtnIcon) playBtnIcon.setAttribute('data-lucide', 'play');
    if (ctrlPlayBtn) ctrlPlayBtn.innerHTML = '<i data-lucide="play" style="width:14px;height:14px;"></i>';
    lucide.createIcons();
}

// ══════════════════════════════════════════════════════════════════════════════
// CHAT SYSTEM
// ══════════════════════════════════════════════════════════════════════════════
const CHIPS = {
    doc: [
        ['scroll-text', 'Resumen ejecutivo', 'Dame un resumen ejecutivo completo de este archivo'],
        ['network', 'Mapa mental', 'Genera un mapa mental de este archivo usando un diagrama Mermaid'],
        ['image', 'Lo visual', '¿Qué muestran las imágenes o fotogramas de este archivo?'],
        ['table-2', 'Tabla resumen', 'Crea una tabla con los puntos más importantes de este archivo'],
    ],
    kb: [
        ['library', 'Temas de la base', '¿Qué temas cubre la base de conocimiento? Resume cada archivo en una línea'],
        ['git-compare', 'Comparar fuentes', '¿Qué temas aparecen en más de un archivo y qué dice cada uno?'],
        ['image', 'Imágenes y videos', '¿Qué se ve en las imágenes de los PDFs y en los videos de la base?'],
        ['network', 'Mapa de la base', 'Genera un mapa mental en Mermaid que conecte los archivos de la base por tema'],
    ],
};

function renderChips() {
    const chips = document.getElementById('suggestion-chips');
    chips.innerHTML = CHIPS[chatMode].map(([icon, label, q]) =>
        `<button class="chip" onclick="sendSuggestion('${q.replace(/'/g, "\\'")}')"><i data-lucide="${icon}"></i>${label}</button>`
    ).join('');
    lucide.createIcons();
}

function setChatMode(mode) {
    if (mode === 'doc' && !currentDocumentId) {
        showToast('Abre un archivo de la base para preguntarle solo a él', 'error');
        return;
    }
    if (chatMode === mode) return;
    chatMode = mode;
    refreshChatState();
    loadCurrentChat();
}

function refreshChatState() {
    const input = document.getElementById('chat-input');
    const btnSend = document.getElementById('btn-send');
    const chips = document.getElementById('suggestion-chips');
    const clearBtn = document.getElementById('btn-clear-chat');
    const statusEl = document.getElementById('chat-status');
    const titleEl = document.getElementById('chat-title');
    const btnKb = document.getElementById('mode-kb');
    const btnDoc = document.getElementById('mode-doc');

    if (btnKb) btnKb.classList.toggle('active', chatMode === 'kb');
    if (btnDoc) {
        btnDoc.classList.toggle('active', chatMode === 'doc');
        btnDoc.disabled = !currentDocumentId;
    }

    let enabled, status, placeholder;
    if (chatMode === 'kb') {
        enabled = allDocs.length > 0;
        if (titleEl) titleEl.textContent = 'Consulta a toda la base';
        status = enabled ? `Buscando en ${allDocs.length} archivo(s): PDFs, imágenes, audios y videos` : 'La base está vacía: sube o indexa archivos';
        placeholder = enabled ? 'Pregunta algo a toda la base de conocimiento...' : 'Primero agrega archivos a la base...';
    } else {
        enabled = !!currentDocument;
        if (titleEl) titleEl.textContent = 'Consulta a este archivo';
        status = enabled ? `Solo en: ${currentDocument.original_name}` : 'Selecciona un archivo';
        placeholder = 'Pregunta algo sobre este archivo...';
    }
    input.disabled = !enabled;
    btnSend.disabled = !enabled;
    chips.style.display = enabled ? 'flex' : 'none';
    clearBtn.style.display = enabled ? 'block' : 'none';
    statusEl.textContent = status;
    input.placeholder = placeholder;
    renderChips();
    applyScene();
}

function chatEmptyState(title, text) {
    document.getElementById('chat-messages').innerHTML = `
        <div class="chat-empty" id="chat-empty">
            <div class="chat-empty-icon"><i data-lucide="message-circle-question"></i></div>
            <h4>${title}</h4>
            <p>${text}</p>
        </div>`;
    lucide.createIcons();
}

async function loadCurrentChat() {
    const url = chatMode === 'kb'
        ? `${API_BASE}/api/kb/history`
        : (currentDocumentId ? `${API_BASE}/api/chat/history/${currentDocumentId}` : null);
    if (!url) {
        chatEmptyState('Comienza una conversación', 'Abre un archivo o cambia a "Toda la base".');
        return;
    }
    const mode = chatMode, docId = currentDocumentId;
    try {
        const res = await fetch(url, { headers: getHeaders() });
        if (!res.ok) throw new Error('Error');
        const messages = await res.json();
        if (mode !== chatMode || docId !== currentDocumentId && mode === 'doc') return;
        if (messages.length === 0) {
            if (mode === 'kb') {
                chatEmptyState('Pregúntale al archivo',
                    allDocs.length ? 'La pregunta se busca en todos los PDFs, audios y videos de la base; la respuesta cita archivo y página o minuto.'
                                   : 'Sube PDFs, audios o videos (o cópialos en la carpeta base_conocimiento y pulsa "Indexar carpeta").');
            } else {
                chatEmptyState('Archivo listo para análisis', 'Haz tu primera pregunta o usa las sugerencias rápidas de abajo.');
            }
            return;
        }
        document.getElementById('chat-messages').innerHTML = '';
        let lastQ = '';
        for (const msg of messages) {
            if (msg.role === 'user') lastQ = msg.content;
            appendMessage(msg.role, msg.content, msg.sources || [], lastQ);
        }
    } catch (err) {
        console.error('Error loading chat history:', err);
    }
}

// compatibilidad con llamadas anteriores
function enableChat() { refreshChatState(); }
function disableChat() { refreshChatState(); }
async function loadChatHistory() { await loadCurrentChat(); }

function sendSuggestion(text) {
    if (document.getElementById('chat-input').disabled) return;
    document.getElementById('chat-input').value = text;
    sendMessage();
}

function handleChatKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

function autoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
}

async function sendMessage() {
    const input = document.getElementById('chat-input');
    const question = input.value.trim();
    if (!question || isProcessing) return;
    if (chatMode === 'doc' && !currentDocumentId) return;
    const mode = chatMode;

    isProcessing = true;
    input.value = '';
    input.style.height = 'auto';

    const messagesEl = document.getElementById('chat-messages');

    // Remove empty state if present
    const emptyState = document.getElementById('chat-empty');
    if (emptyState) emptyState.remove();

    // Append user message
    appendMessage('user', question, []);

    // Show thinking indicator
    const thinkingEl = document.createElement('div');
    thinkingEl.className = 'msg-thinking';
    thinkingEl.id = 'thinking-indicator';
    thinkingEl.innerHTML = `
        <div class="msg-avatar" style="background:linear-gradient(135deg, var(--emerald-500), var(--indigo-500));">
            <i data-lucide="brain" style="width:16px;height:16px;color:white;"></i>
        </div>
        <div class="thinking-dots"><span></span><span></span><span></span></div>
    `;
    messagesEl.appendChild(thinkingEl);
    lucide.createIcons();
    scrollToBottom();

    // Disable send
    document.getElementById('btn-send').disabled = true;

    try {
        const res = mode === 'kb'
            ? await fetch(`${API_BASE}/api/kb/ask`, {
                method: 'POST', headers: getHeaders(), body: JSON.stringify({ question }),
            })
            : await fetch(`${API_BASE}/api/chat/ask`, {
                method: 'POST', headers: getHeaders(),
                body: JSON.stringify({ document_id: currentDocumentId, question }),
            });

        // Remove thinking indicator
        const thinkingRemove = document.getElementById('thinking-indicator');
        if (thinkingRemove) thinkingRemove.remove();

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Error del servidor');
        }

        const data = await res.json();
        const ctxId = appendMessage('assistant', data.answer, data.sources || [], question);
        // Marcado automático: abre la fuente que la respuesta cita primero y subraya de dónde salió
        if ((data.sources || []).length) openSourceIdx(ctxId, citedSourceIndex(data.answer, data.sources));

    } catch (err) {
        const thinkingRemove = document.getElementById('thinking-indicator');
        if (thinkingRemove) thinkingRemove.remove();

        appendMessage('assistant', `⚠️ Error: ${err.message}`, []);
        showToast(err.message, 'error');
    } finally {
        isProcessing = false;
        document.getElementById('btn-send').disabled = false;
        document.getElementById('chat-input').focus();
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// MESSAGE RENDERING
// ══════════════════════════════════════════════════════════════════════════════
function appendMessage(role, content, sources, question = '') {
    const messagesEl = document.getElementById('chat-messages');
    const ctxId = `m${++msgSeq}`;
    if (role === 'assistant') msgCtx[ctxId] = { sources: sources || [], answer: content, question };

    const msgEl = document.createElement('div');
    msgEl.className = `msg msg-${role}`;

    // Avatar
    let avatarHtml;
    if (role === 'user') {
        const initial = (currentUser?.full_name || currentUser?.username || 'U').charAt(0).toUpperCase();
        const color = currentUser?.avatar_color || '#6366f1';
        avatarHtml = `<div class="msg-avatar" style="background:${color};">${initial}</div>`;
    } else {
        avatarHtml = `<div class="msg-avatar"><i data-lucide="brain"></i></div>`;
    }

    // Content
    let renderedContent;
    if (role === 'assistant') {
        renderedContent = renderMarkdown(content, ctxId);
    } else {
        renderedContent = escapeHtml(content);
    }

    // Sources
    let sourcesHtml = '';
    if (sources && sources.length > 0 && role === 'assistant') {
        sourcesHtml = `<div class="msg-sources">${sources.map((s, i) => sourceTag(s, ctxId, i)).join('')}</div>`;
    }

    msgEl.innerHTML = `
        ${avatarHtml}
        <div class="msg-body">
            <div class="msg-bubble">${renderedContent}</div>
            ${sourcesHtml}
        </div>
    `;

    messagesEl.appendChild(msgEl);
    lucide.createIcons();

    // Render Mermaid diagrams
    renderMermaidDiagrams(msgEl);

    scrollToBottom();
    return ctxId;
}

function sourceTag(src, ctxId, i) {
    if (typeof src !== 'object' || src === null) {   // historial antiguo: solo número de página
        return `<span class="source-tag clickable" onclick="openSourceIdx('${ctxId}', ${i})" title="Ver y marcar esta página"><i data-lucide="bookmark"></i>Página ${src}</span>`;
    }
    const isMedia = src.start_sec !== null && src.start_sec !== undefined;
    const where = isMedia ? `${src.kind === 'frame' ? 'Fotograma' : 'Min.'} ${fmtTime(src.start_sec)}`
        : src.media_type === 'image' ? 'Imagen' : `Pág. ${src.page}`;
    const icon = src.media_type ? (KIND_ICON[src.media_type] || 'bookmark') : (isMedia ? 'play' : 'bookmark');
    const name = src.name ? `${escapeHtml(src.name.length > 22 ? src.name.slice(0, 20) + '…' : src.name)} · ` : '';
    const docId = src.doc_id ? `'${src.doc_id}'` : 'null';
    return `<span class="source-tag clickable" onclick="openSourceIdx('${ctxId}', ${i})" title="${escapeHtml(src.name || '')} · abrir y marcar en el visor"><i data-lucide="${icon}"></i>${name}${where}</span>`;
}

function scrollToBottom() {
    const messagesEl = document.getElementById('chat-messages');
    requestAnimationFrame(() => {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    });
}

// ══════════════════════════════════════════════════════════════════════════════
// MARKDOWN & MERMAID RENDERING
// ══════════════════════════════════════════════════════════════════════════════
function renderMarkdown(text, ctxId = '') {
    // Configure marked
    marked.setOptions({
        breaks: true,
        gfm: true,
    });

    // Process: extract mermaid code blocks before markdown parsing
    let mermaidBlocks = [];
    let processed = text.replace(/```mermaid\n([\s\S]*?)```/g, (match, code) => {
        const idx = mermaidBlocks.length;
        mermaidBlocks.push(code.trim());
        return `<div class="mermaid" data-mermaid-idx="${idx}">${escapeHtml(code.trim())}</div>`;
    });

    // Parse markdown (but skip already-processed mermaid blocks)
    let html = marked.parse(processed);

    // Citas de toda la base: [archivo.ext, página X] / [archivo.ext, minuto MM:SS]
    html = html.replace(/\[([^\[\]<>]+?\.[a-z0-9]{2,4}),\s*(p[áa]gina|p[áa]g\.?|minuto|min\.?)\s*([\d:]+)\]/gi,
        (m, name, unit, val) => {
            const safe = name.replace(/&amp;/g, '&').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
            return `<span class="page-link" onclick="openCitation('${safe}', '${unit}', '${val}', '${ctxId}')" title="Abrir y marcar ${name}">${m}</span>`;
        });
    // Citas de un archivo: [Página X] / [Minuto MM:SS] clicables
    html = html.replace(/\[(P[áa]gina|P[áa]g\.?)\s*(\d+)\]/gi,
        (m, label, num) => `<span class="page-link" onclick="markPage(${num}, '${ctxId}')" title="Ver y marcar la página ${num}">${m}</span>`);
    html = html.replace(/\[(Minuto|Min\.?|Fotograma)\s*(\d{1,2}:\d{2}(?::\d{2})?)\]/gi,
        (m, label, t) => `<span class="page-link" onclick="markTime(${parseTime(t)}, '${ctxId}')" title="Ir al ${t} y marcarlo">${m}</span>`);

    return html;
}

async function renderMermaidDiagrams(containerEl) {
    const mermaidEls = containerEl.querySelectorAll('.mermaid[data-mermaid-idx]');
    for (const el of mermaidEls) {
        try {
            const code = el.textContent;
            const id = `mermaid-${Date.now()}-${Math.random().toString(36).substr(2, 6)}`;
            const { svg } = await mermaid.render(id, code);
            el.innerHTML = svg;
        } catch (err) {
            console.warn('Mermaid render error:', err);
            el.innerHTML = `<pre style="color:var(--red-400);font-size:0.78rem;">Error al renderizar diagrama: ${err.message}</pre>`;
        }
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ══════════════════════════════════════════════════════════════════════════════
// CHAT HISTORY MANAGEMENT
// ══════════════════════════════════════════════════════════════════════════════
async function clearChatHistory() {
    const url = chatMode === 'kb' ? `${API_BASE}/api/kb/history`
        : (currentDocumentId ? `${API_BASE}/api/chat/history/${currentDocumentId}` : null);
    if (!url) return;
    if (!confirm(chatMode === 'kb' ? '¿Borrar tu historial de consultas a toda la base?' : '¿Borrar el historial de chat de este archivo?')) return;
    try {
        const res = await fetch(url, { method: 'DELETE', headers: getHeaders() });
        if (!res.ok) throw new Error('Error');
        showToast('Historial eliminado');
        await loadCurrentChat();
    } catch (err) {
        showToast('Error al limpiar el historial', 'error');
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// MOBILE NAVIGATION
// ══════════════════════════════════════════════════════════════════════════════
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('overlay');
    const chatPanel = document.getElementById('chat-panel');
    chatPanel.classList.remove('open');
    sidebar.classList.toggle('open');
    overlay.classList.toggle('active', sidebar.classList.contains('open'));
}

function toggleChat() {
    const chatPanel = document.getElementById('chat-panel');
    const overlay = document.getElementById('overlay');
    const sidebar = document.getElementById('sidebar');
    sidebar.classList.remove('open');
    chatPanel.classList.toggle('open');
    overlay.classList.toggle('active', chatPanel.classList.contains('open'));
}

function closeMobilePanels() {
    document.getElementById('sidebar').classList.remove('open');
    document.getElementById('chat-panel').classList.remove('open');
    document.getElementById('overlay').classList.remove('active');
}

// ══════════════════════════════════════════════════════════════════════════════
// INITIALIZATION
// ══════════════════════════════════════════════════════════════════════════════
(async function init() {
    if (!requireAuth()) return;
    await refreshCurrentUser();
    initUploadZone();
    await loadDocuments();
    await loadCurrentChat();
    pollSync();
    if (currentUser && currentUser.role === 'admin') {
        if (typeof initAdminPanel === 'function') {
            initAdminPanel();
        }
    }
})();
