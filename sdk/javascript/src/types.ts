/** FindYourself TypeScript SDK 类型定义 */

export interface RatingSummary {
  average_rating: number;
  rating_count: number;
  score: number;
  distribution?: Record<string, number>;
}

export interface PluginCard {
  skill_id: string;
  name: string;
  version: string;
  domain: string;
  source: string;
  license: string;
  package_hash: string;
  gate_profile: 'plugin' | 'instruction';
  signature_verified: boolean;
  capabilities: string[];
  risk_level: 'low' | 'medium' | 'high';
  risk_reasons: string[];
  rating: RatingSummary;
}

export interface TemplateCard {
  template_id: string;
  name: string;
  scenario: string;
  scenario_label?: string;
  summary: string;
  quality_tier: string;
  member_count: number;
  is_factory: boolean;
  rating: RatingSummary;
}

export interface SecurityFinding {
  severity: 'low' | 'medium' | 'high';
  code: string;
  message: string;
}

export interface SecurityReviewReport {
  passed: boolean;
  risk_level: 'low' | 'medium' | 'high';
  checksum_verified: boolean;
  can_import: boolean;
  requested_tools: string[];
  dangerous_tools: string[];
  sensitive_tools: string[];
  findings: SecurityFinding[];
  requires_user_confirmation: boolean;
}

export interface CodeSymbol {
  name: string;
  kind: string;
  file_path: string;
  start_line: number;
  end_line: number;
  signature: string;
  docstring?: string;
}

export interface ContextSlice {
  file_path: string;
  start_line: number;
  end_line: number;
  symbol_name: string;
  symbol_kind: string;
  relevance_score: number;
  content: string;
}

export interface ContextAssembly {
  prompt_context: string;
  total_chars: number;
  max_chars: number;
  slice_count: number;
  included_slices: Array<{
    file_path: string;
    symbol_name: string;
    lines: string;
    relevance: number;
  }>;
}
