import { createClient } from 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm';
import { SUPABASE_URL, SUPABASE_KEY } from './config.js';

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);
const app = document.getElementById('app');

// Carte : MapLibre + fond de carte OpenFreeMap (gratuit, sans clé, sans limite)
const MAPLIBRE_JS = 'https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.js';
const MAPLIBRE_CSS = 'https://cdn.jsdelivr.net/npm/maplibre-gl@5/dist/maplibre-gl.css';
const MAP_STYLE = 'https://tiles.openfreemap.org/styles/positron';
const ACCENT = '#0071e3';
const FAV = '#ff9f0a';

const ACTIVITIES = [
  'Secours médical', 'Police et sécurité', 'Sécurité civile et incendie', 'Recherche et sauvetage',
  'Offshore et énergie', 'Tourisme', 'Transport et charter', 'Affaires et VIP', 'Travail aérien',
  'Formation', 'Militaire',
];
const SECTORS = { civil: 'Civil', etatique: 'Étatique', militaire: 'Militaire' };

const state = {
  session: null,
  profile: null,
  operators: [],
  favorites: new Set(),
  filters: { q: '', country: '', model: '', activity: '', period: '', favOnly: false },
  view: 'both',        // 'both' | 'list' | 'map'
  syncMap: true,       // la liste suit la zone visible de la carte
  bounds: null,
  map: null,
};

// ---------------------------------------------------------------------------
// Outils
// ---------------------------------------------------------------------------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
function svg(markup) {
  const t = document.createElement('template');
  t.innerHTML = markup.trim();
  return t.content.firstChild;
}
const ICON_STAR = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/></svg>';
const ICON_SEARCH = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15zm5.3-2.2L21 21"/></svg>';

