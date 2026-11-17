import java.io.BufferedReader;
import java.io.FileOutputStream;
import java.io.FileReader;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

import patterns.zones.ZoneEngine;
import patterns.zones.ZoneEngine.Bar;
import patterns.zones.ZoneEngine.Params;
import patterns.zones.ZoneEngine.Result;
import patterns.zones.ZoneEngine.Shown;
import patterns.zones.ZonePlan;

/** Всё, что рисует движок, в текст: зоны по сессиям (границы, вид), прямоугольники и уровни ZonePlan с подсказками.
 *  java Dump bars_raw.csv out.txt [поле=значение ...]   (без полей — как индикатор по умолчанию)
 *  Правка «без изменения поведения»: дамп до и после, cmp. */
public class Dump {
    public static void main(String[] a) throws Exception {
        List<Bar> bars = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new FileReader(a[0]))) {
            for (String s; (s = r.readLine()) != null; ) {
                String[] f = s.split(",");
                bars.add(new Bar(Long.parseLong(f[0]), Double.parseDouble(f[1]), Double.parseDouble(f[2]),
                        Double.parseDouble(f[3]), Double.parseDouble(f[4])));
            }
        }
        Params p = new Params();
        p.compositeMode = true; p.zoneMaxWidthPts = 10;   // как в индикаторе
        for (int i = 2; i < a.length; i++) Bench.setField(p, a[i]);
        Result res = ZoneEngine.run(bars, p);
        try (PrintWriter w = new PrintWriter(new OutputStreamWriter(new FileOutputStream(a[1]), "UTF-8"))) {
            w.println("zones " + res.zones.size() + " comps " + res.comps.size() + " rolls " + Arrays.toString(res.rollGap));
            for (int i = 0; i < res.sessions.size(); i++) {
                StringBuilder b = new StringBuilder(res.sessions.get(i).date.toString());
                for (Shown v : res.visible.get(i)) b.append(String.format(Locale.ROOT, " %.2f-%.2f:%s", v.lo, v.hi, v.st.kind));
                w.println(b);
            }
            ZonePlan plan = ZonePlan.of(res, p);
            for (ZonePlan.Rect r : plan.rects)
                w.println(String.format(Locale.ROOT, "R %d %d %.2f %.2f %s | %s", r.t0, r.t1, r.lo, r.hi, r.kind, r.tip.replace('\n', '|')));
            for (ZonePlan.Level l : plan.levels)
                w.println(String.format(Locale.ROOT, "L %d %d %.2f %b %s", l.t0, l.t1, l.price, l.poc, l.tip.replace('\n', '|')));
        }
    }
}
