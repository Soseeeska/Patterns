package patterns.zones;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * Карта зон ES (ветка Zones-1.1) на чистой Java, без зависимостей от MotiveWave.
 *
 * Порт Python-пайплайна scripts/zone_map_export.py + логики страницы
 * scripts/zone_map_template.html:
 *   1. RTH-дни из 30m-баров, TPO-профиль дня (POC, VA по Далтону), HVN-полоса вокруг POC;
 *   2. композиты close_in_range: ≥ 3 дней — «старый баланс» (VA композита),
 *      2 дня — серая зона (HVN-полоса композита);
 *   3. первородные HVN-зоны (дни с HVN в VA 8–10 TPO), серые — из дней 5–7 TPO;
 *      молодая первородная зона серая, пока формирование + подтверждения < 2 дней;
 *   4. класс HVN-зоны на дату — по отторжениям хвостом на RTH-барах (≥ 60% в одну сторону);
 *   5. подгонка границ по краям тел 30m-свечей за первые N сессий (N = 0 — без подгонки),
 *      перевод серой зоны в основную по отторжениям за эти N сессий;
 *   режим «по композиту» (Params.compositeMode): ядро композита ≥ 3 дней (HVN-полоса вокруг POC
 *      или VA) — проторговка; ядро, пересекающее уже существующее, его подтверждает, новой зоны нет.
 *      HVN- и серые зоны внутри диапазона композита: выше ядра — сопротивление, ниже — поддержка,
 *      на ядре — поглощаются им; вне всех композитов — серые. Правит последний доступный композит;
 *   6. фильтры страницы: типы, «ближайшие к open сессии», отторжения, чистота,
 *      подтверждения, возраст, ширина; серая скрывается, если на > 30% заходит на основную.
 * Все цены профиля — в целых тиках, как в tpo/profile.py.
 */
public final class ZoneEngine {

    // ------------------------------------------------------------------ виды зон
    public static final int K_OLD_BALANCE = 0, K_HVN = 1, K_CORE = 2, K_PART = 3, K_SESS = 4, K_GREY = 5;

    public enum Kind {
        HVN_S("HVN · поддержка"), HVN_R("HVN · сопротивление"), HVN_RANGE("HVN · проторговка"),
        HVN_NEW("HVN · без истории"), OLD_BALANCE("Старый баланс (VA композита)"),
        GREY("Серая (мало истории / узкая)"), CORE("Проторговка композита");
        public final String title;
        Kind(String t) { title = t; }
    }

    // ------------------------------------------------------------------ параметры
    /** Все пороги — как в config.yaml и DEF страницы карты зон. */
    public static final class Params {
        // данные и сессии
        public double tick = 0.25;
        public boolean rollAdjust = true;        // склейка роллов: сырой ряд (MotiveWave) рвётся на смене контракта — цены до ролла
                                                 // сдвигаются на гэп, расчёт идёт в ценах текущего контракта
        public double rollMinGapPts = 20;        // ролл — скачок между соседними 30m-барами не меньше, п. (в окне 10 дней до 3-й пятницы
                                                 // мар / июн / сен / дек; обычный скачок между соседними барами — 1–2 п.)
        public ZoneId tz = ZoneId.of("America/Chicago");
        public int rthStartMin = 8 * 60 + 30, rthEndMin = 15 * 60;   // RTH 08:30–15:00 CT
        public int ethStartMin = 17 * 60;                              // Globex с 17:00 — дата следующего дня
        public int periodMinutes = 30;
        public int fullDayPeriods = 13;
        // профиль
        public double valueAreaPct = 0.70;
        public double hvnShare = 0.70;
        // композиты
        public int compositeMinDays = 3;
        public int greyCompositeMinDays = 2;
        // первородные зоны
        public int hvnMinTpo = 8, hvnMaxTpo = 10;
        public double mergeOverlapMin = 0.50;
        public double mergeMaxWidthPts = 10.0;
        // серые
        public int greyHvnMinTpo = 5, greyHvnMaxTpo = 7;
        public int greyMinHistoryDays = 2;
        public double greyMinWidthPts = 1.0, greyMaxWidthPts = 10.0;
        public double greyMaxOverlap = 0.30;
        // классы
        public double rejectionShare = 0.60;
        // режим «по композиту»
        public boolean compositeMode = false;     // класс зоны по положению в композите, а не по отторжениям
        public boolean coreByVa = false;          // ядро композита: VA (иначе HVN-полоса вокруг POC)
        public double coreAbsorb = 0.30;
        public boolean coreFromSessions = true;   // ядро до обрыва TPO; баланс — если VA дней задевают ядро
        public int coreCliffTpo = 4;              // обрыв: строка на N принтов ниже максимума предыдущего 1 п. (4 тика)
        public boolean compositeShortDays = true;
        public double compFalseBreakMaxPts = 100; // ... если диапазон закрытого композита не шире, п.
        public int compFalseBreakMax = 2;         // ... и не больше стольких продолжений подряд
        public boolean compFalseBreak = true;     // ложный выход: возврат close в диапазон закрытого композита, пока новый
                                                  // не набрал compositeMinDays дней, — дни доклеиваются к нему
        public double compVaOverlap = 0.5;        // VA дня и VA композита пересекаются на ≥ эту долю меньшей из двух (0 — не проверять)
        public boolean compJoinBoth = true;       // склейка: close в диапазоне И пересечение VA (иначе — close в диапазоне ИЛИ пересечение VA);
                                                  // так же и доклейка после ложного выхода
        public double compInsideShare = 0.8;      // ... но день, чей High–Low на ≥ эту долю внутри диапазона композита, клеится по ИЛИ
                                                  // (0 — выкл.)
        public boolean compOutsideDays = true;    // ... и внешний день: диапазон композита на ≥ ту же долю внутри High–Low дня
        public boolean sessionZones = true;       // зоны из узлов RTH TPO каждой сессии, подогнанные по телам / теням свечей;
                                                  // композит только задаёт роль (ядро / выше / ниже) и один раз перекрашивает
        public double sessNodeDrop = 1.0;         // узел — строки не ниже вершины на столько TPO (сглаж. 1 п.)
        public double sessNodeDip = 0.15;         // провал между узлами — от доли меньшей вершины (и ≥ 1 TPO)
        public double sessPeakShare = 0.5;         // узел — вершина не ниже этой доли максимума TPO дня (0 — любая ≥ sessMinPeak)
        public int sessMinPeak = 3;               // вершина узла — не меньше стольких TPO
        public double sessMaxPts = 8;             // зона не шире, п.
        public double sessMinGapPts = 3.0;        // узел ближе этого к существующей зоне (или пересекает её) — её подтверждение
        public boolean sessResyncLater = false;   // зону синхронизирует и более поздний композит, сложившийся на ней (роль — по последнему
                                                  // накрывшему); иначе — только первый композит после её рождения
        public boolean compRollBreak = true;      // ролл разрывает композит: кандидат закрывается на смене контракта (на несклеенном
                                                  // графике баланс до и после ролла — в разных местах), ложный выход через ролл не доклеивает
        public boolean liveComposite = true;      // формирующийся композит: с compositeMinDays дней — со следующей сессии задаёт роли
                                                  // (снимок по уже прошедшим дням); зоны синхронизируются с ним один раз, как с закрытым
        public boolean drawOneColor = true;       // зона рисуется одной полосой: цвет и границы — на последнюю сессию, где она видна
                                                  // (иначе — по участкам, как менялась по сессиям)
        public boolean layoutDropYounger = true;  // раскладка: младшая зона, налезающая на уложенную, не рисуется (а не обрезается впритык)
        public int sessRoleMode = 0;              // роль вне композита: 0 — отторжения, затем закрытие сессии; 1 — только закрытие
        public double sessBodyShare = 0.5;
        public int sessConfirm = 5;               // режим 2: зона рождается серой, через столько полных дней — цвет по отторжениям за них        // подгонка узла по телам: скопление не ниже этой доли максимума тел
        public boolean partition = true;          // разметка композита: тело целиком режется на зоны (ядро / подд. / сопр.)
        public double bodyShare = 0.15;           // тело — строки не ниже этой доли максимума (сглаж. 1 п.), хвосты отпадают
        public double partCoreShare = 0.65;       // ядро — часть тела не ниже этой доли максимума
        public double partDipShare = 0.06;        // провал для нарезки — не мельче этой доли максимума (и не мельче 2 TPO)
        public double partTargetPts = 8, partMaxPts = 10, partMinPts = 2;
        public double partMaxBodyPts = 100;      // тело шире — порог тела поднимается, пока не уложится (остаётся плотная середина)
        public double fitBodyShare = 0.20;        // подгонка по телам: строки, где тел 30m-свечей дней композита меньше этой
                                                  // доли максимума (и меньше 3), — пустота (0 — без подгонки)
        public double fitClusterShare = 0.70;     // зона — скопление тел вокруг вершины: не ниже этой доли её
        public boolean fitEthBodies = true;       // подгонка по телам всех свечей сессии (ETH + RTH), профиль — только RTH
        public double clusterMaxPts = 8;
        public boolean subAddsZones = false;      // уточнения внутри баланса добавляют свои зоны (иначе зоны даёт только главный)          // скопление шире — порог поднимается, остаётся самая плотная часть
        public boolean bodyClusters = true;       // зоны — скопления тел свечей (вершины профиля тел, границы на провалах),
                                                  // без нарезки равными кусками; TPO-профиль задаёт только ядро / выше / ниже
        public double clusterDip = 0.10;          // провал между скоплениями — не мельче этой доли меньшей вершины (и ≥ 1.5 тела)
        public boolean coreNodes = true;          // ядро рисуется узлами профиля (локальные вершины, разделённые провалами)
        public double nodeDipTpo = 2.0;           // провал между узлами — не меньше стольких TPO от обеих вершин (сглаж. 1 п.)
        public double nodeDropTpo = 2.0;          // узел — строки не ниже вершины узла на столько TPO
        public boolean noOverlap = true;          // зоны в сессии не налезают друг на друга: младшие обрезаются
        public double minPiecePts = 1.0;          // обрезанная зона уже этого — не рисуется // режим «по композиту»: короткие дни входят в композиты
        public double subOverlap = 0.70;          // ≥ этой доли диапазона композита внутри раннего баланса — уточнение (0 — выкл.)
        public double subMaxRatio = 2.0;          // ... если тот баланс не шире композита больше чем в столько раз
        public double coreMinDaysShare = 0.5;     // ядро признаётся, если его задевают VA ≥ этой доли дней композита;
                                                  // иначе это не баланс — композит не используется (0 — выкл.)
        public double joinGapPts = 0.25;          // режим «по композиту»: зона того же вида ближе этого к более
                                                  // старой — её подтверждение (ядро не создаётся / не рисуется); < 0 — выкл.
        public double zoneMaxWidthPts = 0;        // > 0: HVN-полоса дня и ядро композита шире — сужаются до окна
                                                  // с POC и максимумом TPO (на странице карты зон лимита нет)          // зона, на > 30% своей ширины лежащая на ядре, поглощается им
        public int minWickTicks = 2;
        // подгонка границ
        public int alignSessions = 0;            // 0 — без подгонки (по умолчанию на странице)
        public double alignSearchFrac = 0.5, alignSearchMinPts = 2.0, alignWPlain = 0.5, alignMinScore = 3;
        public int alignMinWidthTicks = 2;
        public double alignMaxWidthPts = 10.0, alignMaxWidthMult = 1.5;
        public int promoteRejections = 3;        // перевод серой в основную; 0 — не переводить
        // фильтры страницы
        public int sessions = 250;               // сколько последних сессий показывать
        public int nearest = 6;                  // ближайших к open сессии зон; 0 — все
        public int minRejections = 0;
        public int purityMinPct = 0, purityMaxPct = 100;
        public int minConfirmations = 0;
        public double confirmNearPts = 0;        // подтверждение: зазор полосы HVN до зоны ≤ N п.
        public int minAgeDays = 0;
        public double maxWidthPts = 60;          // ≥ 60 — любая
        public final Set<Kind> types = new HashSet<>(Arrays.asList(
                Kind.HVN_S, Kind.HVN_R, Kind.HVN_RANGE, Kind.HVN_NEW, Kind.GREY, Kind.CORE));
        public double confirmStoreMaxPts = 10.0; // MAX_NEAR экспорта
    }

