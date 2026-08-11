/* ══════════════════════════════════════════════════════════════════
   АТЛАС РОССИИ — логика страницы

   Три сцены (гидрография, орография, политика) живут в одном холсте
   Raphael. Каждая собирается один раз при первом показе и дальше
   только прячется/показывается целиком — переключение вкладок не
   пересоздаёт ни одного элемента.

   Главная идея всей отрисовки: скрипт не трогает элементы по одному.
   Цвета, подсветка и толщина линий заданы в CSS; размер подписей
   задаётся один раз на группу и наследуется. Поэтому при зуме на
   каждый кадр приходится несколько записей в DOM, а не тысяча.
   ══════════════════════════════════════════════════════════════════ */
(function () {
'use strict';

var SVGNS = 'http://www.w3.org/2000/svg';
var $ = function (id) { return document.getElementById(id); };

var wrap = $('wrap'), stageEl = $('stage'), boot = $('boot'),
    legend = $('legend');

/* ─── Проверка окружения ──────────────────────────────────────────
   Обе библиотеки приезжают с CDN, данные — четырьмя файлами рядом.
   Если чего-то нет, честно говорим об этом вместо пустого экрана. */
function die(msg) {
  var sp = $('boot-sp');
  if (sp) sp.remove();
  var m = $('boot-msg');
  m.className = 'fail';
  m.textContent = msg;
  throw new Error(msg);
}
if (typeof Raphael === 'undefined') die('Не загрузилась библиотека Raphael. Проверьте подключение к сети и обновите страницу.');
if (typeof Panzoom === 'undefined') die('Не загрузилась библиотека Panzoom. Проверьте подключение к сети и обновите страницу.');
if (!window.RU_BASE || !window.RU_REGIONS || !window.RU_HYDRO || !window.RU_ORO) {
  die('Не загрузились данные карты (папка data). Обновите страницу.');
}

var BASE = window.RU_BASE, REG = window.RU_REGIONS,
    HYD = window.RU_HYDRO, ORO = window.RU_ORO;

var W = BASE.w, H = BASE.h;
stageEl.style.setProperty('--mw', W + 'px');
stageEl.style.setProperty('--mh', H + 'px');

var paper = Raphael(stageEl, W, H);
var canvas = paper.canvas;
canvas.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
canvas.setAttribute('preserveAspectRatio', 'xMidYMid meet');
/* Raphael прописывает холсту overflow:hidden прямо в стиль. Из-за этого
   суша соседних стран обрывалась ровно по границе холста, и на общем
   виде карта выглядела вклеенным прямоугольником. Отпускаем: данные
   соседей собраны с запасом как раз под это. */
canvas.style.overflow = 'visible';

/* ─── Мелкие помощники по SVG ─────────────────────────────────── */
function g(cls, parent) {
  var n = document.createElementNS(SVGNS, 'g');
  if (cls) n.setAttribute('class', cls);
  (parent || canvas).appendChild(n);
  return n;
}

/* Пути рисует Raphael, а вот подписи делаем нативным <text>.
   Raphael к каждому тексту прибивает собственный font-size и
   font-family прямо на узел, и тогда наследование размера от группы
   (на котором держится масштабирование подписей) перестаёт работать. */
function text(parent, x, y, str, cls, rot) {
  var t = document.createElementNS(SVGNS, 'text');
  t.setAttribute('x', x);
  t.setAttribute('y', y);
  t.setAttribute('class', cls);
  if (rot) t.setAttribute('transform', 'rotate(' + rot + ' ' + x + ' ' + y + ')');
  t.appendChild(document.createTextNode(str));
  parent.appendChild(t);
  return t;
}

/* Кладём путь Raphael в нужную группу и вешаем класс. */
function put(parent, d, cls, feat) {
  var el = paper.path(d);
  el.node.setAttribute('class', cls);
  /* Ссылка на объект прямо в узле: при наведении не нужно ни искать
     по индексу, ни разбирать data-атрибуты. */
  if (feat) el.node.__f = feat;
  parent.appendChild(el.node);
  return el.node;
}

/* Штриховка для территорий со спорным статусом. */
(function defs() {
  var d = document.createElementNS(SVGNS, 'defs');
  d.innerHTML =
    '<pattern id="hatch" width="7" height="7" patternUnits="userSpaceOnUse"' +
    ' patternTransform="rotate(45)">' +
    '<line x1="0" y1="0" x2="0" y2="7"/></pattern>';
  canvas.insertBefore(d, canvas.firstChild);
})();

/* ─── Ярусы подписей ───────────────────────────────────────────────
   Подписей на карте больше, чем помещается: 89 субъектов, 62 реки,
   246 озёр. Показывать всё сразу — каша. Поэтому каждая подпись
   получает ярус по значимости объекта, а ярусы включаются по мере
   приближения. Ярус — это отдельная группа <g>, так что показать или
   скрыть полсотни подписей стоит одной записи в DOM. */
var TIER_AT = [0, 1.5, 2.4, 3.8];   // во сколько раз нужно приблизить

function tierOf(v, t0, t1, t2) {
  return v >= t0 ? 0 : v >= t1 ? 1 : v >= t2 ? 2 : 3;
}

/* Сцена = корневая группа + четыре группы подписей + свой поисковый
   индекс. */
function Scene(name) {
  this.name = name;
  this.root = g('scene');
  this.root.style.display = 'none';
  this.shapes = g('shapes', this.root);
  this.lines = g('lines', this.root);
  this.marks = g('marks', this.root);
  this.tiers = [];
  for (var i = 0; i < 4; i++) {
    var t = g('lbl-tier', this.root);
    t.style.fontSize = '14px';
    this.tiers.push(t);
  }
  this.index = [];    // {name, sub, x, y, k} для поиска
  this.built = false;
}

Scene.prototype.label = function (tier, x, y, str, cls, rot) {
  return text(this.tiers[tier], x, y, str, 'lbl ' + (cls || ''), rot);
};

/* Базовый кегль подписей в экранных пикселях. На телефоне карта в кадре
   в четыре раза мельче, и подписи десктопного размера перекрывают её
   целиком — поэтому кегль привязан к ширине окна. */
var labelPx = 14;

/* Размер шрифта задаём одной записью на ярус: делим базовый кегль на
   текущий масштаб, и подпись остаётся одного размера на экране, как
   бы карту ни увеличили. */
Scene.prototype.scaleLabels = function (k) {
  var px = (labelPx / k) + 'px';
  for (var i = 0; i < 4; i++) this.tiers[i].style.fontSize = px;
};

Scene.prototype.applyTiers = function (rel) {
  for (var i = 0; i < 4; i++) {
    this.tiers[i].style.display = rel >= TIER_AT[i] ? '' : 'none';
  }
};

/* Прореживание налезающих подписей.

   Ярусы решают, сколько подписей показать, но не решают, не сядут ли
   две соседние друг на друга: «Бурятия» и «Забайкальский» стоят рядом
   на любом ярусе. Поэтому после каждого изменения масштаба проходим по
   подписям в порядке важности и гасим те, чей прямоугольник пересекает
   уже принятый. Чем ближе масштаб, тем реже конфликты: подписи
   сохраняют экранный размер, а расстояния между ними растут.

   Чтения и записи в DOM разнесены на три прохода, иначе браузер
   пересчитывал бы вёрстку на каждой подписи. */
Scene.prototype.cull = function () {
  var vis = [], i, j, k, kids;

  for (i = 0; i < 4; i++) {
    if (this.tiers[i].style.display === 'none') continue;
    kids = this.tiers[i].children;
    for (j = 0; j < kids.length; j++) {
      kids[j].classList.remove('hide');
      vis.push(kids[j]);
    }
  }

  var rects = new Array(vis.length);
  for (i = 0; i < vis.length; i++) rects[i] = vis[i].getBoundingClientRect();

  var kept = [], r, q, hit;
  for (i = 0; i < vis.length; i++) {
    r = rects[i];
    if (!r.width) continue;
    hit = false;
    for (k = 0; k < kept.length; k++) {
      q = kept[k];
      if (r.left < q.right + 3 && r.right + 3 > q.left &&
          r.top < q.bottom + 1 && r.bottom + 1 > q.top) { hit = true; break; }
    }
    if (hit) vis[i].classList.add('hide');
    else kept.push(r);
  }
};

/* ─── Общая подложка: суша соседей и градусная сетка ─────────────── */
function drawBackdrop(sc, opts) {
  var i;
  for (i = 0; i < BASE.neighbours.length; i++) {
    put(sc.shapes, BASE.neighbours[i], 'neigh');
  }
  if (opts.ruLand) put(sc.shapes, BASE.ru, 'ru-land');
  put(sc.shapes, BASE.grat, 'grat');
  if (opts.coast) put(sc.lines, BASE.ru, 'coast');
}

/* ══════════════════════════════════════════════════════════════════
   Сцена 1. Гидрография
   ══════════════════════════════════════════════════════════════════ */
var hydro = new Scene('hydro');
hydro.build = function () {
  drawBackdrop(this, { ruLand: true, coast: true });
  var i, o;

  /* Озёра рисуем до рек: реки должны втекать в озеро поверх него, а
     не обрываться о его край. */
  for (i = 0; i < HYD.lakes.length; i++) {
    o = HYD.lakes[i];
    o.kind = o.t === 'res' ? 'Водохранилище' : 'Озеро';
    put(this.shapes, o.d, 'lake' + (o.t === 'res' ? ' res' : ''), o);
  }
  for (i = 0; i < HYD.rivers.length; i++) {
    o = HYD.rivers[i];
    o.kind = 'Река';
    put(this.lines, o.d, 'riv' + (o.r === 1 ? ' a' : ''), o);
  }

  /* Моря и океаны — самый верхний уровень чтения карты, они на нулевом
     ярусе почти все. */
  for (i = 0; i < HYD.seas.length; i++) {
    o = HYD.seas[i];
    var st = o.t === 'ocean' ? 0 : tierOf(o.a, 6000, 1200, 200);
    this.label(st, o.lx, o.ly, o.n, 'sea' + (o.t === 'ocean' ? ' ocean' : ''));
    this.index.push({ n: o.n, s: o.t === 'ocean' ? 'Океан' : 'Море', x: o.lx, y: o.ly, k: 1.6 });
  }
  for (i = 0; i < HYD.rivers.length; i++) {
    o = HYD.rivers[i];
    this.label(tierOf(o.len, 420, 200, 90), o.lx, o.ly, o.n, 'water', o.a);
    this.index.push({ n: o.n, s: 'Река', x: o.lx, y: o.ly, k: 2.6 });
  }
  for (i = 0; i < HYD.lakes.length; i++) {
    o = HYD.lakes[i];
    if (!o.n) continue;
    var ll = this.label(tierOf(o.a, 1200, 250, 45), o.lx, o.ly, o.n, 'water');
    ll.setAttribute('dy', '-0.8em');
    this.index.push({ n: o.n, s: o.kind, x: o.lx, y: o.ly, k: 3.2 });
  }
};

/* ══════════════════════════════════════════════════════════════════
   Сцена 2. Орография
   ══════════════════════════════════════════════════════════════════ */
var ORO_KIND = {
  range: 'Горы, хребет', plateau: 'Плоскогорье, нагорье',
  upland: 'Возвышенность', plain: 'Равнина',
  lowland: 'Низменность', pen: 'Полуостров'
};

var oro = new Scene('oro');
oro.build = function () {
  drawBackdrop(this, { ruLand: true, coast: true });
  var i, o;
  /* Данные уже отсортированы по убыванию площади: крупные равнины
     ложатся первыми, возвышенности внутри них — поверх. */
  for (i = 0; i < ORO.items.length; i++) {
    o = ORO.items[i];
    o.kind = ORO_KIND[o.t] || 'Форма рельефа';
    put(this.shapes, o.d, 'oro t-' + o.t, o);
  }
  for (i = 0; i < ORO.items.length; i++) {
    o = ORO.items[i];
    /* Подписи хребтов разворачиваем вдоль главной оси формы — так их
       делают на бумажных картах, и так они не налезают на соседей. */
    var rot = (o.t === 'range' || o.t === 'upland') ? o.r : 0;
    if (Math.abs(rot) < 8) rot = 0;
    this.label(tierOf(o.a, 14000, 3500, 700), o.lx, o.ly, o.n, '', rot);
    this.index.push({ n: o.n, s: o.kind, x: o.lx, y: o.ly, k: 2.4 });
  }
};

/* ══════════════════════════════════════════════════════════════════
   Сцена 3. Политическая карта
   ══════════════════════════════════════════════════════════════════ */
var FD = {
  'ЦФО': ['Центральный', 1], 'СЗФО': ['Северо-Западный', 2],
  'ЮФО': ['Южный', 3], 'СКФО': ['Северо-Кавказский', 4],
  'ПФО': ['Приволжский', 5], 'УФО': ['Уральский', 6],
  'СФО': ['Сибирский', 7], 'ДФО': ['Дальневосточный', 8]
};
var TYPE = {
  republic: 'Республика', krai: 'Край', oblast: 'Область',
  city: 'Город федерального значения', okrug: 'Автономный округ',
  aoblast: 'Автономная область'
};

var pol = new Scene('pol');
pol.build = function () {
  drawBackdrop(this, { ruLand: false, coast: false });
  var i, o, list = REG.items;

  for (i = 0; i < list.length; i++) {
    o = list[i];
    o.kind = TYPE[o.t] || 'Субъект РФ';
    o.fdName = (FD[o.fd] || ['', 1])[0] + ' федеральный округ';
    o.node = put(this.shapes, o.p, 'reg fd' + (FD[o.fd] || ['', 1])[1], o);
  }
  /* Штриховка спорных территорий идёт отдельным слоем поверх заливки:
     так она не мешает подсветке при наведении. */
  for (i = 0; i < list.length; i++) {
    if (list[i].dis) put(this.marks, list[i].p, 'disp');
  }

  for (i = 0; i < list.length; i++) {
    o = list[i];
    var t = tierOf(o.a, 30000, 9000, 2200);
    this.label(t, o.lx, o.ly, o.s, 'dim');

    /* Точка столицы видна всегда, подпись к ней — ярусом позже, иначе
       названия городов сливаются с названиями субъектов. */
    var big = o.t === 'city' ? ' big' : '';
    var d = 'M' + o.cx + ',' + o.cy + 'l0,0';
    put(this.marks, d, 'cap-ring' + big);
    put(this.marks, d, 'cap-core' + big);

    var cl = this.label(Math.min(3, t + 1), o.cx, o.cy, o.cap, 'cap');
    /* Сдвиг в em, а не в единицах карты: em считается от кегля, который
       сам делится на масштаб, поэтому подпись держится на одном
       расстоянии от точки при любом приближении. */
    cl.setAttribute('dy', '-0.95em');
    this.index.push({ n: o.n, s: o.kind + ' · ' + o.cap, x: o.lx, y: o.ly, k: 3, reg: o });
    if (o.cap !== o.n) {
      this.index.push({ n: o.cap, s: 'Столица · ' + o.n, x: o.cx, y: o.cy, k: 4, reg: o });
    }
  }
};

var scenes = { hydro: hydro, oro: oro, pol: pol };

/* ══════════════════════════════════════════════════════════════════
   Масштаб и панорама
   ══════════════════════════════════════════════════════════════════ */
var pz = Panzoom(stageEl, {
  canvas: true,          /* тянуть можно за любое место подложки */
  step: 0.35,
  minScale: 0.05,
  maxScale: 40,
  animate: false,
  duration: 380,
  easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
  cursor: ''             /* курсором управляем сами, через класс .drag */
});

var fit = 1;            /* масштаб, при котором карта видна целиком */
var inset = 0;          /* сколько слева занимает раскрытая легенда */

function computeFit() {
  var r = wrap.getBoundingClientRect();
  if (!r.width || !r.height) return;
  /* Легенда висит над картой в левом нижнем углу — ровно там, где
     европейская часть страны. Поэтому вписываем карту не во всё окно,
     а в свободную от панели часть и сдвигаем её вправо на половину
     занятой ширины. На узком экране легенда сворачивается, и поправка
     обнуляется сама. */
  inset = (r.width >= 900 && !legend.classList.contains('folded'))
    ? legend.getBoundingClientRect().width + 34 : 0;
  labelPx = r.width < 620 ? 10 : (r.width < 1100 ? 12 : 14);
  fit = Math.min((r.width - inset) / W, r.height / H) * 0.96;
  pz.setOptions({ minScale: fit * 0.75, maxScale: fit * 18 });
}

/* Ставит точку (px, py) холста в центр свободной части экрана.
   Panzoom пишет transform: scale(k) translate(x, y), холст отцентрован
   в контейнере — значит нужный сдвиг равен вектору от точки до центра
   холста, плюс поправка на легенду (её делим на масштаб, потому что
   translate внутри transform уже умножается на него). */
function panTo(px, py, k, animate) {
  pz.pan(W / 2 - px + (inset / 2) / k, H / 2 - py, { animate: !!animate });
}

function resetView(animate) {
  computeFit();
  pz.zoom(fit, { animate: !!animate });
  panTo(W / 2, H / 2, fit, animate);
  onZoom();
}

function focusOn(px, py, k, animate) {
  computeFit();
  var z = Math.min(Math.max(k * fit, fit), fit * 18);
  pz.zoom(z, { animate: !!animate });
  panTo(px, py, z, animate);
  onZoom();
}

var current = null;     /* активная сцена */
var lastRel = -1;
var lvlEl = $('lvl');
var rafPending = false;

function onZoom() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(function () {
    rafPending = false;
    var k = pz.getScale();
    var rel = fit ? k / fit : 1;
    if (current) {
      current.scaleLabels(k);
      /* Ярусы переключаем только когда порог реально перейден:
         иначе на каждом кадре плавного зума шли бы лишние записи. */
      if (lastRel < 0 || bucket(rel) !== bucket(lastRel)) current.applyTiers(rel);
    }
    lastRel = rel;
    lvlEl.textContent = (rel < 10 ? rel.toFixed(1) : Math.round(rel)) + '×';
    scheduleCull();
  });
}

/* Прореживание считает геометрию текста — делать это на каждом кадре
   зума расточительно. Ждём, пока масштаб успокоится. */
var cullTimer;
function scheduleCull() {
  clearTimeout(cullTimer);
  cullTimer = setTimeout(function () {
    if (current) current.cull();
  }, 160);
}

function bucket(rel) {
  var b = 0;
  for (var i = 1; i < TIER_AT.length; i++) if (rel >= TIER_AT[i]) b = i;
  return b;
}

wrap.addEventListener('wheel', function (e) {
  /* Panzoom сам гасит событие и считает точку под курсором. */
  pz.zoomWithWheel(e);
  /* Карта уехала под неподвижным курсором, а pointermove при этом не
     приходит — подсказка осталась бы от прежнего объекта. */
  hideTip();
  onZoom();
}, { passive: false });

stageEl.addEventListener('panzoomchange', onZoom);

/* Перетаскивание карты браузер завершает обычным кликом. Без этой
   проверки любое движение по карте сбрасывало бы выбранный субъект,
   поэтому кликом считаем только то, что уложилось в пять пикселей. */
var down = false, downX = 0, downY = 0, moved = false;
wrap.addEventListener('pointerdown', function (e) {
  down = true; moved = false;
  downX = e.clientX; downY = e.clientY;
  wrap.classList.add('drag');
});
addEventListener('pointermove', function (e) {
  if (down && Math.abs(e.clientX - downX) + Math.abs(e.clientY - downY) > 5) {
    moved = true;
  }
});
addEventListener('pointerup', function () {
  down = false;
  wrap.classList.remove('drag');
});

$('zin').onclick = function () { pz.zoomIn({ animate: true }); onZoom(); };
$('zout').onclick = function () { pz.zoomOut({ animate: true }); onZoom(); };
$('zreset').onclick = function () { resetView(true); };

var resizeTimer;
addEventListener('resize', function () {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () {
    wrapBox = null;
    /* Развёрнутая легенда на узком экране съедает половину карты —
       сворачиваем её сами, как и при первой загрузке телефона. */
    if (innerWidth < 620 && !legend.classList.contains('folded')) foldLegend(true);
    var before = fit ? pz.getScale() / fit : 1;
    computeFit();
    pz.zoom(fit * before);
    onZoom();
  }, 150);
});

