'use strict';

const WA = '5492643179759';
const money = (value) => '$' + new Intl.NumberFormat('es-AR', { maximumFractionDigits: 0 }).format(value || 0);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const wa = (text) => `https://wa.me/${WA}?text=${encodeURIComponent(text)}`;

const state = { products: [], byId: new Map(), selected: new Map() };

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
  const image = product.image_url
    ? `<img src="${esc(product.image_url)}" alt="${esc(product.name)}" loading="lazy" decoding="async">`
    : dishGlyph(product, index);
  const sizeButtons = variants.length > 1
    ? `<div class="size-row" role="group" aria-label="Tamaño de ${esc(product.name)}">${variants.map((v, i) => `<button type="button" class="size-button" data-variant="${esc(v.id)}" aria-pressed="${i === 0}">${esc(v.name)}</button>`).join('')}</div>`
    : '';
  const ctaText = product.available ? 'Pedir por WhatsApp' : 'Consultar disponibilidad';
  const ctaHref = product.available
    ? wa(`Hola! Quiero pedir 1 ${product.name}${defaultVariant ? ' ' + defaultVariant.name : ''}.`)
    : wa(`Hola! Quiero consultar por ${product.name}.`);
  return `<article class="dish${product.available ? '' : ' is-sold-out'}" data-id="${esc(product.id)}">
    <div class="dish-media">
      ${image}
      <span class="dish-index" aria-hidden="true">${String(product.code).padStart(2, '0')}</span>
      ${product.image_url ? '' : '<span class="photo-state">foto pendiente</span>'}
    </div>
    <div class="dish-body">
      <div class="dish-title-row">
        <h3>${esc(product.name)}${product.available ? '' : '<span class="sold-out">agotado</span>'}</h3>
        <span class="dish-price" data-price>${money(price)}</span>
      </div>
      <p class="dish-desc">${esc(product.description)}</p>
      ${sizeButtons}
      <a class="dish-cta" data-cta href="${ctaHref}">${ctaText} ${arrow}</a>
    </div>
  </article>`;
}

function comboCard(product, index) {
  return `<article class="combo">
    <div class="combo-head"><span>combo ${String(index + 1).padStart(2, '0')}</span><span>${esc((product.days || []).join(' / ') || 'fin de semana')}</span></div>
    <h3>${esc(product.name)}</h3>
    <p>${esc(product.description)}</p>
    <div class="combo-price">${money(product.price)}</div>
    <a class="combo-link" href="${wa(`Hola! Quiero pedir 1 ${product.name}.`)}">Pedir ${arrow}</a>
  </article>`;
}

function priceRow(product) {
  return `<a class="price-row" href="${wa(`Hola! Quiero agregar ${product.name}.`)}">
    <span class="name">${esc(product.name)}<span class="desc">${esc(product.description)}</span></span>
    <span class="dots" aria-hidden="true"></span>
    <span class="price">${money(product.price)}</span>
  </a>`;
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
    const cta = card.querySelector('[data-cta]');
    if (cta) cta.href = wa(`Hola! Quiero pedir 1 ${product.name} ${variant.name}.`);
    state.selected.set(product.id, variant.id);
  });
}

async function loadMenu() {
  const grid = document.querySelector('#burgerGrid');
  const comboGrid = document.querySelector('#comboGrid');
  const extraList = document.querySelector('#extraList');
  const dipList = document.querySelector('#dipList');
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
    grid.innerHTML = burgers.map(dishCard).join('') + noteCard();
    comboGrid.innerHTML = combos.map(comboCard).join('') || '<p class="combo-empty">Consultanos por los combos de esta semana.</p>';
    extraList.innerHTML = extras.map(priceRow).join('') || '<p class="loading">Consultanos por las papas y extras.</p>';
    dipList.innerHTML = dips.map(priceRow).join('') || '<p class="loading">Consultanos por los dips.</p>';
    bindSizes();
    const business = data.business || {};
    document.querySelectorAll('[data-wa-general]').forEach((link) => { link.href = wa('Hola! Quiero hacer un pedido.'); });
    if (business.phone) {
      document.querySelectorAll('.footer-phone').forEach((link) => { link.href = `https://wa.me/${business.phone}`; });
    }
  } catch (error) {
    grid.innerHTML = `<p class="loading">No pudimos cargar la carta. <a href="${wa('Hola! Me pasás la carta?')}">Pedila por WhatsApp</a>.</p>`;
    comboGrid.innerHTML = '';
  }
}

loadMenu();
