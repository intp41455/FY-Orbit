-- ==============================================================================
-- FY Orbit · 星轨 —— Supabase / PostgreSQL 云端全量初始化 DDL
-- 覆盖 88 张核心实体表、外键约束、索引与审计结构
-- ==============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Table: agent_control_capabilities
CREATE TABLE IF NOT EXISTS agent_control_capabilities (
	id VARCHAR(64) NOT NULL, 
	agent_host VARCHAR(64) NOT NULL, 
	provider_id VARCHAR(64) NOT NULL, 
	capabilities JSON NOT NULL, 
	supports_per_member_model BOOLEAN NOT NULL, 
	usage_metering VARCHAR(16) NOT NULL, 
	probe_source VARCHAR(64) NOT NULL, 
	reason TEXT NOT NULL, 
	probed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_agent_control_capabilities PRIMARY KEY (id), 
	CONSTRAINT ck_agent_control_capabilities_capability_usage_metering CHECK (usage_metering IN ('verified', 'unsupported', 'unknown'))
);

-- Table: agent_instances
CREATE TABLE IF NOT EXISTS agent_instances (
	id VARCHAR(64) NOT NULL, 
	team_id VARCHAR(64) NOT NULL, 
	role VARCHAR(64) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	agent_host VARCHAR(64) NOT NULL, 
	provider_id VARCHAR(64) NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	session_id VARCHAR(64) NOT NULL, 
	parent_task_id VARCHAR(64), 
	root_task_id VARCHAR(64), 
	subtask_id VARCHAR(64), 
	model_binding_id VARCHAR(64), 
	requested_model VARCHAR(160) NOT NULL, 
	effective_model VARCHAR(160), 
	effective_confidence VARCHAR(16) NOT NULL, 
	depends_on JSON NOT NULL, 
	run_batch INTEGER NOT NULL, 
	depth INTEGER NOT NULL, 
	steps INTEGER NOT NULL, 
	max_steps INTEGER NOT NULL, 
	budget_reserved_usd NUMERIC(12, 6) NOT NULL, 
	budget_spent_usd NUMERIC(12, 6) NOT NULL, 
	blocked_reason TEXT NOT NULL, 
	current_goal TEXT NOT NULL, 
	plan_version INTEGER NOT NULL, 
	last_event_seq INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_agent_instances PRIMARY KEY (id), 
	CONSTRAINT ck_agent_instances_agent_state CHECK (state IN ('draft', 'starting', 'running', 'blocked', 'paused', 'waiting_rework', 'completed', 'failed', 'cancelled', 'unknown_needs_reconciliation')), 
	CONSTRAINT ck_agent_instances_agent_run_batch_non_negative CHECK (run_batch >= 0), 
	CONSTRAINT uq_agent_team_role UNIQUE (team_id, role), 
	CONSTRAINT fk_agent_instances_team_id_team_definitions FOREIGN KEY(team_id) REFERENCES team_definitions (id) ON DELETE CASCADE
);

-- Table: agent_leases
CREATE TABLE IF NOT EXISTS agent_leases (
	id VARCHAR(64) NOT NULL, 
	agent_id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_agent_leases PRIMARY KEY (id), 
	CONSTRAINT ck_agent_leases_ck_lease_state CHECK (state IN ('active', 'released', 'expired', 'revoked')), 
	CONSTRAINT fk_agent_leases_agent_id_agents FOREIGN KEY(agent_id) REFERENCES agents (id)
);

-- Table: agents
CREATE TABLE IF NOT EXISTS agents (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	semantic_version VARCHAR(80) NOT NULL, 
	capabilities JSON NOT NULL, 
	domains JSON NOT NULL, 
	endpoint_key VARCHAR(80) NOT NULL, 
	protocol_version VARCHAR(40) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	healthy BOOLEAN NOT NULL, 
	max_concurrency INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_agents PRIMARY KEY (id), 
	CONSTRAINT ck_agents_ck_agent_state CHECK (state IN ('candidate', 'registered', 'healthy', 'enabled', 'draining', 'offline', 'revoked')), 
	CONSTRAINT uq_agent_name_version UNIQUE (name, semantic_version)
);

-- Table: approval_team_members
CREATE TABLE IF NOT EXISTS approval_team_members (
	id VARCHAR(64) NOT NULL, 
	team_id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(200) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	granted_by VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_approval_team_members PRIMARY KEY (id), 
	CONSTRAINT ck_approval_team_members_ck_atm_one_role CHECK (role IN ('requester', 'approver', 'member')), 
	CONSTRAINT ck_approval_team_members_ck_atm_state CHECK (state IN ('active', 'revoked')), 
	CONSTRAINT ck_approval_team_members_ck_atm_version_positive CHECK (version >= 1), 
	CONSTRAINT uq_atm_team_member UNIQUE (team_id, user_id), 
	CONSTRAINT fk_approval_team_members_team_id_approval_teams FOREIGN KEY(team_id) REFERENCES approval_teams (id) ON DELETE CASCADE
);

-- Table: approval_teams
CREATE TABLE IF NOT EXISTS approval_teams (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	created_by VARCHAR(200) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	archived_at TIMESTAMP WITH TIME ZONE, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_approval_teams PRIMARY KEY (id), 
	CONSTRAINT ck_approval_teams_ck_at_state CHECK (state IN ('active', 'archived')), 
	CONSTRAINT ck_approval_teams_ck_at_version_positive CHECK (version >= 1)
);

-- Table: archive_forks
CREATE TABLE IF NOT EXISTS archive_forks (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	source_thread_id VARCHAR(200) NOT NULL, 
	source_checkpoint_id VARCHAR(200) NOT NULL, 
	new_thread_id VARCHAR(200) NOT NULL, 
	snapshot_id VARCHAR(64) NOT NULL, 
	session_key VARCHAR(200) NOT NULL, 
	overrides JSON NOT NULL, 
	left_result JSON NOT NULL, 
	right_result JSON NOT NULL, 
	label VARCHAR(200) NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	discard_reason TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_archive_forks PRIMARY KEY (id)
);

-- Table: artifact_gate_checks
CREATE TABLE IF NOT EXISTS artifact_gate_checks (
	id VARCHAR(64) NOT NULL, 
	artifact_version_id VARCHAR(64) NOT NULL, 
	check_name VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	observed_digest VARCHAR(64), 
	evidence JSON NOT NULL, 
	detail TEXT NOT NULL, 
	ran_by VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_artifact_gate_checks PRIMARY KEY (id), 
	CONSTRAINT ck_artifact_gate_checks_ck_agc_status CHECK (status IN ('passed','failed')), 
	CONSTRAINT ck_artifact_gate_checks_ck_agc_positive_version CHECK (version >= 1), 
	CONSTRAINT ck_artifact_gate_checks_ck_agc_observed_digest_is_sha256 CHECK (observed_digest IS NULL OR length(observed_digest) = 64), 
	CONSTRAINT uq_agc_version_check UNIQUE (artifact_version_id, check_name), 
	CONSTRAINT fk_artifact_gate_checks_artifact_version_id_artifact_versions FOREIGN KEY(artifact_version_id) REFERENCES artifact_versions (id) ON DELETE CASCADE
);

-- Table: artifact_versions
CREATE TABLE IF NOT EXISTS artifact_versions (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	artifact_kind VARCHAR(32) NOT NULL, 
	artifact_id VARCHAR(200) NOT NULL, 
	version_no INTEGER NOT NULL, 
	content_digest VARCHAR(64) NOT NULL, 
	workspace_ref JSON NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	submitted_at TIMESTAMP WITH TIME ZONE, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	decided_by VARCHAR(200), 
	decision_note TEXT NOT NULL, 
	created_by VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_artifact_versions PRIMARY KEY (id), 
	CONSTRAINT ck_artifact_versions_ck_agv_kind CHECK (artifact_kind IN ('asset','code_bundle')), 
	CONSTRAINT ck_artifact_versions_ck_agv_state CHECK (state IN ('draft','in_review','verified','released','rejected','blocked')), 
	CONSTRAINT ck_artifact_versions_ck_agv_positive_version CHECK (version >= 1), 
	CONSTRAINT ck_artifact_versions_ck_agv_positive_version_no CHECK (version_no >= 1), 
	CONSTRAINT ck_artifact_versions_ck_agv_digest_is_sha256 CHECK (length(content_digest) = 64), 
	CONSTRAINT ck_artifact_versions_ck_agv_decided_shape CHECK ((state <> 'draft' AND decided_at IS NOT NULL) OR (state = 'draft' AND decided_at IS NULL)), 
	CONSTRAINT ck_artifact_versions_ck_agv_submitted_shape CHECK ((state <> 'draft' AND submitted_at IS NOT NULL) OR (state = 'draft' AND submitted_at IS NULL)), 
	CONSTRAINT uq_agv_artifact_version UNIQUE (artifact_kind, artifact_id, version_no)
);