const countryNames = new Intl.DisplayNames(['fr'], { type: 'region' });
function countryName(code) {
  if (!code) return '';
  try { return countryNames.of(code.toUpperCase()) || code; } catch { return code; }
}
function fmtDate(d) {
  if (!d) return '';
  return new Date(d).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
}
function fmtDateTime(d) {
  return new Date(d).toLocaleString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function plural(n, word) { return n + ' ' + word + (n > 1 ? 's' : ''); }
function render(...nodes) { destroyMap(); app.replaceChildren(...nodes.flat().filter((n) => n !== null && n !== undefined && n !== false)); }
function errorBox(message) { return h('div', { class: 'notice error' }, message); }
function initials(name) {
  const words = String(name || '?').replace(/[^\p{L}\p{N} ]/gu, ' ').split(/\s+/).filter((w) => w.length > 2 || /^\p{Lu}/u.test(w));
  return ((words[0] || '?')[0] + (words[1] ? words[1][0] : '')).toUpperCase();
}
function monogram(name, cls = 'mono') {
  let hash = 0;
  for (const c of String(name)) hash = (hash * 31 + c.codePointAt(0)) % 360;
  return h('div', { class: cls, style: `background: linear-gradient(135deg, hsl(${hash} 55% 38%), hsl(${(hash + 40) % 360} 70% 52%))` }, initials(name));
}
function hasPos(o) { return typeof o.lat === 'number' && typeof o.lon === 'number'; }

// ---------------------------------------------------------------------------
// Connexion
// ---------------------------------------------------------------------------
function authErrorMessage(error) {
  const m = (error?.message || '').toLowerCase();
  if (m.includes('invalid login')) return 'E-mail ou mot de passe incorrect.';
  if (m.includes('already registered') || m.includes('already exists')) return 'Un compte existe déjà avec cet e-mail : utilisez « Se connecter ».';
  if (m.includes('password') && (m.includes('least') || m.includes('short'))) return 'Le mot de passe doit faire au moins 8 caractères.';
  if (m.includes('email not confirmed')) return "Ce compte attend une confirmation par e-mail : prévenez l'administrateur.";
  return error?.message || 'Erreur inconnue';
}

function renderLogin(message, mode = 'login') {
  setChrome(false);
  const creating = mode === 'signup';
  const name = h('input', { type: 'text', placeholder: 'Prénom et nom', autocomplete: 'name', required: creating });
  const email = h('input', { type: 'email', required: true, placeholder: 'Adresse e-mail', autocomplete: 'email' });
  const password = h('input', { type: 'password', required: true, minlength: '8',
    placeholder: creating ? 'Mot de passe (8 caractères minimum)' : 'Mot de passe',
    autocomplete: creating ? 'new-password' : 'current-password' });
  const button = h('button', { class: 'pill-btn accent wide', type: 'submit' }, creating ? 'Créer mon compte' : 'Se connecter');
  const status = h('div');

  const form = h('form', {
    onsubmit: async (e) => {
      e.preventDefault();
      button.disabled = true;
      status.replaceChildren();
      const credentials = { email: email.value.trim(), password: password.value };
      const { data, error } = creating
        ? await supabase.auth.signUp({ ...credentials, options: { data: { display_name: name.value.trim() } } })
        : await supabase.auth.signInWithPassword(credentials);
      button.disabled = false;
      if (error) { status.replaceChildren(errorBox(authErrorMessage(error))); return; }
      if (creating && !data.session) {
        status.replaceChildren(errorBox("Compte créé, mais la confirmation par e-mail est encore activée dans Supabase : prévenez l'administrateur."));
      }
    },
  }, creating ? name : null, email, password, button);

  const seg = h('div', { class: 'segmented login-seg' },
    h('button', { type: 'button', class: creating ? '' : 'on', onclick: () => renderLogin(null, 'login') }, 'Se connecter'),
    h('button', { type: 'button', class: creating ? 'on' : '', onclick: () => renderLogin(null, 'signup') }, 'Créer un compte'));

  render(h('section', { class: 'login' },
    h('img', { class: 'app-icon', src: '/icons/logo.svg', alt: '' }),
    h('h1', {}, 'Helibase'),
    h('p', { class: 'lead' }, "Les exploitants d'hélicoptères Airbus, livraison après livraison."),
    seg,
    message ? errorBox(message) : null,
    form, status,
    h('p', { class: 'hint' }, creating
      ? 'Votre compte est actif immédiatement.'
      : "Mot de passe oublié ? Contactez l'administrateur.")));
  (creating ? name : email).focus();
}

function setChrome(loggedIn) {
  document.getElementById('topnav').hidden = !loggedIn;
  document.getElementById('btn-logout').hidden = !loggedIn;
  const chip = document.getElementById('user-chip');
  chip.hidden = !loggedIn;
  if (loggedIn && state.profile) {
    chip.textContent = initials(state.profile.display_name || state.profile.email);
    chip.title = 'Mon compte — ' + (state.profile.display_name || '') + ' (' + state.profile.email + ')';
  }
  const navAdmin = document.getElementById('nav-admin');
  navAdmin.hidden = !(loggedIn && state.profile?.role === 'admin');
  if (!navAdmin.hidden) refreshAdminBadge();
}

// Nouveaux comptes depuis la dernière visite de l'administrateur sur la page Administration
const ADMIN_SEEN_KEY = 'helibase.adminSeenAt';
function adminSeenAt() {
  try { return localStorage.getItem(ADMIN_SEEN_KEY) || '1970-01-01T00:00:00Z'; } catch { return '1970-01-01T00:00:00Z'; }
}
async function refreshAdminBadge() {
  const nav = document.getElementById('nav-admin');
  const { count } = await supabase.from('profiles').select('id', { count: 'exact', head: true }).gt('created_at', adminSeenAt());
  nav.querySelector('.nav-badge')?.remove();
  if (count > 0 && !location.hash.startsWith('#/admin')) nav.append(h('span', { class: 'nav-badge', title: 'Nouveaux comptes' }, String(count)));
}

async function logout() {
  await supabase.auth.signOut();
}
document.getElementById('btn-logout').addEventListener('click', logout);
document.getElementById('user-chip').addEventListener('click', openAccountDialog);

// Mon compte : nom affiché, mot de passe, déconnexion
function openAccountDialog() {
  const dlg = document.getElementById('account-dialog');
  const name = h('input', { type: 'text', value: state.profile?.display_name || '' });
  const pwd = h('input', { type: 'password', minlength: '8', placeholder: 'Nouveau mot de passe (facultatif)', autocomplete: 'new-password' });
  const status = h('div');
  const save = h('button', { class: 'pill-btn accent wide', type: 'submit' }, 'Enregistrer');
  const form = h('form', { class: 'account-form', onsubmit: async (e) => {
    e.preventDefault();
    save.disabled = true; status.replaceChildren();
    const newName = name.value.trim();
    if (newName && newName !== state.profile.display_name) {
      const { error } = await supabase.from('profiles').update({ display_name: newName }).eq('id', state.session.user.id);
      if (error) { save.disabled = false; status.replaceChildren(errorBox(error.message)); return; }
      state.profile.display_name = newName;
    }
    if (pwd.value) {
      const { error } = await supabase.auth.updateUser({ password: pwd.value });
      if (error) { save.disabled = false; status.replaceChildren(errorBox(authErrorMessage(error))); return; }
    }
    save.disabled = false; pwd.value = '';
    setChrome(true);
    status.replaceChildren(h('div', { class: 'notice' }, 'Modifications enregistrées.'));
  } },
    h('label', {}, 'Nom affiché', name),
    h('label', {}, 'Mot de passe', pwd),
    save, status);
  dlg.replaceChildren(
    h('img', { src: '/icons/logo.svg', alt: '', width: '56', height: '56' }),
    h('h2', {}, 'Mon compte'),
    h('p', { class: 'small muted' }, state.profile?.email || ''),
    form,
    h('div', { class: 'dialog-actions' },
      h('button', { class: 'pill-btn', type: 'button', onclick: () => dlg.close() }, 'Fermer'),
      h('button', { class: 'pill-btn danger', type: 'button', onclick: () => { dlg.close(); logout(); } }, 'Se déconnecter')));
  dlg.showModal();
}

// ---------------------------------------------------------------------------
// Données
// ---------------------------------------------------------------------------
async function loadProfile() {
  const { data, error } = await supabase.from('profiles').select('*').eq('id', state.session.user.id).maybeSingle();
  if (error) throw error;
  state.profile = data;
}

// La base renvoie au plus 1000 lignes par requête : on lit la liste par pages
async function selectAll(build, page = 1000) {
  const rows = [];
  for (let from = 0; ; from += page) {
    const { data, error } = await build().range(from, from + page - 1);
    if (error) return { data: null, error };
    rows.push(...data);
    if (data.length < page) return { data: rows, error: null };
  }
}

async function loadOperators() {
  const [ops, favs] = await Promise.all([
    selectAll(() => supabase.from('operator_overview').select('*')
      .order('last_delivery_date', { ascending: false, nullsFirst: false }).order('id')),
    supabase.from('favorites').select('operator_id'),
  ]);
  if (ops.error) throw ops.error;
  if (favs.error) throw favs.error;
  state.operators = ops.data || [];
  state.favorites = new Set((favs.data || []).map((f) => f.operator_id));
}

async function toggleFavorite(operatorId, button) {
  const isFav = state.favorites.has(operatorId);
  button.disabled = true;
  const { error } = isFav
    ? await supabase.from('favorites').delete().eq('operator_id', operatorId).eq('user_id', state.session.user.id)
    : await supabase.from('favorites').insert({ operator_id: operatorId });
  button.disabled = false;
  if (error) { alert('Impossible de modifier le favori : ' + error.message); return; }
  if (isFav) state.favorites.delete(operatorId); else state.favorites.add(operatorId);
  updateStar(button, !isFav);
  updateMapData();
}

function starButton(operatorId) {
  const b = h('button', { class: 'star', type: 'button' }, svg(ICON_STAR));
  updateStar(b, state.favorites.has(operatorId));
  b.addEventListener('click', (e) => { e.stopPropagation(); toggleFavorite(operatorId, b); });
  return b;
}
function updateStar(b, on) {
  b.classList.toggle('on', on);
  b.title = on ? 'Retirer des favoris' : 'Ajouter aux favoris';
  b.setAttribute('aria-label', b.title);
  b.setAttribute('aria-pressed', String(on));
}

// ---------------------------------------------------------------------------
// Carte
// ---------------------------------------------------------------------------
let maplibrePromise = null;
function loadMapLibre() {
  if (window.maplibregl) return Promise.resolve(window.maplibregl);
  if (!maplibrePromise) {
    maplibrePromise = new Promise((resolve, reject) => {
      document.head.append(h('link', { rel: 'stylesheet', href: MAPLIBRE_CSS }));
      const s = h('script', { src: MAPLIBRE_JS });
      s.onload = () => resolve(window.maplibregl);
      s.onerror = () => { maplibrePromise = null; reject(new Error('La carte n\'a pas pu être chargée.')); };
      document.head.append(s);
    });
  }
  return maplibrePromise;
}

function destroyMap() {
  if (state.map) { try { state.map.remove(); } catch { /* déjà détruite */ } }
  state.map = null;
  state.bounds = null;
}

function toGeoJSON(list) {
  return {
    type: 'FeatureCollection',
    features: list.filter(hasPos).map((o) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [o.lon, o.lat] },
      properties: { id: o.id, fav: state.favorites.has(o.id) },
    })),
  };
}