/* ══════════════════════════════════════════════════════════════════
   Подсказка при наведении и карточка по клику
   ══════════════════════════════════════════════════════════════════ */
var tip = $('tip'), tipShown = false, tipFeat = null, wrapBox = null;

function showTip(feat, e) {
  if (!wrapBox) wrapBox = wrap.getBoundingClientRect();
  /* Содержимое пересобираем только при смене объекта: при движении
     мыши внутри одного региона хватает переноса координат. */
  if (feat !== tipFeat) {
    tipFeat = feat;
    tip.textContent = '';
    var b = document.createElement('b');
    b.textContent = feat.n || feat.kind;
    tip.appendChild(b);
    var sub = feat.cap ? feat.kind + ' · ' + feat.cap : feat.kind;
    if (sub && feat.n) {
      var i = document.createElement('i');
      i.textContent = sub;
      tip.appendChild(i);
    }
  }
  tip.style.left = (e.clientX - wrapBox.left) + 'px';
  tip.style.top = (e.clientY - wrapBox.top) + 'px';
  if (!tipShown) { tip.classList.add('on'); tipShown = true; }
}

function hideTip() {
  if (tipShown) { tip.classList.remove('on'); tipShown = false; }
  tipFeat = null;
}

/* Один обработчик на весь холст вместо тысячи. Объект находим по
   ссылке, положенной прямо в узел при отрисовке. */
