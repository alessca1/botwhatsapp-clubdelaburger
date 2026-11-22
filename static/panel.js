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
const TAB_META = {
  orders: ['Servicio', 'Pedidos'], stock: ['Noche', 'Stock'],
  menu: ['Carta', 'Carta'], chats: ['Bandeja', 'Chats'],
};
const ICON = {
  delivery: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 7h10v9H3zM13 10h4l3 3v3h-7z"/><circle cx="7" cy="18" r="1.5"/><circle cx="17" cy="18" r="1.5"/></svg>',
  pickup: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 8h14l-1.5 12h-11L5 8Z"/><path d="M8 8V6a4 4 0 0 1 8 0v2"/></svg>',
  card: '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 10h18M7 15h3"/></svg>',
  chat: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M20 12a7 7 0 0 1-7 7H8l-4 2 1.2-4A7 7 0 1 1 20 12Z"/></svg>',
  camera: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 8h4l1.5-2h5L16 8h4v11H4z"/><circle cx="12" cy="13" r="3.2"/></svg>',
};
const EMPTY_CHAT_HEAD = '<div class="chat-empty-head"><span class="avatar">?</span><div><strong>Elegí una conversación</strong><small>Los mensajes del bot y del local aparecen acá.</small></div></div>';
const EMPTY_MESSAGES = '<div class="chat-empty-state"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M20 12a7 7 0 0 1-7 7H8l-4 2 1.2-4A7 7 0 1 1 20 12Z"/><path d="M9 11h6M9 14h4"/></svg><p>Elegí un chat de la izquierda para ver la conversación.</p></div>';

let token = '';
let seenOrders = null;
let currentChat = null;
let currentTab = 'orders';
let menuItems = [];
let insumos = [];
const chatMeta = new Map();
const MENU_CATEGORIES = ['Burgers', 'Combos (vie, sáb y dom)', 'Papas y extras', 'Dips', 'Bebidas', 'Otros'];
const DAY_OPTIONS = [
  ['viernes', 'Viernes'], ['sabado', 'Sábado'], ['domingo', 'Domingo'],
  ['lunes', 'Lunes'], ['miercoles', 'Miércoles'], ['jueves', 'Jueves'],
];

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function money(n) { return n == null ? '—' : '$' + Number(n).toLocaleString('es-AR'); }
function initials(value) {
  const clean = String(value || '').trim();
  if (!clean) return '?';
  return clean.split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
}
function timeShort(ts) {
  return new Date(ts * 1000).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', hour12: false });
}
function chatTime(ts) {
  const date = new Date(ts * 1000);
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', hour12: false })
    : date.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' });
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  if (res.status === 401) { logout(); throw new Error('401'); }
  if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.detail || res.status); }
  return res.json();
}

async function upload(path, form) {
  const res = await fetch(path, { method: 'POST', headers: { 'Authorization': 'Bearer ' + token }, body: form });
  if (res.status === 401) { logout(); throw new Error('401'); }
  if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.detail || res.status); }
  return res.json();
}

async function compressPhoto(file) {
  if (!file.type.startsWith('image/') || !window.createImageBitmap) return file;
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  if (scale === 1 && file.size < 1_200_000) return file;
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/webp', 0.86));
  return blob || file;
}

function dateParam() { const d = $('serviceDate').value; return d ? '?service_date=' + d : ''; }

function beep() {
  try {
    const ctx = new AudioContext(); const o = ctx.createOscillator(); const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination); o.frequency.value = 880; g.gain.value = 0.15;
    o.start(); setTimeout(() => { o.stop(); ctx.close(); }, 350);
  } catch (e) { /* audio blocked until user interacts */ }
}

function setStats(node, entries) {
  node.innerHTML = entries.map(([label, value, cls]) =>
    `<div class="stat ${cls || ''}"><span>${esc(label)}</span><b>${value}</b></div>`).join('');
}

