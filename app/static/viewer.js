(() => {
  const viewport = document.querySelector(".scan-viewport");
  if (!viewport) return;

  const sheet = viewport.querySelector(".scan-sheet");
  const image = sheet.querySelector("img");
  const regions = new Map();
  let scale = 1;
  let activeRegion;
  let drag;
  let dragged = false;

  for (const region of sheet.querySelectorAll(".region")) {
    const { x, y, width, height } = region.dataset;
    if ([x, y, width, height].some(value => !value || !value.trim())) continue;
    const [left, top, w, h] = [x, y, width, height].map(Number);
    if (![left, top, w, h].every(Number.isFinite) ||
        left < 0 || top < 0 || w <= 0 || h <= 0 || left + w > 1 || top + h > 1) continue;

    region.style.left = `${left * 100}%`;
    region.style.top = `${top * 100}%`;
    region.style.width = `${w * 100}%`;
    region.style.height = `${h * 100}%`;
    region.hidden = false;
    regions.set(region.hash, region);
  }

  function highlight(hash) {
    activeRegion?.classList.remove("is-active");
    activeRegion = regions.get(hash);
    if (!activeRegion) return;
    activeRegion.classList.add("is-active");
    viewport.scrollLeft = activeRegion.offsetLeft + activeRegion.offsetWidth / 2 - viewport.clientWidth / 2;
    viewport.scrollTop = activeRegion.offsetTop + activeRegion.offsetHeight / 2 - viewport.clientHeight / 2;
  }

  document.querySelectorAll("[data-show-region]").forEach(link => {
    const hash = `#${link.dataset.showRegion}`;
    if (!regions.has(hash)) return;
    link.hidden = false;
    link.addEventListener("click", event => {
      event.preventDefault();
      highlight(hash);
      viewport.focus({ preventScroll: true });
      const viewer = viewport.closest(".viewer");
      const bounds = viewer.getBoundingClientRect();
      if (bounds.bottom <= 0 || bounds.top >= window.innerHeight) viewer.scrollIntoView();
    });
  });

  const controls = document.querySelector("[data-viewer-controls]");
  controls.hidden = false;
  const zoomOut = controls.querySelector('[data-zoom="out"]');
  const zoomIn = controls.querySelector('[data-zoom="in"]');
  const fit = controls.querySelector('[data-zoom="fit"]');
  const maxScale = 6;
  function zoom(nextScale, fitWidth = false) {
    const x = viewport.scrollLeft + viewport.clientWidth / 2;
    const y = viewport.scrollTop + viewport.clientHeight / 2;
    const previousWidth = sheet.clientWidth;
    scale = Math.min(maxScale, Math.max(1, nextScale));
    sheet.style.width = `${scale * 100}%`;
    const ratio = sheet.clientWidth / previousWidth;
    viewport.scrollLeft = fitWidth ? 0 : x * ratio - viewport.clientWidth / 2;
    viewport.scrollTop = y * ratio - viewport.clientHeight / 2;
    zoomOut.disabled = scale <= 1;
    zoomIn.disabled = scale >= maxScale;
  }
  zoomOut.disabled = true;
  zoomOut.addEventListener("click", () => zoom(scale / 1.35));
  zoomIn.addEventListener("click", () => zoom(scale * 1.35));
  fit.addEventListener("click", () => zoom(1, true));

  viewport.addEventListener("pointerdown", event => {
    if (event.pointerType !== "mouse" || event.button !== 0 || event.target === viewport) return;
    drag = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
    dragged = false;
  });
  viewport.addEventListener("pointermove", event => {
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!dragged && Math.hypot(dx, dy) < 4) return;
    if (!dragged) viewport.setPointerCapture(event.pointerId);
    dragged = true;
    viewport.classList.add("is-dragging");
    viewport.scrollLeft = drag.left - dx;
    viewport.scrollTop = drag.top - dy;
  });
  function releaseDrag() {
    drag = null;
    viewport.classList.remove("is-dragging");
  }
  window.addEventListener("pointerup", releaseDrag);
  viewport.addEventListener("pointercancel", releaseDrag);
  viewport.addEventListener("click", event => {
    if (dragged) {
      event.preventDefault();
      dragged = false;
    }
  }, true);

  window.addEventListener("hashchange", () => highlight(location.hash));
  if (image.complete) highlight(location.hash);
  else image.addEventListener("load", () => highlight(location.hash), { once: true });
})();
