'use strict';
const el = selector => document.querySelector(selector);
let token = sessionStorage.getItem('devpilot-token') || '';
let repositories = [], selected = null, request = null, poll = null, registering = false;
const incoming = new URLSearchParams(location.hash.slice(1)).get('access_token');
if (incoming) {
  token = incoming;
  sessionStorage.setItem('devpilot-token', token);
  history.replaceState(null, '', location.pathname);
}
function notify(text, error = false) {
  el('#message').textContent = text;
  el('#message').hidden = !text;
  el('#message').classList.toggle('error', error);
}
function signOut() {
  token = '';
  sessionStorage.removeItem('devpilot-token');
  clearTimeout(poll);
  request?.abort();
  request = null;
  selected = null;
  repositories = [];
  el('#repositories').replaceChildren();
  el('#results').replaceChildren();
  el('#repo-summary').hidden = true;
  el('#account').textContent = 'Sign in ↗';
  el('#search-scope').textContent = 'Select a repository to begin';
  el('#results-title').textContent = 'Sign in to explore your code';
  el('#result-count').textContent = '';
  el('#search-button').disabled = false;
}
function showAuth() { if (!el('#auth-dialog').open) el('#auth-dialog').showModal(); }
async function api(path, options = {}) {
  let response;
  try {
    response = await fetch('/api/v1' + path, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) }
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw Error('Cannot reach DevPilot. Start Docker Desktop and the DevPilot services, then refresh this page.');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/')) {
      signOut(); showAuth(); throw Error('Your session expired. Sign in to continue.');
    }
    throw Error(data.error?.message || (Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : data.detail) || 'The request failed. Please try again.');
  }
  return data;
}
function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}
function renderRepositories() {
  el('#repositories').replaceChildren();
  for (const repo of repositories) {
    const button = node('button', 'repo-button' + (selected?.id === repo.id ? ' selected' : ''));
    button.setAttribute('aria-pressed', String(selected?.id === repo.id));
    const text = node('span', '', repo.repo_name);
    text.append(node('small', '', repo.owner_name));
    button.append(node('span', '', '⌘'), text);
    button.onclick = () => choose(repo);
    el('#repositories').append(button);
  }
  if (!repositories.length) el('#repositories').append(node('p', 'muted sidebar-note', 'No repositories yet. Import one to get started.'));
}
function summary() {
  if (!selected) return;
  el('#repo-summary').hidden = false;
  el('#repo-name').textContent = selected.repo_name;
  el('#repo-owner').textContent = selected.owner_name + ' / ' + (selected.default_branch || 'default branch');
  el('#repo-status').textContent = { indexed: '● Indexed', indexing: '◌ Indexing', pending: '○ Not indexed', failed: '○ Needs attention' }[selected.status] || selected.status;
  el('#repo-commit').textContent = selected.last_indexed_sha?.slice(0, 7) || '';
  el('#search-scope').textContent = '⌘  Searching ' + selected.repo_name;
  el('#index-button').textContent = selected.status === 'indexed' ? 'Reindex ↻' : 'Index repository ↻';
  el('#index-button').disabled = selected.status === 'indexing';
  el('.suggestions').hidden = selected.repo_name !== 'BrainTumorClassifier';
}
function choose(repo) {
  clearTimeout(poll); request?.abort(); request = null;
  el('#search-button').disabled = false;
  selected = repo; renderRepositories(); summary(); notify('');
  el('#results-title').textContent = 'Explore ' + repo.repo_name;
  el('#result-count').textContent = '';
  const empty = node('div', 'empty');
  empty.append(node('div', 'empty-symbol', '⌕'), node('h3', '', repo.status === 'indexed' ? 'Your source, ready to explore.' : 'Prepare this repository for search'), node('p', '', repo.status === 'indexed' ? 'Choose an example above or ask your own question.' : 'Click Index repository to create searchable code chunks.'));
  el('#results').replaceChildren(empty);
}
async function loadRepositories() {
  if (!token) { showAuth(); return; }
  repositories = (await api('/repositories')).items;
  const next = repositories.find(r => r.id === selected?.id) || repositories.find(r => r.repo_name === 'BrainTumorClassifier') || repositories[0];
  if (next) {
    if (next.id === selected?.id) { selected = next; summary(); renderRepositories(); }
    else choose(next);
  } else renderRepositories();
}
async function initialize() {
  if (!token) return;
  try {
    const me = await api('/auth/me');
    el('#account').textContent = 'Sign out ↗'; el('#account').title = me.email;
    await loadRepositories();
  } catch (error) { notify(error.message, true); }
}
function highlight(line, language) {
  const fragment = document.createDocumentFragment();
  if (language !== 'python') { fragment.append(document.createTextNode(line)); return fragment; }
  const pattern = /(#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:def|class|return|import|from|if|else|for|in|with|as|None|True|False|and|or|not|while|try|except|raise|await|async)\b)/g;
  let last = 0;
  for (const match of line.matchAll(pattern)) {
    fragment.append(document.createTextNode(line.slice(last, match.index)));
    const kind = match[0].startsWith('#') ? 'comment' : /^["']/.test(match[0]) ? 'string' : 'keyword';
    fragment.append(node('span', 'token-' + kind, match[0]));
    last = match.index + match[0].length;
  }
  fragment.append(document.createTextNode(line.slice(last)));
  return fragment;
}
function renderResults(data, repo, duration) {
  el('#results').replaceChildren();
  el('#results-title').textContent = 'Relevant source code';
  el('#result-count').textContent = data.results.length + ' results · ' + duration + 's';
  if (!data.results.length) { notify('No matching chunks returned. Try a different question.'); return; }
  data.results.forEach((hit, index) => {
    const card = node('article', 'result-card'), top = node('div', 'result-top');
    const file = node('div', 'result-file'), details = node('div');
    details.append(node('strong', '', hit.path), node('div', 'line-label', 'Lines ' + hit.start_line + '–' + hit.end_line + ' · ' + (hit.language || 'text')));
    file.append(node('span', 'file-symbol', hit.language === 'python' ? 'PY' : '{ }'), details);
    const relevance = node('span', 'relevance');
    relevance.title = 'Similarity ranks excerpts. It is not an accuracy or confidence percentage.';
    const meter = node('span', 'meter'), bar = node('i');
    bar.style.width = (Math.max(0, Math.min(1, hit.score)) * 100) + '%';
    meter.append(bar); relevance.append(meter, document.createTextNode(Number(hit.score).toFixed(3) + ' similarity'));
    top.append(file, relevance);
    const code = node('div', 'code'); code.tabIndex = 0; code.setAttribute('aria-label', hit.path + ' source code');
    hit.content.split('\n').forEach((line, offset) => {
      const row = node('div', 'code-line'), text = node('code', 'code-text');
      text.append(highlight(line, hit.language));
      row.append(node('span', 'line-number', String(hit.start_line + offset)), text); code.append(row);
    });
    const bottom = node('div', 'result-bottom'), copy = node('button', '', 'Copy code'), link = node('a', '', 'View on GitHub ↗');
    const expand = node('button', '', 'Expand code');
    expand.setAttribute('aria-expanded', 'false');
    expand.onclick = () => {
      const expanded = expand.getAttribute('aria-expanded') !== 'true';
      expand.setAttribute('aria-expanded', String(expanded));
      code.style.maxHeight = expanded ? 'none' : '';
      expand.textContent = expanded ? 'Collapse code' : 'Expand code';
    };
    copy.onclick = async () => { try { await navigator.clipboard.writeText(hit.content); copy.textContent = 'Copied ✓'; setTimeout(() => copy.textContent = 'Copy code', 2000); } catch { notify('Select and copy the code directly; clipboard access is unavailable.', true); } };
    link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.href = 'https://github.com/' + encodeURIComponent(repo.owner_name) + '/' + encodeURIComponent(repo.repo_name) + '/blob/' + encodeURIComponent(repo.last_indexed_sha || repo.default_branch || 'main') + '/' + hit.path.split('/').map(encodeURIComponent).join('/') + '#L' + hit.start_line + '-L' + hit.end_line;
    bottom.append(node('span', '', 'MATCH ' + String(index + 1).padStart(2, '0')), expand, copy, link);
    card.append(top, code, bottom); el('#results').append(card);
  });
}
el('#search-form').onsubmit = async event => {
  event.preventDefault();
  if (!token) { showAuth(); return; }
  if (!selected) { notify('Import or select a repository first.'); return; }
  const query = el('#query').value.trim(); if (!query) return;
  request?.abort(); const controller = new AbortController(); request = controller;
  const repo = { ...selected }, started = performance.now();
  el('#search-button').disabled = true; notify('');
  el('#results-title').textContent = 'Finding relevant code…'; el('#result-count').textContent = '';
  const loading = node('div', 'loading'); loading.setAttribute('role', 'status');
  loading.append(node('span', 'spinner'), document.createTextNode('Searching your repository. The first search may take a moment.'));
  el('#results').replaceChildren(loading);
  try {
    const data = await api('/repositories/' + repo.id + '/search', { method: 'POST', body: JSON.stringify({ query, top_k: 3 }), signal: controller.signal });
    if (selected?.id === repo.id) renderResults(data, repo, ((performance.now() - started) / 1000).toFixed(1));
  } catch (error) { if (error.name !== 'AbortError') { notify(error.message, true); el('#results').replaceChildren(); el('#results-title').textContent = 'Search needs attention'; } }
  finally { if (request === controller) { el('#search-button').disabled = false; request = null; } }
};
document.querySelectorAll('[data-query]').forEach(button => button.onclick = () => { el('#query').value = button.dataset.query; el('#search-form').requestSubmit(); });
el('#account').onclick = () => token ? signOut() : showAuth();
el('#refresh').onclick = () => loadRepositories().catch(error => notify(error.message, true));
el('#add-repo').onclick = () => token ? el('#import-dialog').showModal() : showAuth();
document.querySelectorAll('.close-dialog').forEach(button => button.onclick = () => button.closest('dialog').close());
el('#auth-toggle').onclick = () => {
  registering = !registering;
  el('#auth-title').textContent = registering ? 'Create your workspace' : 'Welcome to your workspace';
  el('#auth-submit').textContent = registering ? 'Create account →' : 'Sign in →';
  el('#auth-toggle').textContent = registering ? 'Already have an account? Sign in' : 'New here? Create an account';
  el('#password').autocomplete = registering ? 'new-password' : 'current-password';
  el('#password').minLength = registering ? 12 : 1; el('#auth-error').textContent = '';
};
el('#auth-form').onsubmit = async event => {
  event.preventDefault(); el('#auth-submit').disabled = true; el('#auth-error').textContent = '';
  try {
    const body = JSON.stringify({ email: el('#email').value, password: el('#password').value });
    if (registering) await api('/auth/register', { method: 'POST', body });
    token = (await api('/auth/login', { method: 'POST', body })).access_token;
    sessionStorage.setItem('devpilot-token', token); el('#password').value = ''; el('#auth-dialog').close(); await initialize();
  } catch (error) { el('#auth-error').textContent = error.message; }
  finally { el('#auth-submit').disabled = false; }
};
el('#import-form').onsubmit = async event => {
  event.preventDefault(); const button = event.target.querySelector('.primary'); button.disabled = true; el('#import-error').textContent = '';
  try {
    const repo = await api('/repositories', { method: 'POST', body: JSON.stringify({ url: el('#repo-url').value }) });
    el('#import-dialog').close(); await loadRepositories(); choose(repo); notify('Repository added. Click Index repository to prepare its code for search.');
  } catch (error) { el('#import-error').textContent = error.message; }
  finally { button.disabled = false; }
};
el('#index-button').onclick = async () => {
  if (!selected) return; const id = selected.id; el('#index-button').disabled = true;
  try {
    const job = await api('/repositories/' + id + '/index', { method: 'POST' }); notify('Indexing started. Leave this page open to follow progress.');
    async function check() {
      if (selected?.id !== id || !token) return;
      try {
        const current = await api('/repositories/' + id + '/jobs/' + job.id);
        if (current.status === 'succeeded') { await loadRepositories(); notify('Indexing complete. Your repository is ready to search.'); }
        else if (current.status === 'failed') { await loadRepositories(); notify(current.error || 'Indexing failed. Please retry.', true); }
        else { notify('Indexing ' + (current.files_done ?? 0) + ' / ' + (current.files_total ?? '…') + ' files · ' + current.status); poll = setTimeout(check, 3000); }
      } catch (error) { notify(error.message, true); el('#index-button').disabled = false; }
    }
    poll = setTimeout(check, 2000);
  } catch (error) { notify(error.message, true); el('#index-button').disabled = false; }
};
async function health() {
  try { const response = await fetch('/readyz'); el('#connection').textContent = response.ok ? 'Local workspace online' : 'Database unavailable'; el('#connection').classList.toggle('online', response.ok); }
  catch { el('#connection').textContent = 'Workspace offline'; el('#connection').classList.remove('online'); }
}
health(); setInterval(health, 30000); initialize();
