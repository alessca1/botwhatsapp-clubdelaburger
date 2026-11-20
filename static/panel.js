'use strict';
const $ = (id) => document.getElementById(id);
const COLUMNS = [
  ['nuevo', 'Nuevos'], ['confirmado', 'Confirmados'], ['en_cocina', 'En cocina'],
  ['listo', 'Listos'], ['en_camino', 'En camino'], ['entregado', 'Entregados'], ['cancelado', 'Cancelados'],
];
const NEXT = {
  nuevo: [['confirmado', 'Confirmar']], confirmado: [['en_cocina', 'A cocina']],
  en_cocina: [['listo', 'Listo']], listo: [['en_camino', 'Salió'], ['entregado', 'Entregado']],
  en_camino: [['entregado', 'Entregado']], entregado: [], cancelado: [['nuevo', 'Reabrir']],
};
let token = '';
let seenOrders = null;
let currentChat = null;
let currentTab = 'orders';

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function money(n) { return n == null ? '—' : '$' + Number(n).toLocaleString('es-AR'); }

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  if (res.status === 401) { logout(); throw new Error('401'); }
  if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.detail || res.status); }
  return res.json();
}

function dateParam() { const d = $('serviceDate').value; return d ? '?service_date=' + d : ''; }

function beep() {
  try {
    const ctx = new AudioContext(); const o = ctx.createOscillator(); const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination); o.frequency.value = 880; g.gain.value = 0.15;
    o.start(); setTimeout(() => { o.stop(); ctx.close(); }, 350);
  } catch (e) { /* audio blocked until user interacts */ }
}

async function loadStatus() {
  const s = await api('/admin/status');
  const state = s.open_now ? '🟢 Abierto' : (s.order_window === 'preorder' ? '🟡 Tomando pre-pedidos' : '🔴 Cerrado');
  const warn = s.products_without_price ? ` · ⚠️ ${s.products_without_price} productos sin precio` : '';
  const bot = s.bot_enabled ? '' : ' · Bot APAGADO';
  const number = s.business_number_display ? ` · ${s.business_number_display}` : '';
  $('status').textContent = `${state} · ${s.local_time}${number}${bot}${warn}`;
}

async function loadOrders() {
  const data = await api('/admin/orders' + dateParam());
  if (!$('serviceDate').value) $('serviceDate').value = data.service_date;
  const ids = new Set(data.orders.map((o) => o.id));
  if (seenOrders && data.orders.some((o) => !seenOrders.has(o.id))) beep();
  seenOrders = ids;
  const active = data.orders.filter((o) => o.status !== 'cancelado');
  const total = active.reduce((a, o) => a + o.total, 0);
  $('orderSummary').innerHTML = `<div><b>${active.length}</b>pedidos</div><div><b>${money(total)}</b>total</div>` +
    `<div><b>${data.orders.filter((o) => o.status === 'nuevo').length}</b>sin confirmar</div>`;
  $('board').innerHTML = COLUMNS.map(([key, label]) => {
    const list = data.orders.filter((o) => o.status === key)
      .sort((a, b) => (a.slot === 'ASAP' ? '00' : a.slot).localeCompare(b.slot === 'ASAP' ? '00' : b.slot));
    if (!list.length && (key === 'cancelado' || key === 'entregado')) return '';
    return `<div class="col"><h3>${label} (${list.length})</h3>${list.map(orderCard).join('')}</div>`;
  }).join('');
}

function orderCard(o) {
  const items = o.items.map((i) => `<li>${i.qty} x ${esc(i.name)}${i.note ? ' <i>(' + esc(i.note) + ')</i>' : ''}</li>`).join('');
  const slot = o.slot === 'ASAP' ? 'Lo antes posible' : o.slot;
  const mode = o.mode === 'delivery' ? `🛵 ${esc(o.address)}` : '🥡 Retira';
  const pay = esc(o.payment) + (o.receipt_received ? ' <span class="tag ok">comprobante</span>' : '');
  const fee = o.mode === 'delivery' && o.delivery_fee == null ? '<span class="tag warn">envío a cotizar</span>' : '';
  const buttons = (NEXT[o.status] || []).map(([s, l]) => `<button data-order="${o.id}" data-status="${s}">${l}</button>`).join('');
  const feeBtn = o.mode === 'delivery' && !['entregado', 'cancelado'].includes(o.status)
    ? `<button class="ghost" data-fee="${o.id}" data-status="${o.status}">Envío $</button>` : '';
  const cancel = !['cancelado', 'entregado'].includes(o.status)
    ? `<button class="ghost" data-order="${o.id}" data-status="cancelado">Cancelar</button>` : '';
  return `<div class="order ${o.status === 'nuevo' ? 'new' : ''}">
    <div class="top"><span>#${o.number} · ${esc(slot)}</span><span>${money(o.total)}</span></div>
    <div class="meta">${esc(o.name)} · +${esc(o.phone)}</div>
    <ul>${items}</ul>
    <div class="meta">${mode}</div>
    <div class="meta">💳 ${pay} ${fee}</div>
    <div class="actions">${buttons}${feeBtn}${cancel}<button class="ghost" data-chat="${o.conversation_id}">Chat</button></div>
  </div>`;
}

