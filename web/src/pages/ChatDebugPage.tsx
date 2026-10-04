import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  chatDebugApi, streamChat,
  type PromptTemplateSummary, type StreamChatBody, type TemplateMetaBadge,
  type ToolMeta, type ToolTraceEntry,
} from '../api/chatDebug';

/** 变量填值表单的字符串状态（bool 用 checkbox，list 用逗号分隔）。 */
type VarDraft = Record<string, string | boolean>;

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

function emptyDraft(schema: PromptTemplateSummary['variables_schema']): VarDraft {
  const draft: VarDraft = {};
  for (const [name, spec] of Object.entries(schema ?? {})) {
    const type = spec?.type ?? 'str';
    if (type === 'bool') draft[name] = Boolean(spec?.default);
    else if (spec?.default !== undefined && spec?.default !== null) draft[name] = String(spec.default);
    else draft[name] = '';
  }
  return draft;
}

function coerceVariables(schema: PromptTemplateSummary['variables_schema'],
                         draft: VarDraft): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, spec] of Object.entries(schema ?? {})) {
    const type = spec?.type ?? 'str';
    const raw = draft[name];
    if (raw === undefined || raw === '') {
      if (spec?.default !== undefined && spec?.default !== null) out[name] = spec.default;
      continue;
    }
    if (type === 'bool') out[name] = raw === true || raw === 'true';
    else if (type === 'int') out[name] = parseInt(String(raw), 10);
    else if (type === 'float') out[name] = parseFloat(String(raw));
    else if (type === 'list') {
      out[name] = String(raw).split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    } else out[name] = String(raw);
  }
  return out;
}

/** 解析 P1-01 「用此模板发起试跑」深链: ?template=&version=&variables=<json> */
function parseLaunchParams(params: URLSearchParams):
  { template: string; version: number | null; variables: Record<string, unknown> } {
  const rawVars = params.get('variables');
  let variables: Record<string, unknown> = {};
  if (rawVars) {
    try {
      const parsed = JSON.parse(rawVars);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) variables = parsed;
    } catch { /* 深链参数非法时忽略 */ }
  }
  const version = params.get('version');
  return {
    template: params.get('template') ?? '',
    version: version ? Number(version) : null,
    variables,
  };
}