    // ------------------------------------------------------------------ входные бары
    public static final class Bar {
        public final long t;                 // начало бара, epoch ms
        public final double o, h, l, c;
        public Bar(long t, double o, double h, double l, double c) { this.t = t; this.o = o; this.h = h; this.l = l; this.c = c; }
    }

    // ------------------------------------------------------------------ профиль
    static final class Profile {
        final int low; final int[] counts;
        Profile(int low, int[] counts) { this.low = low; this.counts = counts; }
        int high() { return low + counts.length - 1; }
        int max() { int m = 0; for (int v : counts) m = Math.max(m, v); return m; }
    }

    static Profile buildProfile(int[] lows, int[] highs) {
        int lo = Integer.MAX_VALUE, hi = Integer.MIN_VALUE;
        for (int i = 0; i < lows.length; i++) { lo = Math.min(lo, lows[i]); hi = Math.max(hi, highs[i]); }
        int n = hi - lo + 1;
        int[] diff = new int[n + 1];
        for (int i = 0; i < lows.length; i++) { diff[lows[i] - lo]++; diff[highs[i] - lo + 1]--; }
        int[] c = new int[n]; int acc = 0;
        for (int i = 0; i < n; i++) { acc += diff[i]; c[i] = acc; }
        return new Profile(lo, c);
    }

    static Profile merge(Profile a, Profile b) {
        int lo = Math.min(a.low, b.low), hi = Math.max(a.high(), b.high());
        int[] c = new int[hi - lo + 1];
        for (int i = 0; i < a.counts.length; i++) c[a.low - lo + i] += a.counts[i];
        for (int i = 0; i < b.counts.length; i++) c[b.low - lo + i] += b.counts[i];
        return new Profile(lo, c);
    }

    static int pocIndex(Profile p) {
        int[] c = p.counts; int m = p.max();
        double center = (c.length - 1) / 2.0, best = Double.MAX_VALUE; int bi = 0;
        for (int i = 0; i < c.length; i++) {
            if (c[i] != m) continue;
            double d = Math.abs(i - center);
            if (d < best) { best = d; bi = i; }
        }
        return bi;
    }

    /** (poc, val, vah) в тиках — Value Area по Далтону (пары строк). */
    static int[] valueArea(Profile p, double pct) {
        int[] c = p.counts; int n = c.length;
        int poc = pocIndex(p);
        long total = 0; for (int v : c) total += v;
        double target = pct * total;
        int lo = poc, hi = poc; long acc = c[poc];
        while (acc < target && (lo > 0 || hi < n - 1)) {
            long up = hi < n - 1 ? sum(c, hi + 1, Math.min(hi + 3, n)) : -1;
            long dn = lo > 0 ? sum(c, Math.max(lo - 2, 0), lo) : -1;
            if (up >= dn && up >= 0) {
                int nh = Math.min(hi + 2, n - 1);
                acc += sum(c, hi + 1, nh + 1); hi = nh;
                if (up == dn && lo > 0) { int nl = Math.max(lo - 2, 0); acc += sum(c, nl, lo); lo = nl; }
            } else {
                int nl = Math.max(lo - 2, 0); acc += sum(c, nl, lo); lo = nl;
            }
        }
        return new int[]{p.low + poc, p.low + lo, p.low + hi};
    }

    private static long sum(int[] c, int a, int b) { long s = 0; for (int i = a; i < b; i++) s += c[i]; return s; }

    /** HVN-полоса: строки вокруг POC с TPO ≥ share × максимум, непрерывно от POC. */
    static int[] hvnBand(Profile p, int pocTicks, double share) {
        int[] c = p.counts; int i = pocTicks - p.low; double thr = share * p.max();
        int lo = i, hi = i;
        while (lo > 0 && c[lo - 1] >= thr) lo--;
        while (hi < c.length - 1 && c[hi + 1] >= thr) hi++;
        return new int[]{p.low + lo, p.low + hi};
    }

    // ------------------------------------------------------------------ RTH-день
    static final class Day {
        LocalDate date; int n; boolean shortDay;
        double open, high, low, close;
        final List<int[]> periods = new ArrayList<>();   // {period, lowTicks, highTicks, openTicks, closeTicks}
        final List<int[]> bodies = new ArrayList<>();    // тела всех 30m-свечей сессии (ETH + RTH): {open, close} в тиках
        final List<double[]> sbars = new ArrayList<>();  // все 30m-свечи сессии (ETH + RTH): {o, h, l, c}
        Profile profile; int poc, val, vah;              // тики
        int hvnTpo, bandLo, bandHi;                      // per_day (только полные дни)
    }

    // ------------------------------------------------------------------ зона
    public static final class Zone {
        public int k; public double lo, hi; public LocalDate av; public String src;
        public boolean young; public LocalDate greyUntil;   // young && greyUntil == null — серая всё время
        public boolean fixedGrey;                            // ядро композита-уточнения: всегда серая
        public String info = "";                             // ядро: как посчитано (для подсказки)
        public List<Zone> nodes = List.of();                 // ядро: узлы профиля (рисуются вместо ядра)
        public Kind partKind; public Comp partOf;            // кусок разметки композита: вид и композит
        public Kind recolorKind; long recolorAt = Long.MAX_VALUE;   // сессионная зона: роль после (первой) перекраски композитом
        long[] rcAt = new long[0]; Kind[] rcKind = new Kind[0]; Comp[] rcComp = new Comp[0];   // все синхронизации: с сессии, роль, композит
        long hideFrom = Long.MAX_VALUE;                      // разметка: с этого дня зона внутри тела композита не рисуется
        Zone nodeOf;                                         // узел: его ядро
        int formationDays; List<LocalDate> conf = List.of();
        // события (дни от 1970-01-01)
        public long[] L = new long[0], S = new long[0];
        public final List<long[]> cf = new ArrayList<>();   // {ord, gap*100}
        public int ai;                                       // индекс av в списке полных RTH-дней
        public double blo, bhi;                              // границы после подгонки
        long[] coreConf = new long[0];                       // ядро: дни подтверждений другими композитами
        long[] govAv = new long[0]; Comp[] gov = new Comp[0]; // композиты, в диапазон которых попадает зона
        public long avOrd() { return av.toEpochDay(); }
    }

    /** Композит ≥ compositeMinDays: диапазон и его ядро (зона проторговки). */
    public static final class Comp {
        public double lo, hi; public LocalDate start, end, av; public int days; public Zone core;
        public Comp parent;                                  // уточнение внутри более раннего баланса
        public Comp extendsOf;                               // продолжение этого композита после ложного выхода
        public boolean live;                                 // снимок формирующегося композита
        public double bodyLo, bodyHi;                        // тело профиля (без хвостов)
        public double coreLo, coreHi;                        // ядро тела (строки не ниже partCoreShare максимума)
        List<int[]> segs = List.of();                        // разметка тела: {lo, hi, 0 ядро / 1 поддержка / 2 сопротивление}, тики
    }

    /** Состояние зоны на сессию. */
    public static final class State {
        public Comp comp;                                   // режим «по композиту»: правящий композит
        public Kind kind; public boolean promoted; public int rej, nl, ns, conf; public Double purity; public long age;
        public boolean recolored;                           // сессионная зона перекрашена композитом
    }

    public static final class Session {
        public LocalDate date; public long ord; public int firstBar, lastBar; public long startMs, endMs;
        public double open; public Double prevVah, prevVal, prevPoc; public LocalDate prevDate;
    }

    /** Видимая в сессию зона. */
    public static final class Shown {
        public final Zone z; public final State st;
        public double lo, hi;                               // границы на эту сессию (после обрезки пересечений)
        Shown(Zone z, State st) { this.z = z; this.st = st; lo = z.blo; hi = z.bhi; }
    }

    public static final class Result {
        public final List<Zone> zones = new ArrayList<>();
        public final List<Session> sessions = new ArrayList<>();     // показываемые сессии
        public final List<List<Shown>> visible = new ArrayList<>();  // по сессиям
        public int rthDays, fullDays, composites;
        public final List<Comp> comps = new ArrayList<>();
        public final List<Comp> notBalance = new ArrayList<>();
        public final List<Comp> subComps = new ArrayList<>();
        public final List<Comp> liveComps = new ArrayList<>();       // снимки формирующихся композитов (балансы, не уточнения)
        public long[] rollMs = new long[0];                          // роллы: начало первого бара нового контракта, epoch ms
        public double[] rollGap = new double[0];                     // ... и гэп (новый − старый), п.
        public List<Bar> bars = List.of();                           // бары расчёта (после склейки роллов)        // уточнения внутри баланса (ядро — серая зона)      // композиты, где дни не сошлись (без ядра)
        public String note = "";
    }