$('board').addEventListener('click', async (e) => {
  const b = e.target.closest('button'); if (!b) return;
  try {
    if (b.dataset.chat) { switchTab('chats'); openChat(b.dataset.chat); return; }
    if (b.dataset.fee) {
      const value = prompt('Costo del envío (solo números):'); if (value === null) return;
      const fee = parseInt(value.replace(/\D/g, ''), 10); if (Number.isNaN(fee)) return;
      const status = b.dataset.status === 'nuevo' ? 'confirmado' : b.dataset.status;
      await api(`/admin/orders/${b.dataset.fee}/status`, { method: 'POST', body: JSON.stringify({ status, delivery_fee: fee }) });
    } else {
      if (b.dataset.status === 'cancelado' && !confirm('¿Cancelar el pedido? Se le avisa al cliente y se devuelve el stock.')) return;
      b.disabled = true;
      await api(`/admin/orders/${b.dataset.order}/status`, { method: 'POST', body: JSON.stringify({ status: b.dataset.status }) });
    }
    loadOrders();
  } catch (err) { alert('No se pudo actualizar: ' + err.message); }
});

async function loadStock() {
  const data = await api('/admin/stock' + dateParam());
  $('stockTable').innerHTML = '<tr><th>Insumo</th><th>Inicial</th><th>Usado</th><th>Quedan</th><th>Ajustar quedan</th><th>Por defecto cada noche</th><th></th></tr>' +
    data.insumos.map((i) => {
      const left = i.remaining == null ? 'Sin control' : i.remaining;
      const cls = i.remaining === 0 ? 'zero' : (i.remaining != null && i.remaining < 10 ? 'low' : '');
      return `<tr data-id="${i.id}">
        <td>${esc(i.name)} <span class="desc">(${esc(i.unit)})</span></td>
        <td>${i.initial ?? '—'}</td><td>${i.used}</td><td class="${cls}">${left}</td>
        <td><input type="number" min="0" class="remaining" placeholder="${i.remaining ?? ''}"> <button data-act="remaining">Guardar</button></td>
        <td><input type="number" min="0" class="default" placeholder="${i.default_daily ?? 'sin control'}"> <button class="ghost" data-act="default">Guardar</button></td>
        <td><button class="ghost" data-act="zero">Agotado</button> <button class="ghost" data-act="unlimited">Sin control</button></td></tr>`;
    }).join('');
}

$('stockTable').addEventListener('click', async (e) => {
  const b = e.target.closest('button'); if (!b) return;
  const row = b.closest('tr'); const id = row.dataset.id; let body;
  if (b.dataset.act === 'remaining') {
    const v = row.querySelector('.remaining').value; if (v === '') return; body = { remaining: parseInt(v, 10) };
  } else if (b.dataset.act === 'default') {
    const v = row.querySelector('.default').value; body = v === '' ? { clear_default: true } : { default_daily: parseInt(v, 10) };
  } else if (b.dataset.act === 'zero') { body = { remaining: 0 }; } else { body = { unlimited: true }; }
  try { await api(`/admin/stock/${id}${dateParam()}`, { method: 'PUT', body: JSON.stringify(body) }); loadStock(); }
  catch (err) { alert('No se pudo guardar: ' + err.message); }
});

async function loadMenu() {
  const items = await api('/admin/menu');
  $('menuTable').innerHTML = '<tr><th>Producto</th><th>Precio directo por variante</th><th>Disponible</th><th>Receta (insumos por unidad)</th></tr>' +
    items.map((p) => {
      const prices = p.variants.length
        ? p.variants.map((v) => `<label class="variantRow">${esc(v.name)} <input type="number" min="0" class="variant" data-vid="${esc(v.id)}" value="${v.price ?? ''}" placeholder="sin precio"></label>`).join('')
        : `<input type="number" min="0" class="price" value="${p.price ?? ''}" placeholder="sin precio">`;
      return `<tr data-id="${p.id}">
      <td><b>${esc(p.name)}</b>${p.days.length ? ' <span class="tag">' + esc(p.days.join(', ')) + '</span>' : ''}<div class="desc">${esc(p.description)}</div></td>
      <td>${prices} <button data-act="price">Guardar</button></td>
      <td><input type="checkbox" class="avail" ${p.available ? 'checked' : ''}></td>
      <td class="desc">${Object.entries(p.recipe).map(([k, v]) => esc(k) + ' x' + v).join(', ')}</td></tr>`;
    }).join('');
}

