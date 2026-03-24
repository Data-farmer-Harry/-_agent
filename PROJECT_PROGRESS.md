# Project Progress

## 2026-03-24 - Agent Architecture Refocus: LLM Orchestrates, Python Executes

### Round Goal
- Refocus the repo around the actual intended product shape:
  - the project is a materials-research agent, not a fixed workflow page
  - the LLM should decide what the user is trying to do
  - Python should remain the real execution engine for phase-diagram generation
- Ensure the three current routes all work end to end before closing the task:
  - `phase_diagram.generate`
  - `phase_diagram.recognize`
  - `phase_diagram.redraw_html`

### Architecture Changes
- Added an explicit LLM-driven task decider in [agent_decision_service.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/agent_decision_service.py).
  - The agent now chooses among:
    - `phase_diagram.generate`
    - `phase_diagram.recognize`
    - `phase_diagram.redraw_html`
    - `lammps.generate`
    - `generic.unknown`
  - The decider records:
    - `intent`
    - `decision_source`
    - `decision_confidence`
- Kept chat input assembly in [agent_chat_service.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/agent_chat_service.py), but moved real route choice out of chat preprocessing and into the runtime routing path.
- The core generation semantics are now much clearer:
  - `phase_diagram.generate` means:
    - LLM interprets request
    - LLM writes Python
    - local Python executes the generated code
    - review/repair/fallback guardrails run before the artifact is accepted
- Added and stabilized the HTML redraw capability:
  - [phase_diagram_html_service.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/phase_diagram_html_service.py)
  - [phase_diagram_html_redraw_tool.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/tools/phase_diagram_html_redraw_tool.py)
  - [phase_diagram_html_review_tool.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/tools/phase_diagram_html_review_tool.py)
- Extended the frontend and runtime contracts so the agent intent and route choice are visible in the UI:
  - [AgentWorkbench.tsx](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/app/AgentWorkbench.tsx)
  - [useAgentChat.ts](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/features/chat/useAgentChat.ts)
  - [TracePanel.tsx](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/features/trace/TracePanel.tsx)
  - [ResultViewer.tsx](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/features/result/ResultViewer.tsx)

### Important Fixes
- Fixed a real HTML redraw review bug in [phase_diagram_html_service.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/phase_diagram_html_service.py).
  - Root cause:
    - long HTML pages were being truncated before review
    - the LLM reviewer then falsely concluded the page was incomplete
  - Fix:
    - long HTML now stays on heuristic contract review instead of sending a cut-off preview to the model
    - HTML normalization also now enforces the standardized `<main id="phase-diagram-agent-result">` root more consistently
- Strengthened browser smoke automation in [frontend_smoke.mjs](/Users/harry/Desktop/相图计算/phase_diagram_agent/scripts/frontend_smoke.mjs).
  - It now verifies:
    - status chips
    - route/tool selection
    - non-placeholder run id
    - rendered iframe content
  - This prevents false positives where the page merely happened to contain a route name somewhere in static text.

### Tests And Verification
- Backend test suite passed:
  - `./.venv/bin/python -m unittest discover -t . -s tests -p 'test_*.py'`
  - Result: `42` tests passed
- Frontend production build passed:
  - `npm run build`
- Added regression coverage for long HTML review handling in [test_backend_contracts.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/tests/test_backend_contracts.py).

### Live Smokes
- Backend generate smoke passed:
  - route: `phase_diagram.generate`
  - decision source: `llm_agent_decider`
  - codegen source: `llm_codegen`
  - selected tool: `phase_diagram_codegen`
  - final status: `success=true`
- Backend recognize smoke passed:
  - route: `phase_diagram.recognize`
  - selected tool: `phase_diagram_image_parse`
  - deliverable: `json`
  - final status: `success=true`
- Backend HTML redraw smoke passed after the review fix:
  - route: `phase_diagram.redraw_html`
  - selected tool: `phase_diagram_html_redraw`
  - review mode: `heuristic_redraw_review`
  - final status: `success=true`
- Real frontend browser smoke passed for HTML redraw:
  - final DOM showed:
    - `completed`
    - `ready`
    - `phase_diagram.redraw_html`
    - `phase_diagram_html_redraw`
  - rendered iframe length: `8576`
- Real frontend browser smoke passed for Python generation:
  - final DOM showed:
    - `completed`
    - `ready`
    - `phase_diagram.generate`
    - `phase_diagram_codegen`
  - rendered iframe length: `33870`