async function loadStatus() {
  const s = await api('/admin/status');
  const state = s.open_now ? 'Abierto' : (s.order_window === 'preorder' ? 'Pre-pedidos' : 'Cerrado');
  const dot = s.open_now ? 'open' : (s.order_window === 'preorder' ? 'preorder' : 'closed');
  const extras = [];
  if (s.bot_enabled) extras.push('<span class="status-main"><span class="status-dot open"></span>Bot activo</span>');
  else extras.push('<span class="status-warn">Bot apagado</span>');
  if (s.products_without_price) extras.push(`<span class="status-warn">${s.products_without_price} productos sin precio</span>`);
  $('status').innerHTML = `<span class="status-main"><span class="status-dot ${dot}"></span>${state}</span>
    <span class="status-time">${esc(s.local_time)}${s.business_number_display ? ' · ' + esc(s.business_number_display) : ''}</span>
    ${extras.join('')}`;
}

async function loadOrders() {
  const data = await api('/admin/orders' + dateParam());
  if (!$('serviceDate').value) $('serviceDate').value = data.service_date;
  const ids = new Set(data.orders.map((o) => o.id));
  if (seenOrders && data.orders.some((o) => !seenOrders.has(o.id))) beep();
  seenOrders = ids;
  const active = data.orders.filter((o) => o.status !== 'cancelado');
  const total = active.reduce((a, o) => a + o.total, 0);
  const newCount = data.orders.filter((o) => o.status === 'nuevo').length;
  const kitchen = data.orders.filter((o) => o.status === 'en_cocina').length;
  setStats($('orderSummary'), [
    ['Pedidos', active.length, ''],
    ['Total de la noche', money(total), ''],
    ['Sin confirmar', newCount, newCount ? 'is-alert' : ''],
    ['En cocina', kitchen, kitchen ? 'is-alert' : ''],
  ]);
  $('board').innerHTML = COLUMNS.map(([key, label]) => {
    const list = data.orders.filter((o) => o.status === key)
      .sort((a, b) => (a.slot === 'ASAP' ? '00' : a.slot).localeCompare(b.slot === 'ASAP' ? '00' : b.slot));
    if (!list.length && (key === 'cancelado' || key === 'entregado')) return '';
    return `<section class="board-col" data-status="${key}">
      <header class="board-col-header"><span class="status-dot"></span><h2>${label}</h2><span class="count">${list.length}</span></header>
      <div class="board-col-body">${list.length ? list.map(orderCard).join('') : '<p class="empty">Sin pedidos.</p>'}</div>
    </section>`;
  }).join('');
}

