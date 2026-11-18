package patterns.zones;
import java.awt.*; import java.awt.image.BufferedImage; import java.io.*; import java.time.*; import java.util.*; import java.util.List;
import javax.imageio.ImageIO;
import patterns.zones.ZoneEngine.*;
/** Профили RTH-дней, как их видит алгоритм при постройке зон: квадраты TPO (1 п.), найденные узлы → зоны (цвет на конец данных),
 *  подтверждения. java ProfView bars.csv out.png from to [дата данных] [lo hi] */
public class ProfView {
  public static void main(String[] a) throws Exception {
    List<Bar> bars = Check.read(a[0]);                // -Dfrom / -Dto — как в Check
    ZoneId tz = ZoneId.of("America/Chicago");
    LocalDate from = LocalDate.parse(a[2]), to = LocalDate.parse(a[3]), dataEnd = LocalDate.parse(a[4]);
    long end = dataEnd.plusDays(1).atTime(16, 0).atZone(tz).toInstant().toEpochMilli();
    bars.removeIf(b -> b.t >= end);
    double PLO = Double.parseDouble(a[5]), PHI = Double.parseDouble(a[6]);
    Params p = new Params(); p.compositeMode = true; p.zoneMaxWidthPts = 10;
    Result res = ZoneEngine.run(bars, p);
    double tick = p.tick;
    // дни, как в движке
    TreeMap<LocalDate, Day> days = new TreeMap<>();
    for (Bar b : res.bars) {
      LocalDateTime lt = LocalDateTime.ofInstant(Instant.ofEpochMilli(b.t), tz); int m = lt.getHour() * 60 + lt.getMinute();
      LocalDate sd = m >= p.ethStartMin ? lt.toLocalDate().plusDays(1) : lt.toLocalDate();
      if (m >= p.rthStartMin && m < p.rthEndMin) {
        Day d = days.computeIfAbsent(lt.toLocalDate(), x -> { Day y = new Day(); y.date = x; return y; });
        d.periods.add(new int[]{(m - p.rthStartMin) / 30, ZoneEngine.ticks(b.l, tick), ZoneEngine.ticks(b.h, tick), ZoneEngine.ticks(b.o, tick), ZoneEngine.ticks(b.c, tick)});
      }
    }
    for (Bar b : res.bars) {
      LocalDateTime lt = LocalDateTime.ofInstant(Instant.ofEpochMilli(b.t), tz); int m = lt.getHour() * 60 + lt.getMinute();
      LocalDate sd = m >= p.ethStartMin ? lt.toLocalDate().plusDays(1) : lt.toLocalDate();
      Day d = days.get(sd); if (d != null) { d.bodies.add(new int[]{ZoneEngine.ticks(b.o, tick), ZoneEngine.ticks(b.c, tick)}); d.sbars.add(new double[]{b.o, b.h, b.l, b.c}); }
    }
    List<Day> show = new ArrayList<>();
    for (Day d : days.subMap(from, true, to, true).values()) {
      int n = d.periods.size(); if (n < p.fullDayPeriods) continue;
      int[] lo = new int[n], hi = new int[n]; for (int i = 0; i < n; i++) { lo[i] = d.periods.get(i)[1]; hi[i] = d.periods.get(i)[2]; }
      d.profile = ZoneEngine.buildProfile(lo, hi); int[] va = ZoneEngine.valueArea(d.profile, p.valueAreaPct); d.poc = va[0]; d.val = va[1]; d.vah = va[2];
      show.add(d);
    }
    Map<LocalDate, List<Zone>> born = new HashMap<>();
    for (Zone z : res.zones) if (z.k == ZoneEngine.K_SESS && z.born != null) born.computeIfAbsent(z.born, x -> new ArrayList<>()).add(z);
    Map<Zone, Kind> kindNow = new HashMap<>();
    for (Shown v : res.visible.get(res.visible.size() - 1)) kindNow.put(v.z, v.st.kind);
    Map<Kind, Color> col = Map.of(Kind.HVN_S, new Color(0x1f9d3a), Kind.HVN_R, new Color(0xc0392b), Kind.CORE, new Color(0x3d6bff), Kind.GREY, new Color(0x8d959c));
    int colW = 78, L = 10, R = 80, T = 60, B = 50, W = L + R + colW * show.size(), H = 1300;
    BufferedImage img = new BufferedImage(W, H, BufferedImage.TYPE_INT_RGB);
    Graphics2D g = img.createGraphics(); g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
    g.setColor(new Color(0x111316)); g.fillRect(0, 0, W, H);
    java.util.function.DoubleUnaryOperator ty = v -> T + (PHI - v) / (PHI - PLO) * (H - T - B);
    g.setFont(new Font("SansSerif", Font.PLAIN, 12));
    for (double y = Math.ceil(PLO / 5) * 5; y <= PHI; y += 5) { int yy = (int) ty.applyAsDouble(y); g.setColor(((int) y) % 10 == 0 ? new Color(0x262b31) : new Color(0x1a1e23)); g.drawLine(L, yy, W - R, yy); if (((int) y) % 10 == 0) { g.setColor(new Color(0x8a9097)); g.drawString(String.valueOf((int) y), W - R + 6, yy + 4); } }
    // композиты: тело и ядро
    for (Comp c : res.comps) {
      if (c.end.isBefore(from) || c.start.isAfter(to)) continue;
      int i0 = -1, i1 = -1; for (int i = 0; i < show.size(); i++) { if (!show.get(i).date.isBefore(c.start) && i0 < 0) i0 = i; if (!show.get(i).date.isAfter(c.end)) i1 = i; }
      if (i0 < 0) continue;
      g.setColor(new Color(61, 107, 255, 40)); g.fillRect(L + i0 * colW, (int) ty.applyAsDouble(c.core.hi), (show.size() - i0) * colW, Math.max(2, (int) (ty.applyAsDouble(c.core.lo) - ty.applyAsDouble(c.core.hi))));
      g.setColor(new Color(0x3d6bff)); g.setStroke(new BasicStroke(1.5f)); g.drawRect(L + i0 * colW, (int) ty.applyAsDouble(c.hi), (i1 - i0 + 1) * colW, (int) (ty.applyAsDouble(c.lo) - ty.applyAsDouble(c.hi)));
      g.drawString(String.format(Locale.ROOT, "композит %s–%s%s, ядро %.2f–%.2f (полоса — ядро до конца картинки)", c.start, c.end, c.primary ? " ПЕРВИЧНЫЙ" : "", c.core.lo, c.core.hi), L + i0 * colW + 4, (int) ty.applyAsDouble(c.hi) - 6);
    }
    int onePt = (int) Math.round(1 / tick), maxW = (int) Math.round(p.sessMaxPts / tick);
    for (int i = 0; i < show.size(); i++) {
      Day d = show.get(i); int x0 = L + i * colW;
      g.setColor(new Color(0x262b31)); g.drawLine(x0, T - 20, x0, H - B);
      g.setColor(new Color(0xc8ccd0)); g.drawString(d.date.getDayOfMonth() + "." + String.format("%02d", d.date.getMonthValue()), x0 + 4, H - B + 16);
      // квадраты TPO по 1 п.: в строке — сколько 30m-периодов задели пункт
      Map<Integer, Integer> rows = new TreeMap<>();
      for (int[] q : d.periods) { int a1 = (int) Math.floor(q[1] * tick), a2 = (int) Math.floor(q[2] * tick); for (int k = a1; k <= a2; k++) rows.merge(k, 1, Integer::sum); }
      for (var e : rows.entrySet()) {
        int k = e.getKey(); if (k < PLO || k > PHI) continue;
        boolean inVa = k >= Math.floor(d.val * tick) && k <= Math.floor(d.vah * tick);
        g.setColor(inVa ? new Color(0x5a6068) : new Color(0x3a3f45));
        int yy = (int) ty.applyAsDouble(k + 1), hh = Math.max(1, (int) (ty.applyAsDouble(k) - ty.applyAsDouble(k + 1)) - 1);
        g.fillRect(x0 + 2, yy, Math.min(colW - 30, e.getValue() * 4), hh);
      }
      int pocPt = (int) Math.floor(d.poc * tick); g.setColor(new Color(0xe2662c)); g.fillRect(x0 + 2, (int) ty.applyAsDouble(pocPt + 1), 4, Math.max(1, (int) (ty.applyAsDouble(pocPt) - ty.applyAsDouble(pocPt + 1))));
      // узлы алгоритма (до сетки) — белый пунктир справа
      for (int[] nd : ZoneEngine.dayNodes(d, p, onePt, maxW)) {
        double nlo = nd[0] * tick, nhi = nd[1] * tick;
        if (p.gridPts > 0) { double glo = nlo, ghi = nhi; if (nd[4] == 1) { double mm = (glo + ghi) / 2; glo = mm - 0.01; ghi = mm + 0.01; } double[] gg = ZoneEngine.snapGrid(glo, ghi, p); nlo = gg[0]; nhi = gg[1]; }
        boolean isZone = false;
        for (Zone z : born.getOrDefault(d.date, List.of())) if (Math.abs(z.lo - nlo) < 1e-6 && Math.abs(z.hi - nhi) < 1e-6) isZone = true;
        int yy0 = (int) ty.applyAsDouble(nhi), yy1 = (int) ty.applyAsDouble(nlo);
        g.setColor(isZone ? Color.WHITE : new Color(0x777777));
        g.setStroke(new BasicStroke(1.2f, BasicStroke.CAP_BUTT, BasicStroke.JOIN_MITER, 10f, new float[]{3f, 3f}, 0f));
        g.drawRect(x0 + colW - 26, yy0, 22, Math.max(2, yy1 - yy0));
        if (nd[3] == 1) { g.setColor(new Color(0xaaaaaa)); g.drawString("м", x0 + colW - 22, yy0 - 2); }
      }
      // зоны, родившиеся из дня: заливка цветом на конец данных
      for (Zone z : born.getOrDefault(d.date, List.of())) {
        Kind k = kindNow.get(z); if (k == null) continue;
        g.setColor(col.getOrDefault(k, Color.GRAY)); int yy0 = (int) ty.applyAsDouble(z.hi), yy1 = (int) ty.applyAsDouble(z.lo);
        g.fillRect(x0 + colW - 24, yy0 + 1, 18, Math.max(2, yy1 - yy0 - 1));
      }
    }
    g.setFont(new Font("SansSerif", Font.BOLD, 14)); g.setColor(Color.WHITE);
    g.drawString("Профили RTH-дней, как их видит алгоритм (квадрат 1 п.; светлее — VA, оранжевый — POC). Справа в колонке: пунктир — узел (белый — стал зоной, серый — подтверждение", 12, 20);
    g.drawString("старой зоны), заливка — цвет зоны на конец данных (зелёная / красная / синяя / серая), «м» — минорный узел. История с 10.10.2022, данные по " + dataEnd + ".", 12, 38);
    ImageIO.write(img, "png", new File(a[1]));
  }
}
