import { Fragment, useEffect, useRef, type ChangeEvent, type KeyboardEvent, type ReactNode } from 'react';
import { Send, Sparkles, Database, FlaskConical, Activity, ClipboardList, Download, Terminal, ShieldCheck } from 'lucide-react';
import { ArtifactResultPanel } from '../result/ArtifactResultPanel';
import type { ClientSettings, RecognitionResult, UploadedAsset } from '../../types/api';
import type { ConversationMessage, LiveProgressSnapshot } from './useAgentChat';

interface AgentConversationPanelProps {
  settings: ClientSettings;
  messages: ConversationMessage[];
  liveStatusMessage: string;
  showLiveStatus: boolean;
  liveProgress: LiveProgressSnapshot | null;
  draftMessage: string;
  uploadedAssets: UploadedAsset[];
  disabled: boolean;
  connectionMessage: string;
  connectionStatus: 'resolving' | 'ready' | 'offline';
  onDraftMessageChange: (value: string) => void;
  onFilesAdded: (files: File[]) => void;
  onRemoveAsset: (assetId: string) => void;
  onSend: (manualPrompt?: string) => void;
  onAiAnalyze: (prompt: string) => void;
  onRequestPromptSuggestion: () => void | Promise<void>;
  isSuggestingPrompt: boolean;
}

function renderInlineMarkdown(text: string, strongClassName = 'font-semibold text-slate-900'): ReactNode[] {
  const normalized = text.replace(/\\\*/g, '*');
  const parts = normalized.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return (
        <strong key={`strong-${index}`} className={strongClassName}>
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <Fragment key={`text-${index}`}>{part}</Fragment>;
  });
}

function renderMessageContent(
  content: string,
  options?: { textClassName?: string; strongClassName?: string; listItemClassName?: string },
): ReactNode {
  const textClassName = options?.textClassName ?? 'text-slate-700';
  const strongClassName = options?.strongClassName ?? 'font-semibold text-slate-900';
  const listItemClassName = options?.listItemClassName ?? textClassName;
  const lines = content.split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const rawLine = lines[index];
    const line = rawLine.trim();

    if (!line) {
      index += 1;
      continue;
    }

    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const title = headingMatch[2];
      const headingClass =
        level === 1
          ? 'text-xl font-bold text-slate-900'
          : level === 2
            ? 'text-lg font-bold text-slate-900'
            : 'text-base font-semibold text-indigo-700';
      blocks.push(
        <h3 key={`heading-${index}`} className={`${headingClass} mt-4 first:mt-0`}>
          {renderInlineMarkdown(title, strongClassName)}
        </h3>,
      );
      index += 1;
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      let listIndex = index;
      while (listIndex < lines.length && /^\d+\.\s+/.test(lines[listIndex].trim())) {
        items.push(lines[listIndex].trim().replace(/^\d+\.\s+/, ''));
        listIndex += 1;
      }
      blocks.push(
        <ol key={`ordered-${index}`} className="ml-5 list-decimal space-y-2">
          {items.map((item, itemIndex) => (
            <li key={`ordered-item-${index}-${itemIndex}`} className={`pl-1 ${listItemClassName}`}>
              {renderInlineMarkdown(item, strongClassName)}
            </li>
          ))}
        </ol>,
      );
      index = listIndex;
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      let listIndex = index;
      while (listIndex < lines.length && /^[-*]\s+/.test(lines[listIndex].trim())) {
        items.push(lines[listIndex].trim().replace(/^[-*]\s+/, ''));
        listIndex += 1;
      }
      blocks.push(
        <ul key={`unordered-${index}`} className="ml-5 list-disc space-y-2">
          {items.map((item, itemIndex) => (
            <li key={`unordered-item-${index}-${itemIndex}`} className={`pl-1 ${listItemClassName}`}>
              {renderInlineMarkdown(item, strongClassName)}
            </li>
          ))}
        </ul>,
      );
      index = listIndex;
      continue;
    }

    const paragraphLines: string[] = [];
    let paragraphIndex = index;
    while (paragraphIndex < lines.length) {
      const candidate = lines[paragraphIndex].trim();
      if (!candidate || /^(#{1,3})\s+/.test(candidate) || /^\d+\.\s+/.test(candidate) || /^[-*]\s+/.test(candidate)) {
        break;
      }
      paragraphLines.push(candidate);
      paragraphIndex += 1;
    }

    blocks.push(
      <p key={`paragraph-${index}`} className={`leading-8 ${textClassName}`}>
        {renderInlineMarkdown(paragraphLines.join(' '), strongClassName)}
      </p>,
    );
    index = paragraphIndex;
  }

  return <div className="space-y-3">{blocks}</div>;
}