wrap.addEventListener('pointermove', function (e) {
  if (down) { hideTip(); return; }     /* во время перетаскивания не мешаем */
  var f = e.target && e.target.__f;
  if (f) showTip(f, e); else hideTip();
});
wrap.addEventListener('pointerleave', hideTip);
addEventListener('scroll', function () { wrapBox = null; }, true);

var card = $('card'), curNode = null;

function select(feat) {
  if (curNode) curNode.classList.remove('on');
  curNode = null;
  if (!feat) { card.classList.remove('on'); return; }

  if (feat.node) { feat.node.classList.add('on'); curNode = feat.node; }

  $('card-kind').textContent = feat.kind || '';
  $('card-name').textContent = feat.n;

  var rows = $('card-rows');
  rows.innerHTML = '';
  function row(k, v) {
    if (!v) return;
    var dt = document.createElement('dt'); dt.textContent = k;
    var dd = document.createElement('dd'); dd.textContent = v;
    rows.appendChild(dt); rows.appendChild(dd);
  }
  if (feat.cap) row('Столица', feat.cap);
  if (feat.fdName) row('Округ', feat.fdName);
  if (feat.t && TYPE[feat.t]) row('Статус', TYPE[feat.t]);

  card.classList.add('on');
}

wrap.addEventListener('click', function (e) {
  if (moved) return;
  if (e.target.closest && e.target.closest('.panel')) return;
  var f = e.target && e.target.__f;
  select(f && f.kind ? f : null);
});