    // ================================================================== расчёт
    public static Result run(List<Bar> bars, Params p) {
        Result res = new Result();
        double tick = p.tick;
        bars = new ArrayList<>(bars);
        bars.sort((a, b) -> Long.compare(a.t, b.t));
        if (p.rollAdjust) bars = rollAdjust(bars, p, res);
        res.bars = bars;

        // ---------- RTH-дни и сессии
        TreeMap<LocalDate, Day> days = new TreeMap<>();
        LocalDate[] sessOf = new LocalDate[bars.size()];
        boolean[] isRth = new boolean[bars.size()];
        LocalDate[] rthDateOf = new LocalDate[bars.size()];
        for (int i = 0; i < bars.size(); i++) {
            Bar b = bars.get(i);
            LocalDateTime lt = LocalDateTime.ofInstant(Instant.ofEpochMilli(b.t), p.tz);
            int m = lt.getHour() * 60 + lt.getMinute();
            LocalDate d = lt.toLocalDate();
            sessOf[i] = m >= p.ethStartMin ? d.plusDays(1) : d;
            if (m >= p.rthStartMin && m < p.rthEndMin) {
                isRth[i] = true; rthDateOf[i] = d;
                Day day = days.computeIfAbsent(d, x -> { Day y = new Day(); y.date = x; return y; });
                int per = (m - p.rthStartMin) / p.periodMinutes;
                if (day.periods.isEmpty()) { day.open = b.o; day.high = b.h; day.low = b.l; }
                day.high = Math.max(day.high, b.h); day.low = Math.min(day.low, b.l); day.close = b.c;
                day.periods.add(new int[]{per, ticks(b.l, tick), ticks(b.h, tick), ticks(b.o, tick), ticks(b.c, tick)});
            }
        }
        for (int i = 0; i < bars.size(); i++) {           // тела свечей всей сессии — для подгонки по телам
            Day d = days.get(sessOf[i]);
            if (d != null) {
                Bar bb = bars.get(i);
                d.bodies.add(new int[]{ticks(bb.o, tick), ticks(bb.c, tick)});
                d.sbars.add(new double[]{bb.o, bb.h, bb.l, bb.c});
            }
        }
        List<Day> all = new ArrayList<>(days.values());
        List<Day> full = new ArrayList<>();
        for (Day d : all) {
            d.n = d.periods.size();
            d.shortDay = d.n < p.fullDayPeriods;
            int[] lows = new int[d.n], highs = new int[d.n];
            for (int i = 0; i < d.n; i++) { lows[i] = d.periods.get(i)[1]; highs[i] = d.periods.get(i)[2]; }
            d.profile = buildProfile(lows, highs);
            int[] va = valueArea(d.profile, p.valueAreaPct);
            d.poc = va[0]; d.val = va[1]; d.vah = va[2];
            if (!d.shortDay) {
                full.add(d);
                int[] c = d.profile.counts; int mx = 0;
                for (int t = d.val; t <= d.vah; t++) mx = Math.max(mx, c[t - d.profile.low]);
                d.hvnTpo = mx;
                int[] band = narrow(d.profile, hvnBand(d.profile, d.poc, p.hvnShare), d.poc,
                        (int) Math.round(p.zoneMaxWidthPts / tick));
                d.bandLo = band[0]; d.bandHi = band[1];
            }
        }
        res.rthDays = all.size(); res.fullDays = full.size();
        if (full.size() < 3) { res.note = "мало RTH-дней в истории"; return res; }
        Map<LocalDate, LocalDate> nextTradeDay = new HashMap<>();
        for (int i = 0; i + 1 < all.size(); i++) nextTradeDay.put(all.get(i).date, all.get(i + 1).date);

        // ---------- композиты close_in_range
        List<Zone> ob = new ArrayList<>(), greyComp = new ArrayList<>();
        {
            List<Day> cand = new ArrayList<>(); Profile cp = null;
            List<Comp> live = res.liveComps;
            List<Day> lastCand = null; Profile lastCp = null; Comp lastComp = null, extOf = null;
            int extCount = 0, lastExt = 0;                  // сколько раз текущий / закрытый композит продолжался
            // режим «по композиту»: короткие дни (ранние закрытия) тоже входят в композит, как в TPO платформы
            for (Day d : p.compositeMode && p.compositeShortDays ? all : full) {
                if (cand.isEmpty()) { cand.add(d); cp = d.profile; continue; }
                if (p.compositeMode && p.compRollBreak && rollBetween(res, p, cand.get(cand.size() - 1).date, d.date)) {
                    // ролл между последним днём кандидата и этим днём: кандидат закрывается, этот день открывает новый
                    Comp made = closeComposite(cand, cp, d.date, p, ob, greyComp, res.comps, res.notBalance);
                    if (made != null) made.extendsOf = extOf;
                    lastCand = null; lastCp = null; lastComp = made; extOf = null; lastExt = 0; extCount = 0;
                    cand = new ArrayList<>(); cand.add(d); cp = d.profile;
                    continue;
                }
                int c = ticks(d.close, tick);
                if (cand.size() >= p.compositeMinDays) lastCand = null;
                if (p.compositeMode && p.compFalseBreak && lastCand != null && c >= lastCp.low && c <= lastCp.high()
                        && (!p.compJoinBoth || vaJoins(d, lastCp, p) || insideDay(d, lastCp, p))
                        && (lastCp.high() - lastCp.low) * tick <= p.compFalseBreakMaxPts && extCount < p.compFalseBreakMax) {
                    // ложный выход: композит закрылся, новый ещё не набрал дней, а цена закрылась обратно в его
                    // диапазоне — дни после выхода доклеиваются к нему, композит продолжается
                    extCount = lastExt;
                    List<Day> m = new ArrayList<>(lastCand); m.addAll(cand); m.add(d);
                    Profile mp = lastCp;
                    for (Day x : cand) mp = merge(mp, x.profile);
                    cand = m; cp = merge(mp, d.profile); extOf = lastComp; lastCand = null; extCount++;
                    live(cand, cp, nextTradeDay.get(d.date), p, live);
                    continue;
                }
                boolean join = c >= cp.low && c <= cp.high();
                boolean or = !p.compJoinBoth || insideDay(d, cp, p);          // день внутри композита — ИЛИ, иначе И
                if (p.compositeMode && !or) join = join && vaJoins(d, cp, p);   // close в диапазоне И наслоение VA
                else if (!join && p.compositeMode && p.compVaOverlap > 0) join = vaJoins(d, cp, p);   // ... ИЛИ
                if (join) { cand.add(d); cp = merge(cp, d.profile); live(cand, cp, nextTradeDay.get(d.date), p, live); continue; }
                Comp made = closeComposite(cand, cp, d.date, p, ob, greyComp, res.comps, res.notBalance);
                if (made != null) made.extendsOf = extOf;
                lastCand = cand; lastCp = cp; lastComp = made; extOf = null; lastExt = extCount; extCount = 0;
                cand = new ArrayList<>(); cand.add(d); cp = d.profile;
            }
            // незакрытый композит в конце данных не используется
        }
        res.composites = ob.size();

        // ---------- первородные HVN-зоны и серые из слабых HVN-дней
        List<Zone> prim = primaryZones(full, p.hvnMinTpo, p.hvnMaxTpo, p, nextTradeDay, K_HVN);
        List<Zone> greyHvn = primaryZones(full, p.greyHvnMinTpo, p.greyHvnMaxTpo, p, nextTradeDay, K_GREY);
        for (Zone z : prim) {
            int need = p.greyMinHistoryDays - z.formationDays;
            if (need <= 0) continue;
            z.young = true;
            z.greyUntil = z.conf.size() < need ? null : z.conf.get(need - 1).plusDays(1);
        }
        List<Zone> zones = res.zones;
        zones.addAll(ob);
        zones.addAll(prim);
        List<Zone> greyAll = new ArrayList<>(greyComp); greyAll.addAll(greyHvn);
        for (Zone z : greyAll) {
            double w = z.hi - z.lo;
            if (w >= p.greyMinWidthPts - 1e-9 && w <= p.greyMaxWidthPts + 1e-9) zones.add(z);
        }
        if (p.compositeMode) {
            // композит, чей диапазон почти целиком внутри более раннего баланса, — уточнение: своего баланса
            // не даёт, его ядро — серая зона, ценой в этом диапазоне управляет родитель
            if (p.subOverlap > 0) {
                List<Comp> parents = new ArrayList<>();
                for (Comp c : res.comps) {
                    double w = c.hi - c.lo;
                    Comp par = null;
                    for (int i = parents.size() - 1; i >= 0 && par == null; i--) {
                        Comp q = parents.get(i);
                        double inter = Math.min(c.hi, q.hi) - Math.max(c.lo, q.lo);
                        if (inter >= p.subOverlap * w && q.hi - q.lo <= p.subMaxRatio * w) par = q;
                    }
                    if (c.extendsOf != null && parents.contains(c.extendsOf)) {   // продолжение после ложного выхода
                        parents.set(parents.indexOf(c.extendsOf), c); continue;
                    }
                    if (par == null) { parents.add(c); continue; }
                    c.parent = par;
                    res.subComps.add(c);
                    if (p.partition) continue;
                    Zone g = new Zone();
                    g.k = K_GREY; g.src = "sub"; g.fixedGrey = true; g.av = c.av; g.lo = c.core.lo; g.hi = c.core.hi;
                    zones.add(g);
                }
                res.comps.clear(); res.comps.addAll(parents);
                // снимок формирующегося композита внутри более раннего баланса — уточнение, ролей не задаёт
                res.liveComps.removeIf(c -> {
                    double w = c.hi - c.lo;
                    for (Comp q : parents) {
                        if (!q.av.isBefore(c.av) || (c.start.equals(q.start))) continue;   // только закрытые раньше, не он сам
                        double inter = Math.min(c.hi, q.hi) - Math.max(c.lo, q.lo);
                        if (inter >= p.subOverlap * w && q.hi - q.lo <= p.subMaxRatio * w) return true;
                    }
                    return false;
                });
            }
            // ядра: новое, пересекающее существующее ≥ mergeOverlapMin (от более узкого), его подтверждает
            List<Zone> cores = new ArrayList<>();
            Map<Zone, List<Long>> cc = new HashMap<>();
            for (Comp c : res.comps) {
                int lo = ticks(c.core.lo, tick), hi = ticks(c.core.hi, tick);
                Zone best = null; double bestOv = -1;
                for (Zone e : cores) {
                    double ov = overlap(ticks(e.lo, tick), ticks(e.hi, tick), lo, hi);
                    boolean near = p.joinGapPts >= 0 && Math.max(e.lo, c.core.lo) - Math.min(e.hi, c.core.hi) <= p.joinGapPts + 1e-9;
                    if ((ov >= p.mergeOverlapMin || near) && ov > bestOv) { best = e; bestOv = ov; }
                }
                if (best != null) { cc.get(best).add(c.av.toEpochDay()); c.core = best; }
                else { cores.add(c.core); cc.put(c.core, new ArrayList<>()); }
            }
            for (Zone e : cores) e.coreConf = cc.get(e).stream().mapToLong(Long::longValue).toArray();
            if (!p.partition) {
                for (Zone e : cores) {
                    if (e.nodes.isEmpty()) { zones.add(e); continue; }
                    for (Zone nz : e.nodes) { nz.coreConf = e.coreConf; zones.add(nz); }
                }
            } else {
                // разметка: первый композит на уровне — главный; совпадающий кусок позднего композита его подтверждает
                List<Comp> bal = new ArrayList<>(res.comps);
                if (p.subAddsZones) bal.addAll(res.subComps);
                bal.sort((x, y) -> x.av.compareTo(y.av));
                List<Zone> parts = new ArrayList<>();
                Map<Zone, List<Long>> pc = new HashMap<>();
                for (Comp c : bal) for (int[] sg : c.segs) {
                    Zone best = null; double bestOv = -1;
                    for (Zone e : parts) {
                        double ov = overlap(ticks(e.lo, tick), ticks(e.hi, tick), sg[0], sg[1]);
                        if (ov >= p.mergeOverlapMin && ov > bestOv) { best = e; bestOv = ov; }
                    }
                    if (best == null && p.joinGapPts >= 0) {        // вплотную к зоне того же вида — её подтверждение
                        int kind = sg[2];
                        for (Zone e : parts) {
                            int ek = e.partKind == Kind.CORE ? 0 : e.partKind == Kind.HVN_S ? 1 : 2;
                            double gap = Math.max(e.lo, sg[0] * tick) - Math.min(e.hi, sg[1] * tick);
                            if (ek == kind && gap <= p.joinGapPts + 1e-9) { best = e; break; }
                        }
                    }
                    if (best != null) { pc.get(best).add(c.av.toEpochDay()); continue; }
                    Zone z = new Zone();
                    z.k = K_PART; z.src = "part"; z.av = c.av; z.partOf = c; z.info = c.core.info;
                    z.partKind = sg[2] == 0 ? Kind.CORE : sg[2] == 1 ? Kind.HVN_S : Kind.HVN_R;
                    z.lo = sg[0] * tick; z.hi = sg[1] * tick;
                    parts.add(z); pc.put(z, new ArrayList<>());
                }
                for (Zone e : parts) { e.coreConf = pc.get(e).stream().mapToLong(Long::longValue).toArray(); zones.add(e); }
                // дневные HVN-зоны и серые внутри тела сложившегося композита — не рисуются
                for (Zone z : zones) {
                    if (z.k != K_HVN && z.k != K_GREY) continue;
                    double mid = (z.lo + z.hi) / 2;
                    for (Comp c : bal) if (c.bodyLo <= mid && mid <= c.bodyHi) z.hideFrom = Math.min(z.hideFrom, c.av.toEpochDay());
                }
            }
        }

        if (p.compositeMode && p.sessionZones) {
            // зоны из сессий: узлы RTH TPO дня, подогнанные по телам; роль — композит / отторжения / выход / серая
            List<Comp> bal = new ArrayList<>(res.comps);
            if (p.liveComposite) bal.addAll(res.liveComps);
            bal.sort((x, y) -> x.av.compareTo(y.av));
            zones.clear();
            List<Zone> ses = new ArrayList<>();
            Map<Zone, List<Long>> cf = new HashMap<>();
            int onePt = (int) Math.round(1.0 / tick), maxW = (int) Math.round(p.sessMaxPts / tick);
            double mwk = p.minWickTicks * tick;
            for (Day d : full) {
                LocalDate av = nextTradeDay.get(d.date);
                if (av == null) continue;
                for (int[] nd : dayNodes(d, p, onePt, maxW)) {
                    Zone best = null;
                    for (Zone e : ses) {
                        double gap = Math.max(e.lo, nd[0] * tick) - Math.min(e.hi, nd[1] * tick);   // < 0 — пересекаются
                        if (gap < p.sessMinGapPts - 1e-9) { best = e; break; }
                    }
                    if (best != null) { cf.get(best).add(av.toEpochDay()); continue; }
                    Zone z = new Zone();
                    z.k = K_SESS; z.src = "sess"; z.av = av; z.lo = nd[0] * tick; z.hi = nd[1] * tick;
                    double mid = (z.lo + z.hi) / 2;
                    Comp gov = null;
                    for (Comp c : bal) if (!c.av.isAfter(av) && c.bodyLo <= mid && mid <= c.bodyHi) gov = c;
                    z.partKind = gov != null ? roleIn(gov, mid) : roleByDay(d, z.lo, z.hi, mwk, p.sessRoleMode);
                    if (gov != null) z.partOf = gov;
                    // синхронизация с композитом: один композит (первый, накрывший зону после рождения, или формирующийся, в
                    // котором она родилась); пока он формируется — каждый снимок, после закрытия — заморожено
                    LocalDate grp = gov != null && gov.live ? gov.start : null;
                    List<Long> at = new ArrayList<>(); List<Kind> kd = new ArrayList<>(); List<Comp> cc = new ArrayList<>();
                    for (Comp c : bal) {
                        if (!c.av.isAfter(av) || c.bodyLo > mid || mid > c.bodyHi) continue;
                        if (!p.sessResyncLater) {
                            if (grp == null) grp = c.start;
                            if (!c.start.equals(grp)) continue;
                        }
                        at.add(c.av.toEpochDay()); kd.add(roleIn(c, mid)); cc.add(c);
                        if (!c.live && !p.sessResyncLater) break;
                    }
                    if (!at.isEmpty()) {
                        z.rcAt = at.stream().mapToLong(Long::longValue).toArray(); z.rcKind = kd.toArray(new Kind[0]); z.rcComp = cc.toArray(new Comp[0]);
                        z.recolorAt = z.rcAt[0]; z.recolorKind = z.rcKind[0]; z.info = cc.get(cc.size() - 1).core.info;
                    }
                    ses.add(z); cf.put(z, new ArrayList<>());
                }
            }
            for (Zone z : ses) { z.coreConf = cf.get(z).stream().mapToLong(Long::longValue).toArray(); zones.add(z); }
        }

        // ---------- события по RTH-барам: отторжения (лонг / шорт)
        List<Integer> rthIdx = new ArrayList<>();
        for (int i = 0; i < bars.size(); i++) if (isRth[i]) rthIdx.add(i);
        int nR = rthIdx.size();
        double[] RO = new double[nR], RH = new double[nR], RL = new double[nR], RC = new double[nR];
        long[] RD = new long[nR];
        Map<LocalDate, Integer> firstRth = new HashMap<>();
        for (int j = 0; j < nR; j++) {
            Bar b = bars.get(rthIdx.get(j));
            RO[j] = b.o; RH[j] = b.h; RL[j] = b.l; RC[j] = b.c;
            LocalDate d = rthDateOf[rthIdx.get(j)];
            RD[j] = d.toEpochDay();
            firstRth.putIfAbsent(d, j);
        }
        double mw = p.minWickTicks * tick;
        long[] fullOrd = new long[full.size()];
        for (int i = 0; i < full.size(); i++) fullOrd[i] = full.get(i).date.toEpochDay();
        for (Zone z : zones) {
            Integer s = firstRth.get(z.av);
            if (s != null) {
                List<Long> lg = new ArrayList<>(), sh = new ArrayList<>();
                for (int j = s; j < nR; j++) {
                    int t = interaction(RO[j], RH[j], RL[j], RC[j], z.lo, z.hi, mw);
                    if (t == 1) lg.add(RD[j]); else if (t == 2) sh.add(RD[j]);
                }
                z.L = lg.stream().mapToLong(Long::longValue).toArray();
                z.S = sh.stream().mapToLong(Long::longValue).toArray();
            }
            // подтверждения: зачётные HVN-дни (8–10 TPO) после появления, зазор до зоны
            for (Day d : full) {
                if (d.date.isBefore(z.av)) continue;
                if (d.hvnTpo < p.hvnMinTpo || d.hvnTpo > p.hvnMaxTpo) continue;
                double gap = Math.max(0, Math.max(d.bandLo * tick - z.hi, z.lo - d.bandHi * tick));
                if (gap <= p.confirmStoreMaxPts) z.cf.add(new long[]{d.date.toEpochDay(), Math.round(gap * 100)});
            }
            z.ai = lowerBound(fullOrd, z.avOrd());
        }

        // ---------- сессии (все бары), подгонка границ
        List<Session> sessAll = new ArrayList<>();
        for (int i = 0; i < bars.size(); i++) {
            Session cur = sessAll.isEmpty() ? null : sessAll.get(sessAll.size() - 1);
            if (cur == null || !cur.date.equals(sessOf[i])) {
                cur = new Session(); cur.date = sessOf[i]; cur.ord = cur.date.toEpochDay();
                cur.firstBar = i; cur.open = bars.get(i).o; cur.startMs = bars.get(i).t;
                sessAll.add(cur);
            }
            cur.lastBar = i; cur.endMs = bars.get(i).t + p.periodMinutes * 60_000L;
        }
        long[] sessOrd = new long[sessAll.size()];
        for (int i = 0; i < sessAll.size(); i++) sessOrd[i] = sessAll.get(i).ord;
        for (Zone z : zones) {
            z.blo = z.lo; z.bhi = z.hi;
            if (p.alignSessions <= 0) continue;
            int i0 = lowerBound(sessOrd, z.avOrd());
            int i1 = Math.min(i0 + p.alignSessions, sessAll.size());
            if (i1 <= i0) continue;
            int a = sessAll.get(i0).firstBar, e = sessAll.get(i1 - 1).lastBar + 1;
            double w = z.hi - z.lo;
            double search = Math.max(p.alignSearchFrac * w, p.alignSearchMinPts);
            double maxW = Math.max(p.alignMaxWidthPts, p.alignMaxWidthMult * w);
            if (p.compositeMode && p.zoneMaxWidthPts > 0) maxW = Math.min(maxW, Math.max(w, p.zoneMaxWidthPts));
            double[] r = alignZone(bars.subList(a, e), z.lo, z.hi, tick, search, mw, p.alignWPlain,
                    p.alignMinScore, p.alignMinWidthTicks * tick, maxW);
            z.blo = Math.rint(r[0] / tick) * tick; z.bhi = Math.rint(r[1] / tick) * tick;
        }

        if (p.compositeMode) {
            // ядро, нарисованное узлами, в списке зон нет: его границы — от нижнего до верхнего узла
            for (Comp c : res.comps) {
                Zone e = c.core;
                if (!zones.contains(e) && e.nodes.isEmpty()) { e.blo = e.lo; e.bhi = e.hi; }   // ядро не на графике (разметка)
                if (e.nodes.isEmpty()) continue;
                e.blo = Double.MAX_VALUE; e.bhi = -Double.MAX_VALUE;
                for (Zone nz : e.nodes) { e.blo = Math.min(e.blo, nz.blo); e.bhi = Math.max(e.bhi, nz.bhi); }
            }
            for (Zone z : zones) {
                if (z.k != K_HVN && z.k != K_GREY) continue;
                double mid = (z.blo + z.bhi) / 2;
                List<Comp> g = new ArrayList<>();
                for (Comp c : res.comps) if (c.lo <= mid && mid <= c.hi) g.add(c);
                z.gov = g.toArray(new Comp[0]);
                z.govAv = g.stream().mapToLong(c -> c.av.toEpochDay()).toArray();
            }
        }

        // ---------- показываемые сессии и предыдущий RTH-день
        int from = Math.max(0, sessAll.size() - Math.max(1, p.sessions));
        for (int i = from; i < sessAll.size(); i++) {
            Session s = sessAll.get(i);
            int k = lowerBound(fullOrd, s.ord) - 1;
            if (k >= 0) {
                Day y = full.get(k);
                s.prevDate = y.date; s.prevVah = y.vah * tick; s.prevVal = y.val * tick; s.prevPoc = y.poc * tick;
            }
            res.sessions.add(s);
        }

        // ---------- фильтры страницы
        Set<Zone> keep = new LinkedHashSet<>();
        for (Session s : res.sessions) {
            List<double[]> cand = new ArrayList<>();   // {zoneIndex, dist}
            for (int zi = 0; zi < zones.size(); zi++) {
                Zone z = zones.get(zi);
                if (z.avOrd() > s.ord) continue;
                State st = stateAt(z, s.ord, p, fullOrd);
                if (!passes(z, st, p)) continue;
                double dist = s.open < z.blo ? z.blo - s.open : s.open > z.bhi ? s.open - z.bhi : 0;
                cand.add(new double[]{zi, dist});
            }
            cand.sort((x, y) -> Double.compare(x[1], y[1]));      // стабильная, как Array.sort
            int n = p.nearest > 0 ? Math.min(p.nearest, cand.size()) : cand.size();
            for (int i = 0; i < n; i++) keep.add(zones.get((int) cand.get(i)[0]));
        }
        for (Session s : res.sessions) {
            List<Shown> list = new ArrayList<>();
            for (Zone z : keep) {
                if (z.avOrd() > s.ord) continue;
                State st = stateAt(z, s.ord, p, fullOrd);
                if (st.kind != null) list.add(new Shown(z, st));      // null — поглощена ядром композита
            }
            List<Shown> out = new ArrayList<>();
            for (Shown v : list) {
                // режим «по композиту»: зона, лежащая на ядре, — его подтверждение
                boolean hide = false;
                if (p.compositeMode && v.st.kind != Kind.CORE && v.z.k != K_PART && v.z.k != K_SESS) {
                    for (Shown m : list) {
                        if (m.st.kind != Kind.CORE) continue;
                        double inter = Math.min(v.z.bhi, m.z.bhi) - Math.max(v.z.blo, m.z.blo);
                        if (inter > 0 && inter / Math.max(v.z.bhi - v.z.blo, tick) > p.coreAbsorb) { hide = true; break; }
                    }
                    if (hide) continue;
                }
                // зона вплотную к более старой видимой зоне того же вида — её подтверждение
                if (p.compositeMode && p.joinGapPts >= 0 && v.st.kind != Kind.CORE && v.z.k != K_PART && v.z.k != K_SESS) {
                    for (Shown m : list) {
                        if (m == v || m.st.kind != v.st.kind) continue;
                        boolean older = m.z.avOrd() < v.z.avOrd() || (m.z.avOrd() == v.z.avOrd() && list.indexOf(m) < list.indexOf(v));
                        if (older && Math.max(m.z.blo, v.z.blo) - Math.min(m.z.bhi, v.z.bhi) <= p.joinGapPts + 1e-9) { hide = true; break; }
                    }
                    if (hide) continue;
                }
                // зона из серого источника — подтверждение первородной зоны под ней
                boolean secondary = v.st.kind == Kind.GREY || (p.compositeMode && v.z.k == K_GREY);
                if (!secondary) { out.add(v); continue; }
                for (Shown m : list) {
                    if (m.st.kind == Kind.GREY || (p.compositeMode && m.z.k == K_GREY)) continue;
                    double inter = Math.min(v.z.bhi, m.z.bhi) - Math.max(v.z.blo, m.z.blo);
                    if (inter > 0 && inter / Math.max(v.z.bhi - v.z.blo, tick) > p.greyMaxOverlap) { hide = true; break; }
                }
                if (!hide) out.add(v);
            }
            if (p.compositeMode && p.noOverlap) out = layout(out, p);
            res.visible.add(out);
        }
        return res;
    }

