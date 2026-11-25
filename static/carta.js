'use strict';

const WA = '5492643179759';
const money = (value) => '$' + new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(value || 0);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const wa = (text) => `https://wa.me/${WA}?text=${encodeURIComponent(text)}`;

const state = { products: [], byId: new Map(), selected: new Map(), cart: [], customizing: null };
const CART_KEY = 'club-cart-v1';
const COMBO_RULES = {
  combo1: { burgers: 2, size: 'Doble', dips: 1 },
  combo2: { burgers: 1, size: 'Triple', dips: 1 },
  combo3: { burgers: 4, size: 'Doble', dips: 2 },
};
const DAY_LABELS = {
  lunes: 'lunes', martes: 'martes', miercoles: 'miércoles', jueves: 'jueves',
  viernes: 'viernes', sabado: 'sábado', domingo: 'domingo',
};

function readCart() {
  try {
    const raw = JSON.parse(localStorage.getItem(CART_KEY) || '[]');
    return Array.isArray(raw) ? raw.filter((line) => line && line.id && line.qty > 0) : [];
  } catch (error) {
    return [];
  }
}

function saveCart() {
  try { localStorage.setItem(CART_KEY, JSON.stringify(state.cart)); } catch (error) { /* private mode */ }
}

state.cart = readCart();

const arrow = '<svg aria-hidden="true" viewBox="0 0 16 16"><path d="M2 8h10M8 3l5 5-5 5"/></svg>';

function ingredientFlags(text) {
  const t = String(text || '').toLowerCase();
  return {
    chicken: /pollo/.test(t),
    cheese: /cheddar|tybo|queso/.test(t),
    bacon: /panceta/.test(t),
    tomato: /tomate/.test(t),
    lettuce: /lechuga/.test(t),
    onion: /cebolla|aros de cebolla/.test(t),
    egg: /huevo/.test(t),
    guac: /guacamole/.test(t),
    honey: /miel/.test(t),
    spicy: /picante/.test(t),
  };
}

function pattyCount(text) {
  const m = String(text || '').match(/(\d+)\s*medallones/);
  return m ? Math.min(3, Math.max(1, Number(m[1]))) : 1;
}

function dishGlyph(product, index) {
  const flags = ingredientFlags(product.description);
  const tones = ['#ead8bc', '#f0d6a8', '#dce2d0', '#e8d9d3'];
  const tone = tones[index % tones.length];
  const layers = [];
  layers.push({ kind: 'bottom' });
  const patties = pattyCount(product.description);
  for (let i = 0; i < patties; i += 1) layers.push({ kind: 'patty' });
  if (flags.cheese) layers.push({ kind: 'cheese' });
  if (flags.bacon) layers.push({ kind: 'bacon' });
  if (flags.egg) layers.push({ kind: 'egg' });
  if (flags.tomato) layers.push({ kind: 'tomato' });
  if (flags.lettuce) layers.push({ kind: 'lettuce' });
  if (flags.onion) layers.push({ kind: 'onion' });
  if (flags.honey) layers.push({ kind: 'honey' });
  if (flags.guac) layers.push({ kind: 'guac' });
  layers.push({ kind: 'top' });

  const heights = { bottom: 20, patty: 23, cheese: 11, bacon: 11, egg: 17, tomato: 12, lettuce: 12, onion: 11, honey: 8, guac: 10, top: 62 };
  let y = 238;
  const shapes = layers.map((layer) => {
    y -= heights[layer.kind];
    return shape(layer.kind, y);
  }).join('');

  return `<svg viewBox="0 0 360 280" role="img" aria-label="${esc(product.name)}" xmlns="http://www.w3.org/2000/svg">
    <rect width="360" height="280" fill="${tone}"/>
    <circle cx="180" cy="146" r="116" fill="none" stroke="#181310" stroke-opacity=".14"/>
    <circle cx="180" cy="146" r="92" fill="none" stroke="#181310" stroke-opacity=".1"/>
    <ellipse cx="180" cy="252" rx="128" ry="13" fill="#181310" opacity=".12"/>
    ${shapes}
    <path d="M52 36h48M52 46h30" stroke="#181310" stroke-opacity=".25" stroke-width="2"/>
    <path d="M260 246h48M278 256h30" stroke="#181310" stroke-opacity=".25" stroke-width="2"/>
  </svg>`;
}