$('menuTable').addEventListener('click', async (e) => {
  const b = e.target.closest('button'); if (!b) return;
  const row = b.closest('tr'); let body;
  const variants = [...row.querySelectorAll('.variant')];
  if (variants.length) {
    const values = {};
    for (const input of variants) {
      if (input.value !== '') values[input.dataset.vid] = parseInt(input.value, 10);
    }
    body = { variants: values };
  } else {
    const v = row.querySelector('.price').value;
    body = v === '' ? { clear_price: true } : { price: parseInt(v, 10) };
  }
  try { await api(`/admin/menu/${row.dataset.id}`, { method: 'PATCH', body: JSON.stringify(body) }); loadMenu(); loadStatus(); }
  catch (err) { alert('No se pudo guardar: ' + err.message); }
});
$('menuTable').addEventListener('change', async (e) => {
  if (!e.target.classList.contains('avail')) return;
  const row = e.target.closest('tr');
  try { await api(`/admin/menu/${row.dataset.id}`, { method: 'PATCH', body: JSON.stringify({ available: e.target.checked }) }); }
  catch (err) { alert('No se pudo guardar: ' + err.message); loadMenu(); }
});

async function loadChats() {
  const list = await api('/admin/conversations');
  const pending = list.filter((c) => c.needs_human).length;
  $('chatBadge').hidden = !pending; $('chatBadge').textContent = pending;
  if (currentTab !== 'chats') return;
  $('chatList').innerHTML = list.map((c) => `<li data-id="${esc(c.id)}" class="${c.id === currentChat ? 'active' : ''}">
    ${c.needs_human ? '🙋 ' : ''}${c.bot_paused ? '⏸️ ' : ''}<b>${esc(c.name || '+' + c.wa_id)}</b>
    <small>+${esc(c.wa_id)} · ${new Date(c.updated * 1000).toLocaleString('es-AR')}</small></li>`).join('');
}

async function openChat(id) {
  currentChat = id; $('replyForm').hidden = false;
  const msgs = await api(`/admin/conversations/${encodeURIComponent(id)}/messages`);
  $('chatHead').textContent = '+' + id.split(':').pop();
  const box = $('messages');
  box.innerHTML = msgs.map((m) => `<div class="msg ${m.direction}"><small>${m.direction === 'in' ? 'Cliente' : m.direction === 'bot' ? 'Bot' : 'Local'} · ${new Date(m.created * 1000).toLocaleTimeString('es-AR')}</small>${esc(m.text)}</div>`).join('');
  box.scrollTop = box.scrollHeight;
  loadChats();
}

$('chatList').addEventListener('click', (e) => { const li = e.target.closest('li'); if (li) openChat(li.dataset.id); });
$('replyForm').addEventListener('submit', async (e) => {
  e.preventDefault(); const text = $('replyText').value.trim(); if (!text || !currentChat) return;
  try { await api(`/admin/conversations/${encodeURIComponent(currentChat)}/reply`, { method: 'POST', body: JSON.stringify({ text }) }); $('replyText').value = ''; openChat(currentChat); }
  catch (err) { alert('No se pudo enviar: ' + err.message); }
});
for (const [id, action] of [['pauseBot', 'pause'], ['resumeBot', 'resume'], ['resolved', 'resolved']]) {
  $(id).addEventListener('click', async () => {
    if (!currentChat) return;
    await api(`/admin/conversations/${encodeURIComponent(currentChat)}/bot`, { method: 'POST', body: JSON.stringify({ action }) });
    loadChats();
  });
}

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('nav button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.tab').forEach((t) => { t.hidden = t.id !== 'tab-' + tab; });
  refresh();
}
document.querySelectorAll('nav button').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
$('serviceDate').addEventListener('change', () => { seenOrders = null; refresh(); });

async function refresh() {
  try {
    await loadStatus();
    if (currentTab === 'orders') await loadOrders();
    if (currentTab === 'stock') await loadStock();
    if (currentTab === 'menu') await loadMenu();
    await loadChats();
  } catch (e) { /* shown on next refresh */ }
}

function logout() { try { sessionStorage.removeItem('club_token'); } catch (e) { /* ignore */ } token = ''; $('app').hidden = true; $('login').hidden = false; }
async function start() {
  $('login').hidden = true; $('app').hidden = false;
  await refresh();
  setInterval(() => { if (currentTab === 'orders' || currentTab === 'chats') refresh(); }, 10000);
}
$('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault(); token = $('token').value.trim();
  try { await fetch('/admin/status', { headers: { Authorization: 'Bearer ' + token } }).then((r) => { if (!r.ok) throw new Error(); });
    try { sessionStorage.setItem('club_token', token); } catch (err) { /* private mode */ }
    start();
  } catch (err) { $('loginError').textContent = 'Clave incorrecta'; }
});
try { token = sessionStorage.getItem('club_token') || ''; } catch (e) { token = ''; }
if (token) start();
