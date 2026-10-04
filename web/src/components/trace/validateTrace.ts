/**
 * P1-16 — PMI Trace Schema v1.0 前端字段校验器。
 *
 * 机器可读 schema：outputs/opencode_pmi_trace_schema_v1.0.schema.json
 * （JSON Schema draft 2020-12）。本文件将其核心约束以零依赖 TypeScript 实现，
 * 用于 trace 加载时的前置校验，并产出结构化校验日志（字段名/类型/缺失项）。
 *
 * 覆盖范围：
 * - 14 个顶层必备字段 + additionalProperties: false
 * - 各分块字段类型 / enum / pattern / const（含 egress 的 local_only 与
 *   plaintext_egress=false 两条硬约束）
 * - 全部 allOf 条件分支：L1/L2/L3 分层语义、写回三件套、授权单门、
 *   gdrive 加密外传、断网降级、黑名单脱敏一致性
 *
 * 未覆盖（前端校验范畴外，诚实声明）：
 * - "format": "date-time" 只做可解析 + 时区感知检查，非完整 RFC3339 文法
 * - integrity.trace_hash 的哈希重算比对（需要与写入端一致的规范化算法，
 *   见 schema 文档 §3；本视图只做字段形态检查）
 */

export interface ValidationIssue {
  path: string;
  field: string;
  expected: string;
  actual: string;
  status: 'pass' | 'fail';
}

export interface TraceValidationResult {
  format: 'pmi-trace' | 'canvas-timeline' | 'unknown';
  eventCount: number;
  issues: ValidationIssue[];
  passCount: number;
  failCount: number;
  ok: boolean;
}

const HEX64 = /^[0-9a-f]{64}$/;
const TRACE_ID = /^[0-9a-f]{32}$/;
const DOMAINS = ['personal', 'work', 'shared'] as const;

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function actualOf(v: unknown): string {
  if (v === undefined) return '<missing>';
  if (v === null) return 'null';
  if (Array.isArray(v)) return `array(${v.length})`;
  if (typeof v === 'object') return 'object';
  if (typeof v === 'string') return v.length > 80 ? `"${v.slice(0, 77)}..."` : `"${v}"`;
  return String(v);
}

/** 校验上下文：逐字段记录 pass/fail 日志条目。 */
class Ctx {
  issues: ValidationIssue[] = [];

  check(path: string, field: string, expected: string, cond: boolean, actual?: unknown): boolean {
    this.issues.push({
      path,
      field,
      expected,
      actual: actualOf(actual),
      status: cond ? 'pass' : 'fail',
    });
    return cond;
  }

  required(parent: Record<string, unknown>, path: string, field: string): unknown {
    const v = parent[field];
    const present = v !== undefined;
    this.issues.push({
      path,
      field,
      expected: 'required',
      actual: present ? actualOf(v) : '<missing>',
      status: present ? 'pass' : 'fail',
    });
    return v;
  }

  /** additionalProperties: false — 未声明字段一律拒绝。 */
  noExtra(parent: Record<string, unknown>, path: string, allowed: string[]): void {
    for (const key of Object.keys(parent)) {
      const ok = allowed.includes(key);
      this.issues.push({
        path,
        field: `(additionalProperty) ${key}`,
        expected: `仅允许 [${allowed.join(', ')}]`,
        actual: ok ? '已声明' : '未声明字段（拒绝）',
        status: ok ? 'pass' : 'fail',
      });
    }
  }

  get fails(): number {
    return this.issues.filter((i) => i.status === 'fail').length;
  }
}

function checkString(
  ctx: Ctx,
  parent: Record<string, unknown>,
  path: string,
  field: string,
  opts: { min?: number; max?: number; pattern?: RegExp; patternDesc?: string } = {},
): boolean {
  const v = parent[field];
  if (v === undefined || v === null) return false;
  if (typeof v !== 'string') {
    ctx.check(path, field, 'string', false, v);
    return false;
  }
  let ok = true;
  if (opts.min !== undefined) ok = ok && v.length >= opts.min;
  if (opts.max !== undefined) ok = ok && v.length <= opts.max;
  if (opts.pattern) ok = ok && opts.pattern.test(v);
  const expected =
    `string` +
    (opts.min !== undefined ? ` minLength=${opts.min}` : '') +
    (opts.max !== undefined ? ` maxLength=${opts.max}` : '') +
    (opts.pattern ? ` pattern(${opts.patternDesc ?? 'regex'})` : '');
  return ctx.check(path, field, expected, ok, v);
}

