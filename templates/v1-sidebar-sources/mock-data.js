/* Mock data for the UX template. All content is illustrative placeholder copy
 * for evaluating layout and interaction — not authoritative UNDRR guidance. */

window.MOCK = {
  // Mock runtime configuration. Keys are named after the server-side settings
  // they will map to, so a later build can replace this object with values
  // injected from the environment (e.g. LOCAL_AUTH_ENABLED=false hides the
  // local sign-in form, registration and password reset; SSO stays).
  config: {
    LOCAL_AUTH_ENABLED: true,
  },

  user: { name: 'Amina Mwangi', initials: 'AM', org: 'UNDRR · ROAP' },

  suggestions: [
    'What are the four priorities of the Sendai Framework?',
    'How is progress on Early Warnings for All measured?',
    'Summarise key findings from the latest GAR report',
    'Draft a one-paragraph briefing note on urban flood risk in South-East Asia',
  ],

  history: [
    { group: 'Today', items: [
      { id: 's1', title: 'Early warning systems and Sendai Priority 4', active: true },
      { id: 's2', title: 'Heatwave preparedness checklist for cities' },
    ]},
    { group: 'Yesterday', items: [
      { id: 's3', title: 'Sendai Framework mid-term review — key gaps' },
      { id: 's4', title: 'Nature-based solutions for coastal risk' },
    ]},
    { group: 'Previous 7 days', items: [
      { id: 's5', title: 'Difference between hazard, exposure and vulnerability' },
      { id: 's6', title: 'GAR 2025 headline numbers' },
      { id: 's7', title: 'Draft talking points: Global Platform side event' },
      { id: 's8', title: 'Risk-informed development finance — examples' },
    ]},
  ],

  conversation: {
    id: 's1',
    title: 'Early warning systems and Sendai Priority 4',
    messages: [
      {
        role: 'user',
        text: 'How do multi-hazard early warning systems relate to the Sendai Framework, and what is the Early Warnings for All initiative trying to achieve?',
      },
      {
        role: 'assistant',
        sources: [1, 2, 3, 4],
        html: `
<p>Multi-hazard early warning systems (MHEWS) sit at the centre of the Sendai Framework's approach to preparedness. The Framework, adopted in 2015 for the period 2015–2030, makes early warning an explicit global target: <strong>Target G</strong> calls on countries to substantially increase the availability of, and access to, multi-hazard early warning systems and disaster risk information by 2030 <button class="cite" data-src="1" type="button">1</button>.</p>
<p>Within the four Priorities for Action, MHEWS are mainly addressed under <strong>Priority 4 — enhancing disaster preparedness for effective response and to "Build Back Better"</strong>, but they also depend on Priority 1 (understanding risk), because a warning is only as good as the risk knowledge behind it <button class="cite" data-src="1" type="button">1</button> <button class="cite" data-src="3" type="button">3</button>.</p>
<h4>The four pillars of an effective MHEWS</h4>
<ol>
  <li><strong>Disaster risk knowledge</strong> — systematic collection of data and risk assessments.</li>
  <li><strong>Detection, observation, monitoring, analysis and forecasting</strong> of hazards and possible consequences.</li>
  <li><strong>Warning dissemination and communication</strong> — official, timely, understandable warnings that reach everyone at risk.</li>
  <li><strong>Preparedness and response capabilities</strong> at all levels, so that people know what to do when a warning is issued <button class="cite" data-src="3" type="button">3</button>.</li>
</ol>
<h4>Early Warnings for All (EW4All)</h4>
<p>Launched by the UN Secretary-General in 2022, EW4All aims to ensure that <em>everyone on Earth is protected by early warning systems by the end of 2027</em>. The Executive Action Plan organises work around the same four pillars, with WMO, UNDRR, ITU and IFRC each leading one pillar <button class="cite" data-src="2" type="button">2</button>. The most recent global status assessment reports that just over half of countries now say they have a MHEWS in place, with coverage still lowest in least developed countries and small island developing states <button class="cite" data-src="4" type="button">4</button>.</p>
<p>In short: the Sendai Framework sets the <em>target</em> (Target G), the four-pillar model describes <em>what a good system looks like</em>, and EW4All is the <em>time-bound initiative</em> to close the coverage gap.</p>`,
        meta: { mode: 'SYNTHESIS', time: '09:41' },
      },
      {
        role: 'user',
        text: 'Which of those pillars is usually the weakest in practice?',
      },
      {
        role: 'assistant',
        sources: [4, 5, 3],
        html: `
<p>Across recent assessments the weakest links are consistently <strong>Pillar 1 (risk knowledge)</strong> and <strong>Pillar 4 (preparedness and response capability)</strong>, rather than the forecasting technology itself <button class="cite" data-src="4" type="button">4</button>.</p>
<ul>
  <li><strong>Risk knowledge</strong> is often incomplete at local level: hazard maps exist, but exposure and vulnerability data (who lives where, in what kind of housing, with what means to act) are outdated or not shared across agencies <button class="cite" data-src="4" type="button">4</button>.</li>
  <li><strong>Last-mile dissemination and preparedness</strong> fails when warnings are issued but not understood or acted on — for example, warnings that are not in local languages, or that reach mobile phones but not the people without them <button class="cite" data-src="5" type="button">5</button> <button class="cite" data-src="3" type="button">3</button>.</li>
</ul>
<p>A practical way to frame this for a country review is to ask, for each pillar: <em>Does the capability exist? Is it used routinely? Does it reach the most at-risk groups?</em> Most gaps appear in the third question <button class="cite" data-src="4" type="button">4</button>.</p>`,
        meta: { mode: 'SYNTHESIS', time: '09:43' },
      },
    ],
  },

  sources: [
    {
      n: 1, type: 'KB',
      title: 'Sendai Framework for Disaster Risk Reduction 2015–2030',
      domain: 'undrr.org',
      url: 'https://www.undrr.org/publication/sendai-framework-disaster-risk-reduction-2015-2030',
      snippet: 'Target (g): Substantially increase the availability of and access to multi-hazard early warning systems and disaster risk information and assessments to people by 2030.',
      section: 'Global targets · Priority 4',
    },
    {
      n: 2, type: 'KB',
      title: 'Early Warnings for All — Executive Action Plan 2023–2027',
      domain: 'undrr.org',
      url: 'https://www.undrr.org/early-warnings-for-all',
      snippet: 'The initiative calls for the whole world to be covered by an early warning system by the end of 2027, structured around the four MHEWS pillars with designated pillar leads.',
      section: 'Executive summary',
    },
    {
      n: 3, type: 'KB',
      title: 'Multi-hazard early warning systems: a checklist',
      domain: 'preventionweb.net',
      url: 'https://www.preventionweb.net/publication/multi-hazard-early-warning-systems-checklist',
      snippet: 'An effective people-centred MHEWS comprises four interrelated key elements: risk knowledge; detection, monitoring, analysis and forecasting; warning dissemination and communication; preparedness and response capabilities.',
      section: 'Key elements',
    },
    {
      n: 4, type: 'WEB',
      title: 'Global status of multi-hazard early warning systems — 2024 report',
      domain: 'wmo.int',
      url: 'https://wmo.int/publication-series/global-status-of-multi-hazard-early-warning-systems-2024',
      snippet: 'Just over half of countries report having a multi-hazard early warning system. Coverage remains lowest among least developed countries and small island developing states.',
      section: 'Key messages',
    },
    {
      n: 5, type: 'WEB',
      title: 'Why warnings fail at the last mile: lessons from recent floods',
      domain: 'reliefweb.int',
      url: 'https://reliefweb.int/report/world/last-mile-early-warning-lessons',
      snippet: 'Warnings were issued on time but many households did not receive them in a language they understood, or did not know what action to take.',
      section: 'Findings',
    },
  ],

  loadingSteps: ['Understanding the question', 'Searching UNDRR knowledge base', 'Checking recent online sources', 'Writing the answer'],

  error: {
    title: 'The assistant could not answer',
    detail: 'The request timed out after 60 seconds. Your message has not been lost — you can retry, or edit it and send again.',
    code: 'HTTP 504 · request_id 7f3a-…',
  },
};