-- Table: artifacts
CREATE TABLE IF NOT EXISTS artifacts (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64), 
	domain VARCHAR(16) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	size INTEGER NOT NULL, 
	media_type VARCHAR(120) NOT NULL, 
	verified BOOLEAN NOT NULL, 
	verifier VARCHAR(120), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_artifacts PRIMARY KEY (id), 
	CONSTRAINT ck_artifacts_ck_art_domain CHECK (domain IN ('personal', 'work', 'shared')), 
	CONSTRAINT fk_artifacts_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id)
);

-- Table: assets
CREATE TABLE IF NOT EXISTS assets (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	name VARCHAR(500) NOT NULL, 
	storage_path VARCHAR(1000) NOT NULL, 
	mime VARCHAR(200) NOT NULL, 
	size INTEGER NOT NULL, 
	meta JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_assets PRIMARY KEY (id), 
	CONSTRAINT ck_assets_asset_kind CHECK (kind IN ('image', 'audio', 'music', 'doc'))
);

-- Table: audit_anchors
CREATE TABLE IF NOT EXISTS audit_anchors (
	id VARCHAR(64) NOT NULL, 
	seq INTEGER NOT NULL, 
	storage VARCHAR(120) NOT NULL, 
	head_hash VARCHAR(64) NOT NULL, 
	evidence JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_audit_anchors PRIMARY KEY (id)
);

-- Table: audit_events
CREATE TABLE IF NOT EXISTS audit_events (
	id VARCHAR(64) NOT NULL, 
	seq INTEGER NOT NULL, 
	actor VARCHAR(200) NOT NULL, 
	action VARCHAR(80) NOT NULL, 
	target VARCHAR(200), 
	details JSON NOT NULL, 
	previous_hash VARCHAR(64) NOT NULL, 
	hash VARCHAR(64) NOT NULL, 
	CONSTRAINT pk_audit_events PRIMARY KEY (id), 
	CONSTRAINT uq_audit_events_seq UNIQUE (seq)
);

-- Table: auth_sessions
CREATE TABLE IF NOT EXISTS auth_sessions (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	csrf_secret VARCHAR(64) NOT NULL, 
	token_hash VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	rotation_version INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_auth_sessions PRIMARY KEY (id)
);

-- Table: avatar_profiles
CREATE TABLE IF NOT EXISTS avatar_profiles (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	portrait JSON NOT NULL, 
	params JSON NOT NULL, 
	base_signature JSON NOT NULL, 
	overrides JSON NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	params_fingerprint VARCHAR(64) NOT NULL, 
	engine_version VARCHAR(32) NOT NULL, 
	likeness_score INTEGER, 
	likeness_note TEXT NOT NULL, 
	is_house_avatar BOOLEAN NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_avatar_profiles PRIMARY KEY (id), 
	CONSTRAINT ck_avatar_profiles_avatar_state CHECK (state IN ('draft', 'confirmed')), 
	CONSTRAINT ck_avatar_profiles_avatar_likeness_range CHECK (likeness_score IS NULL OR (likeness_score >= 1 AND likeness_score <= 10)), 
	CONSTRAINT ck_avatar_profiles_avatar_version_positive CHECK (version >= 1)
);

-- Table: budget_ledger
CREATE TABLE IF NOT EXISTS budget_ledger (
	id VARCHAR(64) NOT NULL, 
	reservation_id VARCHAR(64), 
	task_id VARCHAR(64) NOT NULL, 
	delta NUMERIC(12, 6) NOT NULL, 
	reason VARCHAR(64) NOT NULL, 
	seq INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_budget_ledger PRIMARY KEY (id), 
	CONSTRAINT fk_budget_ledger_reservation_id_budget_reservations FOREIGN KEY(reservation_id) REFERENCES budget_reservations (id)
);

-- Table: budget_reservations
CREATE TABLE IF NOT EXISTS budget_reservations (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	period VARCHAR(32) NOT NULL, 
	scope VARCHAR(32) NOT NULL, 
	amount NUMERIC(12, 6) NOT NULL, 
	currency VARCHAR(8) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	idempotency_key VARCHAR(120) NOT NULL, 
	settled_at TIMESTAMP WITH TIME ZONE, 
	released_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_budget_reservations PRIMARY KEY (id), 
	CONSTRAINT ck_budget_reservations_ck_res_state CHECK (state IN ('reserved', 'settled', 'released', 'cancelled', 'unknown')), 
	CONSTRAINT uq_res_idem UNIQUE (idempotency_key), 
	CONSTRAINT fk_budget_reservations_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id)
);

-- Table: bus_context
CREATE TABLE IF NOT EXISTS bus_context (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	room VARCHAR(200) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	ref TEXT NOT NULL, 
	content TEXT NOT NULL, 
	added_by VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_bus_context PRIMARY KEY (id), 
	CONSTRAINT ck_bus_context_bus_context_kind CHECK (kind IN ('file_ref', 'text'))
);

-- Table: cabin_interiors
CREATE TABLE IF NOT EXISTS cabin_interiors (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	house_id VARCHAR(32) NOT NULL, 
	layout JSON NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_cabin_interiors PRIMARY KEY (id), 
	CONSTRAINT uq_cabin_interior_owner_house UNIQUE (owner_id, house_id), 
	CONSTRAINT ck_cabin_interiors_cabin_interior_version_positive CHECK (version >= 1), 
	CONSTRAINT ck_cabin_interiors_cabin_interior_house_nonempty CHECK (length(house_id) > 0)
);

-- Table: canvas_events
CREATE TABLE IF NOT EXISTS canvas_events (
	id VARCHAR(64) NOT NULL, 
	instance_id VARCHAR(64) NOT NULL, 
	seq INTEGER NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64), 
	agent_id VARCHAR(64), 
	details JSON NOT NULL, 
	trace_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_canvas_events PRIMARY KEY (id), 
	CONSTRAINT fk_canvas_events_instance_id_canvas_instances FOREIGN KEY(instance_id) REFERENCES canvas_instances (id) ON DELETE CASCADE
);

-- Table: canvas_instances
CREATE TABLE IF NOT EXISTS canvas_instances (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	project_name VARCHAR(100) NOT NULL, 
	domain VARCHAR(16) NOT NULL, 
	template_id VARCHAR(32) NOT NULL, 
	orchestrator_id VARCHAR(64) NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	config JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_canvas_instances PRIMARY KEY (id), 
	CONSTRAINT ck_canvas_instances_ck_canvas_domain CHECK (domain IN ('personal', 'work')), 
	CONSTRAINT ck_canvas_instances_ck_canvas_state CHECK (state IN ('active', 'paused', 'completed', 'cancelled'))
);

-- Table: claw_conflicts
CREATE TABLE IF NOT EXISTS claw_conflicts (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	conflict_class VARCHAR(40) NOT NULL, 
	parties JSON NOT NULL, 
	detail TEXT NOT NULL, 
	level INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	resolution_note TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_claw_conflicts PRIMARY KEY (id)
);

-- Table: claw_decision_preferences
CREATE TABLE IF NOT EXISTS claw_decision_preferences (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	pattern_key VARCHAR(200) NOT NULL, 
	decision TEXT NOT NULL, 
	occurrences INTEGER NOT NULL, 
	last_task_id VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_claw_decision_preferences PRIMARY KEY (id), 
	CONSTRAINT uq_claw_pref_owner_pattern UNIQUE (owner_id, pattern_key)
);

-- Table: claw_fact_baseline
CREATE TABLE IF NOT EXISTS claw_fact_baseline (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	fact_key VARCHAR(200) NOT NULL, 
	fact_value TEXT NOT NULL, 
	verified_by VARCHAR(200) NOT NULL, 
	source_task_id VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_claw_fact_baseline PRIMARY KEY (id), 
	CONSTRAINT uq_claw_fact_owner_key UNIQUE (owner_id, fact_key)
);

-- Table: claw_gate_decisions
CREATE TABLE IF NOT EXISTS claw_gate_decisions (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	task_id VARCHAR(200) NOT NULL, 
	agent_role VARCHAR(64) NOT NULL, 
	layer VARCHAR(32) NOT NULL, 
	verdict VARCHAR(16) NOT NULL, 
	findings JSON NOT NULL, 
	fact_keys_checked JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_claw_gate_decisions PRIMARY KEY (id)
);

