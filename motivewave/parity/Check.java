package patterns.zones;

import java.io.BufferedReader;
import java.io.FileReader;
import java.util.ArrayList;
import java.util.BitSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

import patterns.zones.ZoneEngine.Bar;
import patterns.zones.ZoneEngine.Kind;
import patterns.zones.ZoneEngine.Params;
import patterns.zones.ZoneEngine.Result;
import patterns.zones.ZoneEngine.Shown;
import patterns.zones.ZoneEngine.Zone;

/** Сводка по карте зон на последнюю сессию: виды, почему серые (по последнему правилу, которое посерело), ширины, плотность
 *  у цены, пересечения прямоугольников на графике, разрывы и обрывы зон до правого края.
 *  java -cp <classes> patterns.zones.Check bars_raw.csv [поле=значение ...]   (без полей — как индикатор)
 *  -Dfrom=YYYY-MM-DD — история с этой даты (MotiveWave грузит 1500 дней назад от сегодня), -Dto=YYYY-MM-DD — данные по дату. */
public class Check {
    public static void main(String[] a) throws Exception {
        List<Bar> bars = read(a[0]);
        Params p = new Params();
        p.compositeMode = true; p.zoneMaxWidthPts = 10;   // как в индикаторе
        for (int i = 1; i < a.length; i++) set(p, a[i]);
        Result res = ZoneEngine.run(bars, p);
        int n = res.visible.size();
        List<Shown> last = res.visible.get(n - 1);
        long o = res.sessions.get(n - 1).ord;
        Map<Kind, Integer> kinds = new TreeMap<>(); Map<String, Integer> grey = new TreeMap<>(); TreeMap<Double, Integer> w = new TreeMap<>();
        for (Shown v : last) {
            kinds.merge(v.st.kind, 1, Integer::sum);
            w.merge(Math.rint((v.hi - v.lo) * 4) / 4, 1, Integer::sum);
            if (v.st.kind == Kind.GREY) grey.merge(cause(v.z, o), 1, Integer::sum);
        }
        System.out.printf(Locale.ROOT, "сессий %d (%s … %s), зон в последнюю сессию %d%n", n, res.sessions.get(0).date, res.sessions.get(n - 1).date, last.size());
        System.out.println("виды: " + kinds);
        System.out.println("серые — почему:"); grey.forEach((k, v) -> System.out.printf(Locale.ROOT, "  %-62s %4d%n", k, v));
        System.out.print("ширина, п.:"); w.forEach((k, v) -> System.out.printf(Locale.ROOT, " %.2f→%d", k, v)); System.out.println();
        double open = res.sessions.get(n - 1).open; int near = 0;
        for (Shown v : last) if (Math.abs((v.lo + v.hi) / 2 - open) <= 150) near++;
        System.out.printf(Locale.ROOT, "в ± 150 п. от open последней сессии: %d зон (%.1f на 100 п.)%n", near, near / 3.0);
        // пересечения прямоугольников на графике и непрерывность зон
        ZonePlan plan = ZonePlan.of(res, p);
        int overl = 0;
        for (int i = 0; i < plan.rects.size(); i++) for (int j = i + 1; j < plan.rects.size(); j++) {
            ZonePlan.Rect x = plan.rects.get(i), y = plan.rects.get(j);
            if (x.z == y.z || x.t1 <= y.t0 || y.t1 <= x.t0) continue;
            if (Math.min(x.hi, y.hi) - Math.max(x.lo, y.lo) > 1e-9) overl++;
        }
        Map<Zone, BitSet> vis = new LinkedHashMap<>();
        for (int i = 0; i < n; i++) for (Shown v : res.visible.get(i)) vis.computeIfAbsent(v.z, z -> new BitSet(n)).set(i);
        int gaps = 0, ends = 0;
        for (BitSet b : vis.values()) { int f = b.nextSetBit(0), l = b.length() - 1; if (b.cardinality() != l - f + 1) gaps++; if (l != n - 1) ends++; }
        System.out.printf("пересекающихся прямоугольников на графике %d; зон с разрывами %d; обрываются до правого края %d%n", overl, gaps, ends);
        int ex = 0, exS = 0, exNear = 0;
        for (Shown v : last) if (v.z.ext) { ex++; if (v.st.kind == Kind.HVN_S) exS++; if (Math.abs((v.lo + v.hi) / 2 - open) <= 150) exNear++; }
        System.out.printf("из них зон экстремумов: %d (зелёных %d, красных %d), в ± 150 п. от open %d%n", ex, exS, ex - exS, exNear);
    }

    /** Последнее правило, которое сделало зону серой (правила идут по порядку, см. README «Серые»). */
    static String cause(Zone z, long o) {
        if (z.syncMinor) return "синхронизация с композитом: не истинная в узле / в провале";
        if (z.inverted) return "стык «зелёные над красными»";
        if (z.oneSquare) return "узкая (oneSquareGrey)";
        if (z.wavering) return "колеблется в своём композите";
        if (z.narrow) return "скопление уже квадрата (gridNarrowGrey)";
        if (z.minor && o < z.minorEnd) return "минорный узел сессии";
        return "вне композита: отторжений поровну, сессия закрылась внутри";
    }

    /** Бары CSV (export_bars.py), с учётом -Dfrom / -Dto. */
    static List<Bar> read(String file) throws Exception {
        List<Bar> bars = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new FileReader(file))) {
            for (String s; (s = r.readLine()) != null; ) {
                String[] f = s.split(",");
                bars.add(new Bar(Long.parseLong(f[0]), Double.parseDouble(f[1]), Double.parseDouble(f[2]), Double.parseDouble(f[3]), Double.parseDouble(f[4])));
            }
        }
        java.time.ZoneId tz = java.time.ZoneId.of("America/Chicago");
        String from = System.getProperty("from"), to = System.getProperty("to");
        if (from != null) { long t = java.time.LocalDate.parse(from).atStartOfDay(tz).toInstant().toEpochMilli(); bars.removeIf(b -> b.t < t); }
        if (to != null) { long t = java.time.LocalDate.parse(to).plusDays(1).atTime(16, 0).atZone(tz).toInstant().toEpochMilli(); bars.removeIf(b -> b.t >= t); }
        return bars;
    }

    static void set(Params p, String kv) throws Exception {
        String[] f = kv.split("=", 2);
        java.lang.reflect.Field fd = Params.class.getField(f[0]);
        Class<?> t = fd.getType();
        fd.set(p, t == boolean.class ? (Object) Boolean.parseBoolean(f[1]) : t == int.class ? (Object) Integer.parseInt(f[1]) : (Object) Double.parseDouble(f[1]));
    }
}
