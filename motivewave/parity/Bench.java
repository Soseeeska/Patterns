import java.io.BufferedReader;
import java.io.FileReader;
import java.lang.reflect.Field;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

import patterns.zones.ZoneEngine;
import patterns.zones.ZoneEngine.Bar;
import patterns.zones.ZoneEngine.Kind;
import patterns.zones.ZoneEngine.Params;
import patterns.zones.ZoneEngine.Result;
import patterns.zones.ZoneEngine.Shown;

/** Замер движка против ручных зон (output/manual_zones_bench.csv из scripts/manual_zones.py).
 *  java Bench bars.csv manual.csv [поле=значение ...]   (поля ZoneEngine.Params, например compositeMode=true)
 *
 *  Для ручной зоны (цвет, сессия начала, lo, hi): лучшая по IoU видимая зона алгоритма в сессиях
 *  [начало, начало + 10). Совпадение — IoU ≥ 0.3 / ≥ 0.5. Цвет: зелёная — поддержка, красная —
 *  сопротивление, синяя — ядро / проторговка / старый баланс, серая — серая / без истории.
 *  Плотность: в сессию после начала — зон алгоритма с серединой в ± 15 п. от ручной против ручных
 *  зон (начатых к этой сессии) там же. */
public class Bench {
    public static void main(String[] a) throws Exception {
        List<Bar> bars = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new FileReader(a[0]))) {
            for (String s; (s = r.readLine()) != null; ) {
                String[] f = s.split(",");
                bars.add(new Bar(Long.parseLong(f[0]), Double.parseDouble(f[1]), Double.parseDouble(f[2]), Double.parseDouble(f[3]), Double.parseDouble(f[4])));
            }
        }
        List<String[]> man = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new FileReader(a[1]))) { for (String s; (s = r.readLine()) != null; ) man.add(s.split(",")); }
        Params p = new Params();
        p.rollAdjust = Boolean.getBoolean("roll");   // бары из export_bars уже склеены; -Droll=true — сырые бары (--raw), как в MotiveWave
        p.sessions = 1000; p.zoneMaxWidthPts = 10;
        for (int i = 2; i < a.length; i++) {
            String[] kv = a[i].split("=");
            Field f = Params.class.getField(kv[0]);
            Class<?> t = f.getType();
            f.set(p, t == boolean.class ? (Object) Boolean.parseBoolean(kv[1]) : t == int.class ? (Object) Integer.parseInt(kv[1]) : (Object) Double.parseDouble(kv[1]));
        }
        Result res = ZoneEngine.run(bars, p);
        if (p.rollAdjust && res.rollMs.length > 0)         // ручные зоны в ценах графика (manual_zones_bench_raw.csv) → в цены движка
            for (String[] mz : man) {
                long t = LocalDate.parse(mz[1]).atStartOfDay(p.tz).toInstant().toEpochMilli();
                double sh = ZoneEngine.rollShift(res, t);
                mz[2] = String.valueOf(Double.parseDouble(mz[2]) + sh); mz[3] = String.valueOf(Double.parseDouble(mz[3]) + sh);
            }
        long[] ord = res.sessions.stream().mapToLong(s -> s.ord).toArray();
        int n = 0, m3 = 0, m5 = 0, colorOk = 0; double sumIou = 0, sumEdge = 0, dens = 0, densMan = 0; int densN = 0;
        List<Double> ious = new ArrayList<>(), widths = new ArrayList<>();
        Set<Object> allShown = new HashSet<>();
        java.util.TreeMap<String, Integer> conf = new java.util.TreeMap<>();
        for (List<Shown> l : res.visible) for (Shown s : l) { if (allShown.add(s.z)) widths.add(s.hi - s.lo); }
        for (String[] mz : man) {
            long o = LocalDate.parse(mz[1]).toEpochDay();
            double lo = Double.parseDouble(mz[2]), hi = Double.parseDouble(mz[3]);
            int i0 = Arrays.binarySearch(ord, o); if (i0 < 0) i0 = -i0 - 1;
            if (i0 >= ord.length || ord[i0] - o > 5) continue;
            n++;
            double best = 0; Shown bs = null;
            for (int i = i0; i < Math.min(ord.length, i0 + Integer.getInteger("win", 10)); i++)
                for (Shown s : res.visible.get(i)) {
                    double inter = Math.min(hi, s.hi) - Math.max(lo, s.lo), uni = Math.max(hi, s.hi) - Math.min(lo, s.lo);
                    double iou = inter > 0 ? inter / uni : 0;
                    if (iou > best) { best = iou; bs = s; }
                }
            ious.add(best); sumIou += best;
            if (best >= 0.3) conf.merge(mz[0] + " → " + bs.st.kind.name(), 1, Integer::sum);
            if (best >= 0.3) { m3++; sumEdge += (Math.abs(bs.lo - lo) + Math.abs(bs.hi - hi)) / 2; if (agree(mz[0], bs.st.kind)) colorOk++; }
            if (best >= 0.5) m5++;
            int i1 = Math.min(ord.length - 1, i0 + 1);
            double mid = (lo + hi) / 2; int cA = 0, cM = 0;
            for (Shown s : res.visible.get(i1)) if (Math.abs((s.lo + s.hi) / 2 - mid) <= 15) cA++;
            for (String[] q : man) {
                double qm = (Double.parseDouble(q[2]) + Double.parseDouble(q[3])) / 2;
                if (LocalDate.parse(q[1]).toEpochDay() <= ord[i1] && Math.abs(qm - mid) <= 15) cM++;
            }
            dens += cA; densMan += cM; densN++;
        }
        ious.sort(null); widths.sort(null);
        System.out.printf(Locale.ROOT, "ручных %d | найдено IoU≥0.3: %d (%.0f%%), ≥0.5: %d (%.0f%%) | IoU медиана %.2f | край ошибка %.2f п. | цвет совпал %.0f%% | ширина зон алгоритма медиана %.2f | плотность ±15 п.: алгоритм %.1f / ручных %.1f%n",
                n, m3, 100.0 * m3 / n, m5, 100.0 * m5 / n, ious.get(ious.size() / 2), m3 > 0 ? sumEdge / m3 : 0, m3 > 0 ? 100.0 * colorOk / m3 : 0,
                widths.get(widths.size() / 2), dens / densN, densMan / densN);
        if (Boolean.getBoolean("conf")) System.out.println(conf);
        List<Shown> last = res.visible.get(res.visible.size() - 1);
        double plo = Double.MAX_VALUE, phi = -Double.MAX_VALUE;
        for (Shown v : last) { plo = Math.min(plo, v.lo); phi = Math.max(phi, v.hi); }
        double lastClose = res.sessions.get(res.sessions.size() - 1).open;
        int near = 0; for (Shown v : last) if (Math.abs((v.lo + v.hi) / 2 - lastClose) <= 150) near++;
        System.out.printf(Locale.ROOT, "   последняя сессия: зон %d, в ± 150 п. от цены %d (%.1f на 100 п.; у ручных ≈ 8)%n", last.size(), near, near / 3.0);
    }

    static boolean agree(String c, Kind k) {
        switch (c) {
            case "зелёная": return k == Kind.HVN_S;
            case "красная": return k == Kind.HVN_R;
            case "синяя": return k == Kind.CORE || k == Kind.HVN_RANGE || k == Kind.OLD_BALANCE;
            case "серая": return k == Kind.GREY || k == Kind.HVN_NEW;
            default: return false;
        }
    }
}