-- Table: claw_participation_modes
CREATE TABLE IF NOT EXISTS claw_participation_modes (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_claw_participation_modes PRIMARY KEY (id), 
	CONSTRAINT uq_claw_participation_owner UNIQUE (owner_id)
);

-- Table: collaboration_roles
CREATE TABLE IF NOT EXISTS collaboration_roles (
	id VARCHAR(64) NOT NULL, 
	record_kind VARCHAR(16) NOT NULL, 
	record_id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	user_id VARCHAR(200) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	granted_by VARCHAR(200) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_collaboration_roles PRIMARY KEY (id), 
	CONSTRAINT ck_collaboration_roles_ck_cr_role CHECK (role IN ('admin','manager','viewer')), 
	CONSTRAINT ck_collaboration_roles_ck_cr_state CHECK (state IN ('active','revoked')), 
	CONSTRAINT ck_collaboration_roles_ck_cr_version_positive CHECK (version >= 1), 
	CONSTRAINT ck_collaboration_roles_ck_cr_record_nonempty CHECK (length(record_id) > 0), 
	CONSTRAINT ck_collaboration_roles_ck_cr_user_nonempty CHECK (length(user_id) > 0), 
	CONSTRAINT uq_cr_record_user UNIQUE (record_kind, record_id, user_id)
);

-- Table: comments
CREATE TABLE IF NOT EXISTS comments (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	record_kind VARCHAR(16) NOT NULL, 
	record_id VARCHAR(64) NOT NULL, 
	author_id VARCHAR(200) NOT NULL, 
	parent_comment_id VARCHAR(64), 
	body TEXT NOT NULL, 
	mentions JSON NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	edited_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_comments PRIMARY KEY (id), 
	CONSTRAINT ck_comments_ck_comment_kind CHECK (record_kind IN ('task','canvas','artifact','memory')), 
	CONSTRAINT ck_comments_ck_comment_body_nonempty CHECK (length(body) > 0), 
	CONSTRAINT ck_comments_ck_comment_record_nonempty CHECK (length(record_id) > 0), 
	CONSTRAINT ck_comments_ck_comment_version_positive CHECK (version >= 1), 
	CONSTRAINT fk_comments_parent_comment_id_comments FOREIGN KEY(parent_comment_id) REFERENCES comments (id) ON DELETE SET NULL
);

-- Table: control_requests
CREATE TABLE IF NOT EXISTS control_requests (
	id VARCHAR(64) NOT NULL, 
	team_id VARCHAR(64) NOT NULL, 
	agent_instance_id VARCHAR(64), 
	operation VARCHAR(32) NOT NULL, 
	idempotency_key VARCHAR(120) NOT NULL, 
	expected_version INTEGER NOT NULL, 
	target_version INTEGER, 
	operator VARCHAR(200) NOT NULL, 
	scope JSON NOT NULL, 
	reason TEXT NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	result JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	applied_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_control_requests PRIMARY KEY (id), 
	CONSTRAINT ck_control_requests_control_operation CHECK (operation IN ('pause', 'resume', 'reassign', 'rework', 'cancel', 'switch_model', 'spawn')), 
	CONSTRAINT ck_control_requests_control_state CHECK (state IN ('pending', 'applied', 'rejected', 'unknown_needs_reconciliation', 'superseded')), 
	CONSTRAINT fk_control_requests_team_id_team_definitions FOREIGN KEY(team_id) REFERENCES team_definitions (id) ON DELETE CASCADE, 
	CONSTRAINT uq_control_requests_idempotency_key UNIQUE (idempotency_key)
);

-- Table: conversations
CREATE TABLE IF NOT EXISTS conversations (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	domain VARCHAR(16) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_conversations PRIMARY KEY (id), 
	CONSTRAINT ck_conversations_ck_conv_domain CHECK (domain IN ('personal', 'work', 'shared')), 
	CONSTRAINT ck_conversations_ck_conv_mode CHECK (mode IN ('listen', 'explore', 'research', 'engineering', 'creative'))
);

-- Table: dispatch_records
CREATE TABLE IF NOT EXISTS dispatch_records (
	id VARCHAR(64) NOT NULL, 
	instance_id VARCHAR(64) NOT NULL, 
	root_task_id VARCHAR(64) NOT NULL, 
	parent_task_id VARCHAR(64), 
	subtask_id VARCHAR(64) NOT NULL, 
	orchestrator_id VARCHAR(64) NOT NULL, 
	worker_id VARCHAR(64) NOT NULL, 
	idempotency_key VARCHAR(120) NOT NULL, 
	input_ref JSON NOT NULL, 
	goal VARCHAR(300) NOT NULL, 
	acceptance_criteria TEXT NOT NULL, 
	budget_slice NUMERIC(12, 6) NOT NULL, 
	deadline TIMESTAMP WITH TIME ZONE NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	attempts INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_dispatch_records PRIMARY KEY (id), 
	CONSTRAINT ck_dispatch_records_ck_dispatch_state CHECK (state IN ('planned', 'pending_adapter', 'dispatched', 'accepted', 'running', 'waiting_rework', 'completed', 'failed', 'cancelled', 'unknown_needs_reconciliation')), 
	CONSTRAINT fk_dispatch_records_instance_id_canvas_instances FOREIGN KEY(instance_id) REFERENCES canvas_instances (id) ON DELETE CASCADE, 
	CONSTRAINT uq_dispatch_records_idempotency_key UNIQUE (idempotency_key)
);

-- Table: file_revisions
CREATE TABLE IF NOT EXISTS file_revisions (
	id VARCHAR(64) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	rel_path VARCHAR(1000) NOT NULL, 
	revision INTEGER NOT NULL, 
	content_sha256 VARCHAR(64) NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	encoding VARCHAR(32) NOT NULL, 
	read_only BOOLEAN NOT NULL, 
	is_deleted BOOLEAN NOT NULL, 
	source_actor VARCHAR(200) NOT NULL, 
	source_task_id VARCHAR(64), 
	before_sha256 VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_file_revisions PRIMARY KEY (id), 
	CONSTRAINT workspace_path_revision UNIQUE (workspace_id, rel_path, revision), 
	CONSTRAINT ck_file_revisions_revision_positive CHECK (revision >= 1), 
	CONSTRAINT fk_file_revisions_workspace_id_workspace_manifests FOREIGN KEY(workspace_id) REFERENCES workspace_manifests (id) ON DELETE CASCADE
);

-- Table: grants
CREATE TABLE IF NOT EXISTS grants (
	id VARCHAR(64) NOT NULL, 
	source_domain VARCHAR(16) NOT NULL, 
	consumer_domain VARCHAR(16) NOT NULL, 
	record_ids JSON NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	scope_hash VARCHAR(64) NOT NULL, 
	destination VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_grants PRIMARY KEY (id), 
	CONSTRAINT ck_grants_ck_grant_srccol CHECK (source_domain IN ('personal', 'work', 'shared')), 
	CONSTRAINT ck_grants_ck_grant_consumcol CHECK (consumer_domain IN ('personal', 'work', 'shared')), 
	CONSTRAINT ck_grants_ck_grant_state CHECK (state IN ('active', 'revoked', 'expired')), 
	CONSTRAINT ck_grants_ck_grant_nonempty_records CHECK (json_array_length(record_ids) > 0), 
	CONSTRAINT ck_grants_ck_grant_destination CHECK (destination IN ('internal', 'gdrive'))
);

-- Table: handoff_packets
CREATE TABLE IF NOT EXISTS handoff_packets (
	id VARCHAR(64) NOT NULL, 
	instance_id VARCHAR(64) NOT NULL, 
	stage VARCHAR(64) NOT NULL, 
	goal VARCHAR(300) NOT NULL, 
	completed_items JSON NOT NULL, 
	artifact_refs JSON NOT NULL, 
	evidence_refs JSON NOT NULL, 
	unresolved_issues JSON NOT NULL, 
	risks JSON NOT NULL, 
	next_steps JSON NOT NULL, 
	source_task_id VARCHAR(64) NOT NULL, 
	source_worker_id VARCHAR(64) NOT NULL, 
	target_worker_id VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_handoff_packets PRIMARY KEY (id), 
	CONSTRAINT fk_handoff_packets_instance_id_canvas_instances FOREIGN KEY(instance_id) REFERENCES canvas_instances (id) ON DELETE CASCADE
);

