// Lightweight component state management
const state = {
  currentRunId: null,
  pollInterval: null,
  hasChatStarted: false,
  chatMode: 'md', // 'md' | 'conversation'
  currentSessionId: null,
  serverRuns: []
};

const STORAGE_KEY_SESSIONS = 'md_agent_sessions';
const STORAGE_KEY_PREFIX = 'md_agent_chat_';

const els = {
  layout: document.querySelector('.layout'),
  sidebar: document.querySelector('.sidebar'),
  menuToggle: document.getElementById('menu-toggle'),
  chatContainer: document.getElementById('chat-container'),
  emptyState: document.getElementById('empty-state'),
  chatFlow: document.getElementById('chat-flow'),
  userInput: document.getElementById('user-input'),
  sendBtn: document.getElementById('send-btn'),
  newChatBtn: document.getElementById('new-chat-btn'),
  historyContainer: document.getElementById('history-container'),
  appStatus: document.getElementById('app-status'),
  suggestionCards: document.querySelectorAll('.suggestion-card'),
  inputBox: document.querySelector('.input-box'),
  modeMd: document.getElementById('mode-md'),
  modeConv: document.getElementById('mode-conv'),
  greetingTitle: document.querySelector('.greeting-title'),
  greetingSubtitle: document.querySelector('.greeting-subtitle'),
  suggestionGrid: document.querySelector('.suggestion-grid'),
  settingsBtn: document.getElementById('settings-btn'),
  settingsModal: document.getElementById('settings-modal'),
  closeSettingsBtn: document.getElementById('close-settings-btn'),
  cancelSettingsBtn: document.getElementById('cancel-settings-btn'),
  saveSettingsBtn: document.getElementById('save-settings-btn'),
  inpProvider: document.getElementById('llm-provider'),
  inpBaseUrl: document.getElementById('llm-base-url'),
  inpModel: document.getElementById('llm-model'),
  inpApiKey: document.getElementById('llm-api-key'),
  inpLammpsCmd: document.getElementById('lammps-cmd'),
  inpPotentialsDir: document.getElementById('potentials-dir')
};

// Network util
async function api(endpoint, method = 'GET', body = null) {
  const options = { method, headers: {} };
  if (body) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  try {
    const response = await fetch(endpoint, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `API error: ${response.status}`);
    els.appStatus.classList.add('hidden'); // hidden means connected
    return payload;
  } catch (err) {
    els.appStatus.classList.remove('hidden');
    els.appStatus.textContent = '连接失败';
    throw err;
  }
}

function refreshSendButton() {
  const hasPrompt = els.userInput.value.trim() !== '';
  els.sendBtn.disabled = !hasPrompt;
}

// UI Initialization
function init() {
  renderSessionList();
  refreshSendButton();
  
  els.sendBtn.addEventListener('click', () => handleChatSubmit(els.userInput.value));
  els.newChatBtn.addEventListener('click', resetView);

  // Mode toggle
  els.modeMd.addEventListener('click', () => switchMode('md'));
  els.modeConv.addEventListener('click', () => switchMode('conversation'));

  // Sidebar toggle
  els.menuToggle.addEventListener('click', toggleSidebar);
  // Close sidebar on mobile when clicking main content area
  document.querySelector('.main-content').addEventListener('click', () => {
    if (window.innerWidth <= 768 && els.layout.classList.contains('sidebar-open')) {
      els.layout.classList.remove('sidebar-open');
    }
  });

  els.settingsBtn.addEventListener('click', openSettings);
  els.closeSettingsBtn.addEventListener('click', closeSettings);
  els.cancelSettingsBtn.addEventListener('click', closeSettings);
  els.saveSettingsBtn.addEventListener('click', saveSettings);
  
  els.userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleChatSubmit(els.userInput.value);
    }
  });

  els.userInput.addEventListener('input', () => {
    // Auto-resize
    els.userInput.style.height = 'auto';
    els.userInput.style.height = (els.userInput.scrollHeight) + 'px';
    refreshSendButton();
  });

  els.userInput.addEventListener('focus', () => els.inputBox.classList.add('focused'));
  els.userInput.addEventListener('blur', () => els.inputBox.classList.remove('focused'));

  els.suggestionCards.forEach(card => {
    card.addEventListener('click', () => {
       const query = card.getAttribute('data-query');
      handleChatSubmit(query);
    });
  });
}

