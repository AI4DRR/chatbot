/* AI4DRR Chatbot — frontend configuration.
 * Same-origin by default: the app service serves this page at "/" and the API at "/api".
 * Set window.AI4DRR_API_BASE before this script (or edit API_BASE) to point elsewhere,
 * e.g. "/drr-chat/api" behind the Drupal proxy. No secrets belong here. */
window.AI4DRR = {
  API_BASE: window.AI4DRR_API_BASE || '/api',

  // Shown on the welcome screen. UI copy only — not answers, not data.
  SUGGESTIONS: [
    'What are the four priorities of the Sendai Framework?',
    'How is progress on Early Warnings for All measured?',
    'Summarise key findings from the latest GAR report',
    'What role do local governments play in urban resilience?',
  ],

  // Progress labels while a turn is in flight. The API returns one JSON body
  // after all calls complete (no streaming), so these advance on timers as an
  // indication of the pipeline's stages, not on real events.
  PROGRESS_STEPS: [
    { label: 'Understanding your question', after: 0 },
    { label: 'Searching the UNDRR knowledge base', after: 5000 },
    { label: 'Writing the answer with sources', after: 9000 },
  ],

  // Legacy convention kept from the previous frontend: a new session gets a
  // title from the first question after the first successful turn.
  TITLE_WORDS: 7,
  DEFAULT_TITLE: 'New Chat Sessions',
};