function checkEnum(
  ctx: Ctx,
  parent: Record<string, unknown>,
  path: string,
  field: string,
  values: readonly (string | null)[],
): boolean {
  const v = parent[field];
  if (v === undefined) return false;
  const allowed = values.includes(null) ? v === null || values.includes(v as string) : values.includes(v as string);
  return ctx.check(path, field, `enum[${values.join(' | ')}]`, allowed, v);
}

function checkBool(ctx: Ctx, parent: Record<string, unknown>, path: string, field: string): boolean {
  const v = parent[field];
  if (v === undefined) return false;
  return ctx.check(path, field, 'boolean', typeof v === 'boolean', v);
}

function checkInt(
  ctx: Ctx,
  parent: Record<string, unknown>,
  path: string,
  field: string,
  min?: number,
): boolean {
  const v = parent[field];
  if (v === undefined) return false;
  const ok =
    typeof v === 'number' && Number.isInteger(v) && (min === undefined || v >= min);
  return ctx.check(path, field, `integer${min !== undefined ? ` minimum=${min}` : ''}`, ok, v);
}

function checkConst(ctx: Ctx, parent: Record<string, unknown>, path: string, field: string, expected: unknown): boolean {
  const v = parent[field];
  if (v === undefined) return false;
  return ctx.check(path, field, `const ${JSON.stringify(expected)}`, v === expected, v);
}

/** format: date-time — 可解析且时区感知（Z 或 ±hh:mm / ±hhmm 偏移）。 */
function checkDateTime(ctx: Ctx, parent: Record<string, unknown>, path: string, field: string): boolean {
  const v = parent[field];
  if (typeof v !== 'string') {
    if (v !== undefined) ctx.check(path, field, 'date-time(string)', false, v);
    return false;
  }
  const tzAware = /[zZ]$/.test(v) || /[+-]\d{2}:?\d{2}$/.test(v);
  const parseable = !Number.isNaN(Date.parse(v));
  return ctx.check(path, field, 'date-time(可解析且时区感知 UTC)', tzAware && parseable, v);
}

function checkHex64(ctx: Ctx, parent: Record<string, unknown>, path: string, field: string): boolean {
  return checkString(ctx, parent, path, field, { pattern: HEX64, patternDesc: '64位小写hex' });
}

// ---------------------------------------------------------------------------
// 分块校验器
// ---------------------------------------------------------------------------

function validateActor(ctx: Ctx, ev: Record<string, unknown>): void {
  const a = ev.actor;
  if (!isObj(a)) {
    ctx.check('actor', '', 'object', false, a);
    return;
  }
  ctx.noExtra(a, 'actor', ['actor_id', 'actor_kind', 'conversation_id', 'task_id']);
  ctx.required(a, 'actor', 'actor_id');
  checkString(ctx, a, 'actor', 'actor_id', { min: 1, max: 200 });
  ctx.required(a, 'actor', 'actor_kind');
  checkEnum(ctx, a, 'actor', 'actor_kind', ['owner', 'service', 'agent', 'anonymous']);
  for (const f of ['conversation_id', 'task_id'] as const) {
    const v = a[f];
    if (v !== undefined && v !== null) checkString(ctx, a, 'actor', f);
    else if (v === null || v === undefined) {
      if (v === null) ctx.check(`actor`, f, 'string|null', true, v);
    }
  }
}