-- Table: hitl_interrupts
CREATE TABLE IF NOT EXISTS hitl_interrupts (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	execution_id VARCHAR(200) NOT NULL, 
	checkpoint VARCHAR(200) NOT NULL, 
	context JSON NOT NULL, 
	options JSON NOT NULL, 
	decision VARCHAR(64), 
	resolution JSON, 
	status VARCHAR(24) NOT NULL, 
	decided_by VARCHAR(200), 
	reason TEXT NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_hitl_interrupts PRIMARY KEY (id), 
	CONSTRAINT ck_hitl_interrupts_ck_hitl_status CHECK (status IN ('pending','approved','rejected','cancelled','expired')), 
	CONSTRAINT ck_hitl_interrupts_ck_hitl_decided_shape CHECK (((status = 'pending' AND decision IS NULL) OR (status <> 'pending' AND decision IS NOT NULL))), 
	CONSTRAINT ck_hitl_interrupts_ck_hitl_decided_at_shape CHECK ((status <> 'pending' AND decided_at IS NOT NULL) OR (status = 'pending' AND decided_at IS NULL)), 
	CONSTRAINT ck_hitl_interrupts_ck_hitl_version_positive CHECK (version >= 1)
);

-- Table: hub_connections
CREATE TABLE IF NOT EXISTS hub_connections (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	"group" VARCHAR(16) NOT NULL, 
	preset_id VARCHAR(48) NOT NULL, 
	icon VARCHAR(16) NOT NULL, 
	description TEXT NOT NULL, 
	endpoint_config JSON NOT NULL, 
	secret_config JSON NOT NULL, 
	secret_fields JSON NOT NULL, 
	capabilities JSON NOT NULL, 
	manifest JSON, 
	params JSON, 
	state VARCHAR(24) NOT NULL, 
	last_health_at TIMESTAMP WITH TIME ZONE, 
	last_health_ok BOOLEAN, 
	last_health_detail TEXT NOT NULL, 
	last_health_latency_ms INTEGER, 
	preference INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_hub_connections PRIMARY KEY (id), 
	CONSTRAINT uq_hub_connection_owner_name UNIQUE (owner_id, name), 
	CONSTRAINT ck_hub_connections_hub_connection_kind CHECK (kind IN ('openai_chat', 'anthropic', 'mcp_server', 'http_webhook', 'knowledge_source', 'tool_plugin')), 
	CONSTRAINT ck_hub_connections_hub_connection_state CHECK (state IN ('active', 'disabled', 'needs_credentials', 'error')), 
	CONSTRAINT ck_hub_connections_hub_preference_range CHECK (preference >= 0 AND preference <= 10), 
	CONSTRAINT ck_hub_connections_hub_connection_version_positive CHECK (version >= 1)
);

-- Table: interruption_events
CREATE TABLE IF NOT EXISTS interruption_events (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	interruption_class VARCHAR(40) NOT NULL, 
	task_id VARCHAR(200) NOT NULL, 
	thread_id VARCHAR(200) NOT NULL, 
	message_id VARCHAR(200) NOT NULL, 
	stash_id VARCHAR(64) NOT NULL, 
	detail TEXT NOT NULL, 
	resume_policy VARCHAR(20) NOT NULL, 
	provider_fp VARCHAR(64) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	last_resume_note TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	resumed_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_interruption_events PRIMARY KEY (id)
);

-- Table: kb_chunks
CREATE TABLE IF NOT EXISTS kb_chunks (
	id VARCHAR(64) NOT NULL, 
	doc_id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	seq INTEGER NOT NULL, 
	content TEXT NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	CONSTRAINT pk_kb_chunks PRIMARY KEY (id), 
	CONSTRAINT fk_kb_chunks_doc_id_kb_documents FOREIGN KEY(doc_id) REFERENCES kb_documents (id) ON DELETE CASCADE
);

-- Table: kb_documents
CREATE TABLE IF NOT EXISTS kb_documents (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	name VARCHAR(500) NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	external_id VARCHAR(300) NOT NULL, 
	size INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	error VARCHAR(1000) NOT NULL, 
	chunk_count INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_kb_documents PRIMARY KEY (id), 
	CONSTRAINT ck_kb_documents_kb_document_status CHECK (status IN ('indexing', 'ready', 'failed'))
);

-- Table: memories
CREATE TABLE IF NOT EXISTS memories (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	domain VARCHAR(16) NOT NULL, 
	category VARCHAR(32) NOT NULL, 
	content TEXT NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	active BOOLEAN NOT NULL, 
	endorsed BOOLEAN NOT NULL, 
	hypothesis_status VARCHAR(16) NOT NULL, 
	tier VARCHAR(8) DEFAULT 'medium' NOT NULL, 
	session_id VARCHAR(64), 
	tier_expires_at TIMESTAMP WITH TIME ZONE, 
	session_count INTEGER DEFAULT '0' NOT NULL, 
	reinforcement_count INTEGER DEFAULT '0' NOT NULL, 
	last_used_at TIMESTAMP WITH TIME ZONE, 
	tier_changed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_memories PRIMARY KEY (id), 
	CONSTRAINT ck_memories_ck_mem_domain CHECK (domain IN ('personal', 'work', 'shared')), 
	CONSTRAINT ck_memories_ck_mem_hyp_status CHECK (hypothesis_status IN ('fact', 'hypothesis', 'theory', 'unverified')), 
	CONSTRAINT ck_memories_ck_mem_tier CHECK (tier IN ('short', 'medium', 'long')), 
	CONSTRAINT ck_memories_ck_mem_short_has_deadline CHECK (tier != 'short' OR tier_expires_at IS NOT NULL), 
	CONSTRAINT ck_memories_ck_mem_long_never_expires CHECK (tier != 'long' OR tier_expires_at IS NULL), 
	CONSTRAINT ck_memories_ck_mem_counters_nonneg CHECK (session_count >= 0 AND reinforcement_count >= 0)
);

-- Table: memory_revisions
CREATE TABLE IF NOT EXISTS memory_revisions (
	id VARCHAR(64) NOT NULL, 
	memory_id VARCHAR(64) NOT NULL, 
	version INTEGER NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	redacted BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_memory_revisions PRIMARY KEY (id), 
	CONSTRAINT fk_memory_revisions_memory_id_memories FOREIGN KEY(memory_id) REFERENCES memories (id)
);

-- Table: memory_tier_sessions
CREATE TABLE IF NOT EXISTS memory_tier_sessions (
	id VARCHAR(64) NOT NULL, 
	memory_id VARCHAR(64) NOT NULL, 
	session_id VARCHAR(64) NOT NULL, 
	first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_memory_tier_sessions PRIMARY KEY (id), 
	CONSTRAINT uq_mem_tier_session UNIQUE (memory_id, session_id), 
	CONSTRAINT fk_memory_tier_sessions_memory_id_memories FOREIGN KEY(memory_id) REFERENCES memories (id)
);

-- Table: messages
CREATE TABLE IF NOT EXISTS messages (
	id VARCHAR(64) NOT NULL, 
	conversation_id VARCHAR(64) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	source VARCHAR(200), 
	model VARCHAR(120), 
	client_message_id VARCHAR(120) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_messages PRIMARY KEY (id), 
	CONSTRAINT ck_messages_ck_msg_role CHECK (role IN ('user', 'assistant', 'system')), 
	CONSTRAINT uq_msg_conv_clientmsg UNIQUE (conversation_id, client_message_id), 
	CONSTRAINT fk_messages_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id)
);

-- Table: model_bindings
CREATE TABLE IF NOT EXISTS model_bindings (
	id VARCHAR(64) NOT NULL, 
	team_id VARCHAR(64) NOT NULL, 
	agent_instance_id VARCHAR(64), 
	scope VARCHAR(16) NOT NULL, 
	scope_key VARCHAR(64) NOT NULL, 
	provider_id VARCHAR(64) NOT NULL, 
	requested_model VARCHAR(160) NOT NULL, 
	effective_model VARCHAR(160), 
	effective_confidence VARCHAR(16) NOT NULL, 
	model_revision VARCHAR(120), 
	credential_ref VARCHAR(200) NOT NULL, 
	credential_configured BOOLEAN NOT NULL, 
	endpoint_ref VARCHAR(200) NOT NULL, 
	params JSON NOT NULL, 
	capability_snapshot JSON NOT NULL, 
	pricing_version VARCHAR(64) NOT NULL, 
	budget_reserved_usd NUMERIC(12, 6) NOT NULL, 
	frozen BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_model_bindings PRIMARY KEY (id), 
	CONSTRAINT ck_model_bindings_binding_scope CHECK (scope IN ('global', 'team', 'role', 'node')), 
	CONSTRAINT ck_model_bindings_binding_confidence CHECK (effective_confidence IN ('exact', 'unknown', 'auto', 'not_executed')), 
	CONSTRAINT fk_model_bindings_team_id_team_definitions FOREIGN KEY(team_id) REFERENCES team_definitions (id) ON DELETE CASCADE
);