function updateMapData() {
  const src = state.map?.getSource?.('ops');
  if (src) src.setData(toGeoJSON(filteredOperators()));
}

function highlightOnMap(o) {
  const src = state.map?.getSource?.('hl');
  if (!src) return;
  src.setData({ type: 'FeatureCollection', features: o && hasPos(o)
    ? [{ type: 'Feature', geometry: { type: 'Point', coordinates: [o.lon, o.lat] }, properties: {} }] : [] });
}

async function initExplorerMap(container, onMove) {
  let maplibregl;
  try { maplibregl = await loadMapLibre(); } catch (e) {
    container.append(h('div', { class: 'map-msg' }, e.message)); return;
  }
  if (!container.isConnected) return;
  const map = new maplibregl.Map({
    container, style: MAP_STYLE, center: [10, 28], zoom: 1.2,
    attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false,
  });
  state.map = map;
  map.touchZoomRotate?.disableRotation?.();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');

  map.on('load', () => {
    const fontLayer = map.getStyle().layers.find((l) => l.layout && l.layout['text-font']);
    const font = fontLayer ? fontLayer.layout['text-font'] : ['Noto Sans Regular'];
    map.addSource('ops', { type: 'geojson', data: toGeoJSON(filteredOperators()), cluster: true, clusterRadius: 46, clusterMaxZoom: 11 });
    map.addSource('hl', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'clusters', type: 'circle', source: 'ops', filter: ['has', 'point_count'],
      paint: {
        'circle-color': ACCENT, 'circle-opacity': .92, 'circle-stroke-color': '#fff', 'circle-stroke-width': 3,
        'circle-radius': ['step', ['get', 'point_count'], 15, 10, 19, 50, 25, 200, 31],
      } });
    map.addLayer({ id: 'cluster-count', type: 'symbol', source: 'ops', filter: ['has', 'point_count'],
      layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': font, 'text-size': 12.5, 'text-allow-overlap': true },
      paint: { 'text-color': '#fff' } });
    map.addLayer({ id: 'hl', type: 'circle', source: 'hl',
      paint: { 'circle-radius': 15, 'circle-color': ACCENT, 'circle-opacity': .18, 'circle-stroke-color': ACCENT, 'circle-stroke-width': 2 } });
    map.addLayer({ id: 'points', type: 'circle', source: 'ops', filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-radius': 7.5, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2.5,
        'circle-color': ['case', ['get', 'fav'], FAV, ACCENT],
      } });

    map.on('click', 'clusters', async (e) => {
      const f = e.features[0];
      const zoom = await map.getSource('ops').getClusterExpansionZoom(f.properties.cluster_id);
      map.easeTo({ center: f.geometry.coordinates, zoom });
    });
    map.on('click', 'points', (e) => {
      const o = state.operators.find((x) => x.id === e.features[0].properties.id);
      if (!o) return;
      new maplibregl.Popup({ offset: 14, closeButton: false, maxWidth: '260px' })
        .setLngLat(e.features[0].geometry.coordinates)
        .setDOMContent(h('div', {},
          h('strong', {}, o.name),
          h('div', { class: 'muted' }, [o.city, countryName(o.country)].filter(Boolean).join(', ')),
          o.last_delivery_model ? h('div', { class: 'muted' }, 'Dernière livraison : ' + o.last_delivery_model + ' · ' + fmtDate(o.last_delivery_date)) : null,
          h('a', { href: '#/exploitant/' + o.id }, 'Voir la fiche →')))
        .addTo(map);
    });
    for (const layer of ['clusters', 'points']) {
      map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
    }

    const pts = filteredOperators().filter(hasPos);
    if (pts.length) {
      const b = new maplibregl.LngLatBounds();
      pts.forEach((o) => b.extend([o.lon, o.lat]));
      map.fitBounds(b, { padding: 60, maxZoom: 6, duration: 0 });
    }
    onMove();
  });
  map.on('moveend', onMove);
}

async function miniMap(container, o, bases) {
  const points = [o, ...bases].filter(hasPos);
  if (!points.length) { container.append(h('div', { class: 'map-msg' }, 'Position non renseignée')); return; }
  let maplibregl;
  try { maplibregl = await loadMapLibre(); } catch (e) { container.append(h('div', { class: 'map-msg' }, e.message)); return; }
  if (!container.isConnected) return;
  const map = new maplibregl.Map({ container, style: MAP_STYLE, center: [points[0].lon, points[0].lat], zoom: 6,
    attributionControl: { compact: true }, interactive: true, dragRotate: false });
  state.map = map;
  points.forEach((p, i) => new maplibregl.Marker({ color: i === 0 ? ACCENT : '#86868b' }).setLngLat([p.lon, p.lat]).addTo(map));
  if (points.length > 1) {
    const b = new maplibregl.LngLatBounds();
    points.forEach((p) => b.extend([p.lon, p.lat]));
    map.fitBounds(b, { padding: 40, maxZoom: 8, duration: 0 });
  }
}