function validateQueryTarget(ctx: Ctx, ev: Record<string, unknown>): void {
  const q = ev.query_target;
  if (!isObj(q)) {
    ctx.check('query_target', '', 'object', false, q);
    return;
  }
  ctx.noExtra(q, 'query_target', ['kind', 'record_id', 'record_kind', 'domain', 'locator']);
  for (const f of ['kind', 'record_id', 'record_kind', 'domain'] as const) ctx.required(q, 'query_target', f);
  checkEnum(ctx, q, 'query_target', 'kind', [
    'rule',
    'memory',
    'source_segment',
    'profile_revision',
    'artifact',
    'import',
  ]);
  checkString(ctx, q, 'query_target', 'record_id', { min: 1, max: 200 });
  checkString(ctx, q, 'query_target', 'record_kind', { min: 1, max: 24 });
  checkEnum(ctx, q, 'query_target', 'domain', DOMAINS);
  const loc = q.locator;
  if (loc !== undefined && loc !== null) checkString(ctx, q, 'query_target', 'locator');
}

function validateQueryMethod(ctx: Ctx, ev: Record<string, unknown>): void {
  const q = ev.query_method;
  if (!isObj(q)) {
    ctx.check('query_method', '', 'object', false, q);
    return;
  }
  ctx.noExtra(q, 'query_method', ['mode', 'index_tier', 'cache_hit', 'authorization', 'online']);
  for (const f of ['mode', 'index_tier', 'cache_hit', 'authorization'] as const) ctx.required(q, 'query_method', f);
  checkEnum(ctx, q, 'query_method', 'mode', ['exact', 'fts', 'vector', 'graph', 'scan']);
  checkEnum(ctx, q, 'query_method', 'index_tier', ['hot_local', 'cold_remote', 'none']);
  checkBool(ctx, q, 'query_method', 'cache_hit');
  checkBool(ctx, q, 'query_method', 'online');
  const auth = q.authorization;
  if (!isObj(auth)) {
    ctx.check('query_method.authorization', '', 'object', false, auth);
  } else {
    ctx.noExtra(auth, 'query_method.authorization', ['predicate', 'grant_id', 'consumer_domain']);
    ctx.required(auth, 'query_method.authorization', 'predicate');
    checkConst(ctx, auth, 'query_method.authorization', 'predicate', 'grants.is_authorized');
    const gid = auth.grant_id;
    if (gid !== undefined && gid !== null) checkString(ctx, auth, 'query_method.authorization', 'grant_id');
    else if (gid === null) ctx.check('query_method.authorization', 'grant_id', 'string|null', true, gid);
    const cd = auth.consumer_domain;
    if (cd !== undefined) checkEnum(ctx, auth, 'query_method.authorization', 'consumer_domain', DOMAINS);
  }
}

function validateHits(ctx: Ctx, ev: Record<string, unknown>): void {
  const hits = ev.hits;
  if (!Array.isArray(hits)) {
    ctx.check('hits', '', 'array', false, hits);
    return;
  }
  ctx.check('hits', '', 'array（可为空——查询无果也是合法 trace）', true, hits);
  hits.forEach((h, i) => {
    const p = `hits[${i}]`;
    if (!isObj(h)) {
      ctx.check(p, '', 'object', false, h);
      return;
    }
    ctx.noExtra(h, p, [
      'record_id',
      'record_kind',
      'domain',
      'score',
      'content_hash',
      'hypothesis_status',
      'tier',
      'excerpt_redacted',
    ]);
    for (const f of ['record_id', 'record_kind', 'domain', 'score', 'content_hash', 'tier'] as const)
      ctx.required(h, p, f);
    checkString(ctx, h, p, 'record_id', { min: 1, max: 200 });
    checkString(ctx, h, p, 'record_kind', { min: 1, max: 24 });
    checkEnum(ctx, h, p, 'domain', DOMAINS);
    const score = h.score;
    if (score !== undefined)
      ctx.check(p, 'score', 'number minimum=0', typeof score === 'number' && !Number.isNaN(score) && score >= 0, score);
    checkHex64(ctx, h, p, 'content_hash');
    const hs = h.hypothesis_status;
    if (hs !== undefined)
      checkEnum(ctx, h, p, 'hypothesis_status', ['fact', 'hypothesis', 'theory', 'unverified', null]);
    checkEnum(ctx, h, p, 'tier', ['hot', 'cold']);
    const er = h.excerpt_redacted;
    if (er !== undefined) checkBool(ctx, h, p, 'excerpt_redacted');
  });
}

