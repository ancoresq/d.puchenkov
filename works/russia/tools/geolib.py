# -*- coding: utf-8 -*-
"""
Общая библиотека сборки карт: проекция, упрощение, генерация путей.

Проекция — равновеликая коническая Альберса. Для России это стандартный
выбор: страна вытянута по широте, конус с двумя стандартными параллелями
даёт минимальные искажения площадей и узнаваемый «веер» очертаний.
"""
import math, json

# --- Параметры проекции ------------------------------------------------
LON0 = 100.0     # центральный меридиан (примерно середина страны)
LAT0 = 56.0      # широта начала координат
SP1, SP2 = 50.0, 70.0   # стандартные параллели

_r = math.radians
_n = (math.sin(_r(SP1)) + math.sin(_r(SP2))) / 2.0
_C = math.cos(_r(SP1)) ** 2 + 2 * _n * math.sin(_r(SP1))
_rho0 = math.sqrt(_C - 2 * _n * math.sin(_r(LAT0))) / _n


def albers(lon, lat):
    """Географические координаты -> плоские (безразмерные, y вниз)."""
    # Нормализуем долготу относительно центрального меридиана в (-180,180].
    # Без этого Чукотка, лежащая за 180-м, улетает на другой край карты.
    dl = (lon - LON0 + 180.0) % 360.0 - 180.0
    lat = max(-89.9, min(89.9, lat))
    v = _C - 2 * _n * math.sin(_r(lat))
    if v < 0:
        v = 0.0
    rho = math.sqrt(v) / _n
    th = _n * _r(dl)
    # У Альберса ось y смотрит на север, у SVG — вниз. Знак меняем здесь,
    # один раз, чтобы дальше по конвейеру о нём можно было не помнить.
    return rho * math.sin(th), -(_rho0 - rho * math.cos(th))


# --- Упрощение ---------------------------------------------------------
def dp(pts, eps):
    """Дуглас-Пойкер. Итеративный, чтобы не упереться в лимит рекурсии
    на кольцах в десятки тысяч точек."""
    n = len(pts)
    if n < 3:
        return pts[:]
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    e2 = eps * eps
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        dd = dx * dx + dy * dy
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if dd == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / dd
                t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d2 > best:
                best, bi = d2, i
        if best > e2:
            keep[bi] = True
            stack.append((a, bi))
            stack.append((bi, b))
    return [pts[i] for i in range(n) if keep[i]]


def ring_area(pts):
    """Ориентированная площадь кольца (знак = направление обхода)."""
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def centroid(pts):
    """Центроид кольца. Для меток надёжнее среднего арифметического точек:
    последнее уезжает туда, где вершины гуще."""
    a = ring_area(pts)
    if abs(a) < 1e-12:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    cx = cy = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        cr = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    return cx / (6 * a), cy / (6 * a)


MAX_DL = 115.0   # предел удаления от центрального меридиана, градусы


def lon_ok(ring):
    """Лежит ли кольцо в полосе долгот, которую честно рисует конус.

    За пределами полосы проекция Альберса заворачивается: у меридиана
    340° при центральном 100° угол выходит за прямой, и Северная
    Америка складывается поверх Арктики тонкими полосами. Такие кольца
    нужно отбрасывать до проекции — после неё они уже неотличимы от
    настоящей геометрии.
    """
    for c in ring:
        dl = (c[0] - LON0 + 180.0) % 360.0 - 180.0
        if dl < -MAX_DL or dl > MAX_DL:
            return False
    return True


