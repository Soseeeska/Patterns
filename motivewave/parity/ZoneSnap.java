package patterns.zones;

import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.TreeMap;

import patterns.zones.ZoneEngine.Bar;
import patterns.zones.ZoneEngine.Comp;
import patterns.zones.ZoneEngine.Params;
import patterns.zones.ZoneEngine.Result;
import patterns.zones.ZoneEngine.Session;
import patterns.zones.ZoneEngine.Shown;
import patterns.zones.ZoneEngine.Zone;

/** Зоны 1.8 на каждый RTH-день так, как их видит индикатор к открытию RTH этого дня — без заглядывания вперёд.
 *  Для дня D движку отдаются бары [D − hist дней, 08:30 CT дня D) и одна плоская свеча 08:30 по последнему close
 *  (без неё движок не знает, что D — следующий торговый день, и зоны вчерашней сессии не появляются).
 *  Пишет CSV: день × видимая зона (границы на графике, вид, почему серая, правящий композит, отторжения).
 *  java -cp <classes> patterns.zones.ZoneSnap bars_raw.csv out.csv с_даты по_дату [поле=значение ...]
 *  -Dhist=1500 — глубина истории, календарных дней (как MotiveWave). */
public class ZoneSnap {
    public static void main(String[] a) throws Exception {
        List<Bar> bars = Check.read(a[0]);
        LocalDate from = LocalDate.parse(a[2]), to = LocalDate.parse(a[3]);
        int hist = Integer.getInteger("hist", 1500);
        Params p = new Params();
        p.compositeMode = true; p.zoneMaxWidthPts = 10;   // как в индикаторе
        p.sessions = 5;                                  // нужна только последняя сессия
        for (int i = 4; i < a.length; i++) Check.set(p, a[i]);
        ZoneId tz = p.tz;
        TreeMap<LocalDate, Integer> rthFirst = new TreeMap<>();   // RTH-день → индекс его первого бара
        for (int i = 0; i < bars.size(); i++) {
            LocalDateTime lt = LocalDateTime.ofInstant(Instant.ofEpochMilli(bars.get(i).t), tz);
            int m = lt.getHour() * 60 + lt.getMinute();
            if (m >= p.rthStartMin && m < p.rthEndMin) rthFirst.putIfAbsent(lt.toLocalDate(), i);
        }
        try (PrintWriter w = new PrintWriter(new OutputStreamWriter(new FileOutputStream(a[1]), "UTF-8"))) {
            w.println("day,lo,hi,kind,av,born,src,zlo,zhi,minor,sync_minor,va_major,inverted,wavering,primary_color,near_core,"
                    + "age,rej,nl,ns,conf,comp_start,comp_end,comp_lo,comp_hi,core_lo,core_hi,comp_live,comp_primary,grey_cause,"
                    + "prev_vah,prev_val,prev_poc");
            for (LocalDate d : rthFirst.subMap(from, true, to, true).keySet()) {
                int i0 = rthFirst.get(d);
                long t0 = d.minusDays(hist).atStartOfDay(tz).toInstant().toEpochMilli();
                List<Bar> cut = new ArrayList<>();
                for (int i = 0; i < i0; i++) if (bars.get(i).t >= t0) cut.add(bars.get(i));
                if (cut.isEmpty()) continue;
                double c = cut.get(cut.size() - 1).c;
                cut.add(new Bar(bars.get(i0).t, c, c, c, c));
                Result res = ZoneEngine.run(cut, p);
                if (res.sessions.isEmpty()) continue;
                int n = res.sessions.size();
                Session s = res.sessions.get(n - 1);
                if (!s.date.equals(d)) { System.err.println("пропуск " + d + ": последняя сессия " + s.date); continue; }
                for (Shown v : res.visible.get(n - 1)) {
                    Zone z = v.z; Comp cp = v.st.comp;
                    w.println(String.join(",", d.toString(), f(v.lo), f(v.hi), v.st.kind.name(), String.valueOf(z.av),
                            String.valueOf(z.born), String.valueOf(z.src), f(z.lo), f(z.hi), b(z.minor && s.ord < z.minorEnd),
                            b(z.syncMinor), String.valueOf(z.vaMajor), b(z.inverted), b(z.wavering), b(z.primaryColor),
                            b(z.nearCore), String.valueOf(v.st.age), String.valueOf(v.st.rej), String.valueOf(v.st.nl),
                            String.valueOf(v.st.ns), String.valueOf(v.st.conf),
                            cp == null ? "" : String.valueOf(cp.start), cp == null ? "" : String.valueOf(cp.end),
                            cp == null ? "" : f(cp.lo), cp == null ? "" : f(cp.hi),
                            cp == null ? "" : f(cp.core.blo), cp == null ? "" : f(cp.core.bhi),
                            cp == null ? "" : b(cp.live), cp == null ? "" : b(cp.primary),
                            v.st.kind == ZoneEngine.Kind.GREY ? '"' + Check.cause(z, s.ord) + '"' : "",
                            s.prevVah == null ? "" : f(s.prevVah), s.prevVal == null ? "" : f(s.prevVal),
                            s.prevPoc == null ? "" : f(s.prevPoc)));
                }
                w.flush();
            }
        }
    }

    static String f(double v) { return String.format(Locale.ROOT, "%.2f", v); }
    static String b(boolean v) { return v ? "1" : "0"; }
}