function orderCard(o) {
  const created = timeShort(o.created);
  const slot = o.slot === 'ASAP' ? 'Lo antes posible' : o.slot;
  const items = o.items.map((i) => `<li><span class="qty">${i.qty}×</span><span>${esc(i.name)}${i.note ? ` <em class="note">(${esc(i.note)})</em>` : ''}</span></li>`).join('');
  const mode = o.mode === 'delivery'
    ? `${ICON.delivery}<span>Delivery · ${esc(o.address || 'dirección a confirmar')}</span>`
    : `${ICON.pickup}<span>Retiro en el local</span>`;
  const receipt = o.receipt_received ? '<span class="tag ok">Comprobante</span>' : '';
  const fee = o.mode === 'delivery' && o.delivery_fee == null ? '<span class="tag warn">Envío a cotizar</span>' : '';
  const newTag = o.status === 'nuevo' ? '<span class="tag warn">Nuevo</span>' : '';
  const actions = (NEXT[o.status] || []).map(([status, label], index) =>
    `<button class="btn ${index === 0 ? 'btn-primary' : 'btn-ghost'} btn-sm" data-order="${o.id}" data-status="${status}">${label}</button>`).join('');
  const feeBtn = o.mode === 'delivery' && !['entregado', 'cancelado'].includes(o.status)
    ? `<button class="btn btn-ghost btn-sm" data-fee="${o.id}" data-status="${o.status}">Envío $</button>` : '';
  const cancel = !['cancelado', 'entregado'].includes(o.status)
    ? `<button class="btn btn-ghost btn-sm" data-order="${o.id}" data-status="cancelado">Cancelar</button>` : '';
  const chat = o.has_chat === false ? '' : `<button class="btn btn-ghost btn-sm" data-chat="${o.conversation_id}">${ICON.chat} Chat</button>`;
  return `<article class="order-card ${o.status === 'nuevo' ? 'is-new' : ''}">
    <header><span class="order-number">#${o.number}</span><span class="order-total">${money(o.total)}</span></header>
    <div class="order-sub"><span>${created}</span><span>${esc(slot)}</span></div>
    <div class="order-customer"><strong>${esc(o.name || 'Sin nombre')}</strong><span>+${esc(o.phone || '')}</span></div>
    <ul class="order-items">${items}</ul>
    <div class="order-meta">${mode}</div>
    <div class="order-meta">${ICON.card}<span>${esc(o.payment || 'Pago a confirmar')}</span>${receipt}${fee}</div>
    ${newTag ? `<div class="order-tags">${newTag}</div>` : ''}
    <div class="order-actions">${actions}${feeBtn}${cancel}${chat}</div>
  </article>`;
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
$('refreshOrders').addEventListener('click', () => loadOrders());

async function loadStock() {
  const data = await api('/admin/stock' + dateParam());
  const rows = data.insumos;
  const low = rows.filter((i) => i.remaining != null && i.remaining > 0 && i.remaining < 10).length;
  const zero = rows.filter((i) => i.remaining === 0).length;
  const unlimited = rows.filter((i) => i.remaining == null).length;
  setStats($('stockSummary'), [
    ['Insumos', rows.length, ''],
    ['Bajo stock', low, low ? 'is-alert' : ''],
    ['Agotados', zero, zero ? 'is-danger' : ''],
    ['Sin control', unlimited, ''],
  ]);
  $('stockTable').innerHTML = `<div class="stock-head"><span>Insumo</span><span>Movimiento</span><span>Ajustar quedan</span><span>Por defecto</span><span></span></div>` +
    rows.map((i) => {
      const left = i.remaining == null ? 'Sin control' : i.remaining;
      const cls = i.remaining === 0 ? 'zero' : (i.remaining != null && i.remaining < 10 ? 'low' : '');
      const rowCls = i.remaining === 0 ? 'is-zero' : (i.remaining != null && i.remaining < 10 ? 'is-low' : '');
      const pct = i.initial ? Math.max(0, Math.min(100, (i.remaining / i.initial) * 100)) : 0;
      return `<article class="stock-row ${rowCls} ${i.remaining == null ? 'is-unlimited' : ''}" data-id="${esc(i.id)}">
        <div class="stock-name"><span class="stock-code">${esc(i.id.slice(0, 4).toUpperCase())}</span><div><strong>${esc(i.name)}</strong><small>${esc(i.unit)}</small></div></div>
        <div class="stock-meter"><div class="stock-numbers"><span>Inicial ${i.initial ?? '—'}</span><span>Usado ${i.used}</span><strong class="${cls}">${left}</strong></div><div class="stock-bar"><span style="--pct:${pct}%"></span></div></div>
        <div class="stock-adjust"><label>Quedan <input type="number" min="0" inputmode="numeric" class="remaining" placeholder="${i.remaining ?? ''}"></label><button class="btn btn-sm" data-act="remaining">Guardar</button></div>
        <div class="stock-default"><label>Por defecto <input type="number" min="0" inputmode="numeric" class="default" placeholder="${i.default_daily ?? 'sin control'}"></label><button class="btn btn-ghost btn-sm" data-act="default">Guardar</button></div>
        <div class="stock-actions"><button class="btn btn-ghost btn-sm" data-act="zero">Agotado</button><button class="btn btn-ghost btn-sm" data-act="unlimited">Sin control</button></div>
      </article>`;
    }).join('');
}

$('stockTable').addEventListener('click', async (e) => {
  const b = e.target.closest('button'); if (!b) return;
  const row = b.closest('.stock-row'); const id = row.dataset.id; let body;
  if (b.dataset.act === 'remaining') {
    const v = row.querySelector('.remaining').value; if (v === '') return; body = { remaining: parseInt(v, 10) };
  } else if (b.dataset.act === 'default') {
    const v = row.querySelector('.default').value; body = v === '' ? { clear_default: true } : { default_daily: parseInt(v, 10) };
  } else if (b.dataset.act === 'zero') { body = { remaining: 0 }; } else { body = { unlimited: true }; }
  try { await api(`/admin/stock/${id}${dateParam()}`, { method: 'PUT', body: JSON.stringify(body) }); loadStock(); }
  catch (err) { alert('No se pudo guardar: ' + err.message); }
});

async function loadMenu() {
  const [items, insumoRows] = await Promise.all([api('/admin/menu'), api('/admin/insumos')]);
  menuItems = items;
  insumos = insumoRows;
  const noPrice = items.filter((p) => p.price == null && !p.variants.length).length;
  const paused = items.filter((p) => !p.available).length;
  const photos = items.filter((p) => p.image_url).length;
  setStats($('menuSummary'), [
    ['Productos', items.length, ''],
    ['Sin precio', noPrice, noPrice ? 'is-alert' : ''],
    ['Pausados', paused, paused ? 'is-alert' : ''],
    ['Con foto', photos, ''],
  ]);
  $('menuTable').innerHTML = items.map((p, index) => {
    const prices = p.variants.length
      ? `<div class="price-fields">${p.variants.map((v) => `<label class="price-field"><span>${esc(v.name)}</span><input type="number" min="0" class="variant" data-vid="${esc(v.id)}" value="${v.price ?? ''}" placeholder="sin precio"></label>`).join('')}</div>`
      : `<div class="price-fields"><label class="price-field"><span>Precio</span><input type="number" min="0" class="price" value="${p.price ?? ''}" placeholder="sin precio"></label></div>`;
    const photo = p.image_url
      ? `<img src="${esc(p.image_url)}" alt="Foto de ${esc(p.name)}" loading="lazy">`
      : `<div class="photo-empty">${ICON.camera}<span>Sin foto</span></div>`;
    return `<article class="menu-card" data-id="${esc(p.id)}">
      <div class="menu-photo">${photo}<label class="photo-upload">${p.image_url ? 'Cambiar foto' : 'Subir foto'}<input type="file" class="photoFile" accept="image/jpeg,image/png,image/webp"></label>${p.image_url ? '<button type="button" class="photo-remove" data-act="removePhoto">Quitar</button>' : ''}</div>
      <div class="menu-info">
        <div class="menu-head"><span class="menu-code">${String(index + 1).padStart(2, '0')}</span><h3>${esc(p.name)}</h3><label class="switch" title="${p.available ? 'Pausar producto' : 'Activar producto'}"><input type="checkbox" class="avail" ${p.available ? 'checked' : ''}><span class="switch-track"></span><span>${p.available ? 'Disponible' : 'Pausado'}</span></label></div>
        <p class="menu-cat">${esc(p.category)}</p>
        <p class="menu-desc">${esc(p.description)}</p>
        ${p.days.length ? `<div class="menu-days">${p.days.map((d) => `<span class="tag">${esc(d)}</span>`).join('')}</div>` : ''}
        ${prices}
        <button class="btn btn-primary btn-sm menu-save" data-act="price">Guardar precios</button>
        <div class="menu-admin-actions">
          <button class="btn btn-ghost btn-sm" data-act="edit">Editar</button>
          <button class="btn btn-ghost btn-sm btn-danger" data-act="delete">Borrar</button>
        </div>
        <div class="recipe">Receta: ${Object.entries(p.recipe).map(([k, v]) => esc(k) + ' ×' + v).join(', ') || 'sin receta'}</div>
      </div>
    </article>`;
  }).join('');
}

function categoryOptions(selected) {
  const categories = [...new Set([...MENU_CATEGORIES, ...menuItems.map((p) => p.category).filter(Boolean)])];
  return categories.map((category) => `<option value="${esc(category)}"${category === selected ? ' selected' : ''}>${esc(category)}</option>`).join('');
}

function drawDays(selected) {
  $('productDays').innerHTML = DAY_OPTIONS.map(([value, label]) =>
    `<label><input type="checkbox" class="productDay" value="${value}"${selected.includes(value) ? ' checked' : ''}>${label}</label>`).join('');
}

function drawRecipe(recipe) {
  $('productRecipe').innerHTML = insumos.map((insumo) =>
    `<label class="recipe-item"><span>${esc(insumo.name)}<small>${esc(insumo.unit)}</small></span><input type="number" min="0" max="50" class="recipe-qty" data-id="${esc(insumo.id)}" value="${recipe[insumo.id] || ''}" placeholder="0"></label>`).join('');
}

function updateProductCategoryFields() {
  const isCombo = $('productCategory').value.startsWith('Combos');
  if (isCombo && !$('productId').value && !document.querySelector('.productDay:checked')) {
    drawDays(['viernes', 'sabado', 'domingo']);
  }
  $('productDaysBox').hidden = !isCombo;
}

function openProductDialog(product = null) {
  $('productForm').reset();
  $('productFormError').textContent = '';
  $('productId').value = product ? product.id : '';
  $('productDialogTitle').textContent = product ? 'Editar producto' : 'Nuevo producto';
  $('productCategory').innerHTML = categoryOptions(product ? product.category : 'Bebidas');
  $('productName').value = product ? product.name : '';
  $('productDescription').value = product ? product.description : '';
  $('productPrice').value = product && product.price != null ? product.price : '';
  $('productAvailable').checked = product ? product.available : true;
  $('productAliases').value = product ? (product.aliases || []).join(', ') : '';
  const hasVariants = Boolean(product && product.variants && product.variants.length);
  $('productPriceField').hidden = hasVariants;
  $('productVariantNote').hidden = !hasVariants;
  $('productDelete').hidden = !product;
  $('productSave').textContent = product ? 'Guardar cambios' : 'Crear producto';
  drawDays(product ? product.days : []);
  drawRecipe(product ? product.recipe : {});
  updateProductCategoryFields();
  $('productDialog').showModal();
  $('productName').focus();
}

function closeProductDialog() {
  $('productDialog').close();
}

async function deleteProduct(productId) {
  const product = menuItems.find((p) => p.id === productId);
  const name = product ? product.name : 'este producto';
  if (!confirm(`¿Borrar "${name}"? Los pedidos ya hechos se conservan.`)) return;
  try {
    await api(`/admin/menu/${productId}`, { method: 'DELETE' });
    if ($('productDialog').open) closeProductDialog();
    await loadMenu();
    await loadStatus();
  } catch (err) {
    alert('No se pudo borrar: ' + err.message);
  }
}

$('addProduct').addEventListener('click', () => openProductDialog());
$('productCategory').addEventListener('change', updateProductCategoryFields);
$('productDelete').addEventListener('click', () => {
  const productId = $('productId').value;
  if (productId) deleteProduct(productId);
});
document.querySelectorAll('[data-close-product]').forEach((button) =>
  button.addEventListener('click', closeProductDialog));
$('productDialog').addEventListener('click', (event) => {
  if (event.target === $('productDialog')) closeProductDialog();
});

$('productForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const productId = $('productId').value;
  const name = $('productName').value.trim();
  const category = $('productCategory').value;
  if (!name) {
    $('productFormError').textContent = 'Poné un nombre para el producto.';
    return;
  }
  const body = {
    name, category,
    description: $('productDescription').value.trim(),
    available: $('productAvailable').checked,
    days: category.startsWith('Combos')
      ? [...document.querySelectorAll('.productDay:checked')].map((input) => input.value) : [],
    aliases: $('productAliases').value.split(',').map((value) => value.trim()).filter(Boolean),
    recipe: Object.fromEntries([...document.querySelectorAll('.recipe-qty')]
      .map((input) => [input.dataset.id, Number(input.value || 0)])
      .filter(([, value]) => value > 0)),
  };
  if (!$('productPriceField').hidden) {
    const rawPrice = $('productPrice').value;
    if (rawPrice === '') {
      if (productId) body.clear_price = true;
    } else {
      body.price = Number(rawPrice);
    }
  }
  const save = $('productSave');
  save.disabled = true;
  $('productFormError').textContent = '';
  try {
    await api(productId ? `/admin/menu/${productId}` : '/admin/menu', {
      method: productId ? 'PATCH' : 'POST',
      body: JSON.stringify(body),
    });
    closeProductDialog();
    await loadMenu();
    await loadStatus();
  } catch (err) {
    $('productFormError').textContent = 'No se pudo guardar: ' + err.message;
  } finally {
    save.disabled = false;
  }
});