function shape(kind, y) {
  const bun = '#d99b4a';
  const bunLight = '#f0b23f';
  const bunDark = '#b87430';
  const patty = '#5c3322';
  const pattyLine = '#3e2318';
  const cheese = '#f0b23f';
  switch (kind) {
    case 'bottom':
      return `<rect x="76" y="${y}" width="208" height="20" rx="9" fill="${bunDark}"/><rect x="84" y="${y + 3}" width="192" height="8" rx="4" fill="${bun}"/>`;
    case 'patty':
      return `<rect x="66" y="${y}" width="228" height="23" rx="8" fill="${patty}"/><path d="M78 ${y + 7}h204M78 ${y + 13}h204M78 ${y + 19}h204" stroke="${pattyLine}" stroke-width="1.5" stroke-linecap="round" opacity=".65"/>`;
    case 'cheese':
      return `<path d="M68 ${y + 2}h224v6c-14 0-15 10-29 10s-14-10-28-10-14 10-28 10-14-10-28-10-14 10-28 10-14-10-28-10-14 10-28 10-12-10-27-10z" fill="${cheese}"/><path d="M68 ${y + 2}h224" stroke="#c1841d" stroke-width="1.5" opacity=".6"/>`;
    case 'bacon':
      return `<path d="M72 ${y + 4}c28-9 40 9 68 0s40 9 68 0 40 9 68 0" fill="none" stroke="#a84a32" stroke-width="8" stroke-linecap="round"/><path d="M72 ${y + 4}c28-9 40 9 68 0s40 9 68 0 40 9 68 0" fill="none" stroke="#c76a4a" stroke-width="2" stroke-linecap="round"/>`;
    case 'egg':
      return `<ellipse cx="180" cy="${y + 9}" rx="83" ry="15" fill="#fff9ed"/><circle cx="180" cy="${y + 9}" r="8" fill="${bunLight}"/>`;
    case 'tomato':
      return `<rect x="82" y="${y}" width="196" height="12" rx="6" fill="#c73b27"/><path d="M106 ${y + 5}h148" stroke="#e46a4f" stroke-width="2" stroke-linecap="round" opacity=".7"/>`;
    case 'lettuce':
      return `<path d="M64 ${y + 7}c18-10 32 8 50-1s32 8 50-1 32 8 50-1 32 8 50-1 24 7 32 2v7c-18 8-34-5-52 2s-34-5-52 2-34-5-52 2-34-5-52 2-24-5-24-5z" fill="#697548"/>`;
    case 'onion':
      return `<rect x="76" y="${y}" width="208" height="11" rx="5" fill="#d9a15f"/><path d="M90 ${y + 5}h56M158 ${y + 5}h44M214 ${y + 5}h54" stroke="#f0c68c" stroke-width="2" stroke-linecap="round"/>`;
    case 'honey':
      return `<path d="M82 ${y + 4}h196" stroke="#d9901f" stroke-width="5" stroke-linecap="round" opacity=".85"/>`;
    case 'guac':
      return `<path d="M72 ${y + 7}c24-8 38 7 62 0s38 7 62 0 38 7 62 0 30 5 30 5v5c-20 6-34-4-54 2s-34-4-54 2-34-4-54 2-34-4-54 2-28-4-28-4z" fill="#7c8c4b"/>`;
    case 'top':
      return `<path d="M66 ${y + 62}Q66 ${y + 2} 180 ${y}q114 2 114 62z" fill="${bunLight}"/><path d="M78 ${y + 54}q10-38 102-40" fill="none" stroke="#f7cc74" stroke-width="3" opacity=".55"/><g fill="#fff4d7"><ellipse cx="130" cy="${y + 22}" rx="4" ry="2"/><ellipse cx="180" cy="${y + 14}" rx="4" ry="2"/><ellipse cx="228" cy="${y + 24}" rx="4" ry="2"/><ellipse cx="106" cy="${y + 42}" rx="4" ry="2"/><ellipse cx="252" cy="${y + 42}" rx="4" ry="2"/><ellipse cx="158" cy="${y + 38}" rx="4" ry="2"/><ellipse cx="204" cy="${y + 36}" rx="4" ry="2"/></g>`;
    default:
      return '';
  }
}

