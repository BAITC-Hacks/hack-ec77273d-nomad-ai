'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const number = value => value == null ? '—' : new Intl.NumberFormat('ru-RU', {maximumFractionDigits:1}).format(value);
const percent = value => value == null ? '—' : `${number(value)}%`;
const date = value => value ? new Intl.DateTimeFormat('ru-RU', {day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(`${value.slice(0,10)}T00:00:00Z`)) : '—';
const initials = value => String(value || '?').trim().split(/\s+/).slice(0,2).map(word => word[0]).join('').toUpperCase();
const formats = {online:'Онлайн',offline:'Очно',self_paced:'В своём темпе'};
const workFormats = {office:'В офисе',hybrid:'Гибридный формат',remote:'Удалённо'};
const statuses = {completed:'Завершено',in_progress:'В процессе',dropped:'Прервано',no_show:'Не посещено',declined:'Отклонено',overdue:'Срок истёк'};
const factors = {grade:'Текущий грейд',target:'Карьерная цель',gap:'Разрывы навыков',history:'История участия',eligibility:'Доступность',impact:'Рассчитанный эффект'};
const sourceNames = {career_goal:'Выбранная карьерная цель',next_grade:'Следующий грейд',current_grade:'Требования текущего грейда'};
const fallbackNames = {disabled:'Модель отключена',ai_disabled:'Модель отключена',not_requested:'Проверенный расчёт готов',deterministic:'Проверенный расчёт готов',timeout:'Модель не ответила за отведённое время',invalid_response:'Ответ модели не прошёл проверку',provider_error:'Сервис модели недоступен',no_available_route:'Нет допустимых маршрутов',no_candidates:'Нет допустимых маршрутов',external_not_allowed:'Для внешнего API не разрешена передача производных фактов',external_data_not_allowed:'Для внешнего API не разрешена передача производных фактов',missing_api_key:'Провайдер модели не настроен'};
const iconPaths = {growth:'<path d="M3 17 9 11l4 4 8-11M15 4h6v6"/>',people:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.87"/><circle cx="9" cy="7" r="4"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',overview:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6"/>',lab:'<path d="M9 3h6m-5 0v7L4 20h16l-6-10V3M7 15h10"/>',search:'<circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/>'};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || ''}</svg>`;
const brand = `<a class="brand" href="/" aria-label="Career Quest, главная"><span class="brand-mark" aria-hidden="true">↗</span><span>Career Quest<small>Карьерный навигатор</small></span></a>`;
const state = {session:null,view:'personal',token:0,abort:null,profile:null,recommendations:null,aiPending:false,aiElapsed:null,profileId:null,selectedRoute:null,requirementTab:'target',skills:{},events:{},directoryOffset:0,directoryQuery:'',hrTab:'gaps',overview:null};
let toastTimer, searchTimer;

function beginLoad() {
  state.abort?.abort();
  state.abort = new AbortController();
  state.token += 1;
  return {token:state.token,signal:state.abort.signal};
}
function current(token) { return token === state.token && Boolean(state.session); }
function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').classList.remove('hidden');
  toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), 5500);
}
async function api(url, options = {}) {
  const headers = {...options.headers};
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(url, {...options,headers,credentials:'same-origin'});
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const error = new Error(data.error?.message || (typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить запрос. Попробуйте ещё раз.'));
    error.status = response.status;
    error.details = data.error?.details || [];
    error.code = data.error?.code;
    if (response.status === 401 && state.session) {
      state.session = null;
      beginLoad();
      showLogin('Сессия завершена. Войдите снова.');
    }
    throw error;
  }
  return data;
}
function errorMarkup(error) {
  return `<div class="notice error" role="alert">${esc(error.message)}${error.details?.length ? `<ul class="detail-errors">${error.details.slice(0,20).map(detail => `<li><strong>${esc(detail.path)}</strong>: ${esc(detail.message)}</li>`).join('')}</ul>` : ''}</div>`;
}
const loader = text => `<div class="loader" role="status"><span class="spinner" aria-hidden="true"></span>${esc(text)}</div>`;
function heading(kicker, title, description, asOf = state.session?.as_of_date) {
  return `<div class="headrow"><div><div class="eyebrow">${esc(kicker)}</div><h1>${esc(title)}</h1><p>${esc(description)}</p></div>${asOf ? `<div class="snapshot">Дата демо<strong>${date(asOf)}</strong></div>` : ''}</div>`;
}

function showLogin(message = '') {
  state.profile = null; state.recommendations = null; state.skills = {}; state.events = {};
  document.title = 'Вход · Career Quest';
  $('#app').innerHTML = `<div class="login-layout"><section class="login-story">${brand}<div class="login-story-content"><div class="eyebrow">Развитие начинается с выбора</div><h1>Ваш следующий шаг.<br>Ваш маршрут.</h1><p>Поймите, какие навыки приблизят вас к цели. Сравните доступные варианты и выберите подходящий темп.</p><div class="journey-preview" aria-label="Как работает навигатор"><div class="route-line"><span class="route-node">1</span><div><strong>Узнайте свою точку старта</strong><p>Навыки, опыт и требования к цели</p></div></div><div class="route-line"><span class="route-node">2</span><div><strong>Сравните возможные шаги</strong><p>Реальные курсы и понятный эффект</p></div></div><div class="route-line"><span class="route-node">3</span><div><strong>Заметьте свой прогресс</strong><p>Обновление навыков после завершения</p></div></div></div></div><div class="login-foot">HackAlem AI · Halyk Bank · Синтетические данные</div></section><main class="login-form-area" id="content"><form class="login-card" id="login-form"><h2>Добро пожаловать</h2><p class="muted">Войдите в своё пространство развития.</p><div class="segmented" role="group" aria-label="Роль"><label><input type="radio" name="actor_role" value="employee" checked>Сотрудник</label><label><input type="radio" name="actor_role" value="hr">HR / Руководитель</label></div><div class="form-field"><label for="login-employee">ID сотрудника</label><input id="login-employee" name="employee_id" placeholder="E0001" autocomplete="username" required maxlength="100"><p class="form-hint" id="employee-hint">Откроется только ваш личный профиль.</p></div><div class="form-field"><label for="login-code">Код доступа</label><input id="login-code" name="access_code" type="password" autocomplete="current-password" required><p class="form-hint">Используйте выданный код для выбранной роли.</p></div><div id="login-error" aria-live="polite">${message ? `<p class="status error">${esc(message)}</p>` : ''}</div><button class="btn full" type="submit">Войти в пространство <span aria-hidden="true">→</span></button><p class="login-note">Локальная демонстрация: коды создаются при запуске в <code>var/demo_accounts.json</code>. Сотруднику нужен его собственный код; для HR используется отдельный код.</p></form></main></div>`;
  $('#login-form').addEventListener('change', event => {
    if (event.target.name !== 'actor_role') return;
    const isEmployee = event.target.value === 'employee';
    $('#login-employee').required = isEmployee;
    $('#employee-hint').textContent = isEmployee ? 'Откроется только ваш личный профиль.' : 'Необязательно. Укажите свой ID, чтобы также видеть личный маршрут.';
  });
  $('#login-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget, button = $('button[type=submit]', form), fields = new FormData(form);
    button.disabled = true; $('#login-error').innerHTML = '';
    try {
      state.session = await api('/api/login', {method:'POST',body:JSON.stringify({actor_role:fields.get('actor_role'),employee_id:fields.get('employee_id').trim() || null,access_code:fields.get('access_code')})});
      mountApp();
    } catch (error) { $('#login-error').innerHTML = errorMarkup(error); button.disabled = false; }
  });
}