export function ChatDebugPage() {
  const [params] = useSearchParams();
  const launch = useMemo(() => parseLaunchParams(params), [params]);

  const [prompts, setPrompts] = useState<PromptTemplateSummary[]>([]);
  const [tools, setTools] = useState<ToolMeta[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<string>(launch.template);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(launch.version);
  const [varDraft, setVarDraft] = useState<VarDraft>({});
  const [boundTools, setBoundTools] = useState<string[]>([]);
  const [model, setModel] = useState('default');

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [trace, setTrace] = useState<ToolTraceEntry[]>([]);
  const [streamMeta, setStreamMeta] = useState<TemplateMetaBadge | null>(null);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // 初次加载模板库 + 工具注册表；命中试跑深链时自动填充变量。
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [promptList, toolList] = await Promise.all([
          chatDebugApi.listPrompts(),
          chatDebugApi.discoverTools(),
        ]);
        if (cancelled) return;
        setPrompts(Array.isArray(promptList) ? promptList : []);
        setTools(toolList?.tools ?? []);
        if (launch.template) {
          const tpl = (Array.isArray(promptList) ? promptList : [])
            .find((p) => p.name === launch.template);
          if (tpl) {
            const draft = emptyDraft(tpl.variables_schema);
            for (const [k, v] of Object.entries(launch.variables)) {
              if (k in draft) draft[k] = typeof draft[k] === 'boolean' ? Boolean(v) : String(v);
            }
            setVarDraft(draft);
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeTemplate = useMemo(
    () => prompts.find((p) => p.name === selectedTemplate) ?? null,
    [prompts, selectedTemplate],
  );

  function onSelectTemplate(name: string) {
    setSelectedTemplate(name);
    setSelectedVersion(null);
    const tpl = prompts.find((p) => p.name === name);
    setVarDraft(tpl ? emptyDraft(tpl.variables_schema) : {});
  }

  const send = useCallback(async () => {
    const content = input.trim();
    if (!content || streaming) return;
    setError(null);
    setMessages((m) => [...m, { role: 'user', content }]);
    setInput('');
    setStreaming(true);
    setTrace([]);
    setStreamMeta(null);

    const body: StreamChatBody = {
      prompt: content,
      model,
      task_id: 'chat-debug',
      tools: boundTools,
    };
    if (activeTemplate) {
      body.template_name = activeTemplate.name;
      body.template_version = selectedVersion ?? activeTemplate.latest_version;
      body.variables = coerceVariables(activeTemplate.variables_schema, varDraft);
    }

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let assistant = '';
    const traces: ToolTraceEntry[] = [];

    const onEvent = (event: string, data: Record<string, unknown>) => {
      if (event === 'message_start') {
        const tpl = data.template as TemplateMetaBadge | undefined;
        if (tpl) setStreamMeta(tpl);
      } else if (event === 'delta') {
        assistant += String(data.text ?? '');
        setMessages((m) => {
          const next = [...m];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant') last.content = assistant;
          else next.push({ role: 'assistant', content: assistant });
          return next;
        });
      } else if (event === 'tool_call' || event === 'tool_result') {
        const idx = Number(data.index ?? traces.length);
        const entry: ToolTraceEntry = traces[idx] ?? {
          index: idx, name: String(data.name ?? ''), arguments: {},
        };
        if (event === 'tool_call') {
          entry.name = String(data.name ?? entry.name);
          entry.arguments = (data.arguments as Record<string, unknown>) ?? {};
        } else {
          entry.call_id = data.call_id ? String(data.call_id) : entry.call_id;
          entry.result = data.result;
          entry.executed = Boolean(data.executed ?? true);
        }
        traces[idx] = { ...entry };
        setTrace([...traces]);
      } else if (event === 'error') {
        setError(String(data.message ?? 'stream error'));
      }
    };

    try {
      await streamChat(body, onEvent, ctrl.signal);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }, [input, streaming, model, boundTools, activeTemplate, selectedVersion, varDraft]);

  const schemaEntries = Object.entries(activeTemplate?.variables_schema ?? {});

  return (
    <div data-testid="chat-debug-root">
      <div className="page-head">
        <h2>Chat 调试预览</h2>
        <div className="row" data-testid="chat-debug-badges">
          {streamMeta && (
            <>
              <span className="badge ok" data-testid="badge-template">
                {streamMeta.name} v{streamMeta.version}
              </span>
              <span className="badge" title="审计链一致（P1-01 §4）">
                variables_hash: {streamMeta.variables_hash.slice(0, 12)}
              </span>
            </>
          )}
          {boundTools.length > 0 && (
            <span className="badge accent" data-testid="badge-tools">
              工具 × {boundTools.length}
            </span>
          )}
        </div>
      </div>

      <div className="grid cols-2" style={{ gridTemplateColumns: '340px 1fr', alignItems: 'start' }}>
        {/* 左侧：配置区（模板 + 变量 + 工具 + 模型） */}
        <div className="card" data-testid="chat-debug-config">
          <strong>配置</strong>
          <div style={{ marginTop: '0.75rem' }}>
            <label htmlFor="tpl-select">提示词模板</label>
            <select
              id="tpl-select"
              data-testid="template-select"
              value={selectedTemplate}
              onChange={(e) => onSelectTemplate(e.target.value)}
            >
              <option value="">（不使用模板）</option>
              {prompts.map((p) => (
                <option key={p.name} value={p.name} disabled={!p.is_active}>
                  {p.name} v{p.latest_version}{p.is_active ? '' : '（未启用）'}
                </option>
              ))}
            </select>
          </div>

          {schemaEntries.length > 0 && (
            <fieldset data-testid="variable-form" style={{ marginTop: '0.75rem' }}>
              <legend>变量填值</legend>
              {schemaEntries.map(([name, spec]) => (
                <div key={name} style={{ marginBottom: '0.5rem' }}>
                  <label htmlFor={`var-${name}`}>
                    {name}
                    {spec?.required ? ' *' : ''}
                    {spec?.type ? ` (${spec.type})` : ''}
                  </label>
                  {spec?.type === 'bool' ? (
                    <input
                      id={`var-${name}`} type="checkbox" data-testid={`var-input-${name}`}
                      checked={varDraft[name] === true}
                      onChange={(e) => setVarDraft((d) => ({ ...d, [name]: e.target.checked }))}
                    />
                  ) : (
                    <input
                      id={`var-${name}`} data-testid={`var-input-${name}`}
                      type={spec?.type === 'int' || spec?.type === 'float' ? 'number' : 'text'}
                      value={String(varDraft[name] ?? '')}
                      onChange={(e) => setVarDraft((d) => ({ ...d, [name]: e.target.value }))}
                    />
                  )}
                </div>
              ))}
            </fieldset>
          )}

          <fieldset style={{ marginTop: '0.75rem' }}>
            <legend>绑定工具（{tools.length}）</legend>
            {tools.length === 0 && loaded && (
              <div className="muted">暂无已注册工具（可在 /api/tools/register 登记）。</div>
            )}
            {tools.map((t) => (
              <label key={t.name} className="row" style={{ gap: '0.4rem', margin: '0.2rem 0' }}>
                <input
                  type="checkbox" data-testid={`tool-check-${t.name}`}
                  checked={boundTools.includes(t.name)}
                  onChange={(e) => setBoundTools((list) => e.target.checked
                    ? [...list, t.name]
                    : list.filter((n) => n !== t.name))}
                />
                <span title={t.description}>{t.name}</span>
              </label>
            ))}
          </fieldset>

          <div style={{ marginTop: '0.75rem' }}>
            <label htmlFor="model-input">模型</label>
            <input
              id="model-input" data-testid="model-input" value={model}
              onChange={(e) => setModel(e.target.value)}
            />
          </div>
        </div>

        {/* 右侧：对话区 + 工具 trace 面板 */}
        <div className="card" data-testid="chat-debug-conversation">
          <div className="chat-scroll" data-testid="chat-messages" aria-live="polite">
            {messages.length === 0 && (
              <div className="muted">选择模板与工具后发送消息，观察流式回复与真实工具调用。</div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`bubble ${m.role}`} data-testid={`chat-msg-${m.role}`}>
                <div>{m.content}</div>
              </div>
            ))}
          </div>

          <div className="chat-composer">
            <textarea
              aria-label="调试消息" data-testid="chat-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="例如：调用 add 计算 3 和 4 的和"
              disabled={streaming}
            />
            <button
              className="primary" data-testid="chat-send"
              onClick={() => void send()}
              disabled={streaming || !input.trim()}
            >
              {streaming ? '流式回复中…' : '发送'}
            </button>
          </div>

          <div data-testid="chat-trace-panel" style={{ marginTop: '0.75rem' }}>
            <strong>工具调用 trace</strong>
            {trace.length === 0 ? (
              <div className="muted">本轮未触发工具调用。</div>
            ) : (
              trace.map((t) => (
                <div key={t.index} className="card" data-testid="chat-trace-entry"
                     style={{ marginTop: '0.4rem' }}>
                  <div>
                    触发了工具 <strong>{t.name}</strong>
                    {t.executed ? (
                      <span className="badge ok">已真实执行</span>
                    ) : (
                      <span className="badge">未执行</span>
                    )}
                  </div>
                  <div className="muted" style={{ fontSize: '0.8rem' }}>
                    参数：{JSON.stringify(t.arguments)}
                  </div>
                  {t.result !== undefined && (
                    <div className="muted" style={{ fontSize: '0.8rem' }}>
                      结果：{JSON.stringify(t.result)}
                    </div>
                  )}
                  {t.call_id && (
                    <div className="muted" style={{ fontSize: '0.75rem' }}>
                      call_id: {t.call_id}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>

          {error && <div className="error-text" role="alert">{error}</div>}
        </div>
      </div>
    </div>
  );
}
