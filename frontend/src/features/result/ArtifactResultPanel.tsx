import { useEffect, useMemo, useState } from 'react';
import { Loader2, Sparkles, Activity, Play, BarChart3, Box, Atom, FileText, Film, Image as ImageIcon, ShieldAlert, ShieldCheck, Gauge } from 'lucide-react';
import { getArtifactText, resolveArtifactUrl } from '../../services/api';
import type {
  ArtifactRef,
  AgentJobResumeRequest,
  ClientSettings,
  LammpsEvidenceRef,
  LammpsRepairHistoryEntry,
  LammpsReviewFinding,
  LammpsReviewPayload,
  LammpsReviewScore,
  PhysicalQualityReport,
  ResultProfile,
} from '../../types/api';

interface ArtifactResultPanelProps {
  settings: ClientSettings;
  runId: string;
  routeName: string;
  statusLabel: string;
  htmlContent?: string;
  artifacts: ArtifactRef[];
  summary: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  isLoading: boolean;
  onAiAnalyze: (prompt: string) => void;
  onResumeJob?: (jobId: string, payload: AgentJobResumeRequest) => Promise<void>;
}

type LammpsRagHit = Record<string, unknown>;
type AuditTone = 'green' | 'amber' | 'red' | 'slate';

interface LammpsRedBlueAudit {
  review: LammpsReviewPayload | null;
  reviewMode: string;
  passed: boolean | null;
  score: LammpsReviewScore | null;
  findings: LammpsReviewFinding[];
  evidenceRefs: LammpsEvidenceRef[];
  issues: string[];
  advisoryIssues: string[];
  llmBlockingCandidates: string[];
  repairHistory: LammpsRepairHistoryEntry[];
  parseAudits: Array<Record<string, unknown>>;
}

interface BluePatchDiffRow {
  path: string;
  op: string;
  before: unknown;
  after: unknown;
  status: 'applied' | 'changed' | 'rejected' | 'candidate';
  reason: string;
}

interface EvidenceArtifactMatch {
  artifact: ArtifactRef;
  reason: string;
  score: number;
}

interface EvidenceMetadataEntry {
  key: string;
  value: string;
}

interface SharedMemoryEvidenceView {
  memoryId: string;
  locked: boolean;
  l1: Record<string, unknown>;
  l2Digest: string;
  l3Pointer: Record<string, unknown>;
  sourceRefs: string[];
  layers: string[];
}

interface EvidenceProvenanceRow {
  evidence: LammpsEvidenceRef;
  matches: EvidenceArtifactMatch[];
  metadataEntries: EvidenceMetadataEntry[];
  relatedFindings: LammpsReviewFinding[];
  sharedMemory: SharedMemoryEvidenceView | null;
}

interface DAGTimelineNode {
  nodeId: string;
  label: string;
  status: string;
  dependencies: string[];
  resourceClass: string;
  critical: boolean;
  attempt: number | null;
  durationSeconds: number | null;
  checkpointId: string;
  error: string;
  fallback: string;
}

interface DAGLifecycleTransition {
  fromState: string;
  toState: string;
  reason: string;
  emittedAt: string;
  terminationReason: string;
}

interface DAGTimelineView {
  enabled: boolean;
  status: string;
  planId: string;
  durationSeconds: number | null;
  nodes: DAGTimelineNode[];
  degradation: Record<string, unknown>;
  events: Array<Record<string, unknown>>;
  transitions: DAGLifecycleTransition[];
  checkpoints: Array<Record<string, unknown>>;
  lifecycleState: string;
  terminationReason: string;
  fallback: string;
}

type ExecutionTrustTone = 'green' | 'amber' | 'red' | 'slate';
type ExecutionTrustKind = 'real_science' | 'mock_demo' | 'partial_diagnostic' | 'real_diagnostic' | 'unknown';

interface ExecutionTrustView {
  kind: ExecutionTrustKind;
  tone: ExecutionTrustTone;
  label: string;
  title: string;
  statement: string;
  canUseAsScience: boolean;
  runMode: string;
  trustLevel: string;
  confidence: number | null;
  terminationReason: string;
  chips: string[];
  warnings: string[];
  artifacts: ArtifactRef[];
}

interface RecoveryControlView {
  visible: boolean;
  tone: ExecutionTrustTone;
  mode: string;
  title: string;
  statement: string;
  actionLabel: string;
  prompt: string;
  sourceJobId: string;
  resumeSupported: boolean;
  lastCheckpointId: string;
  failedNodes: string[];
  invalidatedNodes: string[];
  reusedNodes: string[];
  chips: string[];
  artifacts: ArtifactRef[];
}

interface AgentObservabilityRouteView {
  name: string;
  computeDomain: string;
  intent: string;
  nextStep: string;
  selectedTool: string;
  decisionSource: string;
  confidence: number | null;
  reason: string;
  supervisorAuditPassed: boolean | null;
  supervisorRequiresReview: boolean | null;
  supervisorLlmReviewed: boolean | null;
  supervisorDagValid: boolean | null;
  supervisorDagNodeCount: number;
  supervisorConfidenceMargin: number | null;
  supervisorConfidenceSource: string;
  supervisorTopRoute: string;
  supervisorFailures: string[];
}

interface AgentObservabilityToolResult {
  toolName: string;
  success: boolean | null;
  summary: string;
  error: string;
  artifactCount: number;
  source: string;
}

interface AgentObservabilityToolsView {
  needTool: boolean | null;
  selectedTools: string[];
  allowedCount: number;
  confidence: number | null;
  source: string;
  reason: string;
  resultCount: number;
  successCount: number;
  failureCount: number;
  results: AgentObservabilityToolResult[];
  skills: string[];
}

interface AgentObservabilityRagView {
  materialsAvailable: boolean;
  materialsRequested: boolean;
  materialsUsed: boolean;
  materialsGateReason: string;
  materialsHitCount: number;
  materialsPlanningHits: number;
  materialsErrorHits: number;
  material: string;
  materialsTitles: string[];
  thermoAvailable: boolean;
  thermoMatched: boolean | null;
  thermoCandidateCount: number;
  thermoStrategy: string;
  thermoTopDatabase: string;
  sharedAvailable: boolean;
  sharedBackend: string;
  sharedSelectedCount: number;
  sharedWriteCount: number;
  sharedConflictCount: number;
  sharedUnsafeWriteCount: number;
}

interface AgentObservabilityLlmCall {
  tier: string;
  model: string;
  capability: string;
  success: boolean | null;
  durationMs: number | null;
  score: number | null;
  fallbackFrom: string;
  reasons: string[];
}

interface AgentObservabilityLlmView {
  available: boolean;
  totalCalls: number;
  tierCounts: Record<string, number>;
  fallbackCount: number;
  successRate: number | null;
  avgLatencyMs: number | null;
  recentCalls: AgentObservabilityLlmCall[];
}

interface AgentObservabilityView {
  visible: boolean;
  route: AgentObservabilityRouteView;
  tools: AgentObservabilityToolsView;
  rag: AgentObservabilityRagView;
  llm: AgentObservabilityLlmView;
}

const LAMMPS_PREFLIGHT_TOPOLOGY: Record<string, {
  label: string;
  dependencies: string[];
  resourceClass: string;
  critical: boolean;
  fallback: string;
}> = {
  constraint_extract: {
    label: 'Constraint extract',
    dependencies: [],
    resourceClass: 'cpu',
    critical: true,
    fallback: '',
  },
  materials_rag_search: {
    label: 'Materials RAG search',
    dependencies: [],
    resourceClass: 'network',
    critical: false,
    fallback: 'registry + user input',
  },
  registry_lookup: {
    label: 'Registry lookup',
    dependencies: [],
    resourceClass: 'cpu',
    critical: true,
    fallback: '',
  },
  attachment_inspection: {
    label: 'Attachment inspection',
    dependencies: [],
    resourceClass: 'cpu',
    critical: false,
    fallback: 'skip when empty',
  },
  runtime_diagnostics: {
    label: 'Runtime diagnostics',
    dependencies: [],
    resourceClass: 'cpu',
    critical: true,
    fallback: '',
  },
  preflight_merge: {
    label: 'Preflight merge',
    dependencies: ['constraint_extract', 'materials_rag_search', 'registry_lookup', 'attachment_inspection', 'runtime_diagnostics'],
    resourceClass: 'cpu',
    critical: true,
    fallback: '',
  },
  red_pre_execution_review: {
    label: 'Red pre-exec review',
    dependencies: ['preflight_merge'],
    resourceClass: 'network',
    critical: true,
    fallback: 'deterministic guardrail',
  },
};