$('menuTable').addEventListener('click', async (e) => {
  const b = e.target.closest('button'); if (!b) return;
  const card = b.closest('.menu-card');
  if (b.dataset.act === 'removePhoto') {
    if (!confirm('¿Quitar la foto de este producto?')) return;
    try { await api(`/admin/menu/${card.dataset.id}/image`, { method: 'DELETE' }); loadMenu(); }
    catch (err) { alert('No se pudo quitar: ' + err.message); }
    return;
  }
  if (b.dataset.act === 'edit') {
    openProductDialog(menuItems.find((product) => product.id === card.dataset.id));
    return;
  }
  if (b.dataset.act === 'delete') {
    await deleteProduct(card.dataset.id);
    return;
  }
  if (b.dataset.act !== 'price') return;
  let body;
  const variants = [...card.querySelectorAll('.variant')];
  if (variants.length) {
    const values = {};
    for (const input of variants) {
      if (input.value !== '') values[input.dataset.vid] = parseInt(input.value, 10);
    }
    body = { variants: values };
  } else {
    const v = card.querySelector('.price').value;
    body = v === '' ? { clear_price: true } : { price: parseInt(v, 10) };
  }
  b.disabled = true;
  try { await api(`/admin/menu/${card.dataset.id}`, { method: 'PATCH', body: JSON.stringify(body) }); loadMenu(); loadStatus(); }
  catch (err) { alert('No se pudo guardar: ' + err.message); b.disabled = false; }
});

