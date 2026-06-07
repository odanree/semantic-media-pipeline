/**
 * Test data factories — reusable across all test files.
 * Avoids duplication and keeps test files focused on assertions only.
 */

export function makeResult(overrides?: Partial<{
  id: string
  file_path: string
  file_type: string
  similarity: number
  timestamp: number
  frame_index: number
  audio_segment_start_sec: number | null
  audio_segment_end_sec: number | null
  audio_rms_energy: number | null
  user_vote: 1 | -1 | null
  vote_label: Record<string, number> | null
  vote_query: string | null
}>) {
  return {
    id: 'test-id',
    file_path: '/media/test.mp4',
    file_type: 'video',
    similarity: 0.85,
    ...overrides,
  }
}
