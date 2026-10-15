import { request } from './client';

/** 与后端 `api/routes/legal.py::legal_notice` 的响应逐字对齐。 */
export interface LegalNotice {
  license: {
    id: string;
    holder: string;
    change_rule: string;
    license_file: string;
    notice_file: string;
  };
  notice_text: string;
  third_party: {
    backend: string[];
    backend_source: string;
    frontend: string[];
    frontend_source: string;
  };
}

export const legalApi = {
  notice: () => request<LegalNotice>('/api/legal/notice'),
};