### Current Result
- The repo is now aligned with the intended product story:
  - LLM decides what the user wants
  - Python executes real computation/generation for phase-diagram tasks
  - multimodal recognition and HTML redraw are separate first-class tool chains
  - the frontend exposes the route, tool, and review path instead of acting like a hidden workflow

## 2026-03-24 - Agent Chat 404 Recovery And Full-Flow Hardening

### Round Goal
- Fix the reported frontend chat failure where the UI showed:
  - stream fallback message
  - final `{"detail":"Not Found"}`
- Verify the full materials-agent flow end to end before closing the task.

### Root Cause
- The React frontend persists `apiBaseUrl` in browser local storage.
- Earlier local runs used `http://127.0.0.1:8011`.
- The current `useLocalSettings` hook only normalized an old `8001` value, not `8011`.
- A stale backend on `8011` can still answer legacy endpoints like `/api/generate-and-run`, but return `404` for the newer agent endpoints:
  - `/api/agent/catalog`
  - `/api/agent/manifest`
  - `/api/agent/chat`
  - `/api/agent/chat/stream`

### Frontend Changes
- Added backend capability probing in [api.ts](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/services/api.ts).
  - New `probeAgentBackend(...)` probes:
    - `/api/agent/manifest`
    - `/api/agent/catalog`
    - `/api/health`
- Hardened local settings in [useLocalSettings.ts](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/features/settings/useLocalSettings.ts).
  - The app now probes common local candidates like `8000` and `8011`.
  - It auto-switches to the best backend that actually supports the agent routes.
  - It exposes a connection state:
    - `resolving`
    - `ready`
    - `agent-unavailable`
    - `offline`
- Updated the main workbench in [AgentWorkbench.tsx](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/app/AgentWorkbench.tsx).
  - Latest-result loading and catalog loading now wait until an agent-capable backend is confirmed.
  - The send path is blocked when the connected backend does not support the agent routes.
  - Header now shows connection status next to run status.
- Updated the chat panel in [AgentConversationPanel.tsx](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/features/chat/AgentConversationPanel.tsx).
  - Shows a visible backend connection hint.
  - Disables the send button until an agent-capable backend is ready.
- Added UI styles for connection state in [styles.css](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/shared/styles.css).
- Improved user-facing error messaging in [useAgentChat.ts](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/features/chat/useAgentChat.ts).
  - `Not Found` is now translated into a direct diagnosis that the frontend is likely connected to an old backend or wrong port.

### Backend Changes
- Added route-registration and stream-contract tests in [test_http_routes.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/tests/test_http_routes.py).
  - Verifies FastAPI actually registers:
    - `/api/agent/catalog`
    - `/api/agent/manifest`
    - `/api/agent/chat`
    - `/api/agent/chat/stream`
  - Verifies the stream endpoint emits `run_started` and `run_completed`.

### Verification
- Frontend build passed:
  - `npm run build`
- Backend tests passed:
  - `./.venv/bin/python -m unittest discover -t . -s tests -p 'test_*.py'`
  - Result: `38` tests passed
- Agent text chat smoke passed with the exact reported shape of request:
  - request: `Fe-Cu`, `300K-2700K`
  - route: `phase_diagram.generate`
  - selected tool: `phase_diagram_codegen`
  - termination reason: `completed`
  - review: `passed=True`, `confidence=0.92`
  - html generated: `True`
- Agent stream smoke passed:
  - contains `event: run_started`
  - contains `event: run_completed`
  - contains `phase_diagram_result_review`

### Current Result
- The repo now guards against stale backend ports instead of blindly trusting old local storage state.
- The reported `{"detail":"Not Found"}` failure mode is now both:
  - less likely to happen
  - much easier to diagnose if a truly wrong backend is still running

### Known Bounds
- The frontend auto-detection is optimized for local development and common loopback ports.
- If a user manually points the UI at a custom backend that is reachable but does not expose the new agent routes, the UI will now block sending and explain why instead of failing mid-run.

## 2026-03-24 - Fixed Ports And Real Frontend E2E Recovery

### Round Goal
- Remove stale local services.
- Fix the frontend so it no longer mistakes the frontend server for the backend.
- Lock the working environment to fixed ports and verify the flow in a real browser context.

### What Was Running
- Stale frontend process on `4176`
  - cwd: [frontend-react](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react)
- Stale backend process on `8011`
  - cwd: [backend](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend)

### Root Cause
- The previous frontend probe only checked HTTP status codes.
- A frontend/dev server can return `200` HTML for unknown `GET` paths, which made the app falsely believe `localhost:4176` was a valid agent backend.
- The browser then attempted `POST /api/agent/chat` against the wrong server and failed with `HTTP 404`.