$('card-close').onclick = function () { select(null); };

/* ══════════════════════════════════════════════════════════════════
   Поиск
   ══════════════════════════════════════════════════════════════════ */
var qEl = $('q'), hitsEl = $('hits'), findEl = $('find');
var hits = [], cur = -1;

/* Регистр и «ё» приводим к одному виду: человек ищет «оленек», а на
   карте «Оленёк». */
function norm(s) { return s.toLowerCase().replace(/ё/g, 'е'); }

function search(q) {
  var nq = norm(q.trim());
  if (!nq || !current) return [];
  var out = [], i, it, p;
  for (i = 0; i < current.index.length; i++) {
    it = current.index[i];
    p = norm(it.n).indexOf(nq);
    if (p < 0) continue;
    /* Совпадение с начала названия важнее совпадения в середине. */
    out.push({ it: it, rank: (p === 0 ? 0 : 1) * 1000 + it.n.length, at: p });
    if (out.length > 400) break;
  }
  out.sort(function (a, b) { return a.rank - b.rank; });
  return out.slice(0, 8);
}

function renderHits() {
  hitsEl.innerHTML = '';
  if (!hits.length) {
    if (qEl.value.trim()) {
      var li = document.createElement('li');
      li.className = 'empty';
      li.textContent = 'Ничего не найдено на этой карте';
      hitsEl.appendChild(li);
      hitsEl.classList.add('on');
      qEl.setAttribute('aria-expanded', 'true');
    } else {
      hitsEl.classList.remove('on');
      qEl.setAttribute('aria-expanded', 'false');
    }
    return;
  }
  hits.forEach(function (h, i) {
    var li = document.createElement('li');
    var b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('role', 'option');
    b.setAttribute('aria-selected', i === cur ? 'true' : 'false');
    if (i === cur) b.dataset.cur = '1';

    var nm = document.createElement('span');
    nm.className = 'nm';
    var n = h.it.n, a = h.at, len = qEl.value.trim().length;
    nm.appendChild(document.createTextNode(n.slice(0, a)));
    var mk = document.createElement('mark');
    mk.textContent = n.slice(a, a + len);
    nm.appendChild(mk);
    nm.appendChild(document.createTextNode(n.slice(a + len)));

    var sb = document.createElement('span');
    sb.className = 'sub';
    sb.textContent = h.it.s;

    b.appendChild(nm); b.appendChild(sb);
    b.onclick = function () { go(h.it); };
    li.appendChild(b);
    hitsEl.appendChild(li);
  });
  hitsEl.classList.add('on');
  qEl.setAttribute('aria-expanded', 'true');
}