function dishCard(product, index) {
  const variants = (product.variants || []).filter((v) => v.price != null);
  const defaultVariant = variants[0] || null;
  state.selected.set(product.id, defaultVariant ? defaultVariant.id : null);
  const price = defaultVariant ? defaultVariant.price : product.price;
  const titleName = `${product.name}${defaultVariant ? ` ${defaultVariant.name}` : ''}`;
  const image = product.image_url
    ? `<img src="${esc(product.image_url)}" alt="${esc(product.name)}" loading="lazy" decoding="async">`
    : dishGlyph(product, index);
  const sizeButtons = variants.length > 1
    ? `<div class="size-row" role="group" aria-label="Tamaño de ${esc(product.name)}">${variants.map((v, i) => `<button type="button" class="size-button" data-variant="${esc(v.id)}" aria-pressed="${i === 0}">${esc(v.name)}</button>`).join('')}</div>`
    : '';
  const actions = product.available
    ? `<div class="dish-actions"><button type="button" class="dish-add" data-add="${esc(product.id)}">Agregar al carrito</button><button type="button" class="dish-customize" data-customize="${esc(product.id)}">Personalizar</button></div>`
    : `<div class="dish-actions"><button type="button" class="dish-add" disabled>Agotado</button></div>`;
  return `<article class="dish${product.available ? '' : ' is-sold-out'}" data-id="${esc(product.id)}">
    <div class="dish-media">
      ${image}
      <span class="dish-index" aria-hidden="true">${String(product.code).padStart(2, '0')}</span>
      ${product.image_url ? '' : '<span class="photo-state">foto pendiente</span>'}
    </div>
    <div class="dish-body">
      <div class="dish-title-row">
        <h3>${esc(titleName)}${product.available ? '' : '<span class="sold-out">agotado</span>'}</h3>
        <span class="dish-price" data-price>${money(price)}</span>
      </div>
      <p class="dish-desc">${esc(product.description)}</p>
      ${sizeButtons}
      ${actions}
    </div>
  </article>`;
}

function comboCard(product, index) {
  return `<article class="combo">
    <div class="combo-head"><span>combo ${String(index + 1).padStart(2, '0')}</span><span>${esc((product.days || []).join(' / ') || 'fin de semana')}</span></div>
    <h3>${esc(product.name)}</h3>
    <p>${esc(product.description)}</p>
    <div class="combo-price">${money(product.price)}</div>
    <button type="button" class="combo-link" data-add="${esc(product.id)}">Agregar al carrito ${arrow}</button>
  </article>`;
}

function priceRow(product) {
  return `<button type="button" class="price-row" data-add="${esc(product.id)}">
    <span class="name">${esc(product.name)}<span class="desc">${esc(product.description)}</span></span>
    <span class="dots" aria-hidden="true"></span>
    <span class="price">${money(product.price)}</span>
  </button>`;
}

function noteCard() {
  return `<article class="dish dish-note">
    <div>
      <div class="note-mark" aria-hidden="true">*</div>
      <h3>La letra chica</h3>
      <p>Delivery propio hasta 10 km. Retiro en el salón de Av. Libertador 1525. Los pedidos anticipados se abonan antes.</p>
    </div>
    <ul class="note-facts">
      <li><span>Efectivo o transferencia</span><b>Mathias1998</b></li>
      <li><span>Primer retiro</span><b>20:00</b></li>
      <li><span>Primer delivery</span><b>20:30</b></li>
      <li><span>Delivery propio</span><b>10 km</b></li>
      <li><span>Último pedido</span><b>00:50</b></li>
      <li><span>Anticipados desde</span><b>10:00</b></li>
    </ul>
    <a class="dish-cta" href="${wa('Hola! Quiero hacer un pedido.')}">Pedir por WhatsApp ${arrow}</a>
  </article>`;
}

function bindSizes() {
  const grid = document.querySelector('#burgerGrid');
  grid.addEventListener('click', (event) => {
    const button = event.target.closest('.size-button');
    if (!button) return;
    const card = button.closest('.dish');
    const product = state.byId.get(card.dataset.id);
    if (!product) return;
    const variant = (product.variants || []).find((v) => v.id === button.dataset.variant);
    if (!variant) return;
    card.querySelectorAll('.size-button').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
    const priceNode = card.querySelector('[data-price]');
    if (priceNode) priceNode.textContent = money(variant.price);
    const title = card.querySelector('h3');
    if (title) title.firstChild.textContent = `${product.name} ${variant.name}`;
    state.selected.set(product.id, variant.id);
  });
}

function removalOptions(product) {
  const text = String(product.description || '').toLowerCase();
  const options = [
    ['tomate', 'sin tomate'],
    ['lechuga', 'sin lechuga'],
    ['cebolla', 'sin cebolla'],
    ['jamon', 'sin jamón'],
    ['panceta', 'sin panceta'],
    ['huevo', 'sin huevo'],
    ['picante', 'sin picante'],
    ['guacamole', 'sin guacamole'],
    ['cheddar', 'sin cheddar'],
    ['tybo', 'sin tybo'],
    ['queso azul', 'sin queso azul'],
  ];
  const seen = new Set();
  return options.filter(([needle, label]) => {
    if (seen.has(label) || !text.includes(needle)) return false;
    seen.add(label);
    return true;
  }).map(([, label]) => label);
}

