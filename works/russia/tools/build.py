# -*- coding: utf-8 -*-
"""
Сборка данных карт: GeoJSON Natural Earth -> компактные JS-файлы с
готовыми path-строками для Raphael.

Запускается один раз при обновлении данных, в браузер попадает только
результат. Так страница не тянет 80 МБ геометрии и не считает проекцию
на клиенте.
"""
import json, math, os, sys, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geolib import (albers, dp, ring_area, centroid, principal_angle, polys,
                    lines, Fitter, project_ring, path_of_rings, fmt, load,
                    chaikin, clip_rect, lon_ok)
from regions_table import REGIONS, CAPITAL_FIX

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
os.makedirs(OUT, exist_ok=True)

WIDTH = 2600      # ширина холста в «мировых» единицах карты
PAD = 40

# ══════════════════════════════════════════════════════════════════════
# 1. Политическая карта
# ══════════════════════════════════════════════════════════════════════
adm = load('ne_10m_admin_1.geojson')
by_code = {f['properties']['adm1_code']: f for f in adm['features']}

missing = [c for c, *_ in REGIONS if c not in by_code]
if missing:
    sys.exit('нет геометрии для: ' + ', '.join(missing))

# Общий охват карты считаем по субъектам РФ: рамка должна обнимать
# страну, а не Евразию целиком.
bx0 = by0 = 1e9
bx1 = by1 = -1e9
for code, *_ in REGIONS:
    for poly in polys(by_code[code]['geometry']):
        for x, y in poly[0]:
            px, py = albers(x, y)
            bx0, by0 = min(bx0, px), min(by0, py)
            bx1, by1 = max(bx1, px), max(by1, py)

FIT = Fitter().fit((bx0, by0, bx1, by1), WIDTH, PAD)
HEIGHT = FIT.h
print(f'холст: {WIDTH} x {HEIGHT}')


def rings_to_path(geom, eps, min_area, keep_largest=True):
    """Геометрия GeoJSON -> (path, суммарная площадь, крупнейшее кольцо)."""
    out, total, best, best_a = [], 0.0, None, 0.0
    for poly in polys(geom):
        for ri, ring in enumerate(poly):
            if not lon_ok(ring):
                continue
            pts = project_ring(ring, FIT)
            if len(pts) < 4:
                continue
            a = abs(ring_area(pts))
            if ri == 0 and a > best_a:
                best_a, best = a, pts
            # Мелкие острова и озёра-дырки при таком масштабе всё равно
            # схлопываются в точку, зато утяжеляют файл на десятки процентов.
            if a < min_area:
                continue
            s = dp(pts, eps)
            if len(s) < 4:
                continue
            out.append(s)
            total += a if ri == 0 else -a
    if not out and best is not None and keep_largest:
        out = [dp(best, eps)]
        total = best_a
    return path_of_rings(out), total, best


# --- координаты столиц -------------------------------------------------
places = load('ne_10m_populated_places.geojson')
place_xy = {}
for f in places['features']:
    q = f['properties']
    if q.get('ADM0_A3') != 'RUS':
        continue
    nm = (q.get('NAME_RU') or '').strip()
    if nm and nm not in place_xy:
        place_xy[nm] = tuple(f['geometry']['coordinates'][:2])

regions_js, no_cap = [], []
for code, name, short, typ, cap, fd, disputed in REGIONS:
    feat = by_code[code]
    path, area, big = rings_to_path(feat['geometry'], 0.7, 3.0)

    q = feat['properties']
    lx, ly = FIT.px(*albers(q['longitude'], q['latitude']))

    ll = CAPITAL_FIX.get(cap) or place_xy.get(cap)
    if ll is None:
        no_cap.append(cap)
        cx, cy = lx, ly
    else:
        cx, cy = FIT.px(*albers(ll[0], ll[1]))

    regions_js.append({
        # adm1_code в выдачу не кладём: странице он не нужен, это
        # служебный ключ связки со справочником, который живёт только
        # здесь, в сборке.
        #
        # Ключ признака штриховки — 'dis', а не 'd'. Короткое 'd'
        # у всех остальных слоёв означает строку SVG-пути, и общая
        # проверка вида feat.d срабатывала на каждой реке и горе разом.
        'n': name, 's': short, 't': typ, 'cap': cap, 'fd': fd,
        'dis': disputed, 'a': round(area),
        'lx': round(lx, 1), 'ly': round(ly, 1),
        'cx': round(cx, 1), 'cy': round(cy, 1),
        'p': path,
    })