// ---------------------------------------------------------------------------
// Liste + carte des exploitants
// ---------------------------------------------------------------------------
function filteredOperators() {
  const f = state.filters;
  const q = f.q.trim().toLowerCase();
  const minDate = f.period ? new Date(Date.now() - Number(f.period) * 30.44 * 864e5) : null;
  return state.operators.filter((o) => {
    if (f.favOnly && !state.favorites.has(o.id)) return false;
    if (f.country && o.country !== f.country) return false;
    if (f.model && !(o.models || []).includes(f.model)) return false;
    if (f.activity && !(o.activities || []).includes(f.activity)) return false;
    if (minDate && (!o.last_delivery_date || new Date(o.last_delivery_date) < minDate)) return false;
    if (q) {
      const hay = [o.name, ...(o.aliases || []), o.city, countryName(o.country)].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function operatorCard(o) {
  return h('article', {
    class: 'card', tabindex: '0', 'data-id': o.id,
    onclick: () => { location.hash = '#/exploitant/' + o.id; },
    onkeydown: (e) => { if (e.key === 'Enter') location.hash = '#/exploitant/' + o.id; },
    onmouseenter: () => highlightOnMap(o),
    onmouseleave: () => highlightOnMap(null),
  },
    starButton(o.id),
    h('div', { class: 'card-top' },
      monogram(o.name),
      h('div', {},
        h('h3', {}, o.name),
        h('div', { class: 'where' }, [o.city, countryName(o.country)].filter(Boolean).join(', ') || 'Localisation inconnue',
          o.sector && o.sector !== 'civil' ? ' · ' + SECTORS[o.sector] : ''))),
    h('div', { class: 'metrics' },
      h('div', { class: 'metric' },
        h('div', { class: 'eyebrow' }, 'Dernière livraison'),
        o.last_delivery_model
          ? h('div', { class: 'value' }, o.last_delivery_model,
              h('small', {}, fmtDate(o.last_delivery_date)),
              o.last_delivery_is_new === null || o.last_delivery_is_new === undefined ? null
                : h('span', { class: 'tag ' + (o.last_delivery_is_new ? 'new' : 'used') }, o.last_delivery_is_new ? 'Neuf' : 'Occasion'))
          : h('div', { class: 'value' }, h('small', {}, '—'))),
      h('div', { class: 'metric' },
        h('div', { class: 'eyebrow' }, 'Flotte Airbus'),
        h('div', { class: 'value' }, String(o.fleet_count || 0), h('small', {}, (o.models || []).join(', '))))),
    (o.activities || []).length ? h('div', { class: 'tags' }, o.activities.map((a) => h('span', { class: 'tag' }, a))) : null);
}

function renderList() {
  const f = state.filters;
  const countries = [...new Set(state.operators.map((o) => o.country).filter(Boolean))]
    .sort((a, b) => countryName(a).localeCompare(countryName(b), 'fr'));
  const models = [...new Set(state.operators.flatMap((o) => o.models || []))].sort();

  const listBox = h('div');
  const meta = h('div', { class: 'list-meta' });
  const mapEl = h('div', { id: 'map' });
  const syncBtn = h('button', { class: 'toggle-chip' + (state.syncMap ? ' on' : ''), type: 'button',
    onclick: () => { state.syncMap = !state.syncMap; syncBtn.classList.toggle('on', state.syncMap); refreshList(); } },
    'Filtrer la liste selon la carte');
  const explorer = h('div', { class: 'explorer' },
    h('div', { class: 'list-col' }, meta, listBox),
    h('div', { class: 'map-wrap' }, mapEl, h('div', { class: 'map-overlay' }, syncBtn)));

  const mapActive = () => state.view !== 'list' && state.map;

  function refreshList() {
    const all = filteredOperators();
    let list = all;
    if (state.syncMap && mapActive() && state.bounds) {
      list = all.filter((o) => hasPos(o) && state.bounds.contains([o.lon, o.lat]));
    }
    const noPos = all.filter((o) => !hasPos(o)).length;
    meta.replaceChildren(
      h('span', {}, plural(list.length, 'exploitant'),
        state.syncMap && mapActive() && state.bounds && list.length !== all.length ? ' dans la zone visible' : ''),
      noPos && state.view !== 'list' ? h('span', { class: 'small' }, plural(noPos, 'exploitant') + ' sans position') : h('span'));
    listBox.replaceChildren(list.length
      ? h('div', { class: 'cards' }, list.map(operatorCard))
      : h('div', { class: 'empty' }, state.operators.length
          ? (state.syncMap && state.bounds ? 'Aucun exploitant dans cette zone. Dézoomez ou déplacez la carte.' : 'Aucun exploitant ne correspond à ces filtres.')
          : 'La base est encore vide.\nLe premier chargement des données (registres officiels, Airbus, Helis.com) arrive à l\'étape suivante.'));
  }
  function refreshAll() {
    updateMapData();
    refreshList();
    toolbar.querySelectorAll('.chip-select').forEach((c) => c.classList.toggle('set', !!c.querySelector('select').value));
  }

  const chip = (key, label, options) => h('div', { class: 'chip-select' + (f[key] ? ' set' : '') },
    h('select', { 'aria-label': label, onchange: (e) => { f[key] = e.target.value; refreshAll(); } },
      h('option', { value: '' }, label),
      options.map(([v, t]) => h('option', { value: v, selected: f[key] === v }, t))));

  const favChip = h('button', { class: 'toggle-chip' + (f.favOnly ? ' on' : ''), type: 'button',
    onclick: () => { f.favOnly = !f.favOnly; favChip.classList.toggle('on', f.favOnly); refreshAll(); } },
    h('span', { class: 'dot-star' }, '★'), 'Mes favoris');

  const toolbar = h('div', { class: 'toolbar' },
    h('div', { class: 'search' }, svg(ICON_SEARCH),
      h('input', { type: 'search', placeholder: 'Rechercher un exploitant, une ville…', value: f.q,
        oninput: (e) => { f.q = e.target.value; refreshAll(); } })),
    chip('country', 'Pays', countries.map((c) => [c, countryName(c)])),
    chip('model', 'Modèle', models.map((m) => [m, m])),
    chip('activity', 'Activité', ACTIVITIES.map((a) => [a, a])),
    chip('period', 'Livré depuis', [['3', '3 mois'], ['6', '6 mois'], ['12', '1 an'], ['24', '2 ans']]),
    favChip);

  const views = [['both', 'Liste et carte'], ['list', 'Liste'], ['map', 'Carte']];
  const seg = h('div', { class: 'segmented', role: 'tablist' });
  const applyView = () => {
    explorer.classList.toggle('only-list', state.view === 'list');
    explorer.classList.toggle('only-map', state.view === 'map');
    seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.v === state.view));
    if (state.map) setTimeout(() => state.map.resize(), 0);
    refreshList();
  };
  views.forEach(([v, t]) => seg.append(h('button', { type: 'button', 'data-v': v, onclick: () => { state.view = v; applyView(); } }, t)));

  render(
    h('div', { class: 'page-head' },
      h('div', {}, h('h1', {}, 'Exploitants'),
        h('div', { class: 'sub' }, plural(state.operators.length, 'exploitant') + ' d\'hélicoptères Airbus')),
      seg),
    toolbar, explorer);
  applyView();
  initExplorerMap(mapEl, () => { state.bounds = state.map?.getBounds?.() || null; refreshList(); });
}

// ---------------------------------------------------------------------------
// Fiche exploitant
// ---------------------------------------------------------------------------
async function renderOperator(id) {
  render(h('p', { class: 'muted center pad' }, 'Chargement de la fiche…'));
  const [opRes, fleetRes, ordersRes, notesRes, basesRes] = await Promise.all([
    supabase.from('operator_overview').select('*').eq('id', id).maybeSingle(),
    supabase.from('assignments').select('*, helicopters(*), sources(label, url)').eq('operator_id', id)
      .order('start_date', { ascending: false, nullsFirst: false }),
    supabase.from('orders').select('*, sources(label, url)').eq('operator_id', id).order('announced_on', { ascending: false }),
    supabase.from('notes').select('id, body, created_at, updated_at, author_id, profiles(display_name, email)')
      .eq('operator_id', id).order('created_at', { ascending: false }),
    supabase.from('bases').select('*').eq('operator_id', id),
  ]);
  const err = [opRes, fleetRes, ordersRes, notesRes, basesRes].find((r) => r.error);
  if (err) { render(errorBox('Erreur de chargement : ' + err.error.message)); return; }
  const o = opRes.data;
  if (!o) { render(h('a', { class: 'back', href: '#/' }, '‹ Exploitants'), errorBox('Exploitant introuvable.')); return; }

  const fleet = fleetRes.data || [];
  const orders = ordersRes.data || [];
  const bases = basesRes.data || [];
  const inService = fleet.filter((a) => !a.end_date);
  const pending = orders.filter((c) => c.status !== 'livree').reduce((n, c) => n + (c.quantity || 0), 0);

  const photo = o.photo_url ? h('figure', { class: 'op-photo' },
    h('img', { src: o.photo_url, alt: o.name, loading: 'lazy', referrerpolicy: 'no-referrer',
      onerror: (e) => e.target.closest('figure').remove() }),
    o.photo_credit ? h('figcaption', {}, 'Photo : ' + o.photo_credit) : null) : null;

  const hero = h('section', { class: 'hero' },
    starButton(o.id),
    monogram(o.name),
    h('div', {},
      h('h1', {}, o.name),
      h('div', { class: 'where' }, [o.city, countryName(o.country)].filter(Boolean).join(', ') || 'Localisation inconnue',
        ' · ', SECTORS[o.sector] || ''),
      (o.aliases || []).length ? h('div', { class: 'small muted' }, 'Aussi connu sous : ' + o.aliases.join(', ')) : null,
      (o.activities || []).length ? h('div', { class: 'tags' }, o.activities.map((a) => h('span', { class: 'tag' }, a))) : null));

  const tile = (label, value, sub) => h('div', { class: 'tile' }, h('div', { class: 'eyebrow' }, label),
    h('div', { class: 'value' }, value, sub ? h('small', {}, ' ' + sub) : null));
  const tiles = h('div', { class: 'tiles' },
    tile('Flotte en service', String(inService.length), inService.length > 1 ? 'appareils' : 'appareil'),
    tile('Dernière livraison', o.last_delivery_model || '—', o.last_delivery_date ? fmtDate(o.last_delivery_date) : ''),
    tile('Commandes en cours', String(pending), pending > 1 ? 'appareils' : 'appareil'),
    tile('Notes', String((notesRes.data || []).length)));

  // Présentation de la société
  const host = (url) => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; } };
  const aboutPanel = h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'La société'),
      h('button', { class: 'pill-btn', type: 'button', onclick: () => editOperatorInfo(o) }, 'Modifier')),
    o.summary ? h('p', { class: 'about' }, o.summary)
      : h('p', { class: 'muted small' }, 'Aucune présentation trouvée automatiquement. Cliquez sur « Modifier » pour en ajouter une.'),
    o.summary_source ? h('p', { class: 'small muted' }, 'Source : ',
      h('a', { href: o.summary_source, target: '_blank', rel: 'noopener' }, host(o.summary_source))) : null);

  // Coordonnées
  const line = (label, value, href) => value ? h('div', { class: 'contact-row' },
    h('span', { class: 'contact-label' }, label),
    href ? h('a', { href, target: href.startsWith('http') ? '_blank' : null, rel: 'noopener' }, value) : h('span', {}, value)) : null;
  const contactRows = [
    line('Adresse', o.address || [o.city, countryName(o.country)].filter(Boolean).join(', ')),
    line('Téléphone', o.phone, o.phone ? 'tel:' + o.phone.replace(/[^+\d]/g, '') : null),
    line('E-mail', o.email, o.email ? 'mailto:' + o.email : null),
    line('Site web', o.website ? host(o.website) : null, o.website),
  ].filter(Boolean);
  const contactPanel = h('section', { class: 'panel' },
    h('div', { class: 'panel-head' }, h('h2', {}, 'Coordonnées'),
      h('button', { class: 'pill-btn', type: 'button', onclick: () => editOperatorInfo(o) }, 'Modifier')),
    contactRows.length ? h('div', { class: 'contact' }, contactRows) : h('p', { class: 'muted small' }, 'Aucune coordonnée connue.'),
    o.info_edited_at ? h('p', { class: 'small muted' }, 'Complété à la main le ' + fmtDate(o.info_edited_at)) : null);

  // Flotte : repliée, visible au clic
  const fleetTable = fleet.length
    ? h('table', {},
        h('thead', {}, h('tr', {}, ['Modèle', 'N° de série', 'Immat.', 'Date', '', 'Source'].map((t) => h('th', {}, t)))),
        h('tbody', {}, fleet.map((a) => h('tr', {},
          h('td', {}, h('strong', {}, a.helicopters?.model || '')),
          h('td', {}, a.helicopters?.msn || ''),
          h('td', {}, a.registration || ''),
          h('td', {}, a.start_date ? fmtDate(a.start_date) : h('span', { class: 'muted small' }, 'date inconnue')),
          h('td', {}, a.is_new === null ? null : h('span', { class: 'tag ' + (a.is_new ? 'new' : 'used') }, a.is_new ? 'Neuf' : 'Occasion'),
            a.end_date ? h('div', { class: 'small muted' }, 'parti le ' + fmtDate(a.end_date)) : null),
          h('td', { class: 'small' }, a.sources?.url ? h('a', { href: a.sources.url, target: '_blank', rel: 'noopener' }, a.sources.label) : (a.sources?.label || ''))))))
    : h('p', { class: 'muted' }, 'Aucun appareil enregistré.');
  const models = [...new Set(inService.map((a) => a.helicopters?.model).filter(Boolean))];
  const fleetPanel = h('details', { class: 'panel fleet' },
    h('summary', {}, h('h2', {}, 'Flotte Airbus'),
      h('span', { class: 'muted small' }, plural(inService.length, 'appareil') + (models.length ? ' · ' + models.join(', ') : '')),
      h('span', { class: 'chevron', 'aria-hidden': 'true' }, '›')),
    fleetTable);

  const upcoming = orders.filter((c) => c.status !== 'livree');
  const pressDelivered = orders.filter((c) => c.status === 'livree');
  const srcLink = (c) => c.sources?.url ? h('a', { href: c.sources.url, target: '_blank', rel: 'noopener' }, c.sources.label || 'Article')
    : (c.sources?.label || '');
  const ordersPanel = [
    pressDelivered.length ? h('section', { class: 'panel' }, h('h2', {}, 'Livraisons relevées dans la presse'),
      h('table', {}, h('tbody', {}, pressDelivered.map((c) => h('tr', {},
        h('td', {}, h('strong', {}, (c.quantity ? c.quantity + ' × ' : '') + c.model)),
        h('td', {}, c.announced_on ? fmtDate(c.announced_on) : ''),
        h('td', { class: 'small' }, srcLink(c))))))) : null,
    upcoming.length ? h('section', { class: 'panel' }, h('h2', {}, 'Livraisons à venir'),
      h('table', {}, h('tbody', {}, upcoming.map((c) => h('tr', {},
        h('td', {}, h('strong', {}, (c.quantity ? c.quantity + ' × ' : '') + c.model)),
        h('td', {}, c.expected ? 'Échéance ' + c.expected : ''),
        h('td', { class: 'small muted' }, c.announced_on ? 'Annoncé le ' + fmtDate(c.announced_on) : ''),
        h('td', { class: 'small' }, srcLink(c))))))) : null,
  ];
  const mapBox = h('div', { class: 'mini-map' });
  const locPanel = h('section', { class: 'panel' }, h('h2', {}, 'Localisation'), mapBox,
    bases.length ? h('p', { class: 'small muted' }, 'Bases : ' + bases.map((b) => b.name).join(', ')) : null);

  render(
    h('a', { class: 'back', href: '#/' }, '‹ Exploitants'),
    photo, hero, tiles,
    h('div', { class: 'grid-2' },
      h('div', {}, aboutPanel, contactPanel, fleetPanel, ordersPanel),
      h('div', {}, notesPanel(o.id, notesRes.data || []), locPanel)));
  miniMap(mapBox, o, bases);
}

