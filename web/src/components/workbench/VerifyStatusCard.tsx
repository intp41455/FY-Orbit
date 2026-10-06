import { LineIcon } from '../ui/LineIcon';
import type { TaskSummary } from '../../api/types';
import { WbIcon } from './WbIcon';
import type { FileContent } from '../../api/workbench';

/**
 * 右区「验证状态卡」——/workbench 全页唯一视觉重心。
 *
 * 三条铁律：
 *  1. **绝不用绿色表达成功**。完成任务色是 --ui-st-complete #0369a1 深蓝，
 *     并且必须同时有 LineIcon check + 中文文字（颜色不是唯一信息通道）。
 *  2. **数据必须真实**。三个数据源分别是 FileContent.sha256/revision、
 *     TerminalSession.state/exit_code、TaskSummary.steps/max_steps，
 *     缺任意一个就显示「暂无数据 · 来源不可得」，不许画假勾、不许自己捏哈希。
 *  3. 卡片只读展示，不承载业务决策；放行动作在下方「放行闸门」里单独给出。
 */

export interface VerifySources {
  /** 制品文件：来自 CodeEditor 读盘的真实 FileContent（未Selected时为 null）。 */
  file: FileContent | null;
  /** 制品哈希落库时间（读盘时刻），非「构建时间」伪造。 */
  fileReadAt: string | null;
  /** 最近一次终端会话状态。 */
  terminal: { sessionId: string | null; state: string | null; exitCode: number | null } | null;
  /** 最近一次任务。 */
  task: TaskSummary | null;
}

const NO_DATA = '暂无数据 · 来源不可得';

function Row({
  icon,
  label,
  value,
  mono,
  missing,
}: {
  icon: Parameters<typeof LineIcon>[0]['name'];
  label: string;
  value: string;
  mono?: boolean;
  missing?: boolean;
}) {
  return (
    <div className="wb-verify-row">
      <span className="wb-verify-ico" aria-hidden="true">
        <LineIcon name={icon} size={16} />
      </span>
      <span className="wb-verify-label">{label}</span>
      <span className={`wb-verify-value${mono ? ' is-mono' : ''}`} data-missing={missing ? 'true' : undefined}>
        {value}
      </span>
    </div>
  );
}

/** 该函数只回答一件事：是不是所有来源都到齐且通过。不许"看起来通过"。 */
export function deriveTrust(src: VerifySources) {
  const hasFile = Boolean(src.file?.sha256);
  const hasTask = Boolean(src.task);
  const terminalOk =
    Boolean(src.terminal?.sessionId) &&
    src.terminal?.state === 'exited' &&
    src.terminal.exitCode === 0;
  const taskOk = src.task?.state === 'succeeded';
  const all = hasFile && terminalOk && taskOk;
  const partial = hasFile || hasTask || Boolean(src.terminal?.sessionId);
  return {
    level: all ? ('trusted' as const) : partial ? ('partial' as const) : ('none' as const),
    hasFile,
    terminalOk,
    taskOk,
  };
}

export function VerifyStatusCard({ src }: { src: VerifySources }) {
  const t = deriveTrust(src);
  const trusted = t.level === 'trusted';

  return (
    <div className="wb-verify" data-level={t.level}>
      <div className="wb-verify-seal">
        <span className="wb-verify-seal-ico" aria-hidden="true">
          {trusted ? <LineIcon name="check" size={22} /> : <WbIcon name="shield" size={22} />}
        </span>
        <div className="wb-verify-seal-text">
          <strong className="wb-verify-seal-title">{trusted ? '可信构建' : '证据未齐'}</strong>
          <span className="wb-verify-seal-sub">
            {trusted
              ? '制品哈希 / 终端退出码 / 任务步数三方来源一致'
              : '缺少一项或多项证据，不作任何通过结论'}
          </span>
        </div>
      </div>

      {/* 完成语义：深蓝 + LineIcon check + 中文文字，三通道齐全且不带任何绿色。 */}
      <div className={`wb-verify-ledger${trusted ? ' is-complete' : ''}`}>
        <span aria-hidden="true">
          <LineIcon name={trusted ? 'check' : 'info'} size={16} />
        </span>
        <strong>{trusted ? 'TRUSTED BUILD · 已核验' : 'TRUSTED BUILD · 未达成'}</strong>
      </div>

      <Row
        icon="budget"
        label="退出码"
        value={
          src.terminal?.exitCode === null || src.terminal?.exitCode === undefined
            ? src.terminal?.sessionId
              ? `尚无退出码（会话 ${src.terminal.state ?? '未知'}）`
              : NO_DATA
            : String(src.terminal.exitCode)
        }
        missing={src.terminal?.exitCode === null || src.terminal?.exitCode === undefined}
      />
      <Row
        icon="hash"
        label="制品哈希"
        value={src.file?.sha256 ? `SHA-256 ${src.file.sha256.slice(0, 32)}…` : NO_DATA}
        mono
        missing={!src.file?.sha256}
      />
      <Row
        icon="layers"
        label="制品版本"
        value={src.file ? `r${src.file.revision} · ${src.file.size_bytes} B` : NO_DATA}
        missing={!src.file}
      />
      <Row
        icon="clock"
        label="取数时间"
        value={src.fileReadAt ?? NO_DATA}
        missing={!src.fileReadAt}
      />
      <Row
        icon="timeline"
        label="任务步数"
        value={src.task ? `${src.task.steps}/${src.task.max_steps}（${src.task.state}）` : NO_DATA}
        missing={!src.task}
      />

      <p className="wb-verify-foot muted">
        构建时间取自「上一次真实读盘时刻」，不是渲染时刻；哈希取自服务端返回的
        <code> FileContent.sha256</code>，前端不自行计算、不缓存旧值。
        {!trusted && ' 任一项缺失时本卡只呈现占位文案，不做通过暗示。'}
      </p>
    </div>
  );
}