-- Table: notifications
CREATE TABLE IF NOT EXISTS notifications (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	record_kind VARCHAR(16) NOT NULL, 
	record_id VARCHAR(64) NOT NULL, 
	comment_id VARCHAR(64) NOT NULL, 
	author_id VARCHAR(200) NOT NULL, 
	summary VARCHAR(240) NOT NULL, 
	read_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_notifications PRIMARY KEY (id), 
	CONSTRAINT ck_notifications_ck_notif_kind CHECK (kind IN ('mention', 'comment_reply')), 
	CONSTRAINT ck_notifications_ck_notif_version_positive CHECK (version >= 1), 
	CONSTRAINT ck_notifications_ck_notif_record_nonempty CHECK (length(record_id) > 0), 
	CONSTRAINT uq_notif_recipient_comment UNIQUE (owner_id, comment_id, kind)
);

-- Table: operations
CREATE TABLE IF NOT EXISTS operations (
	id VARCHAR(64) NOT NULL, 
	proposal_id VARCHAR(64) NOT NULL, 
	idempotency_key VARCHAR(120) NOT NULL, 
	op_type VARCHAR(32) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	target VARCHAR(300), 
	payload_digest VARCHAR(64) NOT NULL, 
	external_id VARCHAR(200), 
	external_state VARCHAR(64), 
	attempt INTEGER NOT NULL, 
	last_error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_operations PRIMARY KEY (id), 
	CONSTRAINT ck_operations_ck_op_state CHECK (state IN ('pending', 'claimed', 'succeeded', 'failed', 'unknown')), 
	CONSTRAINT uq_op_one_per_proposal UNIQUE (proposal_id), 
	CONSTRAINT uq_op_idem UNIQUE (idempotency_key), 
	CONSTRAINT fk_operations_proposal_id_proposals FOREIGN KEY(proposal_id) REFERENCES proposals (id)
);

-- Table: orchestrator_leases
CREATE TABLE IF NOT EXISTS orchestrator_leases (
	id VARCHAR(64) NOT NULL, 
	root_task_id VARCHAR(64) NOT NULL, 
	orchestrator_id VARCHAR(64) NOT NULL, 
	fencing_token INTEGER NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	handoff_packet JSON NOT NULL, 
	takeover_summary JSON NOT NULL, 
	granted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_orchestrator_leases PRIMARY KEY (id), 
	CONSTRAINT ck_orchestrator_leases_state CHECK (state IN ('active', 'revoked', 'released')), 
	CONSTRAINT ck_orchestrator_leases_fencing_token_positive CHECK (fencing_token >= 1)
);

-- Table: preview_sessions
CREATE TABLE IF NOT EXISTS preview_sessions (
	id VARCHAR(64) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	process_pid INTEGER, 
	target_port INTEGER NOT NULL, 
	entry_path VARCHAR(500) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	health VARCHAR(16) NOT NULL, 
	access_subject VARCHAR(200) NOT NULL, 
	access_lease VARCHAR(64) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_preview_sessions PRIMARY KEY (id), 
	CONSTRAINT ck_preview_sessions_state CHECK (state IN ('starting', 'healthy', 'unhealthy', 'stopped', 'failed')), 
	CONSTRAINT ck_preview_sessions_kind CHECK (kind IN ('http', 'api')), 
	CONSTRAINT ck_preview_sessions_port_range CHECK (target_port > 0 AND target_port < 65536), 
	CONSTRAINT fk_preview_sessions_workspace_id_workspace_manifests FOREIGN KEY(workspace_id) REFERENCES workspace_manifests (id) ON DELETE CASCADE
);

-- Table: preview_sources
CREATE TABLE IF NOT EXISTS preview_sources (
	id VARCHAR(64) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	rel_path VARCHAR(1000) NOT NULL, 
	media_type VARCHAR(100) NOT NULL, 
	preview_session_id VARCHAR(64), 
	version VARCHAR(64) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	created_by VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_preview_sources PRIMARY KEY (id), 
	CONSTRAINT ck_preview_sources_psrc_kind CHECK (kind IN ('static', 'process', 'data')), 
	CONSTRAINT ck_preview_sources_psrc_state CHECK (state IN ('active', 'offline')), 
	CONSTRAINT fk_preview_sources_workspace_id_workspace_manifests FOREIGN KEY(workspace_id) REFERENCES workspace_manifests (id) ON DELETE CASCADE
);

-- Table: profile_evidence
CREATE TABLE IF NOT EXISTS profile_evidence (
	id VARCHAR(64) NOT NULL, 
	subject_id VARCHAR(64) NOT NULL, 
	source_segment_id VARCHAR(64) NOT NULL, 
	claim TEXT NOT NULL, 
	evidence_kind VARCHAR(32) NOT NULL, 
	polarity VARCHAR(16) NOT NULL, 
	proposed_dimension VARCHAR(64) NOT NULL, 
	confidence NUMERIC(5, 4) NOT NULL, 
	counter_evidence_refs JSON NOT NULL, 
	review_status VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_profile_evidence PRIMARY KEY (id), 
	CONSTRAINT ck_profile_evidence_ck_evidence_kind CHECK (evidence_kind IN ('self_report', 'observed_stat', 'assessment', 'third_party_statement', 'model_hypothesis')), 
	CONSTRAINT ck_profile_evidence_ck_evidence_polarity CHECK (polarity IN ('positive', 'neutral', 'negative')), 
	CONSTRAINT ck_profile_evidence_ck_evidence_review_status CHECK (review_status IN ('candidate', 'accepted', 'edited', 'rejected', 'uncertain')), 
	CONSTRAINT fk_profile_evidence_subject_id_profile_subjects FOREIGN KEY(subject_id) REFERENCES profile_subjects (id) ON DELETE CASCADE, 
	CONSTRAINT fk_profile_evidence_source_segment_id_source_segments FOREIGN KEY(source_segment_id) REFERENCES source_segments (id) ON DELETE CASCADE
);

-- Table: profile_feedback
CREATE TABLE IF NOT EXISTS profile_feedback (
	id VARCHAR(64) NOT NULL, 
	evidence_id VARCHAR(64) NOT NULL, 
	subject_id VARCHAR(64) NOT NULL, 
	action VARCHAR(32) NOT NULL, 
	feedback_text TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_profile_feedback PRIMARY KEY (id), 
	CONSTRAINT fk_profile_feedback_evidence_id_profile_evidence FOREIGN KEY(evidence_id) REFERENCES profile_evidence (id) ON DELETE CASCADE, 
	CONSTRAINT fk_profile_feedback_subject_id_profile_subjects FOREIGN KEY(subject_id) REFERENCES profile_subjects (id) ON DELETE CASCADE
);

-- Table: profile_imports
CREATE TABLE IF NOT EXISTS profile_imports (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	subject_id VARCHAR(64), 
	subject_candidates JSON NOT NULL, 
	original_asset_ref VARCHAR(300), 
	source_type VARCHAR(32) NOT NULL, 
	mime VARCHAR(64) NOT NULL, 
	size INTEGER NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	parser_version VARCHAR(32) NOT NULL, 
	consent_scope VARCHAR(64) NOT NULL, 
	privacy_domain VARCHAR(16) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	error_code VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_profile_imports PRIMARY KEY (id), 
	CONSTRAINT fk_profile_imports_subject_id_profile_subjects FOREIGN KEY(subject_id) REFERENCES profile_subjects (id) ON DELETE CASCADE
);

-- Table: profile_revisions
CREATE TABLE IF NOT EXISTS profile_revisions (
	id VARCHAR(64) NOT NULL, 
	subject_id VARCHAR(64) NOT NULL, 
	revision INTEGER NOT NULL, 
	profile_run_id VARCHAR(64), 
	core_summary JSON NOT NULL, 
	clusters JSON NOT NULL, 
	edges JSON NOT NULL, 
	metrics JSON NOT NULL, 
	limitations JSON NOT NULL, 
	user_review_state VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_profile_revisions PRIMARY KEY (id), 
	CONSTRAINT fk_profile_revisions_subject_id_profile_subjects FOREIGN KEY(subject_id) REFERENCES profile_subjects (id) ON DELETE CASCADE, 
	CONSTRAINT fk_profile_revisions_profile_run_id_profile_runs FOREIGN KEY(profile_run_id) REFERENCES profile_runs (id) ON DELETE SET NULL
);