function editOperatorInfo(o) {
  const field = (name, label, value, type = 'text', multiline = false) => h('label', { class: 'field' },
    h('span', {}, label),
    multiline ? h('textarea', { name, rows: 5 }, value || '') : h('input', { name, type, value: value || '' }));
  const dlg = h('dialog', { class: 'sheet' });
  const form = h('form', { class: 'account-form', onsubmit: async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    const info = {};
    for (const [k, v] of Object.entries(data)) if ((v || '') !== (o[k] || '')) info[k] = v;
    if (!Object.keys(info).length) { dlg.close(); return; }
    const { error } = await supabase.rpc('update_operator_info', { target: o.id, info });
    if (error) { alert('Enregistrement impossible : ' + error.message); return; }
    dlg.close();
    renderOperator(o.id);
  } },
    h('h2', {}, o.name),
    field('summary', 'Présentation de la société', o.summary, 'text', true),
    field('address', 'Adresse', o.address),
    field('phone', 'Téléphone', o.phone, 'tel'),
    field('email', 'E-mail', o.email, 'email'),
    field('website', 'Site web', o.website, 'url'),
    field('photo_url', 'Adresse d\'une photo (lien vers une image)', o.photo_url, 'url'),
    h('div', { class: 'dialog-actions' },
      h('button', { class: 'pill-btn', type: 'button', onclick: () => dlg.close() }, 'Annuler'),
      h('button', { class: 'pill-btn accent', type: 'submit' }, 'Enregistrer')));
  dlg.append(form);
  dlg.addEventListener('close', () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}