function go(it) {
  focusOn(it.x, it.y, it.k, true);
  if (it.reg) select(it.reg);
  hitsEl.classList.remove('on');
  qEl.setAttribute('aria-expanded', 'false');
  qEl.blur();
}

qEl.addEventListener('input', function () {
  findEl.classList.toggle('filled', !!qEl.value);
  hits = search(qEl.value);
  cur = hits.length ? 0 : -1;
  renderHits();
});

qEl.addEventListener('keydown', function (e) {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (!hits.length) return;
    e.preventDefault();
    cur = (cur + (e.key === 'ArrowDown' ? 1 : hits.length - 1)) % hits.length;
    renderHits();
  } else if (e.key === 'Enter') {
    if (cur >= 0 && hits[cur]) { e.preventDefault(); go(hits[cur].it); }
  } else if (e.key === 'Escape') {
    qEl.value = '';
    findEl.classList.remove('filled');
    hits = [];
    renderHits();
  }
});

$('qclear').onclick = function () {
  qEl.value = '';
  findEl.classList.remove('filled');
  hits = [];
  renderHits();
  qEl.focus();
};

document.addEventListener('click', function (e) {
  if (!findEl.contains(e.target)) {
    hitsEl.classList.remove('on');
    qEl.setAttribute('aria-expanded', 'false');
  }
});

