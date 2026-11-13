import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Font;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.time.Instant;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;

import javax.imageio.ImageIO;

import patterns.zones.ZoneEngine;
import patterns.zones.ZoneEngine.Bar;
import patterns.zones.ZoneEngine.Kind;
import patterns.zones.ZoneEngine.Params;
import patterns.zones.ZoneEngine.Result;
import patterns.zones.ZonePlan;

/** Превью того, что рисует TpoZonesStudy (тот же ZoneEngine + ZonePlan, цвета и прозрачность по умолчанию):
 *  java Preview bars.csv out.png [сессий на картинке] [N подгонки]
 *  Ось времени — по барам (без пустых выходных), как у MotiveWave. */
public class Preview {
    public static void main(String[] a) throws Exception {
        List<Bar> bars = new ArrayList<>();
        try (BufferedReader r = new BufferedReader(new FileReader(a[0]))) {
            for (String s; (s = r.readLine()) != null; ) {
                String[] f = s.split(",");
                bars.add(new Bar(Long.parseLong(f[0]), Double.parseDouble(f[1]), Double.parseDouble(f[2]),
                        Double.parseDouble(f[3]), Double.parseDouble(f[4])));
            }
        }
        int k = a.length > 2 ? Integer.parseInt(a[2]) : 5;
        Params p = new Params();
        if (a.length > 3) p.alignSessions = Integer.parseInt(a[3]);
        Result res = ZoneEngine.run(bars, p);
        ZonePlan plan = ZonePlan.of(res, p);
        int n = bars.size();
        long[] st = new long[n];
        for (int i = 0; i < n; i++) st[i] = bars.get(i).t;

        ZoneId tz = p.tz;
        int f0 = n - 1, seen = 0; java.time.LocalDate cur = null;
        for (int i = n - 1; i >= 0; i--) {
            var lt = Instant.ofEpochMilli(st[i]).atZone(tz).toLocalDateTime();
            var d = lt.getHour() * 60 + lt.getMinute() >= p.ethStartMin ? lt.toLocalDate().plusDays(1) : lt.toLocalDate();
            if (!d.equals(cur)) { cur = d; if (++seen > k) break; }
            f0 = i;
        }
        double lo = Double.MAX_VALUE, hi = -Double.MAX_VALUE;
        for (int i = f0; i < n; i++) { lo = Math.min(lo, bars.get(i).l); hi = Math.max(hi, bars.get(i).h); }
        double pLo = lo - 12, pHi = hi + 12;
        int W = 1800, H = 1000, PW = W - 70, PH = H - 40, extra = 8;
        double bw = (double) (PW - 20) / (n - f0 + extra);
        final int first = f0;
        java.util.function.LongToDoubleFunction tx = time -> {
            int i = Arrays.binarySearch(st, time);
            double x;
            if (i >= 0) x = i - first;
            else {
                int ins = -i - 1;
                if (ins == 0) x = -1;
                else x = ins - 1 - first + Math.min(ins >= n ? 1e9 : 1.0, (time - st[ins - 1]) / 1_800_000.0);
            }
            return 10 + x * bw;
        };
        java.util.function.DoubleUnaryOperator ty = v -> (pHi - v) / (pHi - pLo) * PH + 10;

        Map<Kind, Color> base = new EnumMap<>(Kind.class);
        base.put(Kind.HVN_S, new Color(0x2fb35e)); base.put(Kind.HVN_R, new Color(0xef5a59));
        base.put(Kind.HVN_RANGE, new Color(0x8f84ea)); base.put(Kind.HVN_NEW, new Color(0x8d959c));
        base.put(Kind.OLD_BALANCE, new Color(0x4a8fe6)); base.put(Kind.GREY, new Color(0x8d959c));
        BufferedImage img = new BufferedImage(W, H, BufferedImage.TYPE_INT_RGB);
        Graphics2D g = img.createGraphics();
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
        g.setColor(new Color(0x15181c)); g.fillRect(0, 0, W, H);
        g.setClip(0, 0, PW, H - 30);
        // разделители сессий и RTH-подсветка
        for (var s : res.sessions) {
            double x = tx.applyAsDouble(s.startMs);
            if (x < 0 || x > PW) continue;
            g.setColor(new Color(0x262b31)); g.drawLine((int) x, 0, (int) x, H);
        }
        for (ZonePlan.Rect r : plan.rects) {
            double x0 = tx.applyAsDouble(r.t0), x1 = tx.applyAsDouble(r.t1);
            if (x1 < 0) continue;
            double y0 = ty.applyAsDouble(r.z.bhi), y1 = ty.applyAsDouble(r.z.blo);
            Color c = base.get(r.kind);
            int op = r.kind == Kind.OLD_BALANCE ? 10 : r.kind == Kind.GREY ? 14 : 24;
            g.setColor(new Color(c.getRed(), c.getGreen(), c.getBlue(), Math.round(op * 2.55f)));
            g.fill(new java.awt.geom.Rectangle2D.Double(x0, y0, x1 - x0, Math.max(2, y1 - y0)));
            g.setColor(new Color(c.getRed(), c.getGreen(), c.getBlue(), 217));
            g.setStroke(new BasicStroke(1f));
            g.draw(new java.awt.geom.Line2D.Double(x0, y0, x1, y0));
            g.draw(new java.awt.geom.Line2D.Double(x0, y1, x1, y1));
        }
        Color up = new Color(0xc9ced3), dn = new Color(0xc9ced3), upFill = new Color(0x15181c);
        for (int i = f0; i < n; i++) {
            Bar b = bars.get(i);
            double x = tx.applyAsDouble(b.t) + bw / 2;
            g.setColor(b.c >= b.o ? up : dn);
            g.draw(new java.awt.geom.Line2D.Double(x, ty.applyAsDouble(b.h), x, ty.applyAsDouble(b.l)));
            double y0 = ty.applyAsDouble(Math.max(b.o, b.c)), y1 = ty.applyAsDouble(Math.min(b.o, b.c));
            var rect = new java.awt.geom.Rectangle2D.Double(x - bw * 0.35, y0, bw * 0.7, Math.max(1, y1 - y0));
            if (b.c >= b.o) { g.setColor(upFill); g.fill(rect); g.setColor(up); g.draw(rect); } else { g.setColor(dn); g.fill(rect); }
        }
        float[] dash = {5f, 4f};
        for (ZonePlan.Level l : plan.levels) {
            double x0 = tx.applyAsDouble(l.t0), x1 = tx.applyAsDouble(l.t1);
            if (x1 < 0) continue;
            double y = ty.applyAsDouble(l.price);
            g.setColor(l.poc ? new Color(0xf08a52) : new Color(0x8a9097));
            g.setStroke(l.poc ? new BasicStroke(1.6f) : new BasicStroke(1f, BasicStroke.CAP_BUTT, BasicStroke.JOIN_MITER, 10f, dash, 0f));
            g.draw(new java.awt.geom.Line2D.Double(x0, y, x1, y));
        }
        g.setClip(null);
        g.setStroke(new BasicStroke(1f));
        g.setFont(new Font("SansSerif", Font.PLAIN, 12));
        g.setColor(new Color(0x9aa3ab));
        for (double pr = Math.ceil(pLo / 10) * 10; pr <= pHi; pr += 10) g.drawString(String.format("%.0f", pr), PW + 8, (int) ty.applyAsDouble(pr) + 4);
        for (var s : res.sessions) {
            double x = tx.applyAsDouble(s.startMs);
            if (x < 0 || x > PW - 40) continue;
            g.drawString(s.date.toString(), (int) x + 4, H - 12);
        }
        g.setColor(new Color(0xe7eaed)); g.setFont(new Font("SansSerif", Font.BOLD, 14));
        g.drawString("TPO Zones 1.1 — превью (ES, 30m, ETH+RTH, подгонка " + p.alignSessions + " сесс.)", 16, 24);
        ImageIO.write(img, "png", new File(a[1]));
        System.out.println(a[1] + " rects=" + plan.rects.size() + " levels=" + plan.levels.size());
    }
}
