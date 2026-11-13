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
 *   6. фильтры страницы: типы, «ближайшие к open сессии», отторжения, чистота,
 *      подтверждения, возраст, ширина; серая скрывается, если на > 30% заходит на основную.
 * Все цены профиля — в целых тиках, как в tpo/profile.py.
 */
public final class ZoneEngine {

    // ------------------------------------------------------------------ виды зон
    public static final int K_OLD_BALANCE = 0, K_HVN = 1, K_GREY = 5;

    public enum Kind {
        HVN_S("HVN · поддержка"), HVN_R("HVN · сопротивление"), HVN_RANGE("HVN · проторговка"),
        HVN_NEW("HVN · без истории"), OLD_BALANCE("Старый баланс (VA композита)"),
        GREY("Серая (мало истории / узкая)");
        public final String title;
        Kind(String t) { title = t; }
    }

    // ------------------------------------------------------------------ параметры
    /** Все пороги — как в config.yaml и DEF страницы карты зон. */
    public static final class Params {
        // данные и сессии
        public double tick = 0.25;
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
                Kind.HVN_S, Kind.HVN_R, Kind.HVN_RANGE, Kind.HVN_NEW, Kind.GREY));
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
        final List<int[]> periods = new ArrayList<>();   // {period, lowTicks, highTicks}
        Profile profile; int poc, val, vah;              // тики
        int hvnTpo, bandLo, bandHi;                      // per_day (только полные дни)
    }

    // ------------------------------------------------------------------ зона
    public static final class Zone {
        public int k; public double lo, hi; public LocalDate av; public String src;
        public boolean young; public LocalDate greyUntil;   // young && greyUntil == null — серая всё время
        int formationDays; List<LocalDate> conf = List.of();
        // события (дни от 1970-01-01)
        public long[] L = new long[0], S = new long[0];
        public final List<long[]> cf = new ArrayList<>();   // {ord, gap*100}
        public int ai;                                       // индекс av в списке полных RTH-дней
        public double blo, bhi;                              // границы после подгонки
        public long avOrd() { return av.toEpochDay(); }
    }

    /** Состояние зоны на сессию. */
    public static final class State {
        public Kind kind; public boolean promoted; public int rej, nl, ns, conf; public Double purity; public long age;
    }

    public static final class Session {
        public LocalDate date; public long ord; public int firstBar, lastBar; public long startMs, endMs;
        public double open; public Double prevVah, prevVal, prevPoc; public LocalDate prevDate;
    }

    /** Видимая в сессию зона. */
    public static final class Shown {
        public final Zone z; public final State st;
        Shown(Zone z, State st) { this.z = z; this.st = st; }
    }

    public static final class Result {
        public final List<Zone> zones = new ArrayList<>();
        public final List<Session> sessions = new ArrayList<>();     // показываемые сессии
        public final List<List<Shown>> visible = new ArrayList<>();  // по сессиям
        public int rthDays, fullDays, composites;
        public String note = "";
    }

    // ================================================================== расчёт
    public static Result run(List<Bar> bars, Params p) {
        Result res = new Result();
        double tick = p.tick;
        bars = new ArrayList<>(bars);
        bars.sort((a, b) -> Long.compare(a.t, b.t));

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
                day.periods.add(new int[]{per, ticks(b.l, tick), ticks(b.h, tick)});
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
                int[] band = hvnBand(d.profile, d.poc, p.hvnShare);
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
            for (Day d : full) {
                if (cand.isEmpty()) { cand.add(d); cp = d.profile; continue; }
                int c = ticks(d.close, tick);
                if (c >= cp.low && c <= cp.high()) { cand.add(d); cp = merge(cp, d.profile); continue; }
                closeComposite(cand, cp, d.date, p, ob, greyComp);
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
            double[] r = alignZone(bars.subList(a, e), z.lo, z.hi, tick, search, mw, p.alignWPlain,
                    p.alignMinScore, p.alignMinWidthTicks * tick, maxW);
            z.blo = Math.rint(r[0] / tick) * tick; z.bhi = Math.rint(r[1] / tick) * tick;
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
            for (Zone z : keep) if (z.avOrd() <= s.ord) list.add(new Shown(z, stateAt(z, s.ord, p, fullOrd)));
            List<Shown> out = new ArrayList<>();
            for (Shown v : list) {
                if (v.st.kind != Kind.GREY) { out.add(v); continue; }
                boolean hide = false;
                for (Shown m : list) {
                    if (m.st.kind == Kind.GREY) continue;
                    double inter = Math.min(v.z.bhi, m.z.bhi) - Math.max(v.z.blo, m.z.blo);
                    if (inter > 0 && inter / Math.max(v.z.bhi - v.z.blo, tick) > p.greyMaxOverlap) { hide = true; break; }
                }
                if (!hide) out.add(v);
            }
            res.visible.add(out);
        }
        return res;
    }

    // ------------------------------------------------------------------ композиты
    private static void closeComposite(List<Day> cand, Profile cp, LocalDate endDay, Params p,
                                       List<Zone> ob, List<Zone> greyComp) {
        int n = cand.size();
        if (n < p.greyCompositeMinDays) return;
        int[] va = valueArea(cp, p.valueAreaPct);
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
        if (!p.types.contains(st.kind)) return false;
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

    private ZoneEngine() {}
}