/* ══════════════════════════════════════════════════════════════════
   Легенда
   ══════════════════════════════════════════════════════════════════ */
var legendBody = $('legend-body'), legendTitle = $('legend-title');

/* mark — форма образца: '' прямоугольник, 'ln' линия, 'dot' точка. */
function keyRow(mark, style, name, note) {
  var d = document.createElement('div');
  d.className = 'key';
  var i = document.createElement('i');
  if (mark) i.className = mark;
  i.setAttribute('style', style);
  var s = document.createElement('span');
  var b = document.createElement('b');
  b.textContent = name;
  s.appendChild(b);
  if (note) s.appendChild(document.createTextNode(' — ' + note));
  d.appendChild(i); d.appendChild(s);
  return d;
}

function head(t) {
  var h = document.createElement('h4');
  h.textContent = t;
  return h;
}

function note(t) {
  var p = document.createElement('p');
  p.className = 'note';
  p.textContent = t;
  return p;
}

var LEGENDS = {
  hydro: function (box) {
    box.appendChild(head('Водотоки'));
    box.appendChild(keyRow('ln', 'border-top-color:var(--h-river);border-top-width:3px',
      'Крупные реки', 'Волга, Обь, Енисей, Лена, Амур'));
    box.appendChild(keyRow('ln', 'border-top-color:var(--h-river-2);border-top-width:2px',
      'Притоки', 'подписаны при приближении'));
    box.appendChild(head('Водоёмы'));
    box.appendChild(keyRow('', 'background:var(--h-lake)', 'Озёра'));
    box.appendChild(keyRow('', 'background:var(--h-res)', 'Водохранилища'));
    box.appendChild(keyRow('', 'background:var(--m-water-2)', 'Моря и океаны',
      'подписаны прописными'));
    box.appendChild(note('Подписей тем больше, чем ближе масштаб: на общем виде показаны только главные реки и моря.'));
  },
  oro: function (box) {
    box.appendChild(head('Горный рельеф'));
    box.appendChild(keyRow('', 'background:var(--o-range)', 'Горы и хребты'));
    box.appendChild(keyRow('', 'background:var(--o-plateau)', 'Плоскогорья, нагорья'));
    box.appendChild(keyRow('', 'background:var(--o-upland)', 'Возвышенности'));
    box.appendChild(head('Равнинный рельеф'));
    box.appendChild(keyRow('', 'background:var(--o-plain)', 'Равнины'));
    box.appendChild(keyRow('', 'background:var(--o-lowland)', 'Низменности'));
    box.appendChild(head('Прочее'));
    box.appendChild(keyRow('', 'background:var(--o-pen)', 'Полуострова'));
    box.appendChild(note('Границы форм рельефа генерализованы: это ареалы, а не изолинии высот. Часть контуров — Русская равнина, Тиманский кряж, Сибирские Увалы и другие — построена схематично по физико-географическому районированию.'));
  },
  pol: function (box) {
    box.appendChild(head('Федеральные округа'));
    Object.keys(FD).forEach(function (code) {
      box.appendChild(keyRow('', 'background:var(--fd-' + FD[code][1] + ')',
                             FD[code][0], code));
    });
    box.appendChild(head('Обозначения'));
    box.appendChild(keyRow('dot', 'background:var(--warn);border-color:var(--m-halo)',
      'Города федерального значения'));
    box.appendChild(keyRow('dot', 'background:var(--m-halo);border-color:var(--m-label)',
      'Столицы субъектов'));
    box.appendChild(keyRow('', 'background-image:repeating-linear-gradient(45deg,' +
      'var(--text-dim) 0 2px,transparent 2px 5px)', 'Спорный статус'));
    box.appendChild(note('Подписи субъектов и столиц появляются по мере приближения: на общем виде показаны только крупнейшие.'));
  }
};