function mountApp() {
  const hr = state.session.actor_role === 'hr';
  $('#app').innerHTML = `<div class="layout"><aside class="sidebar">${brand}<div><div class="nav-title">Ваше пространство</div><nav class="nav" aria-label="Основная навигация">${state.session.employee_id ? `<button type="button" data-nav="personal">${icon('growth')}Моё развитие</button>` : ''}${hr ? `<button type="button" data-nav="overview">${icon('overview')}Обзор команды</button><button type="button" data-nav="people">${icon('people')}Сотрудники</button><button type="button" data-nav="import">${icon('upload')}Загрузка данных</button><a href="/admin/ai">${icon('lab')}Проверка AI</a>` : ''}</nav></div><div class="sidebar-bottom"><div class="divider"></div>Маленький шаг сегодня —<br>больше возможностей завтра.<p style="margin:18px 0 0">Синтетические данные<br>Демонстрация HackAlem AI</p></div></aside><main class="workspace"><header class="topbar"><span class="topbar-title" id="crumb">Карьерный навигатор</span><div class="topbar-right"><span class="role-pill">${hr ? 'HR / Руководитель' : 'Личный кабинет'}</span><button class="btn quiet small" id="logout">Выйти</button></div></header><div class="content" id="content" tabindex="-1"></div></main></div>`;
  document.querySelectorAll('[data-nav]').forEach(button => button.addEventListener('click', () => navigate(button.dataset.nav)));
  $('#logout').addEventListener('click', async event => {
    event.currentTarget.disabled = true;
    try { await api('/api/logout', {method:'POST'}); state.session = null; beginLoad(); history.replaceState(null,'','/'); showLogin(); }
    catch (error) { toast(error.message); if ($('#logout')) $('#logout').disabled = false; }
  });
  const path = location.pathname;
  const desired = !hr ? 'personal' : path === '/employee' && state.session.employee_id ? 'personal' : 'overview';
  navigate(desired);
  loadCatalogs();
}
async function loadCatalogs() {
  const session = state.session;
  const results = await Promise.allSettled([api('/api/skills'), api('/api/events')]);
  if (!state.session || state.session !== session) return;
  if (results[0].status === 'fulfilled') state.skills = Object.fromEntries((results[0].value.items || []).map(row => [row.skill_id,row]));
  if (results[1].status === 'fulfilled') state.events = Object.fromEntries((results[1].value.items || []).map(row => [row.event_id,row]));
  if (state.profile && $('#profile-data')) { renderProfile(); if (state.recommendations) renderRecommendations(); }
}
function navigate(view) {
  if (state.session.actor_role !== 'hr') view = 'personal';
  if (view === 'personal' && !state.session.employee_id) view = 'overview';
  state.view = view;
  document.querySelectorAll('[data-nav]').forEach(button => { button.classList.toggle('active',button.dataset.nav === view); button.setAttribute('aria-current',button.dataset.nav === view ? 'page' : 'false'); });
  $('#crumb').textContent = {personal:'Личное развитие',overview:'Команда / Обзор',people:'Команда / Сотрудники',import:'Команда / Загрузка данных'}[view];
  history.replaceState(null,'',view === 'personal' ? '/employee' : '/hr');
  ({personal:() => loadProfile(null),overview:loadOverview,people:loadDirectory,import:showImport}[view])();
}
const skillName = id => state.skills[id]?.name || state.profile?.trajectory?.gaps?.find(gap => gap.skill_id === id)?.name || id;
const eventName = id => state.events[id]?.title || id;
function ownProfile() { return state.profile?.employee.employee_id === state.session?.employee_id; }