    /** Раскладка без пересечений: ядра, затем остальные зоны (старшие первыми), затем серые; зона,
     *  налезающая на уже уложенные, обрезается до самого широкого свободного куска (≥ minPiecePts). */
    static List<Shown> layout(List<Shown> in, Params p) {
        List<Shown> order = new ArrayList<>(in);
        order.sort((x, y) -> {
            int rx = rank(x.st.kind), ry = rank(y.st.kind);
            if (rx != ry) return rx - ry;
            int c = x.z.av.compareTo(y.z.av);
            return c != 0 ? c : Double.compare(x.z.blo, y.z.blo);
        });
        List<double[]> used = new ArrayList<>();
        List<Shown> out = new ArrayList<>();
        for (Shown v : order) {
            double bestLo = 0, bestHi = -1;
            List<double[]> pieces = new ArrayList<>(); pieces.add(new double[]{v.z.blo, v.z.bhi});
            for (double[] u : used) {
                List<double[]> next = new ArrayList<>();
                for (double[] q : pieces) {
                    if (u[1] <= q[0] || u[0] >= q[1]) { next.add(q); continue; }
                    if (u[0] > q[0]) next.add(new double[]{q[0], u[0]});
                    if (u[1] < q[1]) next.add(new double[]{u[1], q[1]});
                }
                pieces = next;
            }
            for (double[] q : pieces) if (q[1] - q[0] > bestHi - bestLo) { bestLo = q[0]; bestHi = q[1]; }
            if (p.layoutDropYounger && (bestLo != v.z.blo || bestHi != v.z.bhi)) continue;   // налезает — не рисуется
            if (bestHi - bestLo < p.minPiecePts - 1e-9) continue;
            v.lo = bestLo; v.hi = bestHi;
            used.add(new double[]{bestLo, bestHi});
            out.add(v);
        }
        return out;
    }