/* ══════════════════════════════════════════════════════════════════
   Вкладки
   ══════════════════════════════════════════════════════════════════ */
var TITLES = { hydro: 'Гидрография', oro: 'Орография', pol: 'Политическая карта' };
var PLACEHOLDER = {
  hydro: 'Река, озеро, море…',
  oro: 'Хребет, равнина, плато…',
  pol: 'Субъект или столица…'
};

function activate(key) {
  var sc = scenes[key];
  if (!sc.built) { sc.build(); sc.built = true; }

  if (current && current !== sc) current.root.style.display = 'none';
  sc.root.style.display = '';
  current = sc;

  legendTitle.textContent = TITLES[key];
  legendBody.innerHTML = '';
  LEGENDS[key](legendBody);

  qEl.placeholder = PLACEHOLDER[key];
  qEl.value = '';
  findEl.classList.remove('filled');
  hits = [];
  renderHits();

  select(null);
  lastRel = -1;
  sc.applyTiers(pz.getScale() / fit);
  onZoom();
}

var tabPhys = $('tab-phys'), tabPol = $('tab-pol');
var subwrap = $('subwrap'), subtabs = $('subtabs');
var mode = 'pol', subMode = 'hydro';

/* Бегунок сегментированного переключателя двигаем через CSS-переменные
   родителя: одна запись вместо перебора кнопок. */
