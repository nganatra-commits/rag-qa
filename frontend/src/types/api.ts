/**
 * Wire-format types mirroring backend/src/ragqa/api/schemas.py.
 * Keep in lockstep — these flow over HTTP.
 */

export type BindingMethod =
  | "explicit_reference"
  | "captioned"
  | "layout_anchored"
  | "section_floor"
  | "unbound";

export interface ImageRef {
  image_id: string;
  uri: string;
  cdn_url: string | null;
  page: number;
  bbox: number[];
  alt_text: string;
  ocr_text: string;
  caption: string;
  binding_method: BindingMethod;
  binding_score: number;
}

export interface Chunk {
  chunk_id: string;
  doc_id: string;
  doc_version: string;
  source_file: string;
  page_start: number;
  page_end: number;
  section_path: string[];
  element_type: string;
  text: string;
  images: ImageRef[];
  embedding_model: string;
  parser_version: string;
  vlm_version: string;
  indexed_at: string;
  content_hash: string;
}

export interface RetrievalHit {
  chunk: Chunk;
  score: number;
  rerank_score: number | null;
  rank: number;
}

export interface AnswerCitation {
  chunk_id: string;
  doc_id: string;
  section_path: string[];
  page_start: number;
  page_end: number;
}

export interface AnswerImage {
  image_id: string;
  cdn_url: string;
  page: number;
  caption: string;
  alt_text: string;
  chunk_id: string;
  binding_method: BindingMethod;
  binding_score: number;
}

export interface AnswerResponse {
  query: string;
  answer: string;
  citations: AnswerCitation[];
  images: AnswerImage[];
  referenced_image_ids: string[];
  chunks: Chunk[];
  is_refusal?: boolean;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
}

export interface RetrieveResponse {
  query: string;
  hits: RetrievalHit[];
  latency_ms: number;
}

export interface HealthResponse {
  status: string;
  version: string;
  index: string;
  namespace: string;
  indexed_chunks: number;
  indexed_vectors: number;
}