async function loadProfile(id) {
  const {token,signal} = beginLoad();
  state.profileId = id; state.profile = null; state.recommendations = null; state.selectedRoute = null; state.aiPending = false; state.aiElapsed = null; state.requirementTab = 'target';
  const isOwn = !id || id === state.session.employee_id;
  const base = isOwn ? '/api/me' : `/api/employees/${encodeURIComponent(id)}`;
  const profileUrl = isOwn ? base : `${base}/profile`;
  document.title = `${isOwn ? 'Моё развитие' : 'Профиль сотрудника'} · Career Quest`;
  $('#content').innerHTML = `${id ? '<button class="btn quiet back-link" id="back-directory">← К сотрудникам</button>' : ''}${heading('Личная траектория',isOwn ? 'Развитие в вашем темпе' : 'Маршрут сотрудника','Сравните эффект доступных шагов и выберите то, что приближает к вашей цели.')}<div id="profile-data" aria-busy="true"><div class="skeleton" style="height:260px"></div></div><section id="recommendations" aria-label="Рекомендации"><div class="section-head"><div><h2>Следующий достижимый шаг</h2><p>Проверяем доступность, требования и реальный эффект.</p></div></div>${loader('Рассчитываем варианты…')}</section><div id="profile-details"></div>`;
  $('#back-directory')?.addEventListener('click',() => navigate('people'));
  const profilePromise = api(profileUrl,{signal}).then(data => {
    if (!current(token)) return;
    state.profile = data;
    if (data.events) state.events = {...state.events,...Object.fromEntries(data.events.map(row => [row.event_id,row]))};
    renderProfile();
    if (state.recommendations) renderRecommendations();
  }).catch(error => {
    if (!current(token) || error.name === 'AbortError') return;
    $('#profile-data').innerHTML = errorMarkup(error); $('#profile-data').setAttribute('aria-busy','false');
  });
  const quickPromise = api(`${base}/recommendations?ai=false`,{signal}).then(data => {
    if (!current(token)) return null;
    state.recommendations = data; renderRecommendations(); return data;
  }).catch(error => {
    if (!current(token) || error.name === 'AbortError') return null;
    $('#recommendations').innerHTML = `<div class="section-head"><h2>Следующий достижимый шаг</h2></div>${errorMarkup(error)}<button class="btn secondary small" id="retry-routes" style="margin-top:12px">Повторить расчёт</button>`;
    $('#retry-routes').addEventListener('click',() => loadProfile(id)); return null;
  });
  const fast = await quickPromise;
  if (!current(token) || !fast?.recommendations?.length) { await profilePromise; return; }
  state.aiPending = true; renderRecommendations();
  const started = performance.now();
  try {
    const enhanced = await api(`${base}/recommendations?ai=true`,{signal});
    if (!current(token)) return;
    // A late model result must never overwrite a completed activity or a new target.
    if (state.profile && enhanced.revision !== state.profile.employee.revision) return;
    state.recommendations = enhanced;
  } catch (error) {
    if (!current(token) || error.name === 'AbortError') return;
    state.recommendations = {...fast,mode:'fallback',fallback_reason:error.message};
  } finally {
    if (current(token)) { state.aiPending = false; state.aiElapsed = (performance.now() - started) / 1000; renderRecommendations(); }
  }
  await profilePromise;
}