function validateAgent(ctx: Ctx, ev: Record<string, unknown>): void {
  const a = ev.agent;
  if (!isObj(a)) {
    ctx.check('agent', '', 'object', false, a);
    return;
  }
  ctx.noExtra(a, 'agent', ['agent_id', 'agent_version', 'role', 'model', 'provider']);
  for (const f of ['agent_id', 'role'] as const) ctx.required(a, 'agent', f);
  checkString(ctx, a, 'agent', 'agent_id', { min: 1, max: 100 });
  const av = a.agent_version;
  if (av !== undefined && av !== null) checkString(ctx, a, 'agent', 'agent_version');
  checkEnum(ctx, a, 'agent', 'role', ['center', 'worker', 'tool_gateway', 'executor', 'service']);
  for (const f of ['model', 'provider'] as const) {
    const v = a[f];
    if (v !== undefined && v !== null) checkString(ctx, a, 'agent', f);
    else if (v === null) ctx.check('agent', f, 'string|null', true, v);
  }
}

function validateWriteback(ctx: Ctx, ev: Record<string, unknown>): void {
  const w = ev.writeback;
  if (!isObj(w)) {
    ctx.check('writeback', '', 'object', false, w);
    return;
  }
  ctx.noExtra(w, 'writeback', ['action', 'target_ids', 'revision', 'source_relations', 'audit_seq', 'audit_hash']);
  for (const f of ['action', 'target_ids'] as const) ctx.required(w, 'writeback', f);
  checkEnum(ctx, w, 'writeback', 'action', ['none', 'memory.upsert', 'memory.activated', 'memory.hypothesis_denied']);
  const tids = w.target_ids;
  if (Array.isArray(tids)) {
    const okItems = tids.every((t) => typeof t === 'string' && t.length >= 1 && t.length <= 200);
    ctx.check('writeback', 'target_ids', 'array[string 1..200]', okItems, tids);
  }
  const rev = w.revision;
  if (rev !== undefined && rev !== null)
    checkInt(ctx, w, 'writeback', 'revision', 1);
  else if (rev === null) ctx.check('writeback', 'revision', 'integer|null', true, rev);
  const rels = w.source_relations;
  if (rels !== undefined) {
    if (!Array.isArray(rels)) {
      ctx.check('writeback', 'source_relations', 'array', false, rels);
    } else {
      rels.forEach((r, i) => {
        const p = `writeback.source_relations[${i}]`;
        if (!isObj(r)) {
          ctx.check(p, '', 'object', false, r);
          return;
        }
        ctx.noExtra(r, p, ['source_id', 'derived_id', 'relation_type', 'permission_snapshot']);
        for (const f of ['source_id', 'derived_id', 'relation_type'] as const) ctx.required(r, p, f);
        checkString(ctx, r, p, 'source_id', { min: 1, max: 200 });
        checkString(ctx, r, p, 'derived_id', { min: 1, max: 200 });
        checkEnum(ctx, r, p, 'relation_type', ['derives', 'quotes', 'cites', 'supports']);
      });
    }
  }
  const as = w.audit_seq;
  if (as !== undefined && as !== null) checkInt(ctx, w, 'writeback', 'audit_seq', 1);
  else if (as === null) ctx.check('writeback', 'audit_seq', 'integer|null', true, as);
  const ah = w.audit_hash;
  if (ah !== undefined && ah !== null) checkHex64(ctx, w, 'writeback', 'audit_hash');
  else if (ah === null) ctx.check('writeback', 'audit_hash', '64hex|null', true, ah);
}

