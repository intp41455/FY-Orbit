"""Route aggregation for the FastAPI application."""

from fastapi import APIRouter

from . import a2a, agent_dispatch, agent_teams, agents, assessments, auth, avatar, butler, cabin, cabin_gameplay, canvas, catalog, charts, conversations, dsl_canvas, export, git_repo, guest, health, inference, knowledge, media, memory, profiles, prompts, proposals, session_state, skills, stash, streaming, sync, tasks, tools, workbench, workflow_gen

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(tasks.router)
api_router.include_router(memory.router)
api_router.include_router(proposals.router)
api_router.include_router(assessments.router)
api_router.include_router(catalog.router)
api_router.include_router(export.router)
api_router.include_router(inference.router)
api_router.include_router(a2a.router)
api_router.include_router(agents.router)
api_router.include_router(skills.router)
api_router.include_router(media.router)
api_router.include_router(profiles.router)
api_router.include_router(canvas.router)
api_router.include_router(sync.router)
api_router.include_router(charts.router)
api_router.include_router(workbench.router)
api_router.include_router(agent_teams.router)
api_router.include_router(git_repo.router)
api_router.include_router(tools.router)  # P1-05 tool calling registry
api_router.include_router(streaming.router)
api_router.include_router(prompts.router)  # P1-06 prompt template library
api_router.include_router(stash.router)  # P1-04 work stash (记录暂存区)
api_router.include_router(dsl_canvas.router)  # P1-18 受限 DSL 画布
api_router.include_router(agent_dispatch.router)  # P1-20 子 Agent 派发验证
api_router.include_router(session_state.router)  # P1-21 会话状态持久化
api_router.include_router(butler.router)  # P2 数码小屋专属管家（真模型台词通道）
api_router.include_router(cabin.router)  # W1 数码小屋室内布置（家具布局 CRUD）
api_router.include_router(knowledge.router)  # W3 本地知识库 RAG（拖拽导入/检索/适配器）
api_router.include_router(workflow_gen.router)  # W5 工作流三视图（一句话生成/拖拽/导出）
api_router.include_router(avatar.router)  # W11 个性化像素角色生成系统（角色工坊/分享卡）
api_router.include_router(cabin_gameplay.router)  # W2 小屋玩法循环/探险/任务（服务端权威结算）
api_router.include_router(guest.router)  # W8 游客会话与账号三层分层（游客/注册/会员位）
from . import bus  # W7 Agent 通信总线（房间消息/共享上下文）
api_router.include_router(bus.router)  # W7 Agent 通信总线与共享上下文
from . import hub  # W6 超级中台适配器中心（统一万能适配层）
api_router.include_router(hub.router)  # W6 超级中台适配器中心
from . import assets  # W9 多模态与个人资产库（本地磁盘 + 生成通道）
api_router.include_router(assets.router)  # W9 个人资产库 / 生成通道
from . import automation  # W10-B GUI 自动化权限门（截图/点击/输入，默认关闭）
api_router.include_router(automation.router)  # W10-B /api/automation/permissions
from . import hitl  # 需求12 Human-in-the-loop 执行中断与恢复
api_router.include_router(hitl.router)  # 需求12 /api/hitl/interrupts
from . import team_approval  # 需求6 团队级权限与审批流
api_router.include_router(team_approval.router)  # 需求6 /api/team-approvals
from . import artifact_gate  # 需求7 产物版本门禁
api_router.include_router(artifact_gate.router)  # 需求7 /api/artifact-gates
from . import collaboration  # 需求15 多人协作闭环（评论/@人/通知/角色）
api_router.include_router(collaboration.router)  # 需求15 /api/collaboration

