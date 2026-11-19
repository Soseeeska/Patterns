package patterns.zones;
import java.awt.*; import java.awt.image.BufferedImage; import java.io.*; import java.time.*; import java.util.*; import java.util.List;
import javax.imageio.ImageIO;
import patterns.zones.ZoneEngine.*;
/** Композит, разобранный на сессионные RTH TPO-профили (не склеенные): java CompSess bars.csv outDir N [поле=значение ...]
 *  Берёт N закрытых композитов, равномерно по последнему году; по дню до и после — бледно. */
public class CompSess {
  static final ZoneId TZ = ZoneId.of("America/Chicago");
  public static void main(String[] a) throws Exception {
    List<Bar> bars = Check.read(a[0]);
    Params p = new Params(); p.compositeMode = true; p.zoneMaxWidthPts = 10;
    for (int i = 3; i < a.length; i++) Check.set(p, a[i]);
    Result res = ZoneEngine.run(bars, p);
    // RTH-периоды по датам
    TreeMap<LocalDate, List<double[]>> days = new TreeMap<>();
    for (Bar b : res.bars) {
      LocalDateTime lt = LocalDateTime.ofInstant(Instant.ofEpochMilli(b.t), TZ); int m = lt.getHour() * 60 + lt.getMinute();
      if (m >= p.rthStartMin && m < p.rthEndMin) days.computeIfAbsent(lt.toLocalDate(), x -> new ArrayList<>()).add(new double[]{(m - p.rthStartMin) / 30, b.o, b.h, b.l, b.c});
    }
    LocalDate last = days.lastKey(), since = last.minusYears(1);
    List<Comp> cs = new ArrayList<>();
    for (Comp c : res.comps) if (!c.live && c.start.isAfter(since)) cs.add(c);
    int N = Integer.parseInt(a[2]);
    List<Comp> pick = new ArrayList<>();
    for (int k = 0; k < N && !cs.isEmpty(); k++) { Comp c = cs.get((int) Math.round(k * (cs.size() - 1) / (double) Math.max(1, N - 1))); if (!pick.contains(c)) pick.add(c); }
    System.out.println("закрытых композитов за год: " + cs.size());
    int k = 0;
    for (Comp c : pick) draw(c, days, res, p, new File(a[1], String.format("comp_%02d_%s.png", ++k, c.start)));
  }