-- Table: profile_runs
CREATE TABLE IF NOT EXISTS profile_runs (
	id VARCHAR(64) NOT NULL, 
	subject_id VARCHAR(64) NOT NULL, 
	input_snapshot_hash VARCHAR(64) NOT NULL, 
	schema_version VARCHAR(16) NOT NULL, 
	rule_version VARCHAR(32) NOT NULL, 
	model VARCHAR(100), 
	workflow_task_id VARCHAR(64), 
	state VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_profile_runs PRIMARY KEY (id), 
	CONSTRAINT fk_profile_runs_subject_id_profile_subjects FOREIGN KEY(subject_id) REFERENCES profile_subjects (id) ON DELETE CASCADE
);

-- Table: profile_subjects
CREATE TABLE IF NOT EXISTS profile_subjects (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	label VARCHAR(100) NOT NULL, 
	description TEXT NOT NULL, 
	confirmed BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_profile_subjects PRIMARY KEY (id), 
	CONSTRAINT ck_profile_subjects_ck_profile_subject_kind CHECK (kind IN ('self', 'person', 'project', 'org', 'work', 'topic', 'other'))
);

-- Table: prompt_render_logs
CREATE TABLE IF NOT EXISTS prompt_render_logs (
	id VARCHAR(64) NOT NULL, 
	template_name VARCHAR(200) NOT NULL, 
	version INTEGER NOT NULL, 
	variables_hash VARCHAR(64) NOT NULL, 
	scope VARCHAR(20), 
	task_id VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_prompt_render_logs PRIMARY KEY (id)
);

-- Table: prompt_templates
CREATE TABLE IF NOT EXISTS prompt_templates (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	latest_version INTEGER NOT NULL, 
	variables_schema JSON NOT NULL, 
	owner VARCHAR(200) NOT NULL, 
	scope VARCHAR(20) NOT NULL, 
	description TEXT NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_prompt_templates PRIMARY KEY (id), 
	CONSTRAINT ck_prompt_templates_ck_prompt_tpl_scope CHECK (scope IN ('platform', 'workbench', 'game_tree'))
);

-- Table: prompt_versions
CREATE TABLE IF NOT EXISTS prompt_versions (
	id VARCHAR(64) NOT NULL, 
	prompt_id VARCHAR(64) NOT NULL, 
	version INTEGER NOT NULL, 
	content TEXT NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	variables_schema JSON NOT NULL, 
	created_by VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_prompt_versions PRIMARY KEY (id), 
	CONSTRAINT uq_prompt_version_no UNIQUE (prompt_id, version), 
	CONSTRAINT fk_prompt_versions_prompt_id_prompt_templates FOREIGN KEY(prompt_id) REFERENCES prompt_templates (id)
);

-- Table: proposals
CREATE TABLE IF NOT EXISTS proposals (
	id VARCHAR(64) NOT NULL, 
	operation VARCHAR(32) NOT NULL, 
	target_id VARCHAR(64), 
	expected_version INTEGER NOT NULL, 
	payload JSON NOT NULL, 
	reason TEXT NOT NULL, 
	rollback TEXT NOT NULL, 
	digest VARCHAR(64) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	execution_id VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_proposals PRIMARY KEY (id), 
	CONSTRAINT ck_proposals_ck_prop_operation CHECK (operation IN ('memory.upsert', 'memory.delete', 'grant.add', 'grant.revoke', 'agent.register', 'agent.drain', 'skill.stage', 'skill.promote', 'skill.disable', 'config.model', 'conversation.delete', 'task.merge', 'task.release', 'prompt.stage', 'prompt.activate', 'prompt.disable')), 
	CONSTRAINT ck_proposals_ck_prop_status CHECK (status IN ('pending', 'approved_pending_execution', 'executing', 'executed', 'failed', 'unknown', 'rejected', 'expired')), 
	CONSTRAINT uq_prop_digest UNIQUE (digest)
);

-- Table: review_decisions
CREATE TABLE IF NOT EXISTS review_decisions (
	id VARCHAR(64) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	artifact_version VARCHAR(64) NOT NULL, 
	acceptance_items JSON NOT NULL, 
	evidence_refs JSON NOT NULL, 
	decision VARCHAR(16) NOT NULL, 
	reason TEXT NOT NULL, 
	decider VARCHAR(200) NOT NULL, 
	verification_id VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_review_decisions PRIMARY KEY (id), 
	CONSTRAINT ck_review_decisions_decision CHECK (decision IN ('pass', 'rework', 'blocked')), 
	CONSTRAINT fk_review_decisions_workspace_id_workspace_manifests FOREIGN KEY(workspace_id) REFERENCES workspace_manifests (id) ON DELETE CASCADE
);

-- Table: review_notes
CREATE TABLE IF NOT EXISTS review_notes (
	id VARCHAR(64) NOT NULL, 
	session_id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	page VARCHAR(500) NOT NULL, 
	mode VARCHAR(20) NOT NULL, 
	tag VARCHAR(40) NOT NULL, 
	element_id VARCHAR(200) NOT NULL, 
	element_class VARCHAR(300) NOT NULL, 
	text VARCHAR(300) NOT NULL, 
	selector TEXT NOT NULL, 
	dom_path JSON NOT NULL, 
	region JSON NOT NULL, 
	strokes JSON NOT NULL, 
	audio_ref VARCHAR(500) NOT NULL, 
	audio_transcript TEXT NOT NULL, 
	note TEXT NOT NULL, 
	marker VARCHAR(60) NOT NULL, 
	short_code VARCHAR(12) NOT NULL, 
	dom_digest VARCHAR(32) NOT NULL, 
	prev_dom_digest VARCHAR(32) NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	applied_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_review_notes PRIMARY KEY (id)
);

-- Table: review_sessions
CREATE TABLE IF NOT EXISTS review_sessions (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	page VARCHAR(500) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	route VARCHAR(30) NOT NULL, 
	iteration INTEGER NOT NULL, 
	refresh_state VARCHAR(20) NOT NULL, 
	refreshed_at TIMESTAMP WITH TIME ZONE, 
	refresh_note TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_review_sessions PRIMARY KEY (id)
);

-- Table: search_documents
CREATE TABLE IF NOT EXISTS search_documents (
	id VARCHAR(64) NOT NULL, 
	record_id VARCHAR(64) NOT NULL, 
	record_kind VARCHAR(24) NOT NULL, 
	domain VARCHAR(16) NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	embedding VECTOR(1536), 
	version INTEGER NOT NULL, 
	tombstoned_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_search_documents PRIMARY KEY (id), 
	CONSTRAINT ck_search_documents_ck_sdoc_domain CHECK (domain IN ('personal', 'work', 'shared'))
);

-- Table: service_identities
CREATE TABLE IF NOT EXISTS service_identities (
	id VARCHAR(64) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	semantic_version VARCHAR(80) NOT NULL, 
	task_binding VARCHAR(64), 
	domains JSON NOT NULL, 
	capabilities JSON NOT NULL, 
	secret_hash VARCHAR(64) NOT NULL, 
	lease_expires_at TIMESTAMP WITH TIME ZONE, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	state VARCHAR(16) NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_service_identities PRIMARY KEY (id), 
	CONSTRAINT ck_service_identities_ck_svcident_kind CHECK (kind IN ('worker', 'agent', 'tool_gateway', 'executor', 'release')), 
	CONSTRAINT ck_service_identities_ck_svcident_state CHECK (state IN ('active', 'revoked'))
);

-- Table: session_state_snapshots
CREATE TABLE IF NOT EXISTS session_state_snapshots (
	id VARCHAR(64) NOT NULL, 
	session_key VARCHAR(200) NOT NULL, 
	conversation_id VARCHAR(64), 
	schema_version INTEGER NOT NULL, 
	payload JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_session_state_snapshots PRIMARY KEY (id), 
	CONSTRAINT uq_session_state_snapshots_session_key UNIQUE (session_key)
);

-- Table: skill_evaluations
CREATE TABLE IF NOT EXISTS skill_evaluations (
	id VARCHAR(64) NOT NULL, 
	skill_id VARCHAR(64) NOT NULL, 
	subject_digest VARCHAR(64) NOT NULL, 
	static_passed BOOLEAN NOT NULL, 
	dynamic_passed BOOLEAN NOT NULL, 
	functional_passed BOOLEAN NOT NULL, 
	professional_passed BOOLEAN NOT NULL, 
	report JSON NOT NULL, 
	evaluator VARCHAR(120) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_skill_evaluations PRIMARY KEY (id), 
	CONSTRAINT fk_skill_evaluations_skill_id_skills FOREIGN KEY(skill_id) REFERENCES skills (id)
);