function serviceWeekday() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Argentina/San_Juan', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date());
  const part = (type) => Number(parts.find((item) => item.type === type).value);
  const local = new Date(Date.UTC(part('year'), part('month') - 1, part('day'), part('hour')));
  if (part('hour') < 1) local.setUTCDate(local.getUTCDate() - 1);
  return ['domingo', 'lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado'][local.getUTCDay()];
}

function comboAvailableToday(combo) {
  const days = combo.days || [];
  return !days.length || days.includes(serviceWeekday());
}

function comboUnits(rule) {
  const units = [];
  state.cart.forEach((line) => {
    if (line.kind !== 'burger' || line.id === 'crispy') return;
    if (String(line.variantName || '').trim().toLowerCase() !== rule.size.toLowerCase()) return;
    const price = Number(line.basePrice ?? line.unit ?? 0);
    const choices = line.removals && line.removals.length ? `, ${line.removals.join(' y ')}` : '';
    const label = `${line.name}${line.variantName ? ` ${line.variantName}` : ''}${choices}`;
    for (let i = 0; i < line.qty; i += 1) units.push({ key: line.key, price, label });
  });
  if (units.length < rule.burgers) return null;
  return units.sort((a, b) => b.price - a.price).slice(0, rule.burgers);
}

function comboOptions() {
  const dips = state.products.filter((product) => product.kind === 'dip' && product.available && product.price != null);
  const candidates = [];
  state.products
    .filter((product) => product.kind === 'combo' && product.available && product.price != null)
    .forEach((combo) => {
      const rule = COMBO_RULES[combo.id];
      if (!rule) return;
      if (dips.length < rule.dips) return;
      const units = comboUnits(rule);
      if (!units) return;
      const baseTotal = units.reduce((sum, unit) => sum + unit.price, 0);
      const savings = baseTotal - combo.price;
      if (savings <= 0) return;
      candidates.push({ combo, rule, units, baseTotal, savings, availableToday: comboAvailableToday(combo) });
    });
  return candidates.sort((a, b) => b.savings - a.savings);
}

function comboSuggestionHTML() {
  const candidates = comboOptions();
  if (!candidates.length) return '';
  const { combo, rule, baseTotal, savings, availableToday } = candidates[0];
  const dips = state.products.filter((product) => product.kind === 'dip' && product.available && product.price != null);
  const selects = Array.from({ length: rule.dips }, (_, index) => `<label>Dip ${index + 1}<select data-combo-dip="${index}">${dips.map((dip, dipIndex) => `<option value="${esc(dip.name)}"${dipIndex === index ? ' selected' : ''}>${esc(dip.name)}</option>`).join('')}</select></label>`).join('');
  const dayLabels = (combo.days || []).map((day) => DAY_LABELS[day] || day);
  const days = dayLabels.length > 1
    ? `${dayLabels.slice(0, -1).join(', ')} y ${dayLabels[dayLabels.length - 1]}`
    : (dayLabels[0] || '');
  const availability = availableToday
    ? '<div class="combo-availability-row"><span class="combo-availability">Disponible hoy</span></div>'
    : `<div class="combo-availability-row"><span class="combo-availability">Válido ${esc(days)}</span><span>Pedilo para uno de esos días.</span></div>`;
  return `<div class="combo-suggest">
    <div class="combo-suggest-top"><span>Combo sugerido</span><b>Ahorrás ${money(savings)}</b></div>
    <h3>${esc(combo.name)}</h3>
    <p>${esc(combo.description)}</p>
    <div class="combo-suggest-price"><strong>${money(combo.price)}</strong><span>vs. ${money(baseTotal)} en burgers</span></div>
    ${availability}
    <div class="combo-dips">${selects}</div>
    <button type="button" class="combo-suggest-button" data-combo-suggest="${esc(combo.id)}">Cambiar por este combo</button>
  </div>`;
}