    private static int rank(Kind k) { return k == Kind.CORE ? 0 : k == Kind.GREY ? 2 : 1; }

    // ------------------------------------------------------------------ роллы
    /** Склейка роллов: в окне 10 дней до 3-й пятницы марта / июня / сентября / декабря — самый большой скачок между
     *  соседними (через periodMinutes) барами, если он не меньше rollMinGapPts, — смена контракта. Цены всех баров до
     *  ролла сдвигаются на его гэп (аддитивно, как tpo/rolls.py, но гэп фактический, а не по модели carry). */
    static List<Bar> rollAdjust(List<Bar> bars, Params p, Result res) {
        long step = p.periodMinutes * 60_000L;
        Map<LocalDate, int[]> best = new HashMap<>();          // 3-я пятница → {индекс бара, }
        Map<LocalDate, Double> bestGap = new HashMap<>();
        for (int i = 1; i < bars.size(); i++) {
            Bar b = bars.get(i), a = bars.get(i - 1);
            if (b.t - a.t != step) continue;
            LocalDate d = LocalDateTime.ofInstant(Instant.ofEpochMilli(b.t), p.tz).toLocalDate();
            int m = d.getMonthValue();
            if (m % 3 != 0) continue;
            LocalDate f = d.withDayOfMonth(1);
            while (f.getDayOfWeek() != java.time.DayOfWeek.FRIDAY) f = f.plusDays(1);
            f = f.plusWeeks(2);                                  // 3-я пятница
            if (d.isAfter(f) || d.isBefore(f.minusDays(10))) continue;
            double g = b.o - a.c;
            if (Math.abs(g) < p.rollMinGapPts - 1e-9) continue;
            if (!bestGap.containsKey(f) || Math.abs(g) > Math.abs(bestGap.get(f))) { bestGap.put(f, g); best.put(f, new int[]{i}); }
        }
        if (best.isEmpty()) return bars;
        List<LocalDate> keys = new ArrayList<>(best.keySet());
        keys.sort(null);
        res.rollMs = new long[keys.size()]; res.rollGap = new double[keys.size()];
        for (int k = 0; k < keys.size(); k++) {
            res.rollMs[k] = bars.get(best.get(keys.get(k))[0]).t;
            res.rollGap[k] = Math.rint(bestGap.get(keys.get(k)) / p.tick) * p.tick;
        }
        List<Bar> out = new ArrayList<>(bars.size());
        for (Bar b : bars) {
            double sh = rollShift(res, b.t);
            out.add(sh == 0 ? b : new Bar(b.t, b.o + sh, b.h + sh, b.l + sh, b.c + sh));
        }
        return out;
    }

    /** Ролл после RTH дня a и не позже дня b (a < b): смена контракта в 15:00 CT дня ролла — после его RTH. */
    static boolean rollBetween(Result res, Params p, LocalDate a, LocalDate b) {
        for (long t : res.rollMs) {
            LocalDate r = LocalDateTime.ofInstant(Instant.ofEpochMilli(t), p.tz).toLocalDate();
            if (!r.isBefore(a) && r.isBefore(b)) return true;
        }
        return false;
    }

    /** Сдвиг цен бара, начавшегося в t, к текущему контракту: сумма гэпов роллов после t. */
    public static double rollShift(Result res, long t) {
        double s = 0;
        for (int k = 0; k < res.rollMs.length; k++) if (res.rollMs[k] > t) s += res.rollGap[k];
        return s;
    }

    // ------------------------------------------------------------------ композиты
    /** VA дня и VA композита пересекаются на ≥ compVaOverlap меньшей из двух (compVaOverlap = 0 — проверка выключена). */
    static boolean vaJoins(Day d, Profile cp, Params p) {
        if (p.compVaOverlap <= 0) return true;
        int[] cva = valueArea(cp, p.valueAreaPct);
        int inter = Math.min(d.vah, cva[2]) - Math.max(d.val, cva[1]) + 1;
        int narrow = Math.min(d.vah - d.val, cva[2] - cva[1]) + 1;
        return inter > 0 && inter / (double) narrow >= p.compVaOverlap;
    }

    /** Внутренний день: High–Low дня на ≥ compInsideShare внутри диапазона композита; внешний (compOutsideDays) —
     *  наоборот, диапазон композита на ≥ эту долю внутри High–Low дня (compInsideShare = 0 — выкл.). */
    static boolean insideDay(Day d, Profile cp, Params p) {
        if (p.compInsideShare <= 0) return false;
        int lo = ticks(d.low, p.tick), hi = ticks(d.high, p.tick);
        int in = Math.min(hi, cp.high()) - Math.max(lo, cp.low);
        if (in < 0) return false;
        int w = p.compOutsideDays ? Math.min(hi - lo, cp.high() - cp.low) : hi - lo;   // доля от более узкого
        return w == 0 || in / (double) w >= p.compInsideShare - 1e-9;
    }

    /** Снимок формирующегося композита после дня: кандидат ≥ compositeMinDays дней — композит со следующей сессии av
     *  (ядро, тело — по уже прошедшим дням). Не баланс — снимка нет. */
    private static void live(List<Day> cand, Profile cp, LocalDate av, Params p, List<Comp> out) {
        if (!p.compositeMode || !p.liveComposite || av == null) return;
        int fullDays = 0; for (Day d : cand) if (!d.shortDay) fullDays++;
        if (fullDays < p.compositeMinDays) return;                // короткие дни — в профиле, но не в счёте дней
        List<Comp> comps = new ArrayList<>();
        closeComposite(new ArrayList<>(cand), cp, av, p, new ArrayList<>(), new ArrayList<>(), comps, new ArrayList<>());
        for (Comp c : comps) { c.live = true; out.add(c); }
    }

    private static Comp closeComposite(List<Day> cand, Profile cp, LocalDate endDay, Params p,
                                       List<Zone> ob, List<Zone> greyComp, List<Comp> comps, List<Comp> notBalance) {
        int n = cand.size();
        Comp made = null;
        if (n < p.greyCompositeMinDays) return null;
        int[] va = valueArea(cp, p.valueAreaPct);
        if (n >= p.compositeMinDays) {
            Comp c = new Comp();
            made = c;
            c.lo = cp.low * p.tick; c.hi = cp.high() * p.tick; c.av = endDay; c.days = n;
            c.start = cand.get(0).date; c.end = cand.get(n - 1).date;
            Zone core = new Zone();
            core.k = K_CORE; core.av = endDay; core.src = "core";
            core.info = coreInfo(cand, cp, va[0], p.tick);
            int[] band = p.coreByVa ? new int[]{va[1], va[2]} : hvnBand(cp, va[0], p.hvnShare);
            int poc = va[0];
            int cap = (int) Math.round(p.zoneMaxWidthPts / p.tick);
            if (p.coreFromSessions && !p.coreByVa) {
                // ядро — от POC композита до обрыва TPO (и не ниже hvnShare максимума), не шире лимита;
                // композит — баланс, если VA его дней задевают ядро
                band = narrow(cp, cliffBand(cp, poc, p.hvnShare, p.coreCliffTpo, 4), poc, cap);
                int hit = 0;
                for (Day d : cand) if (d.val <= band[1] && d.vah >= band[0]) hit++;
                if (p.coreMinDaysShare > 0 && hit < Math.ceil(p.coreMinDaysShare * n - 1e-9)) {
                    band = null;                // не баланс: дни композита не сходятся в одной зоне
                }
            } else band = narrow(cp, band, poc, cap);
            if (band == null) notBalance.add(c);
            else {
                core.lo = band[0] * p.tick; core.hi = band[1] * p.tick;
                c.core = core;
                comps.add(c);
                if (p.partition) {
                    int[] body = new int[4];
                    c.segs = partition(cand, cp, poc, p, body);
                    c.bodyLo = body[0] * p.tick; c.bodyHi = body[1] * p.tick;
                    c.coreLo = body[2] * p.tick; c.coreHi = body[3] * p.tick;
                }
                if (p.coreNodes && !p.partition) {
                    List<int[]> nd = nodes(cp, poc, p.hvnShare, p.nodeDipTpo, p.nodeDropTpo, (int) Math.round(1.0 / p.tick));
                    if (!nd.isEmpty()) {
                        List<Zone> zs = new ArrayList<>();
                        int lo = Integer.MAX_VALUE, hi = Integer.MIN_VALUE;
                        for (int[] q : nd) {
                            Zone nz = new Zone();
                            nz.k = K_CORE; nz.av = endDay; nz.src = "node"; nz.info = core.info; nz.nodeOf = core;
                            nz.lo = q[0] * p.tick; nz.hi = q[1] * p.tick;
                            zs.add(nz); lo = Math.min(lo, q[0]); hi = Math.max(hi, q[1]);
                        }
                        core.nodes = zs;
                        core.lo = lo * p.tick; core.hi = hi * p.tick;      // ядро для классов — от нижнего до верхнего узла
                    }
                }
            }
        }
        Zone z = new Zone();
        z.av = endDay; z.src = "comp";
        if (n >= p.compositeMinDays) {
            z.k = K_OLD_BALANCE; z.lo = va[1] * p.tick; z.hi = va[2] * p.tick;
            ob.add(z);
        } else {
            int[] band = hvnBand(cp, va[0], p.hvnShare);
            z.k = K_GREY; z.lo = band[0] * p.tick; z.hi = band[1] * p.tick;
            greyComp.add(z);
        }
        return made;
    }

    /** Роль цены mid в композите: ядро — проторговка, выше — сопротивление, ниже — поддержка. */
    static Kind roleIn(Comp c, double mid) {
        return mid > c.coreHi ? Kind.HVN_R : mid < c.coreLo ? Kind.HVN_S : Kind.CORE;
    }

