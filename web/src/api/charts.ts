/**
 * Charts, Astrology, Tarot, and Fortune API Client (B-Line Deterministic Endpoints)
 */

import { request } from './client';

export interface ChartRequest {
  birth_date: string;
  birth_time?: string | null;
  timezone_str?: string;
  longitude?: number | null;
  latitude?: number | null;
  location_name?: string | null;
  calendar?: string;
  system?: 'bazi' | 'western' | 'ziwei';
  house_system?: string;
  unknown_time?: boolean;
  gender?: string;
}

export interface ChartResult {
  chart_id: string;
  system: string;
  computed_at: string;
  engine_version: string;
  normalized_utc: string | null;
  unknown_time: boolean;
  is_external_imported: boolean;
  external_source: string | null;
  computed_data: Record<string, any>;
  warnings: string[];
  data_hash: string;
}

export interface InterpretRequest {
  chart_id: string;
  perspective?: 'psychological' | 'traditional' | 'comparative';
  user_notes?: string | null;
  authorized_domain?: string;
  include_web_search?: boolean;
  include_personal_memory?: boolean;
}

export interface InterpretationResult {
  chart_id: string;
  system: string;
  perspective: string;
  sections: Record<string, string>;
  web_citations: Array<Record<string, any>>;
  personal_citations: Array<Record<string, any>>;
  disclaimer: string;
}

export interface DailyFortuneRequest {
  birth_date: string;
  target_date?: string | null;
  birth_time?: string | null;
  timezone_str?: string;
}

export interface DailyFortuneResult {
  date: string;
  solar_date: string;
  lunar_date: string;
  day_pillar: string;
  day_element: string;
  user_element: string;
  relation: string;
  luck_score: number;
  favorable: string[];
  unfavorable: string[];
  oracle_message: string;
  lucky_color: string;
  lucky_direction: string;
  cabin_event: Record<string, any>;
  disclaimer: string;
}

export interface TarotCard {
  card_id: number;
  name: string;
  arcana: string;
  is_reversed: boolean;
  position_label: string;
  keywords: string[];
  meaning: string;
  guidance: string;
}

export interface TarotDrawRequest {
  spread?: 'single' | 'three_cards';
  question?: string | null;
  seed?: number | null;
}

export interface TarotDrawResult {
  draw_id: string;
  drawn_at: string;
  spread: string;
  question: string | null;
  cards: TarotCard[];
  overall_reading: string;
  cabin_interaction: Record<string, any>;
  disclaimer: string;
}

export interface SynastryRequest {
  chart_a: ChartRequest;
  chart_b: ChartRequest;
  relationship_type?: string;
}

export interface SynastryResult {
  compatibility_score: number;
  element_balance: Record<string, any>;
  synergy_points: string[];
  friction_points: string[];
  actionable_advice: string;
  disclaimer: string;
}

export const chartsApi = {
  computeChart: (data: ChartRequest) =>
    request<ChartResult>('/api/charts/compute', {
      method: 'POST',
      body: data,
    }),

  importChart: (data: { raw_text?: string; raw_json?: any; source_software?: string; system?: string }) =>
    request<ChartResult>('/api/charts/import', {
      method: 'POST',
      body: data,
    }),

  interpretChart: (data: InterpretRequest) =>
    request<InterpretationResult>('/api/charts/interpret', {
      method: 'POST',
      body: data,
    }),

  getDailyFortune: (data: DailyFortuneRequest) =>
    request<DailyFortuneResult>('/api/charts/fortune/daily', {
      method: 'POST',
      body: data,
    }),

  drawTarot: (data: TarotDrawRequest) =>
    request<TarotDrawResult>('/api/charts/tarot/draw', {
      method: 'POST',
      body: data,
    }),

  computeSynastry: (data: SynastryRequest) =>
    request<SynastryResult>('/api/charts/synastry', {
      method: 'POST',
      body: data,
    }),
};