$('menuTable').addEventListener('change', async (e) => {
  const card = e.target.closest('.menu-card'); if (!card) return;
  if (e.target.classList.contains('avail')) {
    e.target.disabled = true;
    try {
      await api(`/admin/menu/${card.dataset.id}`, { method: 'PATCH', body: JSON.stringify({ available: e.target.checked }) });
      const label = card.querySelector('.switch span:last-child');
      if (label) label.textContent = e.target.checked ? 'Disponible' : 'Pausado';
    } catch (err) { alert('No se pudo guardar: ' + err.message); e.target.checked = !e.target.checked; }
    finally { e.target.disabled = false; }
    return;
  }
  if (e.target.classList.contains('photoFile')) {
    const file = e.target.files[0]; if (!file) return;
    card.classList.add('is-uploading');
    try {
      const body = await compressPhoto(file);
      const form = new FormData();
      form.append('file', body, body.name || 'foto.webp');
      await upload(`/admin/menu/${card.dataset.id}/image`, form);
      loadMenu();
    } catch (err) { alert('No se pudo subir la foto: ' + err.message); }
    finally { card.classList.remove('is-uploading'); e.target.value = ''; }
  }
});

async function loadChats() {
  const list = await api('/admin/conversations');
  chatMeta.clear();
  list.forEach((c) => chatMeta.set(c.id, c));
  const pending = list.filter((c) => c.needs_human).length;
  $('chatBadge').hidden = !pending; $('chatBadge').textContent = pending;
  if (currentTab !== 'chats') return;
  $('chatList').innerHTML = list.length ? list.map((c) => `<li data-id="${esc(c.id)}" class="${c.id === currentChat ? 'active' : ''}">
    <span class="avatar">${esc(initials(c.name || c.wa_id))}</span>
    <div class="chat-list-body"><div class="chat-list-top"><strong>${esc(c.name || '+' + c.wa_id)}</strong><time>${chatTime(c.updated)}</time></div><small>+${esc(c.wa_id)}</small><div class="chat-tags">${c.needs_human ? '<span class="tag human">Pide persona</span>' : ''}${c.bot_paused ? '<span class="tag">Bot en pausa</span>' : ''}</div></div>
  </li>`).join('') : '<li class="empty">Sin conversaciones todavía.</li>';
}

