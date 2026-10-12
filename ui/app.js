const $ = id => document.getElementById(id);
let token, jobId = null, page = 1, pages = 1, currentRows = [], timer, loading = false, jobs = [], generation = 0;
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function score(value) {
  return `<span class="score ${value == null ? 'none' : value < 50 ? 'low' : value < 90 ? 'mid' : ''}">${value ?? '—'}</span>`;
}
function timing(value) { return value == null ? '—' : `${(value / 1000).toFixed(2)} s`; }
function metricLevel(key, value, device) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'unknown';
  const desktop = device === 'desktop';
  const thresholds = {
    'first-contentful-paint': desktop ? [1200,2500] : [1800,3000],
    'speed-index': desktop ? [3400,5000] : [4300,5800],
    'total-blocking-time': [200,600],
    'largest-contentful-paint': desktop ? [1200,2500] : [2500,4000],
    'cumulative-layout-shift': [0.1,0.25]
  };
  const [good, moderate] = thresholds[key];
  return value <= good ? 'good' : value <= moderate ? 'moderate' : 'poor';
}
const levelLabel = {good:'Good', moderate:'Needs improvement', poor:'Poor', unknown:'Unavailable'};
function renderMetric(key, name, row) {
  const value = row.metrics?.[key];
  const level = metricLevel(key, value, row.strategy);
  const formatted = value == null ? '—' : key === 'cumulative-layout-shift' ? value.toFixed(3) : key === 'total-blocking-time' ? `${value.toFixed(0)} ms` : timing(value);
  return `<div class="metric-card ${level}"><span>${name}</span><strong>${formatted}</strong><span class="severity">${levelLabel[level]}</span></div>`;
}
function renderIssues(issues) {
  return [...issues].sort((a,b) => a.score - b.score).map(issue => {
    const level = issue.score < 0.5 ? 'poor' : 'moderate';
    const savings = issue.details?.overallSavingsMs;
    return `<div class="issue ${level}"><div class="issue-heading"><h4>${escape(issue.title)}</h4><span class="severity">${level === 'poor' ? 'High priority' : 'Needs attention'}</span></div><small>${escape(issue.displayValue || '')}${typeof savings === 'number' && savings > 0 ? ` · Potential saving: ${timing(savings)}` : ''}</small><p>${escape(issue.description)}</p>${issue.details ? `<details><summary>Affected resources & audit details</summary><pre>${escape(JSON.stringify(issue.details,null,2))}</pre></details>` : ''}</div>`;
  }).join('') || '<p>No failed scored audits were returned.</p>';
}
async function history() {
  jobs = await api('/api/jobs');
  $('history').innerHTML = '<option value="">Select an audit</option>' + jobs.map(job => `<option value="${escape(job.id)}">${escape(job.url)} · ${escape(job.status)} · ${new Date(job.createdAt).toLocaleString()}</option>`).join('');
  $('history').value = jobId || '';
}
async function refresh() {
  if (!jobId || loading) return;
  loading = true;
  const selected = jobId, version = generation;
  try {
    const query = new URLSearchParams({page, size:$('size').value, search:$('search').value, device:$('device').value});
    const [status, results] = await Promise.all([api(`/api/jobs/${selected}`), api(`/api/jobs/${selected}/results?${query}`)]);
    if (selected !== jobId || version !== generation) return;
    $('progress-panel').hidden = false;
    $('status').textContent = status.status === 'running' ? status.phase === 'auditing' ? 'Analyzing pages' : 'Discovering pages' : status.status === 'failed' ? 'Finished with errors' : status.status === 'saved' ? 'Saved audit' : 'Audit complete';
    $('message').textContent = status.message;
    $('count').textContent = status.expected ? `${status.audited} / ${status.expected} audits` : 'Discovering URLs';
    if (status.expected) $('progress').value = status.audited / status.expected * 100;
    else if (status.status === 'running') $('progress').removeAttribute('value');
    else $('progress').value = 0;
    $('pages-stat').textContent = status.discovered;
    $('audits-stat').textContent = status.audited;
    $('score-stat').textContent = status.average ?? '—';
    $('errors-stat').textContent = status.errors;
    const warnings = [...(status.discovery.warnings || [])];
    if (status.discovery.truncated) warnings.unshift('Page limit reached. Increase the limit in a new audit to discover more URLs.');
    $('discovery-notes').hidden = !warnings.length;
    $('notes').innerHTML = warnings.map(w => `<p>${escape(w)}</p>`).join('');
    $('start').disabled = jobs.some(j => j.status === 'running') && status.status === 'running';
    for (const type of ['csv','json','comparison']) { $(type).hidden = !status.audited; $(type).href = `/api/jobs/${selected}/${type}`; }
    page = results.page; pages = results.pages; currentRows = results.items;
    $('empty').hidden = results.total > 0;
    $('table-wrap').hidden = !results.total;
    if (!results.total && status.audited) $('empty').innerHTML = '<h3>No matching pages</h3><p>Try another URL search or device filter.</p>';
    else if (!results.total) $('empty').innerHTML = `<span class="empty-icon">⌁</span><h3>${status.status === 'running' ? 'Your audit is in progress' : 'No audit results'}</h3><p>${status.status === 'running' ? 'Insights appear here as each page finishes.' : 'Review the status and discovery notes above.'}</p>`;
    $('rows').innerHTML = currentRows.map((row, i) => `<tr><td><span class="page-url" title="${escape(row.url)}">${escape(row.url)}</span><span class="device-label">${escape(row.strategy)}${row.error ? ' · Failed' : ''}</span></td>${['performance','accessibility','best-practices','seo'].map(k => `<td>${score(row.scores?.[k])}</td>`).join('')}<td>${timing(row.metrics?.['largest-contentful-paint'])}</td><td>${row.metrics?.['cumulative-layout-shift']?.toFixed(3) ?? '—'}</td><td><button class="secondary insight" data-index="${i}">Insights →</button></td></tr>`).join('');
    $('range').textContent = results.total ? `Showing ${(page - 1) * results.size + 1}–${Math.min(page * results.size, results.total)} of ${results.total} audits` : '0 audits';
    $('page-number').textContent = `${page} / ${pages}`;
    $('prev').disabled = page <= 1; $('next').disabled = page >= pages;
    if (status.status !== 'running') { clearInterval(timer); await history(); $('start').disabled = jobs.some(j => j.status === 'running'); }
  } catch (error) { $('form-error').textContent = error.message; }
  finally { loading = false; if (version !== generation && jobId) refresh(); }
}
function selectJob(id) {
  generation++; jobId = id; page = 1; clearInterval(timer);
  if (!id) return;
  refresh(); timer = setInterval(refresh, 2000);
}
$('audit-form').addEventListener('submit', async event => {
  event.preventDefault(); $('form-error').textContent = ''; $('start').disabled = true;
  try {
    const result = await api('/api/jobs', {method:'POST', headers:{'Content-Type':'application/json','X-Session-Token':token}, body:JSON.stringify({url:$('url').value, strategy:$('strategy').value, maxPages:Number($('limit').value)})});
    selectJob(result.id); await history();
  } catch (error) { $('form-error').textContent = error.message; $('start').disabled = false; }
});
$('history').addEventListener('change', () => selectJob($('history').value));
for (const id of ['device','size']) $(id).addEventListener('change', () => { generation++; page = 1; refresh(); });
let searchTimer;
$('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { generation++; page = 1; refresh(); }, 250); });
$('prev').addEventListener('click', () => { generation++; page--; refresh(); });
$('next').addEventListener('click', () => { generation++; page++; refresh(); });
$('close-detail').addEventListener('click', () => $('detail').close());
$('rows').addEventListener('click', async event => {
  const button = event.target.closest('.insight'); if (!button) return;
  const row = currentRows[Number(button.dataset.index)];
  $('detail-content').innerHTML = `<p class="detail-url">${escape(row.url)} · ${escape(row.strategy)}</p>`;
  $('detail').showModal();
  if (row.error) { $('detail-content').innerHTML += `<p class="error">${escape(row.error)}</p>`; return; }
  const metricNames = {'first-contentful-paint':'First Contentful Paint','largest-contentful-paint':'Largest Contentful Paint','total-blocking-time':'Total Blocking Time','cumulative-layout-shift':'Layout shift','speed-index':'Speed Index'};
  $('detail-content').innerHTML += `<p>Measured ${escape(row.fetchTime || 'at an unavailable time')}</p><div class="metric-grid">${Object.entries(metricNames).map(([key,name]) => renderMetric(key, name, row)).join('')}</div><h3>Opportunities & diagnostics (${row.issues?.length || 0})</h3>${renderIssues(row.issues || [])}<p class="insight-legend">Metric colors follow the report?s device thresholds. Issue priority uses the Lighthouse audit score; potential savings are estimates.</p><h3>Field data</h3><p>Real-user data is shown only when returned by Google.</p><details><summary>Page field data</summary><pre>${escape(JSON.stringify(row.fieldData,null,2))}</pre></details><details><summary>Origin field data</summary><pre>${escape(JSON.stringify(row.originFieldData,null,2))}</pre></details><details><summary>Run warnings</summary><pre>${escape(JSON.stringify(row.warnings,null,2))}</pre></details><details id="raw-detail"><summary>Complete Lighthouse insights</summary><pre id="raw-data">Open to load all returned audits, including passed audits.</pre></details>`;
  let fetched = false;
  const selected = jobId;
  $('raw-detail').addEventListener('toggle', async event => {
    if (!event.target.open || fetched) return; fetched = true;
    try { const data = await api(`/api/jobs/${selected}/detail?${new URLSearchParams({url:row.url,strategy:row.strategy})}`); if ($('raw-data')) $('raw-data').textContent = JSON.stringify(data,null,2); }
    catch(error) { if ($('raw-data')) $('raw-data').textContent = error.message; }
  });
});
(async () => {
  try { const config = await api('/api/config'); token = config.token; $('key-status').textContent = config.configured ? '● API key configured' : 'Add your API key to .env'; await history(); const active = jobs.find(j => j.status === 'running') || jobs[0]; if (active) { selectJob(active.id); $('history').value = active.id; } }
  catch(error) { $('form-error').textContent = error.message; }
})();