### Changes
- Tightened backend probing in [api.ts](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/services/api.ts).
  - Probes must now return valid JSON with the expected shape.
  - HTML fallback pages can no longer pass as agent APIs.
- Locked frontend backend selection in [useLocalSettings.ts](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/features/settings/useLocalSettings.ts).
  - Fixed backend target is now `http://127.0.0.1:8000`.
  - Stored values like `http://localhost:4176` are coerced back to the fixed backend on reload.
  - Only `127.0.0.1:8000` and `localhost:8000` are accepted variants.
- Killed stale processes on ports `4176` and `8011`.
- Started the verified environment on fixed ports:
  - frontend: `http://127.0.0.1:5174`
  - backend: `http://127.0.0.1:8000`

### Verification
- Frontend build passed:
  - `npm run build`
- Backend test suite passed:
  - `./.venv/bin/python -m unittest discover -t . -s tests -p 'test_*.py'`
  - Result: `38` tests passed
- Real backend health check passed:
  - `GET /api/health` on `8000` returned `200`
- Real backend manifest check passed:
  - `routes=9`
  - `tools=11`
- Real backend chat check passed:
  - route: `phase_diagram.generate`
  - selected tool: `phase_diagram_codegen`
- Real backend stream check passed:
  - emitted `run_started`
  - emitted step events
  - includes review tool