function applyComboSuggestion(comboId) {
  const candidate = comboOptions().find((option) => option.combo.id === comboId);
  if (!candidate) return;
  const fallbackDips = state.products
    .filter((product) => product.kind === 'dip' && product.available && product.price != null)
    .map((dip) => dip.name);
  const dipSelects = [...document.querySelectorAll('[data-combo-dip]')];
  const dips = Array.from({ length: candidate.rule.dips }, (_, index) =>
    (dipSelects[index] && dipSelects[index].value) || fallbackDips[index]).filter(Boolean);
  const chosen = new Map();
  candidate.units.forEach((unit) => chosen.set(unit.key, (chosen.get(unit.key) || 0) + 1));
  state.cart = state.cart.flatMap((line) => {
    const take = chosen.get(line.key) || 0;
    if (!take) return [line];
    const qty = line.qty - take;
    return qty > 0 ? [{ ...line, qty, total: line.unit * qty }] : [];
  });
  const grouped = new Map();
  candidate.units.forEach((unit) => grouped.set(unit.label, (grouped.get(unit.label) || 0) + 1));
  const burgerChoices = [...grouped].map(([label, qty]) => `${qty} x ${label}`).join(', ');
  addLine({
    key: `combo|${candidate.combo.id}|${Date.now()}|${Math.random().toString(36).slice(2, 8)}`,
    kind: 'combo', id: candidate.combo.id, name: candidate.combo.name,
    variantId: null, variantName: '', basePrice: candidate.combo.price, unit: candidate.combo.price,
    qty: 1, extras: [], removals: [], total: candidate.combo.price,
    comboChoices: `${burgerChoices}. Dips: ${dips.join(', ')}`,
  });
  toast(`${candidate.combo.name} agregado`);
}

function cartCount() {
  return state.cart.reduce((sum, line) => sum + line.qty, 0);
}

function cartTotal() {
  return state.cart.reduce((sum, line) => sum + line.total, 0);
}

function cartMessage() {
  const lines = [];
  const extras = new Map();
  state.cart.forEach((line) => {
    if (line.kind === 'burger') {
      let text = `${line.qty} x ${line.name}${line.variantName ? ` ${line.variantName}` : ''}`;
      if (line.removals && line.removals.length) text += `, ${line.removals.join(' y ')}`;
      lines.push(`${text}.`);
      (line.extras || []).forEach((extra) => {
        const current = extras.get(extra.id) || { name: extra.name, qty: 0 };
        current.qty += line.qty * (extra.count || 1);
        extras.set(extra.id, current);
      });
    } else if (line.kind === 'combo') {
      lines.push(`${line.qty} x ${line.name}.`);
      if (line.comboChoices) {
        lines.push(`Elecciones del ${line.name}: ${line.comboChoices}.`);
      }
    } else {
      lines.push(`${line.qty} x ${line.name}.`);
    }
  });
  extras.forEach((extra) => lines.push(`${extra.qty} x ${extra.name}.`));
  return `Hola! Quiero hacer este pedido:\n${lines.join('\n')}`;
}

function addLine(line) {
  const existing = state.cart.find((item) => item.key === line.key);
  if (existing) {
    existing.qty = Math.min(20, existing.qty + line.qty);
    existing.total = existing.unit * existing.qty;
  } else {
    state.cart.push(line);
  }
  saveCart();
  updateCartUI();
}

function addSimple(product) {
  if (!product || !product.available) return;
  const extras = [];
  if (product.kind === 'burger') {
    const variantId = state.selected.get(product.id) || (product.variants[0] && product.variants[0].id) || null;
    const variant = (product.variants || []).find((v) => v.id === variantId);
    const base = variant ? variant.price : product.price;
    const name = `${product.name}${variant ? ` ${variant.name}` : ''}`;
    addLine({
      key: `burger|${product.id}|${variantId || ''}`,
      kind: 'burger', id: product.id, name: product.name,
      variantId: variantId || null, variantName: variant ? variant.name : '',
      basePrice: base, unit: base, qty: 1, extras, removals: [], total: base,
    });
    toast(`${name} agregado`);
  } else {
    addLine({
      key: `${product.kind}|${product.id}`,
      kind: product.kind, id: product.id, name: product.name,
      variantId: null, variantName: '', basePrice: product.price, unit: product.price,
      qty: 1, extras, removals: [], total: product.price,
    });
    toast(`${product.name} agregado`);
  }
}