    /** Роль зоны вне композита по сессии её рождения: отторжения (первичны), иначе — где закрылась сессия, иначе серая. */
    static Kind roleByDay(Day d, double lo, double hi, double mw, int mode) {
        int nl = 0, ns = 0;
        for (double[] b : d.sbars) {
            int t = interaction(b[0], b[1], b[2], b[3], lo, hi, mw);
            if (t == 1) nl++; else if (t == 2) ns++;
        }
        if (mode == 0 && nl > ns) return Kind.HVN_S;
        if (mode == 0 && ns > nl) return Kind.HVN_R;
        double close = d.sbars.isEmpty() ? d.close : d.sbars.get(d.sbars.size() - 1)[3];
        return close > hi ? Kind.HVN_S : close < lo ? Kind.HVN_R : Kind.GREY;
    }

    /** Узлы RTH TPO-профиля дня (сглаж. 1 п.): вершины ≥ sessMinPeak, разделённые провалами; узел — строки не ниже
     *  вершины на sessNodeDrop; затем подгонка по телам свечей сессии в пределах узла ± 1 п. (скопление ≥ 70% максимума).
     *  Ширина — от 1 п. до sessMaxPts. {lo, hi} в тиках. */
    static List<int[]> dayNodes(Day d, Params p, int onePt, int maxW) {
        Profile pf = d.profile; int[] c = pf.counts; int n = c.length, half = onePt / 2;
        double[] sm = new double[n];
        for (int i = 0; i < n; i++) {
            double sum = 0; int k = 0;
            for (int j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) { sum += c[j]; k++; }
            sm[i] = sum / k;
        }
        double dayMax = 0; for (double v : sm) dayMax = Math.max(dayMax, v);
        List<int[]> pcs = new ArrayList<>();
        splitRel(sm, 0, n - 1, p.sessNodeDip, 1.0, pcs);
        int[] occ = new int[n + 2 * onePt + 1];               // тела свечей сессии, со сдвигом onePt
        for (int[] q : d.bodies) {
            int x0 = Math.min(q[0], q[1]) - pf.low + onePt, x1 = Math.max(q[0], q[1]) - pf.low + onePt;
            x0 = Math.max(0, x0); x1 = Math.min(occ.length - 2, x1);
            if (x0 <= x1) { occ[x0]++; occ[x1 + 1]--; }
        }
        for (int i = 1; i < occ.length; i++) occ[i] += occ[i - 1];
        List<int[]> out = new ArrayList<>();
        for (int[] q : pcs) {
            int m = q[0];
            for (int i = q[0]; i <= q[1]; i++) if (sm[i] > sm[m]) m = i;
            if (c[m] < p.sessMinPeak && sm[m] < p.sessMinPeak) continue;
            if (p.sessPeakShare > 0 && sm[m] < p.sessPeakShare * dayMax) continue;
            int lo = m, hi = m;
            while (lo > q[0] && sm[lo - 1] >= sm[m] - p.sessNodeDrop) lo--;
            while (hi < q[1] && sm[hi + 1] >= sm[m] - p.sessNodeDrop) hi++;
            // подгонка по телам: скопление тел в узле ± 1 п.
            int w0 = lo, w1 = hi + 2 * onePt, bm = w0;              // индексы в occ (со сдвигом onePt)
            for (int i = w0; i <= w1; i++) if (occ[i] > occ[bm]) bm = i;
            if (occ[bm] >= 2) {
                double thr = p.sessBodyShare * occ[bm];
                int x0 = bm, x1 = bm;
                while (x0 > w0 && occ[x0 - 1] >= thr) x0--;
                while (x1 < w1 && occ[x1 + 1] >= thr) x1++;
                lo = x0 - onePt; hi = x1 - onePt;
            }
            if (hi - lo < onePt) { int mid = (lo + hi) / 2; lo = mid - onePt / 2; hi = lo + onePt; }
            if (hi - lo > maxW) { int mid = (lo + hi) / 2; lo = mid - maxW / 2; hi = lo + maxW; }
            out.add(new int[]{pf.low + lo, pf.low + hi});
        }
        return out;
    }

    /** Разметка тела композита. Тело — строки от POC, пока сглаженный (1 п.) счёт не ниже bodyShare
     *  максимума; ядро — часть тела не ниже partCoreShare. Ниже ядра — поддержка, выше — сопротивление.
     *  Каждая часть режется по провалам (глубина ≥ max(2, partDipShare × максимум) от обеих сторон), кусок
     *  шире partMaxPts — на равные части ≈ partTargetPts (границы — к краям HVN-полос дней в пределах 2 п.),
     *  куски уже partMinPts присоединяются к соседу. body — {низ, верх} тела в тиках. */
    static List<int[]> partition(List<Day> days, Profile cp, int pocTicks, Params p, int[] body) {
        int[] c = cp.counts; int n = c.length, onePt = (int) Math.round(1.0 / p.tick), half = onePt / 2;
        double[] sm = new double[n];
        for (int i = 0; i < n; i++) {
            double sum = 0; int k = 0;
            for (int j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) { sum += c[j]; k++; }
            sm[i] = sum / k;
        }
        double mx = 0; for (double v : sm) mx = Math.max(mx, v);
        int pi = pocTicks - cp.low, a = pi, b = pi, ca = pi, cb = pi;
        int maxBody = (int) Math.round(p.partMaxBodyPts / p.tick);
        for (double share = p.bodyShare; ; share += 0.05) {
            a = pi; b = pi;
            while (a > 0 && sm[a - 1] >= share * mx) a--;
            while (b < n - 1 && sm[b + 1] >= share * mx) b++;
            if (p.partMaxBodyPts <= 0 || b - a <= maxBody || share >= p.partCoreShare) break;
        }
        while (ca > a && sm[ca - 1] >= p.partCoreShare * mx) ca--;
        while (cb < b && sm[cb + 1] >= p.partCoreShare * mx) cb++;
        body[0] = cp.low + a; body[1] = cp.low + b;
        if (body.length > 3) { body[2] = cp.low + ca; body[3] = cp.low + cb; }
        double dip = Math.max(2.0, p.partDipShare * mx);
        // профиль тел: сколько тел 30m-свечей (open–close) дней композита перекрывает каждую строку
        int[] occ = new int[n + 1];
        for (Day d : days) for (int[] q0 : p.fitEthBodies ? d.bodies : rthBodies(d)) {
            int[] q = {0, 0, 0, q0[0], q0[1]};
            int x0 = Math.max(0, Math.min(q[3], q[4]) - cp.low), x1 = Math.min(n - 1, Math.max(q[3], q[4]) - cp.low);
            if (x0 <= x1) { occ[x0]++; occ[x1 + 1]--; }
        }
        for (int i = 1; i <= n; i++) occ[i] += occ[i - 1];
        int occMax = 0; for (int i = a; i <= b; i++) occMax = Math.max(occMax, occ[i]);
        double bodyThr = Math.max(3, p.fitBodyShare * occMax);
        int maxW = (int) Math.round(p.partMaxPts / p.tick), tgt = (int) Math.round(p.partTargetPts / p.tick),
            minW = (int) Math.round(p.partMinPts / p.tick);
        List<Integer> edges = new ArrayList<>();
        for (Day d : days) if (d.bandHi > 0) { edges.add(d.bandLo - cp.low); edges.add(d.bandHi - cp.low + 1); }
        List<int[]> out = new ArrayList<>();
        int[][] regions = {{a, ca - 1, 1}, {ca, cb, 0}, {cb + 1, b, 2}};
        if (p.bodyClusters) {
            // профиль тел, сглаженный до 1 п.; скопления — по провалам; зона — тела вокруг вершины скопления
            double[] os = new double[n];
            for (int i = 0; i < n; i++) {
                double sum = 0; int k = 0;
                for (int j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) { sum += occ[j]; k++; }
                os[i] = sum / k;
            }
            for (int[] r : regions) {
                if (r[1] < r[0]) continue;
                List<int[]> pcs = new ArrayList<>();
                splitRel(os, r[0], r[1], p.clusterDip, 1.5, pcs);
                for (int[] q : pcs) {
                    int m = q[0];
                    for (int i = q[0]; i <= q[1]; i++) if (os[i] > os[m]) m = i;
                    if (os[m] < bodyThr) continue;                  // тела здесь не скапливаются
                    int x0 = m, x1 = m;
                    for (double sh = p.fitClusterShare; ; sh += 0.05) {
                        double thr = Math.max(bodyThr, sh * os[m]);
                        x0 = m; x1 = m;
                        while (x0 > q[0] && os[x0 - 1] >= thr) x0--;
                        while (x1 < q[1] && os[x1 + 1] >= thr) x1++;
                        if (x1 - x0 <= (int) Math.round(p.clusterMaxPts / p.tick) || sh >= 0.95) break;
                    }
                    if (x1 - x0 + 1 < minW) continue;
                    out.add(new int[]{cp.low + x0, cp.low + x1, r[2]});
                }
            }
            return out;
        }
        for (int[] r : regions) {
            if (r[1] < r[0]) continue;
            List<int[]> pcs = new ArrayList<>();
            splitDips(sm, r[0], r[1], dip, pcs);
            List<int[]> fine = new ArrayList<>();
            for (int[] q : pcs) {
                int w = q[1] - q[0] + 1;
                if (w <= maxW) { fine.add(q); continue; }
                int k = (int) Math.ceil(w / (double) tgt);
                int start = q[0];
                for (int j = 1; j < k; j++) {
                    int cut = q[0] + (int) Math.round(j * w / (double) k), best = cut;
                    for (int e : edges) if (e > start + minW && e < q[1] - minW && Math.abs(e - cut) <= 2 * onePt && Math.abs(e - cut) < Math.abs(best - cut)) best = e;
                    fine.add(new int[]{start, best - 1}); start = best;
                }
                fine.add(new int[]{start, q[1]});
            }
            // слишком узкие — к соседу поуже
            boolean ch = true;
            while (ch && fine.size() > 1) {
                ch = false;
                for (int i = 0; i < fine.size(); i++) {
                    int[] q = fine.get(i);
                    if (q[1] - q[0] + 1 >= minW) continue;
                    int j = i == 0 ? 1 : i == fine.size() - 1 ? i - 1
                            : (fine.get(i - 1)[1] - fine.get(i - 1)[0] <= fine.get(i + 1)[1] - fine.get(i + 1)[0] ? i - 1 : i + 1);
                    int[] m = fine.get(j);
                    fine.set(Math.min(i, j), new int[]{Math.min(q[0], m[0]), Math.max(q[1], m[1])});
                    fine.remove(Math.max(i, j)); ch = true; break;
                }
            }
            for (int[] q : fine) {
                int lo = q[0], hi = q[1];
                if (p.fitBodyShare > 0) {         // подгонка по телам: зона — скопление тел свечей в куске
                    int m = lo;
                    for (int i = lo; i <= hi; i++) if (occ[i] > occ[m]) m = i;
                    double thr = Math.max(bodyThr, p.fitClusterShare * occ[m]);
                    if (occ[m] < thr) continue;         // тела здесь не скапливаются — зоны нет
                    int x0 = m, x1 = m;
                    while (x0 > lo && occ[x0 - 1] >= thr) x0--;
                    while (x1 < hi && occ[x1 + 1] >= thr) x1++;
                    lo = x0; hi = x1;
                    if (hi - lo + 1 < minW) continue;
                }
                if (hi - lo + 1 >= Math.min(minW, onePt)) out.add(new int[]{cp.low + lo, cp.low + hi, r[2]});
            }
        }
        return out;
    }

    private static List<int[]> rthBodies(Day d) {
        List<int[]> out = new ArrayList<>();
        for (int[] q : d.periods) out.add(new int[]{q[3], q[4]});
        return out;
    }