- Real frontend browser automation passed in headless Chrome against [http://127.0.0.1:5174](http://127.0.0.1:5174)
  - intentionally injected stale localStorage backend `http://localhost:4176`
  - page corrected itself to `http://127.0.0.1:8000`
  - button became enabled
  - click created a real run id: `455da9dd5544`
  - final DOM state showed:
    - `completed`
    - `ready`
    - `phase_diagram.generate`
    - `phase_diagram_codegen`
    - `当前结果通过了 agent 自检`
    - `iframeLength = 124527`
    - `HTTP 404 = false`

### Current Working Ports
- Backend: `127.0.0.1:8000`
- Frontend: `127.0.0.1:5174`

## 2026-03-24 - Backend .env Support And LLM Config Recovery

### Round Goal
- Stop relying on one-off shell `export`.
- Make backend LLM configuration persistent across restarts.
- Restore the DashScope config the user had been using before the stale backend cleanup.

### Changes
- Added first-class backend `.env` loading in [config.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/config.py).
  - Backend now reads `backend/.env` automatically at startup.
  - Process environment variables still override `.env`, so CI and temporary overrides keep working.
- Added ignore rules for local env files in [.gitignore](/Users/harry/Desktop/相图计算/phase_diagram_agent/.gitignore).
- Added example config in [backend/.env.example](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/.env.example).
- Added real local config in `backend/.env` for the current machine.
- Added config tests in [test_config_env.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/tests/test_config_env.py).
- Added exact codegen source labeling in:
  - [codegen_service.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/codegen_service.py)
  - [phase_diagram_codegen_tool.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/tools/phase_diagram_codegen_tool.py)
  - current values include:
    - `llm_codegen`
    - `llm_codegen_repaired`
    - `placeholder_fallback`
    - `placeholder_forced`
- Updated docs:
  - [README.md](/Users/harry/Desktop/相图计算/phase_diagram_agent/README.md)
  - [backend/README.md](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/README.md)

### Verification
- Backend tests passed after config refactor:
  - `40` tests passed
- Frontend build still passed.
- Real config verification succeeded:
  - `llm_api_base_url = https://coding.dashscope.aliyuncs.com/v1`
  - `llm_model = qwen3-coder-plus`
  - `llm_key_present = true`
- Real LLM request interpretation succeeded:
  - `planning_source = llm_request_interpreter`
- Real live agent review succeeded in using LLM guardrails:
  - `review_mode = llm_plus_heuristic_guardrail`

### Important Current Limitation
- The LLM configuration problem is fixed.
- However, for the current `Fe-Cu` request, code generation still falls back:
  - `codegen_source = placeholder_fallback`
  - direct inspection showed the raw LLM code was returned, but it failed one quality check:
    - `Fe-Cu binary output is using an Al-Cu-style intermetallic/eutectic topology; replace it with Fe-Cu-like terminal solids and a limited-solubility two-solid-region topology.`
- So the current state is:
  - request understanding: LLM is active
  - review: LLM is active
  - code generation: LLM is configured, but this specific prompt/system still trips the quality guardrail and falls back

## 2026-03-24 - Agent-First Refactor Around LLM Decision And Python Execution

### Round Goal
- Correct the project architecture so it matches the intended product:
  - the LLM decides what the user is asking for
  - Python is the execution engine for compute/generation requests
  - multimodal recognition is a separate agent path
  - explanation/lecture material can go to an HTML redraw path
- Keep the current runtime trace model, but stop treating the project like a fixed workflow page.

### Backend Changes
- Added a real agent decision layer in [agent_decision_service.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/agent_decision_service.py).
  - Decision outputs now include:
    - `route_name`
    - `workspace_id`
    - `intent`
    - `reason`
    - `source`
    - `confidence`
  - Supported decision targets now include:
    - `phase_diagram.generate`
    - `phase_diagram.recognize`
    - `phase_diagram.redraw_html`
    - `phase_diagram.from_image` (legacy)
    - `lammps.generate`
- Refactored [agent_chat_service.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/agent_chat_service.py).
  - Chat no longer hardcodes “image means from_image, text means generate”.
  - It first asks the decision layer what the task is, then builds the matching structured payload.
- Refactored [task_router.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/task_router.py).
  - Runtime routing is now decision-driven instead of keyword-only.
- Upgraded the route catalog/manifest contract in [agent_manifest.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/agent_manifest.py).
  - `phase_diagram.recognize` is now a dedicated recognition route with `json` deliverable.
  - `phase_diagram.from_image` remains as the legacy “recognize + rebuild page” route.
  - `phase_diagram.redraw_html` is now a first-class route with its own HTML tool chain.
- Added and integrated HTML redraw services/tools:
  - [phase_diagram_html_service.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/phase_diagram_html_service.py)
  - [phase_diagram_html_redraw_tool.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/tools/phase_diagram_html_redraw_tool.py)
  - [phase_diagram_html_review_tool.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/tools/phase_diagram_html_review_tool.py)
- Centralized LLM transport in [llm_client.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/llm_client.py).
  - `CodeGenerationService`
  - `PhaseDiagramAgentService`
  - `PhaseDiagramImageService`
  now share the same LLM client abstraction instead of each owning bespoke HTTP glue.
- Extended runtime metadata in [agent_runtime.py](/Users/harry/Desktop/相图计算/phase_diagram_agent/backend/app/services/agent_runtime.py).
  - Trace metadata now carries:
    - `intent`
    - `decision_source`
    - `decision_confidence`
    - sanitized `agent_decision`
    - sanitized `html_redraw_request`

### Frontend Changes
- Updated the React contract so the frontend understands the new agent routes/tools:
  - [types/api.ts](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/types/api.ts)
  - [useAgentChat.ts](/Users/harry/Desktop/相图计算/phase_diagram_agent/frontend-react/src/features/chat/useAgentChat.ts)
- The chat timeline now narrates agent intent first, then tool execution.
- The result/view layer already supports:
  - `phase_diagram.generate`
  - `phase_diagram.recognize`
  - `phase_diagram.redraw_html`
- Added reusable browser smoke scripts:
  - [frontend_smoke.mjs](/Users/harry/Desktop/相图计算/phase_diagram_agent/scripts/frontend_smoke.mjs)
  - [frontend_snapshot.mjs](/Users/harry/Desktop/相图计算/phase_diagram_agent/scripts/frontend_snapshot.mjs)

### Verification
- Backend unit/integration tests:
  - `./.venv/bin/python -m unittest discover -t . -s tests -p 'test_*.py'`
  - result: `42` tests passed
- Frontend build:
  - `npm run build`
  - passed
- Direct backend smoke through app entrypoints:
  - `generate`
    - `success = True`
    - `route = phase_diagram.generate`
    - `intent = python_generation`
    - `selected_tool = phase_diagram_codegen`
    - `has_html = True`
  - `recognize`
    - `success = True`
    - `route = phase_diagram.recognize`
    - `intent = image_recognition`
    - `selected_tool = phase_diagram_image_parse`
    - `has_image_spec = True`
  - `redraw`
    - `success = True`
    - `route = phase_diagram.redraw_html`
    - `intent = html_redraw`
    - `selected_tool = phase_diagram_html_redraw`
    - `has_html = True`
- Real frontend browser smoke against `http://127.0.0.1:5174`
  - final DOM state showed:
    - `completed`
    - `ready`
    - `phase_diagram.redraw_html`
    - `phase_diagram_html_redraw`
    - `iframeLength = 12480`
  - timeline showed:
    - agent decision from `llm_agent_decider`
    - route `phase_diagram.redraw_html`
    - tools `phase_diagram_html_redraw -> phase_diagram_html_review`

### Current Runtime Ports
- Backend: `127.0.0.1:8000`
- Frontend: `127.0.0.1:5174`
