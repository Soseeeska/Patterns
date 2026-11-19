package patterns.zones;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.TreeMap;

import patterns.zones.ZoneEngine.Bar;
import patterns.zones.ZoneEngine.Params;
import patterns.zones.ZoneEngine.Result;
import patterns.zones.ZoneEngine.Shown;

/** Опоздание зон в живой торговле. Индикатор пересчитывает зоны раз в Globex-сессию — на её первом 30m-баре (17:00 CT).
 *  Для каждого RTH-дня D: движок на барах по первый бар следующей сессии включительно (так считает индикатор в 17:00)
 *  и на барах по первый RTH-бар следующего торгового дня (08:30). Зоны дня D (появление после D) должны быть уже в
 *  17:00: печатает, сколько их в каждом прогоне, и строку «НЕТ В 17:00», если в 17:00 их меньше.
 *  java -cp <classes> patterns.zones.LiveLag bars_raw.csv с_даты по_дату [поле=значение ...]
 *  -Dhist=1500 — глубина истории, календарных дней (как MotiveWave). */
public class LiveLag {
    public static void main(String[] a) throws Exception {
        List<Bar> bars = Check.read(a[0]);
        LocalDate from = LocalDate.parse(a[1]), to = LocalDate.parse(a[2]);
        int hist = Integer.getInteger("hist", 1500);
        Params p = new Params();
        p.compositeMode = true; p.zoneMaxWidthPts = 10;   // как в индикаторе
        p.sessions = 5;                                  // нужна только последняя сессия
        for (int i = 3; i < a.length; i++) Check.set(p, a[i]);
        ZoneId tz = p.tz;
        int n = bars.size();
        LocalDate[] sess = new LocalDate[n];
        TreeMap<LocalDate, Integer> rthFirst = new TreeMap<>();   // RTH-день → индекс его первого бара
        for (int i = 0; i < n; i++) {
            LocalDateTime lt = LocalDateTime.ofInstant(Instant.ofEpochMilli(bars.get(i).t), tz);
            int m = lt.getHour() * 60 + lt.getMinute();
            sess[i] = m >= p.ethStartMin ? lt.toLocalDate().plusDays(1) : lt.toLocalDate();
            if (m >= p.rthStartMin && m < p.rthEndMin) rthFirst.putIfAbsent(lt.toLocalDate(), i);
        }
        int bad = 0, days = 0, diff = 0;
        for (LocalDate d : rthFirst.subMap(from, true, to, true).keySet()) {
            LocalDate next = rthFirst.higherKey(d);
            if (next == null) continue;
            int i17 = rthFirst.get(d);                     // первый бар следующей сессии (17:00 после D)
            while (i17 < n && !sess[i17].isAfter(d)) i17++;
            int i0830 = rthFirst.get(next);
            if (i17 >= n || i17 > i0830) continue;
            long t0 = d.minusDays(hist).atStartOfDay(tz).toInstant().toEpochMilli();
            Snap a17 = snap(bars, t0, i17, d, p), a0830 = snap(bars, t0, i0830, d, p);
            days++;
            boolean miss = a17.zones.size() < a0830.zones.size() || a17.live < a0830.live;
            boolean same = a17.zones.equals(a0830.zones);
            if (miss) bad++; else if (!same) diff++;
            System.out.printf(Locale.ROOT, "%s  17:00 (сессия %s): зон дня %d, снимков композита %d, новейшая %s | 08:30 %s: %d, %d, %s%s%n",
                    d, sess[i17], a17.zones.size(), a17.live, a17.newest, next, a0830.zones.size(), a0830.live, a0830.newest,
                    miss ? "  НЕТ В 17:00" : same ? "" : "  иначе: " + a17.zones + " / " + a0830.zones);
        }
        System.out.printf("дней %d, зоны дня опаздывают в %d, есть, но отличаются (края / вид) в %d%n", days, bad, diff);
    }

    static class Snap { List<String> zones = new ArrayList<>(); int live; LocalDate newest; }

    /** Движок на барах [t0, last]: видимые в последней сессии зоны, появившиеся после дня d (края и вид), снимки
     *  формирующегося композита после d, новейшее появление зоны. */
    static Snap snap(List<Bar> bars, long t0, int last, LocalDate d, Params p) {
        List<Bar> cut = new ArrayList<>();
        for (int i = 0; i <= last; i++) if (bars.get(i).t >= t0) cut.add(bars.get(i));
        Result res = ZoneEngine.run(cut, p);
        Snap s = new Snap();
        if (res.sessions.isEmpty()) return s;
        for (Shown v : res.visible.get(res.visible.size() - 1)) {
            if (v.z.av.isAfter(d)) s.zones.add(String.format(Locale.ROOT, "%.2f-%.2f:%s", v.lo, v.hi, v.st.kind));
            if (s.newest == null || v.z.av.isAfter(s.newest)) s.newest = v.z.av;
        }
        java.util.Collections.sort(s.zones);
        for (ZoneEngine.Comp c : res.liveComps) if (c.av.isAfter(d)) s.live++;
        return s;
    }
}