function cartLineHTML(line) {
  const extras = (line.extras || []).map((extra) => `+ ${esc(extra.name)} ${money(extra.price)}`).join(', ');
  const removals = (line.removals || []).map((item) => esc(item)).join(', ');
  return `<div class="cart-line" data-key="${esc(line.key)}">
    <div class="cart-line-top">
      <div>
        <strong>${line.qty} x ${esc(line.name)}${line.variantName ? ` ${esc(line.variantName)}` : ''}</strong>
        ${extras ? `<span class="cart-sub">${extras}</span>` : ''}
        ${removals ? `<span class="cart-sub">${removals}</span>` : ''}
        ${line.comboChoices ? `<span class="cart-sub combo-choices">${esc(line.comboChoices)}</span>` : ''}
      </div>
      <b>${money(line.total)}</b>
    </div>
    <div class="cart-line-actions">
      ${line.kind === 'combo' && line.comboChoices
        ? '<span class="cart-fixed-qty">Combo configurado</span>'
        : `<div class="qty-mini">
        <button type="button" data-cart-minus aria-label="Restar uno">−</button>
        <span>${line.qty}</span>
        <button type="button" data-cart-plus aria-label="Sumar uno">+</button>
      </div>`}
      <button type="button" class="cart-remove" data-cart-remove>Quitar</button>
    </div>
  </div>`;
}

function upsellHTML() {
  if (!state.cart.length) return '';
  const inCart = new Set();
  state.cart.forEach((line) => {
    inCart.add(line.id);
    (line.extras || []).forEach((extra) => inCart.add(extra.id));
  });
  const hasKind = (kind) => state.cart.some((line) => line.kind === kind ||
    (line.extras || []).some((extra) => (state.byId.get(extra.id) || {}).kind === kind));
  const hasBurger = hasKind('burger') || hasKind('combo');
  if (!hasBurger) return '';
  const groups = [];
  if (!hasKind('drink')) {
    const drinks = state.products.filter((p) => p.kind === 'drink' && p.available && p.price != null && !inCart.has(p.id));
    if (drinks.length) groups.push(['Bebidas', drinks]);
  }
  if (!hasKind('dip') && !hasKind('combo')) {
    const dips = state.products.filter((p) => p.kind === 'dip' && p.available && p.price != null && !inCart.has(p.id));
    if (dips.length) groups.push(['Dips', dips]);
  }
  const extras = state.products.filter((p) => p.kind === 'extra' && p.available && p.price != null && !inCart.has(p.id));
  if (extras.length) groups.push(['Extras', extras]);
  if (!groups.length) return '';
  return `<div class="cart-upsell">
    <div class="cart-upsell-head"><span>Sumá algo más</span><small>Para completar el pedido.</small></div>
    ${groups.map(([label, products]) => `<div class="upsell-group"><h3>${label}</h3>${products.slice(0, 4).map((p) => `<button type="button" class="upsell-item" data-add="${esc(p.id)}"><span>${esc(p.name)}</span><b>+ ${money(p.price)}</b></button>`).join('')}</div>`).join('')}
  </div>`;
}

function updateCartUI() {
  const count = cartCount();
  document.querySelectorAll('[data-cart-count]').forEach((node) => { node.textContent = count; });
  const totalNode = document.querySelector('[data-cart-total]');
  if (totalNode) totalNode.textContent = money(cartTotal());
  const body = document.querySelector('#cartBody');
  if (body) {
    body.innerHTML = state.cart.length
      ? state.cart.map(cartLineHTML).join('') + comboSuggestionHTML() + upsellHTML()
      : '<div class="cart-empty"><p>Tu carrito está vacío.</p><a class="text-link" href="#carta" data-close-dialog>Ver la carta</a></div>';
  }
  const checkout = document.querySelector('#cartCheckout');
  if (checkout) {
    checkout.href = state.cart.length ? wa(cartMessage()) : wa('Hola! Quiero hacer un pedido.');
    checkout.textContent = state.cart.length ? 'Enviar pedido por WhatsApp' : 'Pedir por WhatsApp';
  }
}

function toast(text) {
  const node = document.createElement('div');
  node.className = 'toast';
  node.setAttribute('role', 'status');
  node.textContent = text;
  document.body.appendChild(node);
  window.setTimeout(() => node.remove(), 2400);
}

function openCart() {
  updateCartUI();
  const dialog = document.querySelector('#cartDialog');
  if (dialog && !dialog.open) dialog.showModal();
}

function customTotal() {
  const custom = state.customizing;
  if (!custom) return 0;
  const product = state.byId.get(custom.productId);
  const variant = (product.variants || []).find((v) => v.id === custom.variantId);
  const base = variant ? variant.price : product.price;
  const extraTotal = [...custom.extras].reduce((sum, id) => {
    const extra = state.byId.get(id);
    return sum + (extra ? extra.price : 0);
  }, 0);
  return (base + extraTotal) * custom.qty;
}

