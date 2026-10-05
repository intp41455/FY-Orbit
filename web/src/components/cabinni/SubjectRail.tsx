/**
 * 包 D 私有组件 · 画像对象列表（左侧栏）
 * ---------------------------------------------------------------------------
 * 交互契约（最少点击守则 + 包 D 任务书 §6）：
 *   - 整块可点（min-height ≥ --ui-hit-lg）；
 *   - hover 才浮现「编辑 / 导出 / 归档」——absolute 定位，**不占常驻布局**；
 *   - 窄屏无 hover，次要操作转为常显（见 cabin.css §6 的 ≤860px 覆盖）。
 *
 * ⚠ 无效 HTML 教训（web-design-guidelines 检查项）：
 *   次要操作**不能**嵌在主按钮里 —— `<button>` 不能是 `<button>` 的后代，
 *   会造成 hydration 错误，且读屏会把两个控件合成一个。
 *   所以次要操作是主按钮的**兄弟节点**，靠 `<li>` 的 :hover 显形。
 *
 * 诚实性：这三个动作当前后端没有对应端点，因此是**禁用态 + 明示原因**，
 * 不是点了没反应的空按钮（红线二：不许写假效果）。
 */
import { LineIcon } from '../../components/ui/LineIcon';
import type { ProfileSubject } from '../../api/profiles';

export interface SubjectRailProps {
  subjects: ProfileSubject[];
  activeId: string | null;
  onSelect: (s: ProfileSubject) => void;
}

const KIND_LABEL: Record<string, string> = {
  self: '本人',
  person: '外部人物',
  project: '项目对象',
  org: '组织',
  work: '工作组织',
  topic: '特定议题',
  other: '其他',
};

/** 待接线动作：禁用 + title 说明为什么不能点。 */
const PENDING_ACTIONS: readonly { label: string; hint: string }[] = [
  { label: '编辑', hint: '待接线：后端尚未开放画像主体重命名接口' },
  { label: '导出', hint: '待接线：导出接口未就绪（可先用浏览器打印本页 PDF）' },
  { label: '归档', hint: '待接线：后端尚未开放归档端点' },
];

export function SubjectRail({ subjects, activeId, onSelect }: SubjectRailProps) {
  if (subjects.length === 0) {
    return (
      <p className="cabin-ni-evi-empty" data-testid="profile-subjects-empty">
        <LineIcon name="info" size={16} />
        还没有画像对象。先「新建档案对象」，再导入语料。
      </p>
    );
  }

  return (
    <ul
      data-testid="profile-subject-rail"
      style={{
        listStyle: 'none',
        margin: 0,
        padding: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--ui-s-2)',
      }}
    >
      {subjects.map((s) => {
        const active = s.id === activeId;
        return (
          <li key={s.id} className="cabin-ni-psubject-wrap">
            <button
              type="button"
              className="cabin-ni-psubject"
              aria-pressed={active}
              onClick={() => onSelect(s)}
            >
              <span className="cabin-ni-psubject-label">
                <LineIcon name={s.kind === 'self' ? 'user' : 'profiles'} size={18} />
                <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {s.label}
                </span>
                <span
                  className={`ui-badge ${s.kind === 'self' ? 'ui-badge--complete' : 'ui-badge--neutral'}`}
                >
                  {KIND_LABEL[s.kind] ?? s.kind}
                </span>
              </span>

              {s.description && <span className="cabin-ni-psubject-desc">{s.description}</span>}
            </button>

            {/* 次要操作：主按钮的兄弟节点，hover/focus-within 才显形 */}
            <span className="cabin-ni-hover-act cabin-ni-hover-act--row" aria-hidden="false">
              {PENDING_ACTIONS.map((a) => (
                <button
                  key={a.label}
                  type="button"
                  className="ui-btn ui-btn--sm"
                  disabled
                  title={a.hint}
                  data-testid={`profile-subject-${a.label}-${s.id}`}
                  onClick={() => onSelect(s)}
                >
                  {a.label}
                </button>
              ))}
            </span>
          </li>
        );
      })}
    </ul>
  );
}