  static void draw(Comp c, TreeMap<LocalDate, List<double[]>> days, Result res, Params p, File out) throws Exception {
    List<LocalDate> ds = new ArrayList<>();
    LocalDate before = days.lowerKey(c.start), after = days.higherKey(c.end);
    if (before != null) ds.add(before);
    ds.addAll(days.subMap(c.start, true, c.end, true).keySet());
    if (after != null) ds.add(after);
    // квадраты 1 п.: строка q = floor(цена)
    double lo = Double.MAX_VALUE, hi = -Double.MAX_VALUE;
    List<Map<Integer, List<Integer>>> prof = new ArrayList<>(); int maxCnt = 1;
    for (LocalDate d : ds) {
      Map<Integer, List<Integer>> m = new TreeMap<>();
      for (double[] b : days.get(d)) {
        for (int q = (int) Math.floor(b[3]); q <= (int) Math.floor(b[2] - 1e-9); q++) m.computeIfAbsent(q, x -> new ArrayList<>()).add((int) b[0]);
        lo = Math.min(lo, b[3]); hi = Math.max(hi, b[2]);
      }
      for (List<Integer> v : m.values()) maxCnt = Math.max(maxCnt, v.size());
      prof.add(m);
    }
    lo = Math.min(lo, c.lo) - 3; hi = Math.max(hi, c.hi) + 3;
    int rows = (int) Math.ceil(hi - lo);
    int H = 1150, T = 96, B = 40, L = 14, R = 70;
    double rh = (H - T - B) / (double) rows;
    int cw = (int) Math.max(7, Math.min(16, rh * 1.1));
    int colW = maxCnt * cw + 34;
    int W = Math.max(1560, L + R + colW * ds.size());
    BufferedImage img = new BufferedImage(W, H, BufferedImage.TYPE_INT_RGB);
    Graphics2D g = img.createGraphics(); g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
    g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
    g.setColor(new Color(0x131722)); g.fillRect(0, 0, W, H);
    final double fhi = hi; final double frh = rh;
    java.util.function.DoubleToIntFunction ty = v -> (int) Math.round(T + (fhi - v) * frh);
    g.setFont(new Font("SansSerif", Font.PLAIN, 12));
    int step = rows > 120 ? 10 : 5;
    for (double y = Math.ceil(lo / step) * step; y <= hi; y += step) { int yy = ty.applyAsInt(y); g.setColor(new Color(0x1e2230)); g.drawLine(L, yy, W - R, yy); g.setColor(new Color(0x8a9097)); g.drawString(String.valueOf((int) y), W - R + 6, yy + 4); }
    int i0 = before != null ? 1 : 0, i1 = ds.size() - (after != null ? 2 : 1);
    int x0 = L + i0 * colW, x1 = L + (i1 + 1) * colW;
    // композит: ядро, тело, VA, POC, диапазон
    int[] va = ZoneEngine.valueArea(c.prof, p.valueAreaPct);
    g.setColor(new Color(0x2962ff)); g.setComposite(AlphaComposite.getInstance(AlphaComposite.SRC_OVER, 0.28f));
    g.fillRect(x0, ty.applyAsInt(c.core.hi), x1 - x0, Math.max(2, ty.applyAsInt(c.core.lo) - ty.applyAsInt(c.core.hi)));
    g.setComposite(AlphaComposite.SrcOver);
    Stroke dash = new BasicStroke(1.2f, BasicStroke.CAP_BUTT, BasicStroke.JOIN_MITER, 10, new float[]{6, 5}, 0);
    g.setStroke(dash); g.setColor(new Color(0xd1d4dc));
    g.drawLine(x0, ty.applyAsInt(va[2] * p.tick), x1, ty.applyAsInt(va[2] * p.tick)); g.drawLine(x0, ty.applyAsInt(va[1] * p.tick), x1, ty.applyAsInt(va[1] * p.tick));
    g.setColor(new Color(0x787b86));
    g.drawLine(x0, ty.applyAsInt(c.bodyHi), x1, ty.applyAsInt(c.bodyHi)); g.drawLine(x0, ty.applyAsInt(c.bodyLo), x1, ty.applyAsInt(c.bodyLo));
    g.setStroke(new BasicStroke(2f)); g.setColor(new Color(0xff9800)); g.drawLine(x0, ty.applyAsInt(c.pocT * p.tick), x1, ty.applyAsInt(c.pocT * p.tick));
    g.setStroke(new BasicStroke(1.5f)); g.setColor(new Color(0x2962ff)); g.drawRect(x0, ty.applyAsInt(c.hi), x1 - x0, ty.applyAsInt(c.lo) - ty.applyAsInt(c.hi));
    // сессии
    Font lf = new Font("Monospaced", Font.BOLD, (int) Math.max(8, Math.min(13, rh * 0.95)));
    for (int i = 0; i < ds.size(); i++) {
      boolean in = i >= i0 && i <= i1;
      int cx = L + i * colW + 22;
      g.setColor(new Color(0x2a2e39)); g.setStroke(new BasicStroke(1f)); g.drawLine(L + i * colW, T - 30, L + i * colW, H - B);
      LocalDate d = ds.get(i);
      g.setFont(new Font("SansSerif", Font.BOLD, 13)); g.setColor(in ? new Color(0xd1d4dc) : new Color(0x5d606b));
      g.drawString(String.format("%02d.%02d", d.getDayOfMonth(), d.getMonthValue()) + (days.get(d).size() < p.fullDayPeriods ? " корот." : "") + (in ? "" : i == 0 ? " до" : " после"), L + i * colW + 6, T - 14);
      Map<Integer, List<Integer>> m = prof.get(i);
      // VA и POC дня
      int tot = 0; for (List<Integer> v : m.values()) tot += v.size();
      List<Integer> qs = new ArrayList<>(m.keySet()); int poc = qs.get(0);
      for (int q : qs) if (m.get(q).size() > m.get(poc).size() || (m.get(q).size() == m.get(poc).size() && Math.abs(q - mid(qs)) < Math.abs(poc - mid(qs)))) poc = q;
      int vlo = poc, vhi = poc, acc = m.get(poc).size();
      while (acc < 0.7 * tot) {
        int up = m.containsKey(vhi + 1) ? m.get(vhi + 1).size() : -1, dn = m.containsKey(vlo - 1) ? m.get(vlo - 1).size() : -1;
        if (up < 0 && dn < 0) break;
        if (up >= dn) { vhi++; acc += up; } else { vlo--; acc += dn; }
      }
      g.setColor(in ? new Color(0xb2b5be) : new Color(0x4a4e59)); g.setStroke(new BasicStroke(2f));
      g.drawLine(cx - 8, ty.applyAsInt(vhi + 1), cx - 8, ty.applyAsInt(vlo));
      for (int q : qs) {
        List<Integer> v = m.get(q);
        for (int j = 0; j < v.size(); j++) {
          int per = v.get(j);
          float hue = 0.60f - 0.60f * per / 12f;
          Color col = Color.getHSBColor(hue, in ? 0.55f : 0.15f, in ? 0.95f : 0.45f);
          if (q == poc) col = in ? new Color(0xff9800) : new Color(0x7a5a2a);
          int yy = ty.applyAsInt(q + 1), hh = Math.max(1, ty.applyAsInt(q) - yy - (rh > 5 ? 1 : 0));
          int xx = cx + j * cw;
          if (rh >= 9) { g.setFont(lf); g.setColor(col); g.drawString(String.valueOf((char) ('A' + per)), xx, yy + hh - 1); }
          else { g.setColor(col); g.fillRect(xx, yy, cw - 1, hh); }
        }
      }
    }
    // заголовок
    g.setFont(new Font("SansSerif", Font.BOLD, 16)); g.setColor(new Color(0xd1d4dc));
    long nd = days.subMap(c.start, true, c.end, true).size();
    g.drawString(String.format(Locale.ROOT, "Композит %s – %s: %d сессий%s%s; диапазон %.2f–%.2f (%.0f п.)", c.start, c.end, nd,
        c.extendsOf != null ? ", продолжение после ложного выхода" : "", c.primary ? ", первичный (price discovery)" : "", c.lo, c.hi, c.hi - c.lo), L, 24);
    g.setFont(new Font("SansSerif", Font.PLAIN, 13)); g.setColor(new Color(0xb2b5be));
    g.drawString(String.format(Locale.ROOT, "ядро %.2f–%.2f (синяя полоса) · POC композита %.2f (оранжевая) · VA композита %.2f–%.2f (белый пунктир) · тело %.2f–%.2f (серый пунктир) · рамка — диапазон",
        c.core.lo, c.core.hi, c.pocT * p.tick, va[1] * p.tick, va[2] * p.tick, c.bodyLo, c.bodyHi), L, 46);
    g.drawString("сессии RTH по отдельности, квадрат 1 п., буквы A–M — 30-минутки (A 08:30 CT); оранжевая строка — POC сессии, вертикальная черта — её VA 70%; бледные — день до и день после", L, 64);
    ImageIO.write(img, "png", out);
    System.out.println(out + "  " + c.start + "–" + c.end + " " + nd + " д.");
  }
  static double mid(List<Integer> qs) { return (qs.get(0) + qs.get(qs.size() - 1)) / 2.0; }
}