export function AgentConversationPanel({
  settings,
  messages,
  liveStatusMessage,
  showLiveStatus,
  liveProgress,
  draftMessage,
  uploadedAssets,
  disabled,
  connectionMessage,
  connectionStatus,
  onDraftMessageChange,
  onFilesAdded,
  onRemoveAsset,
  onSend,
  onAiAnalyze,
  onRequestPromptSuggestion,
  isSuggestingPrompt,
}: AgentConversationPanelProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const scrollAnchorRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = scrollContainerRef.current;
    const anchor = scrollAnchorRef.current;
    if (!container || !anchor) {
      return;
    }
    const isNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 180;
    if (!isNearBottom && messages.length > 1 && !showLiveStatus) {
      return;
    }
    anchor.scrollIntoView({ behavior: messages.length > 0 ? 'smooth' : 'auto', block: 'end' });
  }, [messages, showLiveStatus, liveStatusMessage, liveProgress]);

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files ? Array.from(event.target.files) : [];
    if (files.length) {
      onFilesAdded(files);
    }
    event.target.value = '';
  };

  return (
    <div className="flex-1 flex flex-col relative bg-[#fcfcfd] overflow-hidden">
      
      {/* 状态横幅，如果是离线 */}
      {connectionStatus === 'offline' && (
        <div className="bg-rose-50 border-b border-rose-100 px-4 py-2 text-rose-600 text-xs text-center">
          {connectionMessage}
        </div>
      )}

      {/* 聊天流内容区 */}
      <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-8 py-8 space-y-8 scroll-smooth" data-testid="conversation-scroll">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center space-y-4 opacity-50">
            <FlaskConical className="w-16 h-16 text-slate-300" />
            <p className="text-slate-400 font-mono text-xs tracking-widest uppercase">System Initialized</p>
          </div>
        ) : (
          messages.map((msg) => {
            const isUser = msg.role === 'user';
            const isWarning = msg.tone === 'warning';
            const isArtifact = msg.kind === 'artifact';
            return (
              <div
                key={msg.id}
                className={`conversation-row flex ${isArtifact ? 'w-full justify-start' : isUser ? 'justify-end' : 'justify-start'} animate-in fade-in slide-in-from-bottom-4 duration-500`}
              >
                <div
                  className={`flex ${isArtifact ? 'w-full max-w-[min(1500px,96%)]' : 'max-w-[90%]'} ${isUser ? 'flex-row-reverse' : 'flex-row'} items-start gap-5`}
                >
                  <div className={`w-9 h-9 rounded-xl flex-shrink-0 flex items-center justify-center border shadow-sm ${
                    isUser ? 'bg-white border-slate-200 text-slate-500' : 'bg-indigo-700 border-indigo-600 text-white shadow-indigo-100'
                  }`}>
                    {isUser ? <Database size={16} /> : <FlaskConical size={18} />}
                  </div>
                  
                  <div className={`flex flex-col ${isArtifact ? 'w-full' : ''} ${isUser ? 'items-end' : 'items-start'}`}>
                    <div
                      className={`conversation-bubble ${isUser ? 'conversation-bubble-user' : 'conversation-bubble-assistant'} group relative ${isArtifact ? 'w-full px-0 py-0 bg-transparent border-0 shadow-none' : 'px-5 py-4 rounded-2xl border shadow-sm'} text-sm leading-relaxed ${
                      isUser 
                        ? 'bg-slate-800 text-white border-slate-700' 
                        : isWarning 
                          ? 'bg-amber-50 text-amber-900 border-amber-200' 
                          : 'bg-white text-slate-800 border-slate-100 shadow-slate-100/50'
                    }`}
                    >
                      <div className={`max-w-none ${isUser ? 'text-white' : 'prose prose-sm prose-slate prose-p:leading-relaxed prose-headings:mb-3'}`}>
                        {renderMessageContent(msg.content, {
                          textClassName: isUser ? 'text-white/95' : 'text-slate-700',
                          strongClassName: isUser ? 'font-semibold text-white' : 'font-semibold text-slate-900',
                          listItemClassName: isUser ? 'text-white/95' : 'text-slate-700',
                        })}
                      </div>

                      {/* Recognition Data displayed inline if any */}
                      {msg.recognitionResult && (
                        <div className="mt-4 p-3 bg-slate-50 border border-slate-200 rounded-lg">
                          <span className="text-xs font-bold text-slate-500 uppercase">Recognition: {msg.recognitionResult.system || 'Unknown'}</span>
                          <p className="text-xs text-slate-600 mt-1">{msg.recognitionResult.raw_summary}</p>
                        </div>
                      )}

                      {/* Artifact Dashboard displayed inline if any */}
                      {msg.kind === 'artifact' && (
                        <div className="mt-4 conversation-artifact-bubble">
                          <ArtifactResultPanel
                             settings={settings}
                             runId={msg.runId || ''}
                             routeName={msg.routeName || ''}
                             statusLabel={msg.runStatus || ''}
                             htmlContent={msg.htmlContent}
                             artifacts={msg.artifacts || []}
                             summary={msg.summary || {}}
                             isLoading={msg.runStatus === 'running' || msg.runStatus === 'queued'}
                             onAiAnalyze={onAiAnalyze}
                          />
                        </div>
                      )}
                      
                      {!isUser && (
                        <div className="absolute -right-10 top-0 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col space-y-1">
                          <button className="p-2 text-slate-300 hover:text-indigo-600 bg-white border border-slate-100 rounded-lg shadow-sm"><ClipboardList size={14} /></button>
                          <button className="p-2 text-slate-300 hover:text-indigo-600 bg-white border border-slate-100 rounded-lg shadow-sm"><Download size={14} /></button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })
        )}

        {/* Loading Animated Status */}
        {showLiveStatus && (
          <div className="flex justify-start">
            <div className="w-full max-w-[min(1200px,94%)] rounded-2xl border border-indigo-100 bg-white/95 px-5 py-4 shadow-sm shadow-indigo-100/60">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-indigo-50 flex items-center justify-center text-indigo-400 border border-indigo-100 shadow-sm shadow-indigo-100">
                    <Activity size={18} className="animate-pulse" />
                  </div>
                  <div className="space-y-1">
                    <p className="text-[10px] font-mono text-indigo-500 uppercase tracking-[0.18em]">
                      {liveStatusMessage || 'HPC_Processing...'}
                    </p>
                    <p className="text-xs text-slate-500">
                      {liveProgress
                        ? liveProgress.percent === null
                          ? `已记录 ${Math.max(liveProgress.steps.length, liveProgress.completed)} 个阶段，等待后端继续推进`
                          : `已完成 ${liveProgress.completed}/${liveProgress.total} 步`
                        : '正在和后端保持同步'}
                    </p>
                  </div>
                </div>
                {liveProgress ? (
                  <div className="rounded-full border border-indigo-100 bg-indigo-50 px-3 py-1 text-[11px] font-semibold text-indigo-600">
                    处理中
                  </div>
                ) : null}
              </div>

              <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100">
                <div
                  className={`h-full rounded-full bg-gradient-to-r from-indigo-500 via-blue-500 to-emerald-400 transition-all duration-500 ${liveProgress?.indeterminate ? 'animate-pulse' : ''}`}
                  style={{
                    width: liveProgress?.indeterminate
                      ? `${Math.min(22 + Math.max(liveProgress.steps.length, liveProgress.completed) * 8, 76)}%`
                      : `${liveProgress?.percent ?? 0}%`,
                  }}
                />
              </div>

              {liveProgress?.steps.length ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {liveProgress.steps.map((step) => (
                    <div
                      key={`${step.index}-${step.status}`}
                      className={`rounded-full border px-3 py-1 text-[11px] ${
                        step.status === 'completed'
                          ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                          : step.status === 'failed'
                            ? 'border-rose-200 bg-rose-50 text-rose-700'
                            : 'border-indigo-200 bg-indigo-50 text-indigo-700'
                      }`}
                    >
                      {step.index}. {step.label}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        )}
        <div ref={scrollAnchorRef} />
      </div>

      {/* 底部输入台 */}
      <div className="px-6 py-4 bg-white border-t border-slate-100 relative shadow-[0_-4px_20px_-10px_rgba(0,0,0,0.05)]">
        <div className="max-w-5xl mx-auto">
          
          <div className="relative group">
            <div className="absolute inset-0 -m-1 bg-gradient-to-r from-indigo-500/10 via-blue-500/5 to-emerald-500/10 rounded-[1.5rem] blur-2xl opacity-0 group-focus-within:opacity-100 transition duration-1000"></div>
            <div className="relative bg-white border border-slate-200 rounded-2xl shadow-xl transition-all group-focus-within:border-indigo-300 group-focus-within:shadow-indigo-500/5 overflow-hidden">
              <div className="flex items-center justify-between px-4 py-1.5 bg-slate-50/50 border-b border-slate-100">
                  <div className="flex items-center space-x-4">
                    <Terminal size={11} className="text-slate-400" />
                    <span className="text-[9px] font-mono text-slate-400 uppercase tracking-[0.2em]">{connectionStatus === 'ready' ? 'Material-AI Console v4.0' : `STATUS: ${connectionStatus}`}</span>
                  </div>
                  <div className="flex items-center space-x-3">
                    <button onClick={() => fileInputRef.current?.click()} className="text-[9px] text-indigo-500 font-bold hover:underline cursor-pointer flex items-center">
                       {uploadedAssets.length > 0 ? `${uploadedAssets.length} Assets Attached` : 'Upload Data'}
                    </button>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept="image/*,.data,.lmp,.lammps,.dat,.eam,.eam.alloy,.eam.fs,.meam,.setfl,.txt,.md,.json,.csv,.log"
                      multiple
                      className="hidden upload-input"
                      onChange={handleFileChange}
                      disabled={disabled}
                    />
                  </div>
              </div>
              
              <div className="flex items-end gap-3 px-4 py-3.5">
                <textarea
                  ref={textareaRef}
                  data-testid="chat-input"
                  value={draftMessage}
                  onChange={(e) => onDraftMessageChange(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={disabled}
                  placeholder="Enter standard query, phase diagram generation, or MD invocation logic..."
                  className="flex-1 max-h-32 min-h-[34px] bg-transparent py-2 outline-none text-sm leading-6 text-slate-700 resize-none font-sans"
                  rows={1}
                />
                <div className="flex items-center space-x-2 self-end pb-0.5">
                  <button 
                    onClick={() => void onRequestPromptSuggestion()}
                    disabled={disabled || isSuggestingPrompt}
                    className="p-2 text-indigo-600 hover:bg-indigo-50 rounded-xl transition-all flex items-center justify-center border border-indigo-100 shadow-sm disabled:cursor-not-allowed disabled:opacity-60"
                    title={isSuggestingPrompt ? '正在生成上下文推荐…' : '生成一条上下文推荐 prompt'}
                  >
                    <Sparkles size={17} className={isSuggestingPrompt ? 'animate-pulse' : ''} />
                  </button>
                  <button 
                    onClick={() => onSend()}
                    data-testid="send-button"
                    disabled={disabled || (!draftMessage.trim() && uploadedAssets.length === 0)}
                    className={`p-2.5 rounded-xl transition-all flex items-center justify-center ${
                      draftMessage.trim() || uploadedAssets.length > 0 ? 'bg-indigo-700 text-white shadow-lg shadow-indigo-200 hover:bg-indigo-800 hover:scale-105 active:scale-95' : 'bg-slate-50 text-slate-200'
                    }`}
                  >
                    <Send size={17} />
                  </button>
                </div>
              </div>
            </div>
          </div>
          
          <div className="mt-2 flex flex-wrap items-center justify-center gap-x-6 gap-y-1 text-[10px] text-slate-400">
            {["Phase Diagram", "LAMMPS MD", "Gemini Analysis"].map((label) => (
              <div key={label} className="flex items-center gap-2">
                <div className="h-1 w-1 rounded-full bg-slate-300" />
                <span>{label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