function notesPanel(operatorId, notes) {
  const list = h('ul', { class: 'notes' });
  const draw = () => {
    list.replaceChildren(...(notes.length ? notes.map((n) => {
      const canDelete = n.author_id === state.session.user.id || state.profile?.role === 'admin';
      return h('li', { class: 'note' },
        h('div', { class: 'note-meta' },
          h('strong', {}, n.profiles?.display_name || n.profiles?.email || 'Utilisateur'),
          h('span', {}, fmtDateTime(n.created_at)),
          canDelete ? h('button', { type: 'button', onclick: async () => {
            if (!confirm('Supprimer cette note ?')) return;
            const { error } = await supabase.from('notes').delete().eq('id', n.id);
            if (error) { alert('Suppression impossible : ' + error.message); return; }
            notes = notes.filter((x) => x.id !== n.id); draw();
          } }, 'Supprimer') : null),
        h('div', { class: 'note-body' }, n.body));
    }) : [h('li', { class: 'muted small' }, 'Aucune note pour le moment.')]));
  };
  const text = h('textarea', { placeholder: 'Ajouter une note : contact, échange, idée de reportage…', required: true });
  const btn = h('button', { class: 'pill-btn accent', type: 'submit' }, 'Ajouter');
  const form = h('form', { class: 'note-form', onsubmit: async (e) => {
    e.preventDefault();
    const body = text.value.trim();
    if (!body) return;
    btn.disabled = true;
    const { data, error } = await supabase.from('notes').insert({ operator_id: operatorId, body })
      .select('id, body, created_at, updated_at, author_id, profiles(display_name, email)').single();
    btn.disabled = false;
    if (error) { alert("Impossible d'ajouter la note : " + error.message); return; }
    notes = [data, ...notes]; text.value = ''; draw();
  } }, text, btn);
  draw();
  return h('section', { class: 'panel' }, h('h2', {}, 'Notes'), form, list);
}

// ---------------------------------------------------------------------------
// Administration
// ---------------------------------------------------------------------------
function adminTabs(active) {
  return h('div', { class: 'segmented admin-tabs' },
    h('a', { href: '#/admin', class: active === 'users' ? 'on' : null }, 'Utilisateurs'),
    h('a', { href: '#/admin/verifier', class: active === 'review' ? 'on' : null }, 'À vérifier'));
}

