/**
 * 数码小屋（P2）键盘控制与输入动作映射系统（遵循 input-systems 与 game-feel 规范）。
 *
 * 核心特性：
 * 1. 动作映射抽象（Actions over raw keys）：
 *    - move_left / move_right: A / D / 方向键左 / 右
 *    - move_up / move_down: W / S / 方向键上 / 下（沿着 2.5D depth 纵深探索）
 *    - jump: Space / W / 方向键上（支持输入缓冲与离散边沿触发）
 *    - crouch: S / 方向键下 / C（长按/静态下蹲动作）
 * 2. 文本框与交互元素隔离保护（不污染 input / textarea / select / contenteditable）；
 * 3. 边沿检测（Edge vs Held）与跳跃预输入缓冲（Input Buffering）；
 * 4. 游玩时自动拦截方向键与空格的默认页面滚动（preventDefault）。
 */

export type CabinInputAction =
  | 'move_left'
  | 'move_right'
  | 'move_up'
  | 'move_down'
  | 'jump'
  | 'crouch';

export type KeyBindingsMap = Record<CabinInputAction, readonly string[]>;

export const DEFAULT_CABIN_KEY_BINDINGS: KeyBindingsMap = {
  move_left: ['KeyA', 'ArrowLeft', 'a', 'A'],
  move_right: ['KeyD', 'ArrowRight', 'd', 'D'],
  move_up: ['KeyW', 'ArrowUp', 'w', 'W'],
  move_down: ['KeyS', 'ArrowDown', 's', 'S'],
  jump: ['Space', 'KeyW', 'ArrowUp', 'w', 'W', ' '],
  crouch: ['KeyS', 'ArrowDown', 's', 'S', 'KeyC', 'c', 'C'],
};

/** 检查事件目标是否属于输入框或正在输入的文本元素（防止键入文字时误触发角色移动） */
export function isEditableElement(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
  if (target.isContentEditable) return true;
  if (target.getAttribute('role') === 'textbox') return true;
  return false;
}

export interface CabinInputSystem {
  /** 检查指定动作当前是否处于活跃按下状态 */
  isActionActive(action: CabinInputAction): boolean;
  /** 获取当前移动摇杆/轴向量：x∈[-1, 1], depth∈[-1, 1]（-1 向内深入，+1 向前移出） */
  getMovementAxis(): { x: number; depth: number };
  /** 消费跳跃触发（边沿或缓冲区有效则返回 true 并清空，保证只起跳一次） */
  consumeJump(): boolean;
  /** 当前跳跃缓冲是否有效（不消费） */
  hasJumpBuffered(): boolean;
  /** 手动触发一次跳跃信号（用于单元测试或虚拟摇杆按钮） */
  triggerJump(): void;
  /** 当前是否长按着下蹲动作（且未在水平奔跑） */
  isCrouchHeld(): boolean;
  /** 当前按键集合 */
  getActiveKeys(): ReadonlySet<string>;
  /** 模拟按键按下（单测或扩展用） */
  simulateKeyDown(keyOrCode: string): void;
  /** 模拟按键抬起（单测或扩展用） */
  simulateKeyUp(keyOrCode: string): void;
  /** 帧更新（递减缓冲倒计时等） */
  update(dtMs: number): void;
  /** 重置全部按键状态 */
  reset(): void;
  /** 销毁并移除事件监听器 */
  destroy(): void;
}

export interface CabinInputOptions {
  bindings?: KeyBindingsMap;
  /** 跳跃缓冲时间（毫秒，默认 150ms） */
  jumpBufferMs?: number;
  /** 挂载事件的目标元素，默认 window */
  target?: Window | HTMLElement | null;
}

