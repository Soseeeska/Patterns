import java.io.BufferedReader;
import java.io.FileReader;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import patterns.zones.ZoneEngine;
import patterns.zones.ZoneEngine.Bar;
import patterns.zones.ZoneEngine.Params;
import patterns.zones.ZoneEngine.Result;
import patterns.zones.ZoneEngine.Session;
import patterns.zones.ZoneEngine.Shown;
import patterns.zones.ZoneEngine.Zone;

/** Прогон движка на CSV-барах (export_bars.py) и дамп в JSON для compare.py.
 *  java ParityDump bars.csv out.json [alignSessions] [promote] */
public class ParityDump {
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
        if (a.length > 2) p.alignSessions = Integer.parseInt(a[2]);
        if (a.length > 3) p.promoteRejections = Integer.parseInt(a[3]);
        long t0 = System.nanoTime();
        Result res = ZoneEngine.run(bars, p);
        long ms = (System.nanoTime() - t0) / 1_000_000;
        try (PrintWriter w = new PrintWriter(a[1], "UTF-8")) {
            w.print("{\"ms\":" + ms + ",\"zones\":[");
            for (int i = 0; i < res.zones.size(); i++) {
                Zone z = res.zones.get(i);
                if (i > 0) w.print(",");
                w.printf(Locale.ROOT, "{\"k\":%d,\"lo\":%s,\"hi\":%s,\"av\":%d,\"ai\":%d,\"young\":%b,\"gu\":%s,\"src\":\"%s\",\"nL\":%d,\"nS\":%d,\"L\":%s,\"S\":%s,\"cf\":%s,\"ab\":[%s,%s]}",
                        z.k, z.lo, z.hi, z.avOrd(), z.ai, z.young,
                        z.greyUntil == null ? "null" : String.valueOf(z.greyUntil.toEpochDay()), z.src,
                        z.L.length, z.S.length, arr(z.L), arr(z.S), cfs(z), z.blo, z.bhi);
            }
            w.print("],\"sessions\":[");
            for (int i = 0; i < res.sessions.size(); i++) {
                Session s = res.sessions.get(i);
                if (i > 0) w.print(",");
                w.printf(Locale.ROOT, "{\"ord\":%d,\"open\":%s,\"vah\":%s,\"val\":%s,\"poc\":%s,\"vis\":[", s.ord, s.open, s.prevVah, s.prevVal, s.prevPoc);
                List<Shown> vis = res.visible.get(i);
                for (int j = 0; j < vis.size(); j++) {
                    Shown v = vis.get(j);
                    if (j > 0) w.print(",");
                    w.printf(Locale.ROOT, "[%s,%s,%d,\"%s\"]", v.z.blo, v.z.bhi, v.z.avOrd(), v.st.kind.name());
                }
                w.print("]}");
            }
            w.print("]}");
        }
        System.out.println("zones=" + res.zones.size() + " sessions=" + res.sessions.size() + " full=" + res.fullDays + " ms=" + ms);
    }

    static String cfs(Zone z) {
        long[] o = new long[z.cf.size()];
        for (int i = 0; i < o.length; i++) o[i] = z.cf.get(i)[0];
        return arr(o);
    }

    static String arr(long[] x) {
        StringBuilder b = new StringBuilder("[");
        for (int i = 0; i < x.length; i++) { if (i > 0) b.append(','); b.append(x[i]); }
        return b.append(']').toString();
    }
}