// ---------------------------------------------------------------------------
// Administration : annonces à vérifier (presse) et appareils de loueurs à rattacher
// ---------------------------------------------------------------------------
async function renderReview() {
  if (state.profile?.role !== 'admin') { location.hash = '#/'; return; }
  render(adminTabs('review'), h('p', { class: 'muted center pad' }, 'Chargement…'));
  const [q, ops] = await Promise.all([
    selectAll(() => supabase.from('review_queue').select('*').eq('status', 'a_verifier').order('created_at', { ascending: false }).order('id')),
    state.operators?.length ? { data: state.operators } : loadOperators().then(() => ({ data: state.operators })),
  ]);
  if (q.error) { render(adminTabs('review'), errorBox(q.error.message)); return; }
  let items = q.data;
  const filter = state.reviewFilter || 'presse';
  const isPress = (it) => it.payload?.origin === 'presse';
  const shown = items.filter((it) => (filter === 'presse') === isPress(it));
  const opNames = h('datalist', { id: 'op-names' }, (ops.data || []).map((o) => h('option', { value: o.name }, countryName(o.country))));

  const card = (it) => {
    const p = it.payload || {};
    const press = isPress(it);
    const field = (label, input) => h('label', { class: 'field' }, h('span', {}, label), input);
    const opIn = h('input', { list: 'op-names', value: press ? (p.operator || '') : '', placeholder: 'Nom de l\'exploitant' });
    const ctyIn = h('input', { value: (p.country || '').toUpperCase(), maxlength: 2, placeholder: 'FR', class: 'cty' });
    const kindIn = h('select', {}, h('option', { value: 'livraison', selected: it.kind === 'livraison' }, 'Livraison'),
      h('option', { value: 'commande', selected: it.kind === 'commande' }, 'Commande'));
    const modelIn = h('input', { value: p.model || '', class: 'mdl' });
    const qtyIn = h('input', { type: 'number', min: 1, value: p.quantity || '', class: 'qty' });
    const dateIn = h('input', { type: 'date', value: p.date || '' });
    // Le pays de l'exploitant choisi dans la liste est repris automatiquement
    opIn.addEventListener('change', () => {
      const o = (ops.data || []).find((x) => x.name.toLowerCase() === opIn.value.trim().toLowerCase());
      if (o && o.country) ctyIn.value = o.country;
    });
    const done = (msg) => { el.replaceChildren(h('p', { class: 'small muted' }, msg)); el.classList.add('done'); };
    const decide = async (status) => supabase.from('review_queue')
      .update({ status, decided_by: state.session.user.id, decided_at: new Date().toISOString() }).eq('id', it.id);

    const findOrCreateOperator = async () => {
      const name = opIn.value.trim(); const country = ctyIn.value.trim().toUpperCase() || null;
      if (!name) { alert('Indiquez le nom de l\'exploitant.'); return null; }
      let o = (state.operators || []).find((x) => x.name.toLowerCase() === name.toLowerCase() && (!country || x.country === country));
      if (o) return o;
      const { data, error } = await supabase.from('operators').insert({ name, country, activities: [], sector: 'civil' }).select().single();
      if (error) { alert('Création de l\'exploitant impossible : ' + error.message); return null; }
      state.operators = [...(state.operators || []), data];
      return data;
    };

    const validate = async () => {
      const o = await findOrCreateOperator(); if (!o) return;
      if (press) {
        const src = await supabase.from('sources').insert({ kind: 'presse', label: p.source || 'Presse', url: p.link || null,
          retrieved_on: new Date().toISOString().slice(0, 10), confidence: 3 }).select().single();
        if (src.error) { alert(src.error.message); return; }
        const { error } = await supabase.from('orders').insert({ operator_id: o.id, model: modelIn.value.trim() || p.model,
          quantity: Number(qtyIn.value) || null, announced_on: dateIn.value || null,
          status: kindIn.value === 'livraison' ? 'livree' : 'annoncee', source_id: src.data.id });
        if (error) { alert(error.message); return; }
      } else {
        const hel = await supabase.from('helicopters').select('id').eq('family', p.family).eq('msn', p.msn).maybeSingle();
        if (hel.error || !hel.data) { alert('Appareil introuvable dans la base.'); return; }
        const { error } = await supabase.from('assignments').update({ operator_id: o.id, owner_name: null })
          .eq('helicopter_id', hel.data.id).is('end_date', null);
        if (error) { alert(error.message); return; }
      }
      const { error } = await decide('valide'); if (error) { alert(error.message); return; }
      done('Validé : ' + o.name);
    };
    const reject = async () => { const { error } = await decide('rejete'); if (error) { alert(error.message); return; } done('Rejeté'); };

    const el = h('article', { class: 'review-card' },
      h('div', { class: 'review-head' },
        h('span', { class: 'tag ' + (it.kind === 'commande' ? 'used' : 'new') }, it.kind === 'commande' ? 'Commande' : 'Livraison'),
        h('strong', {}, it.summary),
        h('span', { class: 'small muted' }, [p.date ? fmtDate(p.date) : fmtDate(it.created_at), p.source].filter(Boolean).join(' · '))),
      press && p.title ? h('p', { class: 'review-title' }, h('a', { href: p.link, target: '_blank', rel: 'noopener' }, p.title + ' ↗')) : null,
      !press ? h('p', { class: 'small muted' }, 'Propriétaire au registre : ' + (p.owner || '?') + (p.city ? ' (' + p.city + ')' : '')) : null,
      h('div', { class: 'review-fields' },
        field('Exploitant', opIn), field('Pays', ctyIn),
        press ? [field('Type', kindIn), field('Modèle', modelIn), field('Quantité', qtyIn), field('Date', dateIn)] : null),
      h('div', { class: 'row-actions' },
        h('button', { class: 'pill-btn', type: 'button', onclick: reject }, 'Rejeter'),
        h('button', { class: 'pill-btn accent', type: 'button', onclick: validate }, press ? 'Valider' : 'Rattacher')));
    return el;
  };

  const seg = (key, label, n) => h('button', { type: 'button', class: filter === key ? 'on' : null,
    onclick: () => { state.reviewFilter = key; renderReview(); } }, label + ' (' + n + ')');
  render(
    adminTabs('review'),
    h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'À vérifier'),
      h('div', { class: 'sub' }, 'Annonces repérées dans la presse et appareils de loueurs dont l\'exploitant reste à identifier.'))),
    h('div', { class: 'segmented' }, seg('presse', 'Presse', items.filter(isPress).length), seg('registre', 'Registres', items.filter((x) => !isPress(x)).length)),
    opNames,
    shown.length ? h('div', { class: 'review-list' }, shown.slice(0, 200).map(card))
      : h('p', { class: 'muted center pad' }, 'Rien à vérifier pour le moment.'),
    shown.length > 200 ? h('p', { class: 'small muted center' }, '200 premières annonces affichées : validez-les pour voir la suite.') : null);
}