    /** Деление [lo, hi] по провалам: глубина ≥ max(abs, rel × меньшая из вершин слева и справа). */
    private static void splitRel(double[] v, int lo, int hi, double rel, double abs, List<int[]> out) {
        int best = -1; double bestDepth = 0;
        for (int i = lo + 1; i < hi; i++) {
            if (!(v[i] <= v[i - 1] && v[i] <= v[i + 1])) continue;
            double l = 0, r = 0;
            for (int j = lo; j < i; j++) l = Math.max(l, v[j]);
            for (int j = i + 1; j <= hi; j++) r = Math.max(r, v[j]);
            double depth = Math.min(l, r) - v[i];
            if (depth >= Math.max(abs, rel * Math.min(l, r)) && depth > bestDepth) { bestDepth = depth; best = i; }
        }
        if (best < 0) { out.add(new int[]{lo, hi}); return; }
        splitRel(v, lo, best - 1, rel, abs, out);
        splitRel(v, best, hi, rel, abs, out);
    }

    private static void splitDips(double[] sm, int lo, int hi, double dip, List<int[]> out) {
        int best = -1; double bestDepth = 0;
        for (int i = lo + 1; i < hi; i++) {
            if (!(sm[i] <= sm[i - 1] && sm[i] <= sm[i + 1])) continue;
            double l = 0, r = 0;
            for (int j = lo; j < i; j++) l = Math.max(l, sm[j]);
            for (int j = i + 1; j <= hi; j++) r = Math.max(r, sm[j]);
            double depth = Math.min(l, r) - sm[i];
            if (depth >= dip && depth > bestDepth) { bestDepth = depth; best = i; }
        }
        if (best < 0) { out.add(new int[]{lo, hi}); return; }
        splitDips(sm, lo, best - 1, dip, out);
        splitDips(sm, best, hi, dip, out);
    }

    /** Подсказка ядра: дни композита, диапазон, POC и две самые высокие вершины профиля (вторая — за провалом вне плато POC). */
    static String coreInfo(List<Day> days, Profile cp, int poc, double tick) {
        StringBuilder b = new StringBuilder("композит ");
        for (int i = 0; i < days.size(); i++) b.append(i == 0 ? "" : ", ").append(days.get(i).date.toString().substring(5));
        b.append(String.format(Locale.ROOT, "; диапазон %.2f – %.2f; POC %.2f (%d TPO)",
                cp.low * tick, cp.high() * tick, poc * tick, cp.counts[poc - cp.low]));
        int[] c = cp.counts; int i1 = poc - cp.low, i2 = -1;
        // плато первой вершины: подряд строки не ниже её TPO − 2; вторая вершина — вне плато, через провал
        int a = i1, z = i1;
        while (a > 0 && c[a - 1] >= c[i1] - 2) a--;
        while (z < c.length - 1 && c[z + 1] >= c[i1] - 2) z++;
        for (int i = 0; i < c.length; i++) if ((i < a || i > z) && (i2 < 0 || c[i] > c[i2])) i2 = i;
        b.append(String.format(Locale.ROOT, "; вершины %.2f (%d)", (cp.low + i1) * tick, cp.counts[i1]));
        if (i2 >= 0) b.append(String.format(Locale.ROOT, ", %.2f (%d)", (cp.low + i2) * tick, cp.counts[i2]));
        return b.toString();
    }

    /** Узлы профиля: участок строк не ниже share × максимум (сглаживание 1 п.) вокруг POC делится по
     *  провалам глубиной ≥ dip TPO от обеих соседних вершин; узел — строки около вершины участка, не ниже
     *  её на drop TPO, не уже 1 п. {lo, hi} в тиках, снизу вверх. */
    static List<int[]> nodes(Profile p, int pocTicks, double share, double dip, double drop, int onePt) {
        int[] c = p.counts; int n = c.length, half = onePt / 2;
        double[] sm = new double[n];
        for (int i = 0; i < n; i++) {
            double sum = 0; int k = 0;
            for (int j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) { sum += c[j]; k++; }
            sm[i] = sum / k;
        }
        double mx = 0; for (double v : sm) mx = Math.max(mx, v);
        int pi = pocTicks - p.low, a = pi, b = pi;
        while (a > 0 && sm[a - 1] >= share * mx) a--;
        while (b < n - 1 && sm[b + 1] >= share * mx) b++;
        // локальные вершины и провалы внутри [a, b]
        List<Integer> peaks = new ArrayList<>();
        for (int i = a; i <= b; i++) {
            boolean up = i == a || sm[i] >= sm[i - 1], dn = i == b || sm[i] >= sm[i + 1];
            if (up && dn && (peaks.isEmpty() || i - peaks.get(peaks.size() - 1) > 1)) peaks.add(i);
        }
        // склейка вершин, между которыми провал мельче dip
        boolean merged = true;
        while (merged && peaks.size() > 1) {
            merged = false;
            for (int k = 0; k + 1 < peaks.size(); k++) {
                int x = peaks.get(k), y = peaks.get(k + 1);
                double low = Double.MAX_VALUE; for (int i = x; i <= y; i++) low = Math.min(low, sm[i]);
                if (Math.min(sm[x], sm[y]) - low < dip) { peaks.remove(sm[x] >= sm[y] ? k + 1 : k); merged = true; break; }
            }
        }
        List<int[]> out = new ArrayList<>();
        for (int k = 0; k < peaks.size(); k++) {
            int x = peaks.get(k);
            int lim0 = k == 0 ? a : argMin(sm, peaks.get(k - 1), x), lim1 = k == peaks.size() - 1 ? b : argMin(sm, x, peaks.get(k + 1));
            int lo = x, hi = x;
            while (lo - 1 > lim0 && sm[lo - 1] >= sm[x] - drop) lo--;
            while (hi + 1 < lim1 && sm[hi + 1] >= sm[x] - drop) hi++;
            while (hi - lo < onePt) { if (lo > lim0 && (hi >= lim1 || sm[lo - 1] >= sm[Math.min(n - 1, hi + 1)])) lo--; else if (hi < lim1) hi++; else break; }
            out.add(new int[]{p.low + lo, p.low + hi});
        }
        return out;
    }

    private static int argMin(double[] v, int a, int b) { int m = a; for (int i = a; i <= b; i++) if (v[i] < v[m]) m = i; return m; }

    /** Полоса вокруг POC до обрыва: строка входит, пока её TPO ≥ share × максимум и не ниже
     *  максимума предыдущих look строк (в сторону POC) на cliff принтов и больше. */
    static int[] cliffBand(Profile p, int pocTicks, double share, int cliff, int look) {
        int[] c = p.counts; int i = pocTicks - p.low; double thr = share * p.max();
        int lo = i, hi = i;
        while (hi + 1 < c.length && c[hi + 1] >= thr && (cliff <= 0 || c[hi + 1] > maxOf(c, Math.max(i, hi - look + 1), hi) - cliff)) hi++;
        while (lo - 1 >= 0 && c[lo - 1] >= thr && (cliff <= 0 || c[lo - 1] > maxOf(c, lo, Math.min(i, lo + look - 1)) - cliff)) lo--;
        return new int[]{p.low + lo, p.low + hi};
    }

    private static int maxOf(int[] c, int a, int b) { int m = 0; for (int k = a; k <= b; k++) m = Math.max(m, c[k]); return m; }

    /** Полоса [lo, hi] шире maxW тиков → окно шириной maxW внутри неё, содержащее POC, с максимумом TPO
     *  (при равенстве — с POC ближе к середине). */
    static int[] narrow(Profile p, int[] band, int poc, int maxW) {
        if (maxW <= 0 || band[1] - band[0] <= maxW) return band;
        int bestLo = band[0]; long best = -1; double bestOff = Double.MAX_VALUE;
        for (int lo = Math.max(band[0], poc - maxW); lo <= Math.min(poc, band[1] - maxW); lo++) {
            long sum = 0;
            for (int t = lo; t <= lo + maxW; t++) sum += p.counts[t - p.low];
            double off = Math.abs(lo + maxW / 2.0 - poc);
            if (sum > best || (sum == best && off < bestOff)) { best = sum; bestLo = lo; bestOff = off; }
        }
        return new int[]{bestLo, bestLo + maxW};
    }

    // ------------------------------------------------------------------ первородные зоны
    private static double overlap(int lo1, int hi1, int lo2, int hi2) {
        int inter = Math.min(hi1, hi2) - Math.max(lo1, lo2) + 1;
        return Math.max(inter, 0) / (double) Math.min(hi1 - lo1 + 1, hi2 - lo2 + 1);
    }

    private static final class PZ {
        int lo, hi; final List<LocalDate> days = new ArrayList<>(); LocalDate fixedOn;
        final List<LocalDate> conf = new ArrayList<>();
    }

    private static List<Zone> primaryZones(List<Day> full, int minTpo, int maxTpo, Params p,
                                           Map<LocalDate, LocalDate> nextTradeDay, int kind) {
        double ovMin = p.mergeOverlapMin;
        int maxW = (int) Math.round(p.mergeMaxWidthPts / p.tick);
        List<PZ> fixed = new ArrayList<>();
        PZ cur = null;
        for (Day d : full) {
            boolean ok = d.hvnTpo >= minTpo && d.hvnTpo <= maxTpo;
            if (cur != null) {
                if (overlap(cur.lo, cur.hi, d.bandLo, d.bandHi) >= ovMin) {
                    if (ok && Math.max(cur.hi, d.bandHi) - Math.min(cur.lo, d.bandLo) <= maxW) {
                        cur.lo = Math.min(cur.lo, d.bandLo); cur.hi = Math.max(cur.hi, d.bandHi);
                        cur.days.add(d.date);
                    }
                    continue;
                }
                cur.fixedOn = d.date; fixed.add(cur); cur = null;
            }
            if (!ok) continue;
            PZ best = null; double bestOv = -1;
            for (PZ z : fixed) {
                double ov = overlap(z.lo, z.hi, d.bandLo, d.bandHi);
                if (ov >= ovMin && ov > bestOv) { best = z; bestOv = ov; }
            }
            if (best != null) best.conf.add(d.date);
            else { cur = new PZ(); cur.lo = d.bandLo; cur.hi = d.bandHi; cur.days.add(d.date); }
        }
        List<Zone> out = new ArrayList<>();
        for (PZ z : fixed) {
            LocalDate av = nextTradeDay.get(z.fixedOn);
            if (av == null) continue;
            Zone r = new Zone();
            r.k = kind; r.lo = z.lo * p.tick; r.hi = z.hi * p.tick; r.av = av;
            r.src = "hvn"; r.formationDays = z.days.size(); r.conf = new ArrayList<>(z.conf);
            out.add(r);
        }
        return out;
    }

    // ------------------------------------------------------------------ отторжения
    private static final int W_NONE = 0, W_INSIDE = 1, W_BOUNDARY = 2, W_OUTSIDE = 3;

    /** 0 — нет / проторговка, 1 — лонг-отторжение, 2 — шорт-отторжение (tpo/zone_classes.py). */
    static int interaction(double o, double h, double l, double c, double lo, double hi, double mw) {
        double bot = Math.min(o, c), top = Math.max(o, c);
        int lower = W_OUTSIDE, upper = W_OUTSIDE;
        if (l >= lo && bot <= hi) lower = W_INSIDE;
        if ((l <= hi && bot > hi) || (l <= lo && bot > lo)) lower = W_BOUNDARY;
        if (bot - l < mw) lower = W_NONE;
        if (top >= lo && h <= hi) upper = W_INSIDE;
        if ((top < lo && h >= lo) || (top < hi && h >= hi)) upper = W_BOUNDARY;
        if (h - top < mw) upper = W_NONE;
        boolean touch = l <= hi && h >= lo;
        int out = 0;
        if (touch && lower == W_BOUNDARY && c > hi) out = 1;
        if (touch && upper == W_BOUNDARY && c < lo) out = 2;
        return out;
    }

