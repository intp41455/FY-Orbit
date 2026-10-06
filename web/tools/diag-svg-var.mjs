import { chromium } from 'playwright';
const b = await chromium.launch();
const page = await b.newPage();
await page.goto('http://127.0.0.1:4173/login');
console.log(JSON.stringify(await page.evaluate(() => {
  const root = getComputedStyle(document.documentElement);
  const tok = root.getPropertyValue('--ui-sky-700').trim();
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 10 10');
  // A: var() inside a presentation attribute
  const a = document.createElementNS('http://www.w3.org/2000/svg','rect');
  a.setAttribute('width','10'); a.setAttribute('height','10');
  a.setAttribute('fill','var(--ui-sky-700)');
  // B: control, literal hex
  const c = document.createElementNS('http://www.w3.org/2000/svg','rect');
  c.setAttribute('width','10'); c.setAttribute('height','10');
  c.setAttribute('fill', tok);
  // C: var() via style property
  const d = document.createElementNS('http://www.w3.org/2000/svg','rect');
  d.setAttribute('width','10'); d.setAttribute('height','10');
  d.style.fill = 'var(--ui-sky-700)';
  svg.append(a,c,d); document.body.appendChild(svg);
  const rd = (el) => { const cs=getComputedStyle(el); return { attr: el.getAttribute('fill'), computedFill: cs.fill }; };
  return { tokenSky700: tok, presentationAttr_var: rd(a), presentationAttr_literal: rd(c), style_var: rd(d) };
}), null, 2));
await b.close();
