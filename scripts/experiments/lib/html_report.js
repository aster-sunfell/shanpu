document.querySelectorAll('[data-timeline-select]').forEach(select => {
 select.addEventListener('change', () => {
  const scope=select.closest('[data-timeline-group]');
  scope.querySelectorAll('.timeline-variant').forEach(plot => {plot.hidden=plot.dataset.key!==select.value;});
 });
});
const menu = document.querySelector('nav');
if (menu) {
const navigation = Array.from(menu.querySelectorAll('a[href^="#"]')).map(link => ({link, section: document.querySelector(link.getAttribute('href'))})).filter(item => item.section);
const mobile = window.matchMedia('(max-width:720px)');
const reducedMotion = window.matchMedia('(prefers-reduced-motion:reduce)');
let navigating = false, navigationTimer, scrollFrame;

function sectionOffset() {
 return mobile.matches ? document.querySelector('aside').getBoundingClientRect().height + 16 : 24;
}
function setCurrent(id) {
 const current = navigation.find(item => item.section.id === id);
 if (!current) return;
 navigation.forEach(({link, section}) => {
  const active = section.id === id;
  link.classList.toggle('active', active);
  if (active) link.setAttribute('aria-current', 'location'); else link.removeAttribute('aria-current');
 });
 // Scroll only the menu, so keeping an item visible never moves the report.
 const rect = current.link.getBoundingClientRect(), bounds = menu.getBoundingClientRect();
 if (mobile.matches) {
  if (rect.left < bounds.left + 8) menu.scrollLeft -= bounds.left + 8 - rect.left;
  else if (rect.right > bounds.right - 8) menu.scrollLeft += rect.right - bounds.right + 8;
 } else {
  if (rect.top < bounds.top + 8) menu.scrollTop -= bounds.top + 8 - rect.top;
  else if (rect.bottom > bounds.bottom - 8) menu.scrollTop += rect.bottom - bounds.bottom + 8;
 }
}
function trackSection() {
 scrollFrame = null;
 if (navigating) return;
 const marker = sectionOffset() + 64;
 let current = navigation[0];
 for (const item of navigation) {
  if (item.section.getBoundingClientRect().top <= marker) current = item;
 }
 if (window.scrollY > 0 && window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 3) current = navigation[navigation.length - 1];
 if (current) setCurrent(current.section.id);
}
function scheduleTrack() {
 if (!scrollFrame) scrollFrame = requestAnimationFrame(trackSection);
}
function finishNavigation() {
 navigating = false;
 clearTimeout(navigationTimer);
}
function navigate(id, updateHistory = true) {
 const target = navigation.find(item => item.section.id === id)?.section;
 if (!target) return;
 if (target.tagName === 'DETAILS') target.open = true;
 navigating = true;
 clearTimeout(navigationTimer);
 setCurrent(id);
 if (updateHistory && location.hash !== '#' + id) history.pushState(null, '', '#' + id);
 window.scrollTo({top: Math.max(0, window.scrollY + target.getBoundingClientRect().top - sectionOffset()), behavior: reducedMotion.matches ? 'instant' : 'smooth'});
 navigationTimer = setTimeout(finishNavigation, 1400);
}
navigation.forEach(({link, section}) => link.addEventListener('click', event => {
 if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
 event.preventDefault(); navigate(section.id);
}));
window.addEventListener('scroll', scheduleTrack, {passive:true});
window.addEventListener('scrollend', finishNavigation);
window.addEventListener('wheel', finishNavigation, {passive:true});
window.addEventListener('touchstart', finishNavigation, {passive:true});
window.addEventListener('keydown', event => {
 if (['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key)) finishNavigation();
});
function resizeNavigation() {
 document.documentElement.style.setProperty('--section-offset', sectionOffset() + 'px');
 scheduleTrack();
}
window.addEventListener('resize', resizeNavigation);
new ResizeObserver(resizeNavigation).observe(document.querySelector('aside'));
window.addEventListener('popstate', () => navigate(location.hash.slice(1) || 'overview', false));
window.addEventListener('hashchange', () => navigate(location.hash.slice(1) || 'overview', false));
resizeNavigation();
if (location.hash) navigate(location.hash.slice(1), false); else setCurrent('overview');

}
