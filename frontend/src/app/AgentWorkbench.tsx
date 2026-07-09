import { useCallback, useMemo, useState } from 'react';
import { Settings, Plus, ChevronRight, Share2, BookOpen, GitBranch, MessageSquare, Trash2 } from 'lucide-react';
import { AgentConversationPanel } from '../features/chat/AgentConversationPanel';
import { TracePanel } from '../features/trace/TracePanel';
import { buildConversationHistory, buildLastRunContext, useAgentChat } from '../features/chat/useAgentChat';
import { SystemSettingsPanel } from '../features/settings/SystemSettingsPanel';
import { useLocalSettings } from '../features/settings/useLocalSettings';
import { deleteConversationRequest, requestPromptSuggestion } from '../services/api';
import type { AgentChatRequest, RunRecordSummary } from '../types/api';

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        resolve(reader.result);
        return;
      }
      reject(new Error(`Failed to read ${file.name} as a data URL.`));
    };
    reader.onerror = () => reject(reader.error ?? new Error(`Failed to read ${file.name}.`));
    reader.readAsDataURL(file);
  });
}

function fileIdentity(file: File): string {
  return `${file.name || 'unnamed'}::${file.type || 'application/octet-stream'}::${file.size}::${file.lastModified}`
}

function mergeUniqueFiles(existing: File[], incoming: File[]): File[] {
  const next = [...existing]
  const seen = new Set(existing.map(fileIdentity))
  for (const file of incoming) {
    const identity = fileIdentity(file)
    if (seen.has(identity)) {
      continue
    }
    next.push(file)
    seen.add(identity)
  }
  return next
}

function buildAutoMultimodalPrompt(files: File[]): string {
  const imageCount = files.filter((file) => file.type.startsWith('image/')).length
  if (imageCount > 0) {
    return imageCount === 1
      ? '请识别并讲解这张上传图片的内容，优先提取文字、关键对象，以及与材料或相图相关的信息。'
      : '请逐张识别并讲解这些上传图片的内容，优先提取文字、关键对象，以及与材料或相图相关的信息。'
  }
  return '请结合我上传的附件进行识别和讲解，先概括内容，再提取关键信息。'
}