if no_cap:
    print('!! столицы без координат:', no_cap)
print('субъектов:', len(regions_js))

# ══════════════════════════════════════════════════════════════════════
# 2. Общая подложка: контур России и соседние страны
# ══════════════════════════════════════════════════════════════════════
countries = load('ne_50m_admin_0_countries.geojson')
ru_path = ''
neigh = []
# Рамка видимости с запасом. Запас щедрый не от лени: холст SVG не
# обрезает содержимое, и суша соседей должна доходить до краёв окна на
# общем виде, иначе у карты появляется видимый прямоугольный шов.
VX0, VY0, VX1, VY1 = -PAD * 16, -PAD * 16, WIDTH + PAD * 16, HEIGHT + PAD * 16

for f in countries['features']:
    q = f['properties']
    a3 = q.get('ADM0_A3') or q.get('adm0_a3')
    rings = []
    for poly in polys(f['geometry']):
        for ri, ring in enumerate(poly):
            if not lon_ok(ring):
                continue
            pts = project_ring(ring, FIT)
            if len(pts) < 4:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            if max(xs) < VX0 or min(xs) > VX1 or max(ys) < VY0 or min(ys) > VY1:
                continue
            if abs(ring_area(pts)) < 6:
                continue
            s = dp(pts, 1.2 if a3 != 'RUS' else 0.7)
            if len(s) >= 4:
                rings.append(s)
    if not rings:
        continue
    if a3 == 'RUS':
        ru_path = path_of_rings(rings)
    else:
        neigh.append(path_of_rings(rings))