-- Table: skills
CREATE TABLE IF NOT EXISTS skills (
	id VARCHAR(64) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	semantic_version VARCHAR(80) NOT NULL, 
	package_hash VARCHAR(64) NOT NULL, 
	domain VARCHAR(16) NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	source VARCHAR(300) NOT NULL, 
	license VARCHAR(120) NOT NULL, 
	signature TEXT, 
	signature_algorithm VARCHAR(32), 
	signing_key_id VARCHAR(64), 
	signature_verified BOOLEAN NOT NULL, 
	scan_report JSON NOT NULL, 
	scan_passed BOOLEAN NOT NULL, 
	gate_profile VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_skills PRIMARY KEY (id), 
	CONSTRAINT ck_skills_ck_skill_domain CHECK (domain IN ('personal', 'work', 'shared')), 
	CONSTRAINT ck_skills_ck_skill_state CHECK (state IN ('staged', 'active', 'disabled', 'deprecated')), 
	CONSTRAINT ck_skills_ck_skill_gate_profile CHECK (gate_profile IN ('instruction', 'plugin')), 
	CONSTRAINT ck_skills_ck_skill_signature_shape CHECK (NOT signature_verified OR (signature IS NOT NULL AND signature_algorithm IS NOT NULL AND signing_key_id IS NOT NULL)), 
	CONSTRAINT uq_skill_name_version UNIQUE (name, semantic_version), 
	CONSTRAINT uq_skill_pkg_hash_immutable UNIQUE (package_hash)
);

-- Table: source_relations
CREATE TABLE IF NOT EXISTS source_relations (
	id VARCHAR(64) NOT NULL, 
	source_id VARCHAR(64) NOT NULL, 
	source_kind VARCHAR(24) NOT NULL, 
	derived_id VARCHAR(64) NOT NULL, 
	derived_kind VARCHAR(24) NOT NULL, 
	relation_type VARCHAR(16) NOT NULL, 
	permission_snapshot JSON NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_source_relations PRIMARY KEY (id), 
	CONSTRAINT ck_source_relations_ck_src_rel_type CHECK (relation_type IN ('derives', 'quotes', 'cites', 'supports')), 
	CONSTRAINT uq_src_derived_rel UNIQUE (source_id, derived_id, relation_type)
);

-- Table: source_segments
CREATE TABLE IF NOT EXISTS source_segments (
	id VARCHAR(64) NOT NULL, 
	import_id VARCHAR(64) NOT NULL, 
	conversation_id VARCHAR(100), 
	message_id VARCHAR(100), 
	speaker VARCHAR(100) NOT NULL, 
	raw_speaker VARCHAR(100), 
	occurred_at TIMESTAMP WITH TIME ZONE, 
	text_content TEXT NOT NULL, 
	content_hash VARCHAR(64) NOT NULL, 
	locator VARCHAR(200) NOT NULL, 
	CONSTRAINT pk_source_segments PRIMARY KEY (id), 
	CONSTRAINT fk_source_segments_import_id_profile_imports FOREIGN KEY(import_id) REFERENCES profile_imports (id) ON DELETE CASCADE
);

-- Table: stream_segments
CREATE TABLE IF NOT EXISTS stream_segments (
	id VARCHAR(64) NOT NULL, 
	message_id VARCHAR(200) NOT NULL, 
	seq INTEGER NOT NULL, 
	frame TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_stream_segments PRIMARY KEY (id), 
	CONSTRAINT uq_stream_segments_msg_seq UNIQUE (message_id, seq)
);

-- Table: sync_conflicts
CREATE TABLE IF NOT EXISTS sync_conflicts (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(64) NOT NULL, 
	entity_type VARCHAR(64) NOT NULL, 
	entity_id VARCHAR(64) NOT NULL, 
	local_version INTEGER NOT NULL, 
	local_payload JSON, 
	remote_version INTEGER NOT NULL, 
	remote_payload JSON, 
	resolution_status VARCHAR(32) NOT NULL CONSTRAINT ck_sync_conflicts_ck_sync_conflicts_status CHECK (resolution_status IN ('pending', 'resolved_local', 'resolved_remote')), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_sync_conflicts PRIMARY KEY (id)
);

-- Table: sync_journals
CREATE TABLE IF NOT EXISTS sync_journals (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(64) NOT NULL, 
	entity_type VARCHAR(64) NOT NULL, 
	entity_id VARCHAR(64) NOT NULL, 
	version INTEGER NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	payload JSON, 
	is_tombstone BOOLEAN NOT NULL, 
	device_id VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_sync_journals PRIMARY KEY (id), 
	CONSTRAINT uq_sync_journal_version UNIQUE (owner_id, entity_type, entity_id, version)
);

-- Table: sync_settings
CREATE TABLE IF NOT EXISTS sync_settings (
	owner_id VARCHAR(64) NOT NULL, 
	mode VARCHAR(32) NOT NULL CONSTRAINT ck_sync_settings_ck_sync_settings_mode CHECK (mode IN ('local_only', 'sync_opt_in')), 
	enabled_categories JSON NOT NULL, 
	paused BOOLEAN NOT NULL, 
	device_id VARCHAR(64), 
	last_synced_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_sync_settings PRIMARY KEY (owner_id)
);

-- Table: task_attempts
CREATE TABLE IF NOT EXISTS task_attempts (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	attempt_no INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	checkpoint_ref VARCHAR(300), 
	started_at TIMESTAMP WITH TIME ZONE, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_task_attempts PRIMARY KEY (id), 
	CONSTRAINT ck_task_attempts_ck_attempt_status CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')), 
	CONSTRAINT uq_attempt_task_no UNIQUE (task_id, attempt_no), 
	CONSTRAINT fk_task_attempts_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id)
);

-- Table: task_claims
CREATE TABLE IF NOT EXISTS task_claims (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	payload JSON NOT NULL, 
	capability VARCHAR(120) NOT NULL, 
	priority INTEGER NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	claimed_by VARCHAR(200) NOT NULL, 
	claimed_at TIMESTAMP WITH TIME ZONE, 
	lease_expires_at TIMESTAMP WITH TIME ZONE, 
	attempt INTEGER NOT NULL, 
	last_note TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_task_claims PRIMARY KEY (id)
);

-- Table: task_dependencies
CREATE TABLE IF NOT EXISTS task_dependencies (
	task_id VARCHAR(64) NOT NULL, 
	depends_on_task_id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_task_dependencies PRIMARY KEY (task_id, depends_on_task_id), 
	CONSTRAINT ck_task_dependencies_ck_task_dep_no_self CHECK (task_id <> depends_on_task_id), 
	CONSTRAINT fk_task_dependencies_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE, 
	CONSTRAINT fk_task_dependencies_depends_on_task_id_tasks FOREIGN KEY(depends_on_task_id) REFERENCES tasks (id) ON DELETE CASCADE
);

-- Table: task_events
CREATE TABLE IF NOT EXISTS task_events (
	id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	from_status VARCHAR(24), 
	to_status VARCHAR(24), 
	detail JSON, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_task_events PRIMARY KEY (id), 
	CONSTRAINT ck_task_events_ck_task_event_kind CHECK (kind IN ('created', 'status', 'progress', 'plan', 'dependency')), 
	CONSTRAINT fk_task_events_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
);

-- Table: tasks
CREATE TABLE IF NOT EXISTS tasks (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	parent_task_id VARCHAR(64), 
	root_task_id VARCHAR(64), 
	goal TEXT NOT NULL, 
	domain VARCHAR(16) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	strategy VARCHAR(16) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	stage VARCHAR(40) NOT NULL, 
	depth INTEGER NOT NULL, 
	steps INTEGER NOT NULL, 
	max_steps INTEGER NOT NULL, 
	max_depth INTEGER NOT NULL, 
	result JSON, 
	failure JSON, 
	deadline TIMESTAMP WITH TIME ZONE NOT NULL, 
	idempotency_key VARCHAR(100) NOT NULL, 
	progress_percent INTEGER, 
	weight INTEGER DEFAULT '1' NOT NULL, 
	critical BOOLEAN DEFAULT '0' NOT NULL, 
	blocked_reason VARCHAR(500), 
	blocked_since TIMESTAMP WITH TIME ZONE, 
	planned_start TIMESTAMP WITH TIME ZONE, 
	planned_end TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_tasks PRIMARY KEY (id), 
	CONSTRAINT ck_tasks_ck_task_domain CHECK (domain IN ('personal', 'work', 'shared')), 
	CONSTRAINT ck_tasks_ck_task_mode CHECK (mode IN ('listen', 'explore', 'research', 'engineering', 'creative')), 
	CONSTRAINT ck_tasks_ck_task_strategy CHECK (strategy IN ('auto', 'single', 'delegate', 'workflow', 'parallel')), 
	CONSTRAINT ck_tasks_ck_task_status CHECK (status IN ('queued', 'running', 'waiting_input', 'waiting_approval', 'completed', 'failed', 'cancelled')), 
	CONSTRAINT ck_tasks_ck_task_stage CHECK (stage IN ('queued', 'requirements', 'planning', 'execution', 'awaiting_approval', 'awaiting_input', 'reconciling', 'review', 'completed', 'failed', 'cancelled')), 
	CONSTRAINT uq_task_owner_idem UNIQUE (owner_id, idempotency_key), 
	CONSTRAINT fk_tasks_parent_task_id_tasks FOREIGN KEY(parent_task_id) REFERENCES tasks (id)
);