function renderCustomize() {
  const custom = state.customizing;
  if (!custom) return;
  const product = state.byId.get(custom.productId);
  if (!product) return;
  const variants = (product.variants || []).filter((v) => v.price != null);
  const extras = [
    ...state.products.filter((p) => p.kind === 'drink'),
    ...state.products.filter((p) => p.kind === 'dip'),
    ...state.products.filter((p) => p.kind === 'extra'),
  ].filter((p) => p.available && p.price != null);
  const removals = removalOptions(product);
  const sizeSection = variants.length > 1
    ? `<div class="custom-section"><h3>Tamaño</h3><div class="choice-grid">${variants.map((v) => `<button type="button" class="choice-button" data-size="${esc(v.id)}" aria-pressed="${v.id === custom.variantId}">${esc(v.name)} <b>${money(v.price)}</b></button>`).join('')}</div></div>`
    : '';
  const extraSection = extras.length
    ? `<div class="custom-section"><h3>Completá tu combo</h3><div class="check-list">${extras.map((p) => `<label class="check-row"><input type="checkbox" data-extra="${esc(p.id)}" ${custom.extras.has(p.id) ? 'checked' : ''}><span>${esc(p.name)}<small>${esc(p.description)}</small></span><b>+ ${money(p.price)}</b></label>`).join('')}</div></div>`
    : '';
  const removeSection = removals.length
    ? `<div class="custom-section"><h3>Sacar ingredientes</h3><div class="check-list">${removals.map((label) => `<label class="check-row"><input type="checkbox" data-remove="${esc(label)}" ${custom.removals.has(label) ? 'checked' : ''}><span>${esc(label)}</span><b>sin costo</b></label>`).join('')}</div></div>`
    : '';
  const body = document.querySelector('#customizeBody');
  body.innerHTML = `<div class="custom-intro"><h2>${esc(product.name)}</h2><p>${esc(product.description)}</p></div>
    ${sizeSection}${extraSection}${removeSection}
    <div class="custom-footer">
      <div class="custom-bottom">
        <div class="custom-qty"><span>Cantidad</span><div class="qty-control"><button type="button" data-qty="-1" aria-label="Restar">−</button><strong data-qty-value>${custom.qty}</strong><button type="button" data-qty="1" aria-label="Sumar">+</button></div></div>
        <div class="custom-total"><span>Total</span><strong data-custom-total>${money(customTotal())}</strong></div>
      </div>
      <button type="button" class="button custom-add" data-custom-add>Agregar al carrito</button>
    </div>`;
}

function updateCustomTotal() {
  const total = customTotal();
  const node = document.querySelector('[data-custom-total]');
  if (node) node.textContent = money(total);
}

function openCustomize(product) {
  if (!product || !product.available || product.kind !== 'burger') return;
  const variants = (product.variants || []).filter((v) => v.price != null);
  const selected = variants.find((v) => v.id === state.selected.get(product.id)) || variants[0] || null;
  state.customizing = {
    productId: product.id,
    variantId: selected ? selected.id : null,
    qty: 1, extras: new Set(), removals: new Set(),
  };
  renderCustomize();
  const dialog = document.querySelector('#customizeDialog');
  if (dialog && !dialog.open) dialog.showModal();
}

function addCustom() {
  const custom = state.customizing;
  if (!custom) return;
  const product = state.byId.get(custom.productId);
  const variant = (product.variants || []).find((v) => v.id === custom.variantId);
  const base = variant ? variant.price : product.price;
  const extras = [...custom.extras].map((id) => {
    const extra = state.byId.get(id);
    return { id, name: extra.name, price: extra.price, count: 1 };
  });
  const removals = [...custom.removals];
  const unit = base + extras.reduce((sum, extra) => sum + extra.price, 0);
  const key = `burger|${product.id}|${custom.variantId || ''}|${extras.map((e) => e.id).join(',')}|${removals.join(',')}`;
  addLine({
    key, kind: 'burger', id: product.id, name: product.name,
    variantId: custom.variantId, variantName: variant ? variant.name : '',
    basePrice: base, unit, qty: custom.qty, extras, removals, total: unit * custom.qty,
  });
  toast(`${product.name}${variant ? ` ${variant.name}` : ''} agregado`);
  const dialog = document.querySelector('#customizeDialog');
  if (dialog && dialog.open) dialog.close();
  state.customizing = null;
}

document.addEventListener('click', (event) => {
  const open = event.target.closest('[data-cart-open]');
  if (open) { openCart(); return; }
  const comboSuggest = event.target.closest('[data-combo-suggest]');
  if (comboSuggest) { applyComboSuggestion(comboSuggest.dataset.comboSuggest); return; }
  const add = event.target.closest('[data-add]');
  if (add) {
    const product = state.byId.get(add.dataset.add);
    if (product) addSimple(product);
    return;
  }
  const customize = event.target.closest('[data-customize]');
  if (customize) {
    const product = state.byId.get(customize.dataset.customize);
    if (product) openCustomize(product);
    return;
  }
  const close = event.target.closest('[data-close-dialog]');
  if (close) {
    const dialog = close.closest('dialog');
    if (dialog) dialog.close();
  }
});

