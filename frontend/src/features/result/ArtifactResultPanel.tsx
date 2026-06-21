import { useEffect, useMemo, useState } from 'react';
import { Loader2, Sparkles, Activity, Play, BarChart3, Box, Atom, FileText, Film, Image as ImageIcon } from 'lucide-react';
import { getArtifactText, resolveArtifactUrl } from '../../services/api';
import type { ArtifactRef, ClientSettings, ResultProfile } from '../../types/api';

interface ArtifactResultPanelProps {
  settings: ClientSettings;
  runId: string;
  routeName: string;
  statusLabel: string;
  htmlContent?: string;
  artifacts: ArtifactRef[];
  summary: Record<string, unknown>;
  isLoading: boolean;
  onAiAnalyze: (prompt: string) => void;
}

type LammpsRagHit = Record<string, unknown>;

function readObject(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function readLammpsRag(summary: Record<string, unknown>): {
  planningHits: LammpsRagHit[];
  errorHits: LammpsRagHit[];
  material: string;
} {
  const rag = readObject(summary.materials_rag);
  const planning = readObject(rag.planning);
  const errorDiagnosis = readObject(rag.error_diagnosis);
  return {
    planningHits: Array.isArray(planning.hits) ? planning.hits as LammpsRagHit[] : [],
    errorHits: Array.isArray(errorDiagnosis.hits) ? errorDiagnosis.hits as LammpsRagHit[] : [],
    material: typeof planning.material === 'string' ? planning.material : '',
  };
}

function formatRagScore(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(3) : '—';
}

export function ArtifactResultPanel({
  settings,
  runId,
  routeName,
  statusLabel,
  htmlContent,
  artifacts,
  summary,
  isLoading,
  onAiAnalyze
}: ArtifactResultPanelProps) {
  const [isGeminiAnalyzing, setIsGeminiAnalyzing] = useState(false);
  const [videoFailures, setVideoFailures] = useState<Record<string, boolean>>({});
  const [selectedArtifactName, setSelectedArtifactName] = useState('');
  const [markdownText, setMarkdownText] = useState('');
  const metrics = summary.metrics && typeof summary.metrics === 'object' ? summary.metrics as Record<string, unknown> : {};
  const resultProfile = summary.result_profile && typeof summary.result_profile === 'object' ? summary.result_profile as ResultProfile : null;
  const lammpsRag = useMemo(() => readLammpsRag(summary), [summary]);
  
  const imageArtifacts = useMemo(() => artifacts.filter((a) => a.kind === 'image' && !a.name.endsWith('.json')), [artifacts]);
  const videoArtifacts = useMemo(() => artifacts.filter((a) => a.kind === 'video'), [artifacts]);
  const markdownArtifact = useMemo(() => artifacts.find((artifact) => artifact.kind === 'markdown'), [artifacts]);
  const downloadArtifacts = useMemo(
    () => artifacts.filter((artifact) => ['csv', 'json', 'code', 'text'].includes(artifact.kind) || artifact.name.startsWith('uploaded_')),
    [artifacts],
  );
  const lammpsStages = useMemo(() => {
    const stages: Array<{ key: string; title: string; subtitle: string; artifact: ArtifactRef; kind: 'video' | 'image' | 'markdown' }> = [];
    videoArtifacts.forEach((artifact, index) => {
      stages.push({
        key: artifact.name,
        title: index === 0 ? '主动画结果' : artifact.name,
        subtitle: 'OVITO 视频',
        artifact,
        kind: 'video',
      });
    });
    imageArtifacts.forEach((artifact, index) => {
      stages.push({
        key: artifact.name,
        title: artifact.name,
        subtitle: index === 0 ? '热力学图 / 快照' : '补充图像',
        artifact,
        kind: 'image',
      });
    });
    if (markdownArtifact) {
      stages.push({
        key: markdownArtifact.name,
        title: markdownArtifact.name,
        subtitle: '文字报告',
        artifact: markdownArtifact,
        kind: 'markdown',
      });
    }
    return stages;
  }, [imageArtifacts, markdownArtifact, videoArtifacts]);
  const lammpsRagPreviewHits = useMemo(
    () => [
      ...lammpsRag.planningHits.slice(0, 4).map((hit) => ({ hit, stage: 'planning' })),
      ...lammpsRag.errorHits.slice(0, 2).map((hit) => ({ hit, stage: 'error' })),
    ],
    [lammpsRag],
  );

  const handleAiClick = async (prompt: string) => {
    setIsGeminiAnalyzing(true);
    await onAiAnalyze(prompt);
    setIsGeminiAnalyzing(false);
  };

  useEffect(() => {
    if (!markdownArtifact) {
      setMarkdownText('');
      return;
    }

    const artifactUrl = markdownArtifact.url || markdownArtifact.path;
    if (!artifactUrl) {
      setMarkdownText('');
      return;
    }

    let cancelled = false;
    void getArtifactText(settings, artifactUrl)
      .then((text) => {
        if (!cancelled) {
          setMarkdownText(text);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMarkdownText('报告暂时加载失败，请直接下载 markdown 文件查看。');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [markdownArtifact, settings]);

  useEffect(() => {
    if (!lammpsStages.length) {
      setSelectedArtifactName('');
      return;
    }
    if (!selectedArtifactName || !lammpsStages.some((stage) => stage.key === selectedArtifactName)) {
      setSelectedArtifactName(lammpsStages[0].key);
    }
  }, [lammpsStages, selectedArtifactName]);

  // Default Inspector for typical states
  if (!htmlContent && artifacts.length === 0) {
    return (
      <div className="flex flex-col bg-[#f8fafc] border border-slate-200 rounded-2xl max-w-2xl w-full my-4 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-slate-200 bg-white/50 backdrop-blur-md">
          <h3 className="text-[10px] font-bold text-slate-500 uppercase tracking-[0.2em] flex items-center">
            <Box className="w-3.5 h-3.5 mr-2 text-indigo-600" /> Material Inspector
          </h3>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-8 flex items-center justify-center text-center">
          <div>
            <Atom className="w-12 h-12 text-slate-300 mx-auto mb-4" />
            <h4 className="text-sm font-bold text-slate-400">Awaiting Subroutines</h4>
            <p className="text-xs text-slate-500 mt-2 max-w-xs leading-relaxed">Agent will pipe phase diagram or MD run telemetry here once generated.</p>
          </div>
        </div>
      </div>
    );
  }

  // Phase Diagram Display styled with user's Tailwind design
  if (routeName === 'phase_diagram.generate' || routeName === 'mixed.request' || htmlContent) {
    return (
      <div className="w-full max-w-[min(1820px,100%)] my-4">
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          {htmlContent ? (
            <iframe
              key={runId}
              srcDoc={htmlContent}
              title="Phase Diagram"
              className="w-full h-[min(980px,calc(100vh-180px))] min-h-[760px] bg-white border-none"
              sandbox="allow-scripts allow-same-origin"
            />
          ) : (
            <div className="flex items-center justify-center h-64 text-sm text-slate-400">Loading projection...</div>
          )}
        </div>
      </div>
    );
  }

  // LAMMPS HUD Display styled with user's Tailwind design
  if (routeName === 'lammps.generate') {
    const selectedStage = lammpsStages.find((stage) => stage.key === selectedArtifactName) ?? lammpsStages[0] ?? null;
    return (
      <div className="flex flex-col bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-[min(1820px,100%)] my-4 overflow-hidden shadow-xl">
        <div className="px-4 py-3 bg-slate-800/80 border-b border-slate-800 flex items-center justify-between backdrop-blur-md">
          <div className="flex items-center space-x-3">
            <Activity className="w-4 h-4 text-emerald-400 animate-pulse" />
            <span className="text-[11px] font-mono text-slate-200 tracking-tight">MD_ANALYSIS_HUD.RUN</span>
          </div>
          <div className="flex items-center space-x-2 text-[10px]">
            <button 
              onClick={() => handleAiClick("请检查此 LAMMPS 输出图谱，分析能量守恒性和组织演变规律。")}
              className="flex items-center text-indigo-400 font-bold hover:text-indigo-300 px-2 py-0.5 rounded cursor-pointer transition-colors"
            >
              {isGeminiAnalyzing ? <Loader2 className="w-3 h-3 animate-spin mr-1.5" /> : <Sparkles className="w-3 h-3 mr-1.5" />}
              ✨ 结果洞察
            </button>
            <span className="text-emerald-500/80 px-2 py-0.5 bg-emerald-500/10 rounded ml-2">DATA: MAPPED</span>
          </div>
        </div>

        <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,2.35fr)_340px]">
          <div className="space-y-4">
            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4 shadow-inner">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500">Current Result</p>
                  <h3 className="mt-2 text-lg font-semibold text-slate-100">{selectedStage?.title || '等待结果返回'}</h3>
                  <p className="mt-1 text-sm text-slate-400">{selectedStage?.subtitle || statusLabel}</p>
                </div>
                {isLoading ? (
                  <div className="rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-1 text-[11px] font-semibold text-emerald-300">
                    处理中
                  </div>
                ) : null}
              </div>
              {resultProfile ? (
                <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1.8fr)_minmax(0,1fr)]">
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/70 px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2 text-[11px]">
                      <span className={`rounded-full border px-3 py-1 font-semibold ${
                        resultProfile.trust_level === 'high'
                          ? 'border-emerald-400/20 bg-emerald-500/10 text-emerald-300'
                          : resultProfile.trust_level === 'medium'
                            ? 'border-amber-400/20 bg-amber-500/10 text-amber-300'
                            : 'border-rose-400/20 bg-rose-500/10 text-rose-300'
                      }`}>
                        {resultProfile.category} · {resultProfile.trust_level}
                      </span>
                      <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1 font-semibold text-slate-300">
                        {resultProfile.source_label}
                      </span>
                      {typeof resultProfile.confidence === 'number' ? (
                        <span className="rounded-full border border-indigo-400/20 bg-indigo-500/10 px-3 py-1 font-semibold text-indigo-300">
                          confidence {Math.round(resultProfile.confidence * 100)}%
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-3 text-sm leading-6 text-slate-300">{resultProfile.trust_statement}</p>
                  </div>
                  <div className="grid gap-3">
                    {resultProfile.warnings?.length ? (
                      <div className="rounded-2xl border border-amber-400/20 bg-amber-500/10 px-4 py-3">
                        <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-amber-300">Warnings</p>
                        <ul className="mt-2 space-y-1 text-sm text-amber-100">
                          {resultProfile.warnings.slice(0, 3).map((item) => (
                            <li key={item}>• {item}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}

              <div className="mt-4 overflow-hidden rounded-xl border border-slate-800 bg-black/85">
                {!selectedStage ? (
                  <div className="flex h-[420px] items-center justify-center text-sm text-slate-500">
                    当前还没有可预览的结果，后处理完成后会按阶段陆续放到这里。
                  </div>
                ) : selectedStage.kind === 'video' ? (
                  <div className="relative flex h-[420px] items-center justify-center bg-black xl:h-[520px]">
                    <video
                      src={resolveArtifactUrl(settings, selectedStage.artifact.url || selectedStage.artifact.path)}
                      className="h-full w-full object-contain"
                      autoPlay
                      loop
                      muted
                      controls
                      playsInline
                      preload="metadata"
                      onError={() => {
                        setVideoFailures((current) => ({ ...current, [selectedStage.artifact.name]: true }));
                      }}
                    />
                    {videoFailures[selectedStage.artifact.name] ? (
                      <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-slate-950/90 px-4 text-center">
                        <Play className="w-8 h-8 text-amber-300" />
                        <div className="space-y-1">
                          <p className="text-xs font-semibold text-slate-100">MP4 预览加载失败</p>
                          <p className="text-[11px] leading-relaxed text-slate-300">
                            当前浏览器没有成功解码这个视频。你仍然可以查看 GIF 轨迹图，或直接下载 MP4 文件。
                          </p>
                        </div>
                        <a
                          href={resolveArtifactUrl(settings, selectedStage.artifact.url || selectedStage.artifact.path)}
                          target="_blank"
                          rel="noreferrer"
                          download
                          className="inline-flex items-center rounded-full border border-slate-600 bg-slate-900/80 px-3 py-1.5 text-[11px] font-semibold text-slate-100 transition hover:border-slate-400 hover:bg-slate-800"
                        >
                          下载 {selectedStage.artifact.name}
                        </a>
                      </div>
                    ) : null}
                  </div>
                ) : selectedStage.kind === 'image' ? (
                  <div className="flex h-[420px] items-center justify-center bg-slate-950 xl:h-[520px]">
                    <img
                      src={resolveArtifactUrl(settings, selectedStage.artifact.url || selectedStage.artifact.path)}
                      alt={selectedStage.artifact.name}
                      className="h-full w-full object-contain"
                    />
                  </div>
                ) : (
                  <div className="max-h-[520px] overflow-auto p-5">
                    <pre className="whitespace-pre-wrap break-words text-sm leading-7 text-slate-200">{markdownText || '报告加载中…'}</pre>
                  </div>
                )}
              </div>
            </div>

            {Object.keys(metrics).length > 0 && (
              <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                {Object.entries(metrics).map(([key, value]) => (
                  <div key={key} className="p-3 bg-black/40 rounded-lg border border-slate-800 flex flex-col justify-center shadow-inner hover:border-slate-700 transition-colors">
                    <span className="text-[8px] text-slate-500 leading-none mb-1 uppercase tracking-wider">{key.replace(/_/g, ' ')}</span>
                    <span className="text-[11px] font-mono text-indigo-300 font-bold">{typeof value === 'number' ? value.toFixed(3) : String(value)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <aside className="space-y-4">
            <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
              <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500">Result Navigator</p>
              <div className="mt-3 space-y-2">
                {lammpsStages.map((stage, index) => {
                  const Icon = stage.kind === 'video' ? Film : stage.kind === 'markdown' ? FileText : ImageIcon
                  const active = stage.key === selectedArtifactName
                  return (
                    <button
                      key={stage.key}
                      type="button"
                      onClick={() => setSelectedArtifactName(stage.key)}
                      className={`w-full rounded-xl border px-3 py-3 text-left transition ${
                        active
                          ? 'border-indigo-400/60 bg-indigo-500/10 text-slate-100'
                          : 'border-slate-800 bg-slate-900/60 text-slate-400 hover:border-slate-700 hover:text-slate-200'
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <div className={`rounded-lg p-2 ${active ? 'bg-indigo-500/20 text-indigo-200' : 'bg-slate-800 text-slate-400'}`}>
                          <Icon className="h-4 w-4" />
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-semibold">{index + 1}. {stage.title}</p>
                          <p className="truncate text-[11px] uppercase tracking-[0.14em] text-slate-500">{stage.subtitle}</p>
                        </div>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>

            {lammpsRagPreviewHits.length ? (
              <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500">Knowledge Grounding</p>
                <p className="mt-2 text-xs leading-relaxed text-slate-400">
                  {lammpsRag.material ? `Material hint: ${lammpsRag.material}. ` : ''}
                  本轮 LAMMPS 请求解释和参数检查使用了这些 RAG 知识卡。
                </p>
                <div className="mt-3 space-y-2">
                  {lammpsRagPreviewHits.map(({ hit, stage }, index) => (
                    <div key={`${stage}-${String(hit.title || 'rag')}-${index}`} className="rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-slate-200">{String(hit.title || 'Untitled knowledge card')}</p>
                          <p className="mt-1 text-[10px] uppercase tracking-[0.16em] text-slate-500">
                            {stage} · {String(hit.doc_type || 'knowledge')}
                          </p>
                        </div>
                        <span className="shrink-0 rounded-full border border-emerald-400/20 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold text-emerald-300">
                          {formatRagScore(hit.score)}
                        </span>
                      </div>
                      <p className="mt-2 text-[11px] leading-relaxed text-slate-400">
                        lexical {formatRagScore(hit.lexical_score)} · bm25 {formatRagScore(hit.bm25_score)} · vector {formatRagScore(hit.vector_score)}
                      </p>
                      {hit.source_url ? (
                        <a
                          href={String(hit.source_url)}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-2 block truncate text-[11px] font-semibold text-indigo-300 hover:text-indigo-200"
                        >
                          {String(hit.source || hit.source_url)}
                        </a>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {downloadArtifacts.length ? (
              <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-slate-500">Downloads</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {downloadArtifacts.map((artifact) => (
                    <a
                      key={artifact.name}
                      href={resolveArtifactUrl(settings, artifact.url || artifact.path)}
                      target="_blank"
                      rel="noreferrer"
                      download
                      className="rounded-full border border-slate-700 bg-slate-900 px-3 py-1.5 text-[11px] font-semibold text-slate-100 transition hover:border-slate-500 hover:bg-slate-800"
                    >
                      {artifact.name}
                    </a>
                  ))}
                </div>
              </div>
            ) : null}

            {isLoading ? (
              <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 p-4">
                <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-emerald-200">Streaming Outputs</p>
                <p className="mt-2 text-sm leading-relaxed text-emerald-50">
                  这条任务正在逐步回传结果。后处理完成的图像、动画和报告会按顺序出现在左侧主预览与右侧导航中。
                </p>
              </div>
            ) : null}
          </aside>
        </div>
      </div>
    );
  }

  return null;
}
