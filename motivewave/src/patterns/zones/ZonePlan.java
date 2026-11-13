package patterns.zones;

import java.time.LocalDate;
import java.time.LocalTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import patterns.zones.ZoneEngine.Kind;
import patterns.zones.ZoneEngine.Params;
import patterns.zones.ZoneEngine.Result;
import patterns.zones.ZoneEngine.Session;
import patterns.zones.ZoneEngine.Shown;
import patterns.zones.ZoneEngine.Zone;

/** Что рисовать: прямоугольники зон и уровни вчерашнего профиля во времени. Без зависимостей от SDK. */
public final class ZonePlan {

    /** Зона на подряд идущих сессиях с одним видом. */
    public static final class Rect {
        public final Zone z; public final Kind kind; public final long t0, t1; public final String tip;
        Rect(Zone z, Kind kind, long t0, long t1, String tip) { this.z = z; this.kind = kind; this.t0 = t0; this.t1 = t1; this.tip = tip; }
    }

    /** Уровень вчерашнего RTH-профиля на сессию. */
    public static final class Level {
        public final long t0, t1; public final double price; public final boolean poc; public final String tip;
        Level(long t0, long t1, double price, boolean poc, String tip) { this.t0 = t0; this.t1 = t1; this.price = price; this.poc = poc; this.tip = tip; }
    }

    public final List<Rect> rects = new ArrayList<>();
    public final List<Level> levels = new ArrayList<>();

    public static ZonePlan of(Result res, Params p) {
        ZonePlan plan = new ZonePlan();
        List<Session> ss = res.sessions;
        int n = ss.size();
        if (n == 0) return plan;
        long[] t0 = new long[n], t1 = new long[n];
        for (int i = 0; i < n; i++) { t0[i] = ss.get(i).startMs; t1[i] = ss.get(i).endMs; }
        // текущая сессия — до планового конца (16:00 по часовому поясу сессий), чтобы зоны шли вправо по мере новых баров
        Session last = ss.get(n - 1);
        long plannedEnd = last.date.atTime(LocalTime.of(16, 0)).atZone(p.tz).toInstant().toEpochMilli();
        if (plannedEnd > t1[n - 1]) t1[n - 1] = plannedEnd;

        Map<Zone, Shown[]> byZone = new HashMap<>();
        List<Zone> order = new ArrayList<>();
        for (int i = 0; i < n; i++)
            for (Shown v : res.visible.get(i))
                byZone.computeIfAbsent(v.z, z -> { order.add(z); return new Shown[n]; })[i] = v;
        for (Zone z : order) {
            Shown[] row = byZone.get(z);
            int i = 0;
            while (i < n) {
                if (row[i] == null) { i++; continue; }
                int j = i;
                while (j + 1 < n && row[j + 1] != null && row[j + 1].st.kind == row[i].st.kind) j++;
                plan.rects.add(new Rect(z, row[i].st.kind, t0[i], t1[j], tip(z, row[j], ss.get(j).date, p)));
                i = j + 1;
            }
        }
        for (int i = 0; i < n; i++) {
            Session s = ss.get(i);
            if (s.prevPoc == null) continue;
            String d = "Вчера (" + s.prevDate + ") ";
            plan.levels.add(new Level(t0[i], t1[i], s.prevVah, false, d + "VAH " + fmt(s.prevVah)));
            plan.levels.add(new Level(t0[i], t1[i], s.prevVal, false, d + "VAL " + fmt(s.prevVal)));
            plan.levels.add(new Level(t0[i], t1[i], s.prevPoc, true, d + "POC " + fmt(s.prevPoc)));
        }
        return plan;
    }

    static String tip(Zone z, Shown v, LocalDate on, Params p) {
        StringBuilder b = new StringBuilder();
        b.append(v.st.kind.title).append("  ").append(fmt(z.blo)).append(" – ").append(fmt(z.bhi));
        b.append("\nпоявилась ").append(z.av).append(", возраст ").append(v.st.age).append(" дн. (на ").append(on).append(')');
        b.append("\nотторжений ").append(v.st.rej).append(" (лонг ").append(v.st.nl).append(" / шорт ").append(v.st.ns);
        if (v.st.purity != null) b.append(", чистота ").append(Math.round(v.st.purity * 100)).append('%');
        b.append(")\nподтверждений ").append(v.st.conf);
        if (z.k == ZoneEngine.K_GREY) b.append("\nисточник: ").append("comp".equals(z.src) ? "короткий композит (2 дня)" : "слабый HVN (5–7 TPO)");
        if (z.young) b.append("\nмолодая первородная: серая ").append(z.greyUntil == null ? "до конца данных" : "до " + z.greyUntil);
        if (v.st.promoted) b.append("\nбыла серой, переведена по отторжениям за подгонку");
        if (p.alignSessions > 0 && (z.blo != z.lo || z.bhi != z.hi))
            b.append("\nподгонка по ").append(p.alignSessions).append(" сесс.: была ").append(fmt(z.lo)).append(" – ").append(fmt(z.hi));
        return b.toString();
    }

    static String fmt(double v) { return String.format(Locale.ROOT, "%.2f", v); }

    private ZonePlan() {}
}
