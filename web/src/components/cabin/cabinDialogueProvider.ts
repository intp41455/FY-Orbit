/**
 * 数码小屋台词通道接线：把 cabinConfig.setDialogueProvider 的接口位接到后端
 * 专属管家 Agent（/api/butler/*，见 api/butler.ts 与 api/routes/butler.py）。
 *
 * 诚实原则（本模块的存在意义）：
 *  - 仅当后端 status.model_configured === true 时安装模型 provider，台词来源标注「模型生成」；
 *  - 未配置 / status 不可达 / 单次 dialogue 调用失败（503 未配置、429 限频、网络异常）时，
 *    回退本地预生成台词池并把来源标注如实改回「预生成台词池」；
 *  - 绝不在未配置模型时把台词池伪装成模型生成。
 *
 * 渲染层（CabinStage / cabinScene）不动：resolveDialogue 只认 string | Promise<string>。
 * 可测性：createCabinDialogueController 接受注入的 api 仿件，vitest 直接分流断言。
 */
import { butlerApi } from '../../api/butler';
import {
  pickDialogueLine,
  setDialogueProvider,
  type DialogueSpeaker,
  type PersonalityId,
} from './cabinConfig';

/** 台词来源：「模型生成」= 后端管家 Agent；「预生成台词池」= 本地静态台词。 */
export type DialogueSource = 'model' | 'pool';

export type DialogueSourceListener = (source: DialogueSource) => void;

/** 与 butlerApi 结构同形的最小接口，测试可注入仿件。 */
export interface ButlerApiLike {
  status(): Promise<{ model_configured: boolean }>;
  dialogue(body: {
    speaker: string;
    personality: string;
    context?: Record<string, unknown>;
  }): Promise<{ line: string }>;
}

export interface CabinDialogueInstallOptions {
  /**
   * 每次生成台词时收集的非敏感场景信息（用户名 / 宠物名等）。
   * 画像与私人记忆数据一律禁止放入（后端也会做白名单过滤）。
   */
  context?: () => Record<string, unknown> | undefined;
}

/** 预生成台词池 provider（cabinConfig 的默认行为，显式封装便于回退）。 */
export function poolProvider(speaker: DialogueSpeaker, personality: PersonalityId): string {
  return pickDialogueLine(speaker, personality);
}

export interface CabinDialogueController {
  /** status 探测完成后的来源；未探测时为 null（此时默认 provider 本就是池）。 */
  getSource(): DialogueSource | null;
  /** 探测 status 并按需安装模型 provider；幂等可重复调用，返回最终来源。 */
  install(opts?: CabinDialogueInstallOptions): Promise<DialogueSource>;
  /** 来源变化订阅；返回取消订阅函数。 */
  subscribe(listener: DialogueSourceListener): () => void;
  /** 测试/卸载用：恢复默认池 provider 并清空来源。 */
  reset(): void;
}

export function createCabinDialogueController(
  options: { api?: ButlerApiLike } = {},
): CabinDialogueController {
  const api: ButlerApiLike = options.api ?? butlerApi;
  let source: DialogueSource | null = null;
  const listeners = new Set<DialogueSourceListener>();

  function setSource(next: DialogueSource): void {
    source = next;
    for (const listener of listeners) listener(next);
  }

  async function install(opts: CabinDialogueInstallOptions = {}): Promise<DialogueSource> {
    let configured = false;
    try {
      const status = await api.status();
      configured = status.model_configured === true;
    } catch {
      // status 不可达（未登录/网络故障）按未配置语义处理：诚实回退池。
      configured = false;
    }

    if (!configured) {
      setDialogueProvider(poolProvider);
      setSource('pool');
      return 'pool';
    }

    // 已配置模型：每次台词先走管家 Agent；单次失败回退池并翻回「预生成台词池」标注。
    setDialogueProvider((speaker, personality) =>
      Promise.resolve()
        .then(() => api.dialogue({ speaker, personality, context: opts.context?.() }))
        .then((res) => {
          setSource('model');
          return res.line;
        })
        .catch(() => {
          setSource('pool');
          return poolProvider(speaker, personality);
        }),
    );
    setSource('model');
    return 'model';
  }

  function getSource(): DialogueSource | null {
    return source;
  }

  function subscribe(listener: DialogueSourceListener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }

  function reset(): void {
    setDialogueProvider(poolProvider);
    source = null;
  }

  return { getSource, install, subscribe, reset };
}

/** CabinPage 使用的默认单例（真实 API）。 */
export const cabinDialogue = createCabinDialogueController();