def clip_rect(pts, x0, y0, x1, y1):
    """Отсечение многоугольника прямоугольником (Сазерленд — Ходжмен).

    Нужно подписям морей: у Чёрного и Берингова центр тяжести полигона
    лежит за краем холста, и подпись просто не рисовалась бы. Считаем
    центр видимой части, а не всей акватории.
    """
    def inside(p, edge):
        return (p[0] >= x0 if edge == 0 else
                p[0] <= x1 if edge == 1 else
                p[1] >= y0 if edge == 2 else p[1] <= y1)

    def cross(a, b, edge):
        if edge in (0, 1):
            xe = x0 if edge == 0 else x1
            t = (xe - a[0]) / (b[0] - a[0])
            return (xe, a[1] + t * (b[1] - a[1]))
        ye = y0 if edge == 2 else y1
        t = (ye - a[1]) / (b[1] - a[1])
        return (a[0] + t * (b[0] - a[0]), ye)

    out = pts[:]
    for edge in range(4):
        if not out:
            return []
        src, out = out, []
        prev = src[-1]
        for cur in src:
            ci, pi = inside(cur, edge), inside(prev, edge)
            if ci:
                if not pi:
                    out.append(cross(prev, cur, edge))
                out.append(cur)
            elif pi:
                out.append(cross(prev, cur, edge))
            prev = cur
    return out


def chaikin(pts, iters=2):
    """Скругление углов по Чайкину. Нужно ручным контурам: заданные
    десятком вершин ареалы иначе выглядят как строгие многоугольники,
    чего в природе не бывает. Новых подробностей метод не выдумывает —
    только срезает углы."""
    for _ in range(iters):
        out = []
        n = len(pts)
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            out.append((x1 * 0.75 + x2 * 0.25, y1 * 0.75 + y2 * 0.25))
            out.append((x1 * 0.25 + x2 * 0.75, y1 * 0.25 + y2 * 0.75))
        pts = out
    return pts


def principal_angle(pts):
    """Угол главной оси кольца в градусах. Нужен, чтобы подписи хребтов
    ложились вдоль хребта, как на бумажных картах."""
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = syy = sxy = 0.0
    for x, y in pts:
        dx, dy = x - mx, y - my
        sxx += dx * dx
        syy += dy * dy
        sxy += dx * dy
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    d = math.degrees(ang)
    # Держим подпись читаемой: никогда не переворачиваем текст вверх ногами.
    while d > 90:
        d -= 180
    while d < -90:
        d += 180
    return d


# --- Геометрия GeoJSON -------------------------------------------------
def polys(geom):
    """Любая полигональная геометрия -> список списков колец."""
    t = geom['type']
    if t == 'Polygon':
        return [geom['coordinates']]
    if t == 'MultiPolygon':
        return geom['coordinates']
    return []


def lines(geom):
    t = geom['type']
    if t == 'LineString':
        return [geom['coordinates']]
    if t == 'MultiLineString':
        return geom['coordinates']
    return []


class Fitter:
    """Переводит плоские координаты проекции в пиксели холста."""

    def __init__(self):
        self.k = 1.0
        self.dx = self.dy = 0.0

    def fit(self, bbox, width, pad):
        x0, y0, x1, y1 = bbox
        self.k = (width - 2 * pad) / (x1 - x0)
        self.dx = pad - x0 * self.k
        self.dy = pad - y0 * self.k
        self.w = width
        self.h = round((y1 - y0) * self.k + 2 * pad)
        return self

    def px(self, x, y):
        return x * self.k + self.dx, y * self.k + self.dy


def project_ring(coords, fitter):
    out = []
    last = None
    for c in coords:
        x, y = fitter.px(*albers(c[0], c[1]))
        # Точки, совпадающие после округления, только раздувают файл.
        if last is not None and abs(x - last[0]) < 0.01 and abs(y - last[1]) < 0.01:
            continue
        out.append((x, y))
        last = (x, y)
    return out


def fmt(v):
    """Округление до 0.1 px. На карте шириной 2600 px это незаметно,
    а размер файла режет почти вдвое против полной точности."""
    s = f'{v:.1f}'
    if s.endswith('.0'):
        s = s[:-2]
    return s


def path_of_rings(rings, close=True):
    out = []
    for r in rings:
        if len(r) < 2:
            continue
        seg = ['M', fmt(r[0][0]), ',', fmt(r[0][1]), 'L']
        seg.append(','.join(fmt(p[0]) + ' ' + fmt(p[1]) for p in r[1:]))
        if close:
            seg.append('Z')
        out.append(''.join(seg))
    return ''.join(out)


def load(name):
    """Читает исходник Natural Earth из ./geo рядом со сборщиком."""
    import os
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'geo')
    with open(os.path.join(base, name), encoding='utf-8') as f:
        return json.load(f)
