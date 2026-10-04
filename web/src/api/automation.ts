/** W10-B · GUI 自动化权限面板后端通道（/api/automation/permissions）。 */
import { request } from './client';

export type AutomationMode = 'off' | 'readonly' | 'safe' | 'full';

export interface AutomationPermissionsView {
  mode: AutomationMode;
  expires_at: string | null;
  ttl_remaining_seconds: number | null;
  max_ttl_seconds: number;
  modes: string[];
  honesty_note: string;
}

export function fetchAutomationPermissions(): Promise<AutomationPermissionsView> {
  return request<AutomationPermissionsView>('/api/automation/permissions');
}

export function putAutomationPermissions(
  mode: AutomationMode,
  ttlSeconds?: number,
): Promise<AutomationPermissionsView> {
  return request<AutomationPermissionsView>('/api/automation/permissions', {
    method: 'PUT',
    body: ttlSeconds ? { mode, ttl_seconds: ttlSeconds } : { mode },
  });
}