function validateEgress(ctx: Ctx, ev: Record<string, unknown>): void {
  const e = ev.egress;
  if (!isObj(e)) {
    ctx.check('egress', '', 'object', false, e);
    return;
  }
  ctx.noExtra(e, 'egress', [
    'authorized',
    'grant_id',
    'destination',
    'cipher',
    'key_id',
    'key_escrow',
    'plaintext_egress',
    'cloud_searchable',
    'audit_seq',
  ]);
  for (const f of [
    'authorized',
    'destination',
    'cipher',
    'key_id',
    'key_escrow',
    'plaintext_egress',
    'audit_seq',
  ] as const)
    ctx.required(e, 'egress', f);
  checkBool(ctx, e, 'egress', 'authorized');
  const gid = e.grant_id;
  if (gid !== undefined && gid !== null) checkString(ctx, e, 'egress', 'grant_id');
  checkEnum(ctx, e, 'egress', 'destination', ['none', 'local', 'gdrive']);
  checkEnum(ctx, e, 'egress', 'cipher', ['none', 'aes-256-gcm']);
  checkString(ctx, e, 'egress', 'key_id', { min: 1, max: 120 });
  checkConst(ctx, e, 'egress', 'key_escrow', 'local_only');
  checkConst(ctx, e, 'egress', 'plaintext_egress', false);
  const cs = e.cloud_searchable;
  if (cs !== undefined) checkBool(ctx, e, 'egress', 'cloud_searchable');
  checkInt(ctx, e, 'egress', 'audit_seq', 1);
}

function validateRedaction(ctx: Ctx, ev: Record<string, unknown>): void {
  const r = ev.redaction;
  if (!isObj(r)) {
    ctx.check('redaction', '', 'object', false, r);
    return;
  }
  ctx.noExtra(r, 'redaction', ['blacklist_version', 'matched', 'redacted_fields']);
  for (const f of ['blacklist_version', 'matched', 'redacted_fields'] as const) ctx.required(r, 'redaction', f);
  const bv = r.blacklist_version;
  if (bv !== undefined && bv !== null) checkString(ctx, r, 'redaction', 'blacklist_version');
  else if (bv === null) ctx.check('redaction', 'blacklist_version', 'string|null（null=黑名单未装载）', true, bv);
  checkBool(ctx, r, 'redaction', 'matched');
  const rf = r.redacted_fields;
  if (Array.isArray(rf))
    ctx.check('redaction', 'redacted_fields', 'array[string]', rf.every((x) => typeof x === 'string'), rf);
}

function validateNetwork(ctx: Ctx, ev: Record<string, unknown>): void {
  const n = ev.network;
  if (n === undefined) return; // 可选块
  if (!isObj(n)) {
    ctx.check('network', '', 'object', false, n);
    return;
  }
  ctx.noExtra(n, 'network', ['online', 'cold_fetch_attempted', 'result', 'degraded']);
  const on = n.online;
  if (on !== undefined) checkBool(ctx, n, 'network', 'online');
  const cfa = n.cold_fetch_attempted;
  if (cfa !== undefined) checkBool(ctx, n, 'network', 'cold_fetch_attempted');
  const res = n.result;
  if (res !== undefined)
    checkEnum(ctx, n, 'network', 'result', [
      'not_attempted',
      'served_hot_only',
      'served_after_decrypt',
      'failed_offline',
    ]);
  const deg = n.degraded;
  if (deg !== undefined) checkBool(ctx, n, 'network', 'degraded');
}

function validateIntegrity(ctx: Ctx, ev: Record<string, unknown>): void {
  const it = ev.integrity;
  if (!isObj(it)) {
    ctx.check('integrity', '', 'object', false, it);
    return;
  }
  ctx.noExtra(it, 'integrity', ['prev_trace_hash', 'trace_hash', 'audit_chain_verified']);
  for (const f of ['prev_trace_hash', 'audit_chain_verified'] as const) ctx.required(it, 'integrity', f);
  checkHex64(ctx, it, 'integrity', 'prev_trace_hash');
  const th = it.trace_hash;
  if (th !== undefined) checkHex64(ctx, it, 'integrity', 'trace_hash');
  checkBool(ctx, it, 'integrity', 'audit_chain_verified');
}