document.querySelectorAll('dialog').forEach((dialog) => {
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close();
  });
});

document.querySelector('#customizeDialog').addEventListener('click', (event) => {
  const custom = state.customizing;
  if (!custom) return;
  const size = event.target.closest('[data-size]');
  if (size) {
    custom.variantId = size.dataset.size;
    document.querySelectorAll('[data-size]').forEach((button) => button.setAttribute('aria-pressed', String(button === size)));
    updateCustomTotal();
    return;
  }
  const extra = event.target.closest('[data-extra]');
  if (extra) {
    if (extra.checked) custom.extras.add(extra.dataset.extra); else custom.extras.delete(extra.dataset.extra);
    updateCustomTotal();
    return;
  }
  const remove = event.target.closest('[data-remove]');
  if (remove) {
    if (remove.checked) custom.removals.add(remove.dataset.remove); else custom.removals.delete(remove.dataset.remove);
    updateCustomTotal();
    return;
  }
  const qty = event.target.closest('[data-qty]');
  if (qty) {
    custom.qty = Math.max(1, Math.min(10, custom.qty + Number(qty.dataset.qty)));
    const value = document.querySelector('[data-qty-value]');
    if (value) value.textContent = custom.qty;
    updateCustomTotal();
    return;
  }
  if (event.target.closest('[data-custom-add]')) addCustom();
});

document.querySelector('#cartDialog').addEventListener('click', (event) => {
  const lineNode = event.target.closest('.cart-line');
  if (!lineNode) return;
  const line = state.cart.find((item) => item.key === lineNode.dataset.key);
  if (!line) return;
  if (event.target.closest('[data-cart-plus]')) line.qty = Math.min(20, line.qty + 1);
  else if (event.target.closest('[data-cart-minus]')) line.qty = Math.max(0, line.qty - 1);
  else if (event.target.closest('[data-cart-remove]')) line.qty = 0;
  else return;
  line.total = line.unit * line.qty;
  state.cart = state.cart.filter((item) => item.qty > 0);
  saveCart();
  updateCartUI();
});

async function loadMenu() {
  const grid = document.querySelector('#burgerGrid');
  const comboGrid = document.querySelector('#comboGrid');
  const extraList = document.querySelector('#extraList');
  const dipList = document.querySelector('#dipList');
  const drinkList = document.querySelector('#drinkList');
  try {
    const response = await fetch('/public/menu', { cache: 'no-store' });
    if (!response.ok) throw new Error('menu');
    const data = await response.json();
    state.products = data.products || [];
    state.byId = new Map(state.products.map((p) => [p.id, p]));
    const burgers = state.products.filter((p) => p.kind === 'burger');
    const combos = state.products.filter((p) => p.kind === 'combo');
    const extras = state.products.filter((p) => p.kind === 'extra');
    const dips = state.products.filter((p) => p.kind === 'dip');
    const drinks = state.products.filter((p) => p.kind === 'drink');
    grid.innerHTML = burgers.map(dishCard).join('') + noteCard();
    comboGrid.innerHTML = combos.map(comboCard).join('') || '<p class="combo-empty">Consultanos por los combos de esta semana.</p>';
    extraList.innerHTML = extras.map(priceRow).join('') || '<p class="list-empty">Consultanos por las papas y extras.</p>';
    dipList.innerHTML = dips.map(priceRow).join('') || '<p class="list-empty">Consultanos por los dips.</p>';
    drinkList.innerHTML = drinks.map(priceRow).join('') || '<p class="list-empty">Consultanos por las opciones y precios del día.</p>';
    bindSizes();
    const business = data.business || {};
    document.querySelectorAll('[data-wa-general]').forEach((link) => { link.href = wa('Hola! Quiero hacer un pedido.'); });
    if (business.phone) {
      document.querySelectorAll('.footer-phone').forEach((link) => { link.href = `https://wa.me/${business.phone}`; });
    }
    updateCartUI();
  } catch (error) {
    grid.innerHTML = `<p class="loading">No pudimos cargar la carta. <a href="${wa('Hola! Me pasás la carta?')}">Pedila por WhatsApp</a>.</p>`;
    comboGrid.innerHTML = '';
    updateCartUI();
  }
}

loadMenu();