function renderProfile() {
  const {employee:e,trajectory:t} = state.profile;
  if (!$('#profile-data')) return;
  const previousTargetChoice = $('#target-choice')?.value;
  const target = t.target || e.target, coverage = t.coverage;
  const options = state.profile.target_options || [];
  $('#profile-data').setAttribute('aria-busy','false');
  $('#profile-data').innerHTML = `<section class="panel profile-hero"><div><div class="profile-person"><span class="avatar">${esc(initials(e.full_name))}</span><div><h2>${esc(e.full_name)}</h2><p>${esc(e.role)} · ${esc(e.grade)} · ${esc(e.department)}</p></div></div><div class="meta-line"><span>${esc(workFormats[e.work_format] || e.work_format)}</span><span>${number(e.tenure_months)} мес. в команде</span><span>Оценка: ${date(e.last_review_date)}</span></div><div class="hero-goal">${esc(sourceNames[target.source] || 'Карьерная цель')}<strong>${esc(target.role)} · ${esc(target.grade)}</strong></div></div><div class="coverage-panel"><div class="coverage-caption">Покрытие требований к цели</div><div class="coverage-value">${coverage == null ? '—' : number(coverage)}${coverage == null ? '' : '<span>%</span>'}</div><div class="progress" role="progressbar" aria-label="Покрытие требований к цели" ${coverage == null ? '' : `aria-valuenow="${Number(coverage)}" aria-valuemin="0" aria-valuemax="100"`}><span style="width:${Math.max(0,Math.min(100,Number(coverage) || 0))}%"></span></div><div class="coverage-caption">${coverage == null ? 'Для расчёта нет требований' : 'Рассчитано по уровням навыков'}</div></div></section><div class="profile-summary"><div class="mini-stat"><strong>${number(t.critical_blockers)}</strong>критических навыков с разрывом</div><div class="mini-stat"><strong>${number(t.critical_gap_units)}</strong>уровней до критических требований</div><div class="mini-stat"><strong>${number(e.completed_event_ids?.length ?? 0)}</strong>разных активностей завершено</div></div>${ownProfile() && options.length ? `<section class="panel target-box"><div><h3>Вы управляете направлением</h3><p>Можно выбрать другую роль или грейд и сравнить варианты развития.</p></div><form class="target-form" id="target-form"><label class="sr-only" for="target-choice">Карьерная цель</label><select id="target-choice">${options.map((option,index) => `<option value="${index}" ${option.role === target.role && option.grade === target.grade ? 'selected' : ''}>${esc(option.role)} · ${esc(option.grade)}</option>`).join('')}</select><button type="submit" class="btn secondary small">Изменить цель</button></form></section>` : ''}`;
  if (previousTargetChoice != null && $('#target-choice')) $('#target-choice').value = previousTargetChoice;
  $('#target-form')?.addEventListener('submit', async event => {
    event.preventDefault(); const button = $('button',event.currentTarget), selected = options[Number($('#target-choice').value)];
    button.disabled = true;
    try { await api('/api/me/target',{method:'PATCH',body:JSON.stringify({role:selected.role,grade:selected.grade,revision:e.revision})}); toast('Цель обновлена. Пересчитываем доступные шаги.'); loadProfile(state.profileId); }
    catch (error) { toast(error.message); button.disabled = false; if (error.status === 409) loadProfile(state.profileId); }
  });
  $('#profile-details').innerHTML = `<div class="profile-columns"><section class="panel box"><h2>Навыки и требования</h2><div class="requirements-switch" role="group" aria-label="Требования"><button data-requirements="target">К выбранной цели</button><button data-requirements="next">Следующий грейд</button><button data-requirements="current">Текущий грейд</button></div><div id="requirements-list"></div><details class="skill-details"><summary>Все навыки профиля · ${Object.keys(e.skills).length}</summary>${Object.entries(e.skills).sort(([a],[b]) => skillName(a).localeCompare(skillName(b),'ru')).map(([id,level]) => `<div class="skill-row"><div class="skill-row-head"><span>${esc(skillName(id))}</span><strong>${number(level)} / 5</strong></div>${levelTrack(level,null)}</div>`).join('')}</details><p class="form-hint" style="margin-top:15px">Уровни учитывают завершения после последней оценки. Одно завершение начисляет прирост один раз.</p></section><section class="panel box"><div class="section-head" style="margin:0 0 10px"><div><h2>История активности</h2><p>Ваш опыт помогает выбрать подходящий формат.</p></div></div>${renderHistory()}</section></div>`;
  document.querySelectorAll('[data-requirements]').forEach(button => button.addEventListener('click',() => { state.requirementTab = button.dataset.requirements; renderRequirements(); }));
  renderRequirements();
}
function levelTrack(level,required) {
  return `<div class="level-track" aria-hidden="true">${Array.from({length:5},(_,index) => `<span class="${index < level ? 'filled' : ''} ${required != null && index + 1 === required ? 'required' : ''}"></span>`).join('')}</div>`;
}
function renderRequirements() {
  if (!$('#requirements-list') || !state.profile) return;
  const tab = state.requirementTab;
  document.querySelectorAll('[data-requirements]').forEach(button => { button.classList.toggle('active',button.dataset.requirements === tab); button.setAttribute('aria-pressed',String(button.dataset.requirements === tab)); });
  const rows = tab === 'target' ? state.profile.trajectory.gaps : state.profile[`${tab}_requirements`];
  $('#requirements-list').innerHTML = `<p class="requirement-caption">Текущий уровень / требуемый. Шкала 0–5. Критические навыки отмечены отдельно.</p>${rows?.length ? rows.map(row => `<div class="skill-row"><div class="skill-row-head"><span class="skill-row-label">${esc(row.name || skillName(row.skill_id))}${row.critical ? '<span class="tag critical">Критичный</span>' : ''}</span><strong>${number(row.current)} / ${number(row.required)}</strong></div>${levelTrack(row.current,row.required)}<div class="form-hint">${row.gap > 0 ? `До требования: ${number(row.gap)} ур.` : 'Требование выполнено'}</div></div>`).join('') : '<div class="empty">В каталоге нет требований для этого уровня.</div>'}`;
}
function renderHistory() {
  const rows = [...(state.profile.history || [])].sort((a,b) => b.date.localeCompare(a.date));
  if (!rows.length) return '<div class="empty">Истории пока недостаточно. Рекомендации опираются на цель и навыки.</div>';
  return `<div class="history-list">${rows.map(row => `<article class="history-item"><div class="history-top"><strong>${esc(row.title || eventName(row.event_id))}</strong><span class="history-status ${row.status === 'completed' ? 'completed' : row.status === 'in_progress' ? 'in_progress' : ''}">${esc(statuses[row.status] || row.status)}</span></div><p>${state.events[row.event_id]?.format === 'self_paced' ? 'Дата записи: ' : ''}${date(row.date)} · Выполнено ${percent(row.completion_pct)}${row.score == null ? '' : ` · Оценка ${number(row.score)} / 100`}${row.feedback_rating == null ? '' : ` · Отзыв ${number(row.feedback_rating)} / 5`}${row.due_date ? ` · Срок ${date(row.due_date)}` : ''}</p></article>`).join('')}</div><p class="form-hint" style="margin-top:12px">В исходной истории курсов «в своём темпе» указана дата записи, а не точная дата завершения. Расчёт после оценки использует её как приближение. Причины пропусков не определяются по истории.</p>`;
}
function impactMarkup(impacts) {
  return `<div class="impact-list">${(impacts || []).map(impact => `<div class="impact-row"><span>${esc(skillName(impact.skill_id))}<small>${impact.required == null ? 'Вне требований выбранной цели' : `Требование цели: ${number(impact.required)}`} · потолок активности ${number(impact.max_level)}</small></span><span class="impact-values">${number(impact.before)} → ${number(impact.after)}<span class="gain" style="display:block">+${number(impact.gain_applied)} ур.</span></span></div>`).join('')}</div>`;
}
function renderRecommendations() {
  const slot = $('#recommendations'), data = state.recommendations;
  if (!slot || !data) return;
  if (state.profile && data.revision !== state.profile.employee.revision) {
    slot.innerHTML = '<div class="notice">Профиль обновился во время расчёта. Обновите варианты, чтобы сравнить актуальный эффект.</div><button class="btn secondary small" id="stale-refresh" style="margin-top:10px">Обновить варианты</button>';
    $('#stale-refresh').addEventListener('click',() => loadProfile(state.profileId)); return;
  }
  const routes = (data.recommendations || []).slice(0,3), isLLM = data.mode === 'llm';
  const reason = fallbackNames[data.fallback_reason] || data.fallback_reason;
  const aiStatus = state.aiPending ? '<span class="tag neutral"><span class="spinner" aria-hidden="true"></span>Career Navigator сравнивает</span>' : `<span class="tag ${isLLM ? '' : 'gold'}">${isLLM ? 'AI · Ответ проверен' : 'Резервный расчёт'}</span>`;
  slot.innerHTML = `<div class="section-head"><div><h2>Следующий достижимый шаг</h2><p>Сравните альтернативы. Каждый маршрут доступен для текущего профиля.</p></div><div class="ai-state" role="status" aria-live="polite"><div class="status-row">${aiStatus}${state.aiElapsed == null ? '' : `<span>${number(state.aiElapsed)} с</span>`}</div><p>${state.aiPending ? 'Рассчитанные варианты уже доступны для выбора.' : !isLLM && reason ? esc(reason) : isLLM ? 'Модель сравнила только проверенные маршруты.' : ''}</p></div></div>${!routes.length ? `<div class="panel empty"><strong>Сейчас нет подходящего добровольного шага</strong>${esc(data.no_step_reason || 'Для выбранной цели не найдено доступных активностей.')}${data.baseline ? `<p style="margin-top:12px">${esc(data.baseline.reason)}</p>` : ''}</div>` : `<div class="route-grid">${routes.map((route,index) => routeMarkup(route,index)).join('')}</div><p class="route-footnote">Это сценарии по правилам каталога. Подтверждённое выполнение обновляет навыки и прогресс; грейд не присваивается автоматически. Обязательные процессы не участвуют в рекомендациях.</p>`}`;
  const routeFootnote = $('.route-footnote',slot);
  if (routeFootnote) routeFootnote.textContent += ' Плановые сроки предполагают нагрузку 2 часа в день.';
  slot.querySelectorAll('[data-select-route]').forEach(button => button.addEventListener('click',() => { state.selectedRoute = button.dataset.selectRoute; renderRecommendations(); toast('Вариант отмечен. Завершение подтверждается отдельно после активности.'); }));
  slot.querySelectorAll('[data-complete-route]').forEach(button => button.addEventListener('click',() => {
    const route = state.recommendations.recommendations.find(row => row.route_id === button.dataset.completeRoute);
    if (route) openCompletion(route);
  }));
}
function routeMarkup(route,index) {
  const steps = route.steps || [], first = steps[0], selected = state.selectedRoute === route.route_id;
  const evidence = route.factors || [];
  return `<article class="panel route-card ${selected ? 'selected' : ''}" aria-label="Маршрут ${index + 1}: ${esc(route.title)}"><div class="route-top"><div class="route-index"><span>${index + 1}</span>${selected ? 'Выбранный вариант' : 'Вариант развития'}</div><span class="tag neutral">${route.action === 'continue' ? 'Продолжить' : 'Начать'}</span></div><div class="route-body"><h3>${esc(route.title)}</h3><div class="route-meta">${first ? `${esc(formats[first.format] || first.format)} · ${number(first.duration_hours)} ч. · ${steps.length === 2 ? '2 шага' : '1 шаг'}` : ''}</div><div class="effect-box"><div class="effect-label">Покрытие цели после первого шага</div><div class="before-after"><span>${percent(route.progress_before)}</span><span class="arrow" aria-hidden="true">→</span><strong>${percent(route.progress_after)}</strong></div><div class="effect-foot">Критический разрыв: ${number(route.critical_gap_before)} → ${number(route.critical_gap_after)} ур.</div></div>${impactMarkup(route.impacts)}<p class="route-reason">${esc(route.reason)}</p><details open><summary>Почему подходит · ${new Set(evidence.map(factor => factor.category)).size} факторов</summary><ul class="factor-list">${evidence.map(factor => `<li><strong>${esc(factors[factor.category] || factor.category)}</strong>${esc(factor.text)}</li>`).join('')}</ul></details><details><summary>Курсы и сроки маршрута</summary>${steps.map((step,i) => `<div class="course-step"><div class="step-index">Шаг ${i + 1}</div><div class="step-title">${esc(step.title)}</div><div class="small">${esc(formats[step.format] || step.format)} · ${number(step.duration_hours)} ч.<br>${date(step.planned_start)} — ${date(step.planned_end)}</div>${impactMarkup(step.impacts)}<div class="form-hint">После шага: ${percent(step.coverage_after)} покрытия · критический разрыв ${number(step.critical_gap_units_after)} ур.</div></div>`).join('')}${steps.length > 1 ? `<p class="effect-foot">Итог обоих шагов: ${percent(steps[steps.length-1].coverage_after)} покрытия требований.</p>` : ''}${route.unlocked_event_ids?.length ? `<p class="form-hint">Откроет доступ: ${route.unlocked_event_ids.map(eventName).map(esc).join(', ')}</p>` : ''}</details><div class="form-hint">Расчётный приоритет: ${number(route.score)}. Это показатель пользы, не вероятность повышения.</div></div>${ownProfile() ? `<div class="route-actions"><button class="btn ${selected ? '' : 'secondary'}" data-select-route="${esc(route.route_id)}" aria-pressed="${selected}">${selected ? '✓ Вариант выбран' : 'Выбрать этот вариант'}</button><button class="btn secondary" data-complete-route="${esc(route.route_id)}">Подтвердить выполнение</button></div>` : '<div class="route-actions"><span class="form-hint">Профиль доступен для просмотра. Выбор и подтверждение — в личном кабинете сотрудника.</span></div>'}</article>`;
}
function openCompletion(route) {
  if (!state.profile || !ownProfile()) return;
  const dialog = $('#completion-dialog'), first = route.steps[0], revision = state.profile.employee.revision, profileId = state.profileId;
  const idempotencyKey = crypto.randomUUID();
  dialog.innerHTML = `<h2 id="completion-title">Подтвердить первый шаг</h2><p>Демонстрация завершения активности на синтетическом профиле.</p><div class="dialog-course">${esc(first.title)}<div class="form-hint">${esc(formats[first.format] || first.format)} · ${number(first.duration_hours)} ч.</div></div><p>После подтверждения система один раз применит рассчитанный прирост и обновит маршруты.</p><div class="notice">Это демо-симуляция выполнения на дату ${date(state.profile.employee.as_of_date)}. Она позволяет проверить эффект, даже если занятие запланировано позднее. Грейд сотрудника остаётся прежним.</div><label class="dialog-check"><input type="checkbox" id="completion-confirmed">Подтверждаю демонстрационное выполнение этой активности.</label><div id="completion-error" aria-live="polite"></div><div class="dialog-actions"><button class="btn secondary" id="completion-cancel">Отмена</button><button class="btn" id="completion-submit" disabled>Применить эффект</button></div>`;
  $('#completion-cancel').addEventListener('click',() => dialog.close());
  $('#completion-confirmed').addEventListener('change',event => { $('#completion-submit').disabled = !event.target.checked; });
  $('#completion-submit').addEventListener('click',async event => {
    const button = event.currentTarget; button.disabled = true; $('#completion-confirmed').disabled = true;
    try {
      await api('/api/me/completions',{method:'POST',body:JSON.stringify({event_id:first.event_id,revision,idempotency_key:idempotencyKey,demo_confirmed:true})});
      dialog.close(); toast('Выполнение сохранено. Навыки и траектория обновлены.'); loadProfile(profileId);
    } catch (error) {
      if (!state.session) { dialog.close(); return; }
      $('#completion-error').innerHTML = errorMarkup(error); $('#completion-confirmed').disabled = false; button.disabled = false;
      if (error.status === 409) { dialog.close(); toast('Профиль уже изменился. Загружено актуальное состояние.'); loadProfile(profileId); }
    }
  });
  dialog.showModal();
}