function toggleSidebar() {
  if (window.innerWidth <= 768) {
    // Mobile: overlay toggle
    els.layout.classList.toggle('sidebar-open');
  } else {
    // Desktop: collapse toggle
    els.sidebar.classList.toggle('collapsed');
  }
}

function switchMode(mode) {
  state.chatMode = mode;
  if (mode === 'md') {
    els.modeMd.classList.add('active');
    els.modeConv.classList.remove('active');
    els.greetingTitle.textContent = '你好，我是 MD Agent';
    els.greetingSubtitle.textContent = '有什么可以帮您模拟的吗？';
    els.suggestionGrid.classList.remove('hidden');
    els.userInput.placeholder = 'Ask Poros (MD Agent)...';
  } else {
    els.modeConv.classList.add('active');
    els.modeMd.classList.remove('active');
    els.greetingTitle.textContent = '你好，我是 MD Agent';
    els.greetingSubtitle.textContent = '自由对话模式，可以提问任何 MD 相关问题';
    els.suggestionGrid.classList.add('hidden');
    els.userInput.placeholder = '输入你的问题...';
  }
}

function scrollToBottom() {
  els.chatContainer.scrollTop = els.chatContainer.scrollHeight;
}

function transitionToChatMode() {
  if (!state.hasChatStarted) {
    els.emptyState.classList.add('hidden');
    els.chatFlow.classList.remove('hidden');
    state.hasChatStarted = true;
  }
}

// ── Session Management (localStorage) ──

function getSessionIndex() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY_SESSIONS) || '[]');
  } catch { return []; }
}

async function fetchServerRuns() {
  try {
    const data = await api('/api/runs');
    state.serverRuns = Array.isArray(data.runs) ? data.runs : [];
  } catch {
    state.serverRuns = [];
  }
}

function saveSessionIndex(index) {
  localStorage.setItem(STORAGE_KEY_SESSIONS, JSON.stringify(index));
}

function createSession() {
  const id = 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
  const session = { id, title: '新对话', createdAt: Date.now(), mode: state.chatMode };
  const index = getSessionIndex();
  index.unshift(session);
  saveSessionIndex(index);
  localStorage.setItem(STORAGE_KEY_PREFIX + id, JSON.stringify([]));
  return id;
}

function ensureSession() {
  if (!state.currentSessionId) {
    state.currentSessionId = createSession();
    renderSessionList();
  }
  return state.currentSessionId;
}

function saveMessageToSession(role, content) {
  const sid = ensureSession();
  const messages = JSON.parse(localStorage.getItem(STORAGE_KEY_PREFIX + sid) || '[]');
  messages.push({ role, content, ts: Date.now() });
  localStorage.setItem(STORAGE_KEY_PREFIX + sid, JSON.stringify(messages));

  // Update title with first user message
  if (role === 'user') {
    const index = getSessionIndex();
    const sess = index.find(s => s.id === sid);
    if (sess && sess.title === '新对话') {
      sess.title = content.length > 30 ? content.slice(0, 30) + '…' : content;
      saveSessionIndex(index);
      renderSessionList();
    }
  }
}

function deleteSession(sessionId) {
  let index = getSessionIndex();
  index = index.filter(s => s.id !== sessionId);
  saveSessionIndex(index);
  localStorage.removeItem(STORAGE_KEY_PREFIX + sessionId);
  if (state.currentSessionId === sessionId) {
    resetView();
  } else {
    renderSessionList();
  }
}

function buildHistoryEntries(localSessions, serverRuns) {
  const localEntries = localSessions.map(sess => ({
    kind: 'local',
    id: sess.id,
    title: sess.title || '新对话',
    timestamp: Number(sess.createdAt || Date.now()),
    active: sess.id === state.currentSessionId,
    statusText: sess.mode === 'conversation' ? 'Conversation' : 'Chat',
  }));

  const runEntries = serverRuns.map(run => {
    const query = (run.original_query || '').trim();
    return {
      kind: 'run',
      id: run.run_id,
      title: query || run.run_id,
      timestamp: Math.round(Number(run.updated_at || 0) * 1000),
      active: run.run_id === state.currentRunId,
      statusText: `${run.status || 'unknown'} | ${run.mode || 'real'}`,
    };
  });

  return [...runEntries, ...localEntries].sort((a, b) => b.timestamp - a.timestamp);
}