// ---------------------------------------------------------------------------
// allOf 条件分支（§7 分层语义约束）
// ---------------------------------------------------------------------------

function validateConditionals(ctx: Ctx, ev: Record<string, unknown>): void {
  const layer = ev.layer;
  const qt = isObj(ev.query_target) ? ev.query_target : {};
  const wb = isObj(ev.writeback) ? ev.writeback : {};
  const eg = isObj(ev.egress) ? ev.egress : {};
  const qm = isObj(ev.query_method) ? ev.query_method : {};
  const rd = isObj(ev.redaction) ? ev.redaction : {};
  const hits = Array.isArray(ev.hits) ? (ev.hits as unknown[]) : [];
  const hitObjs = hits.filter(isObj);

  if (layer === 'L1') {
    ctx.check('(allOf L1)', 'query_target.kind', 'const rule', qt.kind === 'rule', qt.kind);
    ctx.check('(allOf L1)', 'writeback.action', 'const none（规则层只读）', wb.action === 'none', wb.action);
    hitObjs.forEach((h, i) => {
      const has = 'hypothesis_status' in h;
      ctx.check(`(allOf L1) hits[${i}]`, 'hypothesis_status', 'required 且 null', has && h.hypothesis_status === null, h.hypothesis_status);
    });
    ctx.check('(allOf L1)', 'egress.destination', 'enum[none | local]', eg.destination === 'none' || eg.destination === 'local', eg.destination);
  }
  if (layer === 'L2') {
    ctx.check('(allOf L2)', 'query_target.kind', 'const memory', qt.kind === 'memory', qt.kind);
    hitObjs.forEach((h, i) => {
      const ok = 'hypothesis_status' in h && typeof h.hypothesis_status === 'string';
      ctx.check(`(allOf L2) hits[${i}]`, 'hypothesis_status', 'required 且 string', ok, h.hypothesis_status);
    });
  }
  if (layer === 'L3') {
    const ok = ['source_segment', 'profile_revision', 'artifact', 'import'].includes(qt.kind as string);
    ctx.check('(allOf L3)', 'query_target.kind', 'enum[source_segment | profile_revision | artifact | import]', ok, qt.kind);
  }

  const action = wb.action;
  if (action === 'none') {
    ctx.check('(allOf writeback=none)', 'target_ids', 'maxItems=0（action=none 时必须为空）', Array.isArray(wb.target_ids) && wb.target_ids.length === 0, wb.target_ids);
    ctx.check('(allOf writeback=none)', 'audit_seq', 'null', wb.audit_seq === null || wb.audit_seq === undefined, wb.audit_seq);
    ctx.check('(allOf writeback=none)', 'audit_hash', 'null', wb.audit_hash === null || wb.audit_hash === undefined, wb.audit_hash);
  } else if (typeof action === 'string') {
    const tids = wb.target_ids;
    ctx.check('(allOf writeback≠none)', 'target_ids', 'minItems=1', Array.isArray(tids) && tids.length >= 1, tids);
    ctx.check('(allOf writeback≠none)', 'audit_seq', 'required', typeof wb.audit_seq === 'number', wb.audit_seq);
    ctx.check('(allOf writeback≠none)', 'audit_hash', 'required（64hex）', typeof wb.audit_hash === 'string' && HEX64.test(wb.audit_hash), wb.audit_hash);
    ctx.check('(allOf writeback≠none)', 'revision', 'required', typeof wb.revision === 'number', wb.revision);
  }

  if (eg.authorized === true) {
    ctx.check('(allOf egress.authorized=true)', 'grant_id', 'required 且非空', typeof eg.grant_id === 'string' && eg.grant_id.length >= 1, eg.grant_id);
    ctx.check('(allOf egress.authorized=true)', 'destination', 'const gdrive（授权一次，外传目的地必须登记）', eg.destination === 'gdrive', eg.destination);
  } else if (eg.authorized === false) {
    ctx.check('(allOf egress.authorized=false)', 'destination', 'enum[none | local]（未授权不得上云）', eg.destination === 'none' || eg.destination === 'local', eg.destination);
  }

  if (eg.destination === 'gdrive') {
    ctx.check('(allOf destination=gdrive)', 'cipher', 'const aes-256-gcm（上云必须先本地加密）', eg.cipher === 'aes-256-gcm', eg.cipher);
    ctx.check('(allOf destination=gdrive)', 'cloud_searchable', 'required 且 false（云端不可直接检索）', 'cloud_searchable' in eg && eg.cloud_searchable === false, eg.cloud_searchable);
    ctx.check('(allOf destination=gdrive)', 'audit_seq', 'integer ≥1（逐次留痕）', typeof eg.audit_seq === 'number' && Number.isInteger(eg.audit_seq) && eg.audit_seq >= 1, eg.audit_seq);
  }

  if (qm.index_tier === 'cold_remote') {
    ctx.check('(allOf index_tier=cold_remote)', 'online', 'const true', qm.online === true, qm.online);
    const n = ev.network;
    ctx.check('(allOf index_tier=cold_remote)', 'network', 'required', isObj(n), n);
    ctx.check('(allOf index_tier=cold_remote)', 'network.cold_fetch_attempted', 'const true', isObj(n) && n.cold_fetch_attempted === true, isObj(n) ? n.cold_fetch_attempted : undefined);
  }

  if (qm.online === false) {
    ctx.check('(allOf online=false)', 'index_tier', 'enum[hot_local | none]（断网不得命中冷层）', qm.index_tier === 'hot_local' || qm.index_tier === 'none', qm.index_tier);
    const n = ev.network;
    ctx.check('(allOf online=false)', 'network', 'required', isObj(n), n);
    ctx.check('(allOf online=false)', 'network.cold_fetch_attempted', 'const true', isObj(n) && n.cold_fetch_attempted === true, isObj(n) ? n.cold_fetch_attempted : undefined);
    ctx.check('(allOf online=false)', 'network.result', 'enum[served_hot_only | failed_offline]', isObj(n) && (n.result === 'served_hot_only' || n.result === 'failed_offline'), isObj(n) ? n.result : undefined);
    ctx.check('(allOf online=false)', 'network.degraded', 'const true（降级服务）', isObj(n) && n.degraded === true, isObj(n) ? n.degraded : undefined);
  }

  if (rd.matched === true) {
    ctx.check('(allOf redaction.matched=true)', 'blacklist_version', 'required 且非空（不得在黑名单未装载时宣称已脱敏）', typeof rd.blacklist_version === 'string' && rd.blacklist_version.length >= 1, rd.blacklist_version);
    ctx.check('(allOf redaction.matched=true)', 'redacted_fields', 'minItems=1', Array.isArray(rd.redacted_fields) && rd.redacted_fields.length >= 1, rd.redacted_fields);
    hitObjs.forEach((h, i) => {
      ctx.check(`(allOf redaction.matched=true) hits[${i}]`, 'excerpt_redacted', 'const true', h.excerpt_redacted === true, h.excerpt_redacted);
    });
  }
}