-- Table: team_approval_requests
CREATE TABLE IF NOT EXISTS team_approval_requests (
	id VARCHAR(64) NOT NULL, 
	team_id VARCHAR(64) NOT NULL, 
	interrupt_id VARCHAR(64) NOT NULL, 
	requester_id VARCHAR(200) NOT NULL, 
	required_role VARCHAR(16) NOT NULL, 
	round INTEGER NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	detail JSON NOT NULL, 
	supersedes_id VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_team_approval_requests PRIMARY KEY (id), 
	CONSTRAINT ck_team_approval_requests_ck_tar_required_role CHECK (required_role IN ('requester', 'approver', 'member')), 
	CONSTRAINT ck_team_approval_requests_ck_tar_round_positive CHECK (round >= 1), 
	CONSTRAINT ck_team_approval_requests_ck_tar_version_positive CHECK (version >= 1), 
	CONSTRAINT fk_team_approval_requests_team_id_approval_teams FOREIGN KEY(team_id) REFERENCES approval_teams (id) ON DELETE CASCADE, 
	CONSTRAINT fk_team_approval_requests_interrupt_id_hitl_interrupts FOREIGN KEY(interrupt_id) REFERENCES hitl_interrupts (id) ON DELETE CASCADE
);

-- Table: team_definitions
CREATE TABLE IF NOT EXISTS team_definitions (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	mode VARCHAR(32) NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	canvas_instance_id VARCHAR(64), 
	root_task_id VARCHAR(64), 
	coordinator_role VARCHAR(64) NOT NULL, 
	members JSON NOT NULL, 
	role_bindings JSON NOT NULL, 
	default_binding JSON NOT NULL, 
	budget_ref JSON NOT NULL, 
	permission_ref JSON NOT NULL, 
	plan_version INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	last_change_reason TEXT NOT NULL, 
	last_changed_by VARCHAR(200) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_team_definitions PRIMARY KEY (id), 
	CONSTRAINT ck_team_definitions_team_mode CHECK (mode IN ('system_managed', 'product_native')), 
	CONSTRAINT ck_team_definitions_team_state CHECK (state IN ('draft', 'validating', 'ready', 'running', 'paused', 'completed', 'failed', 'cancelled')), 
	CONSTRAINT ck_team_definitions_team_plan_version_positive CHECK (plan_version >= 1), 
	CONSTRAINT ck_team_definitions_team_version_positive CHECK (version >= 1)
);

-- Table: team_events
CREATE TABLE IF NOT EXISTS team_events (
	id VARCHAR(64) NOT NULL, 
	team_id VARCHAR(64) NOT NULL, 
	seq INTEGER NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	task_id VARCHAR(64), 
	agent_instance_id VARCHAR(64), 
	run_batch INTEGER, 
	source VARCHAR(64) NOT NULL, 
	details JSON NOT NULL, 
	evidence_refs JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_team_events PRIMARY KEY (id), 
	CONSTRAINT fk_team_events_team_id_team_definitions FOREIGN KEY(team_id) REFERENCES team_definitions (id) ON DELETE CASCADE
);

-- Table: terminal_sessions
CREATE TABLE IF NOT EXISTS terminal_sessions (
	id VARCHAR(64) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	actor_identity VARCHAR(200) NOT NULL, 
	shell VARCHAR(300) NOT NULL, 
	pty_backend VARCHAR(64) NOT NULL, 
	pid INTEGER, 
	cols INTEGER NOT NULL, 
	rows INTEGER NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	exit_code INTEGER, 
	timeout_seconds FLOAT NOT NULL, 
	event_cursor INTEGER NOT NULL, 
	stop_reason VARCHAR(200), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT pk_terminal_sessions PRIMARY KEY (id), 
	CONSTRAINT ck_terminal_sessions_state CHECK (state IN ('created', 'running', 'exited', 'stopped', 'timeout', 'failed')), 
	CONSTRAINT fk_terminal_sessions_workspace_id_workspace_manifests FOREIGN KEY(workspace_id) REFERENCES workspace_manifests (id) ON DELETE CASCADE
);

-- Table: tombstones
CREATE TABLE IF NOT EXISTS tombstones (
	id VARCHAR(64) NOT NULL, 
	target_id VARCHAR(64) NOT NULL, 
	target_kind VARCHAR(32) NOT NULL, 
	reason VARCHAR(300) NOT NULL, 
	deleted_by VARCHAR(200) NOT NULL, 
	dep_graph_hash VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_tombstones PRIMARY KEY (id)
);

-- Table: user_consents
CREATE TABLE IF NOT EXISTS user_consents (
	id VARCHAR(64) NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	doc_id VARCHAR(80) NOT NULL, 
	doc_version VARCHAR(40) NOT NULL, 
	agreed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ip VARCHAR(64) NOT NULL, 
	CONSTRAINT pk_user_consents PRIMARY KEY (id), 
	CONSTRAINT fk_user_consents_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
);

-- Table: users
CREATE TABLE IF NOT EXISTS users (
	id VARCHAR(64) NOT NULL, 
	email VARCHAR(200) NOT NULL, 
	password_hash VARCHAR(200) NOT NULL, 
	display_name VARCHAR(120) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	plan VARCHAR(16) DEFAULT 'free' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	version INTEGER NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT ck_users_ck_user_status CHECK (status IN ('active', 'deleted', 'guest')), 
	CONSTRAINT ck_users_ck_user_plan CHECK (plan IN ('free', 'pro'))
);

-- Table: work_stashes
CREATE TABLE IF NOT EXISTS work_stashes (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	content TEXT NOT NULL, 
	content_type VARCHAR(100) NOT NULL, 
	metadata JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_work_stashes PRIMARY KEY (id)
);

-- Table: workspace_events
CREATE TABLE IF NOT EXISTS workspace_events (
	id VARCHAR(64) NOT NULL, 
	workspace_id VARCHAR(64) NOT NULL, 
	seq INTEGER NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	rel_path VARCHAR(1000), 
	revision INTEGER, 
	source_actor VARCHAR(200) NOT NULL, 
	source_task_id VARCHAR(64), 
	diff JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_workspace_events PRIMARY KEY (id), 
	CONSTRAINT fk_workspace_events_workspace_id_workspace_manifests FOREIGN KEY(workspace_id) REFERENCES workspace_manifests (id) ON DELETE CASCADE
);

-- Table: workspace_manifests
CREATE TABLE IF NOT EXISTS workspace_manifests (
	id VARCHAR(64) NOT NULL, 
	owner_id VARCHAR(200) NOT NULL, 
	project_name VARCHAR(120) NOT NULL, 
	data_domain VARCHAR(16) NOT NULL, 
	mode VARCHAR(16) NOT NULL, 
	authorized_root VARCHAR(1000) NOT NULL, 
	exec_identity VARCHAR(200) NOT NULL, 
	branch VARCHAR(200) NOT NULL, 
	task_refs JSON NOT NULL, 
	resource_limits JSON NOT NULL, 
	lease_id VARCHAR(64), 
	lease_expires_at TIMESTAMP WITH TIME ZONE, 
	state VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_workspace_manifests PRIMARY KEY (id), 
	CONSTRAINT ck_workspace_manifests_mode CHECK (mode IN ('local', 'cloud')), 
	CONSTRAINT ck_workspace_manifests_state CHECK (state IN ('active', 'paused', 'archived')), 
	CONSTRAINT ck_workspace_manifests_data_domain CHECK (data_domain IN ('personal', 'work', 'shared'))
);