async function renderSessionList() {
  const index = getSessionIndex();
  await fetchServerRuns();
  const entries = buildHistoryEntries(index, state.serverRuns);
  els.historyContainer.innerHTML = '';
  if (entries.length === 0) {
    els.historyContainer.innerHTML = '<div style="padding:12px;color:var(--text-secondary);font-size:13px;">暂无会话记录</div>';
    return;
  }
  entries.forEach(entry => {
    const div = document.createElement('div');
    div.className = `history-item ${entry.active ? 'active' : ''}`;
    div.title = `${new Date(entry.timestamp || Date.now()).toLocaleString()} | ${entry.statusText}`;

    const titleSpan = document.createElement('span');
    titleSpan.className = 'history-title';
    titleSpan.textContent = entry.kind === 'run' ? `[Run] ${entry.title}` : `[Chat] ${entry.title}`;
    div.appendChild(titleSpan);

    if (entry.kind === 'local') {
      const delBtn = document.createElement('button');
      delBtn.className = 'history-del-btn';
      delBtn.innerHTML = '&times;';
      delBtn.title = '删除';
      delBtn.addEventListener('click', (e) => { e.stopPropagation(); deleteSession(entry.id); });
      div.appendChild(delBtn);
      div.addEventListener('click', () => loadSession(entry.id));
    } else {
      div.addEventListener('click', () => loadRun(entry.id));
    }

    els.historyContainer.appendChild(div);
  });
}

function loadSession(sessionId) {
  if (state.pollInterval) clearInterval(state.pollInterval);
  state.currentSessionId = sessionId;
  state.currentRunId = null;
  lastParsedState = null;
  state.hasChatStarted = false;

  els.chatFlow.innerHTML = '';
  els.emptyState.classList.add('hidden');
  els.chatFlow.classList.remove('hidden');
  state.hasChatStarted = true;

  const messages = JSON.parse(localStorage.getItem(STORAGE_KEY_PREFIX + sessionId) || '[]');
  messages.forEach(msg => {
    if (msg.role === 'user') {
      appendUserMessage(msg.content, true);
    } else {
      appendAgentMessage(msg.content, true);
    }
  });
  scrollToBottom();
  refreshSendButton();
  renderSessionList();
}

// Message Rendering Helpers
function appendUserMessage(text, skipSave) {
  transitionToChatMode();
  const messageText = String(text || '');
  const div = document.createElement('div');
  div.className = 'message user';
  div.innerHTML = `
    <div class="msg-avatar user-avatar">U</div>
    <div class="msg-content">
      <div class="msg-bubble"><div class="msg-bubble-text">${escapeHTML(messageText)}</div></div>
    </div>
  `;
  els.chatFlow.appendChild(div);
  scrollToBottom();
  if (!skipSave) saveMessageToSession('user', messageText);
}

function appendAgentMessage(htmlContent, skipSave) {
  transitionToChatMode();
  const div = document.createElement('div');
  div.className = 'message agent';
  div.innerHTML = `
    <div class="msg-avatar agent-avatar">MD</div>
    <div class="msg-content">
      <div class="msg-bubble">${htmlContent}</div>
    </div>
  `;
  els.chatFlow.appendChild(div);
  scrollToBottom();
  if (!skipSave) saveMessageToSession('assistant', htmlContent);
  return div.querySelector('.msg-content');
}