// ---------------------------------------------------------------------------
// 入口
// ---------------------------------------------------------------------------

const TOP_ALLOWED = [
  'trace_version',
  'trace_id',
  'seq',
  'ts',
  'layer',
  'actor',
  'query_target',
  'query_method',
  'hits',
  'agent',
  'writeback',
  'egress',
  'redaction',
  'network',
  'integrity',
];
const TOP_REQUIRED = TOP_ALLOWED.filter((f) => f !== 'network');

/** 校验单条 PMI trace 事件（schema v1.0）。 */
export function validatePmiTraceEvent(event: unknown): TraceValidationResult {
  const ctx = new Ctx();
  const ev = event;
  if (!isObj(ev)) {
    ctx.check('$', '', 'object', false, ev);
    return finish(ctx, 'pmi-trace', 1);
  }
  ctx.noExtra(ev, '$', TOP_ALLOWED);
  for (const f of TOP_REQUIRED) ctx.required(ev, '$', f);

  checkConst(ctx, ev, '$', 'trace_version', '1.0');
  checkString(ctx, ev, '$', 'trace_id', { pattern: TRACE_ID, patternDesc: '32位小写hex' });
  checkInt(ctx, ev, '$', 'seq', 1);
  checkDateTime(ctx, ev, '$', 'ts');
  checkEnum(ctx, ev, '$', 'layer', ['L1', 'L2', 'L3']);

  validateActor(ctx, ev);
  validateQueryTarget(ctx, ev);
  validateQueryMethod(ctx, ev);
  validateHits(ctx, ev);
  validateAgent(ctx, ev);
  validateWriteback(ctx, ev);
  validateEgress(ctx, ev);
  validateRedaction(ctx, ev);
  validateNetwork(ctx, ev);
  validateIntegrity(ctx, ev);
  validateConditionals(ctx, ev);

  return finish(ctx, 'pmi-trace', 1);
}