export function createCabinInput(options: CabinInputOptions = {}): CabinInputSystem {
  const bindings = options.bindings ?? DEFAULT_CABIN_KEY_BINDINGS;
  const jumpBufferDuration = options.jumpBufferMs ?? 150;
  const activeKeys = new Set<string>();
  let jumpBufferTimer = 0;

  const matchesAction = (action: CabinInputAction, keyOrCode: string): boolean => {
    const list = bindings[action];
    return list ? list.includes(keyOrCode) : false;
  };

  const isActionActive = (action: CabinInputAction): boolean => {
    const list = bindings[action];
    if (!list) return false;
    for (const k of list) {
      if (activeKeys.has(k)) return true;
    }
    return false;
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (isEditableElement(e.target)) return;

    const code = e.code;
    const key = e.key;

    // 拦截页面由于方向键或空格造成的滚动条滚动
    if (
      code === 'Space' ||
      key === ' ' ||
      code === 'ArrowUp' ||
      code === 'ArrowDown' ||
      code === 'ArrowLeft' ||
      code === 'ArrowRight'
    ) {
      if (typeof e.preventDefault === 'function') {
        e.preventDefault();
      }
    }

    const wasActive = activeKeys.has(code) || activeKeys.has(key);
    activeKeys.add(code);
    activeKeys.add(key);

    // 离散跳跃检测：边沿按下跳跃键时注入跳跃缓冲
    if (!wasActive && (matchesAction('jump', code) || matchesAction('jump', key))) {
      jumpBufferTimer = jumpBufferDuration;
    }
  };

  const handleKeyUp = (e: KeyboardEvent) => {
    activeKeys.delete(e.code);
    activeKeys.delete(e.key);
  };

  const handleBlur = () => {
    activeKeys.clear();
    jumpBufferTimer = 0;
  };

  const target = options.target !== undefined ? options.target : (typeof window !== 'undefined' ? window : null);

  if (target && typeof target.addEventListener === 'function') {
    target.addEventListener('keydown', handleKeyDown as EventListener, { passive: false });
    target.addEventListener('keyup', handleKeyUp as EventListener);
    target.addEventListener('blur', handleBlur as EventListener);
  }

  return {
    isActionActive(action: CabinInputAction): boolean {
      return isActionActive(action);
    },

    getMovementAxis() {
      let x = 0;
      let depth = 0;

      if (isActionActive('move_left')) x -= 1;
      if (isActionActive('move_right')) x += 1;
      if (isActionActive('move_up')) depth -= 1; // 往地图深处（往里走）
      if (isActionActive('move_down')) depth += 1; // 往前方（往屏幕底部）

      return { x, depth };
    },

    consumeJump() {
      if (jumpBufferTimer > 0) {
        jumpBufferTimer = 0;
        return true;
      }
      return false;
    },

    hasJumpBuffered() {
      return jumpBufferTimer > 0;
    },

    triggerJump() {
      jumpBufferTimer = jumpBufferDuration;
    },

    isCrouchHeld() {
      const { x } = this.getMovementAxis();
      // 不在水平移动，且按下了下蹲动作键（S / ArrowDown / C）
      return x === 0 && isActionActive('crouch');
    },

    getActiveKeys() {
      return activeKeys;
    },

    simulateKeyDown(keyOrCode: string) {
      const wasActive = activeKeys.has(keyOrCode);
      activeKeys.add(keyOrCode);
      if (!wasActive && matchesAction('jump', keyOrCode)) {
        jumpBufferTimer = jumpBufferDuration;
      }
    },

    simulateKeyUp(keyOrCode: string) {
      activeKeys.delete(keyOrCode);
    },

    update(dtMs: number) {
      if (jumpBufferTimer > 0) {
        jumpBufferTimer = Math.max(0, jumpBufferTimer - dtMs);
      }
    },

    reset() {
      activeKeys.clear();
      jumpBufferTimer = 0;
    },

    destroy() {
      activeKeys.clear();
      jumpBufferTimer = 0;
      if (target && typeof target.removeEventListener === 'function') {
        target.removeEventListener('keydown', handleKeyDown as EventListener);
        target.removeEventListener('keyup', handleKeyUp as EventListener);
        target.removeEventListener('blur', handleBlur as EventListener);
      }
    },
  };
}