function escapeHTML(str) {
  return String(str || '').replace(/[&<>'"]/g, tag => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[tag] || tag));
}

// Chat Protocol
let lastParsedState = null;

async function handleChatSubmit(query) {
  query = query.trim();
  if (!query) return;
  els.userInput.value = '';
  els.userInput.style.height = 'auto';
  refreshSendButton();
  
  appendUserMessage(query, false);
  const loadingHtml = `<span style="color:var(--text-secondary);">解析中...</span>`;
  const agentContainer = appendAgentMessage(loadingHtml, true);
  
  try {
    const chatPayload = { message: query };
    // In MD mode, carry forward parsed parameters; in conversation mode, skip them
    if (state.chatMode === 'md') {
      chatPayload.normalized_request = lastParsedState?.normalized_request || {};
    }
    const data = await api('/api/chat', 'POST', chatPayload);
    
    // Construct response
    let rawReply = (data.reply || '分析完成。').replace(/\n/g, '<br/>');
    let toolHtml = '';
    
    // Only show parameter parsing tool-box in MD mode AND when at least one param has a real value
    const nr = data.state?.normalized_request || {};
    const isInvalidParam = v => v === null || v === '' || v === undefined || String(v).toLowerCase() === 'none' || String(v).toLowerCase() === 'null';
    const hasRealParams = Object.values(nr).some(v => !isInvalidParam(v));
    if (state.chatMode === 'md' && hasRealParams) {
       lastParsedState = data.state;
       const req = data.state.normalized_request;
       const paramsLis = Object.entries(req)
           .filter(([_, v]) => !isInvalidParam(v))
           .map(([k, v]) => `<li><span class="key">${k}</span><span class="val">${v}</span></li>`).join('');
           
       const errors = data.state.validation?.errors || [];
       let errHtml = '';
       if (errors.length > 0) {
          errHtml = `<ul style="color:#ef4444; margin-top:12px; font-size:13px; padding-left:16px;">${errors.map(e => `<li>${e}</li>`).join('')}</ul>`;
       }
       
       let actionBtnHtml = '';
       if (data.can_run) {
          // Add a unique ID for this button scope
          const btnId = 'btn-run-' + Date.now();
          actionBtnHtml = `<div class="action-row"><button id="${btnId}" class="btn-primary">启动模拟</button></div>`;
          
          // We need to attach event listener after injecting HTML
          setTimeout(() => {
             const btn = document.getElementById(btnId);
             if (btn) btn.addEventListener('click', () => handleRunSubmit(query, agentContainer, btn));
          }, 0);
       }
       
       toolHtml = `
         <div class="tool-box">
           <h4>Parsed Parameters <span class="status">${data.can_run ? 'Ready' : 'Needs clarification'}</span></h4>
           <ul class="param-list">${paramsLis}</ul>
           ${errHtml}
           ${actionBtnHtml}
         </div>
       `;
    }
    
    const finalHtml = `<div class="markdown-body">${rawReply}</div>${toolHtml}`;
    agentContainer.querySelector('.msg-bubble').innerHTML = finalHtml;
    // Save the final agent reply to session
    saveMessageToSession('assistant', finalHtml);
    scrollToBottom();
    
  } catch (err) {
    agentContainer.querySelector('.msg-bubble').innerHTML = `<span style="color:#ef4444;">系统异常: ${escapeHTML(err.message || '请求失败')}</span>`;
  }
}

// Run Protocol
async function handleRunSubmit(originalQuery, agentContainer, startBtn) {
  if (!lastParsedState) return;
  startBtn.disabled = true;
  startBtn.textContent = '正在提交...';
  
  try {
    const data = await api('/api/run', 'POST', {
      user_query: originalQuery,
      normalized_request: lastParsedState.normalized_request
    });
    if (data.run_id) {
       startBtn.textContent = '已启动';
       // Append a new Agent Bubble for Progress
       const progContainer = appendAgentMessage(`
         <div class="inline-progress" id="prog-${data.run_id}">
            <div class="inline-progress-header">
               <div style="display:flex; align-items:center; gap:8px;">
                   <span>Simulation Status</span>
                   <span class="status-badge" style="display:inline;">Queued</span>
               </div>
               <button class="btn-cancel" onclick="cancelRun('${data.run_id}')" id="cancel-${data.run_id}">停止</button>
            </div>
            <div class="inline-progress-bar"><div class="inline-progress-fill" style="width:0%"></div></div>
            <div class="inline-progress-msg">Waiting in queue...</div>
         </div>
         <div class="run-results-container" id="res-${data.run_id}"></div>
       `, true);
       startPolling(data.run_id, progContainer);
    }
  } catch (err) {
    console.error(err);
    startBtn.disabled = false;
    startBtn.textContent = '重启模拟';
  }
}

function startPolling(runId, domContext) {
  state.currentRunId = runId;
  if (state.pollInterval) clearInterval(state.pollInterval);
  state.pollInterval = setInterval(() => pollStatus(runId, domContext), 2000);
  pollStatus(runId, domContext); 
}

window.cancelRun = async function(runId) {
  try {
     await api(`/api/run/${runId}/cancel`, 'POST');
     const btn = document.getElementById(`cancel-${runId}`);
     if (btn) btn.disabled = true;
  } catch (err) {
     console.error('Failed to cancel run', err);
  }
}

async function pollStatus(runId, domContext) {
  try {
    const data = await api(`/api/run/${runId}`);
    
    const progBox = domContext.querySelector(`#prog-${runId}`);
    if (progBox) {
        const badge = progBox.querySelector('.status-badge');
        badge.textContent = data.status;
        const prog = data.summary?.progress;
        if (prog) {
           progBox.querySelector('.inline-progress-fill').style.width = Math.min(100, Math.max(0, prog.percent || 0)) + '%';
           progBox.querySelector('.inline-progress-msg').textContent = prog.message || `${prog.stage || 'running'}...`;
        }
    }
    
    if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
      clearInterval(state.pollInterval);
      state.pollInterval = null;
      renderSessionList();
      
      const btn = domContext.querySelector(`#cancel-${runId}`);
      if (btn) btn.remove(); // Remove stop button

      if (data.status === 'completed') {
        renderRunResults(data, domContext.querySelector(`#res-${runId}`));
      } else {
        if (progBox) progBox.style.borderColor = data.status === 'cancelled' ? '#fde68a' : '#fecaca';
      }
    }
  } catch (err) {
    console.error('Polling error', err);
  }
}

function renderRunResults(data, resContainer) {
  let html = '';
  
  // Metrics
  const metrics = data.summary?.metrics;
  if (metrics && Object.keys(metrics).length > 0) {
    html += `<div class="metrics-grid">`;
    for (const [k, v] of Object.entries(metrics)) {
       const displayVal = typeof v === 'number' ? parseFloat(v.toFixed(3)) : v;
       html += `<div class="metric-card"><span class="label">${k.replace(/_/g, ' ')}</span><span class="value">${displayVal}</span></div>`;
    }
    html += `</div>`;
  }
  
  const artifacts = data.artifacts || {};
  
  // Thermodynamic plot
  if (artifacts['plot.png']) {
    html += `<div class="plot-card"><h4>Thermodynamics</h4><img src="${artifacts['plot.png']}?t=${Date.now()}" /></div>`;
  }
  
  // Trajectory media
  if (artifacts['ovito.mp4']) {
     html += `<div class="media-box"><h4>Trajectory Video</h4><video src="${artifacts['ovito.mp4']}" controls loop></video></div>`;
  } else if (artifacts['diffusion_trajectory_3d.gif']) {
     html += `<div class="media-box"><h4>Trajectory GIF</h4><img src="${artifacts['diffusion_trajectory_3d.gif']}" /></div>`;
  }
  
  // Downloads
  const downloadables = ['thermo.csv', 'diffusion_trajectory.png'];
  const jsonFiles = Object.keys(artifacts).filter(k => k.endsWith('.json') && k !== 'summary.json' && k !== 'request.json');
  downloadables.push(...jsonFiles);
  const uploadedInputs = Object.keys(artifacts).filter(k => k.startsWith('uploaded_'));
  downloadables.push(...uploadedInputs);
  
  const availableDownloads = Object.entries(artifacts).filter(([k,v]) => downloadables.includes(k));
  if (availableDownloads.length > 0) {
      html += `<div class="downloads-row">`;
      for (const [k, url] of availableDownloads) {
          html += `<a href="${url}" target="_blank" download class="download-btn"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>${k}</a>`;
      }
      html += `</div>`;
  }
  
  // Report (Markdown)
  if (artifacts['report.md']) {
      html += `<div class="markdown-body" id="report-${data.run_id}" style="margin-top:24px;">加载报告中...</div>`;
      resContainer.innerHTML = html;
      fetch(artifacts['report.md'])
        .then(r => r.text())
        .then(text => {
            const rDiv = resContainer.querySelector(`#report-${data.run_id}`);
            if (window.marked) rDiv.innerHTML = marked.parse(text);
            else rDiv.innerHTML = `<pre>${text}</pre>`;
            scrollToBottom();
        });
  } else {
      resContainer.innerHTML = html;
      scrollToBottom();
  }
}

// History loading into chat view (for server-side simulation runs)
async function loadRun(runId) {
  if (state.pollInterval) clearInterval(state.pollInterval);
  state.currentRunId = runId;
  state.currentSessionId = null;
  
  els.emptyState.classList.add('hidden');
  els.chatFlow.classList.remove('hidden');
  els.chatFlow.innerHTML = ''; // clear chat
  state.hasChatStarted = true;
  
  try {
    const data = await api(`/api/run/${runId}`);
    
    // Fake the user query
    if (data.summary && data.summary.request && data.summary.request.original_query) {
        appendUserMessage(data.summary.request.original_query, true);
    } else {
        appendUserMessage("History Check: " + runId, true);
    }
    
    // Show run state
    const progBoxHtml = `
       <div class="inline-progress" id="prog-${runId}">
          <div class="inline-progress-header">
             <div style="display:flex; align-items:center; gap:8px;">
                 <span>Simulation Status</span>
                 <span class="status-badge">${data.status}</span>
             </div>
             ${(data.status === 'queued' || data.status === 'running') ? `<button class="btn-cancel" onclick="cancelRun('${runId}')" id="cancel-${runId}">停止</button>` : ''}
          </div>
          <div class="inline-progress-bar"><div class="inline-progress-fill" style="width:100%"></div></div>
          <div class="inline-progress-msg">Resumed session</div>
       </div>
       <div class="run-results-container" id="res-${runId}"></div>
    `;
    const agentContainer = appendAgentMessage(`<strong>Loaded Run</strong><br/>${progBoxHtml}`, true);
    
    if (data.status === 'queued' || data.status === 'running') {
      startPolling(runId, agentContainer);
    } else if (data.status === 'completed') {
      renderRunResults(data, agentContainer.querySelector(`#res-${runId}`));
    }
    
    renderSessionList(); // update highlights
  } catch (err) {
    console.error('Failed to load run', err);
  }
}

function resetView() {
  if (state.pollInterval) clearInterval(state.pollInterval);
  state.currentRunId = null;
  state.currentSessionId = null;
  lastParsedState = null;
  state.hasChatStarted = false;
  
  els.userInput.value = '';
  els.userInput.style.height = 'auto';
  refreshSendButton();
  
  els.emptyState.classList.remove('hidden');
  els.chatFlow.classList.add('hidden');
  els.chatFlow.innerHTML = '';
  
  // Restore mode toggle to reflect current state
  switchMode(state.chatMode);
  
  renderSessionList();
}

async function openSettings() {
  els.settingsModal.classList.remove('hidden');
  try {
    const [llm, lamp] = await Promise.all([api('/api/config/llm'), api('/api/config/lammps')]);
    els.inpProvider.value = llm.provider || '';
    els.inpBaseUrl.value = llm.base_url || '';
    els.inpModel.value = llm.model || '';
    els.inpApiKey.value = '';
    els.inpApiKey.placeholder = llm.api_key_set ? "Set (Enter new to update)" : "Not set";
    els.inpLammpsCmd.value = lamp.lammps_command || '';
    els.inpPotentialsDir.value = lamp.potentials_dir || '';
  } catch(e) { console.error('Failed fetching configs', e); }
}

function closeSettings() {
  els.settingsModal.classList.add('hidden');
}

async function saveSettings() {
  els.saveSettingsBtn.disabled = true;
  els.saveSettingsBtn.textContent = 'Saving...';
  try {
    const llmPayload = {
      provider: els.inpProvider.value.trim(),
      base_url: els.inpBaseUrl.value.trim(),
      model: els.inpModel.value.trim()
    };
    if (els.inpApiKey.value.trim() !== '') {
       llmPayload.api_key = els.inpApiKey.value.trim();
    }
    await api('/api/config/llm', 'POST', llmPayload);
    
    await api('/api/config/lammps', 'POST', {
       lammps_command: els.inpLammpsCmd.value.trim(),
       potentials_dir: els.inpPotentialsDir.value.trim()
    });
    closeSettings();
  } catch(e) {
    console.error(e);
    alert('Failed to save settings.');
  } finally {
    els.saveSettingsBtn.disabled = false;
    els.saveSettingsBtn.textContent = 'Save';
  }
}

document.addEventListener('DOMContentLoaded', init);