# ══════════════════════════════════════════════════════════════════════
# 3. Гидрография
# ══════════════════════════════════════════════════════════════════════
# Natural Earth хранит названия рек только по-английски — переводим
# вручную. Заодно это фильтр: в карту попадают лишь те реки, которые мы
# осознанно решили подписать.
RIVERS_RU = {
    'Volga': 'Волга', 'Ob': 'Обь', 'Yenisey': 'Енисей', 'Lena': 'Лена',
    'Amur': 'Амур', 'Irtysh': 'Иртыш', 'Ertis': 'Иртыш', 'Angara': 'Ангара',
    'Kama': 'Кама', 'Oka': 'Ока', 'Don': 'Дон', 'Pechora': 'Печора',
    'Severnaya Dvina': 'Северная Двина', 'Sukhona': 'Сухона',
    'Vychegda': 'Вычегда', 'Mezen': 'Мезень', 'Onega': 'Онега',
    'Kolyma': 'Колыма', 'Indigirka': 'Индигирка', 'Yana': 'Яна',
    'Olenëk': 'Оленёк', 'Olenek': 'Оленёк', 'Khatanga': 'Хатанга',
    'Kheta': 'Хета', 'Anabar': 'Анабар', 'Aldan': 'Алдан',
    'Vilyuy': 'Вилюй', 'Vitim': 'Витим', 'Olëkma': 'Олёкма',
    'Olekma': 'Олёкма', 'Lower Tunguska': 'Нижняя Тунгуска',
    'Stony Tunguska': 'Подкаменная Тунгуска',
    'Podkamennaya Tunguska': 'Подкаменная Тунгуска',
    'Selenge (Selenga)': 'Селенга', 'Selenga': 'Селенга',
    'Argun’': 'Аргунь', 'Shilka': 'Шилка', 'Zeya': 'Зея', 'Bureya': 'Бурея',
    'Ussuri': 'Уссури', 'Amgun': 'Амгунь', 'Anadyr’': 'Анадырь',
    'Kamchatka': 'Камчатка', 'Tobol': 'Тобол', 'Ishim': 'Ишим',
    'Tom’': 'Томь', 'Chulym': 'Чулым', 'Ket': 'Кеть', 'Taz': 'Таз',
    'Pur': 'Пур', 'Nadym': 'Надым', 'Biya': 'Бия', 'Katun': 'Катунь',
    'Ural': 'Урал', 'Belaya': 'Белая', 'Vyatka': 'Вятка',
    'Klyazma': 'Клязьма', 'Moscow': 'Москва', 'Neva': 'Нева',
    'Svir’': 'Свирь', 'Volkhov': 'Волхов', 'Kuban': 'Кубань',
    'Terek': 'Терек', 'Samur': 'Самур', 'Khoper': 'Хопёр',
    'Medveditsa': 'Медведица', 'Samara': 'Самара', 'Sura': 'Сура',
    'Vetluga': 'Ветлуга', 'Unzha': 'Унжа', 'Nizhnyaya Tunguska': 'Нижняя Тунгуска',
    'Amga': 'Амга', 'Maya': 'Мая', 'Uda': 'Уда', 'Nizhnyaya Angara': 'Верхняя Ангара',
    'Konda': 'Конда', 'Sosva': 'Северная Сосьва', 'Tura': 'Тура',
    'Iset': 'Исеть', 'Chusovaya': 'Чусовая', 'Yaya': 'Яя',
    'Alazeya': 'Алазея', 'Omolon': 'Омолон', 'Penzhina': 'Пенжина',
    'Markha': 'Марха', 'Tunguska': 'Тунгуска', 'Ilim': 'Илим',
    'Nizhnyaya Tunguska ': 'Нижняя Тунгуска',
}
# Реки, которые уместно подписать крупно: они формируют «скелет» карты.
MAJOR = {'Волга', 'Обь', 'Енисей', 'Лена', 'Амур', 'Иртыш', 'Ангара',
         'Кама', 'Дон', 'Печора', 'Северная Двина', 'Колыма', 'Урал',
         'Индигирка', 'Алдан', 'Вилюй', 'Ока', 'Яна'}

riv = load('ne_10m_rivers_lake_centerlines.geojson')
rivers = {}
for f in riv['features']:
    q = f['properties']
    nm = (q.get('name') or '').strip()
    ru = RIVERS_RU.get(nm)
    if not ru:
        continue
    for ls in lines(f['geometry']):
        if not lon_ok(ls):
            continue
        pts = project_ring(ls, FIT)
        if len(pts) < 2:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) < 0 or min(xs) > WIDTH or max(ys) < 0 or min(ys) > HEIGHT:
            continue
        s = dp(pts, 0.6)
        rivers.setdefault(ru, []).append(s)


def seg_len(s):
    return sum(math.dist(s[i], s[i + 1]) for i in range(len(s) - 1))