async function openChat(id) {
  currentChat = id; $('replyForm').hidden = false;
  await loadChats();
  const meta = chatMeta.get(id) || {};
  const phone = meta.wa_id || id.split(':').pop();
  const name = meta.name || '+' + phone;
  $('chatHead').innerHTML = `<div class="chat-peer"><span class="avatar">${esc(initials(name))}</span><div><strong>${esc(name)}</strong><small>+${esc(phone)}</small></div></div>
    <div class="chat-state">${meta.needs_human ? '<span class="tag human">Pide persona</span>' : ''}${meta.bot_paused ? '<span class="tag">Bot en pausa</span>' : ''}<button type="button" class="btn btn-ghost btn-sm chat-delete" data-delete-chat="${esc(id)}">Borrar chat</button></div>`;
  const box = $('messages');
  try {
    const msgs = await api(`/admin/conversations/${encodeURIComponent(id)}/messages`);
    const labels = { in: 'Cliente', bot: 'Bot', human: 'Local' };
    box.innerHTML = msgs.length ? msgs.map((m) => `<div class="msg ${m.direction}"><div class="bubble">${esc(m.text)}</div><small>${labels[m.direction] || 'Mensaje'} · ${new Date(m.created * 1000).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', hour12: false })}</small></div>`).join('')
      : '<div class="chat-empty-state"><p>Esta conversación todavía no tiene mensajes.</p></div>';
  } catch (err) {
    box.innerHTML = '<div class="chat-empty-state"><p>No pudimos abrir este chat. Puede haber sido borrado.</p></div>';
  }
  box.scrollTop = box.scrollHeight;
  loadChats();
}