async function loadOverview() {
  const {token,signal} = beginLoad(); state.profile = null;
  document.title = 'Обзор команды · Career Quest';
  $('#content').innerHTML = heading('Пространство HR','Развитие команды','Компетенции и доступность развития без публичных рейтингов сотрудников.') + loader('Собираем агрегированные показатели…');
  try {
    const data = await api('/api/hr/overview',{signal}); if (!current(token)) return; state.overview = data;
    $('#content').innerHTML = heading('Пространство HR','Развитие команды','Компетенции и доступность развития без публичных рейтингов сотрудников.',data.as_of_date) + `<div class="stats">${stat('Сотрудников',data.total_employees,'В текущем наборе данных')}${stat('Без доступного шага',data.no_next_step.length,'Нужна проверка цели или каталога')}${stat('Активностей в каталоге',data.activities.length,'Данные о добровольных и обязательных событиях')}</div><div class="tabs" role="group" aria-label="Раздел обзора"><button data-hr-tab="gaps">Разрывы компетенций</button><button data-hr-tab="no-step">Без следующего шага</button><button data-hr-tab="activities">Участие в активностях</button></div><div id="hr-table"></div>`;
    document.querySelectorAll('[data-hr-tab]').forEach(button => button.addEventListener('click',() => { state.hrTab = button.dataset.hrTab; renderOverviewTab(); })); renderOverviewTab();
  } catch (error) { if (current(token) && error.name !== 'AbortError') $('#content').innerHTML += errorMarkup(error); }
}
function stat(label,value,foot) { return `<article class="panel stat"><div class="stat-label">${esc(label)}</div><div class="stat-value">${number(value)}</div><div class="stat-foot">${esc(foot)}</div></article>`; }
function renderOverviewTab() {
  const data = state.overview; if (!data || !$('#hr-table')) return;
  document.querySelectorAll('[data-hr-tab]').forEach(button => { button.classList.toggle('active',button.dataset.hrTab === state.hrTab); button.setAttribute('aria-pressed',String(button.dataset.hrTab === state.hrTab)); });
  let body;
  if (state.hrTab === 'gaps') body = data.skill_gaps.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Компетенция</th><th class="num">С разрывом</th><th class="num">В применимой группе</th><th>Средний разрыв</th></tr></thead><tbody>${data.skill_gaps.map(gap => `<tr><td><strong>${esc(gap.name)}</strong><br><small class="muted">${esc(gap.skill_id)}</small></td><td class="num">${number(gap.affected_count)}</td><td class="num">${number(gap.eligible_population)}</td><td><div class="gap-meter"><div class="progress"><span style="width:${Math.max(0,Math.min(100,Number(gap.mean_gap)*20))}%"></span></div><span>${number(gap.mean_gap)} ур.</span></div></td></tr>`).join('')}</tbody></table></div><p class="table-note">Сравнение с требованиями индивидуальных целей. Применимая группа — сотрудники, для чьей цели требуется этот навык.</p>` : '<div class="empty"><strong>Разрывы не обнаружены</strong>В текущем наборе нет дефицитов по целевым требованиям.</div>';
  else if (state.hrTab === 'no-step') body = data.no_next_step.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Сотрудник</th><th>Причина</th><th></th></tr></thead><tbody>${data.no_next_step.map(row => `<tr><td>${esc(row.employee_id)}</td><td>${esc(row.reason)}</td><td class="num"><button class="btn secondary small" data-open-person="${esc(row.employee_id)}">Профиль →</button></td></tr>`).join('')}</tbody></table></div><p class="table-note">Отсутствие шага может быть связано с выполненными требованиями или ограничениями каталога; это не оценка вовлечённости.</p>` : '<div class="empty"><strong>Для всех есть доступный шаг</strong>По текущим целям и правилам каталога.</div>';
  else body = data.activities.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Активность</th><th class="num">Участников</th><th>Статусы записей</th><th class="num">Завершение</th></tr></thead><tbody>${data.activities.map(row => `<tr><td><strong>${esc(row.title)}</strong><br><span class="tag ${row.mandatory ? 'neutral' : ''}" style="margin-top:6px">${row.mandatory ? 'Обязательная' : 'Добровольная'}</span></td><td class="num">${number(row.unique_participants)}</td><td><div class="activity-counts">${Object.entries(row.status_counts).map(([status,count]) => `<span>${esc(statuses[status] || status)}: ${number(count)}</span>`).join('')}</div></td><td class="num">${percent(row.completion_rate)}<br><small class="muted">${number(row.completed_records)} / ${number(row.total_records)} записей</small></td></tr>`).join('')}</tbody></table></div><p class="table-note">Участники считаются по уникальным сотрудникам; процент завершения — по записям участия. Для повторяемых событий эти числа могут различаться.</p>` : '<div class="empty">Данных об участии пока нет.</div>';
  $('#hr-table').innerHTML = `<section class="panel">${body}</section>`;
  bindPersonButtons($('#hr-table'));
}
function bindPersonButtons(root) {
  root.querySelectorAll('[data-open-person]').forEach(button => button.addEventListener('click',() => { state.view = 'people'; loadProfile(button.dataset.openPerson); }));
}
async function loadDirectory() {
  const {token,signal} = beginLoad(); state.profile = null;
  document.title = 'Сотрудники · Career Quest';
  $('#content').innerHTML = heading('Пространство HR','Сотрудники и их маршруты','Откройте профиль, чтобы увидеть цель, разрывы навыков и доступные варианты развития.') + `<div class="directory-toolbar"><div class="search-field">${icon('search')}<label class="sr-only" for="people-search">Поиск сотрудников</label><input id="people-search" type="search" placeholder="Имя, ID, роль или подразделение" value="${esc(state.directoryQuery)}"></div><div class="directory-count" id="directory-count"></div></div><div id="directory-results">${loader('Загружаем сотрудников…')}</div>`;
  $('#people-search').addEventListener('input',event => {
    state.directoryQuery = event.target.value; state.directoryOffset = 0; clearTimeout(searchTimer);
    searchTimer = setTimeout(() => refreshDirectory(token,signal),250);
  });
  await refreshDirectory(token,signal);
}
let directorySequence = 0;
async function refreshDirectory(token,signal) {
  const sequence = ++directorySequence, limit = 50;
  try {
    const data = await api(`/api/employees?limit=${limit}&offset=${state.directoryOffset}&q=${encodeURIComponent(state.directoryQuery)}`,{signal});
    if (!current(token) || sequence !== directorySequence) return;
    $('#directory-count').textContent = `Найдено: ${number(data.total)}`;
    $('#directory-results').innerHTML = !data.items.length ? '<div class="panel empty"><strong>Никого не нашли</strong>Попробуйте другое имя, роль или ID.</div>' : `<div class="panel table-wrap"><table class="data-table"><thead><tr><th>Сотрудник</th><th>Роль и грейд</th><th>Подразделение</th><th></th></tr></thead><tbody>${data.items.map(person => `<tr><td><div class="person-cell"><span class="avatar">${esc(initials(person.full_name))}</span><div><strong>${esc(person.full_name)}</strong><small>${esc(person.employee_id)}</small></div></div></td><td>${esc(person.role)}<br><small class="muted">${esc(person.grade)}</small></td><td>${esc(person.department)}</td><td class="num"><button class="btn secondary small" data-open-person="${esc(person.employee_id)}">Маршрут →</button></td></tr>`).join('')}</tbody></table></div><div class="pagination"><span>${state.directoryOffset+1}–${state.directoryOffset+data.items.length} из ${number(data.total)}</span><div><button class="btn secondary small" id="people-prev" ${state.directoryOffset === 0 ? 'disabled' : ''}>← Назад</button><button class="btn secondary small" id="people-next" ${state.directoryOffset+limit >= data.total ? 'disabled' : ''}>Далее →</button></div></div>`;
    bindPersonButtons($('#directory-results'));
    $('#people-prev')?.addEventListener('click',() => { state.directoryOffset = Math.max(0,state.directoryOffset-limit); refreshDirectory(token,signal); });
    $('#people-next')?.addEventListener('click',() => { state.directoryOffset += limit; refreshDirectory(token,signal); });
  } catch (error) { if (current(token) && sequence === directorySequence && error.name !== 'AbortError') $('#directory-results').innerHTML = errorMarkup(error); }
}

function showImport() {
  const {token} = beginLoad(); state.profile = null;
  document.title = 'Загрузка данных · Career Quest';
  $('#content').innerHTML = heading('Пространство HR','Загрузка данных','Добавьте проверочные профили с историей или загрузите полный исходный набор.') + `<div class="import-grid"><form class="panel import-panel" id="import-form"><h2>Импорт набора</h2><p>Файлы проверяются вместе, прежде чем изменения будут сохранены.</p><div class="import-mode"><label for="import-mode">Что загрузить</label><select id="import-mode" name="mode"><option value="append">Дополнительные профили и их история</option><option value="replace">Полностью заменить набор данных</option></select></div><div id="zip-field" class="file-field hidden"><label for="dataset-zip">Архив career_quest_dataset.zip</label><p>Можно загрузить ZIP вместо четырёх отдельных файлов ниже.</p><input id="dataset-zip" name="dataset_zip" type="file" accept=".zip"></div>${fileField('employees','employees.json','Исходные профили со skills, hire_date и last_review_date.','.json',true)}${fileField('history','activity_history.csv','Записи активности для загружаемых профилей.','.csv',true)}<div id="catalog-files" class="hidden">${fileField('events','events.json','Каталог активностей с требованиями, приростами и сессиями.','.json',false)}${fileField('skills','skills.json','Компетенции и требования ролей по грейдам.','.json',false)}</div><label class="dialog-check hidden" id="replace-warning"><input type="checkbox" id="replace-confirmed">Подтверждаю замену текущего набора и демонстрационного прогресса.</label><button class="btn full" type="submit" id="import-submit">Проверить и загрузить</button><div id="import-result" class="import-result" aria-live="polite"></div></form><aside class="panel import-guidance"><h3>Для проверки жюри</h3><ol><li>Выберите дополнительные профили и историю в формате стартового набора.</li><li>Для нового каталога выберите полную замену и загрузите четыре файла или ZIP.</li><li>После загрузки откройте список сотрудников: новые ID доступны сразу.</li></ol><p>Используйте синтетические данные. Здесь нужен исходный snapshot навыков; экспорт готового API-профиля для импорта не подходит.</p><p>Дата демо фиксируется настройкой набора. Дата компьютера не меняет доступность занятий.</p></aside></div>`;
  const updateFields = () => {
    const replace = $('#import-mode').value === 'replace', zipped = replace && Boolean($('#dataset-zip').files.length);
    $('#catalog-files').classList.toggle('hidden',!replace); $('#zip-field').classList.toggle('hidden',!replace); $('#replace-warning').classList.toggle('hidden',!replace); $('#replace-confirmed').required = replace;
    ['employees','history','events','skills'].forEach(name => { const input = $(`#file-${name}`); input.required = !zipped && (replace || ['employees','history'].includes(name)); input.disabled = zipped || (!replace && ['events','skills'].includes(name)); });
    $('#dataset-zip').disabled = !replace;
  };
  $('#import-mode').addEventListener('change',updateFields); $('#dataset-zip').addEventListener('change',updateFields); updateFields();
  $('#import-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = $('#import-submit'), form = new FormData(event.currentTarget); button.disabled = true; $('#import-result').innerHTML = loader('Проверяем файлы и сохраняем набор…');
    // Empty optional file inputs are not uploaded as zero-byte datasets.
    for (const [key,value] of [...form.entries()]) if (value instanceof File && !value.name) form.delete(key);
    try {
      const data = await api('/api/hr/import',{method:'POST',body:form});
      state.skills = {}; state.events = {}; state.directoryOffset = 0; state.directoryQuery = '';
      state.session = await api('/api/session');
      if (!current(token)) { toast('Импорт завершён. Набор данных обновлён.'); loadCatalogs(); return; }
      $('#import-result').innerHTML = `<div class="notice"><strong>Данные загружены</strong><p style="margin:6px 0 0">${Object.entries(data.counts || {}).map(([key,value]) => `${esc({employees:'Сотрудников',events:'Активностей',skills:'Навыков',history:'Записей истории',activity_history:'Записей истории'}[key] || key)}: ${number(value)}`).join(' · ')}</p><p style="margin:7px 0 0">Дата демо: ${date(data.as_of_date)}</p></div><button type="button" class="btn secondary small" id="open-imported" style="margin-top:12px">Открыть сотрудников →</button>`;
      $('#open-imported').addEventListener('click',() => navigate('people')); loadCatalogs();
    } catch (error) { if (current(token) && $('#import-result')) $('#import-result').innerHTML = errorMarkup(error); }
    finally { if (current(token) && $('#import-submit')) $('#import-submit').disabled = false; }
  });
}
function fileField(name,title,description,accept,required) {
  return `<div class="file-field"><label for="file-${name}">${esc(title)}</label><p>${esc(description)}</p><input type="file" name="${name}" id="file-${name}" accept="${accept}" ${required ? 'required' : ''}></div>`;
}

(async function initialize() {
  try { state.session = await api('/api/session'); mountApp(); }
  catch (error) { showLogin(error.status === 401 ? '' : 'Сервер пока недоступен. Проверьте запуск приложения и попробуйте войти.'); }
})();