function slide(seg) {
  var on = seg.querySelector('[aria-selected="true"]');
  if (!on) return;
  var a = seg.getBoundingClientRect(), b = on.getBoundingClientRect();
  if (!b.width) return;              /* переключатель ещё свёрнут */
  var cs = getComputedStyle(seg);
  /* Бегунок позиционирован от внутреннего края рамки, поэтому вычитаем
     и рамку, и отступ — иначе он уедет на их сумму. */
  var zero = a.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
  seg.style.setProperty('--seg-w', b.width + 'px');
  seg.style.setProperty('--seg-x', (b.left - zero) + 'px');
}

function syncTabs() {
  tabPhys.setAttribute('aria-selected', mode === 'phys' ? 'true' : 'false');
  tabPol.setAttribute('aria-selected', mode === 'pol' ? 'true' : 'false');
  subwrap.classList.toggle('on', mode === 'phys');
  Array.prototype.forEach.call(subtabs.children, function (b) {
    b.setAttribute('aria-selected', b.dataset.sub === subMode ? 'true' : 'false');
  });
  slide($('tabs'));
  /* Подвкладки разворачиваются анимацией — бегунок ставим после неё,
     иначе он считает ширину по нулю. */
  setTimeout(function () { slide(subtabs); }, mode === 'phys' ? 360 : 0);
  activate(mode === 'pol' ? 'pol' : subMode);
}

$('tabs').addEventListener('click', function (e) {
  var b = e.target.closest('button');
  if (!b || b.dataset.tab === mode) return;
  mode = b.dataset.tab;
  syncTabs();
});

subtabs.addEventListener('click', function (e) {
  var b = e.target.closest('button');
  if (!b || b.dataset.sub === subMode) return;
  subMode = b.dataset.sub;
  syncTabs();
});

/* Стрелки внутри группы вкладок — как того ждут от role="tablist". */
function arrowNav(seg, apply) {
  seg.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    var bs = Array.prototype.slice.call(seg.querySelectorAll('button'));
    var i = bs.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    var n = bs[(i + (e.key === 'ArrowRight' ? 1 : bs.length - 1)) % bs.length];
    n.focus();
    apply(n);
  });
}
arrowNav($('tabs'), function (b) { mode = b.dataset.tab; syncTabs(); });
arrowNav(subtabs, function (b) { subMode = b.dataset.sub; syncTabs(); });

/* ══════════════════════════════════════════════════════════════════
   Тема, легенда, клавиатура
   ══════════════════════════════════════════════════════════════════ */
$('theme').onclick = function () {
  var t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('atlas-theme', t); } catch (e) {}
  var m = document.querySelector('meta[name="theme-color"]');
  if (m) m.setAttribute('content', t === 'dark' ? '#080c14' : '#eef2f8');
};

var fold = $('legend-fold');

function foldLegend(state) {
  legend.classList.toggle('folded', state);
  fold.setAttribute('aria-expanded', state ? 'false' : 'true');
  fold.setAttribute('aria-label', state ? 'Развернуть легенду' : 'Свернуть легенду');
  /* Свёрнутая легенда освобождает левый край. Если карта показана
     целиком — пересобираем посадку, иначе она осталась бы прижатой
     вправо. Приближённый вид не трогаем: человек смотрит на место,
     которое сам выбрал. */
  if (Math.abs(pz.getScale() - fit) < fit * 0.02) resetView(true);
  else computeFit();
}

fold.onclick = function () { foldLegend(!legend.classList.contains('folded')); };

/* На узком экране легенда стартует свёрнутой: карте нужнее место. */
if (innerWidth < 620) foldLegend(true);

addEventListener('keydown', function (e) {
  var typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName);
  if (e.key === 'Escape') { select(null); hideTip(); return; }
  if (typing) return;
  if (e.key === '+' || e.key === '=') { pz.zoomIn({ animate: true }); onZoom(); }
  else if (e.key === '-' || e.key === '_') { pz.zoomOut({ animate: true }); onZoom(); }
  else if (e.key === '0') { resetView(true); }
  else if (e.key === '/') { e.preventDefault(); qEl.focus(); }
});

/* ══════════════════════════════════════════════════════════════════
   Старт
   ══════════════════════════════════════════════════════════════════ */
computeFit();
syncTabs();
resetView(false);
requestAnimationFrame(function () {
  slide($('tabs'));
  boot.classList.add('off');
  setTimeout(function () { boot.remove(); }, 450);
});

})();