def cluster(segs, gap=25.0):
    """Разбивает звенья одного имени на связные реки.

    Имена в Natural Earth не уникальны: Ока впадает и в Волгу, и в
    Ангару, Самара есть на Волге и в Западной Сибири. Без разбора обе
    склеивались бы в одну реку, и подпись садилась бы посередине —
    то есть неизвестно куда. Считаем звенья одной рекой, если их концы
    сходятся ближе чем на gap.
    """
    parent = list(range(len(segs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ends = [(s[0], s[-1]) for s in segs]
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if min(math.dist(a, b) for a in ends[i] for b in ends[j]) <= gap:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    groups = {}
    for i in range(len(segs)):
        groups.setdefault(find(i), []).append(segs[i])
    return list(groups.values())


rivers_js = []
for ru, segs in rivers.items():
    parts = cluster(segs)
    # Одноимённые мелкие протоки подписывать незачем: оставляем те русла,
    # что тянут хотя бы на пятую часть длины главного.
    parts.sort(key=lambda g: -sum(seg_len(s) for s in g))
    top = sum(seg_len(s) for s in parts[0])
    for g in parts:
        L = sum(seg_len(s) for s in g)
        if L < max(30.0, top * 0.2):
            continue
        # Подпись ставим на середину самого длинного звена и разворачиваем
        # вдоль русла — так её читают, не поворачивая голову.
        seg = max(g, key=seg_len)
        m = len(seg) // 2
        a, b = seg[max(0, m - 1)], seg[min(len(seg) - 1, m + 1)]
        ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        if ang > 90:
            ang -= 180
        if ang < -90:
            ang += 180
        rivers_js.append({
            'n': ru, 'r': 1 if ru in MAJOR else 2,
            'lx': round(seg[m][0], 1), 'ly': round(seg[m][1], 1),
            'a': round(ang, 1), 'len': round(L),
            'd': path_of_rings(g, close=False),
        })
rivers_js.sort(key=lambda r: -r['len'])
print('русел:', len(rivers_js))

# --- озёра и водохранилища --------------------------------------------
# Каспий рисуем не здесь: он попадает в слой морей.
LAKE_SKIP = {'Каспийское море', 'Большое Аральское море',
             'Малое Аральское море'}
lak = load('ne_10m_lakes.geojson')
lakes_js = []
for f in lak['features']:
    q = f['properties']
    ru = (q.get('name_ru') or '').strip()
    en = (q.get('name') or '')
    if ru in LAKE_SKIP:
        continue
    path, area, big = rings_to_path(f['geometry'], 0.5, 1.0)
    if not path or big is None:
        continue
    cx, cy = centroid(big)
    if not (0 <= cx <= WIDTH and 0 <= cy <= HEIGHT):
        continue
    # Безымянную мелочь отбрасываем жёстче: без подписи от неё только шум
    # и лишние килобайты. Карелию и Западную Сибирь это не обедняет —
    # там крупных озёр хватает.
    if area < (4 if ru else 30):
        continue
    is_res = ('Reservoir' in en or 'водохранилище' in ru
              or q.get('featurecla') == 'Reservoir')
    lakes_js.append({
        'n': ru, 't': 'res' if is_res else 'lake', 'a': round(area),
        'lx': round(cx, 1), 'ly': round(cy, 1), 'd': path,
    })
lakes_js.sort(key=lambda l: -l['a'])
print('озёр:', len(lakes_js), '(с названием:',
      sum(1 for l in lakes_js if l['n']), ')')

# --- моря, заливы, проливы --------------------------------------------
# Здесь у Natural Earth русские названия уже есть, переводить нечего.
# Полигоны морей в карту не идут: воду даёт фон, а поверх ложится суша.
# От морей нужны только точки подписей.
mar = load('ne_10m_geography_marine_polys.geojson')
SEA_TYPES = {'sea': 'sea', 'gulf': 'bay', 'bay': 'bay', 'strait': 'strait',
             'sound': 'strait', 'channel': 'strait', 'lagoon': 'bay',
             'fjord': 'bay'}
seas_js = []
for f in mar['features']:
    q = f['properties']
    ru = (q.get('name_ru') or '').strip()
    kind = SEA_TYPES.get(q.get('featurecla') or '')
    if not ru or not kind:
        continue
    path, area, big = rings_to_path(f['geometry'], 2.0, 20.0)
    if big is None:
        continue
    vis = clip_rect(big, 70, 40, WIDTH - 70, HEIGHT - 40)
    if len(vis) < 3:
        continue
    va = abs(ring_area(vis))
    if va < 25:            # от акватории видна одна кромка — подписывать нечего
        continue
    cx, cy = centroid(vis)
    seas_js.append({'n': ru, 't': kind, 'a': round(va),
                    'lx': round(cx, 1), 'ly': round(cy, 1)})

# Океаны в наборе морей отсутствуют — Natural Earth считает их слишком
# крупными, чтобы рисовать полигоном. Ставим подписи вручную.
for ru, lon, lat in (('СЕВЕРНЫЙ ЛЕДОВИТЫЙ ОКЕАН', 105.0, 82.5),
                     ('ТИХИЙ ОКЕАН', 168.0, 46.0)):
    ox, oy = FIT.px(*albers(lon, lat))
    seas_js.append({'n': ru, 't': 'ocean', 'a': 10 ** 6,
                    'lx': round(ox, 1), 'ly': round(oy, 1)})
seas_js.sort(key=lambda s: -s['a'])
print('морей и заливов:', len(seas_js))

# ══════════════════════════════════════════════════════════════════════
# 4. Орография
# ══════════════════════════════════════════════════════════════════════
# Ключ — английское имя NE, значение — (русское имя, тип). Тип задаёт
# и цвет заливки, и раздел легенды.
ORO = {
    # горные системы и хребты
    'URAL MOUNTAINS':        ('Уральские горы', 'range'),
    'CAUCASUS MTS.':         ('Кавказские горы', 'range'),
    'ALTAY MOUNTAINS':       ('Алтай', 'range'),
    'EASTERN SAYAN MTS.':    ('Восточный Саян', 'range'),
    'Western Sayan Mts.':    ('Западный Саян', 'range'),
    'Abakanskiy Ra.':        ('Абаканский хребет', 'range'),
    'Tannu-Ola Ra.':         ('хребет Танну-Ола', 'range'),
    'YABLONOVYY RANGE':      ('Яблоновый хребет', 'range'),
    'STANOVOY RANGE':        ('Становой хребет', 'range'),
    'VERKHOYANSK RANGE':     ('Верхоянский хребет', 'range'),
    'CHERSKIY RANGE':        ('хребет Черского', 'range'),
    'Momskiy Mts.':          ('Момский хребет', 'range'),
    'Suntar-Khayata Range':  ('Сунтар-Хаята', 'range'),
    'Dzhugdzhur Range':      ('хребет Джугджур', 'range'),
    'SIKHOTE-ALIN’ RANGE':   ('Сихотэ-Алинь', 'range'),
    'Bureinskiy Ra.':        ('Буреинский хребет', 'range'),
    'Yam Alin’ Range':       ('хребет Ям-Алинь', 'range'),
    'Lesser Khingan Range':  ('Малый Хинган', 'range'),
    'SREDINNYY RANGE':       ('Срединный хребет', 'range'),
    'Koryak Range':          ('Корякское нагорье', 'range'),
    'KOLYMA RANGE':          ('Колымское нагорье', 'range'),
    'Chukchi Range':         ('Чукотское нагорье', 'range'),
    'Lesser Caucasus':       ('Малый Кавказ', 'range'),
    # плато и нагорья
    'CENTRAL SIBERIAN PLATEAU': ('Среднесибирское плоскогорье', 'plateau'),
    'PUTORANA PLATEAU':      ('плато Путорана', 'plateau'),
    'STANOVOY UPLAND':       ('Становое нагорье', 'plateau'),
    'Aldan Upland':          ('Алданское нагорье', 'plateau'),
    'Okhotsk-Kolyma Upland': ('Охотско-Колымское нагорье', 'plateau'),
    'CENTRAL RUSSIAN UPLAND': ('Среднерусская возвышенность', 'upland'),
    'Volga Upland':          ('Приволжская возвышенность', 'upland'),
    'Stavropol’ Plateau':    ('Ставропольская возвышенность', 'upland'),
    'Azov Upland':           ('Приазовская возвышенность', 'upland'),
    # равнины и низменности
    'WESTERN SIBERIAN PLAIN': ('Западно-Сибирская равнина', 'plain'),
    'CASPIAN DEPRESSION':    ('Прикаспийская низменность', 'lowland'),
    'NORTH SIBERIAN LOWLAND': ('Северо-Сибирская низменность', 'lowland'),
    'KOLYMA LOWLAND':        ('Колымская низменность', 'lowland'),
    'VYCHEGDA LOWLAND':      ('Мезенско-Вычегодская низменность', 'lowland'),
    'Bol’shezemel’skaya Tundra': ('Большеземельская тундра', 'lowland'),
    # полуострова и прочие крупные формы рельефа
    'KAMCHATKA PENINSULA':   ('полуостров Камчатка', 'pen'),
    'TAYMYR PENINSULA':      ('полуостров Таймыр', 'pen'),
    'KOLA PENINSULA':        ('Кольский полуостров', 'pen'),
    'YAMAL PENINSULA':       ('полуостров Ямал', 'pen'),
    'GYDA PENINSULA':        ('Гыданский полуостров', 'pen'),
    'Taz Pen.':              ('Тазовский полуостров', 'pen'),
    'Kanin Pen.':            ('полуостров Канин', 'pen'),
    'CRIMEA':                ('Крымский полуостров', 'pen'),
}
# У Natural Earth нет ряда форм рельефа, без которых карта России
# выглядит дырявой: Русской равнины (в наборе есть только вся
# Среднеевропейская целиком, от Германии), Тиманского кряжа, Сибирских
# Увалов, Хибин и других. Контуры ниже нарисованы вручную по школьному
# физико-географическому районированию — это генерализованные границы
# ареалов, а не точные изолинии высот. Для обзорной карты этого хватает,
# для расчётов — нет.
ORO_MANUAL = [
    ('Восточно-Европейская (Русская) равнина', 'plain', [
        (28, 59.5), (30, 63), (34, 66.5), (40, 67.5), (45, 67.5), (50, 67),
        (55, 65), (58, 61), (57.5, 57), (56, 53), (52, 50), (48, 47.5),
        (45, 45.8), (41, 45.2), (37, 46), (33, 47.5), (29, 49.5),
        (24.5, 52), (23.5, 55.5), (25.5, 58)]),
    ('Валдайская возвышенность', 'upland', [
        (31.4, 58.4), (34.2, 58.1), (35.4, 57.1), (34.4, 56.4),
        (32.2, 56.5), (30.8, 57.4)]),
    ('Северные Увалы', 'upland', [
        (43.5, 60.2), (52.5, 59.9), (52.5, 58.5), (43.5, 58.8)]),
    ('Тиманский кряж', 'upland', [
        (48.2, 67.6), (51.2, 67.2), (54.4, 62.2), (52.4, 61.2),
        (49.4, 62.8), (46.2, 66.9)]),
    ('Печорская низменность', 'lowland', [
        (49.5, 68.4), (57.5, 68.2), (60.5, 64.8), (54.5, 63.2),
        (49.5, 64.8), (47.5, 66.8)]),
    ('Мещёрская низменность', 'lowland', [
        (38.4, 56.2), (41.8, 56.0), (41.8, 54.7), (38.6, 54.9)]),
    ('Сибирские Увалы', 'upland', [
        (62, 63.9), (85, 63.2), (85, 61.7), (62, 62.3)]),
    ('Барабинская низменность', 'lowland', [
        (75.4, 56.4), (82.2, 55.9), (82.2, 54.0), (75.4, 54.2)]),
    ('Кузнецкий Алатау', 'range', [
        (87.4, 56.3), (89.3, 55.7), (89.6, 53.4), (88.0, 53.1),
        (86.8, 54.7)]),
    ('Салаирский кряж', 'range', [
        (84.3, 55.0), (86.3, 54.2), (85.6, 52.7), (83.9, 53.6)]),
    ('Витимское плоскогорье', 'plateau', [
        (111.4, 55.6), (116.6, 55.0), (116.0, 51.9), (111.8, 52.5)]),
    ('Юкагирское плоскогорье', 'plateau', [
        (149.5, 66.6), (157.5, 66.0), (157.5, 64.4), (149.5, 65.0)]),
    ('Анадырское плоскогорье', 'plateau', [
        (167.5, 67.3), (176.5, 66.4), (176.5, 64.5), (167.5, 65.2)]),
    ('Хибины', 'range', [
        (33.1, 68.0), (34.3, 67.92), (34.4, 67.5), (33.2, 67.45)]),
]

geo = load('ne_10m_geography_regions_polys.geojson')
oro_js = []
seen = set()
for f in geo['features']:
    q = f['properties']
    nm = q.get('NAME')
    if nm not in ORO or nm in seen:
        continue
    ru, kind = ORO[nm]
    path, area, big = rings_to_path(f['geometry'], 1.0, 8.0)
    if big is None or not path:
        continue
    seen.add(nm)
    cx, cy = centroid(big)
    oro_js.append({
        'n': ru, 't': kind, 'a': round(area),
        'lx': round(cx, 1), 'ly': round(cy, 1),
        'r': round(principal_angle(big), 1),
        'd': path,
    })
for ru, kind, ll in ORO_MANUAL:
    # Скругляем ещё в градусах, до проекции: иначе сглаживание поехало бы
    # вслед за искажениями конуса и на востоке было бы сильнее, чем на западе.
    pts = project_ring([list(p) for p in chaikin(ll, 3)], FIT)
    cx, cy = centroid(pts)
    oro_js.append({
        'n': ru, 't': kind, 'a': round(abs(ring_area(pts))), 'm': 1,
        'lx': round(cx, 1), 'ly': round(cy, 1),
        'r': round(principal_angle(pts), 1),
        'd': path_of_rings([pts]),
    })

oro_js.sort(key=lambda o: -o['a'])
lost = [k for k in ORO if k not in seen]
if lost:
    print('!! не найдено в NE:', lost)
print('форм рельефа:', len(oro_js))

# ══════════════════════════════════════════════════════════════════════
# 5. Градусная сетка
# ══════════════════════════════════════════════════════════════════════
# Меридианы рисуем только в пределах картографируемой полосы долгот.
# Конус Альберса за её краями заворачивается: меридиан 340-й градус
# при центральном 100-м даёт угол больше прямого, и линия ложится
# поперёк карты — как раз те непонятные штрихи над Арктикой.
grat = []
for lon in range(20, 201, 20):
    pts = [FIT.px(*albers(lon, lat)) for lat in range(35, 86)]
    grat.append(dp(pts, 0.5))
for lat in range(40, 85, 10):
    pts = [FIT.px(*albers(lon, lat)) for lon in range(15, 201, 2)]
    grat.append(dp(pts, 0.5))
graticule = path_of_rings(grat, close=False)


# ══════════════════════════════════════════════════════════════════════
def dump(fname, varname, obj, note):
    body = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(OUT, fname), 'w', encoding='utf-8') as f:
        f.write('/* %s\n   Сгенерировано build.py из данных Natural Earth\n'
                '   (public domain). Руками не править — правки затрёт\n'
                '   следующая сборка. */\n' % note)
        f.write('window.%s=%s;\n' % (varname, body))
    print(fname, round(os.path.getsize(os.path.join(OUT, fname)) / 1024), 'КБ')


dump('regions.js', 'RU_REGIONS',
     {'w': WIDTH, 'h': HEIGHT, 'items': regions_js},
     'Политическая карта: 89 субъектов Российской Федерации')

dump('hydro.js', 'RU_HYDRO',
     {'rivers': rivers_js, 'lakes': lakes_js, 'seas': seas_js},
     'Гидрография: реки, озёра, водохранилища, моря')

dump('oro.js', 'RU_ORO', {'items': oro_js},
     'Орография: горы, плоскогорья, возвышенности, низменности')

dump('base.js', 'RU_BASE',
     {'w': WIDTH, 'h': HEIGHT, 'ru': ru_path, 'neighbours': neigh,
      'grat': graticule},
     'Подложка: контур России, соседние страны, градусная сетка')