function truncateLabel(value: string, maxLength = 34): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  if (!normalized) {
    return '';
  }
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}…` : normalized;
}

function getRunLabel(record: RunRecordSummary): string {
  const requestMessage = typeof record.summary?.request_message === 'string' ? record.summary.request_message : '';
  if (requestMessage) {
    return truncateLabel(requestMessage);
  }

  const planning = record.metadata?.planning;
  if (planning && typeof planning === 'object' && typeof (planning as Record<string, unknown>).message === 'string') {
    return truncateLabel((planning as Record<string, string>).message);
  }

  const summary = record.summary || {};
  const request = summary.request;
  if (request && typeof request === 'object') {
    const payload = request as Record<string, unknown>;
    const material = typeof payload.material === 'string' ? payload.material : '';
    const taskType = typeof payload.task_type === 'string' ? payload.task_type : '';
    const temperature = typeof payload.temperature === 'number' ? `${Math.round(payload.temperature)}K` : '';
    const steps = typeof payload.steps === 'number' ? `${payload.steps} steps` : '';
    const label = [material, taskType, temperature, steps].filter(Boolean).join(' · ');
    if (label) {
      return truncateLabel(label);
    }
  }

  const systemName = typeof summary.system_name === 'string' ? summary.system_name : '';
  const diagramType = typeof summary.diagram_type === 'string' ? summary.diagram_type : '';
  const systemLabel = [systemName, diagramType].filter(Boolean).join(' ');
  if (systemLabel) {
    return truncateLabel(systemLabel);
  }

  if (record.final_message) {
    return truncateLabel(record.final_message);
  }

  return record.run_id.slice(0, 8);
}

function formatPromptSuggestionError(message: string, apiBaseUrl: string): string {
  const normalized = message.trim();
  if (/not found/i.test(normalized) || normalized.includes('{"detail":"Not Found"}')) {
    return `当前连接的后端 ${apiBaseUrl} 还没有动态 prompt 推荐接口。请刷新前后端并确认 backend 已更新到最新代码。`;
  }
  if (/failed to fetch/i.test(normalized) || /network/i.test(normalized) || /load failed/i.test(normalized)) {
    return `当前无法连接到后端 ${apiBaseUrl}，所以没法生成上下文推荐 prompt。请确认 backend 正在运行。`;
  }
  return normalized;
}

interface ConversationGroup {
  conversationId: string;
  latestRunId: string;
  latestRecord: RunRecordSummary;
  records: RunRecordSummary[];
  title: string;
}

function buildConversationGroups(runHistory: RunRecordSummary[]): ConversationGroup[] {
  const grouped = new Map<string, RunRecordSummary[]>();
  for (const record of runHistory) {
    const key = record.conversation_id || record.run_id;
    const bucket = grouped.get(key);
    if (bucket) {
      bucket.push(record);
    } else {
      grouped.set(key, [record]);
    }
  }

  return Array.from(grouped.entries())
    .map(([conversationId, records]) => {
      const latestRecord = records[0];
      const titleSource = [...records].reverse().find((record) => getRunLabel(record)) || latestRecord;
      return {
        conversationId,
        latestRunId: latestRecord.run_id,
        latestRecord,
        records,
        title: getRunLabel(titleSource),
      };
    })
    .sort((a, b) => new Date(b.latestRecord.updated_at).getTime() - new Date(a.latestRecord.updated_at).getTime());
}

export function AgentWorkbench() {
  const { settings, apiConnection, updateSettings, refreshApiConnection } = useLocalSettings();
  
  const {
    state,
    liveProgress,
    runHistory,
    sendMessage,
    resumeJob,
    loadRun,
    cancelCurrentRun,
    refreshRunHistory,
    resetConversation,
  } = useAgentChat(settings);

  const [draftMessage, setDraftMessage] = useState('');
  const [uploadedAssets, setUploadedAssets] = useState<File[]>([]);
  const [isSuggestingPrompt, setIsSuggestingPrompt] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const conversationGroups = useMemo(() => buildConversationGroups(runHistory), [runHistory]);

  const handleFilesAdded = useCallback((files: File[]) => {
    setUploadedAssets((current) => mergeUniqueFiles(current, files))
  }, [])

  const handleRemoveAsset = useCallback((assetId: string) => {
    setUploadedAssets((current) => current.filter((file) => fileIdentity(file) !== assetId))
  }, [])

  const handleSend = async (manualPrompt?: string) => {
    const typedText = manualPrompt ?? draftMessage
    const text = typedText.trim() ? typedText : uploadedAssets.length > 0 ? buildAutoMultimodalPrompt(uploadedAssets) : ''
    if (!text.trim() && uploadedAssets.length === 0) return;

    const encodedAssets = await Promise.all(
      uploadedAssets.map(async (file) => ({
        asset_id: fileIdentity(file),
        name: file.name,
        media_type: file.type,
        data_url: await readFileAsDataUrl(file),
        size_bytes: file.size,
      })),
    );

    const request: AgentChatRequest = {
      conversation_id: state.conversationId,
      message: text,
      system_name: '',
      diagram_type: 'binary',
      temperature_min: 0,
      temperature_max: 2000,
      pressure: 101325,
      step_size: 10,
      notes: '',
      uploaded_assets: encodedAssets,
      conversation_history: [],
      last_run_context: {
        run_id: '',
        route_name: '',
        system_name: '',
        final_message: '',
        generated_code_preview: '',
        review_summary: '',
        selected_tool: '',
        generation_source: '',
        request_summary: '',
        review_passed: null,
        review_issues: [],
        review_advisory_issues: [],
        trace_summary: [],
        recognition_summary: ''
      }
    };

    void sendMessage(request);
    
    if (!manualPrompt) {
      setDraftMessage('');
      setUploadedAssets([]);
    }
  };

  const handleAiAnalyze = (prompt: string) => {
    handleSend(prompt);
  };

  const handleRequestPromptSuggestion = async () => {
    if (isSuggestingPrompt || apiConnection.status !== 'ready') {
      return;
    }

    setIsSuggestingPrompt(true);
    try {
      const response = await requestPromptSuggestion(settings, {
        conversation_id: state.conversationId,
        draft_message: draftMessage,
        conversation_history: buildConversationHistory(state.messages),
        last_run_context: buildLastRunContext(state),
        current_context_summary: state.currentContextSummary,
      });
      setDraftMessage(response.suggested_prompt);
    } catch (error) {
      const message = formatPromptSuggestionError(
        error instanceof Error ? error.message : '动态推荐 prompt 失败。',
        settings.apiBaseUrl,
      );
      window.alert(message)
    } finally {
      setIsSuggestingPrompt(false);
    }
  };

  const handleDeleteConversation = async (conversationId: string, title: string) => {
    const confirmed = window.confirm(`确定要删除这段会话吗？\n\n${title}\n\n对应的结果文件、运行记录和会话记忆都会一起删除。`);
    if (!confirmed) {
      return;
    }
    await deleteConversationRequest(settings, conversationId);
    if (state.conversationId === conversationId) {
      resetConversation();
    }
    await refreshRunHistory();
  };

  const AgentMark = () => (
    <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-slate-700 bg-slate-800 shadow-sm">
      <svg viewBox="0 0 44 44" className="h-full w-full">
        <defs>
          <radialGradient id="bgCore" cx="50%" cy="42%" r="70%">
            <stop offset="0%" stopColor="#13253f" />
            <stop offset="100%" stopColor="#0f172a" />
          </radialGradient>
          <linearGradient id="orbitMetal" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#e2e8f0" />
            <stop offset="45%" stopColor="#94a3b8" />
            <stop offset="100%" stopColor="#f8fafc" />
          </linearGradient>
          <radialGradient id="atomBlue" cx="35%" cy="30%" r="70%">
            <stop offset="0%" stopColor="#8be1ff" />
            <stop offset="100%" stopColor="#2563eb" />
          </radialGradient>
          <radialGradient id="atomRed" cx="35%" cy="30%" r="70%">
            <stop offset="0%" stopColor="#ffd4d4" />
            <stop offset="100%" stopColor="#dc2626" />
          </radialGradient>
          <radialGradient id="atomGray" cx="35%" cy="30%" r="70%">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="100%" stopColor="#9ca3af" />
          </radialGradient>
          <linearGradient id="nodeBlue" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#7dd3fc" />
            <stop offset="100%" stopColor="#2563eb" />
          </linearGradient>
          <linearGradient id="nodeSilver" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#f8fafc" />
            <stop offset="100%" stopColor="#94a3b8" />
          </linearGradient>
        </defs>

        <circle cx="22" cy="22" r="18" fill="url(#bgCore)" />

        <ellipse cx="22" cy="25" rx="14.5" ry="6.2" fill="none" stroke="url(#orbitMetal)" strokeWidth="2.1" transform="rotate(-18 22 25)" />
        <ellipse cx="22" cy="22.5" rx="14.2" ry="6" fill="none" stroke="url(#orbitMetal)" strokeWidth="2" transform="rotate(28 22 22.5)" />
        <ellipse cx="22" cy="22" rx="13.2" ry="5.1" fill="none" stroke="#64748b" strokeWidth="1.6" opacity="0.85" />

        <path d="M16.5 15.2 22 11.2 27.5 15.2M16.5 15.2 16.5 21M22 11.2V17M27.5 15.2V21M16.5 21H27.5" stroke="#cbd5e1" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round" fill="none" />
        <rect x="14.7" y="13.4" width="3.6" height="3.6" rx="0.8" fill="url(#nodeBlue)" />
        <rect x="20.2" y="9.4" width="3.6" height="3.6" rx="0.8" fill="url(#nodeSilver)" />
        <rect x="25.7" y="13.4" width="3.6" height="3.6" rx="0.8" fill="url(#nodeBlue)" />

        <circle cx="17.2" cy="24.2" r="5.5" fill="url(#atomBlue)" />
        <circle cx="23.8" cy="26.3" r="6.3" fill="url(#atomRed)" />
        <circle cx="29.8" cy="22.3" r="4.3" fill="url(#atomGray)" />

        <circle cx="15.3" cy="22.2" r="1.5" fill="#dbeafe" />
        <circle cx="21.4" cy="23.3" r="1.6" fill="#fee2e2" />
        <circle cx="28.3" cy="20.7" r="1.2" fill="#ffffff" />

        <circle cx="34.4" cy="17.1" r="1.4" fill="#fb923c" />
        <circle cx="35.4" cy="17.1" r="2.4" fill="none" stroke="#fdba74" strokeWidth="0.9" opacity="0.8" />
      </svg>
    </div>
  );

  return (
    <div className="flex h-screen bg-white font-sans text-slate-800 overflow-hidden">
      
      {/* 侧边栏 */}
      <aside className="w-64 bg-slate-900 flex flex-col shrink-0 z-30 shadow-2xl">
        <div className="p-6 border-b border-slate-800 flex flex-col space-y-5">
          <div className="flex items-center space-x-3">
            <AgentMark />
            <div>
              <h1 className="font-bold text-[10px] tracking-[0.3em] text-white uppercase">Materials Agent</h1>
              <div className="flex items-center space-x-1.5 mt-1">
                <div className={`w-1.5 h-1.5 rounded-full ${apiConnection.status === 'ready' ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'}`}></div>
                <span className="text-[8px] text-slate-500 font-mono">HPC_CLUSTER: {apiConnection.status.toUpperCase()}</span>
              </div>
            </div>
          </div>
          <button
            onClick={resetConversation}
            className="w-full flex items-center justify-between px-3 py-2.5 bg-slate-100 text-slate-900 border border-slate-200 hover:bg-white rounded-xl text-[10px] font-black shadow-sm transition-all group"
          >
            <span className="flex items-center"><Plus className="w-3.5 h-3.5 mr-2" /> 新建研究课题</span>
            <span className="text-[8px] bg-white px-1.5 py-0.5 rounded-md opacity-80 group-hover:opacity-100 text-slate-500 border border-slate-200">⌘N</span>
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-4 py-6">
          <div>
            <h4 className="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-4 px-2">最近研究流</h4>
            <div className="space-y-1">
              {conversationGroups.length === 0 ? (
                <div className="px-2 text-xs text-slate-600">No recent runs</div>
              ) : (
                conversationGroups.slice(0, 15).map((group) => (
                  <div
                    key={group.conversationId}
                    className={`group flex min-w-0 items-center gap-1 rounded-lg ${
                      state.conversationId === group.conversationId ? 'bg-indigo-600 shadow-lg shadow-indigo-600/20' : 'hover:bg-slate-800/40'
                    }`}
                  >
                    <button
                      onClick={() => void loadRun(group.latestRunId)}
                      title={group.title}
                      className={`flex min-w-0 flex-1 items-center p-2.5 rounded-lg text-[11px] cursor-pointer transition-all ${
                        state.conversationId === group.conversationId 
                          ? 'text-white' 
                          : 'text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      <MessageSquare className="w-3.5 h-3.5 mr-3 opacity-60 shrink-0" />
                      <span className="block w-0 min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap font-medium text-left">
                        {group.title}
                      </span>
                      {state.conversationId === group.conversationId && <div className="w-1 h-1 bg-white rounded-full ml-2"></div>}
                    </button>
                    <button
                      onClick={() => void handleDeleteConversation(group.conversationId, group.title)}
                      className={`mr-1 rounded-md p-1.5 transition-all ${
                        state.conversationId === group.conversationId
                          ? 'text-white/80 hover:bg-white/15 hover:text-white'
                          : 'text-slate-500 opacity-0 group-hover:opacity-100 hover:bg-slate-700/70 hover:text-rose-300'
                      }`}
                      title="删除这段会话"
                      aria-label={`删除 ${group.title}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>
        </nav>

        <div className="shrink-0 border-t border-slate-800 bg-slate-950/40 px-4 py-4">
          <div className="bg-black/30 p-4 rounded-xl border border-slate-800/50 space-y-3">
             <div className="flex justify-between items-center text-[9px]">
               <span className="text-slate-500 uppercase font-bold tracking-wider">算力队列负载</span>
               <span className="text-indigo-400 font-mono">Runtime Active</span>
             </div>
             <div className="w-full h-1 bg-slate-800 rounded-full overflow-hidden">
               <div className="h-full bg-gradient-to-r from-indigo-600 to-indigo-400 w-full animate-pulse"></div>
             </div>
          </div>

          <div className="mt-4 border-t border-slate-800 pt-4">
             <button
               onClick={() => setIsSettingsOpen(true)}
               className="w-full flex items-center px-2 py-1.5 text-[10px] text-slate-500 hover:text-slate-300 transition-colors"
             >
               <Settings className="w-3.5 h-3.5 mr-2" /> 系统偏好设置
             </button>
          </div>
        </div>
      </aside>

      {/* 主操作区 (Main Chat Area + Optional Right POP-OUT Panel) */}
      <main className="flex-1 flex flex-col relative bg-[#fcfcfd]">
        
        {/* 顶部导航 */}
        <header className="h-12 border-b border-slate-100 flex items-center justify-between px-8 bg-white/60 backdrop-blur-xl z-20 shadow-sm flex-shrink-0">
          <div className="flex items-center space-x-4">
            <div className="flex items-center text-[11px] font-semibold text-slate-500">
              <span className="hover:text-indigo-600 cursor-pointer">科研工作台</span>
              <ChevronRight className="w-3 h-3 mx-2 opacity-30" />
              <span className="text-slate-800">{state.recognitionResult?.system || '未探索系统'}</span>
            </div>
            <div className="h-4 w-px bg-slate-200"></div>
            {state.isLoading && (
               <span className="px-2 py-0.5 bg-emerald-50 text-emerald-600 text-[8px] rounded-full border border-emerald-100 font-bold uppercase tracking-tighter shadow-sm animate-pulse">Live Computing</span>
            )}
          </div>
          <div className="flex items-center space-x-5">
            <div className="flex space-x-3 border-r border-slate-200 pr-5 text-slate-400">
               <Share2 size={15} className="cursor-pointer hover:text-indigo-600 transition-colors" />
               <BookOpen size={15} className="cursor-pointer hover:text-indigo-600 transition-colors" />
               <GitBranch size={15} className="cursor-pointer hover:text-indigo-600 transition-colors" />
            </div>
            <div className="flex items-center space-x-2.5">
               <div className="text-right">
                  <p className="text-[10px] font-bold leading-none text-slate-700">Prof. User</p>
                  <p className="text-[8px] text-slate-400 uppercase tracking-tighter mt-1">Lead Researcher</p>
               </div>
               <div className="h-8 w-8 rounded-lg bg-indigo-700 text-white flex items-center justify-center font-bold text-xs shadow-lg shadow-indigo-100">
                 USR
               </div>
            </div>
          </div>
        </header>

        <div className="px-8 py-2 border-b border-slate-100 bg-white/80 backdrop-blur-xl">
          <div className="flex items-center gap-2 text-[10px]">
            <span className={`status-chip ${apiConnection.status === 'ready' ? 'status-chip-success' : apiConnection.status === 'offline' ? 'status-chip-danger' : 'status-chip-active'}`}>
              {apiConnection.status}
            </span>
            <span className="status-chip status-chip-muted">{state.route?.name || 'conversation.awaiting'}</span>
            <span className="status-chip status-chip-muted">{state.status}</span>
          </div>
        </div>

        <div className="flex-1 flex overflow-hidden">
          <div className="sidebar-section sr-only" aria-hidden="true">
            {conversationGroups.length === 0 ? null : conversationGroups.slice(0, 15).map((group) => (
              <span key={group.conversationId} className="sidebar-thread">
                {group.title}
              </span>
            ))}
          </div>
          <AgentConversationPanel
            settings={settings}
            messages={state.messages}
            liveStatusMessage={state.statusMessage}
            showLiveStatus={state.isLoading}
            liveProgress={liveProgress}
            draftMessage={draftMessage}
            uploadedAssets={uploadedAssets.map((file) => ({ asset_id: fileIdentity(file), name: file.name, media_type: file.type, data_url: '', size_bytes: file.size }))}
            disabled={state.isLoading}
            connectionMessage={apiConnection.message}
            connectionStatus={apiConnection.status}
            onDraftMessageChange={setDraftMessage}
            onFilesAdded={handleFilesAdded}
            onRemoveAsset={handleRemoveAsset}
            onSend={handleSend}
            onAiAnalyze={handleAiAnalyze}
            onResumeJob={resumeJob}
            onRequestPromptSuggestion={handleRequestPromptSuggestion}
            isSuggestingPrompt={isSuggestingPrompt}
          />
        </div>

        {/* 底层 Trace 面板 (折叠状态) */}
        {state.timeline.length > 0 && (
          <div className="inspector-panel-collapsible border-t border-slate-200 bg-slate-50 text-xs">
            <details>
              <summary className="p-3 font-semibold text-slate-600 cursor-pointer outline-none select-none hover:bg-slate-100">Execution Trace & Timeline</summary>
              <div className="p-4 h-64 overflow-y-auto">
                 <TracePanel
                    runId={state.runId}
                    route={state.route}
                    planSteps={state.planSteps}
                    timeline={state.timeline}
                    status={state.statusMessage}
                    runStatus={state.runStatus}
                    terminationReason={state.terminationReason}
                    isLoading={state.isLoading}
                    responseMetadata={state.responseMetadata}
                    summary={state.summary}
                    recognitionResult={state.recognitionResult}
                    canCancel={state.isLoading}
                    onCancel={() => void cancelCurrentRun()}
                 />
              </div>
            </details>
          </div>
        )}
      </main>

      <SystemSettingsPanel
        open={isSettingsOpen}
        settings={settings}
        connectionStatus={apiConnection.status}
        onClose={() => setIsSettingsOpen(false)}
        onUpdateClientSettings={updateSettings}
        onRefreshConnection={refreshApiConnection}
      />
    </div>
  );
}