async function renderAdmin() {
  if (state.profile?.role !== 'admin') { location.hash = '#/'; return; }
  render(h('p', { class: 'muted center pad' }, 'Chargement…'));
  const { data, error } = await supabase.rpc('admin_list_users');
  if (error) { render(errorBox(error.message)); return; }
  const seenAt = adminSeenAt();
  const isNew = (p) => new Date(p.created_at) > new Date(seenAt);
  const newCount = data.filter(isNew).length;
  try { localStorage.setItem(ADMIN_SEEN_KEY, new Date().toISOString()); } catch { /* navigation privée */ }
  document.querySelector('#nav-admin .nav-badge')?.remove();

  const act = async (fn, args, label) => {
    if (!confirm(label + ' ?')) return;
    const { error: e } = await supabase.rpc(fn, args);
    if (e) { alert(e.message); return; }
    renderAdmin();
  };
  const active = data.filter((p) => p.status === 'active').length;

  render(
    adminTabs('users'),
    h('div', { class: 'page-head' }, h('div', {}, h('h1', {}, 'Administration'),
      h('div', { class: 'sub' }, plural(data.length, 'compte') + ' · ' + plural(active, 'actif') +
        (newCount ? ' · ' + newCount + (newCount > 1 ? ' nouveaux' : ' nouveau') + ' depuis votre dernière visite' : '')))),
    h('section', { class: 'panel' },
      h('h2', {}, 'Utilisateurs'),
      h('p', { class: 'small muted' }, 'Tout nouveau compte a accès immédiatement. Révoquez un compte pour lui couper l\'accès aux données.'),
      h('table', {},
        h('thead', {}, h('tr', {}, ['Utilisateur', 'Inscrit le', 'Dernière connexion', 'Statut', ''].map((t) => h('th', {}, t)))),
        h('tbody', {}, data.map((p) => {
          const self = p.id === state.session.user.id;
          return h('tr', { class: isNew(p) ? 'row-new' : null },
            h('td', {}, h('div', {}, h('strong', {}, p.display_name || ''), isNew(p) ? h('span', { class: 'badge new-account' }, 'Nouveau') : null),
              h('div', { class: 'small muted' }, p.email)),
            h('td', {}, fmtDate(p.created_at)),
            h('td', { class: 'small' }, p.last_sign_in_at ? fmtDateTime(p.last_sign_in_at) : '—'),
            h('td', {},
              h('span', { class: 'badge ' + p.status }, p.status === 'active' ? 'Actif' : 'Révoqué'), ' ',
              p.role === 'admin' ? h('span', { class: 'badge admin' }, 'Admin') : null),
            h('td', {}, self ? h('span', { class: 'small muted' }, 'Vous') : h('div', { class: 'row-actions' },
              p.status === 'active'
                ? h('button', { class: 'pill-btn danger', onclick: () => act('set_user_status', { target: p.id, new_status: 'revoked' }, 'Révoquer ' + p.email) }, 'Révoquer')
                : h('button', { class: 'pill-btn', onclick: () => act('set_user_status', { target: p.id, new_status: 'active' }, 'Réactiver ' + p.email) }, 'Réactiver'),
              p.role === 'admin'
                ? h('button', { class: 'pill-btn', onclick: () => act('set_user_role', { target: p.id, new_role: 'user' }, 'Retirer les droits d\'administrateur de ' + p.email) }, 'Retirer admin')
                : h('button', { class: 'pill-btn', onclick: () => act('set_user_role', { target: p.id, new_role: 'admin' }, 'Nommer ' + p.email + ' administrateur') }, 'Nommer admin'))));
        })))));
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
async function route() {
  if (!state.session) { renderLogin(); return; }
  if (state.profile?.status === 'revoked') {
    setChrome(false);
    document.getElementById('btn-logout').hidden = false;
    render(h('section', { class: 'login' }, h('img', { class: 'app-icon', src: '/icons/logo.svg', alt: '' }),
      h('h1', {}, 'Accès révoqué'),
      h('p', { class: 'lead' }, "Votre accès à Helibase a été désactivé par l'administrateur.")));
    return;
  }
  setChrome(true);
  const hash = location.hash || '#/';
  document.querySelectorAll('.topnav a').forEach((a) =>
    a.classList.toggle('active', (a.dataset.nav === 'admin') === hash.startsWith('#/admin')));
  try {
    if (hash.startsWith('#/exploitant/')) await renderOperator(decodeURIComponent(hash.split('/')[2]));
    else if (hash.startsWith('#/admin/verifier')) await renderReview();
    else if (hash.startsWith('#/admin')) await renderAdmin();
    else { await loadOperators(); renderList(); }
  } catch (e) {
    render(errorBox('Erreur : ' + (e.message || e)));
  }
  window.scrollTo(0, 0);
}

window.addEventListener('hashchange', route);

async function onSession(session) {
  state.session = session;
  state.profile = null;
  if (session) {
    try { await loadProfile(); } catch (e) { render(errorBox('Erreur de profil : ' + e.message)); return; }
  }
  route();
}

supabase.auth.onAuthStateChange((event, session) => {
  if (event === 'INITIAL_SESSION' || event === 'SIGNED_IN' || event === 'SIGNED_OUT') {
    setTimeout(() => onSession(session), 0);
  }
});

const hashParams = new URLSearchParams(location.hash.replace(/^#/, ''));
if (hashParams.get('error_description')) {
  history.replaceState(null, '', location.pathname);
  renderLogin('Le lien de connexion a expiré ou a déjà servi. Demandez-en un nouveau.');
}

// ---------------------------------------------------------------------------
// Installation comme une application
// ---------------------------------------------------------------------------
const installBtn = document.getElementById('btn-install');
const installDialog = document.getElementById('install-dialog');
let deferredPrompt = null;

const isStandalone = () =>
  window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
const ua = navigator.userAgent;
const isIOS = /iPhone|iPad|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const isSafari = /Safari/.test(ua) && !/Chrome|Chromium|CriOS|Edg|OPR|Firefox|FxiOS/.test(ua);
const isMac = /Macintosh/.test(ua) && !isIOS;

function installSteps() {
  if (isIOS) {
    return ['Touchez le bouton Partager (le carré avec une flèche vers le haut).',
      'Choisissez « Sur l\'écran d\'accueil ».', 'Touchez « Ajouter » : Helibase apparaît avec son icône.'];
  }
  if (isMac && isSafari) {
    return ['Dans la barre de menus de Safari, ouvrez le menu « Fichier ».',
      'Choisissez « Ajouter au Dock… ».', 'Cliquez sur « Ajouter » : Helibase s\'ouvre dans sa propre fenêtre depuis le Dock.'];
  }
  if (/Firefox/.test(ua)) {
    return ['Firefox ne sait pas installer les applications web.',
      'Ouvrez Helibase dans Safari, Chrome ou Edge, puis cliquez à nouveau sur « Installer l\'app ».'];
  }
  return ['Cliquez sur l\'icône d\'installation à droite de la barre d\'adresse (un écran avec une flèche).',
    'Ou ouvrez le menu du navigateur (⋮) et choisissez « Installer Helibase ».'];
}

function refreshInstallButton() {
  installBtn.hidden = isStandalone() || !(deferredPrompt || isIOS || isSafari || isMac || /Chrome|Edg/.test(ua));
}

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  refreshInstallButton();
});
window.addEventListener('appinstalled', () => { deferredPrompt = null; installBtn.hidden = true; });

installBtn.addEventListener('click', async () => {
  if (deferredPrompt) {
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    refreshInstallButton();
    return;
  }
  document.getElementById('install-steps').replaceChildren(
    h('ol', {}, installSteps().map((s) => h('li', {}, s))));
  installDialog.showModal();
});

refreshInstallButton();
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
