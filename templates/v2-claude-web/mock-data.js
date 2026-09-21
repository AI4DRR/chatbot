/* Mock data for UX concept v2. Illustrative placeholder content for layout
 * and interaction review only — not authoritative UNDRR guidance. */

window.MOCK = {
  user: { name: 'Amina Mwangi', initials: 'AM', org: 'UNDRR · ROAP' },

  starters: [
    { k: 'Explain', t: 'the four priorities of the Sendai Framework' },
    { k: 'Compare', t: 'MHEWS coverage in LDCs and SIDS' },
    { k: 'Draft', t: 'a briefing note on urban flood risk in South-East Asia' },
    { k: 'Summarise', t: 'key findings of the latest GAR report' },
  ],

  recents: [
    { id: 'c1', title: 'Early warning systems and Sendai Priority 4', when: 'Today', active: true },
    { id: 'c2', title: 'Heatwave preparedness checklist for cities', when: 'Today' },
    { id: 'c3', title: 'Sendai Framework mid-term review — key gaps', when: 'Yesterday' },
    { id: 'c4', title: 'Nature-based solutions for coastal risk', when: 'Yesterday' },
    { id: 'c5', title: 'Difference between hazard, exposure and vulnerability', when: '3 days ago' },
    { id: 'c6', title: 'GAR 2025 headline numbers', when: '4 days ago' },
    { id: 'c7', title: 'Draft talking points: Global Platform side event', when: '6 days ago' },
    { id: 'c8', title: 'Risk-informed development finance — examples', when: 'Last week' },
  ],

  conversation: {
    id: 'c1',
    title: 'Early warning systems and Sendai Priority 4',
    turns: [
      {
        user: 'How do multi-hazard early warning systems relate to the Sendai Framework, and what is the Early Warnings for All initiative trying to achieve?',
        answer: {
          n: 1, sources: [1, 2, 3, 4], time: '09:41',
          html: `
<p>Multi-hazard early warning systems (MHEWS) sit at the centre of the Sendai Framework's approach to preparedness. The Framework, adopted in 2015 for the period 2015–2030, makes early warning an explicit global target: <strong>Target G</strong> calls on countries to substantially increase the availability of, and access to, multi-hazard early warning systems and disaster risk information by 2030<button class="cite" data-src="1" type="button" aria-label="Source 1">1</button>.</p>
<p>Within the four Priorities for Action, MHEWS are mainly addressed under <strong>Priority 4 — enhancing disaster preparedness for effective response and to "Build Back Better"</strong>, but they also depend on Priority 1 (understanding risk), because a warning is only as good as the risk knowledge behind it<button class="cite" data-src="1" type="button" aria-label="Source 1">1</button><button class="cite" data-src="3" type="button" aria-label="Source 3">3</button>.</p>
<h3>The four pillars of an effective MHEWS</h3>
<ol>
  <li><strong>Disaster risk knowledge</strong> — systematic collection of data and risk assessments.</li>
  <li><strong>Detection, observation, monitoring, analysis and forecasting</strong> of hazards and possible consequences.</li>
  <li><strong>Warning dissemination and communication</strong> — official, timely, understandable warnings that reach everyone at risk.</li>
  <li><strong>Preparedness and response capabilities</strong> at all levels, so that people know what to do when a warning is issued<button class="cite" data-src="3" type="button" aria-label="Source 3">3</button>.</li>
</ol>
<h3>Early Warnings for All</h3>
<p>Launched by the UN Secretary-General in 2022, EW4All aims to ensure that <em>everyone on Earth is protected by early warning systems by the end of 2027</em>. Its Executive Action Plan organises work around the same four pillars, with WMO, UNDRR, ITU and IFRC each leading one<button class="cite" data-src="2" type="button" aria-label="Source 2">2</button>. The most recent global status assessment reports that just over half of countries now say they have a MHEWS in place, with coverage still lowest in least developed countries and small island developing states<button class="cite" data-src="4" type="button" aria-label="Source 4">4</button>.</p>
<p>In short: the Sendai Framework sets the <em>target</em>, the four-pillar model describes <em>what a good system looks like</em>, and EW4All is the <em>time-bound initiative</em> to close the coverage gap.</p>`,
        },
      },
      {
        user: 'Which of those pillars is usually the weakest in practice?',
        answer: {
          n: 2, sources: [4, 5, 3], time: '09:43',
          html: `
<p>Across recent assessments the weakest links are consistently <strong>Pillar 1 (risk knowledge)</strong> and <strong>Pillar 4 (preparedness and response capability)</strong>, rather than the forecasting technology itself<button class="cite" data-src="4" type="button" aria-label="Source 4">4</button>.</p>
<ul>
  <li><strong>Risk knowledge</strong> is often incomplete at local level: hazard maps exist, but exposure and vulnerability data — who lives where, in what kind of housing, with what means to act — are outdated or not shared across agencies<button class="cite" data-src="4" type="button" aria-label="Source 4">4</button>.</li>
  <li><strong>Last-mile dissemination and preparedness</strong> fails when warnings are issued but not understood or acted on: warnings that are not in local languages, or that reach mobile phones but not the people without them<button class="cite" data-src="5" type="button" aria-label="Source 5">5</button><button class="cite" data-src="3" type="button" aria-label="Source 3">3</button>.</li>
</ul>
<p>A practical framing for a country review is to ask, for each pillar: <em>Does the capability exist? Is it used routinely? Does it reach the most at-risk groups?</em> Most gaps appear in the third question<button class="cite" data-src="4" type="button" aria-label="Source 4">4</button>.</p>`,
        },
      },
    ],
  },

  sources: {
    1: { type: 'KB',  title: 'Sendai Framework for Disaster Risk Reduction 2015–2030', domain: 'undrr.org', url: 'https://www.undrr.org/publication/sendai-framework-disaster-risk-reduction-2015-2030', section: 'Global targets · Priority 4', year: '2015',
         snippet: 'Target (g): Substantially increase the availability of and access to multi-hazard early warning systems and disaster risk information and assessments to people by 2030.' },
    2: { type: 'KB',  title: 'Early Warnings for All — Executive Action Plan 2023–2027', domain: 'undrr.org', url: 'https://www.undrr.org/early-warnings-for-all', section: 'Executive summary', year: '2022',
         snippet: 'The initiative calls for the whole world to be covered by an early warning system by the end of 2027, structured around the four MHEWS pillars with designated pillar leads.' },
    3: { type: 'KB',  title: 'Multi-hazard early warning systems: a checklist', domain: 'preventionweb.net', url: 'https://www.preventionweb.net/publication/multi-hazard-early-warning-systems-checklist', section: 'Key elements', year: '2018',
         snippet: 'An effective people-centred MHEWS comprises four interrelated key elements: risk knowledge; detection, monitoring, analysis and forecasting; warning dissemination and communication; preparedness and response capabilities.' },
    4: { type: 'WEB', title: 'Global status of multi-hazard early warning systems — 2024 report', domain: 'wmo.int', url: 'https://wmo.int/publication-series/global-status-of-multi-hazard-early-warning-systems-2024', section: 'Key messages', year: '2024',
         snippet: 'Just over half of countries report having a multi-hazard early warning system. Coverage remains lowest among least developed countries and small island developing states.' },
    5: { type: 'WEB', title: 'Why warnings fail at the last mile: lessons from recent floods', domain: 'reliefweb.int', url: 'https://reliefweb.int/report/world/last-mile-early-warning-lessons', section: 'Findings', year: '2025',
         snippet: 'Warnings were issued on time but many households did not receive them in a language they understood, or did not know what action to take.' },
  },

  loadingSteps: ['Reading the question', 'Searching the UNDRR knowledge base', 'Checking recent web sources', 'Composing the answer'],

  error: {
    title: 'That didn’t go through',
    detail: 'The request timed out after 60 seconds. Your message is kept in the conversation — retry it, or edit and send again.',
    code: 'HTTP 504 · request 7f3a-…',
  },
};