    // ------------------------------------------------------------------ подгонка границ
    static double[] alignZone(List<Bar> bars, double lo, double hi, double tick, double search, double minWick,
                              double wPlain, double minScore, double minWidth, double maxWidth) {
        int n = bars.size();
        double[] top = new double[n], bot = new double[n];
        boolean[] upW = new boolean[n], dnW = new boolean[n];
        for (int i = 0; i < n; i++) {
            Bar b = bars.get(i);
            top[i] = Math.max(b.o, b.c); bot[i] = Math.min(b.o, b.c);
            upW[i] = (b.h - top[i]) >= minWick; dnW[i] = (bot[i] - b.l) >= minWick;
        }
        double[] h = best(hi, top, bot, upW, dnW, tick, search, wPlain, minScore);
        double[] l = best(lo, top, bot, upW, dnW, tick, search, wPlain, minScore);
        double nh = h[0], nl = l[0];
        if (!(minWidth <= nh - nl && nh - nl <= maxWidth)) return new double[]{lo, hi};
        return new double[]{nl, nh};
    }

    private static double[] best(double edge, double[] top, double[] bot, boolean[] upW, boolean[] dnW,
                                 double tick, double search, double wPlain, double minScore) {
        double start = edge - search, stop = edge + search + tick / 2;
        int len = (int) Math.ceil((stop - start) / tick);
        if (len <= 0) return new double[]{edge, 0};
        double[] grid = new double[len], sc = new double[len], ex = new double[len];
        double topScore = Double.NEGATIVE_INFINITY;
        for (int g = 0; g < len; g++) {
            grid[g] = Math.rint((start + g * tick) / tick) * tick;
            sc[g] = score(grid[g], top, bot, upW, dnW, tick + 1e-9, wPlain);
            ex[g] = score(grid[g], top, bot, upW, dnW, 1e-9, wPlain);
            topScore = Math.max(topScore, sc[g]);
        }
        if (topScore < minScore) return new double[]{edge, topScore};
        double kmax = Double.NEGATIVE_INFINITY;
        for (int g = 0; g < len; g++) kmax = Math.max(kmax, sc[g] + 1e-3 * ex[g]);
        double bestV = edge, bestD = Double.MAX_VALUE;
        for (int g = 0; g < len; g++) {
            if (sc[g] + 1e-3 * ex[g] != kmax) continue;
            double d = Math.abs(grid[g] - edge);
            if (d < bestD) { bestD = d; bestV = grid[g]; }
        }
        return new double[]{bestV, topScore};
    }

    private static double score(double g, double[] top, double[] bot, boolean[] upW, boolean[] dnW,
                                double tol, double wPlain) {
        int anchor = 0, plain = 0;
        for (int i = 0; i < top.length; i++) {
            boolean nt = Math.abs(top[i] - g) <= tol, nb = Math.abs(bot[i] - g) <= tol;
            boolean an = (nt && upW[i]) || (nb && dnW[i]);
            if (an) anchor++; else if (nt || nb) plain++;
        }
        return anchor + wPlain * plain;
    }

    // ------------------------------------------------------------------ состояние и фильтры
    public static State stateAt(Zone z, long o, Params p, long[] fullOrd) {
        State st = new State();
        st.nl = lowerBound(z.L, o); st.ns = lowerBound(z.S, o);
        int tot = st.nl + st.ns;
        if (p.compositeMode && z.k == K_CORE) {
            st.kind = Kind.CORE; st.rej = tot; st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
            st.age = o - z.avOrd(); st.conf = lowerBound(z.coreConf, o);
            return st;
        }
        if (p.compositeMode && z.k == K_SESS) {
            int ri = lowerBound(z.rcAt, o + 1) - 1;                 // последняя синхронизация к сессии o
            boolean rc = ri >= 0;
            st.kind = rc ? z.rcKind[ri] : z.partKind; st.comp = rc ? (z.rcComp[ri].live ? z.rcComp[ri] : null) : z.partOf;
            st.recolored = rc;
            if (!rc && z.partOf == null && p.sessRoleMode == 2) {     // мало информации — серая, пока не наберёт отторжений
                int idx = z.ai + p.sessConfirm;
                long f = idx < fullOrd.length ? fullOrd[idx] : Long.MAX_VALUE;
                if (o < f) st.kind = Kind.GREY;
                else {
                    int nl = lowerBound(z.L, f), ns = lowerBound(z.S, f);
                    st.kind = nl > ns ? Kind.HVN_S : ns > nl ? Kind.HVN_R : Kind.GREY;
                }
            }
            st.rej = tot; st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
            st.age = o - z.avOrd(); st.conf = lowerBound(z.coreConf, o);
            return st;
        }
        if (p.compositeMode && z.k == K_PART) {
            st.kind = z.partKind; st.comp = z.partOf; st.rej = tot; st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
            st.age = o - z.avOrd(); st.conf = lowerBound(z.coreConf, o);
            return st;
        }
        if (p.compositeMode && p.partition && (z.k == K_HVN || z.k == K_GREY) && o >= z.hideFrom) { st.kind = null; return st; }
        if (p.compositeMode && z.fixedGrey) {
            st.kind = Kind.GREY; st.rej = tot; st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
            st.age = o - z.avOrd();
            return st;
        }
        if (p.compositeMode && (z.k == K_HVN || z.k == K_GREY)) {
            // класс фиксируется по композиту, правившему при появлении зоны (или по первому, накрывшему её позже)
            int gi = lowerBound(z.govAv, z.avOrd() + 1) - 1;
            if (gi < 0 && z.govAv.length > 0) gi = 0;
            if (gi >= 0 && z.govAv[gi] > o) gi = -1;          // этот композит ещё не сложился — пока серая
            // зона, родившаяся во время композита, переходит к нему, когда он сложится
            for (int k = z.gov.length - 1; k >= 0; k--) {
                Comp c = z.gov[k];
                if (!c.start.isAfter(z.av) && !z.av.isAfter(c.av)) { if (c.av.toEpochDay() <= o) gi = k; break; }
            }
            Kind kind;
            if (gi < 0) kind = Kind.GREY;
            else {
                Comp c = z.gov[gi]; st.comp = c;
                Zone core = c.core;
                double w = Math.max(z.bhi - z.blo, p.tick);
                double inter = Math.min(z.bhi, core.bhi) - Math.max(z.blo, core.blo);
                double mid = (z.blo + z.bhi) / 2;
                kind = inter > 0 && inter / w > p.coreAbsorb ? null : mid > core.bhi ? Kind.HVN_R : mid < core.blo ? Kind.HVN_S : null;
            }
            st.kind = kind; st.rej = tot; st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
            st.age = o - z.avOrd();
            int conf = 0; long cnear = Math.round(p.confirmNearPts * 100);
            for (long[] c : z.cf) { if (c[0] >= o) break; if (c[1] <= cnear) conf++; }
            st.conf = conf;
            return st;
        }
        Kind kind = z.k == K_OLD_BALANCE ? Kind.OLD_BALANCE : z.k == K_GREY ? Kind.GREY : null;
        if (kind == null && z.young && (z.greyUntil == null || o < z.greyUntil.toEpochDay())) kind = Kind.GREY;
        if (kind == Kind.GREY) {
            Long pr = promotedAt(z, p, fullOrd);
            if (pr != null && o >= pr) { kind = null; st.promoted = true; }
        }
        if (kind == null) {
            kind = tot == 0 ? Kind.HVN_NEW : (double) st.nl / tot >= p.rejectionShare ? Kind.HVN_S
                    : (double) st.ns / tot >= p.rejectionShare ? Kind.HVN_R : Kind.HVN_RANGE;
        }
        st.kind = kind;
        st.rej = tot;
        st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
        st.age = o - z.avOrd();
        int conf = 0;
        long cnear = Math.round(p.confirmNearPts * 100);
        for (long[] c : z.cf) { if (c[0] >= o) break; if (c[1] <= cnear) conf++; }
        st.conf = conf;
        return st;
    }

    /** День перевода серой зоны в основную или null (≥ promoteRejections отторжений за сессии подгонки). */
    static Long promotedAt(Zone z, Params p, long[] fullOrd) {
        if (p.alignSessions <= 0 || p.promoteRejections <= 0) return null;
        int idx = z.ai + p.alignSessions;
        if (idx >= fullOrd.length) return null;
        long f = fullOrd[idx];
        return lowerBound(z.L, f) + lowerBound(z.S, f) >= p.promoteRejections ? f : null;
    }

    static boolean passes(Zone z, State st, Params p) {
        if (st.kind == null || !p.types.contains(st.kind)) return false;
        if (st.conf < p.minConfirmations || st.rej < p.minRejections || st.age < p.minAgeDays) return false;
        if (p.purityMinPct > 0 && (st.purity == null || st.purity * 100 < p.purityMinPct)) return false;
        if (p.purityMaxPct < 100 && (st.purity == null || st.purity * 100 > p.purityMaxPct)) return false;
        return !(p.maxWidthPts < 60 && z.bhi - z.blo > p.maxWidthPts);
    }

    // ------------------------------------------------------------------ утилиты
    static int ticks(double price, double tick) { return (int) Math.rint(price / tick); }

    static int lowerBound(long[] a, long v) {
        int lo = 0, hi = a.length;
        while (lo < hi) { int m = (lo + hi) >>> 1; if (a[m] < v) lo = m + 1; else hi = m; }
        return lo;
    }

    // ------------------------------------------------------------------ последние сессии
    /** Тесты и отторжения зоны на 30m-барах (все бары — ETH + RTH) последних сессий. */
    public static final class Recent {
        public final Zone z; public int tests, longs, shorts;
        Recent(Zone z) { this.z = z; }
        public int rejections() { return longs + shorts; }
    }

    /** Для каждой зоны, видимой хотя бы в одну из последних {@code lastSessions} показываемых сессий:
     *  тест — бар задел зону (границы после подгонки); отторжение — хвост пересёк границу и close
     *  за зоной (правила tpo/zone_classes.py). Сортировка: отторжения, затем тесты, по убыванию. */
    public static List<Recent> recent(List<Bar> bars, Result res, Params p, int lastSessions) {
        int n = res.sessions.size(), from = Math.max(0, n - lastSessions);
        if (n == 0) return List.of();
        long t0 = res.sessions.get(from).startMs;
        Map<Zone, Recent> out = new java.util.LinkedHashMap<>();
        for (int i = from; i < n; i++) for (Shown v : res.visible.get(i)) out.computeIfAbsent(v.z, Recent::new);
        double mw = p.minWickTicks * p.tick;
        if (!res.bars.isEmpty()) bars = res.bars;              // цены после склейки роллов
        for (Bar b : bars) {
            if (b.t < t0) continue;
            LocalDateTime lt = LocalDateTime.ofInstant(Instant.ofEpochMilli(b.t), p.tz);
            LocalDate d = lt.getHour() * 60 + lt.getMinute() >= p.ethStartMin ? lt.toLocalDate().plusDays(1) : lt.toLocalDate();
            long ord = d.toEpochDay();
            for (Recent r : out.values()) {
                if (r.z.avOrd() > ord) continue;              // зона ещё не появилась
                if (b.l > r.z.bhi || b.h < r.z.blo) continue;
                r.tests++;
                int t = interaction(b.o, b.h, b.l, b.c, r.z.blo, r.z.bhi, mw);
                if (t == 1) r.longs++; else if (t == 2) r.shorts++;
            }
        }
        List<Recent> l = new ArrayList<>(out.values());
        l.sort((a, b) -> a.rejections() != b.rejections() ? b.rejections() - a.rejections() : b.tests - a.tests);
        return l;
    }

    private ZoneEngine() {}
}