function resetChat() {
  currentChat = null; $('replyForm').hidden = true;
  $('chatHead').innerHTML = EMPTY_CHAT_HEAD;
  $('messages').innerHTML = EMPTY_MESSAGES;
}

$('chatHead').addEventListener('click', async (e) => {
  const button = e.target.closest('[data-delete-chat]'); if (!button) return;
  const id = button.dataset.deleteChat;
  if (!confirm('¿Borrar este chat del panel? Se borra el historial local y los mensajes pendientes. No se borra de WhatsApp ni de Pedidos.')) return;
  button.disabled = true;
  try {
    await api(`/admin/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' });
    resetChat();
    await loadChats();
  } catch (err) {
    alert('No se pudo borrar: ' + err.message);
    button.disabled = false;
  }
});
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
    openChat(currentChat);
  });
}

function switchTab(tab) {
  currentTab = tab;
  const [eyebrow, title] = TAB_META[tab] || TAB_META.orders;
  $('tabEyebrow').textContent = eyebrow; $('tabTitle').textContent = title;
  document.querySelectorAll('.mainnav button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.tab').forEach((t) => { t.hidden = t.id !== 'tab-' + tab; });
  refresh();
}
document.querySelectorAll('.mainnav button').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
$('serviceDate').addEventListener('change', () => { seenOrders = null; refresh(); });
$('logout').addEventListener('click', () => logout());

async function refresh() {
  try {
    await loadStatus();
    if (currentTab === 'orders') await loadOrders();
    if (currentTab === 'stock') await loadStock();
    if (currentTab === 'menu') await loadMenu();
    await loadChats();
  } catch (e) { /* shown on next refresh */ }
}

function logout() {
  try { sessionStorage.removeItem('club_token'); } catch (e) { /* ignore */ }
  token = ''; currentChat = null; $('app').hidden = true; $('login').hidden = false; $('chatBadge').hidden = true;
  $('loginError').textContent = '';
}
async function start() {
  const check = await fetch('/admin/status', { headers: { Authorization: 'Bearer ' + token } });
  if (!check.ok) throw new Error('401');
  $('login').hidden = true; $('app').hidden = false;
  await refresh();
  setInterval(() => { if (currentTab === 'orders' || currentTab === 'chats') refresh(); }, 10000);
}
$('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const button = e.submitter || e.target.querySelector('button');
  token = $('token').value.trim();
  button.disabled = true; button.textContent = 'Entrando...';
  try {
    await start();
    button.disabled = false; button.textContent = 'Entrar al panel';
    try { sessionStorage.setItem('club_token', token); } catch (err) { /* private mode */ }
  } catch (err) {
    $('loginError').textContent = 'Clave incorrecta. Probá de nuevo.';
    button.disabled = false; button.textContent = 'Entrar al panel';
  }
});
try { token = sessionStorage.getItem('club_token') || ''; } catch (e) { token = ''; }
if (token) start().catch(() => logout());