/** 识别并校验整个 trace 文档（样例集 / 事件数组 / 单事件 / canvas 往返 trace）。 */
export function validateTraceDocument(doc: unknown): TraceValidationResult {
  if (isObj(doc) && Array.isArray(doc.trace_events)) {
    const results = doc.trace_events.map((e) => validatePmiTraceEvent(e));
    return mergeResults(results, 'pmi-trace');
  }
  if (Array.isArray(doc) && doc.every((e) => isObj(e) && 'trace_id' in (e as object) && 'layer' in (e as object))) {
    return mergeResults(doc.map((e) => validatePmiTraceEvent(e)), 'pmi-trace');
  }
  if (isObj(doc) && 'trace_version' in doc && 'layer' in doc) {
    return validatePmiTraceEvent(doc);
  }
  // canvas 往返 trace（evidence/process-traces 格式）— 非 PMI schema，仅做结构校验。
  if (isObj(doc) && Array.isArray(doc.timeline_events)) {
    return validateCanvasTimeline(doc);
  }
  const ctx = new Ctx();
  ctx.check('$', '', '可识别的 trace 格式（PMI 事件 / trace_events 样例集 / timeline_events canvas trace）', false, doc);
  return finish(ctx, 'unknown', 0);
}

function validateCanvasTimeline(doc: Record<string, unknown>): TraceValidationResult {
  const ctx = new Ctx();
  ctx.check('$', 'title', 'string', typeof doc.title === 'string', doc.title);
  const events = doc.timeline_events as unknown[];
  ctx.check('$', 'timeline_events', 'array', true, events);
  events.forEach((e, i) => {
    const p = `timeline_events[${i}]`;
    if (!isObj(e)) {
      ctx.check(p, '', 'object', false, e);
      return;
    }
    ctx.check(p, 'seq', 'integer', typeof e.seq === 'number', e.seq);
    ctx.check(p, 'event_type', 'string（必需）', typeof e.event_type === 'string' && e.event_type.length > 0, e.event_type);
    ctx.check(p, 'created_at', 'date-time（必需）', typeof e.created_at === 'string' && !Number.isNaN(Date.parse(e.created_at)), e.created_at);
  });
  return finish(ctx, 'canvas-timeline', events.length);
}

function finish(ctx: Ctx, format: TraceValidationResult['format'], eventCount: number): TraceValidationResult {
  const failCount = ctx.fails;
  return {
    format,
    eventCount,
    issues: ctx.issues,
    passCount: ctx.issues.length - failCount,
    failCount,
    ok: failCount === 0,
  };
}

function mergeResults(results: TraceValidationResult[], format: TraceValidationResult['format']): TraceValidationResult {
  const issues = results.flatMap((r, i) =>
    r.issues.map((iss) => (iss.path === '$' ? { ...iss, path: `event[${i}]${iss.path}` } : { ...iss, path: `event[${i}].${iss.path}` })),
  );
  const failCount = issues.filter((i) => i.status === 'fail').length;
  return {
    format,
    eventCount: results.length,
    issues,
    passCount: issues.length - failCount,
    failCount,
    ok: failCount === 0,
  };
}