function readObject(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function readArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

function readStringArray(value: unknown): string[] {
  return readArray(value).map((item) => String(item)).filter((item) => item.trim().length > 0);
}

function mergeRunPayload(summary: Record<string, unknown>, metadata: Record<string, unknown>): Record<string, unknown> {
  return { ...summary, ...metadata };
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

function readPhysicalQuality(summary: Record<string, unknown>): PhysicalQualityReport | null {
  const quality = readObject(summary.quality);
  return Object.keys(quality).length ? quality as PhysicalQualityReport : null;
}

function readLammpsRedBlueAudit(payload: Record<string, unknown>): LammpsRedBlueAudit {
  const review = readObject(payload.review) as LammpsReviewPayload;
  const redReview = readObject(review.red_review);
  const sourceReview = Object.keys(redReview).length ? redReview : review;
  const score = readObject(review.score || sourceReview.score) as LammpsReviewScore;
  const findings = readArray<LammpsReviewFinding>(review.findings || sourceReview.findings);
  const evidenceRefs = readArray<LammpsEvidenceRef>(review.evidence_refs || sourceReview.evidence_refs);
  const repairHistory = readArray<LammpsRepairHistoryEntry>(payload.repair_history);
  const parseAudits = [
    readObject(review.llm_review_parse_audit),
    ...repairHistory.map((entry) => readObject(entry.blue_parse_audit)),
  ].filter((audit) => Object.keys(audit).length > 0);

  return {
    review: Object.keys(review).length ? review : null,
    reviewMode: typeof review.review_mode === 'string' ? review.review_mode : '',
    passed: typeof review.passed === 'boolean' ? review.passed : typeof sourceReview.passed === 'boolean' ? sourceReview.passed : null,
    score: Object.keys(score).length ? score : null,
    findings,
    evidenceRefs,
    issues: readStringArray(review.issues),
    advisoryIssues: readStringArray(review.advisory_issues),
    llmBlockingCandidates: readStringArray(review.llm_blocking_candidates),
    repairHistory,
    parseAudits,
  };
}

function formatPercent(value: unknown): string {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '—';
}

function formatNumber(value: unknown, digits = 3): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function readBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function readToolNamesFromCalls(value: unknown): string[] {
  return readArray<Record<string, unknown>>(value)
    .map((call) => String(readObject(call).tool_name || '').trim())
    .filter(Boolean);
}

function readTitlesFromHits(value: unknown): string[] {
  const titles: string[] = [];
  readArray<Record<string, unknown>>(value).forEach((hit) => {
    const title = String(readObject(hit).title || '').trim();
    if (title && !titles.includes(title)) {
      titles.push(title);
    }
  });
  return titles;
}

function readAgentObservability(payload: Record<string, unknown>, routeName: string): AgentObservabilityView {
  const obs = readObject(payload.agent_observability);
  const routeObs = readObject(obs.route);
  const toolsObs = readObject(obs.tools);
  const ragObs = readObject(obs.rag);
  const llmObs = readObject(obs.llm_routing || payload.llm_routing);
  const supervisorDecision = readObject(routeObs.supervisor_decision || payload.supervisor_decision);
  const supervisorAudit = readObject(supervisorDecision.supervisor_audit);
  const supervisorDag = readObject(supervisorAudit.dag);
  const supervisorFormula = readObject(supervisorAudit.confidence_formula);

  const route: AgentObservabilityRouteView = {
    name: String(routeObs.name || routeName || payload.route_name || 'conversation.answer'),
    computeDomain: String(routeObs.compute_domain || payload.compute_domain || 'none'),
    intent: String(routeObs.intent || payload.intent || ''),
    nextStep: String(routeObs.next_step || payload.next_step || ''),
    selectedTool: String(routeObs.selected_tool || payload.selected_tool || ''),
    decisionSource: String(routeObs.decision_source || payload.decision_source || ''),
    confidence: readNumber(routeObs.decision_confidence ?? payload.decision_confidence),
    reason: String(routeObs.reason || payload.reason || ''),
    supervisorAuditPassed: readBoolean(supervisorAudit.passed),
    supervisorRequiresReview: readBoolean(supervisorAudit.requires_llm_review),
    supervisorLlmReviewed: readBoolean(supervisorAudit.llm_reviewed),
    supervisorDagValid: readBoolean(supervisorDag.valid),
    supervisorDagNodeCount: readNumber(supervisorDag.node_count) ?? 0,
    supervisorConfidenceMargin: readNumber(supervisorAudit.confidence_margin),
    supervisorConfidenceSource: String(supervisorFormula.source || ''),
    supervisorTopRoute: String(supervisorAudit.top_route || ''),
    supervisorFailures: readStringArray(supervisorAudit.critical_failures),
  };

  const toolPolicy = readObject(toolsObs.policy || payload.tool_policy);
  const selectedTools = readStringArray(toolsObs.selected_tools).length
    ? readStringArray(toolsObs.selected_tools)
    : readToolNamesFromCalls(toolPolicy.selected_calls);
  const toolResultsPayload = readArray<Record<string, unknown>>(toolsObs.results).length
    ? readArray<Record<string, unknown>>(toolsObs.results)
    : readArray<Record<string, unknown>>(payload.tool_results);
  const tools: AgentObservabilityToolsView = {
    needTool: readBoolean(toolsObs.need_tool ?? toolPolicy.need_tool),
    selectedTools,
    allowedCount: readArray(toolPolicy.allowed_tools).length,
    confidence: readNumber(toolPolicy.confidence),
    source: String(toolPolicy.source || ''),
    reason: String(toolPolicy.reason || ''),
    resultCount: readNumber(toolsObs.result_count) ?? toolResultsPayload.length,
    successCount: readNumber(toolsObs.success_count) ?? toolResultsPayload.filter((item) => readObject(item).success === true).length,
    failureCount: readNumber(toolsObs.failure_count) ?? toolResultsPayload.filter((item) => readObject(item).success === false).length,
    results: toolResultsPayload.slice(0, 8).map((item) => {
      const result = readObject(item);
      const resultMetadata = readObject(result.metadata);
      return {
        toolName: String(result.tool_name || ''),
        success: readBoolean(result.success),
        summary: String(result.summary || ''),
        error: String(result.error || ''),
        artifactCount: readArray(result.artifacts).length || (readNumber(result.artifact_count) ?? 0),
        source: String(resultMetadata.source || resultMetadata.transport || ''),
      };
    }),
    skills: readArray<Record<string, unknown>>(readObject(toolsObs.skills || payload.skill_policy).selected_skills)
      .map((skill) => String(readObject(skill).skill_id || '').trim())
      .filter(Boolean),
  };

  const materials = readObject(ragObs.materials || payload.materials_rag);
  const planning = readObject(materials.planning);
  const errorDiagnosis = readObject(materials.error_diagnosis);
  const planningHits = readArray(planning.hits);
  const errorHits = readArray(errorDiagnosis.hits);
  const materialTitles = [
    ...readStringArray(materials.titles),
    ...readTitlesFromHits(planning.hits),
    ...readTitlesFromHits(errorDiagnosis.hits),
  ].filter((title, index, all) => title && all.indexOf(title) === index);
  const materialsHitCount = readNumber(materials.hit_count) ?? planningHits.length + errorHits.length;

  const thermo = readObject(ragObs.thermo || payload.thermo_lookup || payload.thermo_rag);
  const thermoTopCandidate = readObject(thermo.top_candidate || readArray<Record<string, unknown>>(thermo.candidates)[0]);
  const shared = readObject(ragObs.shared_memory || payload.shared_memory);
  const sharedRetrieval = readObject(shared.retrieval);

  const rag: AgentObservabilityRagView = {
    materialsAvailable: Boolean(materials.available) || Boolean(Object.keys(materials).length),
    materialsRequested: Boolean(materials.requested),
    materialsUsed: Boolean(materials.used) || materialsHitCount > 0,
    materialsGateReason: String(materials.gate_reason || ''),
    materialsHitCount,
    materialsPlanningHits: readNumber(materials.planning_hit_count) ?? planningHits.length,
    materialsErrorHits: readNumber(materials.error_hit_count) ?? errorHits.length,
    material: String(materials.material || planning.material || errorDiagnosis.material || ''),
    materialsTitles: materialTitles.slice(0, 8),
    thermoAvailable: Boolean(thermo.available) || Boolean(Object.keys(thermo).length),
    thermoMatched: readBoolean(thermo.matched),
    thermoCandidateCount: readNumber(thermo.candidate_count) ?? readArray(thermo.candidates).length,
    thermoStrategy: String(thermo.selection_strategy || ''),
    thermoTopDatabase: String(thermoTopCandidate.database_name || thermoTopCandidate.system_name || ''),
    sharedAvailable: Boolean(shared.available) || Boolean(Object.keys(shared).length),
    sharedBackend: String(shared.backend || sharedRetrieval.backend || ''),
    sharedSelectedCount: readNumber(shared.selected_count) ?? readArray(sharedRetrieval.selected_item_ids).length,
    sharedWriteCount: readNumber(shared.write_count) ?? 0,
    sharedConflictCount: readNumber(shared.conflict_count) ?? 0,
    sharedUnsafeWriteCount: readNumber(shared.unsafe_write_count) ?? 0,
  };

  const tierCounts = Object.entries(readObject(llmObs.tier_counts)).reduce<Record<string, number>>((acc, [key, value]) => {
    const numberValue = readNumber(value);
    if (numberValue !== null) {
      acc[key] = numberValue;
    }
    return acc;
  }, {});
  const recentCalls = readArray<Record<string, unknown>>(llmObs.recent_calls).map((item) => {
    const call = readObject(item);
    return {
      tier: String(call.tier || ''),
      model: String(call.model || readObject(call.route).model || ''),
      capability: String(call.capability || ''),
      success: readBoolean(call.success),
      durationMs: readNumber(call.duration_ms),
      score: readNumber(call.score),
      fallbackFrom: String(call.fallback_from || ''),
      reasons: readStringArray(call.reasons),
    };
  });
  const llm: AgentObservabilityLlmView = {
    available: Boolean(llmObs.available) || recentCalls.length > 0,
    totalCalls: readNumber(llmObs.total_calls) ?? recentCalls.length,
    tierCounts,
    fallbackCount: readNumber(llmObs.fallback_count) ?? recentCalls.filter((call) => call.fallbackFrom).length,
    successRate: readNumber(llmObs.success_rate),
    avgLatencyMs: readNumber(llmObs.avg_latency_ms),
    recentCalls,
  };

  const visible = Boolean(
    route.name ||
      selectedTools.length ||
      tools.resultCount ||
      rag.materialsAvailable ||
      rag.thermoAvailable ||
      rag.sharedAvailable ||
      llm.available,
  );

  return { visible, route, tools, rag, llm };
}

function qualityBadgeClasses(tone: 'green' | 'amber' | 'red' | 'slate'): string {
  if (tone === 'green') {
    return 'border-emerald-400/25 bg-emerald-500/10 text-emerald-200';
  }
  if (tone === 'amber') {
    return 'border-amber-400/25 bg-amber-500/10 text-amber-200';
  }
  if (tone === 'red') {
    return 'border-rose-400/25 bg-rose-500/10 text-rose-200';
  }
  return 'border-slate-700 bg-slate-800 text-slate-300';
}

function auditBadgeClasses(tone: AuditTone): string {
  if (tone === 'green') {
    return 'border-emerald-400/25 bg-emerald-500/10 text-emerald-200';
  }
  if (tone === 'amber') {
    return 'border-amber-400/25 bg-amber-500/10 text-amber-200';
  }
  if (tone === 'red') {
    return 'border-rose-400/25 bg-rose-500/10 text-rose-200';
  }
  return 'border-slate-700 bg-slate-900/70 text-slate-300';
}

function findingTone(severity: unknown): AuditTone {
  if (severity === 'blocking') {
    return 'red';
  }
  if (severity === 'warning') {
    return 'amber';
  }
  if (severity === 'info') {
    return 'green';
  }
  return 'slate';
}

function formatAuditScore(score: LammpsReviewScore | null): string {
  return typeof score?.overall_score === 'number' && Number.isFinite(score.overall_score) ? score.overall_score.toFixed(1) : '—';
}

function formatAuditValue(value: unknown): string {
  if (typeof value === 'boolean') {
    return value ? 'yes' : 'no';
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  if (typeof value === 'string' && value.trim()) {
    return value;
  }
  return '—';
}

function formatPatchValue(value: unknown): string {
  if (value === undefined) {
    return '—';
  }
  if (value === null) {
    return 'null';
  }
  if (typeof value === 'string') {
    return value || '""';
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function truncateText(value: string, maxLength = 120): string {
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;
}

function normalizeMatchText(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function compactHash(value: unknown): string {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text) {
    return '—';
  }
  return text.length > 16 ? `${text.slice(0, 12)}…${text.slice(-4)}` : text;
}

function formatMetadataValue(value: unknown): string {
  if (Array.isArray(value)) {
    const items = value.map((item) => String(item)).filter(Boolean);
    return truncateText(`${items.slice(0, 4).join(', ')}${items.length > 4 ? ` +${items.length - 4}` : ''}`, 140) || '[]';
  }
  if (value && typeof value === 'object') {
    try {
      return truncateText(JSON.stringify(value), 140);
    } catch {
      return '[object]';
    }
  }
  if (value === null) {
    return 'null';
  }
  if (value === undefined || value === '') {
    return '—';
  }
  return truncateText(String(value), 140);
}

function metadataEntries(metadata: Record<string, unknown> | undefined, limit = 6): EvidenceMetadataEntry[] {
  return Object.entries(metadata || {})
    .filter(([, value]) => value !== undefined && value !== '')
    .slice(0, limit)
    .map(([key, value]) => ({ key, value: formatMetadataValue(value) }));
}

function readSharedMemoryEvidence(evidence: LammpsEvidenceRef): SharedMemoryEvidenceView | null {
  const metadata = evidence.metadata || {};
  const l1 = readObject(metadata.l1);
  const l3Pointer = readObject(metadata.l3_pointer);
  const hasSharedMemoryShape = Boolean(
    metadata.controlled_context === 'L1/L2/L3' ||
      String(evidence.source_ref || '').startsWith('shared_memory:') ||
      Object.keys(l1).length ||
      Object.keys(l3Pointer).length
  );
  if (!hasSharedMemoryShape) {
    return null;
  }
  const memoryId = String(
    metadata.memory_id ||
      l1.memory_id ||
      l3Pointer.memory_id ||
      String(evidence.source_ref || '').replace(/^shared_memory:/, '') ||
      ''
  ).trim();
  const sourceRefs = readStringArray(l3Pointer.source_refs);
  const l2Digest = String(metadata.l2_digest || evidence.claim || '').trim();
  return {
    memoryId,
    locked: Boolean(l1.locked),
    l1,
    l2Digest,
    l3Pointer,
    sourceRefs,
    layers: readStringArray(metadata.controlled_context_layers),
  };
}

function isGenericSourceRef(value: unknown): boolean {
  const sourceRef = normalizeMatchText(value);
  return !sourceRef || ['artifact_manifest', 'lammps_execute', 'lammps_validation', 'registry', 'config'].includes(sourceRef);
}

function collectEvidenceArtifactHints(evidence: LammpsEvidenceRef): string[] {
  const hints = new Set<string>();
  const addHint = (value: unknown) => {
    const text = String(value || '').trim();
    if (text) {
      hints.add(text);
    }
  };
  const metadata = evidence.metadata || {};
  readStringArray(metadata.artifact_names).forEach(addHint);
  if (!isGenericSourceRef(evidence.source_ref)) {
    addHint(evidence.source_ref);
  }
  if (evidence.source_type === 'quality_report') {
    addHint('quality_report.json');
  }
  if (evidence.source_type === 'script') {
    addHint('in.lammps');
  }
  if (evidence.source_type === 'execution' || evidence.source_type === 'validation') {
    addHint('trace.json');
  }
  if (evidence.source_type === 'log') {
    addHint('log.lammps');
  }
  return [...hints];
}

function targetArtifactRoles(sourceType: unknown): string[] {
  if (sourceType === 'quality_report') {
    return ['physical_quality_report'];
  }
  if (sourceType === 'execution' || sourceType === 'validation') {
    return ['runtime_trace'];
  }
  if (sourceType === 'script') {
    return ['lammps_script'];
  }
  if (sourceType === 'artifact') {
    return ['trajectory', 'partial_result', 'red_review_post', 'repair_history', 'llm_parse_audit'];
  }
  return [];
}

function artifactFingerprint(artifact: ArtifactRef): string {
  return normalizeMatchText([
    artifact.name,
    artifact.path,
    artifact.url,
    artifact.metadata?.artifact_role,
    artifact.metadata?.content_hash,
  ].filter(Boolean).join(' '));
}

function artifactContentHash(artifact: ArtifactRef): string {
  const hash = artifact.metadata?.content_hash || artifact.metadata?.hash || artifact.metadata?.sha256;
  return typeof hash === 'string' ? hash : '';
}

function matchEvidenceArtifacts(evidence: LammpsEvidenceRef, artifacts: ArtifactRef[]): EvidenceArtifactMatch[] {
  const hints = collectEvidenceArtifactHints(evidence);
  const normalizedHints = hints.map(normalizeMatchText).filter(Boolean);
  const targetRoles = targetArtifactRoles(evidence.source_type);
  const evidenceHash = normalizeMatchText(evidence.content_hash);
  const artifactNames = readStringArray(evidence.metadata?.artifact_names).map(normalizeMatchText);

  return artifacts
    .map((artifact) => {
      const artifactName = normalizeMatchText(artifact.name);
      const role = normalizeMatchText(artifact.metadata?.artifact_role);
      const fingerprint = artifactFingerprint(artifact);
      const reasons: string[] = [];
      let score = 0;

      if (targetRoles.includes(role)) {
        score += 5;
        reasons.push(`role:${role}`);
      }
      if (artifactNames.includes(artifactName)) {
        score += 6;
        reasons.push('artifact manifest');
      }
      normalizedHints.forEach((hint) => {
        if (artifactName === hint) {
          score += 6;
          reasons.push(`name:${hint}`);
        } else if (hint.length >= 4 && fingerprint.includes(hint)) {
          score += 3;
          reasons.push(`ref:${hint}`);
        }
      });
      if (evidenceHash && normalizeMatchText(artifactContentHash(artifact)) === evidenceHash) {
        score += 8;
        reasons.push('content hash');
      }

      return score > 0 ? { artifact, reason: [...new Set(reasons)].join(', '), score } : null;
    })
    .filter((match): match is EvidenceArtifactMatch => Boolean(match))
    .sort((left, right) => right.score - left.score)
    .slice(0, 4);
}

function buildEvidenceProvenanceRows(
  evidenceRefs: LammpsEvidenceRef[],
  findings: LammpsReviewFinding[],
  artifacts: ArtifactRef[],
): EvidenceProvenanceRow[] {
  return evidenceRefs.map((evidence) => {
    const evidenceId = evidence.evidence_id || '';
    return {
      evidence,
      matches: matchEvidenceArtifacts(evidence, artifacts),
      metadataEntries: metadataEntries(evidence.metadata),
      relatedFindings: evidenceId
        ? findings.filter((finding) => readStringArray(finding.evidence_refs).includes(evidenceId)).slice(0, 3)
        : [],
      sharedMemory: readSharedMemoryEvidence(evidence),
    };
  });
}

function readFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function readTimestampDurationSeconds(startedAt: unknown, finishedAt: unknown): number | null {
  if (typeof startedAt !== 'string' || typeof finishedAt !== 'string') {
    return null;
  }
  const started = Date.parse(startedAt);
  const finished = Date.parse(finishedAt);
  if (!Number.isFinite(started) || !Number.isFinite(finished) || finished < started) {
    return null;
  }
  return (finished - started) / 1000;
}

function formatDurationSeconds(value: number | null): string {
  if (value === null) {
    return '—';
  }
  if (value < 1) {
    return `${Math.round(value * 1000)} ms`;
  }
  return `${value.toFixed(2)} s`;
}

function dagStatusBadgeClasses(status: string): string {
  if (status === 'completed') {
    return 'border-emerald-400/25 bg-emerald-500/10 text-emerald-100';
  }
  if (status === 'completed_with_fallback') {
    return 'border-amber-400/25 bg-amber-500/10 text-amber-100';
  }
  if (status === 'failed' || status === 'timed_out') {
    return 'border-rose-400/25 bg-rose-500/10 text-rose-100';
  }
  if (status === 'running') {
    return 'border-indigo-400/25 bg-indigo-500/10 text-indigo-100';
  }
  return 'border-slate-600 bg-slate-900/70 text-slate-300';
}

function readDAGTransitions(lifecycle: Record<string, unknown>): DAGLifecycleTransition[] {
  return readArray<Record<string, unknown>>(lifecycle.events)
    .filter((event) => event.event_type === 'lifecycle.transition')
    .map((event) => ({
      fromState: typeof event.from_state === 'string' ? event.from_state : '',
      toState: typeof event.to_state === 'string' ? event.to_state : '',
      reason: typeof event.reason === 'string' ? event.reason : '',
      emittedAt: typeof event.emitted_at === 'string' ? event.emitted_at : '',
      terminationReason: typeof event.termination_reason === 'string' ? event.termination_reason : '',
    }));
}

function readDAGEvents(preflightDag: Record<string, unknown>, lifecycle: Record<string, unknown>): Array<Record<string, unknown>> {
  const directEvents = readArray<Record<string, unknown>>(preflightDag.events);
  if (directEvents.length) {
    return directEvents;
  }
  return readArray<Record<string, unknown>>(lifecycle.events)
    .filter((event) => event.event_type === 'dag.event')
    .map((event) => readObject(event.metadata));
}

function readDAGTimeline(payload: Record<string, unknown>): DAGTimelineView {
  const preflightDag = readObject(payload.preflight_dag);
  const lifecycle = readObject(payload.lifecycle);
  const results = readObject(preflightDag.results);
  const topologicalOrder = readStringArray(preflightDag.topological_order);
  const nodeIds = topologicalOrder.length
    ? topologicalOrder
    : Object.keys(results).length
      ? Object.keys(results)
      : Object.keys(preflightDag).length
        ? Object.keys(LAMMPS_PREFLIGHT_TOPOLOGY)
        : [];
  const nodes = nodeIds.map((nodeId) => {
    const result = readObject(results[nodeId]);
    const topology = LAMMPS_PREFLIGHT_TOPOLOGY[nodeId] || {
      label: nodeId.replace(/_/g, ' '),
      dependencies: [],
      resourceClass: typeof result.resource_class === 'string' ? result.resource_class : '',
      critical: true,
      fallback: '',
    };
    const metadata = readObject(result.metadata);
    const durationSeconds = readFiniteNumber(result.duration_seconds) ?? readTimestampDurationSeconds(result.started_at, result.finished_at);
    return {
      nodeId,
      label: topology.label,
      status: typeof result.status === 'string' ? result.status : 'pending',
      dependencies: topology.dependencies,
      resourceClass: topology.resourceClass,
      critical: topology.critical,
      attempt: readFiniteNumber(result.attempt),
      durationSeconds,
      checkpointId: typeof result.checkpoint_id === 'string' ? result.checkpoint_id : '',
      error: typeof result.error === 'string' ? result.error : '',
      fallback: typeof metadata.fallback === 'string' ? metadata.fallback : topology.fallback,
    };
  });
  const metadata = readObject(preflightDag.metadata);
  const degradation = readObject(metadata.degradation);
  return {
    enabled: Boolean(Object.keys(preflightDag).length || Object.keys(lifecycle).length),
    status: typeof preflightDag.status === 'string' ? preflightDag.status : '',
    planId: typeof preflightDag.plan_id === 'string' ? preflightDag.plan_id : '',
    durationSeconds: readFiniteNumber(preflightDag.duration_seconds),
    nodes,
    degradation,
    events: readDAGEvents(preflightDag, lifecycle),
    transitions: readDAGTransitions(lifecycle),
    checkpoints: readArray<Record<string, unknown>>(lifecycle.checkpoints),
    lifecycleState: typeof lifecycle.current_state === 'string' ? lifecycle.current_state : '',
    terminationReason: typeof lifecycle.termination_reason === 'string' ? lifecycle.termination_reason : '',
    fallback: typeof preflightDag.fallback === 'string' ? preflightDag.fallback : '',
  };
}

function shouldShowDAGTimeline(timeline: DAGTimelineView): boolean {
  return Boolean(
    timeline.enabled ||
      timeline.nodes.length ||
      timeline.events.length ||
      timeline.transitions.length ||
      timeline.checkpoints.length
  );
}

function trustBadgeClasses(tone: ExecutionTrustTone): string {
  if (tone === 'green') {
    return 'border-emerald-400/25 bg-emerald-500/10 text-emerald-100';
  }
  if (tone === 'amber') {
    return 'border-amber-400/25 bg-amber-500/10 text-amber-100';
  }
  if (tone === 'red') {
    return 'border-rose-400/25 bg-rose-500/10 text-rose-100';
  }
  return 'border-slate-700 bg-slate-900/70 text-slate-300';
}

function collectTrustArtifacts(artifacts: ArtifactRef[], kind: ExecutionTrustKind): ArtifactRef[] {
  const wantedNames = new Set(['quality_report.json', 'trace.json', 'lifecycle.json']);
  const wantedRoles = new Set(['physical_quality_report', 'runtime_lifecycle']);
  if (kind === 'partial_diagnostic') {
    wantedNames.add('partial_result.json');
    wantedRoles.add('partial_result');
  }
  if (kind === 'mock_demo') {
    wantedNames.add('thermo_metadata.json');
  }
  return artifacts
    .filter((artifact) => {
      const role = String(artifact.metadata?.artifact_role || '');
      return wantedNames.has(artifact.name) || wantedRoles.has(role);
    })
    .slice(0, 5);
}

function readExecutionTrust(
  payload: Record<string, unknown>,
  quality: PhysicalQualityReport | null,
  profile: ResultProfile | null,
  dagTimeline: DAGTimelineView,
  artifacts: ArtifactRef[],
): ExecutionTrustView {
  const runtimeProfile = readObject(payload.runtime_profile);
  const partialReport = readObject(payload.partial_report);
  const runMode = String(payload.run_mode || quality?.run_mode || profile?.mode_label || runtimeProfile.run_mode || '').trim();
  const terminationReason = String(payload.termination_reason || runtimeProfile.termination_reason || dagTimeline.terminationReason || '').trim();
  const hasPartialArtifact = artifacts.some((artifact) => artifact.name === 'partial_result.json' || artifact.metadata?.artifact_role === 'partial_result');
  const degradationLevel = typeof dagTimeline.degradation.degradation_level === 'string' ? dagTimeline.degradation.degradation_level : '';
  const isPartial = Boolean(
    Object.keys(partialReport).length ||
      hasPartialArtifact ||
      degradationLevel === 'level_3_partial_report' ||
      terminationReason === 'global_timeout'
  );
  const isMock = runMode === 'mock' || quality?.synthetic_thermo === true;
  const scientificPassed = quality?.scientific_result_passed === true;

  let kind: ExecutionTrustKind = 'unknown';
  if (isPartial) {
    kind = 'partial_diagnostic';
  } else if (isMock) {
    kind = 'mock_demo';
  } else if (runMode === 'real' && scientificPassed) {
    kind = 'real_science';
  } else if (runMode === 'real') {
    kind = 'real_diagnostic';
  }

  const byKind: Record<ExecutionTrustKind, Pick<ExecutionTrustView, 'tone' | 'label' | 'title' | 'statement' | 'canUseAsScience'>> = {
    real_science: {
      tone: 'green',
      label: 'REAL SCIENCE',
      title: '真实 LAMMPS 科学结果',
      statement: '本轮来自真实本地 LAMMPS 执行，且物理质量门标记为 scientific_result_passed=true，可继续作为科学结果审查和引用。',
      canUseAsScience: true,
    },
    mock_demo: {
      tone: 'amber',
      label: 'MOCK DEMO',
      title: 'Mock / synthetic 演示结果',
      statement: '本轮使用 mock fallback 或 synthetic thermo，只能用于 UI、流程和接口验证，不能作为真实科学结论。',
      canUseAsScience: false,
    },
    partial_diagnostic: {
      tone: 'red',
      label: 'PARTIAL DIAGNOSTIC',
      title: 'Partial / 诊断产物',
      statement: '本轮没有完成可信科学结果，只保留 partial/checkpoint/trace 供恢复、重试或排查使用。',
      canUseAsScience: false,
    },
    real_diagnostic: {
      tone: 'red',
      label: 'REAL DIAGNOSTIC',
      title: '真实执行但未通过科学质量门',
      statement: '本轮尝试真实 LAMMPS 执行，但质量门未确认 scientific_result_passed；只能当诊断结果，不能直接当科学结论。',
      canUseAsScience: false,
    },
    unknown: {
      tone: 'slate',
      label: 'UNKNOWN',
      title: '执行模式待确认',
      statement: '后端尚未返回足够的 run_mode / quality / runtime profile 信息，先不要把该结果当作科学结论。',
      canUseAsScience: false,
    },
  };
  const base = byKind[kind];
  const chips = [
    `mode: ${runMode || 'unknown'}`,
    `scientific: ${scientificPassed ? 'yes' : 'no'}`,
    `synthetic: ${quality?.synthetic_thermo ? 'yes' : 'no'}`,
    `quality: ${quality?.passed === undefined ? 'pending' : quality.passed ? 'passed' : 'failed'}`,
  ];
  if (terminationReason) {
    chips.push(`stop: ${terminationReason}`);
  }
  if (profile?.source_label) {
    chips.push(profile.source_label);
  }
  const warnings = [
    ...(profile?.warnings || []),
    ...readStringArray(runtimeProfile.warnings),
    ...(quality?.issues || []),
  ];
  return {
    kind,
    ...base,
    runMode: runMode || 'unknown',
    trustLevel: profile?.trust_level || String(runtimeProfile.trust_level || 'unknown'),
    confidence: typeof profile?.confidence === 'number' ? profile.confidence : null,
    terminationReason,
    chips,
    warnings: [...new Set(warnings)].slice(0, 5),
    artifacts: collectTrustArtifacts(artifacts, kind),
  };
}

function latestCheckpointId(timeline: DAGTimelineView): string {
  const checkpoint = timeline.checkpoints[timeline.checkpoints.length - 1] || {};
  return String(checkpoint.checkpoint_id || checkpoint.stage || timeline.degradation.last_checkpoint_id || '').trim();
}

function readFailureNodes(degradation: Record<string, unknown>): string[] {
  const failureBatch = readObject(degradation.failure_batch);
  const direct = readStringArray(failureBatch.failed_nodes);
  if (direct.length) {
    return direct;
  }
  return readArray<Record<string, unknown>>(failureBatch.findings)
    .map((finding) => String(finding.node_id || '').trim())
    .filter(Boolean);
}

function collectRecoveryArtifacts(artifacts: ArtifactRef[]): ArtifactRef[] {
  const wantedNames = new Set(['partial_result.json', 'lifecycle.json', 'trace.json', 'repair_history.json', 'quality_report.json']);
  const wantedRoles = new Set(['partial_result', 'runtime_lifecycle', 'repair_history', 'physical_quality_report']);
  return artifacts
    .filter((artifact) => wantedNames.has(artifact.name) || wantedRoles.has(String(artifact.metadata?.artifact_role || '')))
    .slice(0, 6);
}

function buildRecoveryPrompt(params: {
  runId: string;
  trust: ExecutionTrustView;
  checkpointId: string;
  degradationLevel: string;
  failedNodes: string[];
  invalidatedNodes: string[];
  reusedNodes: string[];
  terminationReason: string;
}): string {
  const failed = params.failedNodes.length ? params.failedNodes.join(', ') : 'none';
  const invalidated = params.invalidatedNodes.length ? params.invalidatedNodes.join(', ') : 'none';
  const reused = params.reusedNodes.length ? params.reusedNodes.join(', ') : 'none';
  const base = [
    `请基于上一轮 LAMMPS run_id=${params.runId || 'unknown'} 继续处理。`,
    `上一轮信任状态：${params.trust.label}；science usable=${params.trust.canUseAsScience ? 'yes' : 'no'}。`,
    `last_checkpoint_id=${params.checkpointId || 'none'}；degradation=${params.degradationLevel || 'none'}；termination_reason=${params.terminationReason || 'none'}。`,
    `failed_nodes=${failed}；invalidated_nodes=${invalidated}；reused_nodes=${reused}。`,
    '要求：不要把 mock、partial 或未通过 quality gate 的结果描述成真实科学结论；如果能安全复用 checkpoint/trace/lifecycle，请作为新 attempt 继续；如果不能复用，请解释原因并重新规划。',
  ];
  if (params.trust.kind === 'mock_demo') {
    base.push('优先尝试真实 LAMMPS 执行；如果本机 LAMMPS_CMD、POTENTIALS_DIR 或势函数环境缺失，请只输出配置诊断和修复步骤，不要回退成 mock 成功。');
  } else if (params.trust.kind === 'partial_diagnostic') {
    base.push('优先从 partial_result/lifecycle/checkpoint 中恢复 preflight 之后的安全节点；若真实 LAMMPS timestep 级 restart 不安全，请新建 attempt 并复用已完成的 preflight 信息。');
  } else if (params.trust.kind === 'real_diagnostic') {
    base.push('优先修复导致 quality gate 或 Red review 失败的问题，然后重新执行 validation/codegen/execution/review。');
  } else {
    base.push('请先检查 trace、quality_report、lifecycle 和 repair_history，再决定是重试、要求用户补充信息，还是终止。');
  }
  return base.join('\n');
}

function readRecoveryControl(
  payload: Record<string, unknown>,
  runId: string,
  trust: ExecutionTrustView,
  timeline: DAGTimelineView,
  artifacts: ArtifactRef[],
): RecoveryControlView {
  const partialReport = readObject(payload.partial_report);
  const sourceJobId = String(payload.job_id || payload.source_job_id || '').trim();
  const degradationLevel = typeof timeline.degradation.degradation_level === 'string' ? timeline.degradation.degradation_level : 'none';
  const lastCheckpoint = String(partialReport.last_checkpoint_id || latestCheckpointId(timeline)).trim();
  const failedNodes = readFailureNodes(timeline.degradation);
  const invalidatedNodes = readStringArray(timeline.degradation.invalidated_nodes);
  const reusedNodes = readStringArray(timeline.degradation.reused_nodes);
  const resumeSupported = typeof partialReport.resume_supported === 'boolean'
    ? partialReport.resume_supported
    : Boolean(lastCheckpoint || timeline.checkpoints.length);
  const shouldShow = !trust.canUseAsScience || timeline.checkpoints.length > 0 || failedNodes.length > 0 || invalidatedNodes.length > 0;
  const mode = trust.kind === 'partial_diagnostic'
    ? 'checkpoint-guided new attempt'
    : trust.kind === 'mock_demo'
      ? 'retry real execution'
      : trust.kind === 'real_diagnostic'
        ? 'repair and rerun'
        : 'inspect and retry';
  const title = trust.kind === 'partial_diagnostic'
    ? '可从 checkpoint 上下文继续'
    : trust.kind === 'mock_demo'
      ? '可重新尝试真实 LAMMPS'
      : trust.kind === 'real_diagnostic'
        ? '可修复后重跑'
        : '可发起诊断重试';
  const statement = resumeSupported
    ? '当前版本会创建一个新的 agent attempt，并把 checkpoint、失败节点和 trust 状态作为上下文传入；不会原地修改旧 run。'
    : '当前 run 没有可识别 checkpoint；仍可新建一次诊断重试，但不能声称已从 checkpoint 恢复。';
  const prompt = buildRecoveryPrompt({
    runId,
    trust,
    checkpointId: lastCheckpoint,
    degradationLevel,
    failedNodes,
    invalidatedNodes,
    reusedNodes,
    terminationReason: trust.terminationReason,
  });
  return {
    visible: shouldShow,
    tone: trust.canUseAsScience ? 'slate' : trust.tone,
    mode,
    title,
    statement,
    actionLabel: trust.kind === 'mock_demo' ? '重试真实执行' : trust.kind === 'partial_diagnostic' ? '带 checkpoint 继续' : '修复并重试',
    prompt,
    sourceJobId,
    resumeSupported,
    lastCheckpointId: lastCheckpoint,
    failedNodes,
    invalidatedNodes,
    reusedNodes,
    chips: [
      `mode: ${mode}`,
      `checkpoint: ${lastCheckpoint || 'none'}`,
      `resume api: ${sourceJobId ? 'available' : 'prompt fallback'}`,
      `new attempt: yes`,
    ],
    artifacts: collectRecoveryArtifacts(artifacts),
  };
}

function valuesMatch(left: unknown, right: unknown): boolean {
  return formatPatchValue(left) === formatPatchValue(right);
}

function normalizePatchPath(value: unknown): string {
  return typeof value === 'string' ? value.trim().replace(/^\/+/, '') : '';
}

function operationDiffRow(
  operation: Record<string, unknown>,
  status: BluePatchDiffRow['status'],
  snapshots: { before: Record<string, unknown>; after: Record<string, unknown> },
): BluePatchDiffRow | null {
  const path = normalizePatchPath(operation.normalized_path || operation.path);
  const op = typeof operation.op === 'string' ? operation.op : status;
  if (!path || op === 'verify') {
    return null;
  }
  const before = operation.before !== undefined ? operation.before : snapshots.before[path];
  const after = operation.after !== undefined ? operation.after : snapshots.after[path];
  return {
    path,
    op,
    before,
    after,
    status,
    reason: typeof operation.reason === 'string' ? operation.reason : '',
  };
}

function buildBluePatchDiffRows(entry: LammpsRepairHistoryEntry): BluePatchDiffRow[] {
  const policy = entry.policy_report;
  const beforeRequest = readObject(policy?.before_request);
  const afterRequest = readObject(policy?.after_request);
  const appliedOperations = readArray<Record<string, unknown>>(policy?.applied_operations);
  const rejectedOperations = readArray<Record<string, unknown>>(policy?.rejected_operations);
  const patchOperations = readArray<Record<string, unknown>>(entry.patch?.operations);
  const operationByPath = new Map<string, Record<string, unknown>>();
  [...appliedOperations, ...patchOperations].forEach((operation) => {
    const path = normalizePatchPath(operation.normalized_path || operation.path);
    if (path && !operationByPath.has(path)) {
      operationByPath.set(path, operation);
    }
  });

  const rows: BluePatchDiffRow[] = [];
  const rowKeys = new Set<string>();
  const addRow = (row: BluePatchDiffRow | null) => {
    if (!row) {
      return;
    }
    const key = `${row.path}:${row.op}:${formatPatchValue(row.before)}:${formatPatchValue(row.after)}`;
    if (rowKeys.has(key)) {
      return;
    }
    rowKeys.add(key);
    rows.push(row);
  };

  const snapshotKeys = new Set([...Object.keys(beforeRequest), ...Object.keys(afterRequest)]);
  snapshotKeys.forEach((path) => {
    const before = beforeRequest[path];
    const after = afterRequest[path];
    if (valuesMatch(before, after)) {
      return;
    }
    const operation = operationByPath.get(path);
    addRow({
      path,
      op: typeof operation?.op === 'string' ? operation.op : 'modify',
      before,
      after,
      status: policy?.accepted ? 'changed' : 'candidate',
      reason: typeof operation?.reason === 'string' ? operation.reason : '',
    });
  });

  appliedOperations.forEach((operation) => {
    addRow(operationDiffRow(operation, policy?.accepted ? 'applied' : 'candidate', { before: beforeRequest, after: afterRequest }));
  });
  rejectedOperations.forEach((operation) => {
    addRow(operationDiffRow(operation, 'rejected', { before: beforeRequest, after: afterRequest }));
  });
  if (!rows.length) {
    patchOperations.forEach((operation) => {
      addRow(operationDiffRow(operation, 'candidate', { before: beforeRequest, after: afterRequest }));
    });
  }
  return rows;
}

function shouldShowRedBlueAudit(audit: LammpsRedBlueAudit): boolean {
  return Boolean(
    audit.review ||
      audit.findings.length ||
      audit.evidenceRefs.length ||
      audit.repairHistory.length ||
      audit.parseAudits.length
  );
}

function formatLearnedRouteReason(reason: string): string {
  const match = reason.match(/^learned_(shadow|guarded):([^:]+):([0-9.]+)/);
  if (!match) {
    return reason.replace(/^learned_/, 'MLP ');
  }
  const [, mode, tier, confidence] = match;
  return `MLP ${mode} → ${tier} (${Math.round(Number(confidence) * 100)}%)`;
}

function AgentObservabilityCard({ view }: { view: AgentObservabilityView }) {
  if (!view.visible) {
    return null;
  }

  const routeChips = [
    view.route.computeDomain && `domain: ${view.route.computeDomain}`,
    view.route.nextStep && `next: ${view.route.nextStep}`,
    view.route.selectedTool && `tool: ${view.route.selectedTool}`,
    view.route.decisionSource && `source: ${view.route.decisionSource}`,
    view.route.confidence !== null && `confidence ${Math.round(view.route.confidence * 100)}%`,
    view.route.supervisorDagValid !== null && `DAG ${view.route.supervisorDagValid ? 'passed' : 'failed'}`,
    view.route.supervisorLlmReviewed === true
      ? 'LLM reviewed'
      : view.route.supervisorRequiresReview === true
        ? 'review required'
        : view.route.supervisorLlmReviewed === false
          ? 'deterministic'
          : '',
  ].filter(Boolean) as string[];
  const ragChips = [
    view.rag.materialsRequested
      ? `materials hits ${view.rag.materialsHitCount}`
      : view.rag.materialsAvailable
        ? 'materials RAG skipped'
        : 'materials RAG idle',
    view.rag.thermoAvailable ? `thermo candidates ${view.rag.thermoCandidateCount}` : 'thermo RAG idle',
    view.rag.sharedAvailable ? `memory selected ${view.rag.sharedSelectedCount}` : 'memory idle',
  ];
  const latestLlm = view.llm.recentCalls[0];
  const latestLearnedReason = latestLlm?.reasons.find((reason) => reason.startsWith('learned_')) || '';

  return (
    <div data-testid="agent-observability-panel" className="rounded-2xl border border-cyan-400/20 bg-slate-950/95 px-4 py-4 text-cyan-50 shadow-inner">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="rounded-xl border border-cyan-200/20 bg-black/20 p-2">
            <BarChart3 className="h-5 w-5 text-cyan-200" />
          </div>
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-cyan-200/80">Agent Observability</p>
            <h4 className="mt-1 text-base font-semibold">{view.route.name}</h4>
            <p className="mt-1 text-sm leading-6 text-cyan-50/85">
              Route、Tool、RAG/Memory 与 LLM 路由的本轮总览；用于解释 agent 为什么选择这条执行路径。
            </p>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2 text-[11px] font-semibold">
          {routeChips.slice(0, 7).map((chip) => (
            <span key={chip} className="rounded-full border border-cyan-200/20 bg-black/20 px-3 py-1">
              {chip}
            </span>
          ))}
        </div>
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-4">
        <div className="rounded-xl border border-cyan-200/10 bg-black/20 px-3 py-3">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-cyan-200" />
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-200/75">Route</p>
          </div>
          <p className="mt-2 text-sm font-semibold">{view.route.intent || view.route.name}</p>
          <p className="mt-1 line-clamp-3 text-[11px] leading-5 text-cyan-50/70">{view.route.reason || 'No route reason returned.'}</p>
          {view.route.supervisorConfidenceSource ? (
            <div className="mt-2 border-t border-cyan-200/10 pt-2 text-[10px] leading-5 text-cyan-50/60">
              <p>
                {view.route.supervisorConfidenceSource} · top {view.route.supervisorTopRoute || view.route.name}
                {view.route.supervisorConfidenceMargin !== null
                  ? ` · margin ${Math.round(view.route.supervisorConfidenceMargin * 100)}%`
                  : ''}
              </p>
              <p>
                DAG {view.route.supervisorDagValid ? 'valid' : 'invalid'} · {view.route.supervisorDagNodeCount} nodes
                {view.route.supervisorFailures.length ? ` · failed ${view.route.supervisorFailures.join(', ')}` : ''}
              </p>
            </div>
          ) : null}
        </div>

        <div className="rounded-xl border border-cyan-200/10 bg-black/20 px-3 py-3">
          <div className="flex items-center gap-2">
            <Gauge className="h-4 w-4 text-cyan-200" />
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-200/75">Tool calls</p>
          </div>
          <p className="mt-2 text-sm font-semibold">
            {view.tools.needTool === true ? 'tool path active' : view.tools.needTool === false ? 'no tool required' : 'tool policy unknown'}
          </p>
          <p className="mt-1 text-[11px] leading-5 text-cyan-50/70">
            selected {view.tools.selectedTools.length} · results {view.tools.successCount}/{view.tools.resultCount} · failed {view.tools.failureCount}
          </p>
          {view.tools.selectedTools.length ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {view.tools.selectedTools.slice(0, 4).map((tool) => (
                <span key={tool} className="rounded-full border border-cyan-200/20 bg-black/20 px-2 py-0.5 text-[10px] font-semibold">
                  {tool}
                </span>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-[11px] text-cyan-50/55">{view.tools.source || 'policy idle'}</p>
          )}
        </div>

        <div className="rounded-xl border border-cyan-200/10 bg-black/20 px-3 py-3">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-cyan-200" />
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-200/75">RAG / Memory</p>
          </div>
          <p className="mt-2 text-sm font-semibold">
            {view.rag.material || view.rag.thermoTopDatabase || view.rag.sharedBackend || 'retrieval summary'}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {ragChips.map((chip) => (
              <span key={chip} className="rounded-full border border-cyan-200/20 bg-black/20 px-2 py-0.5 text-[10px] font-semibold">
                {chip}
              </span>
            ))}
          </div>
          {view.rag.materialsTitles.length ? (
            <p className="mt-2 line-clamp-2 text-[11px] leading-5 text-cyan-50/65">
              {view.rag.materialsTitles.slice(0, 3).join(' · ')}
            </p>
          ) : view.rag.materialsGateReason ? (
            <p className="mt-2 line-clamp-2 text-[11px] leading-5 text-cyan-50/65">
              gate: {view.rag.materialsGateReason}
            </p>
          ) : null}
        </div>

        <div className="rounded-xl border border-cyan-200/10 bg-black/20 px-3 py-3">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-cyan-200" />
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-200/75">LLM routing</p>
          </div>
          <p className="mt-2 text-sm font-semibold">
            {latestLlm ? `${latestLlm.tier || 'tier'} · ${latestLlm.model || 'model'}` : 'no real LLM call observed'}
          </p>
          <p className="mt-1 text-[11px] leading-5 text-cyan-50/70">
            calls {view.llm.totalCalls} · fallback {view.llm.fallbackCount} · success {view.llm.successRate !== null ? formatPercent(view.llm.successRate) : '—'}
          </p>
          <p className="mt-1 text-[11px] leading-5 text-cyan-50/60">
            avg latency {view.llm.avgLatencyMs !== null ? `${formatNumber(view.llm.avgLatencyMs, 0)} ms` : '—'}
          </p>
          {latestLearnedReason ? (
            <p className="mt-1 truncate text-[11px] leading-5 text-cyan-50/60">
              {formatLearnedRouteReason(latestLearnedReason)}
            </p>
          ) : null}
        </div>
      </div>

      {view.tools.results.length || view.llm.recentCalls.length || Object.keys(view.llm.tierCounts).length ? (
        <div className="mt-3 grid gap-3 xl:grid-cols-3">
          {view.tools.results.length ? (
            <div className="rounded-xl border border-cyan-200/10 bg-black/15 px-3 py-3">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-200/75">Recent tool observations</p>
              <div className="mt-2 space-y-1 text-[11px] leading-5 text-cyan-50/75">
                {view.tools.results.slice(0, 4).map((result, index) => (
                  <p key={`${result.toolName}-${index}`} className="truncate">
                    {result.success === false ? 'failed' : 'ok'} · {result.toolName || 'tool'} · {result.summary || result.error || 'no summary'}
                  </p>
                ))}
              </div>
            </div>
          ) : null}
          {view.llm.recentCalls.length ? (
            <div className="rounded-xl border border-cyan-200/10 bg-black/15 px-3 py-3">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-200/75">Recent LLM decisions</p>
              <div className="mt-2 space-y-1 text-[11px] leading-5 text-cyan-50/75">
                {view.llm.recentCalls.slice(0, 4).map((call, index) => (
                  <p key={`${call.tier}-${call.model}-${index}`} className="truncate">
                    {call.success === false ? 'failed' : 'ok'} · {call.tier || 'tier'} · {call.capability || 'general'} · {call.durationMs !== null ? `${formatNumber(call.durationMs, 0)} ms` : '—'}
                    {call.fallbackFrom ? ` · fallback ${call.fallbackFrom}` : ''}
                  </p>
                ))}
              </div>
            </div>
          ) : null}
          {Object.keys(view.llm.tierCounts).length || view.tools.skills.length ? (
            <div className="rounded-xl border border-cyan-200/10 bg-black/15 px-3 py-3">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-cyan-200/75">Policy summary</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {Object.entries(view.llm.tierCounts).map(([tier, count]) => (
                  <span key={tier} className="rounded-full border border-cyan-200/20 bg-black/20 px-2 py-0.5 text-[10px] font-semibold">
                    {tier}: {count}
                  </span>
                ))}
                {view.tools.skills.slice(0, 4).map((skill) => (
                  <span key={skill} className="rounded-full border border-indigo-200/20 bg-indigo-500/10 px-2 py-0.5 text-[10px] font-semibold text-indigo-100">
                    skill {skill}
                  </span>
                ))}
              </div>
              {view.rag.thermoStrategy ? (
                <p className="mt-2 text-[11px] leading-5 text-cyan-50/65">thermo strategy: {view.rag.thermoStrategy}</p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function ArtifactResultPanel({
  settings,
  runId,
  routeName,
  statusLabel,
  htmlContent,
  artifacts,
  summary,
  metadata = {},
  isLoading,
  onAiAnalyze,
  onResumeJob,
}: ArtifactResultPanelProps) {
  const [isGeminiAnalyzing, setIsGeminiAnalyzing] = useState(false);
  const [isRecoveryLaunching, setIsRecoveryLaunching] = useState(false);
  const [videoFailures, setVideoFailures] = useState<Record<string, boolean>>({});
  const [selectedArtifactName, setSelectedArtifactName] = useState('');
  const [markdownText, setMarkdownText] = useState('');
  const runPayload = useMemo(() => mergeRunPayload(summary, metadata), [summary, metadata]);
  const agentObservability = useMemo(() => readAgentObservability(runPayload, routeName), [routeName, runPayload]);
  const metrics = summary.metrics && typeof summary.metrics === 'object' ? summary.metrics as Record<string, unknown> : {};
  const resultProfile = runPayload.result_profile && typeof runPayload.result_profile === 'object' ? runPayload.result_profile as ResultProfile : null;
  const physicalQuality = useMemo(() => readPhysicalQuality(runPayload), [runPayload]);
  const lammpsRag = useMemo(() => readLammpsRag(runPayload), [runPayload]);
  const redBlueAudit = useMemo(() => readLammpsRedBlueAudit(runPayload), [runPayload]);
  const dagTimeline = useMemo(() => readDAGTimeline(runPayload), [runPayload]);
  const executionTrust = useMemo(
    () => readExecutionTrust(runPayload, physicalQuality, resultProfile, dagTimeline, artifacts),
    [artifacts, dagTimeline, physicalQuality, resultProfile, runPayload],
  );
  const recoveryControl = useMemo(
    () => readRecoveryControl(runPayload, runId, executionTrust, dagTimeline, artifacts),
    [artifacts, dagTimeline, executionTrust, runId, runPayload],
  );
  const evidenceProvenanceRows = useMemo(
    () => buildEvidenceProvenanceRows(redBlueAudit.evidenceRefs, redBlueAudit.findings, artifacts),
    [artifacts, redBlueAudit.evidenceRefs, redBlueAudit.findings],
  );
  
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
    try {
      await onAiAnalyze(prompt);
    } finally {
      setIsGeminiAnalyzing(false);
    }
  };

  const handleRecoveryClick = async () => {
    setIsRecoveryLaunching(true);
    try {
      if (recoveryControl.sourceJobId && onResumeJob) {
        await onResumeJob(recoveryControl.sourceJobId, {
          message: recoveryControl.prompt,
          checkpoint_id: recoveryControl.lastCheckpointId,
          strategy: recoveryControl.mode,
        });
      } else {
        await onAiAnalyze(recoveryControl.prompt);
      }
    } finally {
      setIsRecoveryLaunching(false);
    }
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
      <div className="w-full max-w-[min(1820px,100%)] my-4 space-y-3">
        <AgentObservabilityCard view={agentObservability} />
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
    const qualityTone = !physicalQuality
      ? 'slate'
      : physicalQuality.scientific_result_passed
        ? 'green'
        : physicalQuality.passed
          ? 'amber'
          : 'red';
    const qualityTitle = !physicalQuality
      ? 'Quality pending'
      : physicalQuality.scientific_result_passed
        ? 'Scientific result passed'
        : physicalQuality.passed
          ? 'Workflow-only result'
          : 'Quality gate failed';
    const QualityIcon = qualityTone === 'green' ? ShieldCheck : qualityTone === 'red' ? ShieldAlert : Gauge;
    const qualityIssues = physicalQuality ? [...(physicalQuality.issues || []), ...(physicalQuality.log_errors || [])] : [];
    const qualityWarnings = physicalQuality ? [...(physicalQuality.warnings || [])] : [];
    const showDAGTimeline = shouldShowDAGTimeline(dagTimeline);
    const dagCompletedNodes = dagTimeline.nodes.filter((node) => node.status === 'completed' || node.status === 'completed_with_fallback').length;
    const dagProblemNodes = dagTimeline.nodes.filter((node) => node.status === 'failed' || node.status === 'timed_out').length;
    const dagDegradationLevel = typeof dagTimeline.degradation.degradation_level === 'string' ? dagTimeline.degradation.degradation_level : 'none';
    const showRedBlueAudit = shouldShowRedBlueAudit(redBlueAudit);
    const blockingFindings = redBlueAudit.findings.filter((finding) => finding.severity === 'blocking').length;
    const auditTone: AuditTone = !showRedBlueAudit
      ? 'slate'
      : redBlueAudit.passed === false || blockingFindings > 0 || redBlueAudit.issues.length > 0
        ? 'red'
        : redBlueAudit.repairHistory.length > 0 || redBlueAudit.advisoryIssues.length > 0 || redBlueAudit.llmBlockingCandidates.length > 0
          ? 'amber'
          : 'green';
    const auditTitle = !showRedBlueAudit
      ? 'Red-Blue audit pending'
      : redBlueAudit.passed === false || blockingFindings > 0
        ? 'Red review found blockers'
        : redBlueAudit.repairHistory.length > 0
          ? 'Blue repair path audited'
          : 'Red review passed';
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

        <div className="px-4 pt-4">
          <AgentObservabilityCard view={agentObservability} />
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
              <div
                data-testid="lammps-execution-trust-card"
                className={`mt-4 rounded-2xl border px-4 py-4 ${trustBadgeClasses(executionTrust.tone)}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex items-start gap-3">
                    <div className="rounded-xl border border-current/20 bg-black/20 p-2">
                      {executionTrust.canUseAsScience ? <ShieldCheck className="h-5 w-5" /> : <ShieldAlert className="h-5 w-5" />}
                    </div>
                    <div>
                      <p className="text-[10px] font-bold uppercase tracking-[0.22em] opacity-75">Execution Trust</p>
                      <h4 className="mt-1 text-base font-semibold">{executionTrust.title}</h4>
                      <p className="mt-1 text-sm leading-6 opacity-90">{executionTrust.statement}</p>
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2 text-[11px] font-semibold">
                    <span data-testid="lammps-execution-mode-badge" className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                      {executionTrust.label}
                    </span>
                    <span data-testid="lammps-science-usable-badge" className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                      science usable: {executionTrust.canUseAsScience ? 'yes' : 'no'}
                    </span>
                    <span className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                      trust: {executionTrust.trustLevel}
                    </span>
                    {typeof executionTrust.confidence === 'number' ? (
                      <span className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                        confidence {Math.round(executionTrust.confidence * 100)}%
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
                  <div>
                    <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Mode facts</p>
                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] font-semibold">
                      {executionTrust.chips.map((chip) => (
                        <span key={chip} className="rounded-full border border-current/20 bg-black/20 px-2.5 py-1">
                          {chip}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Trust evidence</p>
                    {executionTrust.artifacts.length ? (
                      <div className="mt-2 flex flex-wrap gap-2 text-[11px] font-semibold">
                        {executionTrust.artifacts.map((artifact) => (
                          <a
                            key={`${artifact.name}-${artifact.path || artifact.url || ''}`}
                            data-testid="lammps-trust-artifact-link"
                            href={resolveArtifactUrl(settings, artifact.url || artifact.path)}
                            target="_blank"
                            rel="noreferrer"
                            download
                            className="rounded-full border border-current/20 bg-black/20 px-2.5 py-1 transition hover:bg-black/30"
                          >
                            {artifact.name}
                          </a>
                        ))}
                      </div>
                    ) : (
                      <p className="mt-2 text-sm leading-6 opacity-75">等待 quality / trace / lifecycle artifact。</p>
                    )}
                  </div>
                </div>
                {executionTrust.warnings.length ? (
                  <div data-testid="lammps-execution-risk-notes" className="mt-3 rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                    <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Risk notes</p>
                    <ul className="mt-2 space-y-1 text-sm leading-6">
                      {executionTrust.warnings.slice(0, 4).map((item) => (
                        <li key={item}>• {item}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
              {recoveryControl.visible ? (
                <div
                  data-testid="lammps-resume-retry-controls"
                  className={`mt-4 rounded-2xl border px-4 py-4 ${trustBadgeClasses(recoveryControl.tone)}`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-[10px] font-bold uppercase tracking-[0.22em] opacity-75">Resume / Retry Controls</p>
                      <h4 className="mt-1 text-base font-semibold">{recoveryControl.title}</h4>
                      <p className="mt-1 text-sm leading-6 opacity-90">{recoveryControl.statement}</p>
                    </div>
                    <button
                      type="button"
                      data-testid="lammps-recovery-action"
                      onClick={handleRecoveryClick}
                      disabled={isLoading || isRecoveryLaunching}
                      className="inline-flex items-center rounded-full border border-current/25 bg-black/25 px-4 py-2 text-[11px] font-bold uppercase tracking-[0.12em] transition hover:bg-black/35 disabled:cursor-not-allowed disabled:opacity-55"
                    >
                      {isRecoveryLaunching ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Play className="mr-2 h-3.5 w-3.5" />}
                      {isLoading ? 'run is active' : recoveryControl.actionLabel}
                    </button>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 text-[11px] font-semibold">
                    {recoveryControl.chips.map((chip) => (
                      <span key={chip} className="rounded-full border border-current/20 bg-black/20 px-2.5 py-1">
                        {chip}
                      </span>
                    ))}
                  </div>
                  <div className="mt-3 grid gap-3 xl:grid-cols-3">
                    <div data-testid="lammps-recovery-mode" className="rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Mode</p>
                      <p className="mt-2 text-sm font-semibold">{recoveryControl.mode}</p>
                      <p className="mt-1 text-[11px] leading-5 opacity-70">
                        {recoveryControl.resumeSupported ? 'checkpoint context available' : 'no safe checkpoint detected'}
                      </p>
                    </div>
                    <div data-testid="lammps-recovery-checkpoint" className="rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Last checkpoint</p>
                      <p className="mt-2 truncate text-sm font-semibold">{recoveryControl.lastCheckpointId || '—'}</p>
                      <p className="mt-1 truncate text-[11px] opacity-70">
                        failed: {recoveryControl.failedNodes.length ? recoveryControl.failedNodes.join(', ') : 'none'}
                      </p>
                    </div>
                    <div className="rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Reuse plan</p>
                      <p className="mt-2 truncate text-[11px] leading-5 opacity-75">
                        reuse: {recoveryControl.reusedNodes.length ? recoveryControl.reusedNodes.join(', ') : 'auto'}
                      </p>
                      <p className="mt-1 truncate text-[11px] leading-5 opacity-75">
                        rerun: {recoveryControl.invalidatedNodes.length ? recoveryControl.invalidatedNodes.join(', ') : 'agent decides'}
                      </p>
                    </div>
                  </div>
                  {recoveryControl.artifacts.length ? (
                    <div className="mt-3 rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Recovery evidence</p>
                      <div className="mt-2 flex flex-wrap gap-2 text-[11px] font-semibold">
                        {recoveryControl.artifacts.map((artifact) => (
                          <a
                            key={`${artifact.name}-${artifact.path || artifact.url || ''}`}
                            data-testid="lammps-recovery-artifact-link"
                            href={resolveArtifactUrl(settings, artifact.url || artifact.path)}
                            target="_blank"
                            rel="noreferrer"
                            download
                            className="rounded-full border border-current/20 bg-black/20 px-2.5 py-1 transition hover:bg-black/30"
                          >
                            {artifact.name}
                          </a>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  <p data-testid="lammps-recovery-notice" className="mt-3 text-[11px] leading-5 opacity-70">
                    说明：{recoveryControl.sourceJobId
                      ? '当前按钮会调用后端 resume API 创建新的 agent attempt，并携带上一轮 run/checkpoint 上下文。'
                      : '当前历史结果缺少 job_id，按钮会通过普通 agent 对话创建新的 attempt，并携带 checkpoint prompt。'}
                    真实 LAMMPS timestep 原地续跑仍需未来显式 restart 文件协议。
                  </p>
                </div>
              ) : null}
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

              <div
                data-testid="lammps-quality-card"
                className={`mt-4 rounded-2xl border px-4 py-4 ${qualityBadgeClasses(qualityTone)}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex items-start gap-3">
                    <div className="rounded-xl border border-current/20 bg-black/20 p-2">
                      <QualityIcon className="h-5 w-5" />
                    </div>
                    <div>
                      <p className="text-[10px] font-bold uppercase tracking-[0.22em] opacity-75">Physical Quality Gate</p>
                      <h4 className="mt-1 text-base font-semibold" data-testid="lammps-quality-title">{qualityTitle}</h4>
                      <p className="mt-1 text-sm leading-6 opacity-90">
                        {physicalQuality
                          ? physicalQuality.scientific_result_passed
                            ? '真实 LAMMPS 输出已通过质量门，可作为科学结果继续审查。'
                            : physicalQuality.passed
                              ? '质量门允许工作流继续，但该结果不可标记为真实科学结果。'
                              : '质量门发现阻断问题，本轮结果应作为诊断而非科学结论。'
                          : '等待后端返回 quality_report.json。'}
                      </p>
                    </div>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2 text-[11px] font-semibold">
                    <span data-testid="lammps-run-mode-badge" className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                      mode: {physicalQuality?.run_mode || 'pending'}
                    </span>
                    <span data-testid="lammps-scientific-badge" className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                      scientific: {physicalQuality?.scientific_result_passed ? 'yes' : 'no'}
                    </span>
                    {physicalQuality?.synthetic_thermo ? (
                      <span data-testid="lammps-synthetic-badge" className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                        synthetic thermo
                      </span>
                    ) : null}
                  </div>
                </div>

                {physicalQuality ? (
                  <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                    <div className="rounded-xl border border-current/10 bg-black/15 px-3 py-2">
                      <span className="block text-[10px] uppercase tracking-[0.16em] opacity-70">thermo rows</span>
                      <strong className="mt-1 block text-sm">{physicalQuality.thermo_rows ?? '—'}</strong>
                    </div>
                    <div className="rounded-xl border border-current/10 bg-black/15 px-3 py-2">
                      <span className="block text-[10px] uppercase tracking-[0.16em] opacity-70">step coverage</span>
                      <strong className="mt-1 block text-sm">{formatPercent(physicalQuality.step_coverage)}</strong>
                    </div>
                    <div className="rounded-xl border border-current/10 bg-black/15 px-3 py-2">
                      <span className="block text-[10px] uppercase tracking-[0.16em] opacity-70">final temp</span>
                      <strong className="mt-1 block text-sm">{formatNumber(physicalQuality.final_temperature, 1)} K</strong>
                    </div>
                    <div className="rounded-xl border border-current/10 bg-black/15 px-3 py-2">
                      <span className="block text-[10px] uppercase tracking-[0.16em] opacity-70">energy drift</span>
                      <strong className="mt-1 block text-sm">{formatNumber(physicalQuality.normalized_energy_drift, 3)}</strong>
                    </div>
                  </div>
                ) : null}

                {qualityIssues.length || qualityWarnings.length ? (
                  <div className="mt-4 grid gap-3 xl:grid-cols-2">
                    {qualityIssues.length ? (
                      <div data-testid="lammps-quality-issues" className="rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                        <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Issues</p>
                        <ul className="mt-2 space-y-1 text-sm leading-6">
                          {qualityIssues.slice(0, 4).map((item) => (
                            <li key={item}>• {item}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {qualityWarnings.length ? (
                      <div data-testid="lammps-quality-warnings" className="rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                        <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Warnings</p>
                        <ul className="mt-2 space-y-1 text-sm leading-6">
                          {qualityWarnings.slice(0, 4).map((item) => (
                            <li key={item}>• {item}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>

              {showDAGTimeline ? (
                <div
                  data-testid="lammps-dag-timeline-card"
                  className="mt-4 rounded-2xl border border-indigo-400/20 bg-indigo-500/10 px-4 py-4 text-indigo-50"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="flex items-start gap-3">
                      <div className="rounded-xl border border-indigo-300/20 bg-black/20 p-2">
                        <Activity className="h-5 w-5 text-indigo-200" />
                      </div>
                      <div>
                        <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-indigo-200/80">DAG Lifecycle Timeline</p>
                        <h4 className="mt-1 text-base font-semibold" data-testid="lammps-dag-status">
                          {dagTimeline.status || dagTimeline.lifecycleState || 'legacy lifecycle'}
                        </h4>
                        <p className="mt-1 text-sm leading-6 text-indigo-50/85">
                          展示本轮 LAMMPS 预检的拓扑顺序、节点状态、降级策略和 checkpoint；即使回退 legacy preflight，也保留生命周期轨迹。
                        </p>
                      </div>
                    </div>
                    <div className="flex flex-wrap justify-end gap-2 text-[11px] font-semibold">
                      <span className="rounded-full border border-indigo-200/20 bg-black/20 px-3 py-1">
                        nodes: {dagCompletedNodes}/{dagTimeline.nodes.length || '—'}
                      </span>
                      <span className={`rounded-full border px-3 py-1 ${dagProblemNodes ? 'border-rose-300/25 bg-rose-500/10 text-rose-100' : 'border-emerald-300/25 bg-emerald-500/10 text-emerald-100'}`}>
                        problems: {dagProblemNodes}
                      </span>
                      <span className="rounded-full border border-indigo-200/20 bg-black/20 px-3 py-1">
                        {formatDurationSeconds(dagTimeline.durationSeconds)}
                      </span>
                      {dagTimeline.planId ? (
                        <span className="rounded-full border border-indigo-200/20 bg-black/20 px-3 py-1">
                          {dagTimeline.planId}
                        </span>
                      ) : null}
                    </div>
                  </div>

                  {dagTimeline.nodes.length ? (
                    <div className="mt-4 grid gap-2" data-testid="lammps-dag-nodes">
                      {dagTimeline.nodes.map((node, index) => (
                        <div
                          key={node.nodeId}
                          data-testid="lammps-dag-node"
                          className="grid gap-3 rounded-xl border border-indigo-200/10 bg-black/20 px-3 py-3 md:grid-cols-[34px_minmax(0,1.25fr)_minmax(0,1fr)_auto]"
                        >
                          <div className="flex h-8 w-8 items-center justify-center rounded-full border border-indigo-200/20 bg-indigo-500/10 text-xs font-bold text-indigo-100">
                            {index + 1}
                          </div>
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="font-semibold text-indigo-50">{node.label}</p>
                              <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${dagStatusBadgeClasses(node.status)}`}>
                                {node.status}
                              </span>
                              {node.critical ? (
                                <span className="rounded-full border border-rose-300/20 bg-rose-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-rose-100">critical</span>
                              ) : (
                                <span className="rounded-full border border-amber-300/20 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-100">optional</span>
                              )}
                            </div>
                            <p className="mt-1 truncate text-[11px] text-indigo-100/65">
                              id {node.nodeId} · resource {node.resourceClass || 'n/a'} · attempt {node.attempt ?? '—'} · {formatDurationSeconds(node.durationSeconds)}
                            </p>
                            {node.error ? <p className="mt-1 line-clamp-2 text-[11px] text-rose-100/80">{node.error}</p> : null}
                          </div>
                          <div className="min-w-0 text-[11px] leading-5 text-indigo-100/75">
                            <p className="font-bold uppercase tracking-[0.12em] text-indigo-200/70">Depends on</p>
                            <p className="mt-1 truncate">{node.dependencies.length ? node.dependencies.join(' → ') : 'parallel root'}</p>
                            {node.fallback ? <p className="mt-1 truncate">fallback: {node.fallback}</p> : null}
                          </div>
                          <div className="min-w-0 text-[11px] leading-5 text-indigo-100/75 md:text-right">
                            <p className="font-bold uppercase tracking-[0.12em] text-indigo-200/70">Checkpoint</p>
                            <p className="mt-1 truncate">{node.checkpointId || '—'}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p data-testid="lammps-dag-nodes" className="mt-4 rounded-xl border border-indigo-200/10 bg-black/20 px-3 py-3 text-sm leading-6 text-indigo-50/80">
                      本轮没有返回节点级 DAG 结果；可能是 feature flag 关闭或在 legacy preflight 阶段完成。
                    </p>
                  )}

                  <div className="mt-3 grid gap-3 xl:grid-cols-3">
                    <div data-testid="lammps-dag-degradation" className="rounded-xl border border-indigo-200/10 bg-black/20 px-3 py-3">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-indigo-200/75">Degradation</p>
                      <p className="mt-2 text-sm font-semibold">{dagDegradationLevel}</p>
                      <p className="mt-1 text-[11px] leading-5 text-indigo-100/70">
                        continue {formatAuditValue(dagTimeline.degradation.can_continue)}
                        {dagTimeline.fallback ? ` · fallback ${dagTimeline.fallback}` : ''}
                        {typeof dagTimeline.degradation.lifecycle_target === 'string' ? ` · target ${dagTimeline.degradation.lifecycle_target}` : ''}
                      </p>
                    </div>
                    <div data-testid="lammps-lifecycle-transitions" className="rounded-xl border border-indigo-200/10 bg-black/20 px-3 py-3">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-indigo-200/75">Lifecycle transitions</p>
                      {dagTimeline.transitions.length ? (
                        <div className="mt-2 space-y-1 text-[11px] leading-5 text-indigo-100/75">
                          {dagTimeline.transitions.slice(-4).map((transition, index) => (
                            <p key={`${transition.fromState}-${transition.toState}-${index}`} className="truncate">
                              {transition.fromState || 'start'} → {transition.toState || 'state'} · {transition.reason || 'transition'}
                            </p>
                          ))}
                        </div>
                      ) : (
                        <p className="mt-2 text-sm leading-6 text-indigo-50/70">暂无 lifecycle transition。</p>
                      )}
                    </div>
                    <div data-testid="lammps-dag-checkpoints" className="rounded-xl border border-indigo-200/10 bg-black/20 px-3 py-3">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-indigo-200/75">Checkpoints</p>
                      <p className="mt-2 text-sm font-semibold">{dagTimeline.checkpoints.length}</p>
                      <p className="mt-1 truncate text-[11px] text-indigo-100/70">
                        {dagTimeline.checkpoints.length
                          ? String(dagTimeline.checkpoints[dagTimeline.checkpoints.length - 1].checkpoint_id || dagTimeline.checkpoints[dagTimeline.checkpoints.length - 1].stage || 'checkpoint')
                          : 'no checkpoint yet'}
                      </p>
                    </div>
                  </div>
                </div>
              ) : null}

              {showRedBlueAudit ? (
                <div
                  data-testid="lammps-red-blue-card"
                  className={`mt-4 rounded-2xl border px-4 py-4 ${auditBadgeClasses(auditTone)}`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="flex items-start gap-3">
                      <div className="rounded-xl border border-current/20 bg-black/20 p-2">
                        {auditTone === 'green' ? <ShieldCheck className="h-5 w-5" /> : <ShieldAlert className="h-5 w-5" />}
                      </div>
                      <div>
                        <p className="text-[10px] font-bold uppercase tracking-[0.22em] opacity-75">Red-Blue Review & Repair</p>
                        <h4 className="mt-1 text-base font-semibold" data-testid="lammps-red-review-status">{auditTitle}</h4>
                        <p className="mt-1 text-sm leading-6 opacity-90">
                          Red Agent 从参数、脚本、一致性、证据、物理和 artifact 维度审查；Blue Agent 只能通过白名单 patch 修复结构化请求。
                        </p>
                      </div>
                    </div>
                    <div className="flex flex-wrap justify-end gap-2 text-[11px] font-semibold">
                      <span data-testid="lammps-red-review-score" className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                        score: {formatAuditScore(redBlueAudit.score)}
                      </span>
                      <span className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                        findings: {redBlueAudit.findings.length}
                      </span>
                      <span className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                        repairs: {redBlueAudit.repairHistory.length}
                      </span>
                      {redBlueAudit.reviewMode ? (
                        <span className="rounded-full border border-current/20 bg-black/20 px-3 py-1">
                          {redBlueAudit.reviewMode}
                        </span>
                      ) : null}
                    </div>
                  </div>

                  <div className="mt-4 grid gap-3 xl:grid-cols-2">
                    <div data-testid="lammps-red-findings" className="rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Red findings</p>
                      {redBlueAudit.findings.length ? (
                        <div className="mt-2 space-y-2">
                          {redBlueAudit.findings.slice(0, 4).map((finding, index) => (
                            <div
                              key={finding.finding_id || `${finding.dimension || 'finding'}-${index}`}
                              className={`rounded-lg border px-3 py-2 ${auditBadgeClasses(findingTone(finding.severity))}`}
                            >
                              <div className="flex flex-wrap items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em]">
                                <span>{finding.severity || 'unknown'}</span>
                                <span>·</span>
                                <span>{finding.dimension || 'general'}</span>
                                {finding.repairable ? <span>· repairable</span> : null}
                              </div>
                              <p className="mt-1 text-sm leading-6">{finding.message || 'No message returned.'}</p>
                              <p className="mt-1 text-[11px] opacity-75">
                                evidence refs: {finding.evidence_refs?.length || 0}
                                {finding.suggested_action ? ` · action: ${finding.suggested_action}` : ''}
                              </p>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="mt-2 text-sm leading-6 opacity-80">没有阻断或警告 finding。</p>
                      )}
                    </div>

                    <div data-testid="lammps-evidence-refs" className="rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Evidence provenance</p>
                          <p className="mt-1 text-[11px] leading-5 opacity-70">可展开查看 Red finding 依赖的来源、hash、metadata 与匹配 artifact。</p>
                        </div>
                        <span className="rounded-full border border-current/20 bg-black/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.12em]">
                          {evidenceProvenanceRows.length} refs
                        </span>
                      </div>
                      {evidenceProvenanceRows.length ? (
                        <div className="mt-2 space-y-2">
                          {evidenceProvenanceRows.slice(0, 6).map((row, index) => {
                            const evidence = row.evidence;
                            const sourceRef = evidence.source_ref || evidence.content_hash || 'source ref pending';
                            return (
                              <details
                                key={evidence.evidence_id || `${evidence.source_ref || 'evidence'}-${index}`}
                                data-testid="lammps-evidence-drilldown"
                                className="group rounded-lg border border-current/10 bg-black/15 px-3 py-2"
                                open={index < 2}
                              >
                                <summary className="cursor-pointer list-none">
                                  <div className="flex flex-wrap items-start justify-between gap-3">
                                    <div className="min-w-0 flex-1">
                                      <div className="flex flex-wrap items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em] opacity-75">
                                        <span>{evidence.authority || 'authority'}</span>
                                        <span>·</span>
                                        <span data-testid="lammps-evidence-source-type">{evidence.source_type || 'source'}</span>
                                        {evidence.evidence_id ? (
                                          <>
                                            <span>·</span>
                                            <span className="normal-case tracking-normal">{evidence.evidence_id}</span>
                                          </>
                                        ) : null}
                                      </div>
                                      <p className="mt-1 line-clamp-2 text-sm leading-6">{evidence.claim || 'Evidence claim not available.'}</p>
                                      <p className="mt-1 truncate text-[11px] opacity-70">{sourceRef}</p>
                                    </div>
                                    <div className="flex shrink-0 flex-col items-end gap-1 text-[10px] font-bold uppercase tracking-[0.12em]">
                                      <span className="rounded-full border border-current/20 bg-black/20 px-2 py-0.5">
                                        {row.matches.length ? `${row.matches.length} artifacts` : 'metadata only'}
                                      </span>
                                      <span className="opacity-60 group-open:hidden">open</span>
                                    </div>
                                  </div>
                                </summary>

                                <div className="mt-3 space-y-3 border-t border-current/10 pt-3 text-[11px] leading-5">
                                  <div className="grid gap-2 sm:grid-cols-2">
                                    <div className="rounded-lg border border-current/10 bg-black/15 px-2 py-2">
                                      <p className="font-bold uppercase tracking-[0.12em] opacity-65">Source ref</p>
                                      <code className="mt-1 block truncate font-mono opacity-90">{sourceRef}</code>
                                    </div>
                                    <div className="rounded-lg border border-current/10 bg-black/15 px-2 py-2">
                                      <p className="font-bold uppercase tracking-[0.12em] opacity-65">Content hash</p>
                                      <code className="mt-1 block font-mono opacity-90">{compactHash(evidence.content_hash)}</code>
                                    </div>
                                  </div>

                                  {row.sharedMemory ? (
                                    <div
                                      data-testid="lammps-shared-memory-evidence"
                                      className="rounded-lg border border-cyan-300/20 bg-cyan-500/10 px-2 py-2 text-cyan-50"
                                    >
                                      <div className="flex flex-wrap items-center justify-between gap-2">
                                        <p className="font-bold uppercase tracking-[0.12em] opacity-80">Shared memory L1/L2/L3</p>
                                        <div className="flex flex-wrap gap-1.5 text-[10px] font-bold uppercase tracking-[0.12em]">
                                          {row.sharedMemory.memoryId ? (
                                            <span className="rounded-full border border-cyan-200/20 bg-black/20 px-2 py-0.5">
                                              {row.sharedMemory.memoryId}
                                            </span>
                                          ) : null}
                                          <span
                                            data-testid="lammps-shared-memory-locked"
                                            className={`rounded-full border px-2 py-0.5 ${row.sharedMemory.locked ? 'border-amber-200/30 bg-amber-500/20 text-amber-100' : 'border-cyan-200/20 bg-black/20'}`}
                                          >
                                            locked: {row.sharedMemory.locked ? 'yes' : 'no'}
                                          </span>
                                        </div>
                                      </div>
                                      <div className="mt-2 grid gap-2 lg:grid-cols-3">
                                        <div data-testid="lammps-shared-memory-l1" className="rounded-lg border border-cyan-200/10 bg-black/20 px-2 py-2">
                                          <p className="font-bold uppercase tracking-[0.12em] opacity-65">L1 structured</p>
                                          <dl className="mt-1 space-y-1">
                                            {[
                                              { key: 'type', value: row.sharedMemory.l1.item_type },
                                              { key: 'authority', value: row.sharedMemory.l1.authority },
                                              { key: 'subject', value: row.sharedMemory.l1.subject },
                                              { key: 'predicate', value: row.sharedMemory.l1.predicate },
                                              { key: 'value', value: row.sharedMemory.l1.value },
                                              { key: 'unit', value: row.sharedMemory.l1.unit },
                                            ].map((entry) => (
                                              <div key={entry.key} className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-1">
                                                <dt className="font-semibold opacity-65">{entry.key}</dt>
                                                <dd className="truncate">{formatMetadataValue(entry.value)}</dd>
                                              </div>
                                            ))}
                                          </dl>
                                        </div>
                                        <div data-testid="lammps-shared-memory-l2" className="rounded-lg border border-cyan-200/10 bg-black/20 px-2 py-2">
                                          <p className="font-bold uppercase tracking-[0.12em] opacity-65">L2 digest</p>
                                          <p className="mt-1 leading-5 opacity-90">{row.sharedMemory.l2Digest || 'No digest available.'}</p>
                                          {row.sharedMemory.layers.length ? (
                                            <p className="mt-2 text-[10px] uppercase tracking-[0.12em] opacity-60">
                                              layers: {row.sharedMemory.layers.join(' / ')}
                                            </p>
                                          ) : null}
                                        </div>
                                        <div data-testid="lammps-shared-memory-l3" className="rounded-lg border border-cyan-200/10 bg-black/20 px-2 py-2">
                                          <p className="font-bold uppercase tracking-[0.12em] opacity-65">L3 pointer</p>
                                          <div className="mt-1 space-y-1">
                                            <p>
                                              <span className="font-semibold opacity-65">content</span>{' '}
                                              <code className="font-mono">{compactHash(row.sharedMemory.l3Pointer.content_hash)}</code>
                                            </p>
                                            <p>
                                              <span className="font-semibold opacity-65">normalized</span>{' '}
                                              <code className="font-mono">{compactHash(row.sharedMemory.l3Pointer.normalized_hash)}</code>
                                            </p>
                                            {row.sharedMemory.sourceRefs.length ? (
                                              <div>
                                                <p className="font-semibold opacity-65">source refs</p>
                                                <div className="mt-1 flex flex-wrap gap-1">
                                                  {row.sharedMemory.sourceRefs.slice(0, 4).map((source) => (
                                                    <span key={source} className="max-w-full truncate rounded-full border border-cyan-200/20 bg-black/20 px-2 py-0.5">
                                                      {source}
                                                    </span>
                                                  ))}
                                                </div>
                                              </div>
                                            ) : null}
                                          </div>
                                        </div>
                                      </div>
                                    </div>
                                  ) : null}

                                  {evidence.supports?.length ? (
                                    <div>
                                      <p className="font-bold uppercase tracking-[0.12em] opacity-65">Supports</p>
                                      <div className="mt-1 flex flex-wrap gap-1.5">
                                        {evidence.supports.slice(0, 6).map((item) => (
                                          <span key={item} className="rounded-full border border-current/20 bg-black/20 px-2 py-0.5">{item}</span>
                                        ))}
                                      </div>
                                    </div>
                                  ) : null}

                                  {row.relatedFindings.length ? (
                                    <div>
                                      <p className="font-bold uppercase tracking-[0.12em] opacity-65">Linked findings</p>
                                      <ul className="mt-1 space-y-1">
                                        {row.relatedFindings.map((finding) => (
                                          <li key={finding.finding_id || finding.message} className="rounded-lg border border-current/10 bg-black/15 px-2 py-1.5">
                                            <span className="font-semibold">{finding.severity || 'finding'}</span>
                                            <span className="opacity-70"> · {finding.dimension || 'general'} · </span>
                                            <span>{finding.message || 'No message returned.'}</span>
                                          </li>
                                        ))}
                                      </ul>
                                    </div>
                                  ) : null}

                                  {row.metadataEntries.length ? (
                                    <div>
                                      <p className="font-bold uppercase tracking-[0.12em] opacity-65">Metadata</p>
                                      <div className="mt-1 grid gap-1.5 sm:grid-cols-2">
                                        {row.metadataEntries.map((entry) => (
                                          <div key={entry.key} className="min-w-0 rounded-lg border border-current/10 bg-black/15 px-2 py-1.5">
                                            <span className="block truncate font-semibold">{entry.key}</span>
                                            <span className="block truncate opacity-75">{entry.value}</span>
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                  ) : null}

                                  {row.matches.length ? (
                                    <div data-testid="lammps-provenance-drilldown">
                                      <p className="font-bold uppercase tracking-[0.12em] opacity-65">Matched source artifacts</p>
                                      <div className="mt-1 flex flex-wrap gap-1.5">
                                        {row.matches.map((match) => (
                                          <a
                                            key={`${match.artifact.name}-${match.reason}`}
                                            data-testid="lammps-evidence-source-link"
                                            href={resolveArtifactUrl(settings, match.artifact.url || match.artifact.path)}
                                            target="_blank"
                                            rel="noreferrer"
                                            download
                                            className="rounded-full border border-current/20 bg-black/20 px-2 py-1 font-semibold transition hover:bg-black/30"
                                            title={match.reason}
                                          >
                                            {match.artifact.name}
                                          </a>
                                        ))}
                                      </div>
                                      <p className="mt-1 opacity-60">匹配依据：{row.matches.map((match) => `${match.artifact.name}(${match.reason})`).join(' · ')}</p>
                                    </div>
                                  ) : (
                                    <p data-testid="lammps-provenance-drilldown" className="rounded-lg border border-current/10 bg-black/15 px-2 py-2 opacity-70">
                                      该 evidence 没有直接匹配到下载 artifact；provenance 已保留在 red_review_post.json / trace.json 中。
                                    </p>
                                  )}
                                </div>
                              </details>
                            );
                          })}
                          {evidenceProvenanceRows.length > 6 ? (
                            <p className="text-[11px] opacity-65">还有 {evidenceProvenanceRows.length - 6} 条 evidence 可在 red_review_post.json 中查看。</p>
                          ) : null}
                        </div>
                      ) : (
                        <p className="mt-2 text-sm leading-6 opacity-80">没有单独返回 evidence ref；可下载 red_review_post.json 查看完整审计。</p>
                      )}
                    </div>
                  </div>

                  {redBlueAudit.repairHistory.length ? (
                    <div data-testid="lammps-blue-patch-history" className="mt-3 rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Blue patch history</p>
                      <div className="mt-2 space-y-2">
                        {redBlueAudit.repairHistory.slice(-3).map((entry, index) => {
                          const policy = entry.policy_report;
                          const patch = entry.patch;
                          const convergence = readObject(entry.convergence_report);
                          const accepted = policy?.accepted === true;
                          const rejectedCount = policy?.rejected_operations?.length || 0;
                          const appliedCount = policy?.applied_operations?.length || 0;
                          const diffRows = buildBluePatchDiffRows(entry);
                          return (
                            <div key={`${entry.stage || 'repair'}-${index}`} className={`rounded-lg border px-3 py-2 ${auditBadgeClasses(accepted ? 'green' : rejectedCount ? 'red' : 'amber')}`}>
                              <div className="flex flex-wrap items-center gap-2 text-[10px] font-bold uppercase tracking-[0.14em]">
                                <span>{entry.entry_type || 'repair_attempt'}</span>
                                <span>·</span>
                                <span>{entry.stage || 'stage unknown'}</span>
                                <span>·</span>
                                <span>{accepted ? 'accepted' : 'not accepted'}</span>
                              </div>
                              <p className="mt-1 text-sm leading-6">
                                {patch?.source || 'patch source unknown'} · ops {patch?.operations?.length || 0} · applied {appliedCount} · rejected {rejectedCount}
                              </p>
                              <p className="mt-1 text-[11px] opacity-75">
                                risk {formatAuditValue(patch?.risk || policy?.risk)}
                                {policy?.termination_reason ? ` · stop: ${policy.termination_reason}` : ''}
                                {typeof convergence.termination_reason === 'string' && convergence.termination_reason
                                  ? ` · convergence: ${convergence.termination_reason}`
                                  : ''}
                              </p>
                              {diffRows.length ? (
                                <div data-testid="lammps-blue-patch-diff" className="mt-3 overflow-hidden rounded-lg border border-current/10 bg-black/20">
                                  <div className="grid grid-cols-[0.95fr_0.75fr_1fr_1fr] gap-0 border-b border-current/10 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.14em] opacity-75">
                                    <span>Field</span>
                                    <span>Status</span>
                                    <span>Before</span>
                                    <span>After</span>
                                  </div>
                                  <div className="divide-y divide-current/10">
                                    {diffRows.slice(0, 6).map((row, rowIndex) => (
                                      <div key={`${row.path}-${row.op}-${rowIndex}`} className="grid grid-cols-[0.95fr_0.75fr_1fr_1fr] gap-0 px-3 py-2 text-[11px] leading-5">
                                        <div className="min-w-0">
                                          <p className="truncate font-semibold">{row.path}</p>
                                          <p className="truncate opacity-65">{row.op}</p>
                                        </div>
                                        <div>
                                          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${
                                            row.status === 'rejected'
                                              ? 'border-rose-400/25 bg-rose-500/10 text-rose-100'
                                              : row.status === 'changed' || row.status === 'applied'
                                                ? 'border-emerald-400/25 bg-emerald-500/10 text-emerald-100'
                                                : 'border-amber-400/25 bg-amber-500/10 text-amber-100'
                                          }`}>
                                            {row.status}
                                          </span>
                                          {row.reason ? <p className="mt-1 line-clamp-2 opacity-65">{row.reason}</p> : null}
                                        </div>
                                        <code className="min-w-0 truncate rounded bg-black/20 px-2 py-1 font-mono opacity-80">{formatPatchValue(row.before)}</code>
                                        <code className="min-w-0 truncate rounded bg-black/20 px-2 py-1 font-mono opacity-90">{formatPatchValue(row.after)}</code>
                                      </div>
                                    ))}
                                  </div>
                                  {diffRows.length > 6 ? (
                                    <p className="border-t border-current/10 px-3 py-2 text-[11px] opacity-65">
                                      还有 {diffRows.length - 6} 个字段变化可在 repair_history.json 中查看。
                                    </p>
                                  ) : null}
                                </div>
                              ) : (
                                <p data-testid="lammps-blue-patch-diff" className="mt-2 text-[11px] opacity-65">
                                  本次 repair 没有字段 mutation；可能是 VERIFY-only、guard stop 或解析失败。
                                </p>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}

                  {redBlueAudit.parseAudits.length || redBlueAudit.issues.length || redBlueAudit.advisoryIssues.length || redBlueAudit.llmBlockingCandidates.length ? (
                    <div data-testid="lammps-parse-audit" className="mt-3 grid gap-3 xl:grid-cols-2">
                      {redBlueAudit.parseAudits.length ? (
                        <div className="rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                          <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">JSON fallback audit</p>
                          <div className="mt-2 space-y-1 text-sm leading-6">
                            {redBlueAudit.parseAudits.slice(0, 4).map((audit, index) => (
                              <p key={`${String(audit.payload_type || audit.source || 'audit')}-${index}`}>
                                {String(audit.payload_type || audit.source || 'parse')} · mode {formatAuditValue(audit.parse_mode)} · success {formatAuditValue(audit.success)}
                              </p>
                            ))}
                          </div>
                        </div>
                      ) : null}
                      {redBlueAudit.issues.length || redBlueAudit.advisoryIssues.length || redBlueAudit.llmBlockingCandidates.length ? (
                        <div className="rounded-xl border border-current/10 bg-black/15 px-3 py-3">
                          <p className="text-[10px] font-bold uppercase tracking-[0.18em] opacity-75">Review notes</p>
                          <ul className="mt-2 space-y-1 text-sm leading-6">
                            {[...redBlueAudit.issues, ...redBlueAudit.llmBlockingCandidates, ...redBlueAudit.advisoryIssues].slice(0, 5).map((item) => (
                              <li key={item}>• {item}</li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
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
